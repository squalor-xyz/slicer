"""Parsing the markdown outline that `slicer import` reads."""

from __future__ import annotations

import unittest

import support  # noqa: F401 (puts src/ on sys.path)

from slicer import outline
from slicer.errors import OutlineError


class OutlineParseTests(unittest.TestCase):
  def parse(self, text: str) -> list[outline.ItemSpec]:
    return outline.parse(text, path="out.md").items

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

  def test_Outline_ReleaseTargetAndWaveKeys_Import(self) -> None:
    spec = self.parse("## T\nrelease_target: v1.20\nwave: 2\n")[0]
    self.assertEqual((spec.pass_key, spec.wave), ("v1.20", 2))
    self.assertEqual(self.parse("## T\npass: v1.20\n")[0].pass_key, "v1.20")
    self.assertIsNone(self.parse("## T\npass: v1.20\n")[0].wave)
    self.assertTrue(self.parse("## T\npass: v1.20\n")[0].used_pass_key)
    self.assertFalse(spec.used_pass_key)
    data = spec.to_dict()
    self.assertEqual((data["pass"], data["release_target"], data["wave"]), ("v1.20", "v1.20", 2))

  def test_Outline_ReleaseTargetAndWaveKeys_ImportThroughTheCli(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.write(
        "draft.md",
        "## New\nrelease_target: v1.20\nwave: 2\n\n## Old\npass: v1.20\n",
      )
      code, _, err = repo.run("import", "draft.md")
      self.assertEqual(code, 0, err)
      self.assertEqual(len([l for l in err.splitlines() if "deprecated" in l]), 1, err)
      state = repo.state()
      self.assertEqual([(i.pass_key, i.wave) for i in state.index.items], [("v1.20", 2), ("v1.20", None)])
      # A refused outline writes nothing and says nothing, deprecated key or not.
      repo.write("refused.md", "## Bad\npass: v1\nstatus: nope\n")
      code, _, err = repo.run("import", "refused.md")
      self.assertNotEqual(code, 0)
      self.assertNotIn("deprecated", err)

  def test_Outline_ShortTitleKey_Parsed(self) -> None:
    specs = self.parse("## A long descriptive title\nshort_title: Short one\n")
    self.assertEqual(specs[0].short_title, "Short one")
    self.assertEqual(specs[0].to_dict()["short_title"], "Short one")

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
    parsed = outline.parse("# Roadmap\n\n## One\n\n## Two\n", path="out.md")
    self.assertEqual(parsed.preamble, "")
    self.assertEqual([spec.title for spec in parsed.items], ["One", "Two"])

  def test_Parse_ProseUnderTheTitle_IsThePreambleWithoutTheTitle(self) -> None:
    parsed = outline.parse(
      "# Roadmap\n\nContext for the queue.\n\nMore context.\n\n## One\n",
      path="out.md",
    )
    self.assertEqual(parsed.preamble, "Context for the queue.\n\nMore context.")
    self.assertEqual([spec.title for spec in parsed.items], ["One"])
    self.assertNotIn("Roadmap", parsed.preamble)

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

  def test_Parse_DependsLines_KeepRawValuesInDeclaredOrder(self) -> None:
    specs = self.parse(
      "## T\ndepends: Parse, then validate\ndepends: A, B\ndepends:\ndepends: ,\n"
    )
    self.assertEqual(specs[0].depends, ["Parse, then validate", "A, B"])


class OutlineRefusalTests(unittest.TestCase):
  def assertRefuses(self, text: str, fragment: str) -> None:
    with self.assertRaises(OutlineError) as caught:
      outline.parse(text, path="out.md")
    self.assertIn(fragment, str(caught.exception))

  def test_Parse_BadWave_NamesTheLine(self) -> None:
    for bad in ("0", "x", "-1", "1.5"):
      with self.subTest(wave=bad):
        self.assertRefuses(f"## T\nsize: M\nwave: {bad}\n", "out.md:3")
        self.assertRefuses(f"## T\nwave: {bad}\n", "wave must be an integer >= 1")

  def test_Parse_ReleaseTargetAndPassDisagree_Refuses(self) -> None:
    self.assertRefuses("## T\nrelease_target: v1\npass: v2\n", "release_target")
    outline.parse("## T\nrelease_target: v1\npass: v1\n", path="out.md")

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

  def test_Parse_ProseBeforeFirstItem_IsThePreamble(self) -> None:
    parsed = outline.parse("Some stray prose.\n\n## T\n", path="out.md")
    self.assertEqual(parsed.preamble, "Some stray prose.")
    self.assertEqual(parsed.items[0].title, "T")

  def test_Parse_NoItems_Refuses(self) -> None:
    self.assertRefuses("# Just a title\n", "no items")

  def test_Parse_Crlf_RefusesBeforeAnythingElse(self) -> None:
    self.assertRefuses("## T\r\nsize: M\r\n", "CRLF")


if __name__ == "__main__":
  unittest.main()
