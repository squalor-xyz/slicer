"""`slicer deps`: unblocked items, one item's edges, and a mermaid graph."""

from __future__ import annotations

import json
import unittest

import support


class DepsTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    support.make_mini(repo)
    repo.run("init")
    repo.run("migrate", "--from", "docs/slices")
    return repo

  def test_Deps_ForAnItem_ShowsWaitsOnBlockedAndDependents(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S02", "--depends-on", "S01", "--depends-on", "S03")  # S01 done, S03 parked
      payload = json.loads(repo.run("deps", "S02", "--json")[1])
      self.assertEqual(payload["waits_on"], ["S01", "S03"])
      self.assertEqual(payload["blocked_by"], ["S03"])
      self.assertEqual(payload["dependents"], [])
      s01 = json.loads(repo.run("deps", "S01", "--json")[1])
      self.assertIn("S02", s01["dependents"])
      self.assertIn("S03 (blocked)", repo.run("deps", "S02")[1])

  def test_Deps_NoId_ListsUnblockedOpenItems(self) -> None:
    with self.repo() as repo:
      ids = [i["id"] for i in json.loads(repo.run("deps", "--json")[1])]
      self.assertIn("S02", ids)  # open, no deps -> unblocked
      repo.run("set", "S02", "--depends-on", "S03")  # S03 parked -> now blocked
      ids = [i["id"] for i in json.loads(repo.run("deps", "--json")[1])]
      self.assertNotIn("S02", ids)

  def test_Deps_UnknownId_IsRefused(self) -> None:
    with self.repo() as repo:
      code, out, _ = repo.run("deps", "S99", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out)["error"]["code"], "no_such_item")

  def test_Deps_Mermaid_RendersEdgesAndFocus(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S02", "--depends-on", "S01")
      repo.run("set", "S04", "--depends-on", "S03")
      whole = repo.run("deps", "--format", "mermaid")[1]
      self.assertIn("graph TD", whole)
      self.assertIn("S02 --> S01", whole)
      self.assertIn("S04 --> S03", whole)
      focused = repo.run("deps", "S01", "--format", "mermaid")[1]
      self.assertIn("S02 --> S01", focused)
      self.assertNotIn("S04 --> S03", focused)  # unrelated edge excluded
      payload = json.loads(repo.run("deps", "--format", "mermaid", "--json")[1])
      self.assertEqual(payload["format"], "mermaid")
      self.assertIn("S02 --> S01", payload["graph"])


if __name__ == "__main__":
  unittest.main()
