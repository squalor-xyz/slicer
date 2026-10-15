"""`slicer relay-run` records slice-relay runs in a local ledger (s282).

The ledger, `.slicer/relay-runs.jsonl`, is gitignored, append-only and never rendered or
read by `check`. The tests drive the CLI in-process and read what `relay-run list --json`
returns; stale detection takes an injected clock. Tests that need a damaged ledger write
the fixture file directly, because production code never produces one.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import support  # noqa: F401 -- puts src/ on sys.path
from slicer import ops, relayrun

LEDGER = ".slicer/relay-runs.jsonl"
T0 = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)
# A begin event whose `run` is a list: valid JSON, wrong type.
WRONG_TYPES = {"v": 1, "event": "begin", "run": [], "at": "2026-10-14T12:00:00Z",
               "items": ["s1"], "worktree": "w"}


def minutes(n: int) -> datetime:
  return T0 + timedelta(minutes=n)


class RelayRunCase(unittest.TestCase):
  def project(self, git: bool = False) -> support.TempRepo:
    repo = support.TempRepo(git=git)
    self.addCleanup(repo.close)
    self.assertEqual(repo.run("init")[0], 0)
    return repo

  def ok(self, repo: support.TempRepo, *argv: str) -> dict:
    code, out, err = repo.run(*argv, "--json")
    self.assertEqual((code, err), (0, ""), f"{argv}: {out}")
    return json.loads(out)

  def refuse(self, repo: support.TempRepo, *argv: str, code: int, error: str) -> dict:
    got, out, err = repo.run(*argv, "--json")
    self.assertEqual(got, code, f"{argv}: {out} {err}")
    payload = json.loads(out)["error"]
    self.assertEqual(payload["code"], error)
    return payload

  def begin(self, repo: support.TempRepo, *ids: str) -> str:
    return self.ok(repo, "relay-run", "begin", *ids)["run"]["id"]

  def step(self, repo: support.TempRepo, run: str, *extra: str) -> dict:
    argv = ["relay-run", "step", run, "--role", "implementer", "--agent", "Claude Code",
            "--vendor", "Anthropic", "--model", "sonnet", "--reasoning", "high",
            "--phase", "start", *extra]
    return self.ok(repo, *argv)

  def runs(self, repo: support.TempRepo, *extra: str) -> list[dict]:
    return self.ok(repo, "relay-run", "list", *extra)["runs"]


class FoldTests(RelayRunCase):
  def test_Begin_Step_End_FoldsIntoOneRun(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1", "s2", "--worktree", "somewhere/wt-one")
    first = self.step(repo, run, "--task", "write the tests")["step"]["id"]
    self.ok(repo, "relay-run", "step", run, "--step", first, "--phase", "done")
    self.ok(
      repo, "relay-run", "step", run, "--role", "reviewer", "--agent", "Codex CLI",
      "--vendor", "OpenAI", "--model", "gpt", "--reasoning", "xhigh", "--phase", "start",
    )
    self.assertEqual(self.runs(repo)[0]["counts"]["running"], 1)
    self.ok(repo, "relay-run", "end", run, "--outcome", "pass")
    runs = self.runs(repo)
    self.assertEqual(len(runs), 1)
    got = runs[0]
    self.assertEqual(got["id"], run)
    self.assertEqual(got["items"], ["s1", "s2"])
    self.assertEqual(got["worktree"], "wt-one")
    self.assertEqual((got["state"], got["outcome"]), ("ended", "pass"))
    self.assertEqual([s["role"] for s in got["steps"]], ["implementer", "reviewer"])
    implementer, reviewer = got["steps"]
    self.assertEqual(
      {k: implementer[k] for k in ("agent", "vendor", "model", "reasoning", "phase", "task")},
      {"agent": "Claude Code", "vendor": "Anthropic", "model": "sonnet",
       "reasoning": "high", "phase": "done", "task": "write the tests"},
    )
    self.assertEqual((reviewer["agent"], reviewer["phase"]), ("Codex CLI", "start"))
    self.assertEqual(
      got["counts"],
      {"steps": 2, "running": 0, "subagents": 0, "roles": {"implementer": 1, "reviewer": 1}},
    )

  def test_Begin_NoWorktree_NamesTheCheckoutItRanIn(self) -> None:
    repo = self.project()
    self.begin(repo, "s1")
    self.assertEqual(self.runs(repo)[0]["worktree"], repo.root.name)

  def test_Step_WithParent_CountsAsSubagent(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    lead = self.step(repo, run)["step"]["id"]
    sub = self.step(repo, run, "--parent", lead, "--task", "one part")["step"]
    self.assertEqual(sub["parent"], lead)
    got = self.runs(repo)[0]
    self.assertEqual(got["counts"]["steps"], 2)
    self.assertEqual(got["counts"]["subagents"], 1)
    self.assertEqual([s["parent"] for s in got["steps"]], ["", lead])

  def test_Step_ParentNotInRun_Refused(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    self.refuse(
      repo, "relay-run", "step", run, "--role", "implementer", "--agent", "A",
      "--phase", "start", "--parent", "9", code=2, error="bad_run_field",
    )
    self.assertEqual(self.runs(repo)[0]["steps"], [])

  def test_Step_UnknownRun_Refused(self) -> None:
    repo = self.project()
    self.refuse(
      repo, "relay-run", "step", "run-nope", "--role", "implementer", "--agent", "A",
      "--phase", "start", code=2, error="unknown_run",
    )
    self.refuse(repo, "relay-run", "end", "run-nope", "--outcome", "pass", code=2, error="unknown_run")

  def test_Step_BadPhase_Refused(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    self.refuse(
      repo, "relay-run", "step", run, "--role", "implementer", "--agent", "A",
      "--phase", "running", code=2, error="bad_run_field",
    )
    self.assertEqual(self.runs(repo)[0]["steps"], [])

  def test_End_BadOutcome_Refused(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    self.refuse(repo, "relay-run", "end", run, "--outcome", "done", code=2, error="bad_run_field")
    self.assertEqual(self.runs(repo)[0]["state"], "active")

  def test_Step_NewStepWithoutAgent_Refused(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    self.refuse(
      repo, "relay-run", "step", run, "--role", "implementer", "--phase", "start",
      code=2, error="bad_run_field",
    )

  def test_Step_TaskWithNewline_Refused(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    self.refuse(
      repo, "relay-run", "step", run, "--role", "gate", "--agent", "A", "--phase", "start",
      "--task", "one\ntwo", code=2, error="bad_run_field",
    )

  def test_Step_UnknownStep_Refused(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    self.refuse(
      repo, "relay-run", "step", run, "--step", "4", "--phase", "done",
      code=2, error="bad_run_field",
    )

  def test_Step_AfterEnd_Refused(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    self.ok(repo, "relay-run", "end", run, "--outcome", "stopped")
    self.refuse(
      repo, "relay-run", "step", run, "--role", "gate", "--agent", "A", "--phase", "start",
      code=2, error="run_ended",
    )
    self.refuse(repo, "relay-run", "end", run, "--outcome", "pass", code=2, error="run_ended")
    self.assertEqual(self.runs(repo)[0]["outcome"], "stopped")

  def test_Counts_Roles_CountOnlyTopLevelSteps(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    lead = self.step(repo, run)["step"]["id"]
    self.step(repo, run, "--parent", lead)
    self.ok(
      repo, "relay-run", "step", run, "--role", "reviewer", "--agent", "A", "--phase", "start",
      "--parent", lead,
    )
    counts = self.runs(repo)[0]["counts"]
    self.assertEqual(counts["roles"], {"implementer": 1})
    self.assertEqual((counts["steps"], counts["subagents"]), (3, 2))

  def test_Counts_Running_IsZeroOnceTheRunEnded(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    self.step(repo, run)
    self.assertEqual(self.runs(repo)[0]["counts"]["running"], 1)
    self.ok(repo, "relay-run", "end", run, "--outcome", "stopped")
    got = self.runs(repo)[0]
    self.assertEqual(got["steps"][0]["phase"], "start")
    self.assertEqual(got["counts"]["running"], 0)

  def test_Run_SubagentsOf_ReturnsDirectChildrenInOrder(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    lead = self.step(repo, run)["step"]["id"]
    first = self.step(repo, run, "--parent", lead)["step"]["id"]
    grandchild = self.step(repo, run, "--parent", first)["step"]["id"]
    second = self.step(repo, run, "--parent", lead)["step"]["id"]
    got = relayrun.list_runs(repo.root, siblings=False)[0]
    self.assertEqual([s.id for s in got.subagents_of(lead)], [first, second])
    self.assertEqual([s.id for s in got.subagents_of(first)], [grandchild])
    self.assertEqual(got.subagents_of(second), [])
    self.assertEqual(got.subagents_of("99"), [])

  def test_Step_TaskWithLineSeparator_RoundTrips(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    task = "one\u2028two\u2029three\u0085four"
    self.step(repo, run, "--task", task)
    self.assertEqual(self.runs(repo)[0]["steps"][0]["task"], task)
    self.step(repo, run)  # the ledger still takes a write after it

  def test_Begin_WorktreeEmpty_Refused(self) -> None:
    repo = self.project()
    self.refuse(repo, "relay-run", "begin", "s1", "--worktree", "", code=2, error="bad_run_field")
    self.assertEqual(self.runs(repo), [])

  def test_Begin_IdCollidesWithExistingRun_DrawsAnotherId(self) -> None:
    repo = self.project()
    draws = iter(["aaaa0000", "aaaa0000", "bbbb0000"])
    with patch("slicer.ops.uuid4", side_effect=lambda: SimpleNamespace(hex=next(draws))):
      first = ops.relay_run_begin(repo.root, ["s1"], now=T0).id
      second = ops.relay_run_begin(repo.root, ["s2"], now=T0).id
    self.assertEqual(first, "run-20261014-120000-aaaa")
    self.assertEqual(second, "run-20261014-120000-bbbb")
    self.assertEqual(len(self.runs(repo)), 2)

  def test_Begin_BlankItem_Refused(self) -> None:
    repo = self.project()
    self.refuse(repo, "relay-run", "begin", "s1", " ", code=2, error="bad_run_field")
    self.assertEqual(self.runs(repo), [])

  def test_Begin_ItemWithWhitespace_Refused(self) -> None:
    repo = self.project()
    self.refuse(repo, "relay-run", "begin", "s1 s2", code=2, error="bad_run_field")
    self.assertEqual(self.runs(repo), [])

  def test_Step_UpdateWithParent_Refused(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    first = self.step(repo, run)["step"]["id"]
    self.refuse(
      repo, "relay-run", "step", run, "--step", first, "--phase", "done", "--parent", first,
      code=2, error="bad_run_field",
    )
    self.assertEqual(self.runs(repo)[0]["steps"][0]["phase"], "start")

  def test_Step_Update_ChangesOnlyTheFieldsGiven(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    first = self.step(repo, run, "--task", "original")["step"]["id"]
    self.ok(repo, "relay-run", "step", run, "--step", first, "--phase", "fail", "--model", "opus")
    step = self.runs(repo)[0]["steps"][0]
    self.assertEqual(
      (step["phase"], step["model"], step["task"], step["agent"], step["reasoning"]),
      ("fail", "opus", "original", "Claude Code", "high"),
    )

  def test_Run_RoundTrip_IsIdentityWithFixedKeyOrder(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    self.step(repo, run, "--task", "t")
    data = self.runs(repo)[0]
    self.assertEqual(
      list(data),
      ["id", "items", "worktree", "begun_at", "updated_at", "state", "outcome", "counts", "steps"],
    )
    self.assertEqual(
      list(data["steps"][0]),
      ["id", "role", "agent", "vendor", "model", "reasoning", "phase", "task", "parent",
       "begun_at", "updated_at"],
    )
    self.assertEqual(list(data["counts"]), ["steps", "running", "subagents", "roles"])
    self.assertEqual(relayrun.Run.from_dict(data).to_dict(), data)
    # A recorded run comes back from `list --json` whole.
    self.assertEqual(self.ok(repo, "relay-run", "list")["runs"], [data])

  def test_Step_Ledger_AppendsOnlyAndNeverRewritesEarlierLines(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    before = repo.read(LEDGER)
    self.step(repo, run)
    after = repo.read(LEDGER)
    self.assertTrue(after.startswith(before))
    self.assertEqual(len(after.splitlines()), len(before.splitlines()) + 1)


class ListTests(RelayRunCase):
  def test_List_Active_ExcludesEndedAndStale(self) -> None:
    repo = self.project()
    fresh = ops.relay_run_begin(repo.root, ["s1"], now=minutes(0)).id
    old = ops.relay_run_begin(repo.root, ["s2"], now=minutes(-90)).id
    done = ops.relay_run_begin(repo.root, ["s3"], now=minutes(-5)).id
    ops.relay_run_end(repo.root, done, "fail", now=minutes(-4))
    runs = {r.id: r for r in relayrun.list_runs(repo.root, now=minutes(1), siblings=False)}
    self.assertEqual(
      {fresh: runs[fresh].state, old: runs[old].state, done: runs[done].state},
      {fresh: "active", old: "stale", done: "ended"},
    )
    active = [r.id for r in relayrun.list_runs(repo.root, now=minutes(1), siblings=False, active=True)]
    self.assertEqual(active, [fresh])
    with patch.object(relayrun, "utcnow", return_value=minutes(1)):
      self.assertEqual([r["id"] for r in self.runs(repo, "--active")], [fresh])
      self.assertEqual(len(self.runs(repo)), 3)

  def test_List_StaleThreshold_IsThirtyMinutesSinceTheLastEvent(self) -> None:
    repo = self.project()
    run = ops.relay_run_begin(repo.root, ["s1"], now=minutes(0)).id

    def state(at: int) -> str:
      return relayrun.list_runs(repo.root, now=minutes(at), siblings=False)[0].state

    self.assertEqual(relayrun.STALE_AFTER, timedelta(minutes=30))
    self.assertEqual(state(30), "active")
    self.assertEqual(state(31), "stale")
    # A new event restarts the clock.
    ops.relay_run_step(
      repo.root, run, role="gate", agent="A", phase="start", now=minutes(31),
    )
    self.assertEqual(state(60), "active")
    self.assertEqual(state(62), "stale")

  def test_List_OrdersRunsByBeginTimeThenId(self) -> None:
    repo = self.project()
    late = ops.relay_run_begin(repo.root, ["s1"], now=minutes(5)).id
    early = ops.relay_run_begin(repo.root, ["s2"], now=minutes(1)).id
    got = [r.id for r in relayrun.list_runs(repo.root, now=minutes(6), siblings=False)]
    self.assertEqual(got, [early, late])

  def test_List_NoLedger_IsEmpty(self) -> None:
    repo = self.project()
    self.assertEqual(self.ok(repo, "relay-run", "list"), {"runs": []})
    code, out, err = repo.run("relay-run", "list")
    self.assertEqual((code, err), (0, ""))
    self.assertEqual(out.strip(), "no relay runs")

  def test_List_ReadsSiblingWorktreeLedger(self) -> None:
    repo = support.TempRepo(git=True)
    self.addCleanup(repo.close)
    with support.isolated_discovery(repo.root):
      self.assertEqual(repo.run("init")[0], 0)
      repo.commit("base")
      sibling = repo.root / "siblings" / "other"
      sibling.parent.mkdir()
      made = repo._git("worktree", "add", "-q", "-b", "other", str(sibling), "HEAD")
      self.assertEqual(made.returncode, 0, made.stderr)
      local = self.begin(repo, "s1")
      remote = ops.relay_run_begin(sibling, ["s2"]).id
      got = {r["id"]: r for r in self.runs(repo)}
      self.assertEqual(set(got), {local, remote})
      self.assertEqual(got[remote]["worktree"], "other")
      self.assertEqual(got[remote]["items"], ["s2"])
      mine = [r.id for r in relayrun.list_runs(repo.root, siblings=False)]
      self.assertEqual(mine, [local])

  def test_List_SiblingLedgerCorrupt_IsSkippedNotFatal(self) -> None:
    repo = support.TempRepo(git=True)
    self.addCleanup(repo.close)
    with support.isolated_discovery(repo.root):
      self.assertEqual(repo.run("init")[0], 0)
      repo.commit("base")
      sibling = repo.root / "siblings" / "broken"
      sibling.parent.mkdir()
      made = repo._git("worktree", "add", "-q", "-b", "broken", str(sibling), "HEAD")
      self.assertEqual(made.returncode, 0, made.stderr)
      (sibling / LEDGER).write_text("not json\n", encoding="utf-8")
      local = self.begin(repo, "s1")
      self.assertEqual([r["id"] for r in self.runs(repo)], [local])


  def test_List_SiblingLedgerWrongType_IsSkippedNotFatal(self) -> None:
    repo = support.TempRepo(git=True)
    self.addCleanup(repo.close)
    with support.isolated_discovery(repo.root):
      self.assertEqual(repo.run("init")[0], 0)
      repo.commit("base")
      sibling = repo.root / "siblings" / "typed"
      sibling.parent.mkdir()
      made = repo._git("worktree", "add", "-q", "-b", "typed", str(sibling), "HEAD")
      self.assertEqual(made.returncode, 0, made.stderr)
      (sibling / LEDGER).write_text(json.dumps(WRONG_TYPES) + "\n", encoding="utf-8")
      local = self.begin(repo, "s1")
      self.assertEqual([r["id"] for r in self.runs(repo)], [local])

  def test_List_SiblingLedgerEndsInTornLine_StillListsItsRuns(self) -> None:
    repo = support.TempRepo(git=True)
    self.addCleanup(repo.close)
    with support.isolated_discovery(repo.root):
      self.assertEqual(repo.run("init")[0], 0)
      repo.commit("base")
      sibling = repo.root / "siblings" / "busy"
      sibling.parent.mkdir()
      made = repo._git("worktree", "add", "-q", "-b", "busy", str(sibling), "HEAD")
      self.assertEqual(made.returncode, 0, made.stderr)
      remote = ops.relay_run_begin(sibling, ["s2"]).id
      path = sibling / LEDGER
      path.write_text(path.read_text(encoding="utf-8") + '{"v":1,"eve', encoding="utf-8")
      self.assertEqual([r["id"] for r in self.runs(repo)], [remote])


class LedgerTests(RelayRunCase):
  def test_Ledger_IsGitignored_AndNotInRender(self) -> None:
    repo = self.project(git=True)
    self.ok(repo, "render")
    repo.commit("base")
    render = repo.root / ".slicer" / "render"

    def snapshot() -> dict[str, bytes]:
      return {str(p.relative_to(render)): p.read_bytes() for p in render.rglob("*") if p.is_file()}

    before = snapshot()
    code_before = repo.run("check")[0]
    run = self.begin(repo, "s1")
    self.step(repo, run)
    self.ok(repo, "relay-run", "end", run, "--outcome", "pass")
    self.assertTrue((repo.root / LEDGER).is_file())
    self.assertEqual(snapshot(), before)
    self.assertEqual(repo.run("check")[0], code_before)
    status = repo._git("status", "--porcelain")
    self.assertEqual(status.stdout.splitlines(), [])
    ignored = repo._git("check-ignore", LEDGER)
    self.assertEqual(ignored.returncode, 0, ignored.stderr)

  def test_Ledger_RenderAndCheckNeverReadIt(self) -> None:
    repo = self.project()
    self.ok(repo, "render")
    (repo.root / LEDGER).write_text("garbage that is not json\n", encoding="utf-8")
    self.assertEqual(repo.run("check")[0], 0)
    self.assertEqual(repo.run("render")[0], 0)

  def test_Init_AddsLedgerToGitignore(self) -> None:
    repo = self.project()
    lines = repo.read(".slicer/.gitignore").splitlines()
    self.assertIn(relayrun.LEDGER_NAME, lines)
    self.assertEqual(relayrun.LEDGER_NAME, "relay-runs.jsonl")
    self.assertEqual(lines.count(relayrun.LEDGER_NAME), 1)

  def test_Begin_ProjectMadeBeforeLedger_AddsGitignoreLine(self) -> None:
    repo = self.project()
    path = repo.root / ".slicer" / ".gitignore"
    path.write_text("feedback.md\n", encoding="utf-8")
    self.begin(repo, "s1")
    self.begin(repo, "s2")
    self.assertEqual(path.read_text(encoding="utf-8").splitlines().count(relayrun.LEDGER_NAME), 1)
    self.assertIn("feedback.md", path.read_text(encoding="utf-8").splitlines())

  def test_Ledger_CorruptLine_ReportedWithFileAndLine(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    with (repo.root / LEDGER).open("a", encoding="utf-8") as fh:
      fh.write("{this is not json\n")
    payload = self.refuse(repo, "relay-run", "list", code=3, error="corrupt")
    self.assertIn("relay-runs.jsonl:2", payload["message"])
    self.refuse(
      repo, "relay-run", "step", run, "--role", "gate", "--agent", "A", "--phase", "start",
      code=3, error="corrupt",
    )

  def test_Ledger_UnknownEvent_ReportedWithFileAndLine(self) -> None:
    repo = self.project()
    self.begin(repo, "s1")
    with (repo.root / LEDGER).open("a", encoding="utf-8") as fh:
      fh.write(json.dumps({"v": 1, "event": "explode", "run": "x", "at": "2026-10-14T12:00:00Z"}) + "\n")
    payload = self.refuse(repo, "relay-run", "list", code=3, error="corrupt")
    self.assertIn("relay-runs.jsonl:2", payload["message"])

  def test_Ledger_StepForRunNeverBegun_ReportedWithFileAndLine(self) -> None:
    repo = self.project()
    event = {
      "v": 1, "event": "step", "run": "run-ghost", "at": "2026-10-14T12:00:00Z", "step": "1",
      "role": "gate", "agent": "A", "vendor": "", "model": "", "reasoning": "",
      "phase": "start", "task": "", "parent": "",
    }
    (repo.root / LEDGER).write_text(json.dumps(event) + "\n", encoding="utf-8")
    payload = self.refuse(repo, "relay-run", "list", code=3, error="corrupt")
    self.assertIn("relay-runs.jsonl:1", payload["message"])

  def test_Ledger_WrongTypes_ReportedWithFileAndLineLocally(self) -> None:
    repo = self.project()
    self.begin(repo, "s1")
    with (repo.root / LEDGER).open("a", encoding="utf-8") as fh:
      fh.write(json.dumps(WRONG_TYPES) + "\n")
    payload = self.refuse(repo, "relay-run", "list", code=3, error="corrupt")
    self.assertIn("relay-runs.jsonl:2", payload["message"])

  def test_Ledger_EachWrongType_IsCorruptNotACrash(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    good = {
      "v": 1, "event": "step", "run": run, "at": "2026-10-14T12:00:00Z", "step": "1",
      "role": "gate", "agent": "A", "vendor": "", "model": "", "reasoning": "",
      "phase": "start", "task": "", "parent": "",
    }
    for key, bad in (
      ("run", []), ("at", 5), ("step", 1), ("step", "x"), ("phase", None), ("role", 3),
      ("task", {}), ("parent", 1), ("event", []),
    ):
      with self.subTest(key=key, bad=bad):
        path = repo.root / LEDGER
        before = path.read_text(encoding="utf-8")
        path.write_text(before + json.dumps({**good, key: bad}) + "\n", encoding="utf-8")
        payload = self.refuse(repo, "relay-run", "list", code=3, error="corrupt")
        self.assertIn("relay-runs.jsonl:2", payload["message"])
        path.write_text(before, encoding="utf-8")
    for bad in ("s1", [1], None):
      with self.subTest(items=bad):
        path = repo.root / LEDGER
        before = path.read_text(encoding="utf-8")
        begin = {"v": 1, "event": "begin", "run": "other", "at": "2026-10-14T12:00:00Z",
                 "items": bad, "worktree": "w"}
        path.write_text(before + json.dumps(begin) + "\n", encoding="utf-8")
        self.refuse(repo, "relay-run", "list", code=3, error="corrupt")
        path.write_text(before, encoding="utf-8")

  def test_Begin_LedgerWrongType_RefusedAndLeftAsItWas(self) -> None:
    repo = self.project()
    (repo.root / LEDGER).write_text(json.dumps(WRONG_TYPES) + "\n", encoding="utf-8")
    before = repo.read(LEDGER)
    self.refuse(repo, "relay-run", "begin", "s1", code=3, error="corrupt")
    self.assertEqual(repo.read(LEDGER), before)

  def test_List_LedgerEndsInTornLine_IgnoresItAsAnAppendInProgress(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    path = repo.root / LEDGER
    path.write_text(path.read_text(encoding="utf-8") + '{"v":1,"event":"ste', encoding="utf-8")
    self.assertEqual([r["id"] for r in self.runs(repo)], [run])
    torn = path.read_bytes()
    # A writer still refuses it, and changes nothing.
    self.refuse(repo, "relay-run", "end", run, "--outcome", "pass", code=3, error="corrupt")
    self.assertEqual(path.read_bytes(), torn)

  def test_List_TornLineInTheMiddle_StillCorrupt(self) -> None:
    repo = self.project()
    self.begin(repo, "s1")
    path = repo.root / LEDGER
    path.write_text('{"v":1,"event":"ste\n' + path.read_text(encoding="utf-8"), encoding="utf-8")
    payload = self.refuse(repo, "relay-run", "list", code=3, error="corrupt")
    self.assertIn("relay-runs.jsonl:1", payload["message"])

  def test_Ledger_NewerSchema_IsRefused(self) -> None:
    repo = self.project()
    event = {"v": 9, "event": "begin", "run": "r", "at": "2026-10-14T12:00:00Z",
             "items": ["s1"], "worktree": "w"}
    (repo.root / LEDGER).write_text(json.dumps(event) + "\n", encoding="utf-8")
    self.refuse(repo, "relay-run", "list", code=3, error="schema_too_new")

  def test_Begin_LedgerLacksFinalNewline_KeepsNewEventOnItsOwnLine(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    path = repo.root / LEDGER
    path.write_text(path.read_text(encoding="utf-8").rstrip("\n"), encoding="utf-8")
    other = self.begin(repo, "s2")
    self.assertEqual(len(repo.read(LEDGER).splitlines()), 2)
    self.assertEqual([r["id"] for r in self.runs(repo)], sorted([run, other]))

  def test_Begin_LedgerEndsInTornLine_RefusedAndLeftAsItWas(self) -> None:
    repo = self.project()
    (repo.root / LEDGER).write_text('{"v":1,"event":"beg', encoding="utf-8")
    payload = self.refuse(repo, "relay-run", "begin", "s1", code=3, error="corrupt")
    self.assertIn("relay-runs.jsonl:1", payload["message"])
    self.assertEqual(repo.read(LEDGER), '{"v":1,"event":"beg')

  def test_RelayRun_IndexBroken_StillWorks(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    (repo.root / ".slicer" / "index.json").write_text("{broken", encoding="utf-8")
    self.assertEqual(self.runs(repo)[0]["id"], run)


if __name__ == "__main__":
  unittest.main()
