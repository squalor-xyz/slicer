"""Release priority in list must preserve readiness and explicit sort choices."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import support

from slicer import graph, ops, tui
from slicer.config import Config
from slicer.model import Index, Item, PassInfo


class ListPassOrderTests(unittest.TestCase):
  def run_ok(self, repo: support.TempRepo, *args: str) -> str:
    code, out, err = repo.run(*args)
    self.assertEqual((code, err), (0, ""), out)
    return out

  def ids(self, repo: support.TempRepo, *args: str) -> list[str]:
    return [it["id"] for it in json.loads(self.run_ok(repo, "list", *args, "--json"))]

  def releases(self, repo: support.TempRepo) -> None:
    self.run_ok(repo, "init")
    for key in ("v1.3", "v1.4", "later"):
      self.run_ok(repo, "prose", "add-pass", key)
    self.run_ok(repo, "add", "deferred", "--pass", "later", "--importance", "3",
      "--urgency", "3", "--effort", "1", "--tree", "core")
    self.run_ok(repo, "add", "future", "--pass", "v1.4", "--importance", "2",
      "--urgency", "2", "--effort", "2", "--tree", "core")
    self.run_ok(repo, "add", "current", "--pass", "v1.3", "--importance", "1",
      "--urgency", "1", "--effort", "3", "--tree", "core")

  def test_List_DeclaredReleases_OverrideOpposingScoresInEveryProfile(self) -> None:
    with support.TempRepo() as repo:
      self.releases(repo)
      expected = ["S03", "S02", "S01"]
      self.assertEqual(self.ids(repo), expected)
      self.assertEqual(self.ids(repo, "--lean"), expected)
      text = self.run_ok(repo, "list")
      self.assertEqual([row.split()[1] for row in text.splitlines()[1:]], expected)
      self.assertEqual(self.ids(repo, "--all"), expected)
      self.assertEqual(self.ids(repo, "--tree", "core"), expected)
      self.assertEqual(self.ids(repo, "--pass", "v1.4"), ["S02"])
      self.run_ok(repo, "set", "S01", "--flag", "chosen")
      self.run_ok(repo, "set", "S03", "--flag", "chosen")
      self.assertEqual(self.ids(repo, "--flag", "chosen"), ["S03", "S01"])

  def test_List_ReadinessGroups_PrecedePassPriority(self) -> None:
    with support.TempRepo() as repo:
      self.releases(repo)
      self.run_ok(repo, "start", "S01")
      self.run_ok(repo, "add", "parked", "--pass", "v1.3", "--effort", "2")
      self.run_ok(repo, "park", "S04")
      self.run_ok(repo, "add", "blocked", "--pass", "v1.3", "--depends-on", "S04",
        "--effort", "2")
      self.assertEqual(self.ids(repo), ["S01", "S03", "S02", "S05", "S04"])
      self.assertEqual(self.ids(repo, "--in-work"), ["S01"])

  def test_List_ReviewViews_KeepMembershipAndApplyPassPriority(self) -> None:
    with support.TempRepo() as repo:
      self.releases(repo)
      for id in ("S01", "S03"):
        self.run_ok(repo, "set", id, "--status", "review")
      self.assertEqual(self.ids(repo, "--review"), ["S03", "S01"])
      self.assertEqual(self.ids(repo, "--status", "review"), ["S03", "S01"])
      for id in ("S01", "S03"):
        self.run_ok(repo, "start", id)
      self.assertEqual(self.ids(repo, "--in-work"), ["S03", "S01"])
      self.assertEqual(self.ids(repo), ["S03", "S01", "S02"])

  def test_List_WithinPass_InheritsScoresAndKeepsManualTies(self) -> None:
    with support.TempRepo() as repo:
      self.releases(repo)
      self.run_ok(repo, "set", "S02", "--pass", "v1.3")
      self.run_ok(repo, "add", "tied", "--pass", "v1.3", "--effort", "2")
      self.assertEqual(self.ids(repo), ["S02", "S04", "S03", "S01"])
      self.run_ok(repo, "set", "S01", "--depends-on", "S03")
      self.assertEqual(self.ids(repo), ["S03", "S02", "S04", "S01"])

  def test_List_PassPriority_SharesRankingAndLeavesExplicitSortsUnchanged(self) -> None:
    with support.TempRepo() as repo:
      self.releases(repo)
      original = ["S01", "S02", "S03"]
      self.assertEqual(self.ids(repo, "--sort", "score"), original)
      self.assertEqual(self.ids(repo, "--sort", "effort"), original)
      self.assertEqual(json.loads(self.run_ok(repo, "next", "--json"))["id"], "S03")
      state = repo.state()
      self.assertEqual([it.id for it in tui.sort_items(
        state, state.index.items, "ranked", True,
      )], ["S03", "S02", "S01"])
      self.assertEqual([it.id for it in tui.sort_items(
        state, state.index.items, "ranked", False,
      )], original)
      self.run_ok(repo, "render")
      with patch("slicer.jsonio.write", side_effect=AssertionError("list wrote state")):
        self.assertEqual(self.ids(repo), ["S03", "S02", "S01"])
        self.assertEqual(self.ids(repo), ["S03", "S02", "S01"])
      self.assertEqual([it.id for it in repo.state().index.items], original)
      self.assertTrue(json.loads(self.run_ok(repo, "check", "--json"))["ok"])
      self.run_ok(repo, "sort", "--by", "score")
      self.assertEqual([it.id for it in repo.state().index.items], original)

  def test_List_CustomPasses_MergeEmptyByScoreAndKeepUnknownAfterDeclared(self) -> None:
    # Alphabetical/version ordering and distinct ranks for unknown keys would
    # produce different results from this deliberately opposed queue.
    index = Index(
      passes=[PassInfo("zeta"), PassInfo("alpha")],
      items=[
        Item("S01", "unknown low", "open", pass_key="unknown", importance=1),
        Item("S02", "empty high", "open", importance=3),
        Item("S03", "alpha high", "open", pass_key="alpha", importance=3),
        Item("S04", "zeta low", "open", pass_key="zeta", importance=1),
        Item("S05", "other high", "open", pass_key="other", importance=3),
      ],
    )
    order = graph.ranked_order(index, Config(), index.items, by_pass=True)
    self.assertEqual([it.id for it in order], ["S02", "S04", "S03", "S05", "S01"])

  def test_List_NoDeclaredPasses_RetainsScoreAndQueueOrder(self) -> None:
    index = Index(items=[
      Item("S01", "low", "open", pass_key="future", importance=1),
      Item("S02", "high", "open", importance=3),
      Item("S03", "tied", "open", pass_key="current", importance=3),
    ])
    for by_pass in (False, True):
      order = graph.ranked_order(index, Config(), index.items, by_pass=by_pass)
      self.assertEqual([it.id for it in order], ["S02", "S03", "S01"])

  def test_Ranking_EmptyPass_MergesAgainstNextDeclaredRowInBothDirections(self) -> None:
    for empty_score, expected in ((40, ["E", "A", "B", "L"]),
                                  (25, ["E", "A", "B", "L"]),
                                  (20, ["A", "B", "E", "L"]),
                                  (10, ["A", "B", "E", "L"])):
      with self.subTest(score=empty_score):
        index = Index(passes=[PassInfo("early"), PassInfo("later")], items=[
          Item("A", "early", "open", pass_key="early", importance=2, urgency=0),
          Item("B", "later", "open", pass_key="later", importance=3, urgency=5),
          Item("E", "empty", "open", importance=empty_score // 10, urgency=empty_score % 10),
          Item("L", "empty low", "open", importance=1, urgency=0),
        ])
        self.assertEqual([it.id for it in graph.ranked_order(index, Config(), index.items)], expected)
        self.assertEqual([it.id for it in graph.ranked_order(
          index, Config(), index.items, descending=False)],
          (["E", "L", "B", "A"] if empty_score == 10 else
           ["L", "B", "A", "E"] if empty_score == 40 else ["L", "E", "B", "A"]))
    # Equal scores across empty and declared passes keep stored position.
    index.items[2].importance, index.items[2].urgency = 2, 0
    index.items = [index.items[2], *index.items[:2], index.items[3]]
    self.assertEqual([it.id for it in graph.ranked_order(index, Config(), index.items)],
      ["E", "A", "B", "L"])

  def test_Next_Passes_UsesEligibleListSubsequenceAndFilters(self) -> None:
    with support.TempRepo() as repo:
      self.releases(repo)
      for title in ("done high", "also done high"):
        self.run_ok(repo, "add", title, "--pass", "v1.3", "--importance", "3", "--urgency", "3")
      for id in ("S04", "S05"):
        self.run_ok(repo, "done", id)
      self.run_ok(repo, "set", "S03", "--depends-on", "S04")
      self.run_ok(repo, "set", "S03", "--size", "S")
      expected = self.ids(repo)
      self.assertEqual(expected, ["S03", "S02", "S01"])
      self.run_ok(repo, "render")
      with patch("slicer.jsonio.write", side_effect=AssertionError("read wrote state")):
        for lean in ((), ("--lean",)):
          for offset, id in enumerate(expected):
            self.assertEqual(json.loads(self.run_ok(repo, "next", "-n", str(offset),
              "--json", *lean))["id"], id)
        self.assertEqual(json.loads(self.run_ok(repo, "next", "--tree", "core", "--size", "S",
          "--json"))["id"], "S03")
        self.assertEqual([it["id"] for it in json.loads(self.run_ok(repo,
          "next", "--batch", "3", "--json"))["items"]], expected)
      self.assertTrue(json.loads(self.run_ok(repo, "check", "--json"))["ok"])
      self.run_ok(repo, "start", "S01")
      self.assertEqual(json.loads(self.run_ok(repo, "next", "--json"))["id"], "S01")

  def test_Next_Review_PrefersReviewingThenDeclaredPassOrder(self) -> None:
    with support.TempRepo() as repo:
      self.releases(repo)
      for id in ("S01", "S02", "S03"):
        self.run_ok(repo, "set", id, "--status", "review")
      self.assertEqual(json.loads(self.run_ok(repo, "next", "--status", "review", "--json"))["id"], "S03")
      self.run_ok(repo, "start", "S01")
      self.assertEqual(json.loads(self.run_ok(repo, "next", "--status", "review", "--json"))["id"], "S01")

  def test_Next_Batch_Passes_PreservesDependencyBeforeDependent(self) -> None:
    with support.TempRepo() as repo:
      self.releases(repo)
      self.run_ok(repo, "set", "S03", "--depends-on", "S01")
      # Readiness puts the unblocked later pass first; the batch can then take
      # the earlier pass dependent after its prerequisite and other ready rows.
      self.assertEqual([it["id"] for it in json.loads(self.run_ok(repo,
        "next", "--batch", "3", "--json"))["items"]], ["S02", "S01", "S03"])

  def test_List_EmptyPass_AllProfilesAndTuiAgree(self) -> None:
    with support.TempRepo() as repo:
      self.releases(repo)
      self.run_ok(repo, "add", "empty high", "--pass", "", "--importance", "3", "--urgency", "3", "--effort", "1")
      self.run_ok(repo, "add", "empty low", "--pass", "", "--importance", "1", "--urgency", "1", "--effort", "1")
      expected = ["S04", "S03", "S02", "S01", "S05"]
      self.assertEqual(self.ids(repo), expected)
      self.assertEqual(self.ids(repo, "--lean"), expected)
      self.assertEqual([row.split()[1] for row in self.run_ok(repo, "list").splitlines()[1:]], expected)
      state = repo.state()
      self.assertEqual([it.id for it in tui.sort_items(state, state.index.items, "ranked", True)], expected)
      self.assertEqual([it.id for it in tui.sort_items(state, state.index.items, "ranked", False)],
        ["S05", "S01", "S02", "S03", "S04"])
      self.assertEqual(json.loads(self.run_ok(repo, "next", "--json"))["id"], "S04")

  def test_Next_ExcludedNamedRow_DoesNotRelocateEmptyPassRows(self) -> None:
    with support.TempRepo() as repo:
      self.releases(repo)
      self.run_ok(repo, "set", "S02", "--importance", "3", "--urgency", "3")
      self.run_ok(repo, "add", "empty", "--pass", "", "--importance", "2", "--urgency", "3", "--effort", "1", "--tree", "core")
      expected = ["S04", "S03", "S02", "S01"]
      self.assertEqual(self.ids(repo), expected)
      state = repo.state()
      elsewhere = {"S03": [{"worktree": "sibling", "owner": "other", "status": "started"}]}
      self.assertEqual(ops.next_item(state, elsewhere=elsewhere).item.id, "S04")
      self.run_ok(repo, "set", "S03", "--tree", "other")
      self.assertEqual(json.loads(self.run_ok(repo, "next", "--tree", "core", "--json"))["id"], "S04")
      self.run_ok(repo, "promote", "S03")
      for offset, id in enumerate(["S04", "S02", "S01"]):
        self.assertEqual(json.loads(self.run_ok(repo, "next", "-n", str(offset), "--json"))["id"], id)
      self.assertEqual([it["id"] for it in json.loads(self.run_ok(repo,
        "next", "--batch", "3", "--json"))["items"]], ["S04", "S02", "S01"])

  def test_Ranking_Passes_AscendingFlipsReadinessAndKeepsTies(self) -> None:
    index = Index(passes=[PassInfo("early"), PassInfo("later")], items=[
      Item("A", "started low", "started", pass_key="later", importance=1, urgency=0),
      Item("B", "open high", "open", pass_key="early", importance=3, urgency=3),
      Item("C", "open tied", "open", pass_key="early", importance=3, urgency=3),
      Item("D", "blocked", "open", pass_key="early", depends_on=["P"]),
      Item("P", "parked", "parked", pass_key="early"),
    ])
    self.assertEqual([it.id for it in graph.ranked_order(index, Config(), index.items)],
      ["A", "B", "C", "D", "P"])
    self.assertEqual([it.id for it in graph.ranked_order(index, Config(), index.items, descending=False)],
      ["P", "D", "B", "C", "A"])
