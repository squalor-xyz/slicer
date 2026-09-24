"""Parsing one slice file, and proving we can write it back unchanged."""

from __future__ import annotations

import unittest

import support  # noqa: F401  (puts src/ on sys.path)

from slicer import legacy
from slicer.errors import LegacyImportError


class SliceFileTests(unittest.TestCase):
  def parse(self, text: str) -> legacy.LegacySlice:
    return legacy.parse_slice(text, path="<test>")

  def test_ParseSlice_StandardFile_ExtractsIdTitleAndSections(self) -> None:
    sl = self.parse(support.MINI_SLICES["done/S01-first-thing.md"])
    self.assertEqual(sl.id, "S01")
    self.assertEqual(sl.title, "first thing, spelled out at length")
    self.assertEqual([h for h, _ in sl.sections], ["Why", "Files", "Failing tests", "Implement", "Check", "Git"])

  def test_ParseSlice_MetaLineWithInteriorMiddot_KeepsFindingsIntact(self) -> None:
    sl = self.parse(support.MINI_SLICES["done/S01-first-thing.md"])
    meta = legacy.parse_meta(sl.meta, path="S01")
    self.assertEqual(meta["findings"], "`F1` · F2")
    self.assertEqual(meta["size"], "M")

  def test_ParseSlice_SizeWithOwnerSuffix_SetsFlag(self) -> None:
    sl = self.parse(support.MINI_SLICES["done/S01-first-thing.md"])
    self.assertEqual(legacy.parse_meta(sl.meta, path="S01")["flags"], ["OWNER"])

  def test_ParseSlice_TreesPlural_RecordsPluralKey(self) -> None:
    sl = self.parse(support.MINI_SLICES["S02-second-thing.md"])
    meta = legacy.parse_meta(sl.meta, path="S02")
    self.assertTrue(meta["trees_plural"])
    self.assertEqual(meta["trees"], "alpha, beta")

  def test_ParseSlice_DependsOnAdjacentToMeta_CapturedSeparately(self) -> None:
    sl = self.parse(support.MINI_SLICES["S02-second-thing.md"])
    self.assertTrue(sl.depends.startswith("**Depends on:** S01"))
    self.assertTrue(sl.meta.startswith("**Findings:**"))

  def test_ParseSlice_ParkedBoldLine_StoredAsLeadNotStatus(self) -> None:
    sl = self.parse(support.MINI_SLICES["S03-third-thing.md"])
    self.assertEqual(len(sl.lead), 1)
    self.assertTrue(sl.lead[0].startswith("**Parked**"))

  def test_ParseSlice_StatusLaterBoldLine_StoredAsLead(self) -> None:
    sl = self.parse(support.MINI_SLICES["S04-fourth-thing.md"])
    self.assertTrue(sl.lead[0].startswith("**Status: later**"))

  def test_ParseSlice_UnknownHeadings_PreservedInOrder(self) -> None:
    sl = self.parse(support.MINI_SLICES["S03-third-thing.md"])
    self.assertEqual([h for h, _ in sl.sections], ["Why", "What landed", "Check", "Git"])

  def test_ParseSlice_MissingFindingsLine_RaisesNamingFile(self) -> None:
    text = "# S99 — no meta\n\nnothing here\n\n## Why\n\nbecause\n"
    with self.assertRaises(LegacyImportError) as caught:
      legacy.parse_slice(text, path="bad.md")
    self.assertIn("bad.md", str(caught.exception))

  def test_ParseSlice_TwoFindingsLines_Raises(self) -> None:
    text = (
      "# S99 — two metas\n\n**Findings:** A · **Size: S** · **Tree:** t\n\n"
      "**Findings:** B · **Size: S** · **Tree:** t\n\n## Why\n\nbecause\n"
    )
    with self.assertRaises(LegacyImportError):
      legacy.parse_slice(text, path="bad.md")

  def test_ParseSlice_CrlfLineEndings_Raises(self) -> None:
    with self.assertRaises(LegacyImportError):
      legacy.parse_slice("# S1 — t\r\n", path="bad.md")

  def test_ParseSlice_NoFinalNewline_Raises(self) -> None:
    with self.assertRaises(LegacyImportError):
      legacy.parse_slice("# S1 — t", path="bad.md")

  def test_EmitSlice_ParsedFile_ReproducesInputBytes(self) -> None:
    for name, text in support.MINI_SLICES.items():
      with self.subTest(name):
        self.assertEqual(self.parse(text).emit(), text)


if __name__ == "__main__":
  unittest.main()
