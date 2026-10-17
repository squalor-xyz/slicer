"""Owner answers release held work without losing the decision or other flags."""

from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

import support


class DecideTests(unittest.TestCase):
  def repo(self):
    repo = support.TempRepo()
    repo.run("init")
    repo.run("add", "waiting")
    repo.run("set", "S01", "--add-flag", "needs-owner", "--add-flag", "keep")
    return repo

  def shown(self, repo):
    return json.loads(repo.run("show", "S01", "--json")[1])

  def test_Decide_ConflictingBodySources_UsageAndNoWrite(self):
    with self.repo() as repo:
      before = self.shown(repo)
      history = repo.run("log", "--json")[1]
      with patch("sys.stdin", io.StringIO("A different answer.")):
        code, out, _ = repo.run("decide", "S01", "--text", "Answer", "--stdin", "--json")
      self.assertEqual((code, json.loads(out).get("error", {}).get("code"),
        self.shown(repo), repo.run("log", "--json")[1]), (2, "usage", before, history))

  def test_Decide_AddsDecisionNoteAndClearsFlag(self):
    with self.repo() as repo:
      repo.run("decide", "S01", "--text", "Use the smaller interface.")
      item = self.shown(repo)
      self.assertEqual((item["fields"]["flags"],
        [(n["kind"], n["text"].split(" — ", 1)[-1]) for n in item["note_records"]]),
        (["keep"], [("decision", "Use the smaller interface.")]))

  def test_Decide_NoHoldFlag_Usage(self):
    with self.repo() as repo:
      repo.run("set", "S01", "--remove-flag", "needs-owner")
      code, out, _ = repo.run("decide", "S01", "--text", "Answer", "--json")
      error = json.loads(out)["error"]
      self.assertEqual((code, error["code"], "needs-owner" in error["message"],
        self.shown(repo)["note_records"]), (2, "usage", True, []))

  def test_Decide_CustomHoldFlags_ClearsEveryHoldFlag(self):
    with self.repo() as repo:
      repo.run("config", "hold_flags", '["needs-owner", "design"]')
      repo.run("set", "S01", "--add-flag", "design")
      repo.run("decide", "S01", "--text", "Answer")
      self.assertEqual(self.shown(repo)["fields"]["flags"], ["keep"])

  def test_Decide_RestrictedKinds_WritesDecision(self):
    with self.repo() as repo:
      repo.run("config", "note_kinds", '["report"]')
      repo.run("decide", "S01", "--text", "Answer")
      self.assertEqual([n["kind"] for n in self.shown(repo)["note_records"]], ["decision"])

  def test_Decide_FileAndStdin_RecordBody(self):
    for source in ("file", "stdin"):
      with self.subTest(source=source), self.repo() as repo:
        repo.write("answer.md", "An owner answer.\n")
        args = ("--file", str(repo.root / "answer.md")) if source == "file" else ("--stdin",)
        with patch("sys.stdin", io.StringIO("An owner answer.\n")):
          repo.run("decide", "S01", *args)
        self.assertEqual([n["text"].split(" — ", 1)[-1] for n in self.shown(repo)["note_records"]],
          ["An owner answer."])

  def test_Decide_BlankAnswer_LeavesHeldItemUnchanged(self):
    with self.repo() as repo:
      before = self.shown(repo)
      code, out, _ = repo.run("decide", "S01", "--text", "  ", "--json")
      self.assertEqual((code, json.loads(out)["error"]["code"],
        "answer cannot be blank" in json.loads(out)["error"]["message"], self.shown(repo)),
        (2, "usage", True, before))

  def test_Decide_PromotedItem_ClearsRenderedHoldFlag(self):
    with self.repo() as repo:
      repo.run("promote", "S01")
      repo.run("decide", "S01", "--text", "Answer", "--render", "--strict")
      rendered = repo.read(".slicer/render/slices/S01.md")
      self.assertEqual(("[needs-owner]" in rendered, "[keep]" in rendered,
        "Answer" in rendered), (False, True, True))
