#pragma once

#include <v8.h> 

#include <deque>
#include <string>

#include "Exception.h"

class CIsolate
{
    v8::Isolate *m_isolate;
    bool m_owner;
    void Init(bool owner);

    // --- Unhandled promise rejections -------------------------------------
    //
    // V8 reports rejections through Isolate::SetPromiseRejectCallback, but the
    // message it hands over carries Locals that die as soon as the callback
    // returns, so the report is flattened into plain data right here. Instances
    // are kept PER ISOLATE (not per isolate wrapper: CIsolate(v8::Isolate*) is
    // created ad-hoc by GetCurrent()), otherwise a rejection recorded on one
    // wrapper would be invisible to the next one.
    struct PendingRejection
    {
        v8::Isolate *isolate;
        size_t promise_id;
        std::string message;
        std::string stack;
        std::string description;
    };

    static std::deque<PendingRejection>& RejectionQueue(void);
    static size_t& RejectionSequence(void);

    static void PromiseRejectCallback(v8::PromiseRejectMessage message);

    static constexpr int KB = 1024;
    static constexpr int MB = KB * 1024;
    static constexpr size_t heap_increase = 8 * MB;
    static constexpr size_t heap_max_increase = 64 * MB;
public:
    CIsolate();
    CIsolate(bool owner);
    CIsolate(v8::Isolate *isolate);
    ~CIsolate(void);

    v8::Isolate *GetIsolate(void);

    CJavascriptStackTracePtr GetCurrentStackTrace(int frame_limit,
            v8::StackTrace::StackTraceOptions options);

    static py::object GetCurrent(void);
    static size_t NearHeapLimitCallback(void* data, size_t current_heap_limit,
                                        size_t initial_heap_limit);

    // Drain the microtask queue of the current isolate. V8 only runs microtasks
    // automatically at certain points (script exit, etc.), so a host that wants
    // determinism -- "run the promise chain I just started" -- needs this.
    static py::object RunMicrotasks(void);

    // Unhandled promise rejections recorded for the current isolate, oldest
    // first. Each entry is a dict with: message, stack, description.
    static py::list GetPendingRejections(void);

    // Drop the recorded rejections of the current isolate (e.g. after the host
    // has logged them).
    static void ClearPendingRejections(void);

    void Enter(void) {
        m_isolate->Enter();
    }

    void Leave(void) {
        m_isolate->Exit();
    }

    void Dispose(void) {
        m_isolate->Dispose();
    }

    bool IsLocked(void) {
        return v8::Locker::IsLocked(m_isolate);
    }
};
