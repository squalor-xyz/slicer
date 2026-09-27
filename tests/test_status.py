"""`slicer status` answers next / progress / blocked in one call."""

from __future__ import annotations

import json
import unittest

import support


class StatusTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    support.make_mini(repo)
    repo.run("init")
    repo.run("migrate", "--from", "docs/slices")
    return repo

  def test_Status_Text_ShowsNextProgressAndBlocked(self) -> None:
    with self.repo() as repo:
      code, out, err = repo.run("status")
      self.assertEqual((code, err), (0, ""))
      self.assertIn("Next", out)
      self.assertIn("Progress", out)
      self.assertIn("items", out)
      self.assertIn("Blocked", out)

  def test_Status_Json_CarriesNextCensusAndBlocked(self) -> None:
    with self.repo() as repo:
      code, out, _ = repo.run("status", "--json")
      self.assertEqual(code, 0)
      payload = json.loads(out)
      self.assertEqual(sorted(payload), ["blocked", "census", "next"])
      self.assertEqual(payload["census"]["total"], 4)
      self.assertIsNotNone(payload["next"])
      self.assertIn("id", payload["next"])

  def test_Status_WithADependency_ReportsTheBlockedEdge(self) -> None:
    with self.repo() as repo:
      # S03 is parked (not done), so S02 depending on it is blocked.
      repo.run("set", "S02", "--depends-on", "S03")
      code, out, _ = repo.run("status", "--json")
      blocked = json.loads(out)["blocked"]
      self.assertIn({"id": "S02", "waiting_on": ["S03"]}, blocked)
      self.assertIn("S02 waits on S03", repo.run("status")[1])

  def test_Status_Census_MatchesStats(self) -> None:
    with self.repo() as repo:
      census = json.loads(repo.run("status", "--json")[1])["census"]
      stats = json.loads(repo.run("stats", "--json")[1])
      self.assertEqual(census, stats)

  def test_Status_AllEligibleDone_ReportsNoNext(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "only thing")
      repo.run("done", "S01")
      payload = json.loads(repo.run("status", "--json")[1])
      self.assertIsNone(payload["next"])
      self.assertIn("nothing unmarked", repo.run("status")[1])


if __name__ == "__main__":
  unittest.main()
