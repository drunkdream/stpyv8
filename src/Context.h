#pragma once

#include <cassert>
#include <memory>

#include "Isolate.h"
#include "Platform.h"
#include "Wrapper.h"
#include "Utils.h"


class CContext;
class CIsolate;

typedef std::shared_ptr<CContext> CContextPtr;
typedef std::shared_ptr<CIsolate> CIsolatePtr;


class CContext
{
    py::object m_global;
    // The isolate owning m_context. V8 removed v8::Context::GetIsolate(), and
    // Isolate::GetCurrent() is wrong whenever a context is touched from a thread
    // whose current isolate is not the owning one (e.g. a CContext copied into a
    // worker thread), so the owner is tracked explicitly.
    v8::Isolate* m_isolate;
    v8::Persistent<v8::Context> m_context;
    // True only when this wrapper OWNS the V8 context (i.e. it created it).
    // Temporary wrappers handed out by GetEntered/GetCurrent/GetCalling merely
    // observe an existing context and must not collect or tear it down.
    bool m_owner;
public:
    CContext(v8::Isolate* isolate, v8::Handle<v8::Context> context);
    CContext(const CContext& context);
    CContext(py::object global);

    ~CContext()
    {
        if (m_context.IsEmpty())
            return;

        v8::Isolate* isolate = m_isolate;

        // A JSIsolate(True) may be disposed while a JSContext local is still
        // alive (Python drops the with-temporary before the frame's locals).
        // Its global handles are already gone, so only discard our local
        // reference -- no Reset(), no other V8 API call.
        if (!IsolateRegistry::IsAlive(isolate))
        {
            m_context.Clear();
            return;
        }

        // Release the Python objects wrapped for this context while its
        // handles are still valid, so repeated create/dispose cycles do not
        // accumulate them. Only the owning isolate's current thread may use
        // V8 APIs (a context can also be destroyed re-entrantly or from
        // another thread).
        if (m_owner && v8::Isolate::GetCurrent() == isolate)
        {
            v8::HandleScope handle_scope(isolate);

            ObjectTracer::DisposeContext(
                isolate, v8::Local<v8::Context>::New(isolate, m_context));
        }

        m_context.Reset();
    }

    v8::Handle<v8::Context> Handle(void) const {
        return v8::Local<v8::Context>::New(m_isolate, m_context);
    }

    py::object GetGlobal(void);

    py::str GetSecurityToken(void);
    void SetSecurityToken(py::str token);

    bool IsEntered(void) {
        return !m_context.IsEmpty();
    }
    void Enter(void) {
        v8::HandleScope handle_scope(m_isolate);
        Handle()->Enter();
    }
    void Leave(void) {
        v8::HandleScope handle_scope(m_isolate);
        Handle()->Exit();
    }

    py::object Evaluate(const std::string& src, const std::string name = std::string(),
                        int line = -1, int col = -1);
    py::object EvaluateW(const std::wstring& src, const std::wstring name = std::wstring(),
                         int line = -1, int col = -1);

    static py::object GetEntered(void);
    static py::object GetCurrent(void);
    static py::object GetCalling(void);
    static bool InContext(void) {
        return v8::Isolate::GetCurrent()->InContext();
    }

    static void Expose(void);
};
