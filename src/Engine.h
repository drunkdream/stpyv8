#pragma once

#include <memory>
#include <string>
#include <vector>
#include <map>

#include "Utils.h"

class CScript;
class CModule;

typedef std::shared_ptr<CScript> CScriptPtr;
typedef std::shared_ptr<CModule> CModulePtr;

class CEngine
{
    v8::Isolate *m_isolate;

    static uintptr_t CalcStackLimitSize(uintptr_t size);
protected:
    CScriptPtr InternalCompile(v8::Handle<v8::String> src, v8::Handle<v8::Value> name, int line, int col);
    CModulePtr InternalCompileModule(v8::Handle<v8::String> src, v8::Handle<v8::Value> name,
                                     int line, int col, py::object loader);

    static void TerminateAllThreads(void);

    static void ReportFatalError(const char* location, const char* message);
    static void ReportMessage(v8::Handle<v8::Message> message, v8::Handle<v8::Value> data);
public:
    CEngine(v8::Isolate *isolate = NULL) : m_isolate(isolate ? isolate : v8::Isolate::GetCurrent()) {}

    CScriptPtr Compile(const std::string& src, const std::string name = std::string(),
                       int line = -1, int col = -1)
    {
        v8::HandleScope scope(m_isolate);

        return InternalCompile(ToString(src), ToString(name), line, col);
    }

    CScriptPtr CompileW(const std::wstring& src, const std::wstring name = std::wstring(),
                        int line = -1, int col = -1)
    {
        v8::HandleScope scope(m_isolate);

        return InternalCompile(ToString(src), ToString(name), line, col);
    }

    // ES-module support: compile `src` with the ECMAScript "Module" goal
    // (ScriptType::kModule) instead of the classic script goal used by Compile.
    // `loader` supplies source text for statically imported specifiers: either
    // a dict {specifier: source} or a callable (specifier, referrer) -> source.
    CModulePtr CompileModule(const std::string& src, const std::string name = std::string(),
                             int line = -1, int col = -1,
                             py::object loader = py::object())
    {
        v8::HandleScope scope(m_isolate);

        return InternalCompileModule(ToString(src), ToString(name), line, col, loader);
    }

    void RaiseError(v8::TryCatch& try_catch);
public:
    static void Expose(void);

    static const std::string GetVersion(void) {
        return v8::V8::GetVersion();
    }
    static bool SetMemoryLimit(int max_young_space_size, int max_old_space_size, int max_executable_size);
    static void SetStackLimit(uintptr_t stack_limit_size);

    py::object ExecuteScript(v8::Handle<v8::Script> script);

    static void SetFlags(const std::string& flags) {
        v8::V8::SetFlagsFromString(flags.c_str(), flags.size());
    }

    static void SetSerializeEnable(bool value);
    static bool IsSerializeEnabled(void);

    static py::object Serialize(void);
    static void Deserialize(py::object snapshot);
    static bool IsDead(void);
};

class CScript
{
    v8::Isolate *m_isolate;
    CEngine& m_engine;

    v8::Persistent<v8::String> m_source;
    v8::Persistent<v8::Script> m_script;
public:
    CScript(v8::Isolate *isolate, CEngine& engine, v8::Persistent<v8::String>& source, v8::Handle<v8::Script> script)
        : m_isolate(isolate), m_engine(engine), m_source(m_isolate, source), m_script(m_isolate, script)
    {

    }

    CScript(const CScript& script)
        : m_isolate(script.m_isolate), m_engine(script.m_engine)
    {
        v8::HandleScope handle_scope(m_isolate);

        m_source.Reset(m_isolate, script.Source());
        m_script.Reset(m_isolate, script.Script());
    }

    ~CScript()
    {
        m_source.Reset();
        m_script.Reset();
    }

    v8::Handle<v8::String> Source() const {
        return v8::Local<v8::String>::New(m_isolate, m_source);
    }

    v8::Handle<v8::Script> Script() const {
        return v8::Local<v8::Script>::New(m_isolate, m_script);
    }

    const std::string GetSource(void) const;

    py::object Run(void);
};

// Per-isolate module graph. Compiled modules are cached by canonical
// specifier (scoped to the context they were compiled in), so a module shared
// by several importers is compiled once and circular imports resolve back to
// the same Module. The Persistents and the Python loader are owned here and
// released by Forget() BEFORE the isolate is disposed.
class CModuleRegistry
{
public:
    // Install the isolate-level ES-module hooks: dynamic import() and
    // import.meta. Called once per isolate while it is created.
    static void Install(v8::Isolate *isolate);

    static void Register(v8::Isolate *isolate, const std::string& canonical,
                         v8::Handle<v8::Module> module);
    static void BeginGraph(v8::Isolate *isolate, py::object loader,
                           const std::string& canonical,
                           v8::Handle<v8::Module> root);
    static void Forget(v8::Isolate *isolate);

    // v8::Module::ResolveModuleCallback
    static v8::MaybeLocal<v8::Module> Resolve(
        v8::Local<v8::Context> context,
        v8::Local<v8::String> specifier,
        v8::Local<v8::FixedArray> import_attributes,
        v8::Local<v8::Module> referrer);

private:
    // v8::HostImportModuleDynamicallyCallback (import())
    static v8::MaybeLocal<v8::Promise> ImportDynamically(
        v8::Local<v8::Context> context,
        v8::Local<v8::Data> host_defined_options,
        v8::Local<v8::Value> resource_name,
        v8::Local<v8::String> specifier,
        v8::Local<v8::FixedArray> import_attributes);

    // v8::HostInitializeImportMetaObjectCallback (import.meta)
    static void InitializeImportMeta(v8::Local<v8::Context> context,
                                     v8::Local<v8::Module> module,
                                     v8::Local<v8::Object> meta);
};

// A compiled ES module. Unlike a classic script, a module has its own scope,
// is instantiated against a module graph, is evaluated exactly once and its
// useful result is the namespace object holding its exports.
class CModule
{
    v8::Isolate *m_isolate;
    std::string m_canonical;
    py::object m_loader;

    v8::Persistent<v8::String> m_source;
    v8::Persistent<v8::Module> m_module;
    // The Promise returned by Module::Evaluate(). Module::GetStatus() reports
    // kEvaluated as soon as synchronous evaluation reaches the first await, so
    // this promise (still pending for a top-level await) is the real signal
    // that the exports bindings are initialised.
    v8::Persistent<v8::Promise> m_evaluatePromise;
public:
    CModule(v8::Isolate *isolate, const std::string& canonical,
            v8::Persistent<v8::String>& source, v8::Handle<v8::Module> module,
            py::object loader)
        : m_isolate(isolate), m_canonical(canonical), m_loader(loader),
          m_source(isolate, source), m_module(isolate, module)
    {
    }

    ~CModule()
    {
        m_source.Reset();
        m_module.Reset();
        m_evaluatePromise.Reset();
    }

    v8::Handle<v8::Module> Module() const {
        return v8::Local<v8::Module>::New(m_isolate, m_module);
    }

    const std::string GetSource(void) const;

    // Instantiate and evaluate, returning the settle Promise so a host can
    // drive a top-level await that depends on real I/O.
    py::object RunAsync(void);

    // The exports namespace; requires the module to have been evaluated.
    py::object Namespace(void);

    py::object Run(void);
};
