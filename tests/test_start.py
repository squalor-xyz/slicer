"""A started status records what is in flight, and `next` prefers it (S44)."""

from __future__ import annotations

import json
import unittest

import support

from slicer import tui
from slicer.config import Config


def set_statuses(repo: support.TempRepo, statuses: dict, **extra) -> None:
  path = repo.root / ".slicer/config.json"
  cfg = json.loads(path.read_text())
  cfg["statuses"] = statuses
  cfg.update(extra)
  path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class StartCliTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.run("add", "a thing")
    return repo

  def test_Start_OpenItem_MovesItToTheStartedStatus(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("start", "S01")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().index.require("S01").status, "started")

  def test_Start_PromotedItem_LeavesTheSliceFileWhereItWas(self) -> None:
    """The status is non-terminal, so it gets no folder -- like parked."""
    with self.repo() as repo:
      repo.run("promote", "S01")
      code, _, err = repo.run("start", "S01")
      self.assertEqual(code, 0, err)
      self.assertTrue((repo.root / ".slicer/slices/S01.json").is_file())
      self.assertFalse((repo.root / ".slicer/slices/started").exists())

  def test_Start_CustomStartedStatus_UsesIt(self) -> None:
    with self.repo() as repo:
      set_statuses(
        repo,
        {"open": "—", "done": "done", "wip": "wip", "retired": "retired"},
        started_status="wip",
        parked_status="",
      )
      code, _, err = repo.run("start", "S01")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().index.require("S01").status, "wip")

  def test_Start_ProjectWithoutAStartState_RefusesClearly(self) -> None:
    with self.repo() as repo:
      set_statuses(
        repo,
        {"open": "—", "done": "done", "retired": "retired"},
        started_status="",
        parked_status="",
      )
      code, _, err = repo.run("start", "S01")
      self.assertEqual(code, 2)
      self.assertIn("no started status", err)
      # not the confusing "unknown status 'started_status'" that reusing
      # _status_cmd's getattr fallback would have produced
      self.assertNotIn("unknown status", err)
      self.assertEqual(repo.state().index.require("S01").status, "open")

  def test_Start_AlreadyStarted_IsASilentNoOp(self) -> None:
    """Same as `done` on a done item: exit 0, and no second log entry."""
    with self.repo() as repo:
      repo.run("start", "S01")
      before = len(repo.state().history())
      code, _, err = repo.run("start", "S01")
      self.assertEqual(code, 0, err)
      self.assertEqual(len(repo.state().history()), before)

  def test_Start_Note_ReachesTheLog(self) -> None:
    with self.repo() as repo:
      repo.run("start", "S01", "--note", "picking this up")
      entry = repo.state().history()[-1]
      self.assertEqual((entry.item, entry.frm, entry.to), ("S01", "open", "started"))
      self.assertEqual(entry.note, "picking this up")

  def test_Start_Json_EmitsTheItem(self) -> None:
    with self.repo() as repo:
      code, out, err = repo.run("start", "S01", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out)["status"], "started")

  def test_Start_Render_LeavesCheckClean(self) -> None:
    with self.repo() as repo:
      repo.run("promote", "S01")
      repo.run("render")
      code, out, _ = repo.run("start", "S01", "--render")
      self.assertEqual(code, 0, out)
      self.assertIn("rendered", out)
      self.assertEqual(repo.run("check")[0], 0)


class StartNextTests(unittest.TestCase):
  """`next` answers "what should I be doing", so started work wins outright."""

  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.run("add", "low priority", "--importance", "1", "--urgency", "1")
    repo.run("add", "high priority", "--importance", "3", "--urgency", "3")
    return repo

  def test_Next_NothingStarted_StillPicksTheHighestScore(self) -> None:
    with self.repo() as repo:
      code, out, err = repo.run("next", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out)["id"], "S02")

  def test_Next_AStartedItem_BeatsAHigherScoringOpenOne(self) -> None:
    with self.repo() as repo:
      repo.run("start", "S01")  # the low-priority one
      code, out, err = repo.run("next", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out)["id"], "S01")

  def test_Next_TwoStartedItems_PicksTheHigherScoring(self) -> None:
    with self.repo() as repo:
      repo.run("start", "S01")
      repo.run("start", "S02")
      code, out, err = repo.run("next", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out)["id"], "S02")

  def test_Next_ABlockedStartedItem_IsStillWithheld(self) -> None:
    """Dependencies hard-gate whatever the status: started does not bypass them."""
    with self.repo() as repo:
      repo.run("set", "S01", "--depends-on", "S02")
      repo.run("start", "S01")
      code, out, err = repo.run("next", "--json")
      self.assertEqual(code, 0, err)
      # S01 is started but waits on S02, so the blocker is what comes back
      self.assertEqual(json.loads(out)["id"], "S02")

  def test_Next_OnlyABlockedStartedItem_ReportsItAsBlocked(self) -> None:
    with self.repo() as repo:
      repo.run("done", "S02")
      repo.run("add", "a blocker")
      repo.run("set", "S01", "--depends-on", "S03")
      repo.run("park", "S03")
      repo.run("start", "S01")
      code, out, _ = repo.run("next", "--json")
      self.assertEqual(code, 2)  # nothing runnable is a normal empty queue
      payload = json.loads(out)
      self.assertIsNone(payload["item"])
      self.assertEqual([b["id"] for b in payload["blocked"]], ["S01"])

  def test_Next_ProjectWithoutAStartState_IsUnaffected(self) -> None:
    with self.repo() as repo:
      set_statuses(
        repo,
        {"open": "—", "done": "done", "retired": "retired"},
        started_status="",
        parked_status="",
      )
      code, out, err = repo.run("next", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out)["id"], "S02")


class StartVisibilityTests(unittest.TestCase):
  """The queue has to show what is in flight."""

  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.run("add", "a thing")
    repo.run("add", "another thing")
    repo.run("start", "S01")
    return repo

  def test_List_FilteredByStarted_ShowsOnlyTheStartedItem(self) -> None:
    with self.repo() as repo:
      code, out, err = repo.run("list", "--status", "started", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual([i["id"] for i in json.loads(out)], ["S01"])

  def test_List_Default_LabelsTheStartedItem(self) -> None:
    with self.repo() as repo:
      code, out, err = repo.run("list")
      self.assertEqual(code, 0, err)
      started = [ln for ln in out.splitlines() if "S01" in ln][0]
      self.assertIn("started", started)

  def test_Stats_CountsTheStartedItem(self) -> None:
    with self.repo() as repo:
      code, out, err = repo.run("stats", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out)["by_status"].get("started"), 1)


class StartConfigTests(unittest.TestCase):
  BASE = {
    "version": 1,
    "id": {"prefix": "S", "width": 2},
    "open_status": "open",
    "done_status": "done",
    "sections": ["Why"],
    "done_dir": "done",
  }

  def test_Config_WithoutAStartedStatus_GainsOnlyThatOneKey(self) -> None:
    cfg = Config.from_dict(
      self.BASE | {"statuses": {"open": "—", "done": "done", "parked": "parked"}}
    )
    self.assertIn("started", cfg.statuses)
    self.assertEqual(cfg.started_status, "started")
    # a status the project deliberately dropped does not come back with it
    self.assertNotIn("later", cfg.statuses)

  def test_Config_WithACustomStartedStatusName_UsesIt(self) -> None:
    cfg = Config.from_dict(
      self.BASE
      | {
        "statuses": {"open": "—", "done": "done", "wip": "wip"},
        "started_status": "wip",
      }
    )
    self.assertEqual(cfg.started_status, "wip")
    self.assertNotIn("started", cfg.statuses)

  def test_Config_WithAnEmptyStartedStatus_StaysWithout(self) -> None:
    cfg = Config.from_dict(
      self.BASE
      | {"statuses": {"open": "—", "done": "done"}, "started_status": ""}
    )
    self.assertEqual(cfg.started_status, "")
    self.assertNotIn("started", cfg.statuses)

  def test_Config_RoundTrips(self) -> None:
    cfg = Config.from_dict(Config().to_dict())
    self.assertEqual(cfg.started_status, "started")


class StartTuiTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.run("add", "a thing")
    return repo

  def test_TuiStartKey_DefaultProject_Starts(self) -> None:
    with self.repo() as repo:
      state = repo.state()
      result = tui.act(state, "s", "S01")
      self.assertEqual(state.index.require("S01").status, "started")
      self.assertIn("started", result.message)

  def test_TuiStartKey_OnAProseRow_IsRefused(self) -> None:
    with self.repo() as repo:
      result = tui.act(repo.state(), "s", "preamble")
      self.assertIn("not to a prose block", result.message)

  def test_TuiStartKey_NoStartState_ReportsCleanly(self) -> None:
    with self.repo() as repo:
      set_statuses(
        repo,
        {"open": "—", "done": "done", "retired": "retired"},
        started_status="",
        parked_status="",
      )
      state = repo.state()
      result = tui.act(state, "s", "S01")
      self.assertIn("no started status", result.message)
      self.assertEqual(state.index.require("S01").status, "open")

  def test_TuiRows_AStartedBlockedItem_StillShowsTheBlockedMarker(self) -> None:
    with self.repo() as repo:
      repo.run("add", "a blocker")
      repo.run("set", "S01", "--depends-on", "S02")
      repo.run("start", "S01")
      row = [r for r in tui.rows(repo.state()) if r.target == "S01"][0]
      self.assertIn("!", row.text)


if __name__ == "__main__":
  unittest.main()
