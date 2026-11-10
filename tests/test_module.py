#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""ES-module support: parsing with the ECMAScript "Module" goal.

``JSContext.eval_module`` / ``JSEngine.compileModule`` compile source as a
module instead of a classic script and expose the exports through the module
namespace object. The suite covers the whole stack: module semantics (L0), a
synchronous host loader for static imports (L1), and dynamic import(),
import.meta and top-level await (L2), including host-driven real I/O.
"""

import unittest

import STPyV8


class TestModule(unittest.TestCase):
    @staticmethod
    def _run(source, name="module.js"):
        """Evaluate *source*, discarding the namespace (for error-path tests).

        Namespace objects are tied to the context that created them, so tests
        that inspect exports must stay inside their ``with JSContext()`` block.
        """
        with STPyV8.JSContext() as ctxt:
            ctxt.eval_module(source, name)

    def test_exports_available_on_namespace(self):
        with STPyV8.JSContext() as ctxt:
            ns = ctxt.eval_module(
                "export const value = 42; "
                "export function add(a, b) { return a + b; }; "
                "export default 7;"
            )
            self.assertEqual(42, ns.value)
            self.assertEqual(3, ns.add(1, 2))
            self.assertEqual(7, ns["default"])
            self.assertIn("value", ns.keys())
            self.assertIn("add", ns.keys())

    def test_module_scope_and_strict_mode(self):
        with STPyV8.JSContext() as ctxt:
            ns = ctxt.eval_module(
                "var leaked = 1; "
                "export const topThis = typeof this; "
                "export const hasGlobalLeak = (typeof globalThis.leaked !== 'undefined');"
            )
            # Module code is always strict and has its own scope: top-level
            # `this` is undefined and module-level `var` never reaches global.
            self.assertEqual("undefined", ns.topThis)
            self.assertFalse(ns.hasGlobalLeak)

    def test_live_binding(self):
        with STPyV8.JSContext() as ctxt:
            ns = ctxt.eval_module(
                "export let n = 1; "
                "export function bump() { n++; return n; }"
            )
            self.assertEqual(2, ns.bump())
            self.assertEqual(3, ns.bump())
            self.assertEqual(3, ns.n)

    def test_evaluated_only_once(self):
        with STPyV8.JSContext() as ctxt:
            module = STPyV8.JSEngine().compileModule(
                "export let count = 1; count++; export const v = count;"
            )
            self.assertIn("count++", module.source)
            first = module.run()
            second = module.run()
            self.assertEqual(2, first.v)
            self.assertEqual(2, second.v)

    def test_classic_eval_unchanged(self):
        with STPyV8.JSContext() as ctxt:
            self.assertEqual(3, ctxt.eval("1+2"))
            # The classic path must still reject module syntax.
            with self.assertRaises(Exception):
                ctxt.eval("export const x = 1;")

    def test_syntax_error(self):
        with self.assertRaises(Exception) as raised:
            self._run("export const = ;")
        self.assertIn("Unexpected token", str(raised.exception))

    def test_top_level_exception_propagates(self):
        with self.assertRaises(Exception) as raised:
            self._run("throw new Error('boom'); export const v = 1;")
        self.assertIn("boom", str(raised.exception))

    def test_static_import_without_loader_reports_error(self):
        with self.assertRaises(Exception) as raised:
            self._run("import x from './a.js'; export const v = 1;")
        message = str(raised.exception)
        self.assertIn("no loader was provided", message)
        self.assertIn("a.js", message)

    def test_import_meta_and_dynamic_import_are_available(self):
        # L2 installs the isolate-level hooks: import.meta carries the module
        # id, and import() returns a promise (rejected here, no loader).
        with STPyV8.JSContext() as ctxt:
            meta = ctxt.eval_module("export const u = import.meta.url;", "meta.js")
            self.assertEqual("meta.js", meta.u)
            ctxt.eval_module(
                "globalThis.err = '';"
                "import('./x.js').then(() => {}, (e) => { globalThis.err = String(e); });"
                "export {};")
            ctxt.run_microtasks()
            self.assertIn("x.js", ctxt.eval("globalThis.err"))


class TestModuleLoader(unittest.TestCase):
    """L1: a synchronous host loader resolves static imports."""

    def test_dict_loader(self):
        sources = {
            "main.js": "import {x} from './a.js'; export const y = x + 1;",
            "a.js": "export const x = 41;",
        }
        with STPyV8.JSContext() as ctxt:
            ns = ctxt.eval_module(sources["main.js"], "main.js", loader=sources)
            self.assertEqual(42, ns.y)

    def test_callable_loader_receives_normalised_specifier(self):
        sources = {"a.js": "export const x = 7;"}
        calls = []

        def loader(specifier, referrer):
            calls.append((specifier, referrer))
            return sources.get(specifier)

        with STPyV8.JSContext() as ctxt:
            ns = ctxt.eval_module(
                "import {x} from './a.js'; export const y = x * 2;",
                "main.js", loader=loader)
            self.assertEqual(14, ns.y)
        self.assertEqual([("a.js", "main.js")], calls)

    def test_transitive_imports_share_a_single_module(self):
        sources = {
            "main.js": "import {a} from './a.js'; import {b} from './b.js'; "
                       "export const v = a + b;",
            "a.js": "import {s} from './shared.js'; export const a = s + 1;",
            "b.js": "import {s} from './shared.js'; export const b = s + 2;",
            "shared.js": "export const s = 10;",
        }
        requests = []

        def loader(specifier, referrer):
            requests.append(specifier)
            return sources[specifier]

        with STPyV8.JSContext() as ctxt:
            ns = ctxt.eval_module(sources["main.js"], "main.js", loader=loader)
            self.assertEqual(23, ns.v)
        self.assertEqual(1, requests.count("shared.js"))

    def test_relative_parent_specifier(self):
        sources = {
            "src/main.js": "import {v} from '../lib/v.js'; export const out = v;",
            "lib/v.js": "export const v = 5;",
        }
        with STPyV8.JSContext() as ctxt:
            ns = ctxt.eval_module(
                sources["src/main.js"], "src/main.js", loader=sources)
            self.assertEqual(5, ns.out)

    def test_circular_imports(self):
        sources = {
            "main.js": "import {getB} from './b.js'; export const v = getB();",
            "b.js": "import {getA} from './a.js'; "
                    "export function getB() { return getA(); }",
            "a.js": "import {getB} from './b.js'; "
                    "export function getA() { return 42; }",
        }
        with STPyV8.JSContext() as ctxt:
            ns = ctxt.eval_module(sources["main.js"], "main.js", loader=sources)
            self.assertEqual(42, ns.v)

    def test_graphs_are_isolated_between_evaluations(self):
        # The same specifier may map to different sources in different runs;
        # a stale cached module must not leak into the next graph.
        first = {"a.js": "export const x = 1;"}
        second = {"a.js": "export const x = 2;"}
        source = "import {x} from './a.js'; export const y = x;"
        with STPyV8.JSContext() as ctxt:
            self.assertEqual(1, ctxt.eval_module(source, "main.js", loader=first).y)
            self.assertEqual(2, ctxt.eval_module(source, "main.js", loader=second).y)

    def test_missing_module(self):
        with STPyV8.JSContext() as ctxt:
            with self.assertRaises(Exception) as raised:
                ctxt.eval_module(
                    "import {z} from './missing.js'; export const v=1;",
                    "main.js", loader={})
        self.assertIn("no module source for 'missing.js'", str(raised.exception))

    def test_loader_exception_is_reported(self):
        def bad(specifier, referrer):
            raise RuntimeError("loader boom " + specifier)

        with STPyV8.JSContext() as ctxt:
            with self.assertRaises(Exception) as raised:
                ctxt.eval_module(
                    "import {z} from './x.js'; export const v=1;",
                    "main.js", loader=bad)
        self.assertIn("loader boom x.js", str(raised.exception))

    def test_json_modules(self):
        with STPyV8.JSContext() as ctxt:
            ns = ctxt.eval_module(
                "import data from './x.json' with { type: 'json' };"
                "export const value = data.value;",
                "main.js", loader={"x.json": '{"value": 7}'})
            self.assertEqual(7, ns.value)

    def test_unknown_import_attribute_type_is_rejected(self):
        # Hosts must reject attributes they do not implement (per spec), not
        # silently ignore them.
        with STPyV8.JSContext() as ctxt:
            with self.assertRaises(Exception) as raised:
                ctxt.eval_module(
                    "import x from './a.js' with { type: 'css' }; export const v=1;",
                    "main.js", loader={"a.js": "export const x = 1;"})
        self.assertIn("unsupported import attribute type", str(raised.exception))

    def test_module_graph_released_with_isolate(self):
        # CIsolate::~CIsolate must release the registry Persistents before
        # Dispose(); otherwise Reset() would touch a dead isolate.
        with STPyV8.JSIsolate():
            with STPyV8.JSContext() as ctxt:
                ns = ctxt.eval_module(
                    "import {x} from './a.js'; export const y = x;",
                    "main.js", loader={"a.js": "export const x = 9;"})
                self.assertEqual(9, ns.y)


class TestModuleDynamic(unittest.TestCase):
    """L2: dynamic import(), import.meta and microtask-only top-level await."""

    def test_import_meta_url_filename_and_resolve(self):
        with STPyV8.JSContext() as ctxt:
            ns = ctxt.eval_module(
                "export const url = import.meta.url;"
                "export const filename = import.meta.filename;"
                "export const resolved = import.meta.resolve('./a.js');",
                "src/main.js")
            self.assertEqual("src/main.js", ns.url)
            self.assertEqual("src/main.js", ns.filename)
            self.assertEqual("src/a.js", ns.resolved)

    def test_dynamic_import_resolves_namespace(self):
        with STPyV8.JSContext() as ctxt:
            ctxt.eval_module(
                "globalThis.done = 0;"
                "import('./a.js').then((m) => { globalThis.done = m.x; });"
                "export {};",
                "main.js", loader={"a.js": "export const x = 7;"})
            ctxt.run_microtasks()
            self.assertEqual(7, ctxt.eval("globalThis.done"))

    def test_dynamic_import_missing_module_rejects(self):
        with STPyV8.JSContext() as ctxt:
            ctxt.eval_module(
                "globalThis.err = '';"
                "import('./missing.js').then("
                "  () => {},"
                "  (e) => { globalThis.err = String(e); });"
                "export {};",
                "main.js", loader={})
            ctxt.run_microtasks()
            self.assertIn("missing.js", ctxt.eval("globalThis.err"))

    def test_dynamic_import_is_cached_within_graph(self):
        requests = []

        def loader(specifier, referrer):
            requests.append(specifier)
            return {"a.js": "export const x = 1;"}[specifier]

        with STPyV8.JSContext() as ctxt:
            ctxt.eval_module(
                "globalThis.n = 0;"
                "import('./a.js').then(() => { globalThis.n++; });"
                "import('./a.js').then(() => { globalThis.n++; });"
                "export {};",
                "main.js", loader=loader)
            ctxt.run_microtasks()
            self.assertEqual(2, ctxt.eval("globalThis.n"))
        self.assertEqual(1, requests.count("a.js"))

    def test_top_level_await_microtask_settles(self):
        with STPyV8.JSContext() as ctxt:
            ns = ctxt.eval_module(
                "export const v = await Promise.resolve(42);", "tla.js")
            self.assertEqual(42, ns.v)

    def test_dynamic_import_with_import_meta(self):
        with STPyV8.JSContext() as ctxt:
            ctxt.eval_module(
                "globalThis.url = '';"
                "import('./a.js').then((m) => { globalThis.url = m.url; });"
                "export {};",
                "main.js", loader={"a.js": "export const url = import.meta.url;"})
            ctxt.run_microtasks()
            self.assertEqual("a.js", ctxt.eval("globalThis.url"))


class TestModuleHostDriven(unittest.TestCase):
    """L2+: cross-context source caching and host-driven top-level await."""

    def test_cross_context_source_cache(self):
        calls = []

        def loader(specifier, referrer):
            calls.append((specifier, referrer))
            return {"a.js": "export const x = 5;"}[specifier]

        with STPyV8.JSIsolate():
            with STPyV8.JSContext() as first:
                v1 = first.eval_module(
                    "import {x} from './a.js'; export const v = x;",
                    "main.js", loader=loader).v
            with STPyV8.JSContext() as second:
                v2 = second.eval_module(
                    "import {x} from './a.js'; export const v = x;",
                    "main.js", loader=loader).v

        self.assertEqual(5, v1)
        self.assertEqual(5, v2)
        # Same loader object -> the source is fetched once for both contexts.
        self.assertEqual([("a.js", "main.js")], calls)

    def test_top_level_await_real_io(self):
        with STPyV8.JSContext() as ctxt:
            promise, resolve, _reject = ctxt.create_promise()
            ctxt.locals.ready = promise
            module = ctxt.compile_module("export const v = await ready;", "tla.js")
            module.run_async()

            ctxt.run_microtasks()
            with self.assertRaises(Exception):
                module.namespace  # still pending

            resolve(42)
            ctxt.run_microtasks()
            self.assertEqual(42, module.namespace.v)

    def test_dynamic_import_of_top_level_await_module(self):
        with STPyV8.JSContext() as ctxt:
            promise, resolve, _reject = ctxt.create_promise()
            ctxt.locals.gate = promise
            ctxt.eval_module(
                "globalThis.result = 0;"
                "import('./slow.js').then((m) => { globalThis.result = m.x; });"
                "export {};",
                "main.js", loader={"slow.js": "export const x = await gate;"})
            ctxt.run_microtasks()
            self.assertEqual(0, ctxt.eval("globalThis.result"))

            resolve(99)
            ctxt.run_microtasks()
            self.assertEqual(99, ctxt.eval("globalThis.result"))


class TestModuleApi(unittest.TestCase):
    """Public API surface and asynchronous failure propagation."""

    def test_jsmodule_is_exported(self):
        self.assertIn("JSModule", STPyV8.__all__)
        self.assertTrue(hasattr(STPyV8, "JSModule"))

    def test_top_level_await_rejection_is_reported(self):
        with STPyV8.JSContext() as ctxt:
            _promise, _resolve, reject = ctxt.create_promise()
            ctxt.locals.ready = _promise
            module = ctxt.compile_module("export const v = await ready;", "tla.js")
            module.run_async()
            ctxt.run_microtasks()
            with self.assertRaises(Exception) as raised:
                reject("oops")
                # A rejected evaluation promise surfaces when the namespace
                # (i.e. the module's exports) is requested.
                ctxt.run_microtasks()
                _namespace = module.namespace
            self.assertIn("oops", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
