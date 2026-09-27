"""Rendering is a deterministic projection, and staleness is a check failure."""

from __future__ import annotations

import unittest

import support

from slicer import render
from slicer.errors import RenderError


class RenderTests(unittest.TestCase):
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
