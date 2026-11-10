#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Regression tests for the build.py environment guards.

These checks are pure Python: they never invoke V8, Boost or the compiled
extension, so they also run before `_STPyV8*.so` exists (which is exactly when
a stale, foreign-toolchain binary has to be detected).
"""

import ctypes.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = str(Path(__file__).resolve().parents[1])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import build  # noqa: E402  (path set up above)


class TestExtensionLoads(unittest.TestCase):
    """The load probe must distinguish a broken .so from a usable one."""

    def test_missing_file_is_reported_as_unloadable(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "_STPyV8.missing.so")
            ok, message = build._extension_loads(sys.executable, missing)
        self.assertFalse(ok)
        self.assertIn("OSError", message)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux only")
    def test_loadable_library_passes(self):
        # `CDLL` resolves a bare soname through the dynamic loader; use libc
        # rather than depending on a compiled extension being present.
        name = ctypes.util.find_library("c") or "libc.so.6"
        ok, _message = build._extension_loads(sys.executable, name)
        self.assertTrue(ok)


class TestPruneIncompatibleExtensions(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.so = os.path.join(self.root, "_STPyV8.fake.so")
        self.build_dir = os.path.join(self.root, "build", "lib.fake")
        self.cfg = types.SimpleNamespace(
            pythons=["/fake/python"],
            extension_path=lambda _python: self.so,
        )
        for patcher in (
            mock.patch.object(build, "REPO_ROOT", self.root),
            mock.patch.object(build, "IS_LINUX", True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _create_outputs(self, with_so=True):
        if with_so:
            with open(self.so, "wb") as handle:
                handle.write(b"not really an extension")
        os.makedirs(os.path.join(self.build_dir, "obj"))
        with open(os.path.join(self.build_dir, "_STPyV8.fake.so"), "wb") as handle:
            handle.write(b"stale cached extension")

    def test_unloadable_extension_and_build_cache_are_removed(self):
        # build_ext --inplace would otherwise copy the stale build/lib*/ copy
        # straight back without relinking.
        self._create_outputs()
        with mock.patch.object(
            build, "_extension_loads", return_value=(False, "OSError: GLIBC_2.38 not found")
        ) as loads:
            build.prune_incompatible_extensions(self.cfg)
        loads.assert_called_once_with("/fake/python", self.so)
        self.assertFalse(os.path.exists(self.so))
        self.assertFalse(os.path.isdir(os.path.join(self.root, "build")))

    def test_loadable_extension_is_kept(self):
        self._create_outputs()
        with mock.patch.object(build, "_extension_loads", return_value=(True, "")):
            build.prune_incompatible_extensions(self.cfg)
        self.assertTrue(os.path.isfile(self.so))
        self.assertTrue(os.path.isdir(self.build_dir))

    def test_failure_reason_is_logged(self):
        self._create_outputs()
        with mock.patch.object(
            build, "_extension_loads", return_value=(False, "OSError: GLIBC_2.38 not found")
        ), mock.patch.object(build, "log") as log:
            build.prune_incompatible_extensions(self.cfg)
        self.assertTrue(
            any("GLIBC_2.38" in call.args[0] for call in log.call_args_list),
            "the loader error must reach the build log",
        )

    def test_missing_extension_leaves_build_cache_alone(self):
        self._create_outputs(with_so=False)
        with mock.patch.object(build, "_extension_loads") as loads:
            build.prune_incompatible_extensions(self.cfg)
        loads.assert_not_called()
        self.assertTrue(os.path.isdir(self.build_dir))

    def test_failed_removal_does_not_drop_build_cache(self):
        self._create_outputs()
        with mock.patch.object(
            build, "_extension_loads", return_value=(False, "OSError: broken")
        ), mock.patch.object(build.os, "remove", side_effect=OSError("read-only")):
            build.prune_incompatible_extensions(self.cfg)
        self.assertTrue(os.path.isdir(self.build_dir))

    def test_non_linux_is_a_noop(self):
        self._create_outputs()
        with mock.patch.object(build, "IS_LINUX", False), mock.patch.object(
            build, "_extension_loads"
        ) as loads:
            build.prune_incompatible_extensions(self.cfg)
        loads.assert_not_called()
        self.assertTrue(os.path.isfile(self.so))
        self.assertTrue(os.path.isdir(self.build_dir))


class TestEnsureBoostWritable(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = os.path.join(tmp.name, "boost")
        self.nested = os.path.join(self.root, "bin.v2", "libs", "python")
        os.makedirs(self.nested)
        os.makedirs(os.path.join(self.root, "stage", "lib"))
        self.cfg = types.SimpleNamespace(boost_root=self.root)
        patcher = mock.patch.object(build, "IS_WINDOWS", False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_missing_boost_root_is_a_noop(self):
        cfg = types.SimpleNamespace(boost_root=os.path.join(self.root, "absent"))
        build.ensure_boost_writable(cfg)

    def test_writable_tree_passes(self):
        build.ensure_boost_writable(self.cfg)

    def test_unwritable_subdirectory_raises_actionable_error(self):
        # Simulate a root-owned bin.v2 left behind by a previous container run.
        # Patch os.access instead of chmod so the test also works as root.
        with mock.patch.object(
            build.os,
            "access",
            side_effect=lambda path, mode: os.path.abspath(path) != self.nested,
        ):
            with self.assertRaises(build.BuildError) as raised:
                build.ensure_boost_writable(self.cfg)
        message = str(raised.exception)
        self.assertIn(self.nested, message)
        self.assertIn("chown", message)
        self.assertIn(build.DOCKER_IMAGE, message)

    def test_windows_is_a_noop(self):
        with mock.patch.object(build, "IS_WINDOWS", True), mock.patch.object(
            build.os, "access", side_effect=AssertionError("should not check")
        ):
            build.ensure_boost_writable(self.cfg)


if __name__ == "__main__":
    unittest.main()
