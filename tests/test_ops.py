"""Mutations: adding, promoting, reordering, and crossing the done boundary."""

from __future__ import annotations

import unittest

import support

from slicer import graph, ops, vcs
from slicer.errors import StateError


class OpsTests(unittest.TestCase):
  def repo(self, git: bool = False) -> support.TempRepo:
    repo = support.TempRepo(git=git)
    support.make_mini(repo)
    repo.run("init")
    repo.run("migrate", "--from", "docs/slices")
    return repo

  def test_Add_NoSliceFile_AppendsAnIdeaAndBumpsNextId(self) -> None:
    with self.repo() as repo:
      code, out, err = repo.run("add", "a new idea", "--size", "M", "--tree", "alpha")
      self.assertEqual(code, 0, err)
      state = repo.state()
      item = state.index.require("S05")
      self.assertFalse(item.has_slice)
      self.assertEqual(state.index.next_id, 6)
      self.assertIsNone(state.find_slice_file("S05"))

  def test_Add_AfterDeletingHistory_StillNeverReusesAnId(self) -> None:
    with self.repo() as repo:
      repo.run("add", "one")
      state = repo.state()
      state.index.items = [i for i in state.index.items if i.id != "S05"]
      state.save_index()
      code, _, _ = repo.run("add", "two")
      self.assertEqual(code, 0)
      self.assertIsNotNone(repo.state().index.get("S06"))

  def test_Promote_IdeaItem_CreatesASliceWithTheConfiguredSections(self) -> None:
    with self.repo() as repo:
      repo.run("add", "a new idea")
      code, _, err = repo.run("promote", "S05")
      self.assertEqual(code, 0, err)
      sl = repo.state().slices["S05"]
      self.assertEqual([s.heading for s in sl.sections], repo.state().config.sections)
      self.assertEqual(sl.boundary, repo.state().config.boundary)

  def test_Promote_AlreadyPromoted_RefusesWithoutForce(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("promote", "S02")
      self.assertEqual(code, 2)
      self.assertIn("--force", err)

  def test_Promote_FromASourceFile_PopulatesTheSections(self) -> None:
    with self.repo() as repo:
      repo.run("add", "a new idea")
      repo.write(
        "draft.md",
        "## a new idea\n\n### Why\nBecause it matters.\n\n### Implement\nDo the thing.\n",
      )
      code, _, err = repo.run("promote", "S05", "--file", str(repo.root / "draft.md"))
      self.assertEqual(code, 0, err)
      sl = repo.state().slices["S05"]
      # Configured order is kept; the source only fills the two it named.
      self.assertEqual([s.heading for s in sl.sections], repo.state().config.sections)
      self.assertEqual(sl.section("Why").body, "Because it matters.")
      self.assertEqual(sl.section("Implement").body, "Do the thing.")
      self.assertEqual(sl.section("Files").body, "")

  def test_Promote_SourceLeadParagraph_LandsOnTheSlice(self) -> None:
    with self.repo() as repo:
      repo.run("add", "a new idea")
      repo.write("draft.md", "## a new idea\n\nA framing sentence.\n\n### Why\nBecause.\n")
      code, _, err = repo.run("promote", "S05", "--file", str(repo.root / "draft.md"))
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().slices["S05"].lead, ["A framing sentence."])

  def test_Promote_SourceWithItemKeys_RefusesAndPointsToSet(self) -> None:
    with self.repo() as repo:
      repo.run("add", "a new idea")
      repo.write("draft.md", "## a new idea\nsize: M\n\n### Why\nBecause.\n")
      code, _, err = repo.run("promote", "S05", "--file", str(repo.root / "draft.md"))
      self.assertEqual(code, 2)
      self.assertIn("size", err)
      self.assertIn("set", err)
      self.assertFalse(repo.state().index.require("S05").has_slice)

  def test_Promote_SourceWithTwoItems_Refuses(self) -> None:
    with self.repo() as repo:
      repo.run("add", "a new idea")
      # A writer who used '##' for sections gets two items, not one; say so.
      repo.write("draft.md", "## Why\nBecause.\n\n## Implement\nDo it.\n")
      code, _, err = repo.run("promote", "S05", "--file", str(repo.root / "draft.md"))
      self.assertEqual(code, 2)
      self.assertIn("one item", err)

  def test_Promote_SourceWithNoSections_Refuses(self) -> None:
    with self.repo() as repo:
      repo.run("add", "a new idea")
      repo.write("draft.md", "## a new idea\n")
      code, _, err = repo.run("promote", "S05", "--file", str(repo.root / "draft.md"))
      self.assertEqual(code, 2)
      self.assertIn("no sections", err)

  def test_Promote_FromSourceAlreadyPromoted_RefusesWithoutForce(self) -> None:
    with self.repo() as repo:
      repo.write("draft.md", "## S02\n\n### Why\nBecause.\n")
      code, _, err = repo.run("promote", "S02", "--file", str(repo.root / "draft.md"))
      self.assertEqual(code, 2)
      self.assertIn("--force", err)

  def test_Move_ItemBeforeAnother_ReordersTheQueueOnly(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("move", "S04", "--before", "S01")
      self.assertEqual(code, 0, err)
      self.assertEqual([i.id for i in repo.state().index.items], ["S04", "S01", "S02", "S03"])

  def test_Move_BeforeItself_IsANoOpAndKeepsTheItem(self) -> None:
    with self.repo() as repo:
      before = [i.id for i in repo.state().index.items]
      code, _, err = repo.run("move", "S02", "--before", "S02")
      self.assertEqual(code, 0, err)
      self.assertEqual([i.id for i in repo.state().index.items], before)

  def test_Move_AfterItself_IsANoOp(self) -> None:
    with self.repo() as repo:
      before = [i.id for i in repo.state().index.items]
      code, _, err = repo.run("move", "S02", "--after", "S02")
      self.assertEqual(code, 0, err)
      self.assertEqual([i.id for i in repo.state().index.items], before)

  def test_Move_WithoutATarget_Refuses(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("move", "S04")
      self.assertEqual(code, 2)
      self.assertIn("--before", err)

  def test_Done_OpenItem_MovesTheSliceFileIntoDone(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("done", "S02")
      self.assertEqual(code, 0, err)
      self.assertTrue((repo.root / ".slicer/slices/done/S02.json").is_file())
      self.assertFalse((repo.root / ".slicer/slices/S02.json").exists())
      self.assertEqual(repo.state().index.require("S02").status, "done")

  def test_Done_InAGitRepo_UsesGitMvSoTheMoveStaysARename(self) -> None:
    with self.repo(git=True) as repo:
      repo.commit("import")
      repo.run("done", "S02")
      staged = repo._git("diff", "--cached", "--name-status", "-M")
      self.assertIn("S02.json", staged.stdout)
      self.assertTrue(staged.stdout.lstrip().startswith("R"), staged.stdout)

  def test_Done_OutsideAGitRepo_StillMovesTheFile(self) -> None:
    with self.repo(git=False) as repo:
      repo.run("done", "S02")
      self.assertTrue((repo.root / ".slicer/slices/done/S02.json").is_file())

  def test_Done_Item_AppendsALogEntryRecordingTheTransition(self) -> None:
    with self.repo() as repo:
      repo.run("done", "S02", "--note", "landed clean")
      entry = repo.state().history()[-1]
      self.assertEqual((entry.item, entry.frm, entry.to), ("S02", "open", "done"))
      self.assertEqual(entry.note, "landed clean")

  def test_Unpark_ParkedItem_ReturnsItToTheQueue(self) -> None:
    with self.repo() as repo:
      repo.run("unpark", "S03")
      self.assertEqual(repo.state().index.require("S03").status, "open")

  def test_Set_StatusCrossingTheDoneBoundary_MovesTheSliceFile(self) -> None:
    # The bug this replaces: `set --status done` changed the field and left
    # the file in the open folder, which `verify` then reported as an error.
    with self.repo() as repo:
      repo.run("set", "S02", "--status", "done")
      self.assertTrue((repo.root / ".slicer/slices/done/S02.json").is_file())
      self.assertFalse((repo.root / ".slicer/slices/S02.json").exists())

  def test_Set_StatusCrossingTheDoneBoundary_LeavesVerifyClean(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S02", "--status", "done")
      code, out, _ = repo.run("verify")
      self.assertEqual(code, 0, out)
      self.assertNotIn("wrong folder", out)

  def test_Set_StatusBackToOpen_MovesTheFileBack(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S02", "--status", "done")
      repo.run("set", "S02", "--status", "open")
      self.assertTrue((repo.root / ".slicer/slices/S02.json").is_file())
      self.assertEqual(repo.run("verify")[0], 0)

  def test_Set_StatusUnchanged_LeavesTheFileWhereItIs(self) -> None:
    with self.repo() as repo:
      before = repo.state().find_slice_file("S02")
      repo.run("set", "S02", "--status", "open")
      self.assertEqual(repo.state().find_slice_file("S02"), before)

  def test_Set_FieldWithNoStatusChange_MovesNothing(self) -> None:
    with self.repo() as repo:
      before = repo.state().find_slice_file("S02")
      repo.run("set", "S02", "--size", "L")
      self.assertEqual(repo.state().find_slice_file("S02"), before)

  def test_Set_StatusAndAField_WriteOneLogEntryCarryingTheTransition(self) -> None:
    with self.repo() as repo:
      before = len(repo.state().history())
      repo.run("set", "S02", "--status", "done", "--size", "L")
      history = repo.state().history()
      self.assertEqual(len(history) - before, 1)
      entry = history[-1]
      self.assertEqual((entry.action, entry.frm, entry.to), ("set", "open", "done"))
      self.assertEqual(entry.note, "size,status")

  def test_Set_WithoutAStatusChange_LeavesTheTransitionBlank(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S02", "--size", "L")
      entry = repo.state().history()[-1]
      self.assertEqual((entry.action, entry.frm, entry.to), ("set", "", ""))

  def test_Done_StillWritesAStatusEntry(self) -> None:
    # `done`, `park` and `unpark` keep their own shape; only `set` changed.
    with self.repo() as repo:
      repo.run("done", "S02")
      self.assertEqual(repo.state().history()[-1].action, "status")

  def test_Retire_MovesTheFileThroughTheSameOnePath(self) -> None:
    with self.repo() as repo:
      repo.run("remove", "S02", "--reason", "superseded")
      self.assertTrue((repo.root / ".slicer/slices/retired/S02.json").is_file())
      self.assertEqual(repo.run("verify")[0], 0)

  def test_Set_Findings_UpdatesTheSliceHeaderToo(self) -> None:
    # The bug this replaces: the roadmap row changed and the slice kept the
    # old text, because nothing kept the two copies in step after `promote`.
    with self.repo() as repo:
      repo.run("set", "S02", "--findings", "G9")
      self.assertEqual(repo.state().slices["S02"].findings_note, "G9")
      _, out, _ = repo.run("show", "S02")
      self.assertIn("G9", out)

  def test_Set_Size_UpdatesTheSlice(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S02", "--size", "L")
      self.assertEqual(repo.state().slices["S02"].size, "L")

  def test_Set_Trees_UpdateTheSliceNoteAndPlural(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S02", "--tree", "alpha", "--tree", "beta")
      sl = repo.state().slices["S02"]
      self.assertEqual(sl.trees_note, "alpha, beta")
      self.assertTrue(sl.trees_plural)

  def test_Set_OneTree_MakesTheSliceSingular(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S02", "--tree", "alpha")
      sl = repo.state().slices["S02"]
      self.assertEqual(sl.trees_note, "alpha")
      self.assertFalse(sl.trees_plural)

  def test_Set_Title_UpdatesTheSlice(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S02", "--title", "A clearer title")
      self.assertEqual(repo.state().slices["S02"].title, "A clearer title")

  def test_Set_ShortTitle_LeavesTheSliceTitleAlone(self) -> None:
    # The index cell and the slice H1 differ on purpose.
    with self.repo() as repo:
      before = repo.state().slices["S02"].title
      repo.run("set", "S02", "--short-title", "Short")
      self.assertEqual(repo.state().slices["S02"].title, before)
      self.assertEqual(repo.state().index.require("S02").short_title, "Short")

  def test_Set_IndexOnlyField_DoesNotRewriteTheSliceFile(self) -> None:
    with self.repo() as repo:
      path = repo.state().find_slice_file("S02")
      before = path.read_bytes()
      repo.run("set", "S02", "--pass", "9")
      self.assertEqual(path.read_bytes(), before)

  def test_Set_OnAnUnpromotedItem_ChangesTheRowAndNothingElse(self) -> None:
    with self.repo() as repo:
      repo.run("add", "No slice here")
      code, _, err = repo.run("set", "S05", "--findings", "G9")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().index.require("S05").findings, "G9")
      self.assertIsNone(repo.state().find_slice_file("S05"))

  def test_Set_StatusAndFindings_SyncsIntoTheMovedFile(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S02", "--status", "done", "--findings", "G9")
      self.assertTrue((repo.root / ".slicer/slices/done/S02.json").is_file())
      self.assertEqual(repo.state().slices["S02"].findings_note, "G9")

  def test_Add_BlankTitle_IsRefusedWithoutWriting(self) -> None:
    with self.repo() as repo:
      before = repo.read(".slicer/index.json")
      code, _, err = repo.run("add", "")
      self.assertEqual(code, 2)
      self.assertIn("cannot be blank", err)
      self.assertEqual(repo.read(".slicer/index.json"), before)

  def test_Add_WhitespaceTitle_IsRefused(self) -> None:
    with self.repo() as repo:
      self.assertEqual(repo.run("add", "   ")[0], 2)

  def test_Add_BlankTitle_DoesNotConsumeAnId(self) -> None:
    # The id is allocated before the item is built, so a late refusal would
    # burn one.
    with self.repo() as repo:
      before = repo.state().index.next_id
      repo.run("add", "")
      self.assertEqual(repo.state().index.next_id, before)

  def test_Add_TitleWithANewline_IsRefused(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("add", "one\ntwo")
      self.assertEqual(code, 2)
      self.assertIn("newline", err)

  def test_Add_NewlineInAnyOneLineField_IsRefused(self) -> None:
    with self.repo() as repo:
      for flag in ("--size", "--findings", "--pass"):
        with self.subTest(flag):
          self.assertEqual(repo.run("add", "ok", flag, "a\nb")[0], 2)

  def test_Add_NewlineInATree_IsRefused(self) -> None:
    with self.repo() as repo:
      self.assertEqual(repo.run("add", "ok", "--tree", "a\nb")[0], 2)

  def test_Set_BlankTitle_IsRefused(self) -> None:
    # `--title ""` arrives as "" rather than None, so it reaches the field.
    with self.repo() as repo:
      code, _, err = repo.run("set", "S02", "--title", "")
      self.assertEqual(code, 2)
      self.assertIn("cannot be blank", err)
      self.assertTrue(repo.state().index.require("S02").title)

  def test_Set_BlankFindings_IsStillAllowed(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("set", "S02", "--findings", "")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().index.require("S02").findings, "")

  def test_Set_NewlineInAField_IsRefusedBeforeAnythingIsApplied(self) -> None:
    with self.repo() as repo:
      before = repo.state().index.require("S02").size
      code, _, err = repo.run("set", "S02", "--size", "XL", "--findings", "a\nb")
      self.assertEqual(code, 2)
      self.assertIn("newline", err)
      self.assertEqual(repo.state().index.require("S02").size, before)

  def test_Set_UnknownStatus_RefusesAndListsTheKnownOnes(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("set", "S02", "--status", "nonsense")
      self.assertEqual(code, 2)
      self.assertIn("parked", err)

  def test_Next_BlockedFirstItem_SkipsToTheNextEligibleOne(self) -> None:
    with self.repo() as repo:
      repo.run("unpark", "S03")
      repo.run("set", "S02", "--depends-on", "S03")
      code, out, _ = repo.run("next")
      self.assertEqual(code, 0)
      self.assertTrue(out.startswith("S03"), out)

  def test_Next_EveryOpenItemBlocked_ExitsTwoAndNamesTheBlockers(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S02", "--depends-on", "S03")
      code, out, _ = repo.run("next")
      self.assertEqual(code, 2)
      self.assertIn("S03", out)

  def test_Edit_UnpromotedItem_RefusesAndSaysWhatToDo(self) -> None:
    with self.repo() as repo:
      repo.run("add", "an idea")
      code, _, err = repo.run("edit", "S05", "--section", "Why", "--stdin")
      self.assertEqual(code, 2)
      self.assertIn("promote", err)

  def test_Show_Section_ReturnsOneSectionBody(self) -> None:
    with self.repo() as repo:
      repo.run("add", "an idea")
      repo.write("draft.md", "## an idea\n\n### Why\nThe reason.\n\n### Implement\nThe plan.\n")
      repo.run("promote", "S05", "--file", str(repo.root / "draft.md"))
      code, out, err = repo.run("show", "S05", "--section", "Why")
      self.assertEqual(code, 0, err)
      self.assertEqual(out.strip(), "The reason.")
      self.assertNotIn("The plan.", out)

  def test_Show_MissingSection_ErrorsClearly(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("show", "S02", "--section", "Nonexistent")
      self.assertEqual(code, 2)
      self.assertIn("Nonexistent", err)

  def test_Show_SectionOnUnpromotedItem_RefusesLikeEdit(self) -> None:
    with self.repo() as repo:
      repo.run("add", "an idea")
      code, _, err = repo.run("show", "S05", "--section", "Why")
      self.assertEqual(code, 2)
      self.assertIn("promote", err)

  def test_Git_WritingSubcommand_IsRefusedByTheAllowlist(self) -> None:
    with self.repo(git=True) as repo:
      for forbidden in ("commit", "push", "tag"):
        with self.subTest(forbidden):
          with self.assertRaises(StateError):
            vcs._run(repo.root, forbidden, "--any")


class DependencyTests(unittest.TestCase):
  def index(self):
    with support.TempRepo() as repo:
      support.make_mini(repo)
      repo.run("init")
      repo.run("migrate", "--from", "docs/slices")
      return repo.state().index

  def test_Cycle_SelfDependency_IsDetected(self) -> None:
    index = self.index()
    index.require("S02").depends_on = ["S02"]
    self.assertEqual(graph.cycles(index), [["S02", "S02"]])

  def test_Cycle_TwoNodeCycle_IsDetectedAndPathReported(self) -> None:
    index = self.index()
    index.require("S02").depends_on = ["S03"]
    index.require("S03").depends_on = ["S02"]
    self.assertTrue(any(set(c) == {"S02", "S03"} for c in graph.cycles(index)))

  def test_Cycle_DiamondWithoutACycle_IsNotReported(self) -> None:
    index = self.index()
    index.require("S02").depends_on = ["S01"]
    index.require("S03").depends_on = ["S01"]
    index.require("S04").depends_on = ["S02", "S03"]
    self.assertEqual(graph.cycles(index), [])

  def test_Depends_UnknownId_IsReportedAsDangling(self) -> None:
    index = self.index()
    index.require("S02").depends_on = ["S99"]
    self.assertIn(("S02", "S99"), graph.dangling(index))

  def test_Blocked_DependencyAlreadyDone_IsNotBlocking(self) -> None:
    index = self.index()
    index.require("S02").depends_on = ["S01"]
    self.assertEqual(graph.blocked_by(index, index.require("S02"), "done"), [])


if __name__ == "__main__":
  unittest.main()


class SetFlagGroupTests(unittest.TestCase):
  """flags and group are settable from the CLI, not only via import (S12)."""

  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.run("add", "a thing")
    return repo

  def test_Set_Flag_IsStored(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S01", "--flag", "OWNER")
      self.assertEqual(repo.state().index.require("S01").flags, ["OWNER"])

  def test_Set_Flag_ExcludesFromTheSyncPointer(self) -> None:
    with self.repo() as repo:
      repo.run("add", "another")
      # exclude OWNER-flagged items from the derived next pointer
      path = repo.root / ".slicer/config.json"
      import json as _json
      cfg = _json.loads(path.read_text())
      cfg["exclude_flags"] = ["OWNER"]
      path.write_text(_json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
      repo.run("set", "S01", "--flag", "OWNER")
      from slicer import sync
      st = repo.state()
      self.assertEqual(sync.next_item(st.index, st.config).id, "S02")

  def test_Set_Flag_ReplacesTheList(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S01", "--flag", "A", "--flag", "B")
      self.assertEqual(repo.state().index.require("S01").flags, ["A", "B"])
      repo.run("set", "S01", "--flag", "C")
      self.assertEqual(repo.state().index.require("S01").flags, ["C"])

  def test_Set_NoFlags_ClearsThem(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S01", "--flag", "OWNER")
      repo.run("set", "S01", "--no-flags")
      self.assertEqual(repo.state().index.require("S01").flags, [])

  def test_Set_Group_IsStoredAndRendersALabelRow(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S01", "--group", "Phase 0 — groundwork")
      self.assertEqual(repo.state().index.require("S01").group, "Phase 0 — groundwork")
      repo.run("render")
      roadmap = repo.read(".slicer/render/ROADMAP.md")
      self.assertIn("| Phase 0 — groundwork |", roadmap)

  def test_Set_Group_EmptyClearsIt(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S01", "--group", "Phase 0")
      repo.run("set", "S01", "--group", "")
      self.assertEqual(repo.state().index.require("S01").group, "")

  def test_Set_FlagWithNewline_IsRefused(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("set", "S01", "--flag", "a\nb")
      self.assertEqual(code, 2)
      self.assertIn("newline", err)
