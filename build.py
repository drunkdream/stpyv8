#!/usr/bin/env python3
"""Cross-platform build driver for STPyV8.

This replaces the former Windows-only ``build_windows.ps1`` with a single
Python entry point. The Windows path is fully implemented and mirrors what used
to be done by hand; the Linux and macOS paths run the same pipeline with each
platform's native tooling. Platform specific bits are isolated in the steps
below so new targets are easy to add.

Pipeline
--------
  1. preflight        - verify interpreters / depot_tools / compiler / Boost
  2. checkout         - pin V8 to the tag declared in ``settings.py``
  3. sync             - ``gclient sync`` V8's third_party dependencies
  4. toolchain fixes  - platform workarounds (Windows SDK, ...)
  5. boost            - static, release Boost (iostreams, filesystem and one
                        Boost.Python per configured interpreter)
  6. v8               - ``gn gen`` + ``ninja v8_monolith``
  7. icu data         - install ``icudtl.dat`` where STPyV8 looks for it
  8. extension        - ``<python> setup.py build_ext --inplace`` (per interpreter)
  9. tests            - run the pytest suite (per interpreter)
 10. wheel            - ``<python> setup.py bdist_wheel --skip-build-v8`` per
                        interpreter (optional; pass ``--wheel``)

GN args are imported from ``settings.py`` so this script can never drift from
the project configuration.

Multiple Python versions
------------------------
Pass ``--python`` more than once. V8 is Python-agnostic and is built only once;
what is version specific is Boost.Python (whose library name embeds the version,
e.g. ``boost_python312``) and the extension module itself. The script therefore
builds one Boost.Python per interpreter and compiles the extension once per
interpreter, producing e.g. ``_STPyV8.cp312-win_amd64.pyd`` next to
``_STPyV8.cp39-win_amd64.pyd`` (or the matching ``.so`` files on POSIX).

Examples
--------
    python build.py
    python build.py --skip-v8-sync --skip-v8-build --skip-boost
    python build.py --v8-home /opt/v8 --boost-root /opt/boost_1_87_0
    python build.py --python C:\\py39\\python.exe --python C:\\py312\\python.exe
    python build.py --debug
    python build.py --docker --wheel        # build inside the stpyv8-builder container
"""

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys

IS_WINDOWS = os.name == "nt"
IS_LINUX = sys.platform.startswith("linux")
IS_MACOS = sys.platform == "darwin"

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
V8_OUT_REL = os.path.join("out.gn", "x64.release.sample")

# Container layout, shared with docker/entrypoint.sh: the repository is mounted at
# /stpyv8 (sources + collected .so/wheels) and the V8/Boost/depot_tools tree at
# /work (built once on the host, then reused across container runs).
DOCKER_IMAGE = "stpyv8-builder"
DOCKER_DIR = os.path.join(REPO_ROOT, "docker")
CONTAINER_REPO = "/stpyv8"
CONTAINER_BUILD_ROOT = "/work"

# PATH as it was before importing settings.py (whose import rewrites PATH with a
# hard-coded separator, which is wrong on Windows).
_BASE_PATH = os.environ.get("PATH", "")


class BuildError(RuntimeError):
    """Raised when a build step cannot proceed."""


def log(message=""):
    print(message, flush=True)


def section(title):
    log("")
    log("==> {}".format(title))


# ---------------------------------------------------------------------------
# Command helpers
# ---------------------------------------------------------------------------
def resolve(exe):
    """Resolve an executable through PATH, honouring PATHEXT on Windows."""
    if os.path.isabs(exe) or os.sep in exe or (os.altsep and os.altsep in exe):
        return exe
    return shutil.which(exe) or exe


def run(cmd, cwd=None, env=None, check=True, capture=False):
    """Run a command, streaming output unless ``capture`` is requested."""
    argv = [str(part) for part in cmd]
    argv[0] = resolve(argv[0])

    # Windows cannot CreateProcess a .bat/.cmd directly, so route it via cmd.exe.
    if IS_WINDOWS and argv[0].lower().endswith((".bat", ".cmd")):
        argv = [os.environ.get("COMSPEC", "cmd.exe"), "/c"] + argv

    log("    $ " + " ".join(argv))
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        check=check,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        text=True,
    )


def output(cmd, cwd=None, env=None):
    """Return stripped stdout of a command, or ``None`` when it fails."""
    try:
        proc = run(cmd, cwd=cwd, env=env, check=False, capture=True)
    except FileNotFoundError:
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip()


def depot_tool(cfg, name):
    """Return the path of a depot_tools entry point (``gclient``/``gn``/``ninja``).

    depot_tools ships both an extension-less Python script and a Windows ``.bat``
    wrapper; on Windows the wrapper must be preferred since CreateProcess cannot
    launch the extension-less script directly.
    """
    if IS_WINDOWS:
        candidates = (name + ".bat", name + ".exe", name + ".cmd", name, name + ".py")
    else:
        candidates = (name, name + ".sh")

    for candidate in candidates:
        path = os.path.join(cfg.depot_home, candidate)
        if os.path.isfile(path):
            return path
    return resolve(name)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def default_vs_install():
    for env_name in ("vs2022_install", "VSINSTALLDIR"):
        if os.environ.get(env_name):
            return os.environ[env_name]

    return (
        os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
    ) + r"\Microsoft Visual Studio\2022\BuildTools"


def _is_v8_checkout(path):
    """Return True when *path* looks like a V8 source checkout."""
    if not os.path.isdir(path):
        return False
    # A V8 checkout ships its public headers under include/ (v8.h).
    return os.path.isfile(os.path.join(path, "include", "v8.h"))


def _resolve_v8_home(path):
    """Normalize ``--v8-home`` to the actual V8 checkout directory.

    Users may point ``--v8-home`` at either the checkout itself
    (``.../v8src/v8``) or at its parent gclient sync root (``.../v8src``) when
    the checkout lives in a ``v8`` subdirectory -- the layout ``gclient sync``
    produces. Accept both by descending into a ``v8`` child when the given path
    is not itself a checkout.
    """
    path = os.path.abspath(path)
    if _is_v8_checkout(path):
        return path
    nested = os.path.join(path, "v8")
    if _is_v8_checkout(nested):
        return nested
    return path


class Config:
    def __init__(self, args):
        requested = [os.path.abspath(p) for p in (args.pythons or [sys.executable])]
        # Keep order but drop duplicates (building the same interpreter twice
        # would only repeat work and clash on the Boost.Python library name).
        self.pythons = list(dict.fromkeys(requested))
        self.python = self.pythons[0]

        self.v8_home = _resolve_v8_home(args.v8_home)
        self.v8_src_root = os.path.abspath(
            args.v8_src_root or os.path.dirname(self.v8_home)
        )
        self.depot_home = os.path.abspath(args.depot_home)
        self.boost_root = os.path.abspath(args.boost_root)

        self.jobs = args.jobs
        self.fetch_v8 = args.fetch_v8
        self.run_build_deps = args.run_build_deps
        self.no_sysroot = args.no_sysroot
        self.windows_sdk_version = args.windows_sdk_version
        self.vs_install = args.vs_install or default_vs_install()
        self.version = args.version
        self.debug = args.debug
        self.clean = args.clean

        self.settings = None
        self._gn_args_override = args.gn_args
        self._py_versions = {}

    # -- python helpers -----------------------------------------------------
    def python_dir_for(self, python):
        return os.path.dirname(os.path.abspath(python))

    @property
    def python_dir(self):
        return self.python_dir_for(self.python)

    def python_version(self, python):
        """Return the ``major.minor`` of an interpreter (cached)."""
        python = os.path.abspath(python)
        if python not in self._py_versions:
            value = output(
                [python, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"]
            )
            if not value:
                raise BuildError("cannot determine version of {}".format(python))
            self._py_versions[python] = value
        return self._py_versions[python]

    def boost_python_lib(self, python):
        """e.g. ``boost_python312`` for a Python 3.12 interpreter."""
        return "boost_python" + self.python_version(python).replace(".", "")

    def extension_path(self, python):
        """Absolute path of the extension that ``python`` will import."""
        suffix = output(
            [
                python,
                "-c",
                "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX') or '')",
            ]
        )
        if not suffix:
            return None
        return os.path.join(REPO_ROOT, "_STPyV8" + suffix)

    # -- derived paths ------------------------------------------------------
    @property
    def v8_out(self):
        return os.path.join(self.v8_home, V8_OUT_REL)

    @property
    def v8_monolith(self):
        name = "v8_monolith.lib" if IS_WINDOWS else "libv8_monolith.a"
        return os.path.join(self.v8_out, "obj", name)

    @property
    def icu_dat(self):
        return os.path.join(self.v8_out, "icudtl.dat")

    @property
    def boost_stage_lib(self):
        return os.path.join(self.boost_root, "stage", "lib")

    @property
    def sdk_include(self):
        return os.path.join(
            os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)",
            "Windows Kits",
            "10",
            "Include",
            self.windows_sdk_version,
        )

    @property
    def icu_user_file(self):
        """Location ``CPlatform::GetICUDataFile`` looks at first (per Config.h)."""
        if IS_WINDOWS:
            base = os.environ.get("APPDATA") or os.path.join(
                os.environ.get("USERPROFILE", ""), "AppData", "Roaming"
            )
            return os.path.join(base, "STPyV8", "icudtl.dat")
        if IS_MACOS:
            return os.path.join(
                os.environ.get("HOME", ""),
                "Library",
                "Application Support",
                "STPyV8",
                "icudtl.dat",
            )
        return os.path.join(
            os.environ.get("HOME", ""), ".local", "share", "stpyv8", "icudtl.dat"
        )

    # -- environment --------------------------------------------------------
    def env(self, python=None):
        """Environment for a subprocess.

        ``python`` selects which interpreter's ``Python_ROOT_DIR`` to expose, so
        that a per-interpreter ``setup.py build_ext`` finds the right headers.
        """
        python_dir = self.python_dir_for(python or self.python)

        env = os.environ.copy()
        env["V8_HOME"] = self.v8_home
        env["DEPOT_HOME"] = self.depot_home
        env["BOOST_ROOT"] = self.boost_root
        if self.version:
            env["STPYV8_VERSION"] = self.version

        path_parts = []

        if IS_WINDOWS:
            env["Python_ROOT_DIR"] = python_dir
            env["DEPOT_TOOLS_WIN_TOOLCHAIN"] = "0"
            env["vs2022_install"] = self.vs_install
            path_parts = [self.boost_stage_lib, self.depot_home, python_dir]
        else:
            # settings.py links against system Boost on POSIX; point the compiler
            # at the tree this script builds so it works without root installs.
            if os.path.isdir(self.boost_stage_lib):
                env["LIBRARY_PATH"] = os.pathsep.join(
                    [self.boost_stage_lib, env.get("LIBRARY_PATH", "")]
                ).strip(os.pathsep)
                env["LD_LIBRARY_PATH"] = os.pathsep.join(
                    [self.boost_stage_lib, env.get("LD_LIBRARY_PATH", "")]
                ).strip(os.pathsep)
            if IS_MACOS and os.path.isdir(self.boost_stage_lib):
                env["DYLD_LIBRARY_PATH"] = os.pathsep.join(
                    [self.boost_stage_lib, env.get("DYLD_LIBRARY_PATH", "")]
                ).strip(os.pathsep)
            env["CPATH"] = os.pathsep.join(
                [self.boost_root, env.get("CPATH", "")]
            ).strip(os.pathsep)
            path_parts = [self.depot_home]

        env["PATH"] = os.pathsep.join([p for p in path_parts + [_BASE_PATH] if p])
        return env

    @property
    def gn_args(self):
        if self._gn_args_override:
            return self._gn_args_override
        args = self.settings.GN_ARGS
        if self.no_sysroot:
            # Build against the host system libraries instead of the extracted
            # sysroot, which cannot be unpacked on NTFS/exFAT (see --no-sysroot).
            if "use_sysroot" not in args:
                args = args + " use_sysroot=false"
        return args


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------
def ensure_python_deps(python):
    # importlib_resources is STPyV8's runtime dep on Python < 3.10 (setup.py:
    # `importlib_resources; python_version < '3.10'`); the tests import STPyV8,
    # so make sure it is installed for every interpreter.
    packages = ["setuptools", "wheel", "pytest", "importlib_resources"]
    # uv-managed interpreters reject pip installs unless --break-system-packages is
    # given, so we try a plain install first and fall back to that flag. The failed
    # attempt's output is captured so the noisy externally-managed error is not
    # shown when the fallback succeeds.
    last_output = None
    for extra in ([], ["--break-system-packages"]):
        cmd = [python, "-m", "pip", "install", "--quiet", *extra, *packages]
        try:
            proc = run(cmd, check=False, capture=True)
        except FileNotFoundError:
            log("    warning: pip not available; assuming build deps are installed")
            return
        if proc.returncode == 0:
            return
        last_output = proc.stdout
    log("    warning: could not install setuptools/wheel/pytest; continuing")
    if last_output:
        for line in last_output.strip().splitlines()[-3:]:
            log("    " + line)


def _system_tool_hint(tool):
    """Best-effort, per-distro install hint for a missing system tool."""
    if IS_MACOS:
        return "Install it with: brew install {}".format(tool)
    if IS_LINUX:
        if os.path.isfile("/etc/debian_version"):
            return "Install it with: sudo apt install {}".format(tool)
        if os.path.isfile("/etc/redhat-release"):
            return "Install it with: sudo dnf install {} (or yum)".format(tool)
        if os.path.isfile("/etc/arch-release"):
            return "Install it with: sudo pacman -S {}".format(tool)
        if os.path.isfile("/etc/alpine-release"):
            return "Install it with: sudo apk add {}".format(tool)
    return "Install '{}' and make sure it is on PATH.".format(tool)


def _python_dev_hint(version):
    """Human-readable 'install this' hint for missing CPython dev headers."""
    if IS_LINUX and os.path.isfile("/etc/debian_version"):
        return (
            "Install the CPython development headers:\n"
            "  sudo apt install python{ver}-dev libpython{ver}-dev\n"
            "(Debian/Ubuntu split the headers across packages; the multiarch "
            "pyconfig.h under /usr/include/<triplet>/python{ver}/ ships in "
            "libpython{ver}-dev).".format(ver=version)
        )
    if IS_LINUX:
        return (
            "Install the CPython development headers (python{ver}-devel on RPM "
            "distros, python{ver}-dev on Debian/Ubuntu).".format(ver=version)
        )
    if IS_MACOS:
        return (
            "Install the CPython development headers for Python {ver} (the "
            "python.org installer or brew provide them; Xcode CLT required).".format(
                ver=version
            )
        )
    return "Install the CPython development headers for Python {}.".format(version)


def ensure_python_dev(cfg, python):
    """Verify the interpreter's C development headers are actually usable.

    On Debian/Ubuntu the Python headers are split across packages and
    /usr/include/pythonX.Y/pyconfig.h pulls in
    <triplet>/pythonX.Y/pyconfig.h (from libpythonX.Y-dev). When that package is
    missing the failure only surfaces much later, inside setup.py build_ext, as
    an obscure 'pyconfig.h: No such file' error. Probe it here with a tiny
    compile so we can fail early with an actionable 'install X' message.
    """
    version = cfg.python_version(python)
    probe = (
        "import sysconfig, subprocess, tempfile, os, sys;"
        "inc = sysconfig.get_path('include');"
        "plat = sysconfig.get_path('platinclude') or inc;"
        "cc = (sysconfig.get_config_var('CC') or 'cc').split();"
        "cf = (sysconfig.get_config_var('CFLAGS') or '').split();"
        "d = tempfile.mkdtemp();"
        "s = os.path.join(d, 't.c');"
        "f = open(s, 'w');"
        "f.write('#include <Python.h>\\nint main(void){return 0;}\\n');"
        "f.close();"
        "o = os.path.join(d, 't.o');"
        "cmd = cc + ['-I' + inc, '-I' + plat] + cf + ['-c', s, '-o', o];"
        "r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True);"
        "sys.stdout.write('HEADERS_OK' if r.returncode == 0 else 'HEADERS_MISSING\\n' + r.stdout)"
    )
    proc = run([python, "-c", probe], check=False, capture=True)
    if proc.returncode == 0 and "HEADERS_OK" in (proc.stdout or ""):
        log("    Python {} development headers OK".format(version))
        return

    out = (proc.stdout or "").strip()
    log("    Python {} development headers NOT usable".format(version))
    for line in out.splitlines()[-8:]:
        if line.strip():
            log("      " + line)
    raise BuildError(
        "Python {} C development headers are missing or incomplete (cannot "
        "compile '#include <Python.h>').\n{}".format(version, _python_dev_hint(version))
    )


def ensure_depot_tools(cfg):
    """Clone depot_tools when it is missing, asking the user first.

    depot_tools is required for every V8 build (fetch / gclient / gn / ninja).
    Rather than failing outright when it is absent, offer to download it so a
    fresh checkout can be bootstrapped interactively. A non-interactive session
    (no TTY / EOF) is treated as a "no" and falls back to the usual error.
    """
    if os.path.exists(cfg.depot_home):
        return

    log("    depot_tools not found at {}".format(cfg.depot_home))
    answer = ""
    try:
        answer = input(
            "    depot_tools is required to build V8. Download it now? [y/N] "
        ).strip().lower()
    except (EOFError, OSError):
        answer = ""

    if answer not in ("y", "yes"):
        raise BuildError(
            "depot_tools not found at {}. Install it from "
            "https://chromium.googlesource.com/chromium/tools/depot_tools.git "
            "or set DEPOT_HOME.".format(cfg.depot_home)
        )

    log("    cloning depot_tools into {}".format(cfg.depot_home))
    os.makedirs(os.path.dirname(cfg.depot_home), exist_ok=True)
    run(
        [
            "git",
            "clone",
            "https://chromium.googlesource.com/chromium/tools/depot_tools.git",
            cfg.depot_home,
        ]
    )


def ensure_boost(cfg):
    """Clone the Boost source tree when it is missing, asking the user first.

    The build invokes Boost's bootstrap + b2 locally, so a full Boost source
    tree (with submodules) is required. Rather than failing outright when it is
    absent, offer to download it so a fresh checkout can be bootstrapped
    interactively. A non-interactive session (no TTY / EOF) is treated as a
    "no" and falls back to the usual error.
    """
    if os.path.exists(cfg.boost_root):
        return

    log("    Boost root not found at {}".format(cfg.boost_root))
    answer = ""
    try:
        answer = input(
            "    Boost is required to build STPyV8. Download it now? [y/N] "
        ).strip().lower()
    except (EOFError, OSError):
        answer = ""

    if answer not in ("y", "yes"):
        raise BuildError(
            "Boost root not found at {}. Provide a Boost source tree via "
            "--boost-root or BOOST_ROOT.".format(cfg.boost_root)
        )

    log("    cloning Boost into {}".format(cfg.boost_root))
    os.makedirs(os.path.dirname(cfg.boost_root), exist_ok=True)

    clone_cmd = [
        "git",
        "clone",
        "--recursive",
        "--depth",
        "1",
        "--shallow-submodules",
        "https://github.com/boostorg/boost.git",
        cfg.boost_root,
    ]
    # Pin to a specific Boost release when one is configured. Passing --branch
    # to clone checks the tag out directly (and --recursive initialises the
    # matching submodules), which is exactly "clone, then checkout <tag> and
    # re-init submodules" but in a single network-efficient step.
    boost_tag = getattr(cfg.settings, "BOOST_GIT_TAG", None)
    if boost_tag:
        clone_cmd[2:2] = ["--branch", boost_tag]
        log("    pinning Boost to tag {}".format(boost_tag))
    run(clone_cmd)


def step_preflight(cfg):
    section("1/9 preflight")

    # Fail fast on the cheapest, most common blocker: the CPython development
    # headers for every requested interpreter. Check this first so that, when a
    # header library is missing, we print the install hint and exit before
    # spending any time cloning depot_tools / Boost / V8.
    for python in cfg.pythons:
        if not os.path.isfile(python):
            raise BuildError(
                "Python interpreter not found at {}. Fix the --python path, or "
                "install the intended CPython together with its dev headers "
                "(pythonX.Y-dev / libpythonX.Y-dev).".format(python)
            )
    for python in cfg.pythons:
        log("    Python {}: {}".format(cfg.python_version(python), python))
        ensure_python_dev(cfg, python)

    ensure_depot_tools(cfg)
    ensure_boost(cfg)

    if IS_WINDOWS:
        if not os.path.isdir(os.path.join(cfg.vs_install, "VC", "Tools", "MSVC")):
            raise BuildError(
                "MSVC Build Tools not found under {}".format(cfg.vs_install)
            )
        if not os.path.isdir(cfg.sdk_include):
            raise BuildError(
                "Windows SDK {} not installed (expected {})".format(
                    cfg.windows_sdk_version, cfg.sdk_include
                )
            )
    elif IS_MACOS:
        if output(["xcode-select", "-p"]) is None:
            raise BuildError(
                "Xcode command line tools not found; run: xcode-select --install"
            )
    elif IS_LINUX:
        for tool in ("git", "patch"):
            if shutil.which(tool) is None:
                raise BuildError(
                    "required tool '{}' not found on PATH.\n{}".format(
                        tool, _system_tool_hint(tool)
                    )
                )

    for python in cfg.pythons:
        ensure_python_deps(python)
    log("    toolchain OK")


def ensure_v8(cfg):
    """Fetch the V8 source tree via depot_tools when it is missing.

    V8 is fetched with depot_tools' ``fetch`` command (which sets up the gclient
    solution). When no checkout exists we ask the user whether to download it;
    passing --fetch-v8 skips the prompt and proceeds automatically, which is
    useful for non-interactive runs.
    """
    if os.path.isdir(os.path.join(cfg.v8_home, ".git")):
        return

    log("    V8 checkout not found at {}".format(cfg.v8_home))
    if cfg.fetch_v8:
        answer = "y"
    else:
        answer = ""
        try:
            answer = input(
                "    V8 source is required to build STPyV8. Download it now? [y/N] "
            ).strip().lower()
        except (EOFError, OSError):
            answer = ""

    if answer not in ("y", "yes"):
        raise BuildError(
            "No V8 checkout at {}. Create one first with:\n"
            "  {}/fetch --no-history v8\n"
            "or re-run this script with --fetch-v8.".format(
                cfg.v8_home, cfg.depot_home
            )
        )

    os.makedirs(cfg.v8_src_root, exist_ok=True)
    run(
        [depot_tool(cfg, "fetch"), "--no-history", "v8"],
        cwd=cfg.v8_src_root,
        env=cfg.env(),
    )


def step_checkout(cfg):
    section("2/9 checkout V8")

    ensure_v8(cfg)

    tag = cfg.settings.V8_GIT_TAG_STABLE
    log("    settings.py pins V8 {}".format(tag))

    if output(["git", "-C", cfg.v8_home, "describe", "--tags", "--exact-match"]) == tag:
        log("    already on the pinned tag")
        return

    # A shallow/no-history clone will not have tags yet.
    run(["git", "-C", cfg.v8_home, "fetch", "origin", "tag", tag])
    run(["git", "-C", cfg.v8_home, "checkout", tag])
    log("    HEAD is now {}".format(output(["git", "-C", cfg.v8_home, "describe", "--tags"])))


def step_sync(cfg):
    section("3/9 gclient sync")

    # gclient refuses to run when a managed repo has uncommitted changes; the
    # toolchain patches below are re-applied after every sync, so discard them.
    build_repo = os.path.join(cfg.v8_home, "build")
    if os.path.isdir(os.path.join(build_repo, ".git")):
        if output(["git", "-C", build_repo, "status", "--porcelain"]):
            log("    discarding uncommitted changes in v8/build")
            run(["git", "-C", build_repo, "checkout", "--", "."])

    # The sysroot tarball contains file names with literal backslashes
    # (e.g. system-systemd\x2dcryptsetup.slice) that cannot be created on
    # NTFS/exFAT mounts, so gclient sync dies with EINVAL. With --no-sysroot we
    # skip all hooks (which include the sysroot extraction) and regenerate the
    # build-essential ones by hand afterwards.
    sync_cmd = [depot_tool(cfg, "gclient"), "sync", "-D", "-j{}".format(cfg.jobs)]
    if cfg.no_sysroot:
        sync_cmd.append("--nohooks")
    run(sync_cmd, cwd=cfg.v8_src_root, env=cfg.env())

    if cfg.no_sysroot:
        _run_v8_lastchange_hooks(cfg)

    if IS_LINUX and cfg.run_build_deps:
        log("    installing additional Linux dependencies (needs sudo)")
        run(
            ["./v8/build/install-build-deps.sh"],
            cwd=cfg.v8_src_root,
            env=cfg.env(),
        )


def _run_v8_lastchange_hooks(cfg):
    """Generate the V8 version headers that 'gclient sync --nohooks' skips.

    gclient's hooks normally produce build/util/LASTCHANGE (and .committime);
    when we pass --nohooks to dodge the NTFS-incompatible sysroot extraction we
    must create these ourselves, or the V8 build cannot derive its version and
    fails. Missing scripts are tolerated (warning only) so unrelated setups
    still proceed.
    """
    lastchange = os.path.join(cfg.v8_home, "build", "util", "lastchange.py")
    if not os.path.isfile(lastchange):
        log("    warning: {} not found; skipping version header generation".format(lastchange))
        return
    for output_file in ("build/util/LASTCHANGE", "build/util/LASTCHANGE.committime"):
        run(
            [cfg.python, lastchange, "-o", output_file],
            cwd=cfg.v8_home,
            env=cfg.env(),
            check=False,
        )


def _patch_sdk_version(path, version):
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    updated = re.sub(r"SDK_VERSION\s*=\s*'[0-9.]+'", "SDK_VERSION = '{}'".format(version), text)
    if updated != text:
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(updated)
        log("    patched {} -> SDK {}".format(os.path.basename(path), version))


def step_toolchain(cfg):
    section("4/9 apply toolchain workarounds")

    if not IS_WINDOWS:
        log("    nothing to do on this platform")
        return

    # (a) V8's toolchain scripts hard-code the SDK revision they shipped with.
    #     Point them at the SDK that is actually installed.
    _patch_sdk_version(
        os.path.join(cfg.v8_home, "build", "toolchain", "win", "setup_toolchain.py"),
        cfg.windows_sdk_version,
    )
    _patch_sdk_version(
        os.path.join(cfg.v8_home, "build", "vs_toolchain.py"),
        cfg.windows_sdk_version,
    )

    # (b) Chromium/V8 builds pass -DNTDDI_VERSION=NTDDI_WIN11_BR. That codename
    #     macro is missing from some SDKs, which silently disables NTDDI guards
    #     (e.g. FILE_INFO_BY_HANDLE_CLASS) and breaks ICU compilation.
    ddkver = os.path.join(cfg.sdk_include, "shared", "sdkddkver.h")
    if os.path.isfile(ddkver):
        with open(ddkver, "r", encoding="utf-8", errors="ignore") as handle:
            text = handle.read()
        if "NTDDI_WIN11_BR" not in text:
            if not text.endswith("\n"):
                text += "\n"
            text += (
                "\n// Added by STPyV8 build.py: codename macro expected by "
                "Chromium/V8 builds.\n#define NTDDI_WIN11_BR                      0x0A00000F\n"
            )
            with open(ddkver, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
            log("    added NTDDI_WIN11_BR to sdkddkver.h")

    # (c) V8 needs dbghelp.dll in the SDK's Debuggers folder, which is absent
    #     when the "Debugging Tools for Windows" component was not installed.
    program_files_x86 = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
    dbg_dir = os.path.join(program_files_x86, "Windows Kits", "10", "Debuggers", "x64")
    dbg_dll = os.path.join(dbg_dir, "dbghelp.dll")
    if not os.path.isfile(dbg_dll):
        os.makedirs(dbg_dir, exist_ok=True)
        system_root = os.environ.get("SystemRoot") or r"C:\Windows"
        source = os.path.join(system_root, "System32", "dbghelp.dll")
        if os.path.isfile(source):
            shutil.copy2(source, dbg_dll)
            log("    copied dbghelp.dll into the SDK Debuggers folder")

    log("    SDK workarounds applied")


def _clean_boost_build_outputs(cfg):
    """Drop Boost's build cache and staged libs so b2 rebuilds them here.

    Only called after detecting that the checked-in b2 -- and therefore the
    existing ``bin.v2`` objects and ``stage`` libs -- were produced by a
    different toolchain and cannot run/link in this environment. Container-built
    (older-glibc) artifacts are forward-compatible with newer hosts, so replacing
    them is safe; only build outputs are removed, never the Boost sources.
    """
    for name in ("bin.v2", "bin", "stage"):
        path = os.path.join(cfg.boost_root, name)
        if os.path.isdir(path):
            log("    removing stale {} (built by another toolchain)".format(path))
            shutil.rmtree(path, ignore_errors=True)


def _bootstrap_boost(cfg):
    runner = "b2.exe" if IS_WINDOWS else "b2"
    b2_path = os.path.join(cfg.boost_root, runner)
    stale = False
    if os.path.isfile(b2_path):
        # A b2 left over from a different toolchain (e.g. the host's newer
        # glibc / libstdc++) cannot run here -- it dies with "GLIBC_2.3x not
        # found" / "GLIBCXX_3.4.x not found". Probe it; if it does not run,
        # rebuild it with the current toolchain instead of failing much later.
        probe = run([b2_path, "--version"], cwd=cfg.boost_root, check=False, capture=True)
        if probe.returncode == 0:
            return
        log("    existing {} does not run in this environment; rebuilding b2".format(runner))
        try:
            os.remove(b2_path)
        except OSError:
            pass
        stale = True

    script = "bootstrap.bat" if IS_WINDOWS else "./bootstrap.sh"
    log("    bootstrapping Boost ({})".format(script))
    run([script], cwd=cfg.boost_root, env=cfg.env())

    if stale:
        # The object cache and staged libs were built by that other toolchain
        # too; drop them so the upcoming b2 run rebuilds everything here.
        _clean_boost_build_outputs(cfg)


def _jam_path(path):
    """Escape a path so Boost.Build's Jam parser keeps the backslashes.

    Inside a Jam double-quoted string ``\\`` is an escape sequence, so an
    unescaped Windows path such as ``C:\\Users\\...`` arrives at Boost with every
    backslash stripped, which breaks interpreter probing. Doubling them restores
    the literal backslashes.
    """
    return path.replace("\\", "\\\\")


def _configure_boost_python(cfg, python=None):
    """Write ``project-config.jam`` for the Boost.Python build.

    When ``python`` is supplied, only that interpreter is declared, so a plain
    ``--with-python`` builds exactly its Boost.Python. That yields the
    un-prefixed name (``boost_pythonXY.lib`` on Windows) that ``settings.py``
    links against. Selecting the version with ``python=<ver>`` instead makes
    Boost emit a ``lib``-prefixed name the extension's link line does not expect,
    so we avoid it.

    Any previous ``using python`` lines are dropped; other configuration (e.g.
    ``using msvc``) is preserved.
    """
    pcj = os.path.join(cfg.boost_root, "project-config.jam")

    kept = []
    if os.path.isfile(pcj):
        with open(pcj, "r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if re.match(r"\s*using\s+python\b", line):
                    continue
                kept.append(line.rstrip("\n"))

    interpreters = [python] if python else cfg.pythons
    for interp in interpreters:
        kept.append('using python : : "{}" ;'.format(_jam_path(os.path.abspath(interp))))

    with open(pcj, "w", encoding="utf-8", newline="") as handle:
        handle.write("\n".join(kept) + "\n")

    log(
        "    configured Boost.Python for {}".format(
            ", ".join(cfg.python_version(p) for p in interpreters)
        )
    )


def expected_boost_libs(cfg):
    libs = []
    for python in cfg.pythons:
        name = cfg.boost_python_lib(python)
        libs.append(name + ".lib" if IS_WINDOWS else "lib" + name + ".a")

    if IS_WINDOWS:
        libs += ["libboost_iostreams.lib", "libboost_filesystem.lib"]
    else:
        libs += ["libboost_iostreams.a", "libboost_filesystem.a"]
    return libs


def step_boost(cfg):
    section("5/9 build Boost")

    _bootstrap_boost(cfg)
    _configure_boost_python(cfg)

    b2 = os.path.join(cfg.boost_root, "b2.exe" if IS_WINDOWS else "b2")
    common = [
        "stage",
        "-j",
        str(cfg.jobs),
        "link=static",
        "runtime-link=static",
        # layout=system      -> predictable names (boost_python312, libboost_*),
        #                       matching what settings.py links against.
        # address-model=64   -> 64 bit (otherwise Boost builds 32 bit!).
        # variant=release    -> /MT_StaticRelease, matching the /MT extension.
        "--layout=system",
        "variant=release",
        "--with-python",
        "--with-iostreams",
        "--with-filesystem",
    ]
    if IS_WINDOWS:
        common.append("address-model=64")
    else:
        # On POSIX the static Boost libraries are linked into the shared
        # _STPyV8 extension, so they must be built as position-independent code.
        # Without -fPIC the final link fails with "recompile with -fPIC".
        common.append("cflags=-fPIC")
        common.append("cxxflags=-fPIC")

    # Build one Boost.Python per interpreter. Each iteration rewrites
    # project-config.jam to declare only that interpreter, so a plain
    # --with-python produces its boost_pythonXY library without a lib prefix
    # (the name settings.py links against). The iostreams/filesystem libraries
    # are shared and simply rebuild incrementally.
    for python in cfg.pythons:
        version = cfg.python_version(python)
        log("    building Boost.Python for Python {}".format(version))
        _configure_boost_python(cfg, python)
        run([b2, *common], cwd=cfg.boost_root, env=cfg.env())

    # Safety net: Boost sometimes emits a lib-prefixed python library
    # (libboost_pythonXY.lib / libboost_pythonXY.a). Copy it to the un-prefixed
    # name settings.py expects so the extension link always resolves.
    for python in cfg.pythons:
        name = cfg.boost_python_lib(python)
        if IS_WINDOWS:
            expected, alt = name + ".lib", "lib" + name + ".lib"
        else:
            expected, alt = "lib" + name + ".a", name + ".a"
        expected_path = os.path.join(cfg.boost_stage_lib, expected)
        alt_path = os.path.join(cfg.boost_stage_lib, alt)
        if not os.path.isfile(expected_path) and os.path.isfile(alt_path):
            shutil.copy2(alt_path, expected_path)
            log("    copied {} -> {}".format(alt, expected))

    for name in expected_boost_libs(cfg):
        if not os.path.isfile(os.path.join(cfg.boost_stage_lib, name)):
            raise BuildError(
                "Boost library missing after build: {}".format(
                    os.path.join(cfg.boost_stage_lib, name)
                )
            )
        log("    found {}".format(name))


def step_v8(cfg):
    section("6/9 build v8_monolith")

    env = cfg.env()
    log("    gn args: {}".format(cfg.gn_args))

    run(
        [depot_tool(cfg, "gn"), "gen", cfg.v8_out, "--args={}".format(cfg.gn_args)],
        cwd=cfg.v8_home,
        env=env,
    )
    run(
        [depot_tool(cfg, "ninja"), "-C", V8_OUT_REL, "v8_monolith", "-j{}".format(cfg.jobs)],
        cwd=cfg.v8_home,
        env=env,
    )

    if not os.path.isfile(cfg.v8_monolith):
        raise BuildError("v8_monolith was not produced at {}".format(cfg.v8_monolith))
    log("    built {}".format(cfg.v8_monolith))


def step_icu(cfg):
    section("7/9 install ICU data")

    if not os.path.isfile(cfg.icu_dat):
        raise BuildError("icudtl.dat not found at {}".format(cfg.icu_dat))

    dst = cfg.icu_user_file
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(cfg.icu_dat, dst)
    log("    installed {}".format(dst))


def clean_extension_outputs(cfg):
    """Remove the in-place extension .so files and the distutils build/ dir.

    These are the extension's own build outputs and the ones most likely to be
    stale/foreign -- e.g. a .so left by a host build against a newer glibc, which
    then fails to import here ("undefined symbol: __libc_single_threaded", a
    glibc 2.32+ symbol). Removing them forces build_ext to recompile from source
    with the current toolchain. Sources and the V8/Boost trees are untouched.
    """
    section("clean extension outputs")
    removed = False
    for so in glob.glob(os.path.join(REPO_ROOT, "_STPyV8*.so")):
        try:
            os.remove(so)
            log("    removed {}".format(os.path.basename(so)))
            removed = True
        except OSError as error:
            log("    warning: could not remove {}: {}".format(so, error))
    build_dir = os.path.join(REPO_ROOT, "build")
    if os.path.isdir(build_dir):
        shutil.rmtree(build_dir, ignore_errors=True)
        log("    removed build/")
        removed = True
    if not removed:
        log("    nothing to clean")


def step_extension(cfg, python):
    version = cfg.python_version(python)
    section("8/9 build STPyV8 extension (Python {})".format(version))

    cmd = [python, "setup.py", "build_ext", "--inplace"]
    # distutils only tracks the object files it compiles; it will NOT relink the
    # extension when an external static library (v8_monolith) is rebuilt. That
    # leaves a stale .so linked against the previous V8 (e.g. an old libc++ V8
    # after switching to libstdc++, or a V8 rebuilt with different flags). Force
    # a rebuild whenever v8_monolith is newer than the extension so the relink
    # always picks up the current V8.
    ext_path = cfg.extension_path(python)
    force = cfg.debug
    if (
        not force
        and os.path.isfile(cfg.v8_monolith)
        and ext_path
        and os.path.isfile(ext_path)
        and os.path.getmtime(cfg.v8_monolith) > os.path.getmtime(ext_path)
    ):
        log("    v8_monolith is newer than the extension; forcing a relink")
        force = True
    if force:
        cmd.append("--force")
    run(cmd, cwd=REPO_ROOT, env=cfg.env(python))

    path = cfg.extension_path(python)
    if not path or not os.path.isfile(path):
        raise BuildError(
            "The _STPyV8 extension for Python {} was not produced (expected {})".format(
                version, path
            )
        )
    log("    built {}".format(os.path.basename(path)))

    if cfg.debug:
        # link.exe (with /DEBUG) writes <output>.pdb beside the .pyd inside the
        # build/lib dir; copy it next to the in-place extension so WinDbg can
        # find it (matched by the embedded PDB GUID, name is conventional).
        stem = os.path.splitext(os.path.basename(path))[0]
        src_pdb = None
        for cand in sorted(glob.glob(os.path.join(REPO_ROOT, "build", "lib*", stem + ".pdb"))):
            src_pdb = cand
            break
        dst_pdb = os.path.join(REPO_ROOT, stem + ".pdb")
        if src_pdb and os.path.isfile(src_pdb):
            shutil.copy2(src_pdb, dst_pdb)
            log("    symbols {}".format(dst_pdb))
        else:
            log("    warning: --debug was set but no PDB was found under build/")


def step_tests(cfg, python):
    section("9/9 run tests (Python {})".format(cfg.python_version(python)))
    run([python, "-m", "pytest", "tests", "-q"], cwd=REPO_ROOT, env=cfg.env(python))


def step_wheel(cfg, python):
    version = cfg.python_version(python)
    section("build wheel (Python {})".format(version))

    # V8 was already built by step_v8, so tell setup.py not to rebuild it
    # (otherwise bdist_wheel would re-run gclient sync + ninja).
    run(
        [python, "setup.py", "bdist_wheel", "--skip-build-v8"],
        cwd=REPO_ROOT,
        env=cfg.env(python),
    )

    dist = os.path.join(REPO_ROOT, "dist")
    if not os.path.isdir(dist):
        raise BuildError("dist/ was not created for Python {}".format(version))

    # Only report the wheels for this interpreter's tag (dist/ may hold wheels
    # from previous runs / other versions).
    tag = "cp" + version.replace(".", "")
    built = sorted(
        name for name in os.listdir(dist) if name.endswith(".whl") and tag in name
    )
    if not built:
        raise BuildError("no wheel produced for Python {} in {}".format(version, dist))
    for name in built:
        log("    built {}".format(name))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Cross-platform build driver for STPyV8.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        # Disable prefix abbreviation: an abbreviated flag (e.g. --host for
        # --hosts) is accepted by argparse but would NOT be recognised by the
        # docker arg-forwarder (which matches raw tokens), so it would leak into
        # the container. Requiring exact flags keeps the two in sync.
        allow_abbrev=False,
    )

    parser.add_argument(
        "--python",
        dest="pythons",
        action="append",
        metavar="INTERPRETER",
        help="interpreter to build for; repeat the flag for several versions "
        "(default: the interpreter running this script)",
    )
    parser.add_argument(
        "--v8-home",
        default=os.environ.get("V8_HOME") or os.path.join(REPO_ROOT, ".build", "v8"),
        help="V8 checkout",
    )
    parser.add_argument("--v8-src-root", help="directory gclient sync runs in (default: parent of --v8-home)")
    parser.add_argument(
        "--depot-home",
        default=os.environ.get("DEPOT_HOME") or os.path.join(REPO_ROOT, ".build", "depot_tools"),
        help="depot_tools checkout",
    )
    parser.add_argument(
        "--boost-root",
        default=os.environ.get("BOOST_ROOT") or os.path.join(REPO_ROOT, ".build", "boost"),
        help="Boost source tree",
    )
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 4, help="parallel build jobs")
    parser.add_argument("--gn-args", help="override the GN args taken from settings.py")
    parser.add_argument("--windows-sdk-version", default="10.0.26100.0", help="[Windows] installed SDK revision")
    parser.add_argument("--vs-install", help="[Windows] Visual Studio / Build Tools install path")
    parser.add_argument("--debug", action="store_true", help="emit an extension PDB (_STPyV8.pdb) for crash symbolization; V8 stays in release mode (STPYV8_PDB)")
    parser.add_argument("--fetch-v8", action="store_true", help="run 'fetch --no-history v8' when no checkout exists")
    parser.add_argument("--run-build-deps", action="store_true", help="[Linux] run V8's install-build-deps.sh (needs sudo)")
    parser.add_argument(
        "--no-sysroot",
        action="store_true",
        help="[Linux/NTFS] skip the sysroot download/extract (gclient --nohooks) and build with use_sysroot=false; "
        "needed when the checkout lives on a filesystem that rejects the sysroot's backslash file names "
        "(e.g. NTFS/exFAT). Requires host build deps (--run-build-deps).",
    )

    parser.add_argument("--skip-v8-sync", action="store_true", help="skip gclient sync")
    parser.add_argument("--skip-v8-build", action="store_true", help="skip gn gen / ninja")
    parser.add_argument("--skip-boost", action="store_true", help="skip the Boost build")
    parser.add_argument("--skip-tests", action="store_true", help="skip the pytest suite")
    parser.add_argument(
        "--wheel",
        action="store_true",
        help="also build a wheel (bdist_wheel --skip-build-v8) for each interpreter",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="remove existing extension outputs (_STPyV8*.so and build/) before "
        "building, forcing a from-scratch extension compile; without it the "
        "build is incremental (only changed sources / a newer v8_monolith relink)",
    )
    parser.add_argument(
        "--version",
        help="override the wheel version (default: the V8 tag from settings.py); "
        "the V8 checkout/build tag is unaffected",
    )

    parser.add_argument(
        "--docker",
        action="store_true",
        help="[Linux/macOS] run the whole pipeline inside the stpyv8-builder "
        "container instead of natively. The repo and the symlink-resolved build "
        "tree (.build) are mounted, so V8/Boost/depot_tools are reused from the "
        "host and the .so / wheels land back in the repo.",
    )
    parser.add_argument(
        "--docker-image",
        default=DOCKER_IMAGE,
        help="[--docker] image name to build and run",
    )
    parser.add_argument(
        "--docker-no-build",
        action="store_true",
        help="[--docker] fail instead of auto-building the image when it is missing",
    )
    parser.add_argument(
        "--docker-shell",
        action="store_true",
        help="[--docker] drop into a bash shell in the container (same mounts) "
        "instead of building; handy to inspect the mount layout",
    )
    parser.add_argument(
        "--hosts",
        "--host",
        dest="hosts",
        action="append",
        metavar="HOST:IP",
        help="[--docker] add an entry to the container's /etc/hosts (docker "
        "--add-host); repeatable, e.g. --hosts my.mirror:10.0.0.1. Accepts "
        "either 'host:ip' or the /etc/hosts order 'ip host'.",
    )

    return parser.parse_args(argv)


def load_settings(cfg, debug):
    """Import settings.py after the environment it reads has been seeded."""
    if debug:
        # Only add symbols to the extension (PDB). V8 stays in release mode:
        # a debug V8 references the debug CRT, which the /MT release extension
        # cannot link against.
        os.environ["STPYV8_PDB"] = "1"
    os.environ["V8_HOME"] = cfg.v8_home
    os.environ["DEPOT_HOME"] = cfg.depot_home
    os.environ["BOOST_ROOT"] = cfg.boost_root
    if IS_WINDOWS:
        os.environ["Python_ROOT_DIR"] = cfg.python_dir

    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)

    import settings  # pylint:disable=import-outside-toplevel

    return settings


# ---------------------------------------------------------------------------
# Docker backend (Linux/macOS): run the pipeline inside stpyv8-builder
# ---------------------------------------------------------------------------
def _docker_present():
    return shutil.which("docker") is not None


def _docker_image_exists(image):
    """True when *image* is already built (quiet; no streamed '$ ...' line)."""
    try:
        subprocess.run(
            ["docker", "image", "inspect", image],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _docker_build_image(image):
    if not os.path.isfile(os.path.join(DOCKER_DIR, "Dockerfile")):
        raise BuildError("docker/Dockerfile not found under {}".format(DOCKER_DIR))
    log("    building image '{}' from {}".format(image, DOCKER_DIR))
    run(["docker", "build", "-t", image, DOCKER_DIR])


def _forward_without_docker_flags(argv):
    """Drop the docker-only and host-path flags; keep the rest for the container.

    ``--python`` is translated into ``UV_PYTHONS`` (the container selects its own
    interpreters), and the three build-tree flags are re-emitted with their
    container paths, so their host values must not be forwarded verbatim.
    """
    value_flags = (
        "--python",
        "--docker-image",
        "--v8-home",
        "--v8-src-root",
        "--depot-home",
        "--boost-root",
        "--hosts",
        "--host",
    )
    bool_flags = ("--docker", "--docker-no-build", "--docker-shell")
    kept = []
    skip_next = False
    for token in argv:
        if skip_next:
            skip_next = False
            continue
        name = token.split("=", 1)[0]
        if token in value_flags:  # "--flag value": drop the value token too
            skip_next = True
            continue
        if token in bool_flags or name in value_flags:  # bool flag, or "--flag=value"
            continue
        kept.append(token)
    return kept


def _normalize_add_host(value):
    """Normalise a --hosts value into docker's ``host:ip`` --add-host form.

    Accepts the three shapes people naturally type and always returns
    ``host:ip`` (docker's required order):
      * ``host:ip``    -> ``host:ip`` (pass-through, incl. ``host:ip:alias``)
      * ``ip host``    -> ``host:ip`` (the /etc/hosts column order)
      * ``ip:host``    -> ``host:ip``
    """
    token = (value or "").strip()
    if not token:
        raise BuildError("--hosts needs a value, e.g. --hosts my.mirror:10.0.0.1")
    # Space-separated: /etc/hosts order ("ip host").
    parts = token.split()
    if len(parts) == 2:
        first, second = parts
        return "{}:{}".format(second, first) if _looks_like_ip(first) else token
    if len(parts) > 2:
        raise BuildError(
            "cannot parse --hosts value {!r}; expected 'host:ip' or 'ip host'".format(value)
        )
    # Single token: figure out which side of ':' is the IP.
    head, sep, tail = token.partition(":")
    if not sep:
        raise BuildError(
            "cannot parse --hosts value {!r}; expected 'host:ip' or 'ip host'".format(value)
        )
    if _looks_like_ip(head):
        # "ip:host" (or "ip:host:alias") -> move the IP to the end.
        rest = tail.split(":")
        return ":".join([rest[0], head] + rest[1:])
    return token  # already host:ip (possibly host:ip:alias)


def _looks_like_ip(text):
    """True when *text* is a dotted-quad IPv4 address."""
    octets = text.split(".")
    if len(octets) != 4:
        return False
    for octet in octets:
        if not octet.isdigit() or not 0 <= int(octet) <= 255:
            return False
    return True


def run_in_docker(args, argv):
    section("docker backend")
    if IS_WINDOWS:
        raise BuildError(
            "--docker is wired up for Linux/macOS hosts only (Docker Desktop path "
            "translation is not handled); run it from WSL or a Linux/macOS shell."
        )
    if not _docker_present():
        raise BuildError("'docker' not found on PATH; install Docker to use --docker.")

    image = args.docker_image or DOCKER_IMAGE

    # Resolve the three build trees to their REAL host paths. This is the crux of
    # the ".build is a symlink" problem: os.path.realpath follows
    # .build -> ~/.stpyv8-build, so we mount the directory it points at. Mounting
    # the symlink itself (or the un-resolved .build/v8) would leave a dangling
    # path inside the container, which has no ~/.stpyv8-build of its own.
    v8_home = _resolve_v8_home(args.v8_home)
    depot_home = os.path.abspath(args.depot_home)
    boost_root = os.path.abspath(args.boost_root)
    reals = [os.path.realpath(p) for p in (v8_home, depot_home, boost_root)]
    try:
        build_root = os.path.commonpath(reals)
    except ValueError:
        raise BuildError(
            "V8 / depot_tools / Boost do not share a common directory ({}); "
            "--docker mounts a single build root, so keep them under one tree "
            "(the default .build layout).".format(reals)
        )
    if not os.path.isdir(build_root):
        log("    note: build root {} does not exist yet; docker will create it".format(build_root))

    def container_path(host_path):
        rel = os.path.relpath(os.path.realpath(host_path), build_root)
        return os.path.join(CONTAINER_BUILD_ROOT, rel)

    c_v8 = container_path(v8_home)
    c_depot = container_path(depot_home)
    c_boost = container_path(boost_root)

    # Always (re)build unless explicitly disabled. `docker build` is a near
    # no-op when nothing changed (every layer hits the cache), so this keeps the
    # image in sync with docker/Dockerfile + entrypoint.sh without the user
    # having to remember to rebuild by hand after editing them.
    if args.docker_no_build:
        if not _docker_image_exists(image):
            raise BuildError(
                "image '{}' is missing and --docker-no-build was given; build it "
                "first with: docker build -t {} {}".format(image, image, DOCKER_DIR)
            )
    else:
        _docker_build_image(image)

    # Turn explicitly requested host interpreters into the versions the
    # container's uv-managed Pythons provide. With no --python we leave UV_PYTHONS
    # unset so the image default (all supported versions) applies.
    env_args = []
    if args.pythons:
        versions = []
        for python in args.pythons:
            version = output(
                [python, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"]
            )
            if not version:
                raise BuildError(
                    "cannot determine the version of host interpreter {}".format(python)
                )
            versions.append(version)
        env_args = ["-e", "UV_PYTHONS=" + " ".join(dict.fromkeys(versions))]

    if args.docker_shell:
        # Same mounts, but start an interactive shell instead of the build.
        run_cmd = ["--entrypoint", "bash"]
    else:
        run_cmd = _forward_without_docker_flags(argv)
        run_cmd += ["--v8-home", c_v8, "--depot-home", c_depot, "--boost-root", c_boost]

    cmd = ["docker", "run", "--rm"]
    # Keep the session interactive when attached to a TTY so depot_tools / gclient
    # and the entrypoint can prompt; stay quiet (no -t) under CI / pipes.
    if sys.stdin.isatty():
        cmd.append("-it")
    # Run as the invoking host user: the bind-mounted repo and build tree are
    # owned by that user, so (a) git never trips its "dubious ownership" guard on
    # the host-owned V8 / DEPS repos, and (b) artifacts written back stay owned
    # by the user (no root-owned files to clean up later). HOME points at a
    # writable tmpfs because the container has no passwd entry for this uid.
    try:
        cmd += ["--user", "{}:{}".format(os.getuid(), os.getgid()), "-e", "HOME=/tmp"]
    except AttributeError:
        pass  # os.getuid unavailable; fall back to running as root
    cmd += [
        "-v", "{}:{}".format(os.path.abspath(REPO_ROOT), CONTAINER_REPO),
        "-v", "{}:{}".format(build_root, CONTAINER_BUILD_ROOT),
    ]
    # Extra /etc/hosts entries inside the container (docker --add-host). The
    # value is normalised to docker's "host:ip" order so /etc/hosts-style
    # "ip host" input works too.
    for host in (args.hosts or []):
        cmd += ["--add-host", _normalize_add_host(host)]
    cmd += env_args
    cmd += [image]
    cmd += run_cmd

    log("    repo    : {} -> {}".format(os.path.abspath(REPO_ROOT), CONTAINER_REPO))
    log("    build   : {} -> {} (resolved through the .build symlink)".format(
        build_root, CONTAINER_BUILD_ROOT))
    log("    v8      : {}".format(c_v8))
    log("    depot   : {}".format(c_depot))
    log("    boost   : {}".format(c_boost))
    if env_args:
        log("    pythons : {}".format(env_args[-1].split("=", 1)[1]))
    log("    $ " + " ".join(cmd))

    # check=False so a failing build surfaces through its streamed output and a
    # clean exit status, not a Python traceback.
    proc = run(cmd, check=False)
    log("")
    if proc.returncode == 0:
        log("container finished; artifacts are in the repo (.so / dist/) and the build tree")
    else:
        log("docker run exited with status {}".format(proc.returncode))
    return proc.returncode


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    args = parse_args(argv)

    if args.docker or args.docker_shell:
        return run_in_docker(args, argv)

    if args.hosts:
        log("note: --hosts only affects --docker runs (container /etc/hosts); "
            "ignored for a native build")

    cfg = Config(args)

    section("STPyV8 build")
    log("  platform : {}".format(sys.platform))
    log("  python   : {}".format(", ".join(cfg.pythons)))
    log("  v8       : {}".format(cfg.v8_home))
    log("  boost    : {}".format(cfg.boost_root))
    if args.version:
        log("  version  : {}".format(args.version))

    cfg.settings = load_settings(cfg, args.debug)

    if cfg.clean:
        clean_extension_outputs(cfg)

    try:
        step_preflight(cfg)
        step_checkout(cfg)

        if args.skip_v8_sync:
            section("3/9 gclient sync")
            log("    skipped (--skip-v8-sync)")
        else:
            step_sync(cfg)

        step_toolchain(cfg)

        if args.skip_boost:
            section("5/9 build Boost")
            log("    skipped (--skip-boost)")
        else:
            step_boost(cfg)

        if args.skip_v8_build:
            section("6/9 build v8_monolith")
            log("    skipped (--skip-v8-build)")
        else:
            step_v8(cfg)

        # V8 and the ICU data are Python-agnostic: everything below runs once
        # per configured interpreter.
        step_icu(cfg)

        for python in cfg.pythons:
            step_extension(cfg, python)

        if args.skip_tests:
            section("9/9 run tests")
            log("    skipped (--skip-tests)")
        else:
            for python in cfg.pythons:
                step_tests(cfg, python)

        if args.wheel:
            for python in cfg.pythons:
                step_wheel(cfg, python)
    except BuildError as error:
        log("")
        log("BUILD FAILED: {}".format(error))
        return 1

    log("")
    log("BUILD COMPLETE")
    log(
        "  V8       : {} ({})".format(
            output(["git", "-C", cfg.v8_home, "describe", "--tags"]), cfg.v8_out
        )
    )
    for python in cfg.pythons:
        path = cfg.extension_path(python)
        built = os.path.basename(path) if path and os.path.isfile(path) else "(missing)"
        log("  py{}    : {}".format(cfg.python_version(python), built))

    if args.wheel:
        dist = os.path.join(REPO_ROOT, "dist")
        if os.path.isdir(dist):
            wheels = sorted(name for name in os.listdir(dist) if name.endswith(".whl"))
            if wheels:
                log("  wheels   : {}".format(", ".join(wheels)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
