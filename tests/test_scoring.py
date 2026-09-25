"""Priority scoring: importance x urgency as settable, sortable data (S38)."""

from __future__ import annotations

import json
import unittest

import support

from slicer.model import Item


class ScoreModelTests(unittest.TestCase):
  def item(self, imp: int, urg: int) -> Item:
    return Item(id="S01", title="t", status="open", importance=imp, urgency=urg)

  def test_Score_ImportanceLeads(self) -> None:
    self.assertEqual(self.item(3, 1).score, 31)
    self.assertEqual(self.item(1, 3).score, 13)
    self.assertGreater(self.item(3, 1).score, self.item(1, 3).score)  # Q2 > Q3

  def test_Score_Default_IsNeutralTwentyTwo(self) -> None:
    self.assertEqual(Item(id="S01", title="t", status="open").score, 22)

  def test_Quadrant_Derivation(self) -> None:
    self.assertEqual(self.item(3, 3).quadrant, "do-now")
    self.assertEqual(self.item(3, 1).quadrant, "schedule")
    self.assertEqual(self.item(1, 3).quadrant, "delegate")
    self.assertEqual(self.item(1, 1).quadrant, "drop")

  def test_Quadrant_NeutralMidpoint_IsUnlabelled(self) -> None:
    # A 2 on either axis has no clean high/low, so no quadrant is claimed --
    # in particular the default 2/2 is not called "do-now".
    self.assertEqual(self.item(2, 2).quadrant, "-")
    self.assertEqual(self.item(3, 2).quadrant, "-")
    self.assertEqual(self.item(2, 1).quadrant, "-")

  def test_Score_RoundTripsThroughSerialization(self) -> None:
    d = self.item(3, 1).to_dict()
    self.assertEqual(d["fields"]["importance"], 3)
    self.assertEqual(d["fields"]["urgency"], 1)
    self.assertEqual(Item.from_dict(d).score, 31)


class ScoreCliTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    return repo

  def test_Add_WithScores_StoresThem(self) -> None:
    with self.repo() as repo:
      repo.run("add", "a thing", "--importance", "3", "--urgency", "1")
      item = repo.state().index.require("S01")
      self.assertEqual((item.importance, item.urgency), (3, 1))
      self.assertEqual(item.score, 31)

  def test_Add_WithoutScores_DefaultsToTwoTwo(self) -> None:
    with self.repo() as repo:
      repo.run("add", "a thing")
      self.assertEqual(repo.state().index.require("S01").score, 22)

  def test_Add_OutOfRange_IsRefused(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("add", "a thing", "--importance", "5")
      self.assertEqual(code, 2)
      self.assertIn("1, 2 or 3", err)

  def test_Set_Scores_UpdateAndValidate(self) -> None:
    with self.repo() as repo:
      repo.run("add", "a thing")
      repo.run("set", "S01", "--importance", "1", "--urgency", "3")
      self.assertEqual(repo.state().index.require("S01").score, 13)
      self.assertEqual(repo.run("set", "S01", "--urgency", "0")[0], 2)

  def test_List_SortScore_OrdersHighestFirst(self) -> None:
    with self.repo() as repo:
      repo.run("add", "low", "--importance", "1", "--urgency", "1")   # S01 = 11
      repo.run("add", "high", "--importance", "3", "--urgency", "3")  # S02 = 33
      repo.run("add", "mid")                                          # S03 = 22
      _, out, _ = repo.run("list", "--sort", "score", "--json")
      self.assertEqual([i["id"] for i in json.loads(out)], ["S02", "S03", "S01"])

  def test_List_WithoutSort_KeepsManualOrder(self) -> None:
    with self.repo() as repo:
      repo.run("add", "low", "--importance", "1", "--urgency", "1")
      repo.run("add", "high", "--importance", "3", "--urgency", "3")
      _, out, _ = repo.run("list", "--json")
      self.assertEqual([i["id"] for i in json.loads(out)], ["S01", "S02"])

  def test_List_TextShowsScoreAndQuadrant(self) -> None:
    with self.repo() as repo:
      repo.run("add", "a thing", "--importance", "3", "--urgency", "3")
      _, out, _ = repo.run("list")
      self.assertIn("33", out)
      self.assertIn("do-now", out)


class ScoreOutlineTests(unittest.TestCase):
  def test_Import_ScoreKeys_LandOnItems(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.write("r.md", "## A thing\nimportance: 3\nurgency: 1\n")
      code, _, err = repo.run("import", "r.md")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().index.require("S01").score, 31)

  def test_Import_BadScore_IsRefusedByTheParser(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.write("r.md", "## A thing\nimportance: 9\n")
      code, _, err = repo.run("import", "r.md")
      self.assertEqual(code, 2)
      self.assertIn("1, 2 or 3", err)


if __name__ == "__main__":
  unittest.main()


class EffectiveScoreTests(unittest.TestCase):
  """A blocker inherits the priority of what depends on it (S39)."""

  def index(self, *items: Item):
    from slicer.model import Index

    return Index(items=list(items))

  def it(self, id: str, imp: int = 2, urg: int = 2, deps: list[str] | None = None) -> Item:
    return Item(id=id, title="t", status="open", importance=imp, urgency=urg, depends_on=deps or [])

  def test_Effective_BlockerInheritsFromDependent(self) -> None:
    from slicer import graph

    idx = self.index(self.it("S01", 3, 3, deps=["S02"]), self.it("S02"))
    eff = graph.effective_scores(idx)
    self.assertEqual(eff["S02"], 33)  # inherited from the 3/3 dependent
    self.assertEqual(eff["S01"], 33)

  def test_Effective_IsTransitive(self) -> None:
    from slicer import graph

    idx = self.index(
      self.it("S01", 3, 3, deps=["S02"]), self.it("S02", deps=["S03"]), self.it("S03")
    )
    self.assertEqual(graph.effective_scores(idx)["S03"], 33)

  def test_Effective_CycleIsSafe(self) -> None:
    from slicer import graph

    idx = self.index(self.it("S01", 3, 1, deps=["S02"]), self.it("S02", 1, 3, deps=["S01"]))
    eff = graph.effective_scores(idx)  # must terminate
    self.assertEqual(eff["S01"], 31)
    self.assertEqual(eff["S02"], 31)  # both equalise to the cycle max

  def test_Effective_UnrelatedItem_KeepsOwnScore(self) -> None:
    from slicer import graph

    idx = self.index(self.it("S01", 3, 3), self.it("S02", 1, 1))
    self.assertEqual(graph.effective_scores(idx)["S02"], 11)

  def test_Dependents_IsTheReverseOfDependsOn(self) -> None:
    from slicer import graph

    idx = self.index(self.it("S01", deps=["S03"]), self.it("S02", deps=["S03"]), self.it("S03"))
    self.assertEqual(sorted(graph.dependents(idx)["S03"]), ["S01", "S02"])


class NextByScoreTests(unittest.TestCase):
  """next returns the highest-effective-score startable item."""

  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    return repo

  def test_Next_PicksHighestEffectiveScore_NotFirstInOrder(self) -> None:
    with self.repo() as repo:
      repo.run("add", "low", "--importance", "1", "--urgency", "1")   # S01, first in order
      repo.run("add", "high", "--importance", "3", "--urgency", "3")  # S02
      _, out, _ = repo.run("next")
      self.assertTrue(out.startswith("S02"), out)

  def test_Next_SurfacesTheBlockerOfCriticalWork(self) -> None:
    with self.repo() as repo:
      repo.run("add", "the critical goal", "--importance", "3", "--urgency", "3")  # S01
      repo.run("add", "its blocker", "--importance", "1", "--urgency", "1")        # S02
      repo.run("set", "S01", "--depends-on", "S02")
      # S01 is blocked by S02; S02 is startable and inherits 33, so it wins.
      _, out, _ = repo.run("next")
      self.assertTrue(out.startswith("S02"), out)

  def test_Next_NeverReturnsABlockedItem_WhateverItsScore(self) -> None:
    with self.repo() as repo:
      repo.run("add", "startable but dull", "--importance", "1", "--urgency", "1")  # S01
      repo.run("add", "critical but blocked", "--importance", "3", "--urgency", "3")  # S02
      repo.run("add", "blocker, parked", "--importance", "1", "--urgency", "1")     # S03
      repo.run("park", "S03")
      repo.run("set", "S02", "--depends-on", "S03")
      # S02 is highest-scored but blocked by parked S03; S01 is the only startable.
      _, out, _ = repo.run("next")
      self.assertTrue(out.startswith("S01"), out)

  def test_List_SortScore_UsesEffectiveScore(self) -> None:
    with self.repo() as repo:
      repo.run("add", "critical goal", "--importance", "3", "--urgency", "3")  # S01=33
      repo.run("add", "its blocker")  # S02, own 22, inherits 33
      repo.run("set", "S01", "--depends-on", "S02")
      _, out, _ = repo.run("list", "--sort", "score")
      # S02 shows an inherited 33 (marked ^) and ranks alongside S01.
      self.assertIn("33^", out)


if __name__ == "__main__":
  unittest.main()
