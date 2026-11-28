"""``JSIsolate`` 销毁顺序回归：context 允许比创建它的 isolate 活得更久。

Python 的 ``with`` 临时对象先析构，而 ``JSContext`` 局部变量往往要等到
frame 清理才释放，所以 ``v8::Isolate::Dispose()`` 可能早于
``~CContext``。修复前，后者会对已失效的 ``Persistent<Context>`` 调用
``Reset()``，在 ``GlobalHandles::NodeSpace::Release`` 里段错误。

本用例显式构造该顺序，守护 ``~CContext`` 在 isolate 注销后不再触碰 V8 API。
"""

import gc

import STPyV8 as v8


def test_context_outlives_disposed_isolate():
    held = []

    with v8.JSIsolate(True):
        ctx = v8.JSContext()
        ctx.enter()
        try:
            assert ctx.eval("1 + 1") == 2
        finally:
            ctx.leave()
        held.append(ctx)

    # 到这里 ~CIsolate 已经 Forget + Dispose，但 ctx 仍被 held 引用着。
    # 让它在 isolate 销毁之后才析构，正是原先崩溃的触发条件。
    gc.collect()
    del ctx

    held.clear()
    gc.collect()

    assert not held
