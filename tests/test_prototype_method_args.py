"""P3: 原型方法应接受 > 10 个参数，不再抛 ``too many arguments``。

历史：``PrototypeMethod`` 用 ``BOOST_PP_FOR((0, 10))`` 把参数个数硬上限锁在
10，第 11 个参数抛 ``too many arguments``，破坏 ``on(...)`` 等变长调用。
现改为按 ``info.Length()`` 变长转发（``PyObject_CallObject`` 无此限制）。
"""

import STPyV8 as v8

# 原型方法参数个数（P3 修复前上限为 10，这里验证 11 个可正常传入）。
_MANY_ARGS_COUNT = 11


class _ManyArgs(v8.JSClass):
    def count(self, *args):
        return len(args)


def _new_ctx():
    ctx = v8.JSContext()
    ctx.enter()
    ctx.locals.ManyArgs = _ManyArgs
    return ctx


def test_prototype_method_accepts_more_than_ten_args():
    ctx = _new_ctx()
    try:
        args = ",".join(["1"] * _MANY_ARGS_COUNT)
        result = ctx.eval("new ManyArgs().count(%s)" % args)
    finally:
        ctx.leave()

    assert result == _MANY_ARGS_COUNT, (
        "%d 个参数应全部传入，实际得到 %r" % (_MANY_ARGS_COUNT, result))
