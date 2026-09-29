"""Creation should expose the same dependency and display fields as later edits."""

from __future__ import annotations

import json
import unittest

import support


class AddOptionsTests(unittest.TestCase):
  def test_Add_Dependencies_PersistAndGateNext(self) -> None:
    for dependencies in (("S01",), ("S01", "S02")):
      with self.subTest(dependencies=dependencies), support.TempRepo() as repo:
        repo.run("init")
        repo.run("add", "First")
        repo.run("add", "Second")
        args = [arg for dep in dependencies for arg in ("--depends-on", dep)]
        code, out, err = repo.run("add", "Dependent", *args, "--importance", "3", "--render", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["depends_on"], list(dependencies))
        self.assertEqual(repo.state().index.require("S03").depends_on, list(dependencies))
        self.assertEqual(json.loads(repo.run("next", "--json")[1])["id"], "S01")
        for dep in dependencies:
          repo.run("done", dep, "--render")
        self.assertEqual(json.loads(repo.run("next", "--json")[1])["id"], "S03")
        self.assertEqual(repo.run("check")[0], 0)

  def test_Add_ShortTitle_PersistsAndRendersWithoutReplacingTitle(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      code, out, err = repo.run("add", "Full detailed title", "--short-title", "Brief", "--render", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out)["short_title"], "Brief")
      item = repo.state().index.require("S01")
      self.assertEqual((item.title, item.short_title), ("Full detailed title", "Brief"))
      self.assertIn("Brief", repo.run("list")[1])
      self.assertIn("Brief", repo.read(".slicer/render/ROADMAP.md"))
      self.assertEqual(repo.run("check")[0], 0)

  def test_Add_OmittedOrEmptyShortTitle_KeepsDefaults(self) -> None:
    for args in ((), ("--short-title", "")):
      with self.subTest(args=args), support.TempRepo() as repo:
        repo.run("init")
        code, _, err = repo.run("add", "Full title", *args)
        self.assertEqual(code, 0, err)
        item = repo.state().index.require("S01")
        self.assertEqual(item.short_title, "Full title")
        self.assertEqual(item.depends_on, [])

  def test_Add_InheritedPass_IsNamedInTheConfirmation(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "Milestone", "--pass", "v1")
      code, out, err = repo.run("add", "Follow-up")
      self.assertEqual(code, 0, err)
      self.assertIn("added S02  Follow-up (pass: v1)", out)
      self.assertEqual(repo.state().index.require("S02").pass_key, "v1")

  def test_Add_ExplicitEmptyPass_AfterAPassedTail_FilesWithNoPass(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "Milestone", "--pass", "v1")
      code, out, err = repo.run("add", "Unfiled", "--pass", "")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().index.require("S02").pass_key, "")
      self.assertEqual(out.strip(), "added S02  Unfiled")

  def test_Add_OmittedPass_AfterAPassedTail_StillInherits(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "Milestone", "--pass", "v1")
      repo.run("add", "Follow-up")
      self.assertEqual(repo.state().index.require("S02").pass_key, "v1")

  def test_Add_EmptyPass_OmitsThePassNote(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      code, out, err = repo.run("add", "First")
      self.assertEqual(code, 0, err)
      self.assertEqual(out.strip(), "added S01  First")
      self.assertNotIn("(pass:", out)

  def test_Add_ExplicitPass_IsNamedWithoutAnEmptyNote(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      code, out, err = repo.run("add", "First", "--pass", "v1")
      self.assertEqual(code, 0, err)
      self.assertIn("added S01  First (pass: v1)", out)
      self.assertNotIn("(pass: )", out)

  def test_Add_Json_CarriesPassWithoutTheConfirmationText(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "Milestone", "--pass", "v1")
      code, out, err = repo.run("add", "Follow-up", "--json")
      self.assertEqual(code, 0, err)
      payload = json.loads(out)
      self.assertEqual(payload["id"], "S02")
      self.assertEqual(payload["fields"]["pass"], "v1")
      self.assertNotIn("(pass:", out)

  def test_Add_NewlineInShortTitle_RejectsWithoutAllocating(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      before = repo.read(".slicer/index.json")
      code, out, _ = repo.run("add", "Title", "--short-title", "bad\nrow", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out)["error"]["code"], "newline_in_field")
      self.assertEqual(repo.read(".slicer/index.json"), before)
