"""Relay commands: the `relay-roles` matrix editor and the `relay-run` ledger.

Both groups serve the slice relay, so their handlers and the helpers only they use live
together. Each handler is attached to its subparser in `cli_parser`; shared helpers come
from `cli_common`, and nothing here imports `cli`.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from slicer import ops, relayrun, store, vcs
from slicer import roles as role_matrix
from slicer.cli_common import (
  OK, _dump_json, _emit, _log_timestamp, _read_user_file, _state, _write_out,
)
from slicer.errors import StateError


def cmd_ai_relay_roles(args: argparse.Namespace) -> int:
  """`ai relay-roles` is `relay-roles show`."""
  return cmd_relay_roles_show(args)


def _csv(value: str | None) -> list[str] | None:
  """A comma-separated flag value. None means the flag was not given; empty clears."""
  if value is None:
    return None
  if value.strip().casefold() == "any":
    return []
  return [part.strip() for part in value.split(",") if part.strip()]


def _read_matrix(args: argparse.Namespace) -> role_matrix.RoleMatrix:
  """The matrix without loading the rest of the project: no lock, no write."""
  root = store.discover(Path(args.root).resolve() if args.root else None)
  return role_matrix.require_matrix(
    store.read_roles(root), root / store.DIR_NAME / store.ROLES_NAME,
  )


def cmd_relay_roles_show(args: argparse.Namespace) -> int:
  matrix = _read_matrix(args)
  text = role_matrix.render_markdown(matrix)
  payload = {
    "relay_roles": text,
    "rows": [row.to_dict() for row in matrix.rows],
    "rules": [rule.to_dict() for rule in matrix.rules],
    "revision": role_matrix.revision(matrix),
  }
  _emit(args, payload, text.rstrip("\n"), keep_empty=("rows", "rules"))
  return OK


def cmd_relay_roles_recommend(args: argparse.Namespace) -> int:
  """Match a slice against the matrix. Reads only: no lock, no write, nothing stored."""
  well_specified = ops.parse_yes_no("well-specified", args.well_specified)
  split = ops.parse_yes_no("split", args.split)
  select = role_matrix.parse_selectors(args.select or [])
  matrix = _read_matrix(args)
  item = _state(args).index.require(args.id)
  picks = role_matrix.resolve(
    matrix, item, well_specified, split, prefer_agent=args.prefer_agent, select=select,
  )
  payload = {
    "id": item.id,
    "well_specified": well_specified,
    "split": split,
    "sliver": role_matrix.is_sliver(item),
    "roles": [pick.to_dict() for pick in picks],
    "unmatched": [pick.role for pick in picks if pick.row is None],
  }
  if args.json:
    # The shape is the contract, so --lean must not drop a null row or an empty list;
    # it only makes the output compact.
    _dump_json(args, payload)
  else:
    _emit(args, payload, "\n".join(line for pick in picks for line in role_matrix.recommend_lines(pick)))
  return OK


def cmd_relay_roles_init(args: argparse.Namespace) -> int:
  state = _state(args)
  written, removed = ops.roles_init(
    state, force=args.force, replace_legacy=args.replace_legacy,
  )
  payload: dict[str, object] = {"written": [str(p) for p in written]}
  if removed:
    payload["removed"] = [str(old) for old in removed]
  lines = [f"wrote {p}" for p in written] + [f"removed {old}" for old in removed]
  _emit(args, payload, "\n".join(lines), keep_empty=("written",))
  return OK


def _row_fields(args: argparse.Namespace) -> dict[str, object]:
  return {
    "size": _csv(args.size),
    "effort": _csv(args.effort),
    "importance": _csv(args.importance),
    "urgency": _csv(args.urgency),
    "well_specified": args.well_specified,
    "split": args.split,
    "sliver": args.sliver,
    "agent": args.agent,
    "vendor": args.vendor,
    "model": args.model,
    "reasoning": args.reasoning,
    "note": args.note,
  }


def _row_line(row: role_matrix.RoleRow) -> str:
  return f"{row.id}  {row.role}  {row.when.describe()}  {row.agent}  {row.model}  {row.reasoning}"


def cmd_relay_roles_add(args: argparse.Namespace) -> int:
  state = _state(args)
  fields = {k: v for k, v in _row_fields(args).items() if v is not None}
  row = ops.role_add(state, args.role, **fields)
  _emit(args, row.to_dict(), f"added {_row_line(row)}")
  return OK


def cmd_relay_roles_set(args: argparse.Namespace) -> int:
  state = _state(args)
  row, changed = ops.role_set(state, args.id, role=args.role, **_row_fields(args))
  if not changed:
    args._no_change = True
  _emit(args, row.to_dict(), f"set {_row_line(row)}" if changed else f"{row.id} unchanged")
  return OK


def cmd_relay_roles_remove(args: argparse.Namespace) -> int:
  state = _state(args)
  row = ops.role_remove(state, args.id)
  _emit(args, row.to_dict(), f"removed {row.id}  {row.role}")
  return OK


def cmd_relay_roles_move(args: argparse.Namespace) -> int:
  state = _state(args)
  row, moved = ops.role_move(state, args.id, before=args.before, last=args.last)
  if not moved:
    args._no_change = True
  _emit(args, row.to_dict(), f"moved {row.id}" if moved else f"{row.id} unchanged")
  return OK


def cmd_relay_roles_rule_add(args: argparse.Namespace) -> int:
  state = _state(args)
  rule = ops.rule_add(state, args.text)
  _emit(args, rule.to_dict(), f"added {rule.id}  {rule.text}")
  return OK


def cmd_relay_roles_rule_edit(args: argparse.Namespace) -> int:
  state = _state(args)
  rule, changed = ops.rule_edit(state, args.id, args.text)
  if not changed:
    args._no_change = True
  _emit(args, rule.to_dict(), f"edited {rule.id}" if changed else f"{rule.id} unchanged")
  return OK


def cmd_relay_roles_rule_remove(args: argparse.Namespace) -> int:
  state = _state(args)
  rule = ops.rule_remove(state, args.id)
  _emit(args, rule.to_dict(), f"removed {rule.id}")
  return OK


def _change_set_text(args: argparse.Namespace) -> str:
  """The change set from exactly one of `--file` and `--stdin`."""
  if args.file is not None and args.stdin:
    raise StateError("choose only one of --file or --stdin", code="usage")
  if args.stdin:
    return sys.stdin.read()
  if args.file is None:
    raise StateError("give the change set with --file or --stdin", code="usage")
  path = Path(args.file)
  if not path.is_absolute():
    path = Path(args.root or ".").resolve() / path
  return _read_user_file(path)


_STEP_WORDS = {
  "add": "added", "remove": "removed", "move": "moved", "set": "set",
  "rule-add": "added", "rule-remove": "removed", "rule-edit": "edited", "rule-move": "moved",
}


def cmd_relay_roles_apply(args: argparse.Namespace) -> int:
  """Check a change set against a copy of the matrix, then save it once (see `ops.roles_apply`)."""
  change_set = role_matrix.parse_change_set(_change_set_text(args))
  result = ops.roles_apply(_state(args), change_set, dry_run=args.dry_run)
  if not result.changed:
    args._no_change = True
  lines = [
    f"{_STEP_WORDS[step['op']]} {step['id']}" if step["changed"] else f"{step['id']} unchanged"
    for step in result.steps
  ]
  if change_set.replace is not None:
    count = len(result.steps)
    lines.insert(0, f"replace: {count} {'step' if count == 1 else 'steps'}")
  if args.dry_run:
    lines.append("dry run: nothing was written")
  lines.append(
    f"revision {result.revision}" if result.changed
    else f"revision {result.revision} (unchanged)"
  )
  _emit(args, result.to_dict(), "\n".join(lines), keep_empty=("steps", "rows", "rules"))
  return OK


def _run_root(args: argparse.Namespace) -> Path:
  return store.discover(Path(args.root).resolve() if args.root else None)


def _emit_run(args: argparse.Namespace, payload: dict[str, object], text: str) -> None:
  """A `relay-run` result. The shape is the contract, so `--lean` only compacts it."""
  if getattr(args, "json", False):
    _dump_json(args, payload)
  elif text:
    _write_out(args, text)


def _step_line(step: relayrun.Step, run: relayrun.Run) -> str:
  phase = f"{step.phase}:{step.waiting_on}" if step.waiting_on else step.phase
  if step.waiting and step.waiting_since:  # how long the wait has run, as of now or the run's end
    phase += f" {relayrun.elapsed_text(step.waiting_since, run.clock(relayrun.utcnow()))}"
  cells = [step.role, step.agent, step.model, step.reasoning, phase, step.task]
  line = ("    " if step.parent else "  ") + f"{step.id}  " + "  ".join(c or "-" for c in cells)
  return f"{line}  reason: {step.reason}" if step.reason else line


def _question_line(step: relayrun.Step) -> str | None:
  """The step's latest question under its line in the text list: its id, whether it is still
  open, its title, and the open count when several questions remain open."""
  question = step.question
  if question is None:
    return None
  state = "open" if question.answer is None else "answered"
  open_count = sum(q.answer is None for q in step.questions)
  count = f" ({open_count} open questions)" if open_count > 1 else ""
  return f"      question {question.id} {state}  {question.title}{count}"


def _say_fallback(report: ops.LedgerReport) -> None:
  """One stderr line when a write went to the checkout's own file: the caller's sandbox
  refused the shared ledger, and the line says where the events are and how they get there."""
  if report.fell_back_from is not None:
    print(
      f"slicer: cannot write the shared relay-run ledger {report.fell_back_from} "
      f"(permission denied); recorded in {report.wrote_to} instead. "
      "`relay-run end`, run where the shared ledger is writable, copies it there",
      file=sys.stderr,
    )


def _say_skipped(report: ops.LedgerReport) -> None:
  """What a copy into the shared ledger left out, so a quiet `end` does not hide it."""
  parts = [f"unreadable local ledger {path}" for path in report.unreadable]
  if report.invalid:
    parts.append(_count_of(report.invalid, "invalid event") + " in the local ledgers")
  if parts:
    print(f"slicer: not copied to the shared relay-run ledger: {'; '.join(parts)}", file=sys.stderr)


def _say_orphaned(report: ops.LedgerReport) -> None:
  """One stderr line per step copied without its parent, so a step that `list` shows as a root
  is not a surprise: the parent is in no ledger the copy held."""
  for step, parent in report.orphaned:
    print(
      f"slicer: step {step} copied to the shared relay-run ledger without its parent {parent}, "
      "which no copied ledger holds; `relay-run list` shows it as a root step",
      file=sys.stderr,
    )


def cmd_relay_run_begin(args: argparse.Namespace) -> int:
  """Not a roadmap write: `ops` takes the project lock and appends to the ledger only."""
  report = ops.LedgerReport()
  run = ops.relay_run_begin(_run_root(args), args.items, args.worktree, report=report)
  _say_fallback(report)
  _say_skipped(report)
  _emit_run(args, {"run": run.to_dict()}, run.id)
  return OK


def cmd_relay_run_step(args: argparse.Namespace) -> int:
  report = ops.LedgerReport()
  run, step = ops.relay_run_step(
    _run_root(args), args.run, phase=args.phase, step=args.step, role=args.role or "",
    agent=args.agent or "", vendor=args.vendor or "", model=args.model or "",
    reasoning=args.reasoning or "", task=args.task or "", parent=args.parent or "",
    waiting_on=args.waiting_on or "", reason=args.reason or "", verdict=args.verdict,
    report=report,
  )
  _say_fallback(report)
  _say_skipped(report)
  _say_orphaned(report)
  _emit_run(args, {"run": run.to_dict(), "step": step.to_dict()}, step.id)
  return OK


def cmd_relay_run_ask(args: argparse.Namespace) -> int:
  report = ops.LedgerReport()
  run, step, question = ops.relay_run_ask(
    _run_root(args), args.run, step=args.step, title=args.title, options=args.option or [],
    recommend=args.recommend or "", blocker=args.blocker, key=args.key or "",
    reasoning=args.reasoning or "", report=report,
  )
  _say_fallback(report)
  _say_skipped(report)
  _say_orphaned(report)
  _emit_run(
    args, {"run": run.to_dict(), "step": step.to_dict(), "question": question.to_dict()},
    question.id,
  )
  return OK


def cmd_relay_run_answer(args: argparse.Namespace) -> int:
  root = _run_root(args)
  if args.get:
    given = [flag for flag, value in (
      ("--option", args.option), ("--text", args.text), ("--why", args.why), ("--by", args.by),
    ) if value is not None]
    if given:
      raise StateError(f"--get reads an answer and takes no {', '.join(given)}", code="usage")
    run, step, question = ops.relay_run_question(root, args.run, args.question)
    answer = question.answer
    if answer is None:
      text = "unanswered"
    else:
      text = answer["option"] or answer["text"]
      if answer["why"]:
        text += f"\nwhy: {answer['why']}"
  else:
    report = ops.LedgerReport()
    run, step, question = ops.relay_run_answer(
      root, args.run, args.question, option=args.option, text=args.text,
      why=args.why or "", by=args.by or "", report=report,
    )
    _say_fallback(report)
    _say_skipped(report)
    _say_orphaned(report)
    text = question.id
  _emit_run(
    args, {"run": run.to_dict(), "step": step.to_dict(), "question": question.to_dict()}, text,
  )
  return OK


def cmd_relay_run_end(args: argparse.Namespace) -> int:
  report = ops.LedgerReport()
  run, closed = ops.relay_run_end(
    _run_root(args), args.run, args.outcome, close_steps=args.close_steps,
    reason=args.reason or "", report=report,
  )
  _say_fallback(report)
  _say_skipped(report)
  _say_orphaned(report)
  running = [step.id for step in run.unfinished_steps()]
  if running:  # `--close-steps` leaves none, so this names the steps a plain `end` left behind
    print(f"slicer: ended {run.id} with running steps: {', '.join(running)}", file=sys.stderr)
  text = f"ended {run.id} {run.outcome}"
  if report.absorbed:
    text += f"; absorbed {_count_of(report.absorbed, 'event')} from local ledgers"
  if closed:
    text += f"; closed steps: {', '.join(closed)}"
  _emit_run(args, {"run": run.to_dict(), "closed_steps": closed}, text)
  return OK


def _count_of(n: int, word: str) -> str:
  """`1 step`, `2 steps`, `0 steps`: the text list says what each number counts."""
  return f"{n} {word}" if n == 1 else f"{n} {word}s"


def cmd_relay_run_list(args: argparse.Namespace) -> int:
  root = _run_root(args)
  runs = relayrun.list_runs(root)
  if args.run is not None:
    runs = [run for run in runs if run.id == args.run]
    if not runs:
      paths = [relayrun.ledger_path(root)]
      if relayrun.common_ledger_path(root) is not None:
        paths.extend(relayrun.legacy_ledger_path(checkout)
                     for checkout in [root, *vcs.sibling_worktrees(root)])
      raise StateError(
        f"unknown run {args.run!r}; searched ledgers: {', '.join(str(path) for path in paths)}. "
        "Use relay-run list to see the known runs.", code="unknown_run",
      )
  if args.active:
    runs = [run for run in runs if run.state == "active"]
  lines = []
  for run in runs:
    counts = run.counts()
    waiting = sorted({step.waiting_on for step in run.steps if step.waiting} if run.waiting else ())
    running = f"{counts['running']} running"
    if counts["waiting"]:
      running += f", {counts['waiting']} waiting"
    lines.append(
      f"{run.id}  {run.state}{'  ' + run.outcome if run.outcome else ''}"
      f"{'  waiting ' + ','.join(waiting) if waiting else ''}  "
      f"items {','.join(run.items)}  wt {run.worktree}  "
      f"{_count_of(counts['steps'], 'step')}, {running}, "
      f"{_count_of(counts['subagents'], 'subagent')}"
    )
    for step in run.steps:
      lines.append(_step_line(step, run))
      question = _question_line(step)
      if question:
        lines.append(question)
  _emit_run(args, {"runs": [run.to_dict() for run in runs]}, "\n".join(lines) or "no relay runs")
  return OK


def _seconds_text(seconds: int | None) -> str:
  return "-" if seconds is None else relayrun.duration_text(seconds)


def _stats_text(stats: dict) -> str:
  """The short form of `relay-run stats`: a summary, a role table and where the rest is."""
  runs, wall, fixes = stats["runs"], stats["wall_seconds"], stats["fix_rounds"]
  gates, wait = stats["gates"], stats["owner_wait"]
  lines = [
    f"relay runs: {runs['total']} ({runs['pass']} pass, {runs['fail']} fail, "
    f"{runs['stopped']} stopped, {runs['unended']} unended); {stats['measured']} measured",
    f"wall time: median {_seconds_text(wall['median'])}, longest {_seconds_text(wall['max'])}",
    f"fix rounds: {fixes['total']} ({fixes['runs_with_any']} runs with any, "
    f"{fixes['runs_with_two_or_more']} with two or more)",
    f"gates: {gates['rounds']} rounds, {gates['fails']} FAIL; {gates['with_verdict']} had a "
    f"recorded verdict, {gates['by_proxy']} counted by proxy",
    f"owner wait: {wait['runs_with_wait']} runs with a recorded wait, "
    f"{_seconds_text(wait['total_seconds'])} in all, median {_seconds_text(wait['median_seconds'])}, "
    f"longest {_seconds_text(wait['max_seconds'])}",
  ]
  if stats["roles"]:
    table = [("role", "steps", "total", "median")] + [
      (role, str(row["steps"]), _seconds_text(row["total_seconds"]),
       _seconds_text(row["median_seconds"]))
      for role, row in stats["roles"].items()
    ]
    widths = [max(len(cells[column]) for cells in table) for column in range(4)]
    for cells in table:
      lines.append("  ".join(
        cell.ljust(width) if column == 0 else cell.rjust(width)
        for column, (cell, width) in enumerate(zip(cells, widths))
      ).rstrip())
  lines.append("per run: --json")
  return "\n".join(lines)


def cmd_relay_run_stats(args: argparse.Namespace) -> int:
  """Measure the ended runs. Reads the ledgers like `relay-run list`: no lock, no write."""
  since = None
  if args.since is not None:
    text = f"{args.since}T00:00:00Z" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.since) else args.since
    # Whole seconds, as the ledger's stamps are: the filter and the printed stamp then agree.
    since = _log_timestamp(text, "--since", code="usage").replace(microsecond=0)
  stats = relayrun.stats(relayrun.list_runs(_run_root(args)), since)
  _emit_run(args, stats, _stats_text(stats))
  return OK
