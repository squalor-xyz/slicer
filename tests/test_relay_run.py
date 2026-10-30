"""`slicer relay-run` records slice-relay runs in a local ledger (s282, s298).

The ledger is `<git common dir>/slicer/relay-runs.jsonl` in a git repo, shared by every
worktree, and `.slicer/relay-runs.jsonl` otherwise (and for a project below the worktree
root). It is append-only, never rendered and never read by `check`. The tests drive the CLI
in-process and read what `relay-run list --json` returns; stale detection takes an injected
clock. Tests that need a damaged ledger write
the fixture file directly, because production code never produces one.
"""

from __future__ import annotations

import errno
import io
import json
import os
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import support  # noqa: F401 -- puts src/ on sys.path
from slicer import ai, ops, relayrun, store
from slicer.cli import main
from slicer.errors import SlicerError

LEDGER = ".slicer/relay-runs.jsonl"
T0 = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)
# A begin event whose `run` is a list: valid JSON, wrong type.
WRONG_TYPES = {"v": 1, "event": "begin", "run": [], "at": "2026-10-14T12:00:00Z",
               "items": ["s1"], "worktree": "w"}


def minutes(n: int) -> datetime:
  return T0 + timedelta(minutes=n)


def without_running(err: str) -> str:
  """The stderr lines of a `relay-run end` that are not its running-steps warning (s326)."""
  return "".join(line for line in err.splitlines(True) if "with running steps" not in line)


class RelayRunCase(unittest.TestCase):
  def project(self, git: bool = False) -> support.TempRepo:
    repo = support.TempRepo(git=git)
    self.addCleanup(repo.close)
    # The fixture sits under TMPDIR, which may be inside a real worktree. Hide that repo, so
    # a ledger path that regressed to the git common dir could not write into the real one.
    self.enterContext(support.isolated_discovery(repo.root))
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
    code, _, err = repo.run("relay-run", "end", run, "--outcome", "pass", "--json")
    self.assertEqual((code, err), (0, f"slicer: ended {run} with running steps: 2\n"))
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
      {"steps": 2, "running": 0, "waiting": 0, "subagents": 0,
       "roles": {"implementer": 1, "reviewer": 1}},
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

  def test_Step_UnknownRole_RefusedWithTheAcceptedRoles(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    payload = self.refuse(
      repo, "relay-run", "step", run, "--role", "tester", "--agent", "A", "--phase", "start",
      code=2, error="bad_run_field",
    )
    self.assertIn(
      "implementer, reviewer, gate, architect, orchestrator, ui, spec-check", payload["message"],
    )
    self.assertEqual(self.runs(repo)[0]["steps"], [])

  def test_Step_UnknownRoleOnUpdate_RefusedAndKeepsTheRole(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    step = self.step(repo, run)["step"]["id"]
    self.refuse(
      repo, "relay-run", "step", run, "--step", step, "--role", "tester", "--phase", "done",
      code=2, error="bad_run_field",
    )
    self.assertEqual(self.runs(repo)[0]["steps"][0]["role"], "implementer")

  def test_Step_UiAndSpecCheckRoles_AcceptedAndCountedByRole(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    self.ok(repo, "relay-run", "step", run, "--role", "ui", "--agent", "Codex CLI",
            "--phase", "start", "--task", "UI pass on the board")
    self.ok(repo, "relay-run", "step", run, "--role", "spec-check", "--agent", "Codex CLI",
            "--phase", "done")
    got = self.runs(repo)[0]
    self.assertEqual([s["role"] for s in got["steps"]], ["ui", "spec-check"])
    self.assertEqual(got["counts"]["roles"], {"ui": 1, "spec-check": 1})
    code, out, err = repo.run("relay-run", "list")
    self.assertEqual((code, err), (0, ""), out)
    self.assertIn("spec-check", out)

  def test_Step_EveryLedgerRoleOfTheRolesJson_Accepted(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    ledger = [item["ledger_role"] for item in ai.relay_contract()["roles"] if item["ledger_role"]]
    self.assertEqual(
      ledger, ["orchestrator", "implementer", "reviewer", "architect", "ui", "spec-check"],
    )
    for role in ledger:
      with self.subTest(role=role):
        _, step = ops.relay_run_step(repo.root, run, phase="start", role=role, agent="A")
        self.assertEqual(step.role, role)

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
    code, _, err = repo.run("relay-run", "end", run, "--outcome", "stopped", "--json")
    self.assertEqual((code, err), (0, f"slicer: ended {run} with running steps: 1\n"))
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

  def test_RelayRunStep_Update_NewTask_Refused(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    first = self.step(repo, run, "--task", "S16 implement")["step"]["id"]
    before = repo.read(LEDGER)
    payload = self.refuse(
      repo, "relay-run", "step", run, "--step", first, "--phase", "start", "--task", "other",
      code=2, error="bad_run_field",
    )
    self.assertIn("a new step without --step", payload["message"])
    self.assertEqual(repo.read(LEDGER), before)
    self.assertEqual(self.runs(repo)[0]["steps"][0]["task"], "S16 implement")

  def test_RelayRunStep_Update_SameTask_Allowed(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    first = self.step(repo, run, "--task", "S16 implement")["step"]["id"]
    before = repo.read(LEDGER)
    self.ok(
      repo, "relay-run", "step", run, "--step", first, "--phase", "start",
      "--task", "S16 implement",
    )
    after = repo.read(LEDGER)
    self.assertTrue(after.startswith(before))
    self.assertEqual(len(after.splitlines()), len(before.splitlines()) + 1)
    step = self.runs(repo)[0]["steps"][0]
    self.assertEqual((step["phase"], step["task"]), ("start", "S16 implement"))

  def test_RelayRunStep_Heartbeat_NoTask_KeepsTask(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    first = self.step(repo, run, "--task", "S16 implement")["step"]["id"]
    self.ok(repo, "relay-run", "step", run, "--step", first, "--phase", "start")
    step = self.runs(repo)[0]["steps"][0]
    self.assertEqual((step["phase"], step["task"]), ("start", "S16 implement"))

  def test_RelayRunStep_Update_TaskOnTasklessStep_Filled(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    first = self.step(repo, run)["step"]["id"]
    self.ok(repo, "relay-run", "step", run, "--step", first, "--phase", "start", "--task", "x")
    self.assertEqual(self.runs(repo)[0]["steps"][0]["task"], "x")
    before = repo.read(LEDGER)
    self.refuse(
      repo, "relay-run", "step", run, "--step", first, "--phase", "start", "--task", "y",
      code=2, error="bad_run_field",
    )
    self.assertEqual(repo.read(LEDGER), before)
    self.assertEqual(self.runs(repo)[0]["steps"][0]["task"], "x")

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
      ["id", "role", "agent", "vendor", "model", "reasoning", "phase", "waiting_on", "task",
       "parent", "begun_at", "updated_at", "question", "reason"],
    )
    self.assertEqual(list(data["counts"]), ["steps", "running", "waiting", "subagents", "roles"])
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


class CloseStepsTests(RelayRunCase):
  """`relay-run end --close-steps` closes the steps still running before it ends the run (s326)."""

  def two_running_one_done(self, repo: support.TempRepo) -> str:
    run = self.begin(repo, "s1")
    self.step(repo, run, "--task", "write tests")
    done = self.step(repo, run, "--task", "old")["step"]["id"]
    self.ok(repo, "relay-run", "step", run, "--step", done, "--phase", "done")
    self.step(repo, run, "--task", "review")
    return run

  def events(self, repo: support.TempRepo) -> list[dict]:
    return [event for _, event in relayrun.read_events(repo.root / LEDGER)]

  def test_RelayRunEnd_CloseStepsDone_MarksRunningStepsDone(self) -> None:
    repo = self.project()
    run = self.two_running_one_done(repo)
    before = len(self.events(repo))
    code, out, err = repo.run("relay-run", "end", run, "--outcome", "pass", "--close-steps", "done")
    self.assertEqual((code, err), (0, ""), out)
    self.assertEqual(out.strip(), f"ended {run} pass; closed steps: 1, 3")
    added = self.events(repo)[before:]
    self.assertEqual([(e["event"], e.get("step"), e.get("phase")) for e in added],
                     [("step", "1", "done"), ("step", "3", "done"), ("end", None, None)])
    # A close names only the phase: the role, the agent and the task stay as recorded.
    self.assertTrue(all(e["role"] == e["task"] == e["parent"] == "" for e in added[:2]))
    got = self.runs(repo)[0]
    self.assertEqual([(s["id"], s["phase"], s["role"], s["task"]) for s in got["steps"]], [
      ("1", "done", "implementer", "write tests"), ("2", "done", "implementer", "old"),
      ("3", "done", "implementer", "review"),
    ])
    self.assertEqual(got["outcome"], "pass")

  def test_RelayRunEnd_CloseStepsDone_JsonNamesTheClosedSteps(self) -> None:
    repo = self.project()
    run = self.two_running_one_done(repo)
    payload = self.ok(repo, "relay-run", "end", run, "--outcome", "pass", "--close-steps", "done")
    self.assertEqual(payload["closed_steps"], ["1", "3"])
    self.assertEqual(payload["run"]["state"], "ended")

  def test_RelayRunEnd_CloseStepsFail_MarksThemFailed(self) -> None:
    repo = self.project()
    run = self.two_running_one_done(repo)
    payload = self.ok(repo, "relay-run", "end", run, "--outcome", "fail", "--close-steps", "fail")
    self.assertEqual(payload["closed_steps"], ["1", "3"])
    self.assertEqual([(s["id"], s["phase"]) for s in payload["run"]["steps"]],
                     [("1", "fail"), ("2", "done"), ("3", "fail")])

  def test_RelayRunEnd_CloseStepsNoneRunning_ClosesNothing(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    before = len(self.events(repo))
    payload = self.ok(repo, "relay-run", "end", run, "--outcome", "pass", "--close-steps", "done")
    self.assertEqual(payload["closed_steps"], [])
    self.assertEqual([e["event"] for e in self.events(repo)[before:]], ["end"])

  def test_RelayRunEnd_CloseStepsOtherPhase_Refused(self) -> None:
    repo = self.project()
    run = self.two_running_one_done(repo)
    before = repo.read(LEDGER)
    with self.assertRaises(SlicerError) as caught:
      ops.relay_run_end(repo.root, run, "pass", close_steps="start")
    self.assertEqual(caught.exception.code, "bad_run_field")
    self.assertEqual(repo.read(LEDGER), before)

  def test_RelayRunEnd_CloseStepsOtherPhaseCli_BadRunFieldAndNothingWritten(self) -> None:
    repo = self.project()
    run = self.two_running_one_done(repo)
    before = repo.read(LEDGER)
    self.refuse(repo, "relay-run", "end", run, "--outcome", "pass", "--close-steps", "bogus",
                code=2, error="bad_run_field")
    self.assertEqual(repo.read(LEDGER), before)

  def test_RelayRunEnd_RunningStepsWithoutFlag_WarnsOnStderr(self) -> None:
    repo = self.project()
    run = self.two_running_one_done(repo)
    before = len(self.events(repo))
    code, out, err = repo.run("relay-run", "end", run, "--outcome", "pass")
    self.assertEqual(code, 0, out)
    self.assertEqual(out.strip(), f"ended {run} pass")
    self.assertEqual(err.splitlines(), [f"slicer: ended {run} with running steps: 1, 3"])
    self.assertEqual([e["event"] for e in self.events(repo)[before:]], ["end"])

  def test_RelayRunEnd_NoRunningStepsWithoutFlag_StaysSilent(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    got = self.step(repo, run)["step"]["id"]
    self.ok(repo, "relay-run", "step", run, "--step", got, "--phase", "done")
    payload = self.ok(repo, "relay-run", "end", run, "--outcome", "pass")
    self.assertEqual(payload["closed_steps"], [])

  def test_RelayRunEnd_CloseSteps_AllOrNothingUnderLock(self) -> None:
    repo = self.project()
    run = self.two_running_one_done(repo)
    real = relayrun.append
    calls: list[str] = []

    def failing(root, event, **kwargs):
      calls.append(str(event["event"]))
      if len(calls) == 2:  # the second close
        raise OSError(errno.EIO, "disk")
      return real(root, event, **kwargs)

    with patch.object(relayrun, "append", failing):
      with self.assertRaises(OSError):
        ops.relay_run_end(repo.root, run, "pass", close_steps="done")
    self.assertEqual(calls, ["step", "step"])  # the end event was never attempted
    self.assertNotIn("end", [e["event"] for e in self.events(repo)])
    got = self.runs(repo)[0]
    self.assertEqual((got["state"], got["outcome"]), ("active", ""))


class ListTests(RelayRunCase):
  def test_RelayRun_List_WithRunId_OnlyThatRun(self) -> None:
    repo = self.project()
    self.begin(repo, "s1")
    second = self.begin(repo, "s2")
    expected = next(run for run in self.runs(repo) if run["id"] == second)
    self.assertEqual(self.ok(repo, "relay-run", "list", second), {"runs": [expected]})

  def test_RelayRun_List_WithRunId_TextOnlyThatRun(self) -> None:
    repo = self.project()
    first = self.begin(repo, "s1")
    self.step(repo, first, "--task", "first-only-task")
    second = self.begin(repo, "s2")
    self.step(repo, second, "--task", "second-only-task")
    code, out, err = repo.run("relay-run", "list", second)
    self.assertEqual((code, err), (0, ""))
    self.assertEqual(out.splitlines(), [
      f"{second}  active  items s2  wt {repo.root.name}  1 step, 1 running, 0 subagents",
      "  1  implementer  Claude Code  sonnet  high  start  second-only-task",
    ])
    self.assertNotIn(first, out)
    self.assertNotIn("first-only-task", out)

  def test_RelayRun_List_UnknownRunId_UnknownRun(self) -> None:
    repo = self.project(git=True)
    repo.commit("base")
    sibling = repo.root / "siblings" / "other"
    sibling.parent.mkdir()
    made = repo._git("worktree", "add", "-q", "-b", "other", str(sibling), "HEAD")
    self.assertEqual(made.returncode, 0, made.stderr)
    for extra in ([], ["--active"]):
      with self.subTest(extra=extra):
        error = self.refuse(repo, "relay-run", "list", "run-nope", *extra,
                            code=2, error="unknown_run")
        self.assertIn("run-nope", error["message"])
        for path in (relayrun.ledger_path(repo.root), repo.root / LEDGER, sibling / LEDGER):
          self.assertIn(str(path), error["message"])

  def test_RelayRun_List_RunIdFromSiblingWorktree_Found(self) -> None:
    repo = self.project(git=True)
    repo.commit("base")
    sibling = repo.root / "siblings" / "other"
    sibling.parent.mkdir()
    made = repo._git("worktree", "add", "-q", "-b", "other", str(sibling), "HEAD")
    self.assertEqual(made.returncode, 0, made.stderr)
    self.begin(repo, "s1")
    remote = "run-old-other"
    (sibling / LEDGER).write_text(legacy_begin(remote, "s2", "other"), encoding="utf-8")
    runs = self.runs(repo, remote)
    self.assertEqual([run["id"] for run in runs], [remote])
    self.assertEqual((runs[0]["items"], runs[0]["worktree"]), (["s2"], "other"))

  def test_RelayRun_List_WithRunId_ActiveFiltersKnownRuns(self) -> None:
    repo = self.project()
    fresh = ops.relay_run_begin(repo.root, ["s1"], now=minutes(0)).id
    stale = ops.relay_run_begin(repo.root, ["s2"], now=minutes(-90)).id
    ended = ops.relay_run_begin(repo.root, ["s3"], now=minutes(-5)).id
    ops.relay_run_end(repo.root, ended, "pass", now=minutes(-4))
    with patch.object(relayrun, "utcnow", return_value=minutes(1)):
      self.assertEqual([run["id"] for run in self.runs(repo, fresh, "--active")], [fresh])
      for run in (stale, ended):
        with self.subTest(run=run):
          self.assertEqual(self.ok(repo, "relay-run", "list", run, "--active"), {"runs": []})

  def test_List_Text_LabelsItemsAndWorktree_AndSingularisesOneStepAndOneSubagent(self) -> None:
    repo = self.project()
    one = self.begin(repo, "s283", "--worktree", ".worktrees/s283")
    first = self.step(repo, one)["step"]["id"]
    self.ok(repo, "relay-run", "step", one, "--step", first, "--phase", "done")
    self.ok(repo, "relay-run", "end", one, "--outcome", "pass")
    two = self.begin(repo, "s284", "--worktree", ".worktrees/s284")
    lead = self.step(repo, two)["step"]["id"]
    self.step(repo, two, "--parent", lead)
    self.begin(repo, "s285", "--worktree", ".worktrees/s285")
    code, out, err = repo.run("relay-run", "list")
    self.assertEqual((code, err), (0, ""))
    headers = [line for line in out.splitlines() if not line.startswith(" ")]
    # Runs begun within one second tie on time, so their order is by a random id: compare sorted.
    self.assertEqual(sorted(line.partition("  items ")[2] for line in headers), [
      "s283  wt s283  1 step, 0 running, 0 subagents",
      "s284  wt s284  2 steps, 2 running, 1 subagent",
      "s285  wt s285  0 steps, 0 running, 0 subagents",
    ], out)

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

  def test_List_ReadsSiblingWorktreeLegacyLedger(self) -> None:
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
      remote = "run-old-other"
      (sibling / LEDGER).write_text(legacy_begin(remote, "s2", "other"), encoding="utf-8")
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
      remote = "run-old-busy"
      (sibling / LEDGER).write_text(legacy_begin(remote, "s2", "busy") + '{"v":1,"eve', encoding="utf-8")
      self.assertEqual([r["id"] for r in self.runs(repo)], [remote])


def legacy_begin(run: str, item: str = "s1", worktree: str = "old") -> str:
  """One begin line as an older slicer wrote it into a worktree's own `.slicer/` ledger."""
  event = relayrun.begin_event(run, T0, [item], worktree)
  return json.dumps(event, ensure_ascii=False) + "\n"


class GitLedgerCase(RelayRunCase):
  """A fixture that is its own git repo, so it has a real common dir of its own."""

  def repo(self) -> support.TempRepo:
    repo = support.TempRepo(git=True)
    self.addCleanup(repo.close)
    self.enterContext(support.isolated_discovery(repo.root))
    self.assertEqual(repo.run("init")[0], 0)
    repo.commit("base")
    return repo

  def linked(self, repo: support.TempRepo, name: str) -> Path:
    path = repo.root / "siblings" / name
    path.parent.mkdir(exist_ok=True)
    made = repo._git("worktree", "add", "-q", "-b", name, str(path), "HEAD")
    self.assertEqual(made.returncode, 0, made.stderr)
    return path

  def common(self, repo: support.TempRepo) -> Path:
    return repo.root / ".git" / "slicer" / relayrun.LEDGER_NAME


class CommonLedgerTests(GitLedgerCase):
  """In a git repo the ledger lives in the common dir, so every worktree shares it."""

  def test_RelayRun_Begin_InLinkedWorktree_WritesCommonLedger(self) -> None:
    repo = self.repo()
    wt = self.linked(repo, "wt")
    # A project made before the ledger was listed: a legacy append would add the line.
    (wt / ".slicer" / ".gitignore").write_text("feedback.md\n", encoding="utf-8")
    ignore = (wt / ".slicer" / ".gitignore").read_text(encoding="utf-8")
    run = ops.relay_run_begin(wt, ["s1"]).id
    lines = self.common(repo).read_text(encoding="utf-8").splitlines()
    self.assertEqual([json.loads(line)["run"] for line in lines], [run])
    self.assertFalse((wt / LEDGER).exists())
    self.assertEqual((wt / ".slicer" / ".gitignore").read_text(encoding="utf-8"), ignore)

  def test_RelayRun_List_SurvivesWorktreeRemove(self) -> None:
    repo = self.repo()
    wt = self.linked(repo, "wt")
    run = ops.relay_run_begin(wt, ["s1"]).id
    gone = repo._git("worktree", "remove", "--force", str(wt))
    self.assertEqual(gone.returncode, 0, gone.stderr)
    self.assertEqual([r["id"] for r in self.runs(repo)], [run])

  def test_RelayRun_Step_FromAnotherWorktree_ExtendsRun(self) -> None:
    repo = self.repo()
    wt = self.linked(repo, "wt")
    run = ops.relay_run_begin(wt, ["s1"]).id
    self.step(repo, run, "--task", "from main")
    ops.relay_run_end(repo.root, run, "pass")
    got = self.runs(repo)
    self.assertEqual([(r["id"], r["outcome"]) for r in got], [(run, "pass")])
    self.assertEqual([s["task"] for s in got[0]["steps"]], ["from main"])

  def test_RelayRun_Step_UnknownRun_NamesCommonLedger(self) -> None:
    repo = self.repo()
    payload = self.refuse(
      repo, "relay-run", "step", "run-nope", "--role", "gate", "--agent", "A",
      "--phase", "start", code=2, error="unknown_run",
    )
    self.assertIn(str(self.common(repo)), payload["message"])

  def test_LedgerLock_Held_TimesOutWithLockedCode(self) -> None:
    if store.fcntl is None:
      self.skipTest("flock is unavailable")
    repo = self.repo()
    lock_path = self.common(repo).with_name(relayrun.COMMON_LOCK_NAME)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o666)
    try:
      store.fcntl.flock(fd, store.fcntl.LOCK_EX | store.fcntl.LOCK_NB)
      with patch.dict(os.environ, {store.LOCK_TIMEOUT_ENV: "0"}):
        payload = self.refuse(repo, "relay-run", "begin", "s1", code=3, error="locked")
    finally:
      store.fcntl.flock(fd, store.fcntl.LOCK_UN)
      os.close(fd)
    self.assertEqual(
      payload["message"],
      "another slicer is writing this project; the lock timed out after 0s. Wait for it "
      f"to finish, or remove {lock_path} if no slicer is running.",
    )

  def test_RelayRun_List_ReadsLegacyWorktreeLedger(self) -> None:
    repo = self.repo()
    wt = self.linked(repo, "wt")
    (wt / LEDGER).write_text(legacy_begin("run-old-wt", "s7"), encoding="utf-8")
    (repo.root / LEDGER).write_text(legacy_begin("run-old-here", "s8"), encoding="utf-8")
    got = self.runs(repo)
    self.assertEqual(sorted(r["id"] for r in got), ["run-old-here", "run-old-wt"])
    self.assertEqual({r["id"]: r["items"] for r in got}["run-old-wt"], ["s7"])

  def test_RelayRun_List_RunInCommonAndLegacyLedger_ListsOnce(self) -> None:
    repo = self.repo()
    wt = self.linked(repo, "wt")
    run = ops.relay_run_begin(wt, ["s1"]).id
    (wt / LEDGER).write_text(self.common(repo).read_text(encoding="utf-8"), encoding="utf-8")
    self.assertEqual([r["id"] for r in self.runs(repo)], [run])

  def test_RelayRun_List_CommonLedgerDamaged_IsCorrupt(self) -> None:
    repo = self.repo()
    ops.relay_run_begin(repo.root, ["s1"])
    with self.common(repo).open("a", encoding="utf-8") as fh:
      fh.write("{not json\n")
    payload = self.refuse(repo, "relay-run", "list", code=3, error="corrupt")
    self.assertIn("relay-runs.jsonl:2", payload["message"])

  def test_RelayRun_NoGit_KeepsSlicerLedger(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    self.assertEqual(json.loads(repo.read(LEDGER).splitlines()[0])["run"], run)
    self.assertEqual([r["id"] for r in self.runs(repo)], [run])

  def test_RelayRun_ProjectBelowWorktreeRoot_KeepsSlicerLedger(self) -> None:
    repo = support.TempRepo(git=True)
    self.addCleanup(repo.close)
    sub = repo.root / "sub"
    sub.mkdir()
    with support.isolated_discovery(repo.root):
      self.assertEqual(repo.run("--root", str(sub), "init")[0], 0)
      run = ops.relay_run_begin(sub, ["s1"]).id
      self.assertEqual(json.loads((sub / LEDGER).read_text(encoding="utf-8"))["run"], run)
      self.assertFalse((repo.root / ".git" / "slicer").exists())

  def test_RelayRun_ConcurrentAppends_FromTwoWorktrees_KeepEveryLine(self) -> None:
    repo = self.repo()
    trees = [self.linked(repo, "one"), self.linked(repo, "two")]
    inside, overlaps, errors = [0], [0], []
    real = relayrun.append

    def slow_append(root: Path, event: dict) -> None:
      inside[0] += 1
      overlaps[0] = max(overlaps[0], inside[0])
      time.sleep(0.01)  # long enough that an unlocked second writer lands inside
      try:
        real(root, event)
      finally:
        inside[0] -= 1

    def work(tree: Path) -> None:
      try:
        for _ in range(6):
          ops.relay_run_begin(tree, ["s1"])
      except Exception as exc:  # noqa: BLE001 -- reported by the assertion below
        errors.append(exc)

    with patch.object(relayrun, "append", slow_append):
      threads = [threading.Thread(target=work, args=(tree,)) for tree in trees]
      for thread in threads:
        thread.start()
      for thread in threads:
        thread.join()
    self.assertEqual(errors, [])
    self.assertEqual(overlaps[0], 1)
    events = relayrun.read_events(self.common(repo))
    self.assertEqual(len(events), 12)
    self.assertEqual(len({event["run"] for _, event in events}), 12)


class SandboxCase(GitLedgerCase):
  """A role that may write only inside its slice worktree still records its steps (s332).

  The sandbox is simulated on the fixture's own `<common>/slicer`: the directory and the
  files in it lose their write bits, which is what a workspace-write sandbox does to the
  main checkout's `.git/`. Never the real repository: the suite refuses writes there.
  """

  def sandbox(self, repo: support.TempRepo):
    """Make the shared ledger unwritable. Returns the function that lifts it again."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
      self.skipTest("root ignores file modes, so the sandbox cannot be simulated")
    shared = self.common(repo).parent
    paths = sorted(shared.iterdir())
    modes = {path: path.stat().st_mode for path in [shared, *paths]}
    for path in paths:
      path.chmod(0o444)
    shared.chmod(0o555)

    def lift() -> None:
      for path, mode in modes.items():  # the directory first, so its files can be reached
        path.chmod(mode)

    self.addCleanup(lift)
    # A simulation that refuses nothing would make every test below pass for no reason.
    with self.assertRaises(PermissionError):
      self.common(repo).open("ab").close()
    return lift

  def cli(self, tree: Path, *argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
      code = main(["--root", str(tree), *argv])
    return code, out.getvalue(), err.getvalue()

  def local(self, tree: Path) -> list[dict]:
    return [event for _, event in relayrun.read_events(tree / LEDGER)]

  def listed(self, tree: Path) -> list[dict]:
    code, out, err = self.cli(tree, "relay-run", "list", "--json")
    self.assertEqual((code, err), (0, ""), out)
    return json.loads(out)["runs"]

  def worktree_with_run(self, repo: support.TempRepo) -> tuple[Path, str]:
    wt = self.linked(repo, "wt")
    return wt, ops.relay_run_begin(wt, ["s1"], now=minutes(0)).id


class SandboxTests(SandboxCase):
  def test_RelayRunStep_SharedLedgerReadOnly_FallsBackToLocalAndWarns(self) -> None:
    repo = self.repo()
    wt, run = self.worktree_with_run(repo)
    shared = self.common(repo).read_bytes()
    self.sandbox(repo)
    code, out, err = self.cli(
      wt, "relay-run", "step", run, "--role", "reviewer", "--agent", "Codex CLI",
      "--phase", "start", "--json",
    )
    self.assertEqual(code, 0, out + err)
    self.assertRegex(json.loads(out)["step"]["id"], r"^c[0-9a-f]{8}-1$")
    self.assertEqual(len(err.splitlines()), 1, err)
    self.assertIn(str(self.common(repo)), err)
    self.assertIn(str(wt / LEDGER), err)
    local = self.local(wt)
    self.assertEqual([(e["event"], e["run"], e["role"]) for e in local], [("step", run, "reviewer")])
    self.assertEqual(self.common(repo).read_bytes(), shared)

  def test_RelayRunBegin_SharedDirUncreatable_BeginsLocally(self) -> None:
    repo = self.repo()
    wt = self.linked(repo, "wt")
    git_dir = repo.root / ".git"
    mode = git_dir.stat().st_mode
    git_dir.chmod(0o555)  # no `.git/slicer` yet, and none can be made
    self.addCleanup(git_dir.chmod, mode)
    code, out, err = self.cli(wt, "relay-run", "begin", "s1")
    self.assertEqual(code, 0, out + err)
    self.assertEqual([(e["event"], e["run"]) for e in self.local(wt)], [("begin", out.strip())])
    self.assertEqual(len(err.splitlines()), 1, err)
    self.assertFalse((git_dir / "slicer").exists())

  def test_RelayRunStep_Fallback_DoesNotEditGitignore(self) -> None:
    for where in ("main checkout", "linked worktree"):
      with self.subTest(where=where):
        repo = self.repo()
        tree = self.linked(repo, "wt") if where == "linked worktree" else repo.root
        run = ops.relay_run_begin(tree, ["s1"], now=minutes(0)).id
        # A project made before the ledger was listed: an ignore edit would add the line.
        ignore = tree / ".slicer" / ".gitignore"
        ignore.write_text("feedback.md\nlock\n", encoding="utf-8")
        exclude = repo.root / ".git" / "info" / "exclude"
        before = (ignore.read_bytes(), exclude.read_bytes() if exclude.exists() else None)
        self.sandbox(repo)
        code, out, err = self.cli(
          tree, "relay-run", "step", run, "--role", "gate", "--agent", "A", "--phase", "start",
        )
        self.assertEqual(code, 0, out + err)
        self.assertEqual(len(self.local(tree)), 1)  # the fallback did write
        after = (ignore.read_bytes(), exclude.read_bytes() if exclude.exists() else None)
        self.assertEqual(after, before)

  def test_RelayRunList_RunSpansSharedAndLocal_OneRunAllSteps(self) -> None:
    repo = self.repo()
    wt, run = self.worktree_with_run(repo)
    self.sandbox(repo)
    for task in ("write tests", "review"):
      ops.relay_run_step(
        wt, run, phase="start", role="implementer", agent="Codex CLI", task=task,
        now=minutes(1),
      )
    for tree in (wt, repo.root):
      with self.subTest(tree=tree.name):
        got = self.listed(tree)
        self.assertEqual([r["id"] for r in got], [run])
        self.assertEqual([s["task"] for s in got[0]["steps"]], ["write tests", "review"])
        self.assertEqual(got[0]["items"], ["s1"])

  def test_RelayRunList_EventsAcrossFiles_OrderedByTimeNoDuplicates(self) -> None:
    repo = self.repo()
    wt, run = self.worktree_with_run(repo)
    fields = {"role": "implementer", "agent": "Codex CLI"}
    ops.relay_run_step(wt, run, phase="start", now=minutes(1), **fields)
    ops.relay_run_step(wt, run, phase="done", step="1", now=minutes(5))
    self.sandbox(repo)
    ops.relay_run_step(wt, run, phase="start", now=minutes(3), **fields)
    ops.relay_run_step(wt, run, phase="fail", step="1", now=minutes(4))
    with (wt / LEDGER).open("a", encoding="utf-8") as fh:  # the shared step, copied again
      fh.write(self.common(repo).read_text(encoding="utf-8").splitlines()[1] + "\n")
    got = relayrun.list_runs(repo.root, now=minutes(6))
    self.assertEqual([r.id for r in got], [run])
    steps = [(s.id, s.phase) for s in got[0].steps]
    self.assertEqual(steps[0], ("1", "done"))
    self.assertRegex(steps[1][0], r"^c[0-9a-f]{8}-1$")
    self.assertEqual(steps[1][1], "start")
    self.assertEqual(got[0].updated_at, relayrun.stamp(minutes(5)))

  def test_RelayRunList_LocalLedgerStepForUnknownRun_IsSkippedNotFatal(self) -> None:
    repo = self.repo()
    wt, run = self.worktree_with_run(repo)
    step = relayrun.step_event("run-ghost", minutes(1), "1", "start", role="a", agent="A")
    (wt / LEDGER).write_text(json.dumps(step) + "\n", encoding="utf-8")
    self.assertEqual([r["id"] for r in self.listed(repo.root)], [run])

  def test_RelayRunStep_Fallback_LocalLedgerDamaged_RefusedAndLeftAsItWas(self) -> None:
    repo = self.repo()
    wt, run = self.worktree_with_run(repo)
    self.sandbox(repo)
    (wt / LEDGER).write_text('{"v":1,"event":"ste', encoding="utf-8")
    code, out, err = self.cli(
      wt, "relay-run", "step", run, "--role", "gate", "--agent", "A", "--phase", "start",
      "--json",
    )
    self.assertEqual(code, 3, out + err)
    self.assertEqual(json.loads(out)["error"]["code"], "corrupt")
    self.assertEqual((wt / LEDGER).read_text(encoding="utf-8"), '{"v":1,"event":"ste')

  def test_RelayRunStep_BeginOnlyInLocalLedger_SharedStepAbsorbsRunFirst(self) -> None:
    repo = self.repo()
    wt = self.linked(repo, "wt")
    ops.relay_run_begin(repo.root, ["s9"], now=minutes(0))  # the shared ledger exists
    lift = self.sandbox(repo)
    run = ops.relay_run_begin(wt, ["s1"], now=minutes(1)).id  # its begin can only go local
    lift()
    ops.relay_run_step(repo.root, run, phase="start", role="gate", agent="A", now=minutes(2))
    shared = [(e["event"], e["run"]) for _, e in relayrun.read_events(self.common(repo))]
    self.assertIn(("begin", run), shared)
    gone = repo._git("worktree", "remove", "--force", str(wt))
    self.assertEqual(gone.returncode, 0, gone.stderr)
    got = {r["id"]: r for r in self.listed(repo.root)}
    self.assertEqual([s["role"] for s in got[run]["steps"]], ["gate"])

  def test_RelayRunStep_SharedChildOfLocalParent_SharedLedgerReadableAfterWorktreeRemoved(self) -> None:
    repo = self.repo()
    wt, run = self.worktree_with_run(repo)  # the begin is in the shared ledger
    lift = self.sandbox(repo)
    _, parent = ops.relay_run_step(wt, run, phase="start", role="gate", agent="A", now=minutes(1))
    self.assertRegex(parent.id, r"^c[0-9a-f]{8}-1$")  # the sandboxed step went to the local file
    lift()
    _, child = ops.relay_run_step(
      repo.root, run, phase="start", role="reviewer", agent="B", parent=parent.id, now=minutes(2),
    )
    gone = repo._git("worktree", "remove", "--force", str(wt))
    self.assertEqual(gone.returncode, 0, gone.stderr)
    got = {r["id"]: r for r in self.listed(repo.root)}
    by_id = {s["id"]: s for s in got[run]["steps"]}
    self.assertEqual(sorted(by_id), sorted([parent.id, child.id]))
    self.assertEqual((by_id[parent.id]["role"], by_id[child.id]["role"]), ("gate", "reviewer"))
    self.assertEqual(by_id[child.id]["parent"], parent.id)

  def test_RelayRunStep_UpdateOfLocalOnlyStep_AbsorbsFirst(self) -> None:
    repo = self.repo()
    wt, run = self.worktree_with_run(repo)  # isolate this trigger from a missing begin
    lift = self.sandbox(repo)
    _, step = ops.relay_run_step(
      wt, run, phase="start", role="reviewer", agent="Codex CLI", now=minutes(1),
    )
    lift()
    self.cli(
      repo.root, "relay-run", "step", run, "--step", step.id, "--phase", "done",
    )
    (wt / LEDGER).unlink()
    got = self.listed(repo.root)[0]["steps"]
    self.assertEqual([(s["id"], s["role"], s["agent"], s["phase"]) for s in got],
                     [(step.id, "reviewer", "Codex CLI", "done")])

  def test_RelayRunStep_UpdateOfSharedStep_AbsorbsNothing(self) -> None:
    repo = self.repo()
    wt, run = self.worktree_with_run(repo)
    _, step = ops.relay_run_step(
      repo.root, run, phase="start", role="reviewer", agent="Codex CLI", now=minutes(1),
    )
    lift = self.sandbox(repo)
    ops.relay_run_step(wt, run, phase="start", role="gate", agent="A", now=minutes(2))
    lift()
    ops.relay_run_step(repo.root, run, step=step.id, phase="done", now=minutes(3))
    (wt / LEDGER).unlink()
    got = self.listed(repo.root)[0]["steps"]
    self.assertEqual([(s["id"], s["role"], s["agent"], s["phase"]) for s in got],
                     [(step.id, "reviewer", "Codex CLI", "done")])

  def test_RelayRunStep_SharedChildOfSharedParent_AbsorbsNothing(self) -> None:
    repo = self.repo()
    wt, run = self.worktree_with_run(repo)  # the begin is in the shared ledger
    lift = self.sandbox(repo)
    ops.relay_run_step(wt, run, phase="start", role="gate", agent="A", now=minutes(1))  # unrelated
    lift()
    self.assertEqual([e["role"] for e in self.local(wt) if e["event"] == "step"], ["gate"])
    _, parent = ops.relay_run_step(
      repo.root, run, phase="start", role="reviewer", agent="B", now=minutes(2),
    )
    report = ops.LedgerReport()
    _, child = ops.relay_run_step(
      repo.root, run, phase="start", role="implementer", agent="C", parent=parent.id,
      now=minutes(3), report=report,
    )
    self.assertEqual(report.absorbed, 0)
    shared = [e["step"] for _, e in relayrun.read_events(self.common(repo)) if e["event"] == "step"]
    self.assertEqual(shared, [parent.id, child.id])

  def test_RelayRunStep_SharedLedgerFileReadOnlyLockWritable_FallsBackToLocal(self) -> None:
    repo = self.repo()
    wt, run = self.worktree_with_run(repo)
    ledger = self.common(repo)
    mode = ledger.stat().st_mode
    ledger.chmod(0o444)  # the lock and the directory stay writable
    self.addCleanup(ledger.chmod, mode)
    before = ledger.read_bytes()
    code, out, err = self.cli(
      wt, "relay-run", "step", run, "--role", "gate", "--agent", "A", "--phase", "start",
    )
    self.assertEqual(code, 0, out + err)
    self.assertEqual(len(err.splitlines()), 1, err)
    self.assertEqual([e["event"] for e in self.local(wt)], ["step"])
    self.assertEqual(ledger.read_bytes(), before)

  def test_RelayRunStep_RunBegunInSiblingLocalLedger_ExtendsIt(self) -> None:
    for sandboxed in (False, True):
      with self.subTest(sandboxed=sandboxed):
        repo = self.repo()
        one, two = self.linked(repo, "one"), self.linked(repo, "two")
        ops.relay_run_begin(repo.root, ["s9"], now=minutes(0))  # the shared ledger exists
        (one / LEDGER).write_text(legacy_begin("run-old-one", "s7"), encoding="utf-8")
        if sandboxed:
          self.sandbox(repo)
        ops.relay_run_step(two, "run-old-one", phase="start", role="gate", agent="A")
        got = {r["id"]: r for r in self.listed(repo.root)}
        self.assertEqual([s["role"] for s in got["run-old-one"]["steps"]], ["gate"])

  def test_Fold_CreatingEventAndLaterPhaseAtTheSameSecond_CreationFirst(self) -> None:
    begin = relayrun.begin_event("r", minutes(0), ["s1"], "wt")
    done = relayrun.step_event("r", minutes(1), "1", "done")  # an update names no role
    start = relayrun.step_event("r", minutes(1), "1", "start", role="a", agent="A")
    shared, local = Path("shared"), Path("local")
    got = relayrun.fold(
      [(shared, [(1, begin), (2, done)]), (local, [(1, start)])], minutes(2), strict=2,
    )
    self.assertEqual([(s.id, s.phase, s.role) for s in got[0].steps], [("1", "done", "a")])

  def test_RelayRunEnd_LocalEventNotValid_NotAbsorbed(self) -> None:
    repo = self.repo()
    wt, run = self.worktree_with_run(repo)
    lift = self.sandbox(repo)
    ops.relay_run_step(wt, run, phase="start", role="gate", agent="A")
    lift()
    good = relayrun.step_event(run, minutes(1), "2", "start", role="gate", agent="A")
    with (wt / LEDGER).open("a", encoding="utf-8") as fh:
      fh.write(json.dumps({**good, "phase": "bogus"}) + "\n")
      fh.write(json.dumps({**good, "step": "3", "at": "2026-13-99T00:00:00Z"}) + "\n")
    code, out, err = self.cli(repo.root, "relay-run", "end", run, "--outcome", "pass")
    self.assertEqual(code, 0, out + err)
    self.assertIn("absorbed 1 event ", out)
    self.assertEqual(len(without_running(err).splitlines()), 1, err)
    self.assertIn("2 invalid", err)
    self.assertEqual([len(r["steps"]) for r in self.listed(repo.root)], [1])

  def test_RelayRunEnd_DamagedLocalLedger_SaysItWasSkipped(self) -> None:
    repo = self.repo()
    wt, run = self.worktree_with_run(repo)
    other = self.linked(repo, "other")
    (other / LEDGER).write_text("not json\n", encoding="utf-8")
    code, out, err = self.cli(repo.root, "relay-run", "end", run, "--outcome", "pass")
    self.assertEqual(code, 0, out + err)
    self.assertEqual(len(err.splitlines()), 1, err)
    self.assertIn(str(other / LEDGER), err)

  def named(self, err: str, step: str, parent: str) -> None:
    """Exactly one stderr line names `step` as copied without the `parent` it lost."""
    lines = [line for line in without_running(err).splitlines() if step in line]
    self.assertEqual(len(lines), 1, err)
    self.assertIn(parent, lines[0])

  def test_RelayRunEnd_ParentInDamagedLocalFile_EndsAndNamesOrphan(self) -> None:
    repo = self.repo()
    wt, run = self.worktree_with_run(repo)  # the begin is in the shared ledger
    other = self.linked(repo, "other")
    lift = self.sandbox(repo)
    _, parent = ops.relay_run_step(wt, run, phase="start", role="gate", agent="A", now=minutes(1))
    _, child = ops.relay_run_step(
      other, run, phase="start", role="reviewer", agent="B", parent=parent.id, now=minutes(2),
    )
    with (wt / LEDGER).open("a", encoding="utf-8") as fh:  # the parent's file is damaged
      fh.write("not json\n")
    lift()
    code, out, err = self.cli(repo.root, "relay-run", "end", run, "--outcome", "pass")
    self.assertEqual(code, 0, out + err)
    self.assertEqual(out.strip(), f"ended {run} pass; absorbed 1 event from local ledgers")
    self.named(err, child.id, parent.id)
    got = self.listed(repo.root)
    self.assertEqual([(r["id"], r["outcome"]) for r in got], [(run, "pass")])
    self.assertEqual([(s["id"], s["parent"]) for s in got[0]["steps"]], [(child.id, "")])

  def test_RelayRunStep_NoSharedBeginAndDamagedParent_AbsorbsFoldsAndNamesOrphan(self) -> None:
    repo = self.repo()
    wt, other = self.linked(repo, "wt"), self.linked(repo, "other")
    ops.relay_run_begin(repo.root, ["s9"], now=minutes(0))  # the shared ledger exists
    lift = self.sandbox(repo)
    run = ops.relay_run_begin(other, ["s1"], now=minutes(1)).id  # its begin can only go local
    _, parent = ops.relay_run_step(wt, run, phase="start", role="gate", agent="A", now=minutes(2))
    _, child = ops.relay_run_step(
      other, run, phase="start", role="reviewer", agent="B", parent=parent.id, now=minutes(3),
    )
    with (wt / LEDGER).open("a", encoding="utf-8") as fh:  # the parent's file is damaged
      fh.write("not json\n")
    lift()
    code, out, err = self.cli(
      repo.root, "relay-run", "step", run, "--role", "gate", "--agent", "D", "--phase", "start",
      "--json",
    )
    self.assertEqual(code, 0, out + err)
    new = json.loads(out)["step"]["id"]
    self.named(err, child.id, parent.id)
    got = {r["id"]: r for r in self.listed(repo.root)}
    self.assertEqual({s["id"]: s["parent"] for s in got[run]["steps"]}, {child.id: "", new: ""})

  def test_RelayRunStep_LocalParentBesideOrphanOfDamagedFile_AbsorbsFoldsAndNamesOrphan(self) -> None:
    repo = self.repo()
    wt, run = self.worktree_with_run(repo)  # the begin is in the shared ledger
    dam = self.linked(repo, "dam")
    lift = self.sandbox(repo)
    _, grand = ops.relay_run_step(dam, run, phase="start", role="gate", agent="A", now=minutes(1))
    _, orphan = ops.relay_run_step(
      wt, run, phase="start", role="reviewer", agent="B", parent=grand.id, now=minutes(2),
    )
    _, parent = ops.relay_run_step(
      wt, run, phase="start", role="implementer", agent="C", now=minutes(3),
    )
    with (dam / LEDGER).open("a", encoding="utf-8") as fh:  # the grandparent's file is damaged
      fh.write("not json\n")
    lift()
    # The new step names `parent`, which only wt's file holds, so `parent` must reach the shared
    # ledger with the new step, and the orphan beside it must not stop that.
    code, out, err = self.cli(
      repo.root, "relay-run", "step", run, "--role", "gate", "--agent", "D", "--phase", "start",
      "--parent", parent.id, "--json",
    )
    self.assertEqual(code, 0, out + err)
    new = json.loads(out)["step"]["id"]
    self.named(err, orphan.id, grand.id)
    got = {r["id"]: r for r in self.listed(repo.root)}
    by_id = {s["id"]: s["parent"] for s in got[run]["steps"]}
    self.assertEqual(by_id, {orphan.id: "", parent.id: "", new: parent.id})

  def test_RelayRunEnd_ChildBeforeParentInOtherFile_KeepsItsParent(self) -> None:
    repo = self.repo()
    wt, run = self.worktree_with_run(repo)  # the begin is in the shared ledger
    other = self.linked(repo, "other")
    lift = self.sandbox(repo)
    _, parent = ops.relay_run_step(other, run, phase="start", role="gate", agent="A", now=minutes(1))
    _, child = ops.relay_run_step(
      wt, run, phase="start", role="reviewer", agent="B", parent=parent.id, now=minutes(2),
    )
    lost = relayrun.step_event(
      run, minutes(3), "c0000dead-1", "start", parent="c0000beef-1", role="tester", agent="C",
    )
    with (wt / LEDGER).open("a", encoding="utf-8") as fh:  # its parent is in no ledger at all
      fh.write(json.dumps(lost) + "\n")
    lift()
    # wt reads its own file first, so the child is seen before the parent that sits in `other`.
    code, out, err = self.cli(wt, "relay-run", "end", run, "--outcome", "pass")
    self.assertEqual(code, 0, out + err)
    self.assertEqual(out.strip(), f"ended {run} pass; absorbed 3 events from local ledgers")
    self.named(err, lost["step"], "c0000beef-1")
    self.assertNotIn(child.id, without_running(err))
    got = {r["id"]: r for r in self.listed(repo.root)}
    by_id = {s["id"]: s["parent"] for s in got[run]["steps"]}
    self.assertEqual(by_id, {parent.id: "", child.id: parent.id, lost["step"]: ""})

  def test_Fold_EventsAtTheSameSecond_FileOrderThenLine(self) -> None:
    begin = relayrun.begin_event("r", minutes(0), ["s1"], "wt")
    first = relayrun.step_event("r", minutes(1), "1", "start", role="first", agent="A")
    second = relayrun.step_event("r", minutes(1), "1", "start", role="second", agent="A")
    third = relayrun.step_event("r", minutes(1), "1", "start", role="third", agent="A")
    base, extra = Path("base.jsonl"), Path("extra.jsonl")
    cases = {
      "later file wins": ([(base, [(1, begin), (2, first)]), (extra, [(1, second)])], "second"),
      "earlier file loses": ([(extra, [(1, second)]), (base, [(1, begin), (2, first)])], "first"),
      "later line wins": ([(base, [(1, begin), (2, first), (3, third)])], "third"),
    }
    for name, (sources, role) in cases.items():
      with self.subTest(name):
        got = relayrun.fold(sources, minutes(2), strict=len(sources))
        self.assertEqual([s.role for s in got[0].steps], [role])

  def test_Fold_StepAtTheSecondOfItsBeginInAnEarlierFile_StillBelongsToTheRun(self) -> None:
    begin = relayrun.begin_event("r", minutes(1), ["s1"], "wt")
    step = relayrun.step_event("r", minutes(1), "1", "start", role="a", agent="A")
    sources = [(Path("one"), [(1, step)]), (Path("two"), [(1, begin)])]
    got = relayrun.fold(sources, minutes(2), strict=2)
    self.assertEqual([(r.id, [s.id for s in r.steps]) for r in got], [("r", ["1"])])

  def test_Fold_StepStampedBeforeItsBegin_StillBelongsToTheRun(self) -> None:
    begin = relayrun.begin_event("r", minutes(5), ["s1"], "wt")
    step = relayrun.step_event("r", minutes(3), "1", "start", role="a", agent="A")  # clock stepped back
    got = relayrun.fold([(Path("one"), [(1, begin), (2, step)])], minutes(6))
    self.assertEqual([(r.id, [s.id for s in r.steps]) for r in got], [("r", ["1"])])

  def test_Fold_IdenticalEventInTwoFiles_FoldedOnce(self) -> None:
    begin = relayrun.begin_event("r", minutes(0), ["s1"], "wt")
    step = relayrun.step_event("r", minutes(1), "1", "start", role="a", agent="A")
    sources = [(Path("one"), [(1, begin), (2, step)]), (Path("two"), [(1, begin), (2, step)])]
    got = relayrun.fold(sources, minutes(2), strict=2)
    self.assertEqual([(r.id, len(r.steps)) for r in got], [("r", 1)])

  def test_Fold_ChildLineBeforeParentSameSecond_Folds(self) -> None:
    begin = relayrun.begin_event("r", minutes(0), ["s1"], "wt")
    parent = relayrun.step_event("r", minutes(1), "1", "start", role="a", agent="A")
    child = relayrun.step_event("r", minutes(1), "2", "start", role="b", agent="B", parent="1")
    sources = [(Path("one"), [(1, begin), (2, child), (3, parent)])]
    got = relayrun.fold(sources, minutes(2))
    self.assertEqual([(s.id, s.parent) for s in got[0].steps], [("1", ""), ("2", "1")])

  def test_Fold_ReversedThreeLevelChainSameSecond_Folds(self) -> None:
    begin = relayrun.begin_event("r", minutes(0), ["s1"], "wt")
    parent = relayrun.step_event("r", minutes(1), "1", "start", role="a", agent="A")
    child = relayrun.step_event("r", minutes(1), "2", "start", role="b", agent="B", parent="1")
    grandchild = relayrun.step_event("r", minutes(1), "3", "start", role="c", agent="C", parent="2")
    sources = [(Path("one"), [(1, begin), (2, grandchild), (3, child), (4, parent)])]
    got = relayrun.fold(sources, minutes(2))
    self.assertEqual(
      [(s.id, s.parent) for s in got[0].steps], [("1", ""), ("2", "1"), ("3", "2")],
    )

  def test_Absorb_ChildSameSecondAsParent_SharedLedgerFolds(self) -> None:
    repo = self.repo()
    wt, run = self.worktree_with_run(repo)
    parent = relayrun.step_event(
      run, minutes(1), "1", "start", role="implementer", agent="A",
    )
    child = relayrun.step_event(
      run, minutes(1), "2", "start", role="reviewer", agent="B", parent="1",
    )
    # Absorb reads this checkout's child before the sibling's parent.
    relayrun.append(repo.root, child, path=repo.root / LEDGER)
    relayrun.append(wt, parent, path=wt / LEDGER)
    code, out, err = self.cli(repo.root, "relay-run", "end", run, "--outcome", "pass")
    self.assertEqual((code, err), (0, f"slicer: ended {run} with running steps: 1, 2\n"), out)
    got = self.listed(repo.root)
    self.assertEqual(
      [(s["id"], s["parent"]) for s in got[0]["steps"]], [("1", ""), ("2", "1")],
    )

  def test_RelayRunEnd_AbsorbsLocalStepsIntoShared(self) -> None:
    repo = self.repo()
    wt, run = self.worktree_with_run(repo)
    lift = self.sandbox(repo)
    ids = [
      ops.relay_run_step(wt, run, phase="start", role="gate", agent="A", task=task)[1].id
      for task in ("write tests", "review")
    ]
    with (wt / LEDGER).open("a", encoding="utf-8") as fh:  # an event the shared ledger has
      fh.write(self.common(repo).read_text(encoding="utf-8").splitlines()[0] + "\n")
    lift()
    code, out, err = self.cli(repo.root, "relay-run", "end", run, "--outcome", "pass")
    self.assertEqual(
      (code, err), (0, f"slicer: ended {run} with running steps: {', '.join(ids)}\n"), out,
    )
    self.assertEqual(out.strip(), f"ended {run} pass; absorbed 2 events from local ledgers")
    shared = [e["event"] for _, e in relayrun.read_events(self.common(repo))]
    self.assertEqual(shared, ["begin", "step", "step", "end"])
    gone = repo._git("worktree", "remove", "--force", str(wt))
    self.assertEqual(gone.returncode, 0, gone.stderr)
    got = self.listed(repo.root)
    self.assertEqual([(r["id"], r["outcome"]) for r in got], [(run, "pass")])
    self.assertEqual([s["task"] for s in got[0]["steps"]], ["write tests", "review"])

  def test_RelayRunStep_WaitInFallbackFile_SurvivesAbsorbAndKeepsRunActive(self) -> None:
    repo = self.repo()
    wt, run = self.worktree_with_run(repo)
    lift = self.sandbox(repo)
    step = ops.relay_run_step(wt, run, phase="start", role="orchestrator", agent="A", now=minutes(1))[1]
    ops.relay_run_step(wt, run, step=step.id, phase="wait", waiting_on="approval", now=minutes(2))
    self.assertEqual(self.local(wt)[-1]["waiting_on"], "approval")
    lift()
    got = relayrun.list_runs(repo.root, now=minutes(600))[0]
    self.assertEqual((got.state, got.steps[0].waiting_on), ("active", "approval"))
    code, out, err = self.cli(repo.root, "relay-run", "end", run, "--outcome", "stopped")
    self.assertEqual(code, 0, out + err)
    shared = [e for _, e in relayrun.read_events(self.common(repo))]
    self.assertEqual(
      [e.get("waiting_on") for e in shared if e["event"] == "step"], [None, "approval"],
    )

  def test_RelayRunEnd_SharedStillReadOnly_EndsLocallyWithoutAbsorbing(self) -> None:
    repo = self.repo()
    wt, run = self.worktree_with_run(repo)
    self.sandbox(repo)
    ops.relay_run_step(wt, run, phase="start", role="gate", agent="A")
    shared = self.common(repo).read_bytes()
    code, out, err = self.cli(wt, "relay-run", "end", run, "--outcome", "fail")
    self.assertEqual(code, 0, out + err)
    self.assertEqual(out.strip(), f"ended {run} fail")
    self.assertEqual(len(without_running(err).splitlines()), 1, err)
    self.assertIn("with running steps", err)
    self.assertEqual([e["event"] for e in self.local(wt)], ["step", "end"])
    self.assertEqual(self.common(repo).read_bytes(), shared)
    got = self.listed(wt)
    self.assertEqual([(r["id"], r["outcome"], len(r["steps"])) for r in got], [(run, "fail", 1)])

  def test_RelayRunEnd_CloseStepsSharedStillReadOnly_ClosesAndEndsLocally(self) -> None:
    repo = self.repo()
    wt, run = self.worktree_with_run(repo)
    self.sandbox(repo)
    local = ops.relay_run_step(wt, run, phase="start", role="gate", agent="A")[1].id
    shared = self.common(repo).read_bytes()
    code, out, err = self.cli(wt, "relay-run", "end", run, "--outcome", "pass",
                              "--close-steps", "done")
    self.assertEqual(code, 0, out + err)
    self.assertEqual(out.strip(), f"ended {run} pass; closed steps: {local}")
    self.assertEqual(len(err.splitlines()), 1, err)  # only the fallback notice
    self.assertEqual([(e["event"], e.get("phase")) for e in self.local(wt)],
                     [("step", "start"), ("step", "done"), ("end", None)])
    self.assertEqual(self.common(repo).read_bytes(), shared)
    got = self.listed(wt)
    self.assertEqual([(s["id"], s["phase"]) for s in got[0]["steps"]], [(local, "done")])

  def test_RelayRunEnd_CloseStepsOnLocalStep_LandsInSharedLedger(self) -> None:
    repo = self.repo()
    wt, run = self.worktree_with_run(repo)
    lift = self.sandbox(repo)
    local = ops.relay_run_step(wt, run, phase="start", role="gate", agent="A")[1].id
    lift()
    code, out, err = self.cli(repo.root, "relay-run", "end", run, "--outcome", "pass",
                              "--close-steps", "done")
    self.assertEqual((code, err), (0, ""), out)
    self.assertEqual(out.strip(), f"ended {run} pass; absorbed 1 event from local ledgers; "
                                  f"closed steps: {local}")
    shared = [(e["event"], e.get("step"), e.get("phase")) for _, e in
              relayrun.read_events(self.common(repo))]
    self.assertEqual(shared, [("begin", None, None), ("step", local, "start"),
                              ("step", local, "done"), ("end", None, None)])
    gone = repo._git("worktree", "remove", "--force", str(wt))
    self.assertEqual(gone.returncode, 0, gone.stderr)
    got = self.listed(repo.root)
    self.assertEqual([(s["id"], s["phase"]) for s in got[0]["steps"]], [(local, "done")])

  def test_RelayRunStep_FallbackAndSharedConcurrent_DistinctIds(self) -> None:
    repo = self.repo()
    wt, run = self.worktree_with_run(repo)
    real_open, real_file_lock = ops._open_run, store.file_lock
    refused, paused = [], []

    def refuse_first_lock(lock_path: Path, *, timeout: float | None = None):
      """The fallback writer's shared lock is refused; the shared writer's is not."""
      if not refused:
        refused.append(lock_path)
        raise PermissionError(errno.EACCES, "sandbox refuses the lock", str(lock_path))
      return real_file_lock(lock_path, timeout=timeout)

    def read_then_shared_write(root, run_id, now):
      """The fallback writer has read the run; the shared writer completes before it writes."""
      found = real_open(root, run_id, now)
      if not paused:
        paused.append(run_id)
        ops.relay_run_step(
          repo.root, run_id, phase="start", role="reviewer", agent="B", now=minutes(2),
        )
      return found

    with patch.object(store, "file_lock", refuse_first_lock), \
        patch.object(ops, "_open_run", read_then_shared_write):
      ops.relay_run_step(wt, run, phase="start", role="gate", agent="A", now=minutes(1))
    self.assertEqual((len(refused), len(paused)), (1, 1))
    shared = [e["step"] for _, e in relayrun.read_events(self.common(repo)) if e["event"] == "step"]
    self.assertEqual(shared, ["1"])
    steps = self.listed(repo.root)[0]["steps"]
    self.assertEqual(sorted(s["role"] for s in steps), ["gate", "reviewer"], steps)
    self.assertEqual(len({s["id"] for s in steps}), 2, steps)

  def test_RelayRunStep_FallbackFromTwoCheckouts_DistinctIds(self) -> None:
    repo = self.repo()
    one, two = self.linked(repo, "one"), self.linked(repo, "two")
    run = ops.relay_run_begin(one, ["s1"], now=minutes(0)).id
    self.sandbox(repo)
    real_open = ops._open_run
    paused = []

    def read_then_other_checkout_writes(root, run_id, now):
      """The first sandboxed writer has read the run; the other checkout's completes first."""
      found = real_open(root, run_id, now)
      if not paused:
        paused.append(run_id)
        ops.relay_run_step(two, run_id, phase="start", role="reviewer", agent="B", now=minutes(2))
      return found

    with patch.object(ops, "_open_run", read_then_other_checkout_writes):
      ops.relay_run_step(one, run, phase="start", role="gate", agent="A", now=minutes(1))
    self.assertEqual(len(paused), 1)
    steps = self.listed(repo.root)[0]["steps"]
    self.assertEqual(sorted(s["role"] for s in steps), ["gate", "reviewer"], steps)
    self.assertEqual(len({s["id"] for s in steps}), 2, steps)

  def test_RelayRunStep_SharedWriteRefusedAfterRead_NumbersFromLocalFileAgain(self) -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
      self.skipTest("root ignores file modes, so the ledger cannot be made read-only")
    repo = self.repo()
    wt, run = self.worktree_with_run(repo)
    ledger = self.common(repo)
    mode = ledger.stat().st_mode
    ledger.chmod(0o444)  # the lock stays writable, so the shared write is refused after the read
    self.addCleanup(ledger.chmod, mode)
    _, first = ops.relay_run_step(wt, run, phase="start", role="gate", agent="A", now=minutes(1))
    self.assertRegex(first.id, r"^c[0-9a-f]{8}-1$")
    prefix = first.id[:-1]
    real_open = ops._open_run
    concurrent = []

    def read_then_other_fallback_lands(root, run_id, now):
      """Before this writer falls back, another sandboxed writer in this checkout lands a step."""
      found = real_open(root, run_id, now)
      if not concurrent:
        concurrent.append(run_id)
        other = relayrun.step_event(
          run_id, minutes(2), prefix + "2", "start", role="reviewer", agent="B",
        )
        relayrun.append(wt, other, path=relayrun.legacy_ledger_path(wt))
      return found

    with patch.object(ops, "_open_run", read_then_other_fallback_lands):
      ops.relay_run_step(wt, run, phase="start", role="implementer", agent="C", now=minutes(3))
    self.assertEqual(len(concurrent), 1)
    steps = [(e["step"], e["role"]) for e in self.local(wt) if e["event"] == "step"]
    self.assertEqual(
      steps, [(prefix + "1", "gate"), (prefix + "2", "reviewer"), (prefix + "3", "implementer")],
    )

  def test_RelayRunStep_PrefixedFallbackId_AcceptedByStepAndParent(self) -> None:
    repo = self.repo()
    wt, run = self.worktree_with_run(repo)
    self.sandbox(repo)
    code, out, err = self.cli(
      wt, "relay-run", "step", run, "--role", "gate", "--agent", "A", "--phase", "start", "--json",
    )
    self.assertEqual(code, 0, out + err)
    lead = json.loads(out)["step"]["id"]
    self.assertRegex(lead, r"^c[0-9a-f]{8}-1$")
    prefix = lead[:-1]
    code, out, err = self.cli(
      wt, "relay-run", "step", run, "--step", lead, "--phase", "done", "--json",
    )
    self.assertEqual(code, 0, out + err)
    self.assertEqual(json.loads(out)["step"]["phase"], "done")
    code, out, err = self.cli(
      wt, "relay-run", "step", run, "--role", "reviewer", "--agent", "B", "--phase", "start",
      "--parent", lead, "--json",
    )
    self.assertEqual(code, 0, out + err)
    child = json.loads(out)["step"]
    self.assertEqual((child["id"], child["parent"]), (prefix + "2", lead))
    got = self.listed(repo.root)[0]
    by_id = {s["id"]: s for s in got["steps"]}
    self.assertEqual((by_id[lead]["phase"], by_id[child["id"]]["parent"]), ("done", lead))
    self.assertEqual(got["counts"]["subagents"], 1)

  def test_RelayRunStep_SharedWriteAfterFallback_NumbersOnlyPlainIds(self) -> None:
    repo = self.repo()
    wt, run = self.worktree_with_run(repo)
    ops.relay_run_step(wt, run, phase="start", role="gate", agent="A", now=minutes(1))
    lift = self.sandbox(repo)
    _, fallback = ops.relay_run_step(
      wt, run, phase="start", role="reviewer", agent="B", now=minutes(2),
    )
    self.assertRegex(fallback.id, r"^c[0-9a-f]{8}-1$")
    lift()
    ops.relay_run_step(wt, run, phase="start", role="implementer", agent="C", now=minutes(3))
    shared = [e["step"] for _, e in relayrun.read_events(self.common(repo)) if e["event"] == "step"]
    self.assertEqual(shared, ["1", "2"])
    local = [e["step"] for e in self.local(wt) if e["event"] == "step"]
    self.assertEqual(local, [fallback.id])


class LedgerTests(RelayRunCase):
  def test_Ledger_InGitRepo_IsUntrackedAndNotInRender(self) -> None:
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
    self.ok(repo, "relay-run", "end", run, "--outcome", "pass", "--close-steps", "done")
    self.assertTrue((repo.root / ".git" / "slicer" / relayrun.LEDGER_NAME).is_file())
    self.assertFalse((repo.root / LEDGER).exists())
    self.assertEqual(snapshot(), before)
    self.assertEqual(repo.run("check")[0], code_before)
    status = repo._git("status", "--porcelain")
    self.assertEqual(status.stdout.splitlines(), [])

  def test_Ledger_InCheckout_IsGitignored(self) -> None:
    repo = self.project(git=True)
    repo.commit("base")
    legacy = repo.root / LEDGER
    legacy.write_text(legacy_begin("run-old"), encoding="utf-8")
    self.assertEqual(repo._git("status", "--porcelain").stdout.splitlines(), [])
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

  def test_Ledger_MalformedStepId_ReportedAsCorrupt(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    good = relayrun.step_event(run, minutes(1), "1", "start", role="gate", agent="A")
    bad_ids = (
      "c1a2b3c4-1", "C1A2B3C4D-1", "c1a2b3c4d-", "c1a2b3c4d-1x", "c1a2b3c4d1", "-1", "1-1",
      " 1", "c1a2b3c4d-1 ",
    )
    for bad in bad_ids:
      with self.subTest(step=bad):
        path = repo.root / LEDGER
        before = path.read_text(encoding="utf-8")
        path.write_text(before + json.dumps({**good, "step": bad}) + "\n", encoding="utf-8")
        try:
          payload = self.refuse(repo, "relay-run", "list", code=3, error="corrupt")
          self.assertIn("relay-runs.jsonl:2", payload["message"])
        finally:
          path.write_text(before, encoding="utf-8")

  def test_Ledger_NumericIdsOnly_StillFoldAndNumberOn(self) -> None:
    repo = self.project()
    run = "run-legacy"
    events = [
      relayrun.begin_event(run, minutes(0), ["s1"], "old"),
      relayrun.step_event(run, minutes(1), "1", "start", role="gate", agent="A"),
      relayrun.step_event(run, minutes(2), "2", "start", role="reviewer", agent="B", parent="1"),
    ]
    (repo.root / LEDGER).write_text(
      "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events), encoding="utf-8",
    )
    self.ok(repo, "relay-run", "step", run, "--step", "2", "--phase", "done")
    steps = self.runs(repo)[0]["steps"]
    self.assertEqual(
      [(s["id"], s["phase"], s["parent"]) for s in steps], [("1", "start", ""), ("2", "done", "1")],
    )
    self.assertEqual(self.step(repo, run)["step"]["id"], "3")


class WaitTests(RelayRunCase):
  """A step in the `wait` phase is a run waiting on the owner, so the run is never stale (s404)."""

  def started(self, repo: support.TempRepo, at: int = 0) -> tuple[str, str]:
    run = ops.relay_run_begin(repo.root, ["s1"], now=minutes(at)).id
    _, step = ops.relay_run_step(
      repo.root, run, role="orchestrator", agent="A", phase="start", task="relay",
      now=minutes(at),
    )
    return run, step.id

  def state(self, repo: support.TempRepo, at: int) -> str:
    return relayrun.list_runs(repo.root, now=minutes(at), siblings=False)[0].state

  def events(self, repo: support.TempRepo) -> list[dict]:
    return [event for _, event in relayrun.read_events(repo.root / LEDGER)]

  def wait(self, repo: support.TempRepo, run: str, step: str, at: int, **extra: str) -> None:
    ops.relay_run_step(repo.root, run, step=step, phase="wait", now=minutes(at), **extra)

  def test_Wait_RunWaitingForHours_IsNeverStale(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.wait(repo, run, step, 5)
    self.assertEqual(self.state(repo, 35), "active")
    self.assertEqual(self.state(repo, 24 * 60), "active")
    active = relayrun.list_runs(repo.root, now=minutes(600), siblings=False, active=True)
    self.assertEqual([r.id for r in active], [run])

  def test_Wait_WithoutIt_RunStillGoesStale(self) -> None:
    repo = self.project()
    self.started(repo)
    self.assertEqual(self.state(repo, 31), "stale")

  def test_Wait_StartAfterWait_EndsItAndStalenessResumesFromThen(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.wait(repo, run, step, 5)
    ops.relay_run_step(repo.root, run, step=step, phase="start", now=minutes(100))
    self.assertEqual(self.state(repo, 130), "active")
    self.assertEqual(self.state(repo, 131), "stale")

  def test_Wait_DoneAfterWait_EndsItAndStalenessResumesFromThen(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.wait(repo, run, step, 5)
    ops.relay_run_step(repo.root, run, step=step, phase="done", now=minutes(100))
    self.assertEqual(self.state(repo, 130), "active")
    self.assertEqual(self.state(repo, 131), "stale")

  def test_Wait_FailAfterWait_EndsIt(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.wait(repo, run, step, 5)
    ops.relay_run_step(repo.root, run, step=step, phase="fail", now=minutes(100))
    self.assertEqual(self.state(repo, 131), "stale")

  def test_Wait_OneStepWaitingAmongOthers_KeepsTheRunActive(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.wait(repo, run, step, 1)
    ops.relay_run_step(
      repo.root, run, role="gate", agent="B", phase="done", now=minutes(2),
    )
    self.assertEqual(self.state(repo, 300), "active")

  def test_Wait_EndedRun_IsEndedNotWaiting(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.wait(repo, run, step, 1)
    ops.relay_run_end(repo.root, run, "stopped", now=minutes(2))
    got = relayrun.list_runs(repo.root, now=minutes(300), siblings=False)[0]
    self.assertEqual(got.state, "ended")
    self.assertEqual(got.counts()["waiting"], 0)

  def test_Wait_WaitingOnDefaultsToOwner(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    step = self.step(repo, run)["step"]["id"]
    got = self.ok(repo, "relay-run", "step", run, "--step", step, "--phase", "wait")
    self.assertEqual(
      (got["step"]["phase"], got["step"]["waiting_on"]), ("wait", "owner"),
    )

  def test_Wait_WaitingOnApproval_IsRecorded(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    step = self.step(repo, run)["step"]["id"]
    got = self.ok(
      repo, "relay-run", "step", run, "--step", step, "--phase", "wait",
      "--waiting-on", "approval",
    )
    self.assertEqual(got["step"]["waiting_on"], "approval")
    self.assertEqual(got["run"]["steps"][0]["waiting_on"], "approval")

  def test_Wait_NewStepInWaitPhase_IsCreatedWaiting(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    got = self.ok(
      repo, "relay-run", "step", run, "--role", "orchestrator", "--agent", "A",
      "--phase", "wait", "--waiting-on", "owner", "--task", "awaiting-approval",
    )
    self.assertEqual((got["step"]["phase"], got["step"]["task"]), ("wait", "awaiting-approval"))

  def test_Wait_UnknownWaitingOn_BadRunFieldAndNothingWritten(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    step = self.step(repo, run)["step"]["id"]
    before = repo.read(LEDGER)
    self.refuse(
      repo, "relay-run", "step", run, "--step", step, "--phase", "wait",
      "--waiting-on", "nobody", code=2, error="bad_run_field",
    )
    self.assertEqual(repo.read(LEDGER), before)

  def test_Wait_WaitingOnWithoutWaitPhase_UsageAndNothingWritten(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    step = self.step(repo, run)["step"]["id"]
    before = repo.read(LEDGER)
    for phase in ("start", "done", "fail"):
      with self.subTest(phase=phase):
        payload = self.refuse(
          repo, "relay-run", "step", run, "--step", step, "--phase", phase,
          "--waiting-on", "owner", code=2, error="usage",
        )
        self.assertIn("--phase wait", payload["message"])
    self.assertEqual(repo.read(LEDGER), before)

  def test_Wait_OtherPhaseWordsStayRefused(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    step = self.step(repo, run)["step"]["id"]
    self.refuse(
      repo, "relay-run", "step", run, "--step", step, "--phase", "waiting",
      code=2, error="bad_run_field",
    )

  def test_Wait_Event_IsAStartHeartbeatAnOlderReaderFolds(self) -> None:
    """An older slicer refuses a phase it does not know as `corrupt`, so the event on the wire
    uses a phase it knows and carries the wait in a field it ignores."""
    repo = self.project()
    run = self.begin(repo, "s1")
    step = self.step(repo, run)["step"]["id"]
    self.ok(repo, "relay-run", "step", run, "--step", step, "--phase", "wait")
    event = self.events(repo)[-1]
    self.assertEqual((event["event"], event["phase"], event["waiting_on"]), ("step", "start", "owner"))
    older_required = ("run", "at", "step", "phase", "role", "agent", "vendor", "model",
                      "reasoning", "task", "parent")
    self.assertTrue(all(key in event for key in older_required))
    self.assertIn(event["phase"], ("start", "done", "fail"))
    self.assertTrue(relayrun.valid_event(event))

  def test_Wait_OtherStepEvents_CarryNoWaitingOnKey(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    step = self.step(repo, run)["step"]["id"]
    self.ok(repo, "relay-run", "step", run, "--step", step, "--phase", "wait")
    self.ok(repo, "relay-run", "step", run, "--step", step, "--phase", "start")
    self.ok(repo, "relay-run", "step", run, "--step", step, "--phase", "done")
    keys = ["waiting_on" in event for event in self.events(repo)]
    self.assertEqual(keys, [False, False, True, False, False])

  def test_Wait_UnknownWaitingOnInLedger_StillFoldsAsWaiting(self) -> None:
    """A newer slicer may name another thing to wait on; this one still treats it as a wait."""
    repo = self.project()
    run, step = self.started(repo)
    event = relayrun.step_event(run, minutes(1), step, "start", waiting_on="answer")
    with (repo.root / LEDGER).open("a", encoding="utf-8") as fh:
      fh.write(json.dumps(event) + "\n")
    got = relayrun.list_runs(repo.root, now=minutes(500), siblings=False)[0]
    self.assertEqual(got.state, "active")
    self.assertEqual(got.steps[0].waiting_on, "answer")

  def test_Wait_WaitingOnNotAString_IsCorrupt(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    event = relayrun.step_event(run, minutes(1), step, "start")
    event["waiting_on"] = 3
    with (repo.root / LEDGER).open("a", encoding="utf-8") as fh:
      fh.write(json.dumps(event) + "\n")
    payload = self.refuse(repo, "relay-run", "list", code=3, error="corrupt")
    self.assertIn("waiting_on", payload["message"])

  def test_Wait_WaitingOnWithDonePhaseInLedger_DoesNotWait(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    event = relayrun.step_event(run, minutes(1), step, "done", waiting_on="owner")
    with (repo.root / LEDGER).open("a", encoding="utf-8") as fh:
      fh.write(json.dumps(event) + "\n")
    got = relayrun.list_runs(repo.root, now=minutes(500), siblings=False)[0]
    self.assertEqual((got.state, got.steps[0].phase, got.steps[0].waiting_on), ("stale", "done", ""))

  def test_Wait_Counts_SeparateWaitingFromRunning(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    ops.relay_run_step(repo.root, run, role="gate", agent="B", phase="start", now=minutes(1))
    self.assertEqual(
      relayrun.list_runs(repo.root, now=minutes(2), siblings=False)[0].counts()["running"], 2,
    )
    self.wait(repo, run, step, 2)
    counts = relayrun.list_runs(repo.root, now=minutes(3), siblings=False)[0].counts()
    self.assertEqual((counts["running"], counts["waiting"]), (1, 1))

  def test_Wait_ListJson_KeysAreTheOldOnesPlusWaiting(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    step = self.step(repo, run)["step"]["id"]
    self.ok(repo, "relay-run", "step", run, "--step", step, "--phase", "wait")
    data = self.runs(repo)[0]
    self.assertEqual(
      list(data),
      ["id", "items", "worktree", "begun_at", "updated_at", "state", "outcome", "counts", "steps"],
    )
    self.assertEqual(list(data["counts"]), ["steps", "running", "waiting", "subagents", "roles"])
    self.assertEqual(
      list(data["steps"][0]),
      ["id", "role", "agent", "vendor", "model", "reasoning", "phase", "waiting_on", "task",
       "parent", "begun_at", "updated_at", "question", "reason"],
    )
    self.assertEqual((data["state"], data["counts"]["waiting"]), ("active", 1))
    self.assertEqual(relayrun.Run.from_dict(data).to_dict(), data)

  def test_Wait_StepFromDictWithoutWaitingOn_ReadsAsNotWaiting(self) -> None:
    """A step dict from before s404 has no `waiting_on`."""
    repo = self.project()
    run = self.begin(repo, "s1")
    self.step(repo, run)
    data = self.runs(repo)[0]
    for step in data["steps"]:
      del step["waiting_on"]
    self.assertEqual(relayrun.Run.from_dict(data).steps[0].waiting_on, "")

  def test_Wait_ListText_ShowsTheWaitingRunAndStep(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    step = self.step(repo, run, "--task", "relay")["step"]["id"]
    self.ok(
      repo, "relay-run", "step", run, "--step", step, "--phase", "wait", "--waiting-on", "approval",
    )
    code, out, err = repo.run("relay-run", "list")
    self.assertEqual((code, err), (0, ""))
    header, line = out.splitlines()
    self.assertIn(f"{run}  active  waiting approval  items s1", header)
    self.assertTrue(header.endswith("1 step, 0 running, 1 waiting, 0 subagents"), header)
    self.assertIn("  wait:approval  relay", line)

  def test_Wait_ListText_NotWaiting_IsUnchanged(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    self.step(repo, run)
    header = repo.run("relay-run", "list")[1].splitlines()[0]
    self.assertNotIn("waiting", header)
    self.assertTrue(header.endswith("1 step, 1 running, 0 subagents"), header)

  def test_Wait_ListText_AfterTheWaitEnds_ShowsNothingWaiting(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    step = self.step(repo, run)["step"]["id"]
    self.ok(repo, "relay-run", "step", run, "--step", step, "--phase", "wait")
    self.ok(repo, "relay-run", "step", run, "--step", step, "--phase", "start")
    out = repo.run("relay-run", "list")[1]
    self.assertNotIn("waiting", out)
    self.assertEqual(self.runs(repo)[0]["steps"][0]["waiting_on"], "")

  def test_Wait_ListActive_KeepsTheWaitingRun(self) -> None:
    repo = self.project()
    run, step = self.started(repo, at=-600)
    self.wait(repo, run, step, -590)
    self.assertEqual([r["id"] for r in self.runs(repo, "--active")], [run])

  def test_Wait_EndCloseSteps_ClosesTheWaitingStep(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    step = self.step(repo, run)["step"]["id"]
    self.ok(repo, "relay-run", "step", run, "--step", step, "--phase", "wait")
    got = self.ok(repo, "relay-run", "end", run, "--outcome", "stopped", "--close-steps", "fail")
    self.assertEqual(got["closed_steps"], [step])
    final = got["run"]["steps"][0]
    self.assertEqual((final["phase"], final["waiting_on"]), ("fail", ""))

  def test_Wait_EndWithoutCloseSteps_NamesTheWaitingStep(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    step = self.step(repo, run)["step"]["id"]
    self.ok(repo, "relay-run", "step", run, "--step", step, "--phase", "wait")
    code, out, err = repo.run("relay-run", "end", run, "--outcome", "stopped")
    self.assertEqual(code, 0, out)
    self.assertEqual(err.splitlines(), [f"slicer: ended {run} with running steps: {step}"])


class AskAnswerTests(RelayRunCase):
  """`relay-run ask` records an owner question on a step and the step waits; `relay-run answer`
  records the answer and ends that wait (s396). The wire is a step event an older slicer folds
  as a heartbeat, so neither writes a kind of line or a phase the strict reader refuses."""

  OLDER_STEP_KEYS = (
    "v", "event", "run", "at", "step", "phase", "role", "agent", "vendor", "model", "reasoning",
    "task", "parent",
  )

  def started(self, repo: support.TempRepo, at: int = 0) -> tuple[str, str]:
    run = ops.relay_run_begin(repo.root, ["s1"], now=minutes(at)).id
    _, step = ops.relay_run_step(
      repo.root, run, role="orchestrator", agent="A", phase="start", task="relay",
      now=minutes(at),
    )
    return run, step.id

  def ask(self, repo: support.TempRepo, run: str, step: str, *extra: str) -> dict:
    argv = ["relay-run", "ask", run, "--step", step, "--title", "Which store?"]
    if "--option" not in extra:
      argv += ["--option", "file:A JSON file", "--option", "db:A database"]
    return self.ok(repo, *argv, *extra)

  def events(self, repo: support.TempRepo) -> list[dict]:
    return [event for _, event in relayrun.read_events(repo.root / LEDGER)]

  def ledger_bytes(self, repo: support.TempRepo) -> bytes:
    return (repo.root / LEDGER).read_bytes()

  def refused(self, repo: support.TempRepo, *argv: str, error: str, code: int = 2) -> dict:
    """The call is refused with `error` and the ledger is as it was."""
    before = self.ledger_bytes(repo)
    payload = self.refuse(repo, *argv, code=code, error=error)
    self.assertEqual(self.ledger_bytes(repo), before)
    return payload

  def test_Ask_OnAStep_RecordsTheQuestionAndTheStepWaitsOnTheOwner(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    got = self.ask(repo, run, step)
    question = got["step"]["question"]
    self.assertEqual(question["id"], "q1")
    self.assertEqual(question["title"], "Which store?")
    self.assertEqual(
      question["options"],
      [{"label": "file", "summary": "A JSON file"}, {"label": "db", "summary": "A database"}],
    )
    self.assertEqual((got["step"]["phase"], got["step"]["waiting_on"]), ("wait", "owner"))
    self.assertEqual(got["question"], question)
    listed = self.runs(repo)[0]["steps"][0]
    self.assertEqual(listed["question"], question)
    self.assertEqual(self.ok(repo, "relay-run", "list", run)["runs"][0]["counts"]["waiting"], 1)

  def test_Ask_Question_KeysFollowTheOwnerQuestionFieldOrder(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    question = self.ask(
      repo, run, step, "--option", "file:A JSON file", "--option", "db:A database",
      "--recommend", "db", "--blocker", "--key", "store.engine", "--reasoning", "one | two",
    )["question"]
    self.assertEqual(
      list(question),
      ["id", "title", "options", "recommend", "blocker", "key", "reasoning", "asked_at",
       "answered_at", "answer"],
    )
    self.assertEqual(
      (question["recommend"], question["blocker"], question["key"], question["reasoning"]),
      ("db", True, "store.engine", "one | two"),
    )
    self.assertEqual((question["answered_at"], question["answer"]), ("", None))

  def test_Ask_WithoutOptionalFields_LeavesThemEmpty(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    question = self.ask(repo, run, step)["question"]
    self.assertEqual(
      (question["recommend"], question["blocker"], question["key"], question["reasoning"]),
      ("", False, "", ""),
    )

  def test_Ask_Run_StaysActiveForHoursAndIsNotStale(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    ops.relay_run_ask(
      repo.root, run, step=step, title="T", options=["a:A", "b:B"], now=minutes(1),
    )
    got = relayrun.list_runs(repo.root, now=minutes(600), siblings=False)[0]
    self.assertEqual((got.state, got.steps[0].phase, got.waiting), ("active", "wait", True))
    self.assertEqual(got.counts()["waiting"], 1)

  def test_Ask_AskedAt_IsTheEventStamp(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    _, _, question = ops.relay_run_ask(
      repo.root, run, step=step, title="T", options=["a:A", "b:B"], now=minutes(7),
    )
    self.assertEqual(question.asked_at, relayrun.stamp(minutes(7)))
    self.assertEqual(self.events(repo)[-1]["at"], question.asked_at)

  def test_Ask_Ids_AreOnePastTheLargestInTheRunAcrossSteps(self) -> None:
    repo = self.project()
    run, first = self.started(repo)
    second = ops.relay_run_step(repo.root, run, role="gate", agent="B", phase="start")[1].id
    self.assertEqual(self.ask(repo, run, first)["question"]["id"], "q1")
    self.assertEqual(self.ask(repo, run, second)["question"]["id"], "q2")
    self.ok(repo, "relay-run", "answer", run, "q1", "--option", "file")
    self.assertEqual(self.ask(repo, run, first)["question"]["id"], "q3")
    other = self.begin(repo, "s2")
    step = self.step(repo, other)["step"]["id"]
    self.assertEqual(self.ask(repo, other, step)["question"]["id"], "q1")

  def test_Ask_Event_IsAStartHeartbeatAnOlderReaderFolds(self) -> None:
    """An older slicer refuses an unknown event kind or phase as `corrupt`, so the question rides
    on a `start` step event, beside the `waiting_on` an older slicer ignores."""
    repo = self.project()
    run, step = self.started(repo)
    self.ask(repo, run, step)
    event = self.events(repo)[-1]
    self.assertEqual((event["event"], event["phase"], event["waiting_on"]), ("step", "start", "owner"))
    self.assertTrue(all(isinstance(event[key], str) for key in self.OLDER_STEP_KEYS[2:]), event)
    self.assertEqual(event["v"], relayrun.VERSION)
    self.assertTrue(relayrun.valid_event(event))
    self.assertEqual(list(event["question"])[:3], ["id", "title", "options"])
    # What an older reader keeps of it: the same event without the keys it does not know.
    older = {key: event[key] for key in self.OLDER_STEP_KEYS}
    self.assertEqual(set(event) - set(older), {"waiting_on", "question"})
    self.assertTrue(relayrun.valid_event(older))
    self.assertEqual(older["phase"], "start")

  def test_Ask_Validation_RefusedAsBadRunFieldAndNothingWritten(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    base = ["relay-run", "ask", run, "--step", step]
    two = ["--option", "a:A", "--option", "b:B"]
    cases = {
      "blank title": [*base, "--title", " ", *two],
      "title with newline": [*base, "--title", "a\nb", *two],
      "title with a pipe": [*base, "--title", "a | b", *two],
      "no option": [*base, "--title", "T"],
      "one option": [*base, "--title", "T", "--option", "a:A"],
      "five options": [
        *base, "--title", "T",
        *[arg for n in "abcde" for arg in ("--option", f"{n}:{n.upper()}")],
      ],
      "option without a summary": [*base, "--title", "T", "--option", "a", "--option", "b:B"],
      "option with a blank summary": [*base, "--title", "T", "--option", "a: ", "--option", "b:B"],
      "option with a blank label": [*base, "--title", "T", "--option", ":A", "--option", "b:B"],
      "duplicate labels": [*base, "--title", "T", "--option", "a:A", "--option", "a:Again"],
      "summary with a semicolon": [*base, "--title", "T", "--option", "a:A; B", "--option", "b:B"],
      "summary with a pipe": [*base, "--title", "T", "--option", "a:A | B", "--option", "b:B"],
      "label with a semicolon": [*base, "--title", "T", "--option", "a;c:A", "--option", "b:B"],
      "label with a pipe": [*base, "--title", "T", "--option", "a|c:A", "--option", "b:B"],
      "recommend names no option": [*base, "--title", "T", *two, "--recommend", "c"],
      "key with a pipe": [*base, "--title", "T", *two, "--key", "a|b"],
      "key with a space": [*base, "--title", "T", *two, "--key", "a b"],
      "reasoning with a newline": [*base, "--title", "T", *two, "--reasoning", "a\nb"],
    }
    for name, argv in cases.items():
      with self.subTest(name):
        self.refused(repo, *argv, error="bad_run_field")
    # A colon in a summary belongs to the summary: the label ends at the first one.
    got = self.ask(repo, run, step, "--option", "a:Use b: it is faster", "--option", "b:B")
    self.assertEqual(got["question"]["options"][0], {"label": "a", "summary": "Use b: it is faster"})

  def test_Ask_EachCharacterTheRuleNames_IsBadRunField(self) -> None:
    """The run report's value rule (relayrun.QUESTION_VALUE_RULE) names `|` in every value but
    `reasoning`, and `;` in a label or summary. `ask` refuses each one in each field it names."""
    repo = self.project()
    run, step = self.started(repo)
    base = ["relay-run", "ask", run, "--step", step]
    two = ["--option", "a:A", "--option", "b:B"]
    cases = {
      "pipe in title": [*base, "--title", "T|x", *two],
      "pipe in a label": [*base, "--title", "T", "--option", "a|x:A", "--option", "b:B"],
      "pipe in a summary": [*base, "--title", "T", "--option", "a:A|x", "--option", "b:B"],
      "pipe in key": [*base, "--title", "T", *two, "--key", "a|b"],
      "semicolon in a label": [*base, "--title", "T", "--option", "a;x:A", "--option", "b:B"],
      "semicolon in a summary": [*base, "--title", "T", "--option", "a:A;x", "--option", "b:B"],
    }
    for name, argv in cases.items():
      with self.subTest(name):
        self.refused(repo, *argv, error="bad_run_field")
    # `a|b` is also no option label, so only the message shows that the pipe check refused it.
    payload = self.refused(
      repo, *base, "--title", "T", *two, "--recommend", "a|b", error="bad_run_field",
    )
    self.assertIn("recommend may not contain `|`", payload["message"])
    got = self.ask(repo, run, step, "--reasoning", "x | y; z")
    self.assertEqual(got["question"]["reasoning"], "x | y; z")

  def test_Ask_Reasoning_MayHoldAPipe(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    got = self.ask(repo, run, step, "--option", "a:A", "--option", "b:B", "--reasoning", "x | y; z")
    self.assertEqual(got["question"]["reasoning"], "x | y; z")

  def test_Ask_StepNotInRun_BadRunField(self) -> None:
    repo = self.project()
    run, _ = self.started(repo)
    self.refused(
      repo, "relay-run", "ask", run, "--step", "9", "--title", "T", "--option", "a:A",
      "--option", "b:B", error="bad_run_field",
    )

  def test_Ask_WithoutStep_IsUsage(self) -> None:
    repo = self.project()
    run, _ = self.started(repo)
    code, out, err = repo.run(
      "relay-run", "ask", run, "--title", "T", "--option", "a:A", "--option", "b:B",
    )
    self.assertEqual((code, out), (2, ""))
    self.assertIn("--step", err)

  def test_Ask_StepAlreadyDoneOrFailed_BadRunField(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    for phase in ("done", "fail"):
      with self.subTest(phase=phase):
        self.ok(repo, "relay-run", "step", run, "--step", step, "--phase", phase)
        self.refused(
          repo, "relay-run", "ask", run, "--step", step, "--title", "T", "--option", "a:A",
          "--option", "b:B", error="bad_run_field",
        )

  def test_Ask_StepWithAnOpenQuestion_QuestionOpenUntilAnswered(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.ask(repo, run, step)
    again = ["relay-run", "ask", run, "--step", step, "--title", "T", "--option", "a:A",
             "--option", "b:B"]
    payload = self.refused(repo, *again, error="question_open")
    self.assertIn("q1", payload["message"])
    self.ok(repo, "relay-run", "answer", run, "q1", "--option", "db")
    self.assertEqual(self.ok(repo, *again)["question"]["id"], "q2")

  def test_Ask_UnknownRun_UnknownRun(self) -> None:
    repo = self.project()
    self.refuse(
      repo, "relay-run", "ask", "run-nope", "--step", "1", "--title", "T", "--option", "a:A",
      "--option", "b:B", code=2, error="unknown_run",
    )

  def test_Ask_EndedRun_RunEnded(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.ok(repo, "relay-run", "end", run, "--outcome", "stopped", "--close-steps", "done")
    self.refused(
      repo, "relay-run", "ask", run, "--step", step, "--title", "T", "--option", "a:A",
      "--option", "b:B", error="run_ended",
    )

  def test_Ask_Text_PrintsOnlyTheQuestionId(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    code, out, err = repo.run(
      "relay-run", "ask", run, "--step", step, "--title", "T", "--option", "a:A",
      "--option", "b:B",
    )
    self.assertEqual((code, out, err), (0, "q1\n", ""))

  def test_Answer_Option_RecordsItAndEndsTheOwnerWait(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.ask(repo, run, step)
    got = self.ok(
      repo, "relay-run", "answer", run, "q1", "--option", "db", "--why", "scales", "--by", "jon",
    )
    question = got["question"]
    self.assertEqual(question["answer"], {"option": "db", "text": "", "why": "scales", "by": "jon"})
    self.assertRegex(question["answered_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
    self.assertEqual((got["step"]["phase"], got["step"]["waiting_on"]), ("start", ""))
    self.assertEqual(got["step"]["question"], question)
    listed = self.runs(repo)[0]
    self.assertEqual(listed["counts"]["waiting"], 0)
    self.assertEqual(listed["steps"][0]["question"], question)

  def test_Answer_Text_RecordsFreeTextWithNoOption(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.ask(repo, run, step)
    got = self.ok(repo, "relay-run", "answer", run, "q1", "--text", "Neither.\nUse sqlite.")
    self.assertEqual(
      got["question"]["answer"],
      {"option": "", "text": "Neither.\nUse sqlite.", "why": "", "by": ""},
    )

  def test_Answer_AnsweredAt_IsTheEventStampAndTheClockRestartsFromIt(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    ops.relay_run_ask(repo.root, run, step=step, title="T", options=["a:A", "b:B"], now=minutes(1))
    _, _, question = ops.relay_run_answer(repo.root, run, "q1", option="a", now=minutes(500))
    self.assertEqual(question.answered_at, relayrun.stamp(minutes(500)))
    self.assertEqual(
      relayrun.list_runs(repo.root, now=minutes(520), siblings=False)[0].state, "active",
    )
    self.assertEqual(
      relayrun.list_runs(repo.root, now=minutes(531), siblings=False)[0].state, "stale",
    )

  def test_Answer_Event_IsAStartHeartbeatAnOlderReaderFolds(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.ask(repo, run, step)
    self.ok(repo, "relay-run", "answer", run, "q1", "--option", "db")
    event = self.events(repo)[-1]
    self.assertEqual((event["event"], event["phase"]), ("step", "start"))
    self.assertNotIn("waiting_on", event)
    self.assertEqual(event["answer"]["question"], "q1")
    self.assertTrue(all(isinstance(event[key], str) for key in self.OLDER_STEP_KEYS[2:]), event)
    self.assertEqual(set(event) - set(self.OLDER_STEP_KEYS), {"answer"})
    self.assertTrue(relayrun.valid_event(event))

  def test_Answer_StepAlreadyDone_StaysDoneAndTheAnswerIsKept(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.ask(repo, run, step)
    self.ok(repo, "relay-run", "step", run, "--step", step, "--phase", "done")
    got = self.ok(repo, "relay-run", "answer", run, "q1", "--option", "file")
    self.assertEqual(got["step"]["phase"], "done")
    self.assertEqual(got["question"]["answer"]["option"], "file")

  def test_Answer_StepWaitingOnApproval_KeepsThatWait(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.ask(repo, run, step)
    self.ok(repo, "relay-run", "step", run, "--step", step, "--phase", "wait", "--waiting-on", "approval")
    got = self.ok(repo, "relay-run", "answer", run, "q1", "--option", "file")
    self.assertEqual((got["step"]["phase"], got["step"]["waiting_on"]), ("wait", "approval"))

  def test_Answer_OptionAndText_IsUsageAndNeitherIsUsage(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.ask(repo, run, step)
    for argv in (
      ["--option", "db", "--text", "x"], [], ["--why", "because"],
    ):
      with self.subTest(argv=argv):
        self.refused(repo, "relay-run", "answer", run, "q1", *argv, error="usage")

  def test_Answer_OptionNotAmongTheQuestions_BadRunField(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.ask(repo, run, step)
    payload = self.refused(
      repo, "relay-run", "answer", run, "q1", "--option", "nope", error="bad_run_field",
    )
    self.assertIn("file", payload["message"])
    self.assertIn("db", payload["message"])

  def test_Answer_BlankTextOrNewlineInBy_BadRunField(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.ask(repo, run, step)
    for argv in (["--text", "  "], ["--option", "db", "--by", "a\nb"]):
      with self.subTest(argv=argv):
        self.refused(repo, "relay-run", "answer", run, "q1", *argv, error="bad_run_field")

  def test_Answer_UnknownQuestion_UnknownQuestion(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.ask(repo, run, step)
    payload = self.refused(
      repo, "relay-run", "answer", run, "q9", "--option", "db", error="unknown_question",
    )
    self.assertIn("q9", payload["message"])

  def test_Answer_QuestionOfAnotherRun_UnknownQuestion(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.ask(repo, run, step)
    other = self.begin(repo, "s2")
    self.refused(
      repo, "relay-run", "answer", other, "q1", "--option", "db", error="unknown_question",
    )

  def test_Answer_AlreadyAnswered_QuestionAnsweredAndTheFirstAnswerStands(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.ask(repo, run, step)
    self.ok(repo, "relay-run", "answer", run, "q1", "--option", "file")
    self.refused(
      repo, "relay-run", "answer", run, "q1", "--option", "db", error="question_answered",
    )
    got = self.ok(repo, "relay-run", "answer", run, "q1", "--get")
    self.assertEqual(got["question"]["answer"]["option"], "file")

  def test_Answer_UnknownRunAndEndedRun_UnknownRunAndRunEnded(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.ask(repo, run, step)
    self.refuse(repo, "relay-run", "answer", "run-nope", "q1", "--option", "db", code=2, error="unknown_run")
    self.ok(repo, "relay-run", "end", run, "--outcome", "stopped", "--close-steps", "done")
    self.refused(repo, "relay-run", "answer", run, "q1", "--option", "db", error="run_ended")

  def test_Answer_Text_PrintsOnlyTheQuestionId(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.ask(repo, run, step)
    code, out, err = repo.run("relay-run", "answer", run, "q1", "--option", "db")
    self.assertEqual((code, out, err), (0, "q1\n", ""))

  def test_AnswerGet_Answered_PrintsTheAnswerAndJsonCarriesTheQuestion(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.ask(repo, run, step)
    self.ok(repo, "relay-run", "answer", run, "q1", "--option", "db", "--why", "scales")
    code, out, err = repo.run("relay-run", "answer", run, "q1", "--get")
    self.assertEqual((code, out, err), (0, "db\nwhy: scales\n", ""))
    got = self.ok(repo, "relay-run", "answer", run, "q1", "--get")
    self.assertEqual(list(got), ["run", "step", "question"])
    self.assertEqual(got["question"]["answer"]["option"], "db")
    self.assertEqual(got["step"]["id"], step)
    self.ok(repo, "relay-run", "answer", run, "q1", "--get", "--lean")

  def test_AnswerGet_FreeText_PrintsTheText(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.ask(repo, run, step)
    self.ok(repo, "relay-run", "answer", run, "q1", "--text", "sqlite")
    code, out, err = repo.run("relay-run", "answer", run, "q1", "--get")
    self.assertEqual((code, out, err), (0, "sqlite\n", ""))

  def test_AnswerGet_Unanswered_ExitsZeroWithNoAnswer(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.ask(repo, run, step)
    got = self.ok(repo, "relay-run", "answer", run, "q1", "--get")
    self.assertEqual((got["question"]["answered_at"], got["question"]["answer"]), ("", None))
    code, out, err = repo.run("relay-run", "answer", run, "q1", "--get")
    self.assertEqual((code, out, err), (0, "unanswered\n", ""))

  def test_AnswerGet_WritesNothingAndWorksOnAnEndedRun(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.ask(repo, run, step)
    self.ok(repo, "relay-run", "answer", run, "q1", "--option", "db")
    self.ok(repo, "relay-run", "end", run, "--outcome", "pass", "--close-steps", "done")
    before = self.ledger_bytes(repo)
    got = self.ok(repo, "relay-run", "answer", run, "q1", "--get")
    self.assertEqual(got["question"]["answer"]["option"], "db")
    self.assertEqual(self.ledger_bytes(repo), before)

  def test_AnswerGet_UnknownQuestionOrRun_Refused(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.ask(repo, run, step)
    self.refused(repo, "relay-run", "answer", run, "q9", "--get", error="unknown_question")
    self.refused(repo, "relay-run", "answer", "run-nope", "q1", "--get", error="unknown_run")

  def test_AnswerGet_WithAnAnswerFlag_IsUsage(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.ask(repo, run, step)
    for flag in (["--option", "db"], ["--text", "x"], ["--why", "w"], ["--by", "jon"]):
      with self.subTest(flag=flag):
        self.refused(repo, "relay-run", "answer", run, "q1", "--get", *flag, error="usage")

  def test_List_Json_StepWithoutAQuestion_HasNullQuestionLast(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    self.step(repo, run)
    data = self.runs(repo)[0]
    self.assertEqual(
      list(data["steps"][0]),
      ["id", "role", "agent", "vendor", "model", "reasoning", "phase", "waiting_on", "task",
       "parent", "begun_at", "updated_at", "question", "reason"],
    )
    self.assertIsNone(data["steps"][0]["question"])
    self.assertEqual(relayrun.Run.from_dict(data).to_dict(), data)

  def test_List_Json_OpenAndAnsweredQuestion_RoundTripThroughFromDict(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.ask(repo, run, step)
    open_data = self.runs(repo)[0]
    self.assertEqual(relayrun.Run.from_dict(open_data).to_dict(), open_data)
    self.ok(repo, "relay-run", "answer", run, "q1", "--text", "x", "--by", "jon")
    done_data = self.runs(repo)[0]
    self.assertEqual(relayrun.Run.from_dict(done_data).to_dict(), done_data)

  def test_List_StepWithSeveralQuestions_ShowsTheLatestAndKeepsEachByIdForAnswers(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.ask(repo, run, step)
    self.ok(repo, "relay-run", "answer", run, "q1", "--option", "file")
    self.ask(repo, run, step)
    self.assertEqual(self.runs(repo)[0]["steps"][0]["question"]["id"], "q2")
    got = self.ok(repo, "relay-run", "answer", run, "q1", "--get")
    self.assertEqual(got["question"]["answer"]["option"], "file")

  def test_List_Text_OpenQuestion_ShowsUnderItsStep(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.ask(repo, run, step)
    code, out, err = repo.run("relay-run", "list")
    self.assertEqual((code, err), (0, ""))
    lines = out.splitlines()
    self.assertEqual(len(lines), 3, out)
    self.assertIn("wait:owner", lines[1])
    self.assertEqual(lines[2], "      question q1 open  Which store?")
    self.assertIn("active  waiting owner", lines[0])

  def test_List_Text_AnsweredQuestion_ShowsAnsweredStateUnderItsStep(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.ask(repo, run, step)
    self.ok(repo, "relay-run", "answer", run, "q1", "--option", "db")
    lines = repo.run("relay-run", "list")[1].splitlines()
    self.assertEqual(lines[2], "      question q1 answered  Which store?")
    self.assertNotIn("waiting", lines[0])

  def test_List_Text_StepWithoutAQuestion_IsUnchanged(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    self.step(repo, run)
    self.assertEqual(len(repo.run("relay-run", "list")[1].splitlines()), 2)

  def test_EndCloseSteps_StepWithAnOpenQuestion_ClosesIt(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.ask(repo, run, step)
    got = self.ok(repo, "relay-run", "end", run, "--outcome", "stopped", "--close-steps", "fail")
    self.assertEqual(got["closed_steps"], [step])
    self.assertEqual(got["run"]["steps"][0]["phase"], "fail")

  def test_Fold_AnswerInALaterFileAtTheSecondOfItsQuestion_AnswersIt(self) -> None:
    begin = relayrun.begin_event("r", minutes(0), ["s1"], "wt")
    step = relayrun.step_event("r", minutes(1), "1", "start", role="a", agent="A")
    ask = relayrun.step_event(
      "r", minutes(2), "1", "start", waiting_on="owner",
      question={"id": "q1", "title": "T", "options": [{"label": "a", "summary": "A"}]},
    )
    answer = relayrun.step_event("r", minutes(2), "1", "start", answer={"question": "q1", "option": "a"})
    shared, local = Path("shared.jsonl"), Path("local.jsonl")
    got = relayrun.fold(
      [(shared, [(1, begin), (2, step), (3, ask)]), (local, [(1, answer)])], minutes(3), strict=2,
    )
    question = got[0].steps[0].question
    self.assertEqual((question.id, question.answer["option"]), ("q1", "a"))
    self.assertEqual(got[0].steps[0].phase, "start")

  def test_Fold_AnswerForAQuestionNoFileHolds_IsIgnoredNotCorrupt(self) -> None:
    begin = relayrun.begin_event("r", minutes(0), ["s1"], "wt")
    step = relayrun.step_event("r", minutes(1), "1", "start", role="a", agent="A")
    answer = relayrun.step_event("r", minutes(2), "1", "start", answer={"question": "q4", "option": "a"})
    got = relayrun.fold([(Path("x.jsonl"), [(1, begin), (2, step), (3, answer)])], minutes(3))
    self.assertEqual((got[0].steps[0].phase, got[0].steps[0].question), ("start", None))

  def test_Fold_SecondAnswerOfOneQuestion_FirstStands(self) -> None:
    begin = relayrun.begin_event("r", minutes(0), ["s1"], "wt")
    step = relayrun.step_event("r", minutes(1), "1", "start", role="a", agent="A")
    ask = relayrun.step_event(
      "r", minutes(2), "1", "start", waiting_on="owner",
      question={"id": "q1", "title": "T", "options": []},
    )
    first = relayrun.step_event("r", minutes(3), "1", "start", answer={"question": "q1", "option": "a"})
    second = relayrun.step_event("r", minutes(4), "1", "start", answer={"question": "q1", "option": "b"})
    events = [(n, e) for n, e in enumerate([begin, step, ask, first, second], 1)]
    got = relayrun.fold([(Path("x.jsonl"), events)], minutes(5))
    answer = got[0].steps[0].question.answer
    self.assertEqual((answer["option"], got[0].steps[0].question.answered_at), ("a", relayrun.stamp(minutes(3))))

  def test_Ledger_MalformedQuestionOrAnswer_IsSkippedAndTheEventStillFolds(self) -> None:
    """One odd line must not blank every board: the fold skips the object, not the ledger."""
    questions = (
      "text", ["q1"], {"id": 3, "title": "T", "options": []}, {"id": "q1", "title": 3, "options": []},
      {"id": "q1", "title": "T", "options": "a"}, {"title": "T", "options": []}, {},
    )
    answers = ("text", ["q1"], {"option": "a"}, {"question": 4}, {})
    cases = [("question", value) for value in questions] + [("answer", value) for value in answers]
    for key, value in cases:
      with self.subTest(key=key, value=value):
        repo = self.project()
        run, step = self.started(repo)
        self.ask(repo, run, step)
        later = relayrun.utcnow() + timedelta(minutes=5)  # the CLI wrote the ask at the real time
        event = relayrun.step_event(run, later, step, "start")
        event[key] = value
        with (repo.root / LEDGER).open("a", encoding="utf-8") as fh:
          fh.write(json.dumps(event) + "\n")
        got = self.runs(repo)[0]["steps"][0]
        self.assertEqual(got["phase"], "start")  # the event still folded, as a heartbeat
        self.assertEqual(got["question"]["id"], "q1")
        self.assertIsNone(got["question"]["answer"])
        # And the ledger still takes writes.
        self.ok(repo, "relay-run", "answer", run, "q1", "--option", "db")

  def test_Ledger_QuestionMissingOptionalKeys_FoldsWithEmptyDefaults(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    event = relayrun.step_event(
      run, minutes(1), step, "start", waiting_on="owner",
      question={"id": "q1", "title": "T", "options": [{"label": "a"}, {"summary": "S"}, "junk"]},
    )
    with (repo.root / LEDGER).open("a", encoding="utf-8") as fh:
      fh.write(json.dumps(event) + "\n")
    question = relayrun.list_runs(repo.root, now=minutes(2), siblings=False)[0].steps[0].question
    self.assertEqual(question.options, [{"label": "a", "summary": ""}, {"label": "", "summary": "S"}])
    self.assertEqual((question.recommend, question.blocker, question.key, question.reasoning), ("", False, "", ""))

  def test_QuestionFromEvent_MissingKeys_DoesNotRaise(self) -> None:
    for data in ({}, {"options": None}, {"id": "q1", "options": "a"}):
      with self.subTest(data=data):
        question = relayrun.Question.from_event(data, "2026-10-14T12:00:00Z")
        self.assertEqual((question.options, question.asked_at), ([], "2026-10-14T12:00:00Z"))

  def test_Answer_OlderOpenQuestion_KeepsTheWaitOfANewerOne(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.ask(repo, run, step)
    self.ok(repo, "relay-run", "step", run, "--step", step, "--phase", "start")  # moves on, q1 open
    self.ask(repo, run, step)
    got = self.ok(repo, "relay-run", "answer", run, "q1", "--option", "file")
    self.assertEqual((got["step"]["phase"], got["step"]["waiting_on"]), ("wait", "owner"))
    self.assertEqual(got["step"]["question"]["id"], "q2")
    got = self.ok(repo, "relay-run", "answer", run, "q2", "--option", "db")
    self.assertEqual((got["step"]["phase"], got["step"]["waiting_on"]), ("start", ""))

  def test_Ask_StepWaitingOnApproval_BadRunFieldNamingTheApprovalAndNothingWritten(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.ok(repo, "relay-run", "step", run, "--step", step, "--phase", "wait", "--waiting-on", "approval")
    payload = self.refused(
      repo, "relay-run", "ask", run, "--step", step, "--title", "T", "--option", "a:A",
      "--option", "b:B", error="bad_run_field",
    )
    self.assertIn("approval", payload["message"])
    self.assertEqual(self.runs(repo)[0]["steps"][0]["waiting_on"], "approval")

  def test_Ask_QuestionOpenMessage_NamesTheOwnerWaitAndTheUnansweredQuestion(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    self.ask(repo, run, step)
    payload = self.refused(
      repo, "relay-run", "ask", run, "--step", step, "--title", "T", "--option", "a:A",
      "--option", "b:B", error="question_open",
    )
    self.assertIn("waits on the owner", payload["message"])
    self.assertIn("q1 is not answered", payload["message"])
    self.assertNotIn("approval", payload["message"])

  def test_Ledger_QuestionWithExtraKeysFromANewerSlicer_StillFolds(self) -> None:
    repo = self.project()
    run, step = self.started(repo)
    event = relayrun.step_event(
      run, minutes(1), step, "start", waiting_on="owner",
      question={"id": "q1", "title": "T", "options": [{"label": "a", "summary": "A", "extra": 1}],
                "deadline": "soon"},
    )
    with (repo.root / LEDGER).open("a", encoding="utf-8") as fh:
      fh.write(json.dumps(event) + "\n")
    got = relayrun.list_runs(repo.root, now=minutes(500), siblings=False)[0]
    self.assertEqual((got.state, got.steps[0].question.id), ("active", "q1"))


class AskAnswerSandboxTests(SandboxCase):
  """A question asked from a sandbox that cannot write the shared ledger (s396)."""

  def test_Ask_SandboxedFallbackFile_PrefixesTheQuestionIdLikeAStepId(self) -> None:
    repo = self.repo()
    wt, run = self.worktree_with_run(repo)
    step = ops.relay_run_step(wt, run, role="orchestrator", agent="A", phase="start", now=minutes(1))[1].id
    first = ops.relay_run_ask(wt, run, step=step, title="T", options=["a:A", "b:B"], now=minutes(2))[2]
    self.assertEqual(first.id, "q1")
    self.sandbox(repo)
    ops.relay_run_answer(wt, run, "q1", option="a", now=minutes(3))
    asked = ops.relay_run_ask(wt, run, step=step, title="T", options=["a:A", "b:B"], now=minutes(4))[2]
    self.assertRegex(asked.id, r"^c[0-9a-f]{8}-q1$")
    self.assertEqual(self.local(wt)[-1]["question"]["id"], asked.id)
    answered = ops.relay_run_answer(wt, run, asked.id, text="x", now=minutes(5))[2]
    self.assertEqual(answered.answer["text"], "x")
    self.assertEqual(self.listed(repo.root)[0]["steps"][0]["question"]["id"], asked.id)

  def test_Answer_QuestionHeldOnlyInTheFallbackFile_AbsorbsItIntoTheSharedLedgerFirst(self) -> None:
    repo = self.repo()
    wt, run = self.worktree_with_run(repo)
    step = ops.relay_run_step(wt, run, role="orchestrator", agent="A", phase="start", now=minutes(1))[1].id
    lift = self.sandbox(repo)
    asked = ops.relay_run_ask(wt, run, step=step, title="T", options=["a:A", "b:B"], now=minutes(2))[2]
    lift()
    ops.relay_run_answer(repo.root, run, asked.id, option="a", now=minutes(3))
    shared = [e for _, e in relayrun.read_events(self.common(repo))]
    self.assertEqual(
      [("question" in e, "answer" in e) for e in shared if e["event"] == "step"][-2:],
      [(True, False), (False, True)],
    )


class ReasonTests(RelayRunCase):
  """`relay-run step --phase fail --reason` records why a step failed (s405)."""

  def failed(self, repo, *extra):
    run = self.begin(repo, "s1")
    step = self.step(repo, run)["step"]["id"]
    got = self.ok(repo, "relay-run", "step", run, "--step", step, "--phase", "fail", *extra)
    return run, step, got

  def events_of(self, repo):
    return [event for _, event in relayrun.read_events(repo.root / LEDGER)]

  def test_Reason_OnFail_IsRecordedAndListed(self) -> None:
    repo = self.project()
    run, step, got = self.failed(repo, "--reason", "out of credits")
    self.assertEqual((got["step"]["phase"], got["step"]["reason"]), ("fail", "out of credits"))
    self.assertEqual(self.runs(repo)[0]["steps"][0]["reason"], "out of credits")
    self.assertEqual(list(got["step"])[-1], "reason")
    code, out, err = repo.run("relay-run", "list")
    self.assertEqual((code, err), (0, ""))
    self.assertIn("out of credits", out)

  def test_Reason_StepWithoutOne_IsEmptyString(self) -> None:
    repo = self.project()
    _, _, got = self.failed(repo)
    self.assertEqual(got["step"]["reason"], "")

  def test_Reason_LaterEvent_ClearsIt(self) -> None:
    repo = self.project()
    run, step, _ = self.failed(repo, "--reason", "rate limit")
    got = self.ok(repo, "relay-run", "step", run, "--step", step, "--phase", "start")
    self.assertEqual(got["step"]["reason"], "")

  def test_Reason_WithoutFailPhase_UsageAndNothingWritten(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    step = self.step(repo, run)["step"]["id"]
    before = repo.read(LEDGER)
    for phase in ("start", "done", "wait"):
      with self.subTest(phase=phase):
        payload = self.refuse(
          repo, "relay-run", "step", run, "--step", step, "--phase", phase,
          "--reason", "rate limit", code=2, error="usage",
        )
        self.assertIn("--phase fail", payload["message"])
    self.assertEqual(repo.read(LEDGER), before)

  def test_Reason_WithNewline_BadRunField(self) -> None:
    repo = self.project()
    run = self.begin(repo, "s1")
    step = self.step(repo, run)["step"]["id"]
    self.refuse(
      repo, "relay-run", "step", run, "--step", step, "--phase", "fail",
      "--reason", "a\nb", code=2, error="bad_run_field",
    )

  def test_Reason_Event_IsWrittenOnlyWhenGiven(self) -> None:
    repo = self.project()
    run, step, _ = self.failed(repo)
    self.assertNotIn("reason", self.events_of(repo)[-1])
    self.ok(repo, "relay-run", "step", run, "--step", step, "--phase", "fail", "--reason", "x")
    self.assertEqual(self.events_of(repo)[-1]["reason"], "x")

  def test_Reason_NotAString_IsCorrupt(self) -> None:
    repo = self.project()
    self.failed(repo, "--reason", "x")
    lines = repo.read(LEDGER).splitlines()
    event = json.loads(lines[-1])
    event["reason"] = 3
    lines[-1] = json.dumps(event)
    (repo.root / LEDGER).write_text("\n".join(lines) + "\n")
    code, out, _ = repo.run("relay-run", "list", "--json")
    self.assertEqual(code, 3)
    self.assertIn("reason", json.loads(out)["error"]["message"])


class EndReasonTests(RelayRunCase):
  """`relay-run end --close-steps fail --reason` records why each step it closes failed (s423)."""

  # The keys of a step event written without a reason, in the order `step_event` writes them.
  STEP_KEYS = (
    "v", "event", "run", "at", "step", "phase", "role", "agent", "vendor", "model", "reasoning",
    "task", "parent",
  )

  def two_running(self, repo: support.TempRepo) -> str:
    run = self.begin(repo, "s1")
    self.step(repo, run, "--task", "write tests")
    self.step(repo, run, "--task", "review")
    return run

  def events_of(self, repo: support.TempRepo) -> list[dict]:
    return [event for _, event in relayrun.read_events(repo.root / LEDGER)]

  def test_RelayRunEnd_CloseStepsFailReason_RecordsReasonOnEachClosedStep(self) -> None:
    repo = self.project()
    run = self.two_running(repo)
    got = self.ok(repo, "relay-run", "end", run, "--outcome", "stopped", "--close-steps", "fail",
                  "--reason", "owner stopped")
    self.assertEqual(got["closed_steps"], ["1", "2"])
    steps = self.runs(repo)[0]["steps"]
    self.assertEqual([(s["phase"], s["reason"]) for s in steps], [("fail", "owner stopped")] * 2)
    # The reason rides on the step events only; the end event carries none.
    self.assertEqual([e.get("reason") for e in self.events_of(repo)[-3:]],
                     ["owner stopped", "owner stopped", None])

  def test_RelayRunEnd_ReasonWithoutCloseStepsFail_IsBadRunField(self) -> None:
    repo = self.project()
    run = self.two_running(repo)
    before = repo.read(LEDGER)
    for extra in ((), ("--close-steps", "done")):
      with self.subTest(close_steps=extra):
        payload = self.refuse(
          repo, "relay-run", "end", run, "--outcome", "pass", "--reason", "x", *extra,
          code=2, error="bad_run_field",
        )
        self.assertIn("--close-steps fail", payload["message"])
    self.assertEqual(repo.read(LEDGER), before)

  def test_RelayRunEnd_ReasonMultiLine_IsRefused(self) -> None:
    repo = self.project()
    run = self.two_running(repo)
    before = repo.read(LEDGER)
    self.refuse(
      repo, "relay-run", "end", run, "--outcome", "stopped", "--close-steps", "fail",
      "--reason", "a\nb", code=2, error="bad_run_field",
    )
    self.assertEqual(repo.read(LEDGER), before)

  def test_RelayRunEnd_CloseStepsFailNoReason_Unchanged(self) -> None:
    repo = self.project()
    run = self.two_running(repo)
    before = len(self.events_of(repo))
    self.ok(repo, "relay-run", "end", run, "--outcome", "stopped", "--close-steps", "fail")
    added = self.events_of(repo)[before:]
    self.assertEqual([e["event"] for e in added], ["step", "step", "end"])
    for event in added[:2]:
      self.assertEqual(list(event), list(self.STEP_KEYS))
      self.assertEqual((event["phase"], event["role"], event["task"], event["parent"]),
                       ("fail", "", "", ""))
    self.assertEqual(list(added[2]), ["v", "event", "run", "at", "outcome"])


if __name__ == "__main__":
  unittest.main()
