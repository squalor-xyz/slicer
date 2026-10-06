"""The `slicer-index` merge driver merges independent items and fields of
`index.json` by identity, and leaves a conflict wherever a list, a queue order or a
value was changed two ways (S217)."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import support
from slicer import jsonio

SRC = Path(__file__).resolve().parents[1] / "src"
BASE = ("first", "second", "third")


def _items(repo: support.TempRepo) -> dict[str, dict]:
  return {i["id"]: i for i in json.loads(repo.read(".slicer/index.json"))["items"]}


def _order(repo: support.TempRepo) -> list[str]:
  return [i["id"] for i in json.loads(repo.read(".slicer/index.json"))["items"]]


class RealGitMergeTests(unittest.TestCase):
  @contextlib.contextmanager
  def _branches(self, ours=(), theirs=(), *, driver: bool = True, sliced=()):
    """Three items on trunk, then `theirs` commands on a `feature` branch and `ours`
    commands on trunk, merged with `feature` the way a person would."""
    with support.TempRepo(git=True) as repo, \
         patch.dict(os.environ, {"PYTHONPATH": str(SRC), "SLICER_NO_CODE_WARNING": "1"}):
      repo._git("config", "merge.slicer-generated.driver", "true")
      if driver:
        repo._git("config", "merge.slicer-index.driver",
                  f"{sys.executable} -m slicer merge-index %O %A %B")
      repo.run("init")
      for title in BASE:
        repo.run("add", title, "--render")
      for item_id in sliced:
        outline = repo.write("outline.md", "## slice\n\nlead\n\n### Why\n\nbecause\n")
        self.assertEqual(repo.run("promote", item_id, "--file", str(outline), "--render")[0], 0)
      repo.commit("base")
      trunk = repo._git("branch", "--show-current").stdout.strip()
      repo._git("checkout", "-q", "-b", "feature")
      self._apply(repo, theirs, "theirs")
      repo._git("checkout", "-q", trunk)
      self._apply(repo, ours, "ours")
      yield repo, repo._git("merge", "--no-edit", "feature")

  def _apply(self, repo: support.TempRepo, commands, message: str) -> None:
    for command in commands:
      code, out, err = repo.run(*command, "--render")
      self.assertEqual(code, 0, f"{command}: {out}{err}")
    repo.commit(message)

  def _clean(self, repo: support.TempRepo, merged) -> None:
    self.assertEqual(merged.returncode, 0, merged.stdout + merged.stderr)
    self.assertNotIn("<<<<<<<", repo.read(".slicer/index.json"))
    self.assertEqual(repo.run("render")[0], 0)
    code, out, err = repo.run("check")
    self.assertEqual(code, 0, out + err)

  def _conflicted(self, repo: support.TempRepo, merged) -> str:
    self.assertNotEqual(merged.returncode, 0, merged.stdout + merged.stderr)
    text = repo.read(".slicer/index.json")
    self.assertIn("<<<<<<< ours", text)
    self.assertIn(">>>>>>> theirs", text)
    return text

  # --- fields of existing items -------------------------------------------------

  def test_Merge_AdjacentFieldsOfOneItem_BothKeptWhereTextMergeConflicts(self) -> None:
    changes = dict(ours=[("set", "S01", "--importance", "3")], theirs=[("set", "S01", "--urgency", "1")])
    with self._branches(driver=False, **changes) as (repo, merged):
      self.assertNotEqual(merged.returncode, 0, "premise: Git's own merge overlaps")
    with self._branches(**changes) as (repo, merged):
      self._clean(repo, merged)
      fields = _items(repo)["S01"]["fields"]
      self.assertEqual((fields["importance"], fields["urgency"]), (3, 1))

  def test_Merge_DifferentItems_BothKept(self) -> None:
    with self._branches(
      ours=[("set", "S01", "--importance", "3")], theirs=[("set", "S02", "--effort", "1")]
    ) as (repo, merged):
      self._clean(repo, merged)
      items = _items(repo)
      self.assertEqual(items["S01"]["fields"]["importance"], 3)
      self.assertEqual(items["S02"]["fields"]["effort"], 1)

  def test_Merge_SameChangeOnBothSides_Coalesces(self) -> None:
    same = [("set", "S01", "--importance", "3")]
    with self._branches(ours=same, theirs=same) as (repo, merged):
      self._clean(repo, merged)
      self.assertEqual(_items(repo)["S01"]["fields"]["importance"], 3)

  def test_Merge_OneScalarChangedTwoWays_ConflictsThereAndKeepsTheRest(self) -> None:
    with self._branches(
      ours=[("set", "S01", "--importance", "3", "--urgency", "1")],
      theirs=[("set", "S01", "--importance", "1")],
    ) as (repo, merged):
      text = self._conflicted(repo, merged)
      self.assertIn("conflict at items/S01/fields/importance", merged.stdout + merged.stderr)
      block = text.split("<<<<<<< ours")[1].split(">>>>>>> theirs")[0]
      self.assertIn('"importance": 3', block)
      self.assertIn('"importance": 1', block)
      self.assertNotIn("urgency", block)
      self.assertIn('"urgency": 1', text)

  # --- lists --------------------------------------------------------------------

  def test_Merge_ListChangedDifferently_Conflicts(self) -> None:
    with self._branches(
      ours=[("set", "S01", "--depends-on", "S02")], theirs=[("set", "S01", "--depends-on", "S03")]
    ) as (repo, merged):
      text = self._conflicted(repo, merged)
      self.assertIn("conflict at items/S01/depends_on", merged.stdout + merged.stderr)
      self.assertIn('"S02"', text)
      self.assertIn('"S03"', text)

  def test_Merge_BothAppendToOneNoteList_Conflicts(self) -> None:
    with self._branches(
      ours=[("note", "S01", "--text", "ours says")], theirs=[("note", "S01", "--text", "theirs says")]
    ) as (repo, merged):
      self._conflicted(repo, merged)
      self.assertIn("conflict at items/S01/note_records", merged.stdout + merged.stderr)

  def test_Merge_ListChangedOnOneSideOnly_TakesTheChange(self) -> None:
    with self._branches(
      ours=[("set", "S01", "--importance", "3")], theirs=[("set", "S01", "--depends-on", "S03")]
    ) as (repo, merged):
      self._clean(repo, merged)
      self.assertEqual(_items(repo)["S01"]["depends_on"], ["S03"])

  # --- additions ----------------------------------------------------------------

  def test_Merge_DistinctNewIds_BothSurviveOursFirst(self) -> None:
    with self._branches(
      ours=[("add", "ours new")], theirs=[("add", "theirs new", "--id", "S10")]
    ) as (repo, merged):
      self._clean(repo, merged)
      self.assertEqual(_order(repo), ["S01", "S02", "S03", "S04", "S10"])

  def test_Merge_NewIdsInTheSameGap_OursInOrderThenTheirsInOrder(self) -> None:
    with self._branches(
      ours=[("add", "o1", "--id", "S20"), ("add", "o2", "--id", "S21"),
            ("move", "S20", "--after", "S01"), ("move", "S21", "--after", "S20")],
      theirs=[("add", "t1", "--id", "S30"), ("add", "t2", "--id", "S31"),
              ("move", "S30", "--after", "S01"), ("move", "S31", "--after", "S30")],
    ) as (repo, merged):
      self._clean(repo, merged)
      self.assertEqual(_order(repo), ["S01", "S20", "S21", "S30", "S31", "S02", "S03"])

  def test_Merge_SameNewIdWithDifferentContent_Collides(self) -> None:
    with self._branches(
      ours=[("add", "same title")],
      theirs=[("add", "same title"), ("set", "S04", "--importance", "3")],
    ) as (repo, merged):
      self._conflicted(repo, merged)

  # --- deletion -----------------------------------------------------------------

  def test_Merge_DeletedOnOneSideUnchangedOnTheOther_IsRemoved(self) -> None:
    with self._branches(
      ours=[("set", "S01", "--importance", "3")], theirs=[("remove", "S02", "--purge")]
    ) as (repo, merged):
      self._clean(repo, merged)
      self.assertEqual(_order(repo), ["S01", "S03"])

  def test_Merge_DeletedOnOneSideEditedOnTheOther_Conflicts(self) -> None:
    with self._branches(
      ours=[("set", "S02", "--importance", "3")], theirs=[("remove", "S02", "--purge")]
    ) as (repo, merged):
      text = self._conflicted(repo, merged)
      self.assertIn('"id": "S02"', text)

  # --- order --------------------------------------------------------------------

  def test_Merge_ReorderOnOneSideOnly_IsKept(self) -> None:
    with self._branches(
      ours=[("set", "S03", "--importance", "3")], theirs=[("move", "S03", "--before", "S01")]
    ) as (repo, merged):
      self._clean(repo, merged)
      self.assertEqual(_order(repo), ["S03", "S01", "S02"])

  def test_Merge_IdenticalReorders_Coalesce(self) -> None:
    move = [("move", "S03", "--before", "S01")]
    with self._branches(ours=move, theirs=move) as (repo, merged):
      self._clean(repo, merged)
      self.assertEqual(_order(repo), ["S03", "S01", "S02"])

  def test_Merge_IncompatibleReorders_Conflict(self) -> None:
    with self._branches(
      ours=[("move", "S01", "--after", "S03")], theirs=[("move", "S03", "--before", "S01")]
    ) as (repo, merged):
      self._conflicted(repo, merged)

  def test_Merge_IndependentSwapsOfDifferentIds_StillConflictRatherThanGuess(self) -> None:
    with self._branches(
      ours=[("move", "S01", "--after", "S02")], theirs=[("move", "S03", "--before", "S02")]
    ) as (repo, merged):
      self._conflicted(repo, merged)
      self.assertIn("conflict at items/order", merged.stdout + merged.stderr)

  # --- filing keys --------------------------------------------------------------

  def test_Merge_TwoIdsOneFilingKey_ConflictsNamingBoth(self) -> None:
    with self._branches(
      ours=[("add", "ours", "--key", "k1")],
      theirs=[("add", "theirs", "--id", "S10", "--key", "k1")],
    ) as (repo, merged):
      text = self._conflicted(repo, merged)
      said = merged.stdout + merged.stderr
      self.assertIn("filing key 'k1' is on S04 and S10", said)
      self.assertIn("(filing key 'k1' is on S04 and S10)", text)
      self.assertIn('"id": "S04"', text)
      self.assertIn('"id": "S10"', text)

  def test_Merge_DifferentFilingKeys_Merge(self) -> None:
    with self._branches(
      ours=[("add", "ours", "--key", "k1")],
      theirs=[("add", "theirs", "--id", "S10", "--key", "k2")],
    ) as (repo, merged):
      self._clean(repo, merged)

  def test_Merge_IdsDifferingOnlyInCase_Conflict(self) -> None:
    with self._branches(
      ours=[("add", "ours", "--id", "S10")], theirs=[("add", "theirs", "--id", "s10")]
    ) as (repo, merged):
      self._conflicted(repo, merged)
      self.assertIn("ids S10 and s10 differ only in case", merged.stdout + merged.stderr)

  def test_Merge_DependencyOnAnItemTheOtherSideRemoved_Conflicts(self) -> None:
    with self._branches(
      ours=[("set", "S01", "--depends-on", "S02")], theirs=[("remove", "S02", "--purge")]
    ) as (repo, merged):
      self._conflicted(repo, merged)
      self.assertIn("S01 depends on unknown id S02", merged.stdout + merged.stderr)

  def test_Merge_DependencyCycleAcrossSides_Conflicts(self) -> None:
    with self._branches(
      ours=[("set", "S01", "--depends-on", "S02")], theirs=[("set", "S02", "--depends-on", "S01")]
    ) as (repo, merged):
      self._conflicted(repo, merged)
      self.assertIn("dependency cycle", merged.stdout + merged.stderr)

  # --- next_id and status folders -----------------------------------------------

  def test_Merge_NextIdStaysTheLargest(self) -> None:
    with self._branches(
      ours=[("add", "a"), ("add", "b")], theirs=[("add", "c", "--id", "S10")]
    ) as (repo, merged):
      self._clean(repo, merged)
      self.assertEqual(json.loads(repo.read(".slicer/index.json"))["next_id"], 11)

  def test_Merge_StatusFolderMoveBesideAnIndependentNote_RendersAndChecks(self) -> None:
    with self._branches(
      sliced=("S01", "S02"),
      ours=[("start", "S01"), ("done", "S01", "--note", "finished")],
      theirs=[("note", "S02", "--text", "a note"), ("set", "S03", "--importance", "3")],
    ) as (repo, merged):
      self._clean(repo, merged)
      items = _items(repo)
      self.assertEqual(items["S01"]["status"], "done")
      self.assertEqual(len(items["S02"]["note_records"]), 1)
      self.assertTrue((repo.root / ".slicer" / "slices" / "done" / "S01.json").exists())
      self.assertFalse((repo.root / ".slicer" / "slices" / "S01.json").exists())


class UnreadableInputTests(unittest.TestCase):
  def _merge(self, base: str, ours: str, theirs: str):
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      for name, text in (("base", base), ("ours", ours), ("theirs", theirs)):
        repo.write(name, text)
      code, _, err = repo.run("merge-index", *(str(repo.root / n) for n in ("base", "ours", "theirs")))
      return code, err, repo.read("ours")

  def _doc(self, *titles: str) -> str:
    items = [{"id": f"s{n:02d}", "title": t, "status": "open"} for n, t in enumerate(titles, 1)]
    return jsonio.dumps({"version": 3, "next_id": 9, "items": items})

  def test_Merge_MalformedJson_LeavesAConflictEvenWhenLinesMergeCleanly(self) -> None:
    base = '{\n  "next_id": 1,\n  "a": 1,\n\n\n  "b": 1,\n'
    ours = base.replace('"a": 1', '"a": 2')
    theirs = base.replace('"b": 1', '"b": 2')
    code, err, merged = self._merge(base, ours, theirs)
    self.assertEqual(code, 1)
    self.assertIn("<<<<<<<", merged)
    self.assertIn("not a readable index", err)

  def test_Merge_JsonThatIsNotAnIndex_LeavesAConflict(self) -> None:
    code, _, merged = self._merge("[]\n", "[1]\n", "[]\n")
    self.assertEqual(code, 1)
    self.assertIn("<<<<<<< ours", merged)

  def test_Merge_FutureSchema_LeavesAConflict(self) -> None:
    future = json.dumps({"version": 999, "items": []}, indent=2) + "\n"
    code, _, merged = self._merge(future, future, future)
    self.assertEqual(code, 1)
    self.assertIn("<<<<<<< ours", merged)

  def test_Merge_ItemWithoutATitle_LeavesAConflict(self) -> None:
    broken = jsonio.dumps({"items": [{"id": "s01", "status": "open"}]})
    code, _, _ = self._merge(broken, broken, broken)
    self.assertEqual(code, 1)

  def test_Merge_ValidDocWithoutACounter_MergesByIdentity(self) -> None:
    doc = {"items": [{"id": "s01", "title": "one", "status": "open"}]}
    code, err, merged = self._merge(
      jsonio.dumps(doc), jsonio.dumps(doc), jsonio.dumps({"items": []})
    )
    self.assertEqual((code, err), (0, ""))
    self.assertEqual(json.loads(merged), {"items": []})

  def test_Merge_CleanResult_IsCanonicalAndStable(self) -> None:
    code, _, merged = self._merge(self._doc("a", "b"), self._doc("a", "b", "c"), self._doc("a", "b"))
    self.assertEqual(code, 0)
    self.assertEqual(merged, self._doc("a", "b", "c"))


if __name__ == "__main__":
  unittest.main()
