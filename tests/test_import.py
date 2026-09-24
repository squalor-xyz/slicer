"""`slicer import`: bulk-loading a roadmap from a markdown outline."""

from __future__ import annotations

import json
import unittest

import support

OUTLINE = """\
# Roadmap

## Parse the config file
size: M
tree: core
findings: G1
group: Phase 0

The loader accepts a missing key.

### Why
A typo reads as a deliberate setting.

### Implement
Raise, and name the key.

**Not in this slice:** the schema doc.

## Fail loudly on a missing key
size: S
tree: core
depends: Parse the config file

## Document the config schema
size: S
status: parked
"""


class ImportTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    return repo

  def test_Import_Outline_AddsEveryItemInOrder(self) -> None:
    with self.repo() as repo:
      repo.write("roadmap.md", OUTLINE)
      code, out, err = repo.run("import", "roadmap.md")
      self.assertEqual(code, 0, err)
      index = repo.state().index
      self.assertEqual([i.id for i in index.items], ["S01", "S02", "S03"])
      self.assertEqual(index.require("S01").title, "Parse the config file")
      self.assertEqual(index.next_id, 4)

  def test_Import_Outline_CarriesEveryKeyOntoTheItem(self) -> None:
    with self.repo() as repo:
      repo.write("roadmap.md", OUTLINE)
      repo.run("import", "roadmap.md")
      item = repo.state().index.require("S01")
      self.assertEqual(item.size, "M")
      self.assertEqual(item.trees, ["core"])
      self.assertEqual(item.findings, "G1")
      self.assertEqual(item.group, "Phase 0")

  def test_Import_DependsByTitle_ResolvesToAnId(self) -> None:
    with self.repo() as repo:
      repo.write("roadmap.md", OUTLINE)
      repo.run("import", "roadmap.md")
      self.assertEqual(repo.state().index.require("S02").depends_on, ["S01"])

  def test_Import_EntryWithSections_GetsASliceFile(self) -> None:
    with self.repo() as repo:
      repo.write("roadmap.md", OUTLINE)
      repo.run("import", "roadmap.md")
      state = repo.state()
      self.assertTrue(state.index.require("S01").has_slice)
      self.assertIsNotNone(state.find_slice_file("S01"))

  def test_Import_EntryWithoutSections_StaysARoadmapRow(self) -> None:
    with self.repo() as repo:
      repo.write("roadmap.md", OUTLINE)
      repo.run("import", "roadmap.md")
      state = repo.state()
      self.assertFalse(state.index.require("S02").has_slice)
      self.assertIsNone(state.find_slice_file("S02"))

  def test_Import_SliceSections_FollowTheConfiguredOrder(self) -> None:
    with self.repo() as repo:
      repo.write("roadmap.md", OUTLINE)
      repo.run("import", "roadmap.md")
      headings = [s.heading for s in repo.state().slices["S01"].sections]
      self.assertEqual(headings, ["Why", "Files", "Failing tests", "Implement", "Check", "Git"])

  def test_Import_LeadParagraph_LandsOnTheSlice(self) -> None:
    with self.repo() as repo:
      repo.write("roadmap.md", OUTLINE)
      repo.run("import", "roadmap.md")
      self.assertEqual(repo.state().slices["S01"].lead, ["The loader accepts a missing key."])

  def test_Import_StatusOnAnEntry_IsApplied(self) -> None:
    with self.repo() as repo:
      repo.write("roadmap.md", OUTLINE)
      repo.run("import", "roadmap.md")
      self.assertEqual(repo.state().index.require("S03").status, "parked")

  def test_Import_DoneEntryWithSections_PutsTheFileUnderDone(self) -> None:
    with self.repo() as repo:
      repo.write("r.md", "## Shipped already\nstatus: done\n\n### Why\nHistory.\n")
      repo.run("import", "r.md")
      self.assertTrue((repo.root / ".slicer/slices/done/S01.json").is_file())
      self.assertFalse((repo.root / ".slicer/slices/S01.json").exists())

  def test_Import_NoBoundaryInTheOutline_AppendsTheMarker(self) -> None:
    with self.repo() as repo:
      repo.write("r.md", "## A thing\n\n### Why\nBecause.\n")
      repo.run("import", "r.md")
      sl = repo.state().slices["S01"]
      self.assertIsNotNone(sl.boundary("**Not in this slice:**"))

  def test_Import_BoundaryAlreadyPresent_IsNotDuplicated(self) -> None:
    with self.repo() as repo:
      repo.write("r.md", "## A thing\n\n### Why\nBecause.\n\n**Not in this slice:** other things.\n")
      repo.run("import", "r.md")
      bodies = "\n".join(s.body for s in repo.state().slices["S01"].sections)
      self.assertEqual(bodies.count("**Not in this slice:**"), 1)

  def test_Import_PassKey_IsNotInheritedFromTheLastItem(self) -> None:
    # `ops.add` inherits the previous item's pass; a bulk load must not,
    # or every entry lands in whichever pass the queue happened to end on.
    with self.repo() as repo:
      repo.run("add", "Existing work", "--pass", "6")
      repo.write("r.md", "## A new thing\n")
      repo.run("import", "r.md")
      self.assertEqual(repo.state().index.require("S02").pass_key, "")

  def test_Import_WritesOneLogEntryPerItem(self) -> None:
    with self.repo() as repo:
      repo.write("roadmap.md", OUTLINE)
      repo.run("import", "roadmap.md")
      adds = [e for e in repo.state().history() if e.action == "add"]
      self.assertEqual([e.item for e in adds], ["S01", "S02", "S03"])

  def test_Import_ThenRenderAndCheck_IsClean(self) -> None:
    with self.repo() as repo:
      repo.write("roadmap.md", OUTLINE)
      repo.run("import", "roadmap.md")
      repo.run("render")
      self.assertEqual(repo.run("check")[0], 0)

  def test_Import_Json_ReportsTheCensus(self) -> None:
    with self.repo() as repo:
      repo.write("roadmap.md", OUTLINE)
      _, out, _ = repo.run("import", "roadmap.md", "--json")
      payload = json.loads(out)
      self.assertEqual(payload["items"], 3)
      self.assertEqual(payload["promoted"], 1)
      self.assertEqual(payload["ids"], ["S01", "S02", "S03"])
      self.assertEqual(payload["depends_edges"], 1)


class ImportRefusalTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    return repo

  def test_Import_DuplicateTitle_RefusesAndWritesNothing(self) -> None:
    with self.repo() as repo:
      repo.write("roadmap.md", OUTLINE)
      repo.run("import", "roadmap.md")
      before = repo.read(".slicer/index.json")
      code, out, _ = repo.run("import", "roadmap.md")
      self.assertEqual(code, 1)
      self.assertIn("already exists as S01", out)
      self.assertEqual(repo.read(".slicer/index.json"), before)

  def test_Import_DuplicateTitleWithForce_AddsAnyway(self) -> None:
    with self.repo() as repo:
      repo.write("roadmap.md", OUTLINE)
      repo.run("import", "roadmap.md")
      code, _, err = repo.run("import", "roadmap.md", "--force")
      self.assertEqual(code, 0, err)
      self.assertEqual(len(repo.state().index.items), 6)

  def test_Import_TitleTwiceInOneOutline_Refuses(self) -> None:
    with self.repo() as repo:
      repo.write("r.md", "## Same\n\n## Same\n")
      code, out, _ = repo.run("import", "r.md")
      self.assertEqual(code, 1)
      self.assertIn("appears twice", out)

  def test_Import_UnknownStatus_RefusesNamingTheKnownOnes(self) -> None:
    with self.repo() as repo:
      repo.write("r.md", "## A thing\nstatus: blocked\n")
      code, out, _ = repo.run("import", "r.md")
      self.assertEqual(code, 1)
      self.assertIn("unknown status 'blocked'", out)

  def test_Import_DanglingDepends_Refuses(self) -> None:
    with self.repo() as repo:
      repo.write("r.md", "## A thing\ndepends: Nothing at all\n")
      code, out, _ = repo.run("import", "r.md")
      self.assertEqual(code, 1)
      self.assertIn("which is not in the outline or the index", out)

  def test_Import_DependsOnAnExistingItem_IsAccepted(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Already here")
      repo.write("r.md", "## A new thing\ndepends: Already here\n")
      code, _, err = repo.run("import", "r.md")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().index.require("S02").depends_on, ["S01"])

  def test_Import_UnparsableOutline_ExitsTwoWithoutWriting(self) -> None:
    with self.repo() as repo:
      repo.write("r.md", "## A thing\nnonsense: x\n")
      code, _, err = repo.run("import", "r.md")
      self.assertEqual(code, 2)
      self.assertIn("unknown key", err)
      self.assertEqual(repo.state().index.items, [])

  def test_Import_DryRun_WritesNothing(self) -> None:
    with self.repo() as repo:
      repo.write("roadmap.md", OUTLINE)
      before = repo.read(".slicer/index.json")
      code, out, err = repo.run("import", "roadmap.md", "--dry-run")
      self.assertEqual(code, 0, err)
      self.assertIn("nothing written", out)
      self.assertEqual(repo.read(".slicer/index.json"), before)

  def test_Import_DryRun_ReportsTheSameCensusAsApplying(self) -> None:
    with self.repo() as repo:
      repo.write("roadmap.md", OUTLINE)
      _, dry, _ = repo.run("import", "roadmap.md", "--dry-run", "--json")
      _, applied, _ = repo.run("import", "roadmap.md", "--json")
      for key in ("items", "promoted", "by_status", "depends_edges"):
        self.assertEqual(json.loads(dry)[key], json.loads(applied)[key], key)

  def test_Import_LegacyFromFlag_PointsAtMigrate(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("import", "--from", "docs/slices")
      self.assertEqual(code, 2)
      self.assertIn("slicer migrate", err)

  def test_Import_NoFileAndNoSkeleton_Refuses(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("import")
      self.assertEqual(code, 2)
      self.assertIn("--skeleton", err)


class SkeletonTests(unittest.TestCase):
  def test_Skeleton_UsesTheProjectsConfiguredSections(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      import json as _json

      path = repo.root / ".slicer/config.json"
      cfg = _json.loads(path.read_text())
      cfg["sections"] = ["Context", "Plan"]
      path.write_text(_json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
      _, out, err = repo.run("import", "--skeleton")
      self.assertIn("### Context", out, err)
      self.assertIn("### Plan", out)
      self.assertNotIn("### Failing tests", out)

  def test_Skeleton_RoundTrips_BackThroughImport(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      _, skeleton, _ = repo.run("import", "--skeleton")
      repo.write("r.md", skeleton)
      code, _, err = repo.run("import", "r.md")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().index.require("S01").title, "Parse the config file")


if __name__ == "__main__":
  unittest.main()


class OutlineTextValidationTests(unittest.TestCase):
  """The bulk path builds items directly, so it needs the same guard."""

  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    return repo

  def test_Import_BlankHeading_IsRefusedByTheParser(self) -> None:
    with self.repo() as repo:
      repo.write("r.md", "## \n")
      code, _, err = repo.run("import", "r.md")
      self.assertEqual(code, 2)
      self.assertIn("no title", err)
      self.assertEqual(repo.state().index.items, [])

  def test_Import_PipeInATitle_LandsAndRendersSafely(self) -> None:
    with self.repo() as repo:
      repo.write("r.md", "## Fix the a|b parser\n")
      self.assertEqual(repo.run("import", "r.md")[0], 0)
      self.assertEqual(repo.run("render")[0], 0)
      self.assertEqual(repo.run("check")[0], 0)
