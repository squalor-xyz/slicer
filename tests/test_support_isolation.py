"""Stop a missing fixture project from exposing its enclosing project's state."""

from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

import support


class TempRepoIsolationTests(unittest.TestCase):
  def test_TempRepoRun_ProjectResolvesOutsideFixture_RefusesBeforeRunning(self) -> None:
    with support.TempRepo() as parent:
      self.assertEqual(parent.run("init")[0], 0)
      self.assertEqual(parent.run("add", "Parent item")[0], 0)
      before = (parent.read(".slicer/index.json"), parent.read(".slicer/log.jsonl"))
      with patch.object(tempfile, "tempdir", str(parent.root)), support.TempRepo() as child:
        with patch("slicer.cli.main") as main:
          with self.assertRaises(AssertionError) as caught:
            child.run("add", "x")
          main.assert_not_called()
        self.assertIn(str(child.root), str(caught.exception))
        self.assertIn(str(parent.root), str(caught.exception))
      self.assertEqual(
        (parent.read(".slicer/index.json"), parent.read(".slicer/log.jsonl")), before
      )

  def test_TempRepoRun_InitInEmptyFixture_StillWorks(self) -> None:
    with support.TempRepo() as parent:
      self.assertEqual(parent.run("init")[0], 0)
      before = parent.read(".slicer/index.json")
      with patch.object(tempfile, "tempdir", str(parent.root)), support.TempRepo() as child:
        self.assertEqual(child.run("init")[0], 0)
        self.assertEqual(child.run("add", "Child item")[0], 0)
        self.assertEqual(child.state().root, child.root)
        self.assertEqual(child.state().index.items[0].title, "Child item")
      self.assertEqual(parent.read(".slicer/index.json"), before)

  def test_TempRepoRun_IsolatedDiscovery_StillAllowsOutsideProjectFixtures(self) -> None:
    with support.TempRepo() as parent:
      self.assertEqual(parent.run("init")[0], 0)
      with patch.object(tempfile, "tempdir", str(parent.root)), support.TempRepo() as child:
        with support.isolated_discovery(child.root):
          code, _, err = child.run("list")
          self.assertEqual(code, 2)
          self.assertIn("no .slicer/", err)

  def test_TempRepoRun_ExplicitParentDiscovery_OptsOutForOneCall(self) -> None:
    with support.TempRepo() as parent:
      self.assertEqual(parent.run("init")[0], 0)
      self.assertEqual(parent.run("add", "Parent item")[0], 0)
      with patch.object(tempfile, "tempdir", str(parent.root)), support.TempRepo() as child:
        code, out, _ = child.run("list", allow_parent_project=True)
        self.assertEqual(code, 0)
        self.assertIn("Parent item", out)
        with self.assertRaises(AssertionError):
          child.run("list")

  def test_TempRepoRun_InitBlockedByEnclosingMerge_LaterMutationIsRefused(self) -> None:
    with support.TempRepo(git=True) as parent:
      self.assertEqual(parent.run("init")[0], 0)
      self.assertEqual(parent.run("add", "Parent item")[0], 0)
      parent.write("tracked.txt", "fixture\n")
      self.assertEqual(parent._git("add", "-f", "tracked.txt").returncode, 0)
      self.assertEqual(parent._git("commit", "-q", "-m", "fixture").returncode, 0)
      marker = parent.write(".git/MERGE_HEAD", parent._git("rev-parse", "HEAD").stdout)
      before = (parent.read(".slicer/index.json"), parent.read(".slicer/log.jsonl"))
      with patch.object(tempfile, "tempdir", str(parent.root)), support.TempRepo() as child:
        code, _, err = child.run("init")
        self.assertEqual(code, 2)
        self.assertIn("Git merge in progress", err)
        self.assertFalse((child.root / ".slicer/config.json").exists())
        marker.unlink()
        with self.assertRaises(AssertionError):
          child.run("add", "x")
      self.assertEqual(
        (parent.read(".slicer/index.json"), parent.read(".slicer/log.jsonl")), before
      )
