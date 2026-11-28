"""JSClass 暴露为 JS 对象时的「数组 / 迭代 / 构造器」协议（回归验收）。

背景
----
pybrowser 把 DOM 集合类（``NamedNodeMap`` / ``DOMStringList`` / ``NodeList`` …）
以 ``JSClass`` 形式暴露给 JS。真实浏览器里这些是「有 ``length`` + 数值下标 +
可迭代」的类数组对象，站点大量使用 ``obj[i]``、``[...obj]``、``for…of``、
``Array.from(obj)``。

历史
----
本文件最初用于登记 4 个 STPyV8 缺口（当时以 ``@unittest.expectedFailure``
标记）：

1. ``__iter__`` 未映射为 JS ``Symbol.iterator``：``[...obj]`` / ``for…of``
   抛 ``TypeError: obj is not iterable``；
2. Python ``list``（实例属性或类属性）透出到 JS 不是 JSArray：
   ``Array.isArray()`` 为 false、无 ``.length`` / ``.includes``；
3. 把 Python 类直接暴露为构造器时 ``Klass.toString()`` 抛
   ``missing 1 required positional argument: 'self'``；
4. 实例方法以「未绑定函数」泄漏到构造器上：``Klass.method()`` 崩，而 JS
   ``class`` 里 ``Klass.method`` 本应为 ``undefined``。

上述缺口已在 Wrapper 层修复，现全部转为普通断言以防止回退。
"""

import unittest

import STPyV8 as v8


class _Seq(v8.JSClass):
    """同时具备 ``__len__ + length 属性 + __getitem__`` 的类数组对象。"""

    def __init__(self, items):
        self._items = list(items)

    def __len__(self):
        return len(self._items)

    @property
    def length(self):
        return len(self._items)

    def __getitem__(self, i):
        return self._items[int(i)]


class _IterSeq(_Seq):
    """在 _Seq 基础上额外实现 ``__iter__``（应成为 JS 可迭代对象）。"""

    def __iter__(self):
        return iter(self._items)


class _HasList(v8.JSClass):
    """暴露 Python list（实例属性 + 类属性）给 JS。"""

    static_list = ["x", "y", "z"]

    def __init__(self):
        self.items = ["a", "b"]


class _Klass(v8.JSClass):
    """作为「全局构造器」暴露的 Python 类。"""

    def foo(self):
        return "bar"


class TestJSClassArrayProtocol(unittest.TestCase):
    # ---- 数值下标 + length + Array.from ----
    def test_numeric_index_and_length_are_supported(self):
        """``__len__`` + ``__getitem__`` 使 ``obj[i]`` / ``Array.from`` 可用。"""
        with v8.JSContext() as ctx:
            ctx.locals.S = _Seq(["a", "b", "c"])
            self.assertEqual("b", ctx.eval("S[1]"))
            self.assertEqual(3, ctx.eval("S.length"))
            self.assertEqual(3, ctx.eval("Array.from(S).length"))
            self.assertEqual(3, ctx.eval("Array.prototype.slice.call(S).length"))

    # ---- __iter__ 应成为 Symbol.iterator ----
    def test_instance_is_iterable_via_dunder_iter(self):
        """实现了 ``__iter__`` 的 JSClass 应可被展开 / ``for…of`` 迭代。"""
        with v8.JSContext() as ctx:
            ctx.locals.S = _IterSeq(["a", "b", "c"])
            self.assertEqual(3, ctx.eval("[...S].length"))
            self.assertEqual(
                3,
                ctx.eval(
                    "(function(){var c=0;for(var x of S)c++;return c;})()"
                ),
            )

    # ---- Python list 透出应是 JSArray ----
    def test_python_list_is_exposed_as_js_array(self):
        """实例属性 / 类属性里的 Python list 应成为真正的 JS 数组。"""
        with v8.JSContext() as ctx:
            ctx.locals.H = _HasList()
            self.assertTrue(ctx.eval("Array.isArray(H.items)"))
            self.assertEqual(2, ctx.eval("H.items.length"))
            self.assertTrue(ctx.eval("H.items.includes('a')"))
            self.assertTrue(ctx.eval("Array.isArray(H.static_list)"))
            self.assertEqual(3, ctx.eval("H.static_list.length"))

    # ---- 类级 toString 不应抛 missing self ----
    def test_class_constructor_to_string_does_not_raise(self):
        """``Klass.toString()`` 应返回字符串（Function.prototype.toString 语义）。"""
        with v8.JSContext() as ctx:
            ctx.locals.K = _Klass
            self.assertEqual("string", ctx.eval("typeof K.toString()"))

    # ---- 实例方法不应以未绑定形式泄漏到构造器 ----
    def test_instance_method_not_leaked_on_constructor(self):
        """JS ``class`` 里 ``Klass.foo`` 应为 undefined（只在 prototype 上）。"""
        with v8.JSContext() as ctx:
            ctx.locals.K = _Klass
            self.assertEqual("undefined", ctx.eval("typeof K.foo"))
            # 实例调用必须仍然正常
            self.assertEqual("bar", ctx.eval("new K().foo()"))


if __name__ == "__main__":
    unittest.main()
