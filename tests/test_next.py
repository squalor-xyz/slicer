"""`slicer next` shows why an item is next and can start it in one shot."""

from __future__ import annotations

import json
import unittest

import support


class NextVerboseTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    return repo

  def test_Next_ShowsScoreAndStatus(self) -> None:
    with self.repo() as repo:
      repo.run("add", "a thing", "--importance", "3", "--urgency", "1")
      _, text, _ = repo.run("next")
      self.assertIn("score 31", text)
      payload = json.loads(repo.run("next", "--json")[1])
      self.assertEqual(payload["effective_score"], 31)

  def test_Next_InheritedScore_IsMarked(self) -> None:
    with self.repo() as repo:
      repo.run("add", "high", "--importance", "3", "--urgency", "3")  # S01
      repo.run("add", "low", "--importance", "1", "--urgency", "1")   # S02
      repo.run("set", "S01", "--depends-on", "S02")                    # S02 inherits 33, S01 blocked
      _, text, _ = repo.run("next")
      self.assertIn("S02", text)
      self.assertIn("score 33^", text)  # inherited from its dependent

  def test_Next_Start_MarksItStartedInOneShot(self) -> None:
    with self.repo() as repo:
      repo.run("add", "work")
      code, text, err = repo.run("next", "--start")
      self.assertEqual(code, 0, err)
      started = repo.state().config.started_status
      self.assertEqual(repo.state().index.require("S01").status, started)
      # idempotent: still returns and stays started
      self.assertEqual(repo.run("next", "--start")[0], 0)
      self.assertEqual(repo.state().index.require("S01").status, started)

  def test_Next_Start_NothingEligible_ChangesNothing(self) -> None:
    with self.repo() as repo:
      repo.run("add", "only")
      repo.run("done", "S01")
      before = repo.state().index.require("S01").status
      code, _, _ = repo.run("next", "--start")
      self.assertEqual(code, 2)
      self.assertEqual(repo.state().index.require("S01").status, before)

  def test_Next_Show_IncludesSliceEqualToShow(self) -> None:
    with self.repo() as repo:
      repo.run("add", "work")
      repo.run("promote", "S01")
      repo.run("edit", "S01", "--section", "Implement", "--text", "Do the thing.")
      repo.run("edit", "S01", "--section", "Check", "--text", "The thing works.")
      payload = json.loads(repo.run("next", "--show", "--json")[1])
      shown = json.loads(repo.run("show", "S01", "--json")[1])
      # The fused read equals the two-call composition: same slice object.
      self.assertEqual(payload["slice"], shown["slice"])

  def test_Next_StartShow_ReturnsStartedItemWithSlice(self) -> None:
    with self.repo() as repo:
      repo.run("add", "work")
      repo.run("promote", "S01")
      repo.run("edit", "S01", "--section", "Implement", "--text", "Do the thing.")
      repo.run("edit", "S01", "--section", "Check", "--text", "The thing works.")
      code, _, err = repo.run("next", "--start", "--show", "--json")
      self.assertEqual(code, 0, err)
      payload = json.loads(repo.run("next", "--show", "--json")[1])
      self.assertEqual(payload["status"], repo.state().config.started_status)
      self.assertIn("slice", payload)

  def test_Next_Show_NoSlice_OmitsSliceKey(self) -> None:
    with self.repo() as repo:
      repo.run("add", "work")  # a bare row, never promoted
      code, _, err = repo.run("next", "--show", "--json")
      self.assertEqual(code, 0, err)
      self.assertNotIn("slice", json.loads(repo.run("next", "--show", "--json")[1]))

  def test_Next_EmptyImplementOrCheck_IsSkippedAndNamed(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Needs a spec")
      repo.run("promote", "S01")
      repo.run("add", "Ready")
      repo.run("promote", "S02")
      repo.run("edit", "S02", "--section", "Implement", "--text", "Do the thing.")
      repo.run("edit", "S02", "--section", "Check", "--text", "The thing works.")
      code, out, err = repo.run("next", "--json")
      self.assertEqual(code, 0, err)
      payload = json.loads(out)
      self.assertEqual(payload["id"], "S02")
      self.assertEqual(payload["unspecified"], [{"id": "S01", "missing": ["Implement", "Check"]}])
      code, text, err = repo.run("next")
      self.assertEqual(code, 0, err)
      self.assertIn("skipped S01: Implement and Check are empty.", text)
      self.assertIn("`slicer edit S01 --section Implement`", text)
      self.assertIn("`slicer edit S01 --section Check`", text)
      self.assertEqual(repo.state().index.require("S01").status, "open")

  def test_Next_OnlyUnspecified_IsTheNoItemPath(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Needs a spec")
      repo.run("promote", "S01")
      repo.run("edit", "S01", "--section", "Implement", "--text", "Do the thing.")
      code, out, _ = repo.run("next", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out), {
        "item": None,
        "blocked": [],
        "unspecified": [{"id": "S01", "missing": ["Check"]}],
      })

  def test_Next_NoSlice_StaysEligible(self) -> None:
    with self.repo() as repo:
      repo.run("add", "bare row")
      code, out, err = repo.run("next", "--json")
      self.assertEqual(code, 0, err)
      payload = json.loads(out)
      self.assertEqual(payload["id"], "S01")
      self.assertNotIn("unspecified", payload)

  def test_Next_Show_NothingEligible_StillEmptyEnvelope(self) -> None:
    with self.repo() as repo:
      repo.run("add", "only")
      repo.run("done", "S01")
      code, out, _ = repo.run("next", "--show", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out), {"item": None, "blocked": []})


if __name__ == "__main__":
  unittest.main()
