"""P2: 异常字符串总长应有上限，避免超大 message 撑爆 V8 堆。

历史：``CJavascriptException::Extract`` 对 message / URL / 源行没有总长限制，
一个 20 万字符 ``Error`` message 会原样拼进异常字符串，循环抛错时足以 OOM
V8 堆。现对 message 与整体字符串都设上限（约 4KB）。
"""

import pytest

import STPyV8 as v8

# 异常消息总长上限（P2 修复后约为 4KB，含 Python 侧 "JSError: " 前缀余量）。
_MAX_EXCEPTION_CHARS = 4096


def test_exception_message_has_length_cap():
    ctx = v8.JSContext()
    ctx.enter()
    try:
        with pytest.raises(Exception) as excinfo:
            # 20 万字符的 message，Extract 会原样拼进异常字符串
            ctx.eval('throw new Error("x".repeat(200000));')
    finally:
        ctx.leave()

    length = len(str(excinfo.value))
    assert length <= _MAX_EXCEPTION_CHARS, (
        "异常字符串长度 %d 超过上限 %d" % (length, _MAX_EXCEPTION_CHARS))
