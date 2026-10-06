"""Keep bounded pickup context deterministic and ordinary audit reads complete."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import support
import test_note
from slicer import model, note_pack, render
from slicer.errors import StateError


NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


def record(identity, text, *, kind="", verifier="", created="", expiry="", author=""):
  return model.NoteRecord(identity, kind, text, created, None, by=author,
    verified_by=verifier, stale_after=expiry)


class SelectionTests(unittest.TestCase):
  def pack(self, records, legacy=None, **kwargs):
    return note_pack.select(records, legacy or [], now=NOW, **kwargs)

  def test_Ranking_TrustDatesAndTies_UseVerificationAndStoredOrder(self):
    records = [
      record("author", "authored", author="human:Owner", created="2029-12-01T00:00:00Z"),
      record("unknown", "unknown", verifier="human:Owner"),
      record("old", "old", verifier="human:Owner", created="2020-01-01T00:00:00Z"),
      record("new", "new", verifier="human:Owner", created="2029-01-01T01:00:00+01:00"),
      record("tie", "tie", verifier="human:Owner", created="2029-01-01T00:00:00Z"),
      record("machine", "machine", verifier="agent", created="2029-12-01T00:00:00Z"),
    ]
    selected, _, manifest = self.pack(records)
    self.assertEqual(manifest["selected_ids"], ["new", "tie", "old", "unknown", "machine", "author"])
    self.assertEqual([n.id for n in selected], manifest["selected_ids"])
    records[0].verified_by = "human:Owner"
    self.assertEqual(self.pack(records)[2]["selected_ids"][0], "author")
    _, _, manifest = self.pack([record("unknown", "x"),
      record("earliest", "x", created="0001-01-01T00:00:00Z")])
    self.assertEqual(manifest["selected_ids"], ["earliest", "unknown"])

  def test_Filters_ReasonPrecedenceAndBoundary_AreExplicit(self):
    records = [
      record("kind", "x", kind="other", expiry="2000-01-01T00:00:00Z"),
      record("expired", "x", kind="report", expiry="2030-01-01T01:00:00+01:00"),
      record("trust", "x", kind="report"),
      record("budget", "xx", kind="decision", verifier="agent"),
      record("current", "x", kind="report", verifier="agent", expiry="2030-01-01T00:00:00.001Z"),
    ]
    selected, _, manifest = self.pack(records, kinds=["report", "decision"],
      min_trust="machine-confirmed", budget=len(records[-1].display()))
    self.assertEqual([n.id for n in selected], ["current"])
    self.assertEqual(manifest["omitted"], [
      {"id": "kind", "reason": "kind"}, {"id": "expired", "reason": "expired"},
      {"id": "trust", "reason": "trust"}, {"id": "budget", "reason": "budget"},
    ])

  def test_Budget_UnicodeSeparatorsAndOversize_KeepWholeDisplayNotes(self):
    records = [record("large", "oversized"), record("unicode", "é😀"), record("last", "字")]
    for budget, expected, characters in ((4, ["unicode", "last"], 4),
                                         (3, ["unicode"], 2), (0, [], 0)):
      with self.subTest(budget=budget):
        selected, _, manifest = self.pack(records, budget=budget)
        self.assertEqual([n.id for n in selected], expected)
        self.assertEqual(manifest["characters"], characters)
    typed = record("typed", "x", kind="report")
    self.assertEqual(self.pack([typed], budget=len(typed.display()))[2]["selected_ids"], ["typed"])
    self.assertEqual(self.pack([typed], budget=len(typed.display()) - 1)[2]["selected_ids"], [])

  def test_Legacy_ItemAndSlice_ShareBudgetWithStableReadIds(self):
    selected, legacy, manifest = self.pack([record("S01:legacy:0", "item")],
      ["too long", "a", "b"], budget=6)
    self.assertEqual([n.id for n in selected], ["S01:legacy:0"])
    self.assertEqual(legacy, ["a"])
    self.assertEqual(manifest["selected_ids"], ["S01:legacy:0", "slice-legacy-2"])
    self.assertEqual(manifest["characters"], 6)
    self.assertEqual(self.pack([], ["a"], min_trust="machine-confirmed")[2]["omitted"],
      [{"id": "slice-legacy-1", "reason": "trust"}])

  def test_EmptyNotes_ZeroBudget_CountSeparatorsBySelection(self):
    _, legacy, manifest = self.pack([], ["", "", "x"], budget=0)
    self.assertEqual(legacy, [""])
    self.assertEqual(manifest["selected_ids"], ["slice-legacy-1"])
    self.assertEqual(manifest["characters"], 0)

  def test_InvalidOptions_RejectAndMalformedExpiry_IsCorrupt(self):
    for options in ({"min_trust": "unknown"}, {"budget": -1}):
      with self.assertRaises(StateError) as caught:
        self.pack([], **options)
      self.assertEqual(caught.exception.code, "usage")
    with self.assertRaises(StateError) as caught:
      self.pack([record("bad", "x", expiry="invalid")])
    self.assertEqual(caught.exception.code, "corrupt")


class PackCommandTests(unittest.TestCase):
  repo = test_note.StructuredNoteTests.repo
  payload = test_note.StructuredNoteTests.payload

  def snapshot(self, repo):
    return {p.relative_to(repo.root): p.read_bytes()
      for p in (repo.root / ".slicer").rglob("*") if p.is_file() and p.name != "lock"}

  def seed(self, repo):
    state = repo.state()
    state.index.require("S01").note_records = [
      record("expired", "old", kind="report", expiry="2030-01-01T00:00:00Z"),
      record("trusted", "yes", kind="report", verifier="human:Owner"),
      record("plain", "unverified"),
    ]
    state.slices["S01"].notes = ["large legacy note", "a"]
    state.save_slice(state.slices["S01"])
    state.save_index()
    self.assertEqual(repo.run("render")[0], 0)

  def test_DefaultAndKindOnly_KeepExpiredAuditNotesAndState(self):
    with self.repo() as repo:
      self.seed(repo)
      before = self.snapshot(repo)
      for command in (("show", "S01"), ("next", "--ready"), ("next", "--ready", "--batch", "2")):
        for options in ((), ("--notes-kind", "report")):
          data = self.payload(repo, *command, *options)
          if "items" in data:
            data = data["items"][0]
          item = data.get("item", data)
          self.assertIn("expired", [n["id"] for n in item["note_records"]])
          self.assertNotIn("note_pack", item)
      self.assertEqual(before, self.snapshot(repo))

  def test_AllProjections_ComposeKindsPackingAndLean_WithoutWrites(self):
    with self.repo() as repo:
      self.seed(repo)
      before = self.snapshot(repo)
      state = repo.state()
      rendered = render.plan(state)
      commands = (("show", "S01"), ("next", "--ready"), ("next", "--ready", "--batch", "2"))
      sections = ((), ("--section", "Implement"), ("--section", "Implement", "--section", "Check"))
      with patch("slicer.cli.datetime") as clock:
        clock.now.return_value = NOW
        for command in commands:
          for section in sections:
            for lean in ((), ("--lean",)):
              data = self.payload(repo, *command, *section, *lean, "--min-trust", "machine-confirmed",
                "--notes-kind", "report", "--notes-budget", "12")
              if "items" in data:
                data = data["items"][0]
              item = data.get("item", data)
              self.assertEqual(item["notes"], ["[report] yes"])
              self.assertEqual(item["note_pack"]["selected_ids"], ["trusted"])
              self.assertEqual(item["note_pack"]["characters"], 12)
              self.assertEqual([n["id"] for n in item["note_records"]], ["trusted"])
              if "slice" in data:
                self.assertEqual(data["slice"].get("notes", []), [])
        context = self.payload(repo, "show", "S01", "--section", "Implement", "--context",
          "--notes-budget", "0", "--lean")
        self.assertEqual(context["note_pack"]["selected_ids"], [])
        self.assertIn("sections", context)
      self.assertEqual(before, self.snapshot(repo))
      self.assertEqual(rendered, render.plan(repo.state()))

  def test_EitherOption_EnablesExpiryAndDefaults_WithLegacyProjection(self):
    with self.repo() as repo:
      self.seed(repo)
      with patch("slicer.cli.datetime") as clock:
        clock.now.return_value = NOW
        for options in (("--min-trust", "unverified"), ("--notes-budget", "100")):
          data = self.payload(repo, "show", "S01", *options)
          self.assertNotIn("expired", data["note_pack"]["selected_ids"])
          self.assertEqual(data["slice"]["notes"], ["large legacy note", "a"])
          self.assertEqual(data["note_pack"]["min_trust"], "unverified")
        data = self.payload(repo, "show", "S01", "--section", "Implement", "--notes-budget", "14")
        self.assertEqual(data["slice_notes"], ["a"])
        self.assertEqual(data["note_pack"]["selected_ids"], ["trusted", "slice-legacy-2"])
        self.assertEqual(data["note_pack"]["characters"], 14)

  def test_Batch_UsesIndependentBudgetsAndSingleInvocationClock(self):
    with self.repo() as repo:
      self.seed(repo)
      self.assertEqual(repo.run("add", "second")[0], 0)
      state = repo.state()
      state.index.require("S02").note_records = [record("second", "abc", expiry="2030-01-01T00:00:01Z")]
      state.save_index()
      with patch("slicer.cli.datetime") as clock:
        clock.now.return_value = NOW
        data = self.payload(repo, "next", "--ready", "--batch", "2", "--notes-budget", "12")
        clock.now.assert_called_once_with(timezone.utc)
      self.assertEqual([e["item"]["note_pack"]["characters"] for e in data["items"]], [12, 3])
      self.assertEqual(data["items"][1]["item"]["note_pack"]["selected_ids"], ["second"])

  def test_EmptyManifest_LeanPreservesCompleteShape_AndRowsNeedNoSlice(self):
    with self.repo() as repo:
      self.assertEqual(repo.run("add", "row")[0], 0)
      expected = {"selected_ids": [], "omitted": [], "characters": 0,
                  "budget": None, "min_trust": "unverified"}
      for options in ((), ("--lean",)):
        data = self.payload(repo, "show", "S02", "--min-trust", "unverified", *options)
        self.assertEqual(data["note_pack"], expected)
        self.assertNotIn("slice", data)
        data = self.payload(repo, "next", "--ready", "--min-trust", "unverified", *options)
        self.assertEqual(data["item"]["note_pack"], expected)

  def test_InvalidOptions_WithStart_FailBeforeAnyWrites(self):
    with self.repo() as repo:
      before = self.snapshot(repo)
      for batch in ((), ("--batch", "2")):
        for options in (("--min-trust", "unknown"), ("--notes-budget", "-1"), ("--notes-budget", "bad")):
          code, out, err = repo.run("next", "--ready", "--start", *batch, *options, "--json")
          self.assertEqual(code, 2, err)
          self.assertEqual(json.loads(out)["error"]["code"], "usage")
          self.assertEqual(before, self.snapshot(repo))
      for options in (("--min-trust", "unverified"), ("--notes-budget", "0")):
        self.assertEqual(repo.run("next", "--start", *options)[0], 2)
        self.assertEqual(before, self.snapshot(repo))

  def test_Start_PackingClaimsNormally_AndBadExpiryCannotPartiallyClaimBatch(self):
    with self.repo() as repo:
      self.seed(repo)
      data = self.payload(repo, "next", "--ready", "--start", "--notes-budget", "0")
      self.assertEqual(data["item"]["status"], "started")
      self.assertEqual(data["item"]["note_pack"]["selected_ids"], [])
      self.assertEqual(repo.state().index.require("S01").attempts, 1)
    with self.repo() as repo:
      repo.run("add", "second")
      state = repo.state()
      state.index.require("S02").note_records = [record("bad", "x", expiry="invalid")]
      state.save_index()
      before = self.snapshot(repo)
      code, out, err = repo.run("next", "--ready", "--batch", "2", "--start",
        "--min-trust", "unverified", "--json")
      self.assertEqual(code, 3, err)
      self.assertEqual(json.loads(out)["error"]["code"], "corrupt")
      self.assertEqual(before, self.snapshot(repo))

  def test_LegacyLift_PackingRead_LeavesHistoricalBytesUntouched(self):
    with self.repo() as repo:
      path = repo.root / ".slicer/index.json"
      data = json.loads(path.read_text())
      data["version"] = 3
      data["items"][0].pop("note_records")
      data["items"][0]["notes"] = ["original bytes\n", "second"]
      path.write_text(json.dumps(data))
      before = self.snapshot(repo)
      result = self.payload(repo, "show", "S01", "--min-trust", "unverified")
      self.assertEqual(result["notes"], ["original bytes\n", "second"])
      self.assertEqual(result["note_pack"]["selected_ids"], ["S01:legacy:0", "S01:legacy:1"])
      self.assertEqual(before, self.snapshot(repo))

  def test_TextPacking_PrintsSelectedNotesAndManifest_ForSectionsAndReady(self):
    with self.repo() as repo:
      self.seed(repo)
      for command in (("show", "S01"), ("show", "S01", "--section", "Implement"),
                      ("show", "S01", "--section", "Implement", "--context"), ("next", "--ready")):
        code, out, err = repo.run(*command, "--min-trust", "human-reviewed")
        self.assertEqual(code, 0, err)
        self.assertIn("[report] yes", out)
        self.assertIn('"selected_ids": ["trusted"]', out)
        self.assertNotIn("large legacy note", out)
