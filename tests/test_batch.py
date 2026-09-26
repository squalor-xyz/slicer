"""Batch requests must validate before writing and retain single-item contracts."""

from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

import support
from slicer import ops, store
from slicer.errors import StateError


class BatchTests(unittest.TestCase):
  def repo(self):
    repo = support.TempRepo()
    repo.run("init")
    for title in ("one", "two"):
      repo.run("add", title)
    for item_id in ("S01", "S02"):
      repo.run("promote", item_id)
    return repo

  def snapshot(self, repo):
    return {str(p.relative_to(repo.root)): p.read_bytes()
            for p in (repo.root / ".slicer").rglob("*") if p.is_file() and p.name != "lock"}

  def test_Commands_Batches_PreserveOrderHistoryAndRender(self):
    with self.repo() as repo:
      for command, options, status in (
        ("start", ["--note", "working"], "started"),
        ("done", ["--note", "finished"], "done"),
        ("park", ["--note", "waiting"], "parked"),
        ("unpark", ["--note", "ready"], "open"),
        ("set", ["--status", "done", "--title", "shared", "--urgency", "3"], "done"),
      ):
        before = len(repo.state().history())
        code, out, err = repo.run(command, "S02", "S01", "S02", *options, "--json", "--render")
        self.assertEqual(code, 0, err)
        self.assertEqual([i["id"] for i in json.loads(out)], ["S02", "S01"])
        state = repo.state()
        entries = state.history()[before:]
        self.assertEqual([e.item for e in entries], ["S02", "S01"])
        self.assertEqual([e.note for e in entries],
                         [",".join(("status", "title", "urgency")) if command == "set" else options[1]] * 2)
        for item_id in ("S01", "S02"):
          self.assertEqual(state.index.require(item_id).status, status)
          self.assertTrue(state.slice_path(item_id).exists())
          if command == "set":
            self.assertEqual(state.slices[item_id].title, "shared")
        self.assertEqual(repo.run("check")[0], 0)

  def test_Input_StdinAndExplicitIds_KeepOutputContract(self):
    for command in ("done", "start", "park", "unpark", "set"):
      with self.subTest(command=command), self.repo() as repo:
        for ids, stdin, expected in (
          (["S01"], "", dict), (["S01", "S01"], "", list),
          (["-"], " S01\nS01\t", list), (["-"], "S02 S01\n", list),
        ):
          with patch("sys.stdin", io.StringIO(stdin)):
            code, out, err = repo.run(command, *ids, "--json")
          self.assertEqual(code, 0, err)
          self.assertIsInstance(json.loads(out), expected)
        for ids, stdin in ((["-"], " \n"), (["S01", "-"], "S02"), (["-", "-"], "")):
          before = self.snapshot(repo)
          with patch("sys.stdin", io.StringIO(stdin)):
            code, out, err = repo.run(command, *ids, "--json")
          self.assertEqual(code, 2)
          self.assertEqual(json.loads(out)["error"]["code"], "usage")
          self.assertEqual(self.snapshot(repo), before)

  def test_InvalidRequests_LeaveMemoryAndDiskUnchanged(self):
    with self.repo() as repo:
      for action in (
        lambda s: ops.set_status_many(s, ["S01", "S99"], "done"),
        lambda s: ops.set_status_many(s, ["S01", "S02"], "invalid"),
        lambda s: ops.set_fields_many(s, ["S01", "S99"], title="changed"),
        lambda s: ops.set_fields_many(s, ["S01", "S02"], title="changed", urgency=4),
        lambda s: ops.set_fields_many(s, ["S01", "S02"], title="changed", unknown=True),
        lambda s: ops.set_fields_many(s, ["S01", "S02"], title="bad\ntext"),
      ):
        state = repo.state()
        before = self.snapshot(repo)
        index = state.index.to_dict()
        with self.assertRaises(StateError):
          action(state)
        self.assertEqual(state.index.to_dict(), index)
        self.assertEqual(self.snapshot(repo), before)
      for command in ("done", "start", "park", "unpark", "set"):
        before = self.snapshot(repo)
        self.assertEqual(repo.run(command, "S01", "S99", "--render")[0], 2)
        self.assertEqual(self.snapshot(repo), before)

  def test_Batches_LoadAndSaveOnce_StatusNoopsDoNotLog(self):
    with self.repo() as repo:
      for command in ("start", "done", "park", "unpark", "set"):
        with patch.object(store, "load", wraps=store.load) as load:
          with patch.object(store.State, "save_index", autospec=True,
                            side_effect=store.State.save_index) as save:
            self.assertEqual(repo.run(command, "S01", "S02")[0], 0)
        self.assertEqual(load.call_count, 1)
        self.assertEqual(save.call_count, 1)
      before = self.snapshot(repo)
      self.assertEqual(repo.run("unpark", "S01", "S02")[0], 0)
      self.assertEqual(self.snapshot(repo), before)

  def test_CustomStatuses_BatchesUseConfigAndRefuseMissingStatus(self):
    with self.repo() as repo:
      path = repo.root / ".slicer/config.json"
      cfg = json.loads(path.read_text())
      cfg["statuses"].update({"todo": "todo", "active": "active", "hold": "hold", "finished": "finished"})
      cfg.update(open_status="todo", started_status="active", parked_status="hold", done_status="finished")
      path.write_text(json.dumps(cfg))
      for command, status in (("start", "active"), ("done", "finished"),
                              ("park", "hold"), ("unpark", "todo")):
        code, out, err = repo.run(command, "S01", "S02", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual([i["status"] for i in json.loads(out)], [status, status])
      for command, field in (("start", "started_status"), ("park", "parked_status")):
        cfg[field] = ""
        path.write_text(json.dumps(cfg))
        before = self.snapshot(repo)
        self.assertEqual(repo.run(command, "S01", "S02")[0], 2)
        self.assertEqual(self.snapshot(repo), before)


if __name__ == "__main__":
  unittest.main()
