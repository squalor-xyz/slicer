"""List shows real work in sibling indexes without changing stored items."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import support

from slicer import store, vcs
from slicer.cli import main


def _run(root: Path, *args: str) -> tuple[int, str, str]:
  out, err = io.StringIO(), io.StringIO()
  with redirect_stdout(out), redirect_stderr(err):
    code = main(["--root", str(root), *args])
  return code, out.getvalue(), err.getvalue()


def _row(text: str, item_id: str) -> str:
  return next(line for line in text.splitlines() if item_id in line.split())


class WorktreeClaimTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo(git=True)
    repo.run("init")
    repo.run("add", "one")
    repo.run("add", "two")
    repo.commit("base")
    return repo

  def sibling(self, repo: support.TempRepo, name: str) -> Path:
    root = repo.root / "siblings" / name
    root.parent.mkdir(exist_ok=True)
    made = repo._git("worktree", "add", "-q", "-b", name, str(root), "HEAD")
    self.assertEqual(made.returncode, 0, made.stderr)
    return root

  def test_List_SiblingStart_ShowsWorktreeAndJsonWithoutChangingStoredItem(self) -> None:
    with self.repo() as repo:
      sibling = self.sibling(repo, "other")
      code, _, err = _run(sibling, "start", "S01")
      self.assertEqual(code, 0, err)
      code, out, err = repo.run("list")
      self.assertEqual(code, 0, err)
      self.assertIn("wt:other", _row(out, "S01"))
      self.assertEqual(repo.state().index.require("S01").status, "open")
      rows = {i["id"]: i for i in json.loads(repo.run("list", "--json")[1])}
      self.assertEqual(rows["S01"]["in_work_elsewhere"], [
        {"worktree": "other", "owner": "Test"},
      ])
      self.assertEqual(rows["S02"]["in_work_elsewhere"], [])
      self.assertNotIn("in_work_elsewhere", repo.state().index.require("S01").to_dict())

  def test_List_LocalClaimWinsText_ButJsonKeepsSibling(self) -> None:
    with self.repo() as repo:
      sibling = self.sibling(repo, "other")
      _run(sibling, "start", "S01")
      repo.run("start", "S01")
      line = _row(repo.run("list")[1], "S01")
      self.assertIn("Test", line)
      self.assertNotIn("wt:other", line)
      row = next(i for i in json.loads(repo.run("list", "--json")[1]) if i["id"] == "S01")
      self.assertEqual(len(row["in_work_elsewhere"]), 1)

  def test_List_UnclaimedStartedState_StillCountsInSiblingAndLocally(self) -> None:
    with self.repo() as repo:
      sibling = self.sibling(repo, "other")
      _run(sibling, "start", "S01")
      _run(sibling, "release", "S01")
      self.assertIn("wt:other", _row(repo.run("list")[1], "S01"))
      row = next(i for i in json.loads(repo.run("list", "--json")[1]) if i["id"] == "S01")
      self.assertEqual(row["in_work_elsewhere"], [{"worktree": "other", "owner": ""}])
      repo.run("start", "S01")
      repo.run("release", "S01")
      line = _row(repo.run("list")[1], "S01")
      self.assertIn("*", line)
      self.assertNotIn("wt:other", line)

  def test_List_MultipleSiblings_ReportsAllInStableOrder(self) -> None:
    with self.repo() as repo:
      b = self.sibling(repo, "b")
      a = self.sibling(repo, "a")
      _run(a, "start", "S01")
      _run(b, "start", "S01")
      self.assertIn("wt:a+1", _row(repo.run("list")[1], "S01"))
      row = next(i for i in json.loads(repo.run("list", "--json")[1]) if i["id"] == "S01")
      self.assertEqual([x["worktree"] for x in row["in_work_elsewhere"]], ["a", "b"])

  def test_List_BranchNameAlone_DoesNotMarkWork(self) -> None:
    with self.repo() as repo:
      made = repo._git("branch", "feature/S01")
      self.assertEqual(made.returncode, 0, made.stderr)
      row = next(i for i in json.loads(repo.run("list", "--json")[1]) if i["id"] == "S01")
      self.assertEqual(row["in_work_elsewhere"], [])
      self.assertNotIn("wt:", _row(repo.run("list")[1], "S01"))

  def test_List_MissingOrUnreadableSibling_SkipsIt(self) -> None:
    with self.repo() as repo:
      sibling = self.sibling(repo, "other")
      index = sibling / ".slicer/index.json"
      index.unlink()
      self.assertEqual(json.loads(repo.run("list", "--json")[1])[0]["in_work_elsewhere"], [])
      index.write_text("not json", encoding="utf-8")
      code, out, err = repo.run("list", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out)[0]["in_work_elsewhere"], [])
      index.write_text("[]", encoding="utf-8")
      code, out, err = repo.run("list", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out)[0]["in_work_elsewhere"], [])

  def test_List_OutsideGit_HasNoSiblingLookup(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "one")
      code, out, err = repo.run("list", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out)[0]["in_work_elsewhere"], [])

  def test_List_EnumeratesOnceAndReadsEachSiblingIndexOnce(self) -> None:
    with self.repo() as repo:
      self.sibling(repo, "a")
      self.sibling(repo, "b")
      with patch.object(vcs, "_worktree_paths", wraps=vcs._worktree_paths) as paths, patch.object(
        store, "read_index", wraps=store.read_index,
      ) as indexes:
        code, _, err = repo.run("list")
      self.assertEqual(code, 0, err)
      self.assertEqual(paths.call_count, 1)
      self.assertEqual(indexes.call_count, 3)  # local state plus two siblings


if __name__ == "__main__":
  unittest.main()
