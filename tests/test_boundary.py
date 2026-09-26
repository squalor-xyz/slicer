"""Scope must survive section edits and upgrades from inline boundary prose."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import support
from slicer import jsonio, legacy, model, tui, verify


class BoundaryTests(unittest.TestCase):
  marker = "**Not in this slice:**"

  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.run("add", "Example")
    return repo

  def test_Boundary_SectionEditsAndAppend_PreserveScope(self) -> None:
    with self.repo() as repo:
      repo.run("promote", "S01", "--boundary", self.marker + " other work.")
      for heading in repo.state().config.sections:
        for args in (("--text", "replacement"), ("--append", "--text", "addition")):
          code, _, err = repo.run("edit", "S01", "--section", heading, *args, "--render")
          self.assertEqual(code, 0, err)
          self.assertEqual(repo.state().slices["S01"].boundary, self.marker + " other work.")
      self.assertEqual(repo.run("check")[0], 0)
      text = repo.read(".slicer/render/slices/S01.md")
      self.assertEqual(text.count(self.marker), 1)
      self.assertLess(text.index(self.marker), text.index("## Why"))
      self.assertEqual(repo.run("render")[0], 0)
      self.assertEqual(repo.read(".slicer/render/slices/S01.md"), text)

  def test_Boundary_OldJson_LoadDoesNotWriteAndSectionSavePersists(self) -> None:
    with self.repo() as repo:
      repo.run("promote", "S01")
      path = repo.root / ".slicer/slices/S01.json"
      data = json.loads(path.read_text())
      del data["boundary"]
      data["sections"][0]["body"] = "before\n\n" + self.marker + " other.\ncontinued\n\nafter"
      path.write_text(json.dumps(data))
      before = path.read_bytes()
      sl = repo.state().slices["S01"]
      self.assertEqual(sl.boundary, self.marker + " other.\ncontinued")
      self.assertEqual(sl.sections[0].body, "before\n\nafter")
      self.assertEqual(path.read_bytes(), before)
      code, _, err = repo.run("edit", "S01", "--section", "Why", "--text", "changed")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(path.read_text())["boundary"], sl.boundary)
      self.assertEqual(repo.state().slices["S01"].boundary, sl.boundary)

  def test_Boundary_ExplicitEmptyField_DoesNotExtractOldProse(self) -> None:
    data = {"id": "S01", "title": "t", "boundary": "", "sections": [
      {"heading": "Why", "body": self.marker + " still prose."}
    ]}
    sl = model.Slice.from_dict(data, boundary_marker=self.marker)
    self.assertEqual(sl.boundary, "")
    self.assertEqual(sl.sections[0].body, data["sections"][0]["body"])

  def test_Boundary_CustomAndDisabledMarkers_RespectConfiguration(self) -> None:
    for marker in ("Outside:", ""):
      with self.subTest(marker=marker), self.repo() as repo:
        cfg = repo.state().config
        cfg.boundary = marker
        jsonio.write(repo.root / ".slicer/config.json", cfg.to_dict())
        path = repo.write("outline.md", "## Example\n\n### Why\nBecause.\n\nOutside: other work.\n")
        code, _, err = repo.run("promote", "S01", "--file", str(path), "--render")
        self.assertEqual(code, 0, err)
        sl = repo.state().slices["S01"]
        self.assertEqual(sl.boundary, "Outside: other work." if marker else "")
        self.assertEqual("Outside:" in sl.section("Why").body, not bool(marker))
        self.assertFalse(verify.offline(repo.state()).findings)

  def test_Boundary_PromoteOverrideAndClear_AreAuthoritative(self) -> None:
    for value in ("", self.marker + " override."):
      with self.subTest(value=value), self.repo() as repo:
        path = repo.write("outline.md", "## Example\n\n### Why\nBecause.\n\n" + self.marker + " source.\n")
        code, _, err = repo.run("promote", "S01", "--file", str(path), "--boundary", value)
        self.assertEqual(code, 0, err)
        sl = repo.state().slices["S01"]
        self.assertEqual(sl.boundary, value)
        self.assertNotIn(self.marker, sl.section("Why").body)
        warnings = [f for f in verify.offline(repo.state()).findings if "scope is unbounded" in f.message]
        self.assertEqual(len(warnings), 0 if value else 1)

  def test_Boundary_EditClear_ReportsJsonAndLogs(self) -> None:
    with self.repo() as repo:
      repo.run("promote", "S01")
      code, out, err = repo.run("edit", "S01", "--boundary", "--text", "", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out), {"id": "S01", "boundary": ""})
      self.assertEqual(repo.state().slices["S01"].boundary, "")
      entry = repo.state().history()[-1]
      self.assertEqual((entry.action, entry.note), ("edit", "boundary"))
      code, out, _ = repo.run("show", "S01", "--json")
      self.assertEqual(json.loads(out)["slice"]["boundary"], "")

  def test_Boundary_InvalidTargetOrAppend_RejectsWithoutInputOrWrites(self) -> None:
    with self.repo() as repo:
      repo.run("promote", "S01", "--render")
      for args in (("--boundary", "--append"), ("--boundary", "--section", "Why"), ()):
        with self.subTest(args=args):
          before = {str(p): p.read_bytes() for p in (repo.root / ".slicer").rglob("*") if p.is_file() and p.name != "lock"}
          with patch("slicer.cli._via_editor") as editor:
            code, out, _ = repo.run("edit", "S01", *args, "--json")
          self.assertEqual(code, 2)
          self.assertEqual(json.loads(out)["error"]["code"], "usage")
          editor.assert_not_called()
          after = {str(p): p.read_bytes() for p in (repo.root / ".slicer").rglob("*") if p.is_file() and p.name != "lock"}
          self.assertEqual(after, before)

  def test_Boundary_Migration_PreservesLegacyProofAndExtractsCanonicalScope(self) -> None:
    with support.TempRepo() as repo:
      support.make_mini(repo)
      repo.run("init")
      for text in support.MINI_SLICES.values():
        self.assertEqual(legacy.roundtrip_slice(text, path="fixture").emit(), text)
      code, _, err = repo.run("migrate", "--from", "docs/slices", "--render")
      self.assertEqual(code, 0, err)
      for sl in repo.state().slices.values():
        self.assertTrue(sl.boundary.startswith(self.marker))
        self.assertFalse(any(self.marker in section.body for section in sl.sections))
      self.assertEqual(repo.run("check")[0], 0)

  def test_Boundary_MigrationMissingScope_RemainsMissing(self) -> None:
    with support.TempRepo() as repo:
      support.make_mini(repo)
      path = repo.root / "docs/slices/done/S01-first-thing.md"
      path.write_text(path.read_text().replace(self.marker + " the second thing.\n\n", ""))
      repo.run("init")
      code, _, err = repo.run("migrate", "--from", "docs/slices")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().slices["S01"].boundary, "")
      self.assertTrue(any(f.item == "S01" and "scope is unbounded" in f.message for f in verify.offline(repo.state()).findings))

  def test_Boundary_Tui_ExposesAndEditsScopeSeparately(self) -> None:
    with self.repo() as repo:
      repo.run("promote", "S01")
      state = repo.state()
      entries = tui.entries(state, "S01")
      at = next(i for i, entry in enumerate(entries) if entry.kind == "boundary")
      self.assertTrue(any(line.entry == at and self.marker in line.text for line in tui.panel(state, "S01")))
      request = tui.act(state, "e", "S01", entry=at, focus="right").edit
      self.assertEqual(request.body, self.marker)
      tui.apply_edit(state, request, self.marker + " more work.")
      self.assertEqual(repo.state().slices["S01"].boundary, self.marker + " more work.")
