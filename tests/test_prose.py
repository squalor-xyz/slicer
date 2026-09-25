"""Editing the roadmap's own prose, and opening or closing a pass group."""

from __future__ import annotations

import json
import unittest

import support

from slicer import prose
from slicer.config import Config
from slicer.errors import StateError
from slicer.migrator import build


class ParseRefTests(unittest.TestCase):
  def test_ParseRef_PreambleAndEpilogue_AreTopLevelBlocks(self) -> None:
    self.assertEqual(prose.parse_ref("preamble").kind, "preamble")
    self.assertEqual(prose.parse_ref("epilogue").kind, "epilogue")

  def test_ParseRef_PassField_SplitsKeyAndField(self) -> None:
    ref = prose.parse_ref("pass.5.intro")
    self.assertEqual((ref.kind, ref.pass_key, ref.field), ("pass", "5", "intro"))

  def test_ParseRef_RoundTripsThroughStr(self) -> None:
    for ref in ("preamble", "epilogue", "pass.5.outro", "pass.2.heading"):
      with self.subTest(ref):
        self.assertEqual(str(prose.parse_ref(ref)), ref)

  def test_ParseRef_UnknownField_RaisesNamingTheValidForms(self) -> None:
    with self.assertRaises(StateError) as caught:
      prose.parse_ref("pass.5.middle")
    self.assertIn("pass.<key>.intro", str(caught.exception))

  def test_ParseRef_Malformed_RaisesNamingTheValidForms(self) -> None:
    for bad in ("", "nonsense", "pass.5", "pass..intro", "pass.5.intro.extra"):
      with self.subTest(bad):
        with self.assertRaises(StateError):
          prose.parse_ref(bad)


class ProseBlockTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    support.make_mini(repo)
    repo.run("init")
    repo.run("migrate", "--from", "docs/slices")
    return repo

  def test_Refs_ImportedIndex_AreListedInRenderOrder(self) -> None:
    with self.repo() as repo:
      refs = prose.refs(repo.state().index)
      self.assertEqual(refs[0], "preamble")
      self.assertEqual(refs[-1], "epilogue")
      self.assertEqual(
        refs[1:4], ["pass.1.heading", "pass.1.intro", "pass.1.outro"]
      )

  def test_Refs_FixtureTree_CoverEveryImportedPass(self) -> None:
    index, _, _ = build(support.LEGACY, Config())
    refs = prose.refs(index)
    for key in ("1", "2", "3", "4"):
      self.assertIn(f"pass.{key}.intro", refs)
    self.assertEqual(len(refs), 2 + 3 * 4)

  def test_Get_UnknownPass_RaisesNamingTheDeclaredOnes(self) -> None:
    with self.repo() as repo:
      with self.assertRaises(StateError) as caught:
        prose.get(repo.state().index, "pass.9.intro")
      self.assertIn("declared passes", str(caught.exception))

  def test_ProseList_Json_NamesEveryBlockWithItsSize(self) -> None:
    with self.repo() as repo:
      code, out, err = repo.run("prose", "list", "--json")
      self.assertEqual(code, 0, err)
      payload = json.loads(out)
      self.assertEqual(payload[0]["ref"], "preamble")
      self.assertGreater(payload[0]["lines"], 0)

  def test_ProseShow_Preamble_PrintsTheStoredText(self) -> None:
    with self.repo() as repo:
      code, out, _ = repo.run("prose", "show", "preamble")
      self.assertEqual(code, 0)
      self.assertIn("Mini index preamble.", out)

  def test_ProseEdit_FromAFile_ReplacesTheBlock(self) -> None:
    with self.repo() as repo:
      repo.write("new.md", "a replacement preamble\n")
      code, _, err = repo.run("prose", "edit", "preamble", "--file", str(repo.root / "new.md"))
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().index.preamble, "a replacement preamble")

  def test_ProseEdit_ChangesWhatTheRoadmapRenders(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      repo.write("new.md", "TOTALLY NEW OPENING\n")
      repo.run("prose", "edit", "preamble", "--file", str(repo.root / "new.md"))
      repo.run("render")
      self.assertIn("TOTALLY NEW OPENING", repo.read(".slicer/render/ROADMAP.md"))

  def test_ProseEdit_WithoutRendering_MakesCheckFail(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      repo.write("new.md", "stale-making change\n")
      repo.run("prose", "edit", "epilogue", "--file", str(repo.root / "new.md"))
      code, out, _ = repo.run("check")
      self.assertEqual(code, 1)
      self.assertIn("ROADMAP.md", out)
      self.assertEqual(repo.run("render")[0], 0)
      self.assertEqual(repo.run("check")[0], 0)

  def test_ProseEdit_IdenticalText_IsReportedAsUnchanged(self) -> None:
    with self.repo() as repo:
      current = repo.state().index.preamble
      repo.write("same.md", current + "\n")
      code, out, _ = repo.run("prose", "edit", "preamble", "--file", str(repo.root / "same.md"))
      self.assertEqual(code, 0)
      self.assertIn("unchanged", out)
      self.assertEqual(repo.state().history(), [])

  def test_ProseEdit_Block_AppendsALogEntry(self) -> None:
    with self.repo() as repo:
      repo.write("new.md", "changed\n")
      repo.run("prose", "edit", "pass.1.outro", "--file", str(repo.root / "new.md"))
      entry = repo.state().history()[-1]
      self.assertEqual((entry.item, entry.action), ("pass.1.outro", "prose"))

  def test_ProseEdit_UnknownBlock_ExitsTwoWithoutWriting(self) -> None:
    with self.repo() as repo:
      before = repo.read(".slicer/index.json")
      code, _, err = repo.run("prose", "edit", "nonsense", "--stdin")
      self.assertEqual(code, 2)
      self.assertIn("valid forms", err)
      self.assertEqual(repo.read(".slicer/index.json"), before)


class PassGroupTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    support.make_mini(repo)
    repo.run("init")
    repo.run("migrate", "--from", "docs/slices")
    return repo

  def test_AddPass_NoItems_StillRendersItsHeadingAndIntro(self) -> None:
    with self.repo() as repo:
      repo.run("prose", "add-pass", "9", "--heading", "# Pass nine")
      repo.write("i.md", "the ninth pass opens here\n")
      repo.run("prose", "edit", "pass.9.intro", "--file", str(repo.root / "i.md"))
      repo.run("render")
      roadmap = repo.read(".slicer/render/ROADMAP.md")
      self.assertIn("# Pass nine", roadmap)
      self.assertIn("the ninth pass opens here", roadmap)

  def test_AddPass_ThenAddItemWithPassFlag_PlacesItInThatGroup(self) -> None:
    with self.repo() as repo:
      repo.run("prose", "add-pass", "9", "--heading", "# Pass nine")
      code, _, err = repo.run("add", "a ninth-pass idea", "--pass", "9")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().index.require("S05").pass_key, "9")

  def test_AddPass_WithAfter_InsertsInThatPosition(self) -> None:
    with self.repo() as repo:
      repo.run("prose", "add-pass", "9", "--after", "1")
      self.assertEqual([p.key for p in repo.state().index.passes], ["1", "9", "2"])

  def test_AddPass_AfterAnUnknownPass_Refuses(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("prose", "add-pass", "9", "--after", "77")
      self.assertEqual(code, 2)
      self.assertIn("declared passes", err)

  def test_AddPass_DuplicateKey_Refuses(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("prose", "add-pass", "1")
      self.assertEqual(code, 2)
      self.assertIn("already exists", err)

  def test_DropPass_WithItems_RefusesNamingTheCount(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("prose", "drop-pass", "1")
      self.assertEqual(code, 2)
      self.assertIn("still has 2 item(s)", err)
      self.assertIn("--pass", err)

  def test_DropPass_Empty_RemovesIt(self) -> None:
    with self.repo() as repo:
      repo.run("prose", "add-pass", "9")
      self.assertEqual(repo.run("prose", "drop-pass", "9")[0], 0)
      self.assertNotIn("9", [p.key for p in repo.state().index.passes])

  def test_DropPass_AfterMovingItsItemsAway_Succeeds(self) -> None:
    with self.repo() as repo:
      repo.run("prose", "add-pass", "9")
      for item_id in ("S01", "S02"):
        repo.run("set", item_id, "--pass", "9")
      self.assertEqual(repo.run("prose", "drop-pass", "1")[0], 0)
      self.assertEqual(repo.state().index.require("S01").pass_key, "9")

  def test_DropPass_Unknown_Refuses(self) -> None:
    with self.repo() as repo:
      self.assertEqual(repo.run("prose", "drop-pass", "77")[0], 2)

  def test_PassKeys_ItemNamingAnUndeclaredPass_IsStillRendered(self) -> None:
    with self.repo() as repo:
      state = repo.state()
      state.index.require("S01").pass_key = "ghost"
      state.save_index()
      self.assertIn("ghost", repo.state().index.pass_keys())
      repo.run("render")
      self.assertIn("First thing", repo.read(".slicer/render/ROADMAP.md"))

  def test_PassKeys_DeclaredOrder_WinsOverItemOrder(self) -> None:
    with self.repo() as repo:
      state = repo.state()
      state.index.passes.reverse()
      state.save_index()
      self.assertEqual(repo.state().index.pass_keys(), ["2", "1"])
      repo.run("render")
      roadmap = repo.read(".slicer/render/ROADMAP.md")
      self.assertLess(roadmap.index("review pass 2"), roadmap.index("review pass 1"))


if __name__ == "__main__":
  unittest.main()


class ProseRenderFlagTests(unittest.TestCase):
  """prose mutations can render in the same step, like item mutations (S47)."""

  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    support.make_mini(repo)
    repo.run("init")
    repo.run("migrate", "--from", "docs/slices")
    repo.run("render")
    return repo

  def test_ProseEdit_Render_LeavesCheckClean(self) -> None:
    with self.repo() as repo:
      repo.write("new.md", "A fresh preamble.")
      code, out, _ = repo.run("prose", "edit", "preamble", "--file", str(repo.root / "new.md"), "--render")
      self.assertEqual(code, 0, out)
      self.assertIn("rendered", out)
      self.assertEqual(repo.run("check")[0], 0)

  def test_ProseAddPass_Render_LeavesCheckClean(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("prose", "add-pass", "9", "--heading", "# Pass 9", "--render")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.run("check")[0], 0)

  def test_ProseEdit_WithoutRender_StaysStale(self) -> None:
    with self.repo() as repo:
      repo.write("new.md", "Another preamble.")
      repo.run("prose", "edit", "preamble", "--file", str(repo.root / "new.md"))
      self.assertEqual(repo.run("check")[0], 1)
