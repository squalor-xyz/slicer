"""Prove temporary Git writers cannot turn successful tests into teardown failures."""

from __future__ import annotations

import errno
import shutil
import unittest
from unittest.mock import patch

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
