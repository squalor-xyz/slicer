"""Parsing the index tables, and keeping every word of prose around them."""

from __future__ import annotations

import unittest

import support  # noqa: F401

from slicer import legacy
from slicer.errors import LegacyImportError


class IndexTableTests(unittest.TestCase):
  def parse(self, text: str = support.MINI_INDEX) -> legacy.LegacyIndex:
    return legacy.parse_index(text, path="<test>")

  def test_ParseIndex_TwoTables_YieldsRowsInFileOrder(self) -> None:
    index = self.parse()
    self.assertEqual(len(index.tables()), 2)
    items = [r.item_id for r in index.rows() if r.kind == "item"]
    self.assertEqual(items, ["S01", "S02", "S03", "S04"])

  def test_ParseIndex_PhaseGroupRow_RecordedAsGroupNotItem(self) -> None:
    groups = [r for r in self.parse().rows() if r.kind == "group"]
    self.assertEqual(len(groups), 1)
    self.assertIn("Phase 0", groups[0].cells[0])

  def test_ParseIndex_StatusCells_ReadVerbatim(self) -> None:
    rows = [r for r in self.parse().rows() if r.kind == "item"]
    self.assertEqual([r.cells[5] for r in rows], ["done", "—", "parked", "later"])

  def test_ParseIndex_DoneRowLink_PointsIntoDoneFolder(self) -> None:
    rows = {r.item_id: r for r in self.parse().rows() if r.kind == "item"}
    self.assertTrue(rows["S01"].link.startswith("done/"))
    self.assertFalse(rows["S02"].link.startswith("done/"))

  def test_ParseIndex_WrongCellCount_RaisesWithLineNumber(self) -> None:
    broken = support.MINI_INDEX.replace("| [S02](S02-second-thing.md) | Second thing | S |", "| oops |")
    with self.assertRaises(LegacyImportError) as caught:
      legacy.parse_index(broken, path="idx.md")
    self.assertIn("idx.md:", str(caught.exception))

  def test_EmitIndex_ParsedIndex_ReproducesInputBytes(self) -> None:
    self.assertEqual(self.parse().emit(), support.MINI_INDEX)


if __name__ == "__main__":
  unittest.main()
