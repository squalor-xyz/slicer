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
from slicer import jsonio, vcs
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


class NotARepoMemoTests(unittest.TestCase):
  """The suite remembers a no answer from git for a fixture that is not a repo (s448).

  Production keeps only yes answers so a `git init` is seen at once; fixtures are
  throwaway directories, so the suite keeps the no answers and forgets them at
  `vcs.clear_cache` and at a fixture's own `git init`.
  """

  def test_Support_NotARepoFixture_IsRepoAskedOnce(self) -> None:
    probes = {
      "is_repo": lambda root: vcs.is_repo(root),
      "_current_worktree": lambda root: vcs._current_worktree(root),
      "git_common_dir": lambda root: vcs.git_common_dir(root),
    }
    for name, probe in probes.items():
      with self.subTest(probe=name), support.TempRepo() as repo, support.isolated_discovery(repo.root):
        with support.count_git_calls() as calls:
          first, second = probe(repo.root), probe(repo.root)
        self.assertFalse(first)
        self.assertFalse(second)
        self.assertEqual(len(calls), 1, calls)
        self.assertEqual(calls[0][0], "rev-parse")
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      with support.count_git_calls() as calls:
        self.assertFalse(vcs.is_repo(repo.root))
        self.assertFalse(vcs.is_repo(repo.root))
        self.assertEqual(calls, [("rev-parse", "--is-inside-work-tree")])
        vcs.clear_cache()  # the session's reload point forgets a no answer too
        self.assertFalse(vcs.is_repo(repo.root))
      self.assertEqual(len(calls), 2, calls)

  def test_Support_NotARepoMemo_ForgottenAfterGitInit(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      self.assertFalse(vcs.is_repo(repo.root))
      self.assertIsNone(vcs._current_worktree(repo.root))
      self.assertIsNone(vcs.git_common_dir(repo.root))
      repo._git("init", "-q")
      self.assertTrue(vcs.is_repo(repo.root))
      self.assertEqual(vcs._current_worktree(repo.root), repo.root)
      self.assertEqual(vcs.git_common_dir(repo.root), repo.root / ".git")

  def test_Support_CodeWarning_OffForTheSuite(self) -> None:
    # tests/test_code_location.py is the only module that turns the warning back on.
    self.assertEqual(os.environ.get("SLICER_NO_CODE_WARNING"), "1")
