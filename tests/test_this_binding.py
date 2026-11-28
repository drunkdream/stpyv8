#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Regression test: `this` of an overridden method must be the receiver.

A JSClass-backed object (a CPythonObject / JSProxy) exposes its methods through
V8's NamedPropertyHandler. When a *JS function* is assigned to such a method
slot and then called as ``obj.method(...)``, the receiver (``this``) must be
``obj`` -- exactly like a native V8 object.

DOM polyfills / analytics SDKs wrap native methods in precisely this way, e.g.::

    var orig = document.createElement;
    document.createElement = function (tag) {
        var el = orig.apply(this, arguments);   // needs this === document
        ...
    };

If V8 wrongly binds ``this`` to the global proxy instead of ``obj``, then
``orig.apply(this, ...)`` performs an illegal invocation (the WebIDL brand check
fails) and the polyfill throws -- leaving pages that depend on it as empty
shells (this is what breaks emonitor et al. inside pybrowser).

The test builds a tiny DOM-like JSClass tree (Document -> Element) and asserts
that after overriding a method the ``this`` inside the override is the receiver
object, both for a global object (``document``) and for a plain nested
instance (``el``).
"""

import unittest

import STPyV8


class Element(STPyV8.JSClass):
    def setAttribute(self, name, value):
        return name


class Document(STPyV8.JSClass):
    def createElement(self, tag):
        return Element()


class InheritedDocument(Document):
    """The prototype stub must resolve methods on the defining base class."""


class DocumentWithOwnAttribute(STPyV8.JSClass):
    def __init__(self):
        self.tag = "own"

    def createElement(self, tag):
        return Element()


class TestThisBindingOfOverriddenMethod(unittest.TestCase):
    def _eval(self, code):
        with STPyV8.JSContext() as ctxt:
            # expose a JSClass instance as the global `document`, mirroring how
            # pybrowser sets ctx.locals.document = bridge.wrap(doc)
            ctxt.locals.document = Document()
            return ctxt.eval(code)

    def test_global_object_overridden_method_this(self):
        """Overriding a global object's method must bind this to that object."""
        ok = self._eval(
            """
            var ok;
            var orig = document.createElement;
            document.createElement = function (tag) {
                ok = (this === document);
                return orig.apply(this, arguments);
            };
            document.createElement('div');
            ok;
            """
        )
        self.assertTrue(
            ok,
            "this of an overridden global method should be the receiver object",
        )

    def test_nested_instance_overridden_method_this(self):
        """Overriding a plain (non-global) instance method must bind this to it."""
        ok = self._eval(
            """
            var el = document.createElement('div');
            var orig = el.setAttribute;
            el.setAttribute = function (k, v) {
                return this === el;
            };
            el.setAttribute('a', '1');
            """
        )
        self.assertTrue(
            ok,
            "this of an overridden nested method should be the receiver instance",
        )

    def test_original_prototype_method_still_works(self):
        """Sanity: the unwrapped native method keeps working normally."""
        out = self._eval(
            """
            var el = document.createElement('div');
            el.setAttribute('a', '1');
            'ok';
            """
        )
        self.assertEqual("ok", out)

    def test_override_with_non_function_value(self):
        """An own property wins even when its value is not callable."""
        self.assertEqual(42, self._eval("document.version = 42; document.version;"))

    def test_instance_own_attribute_is_exposed(self):
        """Values written by Python's __init__ must be visible as own props."""
        with STPyV8.JSContext() as ctxt:
            ctxt.locals.document = DocumentWithOwnAttribute()
            self.assertEqual("own", ctxt.eval("document.tag;"))

    def test_captured_original_method_can_still_be_called(self):
        """`var orig = obj.method` must not resolve to the override (no recursion)."""
        out = self._eval(
            """
            var orig = document.createElement;
            document.createElement = function () { return 'override'; };
            orig.apply(document, ['div']).setAttribute('a', '1');
            """
        )
        self.assertEqual("a", out)

    def test_override_receives_more_than_ten_arguments(self):
        out = self._eval(
            "document.createElement = function () { return arguments.length; };"
            "document.createElement(1,2,3,4,5,6,7,8,9,10,11,12);"
        )
        self.assertEqual(12, out)

    def test_inherited_method_override_binds_receiver(self):
        with STPyV8.JSContext() as ctxt:
            ctxt.locals.document = InheritedDocument()
            ok = ctxt.eval(
                """
                var orig = document.createElement;
                document.createElement = function () {
                    return this === document ? orig.apply(this, arguments) : null;
                };
                document.createElement('div') !== null;
                """
            )
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
