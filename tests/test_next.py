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


if __name__ == "__main__":
  unittest.main()
