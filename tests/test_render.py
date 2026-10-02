"""Rendering is a deterministic projection, and staleness is a check failure."""

from __future__ import annotations

import unittest

import support

from slicer import render, templates
from slicer.config import Config
from slicer.errors import RenderError
from slicer.model import Index, Item, PassInfo, Slice


class RenderTests(unittest.TestCase):
  def test_Render_EmptySize_KeepsValidBoldLabel(self) -> None:
    sl = Slice(id="S01", title="Empty metadata")
    text = render.render_slice(sl, Config(), "{{header}}").decode("utf-8")
    self.assertIn("**Findings:**  · **Size:**  · **Tree:**\n", text)

  def test_Render_PopulatedSize_KeepsValueAndFlagsOutsideBold(self) -> None:
    sl = Slice(
      id="S01", title="Populated metadata", size="M", flags=["OWNER"],
      findings_note="F1", trees_note="alpha, beta", trees_plural=True,
    )
    text = render.render_slice(sl, Config(), "{{header}}").decode("utf-8")
    self.assertIn(
      "**Findings:** F1 · **Size:** M `[OWNER]` · **Trees:** alpha, beta\n", text,
    )

  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    support.make_mini(repo)
    repo.run("init")
    repo.run("migrate", "--from", "docs/slices")
    return repo

  def test_Titled_EmptyVersusSet_OnlyWrapsRealContent(self) -> None:
    self.assertEqual(render._titled("Goals", ""), "")
    self.assertEqual(render._titled("Goals", "   \n"), "")
    self.assertEqual(render._titled("Goals", "a\nb"), "## Goals\n\na\nb")

  def test_Render_GoalsAndNonGoals_AppearUnderRoadmapWhenSetElseCollapse(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      self.assertNotIn("## Goals", repo.read(".slicer/render/ROADMAP.md"))
      for ref, body in (("goals", "- a goal"), ("non_goals", "- a non-goal")):
        repo.write(f"{ref}.md", body + "\n")
        repo.run("prose", "edit", ref, "--file", str(repo.root / f"{ref}.md"))
      repo.run("render")
      roadmap = repo.read(".slicer/render/ROADMAP.md")
      self.assertLess(roadmap.index("## Goals"), roadmap.index("## Non-goals"))
      self.assertLess(roadmap.index("## Non-goals"), roadmap.index("| # |"))
      self.assertIn("- a goal", roadmap)
      self.assertIn("- a non-goal", roadmap)

  def test_Expand_UnknownPlaceholder_RaisesNamingTheKey(self) -> None:
    with self.assertRaises(RenderError) as caught:
      render.expand("hello {{nope}}", {"other": "x"})
    self.assertIn("nope", str(caught.exception))

  def test_Render_Slice_EmitsTheBannerAsTheFirstLine(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      first = repo.read(".slicer/render/slices/S02.md").split("\n", 1)[0]
      self.assertTrue(first.startswith(render.BANNER_PREFIX))

  def test_Render_RunTwice_ProducesIdenticalBytes(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      first = (repo.root / ".slicer/render/ROADMAP.md").read_bytes()
      repo.run("render")
      self.assertEqual((repo.root / ".slicer/render/ROADMAP.md").read_bytes(), first)

  def test_Render_SecondRun_WritesNothingWhenNothingChanged(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      code, out, _ = repo.run("render")
      self.assertEqual(code, 0)
      self.assertIn("already current", out)

  def test_Render_OffSchemaSections_SurviveIntoTheMarkdown(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      self.assertIn("## What landed", repo.read(".slicer/render/slices/S03.md"))

  def test_Render_ProsePreservedFromTheLegacyIndex_AppearsInTheRoadmap(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      roadmap = repo.read(".slicer/render/ROADMAP.md")
      self.assertIn("Mini index preamble.", roadmap)
      self.assertIn("Baseline prose that must survive.", roadmap)
      self.assertIn("## Dependencies", roadmap)

  def test_Check_RenderMatchesState_Passes(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      code, out, _ = repo.run("check")
      self.assertEqual(code, 0, out)

  def test_Check_HandEditedRenderFile_FailsAndNamesIt(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      repo.write(".slicer/render/slices/S02.md", "tampered\n")
      code, out, _ = repo.run("check")
      self.assertEqual(code, 1)
      self.assertIn("slices/S02.md", out)

  def test_Check_MissingRenderFile_FailsAndNamesIt(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      (repo.root / ".slicer/render/slices/S02.md").unlink()
      code, out, _ = repo.run("check")
      self.assertEqual(code, 1)
      self.assertIn("slices/S02.md", out)

  def test_Render_OrphanCarryingTheBanner_IsRemoved(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      repo.write(".slicer/render/slices/S99.md", render.banner("x") + "\n\nstale\n")
      repo.run("render")
      self.assertFalse((repo.root / ".slicer/render/slices/S99.md").exists())

  def test_Render_ForeignFileWithoutTheBanner_IsLeftAlone(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      repo.write(".slicer/render/NOTES.md", "hand written\n")
      repo.run("render")
      self.assertTrue((repo.root / ".slicer/render/NOTES.md").exists())
      self.assertEqual(repo.run("check")[0], 0)

  def test_Html_IsRenderedWithBannerAndItems(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      out = repo.read(".slicer/render/ROADMAP.html")
      self.assertTrue(out.startswith(render.BANNER_PREFIX))
      self.assertIn("<!DOCTYPE html>", out)
      self.assertIn("S02", out)
      self.assertIn('td class="status"', out)

  def test_Html_EscapesItemText_NoInjection(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S02", "--short-title", 'danger <b>& "x"')
      repo.run("render")
      out = repo.read(".slicer/render/ROADMAP.html")
      self.assertIn("&lt;b&gt;", out)
      self.assertIn("&amp;", out)
      self.assertNotIn("<b>&", out)

  def test_Html_IsDeterministic(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      first = (repo.root / ".slicer/render/ROADMAP.html").read_bytes()
      repo.run("render")
      self.assertEqual((repo.root / ".slicer/render/ROADMAP.html").read_bytes(), first)

  def _release_index(self) -> Index:
    """Declaration order is not display order. Empty pass holds one done item
    and one retired item, so the archive is not 'everything with no pass'."""
    return Index(
      passes=[
        PassInfo(key="v1", heading="«v1»", intro="intro-v1", outro="outro-v1"),
        PassInfo(key="beta", heading="«beta»"),
        PassInfo(key="v1.10", heading="«v110»"),
        PassInfo(key="later", heading="«later»"),
        PassInfo(key="v1.2", heading="«v12»"),
        PassInfo(key="v1.1", heading="«v11»"),
        PassInfo(key="v9", heading="«v9»"),
      ],
      items=[
        Item(id="S01", title="legacy", status="done"),
        Item(id="S02", title="one", status="done", pass_key="v1"),
        Item(id="S03", title="deferred", status="retired"),
        Item(id="S04", title="current", status="open", pass_key="v1.2"),
        Item(id="S05", title="side", status="open", pass_key="beta"),
        Item(id="S06", title="patch", status="done", pass_key="v1.1"),
        Item(id="S07", title="ten", status="open", pass_key="v1.10"),
        Item(id="S08", title="one-b", status="done", pass_key="v1"),
        Item(id="S09", title="after", status="parked", pass_key="later"),
      ],
    )

  def _rendered_pair(self, index: Index, cfg: Config | None = None) -> tuple[str, str]:
    cfg = cfg or Config()
    md = render.render_roadmap(
      index, cfg, templates.default("roadmap.md"), templates.default("row.md"),
    ).decode("utf-8")
    html_out = render.render_html(index, cfg).decode("utf-8")
    return md, html_out

  def test_Render_ReleaseGroups_NewestVersionThenUnassignedThenDoneArchive(self) -> None:
    index = self._release_index()
    self.assertEqual(
      index.pass_keys(), ["v1", "beta", "v1.10", "later", "v1.2", "v1.1", "v9", ""],
    )
    md, html_out = self._rendered_pair(index)
    self.assertEqual(md, self._rendered_pair(index)[0])
    headings = ["«v9»", "«v110»", "«v12»", "«v11»", "«v1»", "«beta»", "«later»"]
    ids = ["S07", "S04", "S06", "S02", "S08", "S05", "S09", "S03", "S01"]
    for text in (md, html_out):
      places = [text.index(label) for label in headings]
      self.assertEqual(places, sorted(places))
      id_places = [text.index(item_id) for item_id in ids]
      self.assertEqual(id_places, sorted(id_places))
      for item_id in ids:
        self.assertEqual(text.count(item_id), 1)
      self.assertLess(text.index("intro-v1"), text.index("S02"))
      self.assertLess(text.index("S08"), text.index("outro-v1"))
      self.assertLess(text.index("outro-v1"), text.index("«beta»"))
      self.assertNotIn("pre-v1", text)

  def test_Render_EmptyPassSplit_UsesConfiguredDoneStatus(self) -> None:
    cfg = Config(done_status="shipped")
    index = Index(
      passes=[PassInfo(key="v1", heading="«v1»")],
      items=[
        Item(id="S01", title="shipped-loose", status="shipped"),
        Item(id="S02", title="still-open", status="done"),
        Item(id="S03", title="versioned", status="open", pass_key="v1"),
      ],
    )
    md, html_out = self._rendered_pair(index, cfg)
    for text in (md, html_out):
      self.assertLess(text.index("S03"), text.index("S02"))
      self.assertLess(text.index("S02"), text.index("S01"))

  def test_Html_StaleEdit_IsCaughtByCheck(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      path = repo.root / ".slicer/render/ROADMAP.html"
      path.write_text(path.read_text() + "<!-- tampered -->\n", encoding="utf-8")
      code, out, _ = repo.run("check")
      self.assertEqual(code, 1)
      self.assertIn("ROADMAP.html", out)
      self.assertEqual(repo.run("render")[0], 0)
      self.assertEqual(repo.run("check")[0], 0)


if __name__ == "__main__":
  unittest.main()


class FilePermissionTests(unittest.TestCase):
  """State files are ordinary tracked files, not private temporaries."""

  def test_Write_StateAndRenderFiles_UseTheUmaskDefaultNotMkstemp0600(self) -> None:
    import stat

    with support.TempRepo() as repo:
      support.make_mini(repo)
      repo.run("init")
      repo.run("migrate", "--from", "docs/slices")
      repo.run("render")
      for rel in (".slicer/index.json", ".slicer/slices/S02.json", ".slicer/render/ROADMAP.md"):
        with self.subTest(rel):
          mode = stat.S_IMODE((repo.root / rel).stat().st_mode)
          self.assertTrue(mode & stat.S_IRGRP or mode & stat.S_IROTH, f"{rel} is {oct(mode)}")
