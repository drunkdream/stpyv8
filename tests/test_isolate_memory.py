"""反复创建/销毁 ``JSIsolate`` 时内存不应累积。

每个 isolate 拥有一份 ObjectTemplate / FunctionTemplate 缓存，以及挂在 V8
External 上的 Python 对象（``py::object(cls)``、``PrototypeAccessorData``）。
这些资源由 per-isolate 注册表持有，并在 ``v8::Isolate::Dispose()`` **之前**
释放；否则 Reset 一个已失效的 Persistent 会崩溃，反复创建 isolate 也会不断
累积内存。

这里用 ``JSIsolate(True)``（owner 模式），确保 ``~CIsolate`` 真的 Dispose。
"""

import gc
import sys

import pytest

import STPyV8 as v8

_ISOLATE_ITERATIONS = 30
_MAX_ISOLATE_RSS_GROWTH_MB = 16
_WARMUP_ITERATIONS = 3


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


class _Demo(v8.JSClass):
    def ping(self):
        return "pong"


def _one_isolate():
    with v8.JSIsolate(True):
        with v8.JSContext() as ctx:
            ctx.locals.Demo = _Demo
            assert ctx.eval("new Demo().ping()") == "pong"


def test_repeated_isolate_creation_is_memory_stable():
    if _rss_mb() < 0:
        pytest.skip("需要 /proc/self/status 读取 RSS")

    for _ in range(_WARMUP_ITERATIONS):
        _one_isolate()

    gc.collect()
    baseline = _rss_mb()

    for _ in range(_ISOLATE_ITERATIONS):
        _one_isolate()

    gc.collect()
    growth = _rss_mb() - baseline

    assert growth <= _MAX_ISOLATE_RSS_GROWTH_MB, (
        "%d 次 JSIsolate 创建后 RSS 增长 %d MB（上限 %d MB），模板缓存/isolate 未释放"
        % (_ISOLATE_ITERATIONS, growth, _MAX_ISOLATE_RSS_GROWTH_MB))


def test_isolate_teardown_releases_wrapped_python_objects():
    """确定性守护：per-isolate 缓存里的 Python 对象必须随 isolate 释放。

    ``ClassTemplateEntry``、``SetCallHandler`` 的 payload 和
    ``PrototypeAccessorData`` 都持有 ``py::object(cls)``；若 isolate 销毁时
    注册表未清理，每轮都会给类留下新的引用。用引用计数而非 RSS 判定，
    因此不依赖 Linux，也不受堆分配器缓存干扰。
    """
    for _ in range(_WARMUP_ITERATIONS):
        _one_isolate()

    gc.collect()
    baseline = sys.getrefcount(_Demo)

    for _ in range(_ISOLATE_ITERATIONS):
        _one_isolate()

    gc.collect()
    after = sys.getrefcount(_Demo)

    assert after <= baseline + 1, (
        "%d 次 isolate 创建后类引用计数 %d -> %d，缓存中的 py::object 未释放"
        % (_ISOLATE_ITERATIONS, baseline, after))
