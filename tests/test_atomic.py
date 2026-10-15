"""Atomicity across files and the advisory writer lock (S31).

`jsonio` makes each file write atomic; these tests cover the gaps between
files -- a half-applied outline and two writers racing -- that the lock and the
staged writes close.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

import support

from slicer import store


OUTLINE = (
  "## First thing\n\n### Why\nBecause one.\n\n"
  "## Second thing\n\n### Why\nBecause two.\n"
)


class AtomicTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    return repo

  def test_ApplyOutline_SliceWriteFailsPartway_LeavesNoSliceFiles(self) -> None:
    with self.repo() as repo:
      repo.write("roadmap.md", OUTLINE)
      original = store.State.save_slice
      calls = {"n": 0}

      def flaky(self, sl):
        calls["n"] += 1
        if calls["n"] == 2:
          raise OSError("disk full")
        return original(self, sl)

      store.State.save_slice = flaky
      try:
        code, _, _ = repo.run("import", "roadmap.md")
      finally:
        store.State.save_slice = original

      self.assertNotEqual(code, 0)
      # The first slice write happened, then the second failed: the first file
      # must have been unwound, and the index never saved.
      slices = repo.root / ".slicer" / "slices"
      self.assertEqual(list(slices.glob("*.json")), [])
      self.assertEqual(repo.state().index.items, [])

  def test_Lock_HeldByAnotherWriter_SecondWriterTimesOutCleanly(self) -> None:
    if store.fcntl is None:  # pragma: no cover - POSIX only
      self.skipTest("no flock on this platform")
    with self.repo() as repo:
      lock_path = repo.root / ".slicer" / store.LOCK_NAME
      fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o666)
      store.fcntl.flock(fd, store.fcntl.LOCK_EX)
      os.environ[store.LOCK_TIMEOUT_ENV] = "0.2"
      try:
        code, out, err = repo.run("add", "blocked", "--json")
      finally:
        store.fcntl.flock(fd, store.fcntl.LOCK_UN)
        os.close(fd)
        os.environ.pop(store.LOCK_TIMEOUT_ENV, None)
      self.assertEqual(code, 3)
      self.assertIn('"code": "locked"', out)
      # Nothing was added while the lock was held elsewhere.
      self.assertIsNone(repo.state().index.get("S01"))

  def test_Lock_FcntlUnavailable_MutationStillSucceeds(self) -> None:
    with self.repo() as repo:
      original = store.fcntl
      store.fcntl = None
      try:
        code, _, err = repo.run("add", "unlocked idea")
      finally:
        store.fcntl = original
      self.assertEqual(code, 0, err)
      self.assertIsNotNone(repo.state().index.get("S01"))

  def test_Purge_RemovesBothRowAndFile(self) -> None:
    # Behaviour is preserved after reordering index-save before the unlink.
    with self.repo() as repo:
      repo.write("roadmap.md", "## Only thing\n\n### Why\nBecause.\n")
      repo.run("import", "roadmap.md")
      self.assertIsNotNone(repo.state().find_slice_file("S01"))
      code, _, err = repo.run("remove", "S01", "--purge")
      self.assertEqual(code, 0, err)
      state = repo.state()
      self.assertIsNone(state.index.get("S01"))
      self.assertIsNone(state.find_slice_file("S01"))


class LockGitignoreTests(unittest.TestCase):
  def lines(self, repo: support.TempRepo) -> list[str]:
    return repo.read(".slicer/.gitignore").splitlines()

  def test_Init_GitignoresLockFile(self) -> None:
    with support.TempRepo() as repo:
      self.assertEqual(repo.run("init")[0], 0)
      self.assertIn(store.LOCK_NAME, self.lines(repo))

  def test_ProjectLock_LockFileAbsent_FirstWriteAddsGitignoreLine(self) -> None:
    with support.TempRepo() as repo:
      self.assertEqual(repo.run("init")[0], 0)
      # A project made before `init` listed the lock: no line, and no lock file yet.
      repo.write(".slicer/.gitignore", "feedback.md\n")
      lock_path = repo.root / ".slicer" / store.LOCK_NAME
      self.assertFalse(lock_path.exists())
      self.assertEqual(repo.run("add", "first")[0], 0)
      self.assertTrue(lock_path.is_file())
      self.assertEqual(self.lines(repo), ["feedback.md", "lock"])
      self.assertEqual(repo.run("add", "second")[0], 0)
      self.assertEqual(repo.run("add", "third")[0], 0)
      lock_path.unlink()  # the lock file is scratch; the next write recreates it
      self.assertEqual(repo.run("add", "fourth")[0], 0)
      self.assertEqual(self.lines(repo), ["feedback.md", "lock"])

  def test_ProjectLock_LockFileExists_FirstWriteAddsGitignoreLineOnce(self) -> None:
    with support.TempRepo() as repo:
      self.assertEqual(repo.run("init")[0], 0)
      # A project whose lock file predates this change: the file exists, and no line lists it.
      repo.write(".slicer/.gitignore", "feedback.md\n")
      lock_path = repo.root / ".slicer" / store.LOCK_NAME
      lock_path.write_bytes(b"")
      self.assertEqual(repo.run("add", "first")[0], 0)
      self.assertEqual(self.lines(repo), ["feedback.md", "lock"])
      self.assertEqual(repo.run("add", "second")[0], 0)
      self.assertEqual(self.lines(repo), ["feedback.md", "lock"])

  def test_ProjectLock_FreshProject_GitStatusClean(self) -> None:
    with support.TempRepo(git=True) as repo:
      self.assertEqual(repo.run("init")[0], 0)
      repo.commit("init")
      self.assertEqual(repo.run("add", "first")[0], 0)
      self.assertEqual(repo.run("render")[0], 0)
      self.assertTrue((repo.root / ".slicer" / store.LOCK_NAME).is_file())
      # Checked before any commit: `commit` stages with `git add -A`, which would
      # track the lock file and hide it from status.
      status = repo._git("status", "--porcelain")
      self.assertEqual(status.returncode, 0, status.stderr)
      self.assertNotIn(".slicer/lock", status.stdout)

  def test_ProjectLock_GitignoreUnreadable_WriteStillSucceeds(self) -> None:
    def not_utf8(path: Path) -> None:
      path.write_bytes(b"\xff\xfe feedback.md\n")

    def is_directory(path: Path) -> None:
      path.unlink()
      path.mkdir()

    for name, break_gitignore in (("not UTF-8", not_utf8), ("a directory", is_directory)):
      with self.subTest(case=name), support.TempRepo() as repo:
        self.assertEqual(repo.run("init")[0], 0)
        break_gitignore(repo.root / ".slicer" / ".gitignore")
        code, _, err = repo.run("add", "first")
        self.assertEqual(code, 0, err)
        self.assertIsNotNone(repo.state().index.get("S01"))


if __name__ == "__main__":
  unittest.main()
