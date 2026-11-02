"""Prose-side commands: `prose`, `template`, `config`, `sections`, `goals` and `catalog`.

They read and edit the roadmap's own text, its render templates, its configuration and its
catalog of requirements, constraints, decisions and assumptions, not the slice queue, so
they sit together. Each handler is attached to its subparser in `cli_parser`; shared helpers
come from `cli_common`, and nothing here imports `cli`. Tests patch `_read_user_file` and
`_via_editor`, so they are called as `cli_common.<name>` and each keeps one lookup site.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from slicer import cli_common, model, ops, prose, store, templates
from slicer.cli_common import (
  OK, _begin_write, _body_from, _emit, _mutating, _render_hint, _state, _template_source,
)
from slicer.config import CONFIG_NAME, Config
from slicer.errors import StateError


def _catalog_floor(state: store.State) -> int:
  """The highest catalog counter among siblings that use this catalog prefix."""
  prefix = state.index.catalog.id_prefix.casefold()
  siblings = [index for _, index in store.sibling_ids(state.root)]
  return max(
    (
      index.catalog.next_id for index in siblings
      if index.catalog.id_prefix.casefold() == prefix
    ),
    default=0,
  )


def _catalog_body(args: argparse.Namespace) -> str:
  """The record body from exactly one of --body, --file, or --stdin."""
  if sum((args.body is not None, args.file is not None, args.stdin)) > 1:
    raise StateError("choose only one of --body, --file, or --stdin", code="usage")
  if args.body is not None:
    return args.body
  if args.file:
    return cli_common._read_user_file(args.file).rstrip("\n")
  if args.stdin:
    return sys.stdin.read().rstrip("\n")
  return ""


def _catalog_lines(records: list[model.CatalogRecord], *, heading: bool = True) -> list[str]:
  headings = dict(model.CATALOG_HEADINGS)
  lines: list[str] = []
  kind = ""
  for record in records:
    if heading and record.kind != kind:
      kind = record.kind
      if lines:
        lines.append("")
      lines.append(headings[kind])
    mark = "  retired" if record.status == model.CATALOG_RETIRED else ""
    lines.append(f"  {record.id}  {record.title}{mark}")
    if record.body.strip():
      lines.extend(f"    {line}" for line in record.body.splitlines())
    if record.status == model.CATALOG_RETIRED:
      successor = f" Successor: {record.successor}." if record.successor else ""
      lines.append(f"    Retired: {record.reason}.{successor}")
  return lines


def cmd_catalog_add(args: argparse.Namespace) -> int:
  state = _state(args)
  record = ops.catalog_add(
    state, args.kind, args.title, _catalog_body(args), id_floor=_catalog_floor(state),
  )
  _emit(args, record.to_dict(), f"added {record.id}  {record.kind}  {record.title}")
  return OK


def cmd_catalog_list(args: argparse.Namespace) -> int:
  state = _state(args)
  if args.kind:
    kinds = (model.canonical_kind(args.kind),)
  else:
    kinds = model.CATALOG_KINDS
  records: list[model.CatalogRecord] = []
  for kind in kinds:
    records.extend(state.index.catalog.of_kind(kind, retired=args.retired))
  text = "\n".join(_catalog_lines(records)) or "no catalog records"
  _emit(args, [record.to_dict() for record in records], text)
  return OK


def _cite_text(item: model.Item) -> str:
  cited = ", ".join(item.cites) if item.cites else "nothing"
  return f"{item.id} cites {cited}"


def cmd_catalog_cite(args: argparse.Namespace) -> int:
  state = _state(args)
  item = state.index.require(args.item)
  before = list(item.cites)
  item = ops.catalog_cite(state, args.item, args.ids)
  if item.cites == before:
    args._no_change = True
    _emit(args, item.to_dict() | {"changed": False}, f"{item.id} unchanged")
    return OK
  _emit(args, item.to_dict() | {"changed": True}, _cite_text(item))
  return OK


def cmd_catalog_uncite(args: argparse.Namespace) -> int:
  state = _state(args)
  item = ops.catalog_uncite(state, args.item, args.ids)
  _emit(args, item.to_dict() | {"changed": True}, _cite_text(item))
  return OK


def cmd_catalog_show(args: argparse.Namespace) -> int:
  state = _state(args)
  record = state.index.catalog.require(args.id)
  cited_by = [item.id for item in state.index.items if args.id in item.cites]
  payload = record.to_dict() | {"cited_by": cited_by}
  lines = _catalog_lines([record])
  if cited_by:
    lines.append(f"  cited by: {', '.join(cited_by)}")
  _emit(args, payload, "\n".join(lines))
  return OK


def cmd_catalog_edit(args: argparse.Namespace) -> int:
  state = _state(args)
  record = state.index.catalog.require(args.id)
  if record.status != model.CATALOG_ACTIVE:
    raise StateError(
      f"{record.id} is retired; a retired record stays as it was", code="state",
    )
  body_given = args.body is not None or args.file is not None or args.stdin
  if args.title is None and not body_given:
    body = cli_common._via_editor(record.body)
    if body is None:
      raise StateError("editor exited non-zero; catalog unchanged", code="editor_aborted")
  elif body_given:
    body = _catalog_body(args)
  else:
    body = None
  updated = ops.catalog_edit(state, args.id, title=args.title, body=body)
  _emit(args, updated.to_dict(), f"updated {updated.id}")
  return OK


def cmd_catalog_retire(args: argparse.Namespace) -> int:
  state = _state(args)
  record = ops.catalog_retire(state, args.id, args.reason, args.successor or "")
  _emit(args, record.to_dict(), f"retired {record.id}")
  return OK


def cmd_catalog_move(args: argparse.Namespace) -> int:
  state = _state(args)
  record = ops.catalog_move(state, args.id, before=args.before, after=args.after)
  _emit(args, record.to_dict(), f"moved {record.id}")
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
  """The effective config, one dotted key of it, or a write of one key.

  A read takes no lock and touches no index. A write (a VALUE, or `--unset`) is not
  marked `mutates`, since `main` would lock a read too; it takes the lock itself and
  goes through `_mutating` so `--render` and `--strict` behave as for other writes.
  """
  writing = args.value is not None or args.unset
  if args.unset and args.value is not None:
    raise StateError("--unset takes no VALUE; pass one or the other", code="usage")
  if args.unset and args.key is None:
    raise StateError("--unset needs a KEY", code="usage")
  if not writing:
    for flag in ("render", "strict", "check"):
      if getattr(args, flag):
        raise StateError(
          f"--{flag} has no effect on a read; pass a VALUE or --unset to write", code="usage",
        )
    return _config_read(args)
  root = store.discover(Path(args.root).resolve() if args.root else None)
  with store.project_lock(root):
    _begin_write(args, root)
    return _config_write(args)


def _config_key(data: dict, key: str) -> tuple[bool, object]:
  """Whether a dotted KEY names a value in `data`, and the value when it does."""
  value: object = data
  for part in key.split("."):
    if not isinstance(value, dict) or part not in value:
      return False, None
    value = value[part]
  return True, value


def _config_read(args: argparse.Namespace) -> int:
  root = store.discover(Path(args.root).resolve() if args.root else None)
  data = Config.load(root / store.DIR_NAME / CONFIG_NAME).to_dict()
  if args.key is None:
    width = max(len(k) for k in data)
    lines = [f"{k.ljust(width)}  {_config_text(v, joined=True)}" for k, v in data.items()]
    _emit(args, data, "\n".join(lines))
    return OK
  present, value = _config_key(data, args.key)
  if present:
    _emit(args, {"key": args.key, "value": value}, _config_text(value, joined=False))
    return OK
  # `to_dict()` leaves an unset key out; a key that can be set reads as unset, default applies.
  schema = ops.config_schema()
  if not _config_key(schema, args.key)[0]:
    raise StateError(
      f"{args.key!r} is not a config key; the top-level keys are {', '.join(schema)}",
      code="usage",
    )
  # Lean drops nulls. An unset key keeps `value: null`, so its shape stays {key, value}.
  _emit(args, {"key": args.key, "value": None}, "(unset)", keep_empty=("value",))
  return OK


def _config_shown(value: object) -> str:
  """A value in a write's one-line report: an empty string or list and an absent key stay visible."""
  if value is None:
    return "(unset)"
  if value == "" or value == []:
    return json.dumps(value)
  return _config_text(value, joined=True)


def _config_set_locked(args: argparse.Namespace) -> int:
  state = _state(args)
  change = ops.config_set(state, args.key, args.value, unset=args.unset)
  if not change.changed:
    args._no_change = True
  if change.changed:
    text = (f"{change.key}: {_config_shown(change.previous)} -> {_config_shown(change.value)}"
            + (" (default)" if args.unset and change.value is not None else ""))
  else:
    text = f"{change.key} is already {_config_shown(change.value)}"
  _emit(args, {"key": change.key, "value": change.value, "previous": change.previous}, text)
  return OK


_config_write = _mutating(_config_set_locked)


def _template_rows(root: Path) -> list[dict[str, object]]:
  rows = []
  for name in templates.TEMPLATE_NAMES:
    text = store.read_template_exact(root, name)
    rows.append({
      "name": name,
      "customized": templates.is_customized(name, text),
      "missing": text is None,
    })
  return rows


def cmd_template_list(args: argparse.Namespace) -> int:
  """Each template and whether the project's copy differs from the built-in one."""
  root = store.discover(Path(args.root).resolve() if args.root else None)
  rows = _template_rows(root)
  width = max(len(str(row["name"])) for row in rows)
  lines = [
    f"{str(row['name']).ljust(width)}  "
    + ("missing" if row["missing"] else "customized" if row["customized"] else "default")
    for row in rows
  ]
  _emit(args, rows, "\n".join(lines))
  return OK


def cmd_template_show(args: argparse.Namespace) -> int:
  """One template, byte for byte: the project's copy, or the built-in one with --default."""
  name = templates.require(args.name)
  if args.default:
    text = templates.default(name)
  else:
    root = store.discover(Path(args.root).resolve() if args.root else None)
    text = store.require_template_text(root, name)
  if args.json:
    _emit(args, {"name": name, "text": text}, "")
  else:
    # Not `_emit`: it ends the text with a newline, and a template is whole as it is.
    sys.stdout.write(text)
  return OK


def _template_write(args: argparse.Namespace, change: ops.TemplateChange) -> int:
  if not change.changed:
    args._no_change = True
    text = f"{change.name} is already " + (
      "that text" if change.customized else "the default")
  else:
    verb = "reset" if args.template_command == "reset" else "replaced"
    hint = _render_hint(args)
    text = f"{verb} {change.name}" + (f"; {hint}" if hint else "")
  _emit(args, {"name": change.name, "changed": change.changed,
               "customized": change.customized}, text)
  return OK


def cmd_template_set(args: argparse.Namespace) -> int:
  name = templates.require(args.name)
  text = _template_source(args)
  return _template_write(args, ops.template_set(_state(args), name, text))


def cmd_template_reset(args: argparse.Namespace) -> int:
  name = templates.require(args.name)
  return _template_write(args, ops.template_reset(_state(args), name))


def cmd_goals(args: argparse.Namespace) -> int:
  """Project direction in one read: prose, plus goal and non-goal records."""
  state = _state(args)
  retired = bool(args.retired)
  grouped = {
    "goal": state.index.catalog.of_kind("goal", retired=retired),
    "non_goal": state.index.catalog.of_kind("non_goal", retired=retired),
  }
  blocks = []
  for title, text, kind in (
    ("Goals", state.index.goals, "goal"),
    ("Non-goals", state.index.non_goals, "non_goal"),
  ):
    prose_text = text.rstrip("\n") if text.strip() else ""
    records_text = "\n".join(_catalog_lines(grouped[kind], heading=False))
    parts = [part for part in (prose_text, records_text) if part]
    body = "\n\n".join(parts) if parts else "  (none set)"
    blocks.append(f"{title}:\n{body}")
  payload = {
    "goals": state.index.goals,
    "non_goals": state.index.non_goals,
    "records": {
      kind: [record.to_dict() for record in records] for kind, records in grouped.items()
    },
  }
  _emit(args, payload, "\n\n".join(blocks))
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
  item_count = sum(1 for item in state.index.items if item.pass_key == info.key)
  text = (
    f"created pass {info.key} for {item_count} existing item(s)"
    if item_count
    else f"created pass {info.key} (no items yet); file items with `slicer add --pass {info.key}`"
  )
  _emit(
    args,
    info.to_dict(),
    text,
  )
  return OK


def cmd_prose_drop_pass(args: argparse.Namespace) -> int:
  state = _state(args)
  ops.drop_pass(state, args.key)
  _emit(args, {"key": args.key, "dropped": True}, f"dropped pass {args.key}")
  return OK
