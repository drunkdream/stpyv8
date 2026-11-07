"""异常路径：反复抛出/捕获 JS 异常时不应累积包装对象。

``CPythonObject::ThrowException`` 会把 ``type`` / ``value`` 包装成
``new py::object`` 挂到 error 对象的 private 属性上，由
``ExceptionTranslator::Translate`` 读取并释放。此用例在**单个** context 内
循环抛错，隔离出异常路径自身的内存行为，避免混入反复创建 context 的 V8
堆开销（那是 ``test_context_memory.py`` 的职责）。
"""

import gc

import pytest

import STPyV8 as v8

_EXCEPTION_ITERATIONS = 500
_MAX_EXCEPTION_RSS_GROWTH_MB = 8


def _rss_mb():
    """当前进程常驻内存（MB）；非 Linux 返回 -1，用例会跳过。"""
    try:
        with open("/proc/self/status") as fp:
            for line in fp:
                if line.startswith("VmRSS"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return -1


def _throw_and_catch(ctx):
    try:
        ctx.eval('throw new Error("boom");')
    except Exception:
        return True
    return False


def test_repeated_exceptions_are_memory_stable():
    baseline = _rss_mb()
    if baseline < 0:
        pytest.skip("需要 /proc/self/status 读取 RSS")

    caught = 0

    with v8.JSContext() as ctx:
        # 预热，排除首次分配与异常类型初始化的固定开销
        for _ in range(10):
            caught += _throw_and_catch(ctx)

        gc.collect()
        v8.JSIsolate.current.lowMemory()
        baseline = _rss_mb()

        for _ in range(_EXCEPTION_ITERATIONS):
            caught += _throw_and_catch(ctx)

        gc.collect()
        v8.JSIsolate.current.lowMemory()
        growth = _rss_mb() - baseline

    assert caught == _EXCEPTION_ITERATIONS + 10, "异常未被抛出，用例无效"
    assert growth <= _MAX_EXCEPTION_RSS_GROWTH_MB, (
        "%d 次异常后 RSS 增长 %d MB（上限 %d MB），异常包装对象未释放"
        % (_EXCEPTION_ITERATIONS, growth, _MAX_EXCEPTION_RSS_GROWTH_MB))
