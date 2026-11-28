#!/usr/bin/env python
# -*- coding: utf-8 -*-

import unittest

import STPyV8


class TestIsolate(unittest.TestCase):
    def testBase(self):
        with STPyV8.JSIsolate() as isolate:
            self.assertIsNotNone(isolate.current)
            self.assertFalse(isolate.locked)

    def testUnhandledRejectionIsObservable(self):
        # A bare V8 embed normally loses unhandled promise rejections silently
        # (no event loop, no 'unhandledrejection' event, no process.on), which
        # made "the app crashed" indistinguishable from "all good". The isolate
        # records them via V8's SetPromiseRejectCallback.
        with STPyV8.JSContext() as ctxt:
            STPyV8.JSIsolate.clearPendingRejections()

            ctxt.eval("Promise.reject(new Error('boom-unhandled'));")
            STPyV8.JSIsolate.runMicrotasks()

            pending = STPyV8.JSIsolate.pendingRejections
            self.assertEqual(1, len(pending))
            self.assertEqual("boom-unhandled", pending[0]["message"])
            self.assertIn("boom-unhandled", pending[0]["description"])
            self.assertIn("Error", pending[0]["description"])
            self.assertTrue(pending[0]["stack"])

            # Clearing forgets them (so a host does not report twice).
            STPyV8.JSIsolate.clearPendingRejections()
            self.assertEqual([], STPyV8.JSIsolate.pendingRejections)

    def testThrowInsideThenIsObservable(self):
        with STPyV8.JSContext() as ctxt:
            STPyV8.JSIsolate.clearPendingRejections()

            ctxt.eval(
                "Promise.resolve().then(function(){ throw new Error('boom-in-then'); });"
            )
            STPyV8.JSIsolate.runMicrotasks()

            pending = STPyV8.JSIsolate.pendingRejections
            self.assertEqual(1, len(pending))
            self.assertIn("boom-in-then", pending[0]["description"])
            STPyV8.JSIsolate.clearPendingRejections()

    def testAsyncThrowIsObservable(self):
        with STPyV8.JSContext() as ctxt:
            STPyV8.JSIsolate.clearPendingRejections()

            ctxt.eval("(async function(){ throw new Error('boom-async'); })();")
            STPyV8.JSIsolate.runMicrotasks()

            pending = STPyV8.JSIsolate.pendingRejections
            self.assertEqual(1, len(pending))
            self.assertIn("boom-async", pending[0]["description"])
            STPyV8.JSIsolate.clearPendingRejections()

    def testHandledRejectionIsNotReported(self):
        with STPyV8.JSContext() as ctxt:
            STPyV8.JSIsolate.clearPendingRejections()

            # Handled in the same tick: never reported at all.
            ctxt.eval(
                "Promise.reject(new Error('handled'))"
                ".catch(function(e){ return e.message; });"
            )
            STPyV8.JSIsolate.runMicrotasks()
            self.assertEqual([], STPyV8.JSIsolate.pendingRejections)

            # Handled later: V8 fires kPromiseHandlerAddedAfterReject and the
            # earlier record must be withdrawn, otherwise a chain handled a
            # tick late would still look like a crash.
            ctxt.eval("var p = Promise.reject(new Error('late-handled'));")
            STPyV8.JSIsolate.runMicrotasks()
            self.assertEqual(1, len(STPyV8.JSIsolate.pendingRejections))

            ctxt.eval("p.catch(function(e){ return e.message; });")
            STPyV8.JSIsolate.runMicrotasks()
            self.assertEqual([], STPyV8.JSIsolate.pendingRejections)

    def testRunMicrotasksAdvancesPromiseChain(self):
        # Without an explicit checkpoint the chain simply would not progress.
        with STPyV8.JSContext() as ctxt:
            ctxt.eval("var done = [];")
            ctxt.eval(
                "Promise.resolve(1)"
                ".then(function(v){ done.push('a' + v); return v + 1; })"
                ".then(function(v){ done.push('b' + v); });"
            )
            ctxt.run_microtasks()
            self.assertEqual('["a1","b2"]', ctxt.eval("JSON.stringify(done)"))

    def testContextWrappers(self):
        # The JSContext-level conveniences mirror the isolate-level API and add
        # drain(), which turns an unhandled rejection into an exception.
        with STPyV8.JSContext() as ctxt:
            ctxt.clear_rejections()
            ctxt.eval("Promise.reject(new Error('boom-drain'));")
            ctxt.run_microtasks()
            self.assertEqual(1, len(ctxt.pending_rejections))

            ctxt.clear_rejections()
            self.assertEqual([], ctxt.pending_rejections)

            ctxt.eval("Promise.reject(new Error('boom-drain'));")
            with self.assertRaises(STPyV8.UnhandledRejection) as caught:
                ctxt.drain()
            self.assertIn("boom-drain", str(caught.exception))
            self.assertEqual(1, len(caught.exception.rejections))
            # drain() clears what it reported.
            self.assertEqual([], ctxt.pending_rejections)

            # A clean chain raises nothing.
            ctxt.eval("Promise.resolve(1).then(function(v){ return v; });")
            self.assertEqual([], ctxt.drain())

    def testEnterLeave(self):
        with STPyV8.JSIsolate() as isolate:
            self.assertIsNotNone(isolate.current)
