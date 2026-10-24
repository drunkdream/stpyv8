#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import os
import datetime
import unittest
import pytest

import STPyV8

STPYV8_DEBUG = os.getenv("STPYV8_DEBUG") is not None


def convert(obj):
    if isinstance(obj, STPyV8.JSArray):
        return [convert(v) for v in obj]

    if isinstance(obj, STPyV8.JSObject):
        return dict([[str(k), convert(obj.__getattr__(str(k)))] for k in obj.__dir__()])

    return obj


def _make_dom():
    """Build a small DOM-like JSClass hierarchy (fresh classes per call).

    Mirrors the real-world case where a Python class hierarchy
    (JSElement -> JSHTMLElement -> ... -> JSNode) must map onto the JS
    prototype chain so that ``instanceof``, ``.constructor`` and ``__proto__``
    behave as expected.
    """
    class JSNode(STPyV8.JSClass):
        nodeKind = "node"
        # class / static attributes -- must be exposed on the constructor.
        UNSENT = 0
        OPENED = 1

        @classmethod
        def make(cls):
            return cls()

        def describe(self):
            return "node:" + self.nodeKind

    class JSElement(JSNode):
        pass

    class JSHTMLElement(JSElement):
        def describe(self):  # override the base implementation
            return "html:" + self.nodeKind

    class Element(JSHTMLElement):
        def __init__(self):
            self.tagName = "BODY"

    class Para(JSHTMLElement):
        pass

    return {
        "JSNode": JSNode,
        "JSElement": JSElement,
        "JSHTMLElement": JSHTMLElement,
        "Element": Element,
        "Para": Para,
    }


class TestWrapper(unittest.TestCase):
    def testObject(self):
        with STPyV8.JSContext() as ctxt:
            o = ctxt.eval("new Object()")

            self.assertTrue(hash(o) > 0)

            o1 = o.clone()

            self.assertEqual(hash(o1), hash(o))
            self.assertTrue(o != o1)

        self.assertRaises(UnboundLocalError, o.clone)

    def testAutoConverter(self):
        with STPyV8.JSContext() as ctxt:
            ctxt.eval(
                """
                var_i = 1;
                var_f = 1.0;
                var_s = "test";
                var_b = true;
                var_s_obj = new String("test");
                var_b_obj = new Boolean(true);
                var_f_obj = new Number(1.5);
            """
            )

            _vars = ctxt.locals

            var_i = _vars.var_i

            self.assertTrue(var_i)
            self.assertEqual(1, int(var_i))

            var_f = _vars.var_f

            self.assertTrue(var_f)
            self.assertEqual(1.0, float(_vars.var_f))

            var_s = _vars.var_s
            self.assertTrue(var_s)
            self.assertEqual("test", str(_vars.var_s))

            var_b = _vars.var_b
            self.assertTrue(var_b)
            self.assertTrue(bool(var_b))

            self.assertEqual("test", _vars.var_s_obj)
            self.assertTrue(_vars.var_b_obj)
            self.assertEqual(1.5, _vars.var_f_obj)

            attrs = dir(ctxt.locals)

            self.assertTrue(attrs)
            self.assertTrue("var_i" in attrs)
            self.assertTrue("var_f" in attrs)
            self.assertTrue("var_s" in attrs)
            self.assertTrue("var_b" in attrs)
            self.assertTrue("var_s_obj" in attrs)
            self.assertTrue("var_b_obj" in attrs)
            self.assertTrue("var_f_obj" in attrs)

    def testExactConverter(self):
        class MyInteger(int, STPyV8.JSClass):
            pass

        class MyString(str, STPyV8.JSClass):
            pass

        class MyDateTime(datetime.time, STPyV8.JSClass):
            pass

        class Global(STPyV8.JSClass):
            var_bool = True
            var_int = 1
            var_float = 1.0
            var_str = "str"
            var_datetime = datetime.datetime.now()
            var_date = datetime.date.today()
            var_time = datetime.time()

            var_myint = MyInteger()
            var_mystr = MyString("mystr")
            var_mytime = MyDateTime()

        with STPyV8.JSContext(Global()) as ctxt:
            typename = ctxt.eval(
                "(function (name) { return this[name].constructor.name; })"
            )
            typeof = ctxt.eval("(function (name) { return typeof(this[name]); })")

            self.assertEqual("Boolean", typename("var_bool"))
            self.assertEqual("Number", typename("var_int"))
            self.assertEqual("Number", typename("var_float"))
            self.assertEqual("String", typename("var_str"))
            self.assertEqual("Date", typename("var_datetime"))
            self.assertEqual("Date", typename("var_date"))
            self.assertEqual("Date", typename("var_time"))

            self.assertEqual("MyInteger", typename("var_myint"))
            self.assertEqual("MyString", typename("var_mystr"))
            self.assertEqual("MyDateTime", typename("var_mytime"))

            # A JSClass instance that is not callable reports typeof "object"
            # (only classes defining __call__ are callable => typeof "function").
            self.assertEqual("object", typeof("var_myint"))
            self.assertEqual("object", typeof("var_mystr"))
            self.assertEqual("object", typeof("var_mytime"))

    def testJavascriptWrapper(self):
        with STPyV8.JSContext() as ctxt:
            self.assertEqual(type(None), type(ctxt.eval("null")))
            self.assertEqual(type(None), type(ctxt.eval("undefined")))
            self.assertEqual(bool, type(ctxt.eval("true")))
            self.assertEqual(str, type(ctxt.eval("'test'")))
            self.assertEqual(int, type(ctxt.eval("123")))
            self.assertEqual(float, type(ctxt.eval("3.14")))
            self.assertEqual(datetime.datetime, type(ctxt.eval("new Date()")))
            self.assertEqual(STPyV8.JSArray, type(ctxt.eval("[1, 2, 3]")))
            self.assertEqual(STPyV8.JSFunction, type(ctxt.eval("(function() {})")))
            self.assertEqual(STPyV8.JSObject, type(ctxt.eval("new Object()")))

    def testPythonWrapper(self):
        with STPyV8.JSContext() as ctxt:
            typeof = ctxt.eval("(function type(value) { return typeof value; })")
            protoof = ctxt.eval(
                "(function protoof(value) { return Object.prototype.toString.apply(value); })"
            )

            self.assertEqual("[object Null]", protoof(None))
            self.assertEqual("boolean", typeof(True))
            self.assertEqual("number", typeof(123))
            self.assertEqual("number", typeof(3.14))
            self.assertEqual("string", typeof("test"))

            self.assertEqual("[object Date]", protoof(datetime.datetime.now()))
            self.assertEqual("[object Date]", protoof(datetime.date.today()))
            self.assertEqual("[object Date]", protoof(datetime.time()))

            def test():
                pass

            self.assertEqual("[object Function]", protoof(abs))
            self.assertEqual("[object Function]", protoof(test))
            self.assertEqual("[object Function]", protoof(self.testPythonWrapper))
            self.assertEqual("[object Function]", protoof(int))

    def testFunction(self):
        with STPyV8.JSContext() as ctxt:
            func = ctxt.eval(
                """
                (function ()
                {
                    function a()
                    {
                        return "abc";
                    }

                    return a();
                })
                """
            )

            self.assertEqual("abc", str(func()))
            self.assertTrue(func is not None)
            self.assertFalse(func is None)

            func = ctxt.eval("(function test() {})")

            self.assertEqual("test", func.name)
            self.assertEqual("", func.resname)
            self.assertEqual(0, func.linenum)
            self.assertEqual(14, func.colnum)

            # FIXME
            # Why the setter doesn't work?
            #
            # func.name = "hello"
            # self.assertEqual("hello", func.name)

            func.setName("hello")
            self.assertEqual("hello", func.name)

    def testCall(self):
        class Hello:
            def __call__(self, name):
                return "hello " + name

        class Global(STPyV8.JSClass):
            hello = Hello()

        with STPyV8.JSContext(Global()) as ctxt:
            self.assertEqual("hello world", ctxt.eval("hello('world')"))

    def testJSFunction(self):
        with STPyV8.JSContext() as ctxt:
            hello = ctxt.eval("(function (name) { return 'Hello ' + name; })")

            self.assertTrue(isinstance(hello, STPyV8.JSFunction))
            self.assertEqual("Hello world", hello("world"))
            self.assertEqual("Hello world", hello.invoke(["world"]))

            obj = ctxt.eval(
                "({ 'name': 'world', 'hello': function (name) { return 'Hello ' + name + ' from ' + this.name; }})"
            )
            hello = obj.hello
            self.assertTrue(isinstance(hello, STPyV8.JSFunction))
            self.assertEqual("Hello world from world", hello("world"))

            tester = ctxt.eval("({ 'name': 'tester' })")
            self.assertEqual("Hello world from tester", hello.apply(tester, ["world"]))
            self.assertEqual(
                "Hello world from json", hello.apply({"name": "json"}, ["world"])
            )

    def testConstructor(self):
        with STPyV8.JSContext() as ctx:
            ctx.eval(
                """
                var Test = function() {
                    this.trySomething();
                };
                Test.prototype.trySomething = function() {
                    this.name = 'soirv8';
                };

                var Test2 = function(first_name, last_name) {
                    this.name = first_name + ' ' + last_name;
                };
                """
            )

            self.assertTrue(isinstance(ctx.locals.Test, STPyV8.JSFunction))

            test = STPyV8.JSObject.create(ctx.locals.Test)

            self.assertTrue(isinstance(ctx.locals.Test, STPyV8.JSObject))
            self.assertEqual("soirv8", test.name)

            test2 = STPyV8.JSObject.create(ctx.locals.Test2, ("John", "Doe"))

            self.assertEqual("John Doe", test2.name)

            test3 = STPyV8.JSObject.create(
                ctx.locals.Test2, ("John", "Doe"), {"email": "john.doe@randommail.com"}
            )

            self.assertEqual("john.doe@randommail.com", test3.email)

    def testJSError(self):
        with STPyV8.JSContext() as ctxt:
            try:
                ctxt.eval('throw "test"')
                self.fail()
            except Exception:  # pylint:disable=broad-except
                self.assertTrue(STPyV8.JSError, sys.exc_info()[0])

    def testErrorInfo(self):
        with STPyV8.JSContext():
            with STPyV8.JSEngine() as engine:
                try:
                    engine.compile(
                        """
                        function hello()
                        {
                            throw Error("hello world");
                        }

                        hello();""",
                        "test",
                        10,
                        10,
                    ).run()
                    self.fail()
                except STPyV8.JSError as e:
                    self.assertTrue(
                        "JSError: Error: hello world ( test @ 14 : 28 )  ->" in str(e)
                    )
                    self.assertEqual("Error", e.name)
                    self.assertEqual("hello world", e.message)
                    self.assertEqual("test", e.scriptName)
                    self.assertEqual(14, e.lineNum)
                    self.assertEqual(96, e.startPos)
                    self.assertEqual(97, e.endPos)
                    self.assertEqual(28, e.startCol)
                    self.assertEqual(29, e.endCol)
                    self.assertEqual(
                        'throw Error("hello world");', e.sourceLine.strip()
                    )

                    self.assertEqual(
                        "Error: hello world\n"
                        + "    at hello (test:14:35)\n"
                        + "    at test:17:25",
                        e.stackTrace,
                    )

    def testParseStack(self):
        self.assertEqual(
            [
                ("Error", "unknown source", None, None),
                ("test", "native", None, None),
                ("<anonymous>", "test0", 3, 5),
                ("f", "test1", 2, 19),
                ("g", "test2", 1, 15),
                (None, "test3", 1, None),
                (None, "test3", 1, 1),
            ],
            STPyV8.JSError.parse_stack(
                """Error: err
            at Error (unknown source)
            at test (native)
            at new <anonymous> (test0:3:5)
            at f (test1:2:19)
            at g (test2:1:15)
            at test3:1
            at test3:1:1"""
            ),
        )

    def testStackTrace(self):
        class Global(STPyV8.JSClass):
            def GetCurrentStackTrace(self, limit):  # pylint:disable=no-self-use
                return STPyV8.JSStackTrace.GetCurrentStackTrace(
                    limit, STPyV8.JSStackTrace.Options.Detailed
                )

        with STPyV8.JSContext(Global()) as ctxt:
            st = ctxt.eval(
                """
                function a()
                {
                    return GetCurrentStackTrace(4);
                }
                function b()
                {
                    return eval("a()");
                }
                function c()
                {
                    return new b();
                }
            c();""",
                "test",
            )

            self.assertEqual(4, len(st))
            self.assertEqual(
                "\tat a (test:4:28)\n\tat eval ((eval))\n\tat b (test:8:28)\n\tat c (test:12:28)\n",
                str(st),
            )
            self.assertEqual(
                "test.a (4:28)\n.eval (1:1) eval\ntest.b (8:28) constructor\ntest.c (12:28)",
                "\n".join(
                    [
                        "%s.%s (%d:%d)%s%s"
                        % (
                            f.scriptName,
                            f.funcName,
                            f.lineNum,
                            f.column,
                            " eval" if f.isEval else "",
                            " constructor" if f.isConstructor else "",
                        )
                        for f in st
                    ]
                ),
            )

    def testPythonException(self):
        class Global(STPyV8.JSClass):
            def raiseException(self):  # pylint:disable=no-self-use
                raise RuntimeError("Hello")

        with STPyV8.JSContext(Global()) as ctxt:
            ctxt.eval(
                """
                msg ="";
                try
                {
                    this.raiseException()
                }
                catch(e)
                {
                    msg += "catch " + e + ";";
                }
                finally
                {
                    msg += "finally";
                }"""
            )
            self.assertEqual("catch Error: Hello;finally", str(ctxt.locals.msg))

    def testExceptionMapping(self):
        class TestException(Exception):
            pass

        class Global(STPyV8.JSClass):
            def raiseIndexError(self):  # pylint:disable=no-self-use
                return [1, 2, 3][5]

            def raiseAttributeError(self):  # pylint:disable=no-self-use
                None.hello()

            def raiseSyntaxError(self):  # pylint:disable=no-self-use
                eval("???")  # pylint:disable=eval-used

            def raiseTypeError(self):  # pylint:disable=no-self-use
                int(sys)

            def raiseNotImplementedError(self):  # pylint:disable=no-self-use
                raise NotImplementedError("Not supported")

            def raiseExceptions(self):
                raise TestException()

        with STPyV8.JSContext(Global()) as ctxt:
            ctxt.eval("try { this.raiseIndexError(); } catch (e) { msg = e; }")

            self.assertEqual(
                "RangeError: list index out of range", str(ctxt.locals.msg)
            )

            ctxt.eval("try { this.raiseAttributeError(); } catch (e) { msg = e; }")

            self.assertEqual(
                "ReferenceError: 'NoneType' object has no attribute 'hello'",
                str(ctxt.locals.msg),
            )

            ctxt.eval("try { this.raiseSyntaxError(); } catch (e) { msg = e; }")

            self.assertEqual("SyntaxError: invalid syntax", str(ctxt.locals.msg))

            ctxt.eval("try { this.raiseTypeError(); } catch (e) { msg = e; }")

            if sys.version_info.major >= 3:
                if sys.version_info.minor > 9:
                    self.assertEqual(
                        "TypeError: int() argument must be a string, a bytes-like object or a real number, not 'module'",
                        str(ctxt.locals.msg),
                    )
                else:
                    self.assertEqual(
                        "TypeError: int() argument must be a string, a bytes-like object or a number, not 'module'",
                        str(ctxt.locals.msg),
                    )

            ctxt.eval("try { this.raiseNotImplementedError(); } catch (e) { msg = e; }")

            self.assertEqual("Error: Not supported", str(ctxt.locals.msg))

            self.assertRaises(TestException, ctxt.eval, "this.raiseExceptions();")

    def testArray(self):
        with STPyV8.JSContext() as ctxt:
            array = ctxt.eval(
                """
                var array = new Array();

                for (i=0; i<10; i++)
                {
                    array[i] = 10-i;
                }

                array;
                """
            )

            self.assertTrue(isinstance(array, STPyV8.JSArray))
            self.assertEqual(10, len(array))

            self.assertTrue(5 in array)
            self.assertFalse(15 in array)

            self.assertEqual(10, len(array))

            for i in range(10):
                self.assertEqual(10 - i, array[i])

            array[5] = 0

            self.assertEqual(0, array[5])

            del array[5]

            self.assertEqual(None, array[5])

            # array         [10, 9, 8, 7, 6, None, 4, 3, 2, 1]
            # array[4:7]                  4^^^^^^^^^7
            # array[-3:-1]                         -3^^^^^^-1
            # array[0:0]    []

            self.assertEqual([6, None, 4], array[4:7])
            self.assertEqual([3, 2], array[-3:-1])
            self.assertEqual([], array[0:0])

            self.assertEqual([10, 9, 8, 7, 6, None, 4, 3, 2, 1], list(array))

            array[1:3] = [9, 9]

            self.assertEqual([10, 9, 9, 7, 6, None, 4, 3, 2, 1], list(array))

            array[5:7] = [8, 8]

            self.assertEqual([10, 9, 9, 7, 6, 8, 8, 3, 2, 1], list(array))

            del array[1]

            self.assertEqual([10, None, 9, 7, 6, 8, 8, 3, 2, 1], list(array))

            ctxt.locals.array1 = STPyV8.JSArray(5)
            ctxt.locals.array2 = STPyV8.JSArray([1, 2, 3, 4, 5])

            for i in range(len(ctxt.locals.array2)):
                ctxt.locals.array1[i] = ctxt.locals.array2[i] * 10

            ctxt.eval(
                """
                var sum = 0;

                for (i=0; i<array1.length; i++)
                    sum += array1[i]

                for (i=0; i<array2.length; i++)
                    sum += array2[i]
                """
            )

            self.assertEqual(165, ctxt.locals.sum)

            ctxt.locals.array3 = [1, 2, 3, 4, 5]
            self.assertTrue(ctxt.eval("array3[1] === 2"))
            self.assertTrue(ctxt.eval("array3[9] === undefined"))

            args = [
                [
                    "a = Array(7); for(i=0; i<a.length; i++) a[i] = i; a[3] = undefined; a[a.length-1]; a",
                    "0,1,2,,4,5,6",
                    [0, 1, 2, None, 4, 5, 6],
                ],
                [
                    "a = Array(7); for(i=0; i<a.length - 1; i++) a[i] = i; a[a.length-1]; a",
                    "0,1,2,3,4,5,",
                    [0, 1, 2, 3, 4, 5, None],
                ],
                [
                    "a = Array(7); for(i=1; i<a.length; i++) a[i] = i; a[a.length-1]; a",
                    ",1,2,3,4,5,6",
                    [None, 1, 2, 3, 4, 5, 6],
                ],
            ]

            for arg in args:
                array = ctxt.eval(arg[0])

                self.assertEqual(arg[1], str(array))
                self.assertEqual(arg[2], [array[i] for i in range(len(array))])

            self.assertEqual(
                3,
                ctxt.eval("(function (arr) { return arr.length; })")(
                    STPyV8.JSArray([1, 2, 3])
                ),
            )
            self.assertEqual(
                2,
                ctxt.eval("(function (arr, idx) { return arr[idx]; })")(
                    STPyV8.JSArray([1, 2, 3]), 1
                ),
            )
            self.assertEqual(
                "[object Array]",
                ctxt.eval(
                    "(function (arr) { return Object.prototype.toString.call(arr); })"
                )(STPyV8.JSArray([1, 2, 3])),
            )
            self.assertEqual(
                "[object Array]",
                ctxt.eval(
                    "(function (arr) { return Object.prototype.toString.call(arr); })"
                )(STPyV8.JSArray((1, 2, 3))),
            )
            self.assertEqual(
                "[object Array]",
                ctxt.eval(
                    "(function (arr) { return Object.prototype.toString.call(arr); })"
                )(STPyV8.JSArray(list(range(3)))),
            )

    def testArraySlices(self):
        with STPyV8.JSContext() as ctxt:
            array = ctxt.eval(
                """
                var array = new Array();
                array;
            """
            )

            array[2:4] = [42, 24]
            # array         [None, None, 42, 24]
            self.assertEqual(len(array), 4)
            self.assertEqual(ctxt.eval("array[0]"), None)
            self.assertEqual(ctxt.eval("array[1]"), None)
            self.assertEqual(ctxt.eval("array[2]"), 42)
            self.assertEqual(ctxt.eval("array[3]"), 24)

            array[2:4] = [1, 2]
            # array         [None, None, 1, 2]
            self.assertEqual(len(array), 4)
            self.assertEqual(ctxt.eval("array[0]"), None)
            self.assertEqual(ctxt.eval("array[1]"), None)
            self.assertEqual(ctxt.eval("array[2]"), 1)
            self.assertEqual(ctxt.eval("array[3]"), 2)

            array[0:4:2] = [7, 8]
            # array         [7, None, 8, 2]
            self.assertEqual(len(array), 4)
            self.assertEqual(ctxt.eval("array[0]"), 7)
            self.assertEqual(ctxt.eval("array[1]"), None)
            self.assertEqual(ctxt.eval("array[2]"), 8)
            self.assertEqual(ctxt.eval("array[3]"), 2)

            array[1:4] = [10]
            # array         [7, 10, None, None]
            self.assertEqual(len(array), 4)
            self.assertEqual(ctxt.eval("array[0]"), 7)
            self.assertEqual(ctxt.eval("array[1]"), 10)
            self.assertEqual(ctxt.eval("array[2]"), None)
            self.assertEqual(ctxt.eval("array[3]"), None)

            array[0:7] = [0, 1, 2]
            # array         [0, 1, 2, None, None, None, None]
            self.assertEqual(len(array), 7)
            self.assertEqual(ctxt.eval("array[0]"), 0)
            self.assertEqual(ctxt.eval("array[1]"), 1)
            self.assertEqual(ctxt.eval("array[2]"), 2)
            self.assertEqual(ctxt.eval("array[3]"), None)
            self.assertEqual(ctxt.eval("array[4]"), None)
            self.assertEqual(ctxt.eval("array[5]"), None)
            self.assertEqual(ctxt.eval("array[6]"), None)

            array[0:7] = [0, 1, 2, 3, 4, 5, 6]
            # array         [0, 1, 2, 3, 4, 5, 6]
            self.assertEqual(len(array), 7)
            self.assertEqual(ctxt.eval("array[0]"), 0)
            self.assertEqual(ctxt.eval("array[1]"), 1)
            self.assertEqual(ctxt.eval("array[2]"), 2)
            self.assertEqual(ctxt.eval("array[3]"), 3)
            self.assertEqual(ctxt.eval("array[4]"), 4)
            self.assertEqual(ctxt.eval("array[5]"), 5)
            self.assertEqual(ctxt.eval("array[6]"), 6)

            del array[0:2]
            # array         [None, None, 2, 3, 4, 5, 6]
            self.assertEqual(len(array), 7)
            self.assertEqual(ctxt.eval("array[0]"), None)
            self.assertEqual(ctxt.eval("array[1]"), None)
            self.assertEqual(ctxt.eval("array[2]"), 2)
            self.assertEqual(ctxt.eval("array[3]"), 3)
            self.assertEqual(ctxt.eval("array[4]"), 4)
            self.assertEqual(ctxt.eval("array[5]"), 5)
            self.assertEqual(ctxt.eval("array[6]"), 6)

            del array[3:7:2]
            # array         [None, None, 2, None, 4, None, 6]
            self.assertEqual(len(array), 7)
            self.assertEqual(ctxt.eval("array[0]"), None)
            self.assertEqual(ctxt.eval("array[1]"), None)
            self.assertEqual(ctxt.eval("array[2]"), 2)
            self.assertEqual(ctxt.eval("array[3]"), None)
            self.assertEqual(ctxt.eval("array[4]"), 4)
            self.assertEqual(ctxt.eval("array[5]"), None)
            self.assertEqual(ctxt.eval("array[6]"), 6)

    def testMultiDimArray(self):
        with STPyV8.JSContext() as ctxt:
            ret = ctxt.eval(
                """
                ({
                    'test': function(){
                        return  [
                            [ 1, 'abla' ],
                            [ 2, 'ajkss' ],
                        ]
                    }
                })
                """
            ).test()

            self.assertEqual([[1, "abla"], [2, "ajkss"]], convert(ret))

    def testLazyConstructor(self):
        class Globals(STPyV8.JSClass):
            def __init__(self):
                self.array = STPyV8.JSArray([1, 2, 3])

        with STPyV8.JSContext(Globals()) as ctxt:
            self.assertEqual(2, ctxt.eval("""array[1]"""))

    def testForEach(self):
        class NamedClass:
            foo = 1  # pylint:disable=disallowed-name

            def __init__(self):
                self.bar = 2  # pylint:disable=disallowed-name

            @property
            def foobar(self):
                return self.foo + self.bar

        def gen(x):
            for i in range(x):
                yield i

        with STPyV8.JSContext() as ctxt:
            func = ctxt.eval(
                """(function (k) {
                var result = [];
                for (var prop in k) {
                  result.push(prop);
                }
                return result;
            })"""
            )

            self.assertTrue(
                set(["bar", "foo", "foobar"]).issubset(set(func(NamedClass())))
            )
            self.assertEqual(["0", "1", "2"], list(func([1, 2, 3])))
            self.assertEqual(["0", "1", "2"], list(func((1, 2, 3))))
            self.assertEqual(["1", "2", "3"], list(func({"1": 1, "2": 2, "3": 3})))

            self.assertEqual(["0", "1", "2"], list(func(list(gen(3)))))

    def testDict(self):
        with STPyV8.JSContext() as ctxt:
            obj = ctxt.eval("var r = { 'a' : 1, 'b' : 2 }; r")

            self.assertEqual(1, obj.a)
            self.assertEqual(2, obj.b)

            self.assertEqual({"a": 1, "b": 2}, dict(obj))

            self.assertEqual(
                {
                    "a": 1,
                    "b": [1, 2, 3],
                    "c": {"str": "goofy", "float": 1.234, "obj": {"name": "john doe"}},
                    "d": True,
                    "e": None,
                },
                convert(
                    ctxt.eval(
                        """var x =
                             { a: 1,
                               b: [1, 2, 3],
                               c: { str: 'goofy',
                                    float: 1.234,
                                    obj: { name: 'john doe' }},
                               d: true,
                               e: null }; x"""
                    )
                ),
            )

    def testDate(self):
        with STPyV8.JSContext() as ctxt:
            now1 = ctxt.eval("new Date();")

            self.assertTrue(now1)

            now2 = datetime.datetime.now()

            delta = now2 - now1 if now2 > now1 else now1 - now2

            self.assertTrue(delta < datetime.timedelta(seconds=1))

            func = ctxt.eval("(function (d) { return d.toString(); })")

            now = datetime.datetime.now()

            self.assertTrue(
                str(func(now).startswith(now.strftime("%a %b %d %Y %H:%M:%S")))
            )

            ctxt.eval("function identity(x) { return x; }")

            now3 = now2.replace(microsecond=123000)
            self.assertEqual(now3, ctxt.locals.identity(now3))

    def testUnicode(self):
        with STPyV8.JSContext() as ctxt:
            self.assertEqual("人", ctxt.eval('"人"'))
            self.assertEqual("é", ctxt.eval('"é"'))

            func = ctxt.eval("(function (msg) { return msg.length; })")

            self.assertEqual(2, func("测试"))

    def testClassicStyleObject(self):
        class FileSystemWrapper:
            @property
            def cwd(self):
                return os.getcwd()

        class Global:
            @property
            def fs(self):
                return FileSystemWrapper()

        with STPyV8.JSContext(Global()) as ctxt:
            self.assertEqual(os.getcwd(), ctxt.eval("fs.cwd"))

    def testRefCount(self):
        count = sys.getrefcount(None)

        class Global(STPyV8.JSClass):
            pass

        g = Global()
        g_refs = sys.getrefcount(g)

        with STPyV8.JSContext(g) as ctxt:
            ctxt.eval(
                """
                var none = null;
            """
            )

            self.assertTrue(count <= sys.getrefcount(None) <= count + 2)

            ctxt.eval(
                """
                var none = null;
            """
            )

            self.assertTrue(count <= sys.getrefcount(None) <= count + 2)

            del ctxt

        self.assertEqual(g_refs, sys.getrefcount(g))

    def testProperty(self):
        class Global(STPyV8.JSClass):
            def __init__(self, name):
                self._name = name

            def getname(self):
                return self._name

            def setname(self, name):
                self._name = name

            def delname(self):
                self._name = "deleted"

            name = property(getname, setname, delname)

        g = Global("world")

        with STPyV8.JSContext(g) as ctxt:
            self.assertEqual("world", ctxt.eval("name"))
            self.assertEqual("foobar", ctxt.eval("this.name = 'foobar';"))
            self.assertEqual("foobar", ctxt.eval("name"))
            self.assertTrue(ctxt.eval("delete name"))

        with STPyV8.JSContext() as ctxt:
            self.assertEqual("world", ctxt.eval("name = 'world';"))
            self.assertTrue(ctxt.eval("delete name;"))
            self.assertRaises(ReferenceError, ctxt.eval, "name")

            ctxt.eval(
                """
                let obj = {};
                obj.__defineGetter__('name', function() {
                    return 'test';
                });
            """
            )
            self.assertEqual("test", ctxt.eval("obj.name"))

    def testGetterAndSetter(self):
        class Global(STPyV8.JSClass):
            def __init__(self, testval):
                self.testval = testval

        with STPyV8.JSContext(Global("Test Value A")) as ctxt:
            self.assertEqual("Test Value A", ctxt.locals.testval)
            ctxt.eval(
                """
               this.__defineGetter__("test", function() {
                   return this.testval;
               });
               this.__defineSetter__("test", function(val) {
                   this.testval = val;
               });
           """
            )
            self.assertEqual("Test Value A", ctxt.locals.test)

            ctxt.eval("test = 'Test Value B';")

            self.assertEqual("Test Value B", ctxt.locals.test)

    def testReferenceCount(self):
        class Hello:
            def say(self):
                pass

            def __del__(self):
                owner.deleted = True  # pylint:disable=undefined-variable

        def test():
            with STPyV8.JSContext() as ctxt:
                fn = ctxt.eval("(function (obj) { obj.say(); })")

                obj = Hello()

                before = sys.getrefcount(obj)

                fn(obj)

                after = sys.getrefcount(obj)

                # CPython 3.14's reference-counting optimisations changed the
                # absolute counts sys.getrefcount() reports (a live local can now
                # read one lower than before -- see CPython gh-132346), so the
                # fixed 2/4 expectations only hold on older versions. On 3.14+
                # assert the *increase* caused by exposing obj to JS (STPyV8
                # holds extra references) instead of the exact values.
                if sys.version_info >= (3, 14):
                    self.assertGreater(after, before)
                else:
                    self.assertEqual(2, before)
                    self.assertEqual(4, after)

                del obj

        test()

    def testNullInString(self):
        with STPyV8.JSContext() as ctxt:
            fn = ctxt.eval("(function (s) { return s; })")

            self.assertEqual("hello \0 world", fn("hello \0 world"))

    def testLivingObjectCache(self):
        class Global(STPyV8.JSClass):
            i = 1
            b = True
            o = object()

        with STPyV8.JSContext(Global()) as ctxt:
            self.assertTrue(ctxt.eval("i == i"))
            self.assertTrue(ctxt.eval("b == b"))
            self.assertTrue(ctxt.eval("o == o"))

    @pytest.mark.skipif(STPYV8_DEBUG, reason="Not a test for debug mode")
    def testMemoryLeak(self):
        with STPyV8.JSIsolate():
            with STPyV8.JSContext() as ctxt:
                inner = ctxt.eval("i => Array(1<<20).fill(i)")
                outer = ctxt.eval("o => o[0]")

                for i in range(1000):
                    outer(inner(i))

                self.assertEqual(999, i)

    def testNamedSetter(self):
        class Obj(STPyV8.JSClass):
            @property
            def p(self):
                return self._p

            @p.setter
            def p(self, value):
                self._p = value

        class Global(STPyV8.JSClass):
            def __init__(self):
                self.obj = Obj()
                self.d = {}
                self.p = None

        with STPyV8.JSContext(Global()) as ctxt:
            ctxt.eval(
                """
            x = obj;
            x.y = 10;
            x.p = 10;
            d.y = 10;
            """
            )
            self.assertEqual(10, ctxt.eval("obj.y"))
            self.assertEqual(10, ctxt.eval("obj.p"))
            self.assertEqual(10, ctxt.locals.d["y"])

    def testJsHidden(self):
        class History(STPyV8.JSClass):
            # `self.__stack` is stored as `_History__stack` by Python's private
            # name mangling, and the class name is public -- so without the list
            # below JS could read *and write* the attribute as
            # `h._History__stack`.
            __js_hidden__ = ("_History__stack", "_History__idx")

            def __init__(self):
                self.__stack = []
                self.__idx = 0
                self.length = 0

            def push(self, value):
                self.__stack.append(value)
                self.__idx += 1
                self.length = self.__idx

            def peek(self):
                return self.__stack[self.__idx - 1]

        class Global(STPyV8.JSClass):
            def __init__(self):
                self.h = History()

        global_ = Global()

        with STPyV8.JSContext(global_) as ctxt:
            ctxt.eval("h.push('a'); h.push('b');")

            # Python keeps full access to the mangled attributes.
            self.assertEqual(["a", "b"], global_.h._History__stack)
            self.assertEqual(2, global_.h._History__idx)
            self.assertEqual("b", global_.h.peek())

            # JS cannot see them.
            self.assertEqual("undefined", ctxt.eval("typeof h._History__stack"))
            self.assertEqual("undefined", ctxt.eval("typeof h._History__idx"))
            self.assertEqual(False, ctxt.eval("'_History__stack' in h"))
            self.assertNotIn("_History__stack", convert(ctxt.eval("Object.keys(h)")))

            # JS cannot write them either -- the assignment is silently dropped.
            ctxt.eval("h._History__stack = ['tampered']; h._History__idx = 99;")
            self.assertEqual(["a", "b"], global_.h._History__stack)
            self.assertEqual(2, global_.h._History__idx)

            # ... nor delete them.
            self.assertEqual(False, ctxt.eval("delete h._History__stack"))
            self.assertEqual(["a", "b"], global_.h._History__stack)

            # The public API keeps working from JS.
            self.assertEqual("b", ctxt.eval("h.peek()"))
            self.assertEqual(2, ctxt.eval("h.length"))

    def testJsHiddenInherited(self):
        class Base(STPyV8.JSClass):
            __js_hidden__ = ("_Base__secret",)

            def __init__(self):
                self.__secret = "base"

        class Derived(Base):
            def __init__(self):
                Base.__init__(self)

                # mangled to `_Derived__own`, which is *not* listed anywhere
                self.__own = "own"

        class Global(STPyV8.JSClass):
            def __init__(self):
                self.d = Derived()

        global_ = Global()

        with STPyV8.JSContext(global_) as ctxt:
            # inherited from the base class
            self.assertEqual("undefined", ctxt.eval("typeof d._Base__secret"))
            self.assertEqual(False, ctxt.eval("'_Base__secret' in d"))

            # not listed, so still reachable -- the list is a gate, not a filter
            self.assertEqual("own", ctxt.eval("d._Derived__own"))
            self.assertEqual("base", global_.d._Base__secret)

    def testRealPrototypeAccessorWins(self):
        # Mirrors the scroll-behavior-polyfill fix: a property that belongs on the
        # prototype as a genuine accessor (scrollTop in real browsers) is defined
        # there via Object.defineProperty. The instance interceptor must step
        # aside so the prototype accessor actually fires on instance access,
        # instead of shadowing it with the catch-all Python lookup.
        class Widget(STPyV8.JSClass):
            def __init__(self):
                self._v = 1
                self._top = 0

            # A Python attribute of the same name as the prototype accessor:
            # this is the case the interceptor used to swallow on reads.
            @property
            def top(self):
                return self._top

            @top.setter
            def top(self, value):
                self._top = value

            def toString(self):
                return "py-toString"

        class Global(STPyV8.JSClass):
            def __init__(self):
                self.Widget = Widget
                self.w = Widget()

        global_ = Global()

        with STPyV8.JSContext(global_) as ctxt:
            ctxt.eval(
                """
                var hits = {get: 0, set: 0};
                Object.defineProperty(Widget.prototype, 'v', {
                    configurable: true,
                    get: function () { hits.get++; return this._v; },
                    set: function (x) { hits.set++; this._v = x; }
                });
                Object.defineProperty(Widget.prototype, 'top', {
                    configurable: true,
                    get: function () { return 42; },
                    set: function (x) { this._top = x * 10; }
                });
                """
            )

            # The prototype now carries a real accessor whose .set is a function,
            # so a polyfill's getOwnPropertyDescriptor(...).set won't crash.
            self.assertEqual(
                "function",
                ctxt.eval("typeof (Object.getOwnPropertyDescriptor(Widget.prototype, 'v') || {}).set"),
            )

            # Instance reads/writes route to the prototype accessor -- its
            # getter/setter are actually invoked, not shadowed by the interceptor.
            self.assertEqual(1, ctxt.eval("w.v"))
            self.assertEqual(1, ctxt.eval("hits.get"))

            ctxt.eval("w.v = 5;")
            self.assertEqual(1, ctxt.eval("hits.set"))
            self.assertEqual(5, ctxt.eval("w.v"))

            # Python still sees the change through the backing field.
            self.assertEqual(5, global_.w._v)

            # Reads of a name Python also exposes must reach the prototype
            # accessor (42, not the Python value 0), and writes must reach its
            # setter (which stores into the Python backing field).
            self.assertEqual(42, ctxt.eval("w.top"))
            self.assertTrue(ctxt.eval("'top' in w"))

            ctxt.eval("w.top = 7;")
            self.assertEqual(70, global_.w._top)
            self.assertEqual(42, ctxt.eval("w.top"))

            # `delete` of an inherited accessor is a successful no-op: it must
            # not delete the Python attribute behind it.
            self.assertTrue(ctxt.eval("delete w.top"))
            self.assertEqual(70, global_.w._top)

            # Object.prototype built-ins are not treated as class prototypes,
            # so they keep losing to a Python attribute of the same name.
            self.assertEqual("py-toString", ctxt.eval("w.toString()"))

    def testNativeObjectIdentity(self):
        class Element(STPyV8.JSClass):
            def createElement(self, tag):
                return Element()

        class Window(STPyV8.JSClass):
            def __init__(self):
                self.document = Element()

        window = Window()

        with STPyV8.JSContext(window) as ctxt:
            node = ctxt.eval("document.createElement('div')")
            window.node = node

            ctxt.eval(
                """
            var marker = '__reactEvents$1';

            function getEventListenerSet(target) {
                var s = target[marker];
                if (s === undefined) { s = new Set(); target[marker] = s; }
                return s;
            }

            function dispatch(target, key) {
                var s = getEventListenerSet(target);
                if (s.has(key)) return 'duplicate';
                s.add(key);
                return 'added';
            }
            """
            )

            # React 17's getEventListenerSet() stores a native Set on a DOM node
            # and reads it back on the very next event. A JS object held as a
            # direct attribute of a JSClass must come back as *itself* -- the
            # second read used to return a Python-backed stand-in (typeof
            # "function", instanceof false, inherited methods failing with
            # "incompatible receiver"), which is what broke the SPA render.
            self.assertEqual("added", ctxt.eval("dispatch(node, 'click')"))
            self.assertEqual("duplicate", ctxt.eval("dispatch(node, 'click')"))
            self.assertEqual("added", ctxt.eval("dispatch(node, 'hover')"))

            self.assertEqual(True, ctxt.eval("node[marker] instanceof Set"))
            self.assertEqual("object", ctxt.eval("typeof node[marker]"))
            self.assertEqual(2, ctxt.eval("node[marker].size"))

            # ... and it keeps its own type-specific behaviour.
            ctxt.eval("node.list = [1, 2];")
            self.assertEqual(True, ctxt.eval("Array.isArray(node.list)"))
            self.assertEqual(True, ctxt.eval("node.list === node.list"))

            ctxt.eval("node.map = new Map([['k', 7]]);")
            self.assertEqual(True, ctxt.eval("node.map instanceof Map"))
            self.assertEqual(7, ctxt.eval("node.map.get('k')"))

            # Objects nested inside a plain JS object never round-trip through
            # Python, so they were always fine.
            ctxt.eval("node.holder = { s: new Set([1]) };")
            self.assertEqual(True, ctxt.eval("node.holder.s instanceof Set"))
            self.assertEqual(True, ctxt.eval("node.holder.s.has(1)"))

    def testWatch(self):
        class Obj(STPyV8.JSClass):
            def __init__(self):
                self.p = 1

        class Global(STPyV8.JSClass):
            def __init__(self):
                self.o = Obj()

        with STPyV8.JSContext(Global()) as ctxt:
            ctxt.eval(
                """
            o.watch("p", function (id, oldval, newval) {
                return oldval + newval;
            });
            """
            )

            self.assertEqual(1, ctxt.eval("o.p"))

            ctxt.eval("o.p = 2;")

            self.assertEqual(3, ctxt.eval("o.p"))

            ctxt.eval("delete o.p;")

            self.assertEqual(None, ctxt.eval("o.p"))

            ctxt.eval("o.p = 2;")

            self.assertEqual(2, ctxt.eval("o.p"))

            ctxt.eval("o.unwatch('p');")

            ctxt.eval("o.p = 1;")

            self.assertEqual(1, ctxt.eval("o.p"))

    def testReferenceError(self):
        class Global(STPyV8.JSClass):
            def __init__(self):
                self.s = self

        with STPyV8.JSContext(Global()) as ctxt:
            self.assertRaises(ReferenceError, ctxt.eval, "x")

            self.assertTrue(ctxt.eval("typeof(x) === 'undefined'"))

            self.assertTrue(ctxt.eval("typeof(String) === 'function'"))

            self.assertTrue(ctxt.eval("typeof(s.String) === 'undefined'"))

            self.assertTrue(ctxt.eval("typeof(s.z) === 'undefined'"))

    def testRaiseExceptionInGetter(self):
        class Document(STPyV8.JSClass):
            def __getattr__(self, name):
                if name == "y":
                    raise TypeError()

                return STPyV8.JSClass.__getattr__(self, name)

        class Global(STPyV8.JSClass):
            def __init__(self):
                self.document = Document()

        with STPyV8.JSContext(Global()) as ctxt:
            self.assertEqual(None, ctxt.eval("document.x"))
            self.assertRaises(TypeError, ctxt.eval, "document.y")

    def testUndefined(self):
        class Global(STPyV8.JSClass):
            def returnNull(self):  # pylint:disable=no-self-use
                return STPyV8.JSNull()

            def returnUndefined(self):  # pylint:disable=no-self-use
                return STPyV8.JSUndefined()

            def returnNone(self):  # pylint:disable=no-self-use
                return None

        with STPyV8.JSContext(Global()) as ctxt:
            self.assertFalse(bool(STPyV8.JSNull()))
            self.assertFalse(bool(STPyV8.JSUndefined()))

            self.assertEqual("null", str(STPyV8.JSNull()))
            self.assertEqual("undefined", str(STPyV8.JSUndefined()))

            self.assertTrue(ctxt.eval("null == returnNull()"))
            self.assertTrue(ctxt.eval("undefined == returnUndefined()"))
            self.assertTrue(ctxt.eval("null == returnNone()"))

    def testClassHierarchy(self):
        # Python inheritance must be mirrored into the JS prototype chain so
        # that `instanceof` works against every ancestor and `constructor` is
        # the real (shared) class constructor -- not a fabricated, per-access
        # object whose prototype is Function.prototype.
        dom = _make_dom()

        class Global(STPyV8.JSClass):
            def __init__(self):
                self.body = dom["Element"]()
                self.p = dom["Para"]()
                self.Element = dom["Element"]
                self.JSHTMLElement = dom["JSHTMLElement"]
                self.JSElement = dom["JSElement"]
                self.JSNode = dom["JSNode"]

        with STPyV8.JSContext(Global()) as ctxt:
            self.assertTrue(ctxt.eval("body instanceof Element"))
            self.assertTrue(ctxt.eval("body instanceof JSHTMLElement"))
            self.assertTrue(ctxt.eval("body instanceof JSElement"))
            self.assertTrue(ctxt.eval("body instanceof JSNode"))
            self.assertTrue(ctxt.eval("body instanceof Object"))

            self.assertTrue(ctxt.eval("body.constructor === Element"))
            self.assertEqual("Element", ctxt.eval("body.constructor.name"))
            self.assertEqual("Para", ctxt.eval("p.constructor.name"))

            # A sibling class must not be an instance of the other.
            self.assertFalse(ctxt.eval("p instanceof Element"))

            self.assertTrue(
                ctxt.eval("Object.getPrototypeOf(body) === Element.prototype")
            )
            self.assertEqual(
                "Element,JSHTMLElement,JSElement,JSNode,JSClass,Object",
                ctxt.eval(
                    "(function(){var out=[],p=Object.getPrototypeOf(body),n=0;"
                    "while(p&&n<10){out.push((p.constructor&&p.constructor.name)||'?');"
                    "p=Object.getPrototypeOf(p);n++;}return out.join(',');})()"
                ),
            )

    def testClassHierarchyPrototypeIdentity(self):
        dom = _make_dom()

        class Global(STPyV8.JSClass):
            def __init__(self):
                self.a = dom["Element"]()
                self.b = dom["Element"]()
                self.other = dom["Para"]()
                self.Element = dom["Element"]

        with STPyV8.JSContext(Global()) as ctxt:
            # Same class -> the exact same prototype object.
            self.assertTrue(
                ctxt.eval("Object.getPrototypeOf(a) === Object.getPrototypeOf(b)")
            )
            # Different classes -> different prototypes.
            self.assertFalse(
                ctxt.eval(
                    "Object.getPrototypeOf(a) === Object.getPrototypeOf(other)"
                )
            )
            # The prototype's constructor is the (stable) exposed class.
            self.assertTrue(ctxt.eval("Element.prototype.constructor === Element"))
            # constructor / class identity is stable across accesses (not a
            # fresh fabricated object every time).
            self.assertTrue(ctxt.eval("a.constructor === a.constructor"))
            self.assertTrue(ctxt.eval("a.constructor === Element"))
            self.assertTrue(ctxt.eval("Element === Element"))

    def testClassHierarchyInheritedMembers(self):
        dom = _make_dom()

        class Global(STPyV8.JSClass):
            def __init__(self):
                self.body = dom["Element"]()

        with STPyV8.JSContext(Global()) as ctxt:
            # Own instance attribute.
            self.assertEqual("BODY", ctxt.eval("body.tagName"))
            # Class attribute inherited from the base class.
            self.assertEqual("node", ctxt.eval("body.nodeKind"))
            # Method override resolved through the Python MRO.
            self.assertEqual("html:node", ctxt.eval("body.describe()"))

    def testClassHierarchyNewInstance(self):
        dom = _make_dom()

        class Global(STPyV8.JSClass):
            def __init__(self):
                self.Element = dom["Element"]
                self.JSHTMLElement = dom["JSHTMLElement"]

        with STPyV8.JSContext(Global()) as ctxt:
            self.assertEqual("function", ctxt.eval("typeof Element"))
            # `new Element()` instantiates the Python class (its __init__ runs)
            # and yields a proper instance of the hierarchy.
            self.assertTrue(ctxt.eval("(new Element()) instanceof Element"))
            self.assertTrue(ctxt.eval("(new Element()) instanceof JSHTMLElement"))
            self.assertEqual("BODY", ctxt.eval("(new Element()).tagName"))
            self.assertEqual("html:node", ctxt.eval("(new Element()).describe()"))

    def testPrototypeMethodForwarding(self):
        # A Python class's instance methods must also be reachable on the JS
        # *prototype* object (Class.prototype.method), not only on instances. V8
        # routes only instance access through the NamedPropertyHandler and never
        # promotes those names to prototype own properties, so without exposing
        # them `Class.prototype.method` is `undefined` and SDK code doing
        # `Class.prototype.method.apply(instance, args)` throws
        # "Cannot read properties of undefined (reading 'apply')". (Still true on
        # V8 13: static-attribute exposure was fixed, instance-method-on-prototype
        # was not -- see PopulatePrototypeMethods in src/Wrapper.cpp.)
        dom = _make_dom()

        class Global(STPyV8.JSClass):
            def __init__(self):
                self.Element = dom["Element"]
                self.JSNode = dom["JSNode"]

        with STPyV8.JSContext(Global()) as ctxt:
            # The method is a real own property on the prototype object.
            self.assertEqual("function", ctxt.eval("typeof Element.prototype.describe"))
            self.assertEqual("function", ctxt.eval("typeof JSNode.prototype.describe"))
            self.assertTrue(ctxt.eval("'describe' in Element.prototype"))
            # The prototype query interceptor reports forwarded methods as present,
            # so hasOwnProperty('describe') is true -- matching real JS, where a
            # prototype method is an own property of the prototype object.
            self.assertTrue(ctxt.eval("Element.prototype.hasOwnProperty('describe')"))
            # SDK pattern: grab the prototype method and apply/call it to an
            # instance; `self` is bound from the receiver at call time.
            self.assertEqual(
                "html:node",
                ctxt.eval("Element.prototype.describe.apply(new Element(), [])"),
            )
            self.assertEqual(
                "html:node",
                ctxt.eval("Element.prototype.describe.call(new Element())"),
            )
            # An inherited method is forwarded on the base class's prototype too.
            self.assertEqual(
                "node:node",
                ctxt.eval("JSNode.prototype.describe.apply(new JSNode(), [])"),
            )
            # The ordinary instance path is unchanged (no regression).
            self.assertEqual("html:node", ctxt.eval("(new Element()).describe()"))
            # constructor is NOT shadowed; a missing name still resolves to
            # undefined.
            self.assertTrue(ctxt.eval("Element.prototype.constructor === Element"))
            self.assertTrue(ctxt.eval("Element.prototype.nope === undefined"))

    def testInstallPropertyAccessors(self):
        # A Python class's @property descriptors must be installable as REAL
        # accessor pairs on the JS prototype, so that
        # Object.getOwnPropertyDescriptor(Cls.prototype, "scrollTop").set is a
        # callable -- the scroll-behavior-polyfill pattern. The C++ interceptors
        # answer instance access only, so without install_property_accessors
        # that descriptor is `undefined`.
        class Widget(STPyV8.JSClass):
            def __init__(self):
                self._top = 0
                self._left = 0
                self._size = 0

            @property
            def scrollTop(self):
                return self._top

            @scrollTop.setter
            def scrollTop(self, value):
                self._top = value

            @property
            def scrollLeft(self):
                return self._left

            @scrollLeft.setter
            def scrollLeft(self, value):
                self._left = value

            @property
            def size(self):  # read-only: getter only
                return self._size

        class Global(STPyV8.JSClass):
            def __init__(self):
                self.Widget = Widget

        with STPyV8.JSContext(Global()) as ctxt:
            # Before: no descriptor at all on the prototype.
            self.assertTrue(
                ctxt.eval(
                    "Object.getOwnPropertyDescriptor(Widget.prototype, 'scrollTop') === undefined"
                )
            )

            installed = STPyV8.install_property_accessors(
                ctxt, Widget, ["scrollTop", "scrollLeft", "size"]
            )
            self.assertEqual(["scrollTop", "scrollLeft", "size"], installed)

            # The descriptor now exists with real accessors.
            self.assertEqual(
                "get:function set:function",
                ctxt.eval(
                    "(function () {"
                    " var d = Object.getOwnPropertyDescriptor(Widget.prototype, 'scrollTop');"
                    " return 'get:' + typeof d.get + ' set:' + typeof d.set;"
                    "})()"
                ),
            )
            # A read-only property gets a getter but no setter.
            self.assertEqual(
                "get:function set:undefined",
                ctxt.eval(
                    "(function () {"
                    " var d = Object.getOwnPropertyDescriptor(Widget.prototype, 'size');"
                    " return 'get:' + typeof d.get + ' set:' + typeof d.set;"
                    "})()"
                ),
            )
            # Enumerable + configurable, like the real DOM.
            self.assertTrue(ctxt.eval("'scrollTop' in Widget.prototype"))
            self.assertTrue(
                ctxt.eval("Object.getOwnPropertyNames(Widget.prototype).indexOf('scrollTop') >= 0")
            )
            self.assertTrue(
                ctxt.eval(
                    "Object.getOwnPropertyDescriptor(Widget.prototype, 'scrollTop').configurable"
                )
            )

            # Instance reads/writes run the Python fget/fset.
            self.assertEqual(0, ctxt.eval("(new Widget()).scrollTop"))
            ctxt.eval("var w = new Widget(); w.scrollTop = 7; w.scrollLeft = 9;")
            self.assertEqual(7, ctxt.eval("w.scrollTop"))
            self.assertEqual(9, ctxt.eval("w.scrollLeft"))
            self.assertEqual(7, ctxt.eval("w._top"))
            self.assertEqual(9, ctxt.eval("w._left"))
            self.assertEqual(0, ctxt.eval("w.size"))

            # A non-instance receiver is rejected instead of recursing.
            self.assertTrue(
                ctxt.eval(
                    "(function () {"
                    " try { Widget.prototype.scrollTop; return false; }"
                    " catch (e) { return e instanceof TypeError; }"
                    "})()"
                )
            )

            # The polyfill pattern: grab the descriptor setter and wrap it.
            ctxt.eval("var x;")
            self.assertEqual(
                55,
                ctxt.eval(
                    "(function () {"
                    " var d = Object.getOwnPropertyDescriptor(Widget.prototype, 'scrollTop');"
                    " if (!d || !d.set) { throw new Error('descriptor.set missing'); }"
                    " var orig = d.set;"
                    " Object.defineProperty(Widget.prototype, 'scrollTop', {"
                    "   configurable: true, enumerable: true,"
                    "   get: function () { return this._top; },"
                    "   set: function (v) { orig.call(this, v); }"
                    " });"
                    " x = new Widget();"
                    " x.scrollTop = 55;"
                    " return x.scrollTop;"
                    "})()"
                ),
            )
            # ... and the write really reached the Python object.
            self.assertEqual(55, ctxt.eval("x._top"))

    def testInstallPropertyAccessorsDefaultsAndErrors(self):
        class Base(STPyV8.JSClass):
            @property
            def hidden(self):
                return "base-hidden"

        class Widget(Base):
            def __init__(self):
                self._top = 0

            @property
            def scrollTop(self):
                return self._top

            @scrollTop.setter
            def scrollTop(self, value):
                self._top = value

            @property
            def secret(self):
                return "nope"

        Widget.__js_hidden__ = ("secret",)

        class Global(STPyV8.JSClass):
            def __init__(self):
                self.Widget = Widget

        with STPyV8.JSContext(Global()) as ctxt:
            # No names -> every @property along the MRO, minus dunders and
            # __js_hidden__ ones.
            installed = STPyV8.install_property_accessors(ctxt, Widget)
            self.assertEqual(["scrollTop", "hidden"], installed)

            self.assertEqual("base-hidden", ctxt.eval("(new Widget()).hidden"))
            # A hidden name is not installed (it stays invisible to JS).
            self.assertFalse(
                ctxt.eval("'secret' in Object.getOwnPropertyNames(Widget.prototype)")
            )
            self.assertTrue(ctxt.eval("(new Widget()).secret === undefined"))

        # Bad inputs are rejected up front, not at access time.
        with STPyV8.JSContext(Global()) as ctxt:
            with self.assertRaises(ValueError):
                STPyV8.install_property_accessors(ctxt, Widget, ["notAProperty"])
            with self.assertRaises(RuntimeError):
                STPyV8.install_property_accessors(ctxt, Widget, js_name="NoSuchName")
            with self.assertRaises(TypeError):
                STPyV8.install_property_accessors(object(), Widget)

        # Outside `with context:` there is no context to install into.
        unentered = STPyV8.JSContext.__new__(STPyV8.JSContext)
        with self.assertRaises(RuntimeError):
            STPyV8.install_property_accessors(unentered, Widget)

    def testAutoPrototypeMembers(self):
        # Opt-in automation: with STPyV8.auto_prototype_members = True, entering a
        # context puts the shared members of every class its global object
        # exposes onto that class's JS prototype -- @property accessors and class
        # constants, on top of the instance methods that are always there.
        class Widget(STPyV8.JSClass):
            UNSENT = 0          # class constant -> prototype + constructor
            nodeKind = "widget"

            def __init__(self):
                self._top = 0
                self._secret = 0

            def method(self):    # instance method -> prototype (always)
                return "method"

            @classmethod
            def make(cls):
                return "made"

            @staticmethod
            def util():
                return "util"

            @property
            def scrollTop(self):
                return self._top

            @scrollTop.setter
            def scrollTop(self, value):
                self._top = value

            @property
            def secret(self):
                return self._secret

        Widget.__js_hidden__ = ("secret",)

        class Private(STPyV8.JSClass):
            __js_no_properties__ = True  # opt out entirely

            UNSENT = 7

            def __init__(self):
                self._x = 0

            @property
            def value(self):
                return self._x

        class Plain(STPyV8.JSClass):
            pass  # nothing to install

        class Global(STPyV8.JSClass):
            def __init__(self):
                self.Widget = Widget
                self.Private = Private
                self.Plain = Plain
                self.notAClass = 42

        # Default: off -- nothing is installed behind the user's back.
        with STPyV8.JSContext(Global()) as ctxt:
            self.assertTrue(
                ctxt.eval(
                    "Object.getOwnPropertyDescriptor(Widget.prototype, 'scrollTop') === undefined"
                )
            )
            self.assertTrue(ctxt.eval("Widget.prototype.UNSENT === undefined"))

        previous = STPyV8.auto_prototype_members
        STPyV8.auto_prototype_members = True
        try:
            with STPyV8.JSContext(Global()) as ctxt:
                # 1) instance methods: on the prototype, unconditionally.
                self.assertEqual("function", ctxt.eval("typeof Widget.prototype.method"))

                # 2) @property accessors, installed on entry without a call.
                self.assertEqual(
                    "get:function set:function",
                    ctxt.eval(
                        "(function () {"
                        " var d = Object.getOwnPropertyDescriptor(Widget.prototype, 'scrollTop');"
                        " return 'get:' + typeof d.get + ' set:' + typeof d.set;"
                        "})()"
                    ),
                )
                ctxt.eval("var w = new Widget(); w.scrollTop = 5;")
                self.assertEqual(5, ctxt.eval("w.scrollTop"))
                self.assertEqual(5, ctxt.eval("w._top"))

                # 3) class constants mirrored onto the prototype...
                self.assertEqual(0, ctxt.eval("Widget.prototype.UNSENT"))
                self.assertEqual("widget", ctxt.eval("Widget.prototype.nodeKind"))
                self.assertEqual(0, ctxt.eval("(new Widget()).UNSENT"))
                # ... and still on the constructor (both places, like the DOM).
                self.assertEqual(0, ctxt.eval("Widget.UNSENT"))
                # Non-enumerable, so Object.keys()/for..in stay clean.
                self.assertFalse(
                    ctxt.eval("Object.keys(Widget.prototype).indexOf('UNSENT') >= 0")
                )
                self.assertTrue(ctxt.eval("'UNSENT' in Widget.prototype"))

                # Statics stay on the constructor only -- never on the prototype.
                self.assertEqual("function", ctxt.eval("typeof Widget.make"))
                self.assertEqual("function", ctxt.eval("typeof Widget.util"))
                self.assertTrue(ctxt.eval("Widget.prototype.make === undefined"))
                self.assertTrue(ctxt.eval("Widget.prototype.util === undefined"))
                self.assertFalse(ctxt.eval("'util' in Widget.prototype"))

                # __js_hidden__ names are never installed.
                self.assertTrue(ctxt.eval("(new Widget()).secret === undefined"))
                self.assertFalse(
                    ctxt.eval(
                        "Object.getOwnPropertyNames(Widget.prototype).indexOf('secret') >= 0"
                    )
                )

                # __js_no_properties__ opts the class out entirely.
                self.assertTrue(
                    ctxt.eval(
                        "Object.getOwnPropertyDescriptor(Private.prototype, 'value') === undefined"
                    )
                )
                self.assertTrue(ctxt.eval("Private.prototype.UNSENT === undefined"))

                # A class with nothing to install is simply skipped.
                self.assertEqual("function", ctxt.eval("typeof Plain"))
                self.assertEqual("undefined", ctxt.eval("typeof Plain.prototype.nope"))

                # A non-class attribute is ignored.
                self.assertEqual(42, ctxt.eval("notAClass"))

            # Classes mounted AFTER entry still need the explicit calls.
            with STPyV8.JSContext() as ctxt:
                ctxt.locals.Late = Widget
                self.assertTrue(
                    ctxt.eval(
                        "Object.getOwnPropertyDescriptor(Late.prototype, 'scrollTop') === undefined"
                    )
                )
                self.assertEqual(
                    ["scrollTop"],
                    STPyV8.install_property_accessors(ctxt, Widget, js_name="Late"),
                )
                self.assertEqual(
                    ["UNSENT", "nodeKind"],
                    STPyV8.install_class_attributes(ctxt, Widget, js_name="Late"),
                )
                self.assertEqual(0, ctxt.eval("Late.prototype.UNSENT"))
                # The descriptor exists (reading it on the prototype object
                # itself is an illegal invocation, as in real engines -- so check
                # the descriptor, not a direct read).
                self.assertEqual(
                    "function",
                    ctxt.eval(
                        "typeof Object.getOwnPropertyDescriptor(Late.prototype, 'scrollTop').get"
                    ),
                )
                self.assertEqual(0, ctxt.eval("(new Late()).scrollTop"))
        finally:
            STPyV8.auto_prototype_members = previous

    def testInstallClassAttributes(self):
        # Manual counterpart of the automatic half: mirror a class's constants
        # onto its prototype (readable there and on the constructor, as with
        # Node.ELEMENT_NODE / Node.prototype.ELEMENT_NODE in browsers).
        class Base(STPyV8.JSClass):
            SHARED = "base"

        class Widget(Base):
            UNSENT = 0
            nodeKind = "widget"
            ratio = 1.5
            flag = True
            nothing = None

            def __init__(self):
                self._top = 0

            @property
            def scrollTop(self):
                return self._top

            @scrollTop.setter
            def scrollTop(self, value):
                self._top = value

            def method(self):
                return "method"

            @staticmethod
            def util():
                return "util"

        class Global(STPyV8.JSClass):
            def __init__(self):
                self.Widget = Widget

        with STPyV8.JSContext(Global()) as ctxt:
            self.assertEqual(
                ["UNSENT", "nodeKind", "ratio", "flag", "nothing", "SHARED"],
                STPyV8.install_class_attributes(ctxt, Widget),
            )

            for expression, expected in (
                ("Widget.prototype.UNSENT", 0),
                ("Widget.prototype.nodeKind", "widget"),
                ("Widget.prototype.ratio", 1.5),
                ("Widget.prototype.flag", True),
                ("Widget.prototype.nothing", None),
                ("Widget.prototype.SHARED", "base"),
                ("Widget.UNSENT", 0),
                ("(new Widget()).nodeKind", "widget"),
            ):
                self.assertEqual(expected, ctxt.eval(expression), expression)

            # Non-constants are refused, and never silently skipped.
            with self.assertRaises(ValueError):
                STPyV8.install_class_attributes(ctxt, Widget, ["method"])
            with self.assertRaises(ValueError):
                STPyV8.install_class_attributes(ctxt, Widget, ["scrollTop"])
            with self.assertRaises(ValueError):
                STPyV8.install_class_attributes(ctxt, Widget, ["util"])
            with self.assertRaises(ValueError):
                STPyV8.install_class_attributes(ctxt, Widget, ["noSuchName"])

            # The mirrored constants are read-only: an assignment through an
            # instance is ignored (sloppy mode), so the value stays consistent
            # everywhere -- unlike a writable prototype property, where the write
            # would land on the Python object while reads still resolved to the
            # prototype value.
            ctxt.eval("var w = new Widget(); w.UNSENT = 99;")
            self.assertEqual(0, ctxt.eval("w.UNSENT"))
            self.assertEqual(0, ctxt.eval("Widget.prototype.UNSENT"))
            self.assertEqual(0, ctxt.eval("(new Widget()).UNSENT"))
            self.assertTrue(
                ctxt.eval(
                    "!Object.getOwnPropertyDescriptor(Widget.prototype, 'UNSENT').writable"
                )
            )

    def testClassHierarchyCallable(self):
        # `typeof` is "function" only for classes that define __call__; a plain
        # object reports "object" and is not callable in JS (standard semantics).
        class Callable_(STPyV8.JSClass):
            def __call__(self, name):  # pylint:disable=no-self-use
                return "hi " + name

        class Plain(STPyV8.JSClass):
            pass

        class Global(STPyV8.JSClass):
            def __init__(self):
                self.c = Callable_()
                self.o = Plain()

        with STPyV8.JSContext(Global()) as ctxt:
            self.assertEqual("function", ctxt.eval("typeof c"))
            self.assertEqual("object", ctxt.eval("typeof o"))
            self.assertEqual("hi world", ctxt.eval("c('world')"))
            self.assertTrue(
                ctxt.eval(
                    "(function(){try{o();return false}"
                    "catch(e){return e instanceof TypeError}})()"
                )
            )

    def testClassStaticProperties(self):
        # Python class / static attributes must be visible as own properties on
        # the exposed constructor function, so `XMLHttpRequest.UNSENT` style
        # access works without manually mounting them in JS.
        dom = _make_dom()

        class Global(STPyV8.JSClass):
            def __init__(self):
                self.Element = dom["Element"]
                self.JSNode = dom["JSNode"]

        with STPyV8.JSContext(Global()) as ctxt:
            # Class attributes exposed as static properties.
            self.assertEqual(0, ctxt.eval("Element.UNSENT"))
            self.assertEqual(1, ctxt.eval("Element.OPENED"))
            self.assertEqual("node", ctxt.eval("Element.nodeKind"))
            # Inherited from the base class through the Python MRO.
            self.assertEqual(0, ctxt.eval("JSNode.UNSENT"))
            # A classmethod is exposed as a static callable.
            self.assertEqual("function", ctxt.eval("typeof Element.make"))
            # Native function properties are not clobbered.
            self.assertEqual("Element", ctxt.eval("Element.name"))
            self.assertEqual("function", ctxt.eval("typeof Element"))
            # It is a genuine own property of the constructor.
            self.assertTrue(ctxt.eval("Object.getOwnPropertyNames(Element).indexOf('UNSENT') >= 0"))

    def testDeleteWithNonStringKey(self):
        # CPythonObject::NamedDeleter used to forward a nullptr name to
        # PyObject_HasAttrString whenever the key could not be converted to a
        # UTF-8 string, which crashed with an access violation reading 0x0
        # inside python312.dll (it was the only interceptor missing the guard
        # that NamedGetter/NamedSetter/NamedQuery all have).
        class Obj(STPyV8.JSClass):
            def __init__(self):
                self.x = 1

        class Global(STPyV8.JSClass):
            def __init__(self):
                self.o = Obj()

        with STPyV8.JSContext(Global()) as ctxt:
            # A normal string key still deletes the Python attribute.
            self.assertEqual(1, ctxt.eval("o.x"))
            ctxt.eval("delete o.x")
            self.assertTrue(ctxt.eval("o.x") is None)

            # Symbol keys cannot be forwarded to Python by name: must be a
            # no-op, not a crash.
            self.assertTrue(ctxt.eval("delete o[Symbol('nope')]"))

            # A key V8 cannot encode as UTF-8 must not crash either; it may
            # surface as a JS ReferenceError, but never a native crash.
            try:
                ctxt.eval("delete o['\\uD800']")
            except ReferenceError:
                pass
