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

  def test_Verify_OpenItemNamedInHistory_IsNotFlagged(self) -> None:
    # A commit that merely references an open item (a slice-named or roadmap
    # commit) no longer reads as "secretly finished" -- that was noise.
    with self.repo(git=True) as repo:
      repo.commit("land S02 second thing")
      _, out, _ = repo.run("verify")
      self.assertNotIn("recorded open, but", out)

  def test_Verify_GitCheckDisabled_EmitsNoCrossCheckWarnings(self) -> None:
    with self.repo(git=True) as repo:
      repo.commit("initial import, mentioning nothing")
      path = repo.root / ".slicer/config.json"
      cfg = json.loads(path.read_text())
      cfg["git_check"] = False
      path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
      _, out, _ = repo.run("verify")
      self.assertNotIn("no commit subject mentions it", out)

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
      fields = len(tui.FIELD_SPEC)
      self.assertEqual(tagged, list(range(fields + len(state.slices["S02"].sections))))
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
      entries = tui.entries(state, "S02")
      fields = len(tui.FIELD_SPEC)
      self.assertEqual([e.name for e in entries[:fields]], list(tui.FIELD_SPEC))
      self.assertEqual(
        [e.name for e in entries[fields:]], [s.heading for s in state.slices["S02"].sections]
      )

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
      # Sections come after the fields; entry len(FIELD_SPEC) is the first one.
      first_section = len(tui.FIELD_SPEC)
      result = tui.act(repo.state(), "e", "S02", entry=first_section + 1, focus="right")
      self.assertIsNotNone(result.edit)
      self.assertEqual(result.edit.kind, "section")
      self.assertEqual(result.edit.name, "Files")

  def test_Act_EditOnAnItemRowFromTheLeftPane_AsksForASection(self) -> None:
    with self.repo() as repo:
      result = tui.act(repo.state(), "e", "S02", focus="left")
      self.assertIsNone(result.edit)
      self.assertIn("tab", result.message)

  def test_Act_EditFieldOnAnUnpromotedItem_ReturnsAFieldEdit(self) -> None:
    # Fields live on the item, so they are editable before promotion.
    with self.repo() as repo:
      repo.run("add", "an idea")
      result = tui.act(repo.state(), "e", "S05", focus="right", entry=0)
      self.assertIsNotNone(result.edit)
      self.assertEqual(result.edit.kind, "field")
      self.assertEqual(result.edit.name, "size")

  def test_ApplyEdit_Section_WritesThroughOpsAndAsksForARender(self) -> None:
    with self.repo() as repo:
      state = repo.state()
      request = tui.act(state, "e", "S02", entry=len(tui.FIELD_SPEC), focus="right").edit
      message = tui.apply_edit(state, request, "a brand new why")
      self.assertIn("press r to render", message)
      first = state.slices["S02"].sections[0].heading
      self.assertEqual(repo.state().slices["S02"].section(first).body, "a brand new why")

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


class VerifyCompletenessTests(unittest.TestCase):
  """verify must catch the inconsistent states it used to pass on (S30)."""

  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    return repo

  def test_Verify_DependsOnRetiredItem_IsReported(self) -> None:
    with self.repo() as repo:
      repo.run("add", "base")
      repo.run("add", "dependent")
      repo.run("remove", "S01", "--reason", "obsolete")
      repo.run("set", "S02", "--depends-on", "S01")
      code, out, _ = repo.run("verify")
      self.assertEqual(code, 1)
      self.assertIn("retired", out)
      self.assertIn("S01", out)

  def test_Verify_SliceFilenameDisagreesWithId_IsReported(self) -> None:
    with self.repo() as repo:
      repo.run("add", "a thing")
      repo.run("promote", "S01")
      # Rewrite the file's contained id so name (S01) and id (S99) disagree.
      path = repo.root / ".slicer/slices/S01.json"
      data = json.loads(path.read_text())
      data["id"] = "S99"
      path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
      code, out, _ = repo.run("verify")
      self.assertEqual(code, 1)
      self.assertIn("S01.json", out)
      self.assertIn("disagree", out)

  def test_Verify_HasSliceFalseButFilePresent_IsReported(self) -> None:
    with self.repo() as repo:
      repo.run("add", "a thing")
      repo.run("promote", "S01")
      # Flip has_slice off in the index while the file stays on disk.
      path = repo.root / ".slicer/index.json"
      data = json.loads(path.read_text())
      data["items"][0]["has_slice"] = False
      path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
      code, out, _ = repo.run("verify")
      self.assertEqual(code, 1)
      self.assertIn("not marked as having a slice", out)

  def test_Verify_CleanTreeWithARetiredItem_StillPasses(self) -> None:
    # A retired item nobody depends on is fine.
    with self.repo() as repo:
      repo.run("add", "a thing")
      repo.run("remove", "S01", "--reason", "obsolete")
      code, out, _ = repo.run("verify")
      self.assertEqual(code, 0, out)

  def test_Check_DependsOnRetired_Fails(self) -> None:
    with self.repo() as repo:
      repo.run("add", "base")
      repo.run("add", "dependent")
      repo.run("remove", "S01", "--reason", "obsolete")
      repo.run("set", "S02", "--depends-on", "S01")
      repo.run("render")
      self.assertNotEqual(repo.run("check")[0], 0)


class TuiCreateAndFieldTests(unittest.TestCase):
  """The TUI can create an item and set fields, through the same ops (S40)."""

  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.run("add", "a thing")
    return repo

  def test_Act_AddKey_ReturnsANewItemEditRequest(self) -> None:
    with self.repo() as repo:
      result = tui.act(repo.state(), "a", "S01")
      self.assertIsNotNone(result.edit)
      self.assertEqual(result.edit.kind, "new")

  def test_ApplyEdit_New_CreatesTheItemThroughOps(self) -> None:
    with self.repo() as repo:
      state = repo.state()
      request = tui.act(state, "a", "S01").edit
      message = tui.apply_edit(state, request, "a fresh item")
      self.assertIn("added", message)
      titles = [i.title for i in repo.state().index.items]
      self.assertIn("a fresh item", titles)

  def test_ApplyEdit_New_EmptyTitleCancels(self) -> None:
    with self.repo() as repo:
      state = repo.state()
      request = tui.act(state, "a", "S01").edit
      # a whitespace-only title is not created (ops.add would reject a blank one)
      self.assertEqual(tui.apply_edit(state, request, "   "), "cancelled")
      self.assertEqual(len(repo.state().index.items), 1)

  def test_ApplyEdit_Field_SetsSizeThroughOps(self) -> None:
    with self.repo() as repo:
      state = repo.state()
      request = tui.act(state, "e", "S01", entry=0, focus="right").edit  # size
      self.assertEqual(request.kind, "field")
      tui.apply_edit(state, request, "L")
      self.assertEqual(repo.state().index.require("S01").size, "L")

  def test_ApplyEdit_Field_SetsTreesAsAList(self) -> None:
    with self.repo() as repo:
      state = repo.state()
      trees_entry = list(tui.FIELD_SPEC).index("trees")
      request = tui.act(state, "e", "S01", entry=trees_entry, focus="right").edit
      tui.apply_edit(state, request, "core, cli")
      self.assertEqual(repo.state().index.require("S01").trees, ["core", "cli"])

  def test_ApplyEdit_Field_SetsImportanceThroughOps(self) -> None:
    with self.repo() as repo:
      state = repo.state()
      imp = list(tui.FIELD_SPEC).index("importance")
      request = tui.act(state, "e", "S01", entry=imp, focus="right").edit
      tui.apply_edit(state, request, "3")
      self.assertEqual(repo.state().index.require("S01").importance, 3)

  def test_ApplyEdit_Field_BadValueReportsInsteadOfCrashing(self) -> None:
    with self.repo() as repo:
      state = repo.state()
      imp = list(tui.FIELD_SPEC).index("importance")
      request = tui.act(state, "e", "S01", entry=imp, focus="right").edit
      message = tui.apply_edit(state, request, "9")
      self.assertIn("1, 2 or 3", message)


class RenderFlagTests(unittest.TestCase):
  """A mutating command with --render leaves the render current (S45)."""

  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.run("add", "a thing")
    repo.run("render")
    return repo

  def test_Done_Render_LeavesCheckClean(self) -> None:
    with self.repo() as repo:
      repo.run("promote", "S01")
      repo.run("render")
      code, out, _ = repo.run("done", "S01", "--render")
      self.assertEqual(code, 0, out)
      self.assertIn("rendered", out)
      self.assertEqual(repo.run("check")[0], 0)  # no separate render needed

  def test_Add_Render_LeavesCheckClean(self) -> None:
    with self.repo() as repo:
      repo.run("add", "another", "--render")
      self.assertEqual(repo.run("check")[0], 0)

  def test_Set_Render_LeavesCheckClean(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S01", "--findings", "G9", "--render")
      self.assertEqual(repo.run("check")[0], 0)

  def test_Mutation_WithoutRender_LeavesTheRenderStale(self) -> None:
    with self.repo() as repo:
      repo.run("add", "another")  # no --render
      self.assertEqual(repo.run("check")[0], 1)  # stale, as before

  def test_Render_Json_DoesNotEmitTheExtraLine(self) -> None:
    # The payload stays the command's own; render is a side effect.
    with self.repo() as repo:
      _, out, _ = repo.run("add", "another", "--render", "--json")
      json.loads(out)  # single valid document, not two
      self.assertNotIn("rendered", out)

  def test_Render_ReportsFileCount(self) -> None:
    with self.repo() as repo:
      _, out, _ = repo.run("add", "another", "--render")
      self.assertRegex(out, r"rendered \d+ file")
