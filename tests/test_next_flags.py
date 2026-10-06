"""Flag patterns bound pickups without satisfying excluded dependencies."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout

import support
from slicer import ops
from slicer.cli import build_parser
from slicer.errors import StateError


class NextFlagTests(unittest.TestCase):
  def run_ok(self, repo: support.TempRepo, *args: str) -> str:
    code, out, err = repo.run(*args)
    self.assertEqual(code, 0, err)
    return out

  def add(self, repo: support.TempRepo, title: str, *flags: str, **fields: str) -> str:
    item = json.loads(self.run_ok(repo, "add", title, "--json"))["id"]
    args = ["set", item]
    for flag in flags:
      args.extend(["--flag", flag])
    for key, value in fields.items():
      args.extend(["--" + key.replace("_", "-"), value])
    self.run_ok(repo, *args)
    return item

  def batch_ids(self, repo: support.TempRepo, *filters: str) -> list[str]:
    code, out, err = repo.run("next", "--batch", "20", *filters, "--json")
    self.assertIn(code, (0, 2), err)
    return [item["id"] for item in json.loads(out)["items"]]

  def specify(self, repo: support.TempRepo, item: str) -> None:
    self.run_ok(repo, "promote", item)
    for section in ("Implement", "Check"):
      self.run_ok(repo, "edit", item, "--section", section, "--text", "Specified.")

  def test_NextFlags_Patterns_MatchCaseSensitivelyWithExclusionPrecedence(self) -> None:
    with support.TempRepo() as repo:
      self.run_ok(repo, "init")
      self.add(repo, "untagged")
      self.add(repo, "risk", "risk-api", "safe")
      self.add(repo, "uppercase", "Risk-api")
      self.add(repo, "short", "tag-a")
      self.add(repo, "long", "tag-aa")
      self.add(repo, "literal", "risk-*")
      cases = [
        ((), ["S01", "S02", "S03", "S04", "S05", "S06"]),
        (("--flag", "safe"), ["S02"]),
        (("--flag", "risk-*"), ["S02", "S06"]),
        (("--flag", "tag-?"), ["S04"]),
        (("--flag", "[rR]isk-api"), ["S02", "S03"]),
        (("--flag", "safe", "--flag", "tag-?"), ["S02", "S04"]),
        (("--flag", "*", "--no-flag", "risk-*", "--no-flag", "tag-*"), ["S03"]),
        (("--no-flag", "risk-*"), ["S01", "S03", "S04", "S05"]),
      ]
      for args, expected in cases:
        with self.subTest(args=args):
          self.assertEqual(self.batch_ids(repo, *args), expected)
      # list retains its literal interpretation of the very same argument.
      listed = json.loads(self.run_ok(repo, "list", "--flag", "risk-*", "--json"))
      self.assertEqual([item["id"] for item in listed], ["S06"])

  def test_NextFlags_OffsetAndCapacity_FilterBeforeSelection(self) -> None:
    with support.TempRepo() as repo:
      self.run_ok(repo, "init")
      self.add(repo, "risky", "risk-db")
      self.add(repo, "first safe")
      self.add(repo, "second safe")
      args = ("--no-flag", "risk-*")
      self.assertEqual(json.loads(self.run_ok(repo, "next", *args, "--json"))["id"], "S02")
      self.assertEqual(json.loads(self.run_ok(repo, "next", *args, "-n", "1", "--json"))["id"], "S03")
      batch = json.loads(self.run_ok(repo, "next", *args, "--batch", "2", "--json"))
      self.assertEqual([item["id"] for item in batch["items"]], ["S02", "S03"])
      code, out, _ = repo.run("next", *args, "-n", "2", "--json")
      self.assertEqual(code, 2)
      self.assertIsNone(json.loads(out)["item"])

  def test_NextFlags_ExcludedBlocker_LeavesDependentBlockedInSingleAndBatch(self) -> None:
    with support.TempRepo() as repo:
      self.run_ok(repo, "init")
      self.add(repo, "blocker", "risk-db")
      self.add(repo, "dependent", depends_on="S01", importance="3")
      self.add(repo, "free", importance="1")
      for mode in (("--ready",), ("--batch", "3")):
        payload = json.loads(self.run_ok(repo, "next", *mode, "--no-flag", "risk-*", "--json"))
        selected = [item["id"] for item in payload["items"]] if "items" in payload else [payload["item"]["id"]]
        self.assertEqual(selected, ["S03"])
        self.assertEqual(payload["blocked"], [{"id": "S02", "waiting_on": ["S01"]}])
      # The excluded dependent still raises its blocker's effective score.
      self.run_ok(repo, "set", "S01", "--flag", "safe", "--importance", "1")
      self.run_ok(repo, "set", "S02", "--flag", "risk-db")
      result = json.loads(self.run_ok(repo, "next", "--no-flag", "risk-*", "--json"))
      self.assertEqual((result["id"], result["effective_score"]), ("S01", 32))

  def test_NextFlags_ExcludedDiagnostics_AreAbsentBeforeSkipClassification(self) -> None:
    with support.TempRepo() as repo:
      self.run_ok(repo, "init")
      self.add(repo, "blocker", "risk-db")
      self.add(repo, "blocked", "risk-db", depends_on="S01")
      self.add(repo, "unspecified", "risk-db")
      self.run_ok(repo, "promote", "S03")
      self.add(repo, "capped", "risk-db", attempts="2")
      self.add(repo, "elsewhere", "risk-db")
      self.add(repo, "safe")
      elsewhere = {"S05": [{"worktree": "other", "owner": "Ada", "status": "started"}]}
      for select in (ops.next_item, lambda state, **kw: ops.next_batch(state, 10, **kw)):
        result = select(repo.state(), elsewhere=elsewhere, no_flags=["risk-*"], max_attempts=2)
        self.assertEqual(result.blocked, [])
        self.assertEqual(result.unspecified, [])
        self.assertEqual(result.capped, [])
        self.assertEqual(result.elsewhere, [])

  def test_NextFlags_ReadyTreeSizeCapAndStart_SelectTheSamePool(self) -> None:
    with support.TempRepo() as repo:
      self.run_ok(repo, "init")
      self.add(repo, "risk", "risk-api", tree="core", size="S")
      self.add(repo, "other tree", "safe", tree="other", size="S")
      self.add(repo, "other size", "safe", tree="core", size="M")
      self.add(repo, "capped", "safe", tree="core", size="S", attempts="2")
      self.add(repo, "wanted", "safe", tree="core", size="S")
      self.add(repo, "later", "safe", tree="core", size="S")
      for item in ("S05", "S06"):
        self.specify(repo, item)
      filters = ("--flag", "safe", "--no-flag", "risk-*", "--tree", "core", "--size", "S", "--max-attempts", "2", "--ready", "--section", "Check", "--json")
      single = json.loads(self.run_ok(repo, "next", *filters))
      self.assertEqual(single["item"]["id"], "S05")
      self.assertEqual([s["heading"] for s in single["slice"]["sections"]], ["Check"])
      started = json.loads(self.run_ok(repo, "next", *filters, "--start", "--owner", "Ada"))
      self.assertEqual(started["item"]["id"], single["item"]["id"])
      batch = json.loads(self.run_ok(repo, "next", *filters, "--batch", "2"))
      claimed = json.loads(self.run_ok(repo, "next", *filters, "--batch", "2", "--start"))
      self.assertEqual([i["item"]["id"] for i in claimed["items"]], [i["item"]["id"] for i in batch["items"]])
      self.assertEqual([i["item"]["id"] for i in claimed["items"]], ["S05", "S06"])
      self.assertEqual(repo.state().index.require("S01").status, "open")

  def test_NextFlags_ReviewAndResume_RespectFilters(self) -> None:
    with support.TempRepo() as repo:
      self.run_ok(repo, "init")
      self.add(repo, "risk", "risk-api")
      self.add(repo, "safe", "safe")
      for item in ("S01", "S02"):
        self.specify(repo, item)
        self.run_ok(repo, "start", item)
        self.run_ok(repo, "handoff", item)
      filters = ("--status", "review", "--no-flag", "risk-*", "--max-attempts", "1", "--ready", "--section", "Check", "--json")
      selected = json.loads(self.run_ok(repo, "next", *filters))
      started = json.loads(self.run_ok(repo, "next", *filters, "--start"))
      self.assertEqual(selected["item"]["id"], "S02")
      self.assertEqual(started["item"]["id"], "S02")
      self.assertEqual(started["item"]["status"], "reviewing")
      self.assertEqual(json.loads(self.run_ok(repo, "next", *filters))["item"]["id"], "S02")
      self.assertEqual(repo.state().index.require("S01").status, "review")

  def test_NextFlags_EmptyPatterns_AreUsageAndNeverStart(self) -> None:
    with support.TempRepo() as repo:
      self.run_ok(repo, "init")
      self.add(repo, "work")
      before = repo.read(".slicer/index.json")
      for flag in ("--flag", "--no-flag"):
        for mode in ((), ("--batch", "2")):
          for output in ((), ("--json",)):
            code, out, err = repo.run("next", flag, "", *mode, *output, "--start")
            self.assertEqual(code, 2, err)
            if output:
              self.assertEqual(json.loads(out)["error"]["code"], "usage")
            else:
              self.assertEqual(out, "")
            self.assertEqual(repo.read(".slicer/index.json"), before)
      for select in (ops.next_item, lambda state, **kw: ops.next_batch(state, 2, **kw)):
        with self.assertRaises(StateError) as caught:
          select(repo.state(), flags=[""])
        self.assertEqual(caught.exception.code, "usage")

  def test_NextFlags_Help_ShowsQuotedPatterns(self) -> None:
    out = io.StringIO()
    with redirect_stdout(out), self.assertRaises(SystemExit) as caught:
      build_parser().parse_args(["next", "--help"])
    self.assertEqual(caught.exception.code, 0)
    help_text = " ".join(out.getvalue().split())
    self.assertIn("--flag 'risk-*'", help_text)
    self.assertIn("--no-flag 'risk-*'", help_text)
