"""Dashboards need consistent compact reads without serial process startups."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import support
from test_recursive import make_project, slicer
from slicer import __version__, ai, cli, jsonio, store, vcs
from slicer import snapshot as snapshot_module


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
        "release_target": item.pass_key, "wave": None, "size": item.size, "effort": item.effort, "score": item.score,
        "quadrant": item.quadrant, "held": item.id == "S01", "blocked": item.id == "S02"})
    self.assertEqual(rows, expected)
    self.assertEqual([row["id"] for row in listed], ["S01", ready, "S02"])
    self.assertEqual([row["id"] for row in rows], [row["id"] for row in listed])
    by_id = {row["id"]: row for row in rows}
    self.assertFalse(by_id["S02"]["held"])
    self.assertFalse(by_id[ready]["blocked"])
    self.assertTrue(all(len(row) == 13 for row in rows))
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

  def test_Snapshot_EmptyPartName_UsageNamesIt(self):
    repo = self.fixture()
    for value in ("", "items,", ",items", "items,,health"):
      with self.subTest(parts=value):
        code, out, err = repo.run("snapshot", "--json", "--parts", value)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("usage", err)
        self.assertIn("empty snapshot part name", err)
        self.assertNotIn("unknown", err)

  def test_Snapshot_RepeatedPart_Usage(self):
    repo = self.fixture()
    for value, repeated in (("items,items", "items"), ("items,health,items", "items"),
                            ("health,items,health,items", "health,items"),
                            ("items,items,items", "items")):
      with self.subTest(parts=value):
        code, out, err = repo.run("snapshot", "--json", "--parts", value)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("usage", err)
        self.assertIn("repeated snapshot part(s) " + repeated + ";", err)
        self.assertNotIn("unknown", err)

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

  def scratch(self, name, text):
    """A file outside the fixture project, so writing it changes no part's value."""
    folder = tempfile.TemporaryDirectory()
    self.addCleanup(folder.cleanup)
    path = Path(folder.name) / name
    path.write_text(text, encoding="utf-8")
    return str(path)

  def test_Snapshot_DepsPart_EqualsDepsJson(self):
    repo = self.fixture()
    self.assertEqual(repo.run("set", "S02", "--depends-on", "S01")[0], 0)
    code, out, err = repo.run("deps", "--json")
    self.assertEqual(code, 0, err)
    expected = json.loads(out)
    self.assertTrue(expected)
    part = self.snapshot(repo, "--parts", "deps")["projects"][0]["parts"]
    self.assertEqual(list(part), ["deps"])
    self.assertEqual(list(part["deps"]), ["revision", "fingerprint", "value"])
    self.assertEqual(part["deps"]["value"], expected)
    self.assertEqual(part["deps"]["revision"], snapshot_module.revision(expected))

  def test_Snapshot_DefaultParts_StillFiveParts(self):
    repo = self.fixture()
    parts = self.snapshot(repo)["projects"][0]["parts"]
    self.assertEqual(list(parts), ["items", "goals", "runs", "status", "health"])

  def test_Snapshot_NoSince_EveryPartHasAValue(self):
    repo = self.fixture()
    parts = self.snapshot(repo, "--parts", "items,goals,runs,status,health,deps")["projects"][0]["parts"]
    self.assertEqual(len(parts), 6)
    for name, part in parts.items():
      with self.subTest(part=name):
        self.assertEqual(list(part), ["revision", "fingerprint", "value"])

  def test_Snapshot_Since_MatchingRevision_IsUnchangedWithNoValue(self):
    repo = self.fixture()
    first = self.snapshot(repo)
    path = self.scratch("reply.json", json.dumps(first))
    second = self.snapshot(repo, "--since", path)
    project, before = second["projects"][0], first["projects"][0]
    self.assertEqual(list(project), ["root", "revision", "parts", "error"])
    self.assertEqual(project["revision"], before["revision"])
    self.assertEqual(list(project["parts"]), list(before["parts"]))
    for name, part in project["parts"].items():
      with self.subTest(part=name):
        self.assertEqual(list(part), ["revision", "fingerprint", "unchanged"])
        self.assertEqual(part, {"revision": before["parts"][name]["revision"],
                                "fingerprint": before["parts"][name]["fingerprint"],
                                "unchanged": True})

  def test_Snapshot_Since_ChangedPart_GivesFullValue(self):
    repo = self.fixture()
    first = self.snapshot(repo)["projects"][0]
    path = self.scratch("reply.json", json.dumps({"projects": [first]}))
    self.assertEqual(repo.run("set", "S01", "--status", "done")[0], 0)
    self.assertEqual(repo.run("render")[0], 0)  # keeps `health` as it was
    parts = self.snapshot(repo, "--since", path)["projects"][0]["parts"]
    for name in ("items", "status"):
      with self.subTest(part=name):
        self.assertEqual(list(parts[name]), ["revision", "fingerprint", "value"])
        self.assertNotEqual(parts[name]["revision"], first["parts"][name]["revision"])
        self.assertEqual(parts[name]["revision"], snapshot_module.revision(parts[name]["value"]))
    # `goals` reads the index that `set` rewrote, so its fingerprint moved and it was built;
    # its revision still matched, so only the value is dropped.
    self.assertNotEqual(parts["goals"]["fingerprint"], first["parts"]["goals"]["fingerprint"])
    for name in ("goals", "runs", "health"):
      with self.subTest(part=name):
        self.assertEqual(list(parts[name]), ["revision", "fingerprint", "unchanged"])
        self.assertEqual(parts[name]["revision"], first["parts"][name]["revision"])
        self.assertTrue(parts[name]["unchanged"])

  def test_Snapshot_Since_MatchesByRoot(self):
    repo = self.fixture()
    other = repo.root / "other"
    make_project(other, "Other")
    goals = self.scratch("goals.md", "Other goals")
    self.assertEqual(slicer("--root", str(other), "prose", "edit", "goals", "--file", goals)[0], 0)
    roots = ("--root", str(other), "--root", str(repo.root))
    code, out, err = slicer("snapshot", "--json", *roots)
    self.assertEqual(code, 0, err)
    by_root = {p["root"]: p for p in json.loads(out)["projects"]}
    self.assertNotEqual(by_root[str(other)]["parts"]["goals"]["value"],
                        by_root[str(repo.root)]["parts"]["goals"]["value"])
    # Root A's revisions, filed under root B's name: B gets every value.
    swapped = {"projects": [{"root": str(other), "parts": by_root[str(repo.root)]["parts"]}]}
    path = self.scratch("swapped.json", json.dumps(swapped))
    code, out, err = slicer("snapshot", "--json", "--root", str(other), "--since", path)
    self.assertEqual(code, 0, err)
    parts = json.loads(out)["projects"][0]["parts"]
    self.assertEqual(list(parts["goals"]), ["revision", "fingerprint", "value"])
    self.assertEqual(parts["goals"]["value"]["goals"], "Other goals")
    # Two roots now hold equal goals. Only root A is in the file, so root B is not
    # unchanged merely because its part name and revision match root A's.
    goals = self.scratch("same.md", "Same goals")
    for root in (other, repo.root):
      self.assertEqual(slicer("--root", str(root), "prose", "edit", "goals", "--file", goals)[0], 0)
    code, out, err = slicer("snapshot", "--json", "--parts", "goals", *roots)
    self.assertEqual(code, 0, err)
    held = {p["root"]: p for p in json.loads(out)["projects"]}
    self.assertEqual(held[str(other)]["parts"]["goals"]["revision"],
                     held[str(repo.root)]["parts"]["goals"]["revision"])
    path = self.scratch("only-a.json", json.dumps({"projects": [held[str(repo.root)]]}))
    code, out, err = slicer("snapshot", "--json", "--parts", "goals", "--since", path, *roots)
    self.assertEqual(code, 0, err)
    b, a = (p["parts"]["goals"] for p in json.loads(out)["projects"])
    self.assertEqual(list(b), ["revision", "fingerprint", "value"])
    self.assertEqual(b["value"]["goals"], "Same goals")
    self.assertEqual(a, {"revision": held[str(repo.root)]["parts"]["goals"]["revision"],
                         "fingerprint": held[str(repo.root)]["parts"]["goals"]["fingerprint"],
                         "unchanged": True})

  def test_Snapshot_Since_UnknownRootOrPart_GivesFullValue(self):
    repo = self.fixture()
    other = repo.root / "other"
    third = repo.root / "third"
    make_project(other, "Other")
    make_project(third, "Third")
    names = "items,goals,status"
    code, out, err = slicer("snapshot", "--json", "--parts", names, "--root", str(repo.root),
                            "--root", str(other), "--root", str(third))
    self.assertEqual(code, 0, err)
    here, there, _ = json.loads(out)["projects"]
    shaped = dict(here["parts"])  # goals as held; the rest unusable
    shaped["items"] = {"revision": None}
    shaped["status"] = {"revision": 5}
    shaped["bad"] = "not a part"
    failed = dict(there, error={"code": "corrupt", "message": "x", "command": "snapshot"})
    path = self.scratch("odd.json", json.dumps({"projects": [
      dict(here, parts=shaped), failed, "not a project", {"parts": here["parts"]},
    ]}))
    code, out, err = slicer("snapshot", "--json", "--parts", names, "--since", path,
                            "--root", str(repo.root), "--root", str(other), "--root", str(third))
    self.assertEqual(code, 0, err)
    first, second, last = json.loads(out)["projects"]
    self.assertEqual(list(first["parts"]["items"]), ["revision", "fingerprint", "value"])
    self.assertEqual(list(first["parts"]["status"]), ["revision", "fingerprint", "value"])
    self.assertEqual(first["parts"]["goals"], {"revision": here["parts"]["goals"]["revision"],
                                               "fingerprint": here["parts"]["goals"]["fingerprint"],
                                               "unchanged": True})
    for project in (second, last):
      for name, part in project["parts"].items():
        with self.subTest(root=project["root"], part=name):
          self.assertEqual(list(part), ["revision", "fingerprint", "value"])

  def test_Snapshot_Since_Stdin(self):
    repo = self.fixture()
    names = "items,goals,runs,status,health,deps"
    first = self.snapshot(repo, "--parts", names)
    with patch("sys.stdin", io.StringIO(json.dumps(first))):
      second = self.snapshot(repo, "--parts", names, "--since", "-")
    self.assertEqual(list(second["projects"][0]["parts"]), names.split(","))
    for name, part in second["projects"][0]["parts"].items():
      with self.subTest(part=name):
        held = first["projects"][0]["parts"][name]
        self.assertEqual(part, {"revision": held["revision"], "fingerprint": held["fingerprint"],
                                "unchanged": True})

  def test_Snapshot_Since_Unreadable_IsUsage(self):
    repo = self.fixture()
    missing = str(repo.root.parent / "no-such-reply.json")
    cases = [(missing, missing)]
    for name, text in (("garbage.json", "garbage{"), ("empty.json", ""), ("list.json", "[]"),
                       ("none.json", "{}"), ("scalar.json", '{"projects": 3}')):
      path = self.scratch(name, text)
      cases.append((path, path))
    for path, named in cases:
      with self.subTest(file=named):
        with patch.object(store, "load", side_effect=AssertionError("project read")):
          code, out, err = repo.run("snapshot", "--json", "--since", path)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("usage", err)
        self.assertIn(named, err)
    with patch("sys.stdin", io.StringIO("garbage{")):
      code, out, err = repo.run("snapshot", "--json", "--since", "-")
    self.assertEqual((code, out), (2, ""))
    self.assertIn("stdin", err)

  FINGERPRINTED = "goals,deps,items,status"
  EVERY_PART = "items,goals,runs,status,health,deps"

  def fingerprints(self, repo, names=EVERY_PART):
    parts = self.snapshot(repo, "--parts", names)["projects"][0]["parts"]
    return {name: part["fingerprint"] for name, part in parts.items()}

  def bump_mtime(self, path):
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))

  def loads(self):
    """Run `store.load` for real while counting the calls."""
    return patch.object(store, "load", wraps=store.load)

  def test_Snapshot_Since_UnchangedInputs_SkipsStoreLoad(self):
    repo = self.fixture()
    first = self.snapshot(repo, "--parts", self.FINGERPRINTED)["projects"][0]["parts"]
    path = self.scratch("reply.json", json.dumps({"projects": [
      {"root": str(repo.root), "parts": first, "error": None}]}))
    with patch.object(store, "load", side_effect=AssertionError("project read")):
      second = self.snapshot(repo, "--parts", self.FINGERPRINTED, "--since", path)
    parts = second["projects"][0]["parts"]
    self.assertEqual(list(parts), self.FINGERPRINTED.split(","))
    for name, part in parts.items():
      with self.subTest(part=name):
        self.assertRegex(first[name]["fingerprint"], r"^[0-9a-f]{12}$")
        self.assertEqual(part, {"revision": first[name]["revision"],
                                "fingerprint": first[name]["fingerprint"], "unchanged": True})

  def test_Snapshot_Fingerprint_TouchedInputChanges(self):
    repo = self.fixture()
    before = self.fingerprints(repo)
    self.bump_mtime(repo.root / ".slicer" / "index.json")  # content untouched
    after = self.fingerprints(repo)
    self.assertNotEqual(before["goals"], after["goals"])
    self.assertEqual(self.fingerprints(repo), after)  # stable while nothing moves

  def test_Snapshot_Fingerprint_SliceFileChangeChangesItems(self):
    repo = self.fixture()
    repo.write(".slicer/slices/sub/one.txt", "first")
    before = self.fingerprints(repo)
    repo.write(".slicer/slices/sub/one.txt", "second")  # nested, and a different size
    after = self.fingerprints(repo)
    for name in ("items", "status"):
      with self.subTest(part=name):
        self.assertNotEqual(before[name], after[name])
    for name in ("goals", "deps"):
      with self.subTest(part=name):
        self.assertEqual(before[name], after[name])

  def test_Snapshot_Fingerprint_SiblingWorktreeIndexChangesItems(self):
    repo = self.fixture(git=True)
    repo.commit("project")
    sibling = repo.root / "siblings" / "other"
    made = repo._git("worktree", "add", "-q", "-b", "other", str(sibling), "HEAD")
    self.assertEqual(made.returncode, 0, made.stderr)
    for name in ("index.json", "config.json"):
      self.assertTrue((sibling / ".slicer" / name).is_file())
    before = self.fingerprints(repo)
    self.bump_mtime(sibling / ".slicer" / "index.json")
    after = self.fingerprints(repo)
    for name in ("items", "status"):
      with self.subTest(part=name):
        self.assertNotEqual(before[name], after[name])
    for name in ("goals", "deps"):
      with self.subTest(part=name):
        self.assertEqual(before[name], after[name])
    self.bump_mtime(sibling / ".slicer" / "config.json")
    self.assertNotEqual(self.fingerprints(repo)["items"], after["items"])

  def test_Snapshot_Fingerprint_SameSizeRewriteSameMtime_Changes(self):
    repo = self.fixture()
    before = self.fingerprints(repo)
    index = repo.root / ".slicer" / "index.json"
    stat = index.stat()
    swap = index.with_name("index.swap")
    swap.write_bytes(index.read_bytes())
    os.utime(swap, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    os.replace(swap, index)  # same path, size and mtime; a new file
    moved = index.stat()
    self.assertEqual((moved.st_size, moved.st_mtime_ns), (stat.st_size, stat.st_mtime_ns))
    self.assertNotEqual(moved.st_ino, stat.st_ino)
    after = self.fingerprints(repo)
    for name in self.FINGERPRINTED.split(","):
      with self.subTest(part=name):
        self.assertNotEqual(before[name], after[name])

  def test_Snapshot_Fingerprint_ChangesWithVersion(self):
    repo = self.fixture()
    before = self.fingerprints(repo)
    with patch("slicer.__version__", "0.0.0-other"):
      after = self.fingerprints(repo)
    for name in self.FINGERPRINTED.split(","):
      with self.subTest(part=name):
        self.assertNotEqual(before[name], after[name])
    self.assertEqual(self.fingerprints(repo), before)

  def test_Snapshot_Fingerprint_ChangesWithPartName(self):
    repo = self.fixture()
    fingerprints = self.fingerprints(repo, self.FINGERPRINTED)
    # `goals` and `deps` read the same two files, so only the part name tells them apart.
    self.assertNotEqual(fingerprints["goals"], fingerprints["deps"])

  def test_Snapshot_AlwaysBuiltParts_RunsAndHealth(self):
    repo = self.fixture()
    first = self.snapshot(repo, "--parts", self.EVERY_PART)["projects"][0]["parts"]
    for name in ("runs", "health"):
      self.assertIsNone(first[name]["fingerprint"])
    for name in ("goals", "deps", "items", "status"):
      self.assertIsInstance(first[name]["fingerprint"], str)
    path = self.scratch("reply.json", json.dumps({"projects": [
      {"root": str(repo.root), "parts": first, "error": None}]}))
    self.assertFalse(first["health"]["value"]["merge_in_progress"])
    with patch.object(vcs, "merge_in_progress", return_value=True):
      with self.loads() as loaded:
        second = self.snapshot(repo, "--parts", self.EVERY_PART, "--since", path)
    parts = second["projects"][0]["parts"]
    loaded.assert_called_once()  # the always-built parts need the store
    self.assertEqual(parts["health"]["value"], {"render_stale": False, "merge_in_progress": True})
    self.assertEqual(list(parts["health"]), ["revision", "fingerprint", "value"])
    self.assertIsNone(parts["health"]["fingerprint"])
    self.assertEqual(parts["runs"], {"revision": first["runs"]["revision"], "fingerprint": None,
                                     "unchanged": True})

  def test_Snapshot_Revision_MeaningUnchanged(self):
    repo = self.fixture()
    first = self.snapshot(repo, "--parts", self.EVERY_PART)
    for name, part in first["projects"][0]["parts"].items():
      with self.subTest(part=name):
        self.assertEqual(part["revision"], snapshot_module.revision(part["value"]))
    path = self.scratch("reply.json", json.dumps(first))
    # Answered from the fingerprint once there is one, from the revision before; either way
    # it carries the revision a run without `--since` prints.
    answered = self.snapshot(repo, "--parts", self.FINGERPRINTED, "--since", path)
    fresh = self.snapshot(repo, "--parts", self.FINGERPRINTED)
    for part in answered["projects"][0]["parts"].values():
      self.assertTrue(part["unchanged"])
    for name, part in answered["projects"][0]["parts"].items():
      with self.subTest(part=name):
        self.assertEqual(part["revision"], fresh["projects"][0]["parts"][name]["revision"])
        self.assertEqual(part["revision"], snapshot_module.revision(
          fresh["projects"][0]["parts"][name]["value"]))

  def test_Snapshot_Since_FingerprintFirstThenRevision(self):
    repo = self.fixture()
    names = "goals,items"
    first = self.snapshot(repo, "--parts", names)["projects"][0]
    def reply(**edits):
      parts = {name: dict(part) for name, part in first["parts"].items()}
      for name, change in edits.items():
        parts[name].update(change)
      return self.scratch("reply.json", json.dumps({"projects": [dict(first, parts=parts)]}))

    # A wrong held fingerprint still reads the store, then falls back to the revision.
    wrong = reply(goals={"fingerprint": "0" * 12})
    with self.loads() as loaded:
      parts = self.snapshot(repo, "--parts", names, "--since", wrong)["projects"][0]["parts"]
    loaded.assert_called_once()
    for name in ("goals", "items"):
      self.assertEqual(parts[name], {"revision": first["parts"][name]["revision"],
                                     "fingerprint": first["parts"][name]["fingerprint"],
                                     "unchanged": True})
    # Wrong fingerprint and wrong revision: the value comes back.
    both = reply(goals={"fingerprint": "0" * 12, "revision": "0" * 12})
    parts = self.snapshot(repo, "--parts", names, "--since", both)["projects"][0]["parts"]
    self.assertEqual(list(parts["goals"]), ["revision", "fingerprint", "value"])
    self.assertEqual(parts["goals"]["revision"], first["parts"]["goals"]["revision"])
    # A matching fingerprint is not enough without a string revision to hand back.
    for bad in (None, 5, ["x"]):
      with self.subTest(revision=bad):
        with self.loads() as loaded:
          parts = self.snapshot(repo, "--parts", names,
                                "--since", reply(goals={"revision": bad}))["projects"][0]["parts"]
        loaded.assert_called_once()
        self.assertEqual(list(parts["goals"]), ["revision", "fingerprint", "value"])
        self.assertEqual(parts["goals"]["revision"], first["parts"]["goals"]["revision"])
        self.assertEqual(list(parts["items"]), ["revision", "fingerprint", "unchanged"])
    # A held fingerprint that is not a string never matches.
    for bad in (None, 7):
      with self.subTest(fingerprint=bad):
        with self.loads() as loaded:
          self.snapshot(repo, "--parts", names, "--since", reply(goals={"fingerprint": bad}))
        loaded.assert_called_once()

  def test_Snapshot_Since_ReplyWithoutFingerprint_UsesRevision(self):
    repo = self.fixture()
    first = self.snapshot(repo, "--parts", self.EVERY_PART)["projects"][0]
    older = dict(first, parts={name: {"revision": part["revision"], "value": part["value"]}
                               for name, part in first["parts"].items()})
    path = self.scratch("s456.json", json.dumps({"projects": [older]}))
    with self.loads() as loaded:
      parts = self.snapshot(repo, "--parts", self.EVERY_PART, "--since", path)["projects"][0]["parts"]
    loaded.assert_called_once()
    for name, part in parts.items():
      with self.subTest(part=name):
        self.assertEqual(part, {"revision": first["parts"][name]["revision"],
                                "fingerprint": first["parts"][name]["fingerprint"],
                                "unchanged": True})

  def test_Snapshot_EveryPart_CarriesFingerprintKey(self):
    repo = self.fixture()
    first = self.snapshot(repo, "--parts", self.EVERY_PART)
    path = self.scratch("reply.json", json.dumps(first))
    second = self.snapshot(repo, "--parts", self.EVERY_PART, "--since", path)
    for reply, last in ((first, "value"), (second, "unchanged")):
      for name, part in reply["projects"][0]["parts"].items():
        with self.subTest(part=name, key=last):
          self.assertEqual(list(part), ["revision", "fingerprint", last])
    fingerprinted = {name for name, part in first["projects"][0]["parts"].items()
                     if part["fingerprint"] is not None}
    self.assertEqual(fingerprinted, set(self.FINGERPRINTED.split(",")))

  def test_Snapshot_SiblingInputMissing_NullFingerprintAndPartStillBuilt(self):
    repo = self.fixture(git=True)
    repo.commit("project")
    sibling = repo.root / "siblings" / "other"
    made = repo._git("worktree", "add", "-q", "-b", "other", str(sibling), "HEAD")
    self.assertEqual(made.returncode, 0, made.stderr)
    (sibling / ".slicer" / "index.json").unlink()  # `store.sibling_work` skips this checkout
    parts = self.snapshot(repo, "--parts", "goals,items,status")["projects"][0]["parts"]
    self.assertIsInstance(parts["goals"]["fingerprint"], str)
    for name in ("items", "status"):
      with self.subTest(part=name):
        self.assertEqual(list(parts[name]), ["revision", "fingerprint", "value"])
        self.assertIsNone(parts[name]["fingerprint"])

  def test_Snapshot_UnreadableInput_BuildsAndReportsTheError(self):
    repo = self.fixture()
    first = self.snapshot(repo, "--parts", "goals")
    path = self.scratch("reply.json", json.dumps(first))
    (repo.root / ".slicer" / "index.json").unlink()
    code, out, _ = repo.run("snapshot", "--json", "--parts", "goals", "--since", path)
    self.assertNotEqual(code, 0)
    project = json.loads(out)["projects"][0]
    self.assertEqual((project["parts"], project["error"]["command"]), ({}, "snapshot"))
    self.assertIn("index.json", project["error"]["message"])

  def warned(self, *argv, silenced=False, probe=None):
    """Run `snapshot --json` with the foreign-worktree probe patched; return what it
    printed. tests/support.py silences the warning for the suite, so lift that here."""
    with patch.dict(os.environ, {cli._CODE_WARNING_ENV: "1"} if silenced else {}):
      if not silenced:
        os.environ.pop(cli._CODE_WARNING_ENV, None)
      with patch.object(vcs, "foreign_worktree",
                        side_effect=probe or (lambda root, code: "code is over there")) as seen:
        return slicer("snapshot", "--json", *argv), seen

  def test_Snapshot_ForeignWorktreeCode_WarnsOnStderr(self):
    repo = self.fixture()
    (quiet_code, quiet_out, quiet_err), _ = self.warned("--root", str(repo.root), silenced=True)
    (code, out, err), seen = self.warned("--root", str(repo.root))
    self.assertEqual(err.splitlines(), ["slicer: code is over there"])
    self.assertEqual((code, out), (quiet_code, quiet_out))
    self.assertEqual(quiet_err, "")
    self.assertEqual(code, 0)
    self.assertEqual(json.loads(out)["projects"][0]["root"], str(repo.root))
    self.assertEqual([call.args[0] for call in seen.call_args_list], [repo.root])

  def test_Snapshot_CodeWarningEnv_StaysSilent(self):
    repo = self.fixture()
    (_, _, loud), _ = self.warned("--root", str(repo.root))
    self.assertIn("slicer: code is over there", loud)
    (code, out, err), seen = self.warned("--root", str(repo.root), silenced=True)
    self.assertEqual((code, err), (0, ""))
    json.loads(out)
    seen.assert_not_called()  # silenced before the probe runs, as for every other command

  def test_Snapshot_SeveralRoots_WarnsOncePerMismatch(self):
    repo = self.fixture()
    other = repo.root / "other"
    make_project(other, "Other")
    (code, out, err), seen = self.warned(
      "--root", str(other), "--root", str(repo.root),
      probe=lambda root, code: f"code is over there from {root.name}")
    self.assertEqual(code, 0)
    self.assertEqual(len(json.loads(out)["projects"]), 2)
    # Five parts per project, yet one warning per root, in the order given.
    self.assertEqual(err.splitlines(), [f"slicer: code is over there from {other.name}",
                                        f"slicer: code is over there from {repo.root.name}"])
    self.assertEqual([call.args[0] for call in seen.call_args_list], [other, repo.root])
