#!/usr/bin/env python3
"""Command line entry point.

Exit codes are stable because scripts depend on them:
  0  fine
  1  drift, or a check failed
  2  usage, validation, or nothing to do
  3  internal or state (corrupt, locked, io, config, schema_too_new)
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from contextlib import nullcontext
from pathlib import Path

from slicer import store, vcs
from slicer import __url__, __version__
from slicer.cli_common import (
  INTERNAL, OK, USAGE, _ArgumentParser, _DESCRIPTION, _ISSUES_URL, _ParserError, _begin_write,
  _dump_json, _emit, _exit_for, _json_flags, _write_out,
)
from slicer.cli_parser import build_parser
from slicer.cli_report import cmd_snapshot
from slicer.config import CONFIG_NAME
from slicer.errors import SlicerError, StateError


def _recursive(args: argparse.Namespace) -> int:
  """Run a read-only report once in every project at or below the start directory.

  The handler runs unchanged with `root` pointed at each project; `_emit` hands
  back what it would have printed, so no handler is copied. A project that fails
  is reported in place and the rest still run. The exit is the worst any project
  returned. Nothing here takes the writer lock, because nothing is written.
  """
  if args.command == "next":
    changes = [
      flag for flag, on in (
        ("--start", args.start), ("--render", args.render),
        ("--owner", args.owner is not None), ("--start-to", args.start_to is not None),
      ) if on
    ]
    if changes:
      raise StateError(
        f"--recursive is read-only and cannot be combined with {', '.join(changes)}; "
        "drop -r to change one project",
        code="usage",
      )
  start = (Path(args.root) if args.root else Path.cwd()).resolve()
  found, unreadable = store.discover_all(start)
  if not found and not unreadable:
    raise StateError(
      f"no {store.DIR_NAME}/ found in {start} or any directory below it; "
      "run `slicer init` in a project root, or drop -r",
      code="no_project",
    )
  # An unreadable directory may hold a project, so it gets an io entry of its own
  # and no handler runs there. Entries keep the sorted order of the projects.
  slots = sorted([(path, None) for path in found] + unreadable, key=store.by_relative_parts(start))
  projects: list[dict[str, object]] = []
  for project, failure in slots:
    entry: dict[str, object] = {"path": project.relative_to(start).as_posix() or "."}
    if failure is not None:
      entry["exit"] = INTERNAL
      entry["error"] = {"code": "io", "message": str(failure)}
      projects.append(entry)
      continue
    sub = copy.copy(args)
    sub.root = str(project)
    sub._collect = []
    try:
      code = int(args.func(sub))
      entry["exit"] = code
      if sub._collect:
        entry["result"] = sub._collect[-1]
    except (SlicerError, OSError) as exc:
      error_code = exc.code if isinstance(exc, SlicerError) else "io"
      entry["exit"] = _exit_for(error_code)
      entry["error"] = {"code": error_code, "message": str(exc)}
    projects.append(entry)
  if getattr(args, "json", False):
    _dump_json(args, {"projects": projects})  # formatted only: each result is already lean
  else:
    blocks = []
    for entry in projects:
      body = entry["result"] if "result" in entry else f"error: {entry['error']['message']}"
      blocks.append(f"== {entry['path']} ==" + (f"\n{body}" if body else ""))
    _write_out(args, "\n\n".join(blocks))
  return max(int(entry["exit"]) for entry in projects)


def _error_envelope(args: argparse.Namespace, exc: SlicerError) -> None:
  """Use the same machine-readable failure shape before and after parsing."""
  if getattr(args, "json", False):
    payload = {
      "error": {
        "code": getattr(exc, "code", "error"),
        "message": str(exc),
        "command": getattr(args, "command", None),
      }
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _fail(args: argparse.Namespace, exc: SlicerError) -> int:
  """Report a deliberate failure: an envelope for agents, prose for people.

  Exit status comes from the error code, in one place: internal/state codes
  are 3, and everything else deliberate (usage, validation) stays 2.
  """
  _error_envelope(args, exc)
  print(f"slicer: {exc}", file=sys.stderr)
  return _exit_for(exc.code)


_parser: argparse.ArgumentParser | None = None


def _cached_parser() -> argparse.ArgumentParser:
  """Build the parser once and reuse it. `parse_args` writes only its namespace,
  never the parser, so one instance serves every `main` call — worth doing
  because building ~28 subparsers (with a gettext lookup per help string) costs
  ~17ms, paid on every in-process call otherwise."""
  global _parser
  if _parser is None:
    _parser = build_parser()
  return _parser


_CODE_WARNING_ENV = "SLICER_NO_CODE_WARNING"


def _warn_code_mismatch(args: argparse.Namespace) -> None:
  """Warn on stderr when slicer's own code and the project it discovers are
  different worktrees of one repo -- an editable install run from a sibling
  checkout, whose own `src/` edits are therefore not what runs. Purely advisory:
  it never raises, never touches stdout or the exit code, and is silenced by
  the SLICER_NO_CODE_WARNING environment variable."""
  if os.environ.get(_CODE_WARNING_ENV):
    return
  try:
    root = store.discover(Path(args.root) if getattr(args, "root", None) else None)
    message = vcs.foreign_worktree(root, Path(__file__).resolve().parent)
  except Exception:
    return  # no project here, or any probe failure -- stay silent
  if message:
    print(f"slicer: {message}", file=sys.stderr)


def _reads_only(args: argparse.Namespace) -> bool:
  """A dry run that is promised no lock: it loads the project and writes nothing."""
  return (
    args.command == "relay-roles" and getattr(args, "relay_roles_command", "") == "apply"
    and args.dry_run
  )


def _changes_state(args: argparse.Namespace) -> bool:
  if args.command == "init":
    return True
  if not getattr(args.func, "mutates", False) or _reads_only(args):
    return False
  if args.command in ("import", "migrate", "remove", "retire") and args.dry_run:
    return False
  if args.command == "id-prefix" and (args.prefix is None or args.dry_run):
    return False
  return not (args.command == "import" and args.skeleton)


def main(argv: list[str] | None = None) -> int:
  parser = _cached_parser()
  argv = list(sys.argv[1:] if argv is None else argv)
  args = argparse.Namespace()
  try:
    options = argv[:argv.index("--")] if "--" in argv else argv
    if "--about" in options:
      about = _ArgumentParser(prog="slicer", description=_DESCRIPTION)
      about.add_argument("--about", action="store_true")
      about.add_argument("--root", default=None)
      _json_flags(about)
      about.parse_args(argv, namespace=args)
      _emit(args, {
        "name": "slicer", "version": __version__, "description": _DESCRIPTION,
        "url": __url__, "issues": _ISSUES_URL,
      }, f"slicer {__version__}\n{_DESCRIPTION}\nSource: {__url__}\nIssues: {_ISSUES_URL}")
      return OK
    parser.parse_args(argv, namespace=args)
    if args.command == "snapshot":
      return cmd_snapshot(args)
    root = Path(args.root) if args.root else Path.cwd()
    if getattr(args, "recursive", False):
      return _recursive(args)
    _warn_code_mismatch(args)
    if _changes_state(args):
      vcs.require_no_merge(root)
    # A mutating command holds an advisory lock for its whole run, so two
    # writers serialise instead of racing (a duplicated id, a half-applied
    # outline). Read-only commands need no lock.
    if getattr(args.func, "mutates", False) and not _reads_only(args):
      # A create-path migrate targets its own new .slicer/, not an ancestor's.
      # There is no target project lock until that directory exists.
      creating_migrate = (
        args.command == "migrate"
        and not (root.resolve() / store.DIR_NAME / CONFIG_NAME).is_file()
      )
      with (nullcontext() if creating_migrate else store.project_lock(root)):
        if _changes_state(args):
          _begin_write(args, root, heal=not creating_migrate)
        return int(args.func(args))
    return int(args.func(args))
  except _ParserError as exc:
    # Inspect only explicit option tokens; data after '--' cannot opt into JSON.
    options = argv[:argv.index("--")] if "--" in argv else argv
    args.json = "--json" in options
    _error_envelope(args, StateError(str(exc), code="usage"))
    exc.parser.print_usage(sys.stderr)
    print(f"{exc.parser.prog}: error: {exc}", file=sys.stderr)
    return USAGE
  except SlicerError as exc:
    return _fail(args, exc)
  except OSError as exc:
    # A missing --file, an unreadable path: someone's mistake, not a bug.
    # Anything else still raises, because a traceback is the right report
    # for a defect in slicer itself.
    return _fail(args, StateError(str(exc), code="io"))


if __name__ == "__main__":
  raise SystemExit(main())
