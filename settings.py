import sys
import os
import platform

STPYV8_HOME = os.path.dirname(os.path.realpath(__file__))
DEPOT_HOME = os.environ.get("DEPOT_HOME", os.path.join(STPYV8_HOME, "depot_tools"))
V8_HOME = os.environ.get("V8_HOME", os.path.join(STPYV8_HOME, "v8"))

V8_GIT_URL = "https://chromium.googlesource.com/v8/v8.git"
V8_GIT_TAG_STABLE = "13.1.201.22"
V8_GIT_TAG_MASTER = "master"
V8_GIT_TAG = V8_GIT_TAG_STABLE
DEPOT_GIT_URL = "https://chromium.googlesource.com/chromium/tools/depot_tools.git"

# Allow the wheel version to be overridden (e.g. a local patch suffix such as
# "13.1.201.22.post1") without changing the V8 tag that gets checked out/built.
STPYV8_VERSION = os.environ.get("STPYV8_VERSION", V8_GIT_TAG_STABLE)

v8_deps_linux = os.environ.get("V8_DEPS_LINUX", "1") in ("1",)

os.environ["PATH"] = f"{os.environ.get('PATH', '')}:{DEPOT_HOME}"

ICU_DATA_FOLDER_UNIX = "/usr/share/stpyv8"
ICU_DATA_FOLDER_OSX = "/Library/Application Support/STPyV8/"

if os.name in ("posix",):
    icu_data_folder = (
        ICU_DATA_FOLDER_OSX if sys.platform in ("darwin",) else ICU_DATA_FOLDER_UNIX
    )
else:
    icu_data_folder = None

gn_args = {
    "dcheck_always_on": "false",
    "is_component_build": "false",
    "is_debug": "true" if os.environ.get("STPYV8_DEBUG") else "false",
    "treat_warnings_as_errors": "false",
    # On Windows the STPyV8 extension is compiled with MSVC and the MSVC STL,
    # while V8's bundled libc++ uses the `std::__Cr` inline namespace. Any V8
    # API whose signature contains an STL type (e.g.
    # v8::Message::PrintCurrentStackTrace(isolate, std::ostream&) or
    # v8::platform::NewDefaultPlatform -> std::unique_ptr) then has a different
    # mangled name in V8 than in the extension, so linking fails. Build V8
    # against the MSVC STL on Windows to keep both sides ABI-compatible.
    "use_custom_libcxx": "false" if os.name == "nt" else "true",
    "v8_deprecation_warnings": "true",
    "v8_enable_disassembler": "false",
    "v8_enable_i18n_support": "true",
    "v8_enable_pointer_compression": "false",
    "v8_enable_31bit_smis_on_64bit_arch": "false",
    "v8_imminent_deprecation_warnings": "true",
    "v8_monolithic": "true",
    "v8_use_external_startup_data": "false",
}

source_files = [
    "Exception.cpp",
    "Platform.cpp",
    "Isolate.cpp",
    "Context.cpp",
    "Engine.cpp",
    "Wrapper.cpp",
    "Locker.cpp",
    "Utils.cpp",
    "CrashHandler.cpp",
    "STPyV8.cpp",
]


macros = [
    ("BOOST_PYTHON_STATIC_LIB", None),
    # Our Boost build uses the "system" library layout (boost_python312.lib),
    # which does not match the versioned name Boost auto-link derives from the
    # compiler flags. Disable auto-linking so the explicitly listed library is
    # used instead.
    ("BOOST_ALL_NO_LIB", None),
]
include_dirs = set()
library_dirs = set()
libraries = []
extra_compile_args = []
extra_link_args = []

include_dirs.add(os.path.join(V8_HOME, "include"))
library_dirs.add(
    os.path.join(V8_HOME, os.path.join("out.gn", "x64.release.sample", "obj"))
)


BOOST_PYTHON_LIB_SHORT = f"boost_python{sys.version_info.major}"
BOOST_PYTHON_LIB_LONG = f"boost_python{sys.version_info.major}{sys.version_info.minor}"

BOOST_PYTHON_UBUNTU_MATRIX = {
    "default": BOOST_PYTHON_LIB_LONG,
    "18.04": BOOST_PYTHON_LIB_SHORT,
    "20.04": f"{BOOST_PYTHON_LIB_SHORT}8",
    "22.04": f"{BOOST_PYTHON_LIB_SHORT}10",
}


def get_libboost_python_name():
    if not os.path.exists("/etc/lsb-release"):
        return BOOST_PYTHON_UBUNTU_MATRIX["default"]

    platform_info = {}

    with open("/etc/lsb-release", encoding="utf-8", mode="r") as fd:
        for line in fd.readlines():
            s = line.strip()
            p = s.split("=")

            if len(p) < 2:
                continue

            platform_info[p[0]] = p[1]

    if "DISTRIB_ID" not in platform_info:
        return BOOST_PYTHON_UBUNTU_MATRIX["default"]

    if platform_info["DISTRIB_ID"].lower() not in ("ubuntu",):
        return BOOST_PYTHON_UBUNTU_MATRIX["default"]

    release = platform_info["DISTRIB_RELEASE"]

    if release not in BOOST_PYTHON_UBUNTU_MATRIX:
        return BOOST_PYTHON_UBUNTU_MATRIX["default"]

    return BOOST_PYTHON_UBUNTU_MATRIX[release]


STPYV8_BOOST_PYTHON = os.getenv(
    "STPYV8_BOOST_PYTHON", default=get_libboost_python_name()
)

if os.name in ("nt",):
    include_dirs.add(os.path.join(V8_HOME, "include"))
    library_dirs.add(os.path.join(V8_HOME, "out.gn", "x64.release.sample", "obj"))

    if "BOOST_ROOT" in os.environ:
        include_dirs.add(os.environ.get("BOOST_ROOT"))
        library_dirs.add(os.path.join(os.environ["BOOST_ROOT"], "stage", "lib"))

    if "Python_ROOT_DIR" in os.environ:
        include_dirs.add(os.path.join(os.environ["Python_ROOT_DIR"], "include"))
        library_dirs.add(os.path.join(os.environ["Python_ROOT_DIR"], "libs"))

    if "INCLUDE" in os.environ:
        for p in os.environ["INCLUDE"].split(";"):
            include_dirs.add(p)
    if "LIB" in os.environ:
        for p in os.environ["LIB"].split(";"):
            library_dirs.add(p)

    libraries += [
        "winmm",
        "ws2_32",
        "Advapi32",
        "dbghelp",
        "v8_monolith",
        BOOST_PYTHON_LIB_LONG,
        # Boost components used by the extension (Exception.cpp uses
        # boost::iostreams, Platform.cpp uses boost::filesystem). Auto-linking is
        # disabled via BOOST_ALL_NO_LIB, so they must be listed explicitly.
        # Built with `--layout=system` so the names carry no compiler-version tag.
        "libboost_iostreams",
        "libboost_filesystem",
    ]
    extra_compile_args += ["/O2", "/GL", "/MT", "/EHsc", "/Gy", "/Zi", "/std:c++20", "/Zc:__cplusplus"]
    extra_link_args += ["/DLL", "/OPT:REF", "/OPT:ICF", "/MACHINE:X64"]
    if os.environ.get("STPYV8_PDB"):
        # Emit a PDB so native crashes can be symbolized in WinDbg. This is
        # independent of V8's build flavor: the extension stays a release (/MT)
        # build linked against the release v8_monolith, and only gains symbols.
        # (Do NOT gate this on STPYV8_DEBUG -- a debug V8 pulls in the debug CRT
        # (_malloc_dbg/_CrtDbgReport/...) which the release extension can't link.)
        extra_link_args += ["/DEBUG"]
    macros += [
        ("HAVE_SNPRINTF", None),
    ]

    os.environ["DEPOT_TOOLS_WIN_TOOLCHAIN"] = "0"

elif os.name in ("posix",):
    libraries = [
        "boost_system",
        "boost_iostreams",
        "boost_filesystem",
        "v8_monolith",
        STPYV8_BOOST_PYTHON.replace(".", ""),
    ]

    extra_compile_args.append("-Wno-strict-aliasing")
    extra_compile_args.append("-Wno-array-bounds")

    if platform.system() in ("Linux",):
        libraries.append("rt")
        extra_compile_args.append("-std=c++2a")
    else:
        extra_compile_args.append("-std=c++20")
        extra_link_args.append("-headerpad_max_install_names")


GN_ARGS = " ".join(f"{key}={value}" for key, value in gn_args.items())

include_dirs = list(include_dirs)
library_dirs = list(library_dirs)
