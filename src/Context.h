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
public:
    CContext(v8::Isolate* isolate, v8::Handle<v8::Context> context);
    CContext(const CContext& context);
    CContext(py::object global);

    ~CContext()
    {
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
