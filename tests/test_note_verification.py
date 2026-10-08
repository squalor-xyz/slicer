"""Keep workflow attestation separate from authorship and invalidate it on edits."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

import support
from slicer import model, ops, render, tui
import test_note


class VerificationTests(unittest.TestCase):
  repo = test_note.StructuredNoteTests.repo
  payload = test_note.StructuredNoteTests.payload

  def snapshot(self, repo):
    return {p: p.read_bytes() for p in (repo.root / ".slicer").rglob("*")
      if p.is_file() and p.name != "lock"}

  def note(self, repo):
    self.payload(repo, "note", "S01", "--text", "evidence", "--kind", "report",
      "--owner", "agent", "--stale-after", "2030-01-01T01:00:00+01:00")
    return repo.state().index.require("S01").note_records[0]

  def test_Verification_AttestationAndRepeat_PreserveTextAndAudit(self):
    with self.repo() as repo:
      original = self.note(repo)
      self.assertEqual(original.trust, "unverified")
      self.assertEqual(original.stale_after, "2030-01-01T00:00:00Z")
      for command, owner, expected in (("note-verify", "machine", "machine-confirmed"),
                                      ("note-attest", "Owner", "human-reviewed"),
                                      ("note-verify", "second-machine", "machine-confirmed")):
        with patch("slicer.ops._now", return_value="2026-01-02T03:04:05Z"):
          result = self.payload(repo, command, "S01", "--note-id", original.id, "--owner", owner)
        record = repo.state().index.require("S01").note_records[0]
        self.assertEqual(result["trust"], expected)
        self.assertEqual(record.by, "agent")
        self.assertEqual(record.text, original.text)
        self.assertEqual(record.verified_at, "2026-01-02T03:04:05Z")
        before = self.snapshot(repo)
        self.payload(repo, command, "S01", "--note-id", original.id, "--owner", owner)
        self.assertEqual(before, self.snapshot(repo))
        entry = self.payload(repo, "log", "--action", command)[0]
        self.assertEqual(entry["note"], original.id)
        self.assertEqual(entry["by"], record.verified_by)

  def test_Edit_OpsAndTui_ClearVerificationOnlyOnChange(self):
    for via_tui in (False, True):
      with self.subTest(via_tui=via_tui), self.repo() as repo:
        original = self.note(repo)
        self.payload(repo, "note-attest", "S01", "--note-id", original.id, "--owner", "Owner")
        state = repo.state()
        before = self.snapshot(repo)
        ops.set_note(state, "S01", 0, original.text)
        self.assertEqual(before, self.snapshot(repo))
        if via_tui:
          request = tui.EditRequest(kind="note", target="S01", name="note", body=original.text, index=0)
          self.assertEqual(tui.apply_edit_result(state, request, "changed").severity, "success")
        else:
          ops.set_note(state, "S01", 0, "changed")
        record = repo.state().index.require("S01").note_records[0]
        self.assertEqual((record.verified_by, record.verified_at, record.trust), ("", "", "unverified"))
        for key in ("id", "kind", "by", "created_at", "attempt", "stale_after"):
          self.assertEqual(getattr(record, key), getattr(original, key))
        self.assertIn("human:Owner", self.payload(repo, "log")[0]["note"])

  def test_InvalidInputs_LeaveStateUntouched(self):
    with self.repo() as repo:
      original = self.note(repo)
      commands = []
      for expiry in ("garbage", "2030-01-01", "2030-01-01T00:00:00"):
        commands.append(("note", "S01", "--text", "x", "--stale-after", expiry))
      commands.append(("note", "S01", "--text", "x", "--owner", "human:Owner"))
      for command in ("note-verify", "note-attest"):
        for owner in ("", "  ", "human:Owner", "two\nlines"):
          commands.append((command, "S01", "--note-id", original.id, "--owner", owner))
        commands.append((command, "S01", "--note-id", "missing", "--owner", "Owner"))
      before = self.snapshot(repo)
      for args in commands:
        with self.subTest(args=args):
          code, out, err = repo.run(*args, "--json")
          self.assertEqual(code, 2, err)
          self.assertEqual(json.loads(out)["error"]["code"], "usage")
          self.assertEqual(before, self.snapshot(repo))
      with patch.dict(os.environ, {"SLICER_CLAIM_OWNER": "human:Owner"}):
        self.assertEqual(repo.run("note", "S01", "--text", "x")[0], 2)
      state = repo.state()
      state.config.claim_owner = "human:Owner"
      with patch.dict(os.environ, {"SLICER_CLAIM_OWNER": ""}):
        with self.assertRaisesRegex(Exception, "reserved human:"):
          ops.add_note(state, "S01", "x")
      self.assertEqual(before, self.snapshot(repo))

  def test_Verify_WithoutOwner_UsesActorPrecedenceAndRefusesHuman(self):
    with self.repo() as repo:
      original = self.note(repo)
      with patch.dict(os.environ, {"SLICER_CLAIM_OWNER": "ci-bot"}):
        result = self.payload(repo, "note-verify", "S01", "--note-id", original.id)
      self.assertEqual(result["note_record"]["verified_by"], "ci-bot")
      self.assertEqual(result["trust"], "machine-confirmed")
      before = self.snapshot(repo)
      with patch.dict(os.environ, {"SLICER_CLAIM_OWNER": "human:Owner"}):
        code, out, _ = repo.run("note-verify", "S01", "--note-id", original.id, "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out)["error"]["code"], "usage")
      self.assertEqual(repo.run("note-attest", "S01", "--note-id", original.id)[0], 2)
      self.assertEqual(before, self.snapshot(repo))

  def test_Show_NoteRecords_ExposeDerivedTrustAfterStoredFields(self):
    with self.repo() as repo:
      original = self.note(repo)
      record = self.payload(repo, "show", "S01")["note_records"][0]
      self.assertEqual(list(record)[-5:], ["by", "verified_by", "verified_at", "stale_after", "trust"])
      self.assertEqual(record["trust"], "unverified")
      lean = self.payload(repo, "show", "S01", "--lean")["note_records"][0]
      self.assertNotIn("verified_by", lean)
      self.payload(repo, "note-attest", "S01", "--note-id", original.id, "--owner", "Owner")
      self.assertEqual(self.payload(repo, "show", "S01")["note_records"][0]["trust"], "human-reviewed")

  def test_Legacy_MetadataDefaults_AndSliceNotesCannotBeVerified(self):
    record = model.NoteRecord.from_dict({"id": "old", "kind": "", "text": "legacy",
      "created_at": "", "attempt": None})
    self.assertEqual((record.by, record.verified_by, record.verified_at, record.stale_after), ("", "", "", ""))
    self.assertEqual(record.trust, "unverified")
    with self.repo() as repo:
      state = repo.state()
      state.slices["S01"].notes = ["legacy slice note"]
      state.save_slice(state.slices["S01"])
      before = self.snapshot(repo)
      self.assertEqual(repo.run("note-attest", "S01", "--note-id", "legacy slice note", "--owner", "Owner")[0], 2)
      self.assertEqual(before, self.snapshot(repo))

  def test_Render_FrozenClocks_IdenticalBytesAndStoredMetadata(self):
    with self.repo() as repo:
      original = self.note(repo)
      self.payload(repo, "note-attest", "S01", "--note-id", original.id, "--owner", "Owner")
      state = repo.state()
      with patch("slicer.ops._now", return_value="2000-01-01T00:00:00Z"):
        first = render.plan(state)
      with patch("slicer.ops._now", return_value="2040-01-01T00:00:00Z"):
        second = render.plan(state)
      self.assertEqual(first, second)
      rendered = first["slices/S01.md"].decode()
      for text in ("human-reviewed", "verified_by: human:Owner", "stale_after: 2030-01-01T00:00:00Z"):
        self.assertIn(text, rendered)

  def test_Schema_NewMetadata_PersistsAndOlderReadersRefuse(self):
    with self.repo() as repo:
      original = self.note(repo)
      data = json.loads(repo.read(".slicer/index.json"))
      self.assertEqual(data["version"], model.SCHEMA_VERSION)
      self.assertEqual(data["items"][0]["note_records"][0], original.persisted_dict())
      self.assertNotIn("trust", data["items"][0]["note_records"][0])
      before = self.snapshot(repo)
      with patch("slicer.model.SCHEMA_VERSION", 6):
        code, out, err = repo.run("show", "S01", "--json")
      self.assertEqual(code, 3, err)
      self.assertEqual(json.loads(out)["error"]["code"], "schema_too_new")
      self.assertEqual(before, self.snapshot(repo))

  def test_StrictRenderFailure_VerificationDoesNotLand(self):
    with self.repo() as repo:
      original = self.note(repo)
      repo.write(".slicer/templates/slice.md", "{{unknown}}")
      before = self.snapshot(repo)
      self.assertNotEqual(repo.run("note-attest", "S01", "--note-id", original.id,
        "--owner", "Owner", "--render", "--strict")[0], 0)
      self.assertEqual(before, self.snapshot(repo))


if __name__ == "__main__":
  unittest.main()
