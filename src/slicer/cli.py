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
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from slicer import check as check_mod
from slicer import (
  ai,
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
from slicer import __version__
from slicer.config import CONFIG_NAME, Config
from slicer.errors import SlicerError, StateError, is_internal

OK, DRIFT, USAGE, INTERNAL = 0, 1, 2, 3


def _emit(args: argparse.Namespace, payload: object, text: str) -> None:
  if getattr(args, "json", False):
    print(json.dumps(payload, ensure_ascii=False, indent=2))
  elif text:
    print(text)


def _render_after_mutation(args: argparse.Namespace) -> int:
  """Re-read and render the just-saved state; return how many files changed."""
  state = _state(args)
  expected = render.plan(state)
  return len(render.write(expected, state.render_dir, render.compare(expected, state.render_dir)))


def _mutating(fn):
  """Wrap a mutating handler so `--render` renders after it, from one place.

  The handler has already saved to disk and printed its own result (an object,
  or a JSON array for a batch of ids). If the follow-up render fails, the change
  is still committed, so we must not let the failure reach `main` — that would
  print a *second* document after the handler's result. Instead stdout stays the
  handler's single result unchanged, the render failure goes to stderr, and the
  exit is DRIFT (1): state and `render/` are now out of step, which is what
  `slicer render` resolves. The exit is DRIFT whatever the cause, because the
  mutation the user asked for did succeed; only the projection is stale.
  """
  def wrapped(args: argparse.Namespace) -> int:
    if not (getattr(args, "render", False) and not getattr(args, "dry_run", False)):
      return fn(args)
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
    return code

  # main() locks the project around a mutating command; the flag says which
  # handlers those are, so the render re-read above is inside the same lock.
  wrapped.mutates = True
  return wrapped


def _render_flag(sp: argparse.ArgumentParser) -> argparse.ArgumentParser:
  sp.add_argument("--render", action="store_true", help="render .slicer/render/ after the change")
  return sp


def _render_hint(args: argparse.Namespace) -> str:
  """The `run slicer render` reminder — empty when --render already rendered."""
  return "" if getattr(args, "render", False) else "run `slicer render`"


def _state(args: argparse.Namespace) -> store.State:
  return store.load(Path(args.root) if args.root else None)


def cmd_ai_instructions(args: argparse.Namespace) -> int:
  _emit(args, {"instructions": ai.INSTRUCTIONS}, ai.INSTRUCTIONS.rstrip("\n"))
  return OK


GITATTRIBUTES = """\
# slicer manages this file. History is append-only, so union-merge combines the
# lines both sides added instead of conflicting when branches land in parallel.
# Generated render/ is a projection of index.json: after a merge, resolve
# index.json and re-run `slicer render` rather than merging render/ by hand.
log.jsonl merge=union
"""


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
  jsonio.write_text(base / store.GITATTRIBUTES_NAME, GITATTRIBUTES)
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


def _nonnegative_int(value: str) -> int:
  try:
    number = int(value)
  except ValueError:
    raise argparse.ArgumentTypeError("must be a nonnegative integer") from None
  if number < 0:
    raise argparse.ArgumentTypeError("must be a nonnegative integer")
  return number


def cmd_next(args: argparse.Namespace) -> int:
  state = _state(args)
  result = ops.next_item(state, args.n)
  if result.item is None:
    payload = {"item": None, "blocked": [{"id": i, "waiting_on": b} for i, b in result.blocked]}
    text = "nothing unmarked" if not result.blocked else "\n".join(
      f"blocked {i} waits on {', '.join(b)}" for i, b in result.blocked
    )
    if args.n:
      text = f"no eligible item at offset {args.n}" + (
        f"\n{text}" if result.blocked else ""
      )
    _emit(args, payload, text)
    return USAGE
  item = result.item
  if args.start:
    with store.project_lock(state.root):
      item = ops.start(state, item.id)
  eff = graph.effective_scores(state.index)[item.id]
  path = state.find_slice_file(item.id)
  inherited = "^" if eff > item.score else ""
  payload = item.to_dict() | {"path": str(path) if path else None, "effective_score": eff}
  lines = [
    f"{item.id}  {item.display_title()}",
    f"     score {eff}{inherited} · {state.config.status_label(item.status)}",
  ]
  if path:
    lines.append(f"     {path}")
  if args.show:
    # Fold the follow-up `show ID` into this one call: an agent picking up work
    # reads the slice in the same turn it learns the id, saving a round trip.
    sl = state.slices.get(item.id)
    if sl is None:
      lines.append(f"     (no slice yet; run `slicer promote {item.id}`)")
    else:
      payload = payload | {"slice": sl.to_dict()}
      lines.append(render.render_slice(
        sl, state.config, state.template("slice.md"), item.notes).decode("utf-8"))
  _emit(args, payload, "\n".join(lines))
  return OK


def _item_rows(state: store.State, items: list[model.Item]) -> list[str]:
  """The shared queue-listing row format used by `list` and `find`.

  `^` marks an effective score lifted above the item's own by a dependent, so a
  blocker of a critical item reads at that item's priority.
  """
  cfg = state.config
  eff = graph.effective_scores(state.index)
  return [
    f"{n:>3}  {i.id:<5} {cfg.status_label(i.status):<7} {i.size:<2} "
    f"{eff[i.id]:>2}{'^' if eff[i.id] > i.score else ' '} {i.quadrant:<9} {i.display_title()}"
    for n, i in enumerate(items, 1)
  ]


def _list_in_next_order(state: store.State, items: list[model.Item]) -> list[model.Item]:
  """The sequence `next` walks, then every other visible row by effective score.

  A started or open item with an unfinished dependency is not startable, so it
  joins the trailing group. Ties keep stored order; sorting is stable.
  """
  cfg = state.config
  eff = graph.effective_scores(state.index)
  started = cfg.started_status

  def tier(item: model.Item) -> int:
    if graph.blocked_by(state.index, item, cfg.done_status):
      return 2
    if started and item.status == started:
      return 0
    if item.status == cfg.open_status:
      return 1
    return 2

  return sorted(items, key=lambda item: (tier(item), -eff[item.id]))


def cmd_list(args: argparse.Namespace) -> int:
  state = _state(args)
  if args.all and args.status:
    raise StateError(
      "--all and --status cannot be combined; --status already chooses which statuses to show",
      code="usage",
    )
  items = state.index.items
  if args.status:
    items = [i for i in items if i.status in args.status]
  elif not args.all:
    hidden = {state.config.done_status}
    if state.config.retired_status:
      hidden.add(state.config.retired_status)
    items = [i for i in items if i.status not in hidden]
  if args.tree:
    items = [i for i in items if set(args.tree) & set(i.trees)]
  if args.pass_key:
    items = [i for i in items if i.pass_key == args.pass_key]
  if getattr(args, "sort", None) == "score":
    # A read-only view: sort a copy by effective score, never the stored order.
    # Ties keep their manual position because Python's sort is stable.
    eff = graph.effective_scores(state.index)
    items = sorted(items, key=lambda i: eff[i.id], reverse=True)
  else:
    items = _list_in_next_order(state, items)
  _emit(args, [i.to_dict() for i in items], "\n".join(_item_rows(state, items)) or "no matching items")
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
    section = sl.section(args.section)
    if section is None:
      raise StateError(
        f"{args.id} has no section {args.section!r}", code="no_such_section"
      )
    _emit(args, {"id": args.id, "section": section.heading, "body": section.body}, section.body)
    return OK
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
    depends_on=args.depends_on, short_title=args.short_title,
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
  flags = [] if args.no_flags else args.flag
  items = ops.set_fields_many(
    state, item_ids, title=args.title, short_title=args.short_title, status=args.status,
    size=args.size, trees=args.tree, findings=args.findings, depends_on=args.depends_on,
    pass_key=args.pass_key, flags=flags, group=args.group,
    importance=args.importance, urgency=args.urgency,
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
cmd_start = _status_cmd("started_status")


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


def _census(state: store.State) -> dict:
  """The item census `stats` and `status` share, so they cannot disagree."""
  cfg = state.config
  items = state.index.items
  total = len(items)
  done = sum(1 for it in items if it.status == cfg.done_status)
  by_tree_status = {
    tree: {cfg.status_label(s): n for s, n in cols.items()}
    for tree, cols in model.cross_counts(items, "trees", "status").items()
  }
  return {
    "total": total,
    "completion": {"done": done, "total": total, "percent": round(done * 100 / total) if total else 0},
    "by_status": {cfg.status_label(k): v for k, v in model.counts(items, "status").items()},
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
      lines.append(f"{name:<10} " + " · ".join(f"{k} {v}" for k, v in group.items()))
  if payload["by_tree_status"]:
    lines.append("progress by tree")
    for tree, cols in payload["by_tree_status"].items():
      lines.append(f"  {tree:<10} " + " · ".join(f"{k} {v}" for k, v in cols.items()))
  text = "\n".join(lines)
  _emit(args, payload, text)
  return OK


def cmd_status(args: argparse.Namespace) -> int:
  """The one-call front door: what is next, how far along, and what is blocked."""
  state = _state(args)
  result = ops.next_item(state, 0)
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
  progress = " · ".join(f"{k} {v}" for k, v in census["by_status"].items())
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
  # Newest first by timestamp so union-merged history (which can interleave the
  # lines two branches appended) still reads in order. Reverse the append order
  # first so that, among entries sharing a timestamp, the later-appended one is
  # shown first -- a stable sort then keeps that tie-break.
  entries = sorted(reversed(history), key=lambda e: e.when, reverse=True)[: args.limit]
  filters = (args.item or []) + (args.action or [])
  empty = ("no history for " + " ".join(filters)) if filters else "no history yet"
  text = "\n".join(
    f"{e.when}  {e.item:<5} {e.action:<8} {e.frm or '-'} -> {e.to or '-'}  {e.note}".rstrip()
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
  p = _ArgumentParser(prog="slicer", description=__doc__.splitlines()[0])
  p.add_argument(
    "--root", default=None, help="project root (default: discovered from the working directory)"
  )
  p.add_argument("--version", action="version", version=f"slicer {__version__}")
  sub = p.add_subparsers(dest="command", required=True)

  def add(name: str, fn, help_: str, *, json_flag: bool = True,
          aliases: tuple[str, ...] = ()) -> argparse.ArgumentParser:
    sp = sub.add_parser(name, help=help_, parents=[common], aliases=list(aliases))
    sp.set_defaults(func=fn)
    if json_flag:
      sp.add_argument("--json", action="store_true", help="machine-readable output")
    return sp

  sp = sub.add_parser("ai", help="onboarding instructions for coding agents", parents=[common])
  aisub = sp.add_subparsers(dest="ai_command", required=True)
  inner = aisub.add_parser(
    "instructions", help="print the agent quick start (no project needed)",
    description=(
      "Print generic agent instructions without reading or changing project state. "
      "--root is accepted but unused."
    ),
    parents=[common],
  )
  inner.set_defaults(func=cmd_ai_instructions)
  inner.add_argument("--json", action="store_true", help="machine-readable output")

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

  sp = add("next", cmd_next, "the highest-priority startable item")
  sp.add_argument("-n", type=_nonnegative_int, default=0, metavar="N",
                  help="skip N currently eligible items (default 0); return one item")
  sp.add_argument("--start", action="store_true", help="mark the returned item started")
  sp.add_argument("--show", action="store_true",
                  help="also include the item's full slice, as `show` returns it")

  sp = add("list", cmd_list, "list items in next's order, omitting done and retired unless asked")
  sp.add_argument("--all", action="store_true",
                  help="include done and retired items (default: omit them)")
  sp.add_argument("--status", action="append",
                  help="filter by status (repeatable); replaces the default of omitting done and retired")
  sp.add_argument("--tree", action="append", help="filter by tree (repeatable)")
  sp.add_argument("--pass", dest="pass_key", help="filter by pass")
  sp.add_argument("--sort", choices=["score"], help="order by priority score, highest first")

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
  sp.add_argument("--section", help="print only this section's body")

  sp = _render_flag(add("add", _mutating(cmd_add), "append a roadmap item"))
  sp.add_argument("title")
  sp.add_argument("--depends-on", action="append", help="dependency id (repeatable)")
  sp.add_argument("--short-title", help="short title for the roadmap row")
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
  sp.add_argument("--boundary", help="full scope-boundary paragraph; overrides source/default; empty clears")

  sp = _render_flag(add("move", _mutating(cmd_move), "reorder the queue"))
  sp.add_argument("id")
  sp.add_argument("--before")
  sp.add_argument("--after")
  sp.add_argument("--to", type=int)

  sp = _render_flag(add("sort", _mutating(cmd_sort), "reorder the whole queue by priority score"))
  sp.add_argument("--by", choices=["score"], default="score", help="sort key")

  sp = _render_flag(add("set", _mutating(cmd_set), "change an item's fields"))
  sp.add_argument("id", nargs="+", help="item ids, or - alone to read whitespace-separated ids from stdin")
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

  sp = _render_flag(add("edit", _mutating(cmd_edit), "edit a slice section or scope boundary"))
  sp.add_argument("id")
  sp.add_argument("--section", help="section to edit; cannot combine with --boundary")
  sp.add_argument("--boundary", action="store_true", help="replace the full scope-boundary paragraph")
  sp.add_argument("--append", action="store_true", help="append with a blank line; requires --text, --file, or --stdin; empty input leaves the body unchanged")
  sp.add_argument("--text", help="inline body (exact in replacement mode); empty text clears unless appending; cannot combine with --file/--stdin")
  sp.add_argument("--file")
  sp.add_argument("--stdin", action="store_true")

  sp = _render_flag(add("note", _mutating(cmd_note), "append a dated note to an item"))
  sp.add_argument("id")
  sp.add_argument("--text", help="inline note; cannot combine with --file/--stdin")
  sp.add_argument("--file")
  sp.add_argument("--stdin", action="store_true")

  sp = _render_flag(add("done", _mutating(_status_cmd("done_status")), "mark an item finished"))
  sp.add_argument("id", nargs="+", help="item ids, or - alone to read whitespace-separated ids from stdin")
  sp.add_argument("--note", help="one line recorded in history (see `slicer note` for a durable note on the item)")

  sp = _render_flag(add("start", _mutating(cmd_start), "mark an item in progress"))
  sp.add_argument("id", nargs="+", help="item ids, or - alone to read whitespace-separated ids from stdin")
  sp.add_argument("--note", help="one line recorded in history (see `slicer note` for a durable note on the item)")

  sp = _render_flag(add("park", _mutating(cmd_park), "set an item aside"))
  sp.add_argument("id", nargs="+", help="item ids, or - alone to read whitespace-separated ids from stdin")
  sp.add_argument("--note", help="one line recorded in history (see `slicer note` for a durable note on the item)")

  sp = _render_flag(add("unpark", _mutating(_status_cmd("open_status")), "return a parked item to the queue"))
  sp.add_argument("id", nargs="+", help="item ids, or - alone to read whitespace-separated ids from stdin")
  sp.add_argument("--note", help="one line recorded in history (see `slicer note` for a durable note on the item)")

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
  inner.add_argument("--text", help="inline body, preserved exactly; empty text clears it; cannot combine with --file/--stdin")
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

  sp = add("log", cmd_log, "recent status changes")
  sp.add_argument("--limit", type=int, default=20)
  sp.add_argument("--item", action="append", help="filter to these item ids (repeatable)")
  sp.add_argument("--action", action="append", help="filter to these actions, e.g. set, edit (repeatable)")

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


def main(argv: list[str] | None = None) -> int:
  parser = _cached_parser()
  argv = list(sys.argv[1:] if argv is None else argv)
  args = argparse.Namespace()
  try:
    parser.parse_args(argv, namespace=args)
    # A mutating command holds an advisory lock for its whole run, so two
    # writers serialise instead of racing (a duplicated id, a half-applied
    # outline). Read-only commands need no lock.
    if getattr(args.func, "mutates", False):
      root = Path(args.root) if args.root else None
      with store.project_lock(root):
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
