#include "Wrapper.h"

#include <stdlib.h>
#include <iostream>
#include <vector>

#include <boost/python/raw_function.hpp>
#include <descrobject.h>
#include <datetime.h>

#include "libplatform/libplatform.h"

#include "Context.h"
#include "Utils.h"


#define TERMINATE_EXECUTION_CHECK(returnValue) \
  if(v8::Isolate::GetCurrent()->IsExecutionTerminating()) { \
    ::PyErr_Clear(); \
    ::PyErr_SetString(PyExc_RuntimeError, "execution is terminating"); \
    return returnValue; \
  }

#define CHECK_V8_CONTEXT() \
  if (v8::Isolate::GetCurrent()->GetCurrentContext().IsEmpty()) { \
    throw CJavascriptException("Javascript object out of context", PyExc_UnboundLocalError); \
  }

std::ostream& operator <<(std::ostream& os, const CJavascriptObject& obj)
{
    obj.Dump(os);

    return os;
}


void CWrapper::Expose(void)
{
    PyDateTime_IMPORT;

    py::class_<CJavascriptObject, boost::noncopyable>("JSObject", py::no_init)
    .def("__getattr__", &CJavascriptObject::GetAttr)
    .def("__setattr__", &CJavascriptObject::SetAttr)
    .def("__delattr__", &CJavascriptObject::DelAttr)

    .def("__hash__", &CJavascriptObject::GetIdentityHash)
    .def("clone", &CJavascriptObject::Clone, "Clone the object.")
    .def("__dir__", &CJavascriptObject::GetAttrList)

    // Emulating dict object
    .def("keys", &CJavascriptObject::GetAttrList, "Get a list of the object attributes.")

    .def("__getitem__", &CJavascriptObject::GetAttr)
    .def("__setitem__", &CJavascriptObject::SetAttr)
    .def("__delitem__", &CJavascriptObject::DelAttr)

    .def("__contains__", &CJavascriptObject::Contains)

    .def(int_(py::self))
    .def(float_(py::self))
    .def("__str__", &CJavascriptObject::str)

    .def("__bool__", &CJavascriptObject::operator bool)
    .def("__eq__", &CJavascriptObject::Equals)
    .def("__ne__", &CJavascriptObject::Unequals)

    .def("create", &CJavascriptFunction::CreateWithArgs,
         (py::arg("constructor"),
          py::arg("arguments") = py::tuple(),
          py::arg("propertiesObject") = py::dict()),
         "Creates a new object with the specified prototype object and properties.")
    .staticmethod("create")
    ;

    py::class_<CJavascriptNull, py::bases<CJavascriptObject>, boost::noncopyable>("JSNull")
    .def("__bool__", &CJavascriptNull::nonzero)
    .def("__str__", &CJavascriptNull::str)
    ;

    py::class_<CJavascriptUndefined, py::bases<CJavascriptObject>, boost::noncopyable>("JSUndefined")
    .def("__bool__", &CJavascriptUndefined::nonzero)
    .def("__str__", &CJavascriptUndefined::str)
    ;

    py::class_<CJavascriptArray, py::bases<CJavascriptObject>, boost::noncopyable>("JSArray", py::no_init)
    .def(py::init<py::object>())

    .def("__len__", &CJavascriptArray::Length)

    .def("__getitem__", &CJavascriptArray::GetItem)
    .def("__setitem__", &CJavascriptArray::SetItem)
    .def("__delitem__", &CJavascriptArray::DelItem)

    .def("__iter__", py::range(&CJavascriptArray::begin, &CJavascriptArray::end))

    .def("__contains__", &CJavascriptArray::Contains)
    ;

    py::class_<CJavascriptFunction, py::bases<CJavascriptObject>, boost::noncopyable>("JSFunction", py::no_init)
    .def("__call__", py::raw_function(&CJavascriptFunction::CallWithArgs))

    .def("apply", &CJavascriptFunction::ApplyJavascript,
         (py::arg("self"),
          py::arg("args") = py::list(),
          py::arg("kwds") = py::dict()),
         "Performs a function call using the parameters.")
    .def("apply", &CJavascriptFunction::ApplyPython,
         (py::arg("self"),
          py::arg("args") = py::list(),
          py::arg("kwds") = py::dict()),
         "Performs a function call using the parameters.")
    .def("invoke", &CJavascriptFunction::Invoke,
         (py::arg("args") = py::list(),
          py::arg("kwds") = py::dict()),
         "Performs a binding method call using the parameters.")

    .def("setName", &CJavascriptFunction::SetName)

    .add_property("name", &CJavascriptFunction::GetName, &CJavascriptFunction::SetName, "The name of function")
    .add_property("owner", &CJavascriptFunction::GetOwner)

    .add_property("linenum", &CJavascriptFunction::GetLineNumber, "The line number of function in the script")
    .add_property("colnum", &CJavascriptFunction::GetColumnNumber, "The column number of function in the script")
    .add_property("resname", &CJavascriptFunction::GetResourceName, "The resource name of script")
    .add_property("inferredname", &CJavascriptFunction::GetInferredName, "Name inferred from variable or property assignment of this function")
    ;
    py::objects::class_value_wrapper<std::shared_ptr<CJavascriptObject>,
    py::objects::make_ptr_instance<CJavascriptObject,
    py::objects::pointer_holder<std::shared_ptr<CJavascriptObject>, CJavascriptObject> > >();
}

void CPythonObject::ThrowIf(v8::Isolate* isolate)
{
    CPythonGIL python_gil;

    assert(PyErr_Occurred());

    v8::HandleScope handle_scope(isolate);

    PyObject *exc, *val, *trb;

    ::PyErr_Fetch(&exc, &val, &trb);
    ::PyErr_NormalizeException(&exc, &val, &trb);

    py::object type(py::handle<>(py::allow_null(exc))),
    value(py::handle<>(py::allow_null(val)));

    if (trb) py::decref(trb);

    std::string msg;

    if (::PyObject_HasAttrString(value.ptr(), "args"))
    {
        py::object args = value.attr("args");

        if (PyTuple_Check(args.ptr()))
        {
            for (Py_ssize_t i=0; i<PyTuple_GET_SIZE(args.ptr()); i++)
            {
                py::extract<const std::string> extractor(args[i]);

                if (extractor.check()) msg += extractor();
            }
        }
    }
    else if (::PyObject_HasAttrString(value.ptr(), "message"))
    {
        py::extract<const std::string> extractor(value.attr("message"));

        if (extractor.check()) msg = extractor();
    }
    else if (val)
    {
        if (PyBytes_CheckExact(val))
        {
            msg = PyBytes_AS_STRING(val);
        }
        else if (PyTuple_CheckExact(val))
        {
            for (int i=0; i<PyTuple_GET_SIZE(val); i++)
            {
                PyObject *item = PyTuple_GET_ITEM(val, i);

                if (item && PyBytes_CheckExact(item))
                {
                    msg = PyBytes_AS_STRING(item);
                    break;
                }
            }
        }
    }

    v8::Handle<v8::Value> error;

    if (::PyErr_GivenExceptionMatches(type.ptr(), ::PyExc_IndexError))
    {
        error = v8::Exception::RangeError(v8::String::NewFromUtf8(isolate, msg.c_str(), v8::NewStringType::kNormal, msg.size()).ToLocalChecked());
    }
    else if (::PyErr_GivenExceptionMatches(type.ptr(), ::PyExc_AttributeError))
    {
        error = v8::Exception::ReferenceError(v8::String::NewFromUtf8(isolate, msg.c_str(), v8::NewStringType::kNormal, msg.size()).ToLocalChecked());
    }
    else if (::PyErr_GivenExceptionMatches(type.ptr(), ::PyExc_SyntaxError))
    {
        error = v8::Exception::SyntaxError(v8::String::NewFromUtf8(isolate, msg.c_str(), v8::NewStringType::kNormal, msg.size()).ToLocalChecked());
    }
    else if (::PyErr_GivenExceptionMatches(type.ptr(), ::PyExc_TypeError))
    {
        error = v8::Exception::TypeError(v8::String::NewFromUtf8(isolate, msg.c_str(), v8::NewStringType::kNormal, msg.size()).ToLocalChecked());
    }
    else
    {
        error = v8::Exception::Error(v8::String::NewFromUtf8(isolate, msg.c_str(), v8::NewStringType::kNormal, msg.size()).ToLocalChecked());
    }

    if (error->IsObject())
    {
        v8::Handle<v8::Context> ctxt = isolate->GetCurrentContext();

        v8::Local<v8::String> key_type = v8::String::NewFromUtf8(isolate, "exc_type").ToLocalChecked();
        v8::Local<v8::Private> privateKey_type = v8::Private::ForApi(isolate, key_type);

        v8::Local<v8::String> key_value = v8::String::NewFromUtf8(isolate, "exc_value").ToLocalChecked();
        v8::Local<v8::Private> privateKey_value = v8::Private::ForApi(isolate, key_value);

#ifdef SUPPORT_TRACE_EXCEPTION_LIFECYCLE
        error->ToObject(ctxt).ToLocalChecked()->SetPrivate(ctxt, privateKey_type, v8::External::New(isolate, ObjectTracer::Trace(error, new py::object(type)).Object()));
        error->ToObject(ctxt).ToLocalChecked()->SetPrivate(ctxt, privateKey_value,v8::External::New(isolate, ObjectTracer::Trace(error, new py::object(value)).Object()));
#else
        error->ToObject(ctxt).ToLocalChecked()->SetPrivate(ctxt, privateKey_type, v8::External::New(isolate, new py::object(type)));
        error->ToObject(ctxt).ToLocalChecked()->SetPrivate(ctxt, privateKey_value, v8::External::New(isolate, new py::object(value)));
#endif
    }

    isolate->ThrowException(error);
}

#define _TERMINATE_CALLBACK_EXECUTION_CHECK(returnValue) \
  if(v8::Isolate::GetCurrent()->IsExecutionTerminating()) { \
    ::PyErr_Clear(); \
    ::PyErr_SetString(PyExc_RuntimeError, "execution is terminating"); \
    info.GetReturnValue().Set(returnValue); \
    return v8::Intercepted::kNo; \
  }

#define TRY_HANDLE_EXCEPTION(value) _TERMINATE_CALLBACK_EXECUTION_CHECK(value) \
                                    BEGIN_HANDLE_PYTHON_EXCEPTION \
                                    {
#define END_HANDLE_EXCEPTION(value) } \
                                    END_HANDLE_PYTHON_EXCEPTION \
                                    info.GetReturnValue().Set(value); \
                                    return v8::Intercepted::kYes;

#define _TERMINATE_CALLBACK_EXECUTION_CHECK_NO_INTERCEPT(returnValue) \
  if(v8::Isolate::GetCurrent()->IsExecutionTerminating()) { \
    ::PyErr_Clear(); \
    ::PyErr_SetString(PyExc_RuntimeError, "execution is terminating"); \
    info.GetReturnValue().Set(returnValue); \
    return; \
  }

#define TRY_HANDLE_EXCEPTION_NO_INTERCEPT(value) _TERMINATE_CALLBACK_EXECUTION_CHECK_NO_INTERCEPT(value) \
                                                 BEGIN_HANDLE_PYTHON_EXCEPTION \
                                                 {
#define END_HANDLE_EXCEPTION_NO_INTERCEPT(value) } \
                                                 END_HANDLE_PYTHON_EXCEPTION \
                                                 info.GetReturnValue().Set(value); \
                                                 return;

#define CALLBACK_RETURN_HANDLED(value) do { info.GetReturnValue().Set(value); return v8::Intercepted::kYes; } while(0);
#define CALLBACK_RETURN_NOT_HANDLED(value) do { info.GetReturnValue().Set(value); return v8::Intercepted::kNo; } while(0);
#define CALLBACK_RETURN_NO_INTERCEPT(value) do { info.GetReturnValue().Set(value); return; } while(0);


CPythonObject::CPythonObject()
{
}

CPythonObject::~CPythonObject()
{
}

namespace
{
// Resolves the set of attribute names a Python class keeps out of reach of JS.
// A class declares them in `__js_hidden__` (any iterable of strings); the lists
// found along the whole MRO are unioned, base-most first, so a subclass inherits
// its bases' decisions and its own list is applied last.
std::set<std::string> ResolveJsHiddenNames(py::object cls)
{
    std::set<std::string> hidden;

    PyObject *mro = ::PyObject_GetAttrString(cls.ptr(), "__mro__");

    if (mro && PyTuple_Check(mro))
    {
        Py_ssize_t count = PyTuple_GET_SIZE(mro);

        // Base-most first, so a subclass's own list is applied last.
        for (Py_ssize_t i = count - 1; i >= 0; --i)
        {
            py::object base(py::borrowed<>(PyTuple_GET_ITEM(mro, i)));

            if (!::PyObject_HasAttrString(base.ptr(), "__js_hidden__"))
                continue;

            py::object names(base.attr("__js_hidden__"));

            PyObject *iter = ::PyObject_GetIter(names.ptr());

            if (!iter)
            {
                ::PyErr_Clear();
                continue;
            }

            PyObject *item = NULL;

            while (NULL != (item = ::PyIter_Next(iter)))
            {
                // `= py::object(...)` and not `py::object entry(...)`, which the
                // compiler would read as a function declaration.
                py::object entry = py::object(py::handle<>(item));

                if (PyUnicode_Check(entry.ptr()) || PyBytes_Check(entry.ptr()))
                    hidden.insert(py::extract<std::string>(entry));
            }

            // A broken iterable leaves an error set; the declaration list is not
            // worth propagating that, so drop it and keep what was collected.
            ::PyErr_Clear();

            ::Py_DECREF(iter);
        }
    }

    ::Py_XDECREF(mro);

    return hidden;
}

// True when `name` resolves to a *real* (non-interceptor) property on one of the
// prototypes between `holder` and the root prototype -- e.g. one installed
// through Object.defineProperty(Element.prototype, "scrollTop", {...}).
//
// This must be consulted *before* the Python attribute lookup in every named
// interceptor, not after it. The interceptors installed on the instance template
// are masking: as soon as one reports a property as handled, V8 stops and never
// walks the prototype chain. So a prototype accessor stays unreachable whenever
// Python happens to expose an attribute of the same name -- the getter would
// answer with the Python value (scrollTop -> 0) while the write still reached
// the prototype setter, i.e. reads and writes would disagree. Checking the
// prototype first is what makes a polyfill's redefinition of e.g.
// Element.prototype.scrollTop take effect on instance access, exactly as in a
// browser.
//
// The root prototype (Object.prototype) is deliberately skipped: its built-ins
// (toString, valueOf, hasOwnProperty, ...) must never shadow a Python attribute
// of the same name. The chain is walked with HasRealNamedProperty, which looks
// at real own properties only and never invokes an accessor.
static bool HasRealPrototypeProperty(v8::Local<v8::Object> holder,
                                    v8::Local<v8::Name> name,
                                    v8::Isolate *isolate)
{
    v8::Local<v8::Context> ctx = isolate->GetCurrentContext();

    v8::Local<v8::Value> protoVal = holder->GetPrototype();

    while (protoVal->IsObject())
    {
        v8::Local<v8::Object> proto = protoVal.As<v8::Object>();

        // The root prototype is the last one that still has a prototype of its
        // own; everything it carries is a built-in and is not ours to prefer.
        if (!proto->GetPrototype()->IsObject())
            break;

        v8::Maybe<bool> has = proto->HasRealNamedProperty(ctx, name);

        if (has.IsJust() && has.FromJust())
            return true;

        protoVal = proto->GetPrototype();
    }

    return false;
}

// If `name` is a genuine accessor (installed e.g. via Object.defineProperty on
// the prototype) that has a setter, return that setter function so the named
// setter can forward the assignment to it. Returning kNo from the named setter
// would make V8 create a fresh own property on the instance instead of invoking
// the prototype accessor -- which is exactly what would stop a polyfill's
// redefinition of e.g. Element.prototype.scrollTop from taking effect on writes.
static v8::MaybeLocal<v8::Function> GetRealPrototypeSetter(v8::Local<v8::Object> holder,
                                                          v8::Local<v8::Name> name,
                                                          v8::Isolate *isolate)
{
    v8::Local<v8::Context> ctx = isolate->GetCurrentContext();

    v8::Local<v8::Value> protoVal = holder->GetPrototype();

    while (protoVal->IsObject())
    {
        v8::Local<v8::Object> proto = protoVal.As<v8::Object>();

        // Same boundary as HasRealPrototypeProperty: Object.prototype's
        // built-ins are not a target for forwarding a write.
        if (!proto->GetPrototype()->IsObject())
            break;

        v8::Maybe<bool> has = proto->HasRealNamedProperty(ctx, name);

        if (has.IsJust() && has.FromJust())
        {
            v8::MaybeLocal<v8::Value> maybeDesc = proto->GetOwnPropertyDescriptor(ctx, name);

            if (!maybeDesc.IsEmpty())
            {
                v8::Local<v8::Value> desc = maybeDesc.ToLocalChecked();

                if (desc->IsObject())
                {
                    v8::MaybeLocal<v8::Value> maybeSetter =
                        desc.As<v8::Object>()->Get(ctx, v8::String::NewFromUtf8(isolate, "set", v8::NewStringType::kNormal).ToLocalChecked());

                    if (!maybeSetter.IsEmpty())
                    {
                        v8::Local<v8::Value> setter = maybeSetter.ToLocalChecked();

                        if (setter->IsFunction())
                            return setter.As<v8::Function>();
                    }
                }
            }
            // Real property found but not a settable accessor: stop searching.
            return v8::MaybeLocal<v8::Function>();
        }

        protoVal = proto->GetPrototype();
    }

    return v8::MaybeLocal<v8::Function>();
}
}  // namespace

bool CPythonObject::IsJsHiddenName(PyObject *obj, const std::string& name)
{
    if (obj == nullptr || !PyType_Check(Py_TYPE(obj)))
        return false;

    PyTypeObject *type = Py_TYPE(obj);

    // `__js_hidden__` is a class-level constant, so the merged list only has to
    // be built once per type. Every interceptor holds the GIL when it gets here,
    // which already serialises this cache; the mutex only guards against a
    // future caller that does not.
    static std::mutex s_mutex;

    // The type object is kept alive by the cache entry so that its address stays
    // a valid key (same reasoning as the per-isolate caches in
    // GetObjectTemplate / GetClassTemplate).
    static std::unordered_map<PyTypeObject *, std::pair<py::object, std::set<std::string>>> s_cache;

    std::lock_guard<std::mutex> lock(s_mutex);

    auto it = s_cache.find(type);

    if (it == s_cache.end())
    {
        py::object cls(py::borrowed<>(reinterpret_cast<PyObject *>(type)));

        it = s_cache.emplace(type, std::make_pair(cls, ResolveJsHiddenNames(cls))).first;
    }

    return it->second.second.find(name) != it->second.second.end();
}

v8::Intercepted CPythonObject::NamedGetter(v8::Local<v8::Name> prop, const v8::PropertyCallbackInfo<v8::Value>& info)
{
    v8::HandleScope handle_scope(info.GetIsolate());

    TRY_HANDLE_EXCEPTION(v8::Undefined(info.GetIsolate()))

    CPythonGIL python_gil;

    py::object obj = CJavascriptObject::Wrap(info.Holder());
    if (PyGen_Check(obj.ptr()))
        CALLBACK_RETURN_HANDLED(v8::Undefined(info.GetIsolate()));

    v8::String::Utf8Value name(info.GetIsolate(), v8::Local<v8::String>::Cast(prop));
    if (*name == nullptr)
        CALLBACK_RETURN_HANDLED(v8::Undefined(info.GetIsolate()));

    // Let V8 resolve these through the real prototype chain (each wrapped class
    // now has a genuine FunctionTemplate whose .prototype carries `constructor`)
    // instead of the fabricated values returned by JSClass.__getattr__.
    {
        std::string prop_name(*name);
        if (prop_name == "constructor" || prop_name == "prototype")
            CALLBACK_RETURN_NOT_HANDLED(v8::Handle<v8::Value>());
    }

    // Names the class listed in `__js_hidden__` do not exist as far as JS is
    // concerned, so Python-internal state stays unreachable from scripts.
    if (CPythonObject::IsJsHiddenName(obj.ptr(), *name))
        CALLBACK_RETURN_NOT_HANDLED(v8::Handle<v8::Value>());

    // A genuine property on a class prototype -- e.g. the accessor installed by
    // Object.defineProperty(Element.prototype, "scrollTop", {...}) -- outranks
    // the catch-all Python lookup, so step aside *before* asking Python. Doing
    // this afterwards would leave the Python attribute masking the prototype
    // accessor: reads would return the Python value while writes still reached
    // the prototype setter.
    if (HasRealPrototypeProperty(info.Holder(), prop, info.GetIsolate()))
        CALLBACK_RETURN_NOT_HANDLED(v8::Handle<v8::Value>());

    PyObject *value = ::PyObject_GetAttrString(obj.ptr(), *name);

    if (!value)
    {
        if (PyErr_Occurred())
        {
            if (::PyErr_ExceptionMatches(::PyExc_AttributeError))
            {
                ::PyErr_Clear();
            }
            else
            {
                py::throw_error_already_set();
            }
        }

        if (::PyMapping_Check(obj.ptr()))
        {
            int rc;

#if PY_VERSION_HEX >= 0x030d0000
            rc = ::PyMapping_HasKeyStringWithError(obj.ptr(), *name);
            if (rc == -1)
                ::PyErr_Clear();
#else
            rc = ::PyMapping_HasKeyString(obj.ptr(), *name);
#endif
            if (rc == 1)
            {
                py::object result(py::handle<>(::PyMapping_GetItemString(obj.ptr(), *name)));

                if (!result.is_none())
                    CALLBACK_RETURN_HANDLED(Wrap(result));
            }
        }

        CALLBACK_RETURN_NOT_HANDLED(v8::Handle<v8::Value>());
    }

    py::object attr = py::object(py::handle<>(value));

#ifdef SUPPORT_PROPERTY
    if (PyObject_TypeCheck(attr.ptr(), &::PyProperty_Type))
    {
        py::object getter = attr.attr("fget");

        if (getter.is_none())
            throw CJavascriptException("unreadable attribute", ::PyExc_AttributeError);

        attr = getter();
    }
#endif

    CALLBACK_RETURN_HANDLED(Wrap(attr));

    END_HANDLE_EXCEPTION(v8::Undefined(info.GetIsolate()))
}

v8::Intercepted CPythonObject::NamedSetter(v8::Local<v8::Name> prop, v8::Local<v8::Value> value, const v8::PropertyCallbackInfo<void>& info)
{
    v8::HandleScope handle_scope(info.GetIsolate());

    TRY_HANDLE_EXCEPTION(v8::Undefined(info.GetIsolate()))

    CPythonGIL python_gil;

    py::object obj = CJavascriptObject::Wrap(info.Holder());

    v8::String::Utf8Value name(info.GetIsolate(), prop);

    if (*name == nullptr)
        CALLBACK_RETURN_NOT_HANDLED(v8::Undefined(info.GetIsolate()));

    // A hidden name is not writable either: report the assignment as handled so
    // that it is silently dropped, exactly like assigning to a non-writable
    // property in sloppy mode.
    if (CPythonObject::IsJsHiddenName(obj.ptr(), *name))
        CALLBACK_RETURN_HANDLED(value);

    // A real accessor installed on the prototype (e.g. via
    // Object.defineProperty(Element.prototype, "scrollTop", {...})) must receive
    // the write: returning kNo from the named setter would make V8 create a fresh
    // own property on the instance and never call the prototype accessor's setter,
    // which is exactly what would stop a polyfill's redefinition from taking
    // effect on instance writes. We only forward when there is a genuine setter;
    // for data properties and get-only accessors we fall through to the normal
    // Python attribute path so built-ins keep their existing assignment behaviour.
    if (HasRealPrototypeProperty(info.Holder(), prop, info.GetIsolate()))
    {
        v8::MaybeLocal<v8::Function> setter =
            GetRealPrototypeSetter(info.Holder(), prop, info.GetIsolate());

        if (!setter.IsEmpty())
        {
            v8::Local<v8::Value> argv[] = { value };
            setter.ToLocalChecked()->Call(info.GetIsolate()->GetCurrentContext(),
                                          info.Holder(), 1, argv);
            CALLBACK_RETURN_HANDLED(value);
        }
    }

    py::object newval = CJavascriptObject::Wrap(value);

    bool found = 1 == ::PyObject_HasAttrString(obj.ptr(), *name);

    if (::PyObject_HasAttrString(obj.ptr(), "__watchpoints__"))
    {
        py::dict watchpoints(obj.attr("__watchpoints__"));
        py::str propname(*name, name.length());

        if (watchpoints.has_key(propname))
        {
            py::object watchhandler = watchpoints.get(propname);

            newval = watchhandler(propname, found ? obj.attr(propname) : py::object(), newval);
        }
    }

    if (!found && ::PyMapping_Check(obj.ptr()))
    {
        ::PyMapping_SetItemString(obj.ptr(), *name, newval.ptr());
    }
    else
    {
#ifdef SUPPORT_PROPERTY
        if (found)
        {
            py::object attr = obj.attr(*name);

            if (PyObject_TypeCheck(attr.ptr(), &::PyProperty_Type))
            {
                py::object setter = attr.attr("fset");

                if (setter.is_none())
                    throw CJavascriptException("can't set attribute", ::PyExc_AttributeError);

                setter(newval);

                CALLBACK_RETURN_HANDLED(value);
            }
        }
#endif
        obj.attr(*name) = newval;
    }

    CALLBACK_RETURN_HANDLED(value);

    END_HANDLE_EXCEPTION(v8::Undefined(info.GetIsolate()));
}

v8::Intercepted CPythonObject::NamedQuery(v8::Local<v8::Name> prop, const v8::PropertyCallbackInfo<v8::Integer>& info)
{
    v8::HandleScope handle_scope(info.GetIsolate());

    TRY_HANDLE_EXCEPTION(v8::Handle<v8::Integer>())

    CPythonGIL python_gil;

    py::object obj = CJavascriptObject::Wrap(info.Holder());

    v8::String::Utf8Value name(info.GetIsolate(), prop);

    // Hidden names must not answer the `in` operator either.
    if (*name && CPythonObject::IsJsHiddenName(obj.ptr(), *name))
        CALLBACK_RETURN_NOT_HANDLED(v8::Integer::New(info.GetIsolate(), v8::None));

    // Same precedence as the getter: a real prototype property answers `in` and
    // ownership itself. Claiming the Python attribute here would make V8
    // synthesise a data descriptor for an inherited accessor.
    if (*name && HasRealPrototypeProperty(info.Holder(), prop, info.GetIsolate()))
        CALLBACK_RETURN_NOT_HANDLED(v8::Integer::New(info.GetIsolate(), v8::None));

    if (*name)
    {
        int rc;

        if (PyGen_Check(obj.ptr()))
            CALLBACK_RETURN_HANDLED(v8::Integer::New(info.GetIsolate(), v8::None));

#if PY_VERSION_HEX >= 0x030d0000
        rc = ::PyObject_HasAttrStringWithError(obj.ptr(), *name);
        if (rc == -1)
            ::PyErr_Clear();
#else
        rc = ::PyObject_HasAttrString(obj.ptr(), *name);
#endif
        if (rc == 1)
            CALLBACK_RETURN_HANDLED(v8::Integer::New(info.GetIsolate(), v8::None));

        if (::PyMapping_Check(obj.ptr()))
        {
#if PY_VERSION_HEX >= 0x030d0000
            rc = ::PyMapping_HasKeyStringWithError(obj.ptr(), *name);
            if (rc == -1)
                ::PyErr_Clear();
#else
            rc = ::PyMapping_HasKeyString(obj.ptr(), *name);
#endif
            if (rc == 1)
                CALLBACK_RETURN_HANDLED(v8::Integer::New(info.GetIsolate(), v8::None));
        }
    }

    CALLBACK_RETURN_NOT_HANDLED(v8::Integer::New(info.GetIsolate(), v8::None));
    END_HANDLE_EXCEPTION(v8::Handle<v8::Integer>())
}

v8::Intercepted CPythonObject::NamedDeleter(v8::Local<v8::Name> prop, const v8::PropertyCallbackInfo<v8::Boolean>& info)
{
    v8::HandleScope handle_scope(info.GetIsolate());

    TRY_HANDLE_EXCEPTION(v8::Handle<v8::Boolean>())

    CPythonGIL python_gil;

    py::object obj = CJavascriptObject::Wrap(info.Holder());

    // Non-string keys (Symbols, ...) cannot be forwarded to Python by name, and
    // a failed Utf8Value conversion yields nullptr, which CPython would
    // dereference -- that used to crash with an access violation reading 0x0
    // inside python312.dll (see the NamedGetter/Setter/Query guards above).
    if (!prop->IsString())
        CALLBACK_RETURN_NOT_HANDLED(v8::False(info.GetIsolate()));

    v8::String::Utf8Value name(info.GetIsolate(), v8::Local<v8::String>::Cast(prop));
    if (*name == nullptr)
        CALLBACK_RETURN_NOT_HANDLED(v8::False(info.GetIsolate()));

    // `delete` on a hidden name reports failure and touches nothing.
    if (CPythonObject::IsJsHiddenName(obj.ptr(), *name))
        CALLBACK_RETURN_HANDLED(v8::False(info.GetIsolate()));

    // Deleting a name that resolves to a real prototype property succeeds as a
    // no-op, exactly like `delete el.scrollTop` in a browser -- the Python
    // attribute behind it must not be deleted instead.
    if (HasRealPrototypeProperty(info.Holder(), prop, info.GetIsolate()))
        CALLBACK_RETURN_HANDLED(v8::True(info.GetIsolate()));

    if (!::PyObject_HasAttrString(obj.ptr(), *name) &&
            ::PyMapping_Check(obj.ptr()))
    {
        int rc;

#if PY_VERSION_HEX >= 0x030d0000
        rc = ::PyMapping_HasKeyStringWithError(obj.ptr(), *name);
        if (rc == -1)
            ::PyErr_Clear();
#else
        rc = ::PyMapping_HasKeyString(obj.ptr(), *name);
#endif
        if (rc == 1)
        {
            CALLBACK_RETURN_HANDLED(-1 != ::PyMapping_DelItemString(obj.ptr(), *name));
        }
    }
    else
    {
#ifdef SUPPORT_PROPERTY
        py::object attr = obj.attr(*name);

        if (::PyObject_HasAttrString(obj.ptr(), *name) &&
                PyObject_TypeCheck(attr.ptr(), &::PyProperty_Type))
        {
            py::object deleter = attr.attr("fdel");

            if (deleter.is_none())
                throw CJavascriptException("can't delete attribute", ::PyExc_AttributeError);

            CALLBACK_RETURN_HANDLED(py::extract<bool>(deleter()));
        }
        else
        {
            CALLBACK_RETURN_HANDLED(-1 != ::PyObject_DelAttrString(obj.ptr(), *name));
        }
#else
        CALLBACK_RETURN_HANDLED(-1 != ::PyObject_DelAttrString(obj.ptr(), *name));
#endif
    }

    END_HANDLE_EXCEPTION(v8::Handle<v8::Boolean>())
}

#pragma GCC diagnostic ignored "-Wunused-result"

void CPythonObject::NamedEnumerator(const v8::PropertyCallbackInfo<v8::Array>& info)
{
    v8::HandleScope handle_scope(info.GetIsolate());

    TRY_HANDLE_EXCEPTION_NO_INTERCEPT(v8::Handle<v8::Array>())

    CPythonGIL python_gil;

    py::object obj = CJavascriptObject::Wrap(info.Holder());

    py::list keys;
    bool filter_name = false;

    if (::PySequence_Check(obj.ptr()))
    {
        CALLBACK_RETURN_NO_INTERCEPT(v8::Handle<v8::Array>());
    }
    else if (::PyMapping_Check(obj.ptr()))
    {
        keys = py::list(py::handle<>(PyMapping_Keys(obj.ptr())));
    }
    else if (PyGen_CheckExact(obj.ptr()))
    {
        py::object iter(py::handle<>(::PyObject_GetIter(obj.ptr())));

        PyObject *item = NULL;

        while (NULL != (item = ::PyIter_Next(iter.ptr())))
        {
            keys.append(py::object(py::handle<>(item)));
        }
    }
    else
    {
        keys = py::list(py::handle<>(::PyObject_Dir(obj.ptr())));
        filter_name = true;
    }

    Py_ssize_t len = PyList_GET_SIZE(keys.ptr());
    v8::Handle<v8::Array> result = v8::Array::New(info.GetIsolate(), len);

    if (len > 0)
    {
        for (Py_ssize_t i=0; i<len; i++)
        {
            PyObject *item = PyList_GET_ITEM(keys.ptr(), i);

            // Hidden names never show up in Object.keys() / for..in either.
            if (PyUnicode_Check(item) || PyBytes_Check(item))
            {
                py::str name(py::handle<>(py::borrowed(item)));

                if (CPythonObject::IsJsHiddenName(obj.ptr(), py::extract<std::string>(name)))
                    continue;
            }

            if (filter_name && PyBytes_CheckExact(item))
            {
                py::str name(py::handle<>(py::borrowed(item)));

                // FIXME Are there any methods to avoid such a dirty work?

                if (name.startswith("__") && name.endswith("__"))
                    continue;
            }

            result->Set(v8::Isolate::GetCurrent()->GetCurrentContext(), v8::Uint32::New(info.GetIsolate(), i), Wrap(py::object(py::handle<>(py::borrowed(item)))));
        }

        CALLBACK_RETURN_NO_INTERCEPT(result);
    }

    END_HANDLE_EXCEPTION_NO_INTERCEPT(v8::Handle<v8::Array>())
}

v8::Intercepted CPythonObject::IndexedGetter(uint32_t index, const v8::PropertyCallbackInfo<v8::Value>& info)
{
    v8::HandleScope handle_scope(info.GetIsolate());

    TRY_HANDLE_EXCEPTION(v8::Undefined(info.GetIsolate()));

    CPythonGIL python_gil;

    py::object obj = CJavascriptObject::Wrap(info.Holder());
    if (PyGen_Check(obj.ptr()))
        CALLBACK_RETURN_HANDLED(v8::Undefined(info.GetIsolate()));

    if (::PySequence_Check(obj.ptr()))
    {
        if ((Py_ssize_t) index < ::PySequence_Size(obj.ptr()))
        {
            py::object ret(py::handle<>(::PySequence_GetItem(obj.ptr(), index)));

            CALLBACK_RETURN_HANDLED(Wrap(ret));
        }
    }
    else if (::PyMapping_Check(obj.ptr()))
    {
        char buf[65];

        snprintf(buf, sizeof(buf), "%d", index);

        PyObject *value = ::PyMapping_GetItemString(obj.ptr(), buf);

        if (!value)
        {
            py::long_ key(index);

            value = ::PyObject_GetItem(obj.ptr(), key.ptr());
        }

        if (value)
        {
            CALLBACK_RETURN_HANDLED(Wrap(py::object(py::handle<>(value))));
        }
    }

    CALLBACK_RETURN_NOT_HANDLED(v8::Undefined(info.GetIsolate()));
    END_HANDLE_EXCEPTION(v8::Undefined(info.GetIsolate()))
}

v8::Intercepted CPythonObject::IndexedSetter(uint32_t index, v8::Local<v8::Value> value, const v8::PropertyCallbackInfo<void>& info)
{
    v8::HandleScope handle_scope(info.GetIsolate());

    TRY_HANDLE_EXCEPTION(v8::Undefined(info.GetIsolate()));

    CPythonGIL python_gil;

    py::object obj = CJavascriptObject::Wrap(info.Holder());

    if (::PySequence_Check(obj.ptr()))
    {
        if (::PySequence_SetItem(obj.ptr(), index, CJavascriptObject::Wrap(value).ptr()) < 0)
            info.GetIsolate()->ThrowException(v8::Exception::Error(v8::String::NewFromUtf8(info.GetIsolate(), "fail to set indexed value").ToLocalChecked()));
    }
    else if (::PyMapping_Check(obj.ptr()))
    {
        char buf[65];

        snprintf(buf, sizeof(buf), "%d", index);

        if (::PyMapping_SetItemString(obj.ptr(), buf, CJavascriptObject::Wrap(value).ptr()) < 0)
            info.GetIsolate()->ThrowException(v8::Exception::Error(v8::String::NewFromUtf8(info.GetIsolate(), "fail to set named value").ToLocalChecked()));
    }

    CALLBACK_RETURN_HANDLED(value);
    END_HANDLE_EXCEPTION(v8::Undefined(info.GetIsolate()))
}

v8::Intercepted CPythonObject::IndexedQuery(uint32_t index, const v8::PropertyCallbackInfo<v8::Integer>& info)
{
    v8::HandleScope handle_scope(info.GetIsolate());

    TRY_HANDLE_EXCEPTION(v8::Handle<v8::Integer>());

    CPythonGIL python_gil;

    py::object obj = CJavascriptObject::Wrap(info.Holder());

    if (PyGen_Check(obj.ptr()))
        CALLBACK_RETURN_HANDLED(v8::Integer::New(info.GetIsolate(), v8::ReadOnly));

    if (::PySequence_Check(obj.ptr()))
    {
        if ((Py_ssize_t) index < ::PySequence_Size(obj.ptr()))
        {
            CALLBACK_RETURN_HANDLED(v8::Integer::New(info.GetIsolate(), v8::None));
        }
    }
    else if (::PyMapping_Check(obj.ptr()))
    {
        char buf[65];
        int rc;

        snprintf(buf, sizeof(buf), "%d", index);

#if PY_VERSION_HEX >= 0x030d0000
        rc = ::PyMapping_HasKeyStringWithError(obj.ptr(), buf);
        if (rc == -1)
            ::PyErr_Clear();
#else
        rc = ::PyMapping_HasKeyString(obj.ptr(), buf);
#endif

        if (rc == 1 || ::PyMapping_HasKey(obj.ptr(), py::long_(index).ptr()))
        {
            CALLBACK_RETURN_HANDLED(v8::Integer::New(info.GetIsolate(), v8::None));
        }
    }

    CALLBACK_RETURN_NOT_HANDLED(v8::Integer::New(info.GetIsolate(), v8::None));
    END_HANDLE_EXCEPTION(v8::Handle<v8::Integer>())
}

v8::Intercepted CPythonObject::IndexedDeleter(uint32_t index, const v8::PropertyCallbackInfo<v8::Boolean>& info)
{
    v8::HandleScope handle_scope(info.GetIsolate());

    TRY_HANDLE_EXCEPTION(v8::Handle<v8::Boolean>());

    CPythonGIL python_gil;

    py::object obj = CJavascriptObject::Wrap(info.Holder());

    if (::PySequence_Check(obj.ptr()) && (Py_ssize_t) index < ::PySequence_Size(obj.ptr()))
    {
        CALLBACK_RETURN_HANDLED(0 <= ::PySequence_DelItem(obj.ptr(), index));
    }
    else if (::PyMapping_Check(obj.ptr()))
    {
        char buf[65];

        snprintf(buf, sizeof(buf), "%d", index);

        CALLBACK_RETURN_HANDLED(PyMapping_DelItemString(obj.ptr(), buf) == 0);
    }

    CALLBACK_RETURN_NOT_HANDLED(v8::Handle<v8::Boolean>())
    END_HANDLE_EXCEPTION(v8::Handle<v8::Boolean>())
}

void CPythonObject::IndexedEnumerator(const v8::PropertyCallbackInfo<v8::Array>& info)
{
    v8::HandleScope handle_scope(info.GetIsolate());

    TRY_HANDLE_EXCEPTION_NO_INTERCEPT(v8::Handle<v8::Array>());

    CPythonGIL python_gil;

    py::object obj = CJavascriptObject::Wrap(info.Holder());

    Py_ssize_t len = ::PySequence_Check(obj.ptr()) ? ::PySequence_Size(obj.ptr()) : 0;

    v8::Handle<v8::Array> result = v8::Array::New(info.GetIsolate(), len);

    v8::Local<v8::Context> context = info.GetIsolate()->GetCurrentContext();

    for (Py_ssize_t i=0; i<len; i++)
    {
        result->Set(context, v8::Integer::New(info.GetIsolate(), i), v8::Integer::New(info.GetIsolate(), i));
    }

    CALLBACK_RETURN_NO_INTERCEPT(result);

    END_HANDLE_EXCEPTION_NO_INTERCEPT(v8::Handle<v8::Array>())
}

#define GEN_ARG(z, n, data) CJavascriptObject::Wrap(info[n])
#define GEN_ARGS(count) BOOST_PP_ENUM(count, GEN_ARG, NULL)

#define GEN_CASE_PRED(r, state) \
  BOOST_PP_NOT_EQUAL( \
    BOOST_PP_TUPLE_ELEM(2, 0, state), \
    BOOST_PP_INC(BOOST_PP_TUPLE_ELEM(2, 1, state)) \
  ) \
  /**/

#define GEN_CASE_OP(r, state) \
  ( \
    BOOST_PP_INC(BOOST_PP_TUPLE_ELEM(2, 0, state)), \
    BOOST_PP_TUPLE_ELEM(2, 1, state) \
  ) \
  /**/

#define GEN_CASE_MACRO(r, state) \
  case BOOST_PP_TUPLE_ELEM(2, 0, state): { \
    result = self(GEN_ARGS(BOOST_PP_TUPLE_ELEM(2, 0, state))); \
    break; \
  } \
  /**/


void CPythonObject::Caller(const v8::FunctionCallbackInfo<v8::Value>& info)
{
    v8::HandleScope handle_scope(info.GetIsolate());

    TRY_HANDLE_EXCEPTION_NO_INTERCEPT(v8::Undefined(info.GetIsolate()));

    CPythonGIL python_gil;

    py::object self;

    if (!info.Data().IsEmpty() && info.Data()->IsExternal())
    {
        v8::Handle<v8::External> field = v8::Handle<v8::External>::Cast(info.Data());

        self = *static_cast<py::object *>(field->Value());
    }
    else
    {
        self = CJavascriptObject::Wrap(info.This());
    }

    py::object result;

    switch (info.Length())
    {
        BOOST_PP_FOR((0, 10), GEN_CASE_PRED, GEN_CASE_OP, GEN_CASE_MACRO)
    default:
        info.GetIsolate()->ThrowException(v8::Exception::Error(v8::String::NewFromUtf8(info.GetIsolate(), "too many arguments").ToLocalChecked()));

        CALLBACK_RETURN_NO_INTERCEPT(v8::Undefined(info.GetIsolate()));
    }

    CALLBACK_RETURN_NO_INTERCEPT(Wrap(result));

    END_HANDLE_EXCEPTION_NO_INTERCEPT(v8::Undefined(info.GetIsolate()))
}

// Call handler for a prototype method created by PopulatePrototypeMethods. The
// method name is captured (owned-forever External, same pattern as the External
// <cls> handed to Caller); `self` is bound from the JS receiver at call time, so
// `Class.prototype.method.apply(instance, args)` runs the Python method against
// that instance -- exactly what the instance interceptor does for `instance.method`.
void CPythonObject::PrototypeMethod(const v8::FunctionCallbackInfo<v8::Value>& info)
{
    v8::HandleScope handle_scope(info.GetIsolate());

    TRY_HANDLE_EXCEPTION_NO_INTERCEPT(v8::Undefined(info.GetIsolate()));

    CPythonGIL python_gil;

    if (info.Data().IsEmpty() || !info.Data()->IsExternal())
        CALLBACK_RETURN_NO_INTERCEPT(v8::Undefined(info.GetIsolate()));

    v8::Handle<v8::External> field = v8::Handle<v8::External>::Cast(info.Data());
    std::string name = *static_cast<std::string *>(field->Value());

    // Only forward when the receiver is a wrapped Python instance. Otherwise (e.g.
    // the method is invoked on the prototype object itself) Wrap().attr() would
    // re-enter the prototype interceptor and recurse infinitely.
    if (!CPythonObject::IsWrapped(info.This()))
    {
        info.GetIsolate()->ThrowException(v8::Exception::TypeError(v8::String::NewFromUtf8(
            info.GetIsolate(), (name + " called on a non-instance receiver").c_str()).ToLocalChecked()));
        CALLBACK_RETURN_NO_INTERCEPT(v8::Undefined(info.GetIsolate()));
    }

    // Bind self from the receiver at call time. `.attr()` may raise (e.g. a
    // non-Python `this`); the surrounding TRY_HANDLE_EXCEPTION turns that into a
    // JS exception, matching how a missing/wrong method behaves elsewhere.
    py::object self = CJavascriptObject::Wrap(info.This()).attr(name.c_str());

    py::object result;

    switch (info.Length())
    {
        BOOST_PP_FOR((0, 10), GEN_CASE_PRED, GEN_CASE_OP, GEN_CASE_MACRO)
    default:
        info.GetIsolate()->ThrowException(v8::Exception::Error(v8::String::NewFromUtf8(info.GetIsolate(), "too many arguments").ToLocalChecked()));

        CALLBACK_RETURN_NO_INTERCEPT(v8::Undefined(info.GetIsolate()));
    }

    CALLBACK_RETURN_NO_INTERCEPT(Wrap(result));

    END_HANDLE_EXCEPTION_NO_INTERCEPT(v8::Undefined(info.GetIsolate()))
}

// Prototype-level forwarder (interceptor). Exposes a Python class's instance
// methods on the JS prototype object so `Class.prototype.method` resolves and can
// be .apply()d to an instance. Implemented as a named-property INTERCEPTOR (not
// real own properties) on purpose: instance access keeps flowing through the
// instance interceptor (HasRealNamedProperty does NOT see interceptor-forwarded
// names, so HasRealPrototypeProperty stays false and there is no deferral), which
// avoids the infinite recursion that real own properties would cause.
v8::Intercepted CPythonObject::PrototypeNamedGetter(v8::Local<v8::Name> prop, const v8::PropertyCallbackInfo<v8::Value>& info)
{
    v8::HandleScope handle_scope(info.GetIsolate());

    TRY_HANDLE_EXCEPTION(v8::Undefined(info.GetIsolate()))

    CPythonGIL python_gil;

    v8::Local<v8::Context> ctx = info.GetIsolate()->GetCurrentContext();

    // Step aside for any REAL property anywhere on the prototype chain, INCLUDING
    // Object.prototype built-ins (hasOwnProperty/toString/valueOf/...): those must
    // resolve to the real JS property. Intercepting them would recurse, because the
    // receiver is the prototype object (not a wrapped Python instance) and
    // Wrap().attr() would re-enter this interceptor.
    {
        v8::Local<v8::Object> chain = info.Holder();
        while (!chain.IsEmpty())
        {
            v8::Maybe<bool> has = chain->HasRealNamedProperty(ctx, prop);
            if (has.IsJust() && has.FromJust())
                CALLBACK_RETURN_NOT_HANDLED(v8::Handle<v8::Value>());
            v8::Local<v8::Value> up = chain->GetPrototype();
            if (!up->IsObject())
                break;
            chain = up.As<v8::Object>();
        }
    }

    v8::String::Utf8Value name(info.GetIsolate(), v8::Local<v8::String>::Cast(prop));
    if (*name == nullptr)
        CALLBACK_RETURN_HANDLED(v8::Undefined(info.GetIsolate()));

    std::string prop_name(*name);
    if (prop_name == "constructor" || prop_name == "prototype")
        CALLBACK_RETURN_NOT_HANDLED(v8::Handle<v8::Value>());

    if (info.Data().IsEmpty() || !info.Data()->IsExternal())
        CALLBACK_RETURN_NOT_HANDLED(v8::Handle<v8::Value>());
    v8::Handle<v8::External> field = v8::Handle<v8::External>::Cast(info.Data());
    py::object cls = *static_cast<py::object *>(field->Value());

    PyObject *value = ::PyObject_GetAttrString(cls.ptr(), *name);
    if (!value)
    {
        if (PyErr_Occurred())
            ::PyErr_Clear();
        CALLBACK_RETURN_NOT_HANDLED(v8::Handle<v8::Value>());
    }
    py::object attr = py::object(py::handle<>(value));

    // A regular instance method -> return a self-binding function so that
    // Class.prototype.method.apply(instance, args) runs the Python method against
    // that instance (self is bound from the receiver at call time, in
    // PrototypeMethod). Everything else is wrapped as-is.
    if (PyFunction_Check(attr.ptr()))
    {
        std::string *method_name = new std::string(prop_name); // owned forever (External)
        v8::Local<v8::FunctionTemplate> fn_tmpl = v8::FunctionTemplate::New(info.GetIsolate());
        fn_tmpl->SetCallHandler(PrototypeMethod, v8::External::New(info.GetIsolate(), method_name));
        v8::MaybeLocal<v8::Function> fn = fn_tmpl->GetFunction(ctx);
        if (fn.IsEmpty())
            CALLBACK_RETURN_NOT_HANDLED(v8::Handle<v8::Value>());
        CALLBACK_RETURN_HANDLED(fn.ToLocalChecked());
    }

    CALLBACK_RETURN_HANDLED(Wrap(attr));

    END_HANDLE_EXCEPTION(v8::Undefined(info.GetIsolate()))
}

v8::Intercepted CPythonObject::PrototypeNamedQuery(v8::Local<v8::Name> prop, const v8::PropertyCallbackInfo<v8::Integer>& info)
{
    v8::HandleScope handle_scope(info.GetIsolate());

    TRY_HANDLE_EXCEPTION(v8::Handle<v8::Integer>())

    CPythonGIL python_gil;

    v8::Local<v8::Context> ctx = info.GetIsolate()->GetCurrentContext();

    // Step aside for any REAL property on the prototype chain (incl. Object.prototype
    // built-ins), same as the getter.
    {
        v8::Local<v8::Object> chain = info.Holder();
        while (!chain.IsEmpty())
        {
            v8::Maybe<bool> has = chain->HasRealNamedProperty(ctx, prop);
            if (has.IsJust() && has.FromJust())
                CALLBACK_RETURN_NOT_HANDLED(v8::Handle<v8::Integer>());
            v8::Local<v8::Value> up = chain->GetPrototype();
            if (!up->IsObject())
                break;
            chain = up.As<v8::Object>();
        }
    }

    v8::String::Utf8Value name(info.GetIsolate(), v8::Local<v8::String>::Cast(prop));
    if (*name == nullptr)
        CALLBACK_RETURN_NOT_HANDLED(v8::Handle<v8::Integer>());

    std::string prop_name(*name);
    if (prop_name == "constructor" || prop_name == "prototype")
        CALLBACK_RETURN_NOT_HANDLED(v8::Handle<v8::Integer>());

    if (info.Data().IsEmpty() || !info.Data()->IsExternal())
        CALLBACK_RETURN_NOT_HANDLED(v8::Handle<v8::Integer>());
    v8::Handle<v8::External> field = v8::Handle<v8::External>::Cast(info.Data());
    py::object cls = *static_cast<py::object *>(field->Value());

    // Report forwarded members as non-enumerable, like real JS prototype methods,
    // so `"open" in XHR.prototype` is true without polluting Object.keys().
    if (::PyObject_HasAttrString(cls.ptr(), *name))
        CALLBACK_RETURN_HANDLED(v8::Integer::New(info.GetIsolate(), v8::DontEnum));

    CALLBACK_RETURN_NOT_HANDLED(v8::Handle<v8::Integer>());

    END_HANDLE_EXCEPTION(v8::Handle<v8::Integer>())
}

void CPythonObject::PopulatePrototypeMethods(v8::Isolate *isolate, py::object cls, v8::Local<v8::FunctionTemplate> tmpl)
{
    CPythonGIL python_gil;

    // Skip templates that did NOT Inherit -- i.e. those with no non-builtins
    // ancestor, whose base is Python object (JSClass itself, and object). For such
    // a template V8 defaults the prototype's [[Prototype]] straight to
    // Object.prototype; calling PrototypeTemplate()+SetHandler then materializes it
    // as a SEPARATE object, inserting a spurious level into the chain (the `?` in
    // testClassHierarchy) and hiding Object.prototype from the chain walk. Classes
    // that DO inherit from a user base (the normal case -- anything deriving from
    // JSClass) are handled normally below.
    bool inherited = false;
    PyObject *mro = ::PyObject_GetAttrString(cls.ptr(), "__mro__");
    if (mro && PyTuple_Check(mro))
    {
        Py_ssize_t count = PyTuple_GET_SIZE(mro);
        for (Py_ssize_t i = 1; i < count && !inherited; ++i)
        {
            PyObject *base = PyTuple_GET_ITEM(mro, i); // borrowed
            PyObject *mod = ::PyObject_GetAttrString(base, "__module__");
            bool base_builtins = false;
            if (mod)
            {
                base_builtins = PyUnicode_Check(mod) &&
                                std::string(PyUnicode_AsUTF8(mod)) == "builtins";
                Py_DECREF(mod);
            }
            else
            {
                PyErr_Clear();
            }
            if (!base_builtins)
                inherited = true;
        }
    }
    Py_XDECREF(mro);

    if (!inherited)
        return;

    // Attach the prototype forwarder. The class is handed to the callbacks via the
    // handler data (same owned-forever External pattern as the CallHandler).
    v8::Local<v8::ObjectTemplate> proto = tmpl->PrototypeTemplate();
    proto->SetHandler(v8::NamedPropertyHandlerConfiguration(
        PrototypeNamedGetter, nullptr, PrototypeNamedQuery, nullptr, nullptr,
        v8::External::New(isolate, new py::object(cls))));
}

void CPythonObject::SetupObjectTemplate(v8::Isolate *isolate, v8::Handle<v8::ObjectTemplate> clazz)
{
    v8::HandleScope handle_scope(isolate);

    clazz->SetInternalFieldCount(1);

    clazz->SetHandler(v8::NamedPropertyHandlerConfiguration(
                          NamedGetter,
                          NamedSetter,
                          NamedQuery,
                          NamedDeleter,
                          NamedEnumerator)
                     );

    clazz->SetHandler(v8::IndexedPropertyHandlerConfiguration(
                          IndexedGetter,
                          IndexedSetter,
                          IndexedQuery,
                          IndexedDeleter,
                          IndexedEnumerator
                      )
                     );

    clazz->SetCallAsFunctionHandler(Caller);
}

v8::Handle<v8::ObjectTemplate> CPythonObject::CreateObjectTemplate(v8::Isolate *isolate)
{
    v8::EscapableHandleScope handle_scope(isolate);

    v8::Local<v8::ObjectTemplate> clazz = v8::ObjectTemplate::New(isolate);

    SetupObjectTemplate(isolate, clazz);

    return handle_scope.Escape(clazz);
}

v8::Local<v8::ObjectTemplate> CPythonObject::GetObjectTemplate(v8::Isolate *isolate)
{
    // One template per isolate: a v8::ObjectTemplate is bound to the isolate
    // that created it, so sharing a single (function-local static) template
    // across isolates makes V8 clone a cached instantiation whose map belongs to
    // a foreign isolate (tripping `is_clonable_js_type`).
    static std::mutex s_mutex;
    // v8::Persistent is non-copyable (and non-movable) in this V8, so it cannot
    // be moved into a map element; hold it behind a unique_ptr instead.
    static std::unordered_map<v8::Isolate *, std::unique_ptr<v8::Persistent<v8::ObjectTemplate>>> s_cache;

    std::lock_guard<std::mutex> lock(s_mutex);

    auto it = s_cache.find(isolate);

    if (it == s_cache.end())
    {
        it = s_cache.emplace(
            isolate,
            std::make_unique<v8::Persistent<v8::ObjectTemplate>>(
                isolate, CreateObjectTemplate(isolate))
        ).first;
    }

    return v8::Local<v8::ObjectTemplate>::New(isolate, *it->second);
}

namespace
{
struct ClassTemplateEntry
{
    // Keeps the class alive so its address stays a stable cache key.
    py::object cls;
    std::unique_ptr<v8::Persistent<v8::FunctionTemplate>> tmpl;
};
}  // namespace

v8::Local<v8::FunctionTemplate> CPythonObject::GetClassTemplate(v8::Isolate *isolate, py::object cls)
{
    // A v8::FunctionTemplate is bound to the isolate that created it, so cache
    // one per (isolate, class). recursive_mutex because building a template
    // recurses into the templates of its base classes.
    static std::recursive_mutex s_mutex;
    static std::unordered_map<
        v8::Isolate *,
        std::unordered_map<PyObject *, ClassTemplateEntry>> s_cache;

    std::lock_guard<std::recursive_mutex> lock(s_mutex);

    v8::EscapableHandleScope handle_scope(isolate);

    auto &per_isolate = s_cache[isolate];
    auto it = per_isolate.find(cls.ptr());
    if (it != per_isolate.end())
    {
        return handle_scope.Escape(
            v8::Local<v8::FunctionTemplate>::New(isolate, *it->second.tmpl));
    }

    v8::Local<v8::FunctionTemplate> tmpl = v8::FunctionTemplate::New(isolate);

    std::string cls_name = py::extract<std::string>(cls.attr("__name__"));
    tmpl->SetClassName(
        v8::String::NewFromUtf8(isolate, cls_name.c_str()).ToLocalChecked());

    // Mirror the Python MRO into the JS prototype chain. V8 templates support
    // single inheritance, so inherit from the nearest ancestor that is a user
    // class (skip builtins such as int/str/object).
    PyObject *mro = ::PyObject_GetAttrString(cls.ptr(), "__mro__");
    if (mro && PyTuple_Check(mro))
    {
        Py_ssize_t count = PyTuple_GET_SIZE(mro);
        for (Py_ssize_t i = 1; i < count; ++i)
        {
            py::object base(py::borrowed<>(PyTuple_GET_ITEM(mro, i)));
            std::string module = py::extract<std::string>(base.attr("__module__"));

            if (module == "builtins")
                continue;

            tmpl->Inherit(GetClassTemplate(isolate, base));
            break;
        }
    }
    Py_XDECREF(mro);

    // Calling the exposed class instantiates the Python class (as before). The
    // class *function* stays callable regardless of whether its instances are.
    tmpl->SetCallHandler(Caller, v8::External::New(isolate, new py::object(cls)));

    // `typeof` is "function" only for genuinely callable objects. Make the
    // instances callable only when the Python class defines __call__ (tp_call
    // is set); a plain JSClass instance then reports typeof "object", matching
    // standard JS semantics. Previously every wrapped object was callable, so
    // `typeof` was always "function".
    bool instances_callable =
        PyType_Check(cls.ptr()) &&
        reinterpret_cast<PyTypeObject *>(cls.ptr())->tp_call != nullptr;

    v8::Local<v8::ObjectTemplate> inst = tmpl->InstanceTemplate();
    inst->SetInternalFieldCount(1);
    inst->SetHandler(v8::NamedPropertyHandlerConfiguration(
        NamedGetter, NamedSetter, NamedQuery, NamedDeleter, NamedEnumerator));
    inst->SetHandler(v8::IndexedPropertyHandlerConfiguration(
        IndexedGetter, IndexedSetter, IndexedQuery, IndexedDeleter, IndexedEnumerator));
    if (instances_callable)
        inst->SetCallAsFunctionHandler(Caller);

    // Expose the class's instance methods on the JS prototype object too (see the
    // long note in Wrapper.h). Real own properties, only for classes that define
    // user methods -- so it does not disturb prototype-chain walks, does not
    // shadow defineProperty'd accessors, and leaves method-less base prototypes
    // mapping straight onto Object.prototype.
    PopulatePrototypeMethods(isolate, cls, tmpl);

    per_isolate.emplace(
        cls.ptr(),
        ClassTemplateEntry{
            cls, std::make_unique<v8::Persistent<v8::FunctionTemplate>>(isolate, tmpl)});

    return handle_scope.Escape(tmpl);
}

void CPythonObject::PopulateStaticProperties(v8::Isolate *isolate, py::object cls, v8::Local<v8::Object> func)
{
    // Expose the Python class's own attributes (class/static attributes,
    // classmethods, staticmethods, nested classes, ...) as own properties on the
    // constructor function. V8 creates one function object per context, so a
    // fresh function in a new context lacks the marker below and gets populated
    // again; within a context we populate only once.
    v8::Local<v8::Context> ctx = isolate->GetCurrentContext();

    v8::Local<v8::Private> marker =
        v8::Private::ForApi(isolate,
            v8::String::NewFromUtf8(isolate, "stpyv8_statics_initialized").ToLocalChecked());

    v8::MaybeLocal<v8::Value> existing = func->GetPrivate(ctx, marker);
    if (!existing.IsEmpty() && existing.ToLocalChecked()->IsTrue())
        return;

    // Names that must stay native on the function object.
    static const char *kReserved[] = {
        "prototype", "constructor", "name", "length", "arguments", "caller", nullptr
    };

    CPythonGIL python_gil;

    py::list names(py::handle<>(::PyObject_Dir(cls.ptr())));
    Py_ssize_t n = PyList_GET_SIZE(names.ptr());

    for (Py_ssize_t i = 0; i < n; ++i)
    {
        PyObject *nameobj = PyList_GET_ITEM(names.ptr(), i);
        if (!PyUnicode_Check(nameobj))
            continue;

        const char *cname = ::PyUnicode_AsUTF8(nameobj);
        if (!cname)
            continue;

        std::string name_str(cname);

        // Skip dunder names so we don't surface Python internals as statics.
        if (name_str.size() >= 4 &&
                name_str.compare(0, 2, "__") == 0 &&
                name_str.compare(name_str.size() - 2, 2, "__") == 0)
            continue;

        bool reserved = false;
        for (const char **r = kReserved; *r; ++r)
        {
            if (name_str == *r) { reserved = true; break; }
        }
        if (reserved)
            continue;

        if (!::PyObject_HasAttrString(cls.ptr(), cname))
            continue;

        v8::Local<v8::Value> wrapped = Wrap(cls.attr(cname));
        if (!wrapped.IsEmpty())
            func->Set(ctx, v8::String::NewFromUtf8(isolate, cname).ToLocalChecked(), wrapped);
    }

    func->SetPrivate(ctx, marker, v8::True(isolate));
}

bool CPythonObject::IsWrapped(v8::Handle<v8::Object> obj)
{
    return obj->InternalFieldCount() == 1;
}

py::object CPythonObject::Unwrap(v8::Handle<v8::Object> obj)
{
    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::HandleScope handle_scope(isolate);

    v8::Handle<v8::External> payload = v8::Handle<v8::External>::Cast(obj->GetInternalField(0));

    return *static_cast<py::object *>(payload->Value());
}

void CPythonObject::Dispose(v8::Handle<v8::Value> value)
{
    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::HandleScope handle_scope(isolate);

    if (value->IsObject())
    {
        v8::MaybeLocal<v8::Object> objMaybe = value->ToObject(isolate->GetCurrentContext());

        if (objMaybe.IsEmpty())
        {
            return;
        }

        v8::Handle<v8::Object> obj = objMaybe.ToLocalChecked();

        if (IsWrapped(obj))
        {
            Py_DECREF(CPythonObject::Unwrap(obj).ptr());
        }
    }
}

v8::Handle<v8::Value> CPythonObject::Wrap(py::object obj)
{
    v8::EscapableHandleScope handle_scope(v8::Isolate::GetCurrent());

    // NOTE: the living-map lookup deliberately lives inside WrapInternal, *after*
    // the CJavascriptObject branch. A CJavascriptObject already has a JS identity
    // of its own and must be handed back as-is; consulting the cache for it used
    // to return a stand-in proxy instead (see the comment there).
    return handle_scope.Escape(WrapInternal(obj));
}

v8::Handle<v8::Value> CPythonObject::WrapInternal(py::object obj)
{
    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    assert(isolate->InContext());

    v8::EscapableHandleScope handle_scope(isolate);
    v8::TryCatch try_catch(isolate);

    CPythonGIL python_gil;

    TERMINATE_EXECUTION_CHECK(v8::Undefined(isolate))

    if (obj.is_none()) return v8::Null(isolate);
    if (obj.ptr() == Py_True) return v8::True(isolate);
    if (obj.ptr() == Py_False) return v8::False(isolate);

    py::extract<CJavascriptObject&> extractor(obj);

    if (extractor.check())
    {
        CJavascriptObject& jsobj = extractor();

        if (dynamic_cast<CJavascriptNull *>(&jsobj)) return v8::Null(isolate);
        if (dynamic_cast<CJavascriptUndefined *>(&jsobj)) return v8::Undefined(isolate);

        if (jsobj.Object().IsEmpty())
        {
            ILazyObject *pLazyObject = dynamic_cast<ILazyObject *>(&jsobj);

            if (pLazyObject) pLazyObject->LazyConstructor();
        }

        if (jsobj.Object().IsEmpty())
        {
            throw CJavascriptException("Refer to a null object", ::PyExc_AttributeError);
        }

        // A CJavascriptObject already *is* a JS object, so hand back the very
        // object it wraps -- never a stand-in for it.
        //
        // This branch used to mint a fresh proxy from GetObjectTemplate and
        // register it in the living map, while still returning the real object.
        // Because Wrap() consults that map first, every wrap after the first
        // returned the proxy: a Python-backed object reporting
        // `typeof "function"` and `instanceof Set === false`, whose inherited
        // methods failed with "incompatible receiver". The proxy was only weakly
        // referenced, so whether a given read hit the cache depended on GC
        // timing -- hence the intermittency.
        //
        // That is what broke React 17's getEventListenerSet(), which stores a
        // native Set on a DOM node and reads it back on the very next event.
        // Objects nested inside a plain JS object were spared because reading
        // them is a pure V8 load that never round-trips through Python.
        return handle_scope.Escape(jsobj.Object());
    }

#ifdef SUPPORT_TRACE_LIFECYCLE
    // Everything below mints a JS object for a Python object that has no JS
    // identity of its own. Reuse the one handed out before so that
    // `Wrap(x) === Wrap(x)` (stable identity for constructors, functions and
    // JSClass instances).
    v8::Local<v8::Value> cached = ObjectTracer::FindCache(obj);

    if (!cached.IsEmpty())
        return handle_scope.Escape(cached);
#endif

    v8::Local<v8::Value> result;

    if (PyLong_CheckExact(obj.ptr()))
    {
        result = v8::Integer::New(isolate, ::PyLong_AsLong(obj.ptr()));
    }
    else if (PyBool_Check(obj.ptr()))
    {
        result = v8::Boolean::New(isolate, py::extract<bool>(obj));
    }
    else if (PyBytes_CheckExact(obj.ptr()) || PyUnicode_CheckExact(obj.ptr()))
    {
        result = ToString(obj);
    }
    else if (PyFloat_CheckExact(obj.ptr()))
    {
        result = v8::Number::New(isolate, py::extract<double>(obj));
    }
    else if (PyDateTime_CheckExact(obj.ptr()) || PyDate_CheckExact(obj.ptr()))
    {
        tm ts = { 0 };

        ts.tm_year = PyDateTime_GET_YEAR(obj.ptr()) - 1900;
        ts.tm_mon = PyDateTime_GET_MONTH(obj.ptr()) - 1;
        ts.tm_mday = PyDateTime_GET_DAY(obj.ptr());
        ts.tm_hour = PyDateTime_DATE_GET_HOUR(obj.ptr());
        ts.tm_min = PyDateTime_DATE_GET_MINUTE(obj.ptr());
        ts.tm_sec = PyDateTime_DATE_GET_SECOND(obj.ptr());
        ts.tm_isdst = -1;

        int ms = PyDateTime_DATE_GET_MICROSECOND(obj.ptr());

        result = v8::Date::New(isolate->GetCurrentContext(), ((double) mktime(&ts)) * 1000 + ms / 1000).ToLocalChecked();
    }
    else if (PyTime_CheckExact(obj.ptr()))
    {
        tm ts = { 0 };

        ts.tm_hour = PyDateTime_TIME_GET_HOUR(obj.ptr()) - 1;
        ts.tm_min = PyDateTime_TIME_GET_MINUTE(obj.ptr());
        ts.tm_sec = PyDateTime_TIME_GET_SECOND(obj.ptr());

        int ms = PyDateTime_TIME_GET_MICROSECOND(obj.ptr());

        result = v8::Date::New(isolate->GetCurrentContext(), ((double) mktime(&ts)) * 1000 + ms / 1000).ToLocalChecked();
    }
    else if (PyType_Check(obj.ptr()))
    {
        // A Python class: expose it as its (cached) constructor function -- the
        // very template used to instantiate its objects -- so that `instanceof`
        // and `x.constructor === C` stay consistent.
        v8::Local<v8::FunctionTemplate> tmpl = GetClassTemplate(isolate, obj);

        v8::Local<v8::Function> func =
            tmpl->GetFunction(isolate->GetCurrentContext()).ToLocalChecked();

        // Expose the class's own attributes as static properties on the
        // constructor (e.g. `XMLHttpRequest.UNSENT`).
        PopulateStaticProperties(isolate, obj, func);

        result = func;

#ifdef SUPPORT_TRACE_LIFECYCLE
        if (!result.IsEmpty()) ObjectTracer::Trace(result, new py::object(obj));
#endif
    }
    else if (PyCFunction_Check(obj.ptr()) || PyFunction_Check(obj.ptr()) || PyMethod_Check(obj.ptr()))
    {
        v8::Handle<v8::FunctionTemplate> func_tmpl = v8::FunctionTemplate::New(isolate);
        py::object *object = new py::object(obj);

        func_tmpl->SetCallHandler(Caller, v8::External::New(isolate, object));

        result = func_tmpl->GetFunction(isolate->GetCurrentContext()).ToLocalChecked();

#ifdef SUPPORT_TRACE_LIFECYCLE
        if (!result.IsEmpty()) ObjectTracer::Trace(result, object);
#endif
    }
    else
    {
        // Any other object (JSClass instances, dicts, ...): instantiate the
        // cached template of its Python class so the JS prototype chain mirrors
        // the Python MRO and `instanceof` works.
        //
        // Instantiate through the InstanceTemplate (NOT GetFunction()->NewInstance):
        // the class template carries a call handler (so calling the exposed
        // class instantiates the Python class), and Function::NewInstance would
        // run that handler -- which re-instantiates the Python object and
        // recurses back into Wrap. ObjectTemplate::NewInstance just allocates the
        // object with the template's prototype chain, which is what we want.
        py::object cls = obj.attr("__class__");
        v8::Local<v8::FunctionTemplate> tmpl = GetClassTemplate(isolate, cls);

        v8::MaybeLocal<v8::Object> instance =
            tmpl->InstanceTemplate()->NewInstance(isolate->GetCurrentContext());

        if (!instance.IsEmpty())
        {
            py::object *object = new py::object(obj);

            v8::Handle<v8::Object> realInstance = instance.ToLocalChecked();
            realInstance->SetInternalField(0, v8::External::New(isolate, object));

#ifdef SUPPORT_TRACE_LIFECYCLE
            ObjectTracer::Trace(instance.ToLocalChecked(), object);
#endif

            result = realInstance;
        }

    }

    if (result.IsEmpty()) CJavascriptException::ThrowIf(isolate, try_catch);

    return handle_scope.Escape(result);
}

void CJavascriptObject::CheckAttr(v8::Handle<v8::String> name) const
{
    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    assert(isolate->InContext());

    v8::HandleScope handle_scope(isolate);
    v8::Local<v8::Context> context = isolate->GetCurrentContext();

    if (!Object()->Has(context, name).FromMaybe(false))
    {
        std::ostringstream msg;

        msg << "'" << *v8::String::Utf8Value(isolate, Object()->ObjectProtoToString(context).ToLocalChecked())
            << "' object has no attribute '"
            << *v8::String::Utf8Value(isolate, name) << "'";

        throw CJavascriptException(msg.str(), ::PyExc_AttributeError);
    }
}

py::object CJavascriptObject::GetAttr(const std::string& name)
{
    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::HandleScope handle_scope(isolate);

    CHECK_V8_CONTEXT();

    v8::Local<v8::Context> context = isolate->GetCurrentContext();

    v8::TryCatch try_catch(isolate);

    v8::Handle<v8::String> attr_name = DecodeUtf8(name);

    CheckAttr(attr_name);

    v8::Handle<v8::Value> attr_value = Object()->Get(context, attr_name).ToLocalChecked();

    if (attr_value.IsEmpty())
        CJavascriptException::ThrowIf(isolate, try_catch);

    return CJavascriptObject::Wrap(attr_value, Object());
}

void CJavascriptObject::SetAttr(const std::string& name, py::object value)
{
    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::HandleScope handle_scope(isolate);

    CHECK_V8_CONTEXT();

    v8::Local<v8::Context> context = isolate->GetCurrentContext();

    v8::TryCatch try_catch(isolate);

    v8::Handle<v8::String> attr_name = DecodeUtf8(name);
    v8::Handle<v8::Value> attr_obj = CPythonObject::Wrap(value);

    if (Object()->Has(context, attr_name).FromMaybe(false))
    {
        v8::Handle<v8::Value> UNUSED_VAR(attr_value) = Object()->Get(context, attr_name).ToLocalChecked();
    }

    if (!Object()->Set(context, attr_name, attr_obj).FromMaybe(false))
        CJavascriptException::ThrowIf(isolate, try_catch);
}

void CJavascriptObject::DelAttr(const std::string& name)
{
    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::HandleScope handle_scope(isolate);

    CHECK_V8_CONTEXT();

    v8::Local<v8::Context> context = isolate->GetCurrentContext();

    v8::TryCatch try_catch(isolate);

    v8::Handle<v8::String> attr_name = DecodeUtf8(name);

    CheckAttr(attr_name);

    if (!Object()->Delete(context, attr_name).FromMaybe(false))
        CJavascriptException::ThrowIf(isolate, try_catch);
}

py::list CJavascriptObject::GetAttrList(void)
{
    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::HandleScope handle_scope(isolate);

    CHECK_V8_CONTEXT();

    CPythonGIL python_gil;

    py::list attrs;

    TERMINATE_EXECUTION_CHECK(attrs);

    v8::Local<v8::Context> context = isolate->GetCurrentContext();

    v8::TryCatch try_catch(isolate);

    v8::Handle<v8::Array> props = Object()->GetPropertyNames(context).ToLocalChecked();

    for (size_t i=0; i<props->Length(); i++)
    {
        attrs.append(CJavascriptObject::Wrap(props->Get(context, v8::Integer::New(isolate, i)).ToLocalChecked()));
    }

    if (try_catch.HasCaught()) CJavascriptException::ThrowIf(isolate, try_catch);

    return attrs;
}

int CJavascriptObject::GetIdentityHash(void)
{
    v8::HandleScope handle_scope(v8::Isolate::GetCurrent());
    CHECK_V8_CONTEXT();

    return Object()->GetIdentityHash();
}

CJavascriptObjectPtr CJavascriptObject::Clone(void)
{
    v8::HandleScope handle_scope(v8::Isolate::GetCurrent());
    CHECK_V8_CONTEXT();

    return CJavascriptObjectPtr(new CJavascriptObject(Object()->Clone()));
}

bool CJavascriptObject::Contains(const std::string& name)
{
    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::HandleScope handle_scope(isolate);

    CHECK_V8_CONTEXT();

    v8::Local<v8::Context> context = isolate->GetCurrentContext();

    v8::TryCatch try_catch(isolate);

    bool found = Object()->Has(context, DecodeUtf8(name)).ToChecked();

    if (try_catch.HasCaught()) CJavascriptException::ThrowIf(isolate, try_catch);

    return found;
}

bool CJavascriptObject::Equals(CJavascriptObjectPtr other) const
{
    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::HandleScope handle_scope(isolate);

    CHECK_V8_CONTEXT();

    v8::Local<v8::Context> context = isolate->GetCurrentContext();

    return other.get() && Object()->Equals(context, other->Object()).ToChecked();
}

void CJavascriptObject::Dump(std::ostream& os) const
{
    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::HandleScope handle_scope(isolate);

    CHECK_V8_CONTEXT();

    v8::Local<v8::Context> context = isolate->GetCurrentContext();

    if (m_obj.IsEmpty())
        os << "None";
    else if (Object()->IsInt32())
        os << Object()->Int32Value(context).ToChecked();
    else if (Object()->IsNumber())
        os << Object()->NumberValue(context).ToChecked();
    else if (Object()->IsBoolean())
        os << Object()->BooleanValue(isolate);
    else if (Object()->IsNull())
        os << "None";
    else if (Object()->IsUndefined())
        os << "N/A";
    else if (Object()->IsString())
        os << *v8::String::Utf8Value(isolate, v8::Handle<v8::String>::Cast(Object()));
    else
    {
        v8::MaybeLocal<v8::String> s = Object()->ToString(context);
        if(s.IsEmpty())
            s = Object()->ObjectProtoToString(context);

        if(!s.IsEmpty())
            os << *v8::String::Utf8Value(isolate, s.ToLocalChecked());
    }
}

const std::string CJavascriptObject::str(void) const
{
    std::ostringstream oss;

    Dump(oss);

    return oss.str();
}

CJavascriptObject::operator long() const
{
    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::HandleScope handle_scope(isolate);

    CHECK_V8_CONTEXT();

    v8::Local<v8::Context> context = isolate->GetCurrentContext();

    if (m_obj.IsEmpty())
        throw CJavascriptException("argument must be a string or a number, not 'NoneType'", ::PyExc_TypeError);

    return Object()->Int32Value(context).ToChecked();
}

CJavascriptObject::operator double() const
{
    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::HandleScope handle_scope(isolate);

    CHECK_V8_CONTEXT();

    v8::Local<v8::Context> context = isolate->GetCurrentContext();

    if (m_obj.IsEmpty())
        throw CJavascriptException("argument must be a string or a number, not 'NoneType'", ::PyExc_TypeError);

    return Object()->NumberValue(context).ToChecked();
}

CJavascriptObject::operator bool() const
{
    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::HandleScope handle_scope(isolate);

    CHECK_V8_CONTEXT();

    if (m_obj.IsEmpty()) return false;

    return Object()->BooleanValue(isolate);
}

py::object CJavascriptObject::Wrap(v8::Handle<v8::Value> value, v8::Handle<v8::Object> self)
{
    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    assert(isolate->InContext());

    v8::HandleScope handle_scope(isolate);

    if (value.IsEmpty() || value->IsNull() || value->IsUndefined())
    {
        return py::object();
    }
    if (value->IsTrue())
    {
        return py::object(py::handle<>(py::borrowed(Py_True)));
    }
    if (value->IsFalse())
    {
        return py::object(py::handle<>(py::borrowed(Py_False)));
    }
    if (value->IsInt32())
    {
        return py::object(value->Int32Value(isolate->GetCurrentContext()).ToChecked());
    }
    if (value->IsString())
    {
        v8::String::Utf8Value str(isolate, v8::Handle<v8::String>::Cast(value));

        return py::str(*str, str.length());
    }
    if (value->IsStringObject())
    {
        v8::String::Utf8Value str(isolate, value.As<v8::StringObject>()->ValueOf());

        return py::str(*str, str.length());
    }
    if (value->IsBoolean())
    {
        return py::object(py::handle<>(py::borrowed(value->BooleanValue(isolate) ? Py_True : Py_False)));
    }
    if (value->IsBooleanObject())
    {
        return py::object(py::handle<>(py::borrowed(value.As<v8::BooleanObject>()->BooleanValue(isolate) ? Py_True : Py_False)));
    }
    if (value->IsNumber())
    {
        return py::object(py::handle<>(::PyFloat_FromDouble(value->NumberValue(isolate->GetCurrentContext()).ToChecked())));
    }
    if (value->IsNumberObject())
    {
        return py::object(py::handle<>(::PyFloat_FromDouble(value.As<v8::NumberObject>()->NumberValue(isolate->GetCurrentContext()).ToChecked())));
    }
    if (value->IsDate())
    {
        double n = v8::Handle<v8::Date>::Cast(value)->NumberValue(isolate->GetCurrentContext()).ToChecked();

        if (!std::isnan(n))
        {
            time_t ts = (time_t) floor(n / 1000);

            tm *t = localtime(&ts);

            return py::object(py::handle<>(::PyDateTime_FromDateAndTime(
                                               t->tm_year+1900, t->tm_mon+1, t->tm_mday, t->tm_hour, t->tm_min, t->tm_sec,
                                               ((long long) floor(n)) % 1000 * 1000)));
        }
    }

    return Wrap(value->ToObject(isolate->GetCurrentContext()).ToLocalChecked(), self);
}

py::object CJavascriptObject::Wrap(v8::Handle<v8::Object> obj, v8::Handle<v8::Object> self)
{
    v8::HandleScope handle_scope(v8::Isolate::GetCurrent());

    if (obj.IsEmpty())
    {
        return py::object();
    }
    else if (obj->IsArray())
    {
        v8::Handle<v8::Array> array = v8::Handle<v8::Array>::Cast(obj);

        return Wrap(new CJavascriptArray(array));
    }
    else if (CPythonObject::IsWrapped(obj))
    {
        return CPythonObject::Unwrap(obj);
    }
    else if (obj->IsFunction())
    {
        return Wrap(new CJavascriptFunction(self, v8::Handle<v8::Function>::Cast(obj)));
    }

    return Wrap(new CJavascriptObject(obj));
}

py::object CJavascriptObject::Wrap(CJavascriptObject *obj)
{
    CPythonGIL python_gil;

    TERMINATE_EXECUTION_CHECK(py::object())

    return py::object(py::handle<>(boost::python::converter::shared_ptr_to_python<CJavascriptObject>(CJavascriptObjectPtr(obj))));
}

void CJavascriptArray::LazyConstructor(void)
{
    if (!m_obj.IsEmpty()) return;

    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::HandleScope handle_scope(isolate);
    v8::Local<v8::Context> context = isolate->GetCurrentContext();

    v8::Handle<v8::Array> array;

    if (m_items.is_none())
    {
        array = v8::Array::New(isolate, m_size);
    }
    else if (PyLong_CheckExact(m_items.ptr()))
    {
        m_size = PyLong_AsLong(m_items.ptr());
        array = v8::Array::New(isolate, m_size);
    }
    else if (PyList_Check(m_items.ptr()))
    {
        m_size = PyList_GET_SIZE(m_items.ptr());
        array = v8::Array::New(isolate, m_size);

        for (Py_ssize_t i=0; i< (Py_ssize_t) m_size; i++)
        {
            array->Set(context, v8::Uint32::New(isolate, i), CPythonObject::Wrap(py::object(py::handle<>(py::borrowed(PyList_GET_ITEM(m_items.ptr(), i))))));
        }
    }
    else if (PyTuple_Check(m_items.ptr()))
    {
        m_size = PyTuple_GET_SIZE(m_items.ptr());
        array = v8::Array::New(isolate, m_size);

        for (Py_ssize_t i=0; i< (Py_ssize_t) m_size; i++)
        {
            array->Set(context, v8::Uint32::New(isolate, i), CPythonObject::Wrap(py::object(py::handle<>(py::borrowed(PyTuple_GET_ITEM(m_items.ptr(), i))))));
        }
    }
    else if (PyGen_Check(m_items.ptr()))
    {
        array = v8::Array::New(isolate);

        py::object iter(py::handle<>(::PyObject_GetIter(m_items.ptr())));

        m_size = 0;
        PyObject *item = NULL;

        while (NULL != (item = ::PyIter_Next(iter.ptr())))
        {
            array->Set(context, v8::Uint32::New(isolate, m_size++), CPythonObject::Wrap(py::object(py::handle<>(py::borrowed(item)))));
        }
    }

    m_obj.Reset(isolate, array);
}

size_t CJavascriptArray::Length(void)
{
    LazyConstructor();

    v8::HandleScope handle_scope(v8::Isolate::GetCurrent());
    CHECK_V8_CONTEXT();

    return v8::Handle<v8::Array>::Cast(Object())->Length();
}

py::object CJavascriptArray::GetItem(py::object key)
{
    LazyConstructor();

    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::HandleScope handle_scope(isolate);

    CHECK_V8_CONTEXT();

    v8::Local<v8::Context> context = isolate->GetCurrentContext();

    v8::TryCatch try_catch(isolate);

    if (PySlice_Check(key.ptr()))
    {
        Py_ssize_t arrayLen = v8::Handle<v8::Array>::Cast(Object())->Length();
        Py_ssize_t start, stop, step, sliceLen;

        if (0 == PySlice_GetIndicesEx(PySlice_Cast(key.ptr()), arrayLen, &start, &stop, &step, &sliceLen))
        {
            py::list slice;

            for (Py_ssize_t idx=start; idx<stop; idx+=step)
            {
                v8::Handle<v8::Value> value = Object()->Get(context, v8::Integer::New(isolate, (uint32_t) idx)).ToLocalChecked();

                if (value.IsEmpty()) CJavascriptException::ThrowIf(isolate, try_catch);

                slice.append(CJavascriptObject::Wrap(value, Object()));
            }

            return std::move(slice);
        }
    }
    else if (PyInt_Check(key.ptr()) || PyLong_Check(key.ptr()))
    {
        uint32_t idx = PyInt_Check(key.ptr()) ? (uint32_t) ::PyInt_AsUnsignedLongMask(key.ptr()) : (uint32_t) ::PyLong_AsUnsignedLong(key.ptr());

        if (!Object()->Has(context,idx).ToChecked()) return py::object();

        v8::Handle<v8::Value> value = Object()->Get(context, v8::Integer::New(isolate, idx)).ToLocalChecked();

        if (value.IsEmpty()) CJavascriptException::ThrowIf(isolate, try_catch);

        return CJavascriptObject::Wrap(value, Object());
    }

    throw CJavascriptException("list indices must be integers", ::PyExc_TypeError);
}

py::object CJavascriptArray::SetItem(py::object key, py::object value)
{
    LazyConstructor();

    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::HandleScope handle_scope(isolate);

    CHECK_V8_CONTEXT();

    v8::Local<v8::Context> context = isolate->GetCurrentContext();

    v8::TryCatch try_catch(isolate);

    if (PySlice_Check(key.ptr()))
    {
        PyObject *values = ::PySequence_Fast(value.ptr(), "can only assign an iterable");

        if (values)
        {
            Py_ssize_t itemSize = PySequence_Fast_GET_SIZE(value.ptr());
            PyObject **items = PySequence_Fast_ITEMS(value.ptr());

            Py_ssize_t arrayLen = v8::Handle<v8::Array>::Cast(Object())->Length();
            Py_ssize_t start, stop, step, sliceLen;

            PySlice_Unpack(PySlice_Cast(key.ptr()), &start, &stop, &step);

            /*
             * If the slice start is greater than the array length we append null elements
             * to the tail of the array to fill the gap
             */
            if (start > arrayLen) {
                for (Py_ssize_t idx = arrayLen; idx < start; idx++) {
                    Object()->Set(context, (uint32_t) (arrayLen + idx), v8::Null(isolate));
                }

                arrayLen = v8::Handle<v8::Array>::Cast(Object())->Length();
            }

            /*
             * If the slice stop is greater than the array length (which was potentially
             * modified by the previous check) we append null elements to the tail of the
             * array. This step guarantees that the length of the array will always be
             * greater or equal than stop
             */
            if (stop > arrayLen) {
                for (Py_ssize_t idx = arrayLen; idx < stop; idx++) {
                    Object()->Set(context, (uint32_t) idx, v8::Null(isolate));
                }

                arrayLen = v8::Handle<v8::Array>::Cast(Object())->Length();
            }

            if (0 == PySlice_GetIndicesEx(PySlice_Cast(key.ptr()), arrayLen, &start, &stop, &step, &sliceLen)) {
                if (itemSize != sliceLen)
                {
                    if (itemSize < sliceLen)
                    {
                        Py_ssize_t diff = sliceLen - itemSize;

                        for (Py_ssize_t idx = start + itemSize; idx < arrayLen - diff; idx++)
                        {
                            Object()->Set(context, idx, Object()->Get(context, (uint32_t) (idx + diff)).ToLocalChecked());
                        }
                        for (Py_ssize_t idx = arrayLen - 1; idx > arrayLen - diff - 1; idx--)
                        {
                            Object()->Delete(context, (uint32_t) idx);
                        }
                    }
                    else if (itemSize > sliceLen)
                    {
                        Py_ssize_t diff = itemSize - sliceLen;

                        for (Py_ssize_t idx = arrayLen + diff - 1; idx > stop - 1; idx--)
                        {
                            Object()->Set(context, idx, Object()->Get(context, (uint32_t) (idx - diff)).ToLocalChecked());
                        }
                    }
                }

                for (Py_ssize_t idx = 0; idx < itemSize; idx++)
                {
                    Object()->Set(context, (uint32_t) (start + idx * step), CPythonObject::Wrap(py::object(py::handle<>(py::borrowed(items[idx])))));
                }
            }
        }
    }
    else if (PyInt_Check(key.ptr()) || PyLong_Check(key.ptr()))
    {
        uint32_t idx = PyInt_Check(key.ptr()) ? (uint32_t) ::PyInt_AsUnsignedLongMask(key.ptr()) : (uint32_t) ::PyLong_AsUnsignedLong(key.ptr());

        if (!Object()->Set(context, v8::Integer::New(isolate, idx), CPythonObject::Wrap(value)).ToChecked())
            CJavascriptException::ThrowIf(isolate, try_catch);
    }

    return value;
}

py::object CJavascriptArray::DelItem(py::object key)
{
    LazyConstructor();

    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::HandleScope handle_scope(isolate);

    CHECK_V8_CONTEXT();

    v8::Local<v8::Context> context = isolate->GetCurrentContext();

    v8::TryCatch try_catch(isolate);

    if (PySlice_Check(key.ptr()))
    {
        Py_ssize_t arrayLen = v8::Handle<v8::Array>::Cast(Object())->Length();
        Py_ssize_t start, stop, step, sliceLen;

        if (0 == PySlice_GetIndicesEx(PySlice_Cast(key.ptr()), arrayLen, &start, &stop, &step, &sliceLen))
        {
            for (Py_ssize_t idx = start; idx < stop; idx += step)
            {
                Object()->Delete(context, (uint32_t)idx);
            }
        }

        return py::object();
    }
    else if (PyInt_Check(key.ptr()) || PyLong_Check(key.ptr()))
    {
        uint32_t idx = PyInt_Check(key.ptr()) ? (uint32_t) ::PyInt_AsUnsignedLongMask(key.ptr()) : (uint32_t) ::PyLong_AsUnsignedLong(key.ptr());

        py::object value;

        if (Object()->Has(context, idx).ToChecked())
            value = CJavascriptObject::Wrap(Object()->Get(context, v8::Integer::New(isolate, idx)).ToLocalChecked(), Object());

        if (!Object()->Delete(context, idx).ToChecked())
            CJavascriptException::ThrowIf(isolate, try_catch);

        return value;
    }

    throw CJavascriptException("list indices must be integers", ::PyExc_TypeError);
}

bool CJavascriptArray::Contains(py::object item)
{
    LazyConstructor();

    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::HandleScope handle_scope(isolate);

    CHECK_V8_CONTEXT();

    v8::Local<v8::Context> context = isolate->GetCurrentContext();

    v8::TryCatch try_catch(isolate);

    for (size_t i=0; i<Length(); i++)
    {
        if (Object()->Has(context, i).ToChecked())
        {
            v8::Handle<v8::Value> value = Object()->Get(context, v8::Integer::New(isolate, i)).ToLocalChecked();

            if (try_catch.HasCaught()) CJavascriptException::ThrowIf(isolate, try_catch);

            if (item == CJavascriptObject::Wrap(value, Object()))
            {
                return true;
            }
        }
    }

    if (try_catch.HasCaught()) CJavascriptException::ThrowIf(isolate, try_catch);

    return false;
}

py::object CJavascriptFunction::CallWithArgs(py::tuple args, py::dict kwds)
{
    size_t argc = ::PyTuple_Size(args.ptr());

    if (argc == 0) throw CJavascriptException("missed self argument", ::PyExc_TypeError);

    py::object self = args[0];
    py::extract<CJavascriptFunction&> extractor(self);

    if (!extractor.check()) throw CJavascriptException("missed self argument", ::PyExc_TypeError);

    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::HandleScope handle_scope(isolate);
    CHECK_V8_CONTEXT();

    v8::TryCatch try_catch(isolate);

    CJavascriptFunction& func = extractor();
    py::list argv(args.slice(1, py::_));

    return func.Call(func.Self(), argv, kwds);
}

py::object CJavascriptFunction::Call(v8::Handle<v8::Object> self, py::list args, py::dict kwds)
{
    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::HandleScope handle_scope(isolate);

    CHECK_V8_CONTEXT();

    v8::Local<v8::Context> context = isolate->GetCurrentContext();

    v8::TryCatch try_catch(isolate);

    v8::Handle<v8::Function> func = v8::Handle<v8::Function>::Cast(Object());

    size_t args_count = ::PyList_Size(args.ptr()), kwds_count = ::PyMapping_Size(kwds.ptr());

    std::vector< v8::Handle<v8::Value> > params(args_count + kwds_count);

    for (size_t i=0; i<args_count; i++)
    {
        params[i] = CPythonObject::Wrap(args[i]);
    }

    py::list values = kwds.values();

    for (size_t i=0; i<kwds_count; i++)
    {
        params[args_count+i] = CPythonObject::Wrap(values[i]);
    }

    v8::MaybeLocal<v8::Value> result;

    Py_BEGIN_ALLOW_THREADS

    result = func->Call(context,
                        self.IsEmpty() ? isolate->GetCurrentContext()->Global() : self,
                        params.size(), params.empty() ? NULL : &params[0]);

    Py_END_ALLOW_THREADS

    if (result.IsEmpty()) CJavascriptException::ThrowIf(isolate, try_catch);

    return CJavascriptObject::Wrap(result.ToLocalChecked());
}

py::object CJavascriptFunction::CreateWithArgs(CJavascriptFunctionPtr proto, py::tuple args, py::dict kwds)
{
    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::HandleScope handle_scope(isolate);

    CHECK_V8_CONTEXT();

    if (proto->Object().IsEmpty())
        throw CJavascriptException("Object prototype may only be an Object", ::PyExc_TypeError);

    v8::Local<v8::Context> context = isolate->GetCurrentContext();

    v8::TryCatch try_catch(isolate);

    v8::Handle<v8::Function> func = v8::Handle<v8::Function>::Cast(proto->Object());

    size_t args_count = ::PyTuple_Size(args.ptr());

    std::vector< v8::Handle<v8::Value> > params(args_count);

    for (size_t i=0; i<args_count; i++)
    {
        params[i] = CPythonObject::Wrap(args[i]);
    }

    v8::Handle<v8::Object> result;

    Py_BEGIN_ALLOW_THREADS

    result = func->NewInstance(context, params.size(), params.empty() ? NULL : &params[0]).ToLocalChecked();

    Py_END_ALLOW_THREADS

    if (result.IsEmpty()) CJavascriptException::ThrowIf(isolate, try_catch);

    size_t kwds_count = ::PyMapping_Size(kwds.ptr());
    py::list items = kwds.items();

    for (size_t i=0; i<kwds_count; i++)
    {
        py::tuple item(items[i]);

        py::str key(item[0]);
        py::object value = item[1];

        result->Set(context, ToString(key), CPythonObject::Wrap(value));
    }

    return CJavascriptObject::Wrap(result);
}

py::object CJavascriptFunction::ApplyJavascript(CJavascriptObjectPtr self, py::list args, py::dict kwds)
{
    v8::HandleScope handle_scope(v8::Isolate::GetCurrent());
    CHECK_V8_CONTEXT();

    return Call(self->Object(), args, kwds);
}

py::object CJavascriptFunction::ApplyPython(py::object self, py::list args, py::dict kwds)
{
    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::HandleScope handle_scope(isolate);

    CHECK_V8_CONTEXT();

    v8::Local<v8::Context> context = isolate->GetCurrentContext();

    return Call(CPythonObject::Wrap(self)->ToObject(context).ToLocalChecked(), args, kwds);
}

py::object CJavascriptFunction::Invoke(py::list args, py::dict kwds)
{
    v8::HandleScope handle_scope(v8::Isolate::GetCurrent());
    CHECK_V8_CONTEXT();

    return Call(Self(), args, kwds);
}

const std::string CJavascriptFunction::GetName(void) const
{
    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::HandleScope handle_scope(isolate);

    CHECK_V8_CONTEXT();

    v8::Handle<v8::Function> func = v8::Handle<v8::Function>::Cast(Object());

    v8::String::Utf8Value name(isolate, v8::Handle<v8::String>::Cast(func->GetName()));

    return std::string(*name, name.length());
}

void CJavascriptFunction::SetName(const std::string& name)
{
    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::HandleScope handle_scope(isolate);

    CHECK_V8_CONTEXT();

    v8::Handle<v8::Function> func = v8::Handle<v8::Function>::Cast(Object());

    func->SetName(v8::String::NewFromUtf8(isolate, name.c_str(), v8::NewStringType::kNormal, name.size()).ToLocalChecked());
}

int CJavascriptFunction::GetLineNumber(void) const
{
    v8::HandleScope handle_scope(v8::Isolate::GetCurrent());
    CHECK_V8_CONTEXT();

    v8::Handle<v8::Function> func = v8::Handle<v8::Function>::Cast(Object());

    return func->GetScriptLineNumber();
}

int CJavascriptFunction::GetColumnNumber(void) const
{
    v8::HandleScope handle_scope(v8::Isolate::GetCurrent());
    CHECK_V8_CONTEXT();

    v8::Handle<v8::Function> func = v8::Handle<v8::Function>::Cast(Object());

    return func->GetScriptColumnNumber();
}

const std::string CJavascriptFunction::GetResourceName(void) const
{
    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::HandleScope handle_scope(isolate);
    CHECK_V8_CONTEXT();

    v8::Handle<v8::Function> func = v8::Handle<v8::Function>::Cast(Object());

    v8::String::Utf8Value name(isolate, v8::Handle<v8::String>::Cast(func->GetScriptOrigin().ResourceName()));

    return std::string(*name, name.length());
}

const std::string CJavascriptFunction::GetInferredName(void) const
{
    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::HandleScope handle_scope(isolate);
    CHECK_V8_CONTEXT();

    v8::Handle<v8::Function> func = v8::Handle<v8::Function>::Cast(Object());

    v8::String::Utf8Value name(isolate, v8::Handle<v8::String>::Cast(func->GetInferredName()));

    return std::string(*name, name.length());
}

py::object CJavascriptFunction::GetOwner(void) const
{
    v8::HandleScope handle_scope(v8::Isolate::GetCurrent());
    CHECK_V8_CONTEXT();

    return CJavascriptObject::Wrap(Self());
}

#ifdef SUPPORT_TRACE_LIFECYCLE

ObjectTracer::ObjectTracer(v8::Handle<v8::Value> handle, py::object *object)
    : m_handle(v8::Isolate::GetCurrent(), handle),
      m_object(object), m_living(GetLivingMapping())
{
}

ObjectTracer::~ObjectTracer()
{
    if (!m_handle.IsEmpty())
    {
        Dispose();

        m_living->erase(m_object->ptr());
    }
}

void ObjectTracer::Dispose(void)
{
    m_handle.ClearWeak();
    m_handle.Reset();
}

ObjectTracer& ObjectTracer::Trace(v8::Handle<v8::Value> handle, py::object *object)
{
    std::unique_ptr<ObjectTracer> tracer(new ObjectTracer(handle, object));

    tracer->Trace();

    return *tracer.release();
}

void ObjectTracer::Trace(void)
{
    m_handle.SetWeak(this, WeakCallback, v8::WeakCallbackType::kParameter);

    m_living->insert(std::make_pair(m_object->ptr(), this));
}


void ObjectTracer::WeakCallback(const v8::WeakCallbackInfo<ObjectTracer>& info)
{
    CPythonGIL python_gil;

    std::unique_ptr<ObjectTracer> tracer(info.GetParameter());
}

LivingMap * ObjectTracer::GetLivingMapping(void)
{
    v8::HandleScope handle_scope(v8::Isolate::GetCurrent());

    v8::Handle<v8::Context> ctxt = v8::Isolate::GetCurrent()->GetCurrentContext();

    v8::Local<v8::String> key = v8::String::NewFromUtf8(v8::Isolate::GetCurrent(), "__living__").ToLocalChecked();
    v8::Local<v8::Private> privateKey = v8::Private::ForApi(v8::Isolate::GetCurrent(), key);
    v8::Maybe<bool> result = ctxt->Global()->HasPrivate(ctxt, privateKey);
    v8::MaybeLocal<v8::Value> value;

    if (!(result.IsJust() && result.FromJust()))
        value = v8::MaybeLocal<v8::Value>();
    else
        value = ctxt->Global()->GetPrivate(ctxt, privateKey);

    if (!value.IsEmpty())
    {
        LivingMap *living = (LivingMap *) v8::External::Cast(*value.ToLocalChecked())->Value();

        if (living) return living;
    }

    std::unique_ptr<LivingMap> living(new LivingMap());

    ctxt->Global()->SetPrivate(ctxt, privateKey, v8::External::New(v8::Isolate::GetCurrent(), living.get()));

    ContextTracer::Trace(ctxt, living.get());

    return living.release();
}

v8::Handle<v8::Value> ObjectTracer::FindCache(py::object obj)
{
    LivingMap *living = GetLivingMapping();

    if (living)
    {
        LivingMap::const_iterator it = living->find(obj.ptr());

        if (it != living->end())
        {
            return v8::Local<v8::Value>::New(v8::Isolate::GetCurrent(), it->second->m_handle);
        }
    }

    return v8::Handle<v8::Value>();
}

ContextTracer::ContextTracer(v8::Handle<v8::Context> ctxt, LivingMap *living)
    : m_ctxt(v8::Isolate::GetCurrent(), ctxt), m_living(living)
{
}

ContextTracer::~ContextTracer(void)
{
    v8::HandleScope handle_scope(v8::Isolate::GetCurrent());

    v8::Handle<v8::Context> ctxt = v8::Isolate::GetCurrent()->GetCurrentContext();
    v8::Local<v8::String> key = v8::String::NewFromUtf8(v8::Isolate::GetCurrent(), "__living__").ToLocalChecked();
    v8::Local<v8::Private> privateKey = v8::Private::ForApi(v8::Isolate::GetCurrent(), key);

    Context()->Global()->DeletePrivate(ctxt, privateKey);

    for (LivingMap::const_iterator it = m_living->begin(); it != m_living->end(); it++)
    {
        std::unique_ptr<ObjectTracer> tracer(it->second);

        tracer->Dispose();
    }
}

void ContextTracer::WeakCallback(const v8::WeakCallbackInfo<ContextTracer>& info)
{
    delete info.GetParameter();
}

void ContextTracer::Trace(v8::Handle<v8::Context> ctxt, LivingMap *living)
{
    ContextTracer *tracer = new ContextTracer(ctxt, living);

    tracer->Trace();
}

void ContextTracer::Trace(void)
{
    m_ctxt.SetWeak(this, WeakCallback, v8::WeakCallbackType::kParameter);
}

#endif // SUPPORT_TRACE_LIFECYCLE
