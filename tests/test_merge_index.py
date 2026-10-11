"""The `slicer-index` merge driver resolves a conflict on `next_id` alone to the
larger counter (S191); S217 widened it to independent items and fields."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import support
from slicer import jsonio, store, vcs
from slicer.errors import StateError

SRC = Path(__file__).resolve().parents[1] / "src"


def _index(next_id: int, **status: str) -> str:
  """An index-shaped document; `status` maps an item id to its status."""
  items = [
    {"id": f"s{n:02d}", "title": f"item {n}", "short_title": f"item {n}",
     "status": status.get(f"s{n:02d}", "open"), "has_slice": False, "depends_on": []}
    for n in range(1, 4)
  ]
  return jsonio.dumps({"version": 3, "id_prefix": "s", "next_id": next_id, "items": items})


def _notes_index(notes: dict[str, list[tuple[str, str]]], **status: str) -> str:
  """Like `_index`, with note records: `notes` maps an item id to (note id, created_at)."""
  doc = json.loads(_index(10, **status))
  for item in doc["items"]:
    item["note_records"] = [
      {"id": note_id, "kind": "", "text": f"text of {note_id}", "created_at": when, "attempt": None}
      for note_id, when in notes.get(item["id"], [])
    ]
  return jsonio.dumps(doc)


def _note_ids(text: str) -> dict[str, list[str]]:
  return {
    item["id"]: [n["id"] for n in item["note_records"]]
    for item in json.loads(text)["items"]
  }


class MergeIndexCommandTests(unittest.TestCase):
  def _merge(self, repo: support.TempRepo, base: str, ours: str, theirs: str):
    for name, text in (("base", base), ("ours", ours), ("theirs", theirs)):
      repo.write(name, text)
    code, out, err = repo.run("merge-index", *(str(repo.root / n) for n in ("base", "ours", "theirs")))
    return code, out, err, repo.read("ours")

  def test_MergeIndex_OnlyNextIdDiffers_KeepsTheLargerCounter(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      code, out, err, merged = self._merge(repo, _index(10), _index(12), _index(11))
      self.assertEqual((code, out, err), (0, "", ""))
      self.assertEqual(merged, _index(12))

  def test_MergeIndex_TheirsHoldsTheLargerCounter_OursTakesIt(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      code, _, _, merged = self._merge(repo, _index(10), _index(11), _index(14))
      self.assertEqual(code, 0)
      self.assertEqual(merged, _index(14))

  def test_MergeIndex_DisjointEditsToo_KeepsBothChangesAndTheLargerCounter(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      code, _, _, merged = self._merge(
        repo, _index(10), _index(12, s01="started"), _index(11, s03="done")
      )
      self.assertEqual(code, 0)
      self.assertEqual(merged, _index(12, s01="started", s03="done"))

  def test_MergeIndex_OverlappingEdits_ExitsNonzeroWithConflictMarkers(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      code, _, _, merged = self._merge(
        repo, _index(10), _index(12, s02="started"), _index(11, s02="done")
      )
      self.assertEqual(code, 1)
      self.assertIn("<<<<<<<", merged)
      self.assertIn(">>>>>>>", merged)
      # The counter was settled either way, so it is not part of the conflict.
      self.assertEqual(merged.count("next_id"), 1)
      self.assertIn('"next_id": 12', merged)

  def test_MergeIndex_BothSidesAppendNotesToSameItem_KeepsBoth(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      base = _notes_index({"s01": [("n1", "2026-01-01")]})
      ours = _notes_index({"s01": [("n1", "2026-01-01"), ("n3", "2026-01-03")]})
      theirs = _notes_index({"s01": [("n1", "2026-01-01"), ("n2", "2026-01-02")]})
      code, _, err, merged = self._merge(repo, base, ours, theirs)
      self.assertEqual((code, err), (0, ""))
      self.assertEqual(_note_ids(merged)["s01"], ["n1", "n2", "n3"])

  def test_MergeIndex_SameTimestampNotes_KeepUnionOrder(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      base = _notes_index({"s01": [("nz", "2026-01-01"), ("na", "2026-01-01")]})
      ours = _notes_index({"s01": [("nz", "2026-01-01"), ("na", "2026-01-01"), ("no", "2026-01-01")]})
      theirs = _notes_index({"s01": [("nz", "2026-01-01"), ("na", "2026-01-01"), ("nt", "2026-01-01")]})
      code, _, err, merged = self._merge(repo, base, ours, theirs)
      self.assertEqual((code, err), (0, ""))
      # Ours' notes first, then the notes only theirs has; the ids are not re-sorted.
      self.assertEqual(_note_ids(merged)["s01"], ["nz", "na", "no", "nt"])

  def test_MergeIndex_NotesAppendedToDifferentItems_KeepsAll(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      base = _notes_index({})
      ours = _notes_index({"s01": [("n1", "2026-01-01")]})
      theirs = _notes_index({"s02": [("n2", "2026-01-02")]})
      code, _, err, merged = self._merge(repo, base, ours, theirs)
      self.assertEqual((code, err), (0, ""))
      self.assertEqual(
        _note_ids(merged), {"s01": ["n1"], "s02": ["n2"], "s03": []}
      )

  def test_MergeIndex_SameNoteIdIdenticalOnBothSides_KeptOnce(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      base = _notes_index({})
      ours = _notes_index({"s01": [("n1", "2026-01-01")]})
      theirs = _notes_index({"s01": [("n1", "2026-01-01"), ("n2", "2026-01-02")]})
      code, _, err, merged = self._merge(repo, base, ours, theirs)
      self.assertEqual((code, err), (0, ""))
      self.assertEqual(_note_ids(merged)["s01"], ["n1", "n2"])

  def test_MergeIndex_FieldConflictWithNoteAppends_FailsNamingOnlyTheField(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      base = _notes_index({})
      ours = _notes_index({"s01": [("n1", "2026-01-01")]}, s02="started")
      theirs = _notes_index({"s01": [("n2", "2026-01-02")]}, s02="done")
      code, _, err, merged = self._merge(repo, base, ours, theirs)
      self.assertEqual(code, 1)
      self.assertIn("conflict at items/s02/status", err)
      self.assertNotIn("note_records", err)
      self.assertIn("<<<<<<<", merged)

  def test_MergeIndex_NoteDeletedOneSideUnchangedOnTheOther_StaysDeleted(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      base = _notes_index({"s01": [("n1", "2026-01-01"), ("n2", "2026-01-02")]})
      ours = _notes_index({"s01": [("n2", "2026-01-02")]})
      theirs = _notes_index({"s01": [("n1", "2026-01-01"), ("n2", "2026-01-02"), ("n3", "2026-01-03")]})
      code, _, err, merged = self._merge(repo, base, ours, theirs)
      self.assertEqual((code, err), (0, ""))
      self.assertEqual(_note_ids(merged)["s01"], ["n2", "n3"])

  def test_MergeIndex_NoCounterLine_IsMergedAsItIs(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      doc = {"items": [{"id": "s01", "title": "one", "status": "open"}]}
      code, _, _, merged = self._merge(
        repo, jsonio.dumps(doc), jsonio.dumps(doc), jsonio.dumps({"items": []})
      )
      self.assertEqual(code, 0)
      self.assertEqual(json.loads(merged), {"items": []})

  def test_MergeIndex_NeedsNoProject(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      with patch("slicer.cli.store.discover", side_effect=AssertionError("discover")), \
           patch("slicer.cli.store.load", side_effect=AssertionError("load")), \
           patch("slicer.cli.store.project_lock", side_effect=AssertionError("lock")):
        for name, text in (("base", _index(1)), ("ours", _index(2)), ("theirs", _index(3))):
          repo.write(name, text)
        code, _, err = repo.run(
          "merge-index", *(str(repo.root / n) for n in ("base", "ours", "theirs")),
          allow_parent_project=True,
        )
      self.assertEqual((code, err), (0, ""))
      self.assertEqual(repo.read("ours"), _index(3))

  def test_Vcs_MergeFile_AllowsOnlyTheStdoutForm(self) -> None:
    # Without -p, merge-file rewrites its first file argument in place.
    with support.TempRepo() as repo:
      with self.assertRaises(StateError):
        vcs._run(repo.root, "merge-file", "a", "b", "c")
      with self.assertRaises(StateError):
        vcs._run(repo.root, "merge-file")


class IndexMergeDriverEndToEndTests(unittest.TestCase):
  """Two branches that filed items at different places in the queue, with
  counters that moved differently, merge with no conflict."""

  def _configure(self, repo: support.TempRepo, *, index_driver: bool = True) -> None:
    repo._git("config", "merge.slicer-generated.driver", "true")
    if index_driver:
      repo._git("config", "merge.slicer-index.driver",
                f"{sys.executable} -m slicer merge-index %O %A %B")

  def _diverge(self, repo: support.TempRepo) -> tuple[str, int, int]:
    """Commit a base of three items, then file one item high up the queue on a
    branch (id S10, counter 11) and one at the end on trunk (counter 5)."""
    repo.run("init")
    for title in ("first", "second", "third"):
      repo.run("add", title, "--render")
    repo.commit("base")  # commits .gitattributes so both drivers apply on merge
    trunk = repo._git("branch", "--show-current").stdout.strip()
    repo._git("checkout", "-q", "-b", "feature")
    repo.run("add", "from feature", "--id", "S10", "--render")
    repo.run("move", "S10", "--after", "S01", "--render")
    repo.commit("feature")
    repo._git("checkout", "-q", trunk)
    repo.run("add", "from trunk", "--render")
    repo.commit("trunk")
    return (
      trunk,
      json.loads(repo._git("show", "feature:.slicer/index.json").stdout)["next_id"],
      json.loads(repo._git("show", f"{trunk}:.slicer/index.json").stdout)["next_id"],
    )

  def test_Merge_BothBranchesMovedNextId_CompletesAndCheckPasses(self) -> None:
    with support.TempRepo(git=True) as repo, \
         patch.dict(os.environ, {"PYTHONPATH": str(SRC), "SLICER_NO_CODE_WARNING": "1"}):
      self._configure(repo)
      _, feature_next, trunk_next = self._diverge(repo)
      self.assertNotEqual(feature_next, trunk_next)

      merged = repo._git("merge", "--no-edit", "feature")
      self.assertEqual(merged.returncode, 0, merged.stdout + merged.stderr)
      self.assertNotIn("<<<<<<<", repo.read(".slicer/index.json"))
      state = store.load(repo.root)
      self.assertEqual(state.index.next_id, max(feature_next, trunk_next))
      self.assertEqual(
        [item.id for item in state.index.items], ["S01", "S10", "S02", "S03", "S04"]
      )
      self.assertEqual(repo.run("render")[0], 0)
      code, out, err = repo.run("check")
      self.assertEqual(code, 0, out + err)

  def test_Merge_DriverNotConfigured_FallsBackToAConflict(self) -> None:
    with support.TempRepo(git=True) as repo:
      self._configure(repo, index_driver=False)
      self._diverge(repo)
      self.assertNotEqual(repo._git("merge", "--no-edit", "feature").returncode, 0)


if __name__ == "__main__":
  unittest.main()
