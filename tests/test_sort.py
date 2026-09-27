"""`slicer sort` persists the `list --sort score` ordering in one command."""

from __future__ import annotations

import json
import unittest

import support


class SortTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    support.make_mini(repo)
    repo.run("init")
    repo.run("migrate", "--from", "docs/slices")
    return repo

  def _ids(self, out: str) -> list[str]:
    return [i["id"] for i in json.loads(out)]

  def test_Sort_MakesStoredOrderMatchSortScore(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S04", "--importance", "3", "--urgency", "3")  # give scores some spread
      repo.run("set", "S02", "--importance", "1", "--urgency", "1")
      code, _, err = repo.run("sort")
      self.assertEqual(code, 0, err)
      stored = self._ids(repo.run("list", "--json")[1])
      by_score = self._ids(repo.run("list", "--sort", "score", "--json")[1])
      self.assertEqual(stored, by_score)

  def test_Sort_IsIdempotent_AndDoesNotRelog(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S04", "--importance", "3", "--urgency", "3")
      repo.run("sort")
      before = len(repo.state().history())
      code, out, _ = repo.run("sort", "--json")
      self.assertEqual(code, 0)
      self.assertEqual(json.loads(out), {"by": "score", "moved": 0})
      self.assertEqual(len(repo.state().history()), before)

  def test_Sort_Render_KeepsCheckGreen(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S04", "--importance", "3", "--urgency", "3")
      self.assertEqual(repo.run("sort", "--render")[0], 0)
      self.assertEqual(repo.run("check")[0], 0)


if __name__ == "__main__":
  unittest.main()
