#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import with_statement
from __future__ import print_function

import os
import sys
import re
import json
import collections.abc

import _STPyV8

__version__ = _STPyV8.JSEngine.version

__all__ = [
    "ReadOnly",
    "DontEnum",
    "DontDelete",
    "Internal",
    "JSError",
    "JSObject",
    "JSNull",
    "JSUndefined",
    "JSArray",
    "JSFunction",
    "JSClass",
    "JSEngine",
    "JSContext",
    "JSIsolate",
    "JSStackTrace",
    "JSStackFrame",
    "JSScript",
    "JSLocker",
    "JSUnlocker",
    "JSPlatform",
    "install_property_accessors",
    "auto_property_accessors",
]


# ICU
ICU_DATA_FOLDERS_UNIX = (
    "/usr/share/stpyv8",
    os.path.expanduser("~/.local/share/stpyv8"),
)
ICU_DATA_FOLDERS_OSX = (
    "/Library/Application Support/STPyV8",
    os.path.expanduser("~/Library/Application Support/STPyV8"),
)
ICU_DATA_FOLDERS_WINDOWS = (
    os.path.join(os.environ["PROGRAMDATA"], "STPyV8")
    if "PROGRAMDATA" in os.environ
    else None,
    os.path.join(os.environ["APPDATA"], "STPyV8") if "APPDATA" in os.environ else None,
)

icu_data_folders = None
if os.name in ("posix",):
    icu_data_folders = (
        ICU_DATA_FOLDERS_OSX if sys.platform in ("darwin",) else ICU_DATA_FOLDERS_UNIX
    )
else:
    icu_data_folders = ICU_DATA_FOLDERS_WINDOWS


class JSAttribute:
    def __init__(self, name):
        self.name = name

    def __call__(self, func):
        setattr(func, f"__{self.name}__", True)

        return func


ReadOnly = JSAttribute(name="readonly")
DontEnum = JSAttribute(name="dontenum")
DontDelete = JSAttribute(name="dontdel")
Internal = JSAttribute(name="internal")


class JSError(Exception):
    def __init__(self, impl):
        Exception.__init__(self)
        self._impl = impl

    def __str__(self):
        return str(self._impl)

    def __getattribute__(self, attr):
        impl = super().__getattribute__("_impl")

        try:
            return getattr(impl, attr)
        except AttributeError:
            return super().__getattribute__(attr)

    RE_FRAME = re.compile(
        r"\s+at\s(?:new\s)?(?P<func>.+)\s\((?P<file>[^:]+):?(?P<row>\d+)?:?(?P<col>\d+)?\)"
    )
    RE_FUNC = re.compile(r"\s+at\s(?:new\s)?(?P<func>.+)\s\((?P<file>[^\)]+)\)")
    RE_FILE = re.compile(r"\s+at\s(?P<file>[^:]+):?(?P<row>\d+)?:?(?P<col>\d+)?")

    @staticmethod
    def parse_stack(value):
        stack = []

        def int_or_nul(value):
            return int(value) if value else None

        for line in value.split("\n")[1:]:
            m = JSError.RE_FRAME.match(line)

            if m:
                stack.append(
                    (
                        m.group("func"),
                        m.group("file"),
                        int_or_nul(m.group("row")),
                        int_or_nul(m.group("col")),
                    )
                )
                continue

            m = JSError.RE_FUNC.match(line)

            if m:
                stack.append((m.group("func"), m.group("file"), None, None))
                continue

            m = JSError.RE_FILE.match(line)

            if m:
                stack.append(
                    (
                        None,
                        m.group("file"),
                        int_or_nul(m.group("row")),
                        int_or_nul(m.group("col")),
                    )
                )
                continue

            assert line

        return stack

    @property
    def frames(self):
        return self.parse_stack(self.stackTrace)


_STPyV8._JSError._jsclass = JSError  # pylint:disable=protected-access

JSObject = _STPyV8.JSObject
JSNull = _STPyV8.JSNull
JSUndefined = _STPyV8.JSUndefined
JSArray = _STPyV8.JSArray
JSFunction = _STPyV8.JSFunction
JSPlatform = _STPyV8.JSPlatform


class JSLocker(_STPyV8.JSLocker):
    def __enter__(self):
        self.enter()

        if JSContext.entered:
            self.leave()
            raise RuntimeError("Lock should be acquired before entering the context")

        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if JSContext.entered:
            self.leave()
            raise RuntimeError("Lock should be released after leaving the context")

        self.leave()

    def __bool__(self):
        return self.entered()


class JSUnlocker(_STPyV8.JSUnlocker):
    def __enter__(self):
        self.enter()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.leave()

    def __bool__(self):
        return self.entered()


class JSClass:
    __properties__ = {}
    __watchpoints__ = {}

    # Names listed here are invisible to JS: reading one yields `undefined`,
    # writing one is silently dropped, `'name' in obj` is `False`, `delete` fails,
    # and the name is left out of `Object.keys()` / `for..in`.
    #
    # This is the *only* way to keep an attribute private. The named-property
    # interceptors hand the requested name to `PyObject_GetAttrString`, which is
    # exactly what Python's `getattr()` uses, so JS otherwise gets the same
    # attribute access as Python -- including private-name mangling. `self.__x`
    # inside `class History` is stored as `_History__x`, and because the class
    # name is public (`h.constructor.name`, or just the source) a script only has
    # to spell `h._History__x` to read *and write* it. Underscores are a naming
    # convention, not a gate; this list is a gate.
    #
    # Subclasses union their own list with the ones inherited along the MRO.
    __js_hidden__ = ()

    def __getattr__(self, name):
        if name == "constructor":
            return JSClassConstructor(self.__class__)

        if name == "prototype":
            return JSClassPrototype(self.__class__)

        prop = self.__dict__.setdefault("__properties__", {}).get(name, None)

        if prop and isinstance(prop[0], collections.abc.Callable):
            return prop[0]()

        raise AttributeError(name)

    def __setattr__(self, name, value):
        prop = self.__dict__.setdefault("__properties__", {}).get(name, None)

        if prop and isinstance(prop[1], collections.abc.Callable):
            return prop[1](value)

        return object.__setattr__(self, name, value)

    def toString(self):
        """
        Return the string representation of the object
        """
        return f"[object {self.__class__.__name__}]"

    def toLocaleString(self):
        """
        Return the string representation of the object as a string value
        appropriate to the environment current locale
        """
        return self.toString()

    def valueOf(self):
        """
        Return the primitive value of the object
        """
        return self

    def hasOwnProperty(self, name=None):
        """
        Return a boolean value indicating whether the object has a property
        with the specified name.

        Called with no argument (or ``undefined``) the standard behaviour is to
        return ``False`` -- the missing key is treated as ``undefined``, which is
        never an own property -- rather than throwing a TypeError, matching the
        real ``Object.prototype.hasOwnProperty``.
        """
        if name is None:
            return False
        return hasattr(self, name)

    def isPrototypeOf(self, obj=None):
        """
        Return a boolean value indicating whether the object exists in the
        prototype chain of another object.

        The standard ``Object.prototype.isPrototypeOf`` never throws -- it
        returns ``false`` for any non-object argument (including ``undefined`` /
        ``null``, which st-pyv8 maps to ``None``). In the JSClass model an
        instance's prototype chain mirrors its Python class MRO, so when both
        sides are JSClass instances we check whether *this* instance's class
        appears in the other instance's class MRO.
        """
        if obj is None or not isinstance(obj, JSClass):
            return False
        if self is obj:
            return False
        return self.__class__ in obj.__class__.__mro__

    def __defineGetter__(self, name, getter):
        """
        Bind the object property to a function to be called when that property
        is looked up
        """
        self.__properties__[name] = (getter, self.__lookupSetter__(name))

    def __lookupGetter__(self, name):
        """
        Return the function bound as a getter to the specified property
        """
        return self.__properties__.get(name, (None, None))[0]

    def __defineSetter__(self, name, setter):
        """
        Bind the object property to a function to be called when an attempt
        is made to set that property
        """
        self.__properties__[name] = (self.__lookupGetter__(name), setter)

    def __lookupSetter__(self, name):
        """
        Return the function bound as setter to the specified property
        """
        return self.__properties__.get(name, (None, None))[1]

    def watch(self, prop, handler):
        """
        Watch for a property to be assigned a value and runs a function when
        such assignment occurs
        """
        self.__watchpoints__[prop] = handler

    def unwatch(self, prop):
        """
        Remove a watchpoint set with the watch method
        """
        del self.__watchpoints__[prop]


class JSClassConstructor(JSClass):  # pylint:disable=abstract-method
    def __init__(self, cls):
        self.cls = cls

    @property
    def name(self):
        return self.cls.__name__

    def toString(self):
        return f"function {self.name}() {{\n  [native code]\n}}"

    def __call__(self, *args, **kwds):
        return self.cls(*args, **kwds)


class JSClassPrototype(JSClass):  # pylint:disable=abstract-method
    def __init__(self, cls):
        self.cls = cls

    @property
    def constructor(self):
        return JSClassConstructor(self.cls)

    @property
    def name(self):
        return self.cls.__name__


class JSEngine(_STPyV8.JSEngine):
    def __init__(self):
        _STPyV8.JSEngine.__init__(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        del self


JSScript = _STPyV8.JSScript
JSStackTrace = _STPyV8.JSStackTrace
JSStackTrace.Options = _STPyV8.JSStackTraceOptions
JSStackTrace.GetCurrentStackTrace = staticmethod(
    lambda frame_limit,  # pylint:disable=unnecessary-lambda
    options: _STPyV8.JSIsolate.current.GetCurrentStackTrace(frame_limit, options)
)
JSStackFrame = _STPyV8.JSStackFrame


class JSIsolate(_STPyV8.JSIsolate):
    def __enter__(self):
        self.enter()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.leave()
        del self


class JSContext(_STPyV8.JSContext):
    def __init__(self, obj=None, ctxt=None):
        self.lock = JSLocker()
        self.lock.enter()

        if ctxt:
            _STPyV8.JSContext.__init__(self, ctxt)
        else:
            _STPyV8.JSContext.__init__(self, obj)

        # Remembered for the opt-in auto-install of prototype property
        # accessors (see auto_property_accessors); None when wrapping an
        # existing context, which has no global object of its own to scan.
        self._global_object = None if ctxt else obj

    def __enter__(self):
        self.enter()

        if auto_property_accessors:
            _auto_install_property_accessors(self, getattr(self, "_global_object", None))

        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.leave()

        if hasattr(JSLocker, "lock"):
            self.lock.leave()
            self.lock = None

        del self


# ---------------------------------------------------------------------------
# Prototype property accessors
# ---------------------------------------------------------------------------

# Installed on a class's JS prototype object by install_property_accessors().
# The accessors bridge back into Python with plain getattr()/setattr(), so the
# class's @property fget/fset run exactly as they do for an instance-level
# access: when the accessor fires, `this` is the wrapped instance and STPyV8
# hands it to Python as the underlying Python object.
#
# Why this is needed: the C++ named-property interceptors answer *instance*
# access only. V8 never promotes those names onto the prototype object, so
# Object.getOwnPropertyDescriptor(Cls.prototype, "scrollTop") is `undefined`
# and libraries that grab the descriptor and wrap it -- scroll-behavior-
# polyfill and friends -- have nothing to call. Installing real accessor pairs
# makes the descriptor exist, and instance access keeps working because the
# instance interceptors already defer to real prototype accessors.
_PROPERTY_ACCESSOR_PRELUDE = """
(function (ctor, specsJson, get, set) {
    var specs = JSON.parse(specsJson);
    var proto = ctor.prototype;

    for (var i = 0; i < specs.length; i++) {
        (function (name, hasGetter, hasSetter) {
            var desc = {
                configurable: true,
                enumerable: true,
                get: function () { return get(this, name); }
            };

            if (hasSetter) {
                desc.set = function (value) { set(this, name, value); };
            }

            Object.defineProperty(proto, name, desc);
        })(specs[i][0], specs[i][1], specs[i][2]);
    }
});
"""


def _prototype_property_get(obj, name):
    # A JS-object receiver (Cls.prototype itself, or
    # descriptor.get.call(someJSObject, ...)) must be rejected: getattr() on a
    # CJavascriptObject would re-enter the very accessor being run and recurse
    # forever. Real engines reject that call too ("Illegal invocation").
    if isinstance(obj, JSObject):
        raise TypeError(
            "property '%s' cannot be read on this receiver; it belongs to "
            "instances of the exposed Python class" % name
        )

    return getattr(obj, name)


def _prototype_property_set(obj, name, value):
    if isinstance(obj, JSObject):
        raise TypeError(
            "property '%s' cannot be written on this receiver; it belongs to "
            "instances of the exposed Python class" % name
        )

    setattr(obj, name, value)


def _collect_property_specs(cls, names):
    """Return ``[[name, has_getter, has_setter], ...]`` for *cls*' properties.

    With *names* omitted every ``@property`` along the MRO is collected
    (most-derived definition wins, each name once), skipping dunders and the
    names the class hid from JS via ``__js_hidden__``.
    """
    hidden = set()

    for base in getattr(cls, "__mro__", (cls,)):
        hidden.update(getattr(base, "__js_hidden__", ()) or ())

    if names is None:
        names = []
        seen = set()

        for base in getattr(cls, "__mro__", (cls,)):
            for name, value in vars(base).items():
                if name.startswith("__") or name in hidden or name in seen:
                    continue

                if not isinstance(value, property):
                    continue

                seen.add(name)
                names.append(name)

    specs = []

    for name in names:
        prop = getattr(cls, name, None)

        if not isinstance(prop, property):
            raise ValueError(
                "'%s' is not a @property of %s (got %r); pass the names of "
                "properties defined on the class".format(
                    name, getattr(cls, "__name__", cls), prop
                )
            )

        specs.append([name, prop.fget is not None, prop.fset is not None])

    return specs


def install_property_accessors(context, cls, names=None, js_name=None):
    """Expose a class's ``@property`` descriptors as real accessor pairs on its
    JS prototype object.

    The C++ interceptors answer instance access only, so before this call
    ``Object.getOwnPropertyDescriptor(Cls.prototype, "scrollTop")`` is
    ``undefined`` and code that grabs the descriptor -- e.g.
    ``... .set`` in scroll-behavior-polyfill -- has nothing to call. Afterwards
    the descriptor exists, its get/set run the Python ``fget``/``fset``, and
    instance access is unchanged.

    Args:
        context: an entered :class:`JSContext` (``with context:``).
        cls: the Python class whose ``@property`` descriptors to expose.
        names: property names to expose. Defaults to every ``@property``
            defined along ``cls``'s MRO (read-only ones get a getter only).
        js_name: the name the class's JS constructor is reachable by in
            *context*. Defaults to ``cls.__name__``; pass it when the class is
            exposed under another name (a dotted path works too).

    Returns:
        The list of installed property names.

    Raises:
        TypeError: *context* is not a :class:`JSContext`.
        RuntimeError: the context is not entered, or *js_name* does not
            resolve to the class's JS constructor in it.
        ValueError: *names* contains something that is not a ``@property``.
    """
    if not isinstance(context, JSContext):
        raise TypeError(
            "context must be a STPyV8.JSContext, got %r".format(
                getattr(type(context), "__name__", type(context))
            )
        )

    if not JSContext.inContext:
        raise RuntimeError(
            "the context must be entered (use `with context:`) before "
            "installing property accessors"
        )

    cls_name = getattr(cls, "__name__", cls)
    js_name = cls_name if js_name is None else js_name

    specs = _collect_property_specs(cls, names)

    if not specs:
        return []

    # A Python list is exposed to JS with indexed access only (no .length), so
    # the spec table travels as JSON and is parsed on the JS side.
    specs_json = json.dumps(specs)

    try:
        ctor = context.eval(js_name)
    except Exception as error:  # noqa: BLE001 - re-raised with context below
        # JS errors surface as the matching Python builtin (ReferenceError for
        # an unknown name, ...), so this cannot be narrowed to one type.
        raise RuntimeError(
            "cannot find the JS constructor for {} as '{}' in this context "
            "({}); expose the class in the context first, or pass "
            "js_name=".format(cls_name, js_name, error)
        )

    if not isinstance(ctor, JSFunction):
        raise RuntimeError(
            "'{}' in this context is {}, not the JS constructor for {}; pass "
            "js_name= if the class is exposed under another name".format(
                js_name, getattr(type(ctor), "__name__", type(ctor)), cls_name
            )
        )

    context.eval(_PROPERTY_ACCESSOR_PRELUDE)(ctor, specs_json, _prototype_property_get, _prototype_property_set)

    return [spec[0] for spec in specs]


# Opt-in switch for automatic installation at context entry. Off by default on
# purpose: installing accessors is an observable behaviour change (a prototype
# descriptor appears where there was none, and the names become enumerable), so
# it must not silently rewrite what existing scripts see. Set it to True to have
# every JSContext install the @property accessors of the classes its global
# object exposes.
#
#     STPyV8.auto_property_accessors = True
#
# A class can opt out with ``__js_no_properties__ = True``; names the class hid
# from JS (``__js_hidden__``) are never installed, as with the manual call.
auto_property_accessors = False


def _auto_install_property_accessors(context, obj):
    """Install @property accessors for the classes *obj* exposes to JS.

    *obj* is the global object the context was built from; every attribute that
    is a class is a constructor in the context, named after the attribute. Only
    direct attributes are considered -- classes mounted later (``locals.X = C``)
    or nested inside another object still need the explicit
    :func:`install_property_accessors` call.
    """
    if obj is None:
        return []

    installed = {}

    for name, value in list(vars(obj).items()):
        if not isinstance(value, type):
            continue

        if getattr(value, "__js_no_properties__", False):
            continue

        try:
            names = install_property_accessors(context, value, js_name=name)
        except (RuntimeError, ValueError):
            # Not reachable as a JS constructor under this name (a plain helper
            # class, a shadowed name, ...) -- nothing to install for it.
            continue

        if names:
            installed[name] = names

    return installed


def icu_sync():
    if sys.version_info < (3, 10):
        from importlib_resources import files
    else:
        from importlib.resources import files

    for folder in icu_data_folders:
        if not folder or not os.path.exists(folder):
            continue

        version_file = os.path.join(folder, "stpyv8-version.txt")
        if not os.path.exists(version_file):
            continue

        with open(version_file, encoding="utf-8", mode="r") as fd:
            version = fd.read()

        if version.strip() in (__version__,):
            return

    try:
        stpyv8_icu_files = files("stpyv8-icu")
    except ModuleNotFoundError:
        return

    for f in stpyv8_icu_files.iterdir():
        if f.name not in ("icudtl.dat",):
            continue

        data = f.read_bytes()

        for folder in icu_data_folders:
            try:
                os.makedirs(folder, exist_ok=True)
                with open(os.path.join(folder, "icudtl.dat"), mode="wb") as fd:
                    fd.write(data)

                version_file = os.path.join(folder, "stpyv8-version.txt")
                with open(version_file, encoding="utf-8", mode="w") as fd:
                    fd.write(__version__)
            except PermissionError:
                pass


icu_sync()

v8_default_platform = JSPlatform()
v8_default_platform.init()

v8_default_isolate = JSIsolate()
v8_default_isolate.enter()
