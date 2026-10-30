"""Dashboards need consistent compact reads without serial process startups."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import unittest

import support
from test_recursive import make_project, slicer
from slicer import __version__, ai, jsonio


class SnapshotTests(unittest.TestCase):
  def fixture(self, *, git=False):
    repo = support.TempRepo(git=git)
    self.addCleanup(repo.close)
    self.enterContext(support.isolated_discovery(repo.root))
    make_project(repo.root, "First", "Second")
    return repo

  def snapshot(self, repo, *extra):
    code, out, err = repo.run("snapshot", "--json", *extra)
    self.assertEqual(code, 0, err)
    return json.loads(out)

  def test_Snapshot_OneRoot_CarriesEveryPartInOrder(self):
    repo = self.fixture()
    data = self.snapshot(repo)
    self.assertEqual(list(data), ["snapshot", "slicer", "contract", "projects"])
    self.assertEqual([data[k] for k in ("snapshot", "slicer", "contract")],
                     [1, __version__, ai.RELAY_CONTRACT])
    project = data["projects"][0]
    self.assertEqual(list(project), ["root", "revision", "parts", "error"])
    self.assertIsNone(project["error"])
    self.assertEqual(list(project["parts"]), ["items", "goals", "runs", "status", "health"])
    self.assertEqual(project["parts"]["goals"]["value"],
                     {"goals": "", "non_goals": "", "records": []})
    status = json.loads(repo.run("status", "--json")[1])
    self.assertEqual(project["parts"]["status"]["value"],
                     {"next": status["next"]["id"], "blocked": [], "held": []})
    self.assertEqual(project["parts"]["status"]["value"]["next"],
                     json.loads(repo.run("next", "--json")[1])["id"])
    for ref, body in (("goals", "Ship locally"), ("non_goals", "No server")):
      path = repo.write(ref + ".md", body)
      self.assertEqual(repo.run("prose", "edit", ref, "--file", str(path))[0], 0)
    for kind, title in (("non-goal", "No service"), ("goal", "Ship it"),
                        ("goal", "Old goal"), ("requirement", "Stdlib")):
      self.assertEqual(repo.run("catalog", "add", "--kind", kind, title)[0], 0)
    self.assertEqual(repo.run("catalog", "retire", "C03", "--reason", "Replaced")[0], 0)
    goals = self.snapshot(repo)["projects"][0]["parts"]["goals"]["value"]
    self.assertEqual(goals, {"goals": "Ship locally", "non_goals": "No server", "records": [
      {"id": "C02", "kind": "goal", "title": "Ship it"},
      {"id": "C01", "kind": "non_goal", "title": "No service"},
    ]})

  def test_Snapshot_ItemsRow_MatchesListOrderAndFields(self):
    repo = self.fixture()
    self.assertEqual(repo.run("start", "S01", "--owner", "Ada")[0], 0)
    for args in (("config", "hold_flags", '["WAIT"]'),
                 ("set", "S01", "--flag", "WAIT", "--size", "M", "--effort", "3"),
                 ("set", "S02", "--depends-on", "S01", "--flag", "INFO"),
                 ("move", "S02", "--before", "S01")):
      code, _, err = repo.run(*args)
      self.assertEqual(code, 0, err)
    for title, status in (("Finished", "done"), ("Retired", "retired")):
      code, out, err = repo.run("add", title, "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.run("set", json.loads(out)["id"], "--status", status)[0], 0)
    status = self.snapshot(repo)["projects"][0]["parts"]["status"]["value"]
    next_code, next_out, _ = repo.run("next", "--json")
    self.assertEqual(next_code, 2)
    self.assertIsNone(json.loads(next_out)["item"])
    self.assertEqual(status, {"next": None, "blocked": ["S02"], "held": ["S01"]})
    code, out, err = repo.run("add", "Ready", "--depends-on", "S03", "--json")
    self.assertEqual(code, 0, err)
    ready = json.loads(out)["id"]
    listed = json.loads(repo.run("list", "--json")[1])
    rows = self.snapshot(repo)["projects"][0]["parts"]["items"]["value"]
    expected = []
    for row in listed:
      item = repo.state().index.require(row["id"])
      expected.append({"id": item.id, "short_title": item.short_title,
        "status": item.status, "claim": "Ada" if item.id == "S01" else None, "pass": item.pass_key,
        "size": item.size, "effort": item.effort, "score": item.score,
        "quadrant": item.quadrant, "held": item.id == "S01", "blocked": item.id == "S02"})
    self.assertEqual(rows, expected)
    self.assertEqual([row["id"] for row in listed], ["S01", ready, "S02"])
    self.assertEqual([row["id"] for row in rows], [row["id"] for row in listed])
    by_id = {row["id"]: row for row in rows}
    self.assertFalse(by_id["S02"]["held"])
    self.assertFalse(by_id[ready]["blocked"])
    self.assertTrue(all(len(row) == 11 for row in rows))
    status = self.snapshot(repo)["projects"][0]["parts"]["status"]["value"]
    next_code, next_out, _ = repo.run("next", "--json")
    self.assertEqual(next_code, 0)
    self.assertEqual(json.loads(next_out)["id"], ready)
    self.assertEqual(status, {"next": ready, "blocked": ["S02"], "held": ["S01"]})

  def test_Snapshot_RepeatedRoot_OneProjectEachInGivenOrder(self):
    repo = self.fixture()
    other = repo.root / "other"
    make_project(other, "Other")
    code, out, err = slicer("snapshot", "--json", "--root", str(other), "--root", str(repo.root))
    self.assertEqual(code, 0, err)
    self.assertEqual([p["root"] for p in json.loads(out)["projects"]], [str(other), str(repo.root)])
    code, out, err = slicer("--root", str(other), "snapshot", "--json", "--root", str(repo.root))
    self.assertEqual(code, 0, err)
    self.assertEqual([p["root"] for p in json.loads(out)["projects"]], [str(other), str(repo.root)])

  def test_Snapshot_Recursive_FindsNestedProjects(self):
    repo = self.fixture()
    for name in ("b", "a/deep", "a-b"):
      make_project(repo.root / name, name)
    for name in (".hidden", "node_modules/ignored"):
      make_project(repo.root / name, name)
    data = self.snapshot(repo, "-r")
    self.assertEqual([p["root"] for p in data["projects"]],
                     [str(repo.root / name) for name in ("", "a/deep", "a-b", "b")])
    listed = json.loads(repo.run("list", "-r", "--json")[1])["projects"]
    self.assertEqual([p["root"] for p in data["projects"]],
                     [str((repo.root / p["path"]).resolve()) for p in listed])

  def test_Snapshot_Parts_OnlyNamedParts(self):
    repo = self.fixture()
    parts = self.snapshot(repo, "--parts", "items,health")["projects"][0]["parts"]
    self.assertEqual(list(parts), ["items", "health"])
    lean = self.snapshot(repo, "--parts", "health,goals", "--lean")["projects"][0]["parts"]
    self.assertEqual(list(lean), ["health", "goals"])
    self.assertEqual(lean["goals"]["value"], {"goals": "", "non_goals": "", "records": []})
    code, out, err = repo.run("snapshot", "--json", "--parts", "unknown,other")
    self.assertEqual((code, out), (2, ""))
    self.assertIn("usage", err)
    self.assertIn("unknown,other", err)

  def test_Snapshot_Revision_ChangesOnlyWithItsPart(self):
    repo = self.fixture()
    before = self.snapshot(repo)["projects"][0]
    self.assertEqual(repo.run("snapshot", "--json")[1], repo.run("snapshot", "--json")[1])
    self.assertEqual(repo.run("set", "S01", "--short-title", "Changed")[0], 0)
    after = self.snapshot(repo)["projects"][0]
    self.assertNotEqual(before["revision"], after["revision"])
    self.assertNotEqual(before["parts"]["items"]["revision"], after["parts"]["items"]["revision"])
    self.assertEqual(before["parts"]["goals"]["revision"], after["parts"]["goals"]["revision"])
    digest = lambda value: hashlib.sha256(jsonio.dumps(value).encode("utf-8")).hexdigest()[:12]
    for part in after["parts"].values():
      self.assertEqual(part["revision"], digest(part["value"]))
    self.assertEqual(after["revision"], digest({k: v["revision"] for k, v in after["parts"].items()}))

  def test_Snapshot_BadRoot_ReportsErrorAndOthersPrint(self):
    repo = self.fixture()
    missing = repo.root / "missing"
    code, out, _ = slicer("snapshot", "--json", "--root", str(missing), "--root", str(repo.root))
    self.assertEqual(code, 2)
    bad, good = json.loads(out)["projects"]
    self.assertEqual((bad["root"], bad["parts"], bad["error"]["code"], bad["error"]["command"]),
                     (str(missing), {}, "no_project", "snapshot"))
    self.assertTrue(bad["error"]["message"])
    self.assertIsNone(good["error"])
    self.assertEqual(good["root"], str(repo.root))

  def test_Snapshot_ErrorExit_IsHighestAcrossProjects(self):
    repo = self.fixture()
    corrupt = repo.root / "corrupt"
    make_project(corrupt, "Broken")
    (corrupt / ".slicer" / "index.json").write_text("garbage{", encoding="utf-8")
    missing = repo.root / "missing"
    code, out, _ = slicer("snapshot", "--json", "--root", str(corrupt), "--root", str(missing))
    self.assertEqual(code, 3)
    projects = json.loads(out)["projects"]
    self.assertEqual([p["root"] for p in projects], [str(corrupt), str(missing)])
    self.assertEqual([p["error"]["code"] for p in projects], ["corrupt", "no_project"])
    self.assertEqual([p["parts"] for p in projects], [{}, {}])

  def test_Snapshot_Health_ReportsRenderStaleAndMerge(self):
    repo = self.fixture(git=True)
    health = lambda: self.snapshot(repo, "--parts", "health")["projects"][0]["parts"]["health"]["value"]
    self.assertEqual(health(), {"render_stale": False, "merge_in_progress": False})
    orphan = repo.write(".slicer/render/orphan.md", "<!-- GENERATED by slicer. Stray render -->\n")
    self.assertEqual(health(), {"render_stale": True, "merge_in_progress": False})
    orphan.unlink()
    self.assertEqual(health(), {"render_stale": False, "merge_in_progress": False})
    self.assertEqual(repo.run("set", "S01", "--short-title", "Changed")[0], 0)
    self.assertEqual(health(), {"render_stale": True, "merge_in_progress": False})
    repo.write(".git/MERGE_HEAD", "a" * 40 + "\n")
    self.assertEqual(health(), {"render_stale": True, "merge_in_progress": True})

  def test_Snapshot_Runs_OnlyActive(self):
    repo = self.fixture()
    begin = lambda: json.loads(repo.run("relay-run", "begin", "S01", "--json")[1])["run"]["id"]
    ended = begin()
    self.assertEqual(repo.run("relay-run", "end", ended, "--outcome", "pass")[0], 0)
    active = begin()
    code, _, err = repo.run("relay-run", "step", active, "--role", "implementer",
                           "--agent", "Codex CLI", "--vendor", "OpenAI", "--model", "test-model",
                           "--phase", "start", "--task", "Implement snapshot")
    self.assertEqual(code, 0, err)
    original = json.loads(repo.run("relay-run", "list", "--active", "--json")[1])["runs"][0]
    runs = self.snapshot(repo)["projects"][0]["parts"]["runs"]["value"]
    self.assertEqual(runs, [{"id": active, "items": ["S01"], "worktree": original["worktree"],
      "state": "active", "updated_at": original["updated_at"], "steps": [
        {"id": original["steps"][0]["id"], "role": "implementer", "phase": "start",
         "task": "Implement snapshot", "model": "test-model", "waiting_on": "", "question": None},
      ]}])

  def test_Snapshot_Runs_StepsCarryWaitingOnAndQuestion(self):
    repo = self.fixture()
    run = json.loads(repo.run("relay-run", "begin", "S01", "--json")[1])["run"]["id"]
    def start(role: str) -> str:
      out = repo.run("relay-run", "step", run, "--role", role, "--agent", "Codex CLI",
                     "--phase", "start", "--json")[1]
      return json.loads(out)["step"]["id"]

    running = start("implementer")
    asked = start("reviewer")
    code, _, err = repo.run("relay-run", "ask", run, "--step", asked, "--title", "Which store?",
                            "--option", "file:A JSON file", "--option", "db:A database",
                            "--blocker")
    self.assertEqual(code, 0, err)
    listed = {step["id"]: step for step in
              json.loads(repo.run("relay-run", "list", "--json")[1])["runs"][0]["steps"]}
    runs = self.snapshot(repo)["projects"][0]["parts"]["runs"]["value"]
    steps = {step["id"]: step for step in runs[0]["steps"]}
    self.assertEqual((steps[running]["waiting_on"], steps[running]["question"]), ("", None))
    self.assertEqual(steps[asked]["waiting_on"], "owner")
    self.assertEqual(steps[asked]["question"]["id"], "q1")
    self.assertEqual(steps[asked]["question"], listed[asked]["question"])

  def test_Snapshot_WithoutJson_IsUsage(self):
    repo = self.fixture()
    code, out, err = repo.run("snapshot")
    self.assertEqual((code, out), (2, ""))
    self.assertIn("snapshot requires --json", err)

  def test_Snapshot_WhileLocked_StillReadsAndWritesNothing(self):
    repo = self.fixture()
    before = {p.relative_to(repo.root): p.read_bytes() for p in repo.root.rglob("*") if p.is_file()}
    script = ('import sys; from pathlib import Path; from slicer import store; '
              'lock = store.project_lock(Path(sys.argv[1])); lock.__enter__(); '
              'print("locked", flush=True); sys.stdin.read(); lock.__exit__(None,None,None)')
    env = os.environ | {"PYTHONPATH": str(support.SRC)}
    with subprocess.Popen([sys.executable, "-c", script, str(repo.root)], env=env,
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as proc:
      try:
        self.assertEqual(proc.stdout.readline().strip(), "locked")
        self.snapshot(repo)
      finally:
        proc.communicate(timeout=10)
    after = {p.relative_to(repo.root): p.read_bytes() for p in repo.root.rglob("*") if p.is_file()}
    self.assertEqual(after, before)
