"""`slicer changelog`: per-slice entry files, assembled into the changelog at release.

These sit apart from `cli_track` because they change no tracking state: they write files
outside `.slicer/` (`landed-changed/ID.md` and the changelog), so `main` takes the project
lock for them but does not heal the render or refuse during a merge, and they take no
`--render`. Each handler is attached to its subparser in `cli_parser`; the writes are
`ops.changelog_*`.
"""

from __future__ import annotations

import argparse
import sys

from slicer import ops
from slicer.cli_common import OK, _emit, _state
from slicer.errors import StateError


def _text(args: argparse.Namespace) -> str:
  if args.text is not None and args.stdin:
    raise StateError("choose only one of --text and --stdin", code="usage")
  if args.text is not None:
    return args.text
  if args.stdin:
    return sys.stdin.read()
  raise StateError("changelog add needs the entry text: pass --text TEXT or --stdin", code="usage")


def _listed(run: ops.ChangelogRun) -> list[dict[str, str]]:
  return [{"file": file, "category": category} for file, category in run.entries]


def cmd_changelog_add(args: argparse.Namespace) -> int:
  """Write one item's entry file; the text is checked in the layout `check` reads."""
  text = _text(args)
  added = ops.changelog_add(_state(args), args.id, category=args.category, text=text,
                            replace=args.replace)
  _emit(args, {"id": added.id, "path": added.path, "category": added.category,
               "replaced": added.replaced},
        f"{'replaced' if added.replaced else 'wrote'} {added.path}")
  return OK


cmd_changelog_add.mutates = True


def cmd_changelog_assemble(args: argparse.Namespace) -> int:
  """Write every entry into the Unreleased section, delete the files, or print what it would write."""
  run = ops.changelog_assemble(_state(args), dry_run=args.dry_run)
  payload: dict[str, object] = {
    "changelog": run.changelog, "dry_run": run.dry_run, "entries": _listed(run),
  }
  if run.dry_run:
    payload["text"] = run.text
    if args.json:
      _emit(args, payload, "", keep_empty=("entries",))
    else:
      # Not `_emit`: it ends the text with a newline, and the changelog is whole as it is.
      sys.stdout.write(run.text)
    return OK
  count = len(run.entries)
  text = (f"assembled {count} {'entry' if count == 1 else 'entries'} into {run.changelog} "
          "and removed their files" if count else "no entry files; nothing to assemble")
  _emit(args, payload, text, keep_empty=("entries",))
  return OK


cmd_changelog_assemble.mutates = True


def cmd_changelog_import(args: argparse.Namespace) -> int:
  """Split the Unreleased section into one entry file per bullet, once."""
  run = ops.changelog_import(_state(args), dry_run=args.dry_run)
  payload = {"changelog": run.changelog, "dry_run": run.dry_run, "entries": _listed(run)}
  verb = "would write" if run.dry_run else "wrote"
  lines = [f"{verb} {file} ({category})" if category else f"{verb} {file}"
           for file, category in run.entries]
  lines.append(
    f"{'would leave' if run.dry_run else 'left'} the Unreleased section of {run.changelog} empty"
    if run.entries else f"nothing to import in {run.changelog}"
  )
  _emit(args, payload, "\n".join(lines), keep_empty=("entries",))
  return OK


cmd_changelog_import.mutates = True
