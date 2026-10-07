"""`next --start --render` renders in the same step as the claim (s234)."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import support
from slicer.errors import RenderError
from slicer.store import DIR_NAME, INDEX_NAME


def _check_ok(repo: support.TempRepo) -> bool:
  return repo.run("check", "--json")[0] == 0


class NextRenderTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    for title, item_id in (("one", "S01"), ("two", "S02")):
      repo.run("add", title, "--effort", "1")
      repo.run("promote", item_id)
      for section in ("Implement", "Check"):
        repo.run("edit", item_id, "--section", section, "--text", "Do it.")
    return repo

  def test_Next_StartRender_ClaimsAndLeavesCheckPassing(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      code, out, err = repo.run("next", "--start", "--render", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out)["id"], "S01")
      self.assertEqual(repo.state().index.require("S01").status, "started")
      self.assertTrue(_check_ok(repo))

  def test_Next_StartWithoutRender_LeavesRenderStale(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      repo.run("next", "--start")
      self.assertFalse(_check_ok(repo))

  def test_Next_ReviewStartRender_MovesToReviewingAndLeavesCheckPassing(self) -> None:
    with self.repo() as repo:
      repo.run("start", "S01")
      repo.run("handoff", "S01", "--render")
      code, out, err = repo.run(
        "next", "--status", "review", "--start", "--ready", "--render", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out)["item"]["id"], "S01")
      self.assertEqual(repo.state().index.require("S01").status, "reviewing")
      self.assertTrue(_check_ok(repo))

  def test_Next_BatchStartRender_StartsEveryIdAndLeavesCheckPassing(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      code, out, err = repo.run("next", "--batch", "2", "--start", "--render", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual([i["id"] for i in json.loads(out)["items"]], ["S01", "S02"])
      for item_id in ("S01", "S02"):
        self.assertEqual(repo.state().index.require(item_id).status, "started")
      self.assertTrue(_check_ok(repo))

  def test_Next_RenderWithoutStart_IsUsageAndWritesNothing(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      index = repo.root / DIR_NAME / INDEX_NAME
      before = index.read_bytes()
      code, _, err = repo.run("next", "--render", "--json")
      self.assertEqual(code, 2)
      self.assertIn("--render on next requires --start", err + repo.run("next", "--render")[2])
      self.assertEqual(index.read_bytes(), before)

  def test_Next_StrictWithoutRender_IsUsage(self) -> None:
    with self.repo() as repo:
      code, _, _ = repo.run("next", "--start", "--strict")
      self.assertEqual(code, 2)
      self.assertEqual(repo.state().index.require("S01").status, "open")

  def test_Next_StartRender_NothingEligible_DoesNotRender(self) -> None:
    with self.repo() as repo:
      repo.run("done", "S01")
      repo.run("done", "S02")
      repo.run("render")
      render = repo.root / DIR_NAME / "render" / "ROADMAP.md"
      before = render.read_bytes()
      with patch("slicer.cli.render.plan", side_effect=AssertionError("rendered")):
        code, out, _ = repo.run("next", "--start", "--render", "--json")
      self.assertEqual(code, 2)
      self.assertIsNone(json.loads(out)["item"])
      self.assertEqual(render.read_bytes(), before)

  def test_Next_StartRender_ResumeOwnClaim_WritesNoLogAndDoesNotRender(self) -> None:
    with self.repo() as repo:
      repo.run("next", "--start", "--render")
      log = repo.root / DIR_NAME / "log.jsonl"
      before = log.read_bytes()
      with patch("slicer.cli.render.plan", side_effect=AssertionError("rendered")):
        code, _, err = repo.run("next", "--start", "--render", "--strict", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(log.read_bytes(), before)

  def test_Next_StartRenderStrict_RenderFailure_RollsBackTheClaim(self) -> None:
    with self.repo() as repo:
      index = repo.root / DIR_NAME / INDEX_NAME
      log = repo.root / DIR_NAME / "log.jsonl"
      before = (index.read_bytes(), log.read_bytes())
      with patch("slicer.cli.render.plan", side_effect=RenderError("boom")):
        code, out, _ = repo.run("next", "--start", "--render", "--strict")
      self.assertEqual(code, 2)
      self.assertEqual(out, "")
      self.assertEqual((index.read_bytes(), log.read_bytes()), before)
      self.assertEqual(repo.state().index.require("S01").status, "open")

  def test_Next_StartRenderStrictBatch_RenderFailure_ClaimsNothing(self) -> None:
    with self.repo() as repo:
      with patch("slicer.cli.render.plan", side_effect=RenderError("boom")):
        code, _, _ = repo.run("next", "--batch", "2", "--start", "--render", "--strict")
      self.assertEqual(code, 2)
      for item_id in ("S01", "S02"):
        self.assertEqual(repo.state().index.require(item_id).status, "open")


if __name__ == "__main__":
  unittest.main()
