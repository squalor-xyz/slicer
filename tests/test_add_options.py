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

  def test_Add_NewlineInShortTitle_RejectsWithoutAllocating(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      before = repo.read(".slicer/index.json")
      code, out, _ = repo.run("add", "Title", "--short-title", "bad\nrow", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out)["error"]["code"], "newline_in_field")
      self.assertEqual(repo.read(".slicer/index.json"), before)
