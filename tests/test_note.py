"""`slicer note` appends a dated note to the item; it needs no slice."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

import support
from slicer import model, ops, render, tui
from slicer.config import Config
from slicer.errors import ConfigError


def _today() -> str:
  return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class NoteTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    support.make_mini(repo)
    repo.run("init")
    repo.run("migrate", "--from", "docs/slices")
    return repo

  def _notes(self, repo: support.TempRepo, item_id: str) -> list[str]:
    return repo.state().index.require(item_id).notes

  def test_Note_Text_AppendsADatedParagraphVisibleEverywhere(self) -> None:
    with self.repo() as repo:
      before = len(self._notes(repo, "S02"))
      code, _, err = repo.run("note", "S02", "--text", "tried X, it did not work", "--render")
      self.assertEqual(code, 0, err)
      notes = self._notes(repo, "S02")
      self.assertEqual(len(notes), before + 1)
      self.assertEqual(notes[-1], f"**{_today()}** — tried X, it did not work")
      self.assertIn("tried X, it did not work", repo.run("show", "S02")[1])
      rendered = repo.read(".slicer/render/slices/S02.md")
      self.assertIn("tried X, it did not work", rendered)
      self.assertIn(f"**{_today()}**", rendered)

  def test_Note_CalledTwice_AccumulatesInOrder(self) -> None:
    with self.repo() as repo:
      before = len(self._notes(repo, "S02"))
      repo.run("note", "S02", "--text", "first")
      repo.run("note", "S02", "--text", "second")
      notes = self._notes(repo, "S02")
      self.assertEqual(len(notes), before + 2)
      self.assertTrue(notes[-2].endswith("first"))
      self.assertTrue(notes[-1].endswith("second"))

  def test_Note_FromAFile_AppendsTheFileBody(self) -> None:
    with self.repo() as repo:
      repo.write("n.md", "a note from a file\n")
      code, _, err = repo.run("note", "S02", "--file", str(repo.root / "n.md"))
      self.assertEqual(code, 0, err)
      self.assertTrue(self._notes(repo, "S02")[-1].endswith("a note from a file"))

  def test_Note_OnItemWithoutASlice_IsAllowedAndShows(self) -> None:
    with self.repo() as repo:
      repo.run("add", "no slice yet")  # S05, a bare row
      code, _, err = repo.run("note", "S05", "--text", "context before promoting")
      self.assertEqual(code, 0, err)
      self.assertTrue(self._notes(repo, "S05")[-1].endswith("context before promoting"))
      self.assertIn("context before promoting", repo.run("show", "S05")[1])

  def test_Note_Blank_IsRefused(self) -> None:
    with self.repo() as repo:
      code, out, _ = repo.run("note", "S02", "--text", "   ", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out)["error"]["code"], "usage")

  def test_Note_UnknownItem_IsRefused(self) -> None:
    with self.repo() as repo:
      code, out, _ = repo.run("note", "S99", "--text", "x", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out)["error"]["code"], "no_such_item")

  def test_Note_WithRender_KeepsCheckGreen(self) -> None:
    with self.repo() as repo:
      repo.run("note", "S02", "--text", "keeps check green", "--render")
      self.assertEqual(repo.run("check")[0], 0)


class StructuredNoteTests(unittest.TestCase):
  def repo(self):
    repo = support.TempRepo()
    repo.run("init")
    repo.run("add", "work")
    repo.run("promote", "S01")
    for heading in ("Implement", "Check"):
      repo.run("edit", "S01", "--section", heading, "--text", "Specified.")
    return repo

  def payload(self, repo, *args):
    code, out, err = repo.run(*args, "--json")
    self.assertEqual(code, 0, err)
    return json.loads(out)

  def test_Records_PublicEnvelopes_KeepDisplayStrings(self):
    with self.repo() as repo:
      repo.run("start", "S01")
      repo.run("note", "S01", "--text", "plain")
      repo.run("note", "S01", "--kind", "report", "--text", "verified", "--render")
      shown = self.payload(repo, "show", "S01")
      self.assertEqual(shown["notes"], [f"**{_today()}** — plain", f"**{_today()}** — [report] verified"])
      records = shown["note_records"]
      self.assertEqual(list(records[1]), ["id", "kind", "text", "created_at", "attempt"])
      self.assertEqual(records[1]["attempt"], 1)
      self.assertEqual(records[1]["text"], f"**{_today()}** — verified")
      self.assertTrue(records[1]["created_at"].endswith("Z"))
      from uuid import UUID
      UUID(records[1]["id"])
      envelopes = [self.payload(repo, "list")[0], self.payload(repo, "find", "work")[0],
        self.payload(repo, "set", "S01", "--size", "M", "--render"),
        self.payload(repo, "next", "--ready")["item"]]
      for envelope in envelopes:
        self.assertEqual(envelope["notes"], shown["notes"])
        self.assertEqual(envelope["note_records"], records)
      self.assertEqual(repo.run("check")[0], 0)

  def test_Filters_UnionAndEmpty_SelectCopiesInOrder(self):
    with self.repo() as repo:
      for kind in ("report", "decision", "report"):
        repo.run("note", "S01", "--kind", kind, "--text", kind)
      repo.run("note", "S01", "--text", "plain")
      state = repo.state()
      state.slices["S01"].notes = ["legacy bytes"]
      state.save_slice(state.slices["S01"])
      before = repo.read(".slicer/index.json")
      for command in (("show", "S01"), ("next", "--ready"), ("next", "--ready", "--batch", "2")):
        for section in ((), ("--section", "Implement"),
                        ("--section", "Implement", "--section", "Check")):
          data = self.payload(repo, *command, *section, "--notes-kind", "report", "--notes-kind", "decision", "--lean")
          if "items" in data:
            data = data["items"][0]
          item = data.get("item", data)
          self.assertEqual([n["kind"] for n in item["note_records"]], ["report", "decision", "report"])
          empty = self.payload(repo, *command, *section, "--notes-kind", "absent")
          if "items" in empty:
            empty = empty["items"][0]
          self.assertEqual(empty.get("item", empty)["notes"], [])
      self.assertEqual(self.payload(repo, "show", "S01", "--notes-kind", "")["slice"]["notes"], ["legacy bytes"])
      self.assertEqual(self.payload(repo, "show", "S01", "--notes-kind", "report")["slice"]["notes"], [])
      self.assertEqual(repo.read(".slicer/index.json"), before)
      context = self.payload(repo, "show", "S01", "--section", "Implement", "--context",
        "--notes-kind", "decision", "--lean")
      self.assertEqual([n["kind"] for n in context["note_records"]], ["decision"])
      self.assertEqual(context["sections"], [{"heading": "Implement", "body": "Specified."}])
      self.assertEqual(repo.run("next", "--notes-kind", "report")[0], 2)

  def test_Legacy_ReadOnlyLift_EditDeletePreserveIdentity(self):
    with self.repo() as repo:
      path = repo.root / ".slicer/index.json"
      data = json.loads(path.read_text())
      data["version"] = 3
      item = data["items"][0]
      item.pop("note_records")
      item["notes"] = ["odd original bytes\n", "second", "third"]
      path.write_text(json.dumps(data))
      before = path.read_bytes()
      first = self.payload(repo, "show", "S01")
      self.assertEqual(first["notes"], item["notes"])
      self.assertEqual(first["note_records"], self.payload(repo, "show", "S01")["note_records"])
      self.assertEqual(path.read_bytes(), before)
      state = repo.state()
      ops.set_note(state, "S01", 1, "edited")
      ops.remove_note(state, "S01", 0)
      saved = json.loads(path.read_text())
      self.assertEqual(saved["version"], model.SCHEMA_VERSION)
      self.assertNotIn("notes", saved["items"][0])
      records = self.payload(repo, "show", "S01")["note_records"]
      self.assertEqual([n["id"] for n in records], [n["id"] for n in first["note_records"]][1:])
      self.assertEqual(records[0]["text"], "edited")
      self.assertEqual(records[1]["created_at"], "")
      self.assertIsNone(records[1]["attempt"])

  def test_Legacy_LiftingAndSave_PreserveRenderBytes(self):
    with self.repo() as repo:
      state = repo.state()
      sl = state.slices["S01"]
      sl.notes = ["legacy slice bytes\n"]
      state.save_slice(sl)
      original = ["**2000-01-01** — historical\n", "undated text — untouched"]
      path = repo.root / ".slicer/index.json"
      data = json.loads(path.read_text())
      data["version"] = 3
      data["items"][0].pop("note_records")
      data["items"][0]["notes"] = original
      path.write_text(json.dumps(data))
      expected = render.render_slice(sl, state.config, state.template("slice.md"), original)
      state = repo.state()
      self.assertEqual(render.render_slice(sl, state.config, state.template("slice.md"),
        state.index.require("S01").notes), expected)
      state.save_index()
      state = repo.state()
      self.assertEqual(render.render_slice(sl, state.config, state.template("slice.md"),
        state.index.require("S01").notes), expected)

  def test_Tui_TypedEditAndDelete_PreserveMetadataAndLabels(self):
    with self.repo() as repo:
      repo.run("note", "S01", "--kind", "decision", "--text", "original")
      state = repo.state()
      record = state.index.require("S01").note_records[0]
      before = record.to_dict()
      entry = next(e for e in tui.entries(state, "S01") if e.kind == "note")
      self.assertEqual(entry.body, record.text)
      self.assertIn("[decision] original", "\n".join(line.text for line in tui.panel(state, "S01")))
      request = tui.EditRequest(kind="note", target="S01", name=entry.name,
        body=entry.body, index=entry.index)
      result = tui.apply_edit_result(state, request, record.text.replace("original", "edited"))
      self.assertEqual(result.severity, "success")
      saved = repo.state().index.require("S01").note_records[0].to_dict()
      self.assertEqual({k: v for k, v in saved.items() if k != "text"},
        {k: v for k, v in before.items() if k != "text"})
      self.assertEqual(repo.state().index.require("S01").notes[0].count("[decision]"), 1)
      request.body = saved["text"]
      result = tui.apply_edit_result(state, request, "")
      self.assertEqual(result.severity, "success")
      self.assertEqual(repo.state().index.require("S01").note_records, [])
      added = tui.apply_edit_result(state, tui.EditRequest(kind="note_new", target="S01",
        name="add a note", body=""), "from TUI")
      self.assertEqual(added.severity, "success")
      self.assertEqual(repo.state().index.require("S01").note_records[0].kind, "")

  def test_Kinds_InvalidConfigAndWrites_LeaveFilesUntouched(self):
    for invalid in (None, "report", [""], [1], [" "]):
      with self.assertRaises(ConfigError):
        Config.from_dict({"note_kinds": invalid})
    self.assertEqual(Config.from_dict({}).note_kinds, [])
    self.assertEqual(Config.from_dict({"version": 1, "note_kinds": ["report"]}).to_dict()["version"], 3)
    with self.repo() as repo:
      config = json.loads(repo.read(".slicer/config.json"))
      config["note_kinds"] = ["report"]
      repo.write(".slicer/config.json", json.dumps(config))
      before = {p: p.read_bytes() for p in (repo.root / ".slicer").rglob("*") if p.is_file()}
      for kind in ("", " ", "decision"):
        self.assertEqual(repo.run("note", "S01", "--kind", kind, "--text", "no")[0], 2)
        self.assertEqual({p: p.read_bytes() for p in (repo.root / ".slicer").rglob("*") if p.is_file()}, before)
      config["note_kinds"] = [1]
      repo.write(".slicer/config.json", json.dumps(config))
      invalid_before = {p: p.read_bytes() for p in (repo.root / ".slicer").rglob("*") if p.is_file()}
      self.assertEqual(repo.run("note", "S01", "--text", "no")[0], 3)
      self.assertEqual({p: p.read_bytes() for p in (repo.root / ".slicer").rglob("*") if p.is_file()}, invalid_before)
      config["note_kinds"] = ["report"]
      repo.write(".slicer/config.json", json.dumps(config))
      self.assertEqual(repo.run("note", "S01", "--text", "plain")[0], 0)
      self.assertEqual(repo.run("note", "S01", "--kind", "report", "--text", "yes")[0], 0)


if __name__ == "__main__":
  unittest.main()
