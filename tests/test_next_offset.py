"""Looking ahead must preserve eligibility and leave roadmap state untouched."""

from __future__ import annotations

import json
import unittest

import support
from slicer import ops
from slicer.errors import StateError


class NextOffsetTests(unittest.TestCase):
  def run_ok(self, repo, *args):
    code, out, err = repo.run(*args)
    self.assertEqual(code, 0, err)
    return out

  def test_Next_Offsets_OrderStartedThenOpenWithStablePriorityTies(self):
    with support.TempRepo() as repo:
      self.run_ok(repo, "init")
      for title, importance in [("low started", "1"), ("high open", "3"),
                                ("high started", "2"), ("tied open", "3")]:
        self.run_ok(repo, "add", title, "--importance", importance)
      self.run_ok(repo, "start", "S01", "S03")
      self.run_ok(repo, "promote", "S03")
      before = {str(p): p.read_bytes() for p in (repo.root / ".slicer").rglob("*") if p.is_file()}
      for offset, item_id in enumerate(["S03", "S01", "S02", "S04"]):
        payload = json.loads(self.run_ok(repo, "next", "-n", str(offset), "--json"))
        self.assertEqual(payload["id"], item_id)
        if offset == 0:
          self.assertTrue(payload["path"].endswith("S03.json"))
        else:
          self.assertIsNone(payload["path"])
      self.assertEqual(repo.run("next"), repo.run("next", "-n", "0"))
      self.assertEqual(repo.run("next", "--json"), repo.run("next", "-n", "0", "--json"))
      self.assertTrue(self.run_ok(repo, "next", "-n", "1").startswith("S01  low started"))
      after = {str(p): p.read_bytes() for p in (repo.root / ".slicer").rglob("*") if p.is_file()}
      self.assertEqual(before, after)

  def test_Next_SkippingBlocker_DoesNotUnblockDependent(self):
    with support.TempRepo() as repo:
      self.run_ok(repo, "init")
      self.run_ok(repo, "add", "blocker", "--importance", "1")
      self.run_ok(repo, "add", "critical dependent", "--importance", "3", "--depends-on", "S01")
      self.run_ok(repo, "add", "independent")
      self.run_ok(repo, "start", "S02")
      for title in ["done", "parked", "retired"]:
        self.run_ok(repo, "add", title, "--importance", "3")
      self.run_ok(repo, "done", "S04")
      self.run_ok(repo, "park", "S05")
      self.run_ok(repo, "remove", "S06", "--reason", "not needed")
      self.assertEqual(json.loads(self.run_ok(repo, "next", "--json"))["id"], "S01")
      self.assertEqual(json.loads(self.run_ok(repo, "next", "-n", "1", "--json"))["id"], "S03")
      code, out, _ = repo.run("next", "-n", "2", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out), {"item": None, "blocked": [{"id": "S02", "waiting_on": ["S01"]}]})
      code, out, _ = repo.run("next", "-n", "2")
      self.assertEqual(code, 2)
      self.assertIn("offset 2", out)
      self.assertIn("blocked S02 waits on S01", out)

  def test_Next_EmptyOrExhausted_ReturnsEmptyResult(self):
    with support.TempRepo() as repo:
      self.run_ok(repo, "init")
      for offset in ["0", "1", "999999999999999999999"]:
        code, out, _ = repo.run("next", "-n", offset, "--json")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out), {"item": None, "blocked": []})
      self.assertEqual(repo.run("next")[1], "nothing unmarked\n")
      self.run_ok(repo, "add", "only item")
      code, out, _ = repo.run("next", "-n", "1")
      self.assertEqual(code, 2)
      self.assertEqual(out, "no eligible item at offset 1\n")
      with self.assertRaises(StateError) as caught:
        ops.next_item(repo.state(), -1)
      self.assertEqual(caught.exception.code, "usage")

  def test_Next_InvalidOffset_ReportsUsageInTextAndJson(self):
    with support.TempRepo() as repo:
      for args in [("-n", "-1"), ("-n", "1.5"), ("-n", "word"), ("-n",)]:
        for json_args in [(), ("--json",)]:
          with self.subTest(args=args, json=json_args):
            code, out, err = repo.run("next", *json_args, *args)
            self.assertEqual(code, 2)
            self.assertIn("usage:", err)
            if json_args:
              self.assertEqual(json.loads(out)["error"]["code"], "usage")
            else:
              self.assertEqual(out, "")
