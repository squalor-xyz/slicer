"""`slicer note` appends a dated note to the item; it needs no slice."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

import support


def _today() -> str:
  return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class NoteTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    support.make_mini(repo)
    repo.run("init")
    repo.run("migrate", "--from", "docs/slices")
    return repo

  def _notes(self, repo: support.TempRepo, item_id: str) -> list[str]:
    return repo.state().index.require(item_id).notes

  def test_Note_Text_AppendsADatedParagraphVisibleEverywhere(self) -> None:
    with self.repo() as repo:
      before = len(self._notes(repo, "S02"))
      code, _, err = repo.run("note", "S02", "--text", "tried X, it did not work", "--render")
      self.assertEqual(code, 0, err)
      notes = self._notes(repo, "S02")
      self.assertEqual(len(notes), before + 1)
      self.assertEqual(notes[-1], f"**{_today()}** — tried X, it did not work")
      self.assertIn("tried X, it did not work", repo.run("show", "S02")[1])
      rendered = repo.read(".slicer/render/slices/S02.md")
      self.assertIn("tried X, it did not work", rendered)
      self.assertIn(f"**{_today()}**", rendered)

  def test_Note_CalledTwice_AccumulatesInOrder(self) -> None:
    with self.repo() as repo:
      before = len(self._notes(repo, "S02"))
      repo.run("note", "S02", "--text", "first")
      repo.run("note", "S02", "--text", "second")
      notes = self._notes(repo, "S02")
      self.assertEqual(len(notes), before + 2)
      self.assertTrue(notes[-2].endswith("first"))
      self.assertTrue(notes[-1].endswith("second"))

  def test_Note_FromAFile_AppendsTheFileBody(self) -> None:
    with self.repo() as repo:
      repo.write("n.md", "a note from a file\n")
      code, _, err = repo.run("note", "S02", "--file", str(repo.root / "n.md"))
      self.assertEqual(code, 0, err)
      self.assertTrue(self._notes(repo, "S02")[-1].endswith("a note from a file"))

  def test_Note_OnItemWithoutASlice_IsAllowedAndShows(self) -> None:
    with self.repo() as repo:
      repo.run("add", "no slice yet")  # S05, a bare row
      code, _, err = repo.run("note", "S05", "--text", "context before promoting")
      self.assertEqual(code, 0, err)
      self.assertTrue(self._notes(repo, "S05")[-1].endswith("context before promoting"))
      self.assertIn("context before promoting", repo.run("show", "S05")[1])

  def test_Note_Blank_IsRefused(self) -> None:
    with self.repo() as repo:
      code, out, _ = repo.run("note", "S02", "--text", "   ", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out)["error"]["code"], "usage")

  def test_Note_UnknownItem_IsRefused(self) -> None:
    with self.repo() as repo:
      code, out, _ = repo.run("note", "S99", "--text", "x", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out)["error"]["code"], "no_such_item")

  def test_Note_WithRender_KeepsCheckGreen(self) -> None:
    with self.repo() as repo:
      repo.run("note", "S02", "--text", "keeps check green", "--render")
      self.assertEqual(repo.run("check")[0], 0)


if __name__ == "__main__":
  unittest.main()
