"""Batch pickup stays inside one tree and size, and one failed start claims nothing."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import support

from slicer import ops, store
from slicer.cli import main
from slicer.errors import StateError


def _run(root: Path, *args: str) -> tuple[int, str, str]:
  out, err = io.StringIO(), io.StringIO()
  with redirect_stdout(out), redirect_stderr(err):
    code = main(["--root", str(root), *args])
  return code, out.getvalue(), err.getvalue()


def _ids(payload: dict) -> list[str]:
  return [item["id"] for item in payload["items"]]


class NextBatchTests(unittest.TestCase):
  def run_ok(self, repo: support.TempRepo, *args: str) -> str:
    code, out, err = repo.run(*args)
    self.assertEqual(code, 0, err)
    return out

  def sibling(self, repo: support.TempRepo, name: str) -> Path:
    root = repo.root / "siblings" / name
    root.parent.mkdir(exist_ok=True)
    made = repo._git("worktree", "add", "-q", "-b", name, str(root), "HEAD")
    self.assertEqual(made.returncode, 0, made.stderr)
    return root

  def test_NextBatch_TreeAndSize_ReturnsTheFirstThreeInNextOrder(self) -> None:
    with support.TempRepo() as repo:
      self.run_ok(repo, "init")
      self.run_ok(repo, "add", "other", "--importance", "3", "--size", "S", "--tree", "other")
      self.run_ok(repo, "add", "low", "--importance", "1", "--size", "S", "--tree", "core")
      self.run_ok(repo, "add", "medium elsewhere", "--importance", "3", "--size", "M", "--tree", "core")
      self.run_ok(repo, "add", "mid", "--importance", "2", "--size", "S", "--tree", "core")
      self.run_ok(repo, "add", "high", "--importance", "3", "--size", "S", "--tree", "core")
      self.run_ok(repo, "add", "low later", "--importance", "1", "--size", "S", "--tree", "core")
      self.run_ok(repo, "add", "low last", "--importance", "1", "--size", "S", "--tree", "core")
      code, out, err = repo.run("next", "--batch", "3", "--tree", "core", "--size", "S", "--json")
      self.assertEqual(code, 0, err)
      payload = json.loads(out)
      # Score order, then queue order: S05 (3), S04 (2), S02 (1). S06 and S07
      # are the same size and tree but past K, so they are not blocked.
      self.assertEqual(_ids(payload), ["S05", "S04", "S02"])
      self.assertEqual(payload["blocked"], [])
      self.assertNotIn("unspecified", payload)
      self.assertNotIn("error", payload)
      self.assertEqual(json.loads(self.run_ok(repo, "next", "--json"))["id"], "S01")

  def test_NextBatch_DependentFollowsBlocker_AndStaysBlockedWhenBlockerIsOut(self) -> None:
    with support.TempRepo() as repo:
      self.run_ok(repo, "init")
      self.run_ok(repo, "add", "blocker", "--importance", "1", "--size", "S", "--tree", "core")
      self.run_ok(
        repo, "add", "dependent", "--importance", "3", "--size", "S", "--tree", "core",
        "--depends-on", "S01",
      )
      # Started work ranks first. S02 still has to follow S01, because it waits on it.
      self.run_ok(repo, "start", "S02")
      payload = json.loads(self.run_ok(repo, "next", "--batch", "2", "--json"))
      self.assertEqual(_ids(payload), ["S01", "S02"])
      self.assertEqual(payload["items"][0]["status"], "open")
      self.assertEqual(payload["items"][1]["status"], "started")
      self.assertEqual(payload["blocked"], [])
      # K stops after the blocker. The dependent could be next, so it is not blocked.
      one = json.loads(self.run_ok(repo, "next", "--batch", "1", "--json"))
      self.assertEqual(_ids(one), ["S01"])
      self.assertEqual(one["blocked"], [])
      self.run_ok(repo, "set", "S01", "--size", "L")
      code, out, err = repo.run("next", "--batch", "2", "--size", "S", "--json")
      self.assertEqual(code, 2, err)
      payload = json.loads(out)
      self.assertEqual(payload["items"], [])
      self.assertEqual(payload["blocked"], [{"id": "S02", "waiting_on": ["S01"]}])
      self.assertNotIn("error", payload)

  def test_NextBatch_SiblingClaim_SkipsTheClaimAndKeepsTheDependentBlocked(self) -> None:
    with support.TempRepo(git=True) as repo:
      self.run_ok(repo, "init")
      self.run_ok(repo, "add", "blocker", "--size", "S", "--tree", "core")
      self.run_ok(repo, "add", "dependent", "--size", "S", "--tree", "core", "--depends-on", "S01")
      self.run_ok(repo, "add", "free", "--size", "S", "--tree", "core")
      repo.commit("base")
      sibling = self.sibling(repo, "other")
      code, _out, err = _run(sibling, "start", "S01")
      self.assertEqual(code, 0, err)
      code, out_text, err_text = repo.run("next", "--batch", "3", "--tree", "core", "--size", "S", "--json")
      self.assertEqual(code, 0, err_text)
      payload = json.loads(out_text)
      self.assertEqual(_ids(payload), ["S03"])
      self.assertEqual(payload["blocked"], [{"id": "S02", "waiting_on": ["S01"]}])
      self.assertEqual(
        payload["in_work_elsewhere"],
        [{"id": "S01", "worktree": "other", "owner": "Test", "status": "started"}],
      )

  def test_NextBatch_ReadySection_MatchesOneReadyCall(self) -> None:
    with support.TempRepo() as repo:
      self.run_ok(repo, "init")
      self.run_ok(repo, "add", "work")
      self.run_ok(repo, "add", "later", "--depends-on", "S01")
      self.run_ok(repo, "add", "parked blocker")
      self.run_ok(repo, "add", "held", "--depends-on", "S03")
      self.run_ok(repo, "park", "S03")
      self.run_ok(repo, "promote", "S01")
      self.run_ok(repo, "edit", "S01", "--section", "Implement", "--text", "Do the thing.")
      self.run_ok(repo, "edit", "S01", "--section", "Check", "--text", "The thing works.")
      single = json.loads(self.run_ok(
        repo, "next", "--ready", "--section", "Implement", "--json",
      ))
      payload = json.loads(self.run_ok(
        repo, "next", "--batch", "1", "--ready", "--section", "Implement", "--json",
      ))
      self.assertEqual(len(payload["items"]), 1)
      element = payload["items"][0]
      self.assertEqual(set(element), {"item", "slice"})
      self.assertEqual(element["item"], single["item"])
      self.assertEqual(element["slice"], single["slice"])
      self.assertIn("attempts", element["item"])
      self.assertEqual(
        [section["heading"] for section in element["slice"]["sections"]], ["Implement"],
      )
      # S02 waits on S01, which this batch took, so only the outside wait remains.
      self.assertEqual(payload["blocked"], [{"id": "S04", "waiting_on": ["S03"]}])
      code, out, err = repo.run(
        "next", "--batch", "1", "--ready", "--section", "Nope", "--start", "--json",
      )
      self.assertEqual(code, 2, err)
      self.assertEqual(json.loads(out)["error"]["code"], "no_such_section")
      item = repo.state().index.require("S01")
      self.assertEqual(item.status, "open")
      self.assertEqual(item.claim_owner, "")

  def test_NextBatch_Start_ClaimsEveryIdUnderOneLock(self) -> None:
    with support.TempRepo() as repo:
      self.run_ok(repo, "init")
      self.run_ok(repo, "add", "first")
      self.run_ok(repo, "add", "second")
      with patch("slicer.cli.store.project_lock", wraps=store.project_lock) as locked, \
           patch("slicer.ops.start_many", wraps=ops.start_many) as started:
        payload = json.loads(self.run_ok(
          repo, "next", "--batch", "2", "--start", "--owner", "Ada", "--json",
        ))
      self.assertEqual(locked.call_count, 1)
      self.assertEqual(started.call_count, 1)
      self.assertEqual(started.call_args.args[1], ["S01", "S02"])
      self.assertEqual(started.call_args.kwargs["owner"], "Ada")
      self.assertEqual(_ids(payload), ["S01", "S02"])
      self.assertEqual([item["status"] for item in payload["items"]], ["started", "started"])
      state = repo.state()
      for item_id in ("S01", "S02"):
        item = state.index.require(item_id)
        self.assertEqual(item.status, "started")
        self.assertEqual(item.claim_owner, "Ada")
        self.assertEqual(item.attempts, 1)

  def test_NextBatch_StartFailsOnSecondRecord_LeavesEveryIdUnclaimed(self) -> None:
    with support.TempRepo() as repo:
      self.run_ok(repo, "init")
      self.run_ok(repo, "add", "first")
      self.run_ok(repo, "add", "second")
      for item_id in ("S01", "S02"):
        self.run_ok(repo, "promote", item_id)
        self.run_ok(repo, "edit", item_id, "--section", "Implement", "--text", "Do it.")
        self.run_ok(repo, "edit", item_id, "--section", "Check", "--text", "It works.")
      before_paths = {item_id: repo.state().find_slice_file(item_id) for item_id in ("S01", "S02")}
      before_log = [entry.to_dict() for entry in repo.state().history()]
      real = ops._record

      def boom(state, item, action, frm="", to="", note="", by=""):
        if action == "status" and item == "S02":
          raise StateError("history failed", code="io")
        return real(state, item, action, frm=frm, to=to, note=note, by=by)

      with patch("slicer.ops._record", side_effect=boom):
        code, _out, _err = repo.run("next", "--batch", "2", "--start", "--owner", "Ada", "--json")
      self.assertEqual(code, 3)
      state = repo.state()
      for item_id in ("S01", "S02"):
        item = state.index.require(item_id)
        self.assertEqual(item.status, "open")
        self.assertEqual(item.claim_owner, "")
        self.assertEqual(item.attempts, 0)
        self.assertEqual(state.find_slice_file(item_id), before_paths[item_id])
      self.assertEqual([entry.to_dict() for entry in state.history()], before_log)

  def test_NextBatch_Empty_ExitsWithItemsAndBlocked(self) -> None:
    with support.TempRepo() as repo:
      self.run_ok(repo, "init")
      self.run_ok(repo, "add", "blocker")
      self.run_ok(repo, "add", "dependent", "--depends-on", "S01")
      self.run_ok(repo, "park", "S01")
      code, out, err = repo.run("next", "--batch", "3", "--json")
      self.assertEqual(code, 2, err)
      payload = json.loads(out)
      self.assertEqual(payload, {
        "items": [],
        "blocked": [{"id": "S02", "waiting_on": ["S01"]}],
      })
      self.assertNotIn("error", payload)

  def test_Next_TreeAndSizeWithoutBatch_NarrowTheSingleItem(self) -> None:
    with support.TempRepo() as repo:
      self.run_ok(repo, "init")
      self.run_ok(repo, "add", "other", "--importance", "3", "--size", "S", "--tree", "other")
      self.run_ok(repo, "add", "wanted", "--importance", "1", "--size", "S", "--tree", "core")
      self.run_ok(repo, "add", "medium", "--importance", "3", "--size", "M", "--tree", "core")
      self.run_ok(repo, "add", "second", "--importance", "1", "--size", "S", "--tree", "core")
      self.assertEqual(json.loads(self.run_ok(repo, "next", "--json"))["id"], "S01")
      self.assertEqual(
        json.loads(self.run_ok(repo, "next", "--tree", "core", "--size", "S", "--json"))["id"],
        "S02",
      )
      self.assertEqual(
        json.loads(self.run_ok(repo, "next", "-n", "1", "--tree", "core", "--size", "S", "--json"))["id"],
        "S04",
      )
      self.assertEqual(
        repo.run("next", "--tree", "core", "--size", "S"),
        repo.run("next", "-n", "0", "--tree", "core", "--size", "S"),
      )

  def test_NextBatch_ShowOffsetStatusAndSmallK_AreUsage(self) -> None:
    with support.TempRepo() as repo:
      self.run_ok(repo, "init")
      argparse_cases = [("--batch", "0"), ("--batch", "-1"), ("--batch", "1.5")]
      handler_cases = [
        ("--batch", "1", "--show"),
        ("--batch", "1", "-n", "0"),
        ("--batch", "1", "-n", "1"),
        ("--batch", "1", "--status", "review"),
        ("--batch", "1", "--owner", "Ada"),
      ]
      for args in argparse_cases + handler_cases:
        for json_args in ((), ("--json",)):
          with self.subTest(args=args, json=json_args):
            code, out, err = repo.run("next", *json_args, *args)
            self.assertEqual(code, 2)
            if args in argparse_cases:
              self.assertIn("usage:", err)
            else:
              self.assertIn("slicer:", err)
            if json_args:
              self.assertEqual(json.loads(out)["error"]["code"], "usage")
            else:
              self.assertEqual(out, "")


if __name__ == "__main__":
  unittest.main()
