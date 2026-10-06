"""An attempt cap must stop fresh pickups without stranding active work."""

from __future__ import annotations

import json
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import support
from slicer import ops, store
from slicer.errors import StateError


class NextAttemptsTests(unittest.TestCase):
  def run_ok(self, repo, *args):
    code, out, err = repo.run(*args)
    self.assertEqual(code, 0, err)
    return out

  def snapshot(self, repo):
    return {str(p): p.read_bytes() for p in (repo.root / ".slicer").rglob("*")
            if p.is_file() and p.name != "lock"}

  def test_Next_CappedItems_FilterBeforeOffsetsAndReportQueueOrder(self):
    with support.TempRepo() as repo:
      self.run_ok(repo, "init")
      for title in ("capped", "fresh", "over cap", "also fresh"):
        self.run_ok(repo, "add", title)
      self.run_ok(repo, "set", "S01", "--attempts", "2")
      self.run_ok(repo, "set", "S03", "--attempts", "3")
      before = self.snapshot(repo)
      for offset, expected in (("0", "S02"), ("1", "S04")):
        for profile in ((), ("--ready",), ("--show",)):
          payload = json.loads(self.run_ok(
            repo, "next", "--max-attempts", "2", "-n", offset, "--json", *profile,
          ))
          item = payload["item"] if "--ready" in profile else payload
          self.assertEqual(item["id"], expected)
          self.assertEqual(payload["capped"], [
            {"id": "S01", "attempts": 2, "max_attempts": 2},
            {"id": "S03", "attempts": 3, "max_attempts": 2},
          ])
      self.assertEqual(before, self.snapshot(repo))
      self.assertIn("skipped S01 (attempts 2 >= max_attempts 2)",
                    self.run_ok(repo, "next", "--max-attempts", "2"))
      self.assertNotIn("capped", json.loads(self.run_ok(repo, "next", "--json", "--lean")))

  def test_Next_StartAtBoundary_ReleaseAndResumeKeepAttempt(self):
    with support.TempRepo() as repo:
      self.run_ok(repo, "init")
      self.run_ok(repo, "add", "work")
      self.run_ok(repo, "set", "S01", "--attempts", "1")
      payload = json.loads(self.run_ok(
        repo, "next", "--max-attempts", "2", "--start", "--owner", "Ada", "--json",
      ))
      self.assertEqual(payload["fields"]["attempts"], 2)
      self.run_ok(repo, "release", "S01", "--owner", "Ada")
      for start in ((), ("--start", "--owner", "Ada")):
        payload = json.loads(self.run_ok(repo, "next", "--max-attempts", "1", "--json", *start))
        self.assertEqual(payload["id"], "S01")
        self.assertEqual(payload["fields"]["attempts"], 2)
        self.assertNotIn("capped", payload)
      before = self.snapshot(repo)
      self.run_ok(repo, "next", "--max-attempts", "1", "--start", "--owner", "Ada")
      self.assertEqual(before, self.snapshot(repo))

  def test_NextBatch_CappedBlockers_LeaveDependentsBlockedAndFillOtherSlots(self):
    with support.TempRepo() as repo:
      self.run_ok(repo, "init")
      self.run_ok(repo, "add", "blocker")
      self.run_ok(repo, "add", "dependent", "--depends-on", "S01")
      self.run_ok(repo, "add", "free")
      self.run_ok(repo, "set", "S01", "S02", "--attempts", "2")
      expected = [{"id": i, "attempts": 2, "max_attempts": 2} for i in ("S01", "S02")]
      for start in ((), ("--start", "--owner", "Ada")):
        payload = json.loads(self.run_ok(
          repo, "next", "--batch", "3", "--max-attempts", "2", "--json", *start,
        ))
        self.assertEqual([i["id"] for i in payload["items"]], ["S03"])
        self.assertEqual(payload["capped"], expected)
        self.assertEqual(payload["blocked"], [{"id": "S02", "waiting_on": ["S01"]}])
      self.assertEqual(repo.state().index.require("S01").status, "open")
      self.run_ok(repo, "set", "S02", "--attempts", "0")
      payload = json.loads(self.run_ok(repo, "next", "--batch", "3", "--max-attempts", "2", "--json"))
      self.assertEqual([i["id"] for i in payload["items"]], ["S03"])
      self.assertEqual(payload["blocked"], [{"id": "S02", "waiting_on": ["S01"]}])

  def test_Next_WhollyCapped_ReturnsEmptyWithoutWrites(self):
    with support.TempRepo() as repo:
      self.run_ok(repo, "init")
      self.run_ok(repo, "add", "work")
      self.run_ok(repo, "set", "S01", "--attempts", "1")
      before = self.snapshot(repo)
      for batch in ((), ("--batch", "2")):
        for start in ((), ("--start",)):
          for lean in ((), ("--lean",)):
            code, out, err = repo.run("next", "--max-attempts", "1", "--json", *batch, *start, *lean)
            self.assertEqual(code, 2, err)
            payload = json.loads(out)
            self.assertEqual(payload.get("items", []) if batch else payload["item"], [] if batch else None)
            self.assertEqual(payload["capped"], [{"id": "S01", "attempts": 1, "max_attempts": 1}])
            self.assertNotIn("error", payload)
      self.assertEqual(before, self.snapshot(repo))

  def test_Next_InvalidCap_RejectsBeforeMutation(self):
    with support.TempRepo() as repo:
      self.run_ok(repo, "init")
      self.run_ok(repo, "add", "work")
      before = self.snapshot(repo)
      for value in ("0", "-1", "word", "1.5"):
        code, out, err = repo.run("next", "--max-attempts", value, "--start", "--json")
        self.assertEqual(code, 2, err)
        self.assertEqual(json.loads(out)["error"]["code"], "usage")
      for value in (0, -1, True, "1", 1.5):
        for select, args in ((ops.next_item, ()), (ops.next_batch, (2,))):
          with self.assertRaises(StateError) as caught:
            select(repo.state(), *args, max_attempts=value)
          self.assertEqual(caught.exception.code, "usage")
      self.assertEqual(before, self.snapshot(repo))

  def test_Next_ReviewPickup_CapDoesNotLimitClaims(self):
    with support.TempRepo() as repo:
      self.run_ok(repo, "init")
      self.run_ok(repo, "add", "review work")
      self.run_ok(repo, "set", "S01", "--status", "review", "--attempts", "4")
      for start in ((), ("--start",)):
        payload = json.loads(self.run_ok(
          repo, "next", "--status", "review", "--max-attempts", "1", "--json", *start,
        ))
        self.assertEqual(payload["id"], "S01")
        self.assertEqual(payload["fields"]["attempts"], 4)
        self.assertNotIn("capped", payload)

  def test_Next_CappedAtomicStart_LoadsAfterInterveningWriter(self):
    with support.TempRepo() as repo:
      self.run_ok(repo, "init")
      self.run_ok(repo, "add", "initially eligible")
      self.run_ok(repo, "add", "still eligible")
      real_lock = store.project_lock

      @contextmanager
      def intervening_writer(root):
        with real_lock(root):
          state = store.load(root)
          ops.set_fields(state, "S01", attempts=1)
          yield

      for batch in ((), ("--batch", "2")):
        with patch("slicer.cli.store.project_lock", side_effect=intervening_writer):
          payload = json.loads(self.run_ok(
            repo, "next", "--max-attempts", "1", "--start", "--owner", "Ada", "--json", *batch,
          ))
        picked = payload["items"] if batch else [payload]
        self.assertEqual([i["id"] for i in picked], ["S02"])
        self.assertEqual(repo.state().index.require("S01").status, "open")


if __name__ == "__main__":
  unittest.main()
