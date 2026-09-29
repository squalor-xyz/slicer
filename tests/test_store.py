"""The staging transaction that lets a mutation render before it lands (S105).

`State.staged()` buffers every persistence side effect so a caller can render the
proposed state first and drop the whole batch, untouched, if the render fails.
"""

from __future__ import annotations

import unittest

import support

from slicer import ops, store


class StagingTransactionTests(unittest.TestCase):
  def _repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.run("add", "One")
    return repo

  def test_Staged_CleanExit_FlushesBufferedWrites(self) -> None:
    with self._repo() as repo:
      state = store.load(repo.root)
      with state.staged():
        ops.add_note(state, "S01", "hello")
      reloaded = store.load(repo.root).index.require("S01")
      self.assertTrue(any("hello" in note for note in reloaded.notes))

  def test_Staged_Exception_LeavesDiskUntouched(self) -> None:
    with self._repo() as repo:
      index_before = repo.read(".slicer/index.json")
      log_before = repo.read(".slicer/log.jsonl")
      state = store.load(repo.root)
      with self.assertRaises(RuntimeError):
        with state.staged():
          ops.add_note(state, "S01", "hello")
          raise RuntimeError("boom")
      self.assertEqual(repo.read(".slicer/index.json"), index_before)
      self.assertEqual(repo.read(".slicer/log.jsonl"), log_before)

  def test_Staged_CleanExit_AppliesBufferedSliceMove(self) -> None:
    with self._repo() as repo:
      repo.run("promote", "S01")
      state = store.load(repo.root)
      with state.staged():
        ops.set_status(state, "S01", state.config.done_status)
      self.assertTrue((repo.root / ".slicer/slices/done/S01.json").exists())
      self.assertFalse((repo.root / ".slicer/slices/S01.json").exists())

  def test_Staged_Exception_LeavesSliceFileInPlace(self) -> None:
    with self._repo() as repo:
      repo.run("promote", "S01")
      state = store.load(repo.root)
      with self.assertRaises(RuntimeError):
        with state.staged():
          ops.set_status(state, "S01", state.config.done_status)
          raise RuntimeError("boom")
      self.assertTrue((repo.root / ".slicer/slices/S01.json").exists())
      self.assertFalse((repo.root / ".slicer/slices/done/S01.json").exists())

  def test_BeginStage_Twice_IsRefused(self) -> None:
    with self._repo() as repo:
      state = store.load(repo.root)
      state.begin_stage()
      with self.assertRaises(store.StateError):
        state.begin_stage()
      state.discard_stage()
