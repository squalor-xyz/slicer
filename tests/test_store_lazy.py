"""Keep single-item commands independent of historical slice bodies."""

from __future__ import annotations

import json
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import support
from slicer import jsonio, ops, outline, render, store
from slicer.errors import SlicerError
from slicer.model import Item, Slice


@contextmanager
def slice_reads():
  reads = []
  original = jsonio.read

  def read(path):
    if "slices" in Path(path).parts:
      reads.append(Path(path))
    return original(path)

  with patch.object(jsonio, "read", read):
    yield reads


class LazyStoreTests(unittest.TestCase):
  def test_Load_ManyDoneSlices_NextReadsNone(self) -> None:
    with support.TempRepo() as repo:
      support.many_done_slices(repo)
      with slice_reads() as reads:
        code, out, err = repo.run("next", "--json", "--lean")
      self.assertEqual(code, 0, err)
      self.assertIn("S2001", out)
      self.assertEqual([p.name for p in reads if p.parent.name == "done"], [])

  def test_Load_ShowDoneSlice_ReadsOnlyThatFile(self) -> None:
    with support.TempRepo() as repo:
      support.many_done_slices(repo, 4)
      with slice_reads() as reads:
        code, out, err = repo.run("show", "S0002", "--json", "--lean")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out)["slice"]["title"], "Slice 2")
      self.assertEqual([p.name for p in reads], ["S0002.json"])

  def test_Load_Render_ByteIdenticalToEagerLoad(self) -> None:
    with support.TempRepo() as repo:
      repo.copy_legacy()
      repo.run("init")
      repo.run("migrate", "--from", "docs/slices")
      for root in (repo.root, Path(__file__).resolve().parents[1]):
        with self.subTest(root=root):
          eager = store.load(root)
          # Independent reference: the old loader's folder traversal and parsing.
          eager.slices = {}
          eager.slice_files = {}
          for folder in eager.folders():
            for path in sorted(folder.glob("*.json")):
              sl = store._from_dict(path, lambda d: Slice.from_dict(
                d, boundary_marker=eager.config.boundary), jsonio.read(path))
              eager.slices[sl.id] = sl
              eager.slice_files[sl.id] = path
          expected = render.plan(eager)
          with slice_reads() as reads:
            lazy = store.load(root)
          self.assertEqual(reads, [])
          actual = render.plan(lazy)
          self.assertEqual(actual.keys(), expected.keys())
          for path in expected:
            self.assertEqual(actual[path], expected[path], path)

  def test_Load_FileIdDisagreesWithName_VerifyStillReports(self) -> None:
    with support.TempRepo() as repo:
      support.many_done_slices(repo, 0, 1)
      repo.write(".slicer/slices/s5.json", json.dumps(Slice("s6", "Mismatch").to_dict()))
      with slice_reads() as reads:
        state = repo.state()
      self.assertEqual(reads, [])
      self.assertIsNone(state.slices.get("s5"))
      code, out, err = repo.run("verify", "--json")
      self.assertIn("slice file s5.json contains id 's6'; the name and the id disagree", out)

  def test_Load_CorruptDoneSlice_CheckStillFails(self) -> None:
    with support.TempRepo() as repo:
      support.many_done_slices(repo, 1)
      repo.write(".slicer/slices/done/S0001.json", "{")
      # Corruption belongs to commands that actually inspect the slice bodies.
      with slice_reads() as reads:
        repo.state()
      self.assertEqual(reads, [])
      for command in ("check", "verify"):
        with self.subTest(command=command):
          code, out, err = repo.run(command, "--json")
          self.assertEqual(json.loads(out)["error"]["code"], "corrupt")
          self.assertIn("S0001.json", out)

  def test_Load_CorruptDoneSlice_NextUnaffected(self) -> None:
    with support.TempRepo() as repo:
      support.many_done_slices(repo, 1)
      repo.write(".slicer/slices/done/S0001.json", "{")
      code, out, err = repo.run("next", "--json", "--lean")
      self.assertEqual(code, 0, err)
      self.assertIn("S0002", out)

  def test_LazySlices_SetThenIterate_SeesNewSlice(self) -> None:
    with support.TempRepo() as repo:
      support.many_done_slices(repo, 1, 1)
      with slice_reads() as reads:
        state = repo.state()
        with state.staged():
          state.save_slice(Slice("S0003", "New"))
      self.assertEqual(reads, [])
      with state.staged():
        ops.set_status(state, "S0002", state.config.done_status)
      self.assertEqual({sid: sl.title for sid, sl in state.slices.items()},
                       {"S0001": "Slice 1", "S0002": "Slice 2", "S0003": "New"})
      self.assertEqual(json.loads(repo.read(".slicer/slices/done/S0002.json"))["title"], "Slice 2")
      self.assertEqual({sid: sl.title for sid, sl in repo.state().slices.items()},
                       {"S0001": "Slice 1", "S0002": "Slice 2", "S0003": "New"})

  def test_LazySlices_BatchMoveWithCorruptSlice_NoHalfMove(self) -> None:
    with support.TempRepo() as repo:
      support.many_done_slices(repo, 1, 2)
      repo.write(".slicer/slices/S0003.json", "{")
      code, out, err = repo.run("set", "S0002", "S0003", "--status", "done")
      self.assertEqual(code, 3, err)
      state = repo.state()
      self.assertEqual(state.index.require("S0002").status, state.config.open_status)
      self.assertEqual(json.loads(repo.read(".slicer/slices/S0002.json"))["title"], "Slice 2")
      self.assertEqual(repo.read(".slicer/slices/S0003.json"), "{")
      # Repair only the corrupt fixture so verify can inspect folder consistency.
      repo.write(".slicer/slices/S0003.json", json.dumps(Slice("S0003", "Slice 3").to_dict()))
      code, out, err = repo.run("verify", "--json")
      self.assertNotIn("wrong folder", out)

  def test_LazySlices_DraftCopyFails_OriginalUnchanged(self) -> None:
    with support.TempRepo() as repo:
      support.many_done_slices(repo, 2, 1)
      state = repo.state()
      specs = outline.parse("## Draft slice\n\n### Why\nDraft work.\n").items
      with slice_reads() as reads:
        with patch.object(store.State, "save_index", side_effect=OSError("save failed")):
          with self.assertRaisesRegex(OSError, "save failed"):
            ops.apply_outline(state, specs, promote_all=True)
      self.assertEqual(reads, [])
      # A lookup miss does one full load before it reports not found.
      self.assertIsNone(state.slices.get("S04"))

  def test_LazySlices_Move_UpdatesPathsWithoutReading(self) -> None:
    for staged in (False, True):
      for cached in (False, True):
        with self.subTest(staged=staged, cached=cached), support.TempRepo() as repo:
          support.many_done_slices(repo, 0, 1)
          state = repo.state()
          if cached:
            state.slices["S0001"]
          draft = state.slices.copy()
          src = state.slice_files["S0001"] if cached else state.slice_path("S0001")
          dst = src.parent / "done" / src.name
          if staged:
            state.begin_stage()
          with slice_reads() as reads:
            state.move_slice(src, dst)
          self.assertEqual(reads, [])
          if staged:
            self.assertEqual(state.slice_files["S0001"], src)
            state.commit_stage()
          self.assertEqual(state.slice_files["S0001"], dst)
          self.assertEqual(draft._files["S0001"], src)
          self.assertEqual({sid: sl.title for sid, sl in state.slices.items()},
                           {"S0001": "Slice 1"})
          self.assertEqual(state.slice_files["S0001"], dst)

  def test_LazyBody_FileMovedAfterLoad_ReportsCorrupt(self) -> None:
    with support.TempRepo() as repo:
      support.many_done_slices(repo, 0, 1)
      state = repo.state()
      src = state.slice_path("S0001")
      (src.parent / "done").mkdir(exist_ok=True)
      src.rename(src.parent / "done" / src.name)
      with self.assertRaises(SlicerError) as caught:
        state.slices["S0001"]
      self.assertEqual(caught.exception.code, "corrupt")
      self.assertIn("S0001.json", str(caught.exception))

  def test_Lookup_IdOnlyInMisnamedFile_FoundAfterFullLoad(self) -> None:
    with support.TempRepo() as repo:
      support.many_done_slices(repo, 0, 1)
      state = repo.state()
      state.index.items.append(Item(id="s6", title="Mismatch", status=state.config.open_status,
                                    has_slice=True, importance=1, urgency=1, effort=1))
      state.save_index()
      repo.write(".slicer/slices/s5.json", json.dumps(Slice("s6", "Mismatch").to_dict()))
      state = repo.state()
      self.assertEqual(state.slices["s6"].title, "Mismatch")
      self.assertEqual(state.slices.get("s6").title, "Mismatch")
      self.assertIn("s6", state.slices)
      self.assertEqual(state.slice_files["s6"].name, "s5.json")

  def test_Lookup_UnknownId_StillNotFound(self) -> None:
    with support.TempRepo() as repo:
      support.many_done_slices(repo, 0, 1)
      state = repo.state()
      # s98 is a has_slice row with no file at all: the full load runs and still misses.
      state.index.items.append(Item(id="s98", title="Lost", status=state.config.open_status,
                                    has_slice=True, importance=1, urgency=1, effort=1))
      for sid in ("s98", "s99"):
        with self.subTest(sid=sid):
          self.assertIsNone(state.slices.get(sid))
          self.assertNotIn(sid, state.slices)
          with self.assertRaises(KeyError):
            state.slices[sid]
      self.assertEqual(list(state.slices), ["S0001"])

  def _row_only_beside_corrupt_done(self, repo) -> None:
    support.many_done_slices(repo, 3, 1)
    state = repo.state()
    state.index.items.append(Item(id="S0005", title="Row only", status=state.config.open_status,
                                  has_slice=False, importance=1, urgency=1, effort=1))
    state.index.next_id = 6
    state.save_index()
    repo.write(".slicer/slices/done/S0001.json", "{ not json")

  def test_Lookup_RowOnlyOrUnindexedId_ReadsNoBody(self) -> None:
    with support.TempRepo() as repo:
      self._row_only_beside_corrupt_done(repo)
      state = repo.state()
      with slice_reads() as reads:
        self.assertIsNone(state.slices.get("S0005"))
        self.assertNotIn("nope", state.slices)
      self.assertEqual(reads, [])

  def test_Next_RowOnlyItemBesideCorruptDoneBody_StillSelectsOpenWork(self) -> None:
    with support.TempRepo() as repo:
      self._row_only_beside_corrupt_done(repo)
      code, out, err = repo.run("next", "--json", "--lean")
      self.assertEqual(code, 0, err)
      self.assertIn("S0004", out)

  def test_Show_RowOnlyItemBesideCorruptDoneBody_Succeeds(self) -> None:
    with support.TempRepo() as repo:
      self._row_only_beside_corrupt_done(repo)
      code, out, err = repo.run("show", "S0005", "--json", "--lean")
      self.assertEqual(code, 0, err)
      self.assertIn("S0005", out)
