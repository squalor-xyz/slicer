"""Keep outside-project fixtures meaningful even inside a tracked checkout."""

from __future__ import annotations

import os
from contextlib import nullcontext
from pathlib import Path
import unittest
from unittest.mock import patch

import support
from slicer import store, vcs
from slicer.errors import StateError


class DiscoveryIsolationTests(unittest.TestCase):
  def test_Isolation_NestedUninitializedFixture_HidesEnclosingProjects(self) -> None:
    with support.TempRepo(git=True) as parent:
      self.assertEqual(parent.run("init")[0], 0)
      child = parent.root / "child"
      child.mkdir()
      self.assertEqual(store.discover(child), parent.root)
      self.assertTrue(vcs.is_repo(child))
      with support.isolated_discovery(child):
        with self.assertRaises(StateError) as caught:
          store.discover(child)
        self.assertEqual(caught.exception.code, "state")
        self.assertIn("slicer init", str(caught.exception))
        self.assertFalse(vcs.is_repo(child))

  def test_Isolation_NestedProject_FindsItsOwnConfigAndGit(self) -> None:
    with support.TempRepo(git=True) as parent:
      self.assertEqual(parent.run("init")[0], 0)
      child = parent.root / "child"
      self.assertEqual(parent.run("init", "--root", str(child))[0], 0)
      result = parent._git("init", "-q", str(child))
      self.assertEqual(result.returncode, 0, result.stderr)
      nested = child / "nested" / "deeper"
      nested.mkdir(parents=True)
      with support.isolated_discovery(child):
        for start in (child, nested):
          with self.subTest(start=start):
            self.assertEqual(store.discover(start), child)
            self.assertTrue(vcs.is_repo(start))
            self.assertEqual(vcs._run(start, "rev-parse", "--show-toplevel").stdout.strip(),
                             str(child))

  def test_Isolation_OtherFileChecks_StillUseTheFilesystem(self) -> None:
    with support.TempRepo() as parent:
      ancestor_file = parent.write("keep.txt", "visible")
      child = parent.root / "child"
      child.mkdir()
      child_config = parent.write("child/.slicer/config.json", "{}")
      with support.isolated_discovery(child):
        self.assertTrue(ancestor_file.is_file())
        self.assertTrue(child_config.is_file())
        self.assertFalse((child / "missing").is_file())
        self.assertFalse(child.is_file())

  def test_Isolation_NormalAndExceptionalExit_RestoreDiscoveryAndEnvironment(self) -> None:
    with support.TempRepo(git=True) as parent:
      self.assertEqual(parent.run("init")[0], 0)
      child = parent.root / "child"
      child.mkdir()
      original_is_file = Path.is_file
      key = "GIT_CEILING_DIRECTORIES"
      for previous in (None, str(parent.root.parent)):
        for fail in (False, True):
          with self.subTest(previous=previous, fail=fail), patch.dict(os.environ):
            if previous is None:
              os.environ.pop(key, None)
            else:
              os.environ[key] = previous
            outcome = self.assertRaisesRegex(RuntimeError, "test exception") if fail else nullcontext()
            with outcome:
              with support.isolated_discovery(child):
                self.assertEqual(os.environ[key], str(parent.root))
                self.assertFalse(vcs.is_repo(child))
                if fail:
                  raise RuntimeError("test exception")
            self.assertIs(Path.is_file, original_is_file)
            self.assertEqual(os.environ.get(key), previous)
            self.assertEqual(store.discover(child), parent.root)
            self.assertTrue(vcs.is_repo(child))
