"""JSClass 对象必须暴露 ``Symbol.toStringTag``（回归验收）。

背景
----
Web API 对象（由 STPyV8 的 JSClass 暴露给 V8）在真实浏览器里，
``Object.prototype.toString.call(el)`` 返回 ``[object HTMLDivElement]``，
因为它沿原型链能读到 ``Symbol.toStringTag``。STPyV8 当前返回
``[object Object]``：Vue 3 的 ``toRawType()`` 因此把 DOM 元素误判为普通
对象并做 ``reactive()`` -> Proxy 包装，而 JSClass 的方法/属性带 WebIDL
brand check，经 Proxy receiver 调用一律抛 ``TypeError: Illegal invocation``
（pybrowser 的 v.qq.com 实测每页 14~15 次，涉及 getBoundingClientRect /
clientWidth / style，链接数也少 24 个）。

修法边界
--------
推荐实现：在 C++ 类模板层给 JSClass **原型**安装 ``Symbol.toStringTag``
accessor（返回该 Python 类的 JS 名），让 V8 内置的
``Object.prototype.toString`` 自然产出正确标签。

**不要**通过放宽 brand check 来「允许 Proxy 调用」：真实浏览器对
``new Proxy(el, {}).getBoundingClientRect()`` 同样抛 Illegal invocation。
``test_brand_check_still_enforced_for_proxy_receiver`` 守护这一点；
``test_object_prototype_to_string_remains_native`` 则阻止用「在 JS 里覆盖
Object.prototype.toString」这种应用层 workaround 假装通过。

实现提示（实测 13.1.201.22）
~~~~~~~~~~~~~~~~~~~~~~~~~~~
光在 JS 侧给原型装 symbol **不生效**::

    var p = Object.getPrototypeOf(el);
    Object.defineProperty(p, Symbol.toStringTag,
                          {get: function(){ return 'HTMLDivElement'; }});
    p[Symbol.toStringTag]                       // 'HTMLDivElement'  <- 原型可读
    el[Symbol.toStringTag]                      // undefined         <- 实例读不到
    Object.prototype.toString.call(el)          // '[object Object]' <- 内置仍看不到

命名属性拦截器拦下了实例上的属性查询，symbol key 无法穿透到原型链。因此
修复必须落在 C++ 拦截器层：对 ``Symbol.toStringTag`` 放行（或由拦截器直接
返回该 JSClass 的接口名）。

状态
----
4 个 tag 用例曾以 ``xfail(strict=True)`` 标记；命名拦截器放行 Symbol key
并在类模板原型安装 @@toStringTag 后已全部转正为普通断言。
"""

import STPyV8 as v8


class HTMLDivElement(v8.JSClass):
    """模拟一个由 STPyV8 暴露的 DOM 元素类。"""

    def getBoundingClientRect(self):
        return {"width": 0, "height": 0}


class HTMLSpanElement(HTMLDivElement):
    """子类：tag 必须是子类名，而不是父类名。"""


def _new_ctx():
    ctx = v8.JSContext()
    ctx.enter()
    ctx.locals.div = HTMLDivElement()
    ctx.locals.span = HTMLSpanElement()
    return ctx


def _eval(expr):
    ctx = _new_ctx()
    try:
        return ctx.eval(expr)
    finally:
        ctx.leave()


def test_jsclass_to_string_tag():
    """核心：内置 Object.prototype.toString 必须返回 DOM 接口名。"""
    got = _eval("String(Object.prototype.toString.call(div))")
    assert got == "[object HTMLDivElement]", (
        "JSClass 实例被 toRawType() 误判为普通 Object，"
        "Vue 会把它 Proxy 化并触发 Illegal invocation: %r" % got)


def test_symbol_to_string_tag_is_readable():
    """``el[Symbol.toStringTag]`` 应返回接口名（真实 DOM 行为）。"""
    got = _eval("String(div[Symbol.toStringTag])")
    assert got == "HTMLDivElement", (
        "实例上读不到 Symbol.toStringTag: %r" % got)


def test_to_string_tag_lives_on_prototype():
    """tag 应在原型链上可读，而不是每个实例各持一份 own symbol。"""
    got = _eval("String(Object.getPrototypeOf(div)[Symbol.toStringTag])")
    assert got == "HTMLDivElement", (
        "JSClass 原型上没有 Symbol.toStringTag: %r" % got)


def test_subclass_tag_uses_subclass_name():
    got = _eval("String(Object.prototype.toString.call(span))")
    assert got == "[object HTMLSpanElement]", (
        "子类沿用了父类的 tag: %r" % got)


def test_object_prototype_to_string_remains_native():
    """防止用「JS 覆盖 Object.prototype.toString」的 workaround 假装修复。"""
    got = _eval("String(Object.prototype.toString)")
    assert "[native code]" in got, (
        "Object.prototype.toString 必须是 V8 内置实现: %r" % got)


def test_builtin_and_plain_objects_unchanged():
    """修 tag 不能破坏其它类型，也不能给普通 JS 对象乱贴标签。"""
    expected = {
        "Object.prototype.toString.call([1])": "[object Array]",
        "Object.prototype.toString.call(new Date(0))": "[object Date]",
        "Object.prototype.toString.call(/x/)": "[object RegExp]",
        "Object.prototype.toString.call(null)": "[object Null]",
        "Object.prototype.toString.call(undefined)": "[object Undefined]",
        "Object.prototype.toString.call(function(){})": "[object Function]",
        "Object.prototype.toString.call({})": "[object Object]",
    }
    for expr, want in expected.items():
        got = _eval("String(%s)" % expr)
        assert got == want, "%s 应为 %r，实际 %r" % (expr, want, got)

    got = _eval("String(Object.prototype.toString.call("
                "new (function HTMLFoo(){})()))")
    assert got == "[object Object]", (
        "用户自定义 JS 类被误贴成 DOM 接口标签: %r" % got)


def test_brand_check_still_enforced_for_proxy_receiver():
    """守护正确修法：brand check 不可放宽。

    直接调用正常；Proxy receiver 调用必须继续抛 Illegal invocation，
    与真实浏览器 / WebIDL 一致。若有人试图靠「允许 Proxy 调用」来修
    v.qq.com 的问题，这个用例会拦下。
    """
    direct = _eval("String(div.getBoundingClientRect().width)")
    assert direct == "0", "直接调用应正常: %r" % direct

    proxied = _eval(
        "(function(){try{"
        "  new Proxy(div,{}).getBoundingClientRect();"
        "  return 'NO_ERROR';"
        "}catch(e){return e.name+': '+e.message}})()")
    assert proxied == "TypeError: Illegal invocation", (
        "Proxy receiver 调用应继续抛 Illegal invocation: %r" % proxied)
