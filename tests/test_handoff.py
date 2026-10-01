"""`slicer handoff`: started work moves to review for someone else (S135).

Review sits between implementation and completion. The item leaves `next`,
keeps its slice in the active folder, and blocks its dependents until `done`.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import support
from slicer.config import Config
from slicer.errors import ConfigError, RenderError
from slicer.store import DIR_NAME, INDEX_NAME


def _config(repo: support.TempRepo, **changes: object) -> None:
  path = repo.root / DIR_NAME / "config.json"
  cfg = json.loads(path.read_text(encoding="utf-8"))
  for key, value in changes.items():
    if value is None:
      cfg.pop(key, None)
    else:
      cfg[key] = value
  path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class HandoffTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    _config(repo, claim_owner="Ada")
    repo.run("add", "build it", "--effort", "1")
    repo.run("promote", "S01")
    repo.run("add", "after it", "--effort", "1", "--depends-on", "S01")
    repo.run("start", "S01")
    return repo

  def log(self, repo: support.TempRepo, item_id: str) -> list[dict]:
    return json.loads(repo.run("log", "--item", item_id, "--json")[1])

  def test_Handoff_StartedSlice_MovesToReviewClearsClaimAndLogs(self) -> None:
    with self.repo() as repo:
      code, out, err = repo.run("handoff", "S01", "--render")
      self.assertEqual(code, 0, err)
      self.assertIn("S01 -> review, unclaimed", out)
      item = repo.state().index.require("S01")
      self.assertEqual((item.status, item.claim_owner), ("review", ""))
      self.assertTrue((repo.root / DIR_NAME / "slices" / "S01.json").is_file())
      entry = self.log(repo, "S01")[0]
      self.assertEqual((entry["action"], entry["from"], entry["to"]), ("handoff", "started", "review"))
      self.assertIn("Ada", entry["note"])
      self.assertEqual(repo.run("check")[0], 0)

  def test_Review_IsListedButNotNext_AndDependentStaysBlocked(self) -> None:
    with self.repo() as repo:
      repo.run("handoff", "S01")
      self.assertIn("S01", repo.run("list")[1])
      rows = json.loads(repo.run("list", "--status", "review", "--json")[1])
      self.assertEqual([r["id"] for r in rows], ["S01"])
      code, out, _ = repo.run("next", "--json")
      self.assertEqual(code, 2)
      payload = json.loads(out)
      self.assertIsNone(payload["item"])
      self.assertEqual(payload["blocked"], [{"id": "S02", "waiting_on": ["S01"]}])

  def test_Reviewer_StartsReviewingAndDone(self) -> None:
    with self.repo() as repo:
      repo.run("handoff", "S01")
      _config(repo, claim_owner="Bea")
      repo.run("start", "S01")
      item = repo.state().index.require("S01")
      self.assertEqual((item.status, item.claim_owner), ("reviewing", "Bea"))
      repo.run("done", "S01")
      item = repo.state().index.require("S01")
      self.assertEqual((item.status, item.claim_owner), ("done", ""))

  def test_Handoff_RefusesWhatIsNotStartedOrHasNoSlice(self) -> None:
    with self.repo() as repo:
      repo.run("add", "row only", "--effort", "1")
      repo.run("start", "S03")  # started, but no slice
      repo.run("add", "shipped", "--effort", "1", "--status", "done")
      repo.run("add", "gone", "--effort", "1")
      repo.run("remove", "S05", "--reason", "obsolete")
      repo.run("add", "waiting", "--effort", "1")
      for item_id in ("S03", "S04", "S05", "S06"):
        with self.subTest(item=item_id):
          before = (repo.root / DIR_NAME / INDEX_NAME).read_bytes()
          code, out, _ = repo.run("handoff", item_id, "--json")
          self.assertEqual(code, 2)
          self.assertEqual(json.loads(out)["error"]["code"], "state")
          self.assertEqual((repo.root / DIR_NAME / INDEX_NAME).read_bytes(), before)

  def test_Handoff_BatchWithOneBadId_ChangesNothing(self) -> None:
    with self.repo() as repo:
      code, _, _ = repo.run("handoff", "S01", "S02")
      self.assertEqual(code, 2)
      self.assertEqual(repo.state().index.require("S01").status, "started")

  def test_Handoff_AlreadyInReview_IsANoOp(self) -> None:
    with self.repo() as repo:
      repo.run("handoff", "S01")
      entries = len(self.log(repo, "S01"))
      self.assertEqual(repo.run("handoff", "S01")[0], 0)
      self.assertEqual(len(self.log(repo, "S01")), entries)

  def test_Handoff_StartedWithoutClaim_StillWorks(self) -> None:
    with self.repo() as repo:
      repo.run("release", "S01")
      self.assertEqual(repo.run("handoff", "S01")[0], 0)
      self.assertEqual(repo.state().index.require("S01").status, "review")

  def test_Handoff_StrictRenderFailure_ChangesNothing(self) -> None:
    with self.repo() as repo:
      index = repo.root / DIR_NAME / INDEX_NAME
      log = repo.root / DIR_NAME / "log.jsonl"
      before = (index.read_bytes(), log.read_bytes())
      with patch("slicer.cli.render.plan", side_effect=RenderError("boom")):
        code, out, _ = repo.run("handoff", "S01", "--render", "--strict")
      self.assertEqual(code, 2)
      self.assertEqual(out, "")
      self.assertEqual((index.read_bytes(), log.read_bytes()), before)
      item = repo.state().index.require("S01")
      self.assertEqual((item.status, item.claim_owner), ("started", "Ada"))
      self.assertTrue((repo.root / DIR_NAME / "slices" / "S01.json").is_file())

  def test_Handoff_JsonReportsReviewAndNoClaim(self) -> None:
    with self.repo() as repo:
      payload = json.loads(repo.run("handoff", "S01", "--json")[1])
      self.assertEqual((payload["status"], payload["claim"]), ("review", None))


class ReviewStatusConfigTests(unittest.TestCase):
  BASE = {
    "version": 1,
    "id": {"prefix": "S", "width": 2},
    "open_status": "open",
    "done_status": "done",
    "sections": ["Why"],
    "done_dir": "done",
  }

  def test_Config_OlderWithoutReview_GainsTheDefault(self) -> None:
    cfg = Config.from_dict(self.BASE | {"statuses": {"open": "—", "done": "done"}})
    self.assertEqual(cfg.review_status, "review")
    self.assertEqual(cfg.statuses["review"], "review")

  def test_Config_RenamedReviewStatus_IsKept(self) -> None:
    cfg = Config.from_dict(self.BASE | {
      "statuses": {"open": "—", "done": "done", "ready": "ready for review"},
      "review_status": "ready",
    })
    self.assertEqual(cfg.review_status, "ready")
    self.assertNotIn("review", cfg.statuses)

  def test_Config_EmptyReviewStatus_Disables(self) -> None:
    cfg = Config.from_dict(self.BASE | {"statuses": {"open": "—", "done": "done"}, "review_status": ""})
    self.assertEqual(cfg.review_status, "")
    self.assertNotIn("review", cfg.statuses)

  def test_Config_LabelAlreadyTaken_LeavesReviewOffInsteadOfFailing(self) -> None:
    cfg = Config.from_dict(self.BASE | {"statuses": {"open": "—", "done": "done", "qa": "review"}})
    self.assertEqual(cfg.review_status, "")

  def test_Config_ReviewSharingAnotherRole_IsRejected(self) -> None:
    with self.assertRaises(ConfigError):
      Config.from_dict(self.BASE | {"statuses": {"open": "—", "done": "done"}, "review_status": "open"})

  def test_Cli_DisabledReview_RefusesHandoff(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      _config(repo, review_status="")
      repo.run("add", "x", "--effort", "1")
      repo.run("promote", "S01")
      repo.run("start", "S01")
      code, out, _ = repo.run("handoff", "S01", "--json")
      self.assertEqual(code, 2)
      self.assertIn("review", json.loads(out)["error"]["message"])

  def test_Cli_RenamedReviewStatus_IsWhatHandoffSets(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      path = repo.root / DIR_NAME / "config.json"
      cfg = json.loads(path.read_text(encoding="utf-8"))
      cfg["statuses"]["ready"] = "ready for review"
      cfg["review_status"] = "ready"
      path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
      repo.run("add", "x", "--effort", "1")
      repo.run("promote", "S01")
      repo.run("start", "S01")
      self.assertEqual(repo.run("handoff", "S01")[0], 0)
      self.assertEqual(repo.state().index.require("S01").status, "ready")


if __name__ == "__main__":
  unittest.main()


class ReviewingTests(unittest.TestCase):
  """A reviewer's `start` moves review work to reviewing, out of `next` (S150)."""

  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    _config(repo, claim_owner="Ada")
    repo.run("add", "build it", "--effort", "1")
    repo.run("promote", "S01")
    repo.run("add", "other work", "--effort", "1")
    repo.run("start", "S01")
    repo.run("handoff", "S01")
    _config(repo, claim_owner="Bea")
    return repo

  def item(self, repo: support.TempRepo, item_id: str = "S01"):
    return repo.state().index.require(item_id)

  def test_Start_ReviewItem_MovesToReviewingAndClaims_AndNextSkipsIt(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("start", "S01")
      self.assertEqual(code, 0, err)
      self.assertEqual((self.item(repo).status, self.item(repo).claim_owner), ("reviewing", "Bea"))
      entry = json.loads(repo.run("log", "--item", "S01", "--json")[1])[0]
      self.assertEqual((entry["action"], entry["from"], entry["to"]), ("status", "review", "reviewing"))
      payload = json.loads(repo.run("next", "--json")[1])
      self.assertEqual(payload["id"], "S02")
      code, out, _ = repo.run("next", "-n", "1", "--json")
      self.assertEqual(code, 2)
      self.assertNotIn("S01", [b["id"] for b in json.loads(out)["blocked"]])

  def test_Start_OtherStatuses_StillGoToStarted(self) -> None:
    with self.repo() as repo:
      repo.run("add", "parked one", "--effort", "1", "--status", "parked")
      for item_id in ("S02", "S03"):
        repo.run("start", item_id)
        self.assertEqual(self.item(repo, item_id).status, "started")
      repo.run("start", "S02")
      self.assertEqual(self.item(repo, "S02").status, "started")

  def test_Config_Backfill_FollowsTheReviewRule(self) -> None:
    base = {"statuses": {"open": "—", "done": "done"}}
    self.assertEqual(Config.from_dict(base).statuses["reviewing"], "reviewing")
    self.assertEqual(Config.from_dict(base).reviewing_status, "reviewing")
    taken = Config.from_dict({"statuses": {"open": "—", "done": "done", "busy": "reviewing"}})
    self.assertEqual(taken.reviewing_status, "")
    self.assertNotIn("reviewing", taken.statuses)
    no_review = Config.from_dict(base | {"review_status": ""})
    self.assertEqual(no_review.reviewing_status, "")
    self.assertNotIn("reviewing", no_review.statuses)

  def test_Disabled_StartOnReviewGoesToStartedAsBefore(self) -> None:
    with self.repo() as repo:
      _config(repo, reviewing_status="")
      repo.run("start", "S01")
      self.assertEqual(self.item(repo).status, "started")

  def test_Config_ReviewingSharedWithAnotherRole_IsRejected(self) -> None:
    with self.assertRaises(ConfigError):
      Config.from_dict({"statuses": {"open": "—", "done": "done"}, "reviewing_status": "started"})
    with self.repo() as repo:
      _config(repo, reviewing_status="review")
      code, out, _ = repo.run("list", "--json")
      self.assertEqual(code, 3)
      self.assertEqual(json.loads(out)["error"]["code"], "config")

  def test_Handoff_ReviewingItem_IsRefusedAndWritesNothing(self) -> None:
    with self.repo() as repo:
      repo.run("start", "S01")
      before = (repo.root / DIR_NAME / INDEX_NAME).read_bytes()
      code, out, _ = repo.run("handoff", "S01", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out)["error"]["code"], "state")
      self.assertEqual((repo.root / DIR_NAME / INDEX_NAME).read_bytes(), before)

  def test_Done_FromReviewing_ClearsTheClaim(self) -> None:
    with self.repo() as repo:
      repo.run("start", "S01")
      repo.run("done", "S01")
      self.assertEqual((self.item(repo).status, self.item(repo).claim_owner), ("done", ""))

  def test_Start_ReleasedReviewingItem_StaysReviewingAndReclaims(self) -> None:
    with self.repo() as repo:
      repo.run("start", "S01")
      repo.run("release", "S01")
      self.assertEqual((self.item(repo).status, self.item(repo).claim_owner), ("reviewing", ""))
      self.assertIn("*", next(l for l in repo.run("list")[1].splitlines() if "S01" in l.split()))
      _config(repo, claim_owner="Cy")
      repo.run("start", "S01")
      self.assertEqual((self.item(repo).status, self.item(repo).claim_owner), ("reviewing", "Cy"))

  def test_List_RanksReviewingWithStarted(self) -> None:
    with self.repo() as repo:
      repo.run("start", "S01")
      rows = [i["id"] for i in json.loads(repo.run("list", "--json")[1])]
      self.assertEqual(rows[0], "S01")

  def test_Handoff_ReviewingItem_SaysHowToFinishOrSendBack(self) -> None:
    with self.repo() as repo:
      repo.run("start", "S01")
      _, _, err = repo.run("handoff", "S01")
      self.assertIn("already being reviewed", err)
      self.assertIn("slicer done S01", err)

  def test_List_ReviewingLabel_KeepsColumnsAligned(self) -> None:
    with self.repo() as repo:
      repo.run("start", "S01")
      lines = repo.run("list")[1].splitlines()
      claim_at = lines[0].index("CLAIM")
      for line in lines[1:3]:
        self.assertEqual(line[claim_at - 1], " ", line)
        self.assertNotEqual(line[claim_at], " ", line)


class NextReviewTests(unittest.TestCase):
  """`next --status review`: the reviewer's one-call pickup (S151)."""

  SLICE = (
    "## build it\n\n"
    "### Files\n- src/x.py\n\n"
    "### Implement\nDo it.\n\n"
    "### Check\nRun the tests.\n"
  )

  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    _config(repo, claim_owner="Ada")
    repo.run("add", "build it", "--effort", "1")
    draft = repo.write("s01.md", self.SLICE)
    repo.run("promote", "S01", "--file", str(draft))
    repo.run("add", "other work", "--effort", "1")
    repo.run("start", "S01")
    repo.run("handoff", "S01")
    _config(repo, claim_owner="Bea")
    return repo

  def test_Ready_ReturnsTheReviewItemWithOnlyTheNamedSections(self) -> None:
    with self.repo() as repo:
      code, out, err = repo.run(
        "next", "--status", "review", "--ready", "--section", "Check", "--section", "Files", "--json"
      )
      self.assertEqual(code, 0, err)
      payload = json.loads(out)
      # The documented `--ready` shape, as plain `next --ready` returns it.
      self.assertEqual(set(payload), {"item", "slice", "blocked"})
      self.assertEqual(payload["item"]["id"], "S01")
      self.assertEqual([s["heading"] for s in payload["slice"]["sections"]], ["Files", "Check"])

  def test_Start_MovesToReviewing_AndALaterCallResumesIt(self) -> None:
    with self.repo() as repo:
      code, out, err = repo.run("next", "--status", "review", "--start", "--json")
      self.assertEqual(code, 0, err)
      payload = json.loads(out)
      self.assertEqual((payload["id"], payload["status"]), ("S01", "reviewing"))
      self.assertEqual(payload["claim"]["owner"], "Bea")
      again = json.loads(repo.run("next", "--status", "review", "--json")[1])
      self.assertEqual(again["id"], "S01")

  def test_ReviewItemWithUnfinishedDependency_IsBlocked(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S01", "--depends-on", "S02")
      code, out, _ = repo.run("next", "--status", "review", "--json")
      self.assertEqual(code, 2)
      payload = json.loads(out)
      self.assertIsNone(payload["item"])
      self.assertEqual(payload["blocked"], [{"id": "S01", "waiting_on": ["S02"]}])

  def test_EmptyReviewQueue_IsExit2WithNullItem(self) -> None:
    with self.repo() as repo:
      repo.run("done", "S01")
      code, out, _ = repo.run("next", "--status", "review", "--json")
      self.assertEqual(code, 2)
      self.assertIsNone(json.loads(out)["item"])
      self.assertIn("nothing in review", repo.run("next", "--status", "review")[1])

  def test_OtherStatus_IsAUsageError(self) -> None:
    with self.repo() as repo:
      code, out, _ = repo.run("next", "--status", "parked", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out)["error"]["code"], "usage")

  def test_NoReviewStatus_IsAUsageError(self) -> None:
    with self.repo() as repo:
      repo.run("done", "S01")
      _config(repo, review_status="")
      code, out, _ = repo.run("next", "--status", "review", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out)["error"]["code"], "usage")

  def test_PlainNext_IgnoresTheReviewQueue(self) -> None:
    with self.repo() as repo:
      self.assertEqual(json.loads(repo.run("next", "--json")[1])["id"], "S02")
