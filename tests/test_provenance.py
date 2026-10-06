"""Keep discovery references traceable without making them scheduling edges."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import support
from slicer import graph, model, ops, outline, render, templates, verify
from slicer.errors import StateError


class ProvenanceTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    self.assertEqual(repo.run("init")[0], 0)
    self.assertEqual(repo.run("add", "Source", "--effort", "1")[0], 0)
    return repo

  def snapshot(self, repo: support.TempRepo) -> dict[str, bytes]:
    return {str(p.relative_to(repo.root)): p.read_bytes()
            for p in (repo.root / ".slicer").rglob("*")
            if p.is_file() and p.name != "lock"}

  def test_Add_SourceAnyStatus_PersistsAndRenders(self) -> None:
    for status in ("open", "done", "retired"):
      with self.subTest(status=status), self.repo() as repo:
        self.assertEqual(repo.run("set", "S01", "--status", status)[0], 0)
        code, out, err = repo.run("add", "Found", "--discovered-from", "S01",
                                  "--effort", "1", "--render", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["fields"]["discovered_from"], "S01")
        state = repo.state()
        self.assertEqual(state.index.require("S02").discovered_from, "S01")
        self.assertIn("discovered from S01", repo.read(".slicer/render/ROADMAP.md"))
        self.assertIn("discovered from S01", repo.read(".slicer/render/ROADMAP.html"))
        self.assertEqual(verify.offline(state).problems, [])

  def test_Add_UnknownSource_NoAllocationOrWrites(self) -> None:
    with self.repo() as repo:
      before = self.snapshot(repo)
      code, out, err = repo.run("add", "Found", "--discovered-from", "S99",
                                "--render", "--json")
      self.assertEqual(code, 2, err)
      self.assertEqual(json.loads(out)["error"]["code"], "no_such_item")
      self.assertEqual(self.snapshot(repo), before)
      state = repo.state()
      next_id = state.index.next_id
      with self.assertRaises(StateError):
        ops.add(state, "Found", discovered_from="S99")
      self.assertEqual(state.index.next_id, next_id)

  def test_Import_SourceAnyStatus_PersistsAndReports(self) -> None:
    for status in ("open", "done", "retired"):
      with self.subTest(status=status), self.repo() as repo:
        repo.run("set", "S01", "--status", status)
        text = "## Found\ndiscovered_from: S01\neffort: 1\n"
        parsed = outline.parse(text)
        self.assertEqual(parsed.items[0].to_dict()["discovered_from"], "S01")
        repo.write("outline.md", text)
        before = self.snapshot(repo)
        code, out, err = repo.run("import", "outline.md", "--dry-run", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["depends_edges"], 0)
        self.assertEqual(self.snapshot(repo), before)
        code, _, err = repo.run("import", "outline.md", "--render")
        self.assertEqual(code, 0, err)
        self.assertEqual(repo.state().index.require("S02").discovered_from, "S01")
        self.assertIn("discovered from S01", repo.read(".slicer/render/ROADMAP.md"))

  def test_Import_UnknownOrProspectiveSource_RefusesWholeBatch(self) -> None:
    for source in ("S99", "S02", "New source", "S01,S02"):
      with self.subTest(source=source), self.repo() as repo:
        repo.write("outline.md", "## New source\neffort: 1\n\n"
                   f"## Found\ndiscovered_from: {source}\neffort: 1\n")
        before = self.snapshot(repo)
        for dry in ([], ["--dry-run"]):
          code, out, err = repo.run("import", "outline.md", *dry, "--render", "--json")
          self.assertEqual(code, 1, err)
          self.assertIn("discovered_from", json.loads(out)["problems"][0])
          self.assertEqual(self.snapshot(repo), before)

  def test_List_ExactSource_ComposesAndValidates(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Other source")
      repo.run("add", "Found", "--discovered-from", "S01", "--tree", "core")
      repo.run("add", "Other tree", "--discovered-from", "S01", "--tree", "cli")
      repo.run("add", "Other status", "--discovered-from", "S01",
               "--tree", "core", "--status", "done")
      repo.run("add", "Other source work", "--discovered-from", "S02", "--tree", "core")
      code, out, err = repo.run("list", "--discovered-from", "S01", "--tree", "core",
                                "--status", "open", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual([i["id"] for i in json.loads(out)], ["S03"])
      code, out, _ = repo.run("list", "--discovered-from", "S03", "--json")
      self.assertEqual((code, json.loads(out)), (0, []))
      for source in ("S99", "S0", "s01", ""):
        code, out, _ = repo.run("list", "--discovered-from", source, "--json")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"]["code"], "no_such_item")

  def test_Model_HistoricalField_DefaultsWithoutWritesAndUpgrades(self) -> None:
    with self.repo() as repo:
      path = repo.root / ".slicer/index.json"
      old = json.loads(path.read_text())
      old["version"] = 4
      for item in old["items"]:
        del item["fields"]["discovered_from"]
      path.write_text(json.dumps(old))
      before = self.snapshot(repo)
      code, out, err = repo.run("show", "S01", "--json", "--lean")
      self.assertEqual(code, 0, err)
      self.assertNotIn("discovered_from", json.loads(out)["fields"])
      self.assertEqual(repo.state().index.require("S01").discovered_from, "")
      self.assertEqual(self.snapshot(repo), before)
      repo.run("add", "Found", "--discovered-from", "S01")
      self.assertEqual(json.loads(path.read_text())["version"], 7)
      with patch.object(model, "SCHEMA_VERSION", 4), self.assertRaises(StateError) as exc:
        model.Index.from_dict(json.loads(path.read_text()))
      self.assertEqual(exc.exception.code, "schema_too_new")
      old["items"][0]["fields"]["discovered_from"] = ["S01"]
      with self.assertRaises(StateError) as exc:
        model.Item.from_dict(old["items"][0])
      self.assertEqual(exc.exception.code, "corrupt")

  def test_Render_EmptySource_UnchangedRowAndCustomPlaceholder(self) -> None:
    with self.repo() as repo:
      state = repo.state()
      item = state.index.require("S01")
      row = render.render_row(item, 1, state.config, templates.default("row.md"))
      self.assertEqual(row, "| 1 | S01 | Source |  | 1 |  |  | — |")
      item.discovered_from = "S99"
      custom = render.render_row(item, 1, state.config, "{{id}}: {{discovered_from}}")
      self.assertEqual(custom, "S01: S99")

  def test_Pickup_SourceOpen_DoesNotBlockOrPropagateScore(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Found", "--discovered-from", "S01", "--importance", "3")
      state = repo.state()
      self.assertEqual(state.index.require("S02").depends_on, [])
      self.assertEqual(graph.effective_scores(state.index), {"S01": 22, "S02": 32})
      code, out, err = repo.run("next", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out)["id"], "S02")

  def test_Remove_ReferencedSource_RetiresButPurgeNeedsForce(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Found", "--discovered-from", "S01")
      before = self.snapshot(repo)
      code, out, _ = repo.run("remove", "S01", "--purge", "--dry-run", "--json")
      self.assertEqual(code, 0)
      self.assertIn("discovered from", " ".join(json.loads(out)["blockers"]))
      code, _, err = repo.run("remove", "S01", "--purge")
      self.assertEqual(code, 2, err)
      self.assertEqual(self.snapshot(repo), before)
      code, _, err = repo.run("remove", "S01", "--reason", "Obsolete")
      self.assertEqual(code, 0, err)
      self.assertEqual(verify.offline(repo.state()).problems, [])
      code, _, err = repo.run("remove", "S01", "--purge", "--force")
      self.assertEqual(code, 0, err)
      report = verify.offline(repo.state())
      self.assertTrue(report.problems)
      self.assertTrue(any("discovered_from unknown id S01" in f.message
                          for f in report.findings))

  def test_Purge_ReferencedLatestSource_DoesNotReuseDanglingID(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Latest source")
      state = repo.state()
      state.index.require("S01").discovered_from = "S02"
      state.save_index()
      preview = ops.remove_preview(state, "S02", purge=True)
      self.assertFalse(preview.id_freed)
      result = ops.purge(state, "S02", force=True)
      self.assertFalse(result.id_freed)
      self.assertEqual(ops.add(state, "Replacement").id, "S03")
