"""Python awaitable -> JS Promise bridge.

The bridge assumes the asyncio loop is driven on the same thread that owns the
V8 isolate; tests therefore run the loop explicitly and drain JS microtasks
after each turn.
"""

import asyncio
import gc
import unittest

import STPyV8 as v8


def _pump(loop, ctx, turns=8):
    """Advance the asyncio loop, then let the JS .then chains settle."""
    for _ in range(turns):
        loop.run_until_complete(asyncio.sleep(0))
        ctx.run_microtasks()


async def double_after_await(value):
    """A real async Python function that is called by the tests below."""
    await asyncio.sleep(0)
    return value * 2


class TestCoroutinePromise(unittest.TestCase):
    def test_coroutine_resolves_to_promise(self):
        async def work():
            await asyncio.sleep(0)
            return [1, 2, 3]

        loop = asyncio.new_event_loop()

        try:
            with v8.JSContext() as ctx:
                ctx.locals.work = work

                async def start():
                    ctx.eval(
                        "var p = work(), r, e; p.then(v => r = v, x => e = x.message);"
                    )

                loop.run_until_complete(start())
                _pump(loop, ctx)

                self.assertTrue(ctx.eval("p instanceof Promise"))
                self.assertEqual("function", ctx.eval("typeof p.then"))
                self.assertEqual([1, 2, 3], list(ctx.eval("r")))
                self.assertTrue(ctx.eval("Array.isArray(r)"))
                self.assertTrue(ctx.eval("e === undefined"))
        finally:
            loop.close()

    def test_coroutine_rejection_becomes_js_error(self):
        async def work():
            await asyncio.sleep(0)
            raise ValueError("boom")

        loop = asyncio.new_event_loop()

        try:
            with v8.JSContext() as ctx:
                ctx.locals.work = work

                async def start():
                    ctx.eval(
                        "var p = work(), r, e; p.then(v => r = v, x => e = x.message);"
                    )

                loop.run_until_complete(start())
                _pump(loop, ctx)

                self.assertEqual("boom", ctx.eval("e"))
                self.assertTrue(ctx.eval("r === undefined"))
        finally:
            loop.close()

    def test_asyncio_future_is_exposed_as_promise(self):
        loop = asyncio.new_event_loop()

        try:
            with v8.JSContext() as ctx:

                async def start():
                    fut = asyncio.get_running_loop().create_future()
                    ctx.locals.fut = fut
                    ctx.eval("var fv; fut.then(v => fv = v);")
                    return fut

                fut = loop.run_until_complete(start())
                fut.set_result("future-value")
                _pump(loop, ctx)

                self.assertEqual("future-value", ctx.eval("fv"))
        finally:
            loop.close()

    def test_no_running_loop_rejects_instead_of_hanging(self):
        async def work():
            return 42

        with v8.JSContext() as ctx:
            ctx.locals.work = work
            ctx.eval("var z, ze; work().then(v => z = v, x => ze = x.message);")
            ctx.run_microtasks()

            self.assertTrue(ctx.eval("z === undefined"))
            self.assertEqual("no running event loop", ctx.eval("ze"))

    def test_async_python_function_is_called(self):
        """Define an async Python function, then call it from Python and JS."""
        loop = asyncio.new_event_loop()

        try:
            with v8.JSContext() as ctx:
                ctx.locals.double_async = double_after_await

                # The very same callable is an ordinary Python coroutine function.
                self.assertEqual(84, loop.run_until_complete(double_after_await(42)))

                async def start():
                    ctx.eval(
                        "var out; (async function(){ out = await double_async(42); })();"
                    )

                loop.run_until_complete(start())
                _pump(loop, ctx)

                self.assertEqual(84, ctx.eval("out"))
        finally:
            loop.close()

    def test_late_settle_after_context_teardown_is_safe(self):
        """A Task completing after its JSContext was torn down must not settle
        a Promise into the dead context (the old use-after-free path)."""
        loop = asyncio.new_event_loop()
        called = []

        async def work():
            await asyncio.sleep(0)
            return 42

        def mark():
            called.append(True)

        try:
            with v8.JSContext() as ctx:
                ctx.locals.work = work
                ctx.locals.mark = mark

                async def start():
                    # The Task is scheduled but deliberately left pending here.
                    ctx.eval("var p = work(); p.then(function(){ mark(); });")

                loop.run_until_complete(start())

            del ctx
            gc.collect()

            # The coroutine settles now, after CContext teardown has released
            # every ObjectTracer / py::object owned by that context.
            loop.run_until_complete(asyncio.sleep(0.01))

            # A late settle must be a no-op: no Promise continuation may run
            # against the torn-down context.
            self.assertEqual([], called)
        finally:
            loop.close()

    def test_async_dict_keeps_none_fields(self):
        """A None value in a returned dict becomes JS null, not undefined."""
        async def work():
            return {"a": 1, "nil": None, "nums": [1, 2]}

        loop = asyncio.new_event_loop()

        try:
            with v8.JSContext() as ctx:
                ctx.locals.work = work

                async def start():
                    ctx.eval(
                        "var out, err; work().then(v => out = v, x => err = String(x));"
                    )

                loop.run_until_complete(start())
                _pump(loop, ctx)

                self.assertTrue(ctx.eval("out.nil === null"))
                self.assertEqual(
                    '{"a":1,"nil":null,"nums":[1,2]}',
                    ctx.eval("JSON.stringify(out)"),
                )
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()
