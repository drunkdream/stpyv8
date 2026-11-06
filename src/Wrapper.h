//TODO make this an ifdef guard
#pragma once

#include <memory>
#include <iostream>
#include <map>
#include <mutex>
#include <set>
#include <unordered_map>
#include <sstream>

#include "Exception.h"

#include <boost/iterator/iterator_facade.hpp>

class CJavascriptObject;
class CJavascriptFunction;

typedef std::shared_ptr<CJavascriptObject> CJavascriptObjectPtr;
typedef std::shared_ptr<CJavascriptFunction> CJavascriptFunctionPtr;

class CJavascriptObject;

struct CWrapper
{
    static void Expose(void);
};

class CPythonObject
{
private:
    CPythonObject();
    virtual ~CPythonObject();

public:
    static v8::Intercepted NamedGetter(v8::Local<v8::Name> prop, const v8::PropertyCallbackInfo<v8::Value>& info);
    static v8::Intercepted NamedSetter(v8::Local<v8::Name> prop, v8::Local<v8::Value> value, const v8::PropertyCallbackInfo<void>& info);
    static v8::Intercepted NamedQuery(v8::Local<v8::Name> prop, const v8::PropertyCallbackInfo<v8::Integer>& info);
    static v8::Intercepted NamedDeleter(v8::Local<v8::Name> prop, const v8::PropertyCallbackInfo<v8::Boolean>& info);
    static void NamedEnumerator(const v8::PropertyCallbackInfo<v8::Array>& info);

    static v8::Intercepted IndexedGetter(uint32_t index, const v8::PropertyCallbackInfo<v8::Value>& info);
    static v8::Intercepted IndexedSetter(uint32_t index, v8::Local<v8::Value> value, const v8::PropertyCallbackInfo<void>& info);
    static v8::Intercepted IndexedQuery(uint32_t index, const v8::PropertyCallbackInfo<v8::Integer>& info);
    static v8::Intercepted IndexedDeleter(uint32_t index, const v8::PropertyCallbackInfo<v8::Boolean>& info);
    static void IndexedEnumerator(const v8::PropertyCallbackInfo<v8::Array>& info);

    static void Caller(const v8::FunctionCallbackInfo<v8::Value>& info);

    // The class's OWN Python members are installed as REAL properties on the
    // function template's PrototypeTemplate (see PopulatePrototypeMethods):
    // @property -> native V8 AccessorPair, methods -> data property with a
    // self-binding FunctionTemplate, constants -> data property.
    //
    // Real properties (rather than interceptor-synthesised "virtual" ones) are
    // required because V8's Annex B __lookupGetter__/__lookupSetter__ walk the
    // chain with PROTOTYPE_CHAIN_SKIP_INTERCEPTOR and cannot see virtual members.
    // They also make all six ordinary internal methods consistent without any
    // callback coordination. Inherited members come from tmpl->Inherit(base).
    //
    // The accessor/method callbacks apply a WebIDL brand check
    // (ResolveBrandedReceiver): a non-instance receiver throws
    // "Illegal invocation", matching Chrome's Element.prototype.scrollTop.
    static void PrototypeMethod(const v8::FunctionCallbackInfo<v8::Value>& info);
    static void PrototypeAccessorGet(const v8::FunctionCallbackInfo<v8::Value>& info);
    static void PrototypeAccessorSet(const v8::FunctionCallbackInfo<v8::Value>& info);
    static void PopulatePrototypeMethods(v8::Isolate *isolate, py::object cls, v8::Local<v8::FunctionTemplate> tmpl);

#ifdef SUPPORT_TRACE_LIFECYCLE
    static void DisposeCallback(v8::Persistent<v8::Value> object, void* parameter);
#endif

    static void SetupObjectTemplate(v8::Isolate *isolate, v8::Handle<v8::ObjectTemplate> clazz);
    static v8::Handle<v8::ObjectTemplate> CreateObjectTemplate(v8::Isolate *isolate);
    // Returns the ObjectTemplate belonging to `isolate`, creating (and caching)
    // one on first use. A v8::ObjectTemplate may only be used with the isolate
    // that created it, so it must never be shared across isolates.
    static v8::Local<v8::ObjectTemplate> GetObjectTemplate(v8::Isolate *isolate);

    // Returns (creating and caching per isolate) a FunctionTemplate modelling
    // the given Python class. Instances get this class's prototype -- so the JS
    // prototype chain mirrors the Python MRO and `instanceof` works -- and the
    // class itself is exposed as the matching constructor function.
    static v8::Local<v8::FunctionTemplate> GetClassTemplate(v8::Isolate *isolate, py::object cls);

    // Exposes the Python class's own attributes (class/static attributes,
    // classmethods, staticmethods, nested classes, ...) as own properties on the
    // given constructor function, so that e.g. `XMLHttpRequest.UNSENT` reads the
    // class attribute directly instead of having to be mounted by hand in JS.
    // V8 creates one function object per context, so a fresh function in a new
    // context is re-populated; within a context we do it only once.
    static void PopulateStaticProperties(v8::Isolate *isolate, py::object cls, v8::Local<v8::Object> func);

    static v8::Handle<v8::Value> WrapInternal(py::object obj);

    static bool IsWrapped(v8::Handle<v8::Object> obj);

    // True when the Python type of `obj` lists `name` in `__js_hidden__`.
    // Such names are reported to JS as if they did not exist, which is the only
    // way to keep a Python attribute private: the named-property interceptors
    // forward the requested name verbatim to PyObject_GetAttrString /
    // PyObject_HasAttrString / PyObject_DelAttrString, i.e. JS is given exactly
    // the attribute access that Python's getattr()/hasattr()/delattr() have --
    // including Python's private-name mangling, which is a collision-avoidance
    // convention rather than an access control.
    static bool IsJsHiddenName(PyObject *obj, const std::string& name);

    static v8::Handle<v8::Value> Wrap(py::object obj);
    static py::object Unwrap(v8::Handle<v8::Object> obj);
    static void Dispose(v8::Handle<v8::Value> value);

    static void ThrowIf(v8::Isolate* isolate);
};

struct ILazyObject
{
    virtual void LazyConstructor(void) = 0;
};

class CJavascriptObject : public CWrapper
{
protected:
    v8::Persistent<v8::Object> m_obj;

    void CheckAttr(v8::Handle<v8::String> name) const;

    CJavascriptObject()
    {
    }
public:
    CJavascriptObject(v8::Handle<v8::Object> obj)
        : m_obj(v8::Isolate::GetCurrent(), obj)
    {
    }

    // v8::Persistent is no longer copyable (its default traits are
    // NonCopyablePersistentTraits), so an implicit copy constructor would
    // instantiate the deleted Copy helper. Re-create a new persistent handle
    // pointing at the same JS object instead.
    CJavascriptObject(const CJavascriptObject& other)
    {
        v8::Isolate* isolate = v8::Isolate::GetCurrent();
        v8::HandleScope handle_scope(isolate);
        m_obj.Reset(isolate, other.Object());
    }

    virtual ~CJavascriptObject()
    {
        m_obj.Reset();
    }

    v8::Local<v8::Object> Object(void) const {
        return v8::Local<v8::Object>::New(v8::Isolate::GetCurrent(), m_obj);
    }

    py::object GetAttr(const std::string& name);
    void SetAttr(const std::string& name, py::object value);
    void DelAttr(const std::string& name);

    py::list GetAttrList(void);
    int GetIdentityHash(void);
    CJavascriptObjectPtr Clone(void);

    bool Contains(const std::string& name);

    operator long() const;
    operator double() const;
    operator bool() const;

    bool Equals(CJavascriptObjectPtr other) const;
    bool Unequals(CJavascriptObjectPtr other) const {
        return !Equals(other);
    }

    void Dump(std::ostream& os) const;

    // Returns the same textual representation as Dump(). Registered explicitly
    // as Python's __str__: relying on boost::python's `str(self)` (which streams
    // the object) picks up the implicit conversion to double instead and prints
    // "nan" for every non-numeric value.
    const std::string str(void) const;

    static py::object Wrap(CJavascriptObject *obj);
    static py::object Wrap(v8::Handle<v8::Value> value,
                           v8::Handle<v8::Object> self = v8::Handle<v8::Object>());
    static py::object Wrap(v8::Handle<v8::Object> obj,
                           v8::Handle<v8::Object> self = v8::Handle<v8::Object>());
};

// boost::python's `str(self)` streams the object. Without this overload the
// implicit conversion to double (via `operator double()`) is selected instead,
// which prints "nan" for every value that is not a number.
std::ostream& operator<<(std::ostream& os, const CJavascriptObject& obj);

class CJavascriptNull : public CJavascriptObject
{
public:
    bool nonzero(void) const {
        return false;
    }
    const std::string str(void) const {
        return "null";
    }
};

class CJavascriptUndefined : public CJavascriptObject
{
public:
    bool nonzero(void) const {
        return false;
    }
    const std::string str(void) const {
        return "undefined";
    }
};

class CJavascriptArray : public CJavascriptObject, public ILazyObject
{
    py::object m_items;
    size_t m_size;
public:
    class ArrayIterator
        : public boost::iterator_facade<ArrayIterator, py::object const, boost::forward_traversal_tag, py::object>
    {
        CJavascriptArray *m_array;
        size_t m_idx;
    public:
        ArrayIterator(CJavascriptArray *array, size_t idx)
            : m_array(array), m_idx(idx)
        {
        }

        void increment() {
            m_idx++;
        }

        bool equal(ArrayIterator const& other) const {
            return m_array == other.m_array && m_idx == other.m_idx;
        }

        reference dereference() const {
            return m_array->GetItem(py::long_(m_idx));
        }
    };

    CJavascriptArray(v8::Handle<v8::Array> array)
        : CJavascriptObject(array), m_size(array->Length())
    {
    }

    CJavascriptArray(py::object items)
        : m_items(items), m_size(0)
    {
    }

    size_t Length(void);

    py::object GetItem(py::object key);
    py::object SetItem(py::object key, py::object value);
    py::object DelItem(py::object key);
    bool Contains(py::object item);

    ArrayIterator begin(void) {
        return ArrayIterator(this, 0);
    }
    ArrayIterator end(void) {
        return ArrayIterator(this, Length());
    }

    // ILazyObject
    virtual void LazyConstructor(void);
};

class CJavascriptFunction : public CJavascriptObject
{
    v8::Persistent<v8::Object> m_self;

    py::object Call(v8::Handle<v8::Object> self, py::list args, py::dict kwds);
public:
    CJavascriptFunction(v8::Handle<v8::Object> self, v8::Handle<v8::Function> func)
        : CJavascriptObject(func), m_self(v8::Isolate::GetCurrent(), self)
    {
    }

    ~CJavascriptFunction()
    {
        m_self.Reset();
    }

    v8::Handle<v8::Object> Self(void) const {
        return v8::Local<v8::Object>::New(v8::Isolate::GetCurrent(), m_self);
    }

    static py::object CallWithArgs(py::tuple args, py::dict kwds);
    static py::object CreateWithArgs(CJavascriptFunctionPtr proto, py::tuple args, py::dict kwds);

    py::object ApplyJavascript(CJavascriptObjectPtr self, py::list args, py::dict kwds);
    py::object ApplyPython(py::object self, py::list args, py::dict kwds);
    py::object Invoke(py::list args, py::dict kwds);

    const std::string GetName(void) const;
    void SetName(const std::string& name);

    int GetLineNumber(void) const;
    int GetColumnNumber(void) const;
    const std::string GetResourceName(void) const;
    const std::string GetInferredName(void) const;
    int GetLineOffset(void) const;
    int GetColumnOffset(void) const;

    py::object GetOwner(void) const;
};

#ifdef SUPPORT_TRACE_LIFECYCLE

class ObjectTracer;

typedef std::map<PyObject *, ObjectTracer *> LivingMap;

class ObjectTracer
{
    v8::Persistent<v8::Value> m_handle;
    std::unique_ptr<py::object> m_object;

    LivingMap *m_living;

    void Trace(void);

    static void WeakCallback(const v8::WeakCallbackInfo<ObjectTracer>& info);

    static LivingMap *GetLivingMapping(void);
public:
    ObjectTracer(v8::Handle<v8::Value> handle, py::object *object);
    ~ObjectTracer(void);

    const v8::Persistent<v8::Value>& Handle(void) const {
        return m_handle;
    }
    py::object *Object(void) const {
        return m_object.get();
    }

    void Dispose(void);

    static ObjectTracer& Trace(v8::Handle<v8::Value> handle, py::object *object);

    static v8::Handle<v8::Value> FindCache(py::object obj);
};

class ContextTracer
{
    v8::Persistent<v8::Context> m_ctxt;
    std::unique_ptr<LivingMap> m_living;

    void Trace(void);

    static void WeakCallback(const v8::WeakCallbackInfo<ContextTracer>& info);
public:
    ContextTracer(v8::Handle<v8::Context> ctxt, LivingMap *living);
    ~ContextTracer(void);

    v8::Handle<v8::Context> Context(void) const {
        return v8::Local<v8::Context>::New(v8::Isolate::GetCurrent(), m_ctxt);
    }

    static void Trace(v8::Handle<v8::Context> ctxt, LivingMap *living);
};

#endif
