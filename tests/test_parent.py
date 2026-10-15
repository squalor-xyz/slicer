"""A sliver names the slice it was carved from, one level deep, without becoming a dependency."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import support
from slicer import graph, jsonio, mergeindex, model, ops, outline, render, templates, tui, verify
from slicer.errors import StateError


def _doc(**parents: str) -> dict:
  """An index-shaped document with s01..s03; `parents` maps an item id to its parent."""
  items = []
  for n in range(1, 4):
    item_id = f"s{n:02d}"
    fields = {"parent": parents[item_id]} if item_id in parents else {}
    items.append({
      "id": item_id, "title": f"item {n}", "short_title": f"item {n}", "status": "open",
      "has_slice": False, "depends_on": [], "fields": fields,
    })
  return {"version": model.SCHEMA_VERSION, "id_prefix": "s", "next_id": 4, "items": items}


class ParentTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    self.assertEqual(repo.run("init")[0], 0)
    self.assertEqual(repo.run("add", "Source", "--effort", "1")[0], 0)
    return repo

  def snapshot(self, repo: support.TempRepo) -> dict[str, bytes]:
    return {str(p.relative_to(repo.root)): p.read_bytes()
            for p in (repo.root / ".slicer").rglob("*")
            if p.is_file() and p.name != "lock"}

  def error_code(self, out: str) -> str:
    return json.loads(out)["error"]["code"]

  def test_Add_WithParent_StoresAndRoundTrips(self) -> None:
    with self.repo() as repo:
      code, out, err = repo.run("add", "Sliver", "--parent", "S01", "--effort", "1", "--json")
      self.assertEqual(code, 0, err)
      stored = json.loads(out)
      self.assertEqual(stored["fields"]["parent"], "S01")
      keys = list(stored["fields"])
      self.assertEqual(keys.index("parent"), keys.index("discovered_from") + 1)
      item = repo.state().index.require("S02")
      self.assertEqual(item.parent, "S01")
      self.assertEqual(
        jsonio.dumps(model.Item.from_dict(item.to_dict()).to_dict()),
        jsonio.dumps(item.to_dict()),
      )
      self.assertEqual(model.Item.from_dict(item.to_dict()).parent, "S01")
      self.assertEqual(json.loads(repo.read(".slicer/index.json"))["version"], 9)

  def test_Add_ParentAnyStatus_Accepted(self) -> None:
    for status in ("open", "done", "retired"):
      with self.subTest(status=status), self.repo() as repo:
        self.assertEqual(repo.run("set", "S01", "--status", status)[0], 0)
        code, _, err = repo.run("add", "Sliver", "--parent", "S01")
        self.assertEqual(code, 0, err)
        self.assertEqual(verify.offline(repo.state()).problems, [])

  def test_Add_UnknownParent_Refused(self) -> None:
    with self.repo() as repo:
      before = self.snapshot(repo)
      code, out, err = repo.run("add", "Sliver", "--parent", "S99", "--render", "--json")
      self.assertEqual(code, 2, err)
      self.assertEqual(self.error_code(out), "no_such_item")
      self.assertEqual(self.snapshot(repo), before)
      state = repo.state()
      next_id = state.index.next_id
      with self.assertRaises(StateError) as exc:
        ops.add(state, "Sliver", parent="S99")
      self.assertEqual(exc.exception.code, "no_such_item")
      self.assertEqual(state.index.next_id, next_id)

  def test_Add_ParentThatHasParent_Refused(self) -> None:
    with self.repo() as repo:
      self.assertEqual(repo.run("add", "Sliver", "--parent", "S01")[0], 0)
      before = self.snapshot(repo)
      code, out, err = repo.run("add", "Grandchild", "--parent", "S02", "--render", "--json")
      self.assertEqual(code, 2, err)
      self.assertEqual(self.error_code(out), "parent_nested")
      self.assertIn("S01 as its parent instead", json.loads(out)["error"]["message"])
      self.assertIn("set S02 --parent ''", json.loads(out)["error"]["message"])
      self.assertEqual(self.snapshot(repo), before)

  def test_Set_ParentThatHasParent_Refused(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Sliver", "--parent", "S01")
      repo.run("add", "Other")
      before = self.snapshot(repo)
      code, out, err = repo.run("set", "S03", "--parent", "S02", "--json")
      self.assertEqual(code, 2, err)
      self.assertEqual(self.error_code(out), "parent_nested")
      self.assertEqual(self.snapshot(repo), before)

  def test_Set_ItemWithSlivers_GivenParent_Refused(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Sliver", "--parent", "S01")
      repo.run("add", "Other")
      before = self.snapshot(repo)
      code, out, err = repo.run("set", "S01", "--parent", "S03", "--json")
      self.assertEqual(code, 2, err)
      self.assertEqual(self.error_code(out), "parent_nested")
      self.assertIn("S02", json.loads(out)["error"]["message"])
      self.assertIn("set S02 --parent ''", json.loads(out)["error"]["message"])
      self.assertEqual(self.snapshot(repo), before)

  def test_Set_SelfParent_Refused(self) -> None:
    with self.repo() as repo:
      before = self.snapshot(repo)
      code, out, err = repo.run("set", "S01", "--parent", "S01", "--json")
      self.assertEqual(code, 2, err)
      self.assertEqual(self.error_code(out), "parent_self")
      self.assertIn("set S01 --parent ''", json.loads(out)["error"]["message"])
      self.assertEqual(self.snapshot(repo), before)

  def test_Set_UnknownParent_Refused(self) -> None:
    with self.repo() as repo:
      before = self.snapshot(repo)
      code, out, err = repo.run("set", "S01", "--parent", "S99", "--json")
      self.assertEqual(code, 2, err)
      self.assertEqual(self.error_code(out), "no_such_item")
      self.assertEqual(self.snapshot(repo), before)

  def test_Set_BatchWithOneBadItem_WritesNothing(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Other")
      before = self.snapshot(repo)
      code, out, err = repo.run("set", "S02", "S01", "--parent", "S01", "--json")
      self.assertEqual(code, 2, err)
      self.assertEqual(self.error_code(out), "parent_self")
      self.assertEqual(self.snapshot(repo), before)

  def test_Set_ParentThenEmpty_SetsAndClears(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Other")
      code, out, err = repo.run("set", "S02", "--parent", "S01", "--json", "--lean")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().index.require("S02").parent, "S01")
      log = repo.run("log", "--json", "--lean")[1]
      self.assertIn("parent", log)
      code, _, err = repo.run("set", "S02", "--parent", "")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().index.require("S02").parent, "")
      # An item that lost its parent may become a parent, and be re-parented.
      self.assertEqual(repo.run("set", "S02", "--parent", "S01")[0], 0)

  def test_Check_UnknownParent_ReportsError(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Sliver", "--parent", "S01")
      state = repo.state()
      state.index.require("S02").parent = "S99"
      state.save_index()
      report = verify.offline(repo.state())
      self.assertIn("parent unknown id S99", [f.message for f in report.problems])
      code, out, _ = repo.run("verify", "--json")
      self.assertEqual(code, 1)
      self.assertIn("parent unknown id S99", out)

  def test_Check_NestedParent_ReportsError(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Sliver", "--parent", "S01")
      repo.run("add", "Grandchild")
      state = repo.state()
      state.index.require("S03").parent = "S02"
      state.save_index()
      problems = verify.offline(repo.state()).problems
      self.assertEqual([(f.item, f.message) for f in problems],
                       [("S03", "parent S02 has a parent (S01); a sliver cannot have slivers")])

  def test_Check_SelfParent_ReportsError(self) -> None:
    with self.repo() as repo:
      state = repo.state()
      state.index.require("S01").parent = "S01"
      state.save_index()
      problems = verify.offline(repo.state()).problems
      self.assertEqual([(f.item, f.message) for f in problems],
                       [("S01", "parent is itself")])

  def test_Check_RetiredParent_NotAnError(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Sliver", "--parent", "S01")
      self.assertEqual(repo.run("remove", "S01", "--reason", "Obsolete", "--force")[0], 0)
      self.assertEqual(verify.offline(repo.state()).problems, [])

  def test_MergeIndex_ParentDroppedOnOneSide_Reported(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      base, ours, theirs = _doc(), _doc(s02="s01"), _doc()
      del theirs["items"][0]
      for name, doc in (("base", base), ("ours", ours), ("theirs", theirs)):
        repo.write(name, jsonio.dumps(doc))
      code, out, err = repo.run(
        "merge-index", *(str(repo.root / name) for name in ("base", "ours", "theirs")))
      self.assertNotEqual(code, 0)
      self.assertIn("s02 was a sliver of unknown id s01", out + err)

  def test_MergeIndex_NestedAcrossSides_Reported(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      docs = {"base": _doc(), "ours": _doc(s02="s01"), "theirs": _doc(s01="s03")}
      for name, doc in docs.items():
        repo.write(name, jsonio.dumps(doc))
      code, out, err = repo.run(
        "merge-index", *(str(repo.root / name) for name in ("base", "ours", "theirs")))
      self.assertNotEqual(code, 0)
      self.assertIn("s02 is a sliver of s01, which has a parent", out + err)

  def test_MergeIndex_Schema8And9Sides_MergesCleanAtNine(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      base, ours, theirs = _doc(), _doc(s02="s01"), _doc()
      base["version"] = theirs["version"] = 8
      theirs["items"][2]["status"] = "done"
      for name, doc in (("base", base), ("ours", ours), ("theirs", theirs)):
        repo.write(name, jsonio.dumps(doc))
      code, out, err = repo.run(
        "merge-index", *(str(repo.root / name) for name in ("base", "ours", "theirs")))
      self.assertEqual((code, out, err), (0, "", ""))
      merged = json.loads(repo.read("ours"))
      self.assertEqual(merged["version"], 9)
      self.assertEqual([(i["id"], i["status"], i["fields"].get("parent", ""))
                        for i in merged["items"]],
                       [("s01", "open", ""), ("s02", "open", "s01"), ("s03", "done", "")])

  def test_MergeIndex_Problems_NameUnknownNestedAndSelfParent(self) -> None:
    found = [message for message, _ in mergeindex._problems(
      _doc(s01="s01", s02="s99", s03="s02"))]
    self.assertIn("s01 is its own parent", found)
    self.assertIn("s02 was a sliver of unknown id s99", found)
    self.assertIn("s03 is a sliver of s02, which has a parent", found)
    self.assertEqual([m for m, _ in mergeindex._problems(_doc(s02="s01", s03="s01"))], [])

  def test_Remove_ParentWithSlivers_RefusedWithoutForce(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Sliver", "--parent", "S01")
      repo.run("add", "Sliver two", "--parent", "S01")
      before = self.snapshot(repo)
      for args in (("--reason", "Obsolete"), ("--purge",)):
        code, out, err = repo.run("remove", "S01", *args, "--json")
        self.assertEqual(code, 2, err)
        self.assertIn("S02, S03 are slivers of it", json.loads(out)["error"]["message"])
        self.assertEqual(self.snapshot(repo), before)
      code, out, _ = repo.run("remove", "S01", "--purge", "--dry-run", "--json")
      self.assertEqual(code, 0)
      self.assertIn("S02, S03 are slivers of it", " ".join(json.loads(out)["blockers"]))
      code, _, err = repo.run("remove", "S01", "--reason", "Obsolete", "--force")
      self.assertEqual(code, 0, err)
      self.assertEqual(verify.offline(repo.state()).problems, [])

  def test_Remove_ParentWithOneSliver_NamesIt(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Sliver", "--parent", "S01")
      code, out, _ = repo.run("remove", "S01", "--purge", "--dry-run", "--json")
      self.assertEqual(code, 0)
      self.assertEqual(json.loads(out)["blockers"], ["S02 is a sliver of it"])

  def test_Remove_Sliver_NotBlockedByItsParent(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Sliver", "--parent", "S01")
      code, _, err = repo.run("remove", "S02", "--purge")
      self.assertEqual(code, 0, err)

  def test_Purge_ParentNamedBySliver_KeepsId(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Latest")
      state = repo.state()
      state.index.require("S01").parent = "S02"
      state.save_index()
      preview = ops.remove_preview(state, "S02", purge=True)
      self.assertFalse(preview.id_freed)
      self.assertIn("parent", preview.reason)
      result = ops.purge(state, "S02", force=True)
      self.assertFalse(result.id_freed)
      self.assertEqual(ops.add(state, "Replacement").id, "S03")

  def test_Show_Parent_ListsSlivers(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Sliver one", "--parent", "S01")
      repo.run("add", "Sliver two", "--parent", "S01")
      repo.run("set", "S03", "--status", "done")
      code, out, err = repo.run("show", "S01", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(
        json.loads(out)["slivers"],
        [{"id": "S02", "status": "open", "title": "Sliver one"},
         {"id": "S03", "status": "done", "title": "Sliver two"}],
      )
      code, out, _ = repo.run("show", "S01")
      self.assertIn("Slivers:\n  S02  open  Sliver one\n  S03  done  Sliver two", out)
      code, out, _ = repo.run("show", "S02", "--json")
      self.assertEqual(json.loads(out)["slivers"], [])
      self.assertEqual(json.loads(out)["fields"]["parent"], "S01")
      code, out, _ = repo.run("show", "S02")
      self.assertIn("Parent: S01 Source", out)
      self.assertNotIn("Slivers:", out)

  def test_Show_NoSlivers_LeanDropsTheKey(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Sliver", "--parent", "S01")
      code, out, err = repo.run("show", "S02", "--json", "--lean")
      self.assertEqual(code, 0, err)
      self.assertNotIn("slivers", json.loads(out))
      self.assertEqual(json.loads(out)["fields"]["parent"], "S01")
      self.assertIn("slivers", json.loads(repo.run("show", "S01", "--json", "--lean")[1]))

  def test_Show_ParentWithSlice_ListsSliversAfterSlice(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Sliver", "--parent", "S01")
      outline_file = repo.write("slice.md", "## slice\n\nlead\n\n### Why\n\nbecause\n")
      self.assertEqual(repo.run("promote", "S01", "--file", str(outline_file))[0], 0)
      code, out, _ = repo.run("show", "S01")
      self.assertIn("Slivers:\n  S02  open  Sliver", out)
      self.assertIn("because", out)
      self.assertEqual(json.loads(repo.run("show", "S01", "--json")[1])["slivers"][0]["id"], "S02")

  def test_List_ParentFilter_ReturnsSlivers(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Other parent")
      repo.run("add", "Found", "--parent", "S01", "--tree", "core")
      repo.run("add", "Other tree", "--parent", "S01", "--tree", "cli")
      repo.run("add", "Other status", "--parent", "S01", "--tree", "core", "--status", "done")
      repo.run("add", "Other sliver", "--parent", "S02", "--tree", "core")
      code, out, err = repo.run("list", "--parent", "S01", "--tree", "core",
                                "--status", "open", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual([i["id"] for i in json.loads(out)], ["S03"])
      code, out, _ = repo.run("list", "--parent", "S01", "--json")
      self.assertEqual([i["id"] for i in json.loads(out)], ["S03", "S04"])
      code, out, _ = repo.run("list", "--parent", "S03", "--json")
      self.assertEqual((code, json.loads(out)), (0, []))
      for parent in ("S99", "S0", "s01", ""):
        code, out, _ = repo.run("list", "--parent", parent, "--json")
        self.assertEqual(code, 2)
        self.assertEqual(self.error_code(out), "no_such_item")

  def test_Import_ParentKey_FilesSliversInOneBatch(self) -> None:
    with self.repo() as repo:
      text = ("## Big slice\neffort: 3\n\n"
              "## Sliver by title\nparent: Big slice\neffort: 1\n\n"
              "## Sliver by id\nparent: S01\neffort: 1\n")
      parsed = outline.parse(text)
      self.assertEqual([s.to_dict()["parent"] for s in parsed.items],
                       ["", "Big slice", "S01"])
      repo.write("outline.md", text)
      before = self.snapshot(repo)
      code, out, err = repo.run("import", "outline.md", "--dry-run", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out)["depends_edges"], 0)
      self.assertEqual(self.snapshot(repo), before)
      code, _, err = repo.run("import", "outline.md", "--render")
      self.assertEqual(code, 0, err)
      items = repo.state().index
      self.assertEqual([(i.id, i.parent) for i in items.items],
                       [("S01", ""), ("S02", ""), ("S03", "S02"), ("S04", "S01")])
      self.assertEqual([i.depends_on for i in items.items], [[], [], [], []])
      self.assertEqual(verify.offline(repo.state()).problems, [])
      self.assertIn("sliver of S02", repo.read(".slicer/render/ROADMAP.md"))

  def test_Import_ParentFiledLaterInFile_Resolves(self) -> None:
    with self.repo() as repo:
      repo.write("outline.md", "## Sliver\nparent: Big slice\neffort: 1\n\n"
                 "## Big slice\neffort: 3\n")
      code, _, err = repo.run("import", "outline.md")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().index.require("S02").parent, "S03")

  def test_Import_BadParent_RefusesWholeBatch(self) -> None:
    neither = "nor an existing item ID"
    nested = "is itself a sliver of"
    cases = {
      "unknown id": ("## Found\nparent: S99\neffort: 1\n", neither),
      "unknown title": ("## Found\nparent: Nothing like it\neffort: 1\n", neither),
      "list": ("## Found\nparent: S01, S01\neffort: 1\n", neither),
      "self": ("## Found\nparent: Found\neffort: 1\n", "cannot be the entry itself"),
      "existing sliver": ("## Found\nparent: S02\neffort: 1\n", nested),
      "keyed existing sliver": (
        "## Keyed sliver\nkey: k2\n\n## Found\nparent: Keyed sliver\neffort: 1\n", nested),
      "nested in file": ("## Top\neffort: 1\n\n## Mid\nparent: Top\neffort: 1\n\n"
                         "## Low\nparent: Mid\neffort: 1\n", nested),
      "parent of a parent": ("## Top\nparent: S01\neffort: 1\n\n"
                             "## Low\nparent: Top\neffort: 1\n", nested),
    }
    for name, (text, fragment) in cases.items():
      with self.subTest(case=name), self.repo() as repo:
        repo.run("add", "Sliver", "--parent", "S01")
        repo.run("add", "Keyed sliver", "--key", "k2", "--parent", "S01")
        repo.write("outline.md", text)
        before = self.snapshot(repo)
        for dry in ([], ["--dry-run"]):
          code, out, err = repo.run("import", "outline.md", *dry, "--render", "--json")
          self.assertEqual(code, 1, err)
          problems = json.loads(out)["problems"]
          self.assertEqual(len(problems), 1, problems)
          self.assertIn(fragment, problems[0])
          self.assertEqual(self.snapshot(repo), before)

  def test_Import_ParentNamedLikeAnId_TitleWins(self) -> None:
    with self.repo() as repo:
      repo.write("outline.md", "## S01\neffort: 1\n\n## Found\nparent: S01\neffort: 1\n")
      code, _, err = repo.run("import", "outline.md")
      self.assertEqual(code, 0, err)
      self.assertEqual([(i.id, i.parent) for i in repo.state().index.items],
                       [("S01", ""), ("S02", ""), ("S03", "S02")])

  def test_Import_ParentTitledLikeASliverId_TitleWins(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Sliver", "--parent", "S01")
      repo.write("outline.md", "## S02\neffort: 1\n\n## Found\nparent: S02\neffort: 1\n")
      code, _, err = repo.run("import", "outline.md")
      self.assertEqual(code, 0, err)
      self.assertEqual([(i.id, i.parent) for i in repo.state().index.items][2:],
                       [("S03", ""), ("S04", "S03")])

  def test_Import_ParentIsExistingItemTitle_Resolves(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Long existing title", "--short-title", "Short")
      repo.write("outline.md", "## By title\nparent: Source\neffort: 1\n\n"
                 "## By short title\nparent: Short\neffort: 1\n\n"
                 "## By id\nparent: S02\neffort: 1\n")
      code, _, err = repo.run("import", "outline.md")
      self.assertEqual(code, 0, err)
      self.assertEqual([(i.id, i.parent) for i in repo.state().index.items][2:],
                       [("S03", "S01"), ("S04", "S02"), ("S05", "S02")])

  def test_Import_ReusedKeyedParent_ResolvesToExistingItem(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Keyed", "--key", "k1")
      repo.write("outline.md", "## Keyed\nkey: k1\n\n## Found\nparent: Keyed\neffort: 1\n")
      code, _, err = repo.run("import", "outline.md")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().index.require("S03").parent, "S02")

  def test_Render_Sliver_NamesParent(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Sliver", "--parent", "S01")
      outline_file = repo.write("slice.md", "## slice\n\nlead\n\n### Why\n\nbecause\n")
      self.assertEqual(repo.run("promote", "S02", "--file", str(outline_file), "--render")[0], 0)
      self.assertIn("sliver of S01", repo.read(".slicer/render/ROADMAP.md"))
      self.assertIn("sliver of S01", repo.read(".slicer/render/ROADMAP.html"))
      slice_md = repo.read(".slicer/render/slices/S02.md")
      self.assertIn("Parent: S01 Source", slice_md)
      self.assertEqual(repo.run("check")[0], 0)
      state = repo.state()
      item = state.index.require("S02")
      custom = render.render_row(item, 1, state.config, "{{id}}: {{parent}}")
      self.assertEqual(custom, "S02: S01")

  def test_Panel_Sliver_ShowsParent(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Sliver", "--parent", "S01")
      state = repo.state()
      self.assertIn("parent          S01", [l.text for l in tui.panel(state, "S02")])
      self.assertIn("parent          -", [l.text for l in tui.panel(state, "S01")])

  def test_Render_NoParent_SliceAndRowUnchanged(self) -> None:
    with self.repo() as repo:
      state = repo.state()
      item = state.index.require("S01")
      row = render.render_row(item, 1, state.config, templates.default("row.md"))
      self.assertEqual(row, "| 1 | S01 | Source |  | 1 |  |  | — |")
      self.assertEqual(render.parent_header(state.index, item), "")
      item.parent = "S99"
      self.assertEqual(render.parent_header(state.index, item), "Parent: S99")

  def test_Model_HistoricalField_DefaultsWithoutWritesAndUpgrades(self) -> None:
    with self.repo() as repo:
      path = repo.root / ".slicer/index.json"
      old = json.loads(path.read_text())
      old["version"] = 8
      for item in old["items"]:
        del item["fields"]["parent"]
      path.write_text(json.dumps(old))
      before = self.snapshot(repo)
      code, out, err = repo.run("show", "S01", "--json", "--lean")
      self.assertEqual(code, 0, err)
      self.assertNotIn("parent", json.loads(out)["fields"])
      self.assertEqual(repo.state().index.require("S01").parent, "")
      self.assertEqual(self.snapshot(repo), before)
      repo.run("add", "Sliver", "--parent", "S01")
      self.assertEqual(json.loads(path.read_text())["version"], 9)
      old["items"][0]["fields"]["parent"] = ["S01"]
      with self.assertRaises(StateError) as exc:
        model.Item.from_dict(old["items"][0])
      self.assertEqual(exc.exception.code, "corrupt")

  def test_Load_SchemaTooNew_Refused(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Sliver", "--parent", "S01")
      path = repo.root / ".slicer/index.json"
      self.assertEqual(json.loads(path.read_text())["version"], 9)
      with patch.object(model, "SCHEMA_VERSION", 8), self.assertRaises(StateError) as exc:
        model.Index.from_dict(json.loads(path.read_text()))
      self.assertEqual(exc.exception.code, "schema_too_new")
      data = json.loads(path.read_text())
      data["version"] = 10
      path.write_text(json.dumps(data))
      code, out, _ = repo.run("list", "--json")
      self.assertEqual(code, 3)
      self.assertEqual(self.error_code(out), "schema_too_new")

  def test_Pickup_ParentOpen_DoesNotBlockOrPropagateScore(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Sliver", "--parent", "S01", "--importance", "3")
      state = repo.state()
      self.assertEqual(state.index.require("S02").depends_on, [])
      self.assertEqual(graph.effective_scores(state.index), {"S01": 22, "S02": 32})
      code, out, err = repo.run("next", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out)["id"], "S02")


class SliverHoldTests(unittest.TestCase):
  """Open slivers hold their parent: not ready in `next`, and its finish refuses (s274)."""

  def repo(
    self, promote: bool = False, finish: str = "handoff", **parent_fields: str,
  ) -> support.TempRepo:
    """S01 is the parent; S02 and S03 are its slivers. Promoted only when a finish is tested."""
    repo = support.TempRepo()
    self.assertEqual(repo.run("init")[0], 0)
    self.assertEqual(repo.run("config", "implement_finish", finish)[0], 0)
    self.assertEqual(repo.run("add", "Parent", "--effort", "1", *_flags(parent_fields))[0], 0)
    for title in ("Sliver one", "Sliver two"):
      self.assertEqual(repo.run("add", title, "--parent", "S01", "--effort", "1")[0], 0)
    if promote:
      for item_id in ("S01", "S02", "S03"):
        self.assertEqual(repo.run("promote", item_id)[0], 0)
        for heading in ("Implement", "Check"):
          self.assertEqual(
            repo.run("edit", item_id, "--section", heading, "--text", "Do it.")[0], 0)
    return repo

  def marks(self, repo: support.TempRepo) -> dict[str, str]:
    """The readiness column of `list` text: `!` or a space, by item id."""
    rows = [line for line in repo.run("list")[1].splitlines() if line[:3].strip().isdigit()]
    return {line[5:].split()[0]: line[3] for line in rows}

  def status(self, repo: support.TempRepo, item_id: str) -> str:
    return repo.state().index.require(item_id).status

  def snapshot(self, repo: support.TempRepo) -> dict[str, bytes]:
    return {str(p.relative_to(repo.root)): p.read_bytes()
            for p in (repo.root / ".slicer").rglob("*")
            if p.is_file() and p.name != "lock"}

  def test_Next_ParentWithOpenSliver_BlockedAndNamesSliver(self) -> None:
    with self.repo() as repo:
      code, out, err = repo.run("next", "--ready", "--json", "--lean")
      self.assertEqual(code, 0, err)
      payload = json.loads(out)
      self.assertEqual(payload["item"]["id"], "S02")
      self.assertEqual(payload["blocked"], [{"id": "S01", "waiting_on": ["S02", "S03"]}])
      self.assertEqual(repo.state().index.require("S01").depends_on, [])

  def test_Next_ImplementFinishDone_SliverInReview_ParentStillHeld(self) -> None:
    with self.repo(promote=True, finish="done") as repo:
      self.assertEqual(repo.run("start", "S01", "S02", "S03")[0], 0)
      self.assertEqual(repo.run("handoff", "S02", "S03")[0], 0)
      code, out, _ = repo.run("next", "--ready", "--json", "--lean")
      self.assertEqual(code, 2)  # nothing offered: the parent waits for done, not review
      self.assertEqual(json.loads(out)["blocked"], [{"id": "S01", "waiting_on": ["S02", "S03"]}])
      self.assertEqual(repo.run("done", "S02", "S03")[0], 0)
      payload = json.loads(repo.run("next", "--ready", "--json", "--lean")[1])
      self.assertEqual(payload["item"]["id"], "S01")

  def test_Next_ParentWithDependencyAndSlivers_NamesDependencyFirst(self) -> None:
    with self.repo() as repo:
      self.assertEqual(repo.run("add", "Gate", "--effort", "1")[0], 0)
      self.assertEqual(repo.run("set", "S01", "--depends-on", "S04")[0], 0)
      payload = json.loads(repo.run("next", "--ready", "--json", "--lean")[1])
      self.assertEqual(payload["blocked"], [{"id": "S01", "waiting_on": ["S04", "S02", "S03"]}])
      self.assertEqual(repo.state().index.require("S01").depends_on, ["S04"])

  def test_Next_SliverStarted_ParentStillBlockedUntilHandedOff(self) -> None:
    with self.repo(promote=True) as repo:
      self.assertEqual(repo.run("start", "S01", "S02", "S03")[0], 0)
      code, out, _ = repo.run("next", "--json")
      self.assertEqual(json.loads(out)["id"], "S02")
      self.assertEqual(repo.run("handoff", "S02", "S03")[0], 0)
      code, out, err = repo.run("next", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out)["id"], "S01")

  def test_NextBatch_ParentAfterSlivers_Ordered(self) -> None:
    with self.repo(importance="3", urgency="3") as repo:
      code, out, err = repo.run("next", "--batch", "3", "--json")
      self.assertEqual(code, 0, err)
      payload = json.loads(out)
      self.assertEqual([i["id"] for i in payload["items"]], ["S02", "S03", "S01"])
      self.assertEqual(payload["blocked"], [])
      code, out, _ = repo.run("next", "--batch", "2", "--json")
      payload = json.loads(out)
      self.assertEqual([i["id"] for i in payload["items"]], ["S02", "S03"])

  def test_NextBatch_SliverOutsidePool_ParentStillHeld(self) -> None:
    with self.repo() as repo:
      self.assertEqual(repo.run("promote", "S03")[0], 0)  # empty slice: S03 is unspecified, not offered
      code, out, err = repo.run("next", "--batch", "3", "--json")
      self.assertEqual(code, 0, err)
      payload = json.loads(out)
      self.assertEqual([i["id"] for i in payload["items"]], ["S02"])
      self.assertEqual(payload["blocked"], [{"id": "S01", "waiting_on": ["S03"]}])

  def test_NextBatch_ParentLeftOutOfBatch_ReportsSliversBlocked(self) -> None:
    with self.repo(importance="3", urgency="3") as repo:
      code, out, _ = repo.run("next", "--batch", "1", "--json")
      payload = json.loads(out)
      self.assertEqual([i["id"] for i in payload["items"]], ["S02"])
      self.assertEqual(payload["blocked"], [{"id": "S01", "waiting_on": ["S03"]}])

  def test_Next_ItemWithoutSlivers_Unchanged(self) -> None:
    with self.repo() as repo:
      self.assertEqual(repo.run("set", "S02", "S03", "--parent", "")[0], 0)
      code, out, err = repo.run("next", "--ready", "--json", "--lean")
      self.assertEqual(code, 0, err)
      payload = json.loads(out)
      self.assertEqual(payload["item"]["id"], "S01")
      self.assertNotIn("blocked", payload)  # --lean drops an empty list
      self.assertEqual(
        graph.blocked_by(repo.state().index, repo.state().index.require("S01"), "done"), [])

  def test_Next_ReviewQueue_ParentWithSliversInReview_Offered(self) -> None:
    with self.repo(promote=True) as repo:
      self.assertEqual(repo.run("start", "S01", "S02", "S03")[0], 0)
      self.assertEqual(repo.run("handoff", "S01", "S02", "S03")[0], 0)
      code, out, err = repo.run("next", "--status", "review", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out)["id"], "S01")

  def test_List_ParentWithOpenSliver_MarkedBlocked(self) -> None:
    with self.repo() as repo:
      marks = self.marks(repo)
      self.assertEqual(marks, {"S01": "!", "S02": " ", "S03": " "})
      self.assertEqual(list(marks), ["S02", "S03", "S01"])  # held parent ranks after its slivers
      self.assertEqual(repo.run("set", "S02", "S03", "--status", "done")[0], 0)
      self.assertEqual(self.marks(repo)["S01"], " ")

  def test_Tui_ParentWithOpenSliver_RowMarkedBlocked(self) -> None:
    with self.repo() as repo:
      state = repo.state()
      rows = {r.target: r for r in tui.item_rows(state, list(state.index.items))}
      self.assertIn("!", rows["S01"].text.split()[0])
      self.assertNotIn("!", rows["S02"].text.split()[0])
      self.assertEqual(repo.run("set", "S02", "S03", "--status", "done")[0], 0)
      state = repo.state()
      rows = {r.target: r for r in tui.item_rows(state, list(state.index.items))}
      self.assertNotIn("!", rows["S01"].text.split()[0])

  def test_Deps_ParentWithOpenSliver_NamesSliver(self) -> None:
    with self.repo() as repo:
      payload = json.loads(repo.run("deps", "S01", "--json")[1])
      self.assertEqual(payload["waits_on"], [])
      self.assertEqual(payload["blocked_by"], ["S02", "S03"])
      text = repo.run("deps", "S01")[1]
      self.assertIn("held by slivers  S02, S03", text)
      self.assertEqual([i["id"] for i in json.loads(repo.run("deps", "--json")[1])], ["S02", "S03"])

  def test_Handoff_ParentWithOpenSliver_RefusedWithCode(self) -> None:
    with self.repo(promote=True) as repo:
      self.assertEqual(repo.run("start", "S01", "S02")[0], 0)
      before = self.snapshot(repo)
      code, out, _ = repo.run("handoff", "S01", "--json")
      self.assertEqual(code, 2)
      error = json.loads(out)["error"]
      self.assertEqual(error["code"], "slivers_open")
      self.assertIn("S02 (started)", error["message"])
      self.assertIn("S03 (open)", error["message"])
      self.assertEqual(self.snapshot(repo), before)
      with self.assertRaises(StateError) as exc:
        ops.handoff(repo.state(), "S01")
      self.assertEqual(exc.exception.code, "slivers_open")

  def test_Handoff_ParentWithOneSliverOpen_NamesOnlyThatSliver(self) -> None:
    with self.repo(promote=True) as repo:
      self.assertEqual(repo.run("start", "S01", "S02")[0], 0)
      self.assertEqual(repo.run("handoff", "S02")[0], 0)
      code, out, _ = repo.run("handoff", "S01", "--json")
      self.assertEqual(code, 2)
      message = json.loads(out)["error"]["message"]
      self.assertIn("S03 (open)", message)
      self.assertNotIn("S02", message)

  def test_Handoff_ParentAllSliversHandedOff_Succeeds(self) -> None:
    with self.repo(promote=True) as repo:
      self.assertEqual(repo.run("start", "S01", "S02", "S03")[0], 0)
      self.assertEqual(repo.run("handoff", "S02")[0], 0)
      self.assertEqual(repo.run("set", "S03", "--status", "done")[0], 0)
      code, _, err = repo.run("handoff", "S01")
      self.assertEqual(code, 0, err)
      self.assertEqual(self.status(repo, "S01"), "review")

  def test_Handoff_ParentAndSliversInOneBatch_Succeeds(self) -> None:
    with self.repo(promote=True) as repo:
      self.assertEqual(repo.run("start", "S01", "S02", "S03")[0], 0)
      code, _, err = repo.run("handoff", "S01", "S02", "S03")
      self.assertEqual(code, 0, err)
      self.assertEqual([self.status(repo, i) for i in ("S01", "S02", "S03")], ["review"] * 3)

  def test_Handoff_ImplementFinishDone_SliverInReview_ParentHandedOff(self) -> None:
    with self.repo(promote=True, finish="done") as repo:
      self.assertEqual(repo.run("start", "S01", "S02", "S03")[0], 0)
      self.assertEqual(repo.run("handoff", "S02", "S03")[0], 0)
      code, _, err = repo.run("handoff", "S01")
      self.assertEqual(code, 0, err)
      self.assertEqual(self.status(repo, "S01"), "review")

  def test_Handoff_ParentWithRetiredSliver_NotHeld(self) -> None:
    with self.repo(promote=True) as repo:
      self.assertEqual(repo.run("start", "S01", "S02")[0], 0)
      self.assertEqual(repo.run("remove", "S03", "--reason", "dropped")[0], 0)
      self.assertEqual(repo.run("handoff", "S02")[0], 0)
      self.assertEqual(repo.run("handoff", "S01")[0], 0)

  def test_Handoff_SliverWithParentOpen_Unchanged(self) -> None:
    with self.repo(promote=True) as repo:
      self.assertEqual(repo.run("start", "S02")[0], 0)
      code, _, err = repo.run("handoff", "S02")
      self.assertEqual(code, 0, err)
      self.assertEqual(self.status(repo, "S02"), "review")

  def test_Done_ParentWithSliverInReview_RefusedWithCode(self) -> None:
    with self.repo(promote=True) as repo:
      self.assertEqual(repo.run("start", "S02", "S03")[0], 0)
      self.assertEqual(repo.run("handoff", "S02", "S03")[0], 0)
      self.assertEqual(repo.run("done", "S02")[0], 0)
      before = self.snapshot(repo)
      code, out, _ = repo.run("done", "S01", "--json")
      self.assertEqual(code, 2)
      error = json.loads(out)["error"]
      self.assertEqual(error["code"], "slivers_open")
      self.assertIn("S03 (review)", error["message"])
      self.assertNotIn("S02", error["message"])
      self.assertEqual(self.snapshot(repo), before)
      with self.assertRaises(StateError) as exc:
        ops.done(repo.state(), "S01")
      self.assertEqual(exc.exception.code, "slivers_open")

  def test_Done_RenderedParentWithOpenSliver_RefusedBeforeWriting(self) -> None:
    with self.repo(promote=True) as repo:
      before = self.snapshot(repo)
      code, out, _ = repo.run("done", "S01", "--render", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out)["error"]["code"], "slivers_open")
      self.assertEqual(self.snapshot(repo), before)

  def test_Done_ParentAllSliversDone_Succeeds(self) -> None:
    with self.repo(promote=True) as repo:
      self.assertEqual(repo.run("done", "S02", "S03")[0], 0)
      code, _, err = repo.run("done", "S01")
      self.assertEqual(code, 0, err)
      self.assertEqual(self.status(repo, "S01"), "done")

  def test_Done_ParentAndSliversInOneBatch_Succeeds(self) -> None:
    with self.repo(promote=True) as repo:
      code, _, err = repo.run("done", "S01", "S02", "S03")
      self.assertEqual(code, 0, err)
      self.assertEqual([self.status(repo, i) for i in ("S01", "S02", "S03")], ["done"] * 3)

  def test_Done_BatchWithHeldParent_WritesNothing(self) -> None:
    with self.repo(promote=True) as repo:
      before = self.snapshot(repo)
      code, out, _ = repo.run("done", "S01", "S02", "--json")
      self.assertEqual(code, 2)
      self.assertIn("S03 (open)", json.loads(out)["error"]["message"])
      self.assertEqual(self.snapshot(repo), before)

  def test_Done_ParentWithRetiredSliver_NotHeld(self) -> None:
    with self.repo(promote=True) as repo:
      self.assertEqual(repo.run("remove", "S03", "--reason", "dropped")[0], 0)
      self.assertEqual(repo.run("done", "S02")[0], 0)
      self.assertEqual(repo.run("done", "S01")[0], 0)

  def test_Done_SliverWithParentOpen_Unchanged(self) -> None:
    with self.repo(promote=True) as repo:
      code, _, err = repo.run("done", "S02")
      self.assertEqual(code, 0, err)
      self.assertEqual(self.status(repo, "S02"), "done")


def _flags(fields: dict[str, str]) -> list[str]:
  return [part for key, value in fields.items() for part in (f"--{key}", value)]


if __name__ == "__main__":
  unittest.main()
