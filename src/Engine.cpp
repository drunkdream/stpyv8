#include "Engine.h"
#include "Exception.h"
#include "Wrapper.h"

#include <cstdio>
#include <iostream>
#include <mutex>
#include <unordered_map>

#include <boost/preprocessor.hpp>
#include <boost/thread/mutex.hpp>
#include <boost/thread/locks.hpp>

void CEngine::Expose(void)
{
    py::class_<CEngine, boost::noncopyable>("JSEngine", "JSEngine is a backend Javascript engine.")
    .def(py::init<>("Create a new script engine instance."))
    .add_static_property("version", &CEngine::GetVersion,
                         "Get the V8 engine version.")

    .add_static_property("dead", &CEngine::IsDead,
                         "Check if V8 is dead and therefore unusable.")

    .def("setFlags", &CEngine::SetFlags, "Sets V8 flags from a string.")
    .staticmethod("setFlags")

    .def("terminateAllThreads", &CEngine::TerminateAllThreads,
         "Forcefully terminate the current thread of JavaScript execution.")
    .staticmethod("terminateAllThreads")

    .def("dispose", &v8::V8::Dispose,
         "Releases any resources used by v8 and stops any utility threads "
         "that may be running. Note that disposing v8 is permanent, "
         "it cannot be reinitialized.")
    .staticmethod("dispose")

    .def("lowMemory", &v8::Isolate::LowMemoryNotification,
         "Optional notification that the system is running low on memory.")
    .staticmethod("lowMemory")

    /*
        .def("setMemoryLimit", &CEngine::SetMemoryLimit, (py::arg("max_young_space_size") = 0,
                                                          py::arg("max_old_space_size") = 0,
                                                          py::arg("max_executable_size") = 0),
             "Specifies the limits of the runtime's memory use."
             "You must set the heap size before initializing the VM"
             "the size cannot be adjusted after the VM is initialized.")
        .staticmethod("setMemoryLimit")
    */

    .def("setStackLimit", &CEngine::SetStackLimit, (py::arg("stack_limit_size") = 0),
         "Uses the address of a local variable to determine the stack top now."
         "Given a size, returns an address that is that far from the current top of stack.")
    .staticmethod("setStackLimit")

    /*
        .def("setMemoryAllocationCallback", &MemoryAllocationManager::SetCallback,
                                            (py::arg("callback"),
                                             py::arg("space") = v8::kObjectSpaceAll,
                                             py::arg("action") = v8::kAllocationActionAll),
                                            "Enables the host application to provide a mechanism to be notified "
                                            "and perform custom logging when V8 Allocates Executable Memory.")
        .staticmethod("setMemoryAllocationCallback")
    */

    .def("compile", &CEngine::Compile, (py::arg("source"),
                                        py::arg("name") = std::string(),
                                        py::arg("line") = -1,
                                        py::arg("col") = -1))
    .def("compile", &CEngine::CompileW, (py::arg("source"),
                                         py::arg("name") = std::wstring(),
                                         py::arg("line") = -1,
                                         py::arg("col") = -1))

    .def("compileModule", &CEngine::CompileModule, (py::arg("source"),
                                                     py::arg("name") = std::string(),
                                                     py::arg("line") = -1,
                                                     py::arg("col") = -1,
                                                     py::arg("loader") = py::object()),
         "Compile source with the ES Module goal (import/export syntax). "
         "loader supplies the source of statically imported specifiers.")
    ;

    py::class_<CScript, boost::noncopyable>("JSScript", "JSScript is a compiled JavaScript script.", py::no_init)
    .add_property("source", &CScript::GetSource, "the source code")

    .def("run", &CScript::Run, "Execute the compiled code.")
    ;

    py::objects::class_value_wrapper<std::shared_ptr<CScript>,
    py::objects::make_ptr_instance<CScript,
    py::objects::pointer_holder<std::shared_ptr<CScript>, CScript> > >();

    py::class_<CModule, boost::noncopyable>("JSModule", "JSModule is a compiled ES module.", py::no_init)
    .add_property("source", &CModule::GetSource, "the source code")

    .def("run", &CModule::Run,
         "Instantiate and evaluate the module, then return its namespace object.")
    .def("run_async", &CModule::RunAsync,
         "Instantiate and evaluate, returning the settle Promise for host-driven "
         "top-level await.")
    .add_property("namespace", &CModule::Namespace,
                  "Exports namespace (available once the module is evaluated).")
    ;

    py::objects::class_value_wrapper<std::shared_ptr<CModule>,
    py::objects::make_ptr_instance<CModule,
    py::objects::pointer_holder<std::shared_ptr<CModule>, CModule> > >();
}

bool CEngine::IsDead(void)
{
    return v8::Isolate::GetCurrent()->IsDead();
}

void CEngine::TerminateAllThreads(void)
{
    v8::Isolate::GetCurrent()->TerminateExecution();
}

void CEngine::ReportFatalError(const char* location, const char* message)
{
    std::cerr << "<" << location << "> " << message << std::endl;
}

void CEngine::ReportMessage(v8::Handle<v8::Message> message, v8::Handle<v8::Value> data)
{
    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::Local<v8::Context> context = isolate->GetCurrentContext();

    v8::String::Utf8Value filename(isolate, message->GetScriptResourceName());
    int lineno = message->GetLineNumber(context).ToChecked();
    v8::String::Utf8Value sourceline(isolate, message->GetSourceLine(context).ToLocalChecked());

    std::cerr << *filename << ":" << lineno << " -> " << *sourceline << std::endl;
}

bool CEngine::SetMemoryLimit(int max_young_space_size, int max_old_space_size, int max_executable_size)
{
    v8::ResourceConstraints limit;

    if (max_young_space_size) limit.set_max_young_generation_size_in_bytes(max_young_space_size);
    if (max_old_space_size) limit.set_max_old_generation_size_in_bytes(max_old_space_size);
    //TODO should this be code range size instead?
    //if (max_executable_size) limit.set_max_executable_size(max_executable_size);

    //TODO - memory limits are now only settable on isolate creation
    //return v8::SetResourceConstraints(v8::Isolate::GetCurrent(), &limit);
    return false;
}

void CEngine::SetStackLimit(uintptr_t stack_limit_size)
{
    // This function uses a local stack variable to determine the isolate's stack limit
    uint32_t here;
    uintptr_t stack_limit = reinterpret_cast<uintptr_t>(&here) - stack_limit_size;

    // If the size is very large and the stack is very near the bottom of
    // memory then the calculation above may wrap around and give an address
    // that is above the (downwards-growing) stack. In that case we use a
    // very low address.
    if (stack_limit > reinterpret_cast<uintptr_t>(&here))
        stack_limit = stack_limit_size;

    v8::Isolate::GetCurrent()->SetStackLimit(stack_limit);
}

std::shared_ptr<CScript> CEngine::InternalCompile(v8::Handle<v8::String> src,
        v8::Handle<v8::Value> name,
        int line, int col)
{
    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::HandleScope handle_scope(isolate);
    v8::Local<v8::Context> context = isolate->GetCurrentContext();

    v8::TryCatch try_catch(isolate);

    v8::Persistent<v8::String> script_source(m_isolate, src);

    v8::MaybeLocal<v8::Script> script;
    v8::Handle<v8::String> source = v8::Local<v8::String>::New(m_isolate, script_source);

    Py_BEGIN_ALLOW_THREADS

    if (line >= 0 && col >= 0)
    {
        v8::ScriptOrigin script_origin(name, line, col);
        script = v8::Script::Compile(context, source, &script_origin);
    }
    else
    {
        v8::ScriptOrigin script_origin(name);
        script = v8::Script::Compile(context, source, &script_origin);
    }

    Py_END_ALLOW_THREADS

    if (script.IsEmpty()) CJavascriptException::ThrowIf(m_isolate, try_catch);

    return std::shared_ptr<CScript>(new CScript(m_isolate, *this, script_source, script.ToLocalChecked()));
}

py::object CEngine::ExecuteScript(v8::Handle<v8::Script> script)
{
    v8::Isolate* isolate = v8::Isolate::GetCurrent();
    v8::HandleScope handle_scope(isolate);
    v8::Local<v8::Context> context = isolate->GetCurrentContext();

    v8::TryCatch try_catch(isolate);

    v8::MaybeLocal<v8::Value> result;

    Py_BEGIN_ALLOW_THREADS

    result = script->Run(context);

    Py_END_ALLOW_THREADS

    if (result.IsEmpty())
    {
        if (try_catch.HasCaught())
        {
            if(!try_catch.CanContinue() && PyErr_Occurred())
            {
                throw py::error_already_set();
            }

            CJavascriptException::ThrowIf(m_isolate, try_catch);
        }

        result = v8::Null(m_isolate);
    }

    return CJavascriptObject::Wrap(result.ToLocalChecked());
}

const std::string CScript::GetSource(void) const
{
    v8::HandleScope handle_scope(m_isolate);

    v8::String::Utf8Value source(m_isolate, Source());

    return std::string(*source, source.length());
}

py::object CScript::Run(void)
{
    v8::HandleScope handle_scope(m_isolate);

    return m_engine.ExecuteScript(Script());
}

namespace
{
struct ModuleEntry
{
    std::string canonical;
    v8::Persistent<v8::Module> module;
};

struct ModuleState
{
    // One module graph per context (key = the context global's identity hash),
    // so evaluating in one context never clears another context's graph.
    std::unordered_map<int, std::vector<std::unique_ptr<ModuleEntry>>> graphs;

    // Raw source text cached per canonical specifier and shared across graphs
    // and contexts that use the same loader. Cleared whenever the loader
    // changes, so two loaders can never see each other's sources.
    std::unordered_map<std::string, std::string> sources;

    // Raw borrowed-to-owned pointer instead of py::object: this map has static
    // storage duration, so a py::object member would be destroyed *after* the
    // Python interpreter starts finalizing (Py_DECREF without the GIL -> fatal
    // error). References are managed explicitly by BeginGraph/Forget.
    PyObject *loader = nullptr;   // dict {specifier: source} / callable / None
};

std::recursive_mutex s_moduleMutex;
std::unordered_map<v8::Isolate *, std::unique_ptr<ModuleState>> s_moduleStates;

ModuleState& ModuleStateFor(v8::Isolate *isolate)
{
    auto it = s_moduleStates.find(isolate);

    if (it == s_moduleStates.end())
        it = s_moduleStates.emplace(isolate, std::make_unique<ModuleState>()).first;

    return *it->second;
}

int ContextIdentity(v8::Local<v8::Context> context)
{
    return context->Global()->GetIdentityHash();
}

// The module graph for the context currently running.
std::vector<std::unique_ptr<ModuleEntry>>& GraphFor(ModuleState& state, int identity)
{
    return state.graphs[identity];
}

// Resolve `./` and `../` relative to the referring module's own id. Bare
// specifiers and absolute URLs are handed to the host loader unchanged.
std::string NormalizeSpecifier(const std::string& specifier, const std::string& referrer)
{
    if (specifier.rfind("./", 0) != 0 && specifier.rfind("../", 0) != 0)
        return specifier;

    std::string base;
    size_t slash = referrer.find_last_of('/');

    if (slash != std::string::npos)
        base = referrer.substr(0, slash + 1);

    std::string joined = base + specifier;
    std::vector<std::string> parts;
    size_t start = 0;

    while (start <= joined.size())
    {
        size_t end = joined.find('/', start);
        std::string part = (end == std::string::npos)
            ? joined.substr(start) : joined.substr(start, end - start);

        if (!part.empty() && part != ".")
        {
            if (part == "..")
            {
                if (!parts.empty()) parts.pop_back();
            }
            else parts.push_back(part);
        }

        if (end == std::string::npos) break;
        start = end + 1;
    }

    std::string result;

    for (const std::string& part : parts)
    {
        if (!result.empty()) result += "/";
        result += part;
    }

    return result;
}

void ThrowModuleError(v8::Isolate *isolate, const std::string& message)
{
    isolate->ThrowException(v8::Exception::TypeError(
        v8::String::NewFromUtf8(isolate, message.c_str(), v8::NewStringType::kNormal,
                                static_cast<int>(message.size())).ToLocalChecked()));
}

// A Python exception raised by the loader cannot cross V8's C++ callback
// boundary; convert it into a JS exception carrying the Python message.
std::string PythonErrorMessage(void)
{
    PyObject *type = nullptr, *value = nullptr, *trace = nullptr;
    ::PyErr_Fetch(&type, &value, &trace);
    Py_XDECREF(type);
    Py_XDECREF(trace);

    std::string message = "module loader raised an exception";

    if (value)
    {
        PyObject *text = ::PyObject_Str(value);

        if (text)
        {
            const char *utf8 = ::PyUnicode_AsUTF8(text);
            if (utf8) message = utf8;
            Py_DECREF(text);
        }

        Py_DECREF(value);
    }

    return message;
}

// Acquire the GIL from a V8 callback: dynamic import() can run while the
// interpreter is executing with the GIL released (module evaluation).
struct PyGILGuard
{
    PyGILState_STATE state;

    PyGILGuard() : state(::PyGILState_Ensure()) {}
    ~PyGILGuard() { ::PyGILState_Release(state); }
};

v8::Local<v8::String> Utf8String(v8::Isolate *isolate, const std::string& text)
{
    return v8::String::NewFromUtf8(isolate, text.c_str(), v8::NewStringType::kNormal,
                                   static_cast<int>(text.size())).ToLocalChecked();
}

// Canonical id of a compiled module inside the current graph.
std::string CanonicalOfModule(v8::Isolate *isolate, v8::Local<v8::Module> module)
{
    std::lock_guard<std::recursive_mutex> lock(s_moduleMutex);
    ModuleState& state = ModuleStateFor(isolate);
    int identity = ContextIdentity(isolate->GetCurrentContext());

    for (const auto& entry : GraphFor(state, identity))
    {
        v8::Local<v8::Module> candidate =
            v8::Local<v8::Module>::New(isolate, entry->module);

        if (candidate == module) return entry->canonical;
    }

    return std::string();
}

// Key under which import.meta carries its own URL, so import.meta.resolve()
// can rebase a specifier without per-function data.
v8::Local<v8::Private> ImportMetaUrlKey(v8::Isolate *isolate)
{
    return v8::Private::ForApi(isolate,
        v8::String::NewFromUtf8Literal(isolate, "stpyv8.import_meta_url"));
}

void ImportMetaResolve(const v8::FunctionCallbackInfo<v8::Value>& info)
{
    v8::Isolate *isolate = info.GetIsolate();
    v8::HandleScope scope(isolate);
    v8::Local<v8::Context> context = isolate->GetCurrentContext();

    std::string base;

    v8::Local<v8::Value> url = info.This()
        ->GetPrivate(context, ImportMetaUrlKey(isolate)).ToLocalChecked();

    if (!url.IsEmpty() && url->IsString())
    {
        v8::String::Utf8Value text(isolate, url);
        if (*text) base = *text;
    }

    v8::String::Utf8Value spec(isolate, info[0]);
    std::string resolved = NormalizeSpecifier(*spec ? *spec : "", base);

    info.GetReturnValue().Set(Utf8String(isolate, resolved));
}

// Reads one import attribute from a V8 FixedArray. `stride` is 3 for the
// ResolveModuleCallback form (key, value, source_offset) and 2 for the
// dynamic-import callback form (key, value).
bool GetImportAttribute(v8::Isolate *isolate, v8::Local<v8::FixedArray> attributes,
                        int stride, const char *name, std::string& value)
{
    if (attributes.IsEmpty()) return false;

    v8::Local<v8::Context> context = isolate->GetCurrentContext();
    int length = attributes->Length();

    for (int i = 0; i + stride <= length; i += stride)
    {
        v8::Local<v8::Data> keyData = attributes->Get(context, i);
        if (keyData.IsEmpty() || !keyData->IsValue()) continue;

        v8::Local<v8::Value> key = keyData.As<v8::Value>();
        if (!key->IsString()) continue;

        v8::String::Utf8Value keyText(isolate, key);
        if (*keyText == nullptr || std::string(*keyText) != name) continue;

        v8::Local<v8::Data> valueData = attributes->Get(context, i + 1);
        if (valueData.IsEmpty() || !valueData->IsValue()) return false;

        v8::Local<v8::Value> raw = valueData.As<v8::Value>();
        if (!raw->IsString()) return false;

        v8::String::Utf8Value valueText(isolate, raw);
        if (*valueText == nullptr) return false;

        value = *valueText;
        return true;
    }

    return false;
}

// Quote arbitrary text as a JS string literal (used to embed JSON source).
std::string QuoteForJs(const std::string& text)
{
    std::string out = "\"";

    for (size_t i = 0; i < text.size(); ++i)
    {
        unsigned char c = static_cast<unsigned char>(text[i]);

        switch (c)
        {
        case '\\': out += "\\\\"; break;
        case '"': out += "\\\""; break;
        case '\n': out += "\\n"; break;
        case '\r': out += "\\r"; break;
        case '\t': out += "\\t"; break;
        case '\b': out += "\\b"; break;
        case '\f': out += "\\f"; break;
        default:
            if (c < 0x20)
            {
                char buffer[8];
                std::snprintf(buffer, sizeof(buffer), "\\u%04x", c);
                out += buffer;
            }
            else if (c == 0xE2 && i + 2 < text.size() &&
                     static_cast<unsigned char>(text[i + 1]) == 0x80 &&
                     (static_cast<unsigned char>(text[i + 2]) == 0xA8 ||
                      static_cast<unsigned char>(text[i + 2]) == 0xA9))
            {
                // U+2028/U+2029 would terminate the string for the lexer.
                out += (static_cast<unsigned char>(text[i + 2]) == 0xA8)
                    ? "\\u2028" : "\\u2029";
                i += 2;
            }
            else
            {
                out += static_cast<char>(c);
            }
        }
    }

    out += "\"";
    return out;
}

// Wraps raw JSON text into a module whose default export is the parsed value.
std::string ModuleCodeForSource(const std::string& source, const std::string& type)
{
    if (type.empty()) return source;

    if (type == "json")
        return "export default JSON.parse(" + QuoteForJs(source) + ");";

    return std::string();
}

// Resolve the source text for `canonical`, using and filling the cross-context
// source cache. Returns false with `error` populated on failure.
bool LoadModuleSource(v8::Isolate *isolate, const std::string& canonical,
                      const std::string& referrer,
                      std::string& source, std::string& error)
{
    {
        std::lock_guard<std::recursive_mutex> lock(s_moduleMutex);
        ModuleState& state = ModuleStateFor(isolate);
        auto it = state.sources.find(canonical);

        if (it != state.sources.end())
        {
            source = it->second;
            return true;
        }
    }

    py::object loader;

    {
        std::lock_guard<std::recursive_mutex> lock(s_moduleMutex);
        PyObject *raw = ModuleStateFor(isolate).loader;
        loader = py::object(py::borrowed<>(raw ? raw : Py_None));
    }

    try
    {
        py::object result;

        if (PyDict_Check(loader.ptr()))
            result = loader.attr("get")(py::str(canonical));
        else if (PyCallable_Check(loader.ptr()))
            result = loader(py::str(canonical), py::str(referrer));
        else
        {
            error = "cannot resolve module '" + canonical +
                    "': no loader was provided (pass loader= dict or callable)";
            return false;
        }

        if (result.is_none())
        {
            error = "no module source for '" + canonical + "'";
            return false;
        }

        source = py::extract<std::string>(py::str(result))();
    }
    catch (py::error_already_set&)
    {
        error = PythonErrorMessage();
        return false;
    }
    catch (const std::exception& failure)
    {
        error = std::string("module loader failed: ") + failure.what();
        return false;
    }

    {
        std::lock_guard<std::recursive_mutex> lock(s_moduleMutex);
        ModuleStateFor(isolate).sources[canonical] = source;
    }

    return true;
}

// Bridge between a module's evaluation promise and the promise handed back to
// import(). Holds the module alive until settlement.
struct ImportBridge
{
    v8::Persistent<v8::Promise::Resolver> resolver;
    v8::Persistent<v8::Module> module;
};

void FreeImportBridge(ImportBridge *bridge)
{
    bridge->resolver.Reset();
    bridge->module.Reset();
    delete bridge;
}

void ImportFulfilled(const v8::FunctionCallbackInfo<v8::Value>& info)
{
    v8::Isolate *isolate = info.GetIsolate();
    v8::HandleScope scope(isolate);
    v8::Local<v8::Context> context = isolate->GetCurrentContext();

    auto *bridge = static_cast<ImportBridge *>(
        info.Data().As<v8::External>()->Value());

    if (!bridge) return;

    v8::Local<v8::Module> module =
        v8::Local<v8::Module>::New(isolate, bridge->module);
    v8::Local<v8::Promise::Resolver> resolver =
        v8::Local<v8::Promise::Resolver>::New(isolate, bridge->resolver);

    resolver->Resolve(context, module->GetModuleNamespace()).FromMaybe(false);
    FreeImportBridge(bridge);
}

void ImportRejected(const v8::FunctionCallbackInfo<v8::Value>& info)
{
    v8::Isolate *isolate = info.GetIsolate();
    v8::HandleScope scope(isolate);
    v8::Local<v8::Context> context = isolate->GetCurrentContext();

    auto *bridge = static_cast<ImportBridge *>(
        info.Data().As<v8::External>()->Value());

    if (!bridge) return;

    v8::Local<v8::Promise::Resolver> resolver =
        v8::Local<v8::Promise::Resolver>::New(isolate, bridge->resolver);

    resolver->Reject(context, info[0]).FromMaybe(false);
    FreeImportBridge(bridge);
}
}  // namespace

void CModuleRegistry::Register(v8::Isolate *isolate, const std::string& canonical,
                               v8::Handle<v8::Module> module)
{
    std::lock_guard<std::recursive_mutex> lock(s_moduleMutex);
    ModuleState& state = ModuleStateFor(isolate);
    auto& graph = GraphFor(state, ContextIdentity(isolate->GetCurrentContext()));

    for (const auto& entry : graph)
    {
        if (entry->canonical == canonical)
        {
            entry->module.Reset(isolate, module);
            return;
        }
    }

    std::unique_ptr<ModuleEntry> entry(new ModuleEntry());
    entry->canonical = canonical;
    entry->module.Reset(isolate, module);
    graph.push_back(std::move(entry));
}

void CModuleRegistry::BeginGraph(v8::Isolate *isolate, py::object loader,
                                 const std::string& canonical,
                                 v8::Handle<v8::Module> root)
{
    std::lock_guard<std::recursive_mutex> lock(s_moduleMutex);
    ModuleState& state = ModuleStateFor(isolate);

    PyObject *requested = loader.is_none() ? nullptr : loader.ptr();

    // A different loader can map the same specifier to different source, so all
    // cached graphs and sources become stale. Comparing the object pointer is
    // deliberate: callers normally reuse the same dict/callable.
    if (state.loader != requested)
    {
        for (auto& pair : state.graphs)
            for (auto& entry : pair.second) entry->module.Reset();

        state.graphs.clear();
        state.sources.clear();
    }

    // Never retain a reference to Py_None: hosts (and testRefCount) watch
    // None's refcount, and a reference held until an unrelated isolate is
    // disposed would make it drift unpredictably.
    Py_XDECREF(state.loader);
    state.loader = requested;
    Py_XINCREF(state.loader);

    int identity = ContextIdentity(isolate->GetCurrentContext());
    auto& graph = GraphFor(state, identity);

    // Only this context's previous graph is discarded; other contexts keep
    // their modules alive and unaffected.
    for (auto& entry : graph)
        entry->module.Reset();

    graph.clear();

    std::unique_ptr<ModuleEntry> entry(new ModuleEntry());
    entry->canonical = canonical;
    entry->module.Reset(isolate, root);
    graph.push_back(std::move(entry));
}

void CModuleRegistry::Forget(v8::Isolate *isolate)
{
    // Called from IsolateRegistry::Forget() while the isolate is still alive;
    // Reset() on a Persistent after Dispose() is a use-after-free.
    PyGILState_STATE gil = ::PyGILState_Ensure();
    std::lock_guard<std::recursive_mutex> lock(s_moduleMutex);

    auto it = s_moduleStates.find(isolate);

    if (it != s_moduleStates.end())
    {
        for (auto& pair : it->second->graphs)
            for (auto& entry : pair.second) entry->module.Reset();

        it->second->graphs.clear();
        it->second->sources.clear();
        Py_CLEAR(it->second->loader);
        s_moduleStates.erase(it);
    }

    ::PyGILState_Release(gil);
}

v8::MaybeLocal<v8::Module> CModuleRegistry::Resolve(
    v8::Local<v8::Context> context,
    v8::Local<v8::String> specifier,
    v8::Local<v8::FixedArray> import_attributes,
    v8::Local<v8::Module> referrer)
{
    v8::Isolate *isolate = context->GetIsolate();

    // The returned Local must outlive this callback's handle scope.
    v8::EscapableHandleScope scope(isolate);

    // ResolveModuleCallback passes (key, value, source_offset) triples.
    std::string type;

    if (GetImportAttribute(isolate, import_attributes, 3, "type", type) &&
        type != "json")
    {
        ThrowModuleError(isolate,
            "unsupported import attribute type '" + type + "'");
        return v8::MaybeLocal<v8::Module>();
    }

    v8::String::Utf8Value spec(isolate, specifier);
    std::string raw_specifier(*spec ? *spec : "");
    int identity = ContextIdentity(context);

    std::string referrer_canonical;
    v8::Local<v8::Module> cached;

    {
        std::lock_guard<std::recursive_mutex> lock(s_moduleMutex);
        ModuleState& state = ModuleStateFor(isolate);
        auto& graph = GraphFor(state, identity);

        // Identify the referring module by object identity, not by a hash:
        // V8 hands back the very Module that was registered in this graph.
        for (const auto& entry : graph)
        {
            v8::Local<v8::Module> candidate =
                v8::Local<v8::Module>::New(isolate, entry->module);

            if (candidate == referrer)
            {
                referrer_canonical = entry->canonical;
                break;
            }
        }
    }

    std::string canonical = NormalizeSpecifier(raw_specifier, referrer_canonical);

    {
        std::lock_guard<std::recursive_mutex> lock(s_moduleMutex);
        ModuleState& state = ModuleStateFor(isolate);

        for (const auto& entry : GraphFor(state, identity))
        {
            if (entry->canonical == canonical)
            {
                cached = v8::Local<v8::Module>::New(isolate, entry->module);
                break;
            }
        }
    }

    if (!cached.IsEmpty())
        return v8::MaybeLocal<v8::Module>(scope.Escape(cached));

    std::string source;
    std::string error;

    if (!LoadModuleSource(isolate, canonical, referrer_canonical, source, error))
    {
        ThrowModuleError(isolate, error);
        return v8::MaybeLocal<v8::Module>();
    }

    std::string code = ModuleCodeForSource(source, type);

    if (code.empty())
    {
        ThrowModuleError(isolate,
            "unsupported import attribute type '" + type + "'");
        return v8::MaybeLocal<v8::Module>();
    }

    // is_module=true: same goal as the root module, with a resource name that
    // makes syntax errors and stack traces point at the right file.
    v8::ScriptOrigin origin(Utf8String(isolate, canonical), 0, 0, false, -1,
                            v8::Local<v8::Value>(), false, false, true);
    v8::ScriptCompiler::Source compiler_source(Utf8String(isolate, code), origin);

    v8::MaybeLocal<v8::Module> compiled =
        v8::ScriptCompiler::CompileModule(isolate, &compiler_source);

    // On failure V8 has already scheduled the SyntaxError; the caller's
    // TryCatch turns it into a Python exception.
    if (compiled.IsEmpty())
        return v8::MaybeLocal<v8::Module>();

    v8::Local<v8::Module> dependency = compiled.ToLocalChecked();
    Register(isolate, canonical, dependency);

    return v8::MaybeLocal<v8::Module>(scope.Escape(dependency));
}

void CModuleRegistry::Install(v8::Isolate *isolate)
{
    isolate->SetHostImportModuleDynamicallyCallback(&CModuleRegistry::ImportDynamically);
    isolate->SetHostInitializeImportMetaObjectCallback(&CModuleRegistry::InitializeImportMeta);
}

void CModuleRegistry::InitializeImportMeta(v8::Local<v8::Context> context,
                                           v8::Local<v8::Module> module,
                                           v8::Local<v8::Object> meta)
{
    v8::Isolate *isolate = context->GetIsolate();
    v8::HandleScope scope(isolate);

    std::string canonical = CanonicalOfModule(isolate, module);
    if (canonical.empty()) canonical = "<module>";

    v8::Local<v8::String> url = Utf8String(isolate, canonical);

    // CreateDataProperty matches the spec's HostFinalizeImportMeta.
    meta->CreateDataProperty(context,
        v8::String::NewFromUtf8Literal(isolate, "url"), url).FromMaybe(false);
    meta->CreateDataProperty(context,
        v8::String::NewFromUtf8Literal(isolate, "filename"), url).FromMaybe(false);
    meta->SetPrivate(context, ImportMetaUrlKey(isolate), url).FromMaybe(false);

    v8::MaybeLocal<v8::Function> resolve =
        v8::Function::New(context, ImportMetaResolve);

    if (!resolve.IsEmpty())
        meta->CreateDataProperty(context,
            v8::String::NewFromUtf8Literal(isolate, "resolve"),
            resolve.ToLocalChecked()).FromMaybe(false);
}

v8::MaybeLocal<v8::Promise> CModuleRegistry::ImportDynamically(
    v8::Local<v8::Context> context,
    v8::Local<v8::Data> host_defined_options,
    v8::Local<v8::Value> resource_name,
    v8::Local<v8::String> specifier,
    v8::Local<v8::FixedArray> import_attributes)
{
    (void) host_defined_options;

    // import() may fire while V8 runs with the GIL released (module
    // evaluation), and the host loader is Python code.
    PyGILGuard python_gil;

    v8::Isolate *isolate = context->GetIsolate();
    v8::EscapableHandleScope scope(isolate);

    v8::Local<v8::Promise::Resolver> resolver;

    if (!v8::Promise::Resolver::New(context).ToLocal(&resolver))
        return v8::MaybeLocal<v8::Promise>();

    auto reject = [&](v8::Local<v8::Value> error) -> v8::MaybeLocal<v8::Promise> {
        resolver->Reject(context, error).FromMaybe(false);
        return v8::MaybeLocal<v8::Promise>(scope.Escape(resolver->GetPromise()));
    };

    // The dynamic-import callback passes (key, value) pairs, without offsets.
    std::string type;

    if (GetImportAttribute(isolate, import_attributes, 2, "type", type) &&
        type != "json")
        return reject(v8::Exception::TypeError(Utf8String(isolate,
            "unsupported import attribute type '" + type + "'")));

    std::string referrer;

    if (!resource_name.IsEmpty() && resource_name->IsString())
    {
        v8::String::Utf8Value text(isolate, resource_name);
        if (*text) referrer = *text;
    }

    v8::String::Utf8Value spec(isolate, specifier);
    std::string canonical = NormalizeSpecifier(*spec ? *spec : "", referrer);
    int identity = ContextIdentity(context);

    v8::Local<v8::Module> module;

    {
        std::lock_guard<std::recursive_mutex> lock(s_moduleMutex);
        ModuleState& state = ModuleStateFor(isolate);

        for (const auto& entry : GraphFor(state, identity))
        {
            if (entry->canonical == canonical)
            {
                module = v8::Local<v8::Module>::New(isolate, entry->module);
                break;
            }
        }
    }

    if (module.IsEmpty())
    {
        std::string source;
        std::string error;

        if (!LoadModuleSource(isolate, canonical, referrer, source, error))
            return reject(v8::Exception::TypeError(Utf8String(isolate, error)));

        std::string code = ModuleCodeForSource(source, type);

        if (code.empty())
            return reject(v8::Exception::TypeError(Utf8String(isolate,
                "unsupported import attribute type '" + type + "'")));

        v8::ScriptOrigin origin(Utf8String(isolate, canonical), 0, 0, false, -1,
                                v8::Local<v8::Value>(), false, false, true);
        v8::ScriptCompiler::Source compiler_source(Utf8String(isolate, code), origin);

        v8::TryCatch try_catch(isolate);
        v8::MaybeLocal<v8::Module> compiled =
            v8::ScriptCompiler::CompileModule(isolate, &compiler_source);

        if (compiled.IsEmpty())
        {
            if (try_catch.HasCaught()) return reject(try_catch.Exception());
            return v8::MaybeLocal<v8::Promise>();
        }

        module = compiled.ToLocalChecked();
        Register(isolate, canonical, module);
    }

    if (module->GetStatus() == v8::Module::kUninstantiated)
    {
        v8::TryCatch try_catch(isolate);
        v8::Maybe<bool> instantiated =
            module->InstantiateModule(context, &CModuleRegistry::Resolve);

        if (instantiated.IsNothing())
        {
            if (try_catch.HasCaught()) return reject(try_catch.Exception());
            return v8::MaybeLocal<v8::Promise>();
        }

        if (!instantiated.FromJust())
            return reject(v8::Exception::Error(
                Utf8String(isolate, "module instantiation failed")));
    }

    if (module->GetStatus() == v8::Module::kInstantiated)
    {
        v8::TryCatch try_catch(isolate);
        v8::MaybeLocal<v8::Value> evaluated = module->Evaluate(context);

        if (evaluated.IsEmpty())
        {
            if (try_catch.HasCaught()) return reject(try_catch.Exception());
            return v8::MaybeLocal<v8::Promise>();
        }

        v8::Local<v8::Value> value = evaluated.ToLocalChecked();

        if (value->IsPromise())
        {
            v8::Local<v8::Promise> promise = value.As<v8::Promise>();

            // Settle microtask-only top-level await chains.
            for (int i = 0; i < 10 && promise->State() == v8::Promise::kPending; ++i)
                isolate->PerformMicrotaskCheckpoint();

            if (promise->State() == v8::Promise::kRejected)
                return reject(promise->Result());

            // Still pending: awaiting real I/O. Chain the module's promise to
            // the one returned to import() instead of rejecting, so a host
            // that drives the event loop can settle it later.
            if (promise->State() == v8::Promise::kPending)
            {
                ImportBridge *bridge = new ImportBridge();
                bridge->resolver.Reset(isolate, resolver);
                bridge->module.Reset(isolate, module);

                v8::Local<v8::Function> on_fulfilled = v8::Function::New(
                    context, ImportFulfilled,
                    v8::External::New(isolate, bridge)).ToLocalChecked();
                v8::Local<v8::Function> on_rejected = v8::Function::New(
                    context, ImportRejected,
                    v8::External::New(isolate, bridge)).ToLocalChecked();

                promise->Then(context, on_fulfilled, on_rejected);

                return v8::MaybeLocal<v8::Promise>(
                    scope.Escape(resolver->GetPromise()));
            }
        }
    }
    else if (module->GetStatus() == v8::Module::kErrored)
    {
        return reject(module->GetException());
    }

    resolver->Resolve(context, module->GetModuleNamespace()).FromMaybe(false);

    return v8::MaybeLocal<v8::Promise>(scope.Escape(resolver->GetPromise()));
}

CModulePtr CEngine::InternalCompileModule(v8::Handle<v8::String> src,
        v8::Handle<v8::Value> name,
        int line, int col, py::object loader)
{
    v8::Isolate* isolate = m_isolate;
    v8::HandleScope handle_scope(isolate);
    v8::TryCatch try_catch(isolate);

    v8::Persistent<v8::String> source_persistent(isolate, src);
    v8::Handle<v8::String> source = v8::Local<v8::String>::New(isolate, source_persistent);

    // The resource name doubles as the module's canonical id: relative
    // specifiers are resolved against it by the module registry.
    std::string canonical = "<module>";
    {
        v8::String::Utf8Value resource(isolate, name);
        if (*resource && (*resource)[0]) canonical = *resource;
    }

    // is_module=true keeps ScriptOrigin metadata (error stacks, import.meta,
    // host-defined options) consistent with the module goal.
    v8::ScriptOrigin origin(name,
                            line >= 0 ? line : 0,
                            col >= 0 ? col : 0,
                            false, -1, v8::Local<v8::Value>(),
                            false, false, true);

    v8::ScriptCompiler::Source compiler_source(source, origin);

    v8::MaybeLocal<v8::Module> module;

    Py_BEGIN_ALLOW_THREADS
    module = v8::ScriptCompiler::CompileModule(isolate, &compiler_source);
    Py_END_ALLOW_THREADS

    if (module.IsEmpty()) CJavascriptException::ThrowIf(isolate, try_catch);

    return std::shared_ptr<CModule>(
        new CModule(isolate, canonical, source_persistent,
                    module.ToLocalChecked(), loader));
}

const std::string CModule::GetSource(void) const
{
    v8::HandleScope handle_scope(m_isolate);

    v8::String::Utf8Value source(m_isolate, v8::Local<v8::String>::New(m_isolate, m_source));

    return std::string(*source, source.length());
}

py::object CModule::Run(void)
{
    v8::Isolate* isolate = m_isolate;
    v8::HandleScope handle_scope(isolate);
    v8::Local<v8::Context> context = isolate->GetCurrentContext();
    v8::TryCatch try_catch(isolate);

    v8::Handle<v8::Module> module = Module();

    // Install this module's loader for the duration of instantiation: the
    // ResolveModuleCallback has no user-data pointer, so it reads the
    // per-isolate loader set here.
    CModuleRegistry::BeginGraph(isolate, m_loader, m_canonical, module);

    if (module->GetStatus() == v8::Module::kUninstantiated)
    {
        v8::Maybe<bool> instantiated =
            module->InstantiateModule(context, &CModuleRegistry::Resolve);
        if (instantiated.IsNothing()) CJavascriptException::ThrowIf(isolate, try_catch);

        if (!instantiated.FromJust())
            throw CJavascriptException("module instantiation failed", ::PyExc_RuntimeError);
    }

    if (module->GetStatus() == v8::Module::kInstantiated)
    {
        v8::MaybeLocal<v8::Value> result;

        Py_BEGIN_ALLOW_THREADS
        result = module->Evaluate(context);
        Py_END_ALLOW_THREADS

        if (result.IsEmpty()) CJavascriptException::ThrowIf(isolate, try_catch);

        // V8 13.x always returns a Promise from Evaluate. Drain the microtask
        // queue so a synchronous graph settles before we inspect its state.
        isolate->PerformMicrotaskCheckpoint();

        v8::Handle<v8::Value> value = result.ToLocalChecked();

        if (value->IsPromise())
        {
            v8::Handle<v8::Promise> promise = v8::Handle<v8::Promise>::Cast(value);
            m_evaluatePromise.Reset(isolate, promise);

            // Top-level await: settle a microtask-only chain before declaring
            // the module unusable. Awaiting real I/O still needs an event loop.
            for (int i = 0; i < 10 && promise->State() == v8::Promise::kPending; ++i)
                isolate->PerformMicrotaskCheckpoint();

            if (promise->State() == v8::Promise::kRejected)
            {
                isolate->ThrowException(promise->Result());
                CJavascriptException::ThrowIf(isolate, try_catch);
            }
            else if (promise->State() == v8::Promise::kPending)
            {
                throw CJavascriptException(
                    "module evaluation is still pending: top-level await needs a "
                    "host loader / event loop, which compileModule() does not "
                    "provide yet", ::PyExc_RuntimeError);
            }
        }
    }
    else if (module->GetStatus() == v8::Module::kErrored)
    {
        isolate->ThrowException(module->GetException());
        CJavascriptException::ThrowIf(isolate, try_catch);
    }

    // A module's completion value is undefined; its exports are the namespace
    // object. Re-running returns the same namespace without re-evaluating.
    return CJavascriptObject::Wrap(module->GetModuleNamespace());
}

py::object CModule::RunAsync(void)
{
    v8::Isolate* isolate = m_isolate;
    v8::HandleScope handle_scope(isolate);
    v8::Local<v8::Context> context = isolate->GetCurrentContext();
    v8::TryCatch try_catch(isolate);

    v8::Handle<v8::Module> module = Module();

    CModuleRegistry::BeginGraph(isolate, m_loader, m_canonical, module);

    if (module->GetStatus() == v8::Module::kUninstantiated)
    {
        v8::Maybe<bool> instantiated =
            module->InstantiateModule(context, &CModuleRegistry::Resolve);
        if (instantiated.IsNothing()) CJavascriptException::ThrowIf(isolate, try_catch);

        if (!instantiated.FromJust())
            throw CJavascriptException("module instantiation failed", ::PyExc_RuntimeError);
    }

    if (module->GetStatus() == v8::Module::kErrored)
    {
        isolate->ThrowException(module->GetException());
        CJavascriptException::ThrowIf(isolate, try_catch);
    }

    if (module->GetStatus() == v8::Module::kInstantiated)
    {
        v8::MaybeLocal<v8::Value> evaluated;

        Py_BEGIN_ALLOW_THREADS
        evaluated = module->Evaluate(context);
        Py_END_ALLOW_THREADS

        if (evaluated.IsEmpty()) CJavascriptException::ThrowIf(isolate, try_catch);

        v8::Local<v8::Value> result = evaluated.ToLocalChecked();

        if (result->IsPromise())
        {
            v8::Local<v8::Promise> promise = result.As<v8::Promise>();
            m_evaluatePromise.Reset(isolate, promise);
        }

        // The caller drives this promise (and the microtask queue) from Python.
        return CJavascriptObject::Wrap(result);
    }

    // Already evaluated: hand back an already-settled promise.
    v8::Local<v8::Promise::Resolver> resolver;

    if (!v8::Promise::Resolver::New(context).ToLocal(&resolver))
        return py::object();

    resolver->Resolve(context, module->GetModuleNamespace()).FromMaybe(false);

    v8::Local<v8::Value> settled = resolver->GetPromise();

    return CJavascriptObject::Wrap(settled);
}

py::object CModule::Namespace(void)
{
    v8::Isolate* isolate = m_isolate;
    v8::HandleScope handle_scope(isolate);

    v8::Handle<v8::Module> module = Module();

    // Module::GetStatus() reports kEvaluated as soon as synchronous evaluation
    // reaches the first await, so the evaluate Promise is the real completion
    // signal; reading exports before it settles hits uninitialised bindings.
    if (!m_evaluatePromise.IsEmpty())
    {
        v8::Local<v8::Promise> promise =
            v8::Local<v8::Promise>::New(isolate, m_evaluatePromise);

        if (promise->State() == v8::Promise::kRejected)
        {
            v8::TryCatch try_catch(isolate);
            isolate->ThrowException(promise->Result());
            CJavascriptException::ThrowIf(isolate, try_catch);
        }

        if (promise->State() == v8::Promise::kPending)
            throw CJavascriptException(
                "module is not evaluated yet; drive its promise first",
                ::PyExc_RuntimeError);
    }
    else if (module->GetStatus() != v8::Module::kEvaluated)
    {
        throw CJavascriptException(
            "module has not been evaluated; call run() or run_async() first",
            ::PyExc_RuntimeError);
    }

    return CJavascriptObject::Wrap(module->GetModuleNamespace());
}
