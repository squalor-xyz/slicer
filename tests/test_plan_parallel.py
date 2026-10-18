"""`plan-parallel` lays the open work out in waves that can run side by side."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import support
from slicer.cli import main


def _waves(payload: dict) -> list[list[str]]:
  return [[item["id"] for item in wave["items"]] for wave in payload["waves"]]


def _reasons(payload: dict) -> dict[str, str]:
  return {item["id"]: item.get("reason") for wave in payload["waves"] for item in wave["items"]}


class PlanParallelTests(unittest.TestCase):
  def repo(self, ignore: str = "[]") -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.run("config", "files_section", "Files")
    repo.run("config", "overlap_ignore", ignore)
    (repo.root / "CHANGELOG.md").write_text("Changelog\n")
    return repo

  def file_item(self, repo: support.TempRepo, title: str, files: str | None) -> str:
    """Add an item; with `files`, give it a specified slice whose Files section is that text."""
    item_id = json.loads(repo.run("add", title, "--json")[1])["id"]
    if files is not None:
      repo.run("promote", item_id)
      repo.run("edit", item_id, "--section", "Implement", "--text", "Do it.")
      repo.run("edit", item_id, "--section", "Check", "--text", "It works.")
      repo.run("edit", item_id, "--section", "Files", "--text", files)
    return item_id

  def plan(self, repo: support.TempRepo, *args: str) -> dict:
    code, out, err = repo.run("plan-parallel", *args, "--json")
    self.assertEqual(code, 0, err)
    return json.loads(out)

  def snapshot(self, repo: support.TempRepo) -> dict[str, bytes]:
    return {
      str(p.relative_to(repo.root)): p.read_bytes()
      for p in repo.root.rglob("*") if p.is_file() and p.name != "lock"
    }

  def test_PlanParallel_DisjointReady_SameWave(self) -> None:
    with self.repo() as repo:
      self.file_item(repo, "first", "- `src/a.py`")
      self.file_item(repo, "second", "- `src/b.py`")
      self.file_item(repo, "third", "- `src/c.py`")
      payload = self.plan(repo)
      self.assertEqual(_waves(payload), [["S01", "S02", "S03"]])
      self.assertEqual(payload["waves"][0]["wave"], 1)
      self.assertEqual(payload["width"], 4)
      self.assertEqual(
        list(payload), ["width", "waves", "in_flight", "unplaced", "blocked"],
      )
      self.assertEqual(
        payload["waves"][0]["items"][0], {"id": "S01", "title": "first", "reason": ""},
      )

  def test_PlanParallel_SharedPath_LaterWaveWithOverlapReason(self) -> None:
    with self.repo() as repo:
      self.file_item(repo, "first", "- `src/a.py`\n- `docs/x.md`")
      self.file_item(repo, "overlaps first", "- `src/b.py:12`\n- `docs/x.md`")
      self.file_item(repo, "disjoint", "- `src/c.py`")
      payload = self.plan(repo)
      self.assertEqual(_waves(payload), [["S01", "S03"], ["S02"]])
      self.assertEqual(_reasons(payload)["S02"], "shares: S01 docs/x.md")
      text = repo.run("plan-parallel")[1]
      self.assertIn("wave 2", text)
      self.assertIn("S02", text)
      self.assertIn("shares: S01 docs/x.md", text)

  def test_PlanParallel_IgnoredPath_DoesNotSerialise(self) -> None:
    with self.repo(ignore='["CHANGELOG.md"]') as repo:
      self.file_item(repo, "first", "- `src/a.py`\n- `CHANGELOG.md`")
      self.file_item(repo, "second", "- `src/b.py`\n- `CHANGELOG.md`")
      self.assertEqual(_waves(self.plan(repo)), [["S01", "S02"]])
      repo.run("config", "overlap_ignore", "[]")
      self.assertEqual(_waves(self.plan(repo)), [["S01"], ["S02"]])

  def test_PlanParallel_Dependent_WaveAfterDependency(self) -> None:
    with self.repo() as repo:
      first = self.file_item(repo, "dependency", "- `src/a.py`")
      second = self.file_item(repo, "dependent", "- `src/b.py`")
      third = self.file_item(repo, "later dependent", "- `src/c.py`")
      repo.run("set", second, "--depends-on", first)
      repo.run("set", third, "--depends-on", second)
      # The dependent ranks above its dependency; the plan still orders them.
      repo.run("set", second, "--importance", "5")
      repo.run("set", third, "--importance", "5")
      payload = self.plan(repo)
      self.assertEqual(_waves(payload), [[first], [second], [third]])
      self.assertEqual(_reasons(payload)[second], f"after: {first}")
      self.assertEqual(_reasons(payload)[third], f"after: {second}")

  def test_PlanParallel_Width_CapsEachWave(self) -> None:
    with self.repo() as repo:
      for letter in "abcde":
        self.file_item(repo, f"item {letter}", f"- `src/{letter}.py`")
      payload = self.plan(repo, "--width", "2")
      self.assertEqual(payload["width"], 2)
      self.assertEqual(_waves(payload), [["S01", "S02"], ["S03", "S04"], ["S05"]])
      self.assertEqual(_reasons(payload)["S03"], "width")
      self.assertEqual(_reasons(payload)["S05"], "width")
      self.assertEqual(_waves(self.plan(repo)), [["S01", "S02", "S03", "S04"], ["S05"]])
      code, out, _ = repo.run("plan-parallel", "--width", "0", "--json")
      self.assertNotEqual(code, 0)

  def test_PlanParallel_StartedItem_InFlightBlocksItsPaths(self) -> None:
    with self.repo() as repo:
      running = self.file_item(repo, "running", "- `src/a.py`")
      clash = self.file_item(repo, "clashes", "- `src/a.py`")
      free = self.file_item(repo, "free", "- `src/b.py`")
      follower = self.file_item(repo, "follows running", "- `src/c.py`")
      repo.run("set", follower, "--depends-on", running)
      repo.run("start", running)
      payload = self.plan(repo)
      self.assertEqual(payload["in_flight"], [{"id": running, "title": "running"}])
      self.assertEqual(_waves(payload), [[free, follower], [clash]])
      self.assertEqual(_reasons(payload)[clash], f"shares: {running} src/a.py")
      self.assertEqual(_reasons(payload)[follower], f"after: {running}")

  def test_PlanParallel_AllCandidatesClashWithInFlight_FirstWaveHoldsThem(self) -> None:
    with self.repo() as repo:
      running = self.file_item(repo, "running", "- `src/a.py`")
      clash = self.file_item(repo, "clashes", "- `src/a.py`")
      repo.run("start", running)
      payload = self.plan(repo)
      self.assertEqual(_waves(payload), [[clash]])
      self.assertEqual(payload["waves"][0]["wave"], 1)
      self.assertEqual(_reasons(payload)[clash], f"shares: {running} src/a.py")

  def test_PlanParallel_NoFilesPaths_ListedUnplaced(self) -> None:
    with self.repo() as repo:
      self.file_item(repo, "first", "- `src/a.py`")
      bare = self.file_item(repo, "no section", None)
      empty = self.file_item(repo, "no paths", "Nothing yet.")
      waits = self.file_item(repo, "waits on bare", "- `src/b.py`")
      repo.run("set", waits, "--depends-on", bare)
      payload = self.plan(repo)
      self.assertEqual(_waves(payload), [["S01"]])
      self.assertEqual(
        payload["unplaced"],
        [{"id": bare, "title": "no section", "reason": "no_files"},
         {"id": empty, "title": "no paths", "reason": "no_files"}],
      )
      self.assertEqual(payload["blocked"], [{"id": waits, "waiting_on": [bare]}])
      text = repo.run("plan-parallel")[1]
      self.assertIn("no_files", text)

  def test_PlanParallel_DependencyOutsidePool_Blocked(self) -> None:
    with self.repo() as repo:
      parked = self.file_item(repo, "parked", "- `src/a.py`")
      waits = self.file_item(repo, "waits", "- `src/b.py`")
      free = self.file_item(repo, "free", "- `src/c.py`")
      repo.run("set", waits, "--depends-on", parked)
      repo.run("park", parked)
      payload = self.plan(repo)
      self.assertEqual(_waves(payload), [[free]])
      self.assertEqual(payload["blocked"], [{"id": waits, "waiting_on": [parked]}])

  def test_PlanParallel_HeldAndUnspecified_ReportedLikeNext(self) -> None:
    with self.repo() as repo:
      held = self.file_item(repo, "held", "- `src/a.py`")
      self.file_item(repo, "free", "- `src/b.py`")
      repo.run("add", "thin")
      repo.run("promote", "S03")
      repo.run("set", held, "--add-flag", "needs-owner")
      payload = self.plan(repo)
      self.assertEqual(_waves(payload), [["S02"]])
      self.assertEqual([h["id"] for h in payload["held"]], [held])
      self.assertEqual([u["id"] for u in payload["unspecified"]], ["S03"])

  def test_PlanParallel_Empty_ExitsTwoWithShape(self) -> None:
    with self.repo() as repo:
      self.file_item(repo, "no files", None)
      code, out, _ = repo.run("plan-parallel", "--json")
      payload = json.loads(out)
      self.assertEqual(code, 2)
      self.assertEqual(payload["waves"], [])
      self.assertEqual([u["id"] for u in payload["unplaced"]], ["S01"])
      self.assertNotIn("error", payload)

  def test_PlanParallel_NoFilesSection_Usage(self) -> None:
    with self.repo() as repo:
      repo.run("config", "files_section", "")
      self.file_item(repo, "first", "- `src/a.py`")
      code, out, _ = repo.run("plan-parallel", "--json")
      error = json.loads(out)["error"]
      self.assertEqual((code, error["code"]), (2, "usage"))
      self.assertIn("files_section", error["message"])

  def test_PlanParallel_FilesSectionMissing_Warns(self) -> None:
    with self.repo() as repo:
      self.file_item(repo, "first", "- `src/a.py`")
      repo.run("config", "files_section", "MissingFiles")
      code, out, err = repo.run("plan-parallel", "--json")
      self.assertIn("no item has", err)
      self.assertEqual(json.loads(out)["unplaced"][0]["reason"], "no_files")

  def test_PlanParallel_Lean_KeepsShapeAndDropsEmptyReason(self) -> None:
    with self.repo() as repo:
      self.file_item(repo, "first", "- `src/a.py`")
      payload = json.loads(repo.run("plan-parallel", "--json", "--lean")[1])
      self.assertEqual(payload["waves"][0]["items"], [{"id": "S01", "title": "first"}])
      for key in ("in_flight", "unplaced", "blocked"):
        self.assertEqual(payload[key], [])

  def test_PlanParallel_TreeFilter_NarrowsPool(self) -> None:
    with self.repo() as repo:
      self.file_item(repo, "core one", "- `src/a.py`")
      other = self.file_item(repo, "other", "- `src/b.py`")
      repo.run("set", other, "--tree", "other")
      self.assertEqual(_waves(self.plan(repo, "--tree", "other")), [[other]])

  def test_PlanParallel_SiblingClaim_ReportedAndNotPlaced(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      repo.run("config", "files_section", "Files")
      claimed = self.file_item(repo, "claimed elsewhere", "- `src/a.py`")
      free = self.file_item(repo, "free", "- `src/b.py`")
      repo.commit("base")
      sibling = repo.root / "siblings" / "other"
      sibling.parent.mkdir()
      made = repo._git("worktree", "add", "-q", "-b", "other", str(sibling), "HEAD")
      self.assertEqual(made.returncode, 0, made.stderr)
      out, err = io.StringIO(), io.StringIO()
      with redirect_stdout(out), redirect_stderr(err):
        self.assertEqual(main(["--root", str(sibling), "start", claimed]), 0, err.getvalue())
      payload = self.plan(repo)
      self.assertEqual(_waves(payload), [[free]])
      self.assertEqual(
        payload["in_work_elsewhere"],
        [{"id": claimed, "worktree": "other", "owner": "Test", "status": "started"}],
      )

  def test_PlanParallel_BlockedAndSiblingClaimed_StillReportedBlocked(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      repo.run("config", "files_section", "Files")
      blocker = self.file_item(repo, "blocker", "- `src/a.py`")
      waits = self.file_item(repo, "waits", "- `src/b.py`")
      repo.run("set", waits, "--depends-on", blocker)
      repo.commit("base")
      sibling = repo.root / "siblings" / "other"
      sibling.parent.mkdir()
      made = repo._git("worktree", "add", "-q", "-b", "other", str(sibling), "HEAD")
      self.assertEqual(made.returncode, 0, made.stderr)
      out, err = io.StringIO(), io.StringIO()
      with redirect_stdout(out), redirect_stderr(err):
        self.assertEqual(main(["--root", str(sibling), "start", waits]), 0, err.getvalue())
      payload = self.plan(repo)
      self.assertEqual(_waves(payload), [[blocker]])
      self.assertEqual(payload["blocked"], [{"id": waits, "waiting_on": [blocker]}])

  def test_PlanParallel_DoneDependency_DoesNotGate(self) -> None:
    with self.repo() as repo:
      first = self.file_item(repo, "finished", "- `src/a.py`")
      second = self.file_item(repo, "follows", "- `src/b.py`")
      repo.run("set", second, "--depends-on", first)
      repo.run("start", first)
      repo.run("done", first)
      payload = self.plan(repo)
      self.assertEqual(_waves(payload), [[second]])
      self.assertEqual(_reasons(payload)[second], "")
      self.assertEqual(payload["blocked"], [])

  def test_PlanParallel_HeldBackTwice_ReasonIsTheLatestPass(self) -> None:
    with self.repo() as repo:
      first = self.file_item(repo, "first", "- `src/a.py`")
      second = self.file_item(repo, "second", "- `src/b.py`")
      third = self.file_item(repo, "third", "- `src/c.py`")
      fourth = self.file_item(repo, "fourth", "- `src/d.py`")
      waits = self.file_item(repo, "waits on first", "- `src/e.py`")
      repo.run("set", waits, "--depends-on", first)
      payload = self.plan(repo, "--width", "2")
      # Wave 1 holds `waits` back for its dependency; wave 2 is full before its turn.
      self.assertEqual(_waves(payload), [[first, second], [third, fourth], [waits]])
      self.assertEqual(_reasons(payload)[waits], "width")

  def test_PlanParallel_TwoSharedPaths_NamesTheFirstInTheItemsOrder(self) -> None:
    with self.repo() as repo:
      holder = self.file_item(repo, "holder", "- `src/a.py`\n- `src/b.py`")
      later = self.file_item(repo, "later", "- `src/b.py`\n- `src/a.py`")
      payload = self.plan(repo)
      self.assertEqual(_waves(payload), [[holder], [later]])
      self.assertEqual(_reasons(payload)[later], f"shares: {holder} src/b.py")

  def test_PlanParallel_ReadOnly_NoLockNoWrite(self) -> None:
    with self.repo() as repo:
      self.file_item(repo, "first", "- `src/a.py`")
      self.file_item(repo, "second", "- `src/a.py`")
      before = self.snapshot(repo)
      with patch("slicer.cli.store.project_lock", side_effect=AssertionError("lock")):
        for extra in ((), ("--json",)):
          code, _out, err = repo.run("plan-parallel", *extra)
          self.assertEqual((code, err), (0, ""))
      self.assertEqual(self.snapshot(repo), before)


if __name__ == "__main__":
  unittest.main()
