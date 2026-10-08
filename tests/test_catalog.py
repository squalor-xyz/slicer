"""Catalog records: their own ids and lifecycle, off the slice queue."""

from __future__ import annotations

import json
import unittest

import support
from slicer import jsonio, render
from slicer.config import Config
from slicer.model import SCHEMA_VERSION, Index, Item
from slicer.store import DIR_NAME, INDEX_NAME


def _messages(report: dict) -> str:
  return " ".join(finding["message"] for finding in report["findings"])


class CatalogCommandTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    return repo

  def refused(self, repo: support.TempRepo, code_name: str, *argv: str) -> str:
    """A refused command exits 2 with `code_name` on the JSON envelope."""
    exit_code, out, err = repo.run(*argv, "--json")
    self.assertEqual(exit_code, 2, err)
    error = json.loads(out)["error"]
    self.assertEqual(error["code"], code_name)
    return error["message"]

  def test_Catalog_AddEditRetireMove_KeepsKindOrderAndHistory(self) -> None:
    with self.repo() as repo:
      code, out, err = repo.run(
        "catalog", "add", "--kind", "non-goal", "No service", "--body", "Local only.",
        "--json",
      )
      self.assertEqual((code, err), (0, ""))
      added = json.loads(out)
      self.assertEqual(added["id"], "C01")
      self.assertEqual(added["kind"], "non_goal")
      self.assertEqual(added["status"], "active")

      repo.run("catalog", "add", "--kind", "goal", "Ship it", "--json")
      repo.run("catalog", "add", "--kind", "goal", "Stay small", "--json")
      repo.run("catalog", "add", "--kind", "requirement", "Stdlib only", "--json")
      code, _, err = repo.run("catalog", "edit", "C03", "--title", "Stay tiny", "--json")
      self.assertEqual((code, err), (0, ""))
      code, _, err = repo.run(
        "catalog", "move", "C03", "--before", "C02", "--json",
      )
      self.assertEqual((code, err), (0, ""))
      self.assertEqual(
        [record.id for record in repo.state().index.catalog.records],
        ["C01", "C03", "C02", "C04"],
      )
      code, out, err = repo.run("catalog", "list", "--json")
      self.assertEqual((code, err), (0, ""))
      self.assertEqual(
        [record["id"] for record in json.loads(out)],
        ["C03", "C02", "C01", "C04"],
      )

      message = self.refused(repo, "state", "catalog", "move", "C03", "--before", "C04")
      self.assertIn("move stays inside one kind", message)
      self.assertEqual(
        [record.id for record in repo.state().index.catalog.records],
        ["C01", "C03", "C02", "C04"],
      )

      code, _, err = repo.run("catalog", "retire", "C01", "--reason", "Out of date", "--json")
      self.assertEqual((code, err), (0, ""))
      code, out, err = repo.run("catalog", "list", "--kind", "non_goal", "--json")
      self.assertEqual((code, err, json.loads(out)), (0, "", []))
      code, out, _ = repo.run("catalog", "list", "--kind", "non-goal", "--retired", "--json")
      retired = json.loads(out)
      self.assertEqual(retired[0]["status"], "retired")
      self.assertEqual(retired[0]["reason"], "Out of date")

      message = self.refused(repo, "state", "catalog", "edit", "C01", "--title", "Changed")
      self.assertIn("retired", message)
      code, out, _ = repo.run("log", "--item", "C01", "--json")
      notes = [entry["note"] for entry in json.loads(out)]
      self.assertIn("add", notes)
      self.assertIn("retire", notes)

      code, out, _ = repo.run("list", "--json", "--lean")
      self.assertEqual(json.loads(out), [])
      self.assertEqual(repo.state().index.version, SCHEMA_VERSION)
      self.assertEqual(repo.state().index.items, [])

  def test_Catalog_Successor_RefusesAnotherKindAMissingIdAndACycle(self) -> None:
    with self.repo() as repo:
      repo.run("catalog", "add", "--kind", "goal", "One")
      repo.run("catalog", "add", "--kind", "goal", "Two")
      repo.run("catalog", "add", "--kind", "requirement", "Must")
      before = (repo.root / DIR_NAME / INDEX_NAME).read_bytes()

      message = self.refused(
        repo, "state", "catalog", "retire", "C01", "--reason", "Replaced", "--successor", "C03",
      )
      self.assertIn("successor has to be a goal", message)
      message = self.refused(
        repo, "no_such_record", "catalog", "retire", "C01", "--reason", "Replaced", "--successor", "C99",
      )
      self.assertIn("C99", message)
      message = self.refused(
        repo, "state", "catalog", "retire", "C01", "--reason", "Replaced", "--successor", "C01",
      )
      self.assertIn("cycle", message)
      message = self.refused(repo, "usage", "catalog", "retire", "C01", "--reason", "   ")
      self.assertIn("blank", message)
      self.assertEqual((repo.root / DIR_NAME / INDEX_NAME).read_bytes(), before)

      repo.run("catalog", "retire", "C02", "--reason", "Folded into C01", "--successor", "C01")
      message = self.refused(
        repo, "state", "catalog", "retire", "C01", "--reason", "Folded back", "--successor", "C02",
      )
      self.assertIn("cycle", message)

  def test_Catalog_LoadWithoutCatalogOrCites_DoesNotWrite(self) -> None:
    with self.repo() as repo:
      repo.run("add", "One")
      path = repo.root / DIR_NAME / INDEX_NAME
      data = json.loads(path.read_text(encoding="utf-8"))
      data["version"] = 7
      data.pop("catalog", None)
      for item in data["items"]:
        item.get("fields", {}).pop("cites", None)
      path.write_text(json.dumps(data), encoding="utf-8")
      before = path.read_bytes()
      state = repo.state()
      self.assertEqual(path.read_bytes(), before)
      self.assertEqual(state.index.catalog.records, [])
      self.assertEqual(state.index.items[0].cites, [])
      self.assertEqual(state.index.version, 7)

  def test_Catalog_StoredProblems_FailVerify(self) -> None:
    with self.repo() as repo:
      repo.run("catalog", "add", "--kind", "goal", "One")
      repo.run("catalog", "add", "--kind", "goal", "Two")
      path = repo.root / DIR_NAME / INDEX_NAME
      data = json.loads(path.read_text(encoding="utf-8"))
      data["catalog"]["records"][0]["successor"] = "C02"
      data["catalog"]["records"][1]["successor"] = "C01"
      path.write_text(json.dumps(data), encoding="utf-8")
      code, out, _ = repo.run("verify", "--json")
      self.assertEqual(code, 1)
      self.assertIn("successor cycle", _messages(json.loads(out)))

      data["catalog"]["records"][1]["successor"] = "C99"
      path.write_text(json.dumps(data), encoding="utf-8")
      code, out, _ = repo.run("verify", "--json")
      self.assertIn("successor unknown id C99", _messages(json.loads(out)))

      data["catalog"]["records"][0]["successor"] = ""
      data["catalog"]["records"][0]["status"] = "retired"
      data["catalog"]["records"][0]["reason"] = ""
      data["catalog"]["records"][1]["successor"] = ""
      data["catalog"]["records"][1]["reason"] = "not retired"
      path.write_text(json.dumps(data), encoding="utf-8")
      code, out, _ = repo.run("verify", "--json")
      messages = _messages(json.loads(out))
      self.assertIn("blank reason", messages)
      self.assertIn("retire reason", messages)

      state = repo.state()
      state.index.catalog.id_prefix = state.index.id_prefix
      state.save_index()
      code, out, _ = repo.run("verify", "--json")
      self.assertIn("matches the item prefix", _messages(json.loads(out)))

  def test_Catalog_BadInput_RefusesBeforeAnIdIsTaken(self) -> None:
    with self.repo() as repo:
      message = self.refused(repo, "usage", "catalog", "add", "--kind", "wish", "Nope")
      self.assertIn("wish", message)
      message = self.refused(repo, "blank_title", "catalog", "add", "--kind", "goal", "  ")
      self.assertIn("blank", message)
      message = self.refused(repo, "newline_in_field", "catalog", "add", "--kind", "goal", "Has\nline")
      self.assertIn("newline", message)
      self.assertEqual(repo.state().index.catalog.next_id, 1)
      self.assertEqual(repo.state().index.catalog.records, [])

  def test_Goals_IncludesGoalRecords_AndNotRequirements(self) -> None:
    with self.repo() as repo:
      repo.run("catalog", "add", "--kind", "goal", "Ship it", "--body", "Soon.")
      repo.run("catalog", "add", "--kind", "requirement", "Stdlib")
      code, out, err = repo.run("goals", "--json")
      self.assertEqual((code, err), (0, ""))
      payload = json.loads(out)
      self.assertEqual([record["id"] for record in payload["records"]["goal"]], ["C01"])
      self.assertEqual(payload["records"]["non_goal"], [])
      self.assertNotIn("C02", out)
      code, text, _ = repo.run("goals")
      self.assertIn("C01", text)
      self.assertIn("Ship it", text)
      self.assertNotIn("Stdlib", text)


class CatalogRenderTests(unittest.TestCase):
  def test_Render_EmptyCatalog_MatchesTemplateWithoutTheSlot(self) -> None:
    index = Index(
      goals="- a goal",
      non_goals="- a non-goal",
      items=[Item(id="s01", title="One", status="open")],
    )
    cfg = Config()
    template = (support.SRC / "slicer" / "templates" / "roadmap.md").read_text(encoding="utf-8")
    row = (support.SRC / "slicer" / "templates" / "row.md").read_text(encoding="utf-8")
    with_slot = render.render_roadmap(index, cfg, template, row)
    without = template.replace("\n\n{{catalog}}\n", "\n")
    self.assertNotIn("{{catalog}}", without)
    self.assertEqual(with_slot, render.render_roadmap(index, cfg, without, row))
    html_out = render.render_html(index, cfg).decode("utf-8")
    self.assertNotIn("<h3>", html_out)
    self.assertIn("a goal", html_out)

  def test_Render_Records_AppearUnderTheirKind(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("catalog", "add", "--kind", "goal", "Ship it", "--body", "Soon.")
      repo.run("catalog", "add", "--kind", "requirement", "Stdlib only", "--body", "No packages.")
      repo.run(
        "catalog", "add", "--kind", "decision", "JSON state", "--body", "Files, not a database.",
      )
      repo.run("catalog", "add", "--kind", "decision", "Files")
      repo.run("catalog", "retire", "C03", "--reason", "Superseded", "--successor", "C04")
      repo.run("render")
      roadmap = repo.read(".slicer/render/ROADMAP.md")
      html_out = repo.read(".slicer/render/ROADMAP.html")
      self.assertIn("### C01 Ship it", roadmap)
      self.assertIn("## Requirements", roadmap)
      self.assertIn("### C02 Stdlib only", roadmap)
      self.assertIn("*Retired: Superseded. Successor: C04.*", roadmap)
      self.assertLess(roadmap.index("### C01"), roadmap.index("## Requirements"))
      self.assertIn("<h3>C02 Stdlib only</h3>", html_out)
      self.assertIn("Retired: Superseded. Successor: C04.", html_out)


class CatalogMergeTests(unittest.TestCase):
  def _merge(self, repo: support.TempRepo, base: str, ours: str, theirs: str):
    for name, text in (("base", base), ("ours", ours), ("theirs", theirs)):
      repo.write(name, text)
    code, out, err = repo.run(
      "merge-index", *(str(repo.root / name) for name in ("base", "ours", "theirs")),
    )
    return code, out, err, repo.read("ours")

  def _doc(self, records: list[dict], catalog_next: int, item_next: int = 4) -> str:
    items = [
      {"id": "s01", "title": "item 1", "short_title": "item 1",
       "status": "open", "has_slice": False, "depends_on": []},
    ]
    return jsonio.dumps({
      "version": 8,
      "id_prefix": "s",
      "next_id": item_next,
      "items": items,
      "catalog": {
        "next_id": catalog_next,
        "id_prefix": "C",
        "id_width": 2,
        "records": records,
      },
    })

  def _record(self, record_id: str, title: str, kind: str = "goal") -> dict:
    return {
      "id": record_id, "kind": kind, "title": title, "body": "",
      "status": "active", "reason": "", "successor": "",
    }

  def test_Merge_DisjointCatalogRecords_KeepsBothAndTheLargerCounter(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      base = self._doc([], 1)
      ours = self._doc([self._record("C01", "From ours")], 2)
      theirs = self._doc([self._record("C02", "From theirs")], 5)
      code, out, err, merged = self._merge(repo, base, ours, theirs)
      self.assertEqual((code, out, err), (0, "", ""), merged)
      doc = json.loads(merged)
      self.assertEqual([record["id"] for record in doc["catalog"]["records"]], ["C01", "C02"])
      self.assertEqual(doc["catalog"]["next_id"], 5)
      self.assertEqual(doc["next_id"], 4)

  def test_Merge_SameRecordTitle_ConflictsOnThatField(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      base = self._doc([self._record("C01", "Base")], 2)
      ours = self._doc([self._record("C01", "Ours")], 2)
      theirs = self._doc([self._record("C01", "Theirs")], 2)
      code, _, _, merged = self._merge(repo, base, ours, theirs)
      self.assertEqual(code, 1)
      self.assertIn("<<<<<<<", merged)
      self.assertIn("Ours", merged)
      self.assertIn("Theirs", merged)


class CiteTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    return repo

  def index_bytes(self, repo: support.TempRepo) -> bytes:
    return (repo.root / DIR_NAME / INDEX_NAME).read_bytes()

  def test_Cite_AppendsSkipsDuplicatesAndRefusesUnknownIds(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Ship")
      repo.run("catalog", "add", "--kind", "goal", "Ship it")
      repo.run("catalog", "add", "--kind", "assumption", "One file")
      nxt = json.loads(repo.run("next", "--json")[1])["id"]
      code, out, err = repo.run("catalog", "cite", "S01", "C02", "C01", "C02", "--json")
      self.assertEqual((code, err), (0, ""))
      self.assertEqual(json.loads(out)["fields"]["cites"], ["C02", "C01"])
      before = self.index_bytes(repo)
      code, out, err = repo.run("catalog", "cite", "S01", "C01", "--json")
      self.assertEqual((code, err), (0, ""))
      self.assertFalse(json.loads(out)["changed"])
      self.assertEqual(self.index_bytes(repo), before)
      message = CatalogCommandTests.refused(
        self, repo, "no_such_record", "catalog", "cite", "S01", "C01", "C99",
      )
      self.assertIn("C99", message)
      self.assertEqual(self.index_bytes(repo), before)
      self.assertEqual(repo.state().index.require("S01").cites, ["C02", "C01"])
      actions = [entry["action"] for entry in json.loads(repo.run("log", "--item", "S01", "--json")[1])]
      self.assertEqual(actions.count("cite"), 1)
      self.assertEqual(json.loads(repo.run("next", "--json")[1])["id"], nxt)

  def test_Uncite_MissingCitation_RefusesBeforeWriting(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Ship")
      repo.run("catalog", "add", "--kind", "goal", "Ship it")
      repo.run("catalog", "add", "--kind", "goal", "Stay small")
      repo.run("catalog", "add", "--kind", "goal", "Third")
      repo.run("catalog", "cite", "S01", "C01", "C02")
      before = self.index_bytes(repo)
      message = CatalogCommandTests.refused(
        self, repo, "no_such_record", "catalog", "uncite", "S01", "C99",
      )
      self.assertIn("C99", message)
      message = CatalogCommandTests.refused(
        self, repo, "state", "catalog", "uncite", "S01", "C01", "C03",
      )
      self.assertIn("does not cite", message)
      self.assertEqual(self.index_bytes(repo), before)
      code, out, err = repo.run("catalog", "uncite", "S01", "C01", "--json")
      self.assertEqual((code, err), (0, ""))
      self.assertEqual(json.loads(out)["fields"]["cites"], ["C02"])
      actions = [entry["action"] for entry in json.loads(repo.run("log", "--item", "S01", "--json")[1])]
      self.assertIn("uncite", actions)

  def test_Show_ResolvesCitations(self) -> None:
    with self.repo() as repo:
      repo.run("add", "First")
      repo.run("add", "Second")
      repo.run("catalog", "add", "--kind", "goal", "Ship it", "--body", "Soon.")
      repo.run("catalog", "cite", "S02", "C01")
      repo.run("catalog", "cite", "S01", "C01")
      state = repo.state()
      state.index.require("S02").status = "done"
      state.save_index()
      code, out, err = repo.run("catalog", "show", "C01", "--json")
      self.assertEqual((code, err), (0, ""))
      self.assertEqual(json.loads(out)["cited_by"], ["S01", "S02"])
      code, out, err = repo.run("show", "S01", "--json")
      self.assertEqual((code, err), (0, ""))
      shown = json.loads(out)["citations"]
      self.assertEqual(shown, [{
        "id": "C01",
        "kind": "goal",
        "title": "Ship it",
        "status": "active",
        "reason": "",
        "successor": "",
      }])

  def test_Render_RetiredAssumptionAndDecision_CallOutOnTheRowAndSlice(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Ship", "--findings", "G1")
      repo.run("promote", "S01")
      repo.run("catalog", "add", "--kind", "goal", "Ship it")
      repo.run("catalog", "add", "--kind", "assumption", "One file")
      repo.run("catalog", "add", "--kind", "decision", "JSON")
      repo.run("catalog", "add", "--kind", "requirement", "Stdlib")
      repo.run("catalog", "retire", "C02", "--reason", "Wrong")
      repo.run("catalog", "retire", "C03", "--reason", "Replaced")
      repo.run("catalog", "retire", "C04", "--reason", "Dropped")
      bare = repo.run("show", "S01")[1]
      self.assertNotIn("Cites:", bare)
      repo.run("catalog", "cite", "S01", "C01", "C02", "C03", "C04")
      text = repo.run("show", "S01")[1]
      self.assertIn(
        "Cites: C01 goal: Ship it, C02 assumption: One file (retired), "
        "C03 decision: JSON (retired), C04 requirement: Stdlib",
        text,
      )
      state = repo.state()
      roadmap = render.render_roadmap(
        state.index, state.config, state.template("roadmap.md"), state.template("row.md"),
      ).decode("utf-8")
      html = render.render_html(state.index, state.config).decode("utf-8")
      for body in (roadmap, html):
        self.assertIn("retired assumption C02", body)
        self.assertIn("retired decision C03", body)
        self.assertNotIn("retired requirement", body)
        self.assertNotIn("retired goal", body)

  def test_Verify_DanglingCite_IsAnError(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Ship")
      repo.run("catalog", "add", "--kind", "goal", "Ship it")
      repo.run("catalog", "cite", "S01", "C01")
      code, out, _ = repo.run("verify", "--json")
      self.assertEqual(code, 0, out)
      state = repo.state()
      state.index.require("S01").cites.append("C99")
      state.save_index()
      code, out, _ = repo.run("verify", "--json")
      self.assertEqual(code, 1)
      self.assertIn("cites unknown id C99", _messages(json.loads(out)))


if __name__ == "__main__":
  unittest.main()
