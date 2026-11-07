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
      repo.run("add", "Milestone", "--release-target", "v1")
      code, out, err = repo.run("add", "Follow-up")
      self.assertEqual(code, 0, err)
      self.assertIn("added S02  Follow-up (release target: v1)", out)
      self.assertEqual(repo.state().index.require("S02").pass_key, "v1")

  def test_Add_ExplicitEmptyPass_AfterAPassedTail_FilesWithNoPass(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "Milestone", "--release-target", "v1")
      code, out, err = repo.run("add", "Unfiled", "--release-target", "")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().index.require("S02").pass_key, "")
      self.assertEqual(out.strip(), "added S02  Unfiled")

  def test_Add_OmittedPass_AfterAPassedTail_StillInherits(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "Milestone", "--release-target", "v1")
      repo.run("add", "Follow-up")
      self.assertEqual(repo.state().index.require("S02").pass_key, "v1")

  def test_Add_EmptyPass_OmitsThePassNote(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      code, out, err = repo.run("add", "First")
      self.assertEqual(code, 0, err)
      self.assertEqual(out.strip(), "added S01  First")
      self.assertNotIn("(release target:", out)

  def test_Add_ExplicitPass_IsNamedWithoutAnEmptyNote(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      code, out, err = repo.run("add", "First", "--release-target", "v1")
      self.assertEqual(code, 0, err)
      self.assertIn("added S01  First (release target: v1)", out)
      self.assertNotIn("(release target: )", out)

  def test_Add_Json_CarriesPassWithoutTheConfirmationText(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "Milestone", "--release-target", "v1")
      code, out, err = repo.run("add", "Follow-up", "--json")
      self.assertEqual(code, 0, err)
      payload = json.loads(out)
      self.assertEqual(payload["id"], "S02")
      self.assertEqual(payload["fields"]["pass"], "v1")
      self.assertNotIn("(release target:", out)

  def test_Add_NewlineInShortTitle_RejectsWithoutAllocating(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      before = repo.read(".slicer/index.json")
      code, out, _ = repo.run("add", "Title", "--short-title", "bad\nrow", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out)["error"]["code"], "newline_in_field")
      self.assertEqual(repo.read(".slicer/index.json"), before)

  def test_Add_PrintId_StdoutIsBareId(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      code, out, err = repo.run(
        "add", "Title", "--importance", "3", "--urgency", "3", "--effort", "1", "--print-id",
      )
      self.assertEqual(code, 0, err)
      self.assertEqual(out, "S01\n")
      self.assertIsNotNone(repo.state().index.require("S01"))

  def test_Add_PrintIdWithKeyRetry_PrintsExistingId(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "Title", "--key", "K")
      code, out, err = repo.run("add", "Title", "--key", "K", "--print-id")
      self.assertEqual(code, 0, err)
      self.assertEqual(out, "S01\n")
      self.assertEqual([item.id for item in repo.state().index.items], ["S01"])

  def test_Add_PrintIdWithRender_RenderLineOnStderr(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      code, out, err = repo.run(
        "add", "Title", "--importance", "3", "--urgency", "3", "--effort", "1",
        "--print-id", "--render",
      )
      self.assertEqual(code, 0, err)
      self.assertEqual(out, "S01\n")
      self.assertIn("rendered ", err)

  def test_Add_PrintIdWithJson_Usage(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      before = repo.read(".slicer/index.json")
      code, out, _ = repo.run("add", "Title", "--print-id", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out)["error"]["code"], "usage")
      self.assertEqual(repo.read(".slicer/index.json"), before)

  def test_Add_ReleaseTargetAndWave_StoredAndShownInJson(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      code, out, err = repo.run("add", "Item", "--release-target", "v1.20", "--wave", "2", "--json")
      self.assertEqual(code, 0, err)
      fields = json.loads(out)["fields"]
      self.assertEqual(fields["release_target"], "v1.20")
      self.assertEqual(fields["pass"], "v1.20")
      self.assertEqual(fields["wave"], 2)
      self.assertNotIn("deprecated", err)
      item = repo.state().index.require("S01")
      self.assertEqual((item.pass_key, item.wave), ("v1.20", 2))

  def test_Add_NoWave_JsonCarriesNullWave(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      code, out, err = repo.run("add", "Item", "--release-target", "v1", "--json")
      self.assertEqual(code, 0, err)
      fields = json.loads(out)["fields"]
      keys = list(fields)
      self.assertIsNone(fields["wave"])
      self.assertEqual(keys.index("release_target"), keys.index("pass") + 1)
      self.assertEqual(keys.index("wave"), keys.index("release_target") + 1)

  def test_Set_PassAlias_WorksAndPrintsOneDeprecationNote(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "A")
      repo.run("add", "B")
      code, out, err = repo.run("set", "S01", "S02", "--pass", "v1.20", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual([i["fields"]["release_target"] for i in json.loads(out)], ["v1.20", "v1.20"])
      self.assertEqual(repo.state().index.require("S02").pass_key, "v1.20")
      notes = [line for line in err.splitlines() if "deprecated" in line]
      self.assertEqual(len(notes), 1, err)
      self.assertIn("--release-target", notes[0])
      code, out, err = repo.run("set", "S01", "--release-target", "v1.21")
      self.assertEqual(code, 0, err)
      self.assertNotIn("deprecated", err)

  def test_Add_PassAlias_PrintsOneDeprecationNote(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      code, _, err = repo.run("add", "A", "--pass", "v1")
      self.assertEqual(code, 0, err)
      self.assertEqual(len([line for line in err.splitlines() if "deprecated" in line]), 1, err)
      self.assertEqual(repo.state().index.require("S01").pass_key, "v1")

  def test_List_PassAlias_FiltersAndPrintsOneDeprecationNote(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "A", "--release-target", "v1")
      repo.run("add", "B", "--release-target", "v2")
      code, out, err = repo.run("list", "--pass", "v2", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual([i["id"] for i in json.loads(out)], ["S02"])
      self.assertEqual(len([line for line in err.splitlines() if "deprecated" in line]), 1, err)
      code, out, err = repo.run("list", "--release-target", "v1", "--json")
      self.assertEqual([i["id"] for i in json.loads(out)], ["S01"])
      self.assertEqual(err, "")

  def test_Set_WaveNotAPositiveInteger_IsUsage(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      code, _, err = repo.run("add", "A", "--wave", "1")
      self.assertEqual(code, 0, err)
      before = repo.read(".slicer/index.json")
      for bad in ("0", "x", "-1", "1.5"):
        with self.subTest(wave=bad):
          code, out, _ = repo.run("set", "S01", "--wave", bad, "--json")
          self.assertEqual(code, 2)
          error = json.loads(out)["error"]
          self.assertEqual(error["code"], "usage")
          self.assertIn("wave", error["message"])
          self.assertEqual(repo.read(".slicer/index.json"), before)
      code, out, _ = repo.run("add", "B", "--wave", "0", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out)["error"]["code"], "usage")
      self.assertEqual(repo.read(".slicer/index.json"), before)

  def test_Set_Wave_SetsAndEmptyClears(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "A")
      code, _, err = repo.run("set", "S01", "--wave", "3")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().index.require("S01").wave, 3)
      code, _, err = repo.run("set", "S01", "--wave", "")
      self.assertEqual(code, 0, err)
      self.assertIsNone(repo.state().index.require("S01").wave)
      self.assertNotIn('"wave"', repo.read(".slicer/index.json"))

  def test_Stats_ByReleaseTarget_AndByPassAlias(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "A", "--release-target", "v1")
      repo.run("add", "B", "--release-target", "v1")
      repo.run("add", "C", "--release-target", "v2")
      code, out, err = repo.run("stats", "--json")
      self.assertEqual(code, 0, err)
      payload = json.loads(out)
      self.assertEqual(payload["by_release_target"], {"v1": 2, "v2": 1})
      self.assertEqual(payload["by_pass"], payload["by_release_target"])
      code, out, err = repo.run("stats")
      self.assertIn("target     v1 2 · v2 1", out)
