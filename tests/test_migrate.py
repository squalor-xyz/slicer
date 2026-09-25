"""`slicer migrate`: an existing markdown tree into JSON, fixture tree included."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

import support

from slicer import legacy, migrator
from slicer.config import Config


class MigrateTests(unittest.TestCase):
  def test_Migrate_MiniTree_WritesConfigIndexAndSliceFiles(self) -> None:
    with support.TempRepo() as repo:
      support.make_mini(repo)
      repo.run("init")
      code, out, err = repo.run("migrate", "--from", "docs/slices")
      self.assertEqual(code, 0, err)
      self.assertTrue((repo.root / ".slicer/index.json").is_file())
      self.assertTrue((repo.root / ".slicer/slices/S02.json").is_file())

  def test_Migrate_MiniTree_PlacesDoneSlicesUnderDone(self) -> None:
    with support.TempRepo() as repo:
      support.make_mini(repo)
      repo.run("init")
      repo.run("migrate", "--from", "docs/slices")
      self.assertTrue((repo.root / ".slicer/slices/done/S01.json").is_file())
      self.assertFalse((repo.root / ".slicer/slices/S01.json").is_file())

  def test_Migrate_MiniTree_SetsNextIdAboveHighestId(self) -> None:
    with support.TempRepo() as repo:
      support.make_mini(repo)
      repo.run("init")
      repo.run("migrate", "--from", "docs/slices")
      self.assertEqual(repo.state().index.next_id, 5)

  def test_Migrate_MiniTree_KeepsIndexTitleAndFileTitleSeparately(self) -> None:
    with support.TempRepo() as repo:
      support.make_mini(repo)
      repo.run("init")
      repo.run("migrate", "--from", "docs/slices")
      item = repo.state().index.require("S01")
      self.assertEqual(item.short_title, "First thing")
      self.assertEqual(item.title, "first thing, spelled out at length")

  def test_Migrate_MiniTree_PreservesProseAroundTables(self) -> None:
    with support.TempRepo() as repo:
      support.make_mini(repo)
      repo.run("init")
      repo.run("migrate", "--from", "docs/slices")
      index = repo.state().index
      self.assertIn("Mini index preamble.", index.preamble)
      self.assertIn("Baseline prose that must survive.", index.passes[0].intro)
      self.assertIn("Outro prose for pass 1.", index.passes[0].outro)
      self.assertIn("## Dependencies", index.epilogue)

  def test_Migrate_MiniTree_ReadsDependsOnFromTheSliceFile(self) -> None:
    with support.TempRepo() as repo:
      support.make_mini(repo)
      repo.run("init")
      repo.run("migrate", "--from", "docs/slices")
      self.assertEqual(repo.state().index.require("S02").depends_on, ["S01"])

  def test_Migrate_CollectiveTreesCell_KeptAsOneLiteral(self) -> None:
    with support.TempRepo() as repo:
      support.make_mini(repo)
      repo.run("init")
      repo.run("migrate", "--from", "docs/slices")
      item = repo.state().index.require("S03")
      self.assertEqual(item.trees, ["all four"])
      self.assertTrue(item.trees_literal)

  def test_Migrate_DryRun_WritesNothing(self) -> None:
    with support.TempRepo() as repo:
      support.make_mini(repo)
      repo.run("init")
      before = sorted(p.name for p in (repo.root / ".slicer").rglob("*.json"))
      code, out, err = repo.run("migrate", "--from", "docs/slices", "--dry-run")
      self.assertEqual(code, 0, err)
      after = sorted(p.name for p in (repo.root / ".slicer").rglob("*.json"))
      self.assertEqual(before, after)
      self.assertIn("nothing written", out)

  def test_Migrate_DryRunJson_ReportsMachineReadableCounts(self) -> None:
    with support.TempRepo() as repo:
      support.make_mini(repo)
      repo.run("init")
      code, out, _ = repo.run("migrate", "--from", "docs/slices", "--dry-run", "--json")
      payload = json.loads(out)
      self.assertEqual(payload["items"], 4)
      self.assertEqual(payload["by_status"]["done"], 1)

  def test_Migrate_StatusDisagreesWithLocation_RefusesToWrite(self) -> None:
    with support.TempRepo() as repo:
      folder = support.make_mini(repo)
      # Claim S02 is done while its file still sits outside done/.
      text = repo.read("docs/slices/README.md").replace(
        "| [S02](S02-second-thing.md) | Second thing | S | alpha, beta | F3 | — |",
        "| [S02](S02-second-thing.md) | Second thing | S | alpha, beta | F3 | done |",
      )
      repo.write("docs/slices/README.md", text)
      repo.run("init")
      code, out, _ = repo.run("migrate", "--from", "docs/slices")
      self.assertEqual(code, 1)
      self.assertIn("disagrees with its location", out)
      self.assertFalse((repo.root / ".slicer/slices/S02.json").exists())

  def test_Migrate_UnparsableFile_AbortsWithoutWriting(self) -> None:
    with support.TempRepo() as repo:
      support.make_mini(repo)
      repo.write("docs/slices/S05-broken.md", "# S05 — broken\n\nno meta line\n\n## Why\n\nx\n")
      repo.run("init")
      code, _, err = repo.run("migrate", "--from", "docs/slices")
      self.assertEqual(code, 2)
      self.assertIn("Findings", err)
      self.assertFalse((repo.root / ".slicer/slices/S01.json").exists())

  def test_Migrate_FixtureTree_RoundTripsEveryFileByteIdentical(self) -> None:
    index, slices, located = legacy.read_tree(support.LEGACY)
    for path in sorted(list(support.LEGACY.glob("*.md")) + list((support.LEGACY / "done").glob("*.md"))):
      if path.name == "README.md":
        continue
      with self.subTest(path.name):
        text = path.read_text(encoding="utf-8")
        self.assertEqual(legacy.parse_slice(text, path=str(path)).emit(), text)
    readme = (support.LEGACY / "README.md").read_text(encoding="utf-8")
    self.assertEqual(legacy.parse_index(readme, path="README.md").emit(), readme)

  def test_Migrate_FixtureTree_ReportsTheExpectedCensus(self) -> None:
    index, slices, report = migrator.build(support.LEGACY, Config())
    self.assertEqual(report.problems, [])
    self.assertEqual(report.items, 14)
    self.assertEqual(report.slices, 14)
    self.assertEqual(report.passes, 4)
    self.assertEqual(report.groups, 5)
    self.assertEqual(report.next_id, "S15")
    self.assertEqual(
      report.by_status, {"done": 8, "parked": 2, "—": 3, "later": 1}
    )
    self.assertEqual(
      [i.id for i in index.items if i.status == "open"], ["S12", "S13", "S14"]
    )

  def test_Migrate_FixtureTree_KeepsOffSchemaHeadings(self) -> None:
    _, slices, report = migrator.build(support.LEGACY, Config())
    self.assertIn("What landed", report.off_schema_sections)
    self.assertIn("Code review (same slice)", report.off_schema_sections)
    headings = [s.heading for s in slices["S07"].sections]
    self.assertEqual(headings.index("What landed"), 1)

  @unittest.skipUnless(os.environ.get("SLICER_LEGACY_TREE"), "SLICER_LEGACY_TREE not set")
  def test_Migrate_LiveTree_RoundTripsEveryFileByteIdentical(self) -> None:
    live = Path(os.environ["SLICER_LEGACY_TREE"])
    _, _, report = migrator.build(live, Config())
    self.assertEqual(report.problems, [])


if __name__ == "__main__":
  unittest.main()


class LinkRewriteTests(unittest.TestCase):
  """Imported prose points at files relative to where it used to live."""

  def test_RewriteLinks_DeeperDestination_AddsTheMissingLevel(self) -> None:
    from pathlib import Path

    from slicer.migrator import rewrite_links

    root = Path("/repo")
    got = rewrite_links(
      "see [review](../../20260420-architect-review.md) and [tb](../toolbox.md)",
      root / "docs/slices",
      root / ".slicer/render/slices",
    )
    self.assertIn("(../../../20260420-architect-review.md)", got)
    self.assertIn("(../../../docs/toolbox.md)", got)

  def test_RewriteLinks_UrlsAnchorsAndAbsolutePaths_AreLeftAlone(self) -> None:
    from pathlib import Path

    from slicer.migrator import rewrite_links

    text = "[a](https://example.invalid/x) [b](#section) [c](/abs/path.md)"
    self.assertEqual(
      rewrite_links(text, Path("/repo/docs/slices"), Path("/repo/.slicer/render/slices")), text
    )

  def test_RewriteLinks_SameDirectory_ChangesNothing(self) -> None:
    from pathlib import Path

    from slicer.migrator import rewrite_links

    text = "[a](../x.md)"
    self.assertEqual(rewrite_links(text, Path("/repo/a"), Path("/repo/a")), text)

  def test_Migrate_FixtureTree_EveryRewrittenLinkPointsWhereItUsedTo(self) -> None:
    """The invariant: same destination, expressed from the new location."""
    from pathlib import Path

    from slicer.migrator import CODE_RE, LINK_RE, build

    def targets(text: str) -> list[str]:
      return [m.group(1).partition("#")[0] for m in LINK_RE.finditer(CODE_RE.sub("", text))]

    with support.TempRepo() as repo:
      source = repo.copy_legacy()
      repo.run("init")
      render_dir = repo.root / ".slicer/render/slices"
      _, slices, _ = build(source, Config(), render_dir=render_dir)

      originals: dict[str, str] = {}
      for path in list(source.glob("*.md")) + list((source / "done").glob("*.md")):
        if path.name == "README.md":
          continue
        text = path.read_text(encoding="utf-8")
        originals[text.split(" ", 2)[1]] = text

      checked = 0
      for sid, sl in slices.items():
        before = targets(originals[sid])
        after = targets(
          "\n\n".join([*sl.lead, *sl.notes, *[s.body for s in sl.sections]])
        )
        self.assertEqual(len(before), len(after), f"{sid}: link count changed")
        for was, now in zip(before, after):
          checked += 1
          self.assertEqual(
            (source / was).resolve(),
            (render_dir / now).resolve(),
            f"{sid}: {was} -> {now} points somewhere else",
          )
      self.assertGreater(checked, 10)

  def test_RewriteLinks_InsideCodeSpanOrFence_IsLeftAlone(self) -> None:
    from pathlib import Path

    from slicer.migrator import rewrite_links

    frm, to = Path("/repo/docs/slices"), Path("/repo/.slicer/render/slices")
    span = "run `grep -oh '\\[[^]]*\\](\\([^)]*\\.md\\))'` on it"
    self.assertEqual(rewrite_links(span, frm, to), span)
    fence = "```sh\nsee [x](../y.md)\n```"
    self.assertEqual(rewrite_links(fence, frm, to), fence)
    self.assertIn("(../../../docs/y.md)", rewrite_links("text [x](../y.md)", frm, to))

  def test_Migrate_IndexProse_LinksAreRebasedToTheRoadmapLocation(self) -> None:
    from slicer.migrator import build

    with support.TempRepo() as repo:
      source = repo.copy_legacy()
      repo.run("init")
      render_root = repo.root / ".slicer/render"
      index, _, _ = build(
        source,
        Config(),
        render_dir=render_root / "slices",
        index_render_dir=render_root,
      )
      prose = index.preamble + "\n".join(p.intro + p.outro for p in index.passes) + index.epilogue
      # `../review-protocol.md` meant docs/review-protocol.md when the index
      # lived in docs/slices/; from .slicer/render/ that is two levels further.
      self.assertIn("(../../docs/review-protocol.md)", prose)
      self.assertNotIn("(../review-protocol.md)", prose)


class MigrateGuardTests(unittest.TestCase):
  """migrate replaces the roadmap wholesale, so it must refuse a non-empty one."""

  def nonempty(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.run("add", "my existing item")
    support.make_mini(repo)
    return repo

  def test_Migrate_NonEmptyProject_RefusesWithoutForce(self) -> None:
    with self.nonempty() as repo:
      code, _, err = repo.run("migrate", "--from", "docs/slices")
      self.assertEqual(code, 2)
      self.assertIn("already has a roadmap", err)
      # the original roadmap is untouched
      self.assertEqual([i.title for i in repo.state().index.items], ["my existing item"])

  def test_Migrate_NonEmptyProject_ForceReplaces(self) -> None:
    with self.nonempty() as repo:
      code, _, err = repo.run("migrate", "--from", "docs/slices", "--force")
      self.assertEqual(code, 0, err)
      titles = [i.title for i in repo.state().index.items]
      self.assertNotIn("my existing item", titles)
      self.assertTrue(titles)

  def test_Migrate_NonEmptyProject_DryRunPreviewsWithoutForce(self) -> None:
    with self.nonempty() as repo:
      before = repo.read(".slicer/index.json")
      code, out, err = repo.run("migrate", "--from", "docs/slices", "--dry-run")
      self.assertEqual(code, 0, err)
      self.assertIn("nothing written", out)
      self.assertEqual(repo.read(".slicer/index.json"), before)

  def test_Migrate_EmptyProject_ProceedsWithoutForce(self) -> None:
    with support.TempRepo() as repo:
      support.make_mini(repo)
      repo.run("init")
      code, _, err = repo.run("migrate", "--from", "docs/slices")
      self.assertEqual(code, 0, err)
      self.assertTrue(repo.state().index.items)

  def test_Migrate_NonEmptyProject_Json_CarriesAlreadyExists(self) -> None:
    with self.nonempty() as repo:
      _, out, _ = repo.run("migrate", "--from", "docs/slices", "--json")
      self.assertEqual(json.loads(out)["error"]["code"], "already_exists")
