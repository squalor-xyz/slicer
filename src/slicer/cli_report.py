"""Report commands: `render`, `sync`, `verify`, `refs`, `check`, `stats`, `snapshot`, `log` and `tui`.

These compare, regenerate or summarise project state instead of tracking slices. `snapshot`
builds the parser through `cli_common._built_parser`, and `tui` is imported inside `cmd_tui`
so a terminal-less run never loads curses. Each handler is attached to its subparser in
`cli_parser`; shared helpers come from `cli_common`, and nothing here imports `cli`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from slicer import __version__, ai, model, ops, refs, render, snapshot, store, sync, verify
from slicer.cli_common import (
  DRIFT, OK, USAGE, _begin_write, _built_parser, _census, _check_report, _dump_json, _emit, _exit_for,
  _log_timestamp, _mutating, _state, _status_tally,
)
from slicer.errors import SlicerError, StateError


def cmd_render(args: argparse.Namespace) -> int:
  state = _state(args)
  expected = render.plan(state)
  diff = render.compare(expected, state.render_dir)
  touched = render.write(expected, state.render_dir, diff)
  _emit(args, {"written": touched}, f"rendered {len(touched)} file(s)" if touched else "render already current")
  return OK


def cmd_sync(args: argparse.Namespace) -> int:
  state = _state(args)
  findings = sync.apply(state.root, state.index, state.config, check_only=args.check)
  stale = [f for f in findings if f.stale]
  payload = [{"target": f.target, "path": f.path, "stale": f.stale, "detail": f.detail} for f in findings]
  if args.check:
    text = "\n".join(f"stale {f.path}: {f.detail}" for f in stale) or "sync targets are current"
    _emit(args, payload, text)
    return DRIFT if stale else OK
  text = "\n".join(f"wrote {f.path}" for f in stale) or "sync targets already current"
  _emit(args, payload, text)
  return OK


def cmd_verify(args: argparse.Namespace) -> int:
  if args.landed is None and args.on is not None:
    raise StateError("--on only applies to `verify --landed ID`", code="usage")
  state = _state(args)
  if args.landed is not None:
    found = verify.landed(state, args.landed, "HEAD" if args.on is None else args.on)
    _emit(args, found.to_dict(), found.message)
    return OK if found.landed else DRIFT
  offline = verify.offline(state)
  report = verify.against_git(state)
  report.findings = offline.findings + report.findings
  text = "\n".join(
    f"{f.level:<5} {f.item or '-':<5} {f.message}" for f in report.findings
  ) or f"{report.checked} items verified; nothing to report"
  _emit(args, report.to_dict(), text)
  return DRIFT if report.problems else OK


def _refs_items(state: store.State, named: list[str]) -> list[model.Item]:
  """The items `refs` scans: those named, whatever their status, else every
  item with a slice that is neither done nor retired."""
  if named:
    return [state.index.require(item_id) for item_id in dict.fromkeys(named)]
  cfg = state.config
  skip = {cfg.done_status, cfg.retired_status} - {""}
  return [item for item in state.index.items if item.id in state.slices and item.status not in skip]


def _refs_text(items: list[model.Item], located: list[refs.Located], fixed: int, drift: int) -> str:
  lines: list[str] = []
  for loc in located:
    finding = loc.finding
    anchor = f"`{loc.ref.anchor}` " if loc.ref.anchor else ""
    detail = ""
    if finding.found is not None:
      detail = f" -> {loc.ref.written(finding.found)}" + (" (fixed)" if loc.fixed else "")
    elif finding.paths:
      detail = f" ({', '.join(finding.paths)})"
    elif finding.lines:
      detail = f" (lines {', '.join(map(str, finding.lines))})"
    lines.append(
      f"{loc.item:<5} {loc.place.where}:{loc.place.base + loc.ref.line}  "
      f"{anchor}{loc.ref.written()}  {finding.status}{detail}"
    )
  summary = f"{len(located)} reference(s) in {len(items)} item(s)"
  if fixed:
    summary += f"; fixed {fixed}"
  lines.append(summary + (f"; {drift} need attention" if drift else ""))
  return "\n".join(lines)


def _refs_run(args: argparse.Namespace, state: store.State, *, fix: bool) -> int:
  """Scan, optionally fix, report; returns OK when nothing is left wrong, else DRIFT."""
  items = _refs_items(state, args.id)
  checker = refs.Checker(state.root)
  located = [loc for item in items for loc in refs.scan(item, state.slices.get(item.id), checker)]
  fixed = ops.fix_refs(state, located) if fix else 0
  counts = {status: 0 for status in refs.STATUSES}
  for loc in located:
    counts[loc.finding.status] += 1
  drift = sum(loc.drift for loc in located)
  if fix and not fixed:
    args._no_change = True
  _emit(args, {
    "fix": fix, "items": [item.id for item in items],
    "references": [loc.to_dict() for loc in located],
    "counts": counts, "fixed": fixed, "drift": drift,
  }, _refs_text(items, located, fixed, drift), keep_empty=("items", "references"))
  return DRIFT if drift else OK


def _refs_fixed(args: argparse.Namespace) -> int:
  # `_mutating` stops at a nonzero return before it renders, so the handler
  # returns OK and leaves the verdict for cmd_refs to read.
  args._refs_exit = _refs_run(args, _state(args), fix=True)
  return OK


_refs_write = _mutating(_refs_fixed)


def cmd_refs(args: argparse.Namespace) -> int:
  """Report file:line references in slice text that no longer hold; `--fix`
  rewrites the moved ones. A read unless `--fix`, which takes the writer lock."""
  if not args.fix:
    if args.render or args.strict:
      raise StateError("--render and --strict only apply with --fix", code="usage")
    return _refs_run(args, _state(args), fix=False)
  root = store.discover(Path(args.root) if args.root else None)
  with store.project_lock(root):
    _begin_write(args, root)
    code = _refs_write(args)
  return code if code != OK else args._refs_exit


def cmd_check(args: argparse.Namespace) -> int:
  state = _state(args)
  report, lines = _check_report(state, show_diff=args.diff)
  if report.ok and not lines:
    lines.append(f"check passed: {len(state.index.items)} items, render and sync current")
  elif report.ok:
    lines.append("check passed with warnings")
  else:
    lines.append("check failed; run `slicer render` and `slicer sync`, then re-run")
  _emit(args, report.to_dict(), "\n".join(lines))
  return OK if report.ok else DRIFT


def cmd_stats(args: argparse.Namespace) -> int:
  state = _state(args)
  payload = _census(state)
  groups = (
    ("status", payload["by_status"]),
    ("size", payload["by_size"]),
    ("tree", payload["by_tree"]),
    ("target", payload["by_release_target"]),
  )
  done = payload["completion"]
  lines = [f"{payload['total']} items · {done['done']} done ({done['percent']}%)"]
  for name, group in groups:
    if group:
      body = _status_tally(state.config, group) if name == "status" else " · ".join(f"{k} {v}" for k, v in group.items())
      lines.append(f"{name:<10} " + body)
  if payload["by_tree_status"]:
    lines.append("progress by tree")
    for tree, cols in payload["by_tree_status"].items():
      lines.append(f"  {tree:<10} " + _status_tally(state.config, cols))
  text = "\n".join(lines)
  _emit(args, payload, text)
  return OK


def snapshot_roots(args: argparse.Namespace) -> list[str | None]:
  """The roots `snapshot` reads, in the order given; `[None]` means the current
  directory. `main` asks too, so the code-mismatch warning sees the same roots."""
  roots = ([args.root] if args.root else []) + (args.snapshot_roots or [])
  return roots or [None]


def cmd_snapshot(args: argparse.Namespace) -> int:
  if not args.json:
    raise StateError("snapshot requires --json", code="usage")
  parts = args.parts.split(",")
  choose = "; choose " + ",".join(snapshot.PARTS)
  if "" in parts:
    print("slicer: usage: empty snapshot part name in --parts " + repr(args.parts) + choose, file=sys.stderr)
    return USAGE
  unknown = [name for name in parts if name not in snapshot.PARTS]
  if unknown:
    print("slicer: usage: unknown snapshot part(s) " + ",".join(unknown) + choose, file=sys.stderr)
    return USAGE
  repeated = [name for index, name in enumerate(parts) if name in parts[:index]]
  if repeated:
    print("slicer: usage: repeated snapshot part(s) " + ",".join(dict.fromkeys(repeated)) + choose,
          file=sys.stderr)
    return USAGE
  projects = []
  exit_code = OK
  for root in snapshot_roots(args):
    start = (Path(root) if root else Path.cwd()).resolve()
    if args.recursive:
      found, unreadable = store.discover_all(start)
      slots = sorted([(path, None) for path in found] + unreadable,
                     key=store.by_relative_parts(start))
      if not slots:
        slots = [(start, StateError(f"no project at or below {start}", code="no_project"))]
    else:
      slots = [(start, None)]
    for path, failure in slots:
      try:
        if failure is not None:
          raise failure
        if not path.is_dir():
          raise StateError(f"no project directory: {path}", code="no_project")
        state = store.load(path)

        def read(command):
          sub = _built_parser().parse_args([command, "--json"])
          sub._state = state
          sub._collect = []
          sub.func(sub)
          return sub._collect[-1]

        projects.append(snapshot.build(state, parts, read))
      except (SlicerError, OSError) as exc:
        code = exc.code if isinstance(exc, SlicerError) else "io"
        exit_code = max(exit_code, _exit_for(code))
        projects.append({"root": str(path), "revision": None, "parts": {},
                         "error": {"code": code, "message": str(exc), "command": "snapshot"}})
  _dump_json(args, {"snapshot": 1, "slicer": __version__, "contract": ai.RELAY_CONTRACT,
                    "projects": projects})
  return exit_code


def cmd_log(args: argparse.Namespace) -> int:
  since = (_log_timestamp(args.since, "--since", code="usage")
           if args.since is not None else None)
  until = (_log_timestamp(args.until, "--until", code="usage")
           if args.until is not None else None)
  if since is not None and until is not None and since > until:
    raise StateError("--since must be earlier than or equal to --until.", code="usage")
  state = _state(args)
  history = state.history()
  if args.item:
    wanted = set(args.item)
    history = [e for e in history if e.item in wanted]
  if args.action:
    actions = set(args.action)
    history = [e for e in history if e.action in actions]
  if args.by:
    actors = set(args.by)
    history = [e for e in history if e.by in actors]
  if since is not None or until is not None:
    window = []
    for entry in history:
      instant = _log_timestamp(
        entry.when, f"{state.dir / store.LOG_NAME}: item {entry.item}", code="corrupt",
      )
      if (since is None or since <= instant) and (until is None or instant < until):
        window.append(entry)
    history = window
  entries = model.newest_first(history)[: args.limit]
  filters = (args.item or []) + (args.action or []) + (args.by or [])
  if args.since is not None:
    filters.append(f"since {args.since}")
  if args.until is not None:
    filters.append(f"until {args.until}")
  empty = ("no history for " + " ".join(filters)) if filters else "no history yet"
  text = "\n".join(e.text() for e in entries) or empty
  _emit(args, [e.to_dict() for e in entries], text)
  return OK


def cmd_tui(args: argparse.Namespace) -> int:
  from slicer import tui

  return tui.run(_state(args))
