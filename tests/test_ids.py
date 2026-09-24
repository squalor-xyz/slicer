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
