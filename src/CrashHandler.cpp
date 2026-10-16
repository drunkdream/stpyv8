#ifndef _CRT_SECURE_NO_WARNINGS
#define _CRT_SECURE_NO_WARNINGS
#endif

#include "CrashHandler.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <string>

#ifdef _WIN32
#include <windows.h>
#include <dbghelp.h>
#else
#include <signal.h>
#include <unistd.h>
#include <execinfo.h>
#endif

namespace
{

bool CrashDumpEnabled()
{
    const char *v = std::getenv("STPYV8_CRASH_DUMP");
    return v && *v && std::strcmp(v, "0") != 0;
}

std::string CrashDumpDir()
{
    const char *v = std::getenv("STPYV8_CRASH_DUMP");
    // "1" (or empty) means "use the current working directory".
    if (v && *v && std::strcmp(v, "1") != 0)
        return std::string(v);
    return ".";
}

std::string Timestamp()
{
    std::time_t t = std::time(nullptr);
    std::tm tm_buf;
#ifdef _WIN32
    localtime_s(&tm_buf, &t);
#else
    localtime_r(&t, &tm_buf);
#endif
    char buf[32];
    std::strftime(buf, sizeof(buf), "%Y%m%d-%H%M%S", &tm_buf);
    return std::string(buf);
}

#ifdef _WIN32

LONG WINAPI CrashExceptionFilter(EXCEPTION_POINTERS *info)
{
    std::string path = CrashDumpDir() + "\\stpyv8-crash-" + Timestamp() + "-" +
                       std::to_string(::GetCurrentProcessId()) + ".dmp";

    HANDLE file = ::CreateFileA(path.c_str(), GENERIC_WRITE, 0, nullptr,
                                CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file != INVALID_HANDLE_VALUE)
    {
        MINIDUMP_EXCEPTION_INFORMATION mei;
        mei.ThreadId = ::GetCurrentThreadId();
        mei.ExceptionPointers = info;
        mei.ClientPointers = FALSE;

        ::MiniDumpWriteDump(::GetCurrentProcess(), ::GetCurrentProcessId(), file,
                            static_cast<MINIDUMP_TYPE>(MiniDumpWithDataSegs |
                                                       MiniDumpWithHandleData),
                            info ? &mei : nullptr, nullptr, nullptr);
        ::CloseHandle(file);
    }

    // Continue searching so WER / a debugger still handle the crash and the
    // process terminates as usual.
    return EXCEPTION_CONTINUE_SEARCH;
}

#else  // POSIX

void FatalSignalHandler(int sig, siginfo_t * /*info*/, void * /*ctx*/)
{
    // Not async-signal-safe, but this is a last-resort crash logger.
    std::string path = CrashDumpDir() + "/stpyv8-crash-" + Timestamp() + "-" +
                       std::to_string(::getpid()) + ".txt";

    void *frames[128];
    int n = ::backtrace(frames, 128);

    FILE *f = std::fopen(path.c_str(), "w");
    if (f)
    {
        std::fprintf(f, "STPyV8 fatal signal %d (%s)\n", sig, strsignal(sig));
        std::fflush(f);
        ::backtrace_symbols_fd(frames, n, fileno(f));
        std::fclose(f);
    }

    // Restore the default disposition and re-raise so the exit status reflects
    // the signal and a core dump is produced when enabled.
    ::signal(sig, SIG_DFL);
    ::raise(sig);
}

#endif

}  // namespace

void InstallCrashHandler()
{
    if (!CrashDumpEnabled())
        return;

#ifdef _WIN32
    ::SetUnhandledExceptionFilter(&CrashExceptionFilter);
#else
    struct sigaction sa;
    std::memset(&sa, 0, sizeof(sa));
    sa.sa_sigaction = &FatalSignalHandler;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = SA_SIGINFO | SA_ONSTACK;
    const int signals[] = {SIGSEGV, SIGABRT, SIGBUS, SIGFPE, SIGILL};
    for (int s : signals)
        ::sigaction(s, &sa, nullptr);
#endif
}
