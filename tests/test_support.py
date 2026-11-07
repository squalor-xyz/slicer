"""Prove temporary Git writers cannot turn successful tests into teardown failures."""

from __future__ import annotations

import errno
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import support
from slicer import jsonio
from support import TempRepo


class TempRepoCleanupTests(unittest.TestCase):
  def test_TempRepoClose_TransientRace_RetriesAndRemovesTree(self) -> None:
    for error in (FileNotFoundError("vanished lock"), OSError(errno.ENOTEMPTY, "new lock")):
      with self.subTest(error=type(error).__name__):
        repo = TempRepo(git=True)
        self.addCleanup(repo.close)
        repo.write(".git/race.lock", "lock")
        original = shutil.rmtree
        attempts = []

        def racing_remove(*args, **kwargs):
          attempts.append(args[0])
          if len(attempts) == 1:
            raise error
          return original(*args, **kwargs)

        with patch("support.shutil.rmtree", side_effect=racing_remove):
          repo.close()
        self.assertGreaterEqual(len(attempts), 2)
        self.assertFalse(repo.root.exists())

  def test_TempRepoClose_PersistentRace_ReraisesAfterBoundedAttempts(self) -> None:
    for error in (FileNotFoundError("vanished lock"), OSError(errno.ENOTEMPTY, "new lock")):
      with self.subTest(error=type(error).__name__):
        repo = TempRepo(git=True)
        self.addCleanup(repo.close)
        with patch("support.shutil.rmtree", side_effect=error) as remove:
          with self.assertRaises(type(error)) as caught:
            repo.close()
        self.assertIs(caught.exception, error)
        self.assertEqual(remove.call_count, 3)

  def test_TempRepoClose_UnrelatedError_PropagatesWithoutRetry(self) -> None:
    for error in (PermissionError("denied"), OSError(errno.EIO, "I/O failure")):
      with self.subTest(error=type(error).__name__):
        repo = TempRepo(git=True)
        self.addCleanup(repo.close)
        with patch("support.shutil.rmtree", side_effect=error) as remove:
          with self.assertRaises(type(error)) as caught:
            repo.close()
        self.assertIs(caught.exception, error)
        remove.assert_called_once()

  def test_TempRepoClose_AlreadyRemovedTree_Succeeds(self) -> None:
    repo = TempRepo(git=True)
    self.addCleanup(repo.close)
    shutil.rmtree(repo.root)
    repo.close()
    self.assertFalse(repo.root.exists())


class FsyncStubTests(unittest.TestCase):
  """The suite stubs the durability syncs of `jsonio.write_bytes`; only tests/test_jsonio.py needs them real (s447)."""

  def test_Support_FsyncStub_WriteBytesSkipsTheSyncs(self) -> None:
    self.assertIsNot(os.fsync, support.REAL_FSYNC)
    # The real fsync fails on a descriptor that is not open; the stub accepts it.
    self.assertIsNone(os.fsync(-1))
    with tempfile.TemporaryDirectory() as tmp:
      target = Path(tmp) / "index.json"
      jsonio.write_bytes(target, b"{}\n")
      self.assertEqual(target.read_bytes(), b"{}\n")
    self.assertIsNot(jsonio._full_fsync_darwin, support.REAL_FULL_FSYNC)

  def test_Support_RealSyncs_RestoresFsyncAndFullFsync(self) -> None:
    stub_fsync, stub_full = os.fsync, jsonio._full_fsync_darwin
    self.assertIsNot(stub_fsync, support.REAL_FSYNC)
    self.assertIsNot(stub_full, support.REAL_FULL_FSYNC)
    with support.real_syncs():
      self.assertIs(os.fsync, support.REAL_FSYNC)
      self.assertIs(jsonio._full_fsync_darwin, support.REAL_FULL_FSYNC)
    self.assertIs(os.fsync, stub_fsync)
    self.assertIs(jsonio._full_fsync_darwin, stub_full)

  def test_Support_RealSyncs_RestoresTheStubsAfterAnError(self) -> None:
    stub_fsync, stub_full = os.fsync, jsonio._full_fsync_darwin
    self.assertIsNot(stub_fsync, support.REAL_FSYNC)
    with self.assertRaises(RuntimeError):
      with support.real_syncs():
        raise RuntimeError("boom")
    self.assertIs(os.fsync, stub_fsync)
    self.assertIs(jsonio._full_fsync_darwin, stub_full)
