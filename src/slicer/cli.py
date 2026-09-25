#!/usr/bin/env python3
"""Command line entry point.

Exit codes are stable because scripts depend on them:
  0  fine        1  drift, or a check failed        2  usage, or nothing to do
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from slicer import check as check_mod
from slicer import (
  graph,
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
)
from slicer.config import CONFIG_NAME, Config
from slicer.errors import SlicerError, StateError

OK, DRIFT, USAGE = 0, 1, 2


def _emit(args: argparse.Namespace, payload: object, text: str) -> None:
  if getattr(args, "json", False):
    print(json.dumps(payload, ensure_ascii=False, indent=2))
  elif text:
    print(text)


def _mutating(fn):
  """Wrap a mutating handler so `--render` renders after it, from one place.

  The mutation has already saved to disk, so state is re-read and rendered;
  centralising it here keeps every mutating command in step rather than each
  growing its own render step.
  """
  def wrapped(args: argparse.Namespace) -> int:
    code = fn(args)
    if code == OK and getattr(args, "render", False) and not getattr(args, "dry_run", False):
      state = _state(args)
      expected = render.plan(state)
      written = render.write(expected, state.render_dir, render.compare(expected, state.render_dir))
      if not getattr(args, "json", False):
        print(f"rendered {len(written)} file(s)")
    return code

  return wrapped


def _render_flag(sp: argparse.ArgumentParser) -> argparse.ArgumentParser:
  sp.add_argument("--render", action="store_true", help="render .slicer/render/ after the change")
  return sp


def _state(args: argparse.Namespace) -> store.State:
  return store.load(Path(args.root) if args.root else None)


def cmd_init(args: argparse.Namespace) -> int:
  root = Path(args.root or ".").resolve()
  base = root / store.DIR_NAME
  if (base / CONFIG_NAME).exists() and not args.force:
    raise StateError(
      f"{base} already exists; pass --force to overwrite its config and templates",
      code="already_exists",
    )
  cfg = Config()
  jsonio.write(base / CONFIG_NAME, cfg.to_dict())
  index_path = base / store.INDEX_NAME
  if not index_path.exists():
    jsonio.write(index_path, model.Index(id_prefix=cfg.id_prefix, id_width=cfg.id_width).to_dict())
  for name, text in templates.defaults().items():
    jsonio.write_text(base / store.TEMPLATES_DIR / name, text)
  (base / store.SLICES_DIR / cfg.done_dir).mkdir(parents=True, exist_ok=True)
  _emit(args, {"root": str(root), "dir": str(base)}, f"initialised {base}")
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

  A paragraph after the keys and before the first `###` becomes the slice's
  lead. Each `### ` heading is a section of the slice; an item with no
  sections is a roadmap row only.

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
  specs = outline.parse(_read_user_file(path), path=str(path))

  if args.dry_run:
    report = ops.outline_report(state, specs, force=args.force)
  else:
    report = ops.apply_outline(state, specs, force=args.force)

  lines = [
    f"source     {path}",
    f"outline    {report.items} items, {report.promoted} with slices",
    "status     " + " · ".join(f"{k} {v}" for k, v in report.by_status.items()),
    f"depends    {report.depends_edges} edges",
  ]
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
  if args.dry_run:
    lines.append("nothing written; drop --dry-run to apply")
    _emit(args, report.to_dict(), "\n".join(lines))
    return OK

  lines.append(f"added      {', '.join(report.ids)}")
  lines.append("now run `slicer render`")
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
    f"status     " + " · ".join(f"{k} {v}" for k, v in report.by_status.items()),
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


def cmd_next(args: argparse.Namespace) -> int:
  state = _state(args)
  result = ops.next_item(state)
  if result.item is None:
    payload = {"item": None, "blocked": [{"id": i, "waiting_on": b} for i, b in result.blocked]}
    text = "nothing unmarked" if not result.blocked else "\n".join(
      f"blocked {i} waits on {', '.join(b)}" for i, b in result.blocked
    )
    _emit(args, payload, text)
    return USAGE
  item = result.item
  path = state.find_slice_file(item.id)
  payload = item.to_dict() | {"path": str(path) if path else None}
  _emit(args, payload, f"{item.id}  {item.display_title()}" + (f"\n     {path}" if path else ""))
  return OK


def cmd_list(args: argparse.Namespace) -> int:
  state = _state(args)
  cfg = state.config
  items = state.index.items
  if args.status:
    items = [i for i in items if i.status in args.status]
  if args.tree:
    items = [i for i in items if set(args.tree) & set(i.trees)]
  if args.pass_key:
    items = [i for i in items if i.pass_key == args.pass_key]
  # Effective score: an item inherits the priority of anything that depends on
  # it, so a blocker of a critical item ranks with it. `^` marks an inherited
  # boost above the item's own score.
  eff = graph.effective_scores(state.index)
  if getattr(args, "sort", None) == "score":
    # A read-only view: sort a copy by effective score, never the stored order.
    # Ties keep their manual position because Python's sort is stable.
    items = sorted(items, key=lambda i: eff[i.id], reverse=True)
  rows = [
    f"{n:>3}  {i.id:<5} {cfg.status_label(i.status):<7} {i.size:<2} "
    f"{eff[i.id]:>2}{'^' if eff[i.id] > i.score else ' '} {i.quadrant:<9} {i.display_title()}"
    for n, i in enumerate(items, 1)
  ]
  _emit(args, [i.to_dict() for i in items], "\n".join(rows) or "no matching items")
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
    section = sl.section(args.section)
    if section is None:
      raise StateError(
        f"{args.id} has no section {args.section!r}", code="no_such_section"
      )
    _emit(args, {"id": args.id, "section": section.heading, "body": section.body}, section.body)
    return OK
  if sl is None:
    _emit(args, item.to_dict(), f"{item.id}  {item.display_title()}\n(no slice yet; run `slicer promote {item.id}`)")
    return OK
  text = render.render_slice(sl, state.config, state.template("slice.md")).decode("utf-8")
  _emit(args, item.to_dict() | {"slice": sl.to_dict()}, text)
  return OK


def cmd_add(args: argparse.Namespace) -> int:
  state = _state(args)
  item = ops.add(
    state, args.title, item_id=args.id, size=args.size or "",
    trees=args.tree or [], findings=args.findings or "", status=args.status,
    pass_key=args.pass_key, importance=args.importance, urgency=args.urgency,
  )
  _emit(args, item.to_dict(), f"added {item.id}  {item.display_title()}")
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
  sl = ops.promote(state, args.id, force=args.force, source=source, source_path=source_path)
  _emit(args, sl.to_dict(), f"promoted {sl.id} -> {state.slice_path(sl.id)}")
  return OK


def cmd_move(args: argparse.Namespace) -> int:
  state = _state(args)
  at = ops.move(state, args.id, before=args.before, after=args.after, to=args.to)
  _emit(args, {"id": args.id, "position": at}, f"{args.id} is now at position {at}")
  return OK


def cmd_set(args: argparse.Namespace) -> int:
  state = _state(args)
  flags = [] if args.no_flags else args.flag
  item = ops.set_fields(
    state, args.id, title=args.title, short_title=args.short_title, status=args.status,
    size=args.size, trees=args.tree, findings=args.findings, depends_on=args.depends_on,
    pass_key=args.pass_key, flags=flags, group=args.group,
    importance=args.importance, urgency=args.urgency,
  )
  _emit(args, item.to_dict(), f"updated {item.id}")
  return OK


def cmd_edit(args: argparse.Namespace) -> int:
  state = _state(args)
  # Look the item up first: "S99 has no slice" is a confusing thing to say
  # about an item that does not exist at all.
  state.index.require(args.id)
  sl = state.slices.get(args.id)
  if sl is None:
    raise StateError(
      f"{args.id} has no slice; run `slicer promote {args.id}` first", code="no_slice"
    )
  section = sl.section(args.section)
  body = _body_from(args, section.body if section else "")
  if body is None:
    raise StateError("editor exited non-zero; slice unchanged", code="editor_aborted")
  ops.edit_section(state, args.id, args.section, body)
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
  """The new text for an edit: a file, stdin, or $EDITOR."""
  if args.file:
    return _read_user_file(args.file).rstrip("\n")
  if args.stdin:
    return sys.stdin.read().rstrip("\n")
  return _via_editor(initial)


def _status_cmd(status_attr: str):
  def run(args: argparse.Namespace) -> int:
    state = _state(args)
    status = getattr(state.config, status_attr, status_attr)
    item = ops.set_status(state, args.id, status, note=getattr(args, "note", "") or "")
    _emit(args, item.to_dict(), f"{item.id} -> {state.config.status_label(item.status)}")
    return OK
  return run


def cmd_park(args: argparse.Namespace) -> int:
  state = _state(args)
  item = ops.park(state, args.id, note=getattr(args, "note", "") or "")
  _emit(args, item.to_dict(), f"{item.id} -> {state.config.status_label(item.status)}")
  return OK


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
  _emit(args, {"ref": args.ref, "changed": True}, f"updated {args.ref}; run `slicer render`")
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


def cmd_check(args: argparse.Namespace) -> int:
  state = _state(args)
  report, expected, diff = check_mod.run(state)
  lines: list[str] = []
  for rel in report.stale_render:
    lines.append(f"stale render: {rel}")
    if args.diff:
      lines.append(render.unified(expected, state.render_dir, rel))
  lines.extend(f"orphan render: {rel}" for rel in report.orphan_render)
  lines.extend(f"stale sync: {s}" for s in report.stale_sync)
  lines.extend(f"problem: {p}" for p in report.problems)
  lines.extend(f"warn: {w}" for w in report.warnings)
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
  cfg = state.config
  items = state.index.items
  payload = {
    "total": len(items),
    "by_status": {cfg.status_label(k): v for k, v in model.counts(items, "status").items()},
    "by_size": model.counts(items, "size"),
    "by_tree": model.counts(items, "trees"),
    "by_pass": model.counts(items, "pass_key"),
  }
  groups = (
    ("status", payload["by_status"]),
    ("size", payload["by_size"]),
    ("tree", payload["by_tree"]),
    ("pass", payload["by_pass"]),
  )
  lines = [f"{payload['total']} items"]
  for name, group in groups:
    if group:
      lines.append(f"{name:<10} " + " · ".join(f"{k} {v}" for k, v in group.items()))
  text = "\n".join(lines)
  _emit(args, payload, text)
  return OK


def cmd_log(args: argparse.Namespace) -> int:
  state = _state(args)
  entries = list(reversed(state.history()))[: args.limit]
  text = "\n".join(
    f"{e.when}  {e.item:<5} {e.action:<8} {e.frm or '-'} -> {e.to or '-'}  {e.note}".rstrip()
    for e in entries
  ) or "no history yet"
  _emit(args, [e.to_dict() for e in entries], text)
  return OK


def cmd_tui(args: argparse.Namespace) -> int:
  from slicer import tui

  return tui.run(_state(args))


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
  p = argparse.ArgumentParser(prog="slicer", description=__doc__.splitlines()[0])
  p.add_argument(
    "--root", default=None, help="project root (default: discovered from the working directory)"
  )
  sub = p.add_subparsers(dest="command", required=True)

  def add(name: str, fn, help_: str, *, json_flag: bool = True) -> argparse.ArgumentParser:
    sp = sub.add_parser(name, help=help_, parents=[common])
    sp.set_defaults(func=fn)
    if json_flag:
      sp.add_argument("--json", action="store_true", help="machine-readable output")
    return sp

  sp = add("init", cmd_init, "create .slicer/ in a project")
  sp.add_argument("--force", action="store_true", help="overwrite an existing config and templates")

  sp = _render_flag(add("import", _mutating(cmd_import), "add items in bulk from a markdown outline"))
  sp.add_argument("file", nargs="?", help="the outline file")
  sp.add_argument("--skeleton", action="store_true", help="print a template and exit")
  sp.add_argument("--dry-run", action="store_true", help="report only; write nothing")
  sp.add_argument("--force", action="store_true", help="add even when a title already exists")
  # Caught in the handler so a script written against the old `import
  # --from DIR` gets told where that moved, rather than a bare argparse error.
  sp.add_argument("--from", dest="legacy_from", default=None, help=argparse.SUPPRESS)

  sp = _render_flag(add("migrate", _mutating(cmd_migrate), "convert an existing markdown slice tree"))
  sp.add_argument("--from", dest="source", default="docs/slices", help="the legacy directory")
  sp.add_argument("--dry-run", action="store_true", help="report only; write nothing")
  sp.add_argument("--force", action="store_true", help="replace an existing roadmap")

  add("next", cmd_next, "the highest-priority startable item")

  sp = add("list", cmd_list, "list items")
  sp.add_argument("--status", action="append", help="filter by status (repeatable)")
  sp.add_argument("--tree", action="append", help="filter by tree (repeatable)")
  sp.add_argument("--pass", dest="pass_key", help="filter by pass")
  sp.add_argument("--sort", choices=["score"], help="order by priority score, highest first")

  sp = add("show", cmd_show, "print one slice")
  sp.add_argument("id")
  sp.add_argument("--section", help="print only this section's body")

  sp = _render_flag(add("add", _mutating(cmd_add), "append a roadmap item"))
  sp.add_argument("title")
  sp.add_argument("--id", help="use this id instead of the next free one")
  sp.add_argument("--size")
  sp.add_argument("--tree", action="append")
  sp.add_argument("--findings")
  sp.add_argument("--status")
  sp.add_argument("--pass", dest="pass_key", help="file the item under this pass group")
  sp.add_argument("--importance", type=int, help="1-3; how important (default 2)")
  sp.add_argument("--urgency", type=int, help="1-3; how urgent (default 2)")

  sp = _render_flag(add("promote", _mutating(cmd_promote), "give an item a slice file"))
  sp.add_argument("id")
  sp.add_argument("--force", action="store_true")
  sp.add_argument("--file", help="a one-item outline whose sections fill the slice")
  sp.add_argument("--stdin", action="store_true", help="read that outline from stdin")

  sp = _render_flag(add("move", _mutating(cmd_move), "reorder the queue"))
  sp.add_argument("id")
  sp.add_argument("--before")
  sp.add_argument("--after")
  sp.add_argument("--to", type=int)

  sp = _render_flag(add("set", _mutating(cmd_set), "change an item's fields"))
  sp.add_argument("id")
  sp.add_argument("--title")
  sp.add_argument("--short-title", dest="short_title")
  sp.add_argument("--status")
  sp.add_argument("--size")
  sp.add_argument("--tree", action="append")
  sp.add_argument("--findings")
  sp.add_argument("--depends-on", dest="depends_on", action="append")
  sp.add_argument("--pass", dest="pass_key", help="move the item to this pass group")
  sp.add_argument("--flag", dest="flag", action="append", help="set a flag (repeatable; replaces the list)")
  sp.add_argument("--no-flags", dest="no_flags", action="store_true", help="clear all flags")
  sp.add_argument("--group", help="the phase-label group; --group '' clears it")
  sp.add_argument("--importance", type=int, help="1-3")
  sp.add_argument("--urgency", type=int, help="1-3")

  sp = _render_flag(add("edit", _mutating(cmd_edit), "replace one section of a slice"))
  sp.add_argument("id")
  sp.add_argument("--section", required=True)
  sp.add_argument("--file")
  sp.add_argument("--stdin", action="store_true")

  sp = _render_flag(add("done", _mutating(_status_cmd("done_status")), "mark an item finished"))
  sp.add_argument("id")
  sp.add_argument("--note", help="one line for the log")

  sp = _render_flag(add("park", _mutating(cmd_park), "set an item aside"))
  sp.add_argument("id")

  sp = _render_flag(add("unpark", _mutating(_status_cmd("open_status")), "return a parked item to the queue"))
  sp.add_argument("id")

  sp = sub.add_parser("prose", help="read and edit the roadmap's own prose", parents=[common])
  psub = sp.add_subparsers(dest="prose_command", required=True)

  def padd(name: str, fn, help_: str) -> argparse.ArgumentParser:
    inner = psub.add_parser(name, help=help_, parents=[common])
    inner.set_defaults(func=fn)
    inner.add_argument("--json", action="store_true", help="machine-readable output")
    return inner

  padd("list", cmd_prose_list, "every addressable block, in render order")
  padd("show", cmd_prose_show, "print one block").add_argument("ref")

  inner = _render_flag(padd("edit", _mutating(cmd_prose_edit), "replace one block"))
  inner.add_argument("ref")
  inner.add_argument("--file")
  inner.add_argument("--stdin", action="store_true")

  inner = _render_flag(padd("add-pass", _mutating(cmd_prose_add_pass), "declare a new pass group"))
  inner.add_argument("key")
  inner.add_argument("--heading", help="the markdown heading for the group")
  inner.add_argument("--after", help="insert after this pass instead of at the end")

  _render_flag(padd("drop-pass", _mutating(cmd_prose_drop_pass), "remove an empty pass group")).add_argument("key")

  sp = _render_flag(add("remove", _mutating(cmd_remove), "retire an obsolete item, or purge one outright"))
  sp.add_argument("id")
  mode = sp.add_mutually_exclusive_group(required=True)
  mode.add_argument("--reason", help="retire it, recording why; the id stays claimed")
  mode.add_argument(
    "--purge", action="store_true", help="delete it outright, for something that never should have existed"
  )
  sp.add_argument("--force", action="store_true", help="override the dependents and done guards")

  add("render", cmd_render, "regenerate .slicer/render/")

  sp = add("sync", cmd_sync, "rewrite derived lines in other documents")
  sp.add_argument("--check", action="store_true", help="report drift instead of writing")

  add("verify", cmd_verify, "check the index for consistency (and against git unless git_check is off)")

  sp = add("check", cmd_check, "the CI gate: render, sync and integrity")
  sp.add_argument("--diff", action="store_true", help="show a diff for each stale file")

  add("stats", cmd_stats, "counts by status, size, tree and pass")

  sp = add("log", cmd_log, "recent status changes")
  sp.add_argument("--limit", type=int, default=20)

  add("tui", cmd_tui, "browse and reorder interactively", json_flag=False)
  return p


def _fail(args: argparse.Namespace, exc: SlicerError) -> int:
  """Report a deliberate failure: an envelope for agents, prose for people.

  The human line always goes to stderr, so piping stdout stays safe. With
  `--json`, stdout additionally carries a machine-readable envelope whose
  `code` is the stable part -- the message is free to be reworded.
  """
  if getattr(args, "json", False):
    payload = {
      "error": {
        "code": getattr(exc, "code", "error"),
        "message": str(exc),
        "command": getattr(args, "command", None),
      }
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
  print(f"slicer: {exc}", file=sys.stderr)
  return USAGE


def main(argv: list[str] | None = None) -> int:
  parser = build_parser()
  args = parser.parse_args(argv)
  try:
    return int(args.func(args))
  except SlicerError as exc:
    return _fail(args, exc)
  except OSError as exc:
    # A missing --file, an unreadable path: someone's mistake, not a bug.
    # Anything else still raises, because a traceback is the right report
    # for a defect in slicer itself.
    return _fail(args, StateError(str(exc), code="io"))


if __name__ == "__main__":
  raise SystemExit(main())
