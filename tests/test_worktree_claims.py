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

  def test_NextReview_ItemReviewingInSibling_IsSkipped(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S01", "--status", "review")
      repo.commit("in review")
      sibling = self.sibling(repo, "reviewer")
      code, _, err = _run(sibling, "set", "S01", "--status", "reviewing")
      self.assertEqual(code, 0, err)
      code, out, _ = repo.run("next", "--status", "review", "--json")
      self.assertEqual(code, 2)
      payload = json.loads(out)
      self.assertIsNone(payload["item"])
      self.assertEqual(
        payload["in_work_elsewhere"], [{"id": "S01", "worktree": "reviewer", "owner": ""}]
      )

  def test_SiblingReviewing_IsInWorkElsewhere(self) -> None:
    with self.repo() as repo:
      sibling = self.sibling(repo, "other")
      code, _, err = _run(sibling, "set", "S01", "--status", "reviewing")
      self.assertEqual(code, 0, err)
      self.assertIn("wt:other", _row(repo.run("list")[1], "S01"))
      payload = json.loads(repo.run("next", "--json")[1])
      self.assertEqual(payload["id"], "S02")
      self.assertEqual(
        payload["in_work_elsewhere"], [{"id": "S01", "worktree": "other", "owner": ""}]
      )

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


class NextSkipsSiblingWorkTests(unittest.TestCase):
  """`next` and `status` skip an item a sibling worktree has in work (S136)."""

  repo = WorktreeClaimTests.repo
  sibling = WorktreeClaimTests.sibling

  def test_Next_SiblingStart_SkipsItAndReportsIt(self) -> None:
    with self.repo() as repo:
      _run(self.sibling(repo, "other"), "start", "S01")
      code, out, err = repo.run("next")
      self.assertEqual(code, 0, err)
      self.assertTrue(out.startswith("S02"), out)
      self.assertIn("skipped S01 (in work in wt:other)", out)
      payload = json.loads(repo.run("next", "--json")[1])
      self.assertEqual(payload["id"], "S02")
      self.assertEqual(payload["in_work_elsewhere"], [
        {"id": "S01", "worktree": "other", "owner": "Test"},
      ])

  def test_NextReady_SiblingStart_ReportsTheSkip(self) -> None:
    with self.repo() as repo:
      _run(self.sibling(repo, "other"), "start", "S01")
      payload = json.loads(repo.run("next", "--ready", "--json")[1])
      self.assertEqual(payload["item"]["id"], "S02")
      self.assertEqual([e["id"] for e in payload["in_work_elsewhere"]], ["S01"])

  def test_Next_LocalStartToo_KeepsTheItem(self) -> None:
    with self.repo() as repo:
      _run(self.sibling(repo, "other"), "start", "S01")
      repo.run("start", "S01")
      payload = json.loads(repo.run("next", "--json")[1])
      self.assertEqual(payload["id"], "S01")
      self.assertNotIn("in_work_elsewhere", payload)

  def test_Next_EverythingElsewhere_ReturnsNothingAndSaysWhy(self) -> None:
    with self.repo() as repo:
      sibling = self.sibling(repo, "other")
      _run(sibling, "start", "S01")
      _run(sibling, "start", "S02")
      code, out, _ = repo.run("next", "--json")
      self.assertEqual(code, 2)
      payload = json.loads(out)
      self.assertIsNone(payload["item"])
      self.assertEqual([e["id"] for e in payload["in_work_elsewhere"]], ["S01", "S02"])

  def test_Status_AgreesWithNext(self) -> None:
    with self.repo() as repo:
      _run(self.sibling(repo, "other"), "start", "S01")
      payload = json.loads(repo.run("status", "--json")[1])
      self.assertEqual(payload["next"]["id"], "S02")
      self.assertEqual([e["id"] for e in payload["in_work_elsewhere"]], ["S01"])

  def test_Next_NoSiblings_OmitsTheKey(self) -> None:
    with self.repo() as repo:
      payload = json.loads(repo.run("next", "--json")[1])
      self.assertEqual(payload["id"], "S01")
      self.assertNotIn("in_work_elsewhere", payload)

  def test_Render_StaysLocal_WhenASiblingHasWork(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      _run(self.sibling(repo, "other"), "start", "S01")
      code, out, err = repo.run("check")
      self.assertEqual(code, 0, out + err)  # the ROADMAP next pointer ignores siblings


if __name__ == "__main__":
  unittest.main()
