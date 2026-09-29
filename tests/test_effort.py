"""Optional effort estimates: store, clear, display, and sort without touching priority."""

from __future__ import annotations

import json
import unittest

import support

from slicer import graph, ops, tui, tui_wizard
from slicer.errors import StateError
from slicer.model import Item


class EffortStoreTests(unittest.TestCase):
  def test_Item_MissingEffort_LoadsAsNone(self) -> None:
    item = Item.from_dict({
      "id": "S01",
      "title": "Old",
      "status": "open",
      "fields": {"importance": 2, "urgency": 2},
    })
    self.assertIsNone(item.effort)

  def test_Add_OmittedEffort_StaysUnsetAndJsonCarriesNull(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      code, out, err = repo.run("add", "Plain", "--json")
      self.assertEqual(code, 0, err)
      payload = json.loads(out)
      self.assertIsNone(payload["fields"]["effort"])
      self.assertIsNone(repo.state().index.require("S01").effort)

  def test_AddAndSet_AcceptOnlyOneTwoOrThree(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      code, out, err = repo.run("add", "Weighed", "--effort", "2", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out)["fields"]["effort"], 2)
      for bad in ("0", "4"):
        code, _, err = repo.run("set", "S01", "--effort", bad)
        self.assertEqual(code, 2, err)
        self.assertIn("1, 2 or 3", err)
        self.assertEqual(repo.state().index.require("S01").effort, 2)
      code, out, err = repo.run("set", "S01", "--no-effort", "--json")
      self.assertEqual(code, 0, err)
      self.assertIsNone(json.loads(out)["fields"]["effort"])
      self.assertIsNone(repo.state().index.require("S01").effort)

  def test_Set_EffortAndNoEffortTogether_IsUsage(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "Plain")
      code, _, err = repo.run("set", "S01", "--effort", "1", "--no-effort")
      self.assertEqual(code, 2)
      self.assertIn("cannot be combined", err)
      self.assertIsNone(repo.state().index.require("S01").effort)

  def test_Set_OmittedEffortPreservesValue_AndExplicitNullClears(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "Weighed", "--effort", "2")
      code, _, err = repo.run("set", "S01", "--size", "S")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().index.require("S01").effort, 2)
      ops.set_fields(repo.state(), "S01", effort=None)
      code, out, err = repo.run("show", "S01", "--json")
      self.assertEqual(code, 0, err)
      self.assertIsNone(json.loads(out)["fields"]["effort"])

  def test_Import_EffortKey_StoresAndRejectsOtherValues(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.write("r.md", "## Weighed\neffort: 3\n")
      code, _, err = repo.run("import", "r.md")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().index.require("S01").effort, 3)
      repo.write("bad.md", "## Other\neffort: 9\n")
      code, _, err = repo.run("import", "bad.md")
      self.assertEqual(code, 2)
      self.assertIn("effort must be 1, 2 or 3", err)
      self.assertIsNone(repo.state().index.get("S02"))

  def test_Skeleton_ExampleEffort_RoundTrips(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      _, skeleton, _ = repo.run("import", "--skeleton")
      self.assertIn("effort:     1, 2 or 3", skeleton)
      repo.write("r.md", skeleton)
      code, _, err = repo.run("import", "r.md")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().index.require("S01").effort, 1)

  def test_TuiEdit_BlankClearsAndOtherValuesRefuse(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "Plain", "--effort", "2")
      request = tui.EditRequest(kind="field", target="S01", name="effort", body="2")
      result = tui.apply_edit_result(repo.state(), request, "4")
      self.assertEqual(result.severity, "error")
      self.assertEqual(repo.state().index.require("S01").effort, 2)
      result = tui.apply_edit_result(repo.state(), request, "")
      self.assertEqual(result.severity, "success")
      self.assertIsNone(repo.state().index.require("S01").effort)

  def test_Wizard_BlankEffort_StaysUnsetAndADigitIsStored(self) -> None:
    item = tui_wizard.DraftItem()
    item.values[0] = "From the wizard"
    self.assertIsNone(item.spec().effort)
    item.values[8] = "3"
    self.assertEqual(item.spec().effort, 3)
    item.values[8] = "9"
    with self.assertRaises(StateError) as caught:
      item.spec()
    self.assertIn("1, 2 or 3", str(caught.exception))


class EffortSortTests(unittest.TestCase):
  def load(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.run("add", "Heavy", "--effort", "3")
    repo.run("add", "Unset tie A")
    repo.run("add", "Light", "--effort", "1")
    repo.run("add", "Unset tie B")
    repo.run("add", "Mid", "--effort", "2")
    return repo

  def test_List_SortEffort_OrdersLightestFirstAndWritesNothing(self) -> None:
    with self.load() as repo:
      before = repo.read(".slicer/index.json")
      code, out, err = repo.run("list", "--sort", "effort")
      self.assertEqual(code, 0, err)
      ids = [line.split()[1] for line in out.splitlines()]
      self.assertEqual(ids, ["S03", "S05", "S01", "S02", "S04"])
      self.assertIn(" 1 ", out)
      self.assertIn(" - ", out)
      self.assertEqual(repo.read(".slicer/index.json"), before)

  def test_Sort_ByEffort_PersistsThatOrderAndReportsMoved(self) -> None:
    with self.load() as repo:
      code, out, err = repo.run("sort", "--by", "effort")
      self.assertEqual(code, 0, err)
      self.assertIn("moved", out)
      ids = [item.id for item in repo.state().index.items]
      self.assertEqual(ids, ["S03", "S05", "S01", "S02", "S04"])
      moved = json.loads(repo.run("sort", "--by", "effort", "--json")[1])["moved"]
      self.assertEqual(moved, 0)

  def test_Render_OldRowTemplateWithoutEffort_StillRenders(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "A thing", "--effort", "2")
      repo.write(
        ".slicer/templates/row.md",
        "| {{position}} | {{idcell}} | {{title}} | {{size}} | {{trees}} | {{findings}} | {{status}} |\n",
      )
      code, _, err = repo.run("render")
      self.assertEqual(code, 0, err)
      text = repo.read(".slicer/render/ROADMAP.md")
      self.assertNotIn("| Effort |", text)
      self.assertIn("| Size | Trees |", text)

  def test_Render_ShowsEffortOrADash(self) -> None:
    with self.load() as repo:
      code, _, err = repo.run("render")
      self.assertEqual(code, 0, err)
      markdown = repo.read(".slicer/render/ROADMAP.md")
      html = repo.read(".slicer/render/ROADMAP.html")
      self.assertIn("| Effort |", markdown)
      self.assertIn("| 1 |", markdown)
      self.assertIn("| - |", markdown)
      self.assertIn("<th>Effort</th>", html)
      self.assertIn("<td>1</td>", html)
      self.assertIn("<td>-</td>", html)

  def test_NextAndScoreSort_IgnoreEffort(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "Heavy important", "--importance", "3", "--urgency", "3", "--effort", "3")
      repo.run("add", "Light trivial", "--importance", "1", "--urgency", "1", "--effort", "1")
      repo.run("add", "Blocker", "--importance", "1", "--urgency", "1")
      repo.run("add", "Depends on the blocker", "--importance", "3", "--urgency", "3",
               "--depends-on", "S03")
      default_ids = [line.split()[1] for line in repo.run("list")[1].splitlines()]
      score_ids = [line.split()[1] for line in repo.run("list", "--sort", "score")[1].splitlines()]
      effort_ids = [line.split()[1] for line in repo.run("list", "--sort", "effort")[1].splitlines()]
      nxt = repo.run("next")[1].splitlines()[0].split()[0]
      # Score order and next put the important item first. Effort order puts
      # the light item first, so the two sorts are not the same sequence.
      self.assertEqual(nxt, "S01")
      self.assertEqual(score_ids[0], "S01")
      self.assertEqual(default_ids[0], "S01")
      self.assertEqual(effort_ids[0], "S02")
      blocker = repo.state().index.require("S03")
      scores = graph.effective_scores(repo.state().index)
      self.assertGreater(scores[blocker.id], blocker.score)
