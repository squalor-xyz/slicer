"""Release priority in list must preserve readiness and explicit sort choices."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import support

from slicer import graph, tui
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

  def test_List_PassPriority_LeavesOverridesAndOtherConsumersUnchanged(self) -> None:
    with support.TempRepo() as repo:
      self.releases(repo)
      original = ["S01", "S02", "S03"]
      self.assertEqual(self.ids(repo, "--sort", "score"), original)
      self.assertEqual(self.ids(repo, "--sort", "effort"), original)
      self.assertEqual(json.loads(self.run_ok(repo, "next", "--json"))["id"], "S01")
      state = repo.state()
      self.assertEqual([it.id for it in tui.sort_items(
        state, state.index.items, "ranked", True,
      )], original)
      self.assertEqual([it.id for it in tui.sort_items(
        state, state.index.items, "ranked", False,
      )], list(reversed(original)))
      self.run_ok(repo, "render")
      with patch("slicer.jsonio.write", side_effect=AssertionError("list wrote state")):
        self.assertEqual(self.ids(repo), ["S03", "S02", "S01"])
        self.assertEqual(self.ids(repo), ["S03", "S02", "S01"])
      self.assertEqual([it.id for it in repo.state().index.items], original)
      self.assertTrue(json.loads(self.run_ok(repo, "check", "--json"))["ok"])
      self.run_ok(repo, "sort", "--by", "score")
      self.assertEqual([it.id for it in repo.state().index.items], original)

  def test_List_CustomPasses_UseDeclarationOrderWithSharedFallback(self) -> None:
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
    self.assertEqual([it.id for it in order], ["S04", "S03", "S02", "S05", "S01"])

  def test_List_NoDeclaredPasses_RetainsScoreAndQueueOrder(self) -> None:
    index = Index(items=[
      Item("S01", "low", "open", pass_key="future", importance=1),
      Item("S02", "high", "open", importance=3),
      Item("S03", "tied", "open", pass_key="current", importance=3),
    ])
    for by_pass in (False, True):
      order = graph.ranked_order(index, Config(), index.items, by_pass=by_pass)
      self.assertEqual([it.id for it in order], ["S02", "S03", "S01"])
