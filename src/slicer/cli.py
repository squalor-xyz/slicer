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
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from contextlib import nullcontext, redirect_stderr, redirect_stdout
from pathlib import Path

from slicer import check as check_mod
from slicer import (
  ai,
  graph,
  ids,
  jsonio,
  migrator,
  model,
  ops,
  outline,
  prose,
  render,
  store,
  sync,
  templates,
  verify,
  vcs,
  workflow,
)
from slicer import __doc__ as _package_doc, __url__, __version__
from slicer.config import CONFIG_NAME, Config
from slicer.errors import SlicerError, StateError, is_internal

OK, DRIFT, USAGE, INTERNAL = 0, 1, 2, 3
_DESCRIPTION = _package_doc.splitlines()[0].split(" — ", 1)[1].removesuffix(".")
_ISSUES_URL = __url__ + "/issues"


def _json_flags(parser: argparse.ArgumentParser, *, suppress: bool = False) -> None:
  """`--json` and `--lean`. A suppressed default does not overwrite an earlier one.

  A flag typed before the subcommand is parsed by the parent. The copy on the
  subcommand must not reset that value when the flag is absent there.
  """
  default = argparse.SUPPRESS if suppress else False
  parser.add_argument("--json", action="store_true", default=default, help="machine-readable output")
  parser.add_argument(
    "--lean", action="store_true", default=default,
    help="with --json, compact output that omits empty fields, a repeated short title, and an item path",
  )


def _rest_flag(parser: argparse.ArgumentParser, *, suppress: bool = False) -> None:
  """`--rest` on `ai` and `ai instructions`, suppressed on the subcommand like `--json`."""
  parser.add_argument(
    "--rest", action="store_true", default=argparse.SUPPRESS if suppress else False,
    help="print only what the slicer skill does not already carry",
  )


def _emit(args: argparse.Namespace, payload: object, text: str) -> None:
  if getattr(args, "json", False):
    if getattr(args, "lean", False):
      compact = (",", ":")
      _write_out(args, json.dumps(model.lean(payload), ensure_ascii=False, separators=compact))
    else:
      _write_out(args, json.dumps(payload, ensure_ascii=False, indent=2))
  elif text:
    _write_out(args, text)


def _write_out(args: argparse.Namespace, line: str) -> None:
  """Print, or buffer under `--strict` so no success output escapes before the
  render that gates the change has actually succeeded."""
  _defer(args, "_deferred", line, sys.stdout)


def _write_err(args: argparse.Namespace, line: str) -> None:
  """Same gate as `_write_out`, for a warning that must vanish if `--strict` rolls back."""
  _defer(args, "_deferred_err", line, sys.stderr)


def _defer(args: argparse.Namespace, attr: str, line: str, stream: object) -> None:
  if getattr(args, "_defer_output", False):
    buf = getattr(args, attr, None)
    if buf is None:
      buf = []
      setattr(args, attr, buf)
    buf.append(line)
  else:
    print(line, file=stream)


def _render_after_mutation(args: argparse.Namespace) -> int:
  """Re-read and render the just-saved state; return how many files changed."""
  state = _state(args)
  expected = render.plan(state)
  return len(render.write(expected, state.render_dir, render.compare(expected, state.render_dir)))


def _mutating(fn):
  """Wrap a mutating handler so `--render` renders it, from one place.

  Default `--render` is render-*after*: the handler has already saved to disk and
  printed its own result (an object, or a JSON array for a batch of ids). If the
  follow-up render fails, the change is still committed, so we must not let the
  failure reach `main` — that would print a *second* document after the handler's
  result. Instead stdout stays the handler's single result unchanged, the render
  failure goes to stderr, and the exit is DRIFT (1): state and `render/` are now
  out of step, which is what `slicer render` resolves. The exit is DRIFT whatever
  the cause, because the mutation the user asked for did succeed; only the
  projection is stale.

  `--strict` (with `--render`) flips this to render-*first*: `_mutate_strict`
  buffers the mutation and its output, renders the proposed state, and only then
  lets the change land — the render-first policy `done` already follows, offered
  to any mutation. A render failure there rolls the whole change back and prints
  nothing. Migration uses `_migrate_strict` because it also replaces config and
  templates and may create the tracking directory.
  """
  def wrapped(args: argparse.Namespace) -> int:
    render_on = getattr(args, "render", False) and not getattr(args, "dry_run", False)
    if getattr(args, "strict", False) and not render_on:
      raise StateError("--strict has no effect without --render", code="usage")
    if getattr(args, "check", False) and not render_on:
      raise StateError("--check has no effect without --render", code="usage")
    if not render_on:
      return fn(args)
    if getattr(args, "strict", False):
      if args.command == "migrate":
        return _migrate_strict(fn, args)
      code = _mutate_strict(fn, args)
    else:
      code = fn(args)
      if code != OK:
        return code
      try:
        written = _render_after_mutation(args)
      except (SlicerError, OSError) as exc:
        print(f"slicer: saved, but render failed: {exc}; run `slicer render`", file=sys.stderr)
        return DRIFT
      if not getattr(args, "json", False):
        print(f"rendered {written} file(s)")
    if code == OK and getattr(args, "check", False):
      return _post_check(args, args.command)
    return code

  # main() locks the project around a mutating command; the flag says which
  # handlers those are, so the render re-read above is inside the same lock.
  wrapped.mutates = True
  return wrapped


def _mutate_strict(fn, args: argparse.Namespace) -> int:
  """Render the proposed change before letting it land (the `--strict` policy).

  The handler runs against a staged state (begun in `_state`), so its saves and
  slice moves buffer instead of hitting disk, and its success output buffers too.
  Then the proposed state is rendered: on success the buffered writes and output
  are flushed; on failure the batch is discarded and the error propagates, so a
  change that cannot be rendered never lands and no success document is printed.
  """
  args._defer_output = True
  state = _state(args)  # loads, caches on args, and begins the staging transaction
  try:
    code = fn(args)
    if code != OK:
      state.discard_stage()
      # The handler already explained the refusal. Discarding the stage must
      # not also discard that report.
      _flush_deferred(args)
      return code
    expected = render.plan(state)
    # write_atomic, so a render write that fails partway restores render/ and the
    # staged state rolls back with it -- --strict lands whole or not at all.
    written = len(render.write_atomic(expected, state.render_dir, render.compare(expected, state.render_dir)))
    state.commit_stage()
  except BaseException:
    state.discard_stage()
    raise
  _flush_deferred(args)
  if not getattr(args, "json", False):
    print(f"rendered {written} file(s)")
  return code


def _flush_deferred(args: argparse.Namespace) -> None:
  """Print the output a strict handler held back until its fate was known."""
  for line in getattr(args, "_deferred", []):
    print(line)
  for line in getattr(args, "_deferred_err", []):
    print(line, file=sys.stderr)


def _migrate_strict(fn, args: argparse.Namespace) -> int:
  """Roll back every file migrate can replace if its required render fails.

  Migrate creates config and templates as well as state, so it cannot use the
  staged State transaction used by ordinary mutations. Snapshot its exact
  target directory, never an ancestor found by project discovery.
  """
  base = Path(args.root or ".").resolve() / store.DIR_NAME
  existed = base.exists()
  directories = {
    path.relative_to(base) for path in base.rglob("*") if path.is_dir()
  } if existed else set()
  saved = {
    path.relative_to(base): path.read_bytes()
    for path in base.rglob("*") if path.is_file() and path.name != store.LOCK_NAME
  } if existed else {}
  args._defer_output = True
  try:
    code = fn(args)
    if code != OK:
      return code
    state = store.load(base.parent)
    expected = render.plan(state)
    written = len(render.write_atomic(
      expected, state.render_dir, render.compare(expected, state.render_dir)
    ))
  except BaseException:
    if not existed:
      if base.exists():
        shutil.rmtree(base)
    else:
      for path in sorted(base.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_file() and path.name != store.LOCK_NAME and path.relative_to(base) not in saved:
          path.unlink()
        elif path.is_dir() and path.relative_to(base) not in directories:
          try:
            path.rmdir()
          except OSError:
            pass
      for rel, data in saved.items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    raise
  for line in getattr(args, "_deferred", []):
    print(line)
  if not getattr(args, "json", False):
    print(f"rendered {written} file(s)")
  return code


def _render_flag(sp: argparse.ArgumentParser) -> argparse.ArgumentParser:
  sp.add_argument("--render", action="store_true", help="render .slicer/render/ after the change")
  return sp


def _check_flag(sp: argparse.ArgumentParser) -> argparse.ArgumentParser:
  """`--check` runs `slicer check` after a rendered change has landed."""
  sp.add_argument(
    "--check", action="store_true",
    help="with --render, run slicer check after the change lands; exit 1 if it fails",
  )
  return sp


def _strict_flag(sp: argparse.ArgumentParser) -> argparse.ArgumentParser:
  """`--strict` gates a mutation on its render (with `--render`): the change is
  rolled back, and nothing printed, if the render fails. Offered on the
  mutating commands, including import, migrate, and remove. `done --render`
  already behaves this way and does not take the flag."""
  sp.add_argument(
    "--strict", action="store_true",
    help="with --render, require the render to succeed before the change lands",
  )
  return sp


def _render_hint(args: argparse.Namespace) -> str:
  """The `run slicer render` reminder — empty when --render already rendered."""
  return "" if getattr(args, "render", False) else "run `slicer render`"


def _state(args: argparse.Namespace) -> store.State:
  # Under strict --render the whole mutation runs against one staged state that
  # `_mutate_strict` renders and commits, so cache and stage it here and hand the
  # same instance back to the handler that mutates it. Every other path loads
  # fresh, unchanged.
  cached = getattr(args, "_state", None)
  if cached is not None:
    return cached
  state = store.load(Path(args.root) if args.root else None)
  if getattr(args, "strict", False) and getattr(args, "render", False):
    args._state = state
    state.begin_stage()
  return state


def _ai_finish(args: argparse.Namespace) -> str:
  """implement_finish when config and index both load. Otherwise done.

  Read-only: no lock and no write. A miss warns and keeps the generic text.
  """
  root = Path(args.root).resolve() if getattr(args, "root", None) else None
  try:
    project = store.discover(root)
    cfg = Config.load(project / store.DIR_NAME / CONFIG_NAME)
    store.read_index(project)
  except (OSError, SlicerError) as exc:
    print(f"{exc}; printing the generic guide", file=sys.stderr)
    return "done"
  return cfg.implement_finish


def cmd_ai_instructions(args: argparse.Namespace) -> int:
  # `--rest` is the guide minus the skill, which never reads the project.
  text = ai.rest_text() if args.rest else ai.instructions_text(_ai_finish(args))
  _emit(args, {"instructions": text}, text.rstrip("\n"))
  return OK


def cmd_recommended_workflow(args: argparse.Namespace) -> int:
  """The generic workflow document. Reads no project, takes no lock, writes nothing."""
  doc = workflow.text()
  _emit(args, {"workflow": doc}, doc.rstrip("\n"))
  return OK


def cmd_ai_skill(args: argparse.Namespace) -> int:
  text = ai.skill_text(_ai_finish(args))
  _emit(args, {"skill": text}, text.rstrip("\n"))
  return OK


GITATTRIBUTES = """\
# slicer manages this file. History is append-only, so union-merge combines the
# lines both sides added instead of conflicting when branches land in parallel.
log.jsonl merge=union

# render/ is a generated projection of index.json, never merged by hand. The
# slicer-generated driver keeps the current branch's copy on merge instead of
# writing conflict markers; re-run `slicer render` after resolving index.json so
# the kept files match it. The name resolves only once a clone defines the driver
# (one-time, per clone -- slicer's git allowlist cannot run `git config` for you):
#   git config merge.slicer-generated.name "keep the current branch's generated files"
#   git config merge.slicer-generated.driver true
render/ROADMAP.md merge=slicer-generated
render/ROADMAP.html merge=slicer-generated
render/slices/*.md merge=slicer-generated
"""


def _render_driver_commands() -> list[str]:
  """The two per-clone `git config` commands that turn on the render merge driver
  named in `.gitattributes` (S109). slicer's git allowlist cannot run them, so
  `init` and `setup-git` print them for a person to run once in each clone."""
  return [
    'git config merge.slicer-generated.name "keep the current branch\'s generated files"',
    "git config merge.slicer-generated.driver true",
  ]


def _render_driver_setup() -> str:
  """The `init` hint: why the driver exists, plus the two commands to enable it."""
  indented = "\n".join("  " + command for command in _render_driver_commands())
  return (
    "render/ is pointed at a merge driver so parallel branches don't leave "
    "conflict markers in it.\nEnable it once in this clone:\n" + indented
  )


def cmd_init(args: argparse.Namespace) -> int:
  root = Path(args.root or ".").resolve()
  base = root / store.DIR_NAME
  if (base / CONFIG_NAME).exists() and not args.force:
    raise StateError(
      f"{base} already exists; pass --force to overwrite its config and templates",
      code="already_exists",
    )
  index_path = base / store.INDEX_NAME
  existing = model.Index.from_dict(jsonio.read(index_path)) if index_path.is_file() else None
  if existing is None:
    scheme = Config()
    prefix, width = scheme.id_prefix, scheme.id_width
  else:
    prefix, width = existing.id_prefix, existing.id_width
  # Resolve --id before any write. A bad id, or a counter move over live items,
  # leaves config, templates, and the index untouched.
  next_id = None
  if args.id is not None:
    next_id = ids.starting_number(args.id, prefix, width)
    if existing is not None and existing.items:
      raise StateError(
        f"{base} already has items; a starting id would move the counter over live ids. "
        "Use `add --id` instead",
        code="state",
      )
  cfg = Config()
  jsonio.write(base / CONFIG_NAME, cfg.to_dict())
  if existing is None:
    index = model.Index(id_prefix=cfg.id_prefix, id_width=cfg.id_width)
    if next_id is not None:
      index.next_id = next_id
    jsonio.write(index_path, index.to_dict())
  elif next_id is not None:
    existing.next_id = next_id
    jsonio.write(index_path, existing.to_dict())
  for name, text in templates.defaults().items():
    jsonio.write_text(base / store.TEMPLATES_DIR / name, text)
  jsonio.write_text(base / store.GITATTRIBUTES_NAME, GITATTRIBUTES)
  (base / store.SLICES_DIR / cfg.done_dir).mkdir(parents=True, exist_ok=True)
  text = f"initialised {base}"
  if args.id is not None:
    text += f"\nnext id {args.id}"
  # Point people at the one-time merge-driver setup where it is git-relevant. The
  # JSON payload stays {root, dir}; the hint is human output only.
  if vcs.is_repo(root):
    text += "\n\n" + _render_driver_setup()
  _emit(args, {"root": str(root), "dir": str(base)}, text)
  return OK


def cmd_setup_git(args: argparse.Namespace) -> int:
  """Print the per-clone git config that turns on the render merge driver.

  Read-only and project-independent: it needs no `.slicer/`, so a fresh clone can
  run it before anything else, and it never loads or locks state. slicer's git
  allowlist forbids running `git config`, so this only prints the commands -- run
  them, or `slicer setup-git | sh`. `--json` returns them as a list.
  """
  commands = _render_driver_commands()
  _emit(args, commands, "\n".join(commands))
  return OK


SKELETON_HEAD = """\
<!--
  A slicer outline. Each `## ` heading is one roadmap item.

  Optional key lines go directly under the heading:
    size:     {sizes}
    tree:     which part of the codebase; comma-separated for several
    findings: a reference back to whatever raised this
    status:   {statuses}
    pass:     a group key, if the project uses passes
    group:    a phase label rendered above the item
    depends:  the title of another item, here or already in the roadmap
    importance: 1, 2 or 3; how important (default 2)
    urgency:    1, 2 or 3; how urgent (default 2)
    effort:     1, 2 or 3; optional, omit to leave unset

  A paragraph after the keys and before the first `###` becomes the slice's
  lead. A lead paragraph that starts with the project's boundary marker is
  the scope boundary, not lead prose; put that paragraph in the lead or in
  one section, not both. Each `### ` heading is a section of the slice; an
  item with no sections is a roadmap row only.

  This project's configured sections are:
    {sections}

  Import with:  slicer import THIS-FILE.md --dry-run
-->

# Roadmap
"""

SKELETON_EXAMPLE = """
## Parse the config file
size: {size}
tree: core
findings: G1
importance: 3
urgency: 1
effort: 1

The loader accepts a missing key and carries on with a zero, so a typo in
the config reads as a deliberate setting.

{sections}
"""


def _read_user_file(path: Path | str) -> str:
  """Read a file the user pointed us at, reporting bad bytes as a clean io error."""
  try:
    return Path(path).read_text(encoding="utf-8")
  except UnicodeDecodeError as e:
    raise StateError(f"{path}: not valid UTF-8: {e}", code="io") from None


def cmd_import(args: argparse.Namespace) -> int:
  if getattr(args, "legacy_from", None) is not None:
    raise StateError(
      "--from belongs to `slicer migrate`, which converts an existing markdown "
      "slice tree; `slicer import` takes an outline file",
      code="wrong_command",
    )
  state = _state(args)
  cfg = state.config

  if args.skeleton:
    sizes = "S, M or L by convention; anything you like"
    statuses = ", ".join(sorted(cfg.statuses))
    head = SKELETON_HEAD.format(
      sizes=sizes, statuses=statuses, sections=", ".join(cfg.sections) or "(none)"
    )
    body = SKELETON_EXAMPLE.format(
      size=(cfg.sections and "M") or "M",
      sections="\n\n".join(f"### {h}" for h in cfg.sections),
    )
    _emit(args, {"skeleton": head + body}, head + body)
    return OK

  if not args.file:
    raise StateError("give an outline file, or --skeleton to print a template", code="usage")

  path = Path(args.file)
  if not path.is_absolute():
    path = Path(args.root or ".").resolve() / path
  parsed = outline.parse(_read_user_file(path), path=str(path))
  specs = parsed.items
  preamble = parsed.preamble or None

  if args.dry_run:
    report = ops.outline_report(
      state, specs, force=args.force, preamble=preamble, id_floor=_id_floor(state),
    )
  else:
    report = ops.apply_outline(
      state, specs, force=args.force, preamble=preamble, id_floor=_id_floor(state),
    )

  lines = [
    f"source     {path}",
    f"outline    {report.items} items, {report.promoted} with slices",
    "status     " + _status_tally(state.config, report.by_status),
    f"depends    {report.depends_edges} edges",
  ]
  if report.preamble is not None:
    lines.append("preamble   " + report.preamble.replace("\n", " "))
  if report.off_schema_sections:
    lines.append(
      f"sections   {len(report.off_schema_sections)} off-schema: "
      + ", ".join(f"{k} {v}" for k, v in report.off_schema_sections.items())
    )
  for w in report.warnings:
    lines.append(f"warn       {w}")
  for pr in report.problems:
    lines.append(f"PROBLEM    {pr}")

  if report.problems:
    lines.append("refusing to write: fix the problems above, or re-run with --dry-run to inspect")
    _emit(args, report.to_dict(), "\n".join(lines))
    return DRIFT
  lines.append(f"added      {', '.join(report.ids)}")
  if args.dry_run:
    lines.append("nothing written; drop --dry-run to apply")
    _emit(args, report.to_dict(), "\n".join(lines))
    return OK

  if _render_hint(args):
    lines.append(f"now {_render_hint(args)}")
  _emit(args, report.to_dict(), "\n".join(lines))
  return OK


def cmd_migrate(args: argparse.Namespace) -> int:
  root = Path(args.root or ".").resolve()
  source = Path(args.source)
  if not source.is_absolute():
    source = root / source
  base = root / store.DIR_NAME
  cfg = Config.load(base / CONFIG_NAME) if (base / CONFIG_NAME).is_file() else Config()

  # migrate replaces config.json and index.json wholesale, so refuse to run it
  # over a roadmap that already has items -- one mistaken invocation would
  # destroy it. An empty init'd project has no items and no slices, which is
  # the normal "init then migrate" path, so that is allowed. --dry-run writes
  # nothing, so it stays a safe preview even on a non-empty project.
  index_path = base / store.INDEX_NAME
  existing = len(model.Index.from_dict(jsonio.read(index_path)).items) if index_path.is_file() else 0
  if existing and not args.force and not args.dry_run:
    raise StateError(
      f"{base} already has a roadmap ({existing} items); migrate would replace it "
      "-- pass --force to overwrite, or --dry-run to preview",
      code="already_exists",
    )

  render_root = base / store.RENDER_DIR
  index, slices, report = migrator.build(
    source,
    cfg,
    render_dir=render_root / render.SLICES_SUBDIR,
    index_render_dir=render_root,
  )
  lines = [
    f"source     {source}",
    f"index      {report.passes} passes, {report.groups} group rows, {report.items} items, next id {report.next_id}",
    f"status     " + _status_tally(cfg, report.by_status),
    f"sizes      " + " · ".join(f"{k} {v}" for k, v in report.by_size.items()),
    f"slices     {report.slices} parsed, {report.roundtrip_ok} round-trip byte-identical",
    f"sections   {len(report.off_schema_sections)} off-schema: "
    + ", ".join(f"{k} {v}" for k, v in list(report.off_schema_sections.items())[:6]),
    f"depends    {report.depends_edges} edges",
    f"reconcile  {report.title_differences} title, {report.findings_differences} findings, "
    f"{report.trees_differences} trees differences - both sides kept",
  ]
  for w in report.warnings:
    lines.append(f"warn       {w}")
  for p in report.problems:
    lines.append(f"PROBLEM    {p}")

  if report.problems:
    lines.append("refusing to write: fix the problems above, or re-run with --dry-run to inspect")
    _emit(args, report.to_dict(), "\n".join(lines))
    return DRIFT
  if args.dry_run:
    lines.append(f"would write {store.DIR_NAME}/ under {root} (nothing written)")
    _emit(args, report.to_dict(), "\n".join(lines))
    return OK

  written = migrator.write(root, cfg, index, slices)
  lines.append(f"wrote      {len(written)} files under {base}")
  _emit(args, report.to_dict(), "\n".join(lines))
  return OK


def _nonnegative_int(value: str) -> int:
  try:
    number = int(value)
  except ValueError:
    raise argparse.ArgumentTypeError("must be a nonnegative integer") from None
  if number < 0:
    raise argparse.ArgumentTypeError("must be a nonnegative integer")
  return number


def _positive_int(value: str) -> int:
  try:
    number = int(value)
  except ValueError:
    raise argparse.ArgumentTypeError("must be an integer >= 1") from None
  if number < 1:
    raise argparse.ArgumentTypeError("must be an integer >= 1")
  return number


def _id_floor(state: store.State) -> int:
  """The highest id counter any sibling worktree has reached, so a new id clears it."""
  siblings = [index for _, index in store.sibling_ids(state.root)]
  return ids.floor_from(siblings, state.index.id_prefix)


def cmd_next_id(args: argparse.Namespace) -> int:
  state = _state(args)
  item_id = ids.format_next(state.index, floor=_id_floor(state))
  _emit(args, {"id": item_id}, item_id)
  return OK


def cmd_id_prefix(args: argparse.Namespace) -> int:
  state = _state(args)
  if args.prefix is None:
    _emit(args, {"prefix": state.index.id_prefix}, state.index.id_prefix)
    return OK
  if args.dry_run:
    ids.check_prefix_change(state.index, args.prefix)
    old = state.index.id_prefix
    changed = (old, state.config.id_prefix) != (args.prefix, args.prefix)
  else:
    old, changed = ops.set_id_prefix(state, args.prefix)
  next_id = ids.format_id(args.prefix, state.index.next_id, state.index.id_width)
  payload = {"from": old, "to": args.prefix, "changed": changed, "next_id": next_id}
  if args.dry_run:
    payload["dry_run"] = True
  if not changed:
    text = f"id prefix is already {args.prefix}; next id {next_id}"
  else:
    verb = "would change" if args.dry_run else "changed"
    text = f"id prefix {verb} {old} -> {args.prefix}; next id {next_id}"
  _emit(args, payload, text)
  return OK


def _unspecified_payload(result: ops.NextResult | ops.BatchResult) -> list[dict[str, object]]:
  return [{"id": item_id, "missing": missing} for item_id, missing in result.unspecified]


def _unspecified_lines(result: ops.NextResult | ops.BatchResult) -> list[str]:
  lines: list[str] = []
  for item_id, missing in result.unspecified:
    names = " and ".join(missing)
    edits = " and ".join(
      f"`slicer edit {item_id} --section {shlex.quote(name)}`" for name in missing
    )
    verb = "is" if len(missing) == 1 else "are"
    lines.append(f"skipped {item_id}: {names} {verb} empty. Fill them with {edits}.")
  return lines


def _elsewhere_payload(result: ops.NextResult | ops.BatchResult) -> list[dict[str, str]]:
  """Skipped items, one row per sibling, in list's `{worktree, owner, status}` shape plus the id."""
  return [{"id": item_id} | entry for item_id, entries in result.elsewhere for entry in entries]


def _elsewhere_lines(result: ops.NextResult | ops.BatchResult) -> list[str]:
  return [
    f"skipped {item_id} (in work in {', '.join('wt:' + e['worktree'] for e in entries)})"
    for item_id, entries in result.elsewhere
  ]


def _sibling_only_lines(rows: list[dict[str, str]]) -> list[str]:
  """The `Only in a sibling` block, or nothing when no sibling has unseen open work."""
  if not rows:
    return []
  id_w = max(len(r["id"]) for r in rows)
  tree_w = max(len(r["worktree"]) for r in rows)
  return ["Only in a sibling", *(
    f"  {r['id']:<{id_w}}  {r['worktree']:<{tree_w}}  {r['title']}" for r in rows
  )]


def _note_skips(payload: dict, lines: list[str], result: ops.NextResult | ops.BatchResult) -> None:
  """Add the unspecified and in-work-elsewhere skips, each only when non-empty."""
  unspecified = _unspecified_payload(result)
  if unspecified:
    payload["unspecified"] = unspecified
    lines.extend(_unspecified_lines(result))
  elsewhere = _elsewhere_payload(result)
  if elsewhere:
    payload["in_work_elsewhere"] = elsewhere
    lines.extend(_elsewhere_lines(result))


def _sections_named(
  sl: model.Slice, names: list[str], *, every_match: bool = False,
) -> list[model.Section]:
  """Sections for these headings, in slice order.

  A slice may repeat a heading. One requested name returns the first match;
  `show --context` and several names keep every repeat.
  """
  if len(names) == 1 and not every_match:
    found = sl.section(names[0])
    selected = [found] if found is not None else []
  else:
    wanted = set(names)
    selected = [s for s in sl.sections if s.heading in wanted]
  missing = [name for name in dict.fromkeys(names)
             if not any(s.heading == name for s in sl.sections)]
  if missing:
    raise StateError(f"{sl.id} has no section {missing[0]!r}", code="no_such_section")
  return selected


def _blocked_payload(pairs: list[tuple[str, list[str]]]) -> list[dict[str, object]]:
  return [{"id": item_id, "waiting_on": waiting} for item_id, waiting in pairs]


def _append_blocked(lines: list[str], blocked: list[dict[str, object]]) -> None:
  if blocked:
    lines.append("Blocked")
    lines.extend(
      f"  {entry['id']} waits on {', '.join(entry['waiting_on'])}" for entry in blocked
    )
  else:
    lines.append("Blocked   none")


def _append_slice_hint(
  lines: list[str], item: model.Item, path: object, show_path: bool,
) -> None:
  """Point text at `show`. The path is opt-in so the line is not a file to open."""
  if not path:
    return
  lines.append(f"     (run `slicer show {item.id}`)")
  if show_path:
    lines.append(f"     {path}")


def _next_fields(
  state: store.State, item: model.Item, *, show_path: bool = False,
) -> tuple[dict[str, object], list[str]]:
  """The plain `next` object (item record, path, effective score) and its lines."""
  eff = graph.effective_scores(state.index)[item.id]
  path = state.find_slice_file(item.id)
  inherited = "^" if eff > item.score else ""
  payload = item.to_dict() | {"path": str(path) if path else None, "effective_score": eff}
  lines = [
    f"{item.id}  {item.display_title()}",
    f"     score {eff}{inherited} · {state.config.status_label(item.status)}",
  ]
  _append_slice_hint(lines, item, path, show_path)
  return payload, lines


def _ready_entry(
  args: argparse.Namespace, state: store.State, item: model.Item,
) -> tuple[dict[str, object], list[str]]:
  """One `{item, slice?}` pickup. `blocked` stays with the caller."""
  eff = graph.effective_scores(state.index)[item.id]
  path = state.find_slice_file(item.id)
  sl = state.slices.get(item.id)
  payload: dict[str, object] = {
    "item": {
      "id": item.id,
      "title": item.title,
      "status": item.status,
      "depends_on": list(item.depends_on),
      "attempts": item.attempts,
      "effective_score": eff,
      "path": str(path) if path else None,
    },
  }
  lines = [
    f"{item.id}  {item.display_title()}",
    f"     score {eff}{'^' if eff > item.score else ''} · {state.config.status_label(item.status)}",
  ]
  _append_slice_hint(lines, item, path, bool(getattr(args, "path", False)))
  if sl is None:
    lines.append(f"     (no slice yet; run `slicer promote {item.id}`)")
  else:
    if args.section:
      payload["slice"] = {
        "boundary": sl.boundary,
        "sections": [s.to_dict() for s in _sections_named(sl, args.section)],
      }
    else:
      payload["slice"] = sl.to_dict()
    boundary = " ".join(sl.boundary.split()) or "(none)"
    headings = ", ".join(section.heading for section in sl.sections) or "(none)"
    lines.append(f"     boundary  {boundary}")
    lines.append(f"     sections  {headings}")
  return payload, lines


def _emit_ready(
  args: argparse.Namespace, state: store.State, item: model.Item, result: ops.NextResult,
) -> None:
  """One bounded pickup: who is next, the slice to implement, and who is blocked.

  The item is the identity an agent needs to start work. The full item record,
  the status census, and the goals prose stay on their own commands.
  """
  payload, lines = _ready_entry(args, state, item)
  blocked = _blocked_payload(result.blocked)
  payload["blocked"] = blocked
  _append_blocked(lines, blocked)
  _note_skips(payload, lines, result)
  _emit(args, payload, "\n".join(lines))


def _reject_unknown_sections(args: argparse.Namespace, state: store.State, items: list[model.Item]) -> None:
  """A typo in `--section` must fail before `--start` claims anything."""
  if not (args.ready and args.section):
    return
  for item in items:
    sl = state.slices.get(item.id)
    if sl is not None:
      _sections_named(sl, args.section)


def cmd_next(args: argparse.Namespace) -> int:
  if args.ready and args.show:
    raise StateError(
      "--ready and --show are separate output profiles; pass only one",
      code="usage",
    )
  if args.section and not args.ready:
    raise StateError("--section on next requires --ready", code="usage")
  if args.owner is not None and not args.start:
    raise StateError("--owner on next requires --start", code="usage")
  if args.batch is not None and (args.show or args.n is not None):
    raise StateError("--batch cannot be combined with --show or -n", code="usage")
  if args.batch is not None and args.status is not None:
    raise StateError("--batch cannot be combined with --status", code="usage")
  state = _state(args)
  review = args.status is not None
  if review and (not state.config.review_status or args.status != state.config.review_status):
    supported = (f"only {state.config.review_status!r}, the review status, is supported"
                 if state.config.review_status else "this project declares no review status")
    raise StateError(f"next --status {args.status!r}: {supported}", code="usage")
  elsewhere = store.in_work_elsewhere(state.root, state.index)
  if args.batch is not None:
    return _cmd_next_batch(args, state, elsewhere)
  offset = 0 if args.n is None else args.n
  result = ops.next_item(
    state, offset, elsewhere, review=review, tree=args.tree, size=args.size,
  )
  if result.item is None:
    payload = {"item": None, "blocked": _blocked_payload(result.blocked)}
    parts = [f"blocked {i} waits on {', '.join(b)}" for i, b in result.blocked]
    _note_skips(payload, parts, result)
    text = "\n".join(parts) if parts else ("nothing in review" if review else "nothing unmarked")
    if offset:
      text = f"no eligible item at offset {offset}" + (f"\n{text}" if parts else "")
    _emit(args, payload, text)
    return USAGE
  item = result.item
  _reject_unknown_sections(args, state, [item])
  if args.start:
    with store.project_lock(state.root):
      item = ops.start(state, item.id, owner=args.owner)
  if args.ready:
    _emit_ready(args, state, item, result)
    return OK
  payload, lines = _next_fields(state, item, show_path=bool(args.path))
  _note_skips(payload, lines, result)
  if args.show:
    # Fold the follow-up `show ID` into this one call: an agent picking up work
    # reads the slice in the same turn it learns the id, saving a round trip.
    sl = state.slices.get(item.id)
    if sl is None:
      lines.append(f"     (no slice yet; run `slicer promote {item.id}`)")
    else:
      payload = payload | {"slice": sl.to_dict()}
      # The banner names the source file, which is the path this text keeps out.
      lines.append(render.slice_body(
        sl, state.config, state.template("slice.md"), item.notes))
  _emit(args, payload, "\n".join(lines))
  return OK


def _cmd_next_batch(
  args: argparse.Namespace, state: store.State,
  elsewhere: dict[str, list[dict[str, str]]],
) -> int:
  """Several items from one tree and size. One lock covers the whole start."""
  result = ops.next_batch(state, args.batch, elsewhere, tree=args.tree, size=args.size)
  blocked = _blocked_payload(result.blocked)
  if not result.items:
    payload: dict[str, object] = {"items": [], "blocked": blocked}
    parts = [f"blocked {i} waits on {', '.join(b)}" for i, b in result.blocked]
    _note_skips(payload, parts, result)
    text = "\n".join(parts) if parts else "nothing unmarked"
    _emit(args, payload, text)
    return USAGE
  _reject_unknown_sections(args, state, result.items)
  items = result.items
  if args.start:
    # Empty batches never reach this. One lock and one staged start_many, so a
    # failure keeps every id unclaimed: no status, move, or log line.
    with store.project_lock(state.root):
      with state.staged():
        started = ops.start_many(state, [item.id for item in items], owner=args.owner)
    by_id = {item.id: item for item in started}
    items = [by_id[item.id] for item in items]
  lines: list[str] = []
  if args.ready:
    entries = []
    for item in items:
      entry, item_lines = _ready_entry(args, state, item)
      entries.append(entry)
      lines.extend(item_lines)
    payload = {"items": entries, "blocked": blocked}
  else:
    entries = []
    for item in items:
      entry, item_lines = _next_fields(state, item, show_path=bool(args.path))
      entries.append(entry)
      lines.extend(item_lines)
    payload = {"items": entries, "blocked": blocked}
  _append_blocked(lines, blocked)
  _note_skips(payload, lines, result)
  _emit(args, payload, "\n".join(lines))
  return OK


def _claim_cell(
  cfg: Config, item: model.Item, elsewhere: list[dict[str, str]] | None = None,
) -> str:
  """Owner when claimed, `*` when in progress without one, otherwise `-`.

  A finished item's local claim is hidden, but sibling work remains visible.
  """
  if item.status != cfg.done_status and item.claim_owner:
    return item.claim_owner
  if item.status in cfg.in_work():
    return "*"
  if elsewhere:
    extra = f"+{len(elsewhere) - 1}" if len(elsewhere) > 1 else ""
    return f"wt:{elsewhere[0]['worktree']}{extra}"
  return "-"


def _claim_width(
  cfg: Config, items: list[model.Item],
  elsewhere: dict[str, list[dict[str, str]]] | None = None,
) -> int:
  width = len("CLAIM")
  for item in items:
    width = max(width, len(_claim_cell(cfg, item, (elsewhere or {}).get(item.id))))
  return width


def _status_width(cfg: Config, items: list[model.Item]) -> int:
  """At least the historical 7, wider when a label needs it (`reviewing`, or a
  project's own status), so a long label never pushes the rest of its row."""
  return max([7, *(len(cfg.status_label(item.status)) for item in items)])


def _pass_width(index: model.Index, items: list[model.Item]) -> int | None:
  """The PASS column's width, or None when the roadmap uses no passes at all
  (none declared and none named by an item), so such a project keeps the
  table without the column."""
  if not any(index.pass_keys()):
    return None
  return max([len("PASS"), *(len(i.pass_key or "-") for i in items)])


def _item_rows(
  state: store.State, items: list[model.Item], *,
  claim_w: int | None = None,
  pass_w: int | None = None,
  status_w: int | None = None,
  elsewhere: dict[str, list[dict[str, str]]] | None = None,
) -> list[str]:
  """The shared queue-listing row format used by `list`, `find` and `deps`.

  `^` marks an effective score lifted above the item's own by a dependent, so a
  blocker of a critical item reads at that item's priority. The claim column
  names the owner, or `*` when the item is in progress and unclaimed. The pass
  column appears only when the roadmap uses passes.
  """
  cfg = state.config
  eff = graph.effective_scores(state.index)
  elsewhere = elsewhere or {}
  if claim_w is None:
    claim_w = _claim_width(cfg, items, elsewhere)
  if pass_w is None:
    pass_w = _pass_width(state.index, items)
  if status_w is None:
    status_w = _status_width(cfg, items)
  rows = []
  for n, item in enumerate(items, 1):
    score = str(eff[item.id]) + ("^" if eff[item.id] > item.score else "")
    effort = "-" if item.effort is None else str(item.effort)
    pass_cell = "" if pass_w is None else f"{item.pass_key or '-':<{pass_w}} "
    rows.append(
      f"{n:>3}  {item.id:<5} {cfg.status_label(item.status):<{status_w}} "
      f"{_claim_cell(cfg, item, elsewhere.get(item.id)):<{claim_w}} {pass_cell}{item.size:<4} "
      f"{effort:<6} {score:<5} {item.quadrant:<9} {item.display_title()}"
    )
  return rows


def _list_in_next_order(state: store.State, items: list[model.Item]) -> list[model.Item]:
  """The sequence `next` walks, then every other visible row by effective score."""
  return graph.ranked_order(state.index, state.config, items)


def _list_status_view(args: argparse.Namespace, cfg: Config) -> set[str] | None:
  """The status keys a queue flag keeps, or None to use the default list.

  `--in-work` and `--review` name roles, not labels. An empty role is usage.
  """
  chosen = []
  if args.in_work:
    chosen.append("--in-work")
  if args.review:
    chosen.append("--review")
  if args.status:
    chosen.append("--status")
  if args.all:
    chosen.append("--all")
  if (args.in_work or args.review) and len(chosen) > 1:
    raise StateError(f"{' and '.join(chosen)} cannot be combined", code="usage")
  if args.in_work:
    wanted = cfg.in_work()
    if not wanted:
      raise StateError(
        "this project has no in-work status; set started_status or reviewing_status",
        code="usage",
      )
    return wanted
  if args.review:
    if not cfg.review_status:
      raise StateError(
        "this project has no review status; set review_status before using --review",
        code="usage",
      )
    return {cfg.review_status}
  return None


def cmd_list(args: argparse.Namespace) -> int:
  state = _state(args)
  view = _list_status_view(args, state.config)
  if args.all and args.status:
    raise StateError(
      "--all and --status cannot be combined; --status already chooses which statuses to show",
      code="usage",
    )
  items = state.index.items
  if args.tree:
    items = [i for i in items if set(args.tree) & set(i.trees)]
  if args.pass_key:
    items = [i for i in items if i.pass_key == args.pass_key]
  if args.flag:
    items = [i for i in items if set(args.flag) & set(i.flags)]
  hidden_count = 0
  if view is not None:
    items = [i for i in items if i.status in view]
  elif args.status:
    items = [i for i in items if i.status in args.status]
  elif not args.all:
    hidden = {state.config.done_status}
    if state.config.retired_status:
      hidden.add(state.config.retired_status)
    hidden_count = sum(i.status in hidden for i in items)
    items = [i for i in items if i.status not in hidden]
  if getattr(args, "sort", None) == "score":
    # A read-only view: sort a copy by effective score, never the stored order.
    # Ties keep their manual position because Python's sort is stable.
    eff = graph.effective_scores(state.index)
    items = sorted(items, key=lambda i: eff[i.id], reverse=True)
  elif getattr(args, "sort", None) == "effort":
    # Same tie rule, and still a copy: effort order is not the stored queue.
    items = sorted(items, key=model.effort_rank)
  else:
    items = _list_in_next_order(state, items)
  elsewhere, only_sibling = store.sibling_work(state.root, state.index)
  claim_w = _claim_width(state.config, items, elsewhere)
  pass_w = _pass_width(state.index, items)
  status_w = _status_width(state.config, items)
  pass_head = "" if pass_w is None else f"{'PASS':<{pass_w}} "
  header = (
    f"{'#':>3}  {'ID':<5} {'STATUS':<{status_w}} {'CLAIM':<{claim_w}} {pass_head}{'SIZE':<4} "
    f"{'EFFORT':<6} {'SCORE':<5} {'QUADRANT':<9} TITLE"
  )
  lines = [header, *_item_rows(
    state, items, claim_w=claim_w, pass_w=pass_w, status_w=status_w, elsewhere=elsewhere,
  )]
  if not items:
    lines = ["no matching items"]
  if hidden_count:
    noun = "item" if hidden_count == 1 else "items"
    lines.append(f"{hidden_count} {noun} hidden (done or retired); use --all to show them")
  # Text only: `list --json` is a bare array of items, so sibling-only rows are in `status`.
  lines.extend(_sibling_only_lines(only_sibling))
  _emit(args, [
    i.to_dict() | {"in_work_elsewhere": elsewhere.get(i.id, [])} for i in items
  ], "\n".join(lines))
  return OK


FIND_FIELDS = ("id", "title", "short_title", "findings", "body")


def _field_text(state: store.State, item: model.Item, field: str) -> str:
  """The searchable text of one field. `body` is the item's slice prose —
  scope boundary, lead, and every section heading and body — so `find` reaches
  text that lives only inside a slice."""
  if field != "body":
    return getattr(item, field)
  sl = state.slices.get(item.id)
  if sl is None:
    return ""
  parts = [sl.boundary, *sl.lead]
  for section in sl.sections:
    parts += [section.heading, section.body]
  return "\n".join(parts)


def _snippet(text: str, idx: int, length: int, pad: int = 25) -> str:
  """A one-line window around the hit, whitespace collapsed, elided when cut."""
  start, end = max(0, idx - pad), min(len(text), idx + length + pad)
  frag = " ".join(text[start:end].split())
  return ("…" if start > 0 else "") + frag + ("…" if end < len(text) else "")


def _find_match(
  state: store.State, item: model.Item, fields: tuple[str, ...], needle: str
) -> tuple[str, str] | None:
  """The first field (in scope order) whose text contains needle, with a snippet."""
  for field in fields:
    text = _field_text(state, item, field)
    idx = text.casefold().find(needle)
    if idx != -1:
      return field, _snippet(text, idx, len(needle))
  return None


def cmd_find(args: argparse.Namespace) -> int:
  needle = args.pattern.strip()
  if not needle:
    raise StateError("find needs a nonempty pattern", code="usage")
  if args.fields is None:
    fields = FIND_FIELDS
  else:
    fields = tuple(f.strip() for f in args.fields.split(",") if f.strip())
    unknown = [f for f in fields if f not in FIND_FIELDS]
    if unknown or not fields:
      raise StateError(
        f"unknown --in field(s): {', '.join(unknown) or '(none given)'}; "
        f"choose from {', '.join(FIND_FIELDS)}",
        code="usage",
      )
  state = _state(args)
  needle = needle.casefold()
  hits = []
  for item in state.index.items:
    match = _find_match(state, item, fields, needle)
    if match is not None:
      hits.append((item, match[0], match[1]))
  if args.json and args.lean:
    payload = [
      {
        "id": i.id, "title": i.title, "status": i.status, "has_slice": i.has_slice,
        "match": {"field": f, "snippet": s},
      }
      for i, f, s in hits
    ]
  else:
    payload = [i.to_dict() | {"match": {"field": f, "snippet": s}} for i, f, s in hits]
  rows = _item_rows(state, [i for i, _, _ in hits])
  text = "\n".join(
    f"{row}\n      matched in {f}: {s}" for row, (_, f, s) in zip(rows, hits)
  ) or "no matching items"
  _emit(args, payload, text)
  return OK


def cmd_deps(args: argparse.Namespace) -> int:
  state = _state(args)
  index = state.index
  cfg = state.config
  if args.id is not None:
    state.index.require(args.id)
  if args.format == "mermaid":
    diagram = graph.mermaid(index, focus=args.id)
    _emit(args, {"format": "mermaid", "graph": diagram}, diagram)
    return OK
  if args.id is not None:
    item = index.require(args.id)
    blocked = graph.blocked_by(index, item, cfg.done_status)
    dependents = graph.dependents(index)[args.id]
    payload = {
      "id": args.id, "waits_on": item.depends_on,
      "blocked_by": blocked, "dependents": dependents,
    }
    def _mark(dep: str) -> str:
      return f"{dep} (blocked)" if dep in blocked else dep
    lines = [f"{args.id}  {item.display_title()}"]
    lines.append("waits on         " + (", ".join(_mark(d) for d in item.depends_on) or "-"))
    lines.append("depended on by   " + (", ".join(dependents) or "-"))
    _emit(args, payload, "\n".join(lines))
    return OK
  eff = graph.effective_scores(index)
  unblocked = [it for it in index.items
               if it.status == cfg.open_status and not graph.blocked_by(index, it, cfg.done_status)]
  unblocked.sort(key=lambda it: eff[it.id], reverse=True)
  _emit(args, [it.to_dict() for it in unblocked],
        "\n".join(_item_rows(state, unblocked)) or "nothing unblocked")
  return OK


def cmd_show(args: argparse.Namespace) -> int:
  state = _state(args)
  item = state.index.require(args.id)
  sl = state.slices.get(args.id)
  if args.section is not None:
    # The read counterpart to `edit --section`: one section's body, nothing
    # else, so an agent can round-trip a section without re-parsing the render.
    if sl is None:
      raise StateError(
        f"{args.id} has no slice; run `slicer promote {args.id}` first", code="no_slice"
      )
    selected = _sections_named(
      sl, args.section, every_match=bool(args.context and len(args.section) == 1),
    )
    if args.context:
      payload = {
        "id": args.id,
        "title": item.title,
        "depends_on": list(item.depends_on),
        "boundary": sl.boundary,
        "sections": [s.to_dict() for s in selected],
      }
      lines = [
        f"{item.id}  {item.title}",
        "depends    " + (", ".join(item.depends_on) or "(none)"),
        "boundary   " + (sl.boundary or "(none)"),
      ]
      for section in selected:
        lines.extend(["", f"## {section.heading}", section.body])
      _emit(args, payload, "\n".join(lines))
    elif len(args.section) == 1:
      section = selected[0]
      _emit(
        args, {"id": args.id, "section": section.heading, "body": section.body}, section.body
      )
    else:
      payload = {"id": args.id, "sections": [s.to_dict() for s in selected]}
      text = "\n\n".join(f"## {s.heading}\n{s.body}" for s in selected)
      _emit(args, payload, text)
    return OK
  if args.context:
    raise StateError("--context requires at least one --section", code="usage")
  if sl is None:
    lines = [f"{item.id}  {item.display_title()}", *item.notes,
             f"(no slice yet; run `slicer promote {item.id}`)"]
    _emit(args, item.to_dict(), "\n".join(lines))
    return OK
  text = render.render_slice(sl, state.config, state.template("slice.md"), item.notes).decode("utf-8")
  _emit(args, item.to_dict() | {"slice": sl.to_dict()}, text)
  return OK


def cmd_add(args: argparse.Namespace) -> int:
  state = _state(args)
  item = ops.add(
    state, args.title, item_id=args.id, size=args.size or "",
    trees=args.tree or [], findings=args.findings or "", status=args.status,
    pass_key=args.pass_key, importance=args.importance, urgency=args.urgency,
    effort=args.effort, depends_on=args.depends_on, short_title=args.short_title,
    id_floor=_id_floor(state),
  )
  text = f"added {item.id}  {item.display_title()}"
  if item.pass_key:
    text += f" (pass: {item.pass_key})"
  _emit(args, item.to_dict(), text)
  if item.unscored and item.status in (state.config.open_status, state.config.started_status):
    _write_err(args, (
      f"slicer: {item.id} is unscored (importance 2, urgency 2, no effort), so it ranks on "
      f"nothing; pass --importance, --urgency and --effort, or `slicer set {item.id}` later."
    ))
  return OK


def cmd_promote(args: argparse.Namespace) -> int:
  state = _state(args)
  source = None
  source_path = "<promote>"
  if args.file:
    source = _read_user_file(args.file)
    source_path = args.file
  elif args.stdin:
    source = sys.stdin.read()
    source_path = "<stdin>"
  sl = ops.promote(state, args.id, force=args.force, source=source, source_path=source_path, boundary=args.boundary)
  _emit(args, sl.to_dict(), f"promoted {sl.id} -> {state.slice_path(sl.id)}")
  return OK


def cmd_move(args: argparse.Namespace) -> int:
  state = _state(args)
  at = ops.move(state, args.id, before=args.before, after=args.after, to=args.to)
  _emit(args, {"id": args.id, "position": at}, f"{args.id} is now at position {at}")
  return OK


def cmd_sort(args: argparse.Namespace) -> int:
  state = _state(args)
  moved = ops.sort_queue(state, by=args.by)
  text = f"sorted by {args.by}: {moved} item(s) moved" if moved else f"already sorted by {args.by}"
  _emit(args, {"by": args.by, "moved": moved}, text)
  return OK


def cmd_set(args: argparse.Namespace) -> int:
  item_ids, batch = _batch_ids(args)
  state = _state(args)
  if args.no_effort and args.effort is not None:
    raise StateError("--effort and --no-effort cannot be combined", code="usage")
  if (args.add_flag or args.remove_flag) and (args.flag is not None or args.no_flags):
    raise StateError(
      "cannot combine --add-flag or --remove-flag with --flag or --no-flags",
      code="usage",
    )
  flags = [] if args.no_flags else args.flag
  fields = dict(
    title=args.title, short_title=args.short_title, status=args.status,
    size=args.size, trees=args.tree, findings=args.findings, depends_on=args.depends_on,
    pass_key=args.pass_key, flags=flags, group=args.group,
    importance=args.importance, urgency=args.urgency,
    effort=args.effort, attempts=args.attempts,
  )
  fields = {key: value for key, value in fields.items() if value is not None}
  if args.no_effort:
    fields["effort"] = None
  items = ops.set_fields_many(
    state, item_ids, add_flags=args.add_flag, remove_flags=args.remove_flag, **fields,
  )
  _emit_items(args, items, batch, [f"updated {item.id}" for item in items])
  return OK


def cmd_note(args: argparse.Namespace) -> int:
  state = _state(args)
  body = _body_from(args, "")
  if body is None:
    raise StateError("editor exited non-zero; nothing added", code="editor_aborted")
  ops.add_note(state, args.id, body)
  hint = _render_hint(args)
  _emit(args, {"id": args.id, "added": True}, f"added note to {args.id}" + (f"; {hint}" if hint else ""))
  return OK


def cmd_edit(args: argparse.Namespace) -> int:
  if (args.section is not None) == args.boundary:
    raise StateError("choose exactly one of --section or --boundary", code="usage")
  state = _state(args)
  # Look the item up first: "S99 has no slice" is a confusing thing to say
  # about an item that does not exist at all.
  state.index.require(args.id)
  sl = state.slices.get(args.id)
  if sl is None:
    raise StateError(
      f"{args.id} has no slice; run `slicer promote {args.id}` first", code="no_slice"
    )
  if args.boundary and args.append:
    raise StateError("--append cannot be used with --boundary", code="usage")
  section = sl.section(args.section)
  if args.append and args.text is None and args.file is None and not args.stdin:
    raise StateError("--append requires --text, --file, or --stdin", code="usage")
  initial = sl.boundary if args.boundary else (section.body if section else "")
  body = _body_from(args, initial)
  if body is None:
    raise StateError("editor exited non-zero; slice unchanged", code="editor_aborted")
  if args.boundary:
    ops.edit_boundary(state, args.id, body)
    _emit(args, {"id": args.id, "boundary": body}, f"updated {args.id} / boundary")
  else:
    ops.edit_section(state, args.id, args.section, body, append=args.append)
    _emit(args, {"id": args.id, "section": args.section}, f"updated {args.id} / {args.section}")
  return OK


def _via_editor(initial: str) -> str | None:
  """Open $EDITOR on `initial`; None means the editor failed, so change nothing."""
  editor = os.environ.get("EDITOR", "vi")
  with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
    fh.write(initial + "\n")
    path = fh.name
  try:
    done = subprocess.run([*editor.split(), path], check=False)
    if done.returncode != 0:
      return None
    return _read_user_file(path).rstrip("\n")
  finally:
    Path(path).unlink(missing_ok=True)


def _body_from(args: argparse.Namespace, initial: str) -> str | None:
  """Choose one explicit body source, falling back to $EDITOR only if absent."""
  if sum((args.text is not None, args.file is not None, args.stdin)) > 1:
    raise StateError("choose only one of --text, --file, or --stdin", code="usage")
  if args.text is not None:
    return args.text
  if args.file:
    return _read_user_file(args.file).rstrip("\n")
  if args.stdin:
    return sys.stdin.read().rstrip("\n")
  return _via_editor(initial)


def _batch_ids(args: argparse.Namespace) -> tuple[list[str], bool]:
  raw = args.id
  if "-" in raw:
    if raw != ["-"]:
      raise StateError("use '-' alone to read ids from stdin", code="usage")
    item_ids = sys.stdin.read().split()
    if not item_ids:
      raise StateError("stdin contains no item ids", code="usage")
    return list(dict.fromkeys(item_ids)), True
  return list(dict.fromkeys(raw)), len(raw) > 1


def _emit_items(args: argparse.Namespace, items: list[model.Item], batch: bool, lines: list[str]) -> None:
  payload = [item.to_dict() for item in items]
  _emit(args, payload if batch else payload[0], "\n".join(lines))


def _status_cmd(status_attr: str):
  def run(args: argparse.Namespace) -> int:
    item_ids, batch = _batch_ids(args)
    state = _state(args)
    status = getattr(state.config, status_attr)
    if not status and status_attr in ("parked_status", "started_status"):
      name = status_attr.removesuffix("_status")
      raise StateError(f"this project declares no {name} status; set {status_attr} in config")
    items = ops.set_status_many(state, item_ids, status, note=args.note or "")
    _emit_items(args, items, batch,
                [f"{item.id} -> {state.config.status_label(item.status)}" for item in items])
    return OK
  return run


cmd_park = _status_cmd("parked_status")


def cmd_start(args: argparse.Namespace) -> int:
  """Mark items started, then warn if another checkout already names one.

  The warning is stderr only. `next` does not call this, and a failed start
  does not warn. Ids are read once so a batch still loads and saves once.
  """
  item_ids, batch = _batch_ids(args)
  state = _state(args)
  status = state.config.started_status
  if not status:
    raise StateError("this project declares no started status; set started_status in config")
  items = ops.start_many(state, item_ids, note=args.note or "", owner=args.owner)
  _emit_items(args, items, batch,
              [f"{item.id} -> {state.config.status_label(item.status)}" for item in items])
  for item_id in item_ids:
    names = vcs.elsewhere(state.root, item_id)
    if names:
      _write_err(args, f"slicer: {item_id} is also named by {', '.join(names)}")
  return OK


def cmd_release(args: argparse.Namespace) -> int:
  """Clear claims and leave status alone. Ids are read once."""
  item_ids, batch = _batch_ids(args)
  state = _state(args)
  items = ops.release_many(state, item_ids, owner=args.owner)
  _emit_items(args, items, batch, [f"{item.id} released" for item in items])
  return OK


def cmd_handoff(args: argparse.Namespace) -> int:
  """Hand started slices to review: status to review, claim cleared. Ids are read once."""
  item_ids, batch = _batch_ids(args)
  state = _state(args)
  items = ops.handoff_many(state, item_ids, note=args.note or "", owner=args.owner)
  _emit_items(args, items, batch,
              [f"{item.id} -> {state.config.status_label(item.status)}, unclaimed" for item in items])
  return OK


def cmd_reject(args: argparse.Namespace) -> int:
  """Send failed reviews back with a verdict: status to open (or --to), claim cleared."""
  item_ids, batch = _batch_ids(args)
  state = _state(args)
  items = ops.reject_many(state, item_ids, note=args.note, to=args.to, owner=args.owner)
  _emit_items(args, items, batch,
              [f"{item.id} -> {state.config.status_label(item.status)}, unclaimed" for item in items])
  return OK


def cmd_done(args: argparse.Namespace) -> int:
  if getattr(args, "check", False) and not args.render:
    raise StateError("--check has no effect without --render", code="usage")
  item_ids, batch = _batch_ids(args)
  state = _state(args)
  by = ops.actor(state, args.owner)
  if args.render:
    items, written = ops.done_and_render(state, item_ids, note=args.note or "", by=by)
  else:
    items = ops.set_status_many(
      state, item_ids, state.config.done_status, note=args.note or "", by=by
    )
  _emit_items(args, items, batch,
              [f"{item.id} -> {state.config.status_label(item.status)}" for item in items])
  if args.render and not args.json:
    print(f"rendered {written} file(s)")
  if getattr(args, "check", False):
    return _post_check(args, "done")
  return OK


cmd_done.mutates = True


def cmd_prose_list(args: argparse.Namespace) -> int:
  state = _state(args)
  entries = []
  rows = []
  for ref in prose.refs(state.index):
    lines, preview = prose.summary(state.index, ref)
    entries.append({"ref": ref, "lines": lines, "preview": preview})
    unit = "line " if lines == 1 else "lines"
    rows.append(f"  {ref:<22} {lines:>3} {unit}  {preview}")
  _emit(args, entries, "\n".join(rows) or "no prose blocks")
  return OK


def cmd_prose_show(args: argparse.Namespace) -> int:
  state = _state(args)
  text = prose.get(state.index, args.ref)
  _emit(args, {"ref": args.ref, "text": text}, text)
  return OK


def cmd_sections(args: argparse.Namespace) -> int:
  """The project's section names. Config only: no index, lock, or write."""
  root = store.discover(Path(args.root).resolve() if args.root else None)
  cfg = Config.load(root / store.DIR_NAME / CONFIG_NAME)
  required = set(cfg.required_sections)
  lines = [f"{name}  required" if name in required else name for name in cfg.sections]
  _emit(args, {"sections": list(cfg.sections), "required": list(cfg.required_sections)},
        "\n".join(lines))
  return OK


def _config_text(value: object, *, joined: bool) -> str:
  """One config value as text. Strings print bare, a list as lines or a comma list."""
  if isinstance(value, str):
    return value
  if isinstance(value, list):
    items = [_config_text(v, joined=True) for v in value]
    return ", ".join(items) if joined else "\n".join(items)
  return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def cmd_config(args: argparse.Namespace) -> int:
  """The effective config, or one dotted key of it. Config only: no index, lock, or write."""
  root = store.discover(Path(args.root).resolve() if args.root else None)
  data = Config.load(root / store.DIR_NAME / CONFIG_NAME).to_dict()
  if args.key is None:
    width = max(len(k) for k in data)
    lines = [f"{k.ljust(width)}  {_config_text(v, joined=True)}" for k, v in data.items()]
    _emit(args, data, "\n".join(lines))
    return OK
  value: object = data
  for part in args.key.split("."):
    if not isinstance(value, dict) or part not in value:
      raise StateError(
        f"{args.key!r} is not a config key; the top-level keys are {', '.join(data)}",
        code="usage",
      )
    value = value[part]
  _emit(args, {"key": args.key, "value": value}, _config_text(value, joined=False))
  return OK


def cmd_goals(args: argparse.Namespace) -> int:
  """Project direction in one read: the goals and non_goals prose blocks."""
  state = _state(args)
  goals, non_goals = state.index.goals, state.index.non_goals
  blocks = []
  for title, text in (("Goals", goals), ("Non-goals", non_goals)):
    body = text.rstrip("\n") if text.strip() else "  (none set)"
    blocks.append(f"{title}:\n{body}")
  _emit(args, {"goals": goals, "non_goals": non_goals}, "\n\n".join(blocks))
  return OK


def cmd_prose_edit(args: argparse.Namespace) -> int:
  state = _state(args)
  current = prose.get(state.index, args.ref)
  body = _body_from(args, current)
  if body is None:
    raise StateError("editor exited non-zero; roadmap unchanged", code="editor_aborted")
  if body == current:
    _emit(args, {"ref": args.ref, "changed": False}, f"{args.ref} unchanged")
    return OK
  ops.edit_prose(state, args.ref, body)
  hint = _render_hint(args)
  _emit(args, {"ref": args.ref, "changed": True}, f"updated {args.ref}" + (f"; {hint}" if hint else ""))
  return OK


def cmd_prose_add_pass(args: argparse.Namespace) -> int:
  state = _state(args)
  info = ops.add_pass(state, args.key, heading=args.heading or "", after=args.after)
  _emit(
    args,
    info.to_dict(),
    f"created pass {info.key} (no items yet); file items with `slicer add --pass {info.key}`",
  )
  return OK


def cmd_prose_drop_pass(args: argparse.Namespace) -> int:
  state = _state(args)
  ops.drop_pass(state, args.key)
  _emit(args, {"key": args.key, "dropped": True}, f"dropped pass {args.key}")
  return OK


def cmd_remove(args: argparse.Namespace) -> int:
  state = _state(args)
  if args.dry_run:
    preview = ops.remove_preview(state, args.id, purge=args.purge)
    payload = {
      "id": preview.id, "mode": preview.mode, "dry_run": True,
      "blockers": preview.blockers, "file": preview.file,
      "id_freed": preview.id_freed, "reason": preview.reason,
    }
    verb = "purged" if args.purge else "retired"
    lines = [f"{preview.id} would be {verb} (dry run)"]
    if args.purge:
      lines.append(f"     file: {preview.file}" if preview.file else "     no slice file")
      lines.append(f"     id would be {'freed' if preview.id_freed else 'kept burned'}: {preview.reason}")
    else:
      lines.append("     id stays claimed; slice would move to the retired folder")
    if preview.blockers:
      lines.append("     blockers: " + "; ".join(preview.blockers) + " (pass --force)")
    _emit(args, payload, "\n".join(lines))
    return OK
  if args.purge:
    result = ops.purge(state, args.id, force=args.force)
    freed = "freed" if result.id_freed else "kept burned"
    _emit(
      args,
      {
        "id": result.id,
        "mode": "purge",
        "id_freed": result.id_freed,
        "reason": result.reason,
        "file_removed": result.file_removed,
      },
      f"{result.id} deleted \u00b7 id {freed}: {result.reason}",
    )
    return OK
  item = ops.retire(state, args.id, reason=args.reason, force=args.force)
  _emit(
    args,
    item.to_dict() | {"mode": "retire"},
    f"{item.id} retired \u00b7 {item.reason}\n"
    f"     id stays claimed; still listed as {state.config.status_label(item.status)}",
  )
  return OK


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
  state = _state(args)
  offline = verify.offline(state)
  report = verify.against_git(state)
  report.findings = offline.findings + report.findings
  text = "\n".join(
    f"{f.level:<5} {f.item or '-':<5} {f.message}" for f in report.findings
  ) or f"{report.checked} items verified; nothing to report"
  _emit(args, report.to_dict(), text)
  return DRIFT if report.problems else OK


_BACKTICK_SPAN = re.compile(r"`([^`\n]+)`")
_FENCE_OPEN = re.compile(r"(`{3,}|~{3,})")


def _outside_fences(body: str) -> str:
  """`body` with every line of a fenced code block blanked, fence lines included.

  Lines are blanked rather than dropped so line numbers stay put. A fence closes
  on a later line of at least as many of the same character and nothing else; an
  unclosed fence runs to the end, as CommonMark renders it.
  """
  out: list[str] = []
  fence = ""
  for line in body.split("\n"):
    text = line.strip()
    if not fence:
      opened = _FENCE_OPEN.match(text)
      if opened:
        fence = opened.group(1)
        out.append("")
      else:
        out.append(line)
      continue
    out.append("")
    if text.startswith(fence) and not text.strip(fence[0]):
      fence = ""
  return "\n".join(out)


def _unknown_flags(parser: argparse.ArgumentParser, tokens: list[str]) -> list[str]:
  """The flag-looking tokens the real parser does not recognise in `tokens`.

  `parse_known_args` returns unrecognised options rather than erroring on them,
  so an unknown flag comes back in the extras. A command that fails for another
  reason -- a missing positional, an unknown subcommand, `-h` -- is not a flag
  problem, so those raise or exit and are swallowed. Output is redirected because
  argparse writes usage on the way out.
  """
  sink = io.StringIO()
  try:
    with redirect_stdout(sink), redirect_stderr(sink):
      _, extras = parser.parse_known_args(tokens)
  except (_ParserError, SystemExit):
    return []
  return [tok for tok in extras if tok.startswith("-") and tok != "-"]


def _slice_flag_problems(state: store.State) -> list[str]:
  """`slicer ...` examples in live slices whose flags the parser rejects.

  A removed or renamed flag can sit in a slice's Failing tests until an agent
  copies it into a real command; `check` catches it first. Done and retired
  slices are skipped -- they keep their history, old flag names and all. Only
  backtick commands are parsed; a flag merely mentioned in prose is left alone,
  and the command is never executed. Fenced blocks are literal text to write,
  such as the lines a slice adds for a flag it introduces, so they are skipped.
  """
  cfg = state.config
  skip = {cfg.done_status}
  if cfg.retired_status:
    skip.add(cfg.retired_status)
  parser = _cached_parser()
  problems: list[str] = []
  for item in state.index.items:
    if item.status in skip:
      continue
    sl = state.slices.get(item.id)
    if sl is None:
      continue
    for section in sl.sections:
      for span in _BACKTICK_SPAN.findall(_outside_fences(section.body)):
        command = span.strip()
        if not command.startswith("slicer "):
          continue
        try:
          tokens = shlex.split(command)
        except ValueError:
          continue  # unbalanced quotes -- not a flag problem
        for flag in _unknown_flags(parser, tokens[1:]):
          problems.append(
            f"{item.id} / {section.heading}: unknown flag {flag} in `{command}`"
          )
  return problems


def _check_report(state: store.State, *, show_diff: bool = False) -> tuple[check_mod.CheckReport, list[str]]:
  """The check report and its finding lines, without the final passed or failed line."""
  report, expected, diff = check_mod.run(state)
  # A removed flag in a slice's own `slicer ...` examples is drift too.
  report.problems.extend(_slice_flag_problems(state))
  lines: list[str] = []
  for rel, detail in zip(report.stale_render, report.stale_render_details):
    lines.append(f"stale render: {rel}: {detail['detail']}")
    if show_diff:
      lines.append(render.unified(expected, state.render_dir, rel))
  lines.extend(f"orphan render: {rel}" for rel in report.orphan_render)
  lines.extend(f"stale sync: {s}" for s in report.stale_sync)
  lines.extend(f"problem: {p}" for p in report.problems)
  lines.extend(f"warn: {w}" for w in report.warnings)
  return report, lines


def _post_check(args: argparse.Namespace, verb: str) -> int:
  """Run check against the landed state. Silence means it passed."""
  state = store.load(Path(args.root) if args.root else None)
  report, lines = _check_report(state)
  if report.ok:
    return OK
  for line in lines:
    print(line, file=sys.stderr)
  print(
    f"slicer: {verb} landed, but check failed; run `slicer check` for the report",
    file=sys.stderr,
  )
  return DRIFT


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


def _status_tally(cfg: Config, tally: dict[str, int]) -> str:
  """A status tally for people: JSON keys by status, text shows each label."""
  return " · ".join(f"{cfg.status_label(k)} {v}" for k, v in tally.items())


def _census(state: store.State) -> dict:
  """The item census `stats` and `status` share, so they cannot disagree."""
  cfg = state.config
  items = state.index.items
  total = len(items)
  done = sum(1 for it in items if it.status == cfg.done_status)
  by_tree_status = model.cross_counts(items, "trees", "status")
  return {
    "total": total,
    "completion": {"done": done, "total": total, "percent": round(done * 100 / total) if total else 0},
    "by_status": model.counts(items, "status"),
    "by_size": model.counts(items, "size"),
    "by_tree": model.counts(items, "trees"),
    "by_pass": model.counts(items, "pass_key"),
    "by_tree_status": by_tree_status,
  }


def cmd_stats(args: argparse.Namespace) -> int:
  state = _state(args)
  payload = _census(state)
  groups = (
    ("status", payload["by_status"]),
    ("size", payload["by_size"]),
    ("tree", payload["by_tree"]),
    ("pass", payload["by_pass"]),
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


def cmd_status(args: argparse.Namespace) -> int:
  """The one-call front door: what is next, how far along, and what is blocked."""
  state = _state(args)
  elsewhere, only_sibling = store.sibling_work(state.root, state.index)
  result = ops.next_item(state, 0, elsewhere)
  census = _census(state)
  nxt = result.item
  blocked = [{"id": i, "waiting_on": b} for i, b in result.blocked]
  path = state.find_slice_file(nxt.id) if nxt is not None else None
  payload = {
    "next": (nxt.to_dict() | {"path": str(path) if path else None}) if nxt else None,
    "census": census,
    "blocked": blocked,
  }
  next_line = f"{nxt.id}  {nxt.display_title()}" if nxt else "nothing unmarked"
  progress = _status_tally(state.config, census["by_status"])
  lines = [
    f"Next      {next_line}",
    f"Progress  {census['total']} items ({census['completion']['percent']}% done)"
    + (f" · {progress}" if progress else ""),
  ]
  if blocked:
    lines.append("Blocked")
    lines.extend(f"  {b['id']} waits on {', '.join(b['waiting_on'])}" for b in blocked)
  else:
    lines.append("Blocked   none")
  _note_skips(payload, lines, result)
  if only_sibling:
    payload["only_in_sibling"] = only_sibling
    lines.extend(_sibling_only_lines(only_sibling))
  _emit(args, payload, "\n".join(lines))
  return OK


def cmd_log(args: argparse.Namespace) -> int:
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
  # Newest first by timestamp so union-merged history (which can interleave the
  # lines two branches appended) still reads in order. Reverse the append order
  # first so that, among entries sharing a timestamp, the later-appended one is
  # shown first -- a stable sort then keeps that tie-break.
  entries = sorted(reversed(history), key=lambda e: e.when, reverse=True)[: args.limit]
  filters = (args.item or []) + (args.action or []) + (args.by or [])
  empty = ("no history for " + " ".join(filters)) if filters else "no history yet"
  text = "\n".join(
    (f"{e.when}  {e.item:<5} {e.action:<8} {e.frm or '-'} -> {e.to or '-'}  {e.note}".rstrip()
     + (f"  by {e.by}" if e.by else ""))
    for e in entries
  ) or empty
  _emit(args, [e.to_dict() for e in entries], text)
  return OK


def cmd_tui(args: argparse.Namespace) -> int:
  from slicer import tui

  return tui.run(_state(args))


class _ParserError(Exception):
  """Keep argparse's diagnostic context until main can choose the output format."""

  def __init__(self, parser: argparse.ArgumentParser, message: str) -> None:
    super().__init__(message)
    self.parser = parser


class _ArgumentParser(argparse.ArgumentParser):
  def __init__(self, *args: object, **kwargs: object) -> None:
    # Python 3.14 colorizes usage/help/errors by default, and honors FORCE_COLOR
    # even when output is piped -- which injects ANSI an agent then has to strip
    # and makes slicer's diagnostics differ by environment. Keep them plain and
    # deterministic. `color` is a 3.14+ argument, so only pass it there.
    if sys.version_info >= (3, 14):
      kwargs.setdefault("color", False)
    super().__init__(*args, **kwargs)  # type: ignore[arg-type]

  def error(self, message: str) -> None:
    raise _ParserError(self, message)


def build_parser() -> argparse.ArgumentParser:
  # --root is accepted on both sides of the subcommand, because both
  # `slicer --root x next` and `slicer next --root x` are natural to type.
  # The subcommand copy suppresses its default so that, when it is absent, it
  # does not overwrite a value already parsed from before the subcommand.
  common = argparse.ArgumentParser(add_help=False)
  common.add_argument(
    "--root",
    default=argparse.SUPPRESS,
    help="project root (default: discovered from the working directory)",
  )
  p = _ArgumentParser(
    prog="slicer", description=_DESCRIPTION,
    epilog=f"Source: {__url__}\nIssues: {_ISSUES_URL}",
    formatter_class=argparse.RawDescriptionHelpFormatter,
  )
  p.add_argument(
    "--root", default=None, help="project root (default: discovered from the working directory)"
  )
  p.add_argument("--version", action="version", version=f"slicer {__version__}")
  p.add_argument("--about", action="store_true", help="show version, description, source and issues URLs")
  sub = p.add_subparsers(dest="command", required=True)

  def add(name: str, fn, help_: str, *, json_flag: bool = True,
          aliases: tuple[str, ...] = ()) -> argparse.ArgumentParser:
    sp = sub.add_parser(name, help=help_, parents=[common], aliases=list(aliases))
    sp.set_defaults(func=fn)
    if json_flag:
      _json_flags(sp)
    return sp

  sp = sub.add_parser("ai", help="onboarding instructions for coding agents", parents=[common])
  # A missing subcommand is `instructions`. `--json` here is that command's
  # flag, not a second payload. The subcommand copy suppresses its default.
  _json_flags(sp)
  _rest_flag(sp)
  sp.set_defaults(func=cmd_ai_instructions)
  aisub = sp.add_subparsers(dest="ai_command", required=False)
  inner = aisub.add_parser(
    "instructions", help="print the agent quick start (no project needed)",
    description=(
      "Print the agent quick start. A project with implement_finish handoff "
      "changes step 4. No project, or a config or index that cannot be read, "
      "prints the generic text and warns on stderr. Does not lock or write."
    ),
    parents=[common],
  )
  inner.set_defaults(func=cmd_ai_instructions)
  _json_flags(inner, suppress=True)
  _rest_flag(inner, suppress=True)
  inner = aisub.add_parser(
    "skill", help="print the agent skill for Claude Code, Codex, and Grok",
    description=(
      "Print the SKILL.md for the implement loop. Same project read and "
      "fallback as instructions: warns on stderr, does not lock or write."
    ),
    parents=[common],
  )
  inner.set_defaults(func=cmd_ai_skill)
  _json_flags(inner, suppress=True)

  sp = add("init", cmd_init, "create .slicer/ in a project")
  sp.add_argument("--force", action="store_true", help="overwrite an existing config and templates")
  sp.add_argument("--id", help="id the first add allocates, such as S21")

  add("setup-git", cmd_setup_git,
      "print the git config that turns on the render merge driver (run once per clone)")

  sp = _strict_flag(_render_flag(add("import", _mutating(cmd_import), "add items in bulk from a markdown outline")))
  sp.add_argument("file", nargs="?", help="the outline file")
  sp.add_argument("--skeleton", action="store_true", help="print a template and exit")
  sp.add_argument("--dry-run", action="store_true", help="report only; write nothing")
  sp.add_argument("--force", action="store_true", help="add even when a title already exists")
  # Caught in the handler so a script written against the old `import
  # --from DIR` gets told where that moved, rather than a bare argparse error.
  sp.add_argument("--from", dest="legacy_from", default=None, help=argparse.SUPPRESS)

  sp = _strict_flag(_render_flag(add("migrate", _mutating(cmd_migrate), "convert an existing markdown slice tree")))
  sp.add_argument("--from", dest="source", default="docs/slices", help="the legacy directory")
  sp.add_argument("--dry-run", action="store_true", help="report only; write nothing")
  sp.add_argument("--force", action="store_true", help="replace an existing roadmap")

  sp = add("next", cmd_next, "the highest-priority startable item")
  sp.add_argument("-n", type=_nonnegative_int, default=None, metavar="N",
                  help="skip N currently eligible items (default 0); return one item")
  sp.add_argument("--batch", type=_positive_int, default=None, metavar="K",
                  help="return up to K items; a dependent follows its dependency")
  sp.add_argument("--tree", help="only items in this tree (one tree; not repeatable)")
  sp.add_argument("--size", help="only items of this exact size")
  sp.add_argument("--start", action="store_true", help="mark the returned item or batch started")
  sp.add_argument("--show", action="store_true",
                  help="also include the item's full slice, as `show` returns it")
  sp.add_argument("--ready", action="store_true",
                  help="bounded pickup: item identity, its slice, and blocked ids")
  sp.add_argument("--section", action="append",
                  help="with --ready, return only this section (repeatable)")
  sp.add_argument("--owner", help="with --start, who to claim as (overrides SLICER_CLAIM_OWNER and claim_owner)")
  sp.add_argument("--status", help="draw from this queue instead; only the review status is supported")
  sp.add_argument("--path", action="store_true",
                  help="also print the slice file path (text only; JSON is unchanged)")

  sp = add("next-id", cmd_next_id, "the id the next add would take, without allocating it")

  sp = _strict_flag(_render_flag(add(
    "id-prefix", _mutating(cmd_id_prefix),
    "show the id prefix, or change its case for new ids",
  )))
  sp.add_argument("prefix", nargs="?", help="the new prefix; only its case may differ")
  sp.add_argument("--dry-run", action="store_true", help="report only; write nothing")

  sp = add("list", cmd_list, "list items in next's order, omitting done and retired unless asked")
  sp.add_argument("--all", action="store_true",
                  help="include done and retired items (default: omit them)")
  sp.add_argument("--status", action="append",
                  help="filter by status (repeatable); replaces the default of omitting done and retired")
  sp.add_argument("--in-work", action="store_true",
                  help="only started items, plus reviewing when that status is set")
  sp.add_argument("--review", action="store_true",
                  help="only items in the review status")
  sp.add_argument("--tree", action="append", help="filter by tree (repeatable)")
  sp.add_argument("--pass", dest="pass_key", help="filter by pass")
  sp.add_argument("--flag", action="append",
                  help="filter by flag (repeatable; an item matches if it has any of them)")
  sp.add_argument("--sort", choices=["score", "effort"],
                  help="score: flat priority, highest first; effort: lightest estimate first, unset last")

  sp = add("deps", cmd_deps, "dependencies: unblocked items, or one item's edges")
  sp.add_argument("id", nargs="?")
  sp.add_argument("--format", choices=["mermaid"], help="render the dependency graph")

  sp = add("find", cmd_find, "search items by text")
  sp.add_argument("pattern")
  sp.add_argument(
    "--in", dest="fields",
    help="comma-separated fields to search: id,title,short_title,findings,body (default: all)",
  )

  sp = add("show", cmd_show, "print one slice")
  sp.add_argument("id")
  sp.add_argument(
    "--section", action="append",
    help="print only this section's body; repeat to select several",
  )
  sp.add_argument("--context", action="store_true",
                  help="with --section, include title, dependencies, and scope boundary")

  add("sections", cmd_sections,
      "list configured section names, marking the ones next requires")

  sp = add("config", cmd_config,
           "print the effective config, or one value by dotted key; read-only")
  sp.add_argument("key", nargs="?", help="a top-level key or a dotted path such as id.prefix")

  add("recommended-workflow", cmd_recommended_workflow,
      "print the generic recommended project workflow")

  sp = _strict_flag(_render_flag(add("add", _mutating(cmd_add), "append a roadmap item")))
  sp.add_argument("title")
  sp.add_argument("--depends-on", action="append", help="dependency id (repeatable)")
  sp.add_argument("--short-title", help="short title for the roadmap row")
  sp.add_argument("--id", help="use this id instead of the next free one")
  sp.add_argument("--size")
  sp.add_argument("--tree", action="append")
  sp.add_argument("--findings")
  sp.add_argument("--status")
  sp.add_argument("--pass", dest="pass_key",
                  help="file the item under this pass group (default: the previous item's pass; '' for none)")
  sp.add_argument("--importance", type=int, help="1-3; how important (default 2)")
  sp.add_argument("--urgency", type=int, help="1-3; how urgent (default 2)")
  sp.add_argument("--effort", type=int, help="1-3; optional estimate, omit to leave unset")

  sp = _strict_flag(_render_flag(add("promote", _mutating(cmd_promote), "give an item a slice file")))
  sp.add_argument("id")
  sp.add_argument("--force", action="store_true", help="overwrite an existing slice")
  sp.add_argument("--file", help="a one-item outline whose sections fill the slice")
  sp.add_argument("--stdin", action="store_true", help="read that outline from stdin")
  sp.add_argument("--boundary", help="full scope-boundary paragraph; overrides source/default; empty clears")

  sp = _strict_flag(_render_flag(add("move", _mutating(cmd_move), "reorder the queue")))
  sp.add_argument("id")
  sp.add_argument("--before")
  sp.add_argument("--after")
  sp.add_argument("--to", type=int)

  sp = _strict_flag(_render_flag(add("sort", _mutating(cmd_sort), "reorder the whole queue by priority score")))
  sp.add_argument("--by", choices=["score", "effort"], default="score",
                  help="score (default) or effort: lightest estimate first, unset last")

  sp = _strict_flag(_render_flag(add("set", _mutating(cmd_set), "change an item's fields")))
  sp.add_argument("id", nargs="+", help="item ids, or - alone to read whitespace-separated ids from stdin")
  sp.add_argument("--title", help="also updates a short title that still matches the title")
  sp.add_argument("--short-title", dest="short_title")
  sp.add_argument("--status")
  sp.add_argument("--size")
  sp.add_argument("--tree", action="append")
  sp.add_argument("--findings")
  sp.add_argument("--depends-on", dest="depends_on", action="append",
                  help="dependency id (repeatable; replaces the list)")
  sp.add_argument("--pass", dest="pass_key", help="move the item to this pass group")
  sp.add_argument("--flag", dest="flag", action="append", help="set a flag (repeatable; replaces the list)")
  sp.add_argument("--add-flag", action="append", help="add a flag without replacing the list (repeatable)")
  sp.add_argument("--remove-flag", action="append", help="remove a flag without replacing the list (repeatable)")
  sp.add_argument("--no-flags", dest="no_flags", action="store_true", help="clear all flags")
  sp.add_argument("--group", help="the phase-label group; --group '' clears it")
  sp.add_argument("--importance", type=int, help="1-3")
  sp.add_argument("--urgency", type=int, help="1-3")
  sp.add_argument("--effort", type=int, help="1-3; optional estimate")
  sp.add_argument("--no-effort", action="store_true", help="clear the effort estimate")
  sp.add_argument("--attempts", type=int, help="implementation attempt count; an integer >= 0")

  sp = _strict_flag(_render_flag(add("edit", _mutating(cmd_edit), "edit a slice section or scope boundary")))
  sp.add_argument("id")
  sp.add_argument("--section", help="section to edit; cannot combine with --boundary")
  sp.add_argument("--boundary", action="store_true", help="replace the full scope-boundary paragraph")
  sp.add_argument("--append", action="store_true", help="append with a blank line; requires --text, --file, or --stdin; empty input leaves the body unchanged")
  sp.add_argument("--text", help="inline body (exact in replacement mode); empty text clears unless appending; cannot combine with --file/--stdin")
  sp.add_argument("--file")
  sp.add_argument("--stdin", action="store_true")

  sp = _strict_flag(_render_flag(add("note", _mutating(cmd_note), "append a dated note to an item")))
  sp.add_argument("id")
  sp.add_argument("--text", help="inline note; cannot combine with --file/--stdin")
  sp.add_argument("--file")
  sp.add_argument("--stdin", action="store_true")

  sp = _check_flag(_render_flag(add("done", cmd_done, "mark an item finished")))
  sp.add_argument("id", nargs="+", help="item ids, or - alone to read whitespace-separated ids from stdin")
  sp.add_argument("--note", help="one line recorded in history (see `slicer note` for a durable note on the item)")
  sp.add_argument("--owner", help="who to record (overrides SLICER_CLAIM_OWNER and claim_owner)")

  sp = _strict_flag(_render_flag(add("start", _mutating(cmd_start), "mark an item in progress and claim it")))
  sp.add_argument("id", nargs="+", help="item ids, or - alone to read whitespace-separated ids from stdin")
  sp.add_argument("--note", help="one line recorded in history (see `slicer note` for a durable note on the item)")
  sp.add_argument("--owner", help="who to record (overrides SLICER_CLAIM_OWNER and claim_owner)")

  sp = _strict_flag(_render_flag(add("release", _mutating(cmd_release), "clear a claim without changing status")))
  sp.add_argument("id", nargs="+", help="item ids, or - alone to read whitespace-separated ids from stdin")
  sp.add_argument("--owner", help="who to record (overrides SLICER_CLAIM_OWNER and claim_owner)")

  sp = _check_flag(_strict_flag(_render_flag(add("handoff", _mutating(cmd_handoff), "hand a started slice to review and clear its claim"))))
  sp.add_argument("id", nargs="+", help="item ids, or - alone to read whitespace-separated ids from stdin")
  sp.add_argument("--note", help="one line recorded in history (see `slicer note` for a durable note on the item)")
  sp.add_argument("--owner", help="who to record (overrides SLICER_CLAIM_OWNER and claim_owner)")

  sp = _strict_flag(_render_flag(add("reject", _mutating(cmd_reject), "send a review back with a verdict and clear its claim")))
  sp.add_argument("id", nargs="+", help="item ids, or - alone to read whitespace-separated ids from stdin")
  sp.add_argument("--note", required=True, help="the verdict: added to the item's notes and its history entry")
  sp.add_argument("--to", help="the status to send it back to (default: the open status)")
  sp.add_argument("--owner", help="who to record (overrides SLICER_CLAIM_OWNER and claim_owner)")

  sp = _strict_flag(_render_flag(add("park", _mutating(cmd_park), "set an item aside")))
  sp.add_argument("id", nargs="+", help="item ids, or - alone to read whitespace-separated ids from stdin")
  sp.add_argument("--note", help="one line recorded in history (see `slicer note` for a durable note on the item)")

  sp = _strict_flag(_render_flag(add("unpark", _mutating(_status_cmd("open_status")), "return a parked item to the queue")))
  sp.add_argument("id", nargs="+", help="item ids, or - alone to read whitespace-separated ids from stdin")
  sp.add_argument("--note", help="one line recorded in history (see `slicer note` for a durable note on the item)")

  sp = sub.add_parser("prose", help="read and edit the roadmap's own prose", parents=[common])
  psub = sp.add_subparsers(dest="prose_command", required=True)

  def padd(name: str, fn, help_: str) -> argparse.ArgumentParser:
    inner = psub.add_parser(name, help=help_, parents=[common])
    inner.set_defaults(func=fn)
    _json_flags(inner)
    return inner

  padd("list", cmd_prose_list, "every addressable block, in declaration order")
  padd("show", cmd_prose_show, "print one block").add_argument("ref")

  inner = _strict_flag(_render_flag(padd("edit", _mutating(cmd_prose_edit), "replace one block")))
  inner.add_argument("ref")
  inner.add_argument("--text", help="inline body, preserved exactly; empty text clears it; cannot combine with --file/--stdin")
  inner.add_argument("--file")
  inner.add_argument("--stdin", action="store_true")

  inner = _strict_flag(_render_flag(padd("add-pass", _mutating(cmd_prose_add_pass), "declare a new pass group")))
  inner.add_argument("key")
  inner.add_argument("--heading", help="the markdown heading for the group")
  inner.add_argument("--after", help="insert after this pass instead of at the end")

  _strict_flag(_render_flag(padd("drop-pass", _mutating(cmd_prose_drop_pass), "remove an empty pass group"))).add_argument("key")

  sp = _strict_flag(_render_flag(add("remove", _mutating(cmd_remove), "retire an obsolete item, or purge one outright")))
  sp.add_argument("id")
  mode = sp.add_mutually_exclusive_group(required=True)
  mode.add_argument("--reason", help="retire it, recording why; the id stays claimed")
  mode.add_argument(
    "--purge", action="store_true", help="delete it outright, for something that never should have existed"
  )
  sp.add_argument("--force", action="store_true", help="override the dependents and done guards")
  sp.add_argument("--dry-run", action="store_true", help="preview the removal and its fallout; write nothing")

  add("render", cmd_render, "regenerate .slicer/render/")

  sp = add("sync", cmd_sync, "rewrite derived lines in other documents")
  sp.add_argument("--check", action="store_true", help="report drift instead of writing")

  add("verify", cmd_verify, "check the index for consistency (and against git unless git_check is off)")

  sp = add("check", cmd_check, "the CI gate: render, sync and integrity")
  sp.add_argument("--diff", action="store_true", help="show a diff for each stale file")

  add("goals", cmd_goals, "show project goals and non-goals")

  add("stats", cmd_stats, "counts by status, size, tree and pass")

  add("status", cmd_status, "next item, progress, and blockers in one view")

  sp = add("log", cmd_log, "recent history, newest first")
  sp.add_argument("--limit", type=int, default=20, help="how many entries (default 20)")
  sp.add_argument("--item", action="append", help="filter to these item ids (repeatable)")
  sp.add_argument("--action", action="append", help="filter to these actions, e.g. set, edit (repeatable)")
  sp.add_argument("--by", action="append", help="filter to actions recorded by these owners (repeatable)")

  add("tui", cmd_tui, "browse and reorder interactively (also: ui)",
      json_flag=False, aliases=("ui",))
  return p


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
  return INTERNAL if is_internal(exc.code) else USAGE


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


def _changes_state(args: argparse.Namespace) -> bool:
  if args.command == "init":
    return True
  if not getattr(args.func, "mutates", False):
    return False
  if args.command in ("import", "migrate", "remove") and args.dry_run:
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
    root = Path(args.root) if args.root else Path.cwd()
    _warn_code_mismatch(args)
    if _changes_state(args):
      vcs.require_no_merge(root)
    # A mutating command holds an advisory lock for its whole run, so two
    # writers serialise instead of racing (a duplicated id, a half-applied
    # outline). Read-only commands need no lock.
    if getattr(args.func, "mutates", False):
      # A create-path migrate targets its own new .slicer/, not an ancestor's.
      # There is no target project lock until that directory exists.
      creating_migrate = (
        args.command == "migrate"
        and not (root.resolve() / store.DIR_NAME / CONFIG_NAME).is_file()
      )
      with (nullcontext() if creating_migrate else store.project_lock(root)):
        if _changes_state(args):
          vcs.require_no_merge(root)
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
