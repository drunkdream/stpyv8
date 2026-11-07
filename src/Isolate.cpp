#include "Context.h"
#include "Wrapper.h"
#include "Engine.h"

#include "libplatform/libplatform.h"

#include <map>


size_t CIsolate::NearHeapLimitCallback(void *data,
                                       size_t current_heap_limit, size_t initial_heap_limit)
{
    v8::Isolate *isolate = (v8::Isolate *)data;

    if (current_heap_limit - initial_heap_limit > heap_max_increase)
        return current_heap_limit;

    isolate->AdjustAmountOfExternalAllocatedMemory(heap_increase);
    return current_heap_limit + heap_increase;
}

std::deque<CIsolate::PendingRejection>& CIsolate::RejectionQueue(void)
{
    // Function-local statics: one queue for the process, shared by every isolate
    // wrapper and every thread. Entries are tagged with their isolate so a host
    // only ever sees its own rejections.
    static std::deque<PendingRejection> queue;
    return queue;
}

size_t& CIsolate::RejectionSequence(void)
{
    static size_t sequence = 0;
    return sequence;
}

void CIsolate::PromiseRejectCallback(v8::PromiseRejectMessage message)
{
    v8::Isolate *isolate = v8::Isolate::GetCurrent();

    if (isolate == nullptr)
        return;

    // A rejection is only "unhandled" until a handler is attached; V8 fires
    // kPromiseHandlerAddedAfterReject when that happens, and the entry recorded
    // earlier must then be withdrawn -- otherwise a chain that is handled a tick
    // late would still be reported as a crash.
    if (message.GetEvent() == v8::kPromiseHandlerAddedAfterReject)
    {
        v8::Local<v8::Promise> promise = message.GetPromise();

        if (!promise.IsEmpty())
        {
            // Promises are identified by object identity; the sequence number is
            // stashed on the promise so the record can be found again.
            // GetCreationContext() is a MaybeLocal (empty for a promise whose
            // context is gone), so unwrap it defensively.
            v8::MaybeLocal<v8::Context> maybe_context = promise->GetCreationContext();

            if (!maybe_context.IsEmpty())
            {
                v8::Local<v8::Context> context = maybe_context.ToLocalChecked();
                v8::MaybeLocal<v8::Value> maybe_marker = promise->Get(
                            context,
                            v8::String::NewFromUtf8(isolate, "__stpyv8_rejection_id").ToLocalChecked());

                if (!maybe_marker.IsEmpty() && maybe_marker.ToLocalChecked()->IsNumber())
                {
                    size_t id = (size_t) maybe_marker.ToLocalChecked()->NumberValue(context).FromMaybe(0);
                    std::deque<PendingRejection> &queue = RejectionQueue();

                    for (std::deque<PendingRejection>::iterator it = queue.begin(); it != queue.end(); ++it)
                    {
                        if (it->isolate == isolate && it->promise_id == id)
                        {
                            queue.erase(it);
                            break;
                        }
                    }
                }
            }
        }

        return;
    }

    if (message.GetEvent() != v8::kPromiseRejectWithNoHandler)
        return;

    PendingRejection record;
    record.isolate = isolate;
    record.promise_id = ++RejectionSequence();

    v8::HandleScope handle_scope(isolate);
    v8::Local<v8::Context> context = isolate->GetCurrentContext();

    v8::Local<v8::Value> value = message.GetValue();

    if (!value.IsEmpty() && !context.IsEmpty())
    {
        // message: Error.message when available, else the value's string form.
        if (value->IsObject())
        {
            v8::Local<v8::Object> object = value.As<v8::Object>();
            v8::MaybeLocal<v8::Value> maybe_message =
                object->Get(context, v8::String::NewFromUtf8(isolate, "message").ToLocalChecked());

            if (!maybe_message.IsEmpty())
            {
                v8::MaybeLocal<v8::String> str = maybe_message.ToLocalChecked()->ToString(context);

                if (!str.IsEmpty())
                {
                    v8::String::Utf8Value utf8(isolate, str.ToLocalChecked());

                    if (*utf8 != nullptr)
                        record.message.assign(*utf8, utf8.length());
                }
            }

            v8::MaybeLocal<v8::Value> maybe_stack =
                object->Get(context, v8::String::NewFromUtf8(isolate, "stack").ToLocalChecked());

            if (!maybe_stack.IsEmpty())
            {
                v8::MaybeLocal<v8::String> str = maybe_stack.ToLocalChecked()->ToString(context);

                if (!str.IsEmpty())
                {
                    v8::String::Utf8Value utf8(isolate, str.ToLocalChecked());

                    if (*utf8 != nullptr)
                        record.stack.assign(*utf8, utf8.length());
                }
            }
        }

        if (record.message.empty())
        {
            v8::MaybeLocal<v8::String> str = value->ToString(context);

            if (!str.IsEmpty())
            {
                v8::String::Utf8Value utf8(isolate, str.ToLocalChecked());

                if (*utf8 != nullptr)
                    record.message.assign(*utf8, utf8.length());
            }
        }

        // Human-readable description for logs: "Error: boom" / "boom" / etc.
        v8::MaybeLocal<v8::String> str = value->ToString(context);

        if (!str.IsEmpty())
        {
            v8::String::Utf8Value utf8(isolate, str.ToLocalChecked());

            if (*utf8 != nullptr)
                record.description.assign(*utf8, utf8.length());
        }

        // Stash the id on the promise object so a later handler can withdraw the
        // record (see kPromiseHandlerAddedAfterReject above).
        v8::Local<v8::Promise> promise = message.GetPromise();

        if (!promise.IsEmpty())
        {
            v8::Maybe<bool> ok = promise->Set(
                                      context,
                                      v8::String::NewFromUtf8(isolate, "__stpyv8_rejection_id").ToLocalChecked(),
                                      v8::Number::New(isolate, (double) record.promise_id));

            (void) ok;
        }
    }

    RejectionQueue().push_back(record);
}

void CIsolate::Init(bool owner)
{
    m_owner = owner;

    v8::Isolate::CreateParams create_params;
    create_params.array_buffer_allocator = v8::ArrayBuffer::Allocator::NewDefaultAllocator();
    m_isolate = v8::Isolate::New(create_params);
    m_isolate->AddNearHeapLimitCallback(NearHeapLimitCallback, m_isolate);

    // Surface unhandled promise rejections instead of losing them silently: on a
    // bare V8 embed a rejected promise that nobody handles produces no signal at
    // all (no event loop, no unhandledrejection event, no process.on), which is
    // why a host could not tell "the app crashed" from "everything is fine".
    m_isolate->SetPromiseRejectCallback(PromiseRejectCallback);

    // Same reason for microtasks: run them automatically when the outermost
    // script finishes so a promise chain started by eval actually progresses.
    m_isolate->SetMicrotasksPolicy(v8::MicrotasksPolicy::kAuto);

    // Track everything allocated for this isolate so ~CIsolate can release it
    // before Dispose() invalidates the isolate's global handles.
    IsolateRegistry::Register(m_isolate);
}

py::object CIsolate::RunMicrotasks(void)
{
    v8::Isolate *isolate = v8::Isolate::GetCurrent();

    if (isolate == nullptr)
        return py::object();

    isolate->PerformMicrotaskCheckpoint();

    return py::object();
}

py::list CIsolate::GetPendingRejections(void)
{
    v8::Isolate *isolate = v8::Isolate::GetCurrent();
    py::list result;

    if (isolate == nullptr)
        return result;

    const std::deque<PendingRejection> &queue = RejectionQueue();

    for (std::deque<PendingRejection>::const_iterator it = queue.begin(); it != queue.end(); ++it)
    {
        if (it->isolate != isolate)
            continue;

        py::dict entry;
        entry["message"] = it->message;
        entry["stack"] = it->stack;
        entry["description"] = it->description;
        result.append(entry);
    }

    return result;
}

void CIsolate::ClearPendingRejections(void)
{
    v8::Isolate *isolate = v8::Isolate::GetCurrent();

    if (isolate == nullptr)
        return;

    std::deque<PendingRejection> &queue = RejectionQueue();
    std::deque<PendingRejection> kept;

    for (std::deque<PendingRejection>::const_iterator it = queue.begin(); it != queue.end(); ++it)
    {
        if (it->isolate != isolate)
            kept.push_back(*it);
    }

    queue.swap(kept);
}

CIsolate::CIsolate(bool owner)
{
    CIsolate::Init(owner);
}

CIsolate::CIsolate()
{
    CIsolate::Init(false);
}

CIsolate::CIsolate(v8::Isolate *isolate) : m_isolate(isolate), m_owner(false)
{
}

CIsolate::~CIsolate(void)
{
    if (m_owner)
    {
        // V8 frees its own handles on Dispose(), but our Persistents and the
        // Python objects behind V8 External payloads must be released first --
        // Reset() on a handle whose isolate is gone is a use-after-free.
        IsolateRegistry::Forget(m_isolate);
        m_isolate->Dispose();
    }
}

v8::Isolate *CIsolate::GetIsolate(void)
{
    return m_isolate;
}

CJavascriptStackTracePtr CIsolate::GetCurrentStackTrace(int frame_limit,
        v8::StackTrace::StackTraceOptions options = v8::StackTrace::kOverview)
{
    return CJavascriptStackTrace::GetCurrentStackTrace(m_isolate, frame_limit, options);
}

py::object CIsolate::GetCurrent(void)
{
    v8::Isolate *isolate = v8::Isolate::GetCurrent();
    if(isolate == nullptr || (!isolate->IsInUse()))
    {
        return py::object();
    }

    v8::HandleScope handle_scope(isolate);

    return !isolate ? py::object() :
           py::object(py::handle<>(boost::python::converter::shared_ptr_to_python<CIsolate>(
                                       CIsolatePtr(new CIsolate(isolate)))));
}
