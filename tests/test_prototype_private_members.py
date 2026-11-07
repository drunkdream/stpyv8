"""P1: Python 私有成员（``_`` 开头）不应被装成 JS 原型上的可枚举成员。

历史：``PopulatePrototypeMethods`` 只跳过 ``__`` 开头，导致 ``_private_helper``
这类单下划线方法泄漏到每个 JSClass 原型上，从 JS 侧
``Object.getOwnPropertyNames`` / ``for..in`` 可枚举。现改为跳过任何
``_`` 开头的名字。
"""

import STPyV8 as v8


class _Demo(v8.JSClass):
    """普通类：一个方法、一个 @property、一个 Python 私有方法。"""

    def ping(self):
        return "pong"

    @property
    def label(self):
        return "demo"

    def _private_helper(self):
        return "secret"


def _new_ctx():
    ctx = v8.JSContext()
    ctx.enter()
    ctx.locals.Demo = _Demo
    return ctx


def test_private_members_not_exposed_on_prototype():
    ctx = _new_ctx()
    try:
        names = ctx.eval(
            "JSON.stringify(Object.getOwnPropertyNames(Demo.prototype))")
    finally:
        ctx.leave()

    assert "_private_helper" not in names, (
        "Python 私有方法 _private_helper 出现在 JS 原型上: %s" % names)
