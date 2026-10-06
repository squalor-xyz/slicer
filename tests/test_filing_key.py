"""Prove filing retries preserve bytes and allocate only genuinely new work."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from support import TempRepo, SRC
from slicer import ops, outline, verify
from slicer.model import Item, SCHEMA_VERSION


def snapshot(repo):
  return {str(p.relative_to(repo.root)): p.read_bytes()
          for p in (repo.root / ".slicer").rglob("*")
          if p.is_file() and p.name != "lock"}


class FilingKeyTests(unittest.TestCase):
  def test_Add_RetryWithInvalidReplacement_PreservesAllBytes(self):
    with TempRepo() as repo:
      repo.run("init")
      code, out, _ = repo.run("add", "Original", "--key", " K ", "--render", "--json")
      self.assertEqual(code, 0)
      original = json.loads(out)
      self.assertFalse(original["existing"])
      self.assertEqual(original["fields"]["key"], "K")
      repo.write(".slicer/render/ROADMAP.md", "deliberately stale")
      before = snapshot(repo)
      for extra in ((), ("--render",), ("--render", "--strict")):
        code, out, _ = repo.run("add", "", "--key", "K", "--status", "invalid",
                               "--importance", "99", "--depends-on", "missing",
                               "--id", "../bad", "--json", *extra)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), original | {"existing": True})
        self.assertEqual(snapshot(repo), before)
      state = repo.state()
      with patch.object(state, "save_index", side_effect=AssertionError("save")), patch.object(
        state, "log", side_effect=AssertionError("log")
      ):
        self.assertIs(ops.add(state, "", key="K", status="bad"), state.index.items[0])

  def test_Add_CaseBlankAndTerminalKeys_IdentityRules(self):
    with TempRepo() as repo:
      repo.run("init")
      repo.run("add", "First", "--key", "key")
      repo.run("set", "S01", "--status", "done")
      code, out, _ = repo.run("add", "Second", "--key", "key", "--json")
      self.assertEqual((code, json.loads(out)["id"]), (0, "S01"))
      repo.run("set", "S01", "--status", "retired")
      code, out, _ = repo.run("add", "Third", "--key", "key", "--json")
      self.assertEqual((code, json.loads(out)["id"]), (0, "S01"))
      code, out, _ = repo.run("add", "Upper", "--key", "KEY", "--json")
      self.assertEqual((code, json.loads(out)["id"]), (0, "S02"))
      for key in ("", "  "):
        before = snapshot(repo)
        code, out, _ = repo.run("add", "Blank", "--key", key, "--json")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"]["code"], "blank_key")
        self.assertEqual(snapshot(repo), before)

  def test_Import_MixedAndPureRetry_MapsOnlyNewItems(self):
    with TempRepo() as repo:
      repo.run("init")
      repo.run("add", "Original", "--key", "K", "--status", "done", "--render")
      repo.write("batch.md", "## Replacement\nkey: K\nstatus: invalid\ndepends: missing\n"
                 "### Off schema\nignored\n## New\nkey: N\ndepends: Replacement\n"
                 "importance: 3\nurgency: 2\neffort: 1\n### Why\nNew slice\n")
      before = snapshot(repo)
      code, out, _ = repo.run("import", "batch.md", "--dry-run", "--json")
      self.assertEqual(code, 0)
      planned = json.loads(out)
      self.assertEqual(snapshot(repo), before)
      code, out, _ = repo.run("import", "batch.md", "--render", "--strict", "--json")
      self.assertEqual(code, 0)
      self.assertEqual(json.loads(out), planned)
      self.assertEqual(planned["ids"], ["S01", "S02"])
      self.assertEqual((planned["items"], planned["created"], planned["reused"],
                        planned["promoted"], planned["depends_edges"]), (2, 1, 1, 1, 1))
      self.assertEqual(planned["by_status"], {"done": 1, "open": 1})
      self.assertEqual(planned["off_schema_sections"], {})
      self.assertEqual(planned["warnings"], [])
      self.assertEqual(planned["entries"], [
        {"title": "Replacement", "id": "S01", "existing": True},
        {"title": "New", "id": "S02", "existing": False}])
      state = repo.state()
      self.assertEqual(state.index.require("S01").title, "Original")
      self.assertEqual(state.index.require("S02").depends_on, ["S01"])
      self.assertEqual(state.index.next_id, 3)
      repo.write(".slicer/render/ROADMAP.md", "stale")
      before = snapshot(repo)
      for flags in (("--render",), ("--render", "--strict"), ("--force", "--render")):
        code, out, _ = repo.run("import", "batch.md", "--json", *flags)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["created"], 0)
        self.assertEqual(snapshot(repo), before)
      state = repo.state()
      with patch.object(state, "save_index", side_effect=AssertionError("save")), patch.object(
        state, "log", side_effect=AssertionError("log")
      ):
        self.assertEqual(ops.apply_outline(state, outline.parse(repo.read("batch.md")).items).created, 0)

  def test_Import_InvalidBatch_IsAtomicEvenWithForce(self):
    with TempRepo() as repo:
      repo.run("init")
      repo.run("add", "Old", "--key", "K", "--render")
      cases = [
        "## One\nkey: K\n## Two\nkey: K\n",
        "## Retry\nkey: K\n## Bad\nstatus: invalid\n",
        "## Retry\nkey: K\nunknown: x\n",
        "## Retry\nkey: K\n## Bad\nkey:   \n",
        "## Retry\nkey: K\nkey: again\n",
        "## Retry\nkey: K\n## New\ndepends: Missing\n",
      ]
      for body in cases:
        repo.write("batch.md", body)
        before = snapshot(repo)
        code, _, _ = repo.run("import", "batch.md", "--force", "--render", "--strict", "--json")
        self.assertNotEqual(code, 0, body)
        self.assertEqual(snapshot(repo), before)

  def test_Model_HistoricalMissingAndDuplicateKeys_Validated(self):
    old = Item("S01", "Old", "open").persisted_dict()
    del old["fields"]["key"]
    self.assertEqual(Item.from_dict(old).key, "")
    with TempRepo() as repo:
      repo.run("init")
      before = snapshot(repo)
      self.assertEqual(repo.state().index.version, SCHEMA_VERSION)
      self.assertEqual(snapshot(repo), before)
      repo.run("add", "One", "--key", "K")
      state = repo.state()
      state.index.items.append(Item("S02", "Two", "retired", key="K"))
      self.assertTrue(any("duplicate filing key" in finding.message
                          for finding in verify.offline(state).findings))

  def test_Import_ForwardReuseAndForce_PreservesKeyIdentity(self):
    with TempRepo() as repo:
      repo.run("init")
      repo.run("add", "Old", "--key", "K")
      repo.write("batch.md", "## New\nkey: N\ndepends: Old\n## Old\nkey: K\n")
      code, out, _ = repo.run("import", "batch.md", "--force", "--json")
      self.assertEqual(code, 0)
      self.assertEqual(json.loads(out)["ids"], ["S02", "S01"])
      self.assertEqual(repo.state().index.require("S02").depends_on, ["S01"])
      self.assertEqual(len(repo.state().index.items), 2)

  def test_Schema_HistoricalReadThenSave_UpgradesWithoutLosingKeys(self):
    with TempRepo() as repo:
      repo.run("init")
      repo.run("add", "Old")
      path = repo.root / ".slicer/index.json"
      stored = json.loads(path.read_text())
      stored["version"] = 4
      del stored["items"][0]["fields"]["key"]
      path.write_text(json.dumps(stored))
      before = snapshot(repo)
      self.assertEqual(repo.state().index.require("S01").key, "")
      self.assertEqual(snapshot(repo), before)
      repo.run("add", "New", "--key", "K")
      stored = json.loads(path.read_text())
      self.assertEqual(stored["version"], 6)
      self.assertEqual([item["fields"]["key"] for item in stored["items"]], ["", "K"])
      from slicer import model
      from slicer.errors import StateError
      with patch.object(model, "SCHEMA_VERSION", 4), self.assertRaises(StateError) as caught:
        repo.state()
      self.assertEqual(caught.exception.code, "schema_too_new")

  def test_Add_ConcurrentSameKey_AllocatesOnce(self):
    with TempRepo() as repo:
      repo.run("init")
      env = dict(os.environ, PYTHONPATH=str(SRC))
      command = [sys.executable, "-m", "slicer", "--root", str(repo.root),
                 "add", "Same", "--key", "K", "--json"]
      processes = [subprocess.Popen(command, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, env=env)
                   for _ in range(2)]
      results = []
      for process in processes:
        out, err = process.communicate(timeout=30)
        self.assertEqual(process.returncode, 0, err)
        results.append(json.loads(out))
      self.assertEqual([result["id"] for result in results], ["S01", "S01"])
      self.assertEqual(sorted(result["existing"] for result in results), [False, True])
      self.assertEqual(repo.state().index.next_id, 2)
      self.assertEqual(len(repo.state().index.items), 1)


if __name__ == "__main__":
  unittest.main()
