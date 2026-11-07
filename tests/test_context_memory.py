"""P0: 循环创建 ``JSContext`` 时 RSS 不应线性增长。

每个 context 都会把包装过的 Python 对象（``new py::object``）记录到该 context
的 living map。现在 ``CContext`` 析构时会先同步释放这些对象，再触发一次 V8 GC
回收 context 本身，因此重复创建/销毁不再累积内存。
"""

import gc

import pytest

import STPyV8 as v8

# 当前实测：200 次 context 约 +36MB；修复后应接近 0，阈值留足余量。
_CTX_ITERATIONS = 200
_MAX_CTX_RSS_GROWTH_MB = 8


class _Demo(v8.JSClass):
    def ping(self):
        return "pong"


def _new_ctx():
    ctx = v8.JSContext()
    ctx.enter()
    ctx.locals.Demo = _Demo
    return ctx


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


def test_repeated_context_creation_is_memory_stable():
    baseline = _rss_mb()
    if baseline < 0:
        pytest.skip("需要 /proc/self/status 读取 RSS")

    # 预热，排除首次分配的固定开销
    for _ in range(5):
        ctx = _new_ctx()
        ctx.leave()

    gc.collect()
    v8.JSIsolate.current.lowMemory()
    baseline = _rss_mb()

    for _ in range(_CTX_ITERATIONS):
        ctx = _new_ctx()
        ctx.eval("new Demo().ping()")
        ctx.leave()
        del ctx

    gc.collect()
    v8.JSIsolate.current.lowMemory()
    growth = _rss_mb() - baseline

    assert growth <= _MAX_CTX_RSS_GROWTH_MB, (
        "%d 次 JSContext 创建后 RSS 增长 %d MB（上限 %d MB），存在内存泄漏"
        % (_CTX_ITERATIONS, growth, _MAX_CTX_RSS_GROWTH_MB))
