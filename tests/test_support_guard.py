"""The suite's guard against writes into the real repository's git dir.

A fixture under an in-checkout TMPDIR that is not its own git repo resolves to
this repository, so a regression in discovery can write into the shared
`.git/slicer/` ledger. These tests point the guard at a scratch root and prove
it refuses each write path while reads and outside writes still work.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import support


class SupportGuardTests(unittest.TestCase):
  def setUp(self) -> None:
    tmp = tempfile.TemporaryDirectory(prefix="slicer-test-")
    self.addCleanup(tmp.cleanup)
    self.outside = Path(tmp.name).resolve()
    self.root = self.outside / "common"
    with support.allow_git_dir_writes():
      self.root.mkdir()
    guarded = patch.object(support, "GUARDED_ROOT", self.root)
    guarded.start()
    self.addCleanup(guarded.stop)

  def test_SupportGuard_OpenForWriteUnderGuardedRoot_Raises(self) -> None:
    target = self.root / "slicer" / "relay-runs.jsonl"
    with self.assertRaises(AssertionError) as caught:
      open(target, "a")
    self.assertIn(str(target), str(caught.exception))
    with self.assertRaises(AssertionError):
      target.write_text("x", encoding="utf-8")
    with self.assertRaises(AssertionError):
      os.open(target, os.O_WRONLY | os.O_CREAT)
    self.assertFalse(target.exists())

  def test_SupportGuard_ReadUnderGuardedRoot_Allowed(self) -> None:
    target = self.root / "HEAD"
    with support.allow_git_dir_writes():
      target.write_text("ref: refs/heads/main\n", encoding="utf-8")
    with open(target, encoding="utf-8") as fh:
      self.assertEqual(fh.read(), "ref: refs/heads/main\n")
    self.assertEqual(target.read_text(encoding="utf-8"), "ref: refs/heads/main\n")
    os.close(os.open(target, os.O_RDONLY))

  def test_SupportGuard_ReplaceIntoGuardedRoot_Raises(self) -> None:
    staged = self.outside / "staged.json"
    staged.write_text("{}\n", encoding="utf-8")
    target = self.root / "state.json"
    with self.assertRaises(AssertionError) as caught:
      os.replace(staged, target)
    self.assertIn(str(target), str(caught.exception))
    with self.assertRaises(AssertionError):
      os.rename(staged, target)
    self.assertTrue(staged.exists())
    self.assertFalse(target.exists())

  def test_SupportGuard_MkdirUnderGuardedRoot_Raises(self) -> None:
    target = self.root / "slicer"
    with self.assertRaises(AssertionError) as caught:
      target.mkdir()
    self.assertIn(str(target), str(caught.exception))
    with self.assertRaises(AssertionError):
      os.makedirs(target / "deep", exist_ok=True)
    self.assertFalse(target.exists())

  def test_SupportGuard_WriteOutsideGuardedRoot_Allowed(self) -> None:
    sibling = self.outside / "common-sibling"
    sibling.mkdir()
    (sibling / "a.txt").write_text("a", encoding="utf-8")
    os.replace(sibling / "a.txt", sibling / "b.txt")
    with open(self.outside / "c.txt", "a", encoding="utf-8") as fh:
      fh.write("c")
    self.assertEqual((sibling / "b.txt").read_text(encoding="utf-8"), "a")


if __name__ == "__main__":
  unittest.main()
