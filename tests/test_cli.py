"""The command surface: exit codes, JSON output, verify, stats, log, TUI rows."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import support

from slicer import tui


class CliTests(unittest.TestCase):
  def repo(self, git: bool = False) -> support.TempRepo:
    repo = support.TempRepo(git=git)
    support.make_mini(repo)
    repo.run("init")
    repo.run("migrate", "--from", "docs/slices")
    return repo

  def test_Init_ExistingDirectory_RefusesWithoutForce(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("init")
      self.assertEqual(code, 2)
      self.assertIn("--force", err)

  def test_AnyCommand_OutsideATrackedProject_ExitsTwoWithAnInitHint(self) -> None:
    with support.TempRepo() as repo:
      code, _, err = repo.run("next")
      self.assertEqual(code, 2)
      self.assertIn("slicer init", err)

  def test_List_JsonFlag_EmitsParsableJson(self) -> None:
    with self.repo() as repo:
      code, out, _ = repo.run("list", "--json")
      self.assertEqual(code, 0)
      self.assertEqual([i["id"] for i in json.loads(out)], ["S01", "S02", "S03", "S04"])

  def test_List_StatusFilter_NarrowsToThatStatus(self) -> None:
    with self.repo() as repo:
      _, out, _ = repo.run("list", "--status", "parked", "--json")
      self.assertEqual([i["id"] for i in json.loads(out)], ["S03"])

  def test_Next_JsonFlag_IncludesTheSlicePath(self) -> None:
    with self.repo() as repo:
      _, out, _ = repo.run("next", "--json")
      payload = json.loads(out)
      self.assertEqual(payload["id"], "S02")
      self.assertTrue(payload["path"].endswith("S02.json"))

  def test_Show_UnknownId_ExitsTwo(self) -> None:
    with self.repo() as repo:
      self.assertEqual(repo.run("show", "S99")[0], 2)

  def test_Show_RendersTheSliceWithItsSections(self) -> None:
    with self.repo() as repo:
      _, out, _ = repo.run("show", "S02")
      self.assertIn("## Why", out)
      self.assertIn("Not in this slice", out)

  def test_Stats_JsonFlag_CountsByStatusSizeAndTree(self) -> None:
    with self.repo() as repo:
      _, out, _ = repo.run("stats", "--json")
      payload = json.loads(out)
      self.assertEqual(payload["total"], 4)
      self.assertEqual(payload["by_status"]["done"], 1)
      self.assertEqual(payload["by_tree"]["alpha"], 2)

  def test_Log_AfterTransitions_ListsThemNewestFirst(self) -> None:
    with self.repo() as repo:
      repo.run("done", "S02")
      repo.run("park", "S04")
      _, out, _ = repo.run("log", "--json")
      entries = json.loads(out)
      self.assertEqual(entries[0]["item"], "S04")
      self.assertEqual(entries[1]["item"], "S02")

  def test_Verify_CleanTree_ReportsNoProblems(self) -> None:
    with self.repo() as repo:
      code, _, _ = repo.run("verify")
      self.assertEqual(code, 0)

  def test_Verify_SliceFileInTheWrongFolder_IsAnError(self) -> None:
    with self.repo() as repo:
      state = repo.state()
      state.index.require("S02").status = "done"
      state.save_index()
      code, out, _ = repo.run("verify")
      self.assertEqual(code, 1)
      self.assertIn("wrong folder", out)

  def test_Verify_DoneItemAbsentFromHistory_IsWarnedNotFailed(self) -> None:
    with self.repo(git=True) as repo:
      repo.commit("initial import, mentioning nothing")
      code, out, _ = repo.run("verify")
      self.assertEqual(code, 0)
      self.assertIn("no commit subject mentions it", out)

  def test_Verify_OpenItemNamedInHistory_IsFlaggedForReview(self) -> None:
    with self.repo(git=True) as repo:
      repo.commit("land S02 second thing")
      _, out, _ = repo.run("verify")
      self.assertIn("recorded open, but", out)

  def test_Verify_NotAGitRepository_SaysSoAndStillPasses(self) -> None:
    with self.repo(git=False) as repo:
      code, out, _ = repo.run("verify")
      self.assertEqual(code, 0)
      self.assertIn("not a git repository", out)


class TuiTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    support.make_mini(repo)
    repo.run("init")
    repo.run("migrate", "--from", "docs/slices")
    return repo

  def test_Rows_MixedStatuses_ProduceOneRowPerItemInQueueOrder(self) -> None:
    with self.repo() as repo:
      rows = [r for r in tui.rows(repo.state()) if r.kind == tui.ITEM]
      self.assertEqual([r.target for r in rows], ["S01", "S02", "S03", "S04"])
      self.assertIn("done", rows[0].text)

  def test_Rows_AfterTheItems_ListTheProseBlocksBehindASeparator(self) -> None:
    with self.repo() as repo:
      rows = tui.rows(repo.state())
      kinds = [r.kind for r in rows]
      self.assertEqual(kinds.count(tui.SEPARATOR), 1)
      self.assertLess(kinds.index(tui.SEPARATOR), kinds.index(tui.PROSE))
      self.assertIn("preamble", [r.target for r in rows if r.kind == tui.PROSE])

  def test_Rows_Separator_IsNotSelectable(self) -> None:
    with self.repo() as repo:
      sep = next(r for r in tui.rows(repo.state()) if r.kind == tui.SEPARATOR)
      self.assertFalse(sep.selectable)

  def test_Rows_BlockedOpenItem_IsMarked(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S02", "--depends-on", "S03")
      rows = {r.target: r for r in tui.rows(repo.state()) if r.kind == tui.ITEM}
      self.assertTrue(rows["S02"].blocked)
      self.assertIn("!", rows["S02"].text)

  def test_Panel_Item_ListsEverySectionTaggedWithItsEntry(self) -> None:
    with self.repo() as repo:
      state = repo.state()
      lines = tui.panel(state, "S02")
      tagged = sorted({l.entry for l in lines if l.entry is not None})
      self.assertEqual(tagged, list(range(len(state.slices["S02"].sections))))
      self.assertTrue(any(l.text == "## Why" for l in lines))

  def test_Panel_ProseBlock_ShowsItsTextAsOneEntry(self) -> None:
    with self.repo() as repo:
      lines = tui.panel(repo.state(), "preamble")
      self.assertEqual({l.entry for l in lines if l.entry is not None}, {0})
      self.assertTrue(any("Mini index preamble." in l.text for l in lines))

  def test_Panel_UnpromotedItem_SaysHowToPromoteIt(self) -> None:
    with self.repo() as repo:
      repo.run("add", "an idea")
      text = "\n".join(l.text for l in tui.panel(repo.state(), "S05"))
      self.assertIn("no slice yet", text)

  def test_Entries_Item_AreItsSectionsInOrder(self) -> None:
    with self.repo() as repo:
      state = repo.state()
      names = [e.name for e in tui.entries(state, "S02")]
      self.assertEqual(names, [s.heading for s in state.slices["S02"].sections])

  def test_Act_DoneKey_MarksTheItemDoneThroughTheSameOpsPath(self) -> None:
    with self.repo() as repo:
      state = repo.state()
      self.assertEqual(tui.act(state, "d", "S02").message, "S02 done")
      self.assertEqual(repo.state().index.require("S02").status, "done")

  def test_Act_ReorderKey_MovesTheItemUp(self) -> None:
    with self.repo() as repo:
      tui.act(repo.state(), "K", "S02")
      self.assertEqual([i.id for i in repo.state().index.items], ["S02", "S01", "S03", "S04"])

  def test_Act_ReorderKeyAtTheTop_ReportsInsteadOfMoving(self) -> None:
    with self.repo() as repo:
      self.assertEqual(tui.act(repo.state(), "K", "S01").message, "already first")

  def test_Act_UnknownKey_IsIgnored(self) -> None:
    with self.repo() as repo:
      result = tui.act(repo.state(), "z", "S02")
      self.assertEqual(result.message, "")
      self.assertIsNone(result.edit)

  def test_Act_StatusKeyOnAProseRow_IsRefusedWithAnExplanation(self) -> None:
    with self.repo() as repo:
      result = tui.act(repo.state(), "d", "preamble")
      self.assertIn("not to a prose block", result.message)

  def test_Act_EditOnAProseRow_ReturnsAnEditRequestForThatBlock(self) -> None:
    with self.repo() as repo:
      result = tui.act(repo.state(), "e", "preamble")
      self.assertIsNotNone(result.edit)
      self.assertEqual(result.edit.kind, tui.PROSE)
      self.assertEqual(result.edit.target, "preamble")
      self.assertIn("Mini index preamble.", result.edit.body)

  def test_Act_EditWithTheRightPaneFocused_ReturnsThatSection(self) -> None:
    with self.repo() as repo:
      result = tui.act(repo.state(), "e", "S02", entry=1, focus="right")
      self.assertIsNotNone(result.edit)
      self.assertEqual(result.edit.kind, "section")
      self.assertEqual(result.edit.name, "Files")

  def test_Act_EditOnAnItemRowFromTheLeftPane_AsksForASection(self) -> None:
    with self.repo() as repo:
      result = tui.act(repo.state(), "e", "S02", focus="left")
      self.assertIsNone(result.edit)
      self.assertIn("tab", result.message)

  def test_Act_EditOnAnUnpromotedItem_SaysThereIsNothingToEdit(self) -> None:
    with self.repo() as repo:
      repo.run("add", "an idea")
      result = tui.act(repo.state(), "e", "S05", focus="right", entry=0)
      self.assertIsNone(result.edit)
      self.assertIn("nothing to edit", result.message)

  def test_ApplyEdit_Section_WritesThroughOpsAndAsksForARender(self) -> None:
    with self.repo() as repo:
      state = repo.state()
      request = tui.act(state, "e", "S02", entry=0, focus="right").edit
      message = tui.apply_edit(state, request, "a brand new why")
      self.assertIn("press r to render", message)
      self.assertEqual(repo.state().slices["S02"].section("Why").body, "a brand new why")

  def test_ApplyEdit_ProseBlock_WritesThroughOps(self) -> None:
    with self.repo() as repo:
      state = repo.state()
      request = tui.act(state, "e", "preamble").edit
      tui.apply_edit(state, request, "replaced")
      self.assertEqual(repo.state().index.preamble, "replaced")

  def test_ApplyEdit_UnchangedBody_WritesNothing(self) -> None:
    with self.repo() as repo:
      state = repo.state()
      request = tui.act(state, "e", "preamble").edit
      self.assertIn("unchanged", tui.apply_edit(state, request, request.body))
      self.assertEqual(repo.state().history(), [])


if __name__ == "__main__":
  unittest.main()


class TuiHelpTests(unittest.TestCase):
  """The help line is the only documentation of the key bindings."""

  def test_Help_EveryKeyItNames_IsHandled(self) -> None:
    # It claimed `n new`; `n` promotes, and nothing in the TUI creates an item.
    import re

    from slicer import tui

    handled = set(re.findall(r'if key == "(\w+)"', Path(tui.__file__).read_text()))
    # Handled in the run loop rather than in `act`: movement, tab (as "\t")
    # and quit (as "q" or Esc).
    handled |= {"j", "k", "tab", "q"}
    for token in re.findall(r"(\S+) \w+", tui.HELP):
      for key in token.split("/"):
        if key.isalpha():
          with self.subTest(key):
            self.assertIn(key, handled)

  def test_Help_DoesNotClaimTheTuiCanCreateAnItem(self) -> None:
    from slicer import tui

    self.assertNotIn("new", tui.HELP)
    self.assertIn("n promote", tui.HELP)
