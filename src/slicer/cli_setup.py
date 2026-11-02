"""Setup commands: `init`, `setup-git` and the two merge drivers git runs.

These create or repair a project's `.slicer/` directory and its git wiring rather than read
or track slices, so they sit apart from the tracking handlers. Each handler is attached to
its subparser in `cli_parser`; shared helpers come from `cli_common`, and nothing here
imports `cli`.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from slicer import feedback, mergeindex, mergeroles, ops, store, vcs
from slicer.cli_common import DRIFT, OK, _emit, _write_err


def _render_driver_commands() -> list[str]:
  """The per-clone `git config` commands that turn on the merge drivers named in
  `.gitattributes`: the render files (S109), the index counter (S191) and the relay
  roles matrix (s262). slicer's
  git allowlist cannot run them, so `init` and `setup-git` print them for a person
  to run once in each clone."""
  return [
    'git config merge.slicer-generated.name "keep the current branch\'s generated files"',
    "git config merge.slicer-generated.driver true",
    'git config merge.slicer-index.name "keep the larger next_id when merging the index"',
    'git config merge.slicer-index.driver "slicer merge-index %O %A %B"',
    'git config merge.slicer-relay-roles.name "keep both sides\' relay roles rows and rules with fresh ids"',
    'git config merge.slicer-relay-roles.driver "slicer merge-relay-roles %O %A %B"',
  ]


def _post_merge_hook() -> tuple[str, str]:
  """Print an opt-in repair for the merge driver's roadmap-only leftover.

  Match a whole top-level line of check's indented JSON, never a substring of
  a JSON string (which escapes quotes and newlines). Only sh and slicer are needed.
  """
  install = ('slicer setup-git --hook > "$(git rev-parse --git-path hooks/post-merge)"'
             ' && chmod +x "$(git rev-parse --git-path hooks/post-merge)"')
  hook = r'''#!/bin/sh
# Install (replaces an existing hook; integrate it by hand if needed): {install}
# Git does not run post-merge after a conflicted merge; run slicer render by hand.
report=$(slicer check --json)
status=$?
if [ "$status" -eq 0 ]; then
  exit 0
fi
case "$status:$report" in
  1:*'
  "stale_render_kind": "roadmap_only",
'*)
    if slicer render; then
      printf '%s\n' 'slicer: regenerated roadmap render; review and commit the rewritten files.'
    else
      printf '%s\n' 'slicer: render failed; run slicer check and repair it.' >&2
    fi
    ;;
  *)
    printf '%s\n' 'slicer: merge check needs attention; run slicer check.' >&2
    ;;
esac
exit 0
'''.replace("{install}", install)
  return hook, install


def _render_driver_setup() -> str:
  """The `init` hint: why the drivers exist, plus the commands to enable them."""
  indented = "\n".join("  " + command for command in _render_driver_commands())
  return (
    "render/, index.json and relay-roles.json are pointed at merge drivers so "
    "parallel branches don't leave conflict markers in render/, on the next_id "
    "counter, or on relay-roles ids that both branches issued.\n"
    "Enable them once in this clone:\n" + indented
  )


def _init_relay_roles(root: Path) -> tuple[Path, bool]:
  """`init --relay-roles`: the example matrix `relay-roles init --render` writes, under the
  project lock. An existing matrix is kept, never replaced. Returns its path and whether
  this call wrote it."""
  with store.project_lock(root):
    state = store.load(root)
    path = state.dir / store.ROLES_NAME
    if path.is_file():
      return path, False
    ops.render_gated(state, lambda: ops.roles_init(state))
  return path, True


def cmd_init(args: argparse.Namespace) -> int:
  root = Path(args.root or ".").resolve()
  base = ops.init_project(
    root, force=args.force, id_prefix=args.id_prefix,
    id_width=args.id_width, starting_id=args.id,
  )
  text = f"initialised {base}"
  if args.id is not None:
    text += f"\nnext id {args.id}"
  payload: dict[str, object] = {"root": str(root), "dir": str(base)}
  if args.relay_roles:
    matrix, written = _init_relay_roles(root)
    payload["relay_roles"] = str(matrix)
    if written:
      text += f"\nwrote {matrix}"
    else:
      text += (
        f"\nkept {matrix}: it already exists, and init never replaces it; "
        "`slicer relay-roles init --force` replaces it"
      )
  elif not (base / store.ROLES_NAME).exists():
    # Only while no matrix exists: the hint names `relay-roles init`, which refuses otherwise.
    text += "\nrelay roles: no matrix written; run `slicer relay-roles init` to write the example one"
  # Point people at the one-time merge-driver setup where it is git-relevant. The
  # JSON payload stays {root, dir} unless --relay-roles adds relay_roles; the hints
  # are human output only.
  if vcs.is_repo(root):
    text += "\n\n" + _render_driver_setup()
  _emit(args, payload, text)
  return OK


def cmd_merge_index(args: argparse.Namespace) -> int:
  """The `slicer-index` merge driver: Git passes %O %A %B and reads the result
  from the second path. Needs no project. A clean merge prints nothing; exit 1
  leaves conflict markers in that file and names each conflict on stderr, and
  Git reports a normal conflict."""
  conflicts = mergeindex.merge(
    Path.cwd(), Path(args.base), Path(args.ours), Path(args.theirs)
  )
  return DRIFT if conflicts else OK


def cmd_merge_relay_roles(args: argparse.Namespace) -> int:
  """The `slicer-relay-roles` merge driver: Git passes %O %A %B and reads the result
  from the second path. Needs no project. A clean merge prints nothing and rewrites
  that file; a disagreement exits 2 with code `merge_conflict` and leaves the file as
  it was, so Git reports a normal conflict."""
  mergeroles.merge(Path(args.base), Path(args.ours), Path(args.theirs))
  return OK


def cmd_setup_git(args: argparse.Namespace) -> int:
  """Print the per-clone git config that turns on the merge drivers.

  Without `--attributes` it is read-only and project-independent: it needs no
  `.slicer/`, so a fresh clone can run it before anything else, and it never loads
  or locks state. slicer's git allowlist forbids running `git config`, so this only
  prints the commands -- run them, or `slicer setup-git | sh`. `--json` returns them
  as a list.

  `--hook` prints an optional post-merge script instead, with an install command
  in its comment; JSON returns {hook, install}. It writes nothing and needs no project.

  `--attributes` is the one write: it refreshes `.slicer/.gitattributes` from the
  template under the project lock, so an existing project picks up new lines, and adds
  any line `init` writes that `.slicer/.gitignore` lacks (`check` warns about those).
  It needs a project. Stdout stays the same, and the report goes to stderr, or to the
  `attributes` object under `--json`; the lines added to `.slicer/.gitignore` are named
  on stderr only.
  """
  if args.hook:
    hook, install = _post_merge_hook()
    _emit(args, {"hook": hook, "install": install}, hook.rstrip("\n"))
    return OK
  commands = _render_driver_commands()
  if not getattr(args, "attributes", False):
    _emit(args, commands, "\n".join(commands))
    return OK
  root = store.discover(Path(args.root) if args.root else None)
  with store.project_lock(root):
    vcs.require_no_merge(root)
    path, changed = ops.write_gitattributes(root)
    added = feedback.add_gitignore_lines(root, ops.GITIGNORE_LINES)
  if getattr(args, "json", False):
    attributes = {"path": str(path), "changed": changed}
    _emit(args, {"commands": commands, "attributes": attributes}, "")
    return OK
  _emit(args, commands, "\n".join(commands))
  _write_err(args, f"{'wrote' if changed else 'unchanged'} {path}")
  if added:
    _write_err(args, f"added {', '.join(added)} to {feedback.gitignore_path(root)}")
  return OK
