"""`set` and `add` refuse a dependency edge `check` would reject (S131), so a
mutation that exits 0 cannot leave the roadmap failing its own gate."""

from __future__ import annotations

import json
import unittest

import support
from slicer.store import DIR_NAME, INDEX_NAME


class DependsValidationTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    for title in ("one", "two", "three"):
      repo.run("add", title)
    return repo

  def index_bytes(self, repo: support.TempRepo) -> bytes:
    return (repo.root / DIR_NAME / INDEX_NAME).read_bytes()

  def assertRefused(self, repo: support.TempRepo, code: str, *argv: str) -> str:
    before = self.index_bytes(repo)
    exit_code, out, _ = repo.run(*argv, "--json")
    self.assertEqual(exit_code, 2, out)
    error = json.loads(out)["error"]
    self.assertEqual(error["code"], code)
    self.assertEqual(self.index_bytes(repo), before)  # nothing written
    return error["message"]

  def test_Set_CommaList_IsRefusedNamingTheIdAndRepeatableFlag(self) -> None:
    with self.repo() as repo:
      message = self.assertRefused(repo, "no_such_item", "set", "S03", "--depends-on", "S01,S02")
      self.assertIn("S01,S02", message)
      self.assertIn("repeatable", message)

  def test_Set_UnknownId_IsRefused(self) -> None:
    with self.repo() as repo:
      message = self.assertRefused(repo, "no_such_item", "set", "S03", "--depends-on", "S99")
      self.assertIn("S99", message)

  def test_Set_RefusedWithOtherFields_LeavesEveryFieldUnchanged(self) -> None:
    with self.repo() as repo:
      self.assertRefused(
        repo, "no_such_item", "set", "S02", "S03",
        "--urgency", "3", "--title", "renamed", "--depends-on", "S99",
      )
      items = {it.id: it for it in repo.state().index.items}
      self.assertEqual(items["S03"].title, "three")
      self.assertEqual(items["S03"].urgency, 2)
      self.assertEqual(items["S02"].depends_on, [])

  def test_Set_RepeatedValidIds_StillLand(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("set", "S03", "--depends-on", "S01", "--depends-on", "S02")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().index.require("S03").depends_on, ["S01", "S02"])

  def test_Set_SelfEdge_IsRefused(self) -> None:
    with self.repo() as repo:
      message = self.assertRefused(repo, "state", "set", "S02", "--depends-on", "S02")
      self.assertIn("itself", message)

  def test_Set_EdgeClosingACycle_IsRefusedWithThePath(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S02", "--depends-on", "S01")
      repo.run("set", "S03", "--depends-on", "S02")
      message = self.assertRefused(repo, "state", "set", "S01", "--depends-on", "S03")
      self.assertIn("S01 -> S03 -> S02 -> S01", message)

  def test_Set_RewiringInTheRightOrder_IsAllowed(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S02", "--depends-on", "S01")
      self.assertEqual(repo.run("set", "S02", "--depends-on", "")[0], 0)
      code, _, err = repo.run("set", "S01", "--depends-on", "S02")
      self.assertEqual(code, 0, err)

  def test_Set_RetiredDependency_IsRefused(self) -> None:
    with self.repo() as repo:
      repo.run("remove", "S01", "--reason", "obsolete")
      message = self.assertRefused(repo, "state", "set", "S02", "--depends-on", "S01")
      self.assertIn("retired", message)

  def test_Set_RetiredItem_MayDependOnRetired(self) -> None:
    with self.repo() as repo:
      repo.run("remove", "S01", "--reason", "obsolete")
      repo.run("remove", "S02", "--reason", "obsolete")
      code, _, err = repo.run("set", "S02", "--depends-on", "S01")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().index.require("S02").depends_on, ["S01"])

  def test_Set_DoneItem_MayDependOnRetired(self) -> None:
    with self.repo() as repo:
      repo.run("done", "S02", "--note", "landed")
      repo.run("remove", "S01", "--reason", "obsolete")
      code, _, err = repo.run("set", "S02", "--depends-on", "S01")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().index.require("S02").depends_on, ["S01"])

  def test_Set_RetiredItem_UnknownDependency_IsStillRefused(self) -> None:
    with self.repo() as repo:
      repo.run("remove", "S02", "--reason", "obsolete")
      message = self.assertRefused(repo, "no_such_item", "set", "S02", "--depends-on", "S99")
      self.assertIn("S99", message)

  def test_Set_RetiredItems_Cycle_IsStillRefused(self) -> None:
    with self.repo() as repo:
      repo.run("remove", "S01", "--reason", "obsolete")
      repo.run("remove", "S02", "--reason", "obsolete")
      self.assertEqual(repo.run("set", "S02", "--depends-on", "S01")[0], 0)
      message = self.assertRefused(repo, "state", "set", "S01", "--depends-on", "S02")
      self.assertIn("cycle", message)

  def test_Set_UnrelatedField_IgnoresAPreexistingDanglingEdge(self) -> None:
    with self.repo() as repo:
      path = repo.root / DIR_NAME / INDEX_NAME
      data = json.loads(path.read_text(encoding="utf-8"))
      data["items"][1]["depends_on"] = ["S99"]  # state written before this guard
      path.write_text(json.dumps(data, indent=2), encoding="utf-8")
      code, _, err = repo.run("set", "S02", "--urgency", "3", "--render")
      self.assertEqual(code, 0, err)
      code, out, _ = repo.run("check")
      self.assertNotEqual(code, 0)
      self.assertIn("S99", out)

  def test_Add_UnknownDependency_IsRefusedAndNoItemAdded(self) -> None:
    with self.repo() as repo:
      self.assertRefused(repo, "no_such_item", "add", "four", "--depends-on", "S99")
      self.assertIsNone(repo.state().index.get("S04"))

  def test_Add_ValidDependency_Lands(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("add", "four", "--depends-on", "S01")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().index.require("S04").depends_on, ["S01"])

  def test_Add_ExplicitIdClosingACycle_IsRefused(self) -> None:
    with self.repo() as repo:
      path = repo.root / DIR_NAME / INDEX_NAME
      data = json.loads(path.read_text(encoding="utf-8"))
      data["items"][0]["depends_on"] = ["S09"]  # S01 already waits on a future S09
      path.write_text(json.dumps(data, indent=2), encoding="utf-8")
      message = self.assertRefused(repo, "state", "add", "nine", "--id", "S09", "--depends-on", "S01")
      self.assertIn("cycle", message)


if __name__ == "__main__":
  unittest.main()
