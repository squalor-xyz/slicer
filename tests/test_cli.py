"""The command surface: exit codes, JSON output, verify, stats, log, TUI rows."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import support

from slicer import cli, tui
from slicer.store import State


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
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
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

  def test_Stats_ReportsCompletionAndPerTreeProgress(self) -> None:
    with self.repo() as repo:
      code, text, _ = repo.run("stats")
      self.assertEqual(code, 0)
      payload = json.loads(repo.run("stats", "--json")[1])
      self.assertEqual(payload["completion"], {"done": 1, "total": 4, "percent": 25})
      # per-tree status split; each tree's split sums to its flat count
      self.assertIn("alpha", payload["by_tree_status"])
      self.assertEqual(sum(payload["by_tree_status"]["alpha"].values()), payload["by_tree"]["alpha"])
      self.assertIn("done", payload["by_tree_status"]["alpha"])
      # existing flat buckets unchanged
      self.assertEqual(payload["by_status"]["done"], 1)
      # text surfaces both
      self.assertIn("1 done (25%)", text)
      self.assertIn("progress by tree", text)

  def test_CrossCounts_ListRowsCountUnderEachAndSkipEmpty(self) -> None:
    from slicer import model
    items = [
      model.Item("S01", "a", "done", trees=["x", "y"]),
      model.Item("S02", "b", "open", trees=["x"]),
      model.Item("S03", "c", "open", trees=[]),
    ]
    tab = model.cross_counts(items, "trees", "status")
    self.assertEqual(tab, {"x": {"done": 1, "open": 1}, "y": {"done": 1}})

  def test_Log_AfterTransitions_ListsThemNewestFirst(self) -> None:
    with self.repo() as repo:
      repo.run("done", "S02")
      repo.run("park", "S04")
      _, out, _ = repo.run("log", "--json")
      entries = json.loads(out)
      self.assertEqual(entries[0]["item"], "S04")
      self.assertEqual(entries[1]["item"], "S02")

  def test_Log_ItemFilter_ScopesToThatItem(self) -> None:
    with self.repo() as repo:
      repo.run("done", "S02")
      repo.run("park", "S04")
      entries = json.loads(repo.run("log", "--item", "S02", "--json")[1])
      self.assertTrue(entries)
      self.assertEqual({e["item"] for e in entries}, {"S02"})

  def test_Log_ItemFilter_Repeated_IncludesEach(self) -> None:
    with self.repo() as repo:
      repo.run("done", "S02")
      repo.run("park", "S04")
      entries = json.loads(repo.run("log", "--item", "S02", "--item", "S04", "--json")[1])
      self.assertEqual({e["item"] for e in entries}, {"S02", "S04"})

  def test_Log_ItemFilter_ComposesWithLimit(self) -> None:
    with self.repo() as repo:
      repo.run("start", "S02")
      repo.run("start", "S03")  # unrelated, newer — must not fill S02's limit
      repo.run("done", "S02")
      entries = json.loads(repo.run("log", "--item", "S02", "--limit", "1", "--json")[1])
      self.assertEqual(len(entries), 1)
      self.assertEqual((entries[0]["item"], entries[0]["to"]), ("S02", "done"))

  def test_Log_ItemFilter_NoHistory_ExitsZeroAndSaysSo(self) -> None:
    with self.repo() as repo:
      code, out, _ = repo.run("log", "--item", "S99")
      self.assertEqual(code, 0)
      self.assertIn("no history for S99", out)
      self.assertEqual(json.loads(repo.run("log", "--item", "S99", "--json")[1]), [])

  def test_Log_SetRecordsOldToNewValues(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S02", "--importance", "3", "--urgency", "3")
      repo.run("set", "S02", "--tree", "core", "--size", "M")
      entries = json.loads(repo.run("log", "--item", "S02", "--action", "set", "--json")[1])
      notes = " | ".join(e["note"] for e in entries)
      self.assertIn("importance 2→3", notes)
      self.assertIn("urgency 2→3", notes)
      self.assertIn("size", notes)
      self.assertIn("→core", notes)  # list field rendered

  def test_Log_ActionFilter_ScopesAndComposesWithItem(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S02", "--importance", "3")
      repo.run("done", "S02")
      set_only = json.loads(repo.run("log", "--action", "set", "--json")[1])
      self.assertTrue(set_only)
      self.assertEqual({e["action"] for e in set_only}, {"set"})
      both = json.loads(repo.run("log", "--item", "S02", "--action", "set", "--json")[1])
      self.assertEqual({(e["item"], e["action"]) for e in both}, {("S02", "set")})

  def test_Log_ActionFilter_Repeated_IncludesEach(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S02", "--importance", "3")
      repo.run("done", "S02")
      actions = {e["action"] for e in
                 json.loads(repo.run("log", "--action", "set", "--action", "status", "--json")[1])}
      self.assertEqual(actions, {"set", "status"})

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
    with self.repo(git=False) as repo, support.isolated_discovery(repo.root):
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
      self.assertEqual(tagged, list(range(fields + 1 + len(state.slices["S02"].sections))))
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
        [e.name for e in entries[fields:]], ["boundary"] + [s.heading for s in state.slices["S02"].sections]
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
      # Sections come after fields and the independent boundary.
      first_section = len(tui.FIELD_SPEC) + 1
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
      request = tui.act(state, "e", "S02", entry=len(tui.FIELD_SPEC) + 1, focus="right").edit
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
  """Help and dispatch share bindings rather than drifting independently."""

  def test_Help_EveryKeyItNames_IsHandled(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      state = repo.state()
      extra = tui.Binding(("!",), "!", "help", "Temporary help alias")
      from unittest.mock import patch
      with patch.object(tui, "BINDINGS", (*tui.BINDINGS, extra)):
        self.assertIn("Temporary help alias", "\n".join(tui.help_lines()))
        view = tui.View.initial(state)
        view.handle(state, "!")
        self.assertEqual(view.mode, "help")

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


class TuiAliasTests(unittest.TestCase):
  """`ui` is a true alias of `tui`: same dispatch, project, errors, exit code."""

  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    return repo

  def test_UiAndTui_BothDispatchToTheRunnerForTheSameProject(self) -> None:
    with self.repo() as repo:
      for name in ("tui", "ui"):
        with self.subTest(name=name):
          with patch.object(tui, "run", return_value=7) as run:
            code, _, err = repo.run(name)
          self.assertEqual((code, err), (7, ""))
          self.assertEqual(run.call_count, 1)
          state = run.call_args.args[0]
          self.assertIsInstance(state, State)
          self.assertEqual(state.root, repo.root)

  def test_UiAndTui_ExplicitRootAfterTheName_SelectsThatProject(self) -> None:
    with self.repo() as repo:
      for name in ("tui", "ui"):
        with self.subTest(name=name):
          with patch.object(tui, "run", return_value=0) as run:
            code = cli.main([name, "--root", str(repo.root)])
          self.assertEqual(code, 0)
          self.assertEqual(run.call_args.args[0].root, repo.root)

  def test_UiAndTui_Help_ExitsWithoutOpeningTheRunner(self) -> None:
    for name in ("tui", "ui"):
      with self.subTest(name=name):
        out = io.StringIO()
        with patch.object(tui, "run", side_effect=AssertionError("runner opened")) as run:
          with self.assertRaises(SystemExit) as caught, redirect_stdout(out):
            cli.main([name, "--help"])
        self.assertEqual(caught.exception.code, 0)
        self.assertTrue(out.getvalue().startswith("usage: slicer"))
        self.assertIn("--root", out.getvalue())
        run.assert_not_called()

  def test_UiAndTui_Json_RejectedIdentically(self) -> None:
    with self.repo() as repo:
      codes = {}
      for name in ("tui", "ui"):
        with self.subTest(name=name):
          with patch.object(tui, "run") as run:
            code, out, _ = repo.run(name, "--json")
          run.assert_not_called()
          payload = json.loads(out)
          self.assertEqual(payload["error"]["code"], "usage")
          self.assertIn("--json", payload["error"]["message"])
          self.assertEqual(code, 2)
          codes[name] = code
      self.assertEqual(codes["tui"], codes["ui"])
