"""Retiring an obsolete item, and purging one that should never have existed."""

from __future__ import annotations

import json
import unittest

import support

from slicer import ops, sync
from slicer.config import Config
from slicer.errors import ConfigError


class RetireTests(unittest.TestCase):
  def repo(self, git: bool = False) -> support.TempRepo:
    repo = support.TempRepo(git=git)
    support.make_mini(repo)
    repo.run("init")
    repo.run("migrate", "--from", "docs/slices")
    return repo

  def test_Retire_OpenItem_SetsRetiredStatusAndMovesTheFile(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("remove", "S02", "--reason", "superseded by S03")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().index.require("S02").status, "retired")
      self.assertTrue((repo.root / ".slicer/slices/retired/S02.json").is_file())
      self.assertFalse((repo.root / ".slicer/slices/S02.json").exists())

  def test_Retire_Item_StoresTheReasonAndLogsIt(self) -> None:
    with self.repo() as repo:
      repo.run("remove", "S02", "--reason", "superseded by S03")
      self.assertEqual(repo.state().index.require("S02").reason, "superseded by S03")
      entry = repo.state().history()[-1]
      self.assertEqual((entry.item, entry.action, entry.to), ("S02", "retire", "retired"))
      self.assertEqual(entry.note, "superseded by S03")

  def test_Retire_InAGitRepo_UsesGitMvSoTheMoveStaysARename(self) -> None:
    with self.repo(git=True) as repo:
      repo.commit("import")
      repo.run("remove", "S02", "--reason", "obsolete")
      staged = repo._git("diff", "--cached", "--name-status", "-M")
      self.assertIn("S02.json", staged.stdout)
      self.assertTrue(staged.stdout.lstrip().startswith("R"), staged.stdout)

  def test_Retire_BlankReason_IsRefused(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("remove", "S02", "--reason", "   ")
      self.assertEqual(code, 2)
      self.assertIn("needs a reason", err)
      self.assertEqual(repo.state().index.require("S02").status, "open")

  def test_Retire_RetiredItem_StillAppearsInTheRenderedRoadmapWithItsReason(self) -> None:
    with self.repo() as repo:
      repo.run("remove", "S02", "--reason", "superseded by S03")
      repo.run("render")
      roadmap = repo.read(".slicer/render/ROADMAP.md")
      row = next(l for l in roadmap.splitlines() if "| S02 |" in l or "[S02]" in l)
      self.assertIn("retired", row)
      self.assertIn("superseded by S03", row)

  def test_Retire_RetiredItem_IsNotNamedByNext(self) -> None:
    with self.repo() as repo:
      self.assertTrue(repo.run("next")[1].startswith("S02"))
      repo.run("remove", "S02", "--reason", "obsolete")
      code, out, _ = repo.run("next")
      self.assertEqual(code, 2)
      self.assertNotIn("S02", out)

  def test_Retire_RetiredItem_DropsOutOfTheLaterPointer(self) -> None:
    with self.repo() as repo:
      cfg = repo.state().config
      before = sync.later_pointer(repo.state().index, cfg)
      self.assertIn("S04", before)
      repo.run("remove", "S04", "--reason", "obsolete")
      self.assertNotIn("S04", sync.later_pointer(repo.state().index, cfg))

  def test_Retire_DoneItem_RefusesWithoutForce(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("remove", "S01", "--reason", "obsolete")
      self.assertEqual(code, 2)
      self.assertIn("it is done", err)
      self.assertIn("--force", err)
      self.assertEqual(repo.state().index.require("S01").status, "done")

  def test_Retire_DoneItem_SucceedsWithForce(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("remove", "S01", "--reason", "obsolete", "--force")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().index.require("S01").status, "retired")

  def test_Retire_ItemWithDependents_RefusesNamingThem(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("remove", "S01", "--reason", "obsolete")
      self.assertIn("S02", err)


class PurgeTests(unittest.TestCase):
  def repo(self, git: bool = False) -> support.TempRepo:
    repo = support.TempRepo(git=git)
    support.make_mini(repo)
    repo.run("init")
    repo.run("migrate", "--from", "docs/slices")
    return repo

  def test_Purge_LastAllocatedId_RemovesTheItemAndFreesTheId(self) -> None:
    with self.repo() as repo:
      repo.run("add", "a typo")
      self.assertEqual(repo.state().index.next_id, 6)
      code, out, err = repo.run("remove", "S05", "--purge")
      self.assertEqual(code, 0, err)
      self.assertIsNone(repo.state().index.get("S05"))
      self.assertEqual(repo.state().index.next_id, 5)
      self.assertIn("freed", out)

  def test_Purge_FreedId_IsHandedOutAgainByTheNextAdd(self) -> None:
    with self.repo() as repo:
      repo.run("add", "a typo")
      repo.run("remove", "S05", "--purge")
      repo.run("add", "the real one")
      self.assertEqual(repo.state().index.require("S05").title, "the real one")

  def test_Purge_EarlierId_RemovesTheItemButKeepsTheIdBurned(self) -> None:
    with self.repo() as repo:
      before = repo.state().index.next_id
      code, out, err = repo.run("remove", "S02", "--purge")
      self.assertEqual(code, 0, err)
      self.assertIsNone(repo.state().index.get("S02"))
      self.assertEqual(repo.state().index.next_id, before)
      self.assertIn("not the most recent", out)

  def test_Purge_IdNamedInACommitSubject_KeepsTheIdBurned(self) -> None:
    with self.repo(git=True) as repo:
      repo.run("add", "a published idea")
      repo.commit("land S05 the published idea")
      code, out, err = repo.run("remove", "S05", "--purge")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().index.next_id, 6)
      self.assertIn("commit subject", out)

  def test_Purge_OutsideAGitRepo_SkipsTheHistoryCheckAndFreesTheId(self) -> None:
    with self.repo(git=False) as repo:
      repo.run("add", "a typo")
      repo.run("remove", "S05", "--purge")
      self.assertEqual(repo.state().index.next_id, 5)

  def test_Purge_Item_DeletesItsSliceFile(self) -> None:
    with self.repo() as repo:
      self.assertTrue((repo.root / ".slicer/slices/S02.json").is_file())
      repo.run("remove", "S02", "--purge")
      self.assertFalse((repo.root / ".slicer/slices/S02.json").exists())

  def test_Purge_ItemWithDependents_RefusesNamingThem(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("remove", "S01", "--purge")
      self.assertEqual(code, 2)
      self.assertIn("S02", err)
      self.assertIn("--force", err)
      self.assertIsNotNone(repo.state().index.get("S01"))

  def test_Purge_WithForce_ThenCheck_ReportsTheDanglingDependency(self) -> None:
    with self.repo() as repo:
      self.assertEqual(repo.run("remove", "S01", "--purge", "--force")[0], 0)
      code, out, _ = repo.run("check")
      self.assertEqual(code, 1)
      self.assertIn("S01", out)

  def test_Purge_UnknownId_ExitsTwo(self) -> None:
    with self.repo() as repo:
      self.assertEqual(repo.run("remove", "S99", "--purge")[0], 2)

  def test_Purge_Item_LogsWhatHappenedToTheId(self) -> None:
    with self.repo() as repo:
      repo.run("remove", "S02", "--purge")
      entry = repo.state().history()[-1]
      self.assertEqual((entry.item, entry.action), ("S02", "purge"))
      self.assertIn("most recent", entry.note)


class RemoveCliTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    support.make_mini(repo)
    repo.run("init")
    repo.run("migrate", "--from", "docs/slices")
    return repo

  def test_Remove_WithNeitherReasonNorPurge_ExitsTwo(self) -> None:
    with self.repo() as repo:
      with self.assertRaises(SystemExit) as caught:
        repo.run("remove", "S02")
      self.assertEqual(caught.exception.code, 2)

  def test_Remove_WithBothReasonAndPurge_ExitsTwo(self) -> None:
    with self.repo() as repo:
      with self.assertRaises(SystemExit) as caught:
        repo.run("remove", "S02", "--purge", "--reason", "x")
      self.assertEqual(caught.exception.code, 2)

  def test_Remove_PurgeJson_ReportsWhetherTheIdWasFreed(self) -> None:
    with self.repo() as repo:
      repo.run("add", "a typo")
      _, out, _ = repo.run("remove", "S05", "--purge", "--json")
      payload = json.loads(out)
      self.assertEqual(payload["mode"], "purge")
      self.assertTrue(payload["id_freed"])
      self.assertTrue(payload["file_removed"] is False)

  def test_Remove_RetireJson_CarriesTheReasonAndStatus(self) -> None:
    with self.repo() as repo:
      _, out, _ = repo.run("remove", "S02", "--reason", "obsolete", "--json")
      payload = json.loads(out)
      self.assertEqual(payload["mode"], "retire")
      self.assertEqual(payload["status"], "retired")
      self.assertEqual(payload["fields"]["reason"], "obsolete")


class RetiredStatusConfigTests(unittest.TestCase):
  BASE = {
    "version": 1,
    "id": {"prefix": "S", "width": 2},
    "open_status": "open",
    "done_status": "done",
    "sections": ["Why"],
    "done_dir": "done",
  }

  def test_Config_WithoutARetiredStatus_GainsOnlyThatOneKey(self) -> None:
    cfg = Config.from_dict(
      self.BASE | {"statuses": {"open": "—", "done": "done", "parked": "parked"}}
    )
    self.assertEqual(sorted(cfg.statuses), ["done", "open", "parked", "retired"])
    self.assertNotIn("later", cfg.statuses)
    self.assertEqual(cfg.retired_status, "retired")

  def test_Config_WithACustomRetiredStatusName_UsesIt(self) -> None:
    cfg = Config.from_dict(
      self.BASE
      | {
        "statuses": {"open": "—", "done": "done", "dropped": "dropped"},
        "retired_status": "dropped",
      }
    )
    self.assertEqual(cfg.retired_status, "dropped")
    self.assertEqual(sorted(cfg.statuses), ["done", "dropped", "open"])

  def test_Config_RetiredDirEqualToDoneDir_IsRejected(self) -> None:
    with self.assertRaises(ConfigError):
      Config.from_dict(self.BASE | {"statuses": dict(Config().statuses), "retired_dir": "done"})

  def test_Config_ExistingProjectConfig_LoadsWithoutBeingEdited(self) -> None:
    """A tracking directory written before `remove` existed must still load."""
    with support.TempRepo() as repo:
      support.make_mini(repo)
      repo.run("init")
      path = repo.root / ".slicer/config.json"
      stored = json.loads(path.read_text())
      stored["statuses"].pop("retired", None)
      stored.pop("retired_status", None)
      stored.pop("retired_dir", None)
      path.write_text(json.dumps(stored, ensure_ascii=False, indent=2) + "\n")
      code, _, err = repo.run("migrate", "--from", "docs/slices")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.run("render")[0], 0)
      self.assertEqual(repo.run("check")[0], 0)
      # and the newly available status works without the config being edited
      self.assertEqual(repo.run("remove", "S02", "--reason", "obsolete")[0], 0)
      self.assertEqual(repo.state().index.require("S02").status, "retired")


if __name__ == "__main__":
  unittest.main()
