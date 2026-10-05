"""Sibling worktrees: new ids clear their counters, and `check` flags a clash."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import support

from slicer import ids, store
from slicer.cli import main

OUTLINE = """\
# Roadmap

## First outline item

## Second outline item
"""


def _run(root: Path, *args: str) -> tuple[int, str, str]:
  out, err = io.StringIO(), io.StringIO()
  with redirect_stdout(out), redirect_stderr(err):
    code = main(["--root", str(root), *args])
  return code, out.getvalue(), err.getvalue()


def _index(prefix: str, next_id: int) -> SimpleNamespace:
  return SimpleNamespace(id_prefix=prefix, next_id=next_id)


class FloorFromTests(unittest.TestCase):
  def test_FloorFrom_NoSiblings_IsZero(self) -> None:
    self.assertEqual(ids.floor_from([], "S"), 0)

  def test_FloorFrom_SiblingsOfThisScheme_IsLargestNextId(self) -> None:
    siblings = [_index("S", 4), _index("S", 9), _index("S", 6)]
    self.assertEqual(ids.floor_from(siblings, "S"), 9)

  def test_FloorFrom_PrefixCompared_IgnoresCaseAndOtherSchemes(self) -> None:
    siblings = [_index("s", 7), _index("TASK-", 40)]
    self.assertEqual(ids.floor_from(siblings, "S"), 7)
    self.assertEqual(ids.floor_from([_index("TASK-", 40)], "S"), 0)


class AllocateFloorTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.run("add", "one")
    return repo

  def test_Allocate_FloorAboveNextId_UsesFloorAndAdvancesPast(self) -> None:
    with self.repo() as repo:
      index = repo.state().index
      self.assertEqual(ids.allocate(index, floor=5), "S05")
      self.assertEqual(index.next_id, 6)

  def test_Allocate_FloorBelowNextId_IsIgnored(self) -> None:
    with self.repo() as repo:
      index = repo.state().index
      self.assertEqual(ids.allocate(index, floor=1), "S02")
      self.assertEqual(index.next_id, 3)

  def test_FormatNext_Floor_DoesNotConsume(self) -> None:
    with self.repo() as repo:
      index = repo.state().index
      self.assertEqual(ids.format_next(index, floor=5), "S05")
      self.assertEqual(index.next_id, 2)


class SiblingIdTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo(git=True)
    repo.run("init")
    repo.run("add", "one")
    repo.commit("base")
    return repo

  def sibling(self, repo: support.TempRepo, name: str) -> Path:
    root = repo.root / "siblings" / name
    root.parent.mkdir(exist_ok=True)
    made = repo._git("worktree", "add", "-q", "-b", name, str(root), "HEAD")
    self.assertEqual(made.returncode, 0, made.stderr)
    return root

  def test_SiblingIds_Siblings_ReturnNameAndIndex(self) -> None:
    with self.repo() as repo:
      self.sibling(repo, "other")
      found = store.sibling_ids(repo.root)
      self.assertEqual([name for name, _ in found], ["other"])
      self.assertEqual([i.id for i in found[0][1].items], ["S01"])

  def test_SiblingIds_UnreadableSibling_IsSkipped(self) -> None:
    with self.repo() as repo:
      sibling = self.sibling(repo, "broken")
      (sibling / ".slicer" / "index.json").write_text("{not json", encoding="utf-8")
      self.assertEqual(store.sibling_ids(repo.root), [])

  def test_Add_SiblingAheadOfThisCheckout_TakesAnIdAboveIt(self) -> None:
    with self.repo() as repo:
      sibling = self.sibling(repo, "other")
      _run(sibling, "add", "sibling item")
      _run(sibling, "add", "another sibling item")
      code, out, err = repo.run("add", "here", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out)["id"], "S04")
      self.assertEqual(repo.state().index.next_id, 5)

  def test_Add_ExplicitId_IgnoresTheFloor(self) -> None:
    with self.repo() as repo:
      sibling = self.sibling(repo, "other")
      _run(sibling, "add", "sibling item")
      code, out, err = repo.run("add", "here", "--id", "S02", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out)["id"], "S02")

  def test_NextId_SiblingAhead_ReportsTheFlooredId(self) -> None:
    with self.repo() as repo:
      sibling = self.sibling(repo, "other")
      _run(sibling, "add", "sibling item")
      code, out, _ = repo.run("next-id", "--json")
      self.assertEqual(code, 0)
      self.assertEqual(json.loads(out), {"id": "S03"})

  def test_Import_SiblingAhead_DryRunAndRealRunAgreeOnIds(self) -> None:
    with self.repo() as repo:
      sibling = self.sibling(repo, "other")
      _run(sibling, "add", "sibling item")
      repo.write("roadmap.md", OUTLINE)
      code, out, err = repo.run("import", "roadmap.md", "--dry-run", "--json")
      self.assertEqual(code, 0, err)
      planned = json.loads(out)["ids"]
      self.assertEqual(planned, ["S03", "S04"])
      code, _, err = repo.run("import", "roadmap.md")
      self.assertEqual(code, 0, err)
      self.assertEqual([i.id for i in repo.state().index.items][-2:], planned)

  def test_Check_SiblingFiledOtherTitleUnderSameId_WarnsAndStaysGreen(self) -> None:
    with self.repo() as repo:
      sibling = self.sibling(repo, "other")
      _run(sibling, "add", "their item", "--id", "S02")
      repo.run("add", "our item", "--id", "S02")
      repo.run("render")
      code, out, err = repo.run("check", "--json")
      self.assertEqual(code, 0, err)
      warnings = json.loads(out)["warnings"]
      self.assertIn(
        "S02 is 'our item' here but 'their item' in worktree other; the two will "
        "collide on merge. File this checkout's item under a new id before merging.",
        warnings,
      )

  def test_Check_SiblingHasSameItem_DoesNotWarn(self) -> None:
    with self.repo() as repo:
      self.sibling(repo, "other")
      repo.run("render")
      code, out, err = repo.run("check", "--json")
      self.assertEqual(code, 0, err)
      self.assertFalse([w for w in json.loads(out)["warnings"] if "collide" in w])

  def test_Check_NotAGitRepo_DoesNoSiblingWork(self) -> None:
    repo = support.TempRepo()
    with repo:
      repo.run("init")
      repo.run("add", "one")
      repo.run("render")
      with patch.object(store, "sibling_ids", side_effect=AssertionError("git work")):
        code, _, err = repo.run("check", "--json")
      self.assertEqual(code, 0, err)


if __name__ == "__main__":
  unittest.main()
