"""The id scheme: changeable until the first id is handed out, then frozen."""

from __future__ import annotations

import json
import unittest

import support


def set_scheme(repo: support.TempRepo, prefix: str, width: int) -> None:
  path = repo.root / ".slicer/config.json"
  cfg = json.loads(path.read_text())
  cfg["id"] = {"prefix": prefix, "width": width}
  path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class IdSchemeTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    return repo

  def test_Scheme_ChangedBeforeTheFirstItem_IsAdopted(self) -> None:
    with self.repo() as repo:
      set_scheme(repo, "TASK-", 3)
      repo.run("add", "A thing")
      self.assertEqual([i.id for i in repo.state().index.items], ["TASK-001"])

  def test_Scheme_ChangedBeforeTheFirstItem_PersistsIntoTheIndex(self) -> None:
    with self.repo() as repo:
      set_scheme(repo, "TASK-", 3)
      repo.run("add", "A thing")
      index = json.loads(repo.read(".slicer/index.json"))
      self.assertEqual((index["id_prefix"], index["id_width"]), ("TASK-", 3))

  def test_Scheme_AdoptedForImportToo(self) -> None:
    with self.repo() as repo:
      set_scheme(repo, "R", 4)
      repo.write("r.md", "## One\n\n## Two\n")
      repo.run("import", "r.md")
      self.assertEqual([i.id for i in repo.state().index.items], ["R0001", "R0002"])

  def test_Scheme_ChangedAfterItemsExist_DoesNotRenumber(self) -> None:
    with self.repo() as repo:
      repo.run("add", "A thing")
      set_scheme(repo, "TASK-", 3)
      self.assertEqual([i.id for i in repo.state().index.items], ["S01"])

  def test_Scheme_ChangedAfterItemsExist_IsAVerifyError(self) -> None:
    with self.repo() as repo:
      repo.run("add", "A thing")
      set_scheme(repo, "TASK-", 3)
      code, out, _ = repo.run("verify")
      self.assertEqual(code, 1)
      self.assertIn("does not match", out)
      self.assertIn("TASK-", out)
      self.assertIn("'S'", out)

  def test_Scheme_ChangedAfterItemsExist_FailsCheck(self) -> None:
    with self.repo() as repo:
      repo.run("add", "A thing")
      repo.run("render")
      set_scheme(repo, "TASK-", 3)
      code, out, _ = repo.run("check")
      self.assertEqual(code, 1)
      self.assertIn("id scheme", out)

  def test_Scheme_Matching_ReportsNothing(self) -> None:
    with self.repo() as repo:
      repo.run("add", "A thing")
      code, out, _ = repo.run("verify")
      self.assertEqual(code, 0, out)
      self.assertNotIn("id scheme", out)

  def test_Scheme_NewIdsAfterAdoption_KeepCounting(self) -> None:
    with self.repo() as repo:
      set_scheme(repo, "TASK-", 3)
      repo.run("add", "One")
      repo.run("add", "Two")
      self.assertEqual([i.id for i in repo.state().index.items], ["TASK-001", "TASK-002"])


if __name__ == "__main__":
  unittest.main()


BAD_IDS = [
  "../x",          # traversal: wrote outside the project
  "../../../pwned",
  "S/1",           # a subdirectory store.load never scans
  "S\\1",
  "/abs",          # an absolute right-hand operand discards the left
  "..",
  ".hidden",       # a leading dot makes a hidden file
  "a..b",
  "S|1",           # breaks the roadmap's Slice cell
  "S]1",
  "S(1)",
  "S 1",
  "",
]


class IdRuleTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    return repo

  def test_Rule_AcceptsEveryShapeSlicerProduces(self) -> None:
    from slicer import ids

    for good in ("S01", "TASK-001", "R0001", "HOTFIX", "S01a", "v1.2", "a_b"):
      with self.subTest(good):
        self.assertTrue(ids.is_valid(good))

  def test_Rule_RejectsEveryDangerousShape(self) -> None:
    from slicer import ids

    for bad in BAD_IDS:
      with self.subTest(bad):
        self.assertFalse(ids.is_valid(bad))

  def test_Add_BadExplicitId_IsRefused(self) -> None:
    with self.repo() as repo:
      for bad in BAD_IDS:
        with self.subTest(bad):
          code, _, err = repo.run("add", "thing", "--id", bad)
          self.assertEqual(code, 2, err)
          self.assertIn("not a usable id", err)

  def test_Add_BadExplicitId_WritesNothing(self) -> None:
    with self.repo() as repo:
      before = repo.read(".slicer/index.json")
      repo.run("add", "thing", "--id", "../../../pwned")
      self.assertEqual(repo.read(".slicer/index.json"), before)

  def test_Add_BadExplicitId_CarriesTheBadIdCode(self) -> None:
    with self.repo() as repo:
      _, out, _ = repo.run("add", "thing", "--id", "../x", "--json")
      self.assertEqual(json.loads(out)["error"]["code"], "bad_id")

  def test_Promote_TraversingId_WritesNothingOutsideTheProject(self) -> None:
    with self.repo() as repo:
      repo.run("add", "thing", "--id", "../../../pwned")
      repo.run("promote", "../../../pwned")
      outside = list(repo.root.parent.glob("pwned.json"))
      self.assertEqual(outside, [])

  def test_Scheme_HostilePrefix_IsRefusedAtLoad(self) -> None:
    # A prefix is the front of every generated id, so it is a path too.
    with self.repo() as repo:
      set_scheme(repo, "../", 2)
      code, _, err = repo.run("add", "thing")
      self.assertEqual(code, 2)
      self.assertIn("id.prefix", err)

  def test_SlicePath_HandEditedTraversingId_IsRefused(self) -> None:
    with self.repo() as repo:
      repo.run("add", "A thing")
      path = repo.root / ".slicer/index.json"
      data = json.loads(path.read_text())
      data["items"][0]["id"] = "../escapee"
      path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
      code, out, _ = repo.run("verify")
      self.assertEqual(code, 1)
      self.assertIn("cannot be used as a filename", out)

  def test_Check_StoredBadId_Fails(self) -> None:
    with self.repo() as repo:
      repo.run("add", "A thing")
      repo.run("render")
      path = repo.root / ".slicer/index.json"
      data = json.loads(path.read_text())
      data["items"][0]["id"] = "S/1"
      path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
      self.assertNotEqual(repo.run("check")[0], 0)


class CaseCollisionTests(unittest.TestCase):
  """Two ids differing only in case are one file on a case-insensitive disk."""

  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.run("add", "First")
    return repo

  def test_Add_IdDifferingOnlyInCase_IsRefused(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("add", "Second", "--id", "s01")
      self.assertEqual(code, 2)
      self.assertIn("only in case", err)

  def test_Add_IdDifferingOnlyInCase_DoesNotSayAlreadyExists(self) -> None:
    # It genuinely is not in the index; saying so would send the reader looking
    # for an item that is not there.
    with self.repo() as repo:
      _, _, err = repo.run("add", "Second", "--id", "s01")
      self.assertNotIn("already exists", err)

  def test_Add_IdDifferingOnlyInCase_CarriesItsOwnCode(self) -> None:
    with self.repo() as repo:
      _, out, _ = repo.run("add", "Second", "--id", "s01", "--json")
      self.assertEqual(json.loads(out)["error"]["code"], "case_collision")

  def test_Add_ExactDuplicate_StillSaysAlreadyExists(self) -> None:
    with self.repo() as repo:
      _, _, err = repo.run("add", "Second", "--id", "S01")
      self.assertIn("already exists", err)


class NextIdIntegrityTests(unittest.TestCase):
  """next_id must stay above every id in use, or `add` would mint a duplicate."""

  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.run("add", "first")
    return repo

  def lower_next_id(self, repo: support.TempRepo, to: int = 1) -> None:
    path = repo.root / ".slicer/index.json"
    data = json.loads(path.read_text())
    data["next_id"] = to
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")

  def test_Add_WithLoweredNextId_RefusesInsteadOfDuplicating(self) -> None:
    with self.repo() as repo:
      self.lower_next_id(repo)
      code, _, err = repo.run("add", "second")
      self.assertEqual(code, 2)
      self.assertIn("reuse", err)
      # no duplicate was written
      self.assertEqual([i.id for i in repo.state().index.items], ["S01"])

  def test_Add_WithLoweredNextId_CarriesTheCorruptCode(self) -> None:
    with self.repo() as repo:
      self.lower_next_id(repo)
      _, out, _ = repo.run("add", "second", "--json")
      self.assertEqual(json.loads(out)["error"]["code"], "corrupt")

  def test_Verify_LowNextId_IsAnError(self) -> None:
    with self.repo() as repo:
      self.lower_next_id(repo)
      code, out, _ = repo.run("verify")
      self.assertEqual(code, 1)
      self.assertIn("next_id", out)

  def test_Check_LowNextId_Fails(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      self.lower_next_id(repo)
      code, out, _ = repo.run("check")
      self.assertEqual(code, 1)
      self.assertIn("next_id", out)

  def test_Verify_HealthyNextId_ReportsNothingAboutIt(self) -> None:
    with self.repo() as repo:
      code, out, _ = repo.run("verify")
      self.assertEqual(code, 0, out)
      self.assertNotIn("next_id", out)

  def test_Add_NormalPath_StillAllocatesTheNextId(self) -> None:
    with self.repo() as repo:
      repo.run("add", "second")
      self.assertEqual([i.id for i in repo.state().index.items], ["S01", "S02"])
      self.assertEqual(repo.state().index.next_id, 3)
