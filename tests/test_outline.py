"""Parsing the markdown outline that `slicer import` reads."""

from __future__ import annotations

import unittest

import support  # noqa: F401 (puts src/ on sys.path)

from slicer import outline
from slicer.errors import OutlineError


class OutlineParseTests(unittest.TestCase):
  def parse(self, text: str) -> list[outline.ItemSpec]:
    return outline.parse(text, path="out.md")

  def test_Parse_HeadingOnly_IsAnItemWithNoSlice(self) -> None:
    specs = self.parse("## Just a title\n")
    self.assertEqual(len(specs), 1)
    self.assertEqual(specs[0].title, "Just a title")
    self.assertFalse(specs[0].has_slice)

  def test_Parse_KeyLines_PopulateEveryField(self) -> None:
    specs = self.parse(
      "## A title\n"
      "size: M\n"
      "tree: core\n"
      "findings: G1\n"
      "status: parked\n"
      "pass: 2\n"
      "group: Phase 0\n"
    )
    spec = specs[0]
    self.assertEqual(spec.size, "M")
    self.assertEqual(spec.trees, ["core"])
    self.assertEqual(spec.findings, "G1")
    self.assertEqual(spec.status, "parked")
    self.assertEqual(spec.pass_key, "2")
    self.assertEqual(spec.group, "Phase 0")

  def test_Parse_CommaSeparatedTrees_BecomeAList(self) -> None:
    self.assertEqual(self.parse("## T\ntree: core, cli, docs\n")[0].trees, ["core", "cli", "docs"])

  def test_Parse_RepeatedTreeKey_Accumulates(self) -> None:
    self.assertEqual(self.parse("## T\ntree: core\ntree: cli\n")[0].trees, ["core", "cli"])

  def test_Parse_Sections_KeepHeadingAndBody(self) -> None:
    specs = self.parse("## T\n\n### Why\nBecause.\n\n### Check\nGreen.\n")
    headings = [(s.heading, s.body) for s in specs[0].sections]
    self.assertEqual(headings, [("Why", "Because."), ("Check", "Green.")])
    self.assertTrue(specs[0].has_slice)

  def test_Parse_ProseBeforeFirstSection_BecomesLead(self) -> None:
    specs = self.parse("## T\nsize: M\n\nA lead paragraph.\n\n### Why\nBecause.\n")
    self.assertEqual(specs[0].lead, ["A lead paragraph."])
    self.assertEqual(specs[0].sections[0].heading, "Why")

  def test_Parse_OffSchemaHeading_IsKept(self) -> None:
    specs = self.parse("## T\n\n### What landed\nA thing.\n")
    self.assertEqual(specs[0].sections[0].heading, "What landed")

  def test_Parse_DocumentTitle_IsIgnored(self) -> None:
    self.assertEqual(len(self.parse("# Roadmap\n\n## One\n\n## Two\n")), 2)

  def test_Parse_HtmlComment_IsStripped(self) -> None:
    specs = self.parse("<!-- guidance\n  spanning lines -->\n## T\nsize: S\n")
    self.assertEqual(specs[0].size, "S")

  def test_Parse_BlankBodyInSection_IsEmptyNotMissing(self) -> None:
    specs = self.parse("## T\n\n### Why\n\n### Check\nGreen.\n")
    self.assertEqual(specs[0].sections[0].body, "")

  def test_Parse_MultilineSectionBody_KeepsInternalBlankLines(self) -> None:
    specs = self.parse("## T\n\n### Why\nOne.\n\nTwo.\n")
    self.assertEqual(specs[0].sections[0].body, "One.\n\nTwo.")

  def test_Parse_ColonInProse_IsNotReadAsAKey(self) -> None:
    # The key block ends at the first line that is not `key: value`.
    specs = self.parse("## T\nsize: M\n\nNote that this line has: a colon in it.\n")
    self.assertEqual(specs[0].size, "M")
    self.assertEqual(specs[0].lead, ["Note that this line has: a colon in it."])


class OutlineRefusalTests(unittest.TestCase):
  def assertRefuses(self, text: str, fragment: str) -> None:
    with self.assertRaises(OutlineError) as caught:
      outline.parse(text, path="out.md")
    self.assertIn(fragment, str(caught.exception))

  def test_Parse_UnknownKey_NamesTheKeyAndTheKnownSet(self) -> None:
    self.assertRefuses("## T\nsizes: M\n", "unknown key 'sizes'")

  def test_Parse_UnknownKey_NamesTheLine(self) -> None:
    self.assertRefuses("## T\nsize: M\nnope: x\n", "out.md:3")

  def test_Parse_KeyWithNoValue_Refuses(self) -> None:
    self.assertRefuses("## T\nsize:\n", "'size' has no value")

  def test_Parse_EmptyHeading_Refuses(self) -> None:
    self.assertRefuses("## \n", "no title")

  def test_Parse_SectionBeforeAnyItem_Refuses(self) -> None:
    self.assertRefuses("### Why\nBecause.\n", "before any '##' item")

  def test_Parse_TextBeforeFirstItem_Refuses(self) -> None:
    self.assertRefuses("Some stray prose.\n\n## T\n", "before the first '##'")

  def test_Parse_NoItems_Refuses(self) -> None:
    self.assertRefuses("# Just a title\n", "no items")

  def test_Parse_Crlf_RefusesBeforeAnythingElse(self) -> None:
    self.assertRefuses("## T\r\nsize: M\r\n", "CRLF")


if __name__ == "__main__":
  unittest.main()
