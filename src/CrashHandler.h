#pragma once

// Installs a process-wide crash handler that writes a native dump when the
// process dies from a fatal signal / unhandled exception.
//
// Enabled only when the STPYV8_CRASH_DUMP environment variable is set:
//   STPYV8_CRASH_DUMP=1            -> dump into the current working directory
//   STPYV8_CRASH_DUMP=/some/dir    -> dump into that directory
// Unset / "0" -> no handler is installed (default, zero overhead).
//
// Windows: a minidump (.dmp) via MiniDumpWriteDump (dbghelp).
// POSIX:   a backtrace text file, then the signal is re-raised with its
//          default disposition so a core dump is still produced if enabled.
void InstallCrashHandler();
