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
        self.windows_sdk_version = args.windows_sdk_version
        self.vs_install = args.vs_install or default_vs_install()
        self.version = args.version
        self.debug = args.debug

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
        return self.settings.GN_ARGS


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------
def ensure_python_deps(python):
    packages = ["setuptools", "wheel", "pytest"]
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


def step_preflight(cfg):
    section("1/9 preflight")

    for path, what in ((cfg.depot_home, "depot_tools"), (cfg.boost_root, "Boost root")):
        if not os.path.exists(path):
            raise BuildError("{} not found at {}".format(what, path))

    for python in cfg.pythons:
        if not os.path.isfile(python):
            raise BuildError("Python interpreter not found at {}".format(python))

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
                raise BuildError("required tool '{}' not found on PATH".format(tool))

    for python in cfg.pythons:
        log("    Python {}: {}".format(cfg.python_version(python), python))
        ensure_python_deps(python)
    log("    toolchain OK")


def step_checkout(cfg):
    section("2/9 checkout V8")

    if not os.path.isdir(os.path.join(cfg.v8_home, ".git")):
        if not cfg.fetch_v8:
            raise BuildError(
                "No V8 checkout at {}. Create one first with:\n"
                "  {}/fetch --no-history v8\n"
                "or re-run this script with --fetch-v8.".format(
                    cfg.v8_home, cfg.depot_home
                )
            )
        run(
            [depot_tool(cfg, "fetch"), "--no-history", "v8"],
            cwd=cfg.v8_src_root,
            env=cfg.env(),
        )

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

    run(
        [depot_tool(cfg, "gclient"), "sync", "-D", "-j{}".format(cfg.jobs)],
        cwd=cfg.v8_src_root,
        env=cfg.env(),
    )

    if IS_LINUX and cfg.run_build_deps:
        log("    installing additional Linux dependencies (needs sudo)")
        run(
            ["./v8/build/install-build-deps.sh"],
            cwd=cfg.v8_src_root,
            env=cfg.env(),
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


def _bootstrap_boost(cfg):
    runner = "b2.exe" if IS_WINDOWS else "b2"
    if os.path.isfile(os.path.join(cfg.boost_root, runner)):
        return

    script = "bootstrap.bat" if IS_WINDOWS else "./bootstrap.sh"
    log("    bootstrapping Boost ({})".format(script))
    run([script], cwd=cfg.boost_root, env=cfg.env())


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


def step_extension(cfg, python):
    version = cfg.python_version(python)
    section("8/9 build STPyV8 extension (Python {})".format(version))

    cmd = [python, "setup.py", "build_ext", "--inplace"]
    if cfg.debug:
        # Force a relink so the /DEBUG PDB link flags actually take effect;
        # distutils would otherwise skip compiling/linking when sources are
        # unchanged (a plain rebuild after a non-debug one produces no PDB).
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
        default=os.environ.get("V8_HOME") or os.path.join(REPO_ROOT, "v8"),
        help="V8 checkout",
    )
    parser.add_argument("--v8-src-root", help="directory gclient sync runs in (default: parent of --v8-home)")
    parser.add_argument(
        "--depot-home",
        default=os.environ.get("DEPOT_HOME") or os.path.join(REPO_ROOT, "depot_tools"),
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
        "--version",
        help="override the wheel version (default: the V8 tag from settings.py); "
        "the V8 checkout/build tag is unaffected",
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


def main(argv=None):
    args = parse_args(argv)
    cfg = Config(args)

    section("STPyV8 build")
    log("  platform : {}".format(sys.platform))
    log("  python   : {}".format(", ".join(cfg.pythons)))
    log("  v8       : {}".format(cfg.v8_home))
    log("  boost    : {}".format(cfg.boost_root))
    if args.version:
        log("  version  : {}".format(args.version))

    cfg.settings = load_settings(cfg, args.debug)

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
