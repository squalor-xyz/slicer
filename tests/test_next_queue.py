"""`next --status KEY` claims and resumes work in a custom status queue."""

from __future__ import annotations

import json
import unittest

import support
from slicer.store import DIR_NAME


def _config(repo: support.TempRepo, **changes: object) -> None:
  path = repo.root / DIR_NAME / "config.json"
  cfg = json.loads(path.read_text(encoding="utf-8"))
  cfg.update(changes)
  path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class NextQueueTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    cfg = json.loads((repo.root / DIR_NAME / "config.json").read_text(encoding="utf-8"))
    _config(repo, claim_owner="Ada", statuses=cfg["statuses"] | {"draft": "draft"})
    return repo

  def seed(self, repo: support.TempRepo, *specs: tuple[str, str]) -> None:
    """Add one item per (title, status), each scored 1 so queue order breaks ties."""
    for title, status in specs:
      code, _, err = repo.run("add", title, "--effort", "1", "--status", status)
      self.assertEqual(code, 0, err)

  def item(self, repo: support.TempRepo, item_id: str):
    return repo.state().index.require(item_id)

  def next(self, repo: support.TempRepo, *args: str):
    code, out, err = repo.run("next", "--status", "draft", "--json", *args)
    return code, (json.loads(out) if out.strip() else {}), err

  def log_actions(self, repo: support.TempRepo, item_id: str) -> list[str]:
    return [e["action"] for e in json.loads(repo.run("log", "--item", item_id, "--json")[1])]

  def test_Next_CustomQueue_DrawsOnlyFromThatStatus(self) -> None:
    with self.repo() as repo:
      self.seed(repo, ("plain", "open"), ("later one", "later"), ("drafted", "draft"))
      code, payload, _ = self.next(repo)
      self.assertEqual((code, payload["id"]), (0, "S03"))

  def test_Next_OwnClaimsRankBeforeUnclaimed_AndForeignClaimsAreReported(self) -> None:
    with self.repo() as repo:
      self.seed(repo, ("a", "draft"), ("b", "draft"), ("c", "draft"))
      repo.run("start", "S02", "--owner", "Ada")
      repo.run("set", "S02", "--status", "draft")
      repo.run("start", "S01", "--owner", "Bea")
      repo.run("set", "S01", "--status", "draft")
      code, payload, _ = self.next(repo)
      self.assertEqual((code, payload["id"]), (0, "S02"))
      self.assertEqual(payload["claimed_elsewhere"], [{"id": "S01", "owner": "Bea"}])
      text = repo.run("next", "--status", "draft")[1]
      self.assertIn("skipped S01 (claimed by Bea)", text)

  def test_Next_ReadOnlyResume_WritesNothing(self) -> None:
    with self.repo() as repo:
      self.seed(repo, ("a", "draft"))
      repo.run("next", "--status", "draft", "--start")
      before = (repo.root / DIR_NAME / "log.jsonl").read_bytes()
      code, payload, _ = self.next(repo, "--start")
      self.assertEqual((code, payload["id"]), (0, "S01"))
      self.assertEqual((repo.root / DIR_NAME / "log.jsonl").read_bytes(), before)

  def test_Start_ClaimsInPlace_WithoutStatusChangeOrAttempt(self) -> None:
    with self.repo() as repo:
      self.seed(repo, ("a", "draft"))
      code, payload, err = self.next(repo, "--start")
      self.assertEqual(code, 0, err)
      item = self.item(repo, "S01")
      self.assertEqual((item.status, item.claim_owner, item.attempts), ("draft", "Ada", 0))
      self.assertEqual(self.log_actions(repo, "S01")[0], "claim")

  def test_StartTo_Started_BeginsOneAttempt(self) -> None:
    with self.repo() as repo:
      self.seed(repo, ("a", "draft"))
      code, _, err = self.next(repo, "--start", "--start-to", "started")
      self.assertEqual(code, 0, err)
      item = self.item(repo, "S01")
      self.assertEqual((item.status, item.claim_owner, item.attempts), ("started", "Ada", 1))
      self.assertEqual(self.log_actions(repo, "S01")[0], "status")

  def test_StartTo_OpenAndCustomDestinations_CountNoAttempt(self) -> None:
    with self.repo() as repo:
      self.seed(repo, ("a", "draft"), ("b", "draft"))
      self.assertEqual(self.next(repo, "--start", "--start-to", "open")[0], 0)
      self.assertEqual(self.next(repo, "--start", "--start-to", "later")[0], 0)
      one, two = self.item(repo, "S01"), self.item(repo, "S02")
      self.assertEqual((one.status, one.attempts), ("open", 0))
      self.assertEqual((two.status, two.attempts), ("later", 0))

  def test_MaxAttempts_AppliesOnlyWhenStartingTo_Started(self) -> None:
    with self.repo() as repo:
      self.seed(repo, ("a", "draft"), ("b", "draft"))
      repo.run("set", "S01", "--attempts", "2")
      code, payload, _ = self.next(repo, "--max-attempts", "2")
      self.assertEqual((code, payload["id"]), (0, "S01"))
      self.assertNotIn("capped", payload)
      code, payload, _ = self.next(repo, "--max-attempts", "2", "--start", "--start-to", "started")
      self.assertEqual((code, payload["id"]), (0, "S02"))
      self.assertEqual(payload["capped"], [{"id": "S01", "attempts": 2, "max_attempts": 2}])

  def test_BadInputs_AreUsageErrorsThatChangeNothing(self) -> None:
    with self.repo() as repo:
      self.seed(repo, ("a", "draft"))
      before = {p: p.read_bytes() for p in (repo.root / DIR_NAME).rglob("*")
                if p.is_file() and p.name != "lock"}
      cases = [
        ("next", "--status", "nope", "--json"),
        ("next", "--status", "parked", "--json"),
        ("next", "--status", "done", "--json"),
        ("next", "--status", "started", "--json"),
        ("next", "--status", "draft", "--start", "--start-to", "done", "--json"),
        ("next", "--status", "draft", "--start", "--start-to", "parked", "--json"),
        ("next", "--status", "draft", "--start", "--start-to", "review", "--json"),
        ("next", "--status", "draft", "--start", "--start-to", "reviewing", "--json"),
        ("next", "--status", "draft", "--start", "--start-to", "nope", "--json"),
        ("next", "--status", "draft", "--start-to", "started", "--json"),
        ("next", "--start", "--start-to", "started", "--json"),
        ("next", "--status", "draft", "--owner", "Bea", "--json"),
        ("next", "--status", "draft", "--batch", "1", "--json"),
      ]
      for argv in cases:
        code, out, _ = repo.run(*argv)
        self.assertEqual((code, json.loads(out)["error"]["code"]), (2, "usage"), argv)
      after = {p: p.read_bytes() for p in (repo.root / DIR_NAME).rglob("*")
               if p.is_file() and p.name != "lock"}
      self.assertEqual(before, after)

  def test_UnknownQueue_NamesTheChoices(self) -> None:
    with self.repo() as repo:
      code, out, _ = repo.run("next", "--status", "nope", "--json")
      message = json.loads(out)["error"]["message"]
      self.assertEqual(code, 2)
      for choice in ("draft", "later", "review"):
        self.assertIn(choice, message)

  def test_StartTo_ReviewStatusQueue_IsRefused(self) -> None:
    with self.repo() as repo:
      code, out, _ = repo.run("next", "--status", "review", "--start", "--start-to", "started", "--json")
      self.assertEqual((code, json.loads(out)["error"]["code"]), (2, "usage"))

  def test_SecondPickup_NeverTakesTheSameItem(self) -> None:
    with self.repo() as repo:
      self.seed(repo, ("a", "draft"), ("b", "draft"))
      _config(repo, claim_owner="Ada")
      first = self.next(repo, "--start")[1]["id"]
      _config(repo, claim_owner="Bea")
      code, payload, _ = self.next(repo, "--start")
      self.assertEqual((first, code, payload["id"]), ("S01", 0, "S02"))
      self.assertEqual(payload["claimed_elsewhere"], [{"id": "S01", "owner": "Ada"}])
      code, payload, _ = self.next(repo, "--start")
      self.assertEqual((code, payload["id"]), (0, "S02"))

  def test_EmptyQueue_IsExitTwoWithNoItem(self) -> None:
    with self.repo() as repo:
      code, payload, _ = self.next(repo)
      self.assertEqual((code, payload["item"]), (2, None))
      self.assertIn("nothing in draft", repo.run("next", "--status", "draft")[1])

  def test_Review_AndPlainNext_AreUnchanged(self) -> None:
    with self.repo() as repo:
      self.seed(repo, ("plain", "open"), ("drafted", "draft"))
      payload = json.loads(repo.run("next", "--json")[1])
      self.assertEqual(payload["id"], "S01")
      code, out, _ = repo.run("next", "--status", "review", "--json")
      self.assertEqual(code, 2)
      self.assertIsNone(json.loads(out)["item"])


if __name__ == "__main__":
  unittest.main()
