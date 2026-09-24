"""User text must not be able to break the roadmap's markdown table."""

from __future__ import annotations

import json
import re
import unittest

import support

from slicer import render
from slicer.errors import RenderError

UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")


def cells(row: str) -> int:
  return len(UNESCAPED_PIPE.findall(row)) - 1


def row_for(repo: support.TempRepo, item_id: str) -> str:
  roadmap = repo.read(".slicer/render/ROADMAP.md")
  return next(l for l in roadmap.splitlines() if f"| {item_id} |" in l or f"[{item_id}]" in l)


def poke_index(repo: support.TempRepo, **fields: object) -> None:
  """Write straight into the index, to stage data that input validation now refuses."""
  path = repo.root / ".slicer/index.json"
  data = json.loads(path.read_text())
  data["items"][0].update(fields)
  path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class CellEscapingTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    return repo

  def test_Cell_Pipe_IsEscaped(self) -> None:
    self.assertEqual(render.cell("a|b"), r"a\|b")

  def test_Cell_Newline_BecomesASpace(self) -> None:
    self.assertEqual(render.cell("one\ntwo"), "one two")

  def test_Cell_WhitespaceRun_Collapses(self) -> None:
    self.assertEqual(render.cell("  a   b  "), "a b")

  def test_Row_PipeInTitle_KeepsSevenCells(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Fix the a|b parser", "--size", "M")
      repo.run("render")
      self.assertEqual(cells(row_for(repo, "S01")), 7)

  def test_Row_PipeInFindingsAndTrees_KeepsSevenCells(self) -> None:
    with self.repo() as repo:
      repo.run("add", "A thing", "--findings", "see x|y", "--tree", "core|cli")
      repo.run("render")
      self.assertEqual(cells(row_for(repo, "S01")), 7)

  def test_Row_PipeInAStatusLabel_KeepsSevenCells(self) -> None:
    # Labels come from config, so input validation cannot reach them.
    with self.repo() as repo:
      repo.run("add", "A thing")
      path = repo.root / ".slicer/config.json"
      cfg = json.loads(path.read_text())
      cfg["statuses"]["open"] = "a|b"
      path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
      repo.run("render")
      self.assertEqual(cells(row_for(repo, "S01")), 7)

  def test_Row_PipeInAGroupLabel_KeepsSevenCells(self) -> None:
    with self.repo() as repo:
      repo.write("r.md", "## A thing\ngroup: Phase 0 | groundwork\n")
      repo.run("import", "r.md")
      repo.run("render")
      roadmap = repo.read(".slicer/render/ROADMAP.md")
      group_row = next(l for l in roadmap.splitlines() if "Phase 0" in l)
      self.assertEqual(cells(group_row), 7)

  def test_Row_RetiredReasonWithAPipe_KeepsSevenCells(self) -> None:
    with self.repo() as repo:
      repo.run("add", "A thing")
      repo.run("remove", "S01", "--reason", "superseded by a|b")
      repo.run("render")
      self.assertEqual(cells(row_for(repo, "S01")), 7)


class RepairOnRenderTests(unittest.TestCase):
  """Data stored before input validation existed still has to render."""

  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.run("add", "A thing")
    return repo

  def test_Render_StoredBlankTitle_SaysUntitled(self) -> None:
    with self.repo() as repo:
      poke_index(repo, title="", short_title="")
      repo.run("render")
      self.assertIn("(untitled S01)", row_for(repo, "S01"))

  def test_Render_StoredMultilineTitle_RendersOnOneLine(self) -> None:
    with self.repo() as repo:
      poke_index(repo, title="line one\nline two", short_title="")
      repo.run("render")
      row = row_for(repo, "S01")
      self.assertIn("line one line two", row)
      self.assertEqual(cells(row), 7)

  def test_Render_StoredBadTitle_StillChecksClean(self) -> None:
    with self.repo() as repo:
      poke_index(repo, title="a|b\nc", short_title="")
      repo.run("render")
      self.assertEqual(repo.run("check")[0], 0)


class GeneratedRowAssertionTests(unittest.TestCase):
  def test_Render_RowTemplateWithTooFewCells_IsRefused(self) -> None:
    # The header is fixed in code while row.md is the project's to edit, so a
    # template of the wrong arity is already emitting a broken table.
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "A thing")
      repo.write(".slicer/templates/row.md", "| {{position}} | {{title}} |\n")
      code, _, err = repo.run("render")
      self.assertEqual(code, 2)
      self.assertIn("cells", err)

  def test_Check_BrokenRowTemplate_Fails(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "A thing")
      repo.run("render")
      repo.write(".slicer/templates/row.md", "| {{position}} | {{title}} |\n")
      self.assertNotEqual(repo.run("check")[0], 0)

  def test_CheckRow_MatchingHeader_Passes(self) -> None:
    header = "| # | Slice | Title | Size | Trees | Findings | Status |"
    render._check_row(r"| 1 | S01 | a\|b | M | core | F1 | — |", header)

  def test_CheckRow_EscapedPipesAreNotCellWalls(self) -> None:
    header = "| a | b |"
    render._check_row(r"| x\|y | z |", header)
    with self.assertRaises(RenderError):
      render._check_row("| x|y | z |", header)


if __name__ == "__main__":
  unittest.main()
