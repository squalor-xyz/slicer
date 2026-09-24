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
    repo.run("import", "--from", "docs/slices")
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
      self.assertIsNotNone(sl.boundary(repo.state().config.boundary))

  def test_Promote_AlreadyPromoted_RefusesWithoutForce(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("promote", "S02")
      self.assertEqual(code, 2)
      self.assertIn("--force", err)

  def test_Move_ItemBeforeAnother_ReordersTheQueueOnly(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("move", "S04", "--before", "S01")
      self.assertEqual(code, 0, err)
      self.assertEqual([i.id for i in repo.state().index.items], ["S04", "S01", "S02", "S03"])

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
      repo.run("import", "--from", "docs/slices")
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
