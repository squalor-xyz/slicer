"""`slicer note` appends a dated paragraph that shows up in show and render."""

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

  def test_Note_Text_AppendsADatedParagraphVisibleEverywhere(self) -> None:
    with self.repo() as repo:
      before = len(repo.state().slices["S02"].notes)
      code, _, err = repo.run("note", "S02", "--text", "tried X, it did not work", "--render")
      self.assertEqual(code, 0, err)
      notes = repo.state().slices["S02"].notes
      self.assertEqual(len(notes), before + 1)
      self.assertEqual(notes[-1], f"**{_today()}** — tried X, it did not work")
      self.assertIn("tried X, it did not work", repo.run("show", "S02")[1])
      rendered = repo.read(".slicer/render/slices/S02.md")
      self.assertIn("tried X, it did not work", rendered)
      self.assertIn(f"**{_today()}**", rendered)

  def test_Note_CalledTwice_AccumulatesInOrder(self) -> None:
    with self.repo() as repo:
      before = len(repo.state().slices["S02"].notes)
      repo.run("note", "S02", "--text", "first")
      repo.run("note", "S02", "--text", "second")
      notes = repo.state().slices["S02"].notes
      self.assertEqual(len(notes), before + 2)
      self.assertTrue(notes[-2].endswith("first"))
      self.assertTrue(notes[-1].endswith("second"))

  def test_Note_FromAFile_AppendsTheFileBody(self) -> None:
    with self.repo() as repo:
      repo.write("n.md", "a note from a file\n")
      code, _, err = repo.run("note", "S02", "--file", str(repo.root / "n.md"))
      self.assertEqual(code, 0, err)
      self.assertTrue(repo.state().slices["S02"].notes[-1].endswith("a note from a file"))

  def test_Note_OnItemWithoutASlice_IsRefused(self) -> None:
    with self.repo() as repo:
      repo.run("add", "no slice yet")  # S05, a bare row
      code, out, _ = repo.run("note", "S05", "--text", "x", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out)["error"]["code"], "no_slice")

  def test_Note_Blank_IsRefused(self) -> None:
    with self.repo() as repo:
      code, out, _ = repo.run("note", "S02", "--text", "   ", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out)["error"]["code"], "usage")

  def test_Note_WithRender_KeepsCheckGreen(self) -> None:
    with self.repo() as repo:
      repo.run("note", "S02", "--text", "keeps check green", "--render")
      self.assertEqual(repo.run("check")[0], 0)


if __name__ == "__main__":
  unittest.main()
