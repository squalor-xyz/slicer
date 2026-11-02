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
import shlex
import sys
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

from slicer import (
  ai,
  cli_common,
  graph,
  model,
  note_pack,
  ops,
  queries,
  render,
  snapshot,
  store,
  vcs,
)
from slicer import __url__, __version__
from slicer.cli_ai import (
  cmd_ai_guard, cmd_ai_hooks, cmd_ai_instructions, cmd_ai_prompt, cmd_ai_relay, cmd_ai_relay_brief,
  cmd_ai_relay_orchestrator, cmd_ai_reset, cmd_ai_set, cmd_ai_show, cmd_ai_skill,
  cmd_recommended_workflow,
)
from slicer.cli_common import (
  INTERNAL, OK, USAGE, _ArgumentParser, _DESCRIPTION, _ISSUES_URL, _ParserError, _begin_write,
  _census, _check_flag, _dump_json, _emit, _exit_for, _item_lookup, _json_flags, _mutating,
  _nonnegative_int, _positive_int, _recursive_flag, _render_flag, _rest_flag, _state,
  _status_tally, _strict_flag, _write_out,
)
from slicer.cli_prose import (
  cmd_catalog_add, cmd_catalog_cite, cmd_catalog_edit, cmd_catalog_list, cmd_catalog_move,
  cmd_catalog_retire, cmd_catalog_show, cmd_catalog_uncite, cmd_config, cmd_goals,
  cmd_prose_add_pass, cmd_prose_drop_pass, cmd_prose_edit, cmd_prose_list, cmd_prose_show,
  cmd_sections, cmd_template_list, cmd_template_reset, cmd_template_set, cmd_template_show,
)
from slicer.cli_relay import (
  cmd_ai_relay_roles, cmd_relay_roles_add, cmd_relay_roles_init, cmd_relay_roles_move,
  cmd_relay_roles_recommend, cmd_relay_roles_remove, cmd_relay_roles_rule_add,
  cmd_relay_roles_rule_edit, cmd_relay_roles_rule_remove, cmd_relay_roles_set,
  cmd_relay_roles_show, cmd_relay_run_answer, cmd_relay_run_ask, cmd_relay_run_begin,
  cmd_relay_run_end, cmd_relay_run_list, cmd_relay_run_stats, cmd_relay_run_step,
)
from slicer.cli_report import (
  cmd_check, cmd_log, cmd_refs, cmd_render, cmd_snapshot, cmd_stats, cmd_sync, cmd_tui, cmd_verify,
)
from slicer.cli_setup import cmd_init, cmd_merge_index, cmd_merge_relay_roles, cmd_setup_git
from slicer.cli_track import (
  _status_cmd, cmd_add, cmd_decide, cmd_done, cmd_edit, cmd_feedback, cmd_feedback_report,
  cmd_handoff, cmd_id_prefix, cmd_import, cmd_issues_pull, cmd_land, cmd_migrate, cmd_move,
  cmd_next_id, cmd_note, cmd_note_retract, cmd_note_verify, cmd_park, cmd_promote, cmd_ready,
  cmd_reject, cmd_release, cmd_remove, cmd_set, cmd_sort, cmd_start,
)
from slicer.config import CONFIG_NAME, Config
from slicer.errors import SlicerError, StateError


def _unspecified_payload(result: queries.NextResult | queries.BatchResult | queries.ParallelPlan) -> list[dict[str, object]]:
  return [{"id": item_id, "missing": missing} for item_id, missing in result.unspecified]


def _unspecified_lines(result: queries.NextResult | queries.BatchResult | queries.ParallelPlan) -> list[str]:
  lines: list[str] = []
  for item_id, missing in result.unspecified:
    names = " and ".join(missing)
    edits = " and ".join(
      f"`slicer edit {item_id} --section {shlex.quote(name)}`" for name in missing
    )
    verb = "is" if len(missing) == 1 else "are"
    lines.append(f"skipped {item_id}: {names} {verb} empty. Fill them with {edits}.")
  return lines


def _elsewhere_payload(result: queries.NextResult | queries.BatchResult | queries.ParallelPlan) -> list[dict[str, str]]:
  """Skipped items, one row per sibling, in list's `{worktree, owner, status}` shape plus the id."""
  return [{"id": item_id} | entry for item_id, entries in result.elsewhere for entry in entries]


def _elsewhere_lines(result: queries.NextResult | queries.BatchResult | queries.ParallelPlan) -> list[str]:
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


def _note_skips(
  payload: dict, lines: list[str],
  result: queries.NextResult | queries.BatchResult | queries.ParallelPlan,
) -> None:
  """Expose why candidates were skipped, each category only when non-empty."""
  if result.held:
    payload["held"] = result.held
    lines.append("Held")
    lines.extend(
      f"  {entry['id']} {entry['title']} [{', '.join(entry['flags'])}]"
      for entry in result.held
    )
  if result.capped:
    payload["capped"] = result.capped
    lines.extend(
      f"skipped {entry['id']} (attempts {entry['attempts']} >= max_attempts {entry['max_attempts']})"
      for entry in result.capped
    )
  unknown_files = getattr(result, "unknown_files", [])
  if unknown_files:
    payload["unknown_files"] = unknown_files
    lines.extend(
      f"skipped {item_id} (its files section names no path)"
      for item_id in unknown_files
    )
  unspecified = _unspecified_payload(result)
  if unspecified:
    payload["unspecified"] = unspecified
    lines.extend(_unspecified_lines(result))
  elsewhere = _elsewhere_payload(result)
  if elsewhere:
    payload["in_work_elsewhere"] = elsewhere
    lines.extend(_elsewhere_lines(result))
  if result.claimed_elsewhere:
    payload["claimed_elsewhere"] = result.claimed_elsewhere
    lines.extend(
      f"skipped {entry['id']} (claimed by {entry['owner']})" for entry in result.claimed_elsewhere
    )


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
  payload = item.to_dict() | {
    "path": str(path) if path else None, "effective_score": eff,
    "ready": queries.ready_state(state, item),
  }
  lines = [
    f"{item.id}  {item.display_title()}",
    f"     score {eff}{inherited} · {state.config.status_label(item.status)}",
  ]
  _append_slice_hint(lines, item, path, show_path)
  return payload, lines


def _note_projection(
  args: argparse.Namespace, item: model.Item, sl: model.Slice | None,
) -> tuple[model.Item, model.Slice | None, dict[str, object] | None]:
  """Select notes on detached copies, keeping the loaded state untouched."""
  from copy import deepcopy
  item, sl = deepcopy(item), deepcopy(sl)
  kinds = getattr(args, "notes_kind", None)
  manifest = None
  if getattr(args, "_pack_time", None) is not None:
    item.note_records, legacy, manifest = note_pack.select(
      item.note_records, sl.notes if sl is not None else [], now=args._pack_time,
      kinds=kinds, min_trust=args.min_trust or "unverified", budget=args.notes_budget,
    )
    if sl is not None:
      sl.notes = legacy
  elif kinds is not None:
    item.note_records = [n for n in item.note_records if n.kind in kinds]
    if sl is not None and "" not in kinds:
      sl.notes = []
  return item, sl, manifest


def _prepare_note_pack(args: argparse.Namespace) -> None:
  """Validate pickup options and capture one time before any claims."""
  if args.notes_budget is not None and args.notes_budget < 0:
    raise StateError("--notes-budget must be a nonnegative integer", code="usage")
  packing = args.min_trust is not None or args.notes_budget is not None
  args._pack_time = datetime.now(timezone.utc) if packing else None


def _pack_text(item: model.Item, sl: model.Slice | None, manifest: dict[str, object] | None,
               *, notes: bool = True) -> list[str]:
  """Expose the requested selection and manifest in human output too."""
  if manifest is None:
    return []
  lines = []
  if notes:
    texts = {n.id: n.display() for n in item.note_records}
    # Selected legacy notes retain their relative order, even when earlier ones were omitted.
    legacy_ids = [i for i in manifest["selected_ids"] if i.startswith("slice-legacy-")]
    texts.update(zip(legacy_ids, sl.notes if sl is not None else []))
    lines.extend(texts[i] for i in manifest["selected_ids"])
  lines.append("Note pack  " + json.dumps(manifest, ensure_ascii=False))
  return lines


def _note_fields(item: model.Item, manifest: dict[str, object] | None = None) -> dict[str, object]:
  fields = {"notes": list(item.notes),
            "note_records": [n.to_dict() for n in item.note_records]}
  if manifest is not None:
    fields["note_pack"] = manifest
  return fields


def _ready_entry(
  args: argparse.Namespace, state: store.State, item: model.Item,
) -> tuple[dict[str, object], list[str]]:
  """One `{item, slice?}` pickup. `blocked` stays with the caller."""
  eff = graph.effective_scores(state.index)[item.id]
  path = state.find_slice_file(item.id)
  item, sl, manifest = _note_projection(args, item, state.slices.get(item.id))
  payload: dict[str, object] = {
    "item": {
      "id": item.id,
      "title": item.title,
      "status": item.status,
      "ready": queries.ready_state(state, item),
      "depends_on": list(item.depends_on),
      "attempts": item.attempts,
      **_note_fields(item, manifest),
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
        "notes": list(sl.notes),
        "sections": [s.to_dict() for s in _sections_named(sl, args.section)],
      }
    else:
      payload["slice"] = sl.to_dict()
    boundary = " ".join(sl.boundary.split()) or "(none)"
    headings = ", ".join(section.heading for section in sl.sections) or "(none)"
    lines.append(f"     boundary  {boundary}")
    lines.append(f"     sections  {headings}")
  lines.extend(_pack_text(item, sl, manifest))
  return payload, lines


def _emit_ready(
  args: argparse.Namespace, state: store.State, item: model.Item, result: queries.NextResult,
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
  """Validate sections and requested note packs before `--start` claims anything."""
  for item in items:
    sl = state.slices.get(item.id)
    if args._pack_time is not None:
      _note_projection(args, item, sl)
    if args.ready and args.section and sl is not None:
      _sections_named(sl, args.section)


def cmd_next(args: argparse.Namespace) -> int:
  _prepare_note_pack(args)
  if args._pack_time is not None and not args.ready:
    raise StateError("--min-trust and --notes-budget on next require --ready", code="usage")
  if args.ready and args.show:
    raise StateError(
      "--ready and --show are separate output profiles; pass only one",
      code="usage",
    )
  if args.notes_kind is not None and not args.ready:
    raise StateError("--notes-kind on next requires --ready", code="usage")
  if args.section and not args.ready:
    raise StateError("--section on next requires --ready", code="usage")
  if args.owner is not None and not args.start:
    raise StateError("--owner on next requires --start", code="usage")
  if args.batch is not None and (args.show or args.n is not None):
    raise StateError("--batch cannot be combined with --show or -n", code="usage")
  if args.batch is not None and args.status is not None:
    raise StateError("--batch cannot be combined with --status", code="usage")
  if args.disjoint and args.batch is None:
    raise StateError("--disjoint on next requires --batch", code="usage")
  if args.start_to is not None and not (args.start and args.status is not None):
    raise StateError("--start-to on next requires --start and --status", code="usage")
  if args.render and not args.start:
    raise StateError("--render on next requires --start", code="usage")
  if args.strict and not args.render:
    raise StateError("--strict has no effect without --render", code="usage")
  if args.start and (args.render or args.max_attempts is not None or args.status is not None):
    root = store.discover(Path(args.root) if args.root else None)
    with store.project_lock(root):
      cli_common._heal_render(args)
      return _next_rendered(args) if args.render else _select_next(args, locked=True)
  return _select_next(args, locked=False)


def _select_next(args: argparse.Namespace, *, locked: bool) -> int:
  """Load and select under the writer lock for capped and queue pickups.

  `locked` says the caller already holds the writer lock, so the state read
  here is the state the pickup writes against.
  """
  state = _state(args)
  cfg = state.config
  review = bool(cfg.review_status) and args.status == cfg.review_status
  queue = args.status if args.status is not None and not review else None
  if queue is not None:
    queries.custom_queue(cfg, queue)
  if args.start_to is not None and queue is None:
    raise StateError("--start-to on next requires a custom --status, not the review status",
                     code="usage")
  elsewhere = store.in_work_elsewhere(state.root, state.index)
  if args.batch is not None:
    return _cmd_next_batch(args, state, elsewhere, locked=locked)
  offset = 0 if args.n is None else args.n
  result = queries.next_item(
    state, offset, elsewhere, review=review, tree=args.tree, size=args.size,
    max_attempts=args.max_attempts, flags=args.flag, no_flags=args.no_flag,
    queue=queue, owner=args.owner, start_to=args.start_to, prefer_ready=bool(args.ready),
  )
  if result.item is None:
    payload = {"item": None, "blocked": _blocked_payload(result.blocked)}
    parts = [f"blocked {i} waits on {', '.join(b)}" for i, b in result.blocked]
    _note_skips(payload, parts, result)
    empty = f"nothing in {queue}" if queue else "nothing in review" if review else "nothing unmarked"
    text = "\n".join(parts) if parts else empty
    if offset:
      text = f"no eligible item at offset {offset}" + (f"\n{text}" if parts else "")
    _emit(args, payload, text)
    return USAGE
  item = result.item
  _reject_unknown_sections(args, state, [item])
  if args.start:
    before = _claim_marks([item])
    with (nullcontext() if locked else store.project_lock(state.root)):
      if not locked:
        cli_common._heal_render(args)
      if queue is not None:
        item = ops.claim_in_queue(state, item.id, queue=queue, owner=args.owner, to=args.start_to)
      else:
        item = ops.start(state, item.id, owner=args.owner)
    args._no_change = before == _claim_marks([item])
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
        sl, state.config, state.template("slice.md"), item.rendered_notes))
  _emit(args, payload, "\n".join(lines))
  return OK


# `next --start --render`: cmd_next holds the writer lock itself (next is not
# marked `mutates`, so plain `next` stays lock-free), and the wrapper renders
# inside it, skipping the render when the pickup changed nothing.
_next_rendered = _mutating(lambda args: _select_next(args, locked=True))


def _claim_marks(items: list[model.Item]) -> list[tuple[str, str]]:
  """Status and claim owner per item, to tell whether a pickup changed anything."""
  return [(item.status, item.claim_owner) for item in items]


def _cmd_next_batch(
  args: argparse.Namespace, state: store.State,
  elsewhere: dict[str, list[dict[str, str]]], *, locked: bool,
) -> int:
  """Several items from one tree and size. One lock covers the whole start."""
  result = queries.next_batch(
    state, args.batch, elsewhere, tree=args.tree, size=args.size,
    max_attempts=args.max_attempts, flags=args.flag, no_flags=args.no_flag,
    disjoint=args.disjoint, prefer_ready=bool(args.ready),
  )
  if args.disjoint:
    _warn_files_section(state)
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
    # Empty batches never start. Capped and rendered selection already hold
    # the lock; otherwise take it here. One staged start_many ensures that a
    # failure keeps every id unclaimed: no status, move, or log line.
    before = _claim_marks(items)
    with (nullcontext() if locked else store.project_lock(state.root)):
      if not locked:
        cli_common._heal_render(args)
      with state.staged():
        started = ops.start_many(state, [item.id for item in items], owner=args.owner)
    args._no_change = before == _claim_marks(started)
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


def cmd_plan_parallel(args: argparse.Namespace) -> int:
  """The waves of work that can run side by side. Read-only, like `next`."""
  state = _state(args)
  elsewhere = store.in_work_elsewhere(state.root, state.index)
  plan = queries.plan_parallel(
    state, args.width, elsewhere, tree=args.tree, size=args.size,
    max_attempts=args.max_attempts, flags=args.flag, no_flags=args.no_flag,
  )
  _warn_files_section(state)
  payload = plan.to_dict()
  short = {item.id: item.display_title() for item in state.index.items}

  def block(heading: str, rows: list[dict[str, str]]) -> list[str]:
    id_w = max(len(row["id"]) for row in rows)
    titles = [short.get(row["id"], row["title"]) for row in rows]
    title_w = max(len(title) for title in titles)
    return [heading, *(
      f"  {row['id']:<{id_w}}  {title:<{title_w}}  {row.get('reason', '')}".rstrip()
      for row, title in zip(rows, titles)
    )]

  lines: list[str] = []
  if plan.in_flight:
    lines.extend(block("in flight", plan.in_flight))
  for at, rows in enumerate(plan.waves, start=1):
    lines.extend(block(f"wave {at}", rows))
  if plan.unplaced:
    lines.extend(block("unplaced", plan.unplaced))
  blocked = _blocked_payload(plan.blocked)
  if blocked:
    _append_blocked(lines, blocked)
  _note_skips(payload, lines, plan)
  _emit(args, payload, "\n".join(lines) or "nothing to plan",
        keep_empty=("waves", "in_flight", "unplaced", "blocked"))
  return OK if plan.waves else USAGE


def _claim_width(
  cfg: Config, items: list[model.Item],
  elsewhere: dict[str, list[dict[str, str]]] | None = None,
) -> int:
  width = len("CLAIM")
  for item in items:
    width = max(width, len(store.claim_cell(cfg, item, (elsewhere or {}).get(item.id))))
  return width


def _status_width(
  cfg: Config, items: list[model.Item],
  elsewhere: dict[str, list[dict[str, str]]] | None = None,
) -> int:
  """At least the historical 7, wider when a label needs it (`reviewing`, or a
  project's own status), so a long label never pushes the rest of its row."""
  return max([7, *(len(cfg.status_label(store.displayed_status(
    cfg, item, (elsewhere or {}).get(item.id, []),
  ))) for item in items)])


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
  blocker of a critical item reads at that item's priority. `!` after the row
  number marks an item that waits on an unfinished dependency, as the TUI queue
  does; it takes the space the row leaves there, so the columns do not move. The
  claim column names the owner, or `*` when the item is in progress and unclaimed.
  The pass column appears only when the roadmap uses passes.
  """
  cfg = state.config
  eff = graph.effective_scores(state.index)
  elsewhere = elsewhere or {}
  if claim_w is None:
    claim_w = _claim_width(cfg, items, elsewhere)
  if pass_w is None:
    pass_w = _pass_width(state.index, items)
  if status_w is None:
    status_w = _status_width(cfg, items, elsewhere)
  rows = []
  for n, item in enumerate(items, 1):
    score = str(eff[item.id]) + ("^" if eff[item.id] > item.score else "")
    effort = "-" if item.effort is None else str(item.effort)
    pass_cell = "" if pass_w is None else f"{item.pass_key or '-':<{pass_w}} "
    label = cfg.status_label(store.displayed_status(cfg, item, elsewhere.get(item.id, [])))
    mark = "!" if graph.pending(state.index, item, cfg) else " "
    rows.append(
      f"{n:>3}{mark} {item.id:<5} {label:<{status_w}} "
      f"{store.claim_cell(cfg, item, elsewhere.get(item.id)):<{claim_w}} {pass_cell}{item.size:<4} "
      f"{effort:<6} {score:<5} {item.quadrant:<9} {item.display_title()}"
    )
  return rows


def _list_in_priority_order(state: store.State, items: list[model.Item]) -> list[model.Item]:
  """Use shared readiness and pass ranking, merging empty passes by score."""
  return graph.ranked_order(state.index, state.config, items, by_pass=True)


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
  if args.envelope and not args.json:
    raise StateError("--envelope requires --json; use list --json --envelope", code="usage")
  state = _state(args)
  view = _list_status_view(args, state.config)
  if args.all and args.status:
    raise StateError(
      "--all and --status cannot be combined; --status already chooses which statuses to show",
      code="usage",
    )
  if args.discovered_from is not None:
    state.index.require(args.discovered_from)
  if args.parent is not None:
    state.index.require(args.parent)
  items = state.index.items
  if args.discovered_from is not None:
    items = [i for i in items if i.discovered_from == args.discovered_from]
  if args.parent is not None:
    items = [i for i in items if i.parent == args.parent]
  if args.tree:
    items = [i for i in items if set(args.tree) & set(i.trees)]
  if args.pass_key:
    items = [i for i in items if i.pass_key == args.pass_key]
  if args.flag:
    items = [i for i in items if set(args.flag) & set(i.flags)]
  if args.held:
    items = [i for i in items if queries.held_flags(state, i)]
  elsewhere, only_sibling = store.sibling_work(state.root, state.index)
  hidden_count = 0
  if view is not None:
    items = [i for i in items if {i.status, store.displayed_status(
      state.config, i, elsewhere.get(i.id, []),
    )} & view]
  elif args.status:
    items = [i for i in items if {i.status, store.displayed_status(
      state.config, i, elsewhere.get(i.id, []),
    )} & set(args.status)]
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
    items = _list_in_priority_order(state, items)
  claim_w = _claim_width(state.config, items, elsewhere)
  pass_w = _pass_width(state.index, items)
  status_w = _status_width(state.config, items, elsewhere)
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
  lines.extend(_sibling_only_lines(only_sibling))
  rows = [
    i.to_dict() | {"ready": queries.ready_state(state, i), "in_work_elsewhere": elsewhere.get(i.id, [])}
    for i in items
  ]
  if args.envelope:
    _emit(args, {"items": rows, "only_in_sibling": only_sibling}, "\n".join(lines),
          keep_empty=("items", "only_in_sibling"))
  else:
    _emit(args, rows, "\n".join(lines))
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
  if args.title_exact is not None:
    if args.pattern is not None or args.fields is not None:
      raise StateError(
        "--title-exact cannot be combined with a PATTERN or --in; "
        "use one search mode",
        code="usage",
      )
    return _find_title_exact(args)
  if args.pattern is None:
    raise StateError(
      "find needs a PATTERN or --title-exact TITLE", code="usage",
    )
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
  _emit_find(args, state, hits)
  return OK


def _find_title_exact(args: argparse.Namespace) -> int:
  """Items whose stored full title equals the argument, for client-side dedupe.
  Plain string equality: no casefold, no Unicode normalization, no short title."""
  state = _state(args)
  hits = [(i, "title", i.title) for i in state.index.items if i.title == args.title_exact]
  _emit_find(args, state, hits)
  return OK


def _emit_find(
  args: argparse.Namespace, state: store.State, hits: list[tuple[model.Item, str, str]]
) -> None:
  """The find envelope shared by every search mode."""
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
  elsewhere = store.in_work_elsewhere(state.root, state.index) if not args.json else {}
  rows = _item_rows(state, [i for i, _, _ in hits], elsewhere=elsewhere)
  text = "\n".join(
    f"{row}\n      matched in {f}: {s}" for row, (_, f, s) in zip(rows, hits)
  ) or "no matching items"
  _emit(args, payload, text)


def _warn_files_section(state: store.State) -> None:
  warning = queries.files_section_warning(state)
  if warning is not None:
    print(f"slicer: warning: {warning}", file=sys.stderr)


def _deps_overlap(args: argparse.Namespace, state: store.State) -> int:
  """The paths each pair of the named items shares, by their `files_section`."""
  if len(args.overlap) < 2:
    raise StateError("deps --overlap needs at least two item ids", code="usage")
  if args.id is not None or args.format is not None:
    raise StateError("deps --overlap cannot be combined with ID or --format", code="usage")
  pairs = queries.path_overlap(state, args.overlap)
  _warn_files_section(state)
  lines = [
    f"{pair['a']} {pair['b']}  " + (", ".join(pair["shared"]) or "-")  # type: ignore[arg-type]
    for pair in pairs
  ]
  _emit(args, {"overlap": pairs}, "\n".join(lines))
  return OK


def cmd_deps(args: argparse.Namespace) -> int:
  state = _state(args)
  index = state.index
  cfg = state.config
  if args.overlap is not None:
    return _deps_overlap(args, state)
  if args.id is not None:
    state.index.require(args.id)
  if args.format == "mermaid":
    diagram = graph.mermaid(index, focus=args.id)
    _emit(args, {"format": "mermaid", "graph": diagram}, diagram)
    return OK
  if args.id is not None:
    item = index.require(args.id)
    blocked = graph.pending(index, item, cfg)
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
    held = graph.open_slivers(index, item, cfg.settled_statuses())
    if held:
      lines.append("held by slivers  " + ", ".join(held))
    _emit(args, payload, "\n".join(lines))
    return OK
  eff = graph.effective_scores(index)
  unblocked = [it for it in index.items
               if it.status == cfg.open_status and not graph.pending(index, it, cfg)]
  unblocked.sort(key=lambda it: eff[it.id], reverse=True)
  elsewhere = store.in_work_elsewhere(state.root, state.index) if not args.json else {}
  _emit(args, [it.to_dict() for it in unblocked],
        "\n".join(_item_rows(state, unblocked, elsewhere=elsewhere)) or "nothing unblocked")
  return OK


def cmd_show(args: argparse.Namespace) -> int:
  _prepare_note_pack(args)
  state = _state(args)
  with _item_lookup(state, [args.id]):
    item = state.index.require(args.id)
  item, sl, manifest = _note_projection(args, item, state.slices.get(args.id))
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
      if args.notes_kind is not None or manifest is not None:
        payload.update(_note_fields(item, manifest))
        payload["slice_notes"] = list(sl.notes)
      _emit(args, payload, "\n".join(lines + _pack_text(item, sl, manifest)))
    elif len(args.section) == 1:
      section = selected[0]
      payload = {"id": args.id, "section": section.heading, "body": section.body}
      if args.notes_kind is not None or manifest is not None:
        payload.update(_note_fields(item, manifest))
        payload["slice_notes"] = list(sl.notes)
      _emit(args, payload, "\n".join([section.body, *_pack_text(item, sl, manifest)]))
    else:
      payload = {"id": args.id, "sections": [s.to_dict() for s in selected]}
      text = "\n\n".join(f"## {s.heading}\n{s.body}" for s in selected)
      if args.notes_kind is not None or manifest is not None:
        payload.update(_note_fields(item, manifest))
        payload["slice_notes"] = list(sl.notes)
      _emit(args, payload, "\n".join([text, *_pack_text(item, sl, manifest)]))
    return OK
  if args.context:
    raise StateError(
      "--context requires at least one --section; pass --section NAME, for example "
      "--section Implement. `slicer sections` lists the names",
      code="usage",
    )
  resolved = render.citations(state.index, item)
  header = render.header_lines(state.index, item)
  kids = queries.slivers_of(state, item.id)
  sliver_rows = [{"id": k.id, "status": k.status, "title": k.title} for k in kids]
  sliver_lines = ["Slivers:", *(f"  {k.id}  {k.status}  {k.display_title()}" for k in kids)] if kids else []
  if sl is None:
    lines = [f"{item.id}  {item.display_title()}", *item.rendered_notes]
    if header:
      lines.append(header)
    lines.append(f"(no slice yet; run `slicer promote {item.id}`)")
    _emit(args, item.to_dict() | _note_fields(item, manifest) | {
            "ready": queries.ready_state(state, item), "paths": queries.item_file_paths(state, item.id),
            "citations": resolved, "slivers": sliver_rows},
          "\n".join(lines + sliver_lines + _ready_line(state, item)
                    + _pack_text(item, sl, manifest, notes=False)))
    return OK
  text = render.render_slice(
    sl, state.config, state.template("slice.md"), item.rendered_notes, header,
  ).decode("utf-8")
  _emit(args, item.to_dict() | _note_fields(item, manifest) | {
    "ready": queries.ready_state(state, item),
    "slice": sl.to_dict(), "paths": queries.item_file_paths(state, item.id),
    "citations": resolved, "slivers": sliver_rows,
  }, "\n".join([text, *sliver_lines, *_ready_line(state, item),
                *_pack_text(item, sl, manifest, notes=False)]))
  return OK


def _ready_line(state: store.State, item: model.Item) -> list[str]:
  """The one text line `show` adds once a check is recorded; none before that."""
  if item.readiness is None:
    return []
  record = item.readiness
  verdict = "true" if queries.ready_state(state, item) is True else "stale"
  return [f"Ready: {verdict} ({record.by}, {record.at})"]


def cmd_status(args: argparse.Namespace) -> int:
  """The one-call front door: what is next, how far along, and what is blocked."""
  state = _state(args)
  elsewhere, only_sibling = store.sibling_work(state.root, state.index)
  result = queries.next_item(state, 0, elsewhere)
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

  sp = sub.add_parser("ai", help="agent onboarding: instructions, skill, relay, relay-roles, build-roadmap, getting-started, show, set, reset, hooks, guard, relay-orchestrator, relay-brief", parents=[common])
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
  for name in ai.PROMPT_NAMES:
    help_text = (
      "print the first-session prompt for an agent (no project needed)"
      if name == "getting-started" else
      "print the prompt that builds the roadmap from the project's docs (no project needed)"
    )
    inner = aisub.add_parser(
      name, help=help_text,
      description=(
        help_text[0].upper() + help_text[1:] + ". Does not lock or write. "
        "--json returns {name, prompt, customized} in that key order."
      ),
      parents=[common],
    )
    _json_flags(inner, suppress=True)
    inner.set_defaults(func=cmd_ai_prompt, prompt_name=name)
  for name, fn in (("show", cmd_ai_show), ("set", cmd_ai_set), ("reset", cmd_ai_reset)):
    inner = aisub.add_parser(name, help=f"{name} a project prompt override", parents=[common])
    inner.set_defaults(func=fn if name == "show" else _mutating(fn))
    _json_flags(inner, suppress=True)
    inner.add_argument("name", help=", ".join(ai.PROMPT_NAMES))
    if name == "show":
      inner.add_argument("--default", action="store_true", help="print the built-in prompt")
    else:
      _check_flag(_strict_flag(_render_flag(inner)))
    if name == "set":
      inner.add_argument("--file", help="read the new text from this file, exactly")
      inner.add_argument("--stdin", action="store_true", help="read the new text from stdin")
  inner = aisub.add_parser(
    "skill", help="print or install the agent skill for Claude Code, Codex, and Grok",
    description=(
      "Print the SKILL.md that routes getting started, building a roadmap, "
      "planning and filing, implementing a slice, and a slice relay. Same project read and "
      "fallback as instructions: warns on stderr, does not lock or write. "
      "--output PATH and --install write the text it would have printed "
      "instead, and stdout lists the paths written."
    ),
    parents=[common],
  )
  inner.set_defaults(func=cmd_ai_skill)
  _json_flags(inner, suppress=True)
  inner.add_argument("--output", metavar="PATH", help="write the skill to PATH, creating parent directories")
  inner.add_argument(
    "--install", action="store_true",
    help="write it to .claude/skills/slicer/, .agents/skills/slicer/ and .grok/skills/slicer/ under the project root",
  )
  inner.add_argument(
    "--force", action="store_true", help="replace a destination that holds different text",
  )
  inner = aisub.add_parser(
    "hooks", help="print or install project-local Claude Code hooks",
    description=(
      "Print the Claude Code hooks snippet. --install merges it into the project's "
      ".claude/settings.json; stdout lists the path written. Conflicting slicer hooks "
      "are refused unless --force, which removes the other slicer handlers and keeps every "
      "other handler. Does not take a project lock. "
      "The PreToolUse guard hook needs slicer 1.18 or later on PATH: an older slicer "
      "exits 2 on the unknown subcommand, and Claude Code treats exit 2 as a block."
    ), parents=[common],
  )
  inner.set_defaults(func=cmd_ai_hooks)
  _json_flags(inner, suppress=True)
  inner.add_argument("--install", action="store_true",
                     help="merge hooks into .claude/settings.json under the project root")
  inner.add_argument("--force", action="store_true",
                     help="remove other slicer handlers from conflicting groups (needs --install)")
  inner = aisub.add_parser(
    "guard", help="judge a Claude Code PreToolUse payload from stdin",
    description=(
      "Read a PreToolUse JSON object from stdin and deny explicit tracking paths. "
      "Allows malformed input silently. Stdout is the hook protocol, not --json."
    ), parents=[common],
  )
  inner.set_defaults(func=cmd_ai_guard)
  inner = aisub.add_parser(
    "relay", help="print the slice relay, or write the example role matrix with --init-roles",
    description=(
      "Print the slice relay: an outer session spawns one orchestrator per slice, which "
      "runs the slice through an implementer, a reviewer, an architect and a gate. The text "
      "opens with the `Contract: relay vN` line; `ai relay-orchestrator` prints the "
      "orchestrator's part. A project whose config pins a different relay contract "
      "(`relay_contract`) is refused with code contract_mismatch (`--init-roles` is not). "
      "A project with implement_finish handoff "
      "closes with handoff. A project whose .slicer/relay-roles.json exists gets a "
      "## Roles matrix section after Setup holding the matrix as Markdown, and Setup "
      "proposes roles from it; without the file, Setup names `slicer relay-roles init`. "
      "A leftover .slicer/relay-roles.md is ignored with a stderr warning. No project, or "
      "a config or index that cannot be read, prints the generic text and warns on "
      "stderr. Does not lock or write, except --init-roles, which is "
      "`relay-roles init --render` under the project lock."
    ),
    parents=[common],
  )
  inner.add_argument(
    "--init-roles", action="store_true",
    help="write the example role matrix to .slicer/relay-roles.json and render it",
  )
  inner.add_argument(
    "--force", action="store_true",
    help="with --init-roles, replace an existing .slicer/relay-roles.json",
  )
  inner.set_defaults(func=cmd_ai_relay)
  _json_flags(inner, suppress=True)
  inner = aisub.add_parser(
    "relay-roles", help="print the project's relay role matrix (no lock, no write)",
    description=(
      "Print the role matrix the slice relay embeds, the same as `slicer relay-roles show`. "
      "Needs a project. Without .slicer/relay-roles.json it exits 2 with code "
      "no_relay_roles. Does not lock or write."
    ),
    parents=[common],
  )
  inner.set_defaults(func=cmd_ai_relay_roles)
  _json_flags(inner, suppress=True)
  inner = aisub.add_parser(
    "relay-orchestrator", help="print the contract of the orchestrator the slice relay spawns",
    description=(
      "Print the orchestrator's own contract for one slice and its run report shape, "
      "which `slicer ai relay` tells the outer session to put first in the orchestrator's "
      "brief. Both texts open with the same `Contract: relay vN` line. The finish step and "
      "the report kind follow the project's implement_finish and "
      "handoff_requires_note_kind, as for `ai relay`. No project, or a config or index that "
      "cannot be read, prints the generic text and warns on stderr. A project whose config "
      "pins a different relay contract (`relay_contract`) is refused with code "
      "contract_mismatch. Does not lock or write. "
      "`--session` prints the variant for a session the owner talks to that orchestrates "
      "every slice itself: it begins and ends the run, records its own step, asks the owner "
      "directly and runs Approve and Land, with no hand-back. `--json` returns "
      "{contract, pinned, orchestrator, ...}, and with `--session` also `session: true`."
    ),
    parents=[common],
  )
  inner.add_argument(
    "--session", action="store_true",
    help="print the variant for a session that orchestrates every slice itself",
  )
  inner.set_defaults(func=cmd_ai_relay_orchestrator)
  _json_flags(inner, suppress=True)

  inner = aisub.add_parser(
    "relay-brief", help="print a slice-specific orchestrator brief (no lock, no write)",
    description=(
      "Print a slice-specific orchestrator brief. A project whose config pins a different "
      "relay contract (`relay_contract`) is refused with code contract_mismatch."
    ),
    parents=[common],
  )
  inner.add_argument("id")
  for flag in ("worktree", "branch", "run", "step", "well-specified", "split"):
    inner.add_argument("--" + flag, required=True)
  inner.add_argument("--parallel", action="append", metavar="ID")
  inner.add_argument("--prefer-agent", metavar="AGENT")
  inner.add_argument("--select", action="append", metavar="ROLE=ROW_ID")
  inner.set_defaults(func=cmd_ai_relay_brief)
  _json_flags(inner, suppress=True)

  sp = add("init", cmd_init, "create .slicer/ in a project")
  sp.add_argument("--force", action="store_true", help="overwrite an existing config and templates")
  sp.add_argument("--id-prefix", help="prefix for new ids (default S; --force retains the existing prefix)")
  sp.add_argument("--id-width", type=int, help="minimum digits in new ids (default 2; --force retains the existing width)")
  sp.add_argument("--id", help="id the first add allocates, matching the scheme, such as S21 or sp-21")
  sp.add_argument(
    "--relay-roles", action="store_true",
    help="also write the example relay roles matrix (see `relay-roles init`)",
  )

  sp = add("setup-git", cmd_setup_git,
      "print the git config for the merge drivers (once per clone; the render one is optional)")
  modes = sp.add_mutually_exclusive_group()
  modes.add_argument("--hook", action="store_true",
      help="print an optional post-merge hook and its install command; writes nothing")
  modes.add_argument("--attributes", action="store_true",
      help="also refresh .slicer/.gitattributes from the current template and add any "
           "missing .slicer/.gitignore lines")

  # Git calls this, not a person, so it stays out of the command list.
  sp = add("merge-index", cmd_merge_index, argparse.SUPPRESS, json_flag=False)
  sp.add_argument("base", help="the common ancestor's index.json (Git's %%O)")
  sp.add_argument("ours", help="this side's index.json, rewritten with the result (%%A)")
  sp.add_argument("theirs", help="the other side's index.json (%%B)")

  sp = add("merge-relay-roles", cmd_merge_relay_roles, argparse.SUPPRESS, json_flag=False)
  sp.add_argument("base", help="the common ancestor's relay-roles.json (Git's %%O)")
  sp.add_argument("ours", help="this side's relay-roles.json, rewritten with the result (%%A)")
  sp.add_argument("theirs", help="the other side's relay-roles.json (%%B)")

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

  sp = _strict_flag(_render_flag(add("next", cmd_next, "the highest-priority startable item")))
  _recursive_flag(sp)
  sp.add_argument("-n", type=_nonnegative_int, default=None, metavar="N",
                  help="skip N currently eligible items (default 0); return one item")
  sp.add_argument("--max-attempts", type=_positive_int, default=None, metavar="N",
                  help="skip fresh implementation pickups at N attempts; allow started resumes")
  sp.add_argument("--batch", type=_positive_int, default=None, metavar="K",
                  help="return up to K items; a dependent follows its dependency")
  sp.add_argument("--disjoint", action="store_true",
                  help="with --batch, skip an item whose files_section paths overlap a chosen item's")
  sp.add_argument("--tree", help="only items in this tree (one tree; not repeatable)")
  sp.add_argument("--size", help="only items of this exact size")
  sp.add_argument("--flag", action="append", metavar="PATTERN",
                  help="include any matching flag (repeatable, case-sensitive); quote patterns: --flag 'risk-*'")
  sp.add_argument("--no-flag", action="append", metavar="PATTERN",
                  help="exclude any matching flag, overriding --flag (repeatable); quote patterns: --no-flag 'risk-*'")
  sp.add_argument("--start", action="store_true", help="mark the returned item or batch started")
  sp.add_argument("--show", action="store_true",
                  help="also include the item's full slice, as `show` returns it")
  sp.add_argument("--notes-kind", action="append", help="select exact note kinds (repeatable; requires --ready)")
  sp.add_argument("--min-trust", choices=note_pack.TRUST,
                  help="pack current notes at or above this verification tier")
  sp.add_argument("--notes-budget", type=int,
                  help="pack whole current notes within this nonnegative character budget")
  sp.add_argument("--ready", action="store_true",
                  help="bounded pickup: item identity, its slice, and blocked ids; "
                       "ready-checked items come first, within their pass; "
                       "does not filter, so an item's ready can be false")
  sp.add_argument("--section", action="append",
                  help="with --ready, return only this section (repeatable)")
  sp.add_argument("--owner", help="with --start, who to claim as (overrides SLICER_CLAIM_OWNER and claim_owner)")
  sp.add_argument("--status", help="draw from this queue instead: the review status or a custom status")
  sp.add_argument("--start-to", metavar="KEY",
                  help="with --start and a custom --status, move the claimed item to this status")
  sp.add_argument("--path", action="store_true",
                  help="also print the slice file path (text only; JSON is unchanged)")

  sp = add("plan-parallel", cmd_plan_parallel,
           "waves of items that can run in parallel, from dependencies and files_section paths")
  sp.add_argument("--width", type=_positive_int, default=4, metavar="N",
                  help="at most N items per wave (default 4)")
  sp.add_argument("--max-attempts", type=_positive_int, default=None, metavar="N",
                  help="skip fresh implementation pickups at N attempts; started work stays in flight")
  sp.add_argument("--tree", help="only items in this tree (one tree; not repeatable)")
  sp.add_argument("--size", help="only items of this exact size")
  sp.add_argument("--flag", action="append", metavar="PATTERN",
                  help="include any matching flag (repeatable, case-sensitive); quote patterns: --flag 'risk-*'")
  sp.add_argument("--no-flag", action="append", metavar="PATTERN",
                  help="exclude any matching flag, overriding --flag (repeatable); quote patterns: --no-flag 'risk-*'")

  sp = add("next-id", cmd_next_id, "the id the next add would take, without allocating it")

  sp = _strict_flag(_render_flag(add(
    "id-prefix", _mutating(cmd_id_prefix),
    "show the id prefix, or change its case for new ids",
  )))
  sp.add_argument("prefix", nargs="?", help="the new prefix; only its case may differ")
  sp.add_argument("--dry-run", action="store_true", help="report only; write nothing")

  sp = _recursive_flag(add("list", cmd_list, "list by readiness, pass and score; empty passes merge by score; omit done and retired unless asked"))
  sp.add_argument("--envelope", action="store_true",
                  help="with --json, wrap items and sibling-only rows in an object")
  sp.add_argument("--all", action="store_true",
                  help="include done and retired items (default: omit them)")
  sp.add_argument("--status", action="append",
                  help="filter by status (repeatable); replaces the default of omitting done and retired")
  sp.add_argument("--in-work", action="store_true",
                  help="only started items, plus reviewing when that status is set")
  sp.add_argument("--review", action="store_true",
                  help="only items in the review status")
  sp.add_argument("--held", action="store_true", help="only items carrying a configured hold flag")
  sp.add_argument("--discovered-from", help="filter by exact source item ID")
  sp.add_argument("--parent", help="only slivers of this item ID")
  sp.add_argument("--tree", action="append", help="filter by tree (repeatable)")
  sp.add_argument("--pass", dest="pass_key", help="filter by pass")
  sp.add_argument("--flag", action="append",
                  help="filter by flag (repeatable; an item matches if it has any of them)")
  sp.add_argument("--sort", choices=["score", "effort"],
                  help="score: flat priority, highest first; effort: lightest estimate first, unset last")

  sp = add("deps", cmd_deps, "dependencies: unblocked items, or one item's edges")
  sp.add_argument("id", nargs="?")
  sp.add_argument("--format", choices=["mermaid"], help="render the dependency graph")
  sp.add_argument(
    "--overlap", nargs="+", metavar="ID",
    help="print the paths each pair of these items shares in the files_section "
    "(needs at least two ids)",
  )

  sp = add("find", cmd_find, "search items by text")
  sp.add_argument("pattern", nargs="?")
  sp.add_argument(
    "--in", dest="fields",
    help="comma-separated fields to search: id,title,short_title,findings,body (default: all)",
  )
  sp.add_argument(
    "--title-exact", metavar="TITLE",
    help="match items whose full stored title equals TITLE exactly (case- and "
    "Unicode-sensitive); replaces PATTERN and --in",
  )

  sp = add("show", cmd_show, "print one slice")
  sp.add_argument("id")
  sp.add_argument(
    "--section", action="append",
    help="print only this section's body; repeat to select several",
  )
  sp.add_argument("--notes-kind", action="append", help="select exact note kinds (repeatable; empty selects untyped)")
  sp.add_argument("--min-trust", choices=note_pack.TRUST,
                  help="pack current notes at or above this verification tier")
  sp.add_argument("--notes-budget", type=int,
                  help="pack whole current notes within this nonnegative character budget")
  sp.add_argument("--context", action="store_true",
                  help="with --section, include title, dependencies, and scope boundary")

  add("sections", cmd_sections,
      "list configured section names, marking the ones next requires")

  sp = _check_flag(_strict_flag(_render_flag(add(
    "config", cmd_config,
    "print the effective config, or one value by dotted key; set or unset one key",
  ))))
  sp.add_argument("key", nargs="?", help="a top-level key or a dotted path such as id.width")
  sp.add_argument("value", nargs="?", help="the new value; JSON for a list or an object")
  sp.add_argument("--unset", action="store_true",
                  help="remove the key from config.json so its default applies again")

  add("recommended-workflow", cmd_recommended_workflow,
      "print the generic recommended project workflow")

  sp = _strict_flag(_render_flag(add("add", _mutating(cmd_add), "append a roadmap item")))
  sp.add_argument("title")
  sp.add_argument("--depends-on", action="append", help="dependency id (repeatable)")
  sp.add_argument("--short-title", help="short title for the roadmap row")
  sp.add_argument("--discovered-from", help="existing item ID that discovered this work")
  sp.add_argument("--parent", help="existing item ID this item is a sliver of")
  sp.add_argument("--id", help="use this id instead of the next free one")
  sp.add_argument("--print-id", action="store_true", help="print only the filed item's id")
  sp.add_argument("--key", help="case-sensitive filing identity; retries return the existing item")
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
  sp.add_argument("--parent", help="existing item ID this item is a sliver of; --parent '' clears it")
  sp.add_argument("--discovered-from", help="existing item ID that discovered this work; --discovered-from '' clears it")
  sp.add_argument("--importance", type=int, help="1-3")
  sp.add_argument("--urgency", type=int, help="1-3")
  sp.add_argument("--effort", type=int, help="1-3; optional estimate")
  sp.add_argument("--no-effort", action="store_true", help="clear the effort estimate")
  sp.add_argument("--attempts", type=int, help="implementation attempt count; an integer >= 0")

  sp = _strict_flag(_render_flag(add("edit", _mutating(cmd_edit), "edit a slice section, scope boundary or lead")))
  sp.add_argument("id")
  sp.add_argument("--section", help="section to edit; cannot combine with --boundary or --lead")
  sp.add_argument("--boundary", action="store_true", help="replace the full scope-boundary paragraph; cannot combine with --section or --lead")
  sp.add_argument("--lead", action="store_true", help="replace the lead paragraphs (blank-line separated; empty clears); cannot combine with --section, --boundary, or --append")
  sp.add_argument("--append", action="store_true", help="append with a blank line; requires --text, --file, or --stdin; empty input leaves the body unchanged")
  sp.add_argument("--text", help="inline body (exact in replacement mode); empty text clears unless appending; cannot combine with --file/--stdin")
  sp.add_argument("--file")
  sp.add_argument("--stdin", action="store_true")

  sp = _strict_flag(_render_flag(add("refs", cmd_refs,
    "check file:line references in slice text; --fix rewrites the moved ones")))
  sp.add_argument("id", nargs="*", help="item ids to scan, whatever their status (default: every open slice)")
  sp.add_argument("--fix", action="store_true", help="rewrite moved references in the lead, boundary and sections; notes are only reported")

  sp = _strict_flag(_render_flag(add("decide", _mutating(cmd_decide), "record an owner decision and clear hold flags")))
  sp.add_argument("id")
  sp.add_argument("--text", help="inline answer; cannot combine with --file/--stdin")
  sp.add_argument("--file", help="read the answer from this file; cannot combine with --text/--stdin")
  sp.add_argument("--stdin", action="store_true", help="read the answer from stdin; cannot combine with --text/--file")

  sp = add("ready", _mutating(cmd_ready), "record that a slice's spec check passed")
  sp.add_argument("id")
  sp.add_argument("--by", required=True, help="who ran the spec check, one line")
  sp.add_argument("--note", help="one line kept with the verdict")

  sp = _strict_flag(_render_flag(add("note", _mutating(cmd_note), "append a dated note to an item")))
  sp.add_argument("id")
  sp.add_argument("--kind", help="optional note kind, validated against note_kinds")
  sp.add_argument("--owner", help="note author; uses the claim actor precedence")
  sp.add_argument("--stale-after", default="", help="timezone-aware ISO8601 expiry; normalized to UTC")
  sp.add_argument("--text", help="inline note; cannot combine with --file/--stdin")
  sp.add_argument("--file")
  sp.add_argument("--stdin", action="store_true")

  sp = add("feedback", cmd_feedback, "keep a local log of frictions, bugs, and feature ideas")
  sp.add_argument("--kind", help="friction, bug, or feature; appends an entry")
  sp.add_argument("--item", help="optional item id, stored as written and not looked up")
  sp.add_argument("--text", help="inline body; cannot combine with --file/--stdin")
  sp.add_argument("--file")
  sp.add_argument("--stdin", action="store_true")
  sp.add_argument("--out", help="copy the log's exact bytes to PATH instead of appending")
  sp.add_argument("--force", action="store_true", help="with --out, replace a different existing file")

  sp = add("feedback-report", cmd_feedback_report,
    "file bug and feature entries from the feedback log as GitHub issues with gh")
  sp.add_argument("--repo", help="OWNER/REPO to file to; defaults to config issues_repo")
  sp.add_argument("--kind", action="append",
    help="repeatable; friction, bug, or feature (default: bug and feature)")
  sp.add_argument("--item", help="only entries stored with exactly this item string")
  sp.add_argument("--dry-run", action="store_true", help="print the issues and call nothing")
  sp.add_argument("--yes", action="store_true", help="file the issues with `gh issue create`")

  sp = _strict_flag(_render_flag(add("issues-pull", cmd_issues_pull,
    "file a repository's open GitHub issues as unscored roadmap rows with gh")))
  sp.add_argument("--repo", help="OWNER/REPO to read; defaults to config issues_repo")
  sp.add_argument("--limit", type=int, default=30, help="most issues to read, 1-100 (default 30)")
  sp.add_argument("--dry-run", action="store_true", help="list the issues and write nothing")

  for command in ("note-verify", "note-attest"):
    sp = _strict_flag(_render_flag(add(command, _mutating(cmd_note_verify),
      "record note verification (workflow attestation, not caller authentication)")))
    sp.add_argument("id")
    sp.add_argument("--note-id", required=True, help="stable item-note ID from show --json")
    if command == "note-attest":
      sp.add_argument("--owner", required=True, help="owner name; stored as human:NAME")
    else:
      sp.add_argument("--owner", help="verifying actor; uses the claim actor precedence; no human: prefix")

  sp = _strict_flag(_render_flag(add("note-retract", _mutating(cmd_note_retract),
    "retract an item note as wrong; its text and who, when and why stay on the record")))
  sp.add_argument("id")
  sp.add_argument("--note-id", required=True, help="stable item-note ID from show --json")
  sp.add_argument("--reason", required=True, help="one line saying why the note is wrong")
  sp.add_argument("--owner", help="retracting actor; uses the claim actor precedence; no human: prefix")

  sp = _check_flag(_render_flag(add("done", cmd_done, "mark an item finished")))
  sp.add_argument("id", nargs="+", help="item ids, or - alone to read whitespace-separated ids from stdin")
  sp.add_argument("--note", help="one line recorded in history (see `slicer note` for a durable note on the item)")
  sp.add_argument("--owner", help="who to record (overrides SLICER_CLAIM_OWNER and claim_owner)")

  sp = _render_flag(
    add("land", cmd_land, "mark a merged slice done and print the git commands left"),
    help="accepted for symmetry with done; land always renders and checks",
  )
  sp.add_argument("id", help="the item to land")
  sp.add_argument("--branch", required=True, help="the slice's branch; it must be merged into HEAD")
  sp.add_argument("--note", required=True, help="one line recorded in history, as for `done`")
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

  sp = sub.add_parser("template", help="show, replace and reset the render templates", parents=[common])
  tsub = sp.add_subparsers(dest="template_command", required=True)

  def tadd(name: str, fn, help_: str, *, write: bool = False) -> argparse.ArgumentParser:
    inner = tsub.add_parser(name, help=help_, parents=[common])
    inner.set_defaults(func=_mutating(fn) if write else fn)
    _json_flags(inner)
    if write:
      _check_flag(_strict_flag(_render_flag(inner)))
    return inner

  tadd("list", cmd_template_list, "name each template and say whether it differs from the default")
  inner = tadd("show", cmd_template_show, "print a template byte for byte")
  inner.add_argument("name", help="slice.md, roadmap.md, row.md or relay-brief.md")
  inner.add_argument("--default", action="store_true", help="print the built-in template, not the project's")
  inner = tadd("set", cmd_template_set, "replace a template with new text", write=True)
  inner.add_argument("name", help="slice.md, roadmap.md, row.md or relay-brief.md")
  inner.add_argument("--file", help="read the new text from this file, exactly")
  inner.add_argument("--stdin", action="store_true", help="read the new text from stdin")
  tadd("reset", cmd_template_reset, "restore a template to the built-in text",
       write=True).add_argument("name", help="slice.md, roadmap.md, row.md or relay-brief.md")

  sp = _strict_flag(_render_flag(add("remove", _mutating(cmd_remove), "retire an obsolete item, or purge one outright")))
  sp.add_argument("id")
  mode = sp.add_mutually_exclusive_group(required=True)
  mode.add_argument("--reason", help="retire it, recording why; the id stays claimed")
  mode.add_argument(
    "--purge", action="store_true", help="delete it outright, for something that never should have existed"
  )
  sp.add_argument("--force", action="store_true", help="override the dependents and done guards")
  sp.add_argument("--dry-run", action="store_true", help="preview the removal and its fallout; write nothing")

  # `retire` is `remove --reason` under the name of the status it sets. Its parser has no
  # --purge, and the flag's default keeps cmd_remove on the retire path.
  sp = _strict_flag(_render_flag(add("retire", _mutating(cmd_remove), "retire an obsolete item, as `remove --reason` does")))
  sp.add_argument("id")
  sp.add_argument("--reason", required=True, help="retire it, recording why; the id stays claimed")
  sp.add_argument("--force", action="store_true", help="override the dependents and done guards")
  sp.add_argument("--dry-run", action="store_true", help="preview the retirement and its fallout; write nothing")
  sp.set_defaults(purge=False)

  add("render", cmd_render, "regenerate .slicer/render/")

  sp = add("sync", cmd_sync, "rewrite derived lines in other documents")
  sp.add_argument("--check", action="store_true", help="report drift instead of writing")

  sp = add("verify", cmd_verify, "check the index for consistency (and against git unless git_check is off)")
  sp.add_argument(
    "--landed", metavar="ID",
    help="instead, check that ID's done state is committed in REF's history; exit 1 when it is not",
  )
  sp.add_argument("--on", metavar="REF", help="with --landed, the branch, tag or commit to check (default HEAD)")

  sp = _recursive_flag(add("check", cmd_check, "the CI gate: render, sync and integrity"))
  sp.add_argument("--diff", action="store_true", help="show a diff for each stale file")

  sp = add("goals", cmd_goals, "show project goals and non-goals, and their records")
  sp.add_argument("--retired", action="store_true", help="include retired goal and non-goal records")

  sp = sub.add_parser("catalog", help="project-knowledge records", parents=[common])
  csub = sp.add_subparsers(dest="catalog_command", required=True)

  def cadd(name: str, fn, help_: str, *, mutate: bool = False) -> argparse.ArgumentParser:
    inner = csub.add_parser(name, help=help_, parents=[common])
    inner.set_defaults(func=_mutating(fn) if mutate else fn)
    _json_flags(inner)
    if mutate:
      return _strict_flag(_render_flag(inner))
    return inner

  inner = cadd("add", cmd_catalog_add, "add one record", mutate=True)
  inner.add_argument("--kind", required=True, help="goal, non-goal, requirement, constraint, decision, or assumption")
  inner.add_argument("title")
  inner.add_argument("--body", help="record body; cannot combine with --file or --stdin")
  inner.add_argument("--file")
  inner.add_argument("--stdin", action="store_true")

  inner = cadd("list", cmd_catalog_list, "list records")
  inner.add_argument("--kind", help="limit to one kind; non-goal and non_goal are the same")
  inner.add_argument("--retired", action="store_true", help="append retired records after the active ones")

  cadd("show", cmd_catalog_show, "print one record").add_argument("id")

  inner = cadd("edit", cmd_catalog_edit, "edit an active record's title or body", mutate=True)
  inner.add_argument("id")
  inner.add_argument("--title", help="replacement title")
  inner.add_argument("--body", help="replacement body; cannot combine with --file or --stdin")
  inner.add_argument("--file")
  inner.add_argument("--stdin", action="store_true")

  inner = cadd("retire", cmd_catalog_retire, "retire a record and keep its id", mutate=True)
  inner.add_argument("id")
  inner.add_argument("--reason", required=True, help="why it is no longer in force")
  inner.add_argument("--successor", help="a later record of the same kind")

  inner = cadd("move", cmd_catalog_move, "reorder a record within its kind", mutate=True)
  inner.add_argument("id")
  place = inner.add_mutually_exclusive_group(required=True)
  place.add_argument("--before", metavar="ID")
  place.add_argument("--after", metavar="ID")

  inner = cadd("cite", cmd_catalog_cite, "cite catalog records from a roadmap item", mutate=True)
  inner.add_argument("item")
  inner.add_argument("ids", nargs="+", metavar="ID")

  inner = cadd("uncite", cmd_catalog_uncite, "drop catalog citations from a roadmap item", mutate=True)
  inner.add_argument("item")
  inner.add_argument("ids", nargs="+", metavar="ID")

  sp = sub.add_parser(
    "relay-roles", help="the slice-relay role matrix: rows, conditions and rules",
    description=(
      "The slice-relay role matrix lives in .slicer/relay-roles.json and changes only "
      "through these commands. Each row names a role, the slice conditions it applies to, "
      "and the agent, vendor, model and reasoning effort that run it. slicer renders the "
      "matrix to .slicer/render/RELAY-ROLES.md."
    ),
    parents=[common],
  )
  rsub = sp.add_subparsers(dest="relay_roles_command", required=True)

  def radd(name: str, fn, help_: str, *, mutate: bool = False) -> argparse.ArgumentParser:
    inner = rsub.add_parser(name, help=help_, parents=[common])
    inner.set_defaults(func=_mutating(fn) if mutate else fn)
    _json_flags(inner)
    if mutate:
      return _strict_flag(_render_flag(inner))
    return inner

  def row_flags(inner: argparse.ArgumentParser) -> None:
    inner.add_argument("--size", help="sizes the row applies to, comma-separated such as S,M (any text); empty or any for every size")
    inner.add_argument("--effort", help="slice effort scores 1-3, comma-separated; empty for any")
    inner.add_argument("--importance", help="slice importance scores 1-3, comma-separated; empty for any")
    inner.add_argument("--urgency", help="slice urgency scores 1-3, comma-separated; empty for any")
    inner.add_argument("--well-specified", help="yes, no or any")
    inner.add_argument("--split", help="yes for a part of a split slice, no for a whole one, or any")
    inner.add_argument("--sliver", help="yes for a sliver (an item with a parent), no for any other item, or any")
    inner.add_argument("--agent", help="the harness that runs the role, such as Claude Code")
    inner.add_argument("--vendor", help="the model vendor")
    inner.add_argument("--model", help="the model")
    inner.add_argument("--reasoning", help="reasoning effort such as low, medium, high or xhigh")
    inner.add_argument("--note", help="one line shown in the Note column")

  radd("show", cmd_relay_roles_show, "print the matrix as Markdown (no lock, no write)")

  inner = radd(
    "recommend", cmd_relay_roles_recommend,
    "propose each role's row, and the other rows that match, for a slice from its size, "
    "effort, importance and urgency (no lock, no write)",
  )
  inner.add_argument("id")
  inner.add_argument(
    "--well-specified", required=True, metavar="yes|no",
    help="your judgement of whether the slice is well specified; rows with well_specified null match either",
  )
  inner.add_argument(
    "--split", required=True, metavar="yes|no",
    help="yes for a part of a split slice, no for a whole one; rows with split null match either",
  )
  inner.add_argument(
    "--prefer-agent", metavar="AGENT",
    help="choose each role's first matching row on this agent (compared trimmed, ignoring case) "
    "when the role has one; a role without one keeps its first matching row",
  )
  inner.add_argument(
    "--select", action="append", metavar="ROLE=ROW_ID",
    help="use this row for the role, over the preference and the matrix order; repeat for "
    "other roles. The row must be a row of that role that matches the slice",
  )

  inner = radd("init", cmd_relay_roles_init, "write the example matrix", mutate=True)
  inner.add_argument("--force", action="store_true", help="replace an existing relay-roles.json")
  inner.add_argument(
    "--replace-legacy", action="store_true",
    help="also remove a leftover relay-roles.md once the JSON is written",
  )

  inner = radd("add", cmd_relay_roles_add, "add a row", mutate=True)
  inner.add_argument("--role", required=True, help="implementer, reviewer, gate, architect or orchestrator")
  row_flags(inner)

  inner = radd("set", cmd_relay_roles_set, "change only the fields given on a row", mutate=True)
  inner.add_argument("id")
  inner.add_argument("--role", help="implementer, reviewer, gate, architect or orchestrator")
  row_flags(inner)

  radd("remove", cmd_relay_roles_remove, "drop a row", mutate=True).add_argument("id")

  inner = radd("move", cmd_relay_roles_move, "reorder a row", mutate=True)
  inner.add_argument("id")
  inner.add_argument("--before", metavar="ID", help="place the row before this row")
  inner.add_argument("--last", action="store_true", help="place the row at the end")

  radd("rule-add", cmd_relay_roles_rule_add, "add a rule", mutate=True).add_argument("text")

  inner = radd("rule-edit", cmd_relay_roles_rule_edit, "replace a rule's text", mutate=True)
  inner.add_argument("id")
  inner.add_argument("text")

  radd("rule-remove", cmd_relay_roles_rule_remove, "drop a rule", mutate=True).add_argument("id")

  sp = sub.add_parser(
    "relay-run", help="the local ledger of slice-relay runs and their agent steps",
    description=(
      "A relay orchestrator records each run and each role step here so `relay-run list` "
      "and the TUI can show what is running. In a git repo the ledger is "
      "<git common dir>/slicer/relay-runs.jsonl, shared by every worktree and kept when one "
      "is removed; elsewhere it is .slicer/relay-runs.jsonl. Append-only and disposable: "
      "never rendered, never read by check."
    ),
    parents=[common],
  )
  usub = sp.add_subparsers(dest="relay_run_command", required=True)

  def uadd(name: str, fn, help_: str) -> argparse.ArgumentParser:
    inner = usub.add_parser(name, help=help_, parents=[common])
    inner.set_defaults(func=fn)
    _json_flags(inner)
    return inner

  inner = uadd("begin", cmd_relay_run_begin, "start a run on one or more items and print its id")
  inner.add_argument("items", nargs="+", metavar="ID", help="the roadmap items the run relays")
  inner.add_argument(
    "--worktree", metavar="PATH",
    help="the worktree the work happens in, recorded by its directory name "
    "(default: this checkout)",
  )

  inner = uadd(
    "step", cmd_relay_run_step,
    "record an agent's step, or with --step a later phase of one; prints the step id",
  )
  inner.add_argument("run")
  inner.add_argument(
    "--step", metavar="STEP",
    help="update this step of the run instead of adding one; only the fields given change",
  )
  inner.add_argument(
    "--role", help="implementer, reviewer, gate, architect, orchestrator, ui or spec-check; "
    "ui and spec-check have no matrix row",
  )
  inner.add_argument("--agent", help="the harness that runs the role, such as Claude Code")
  inner.add_argument("--vendor", help="the model vendor")
  inner.add_argument("--model", help="the model")
  inner.add_argument("--reasoning", help="reasoning effort such as low, medium, high or xhigh")
  inner.add_argument(
    "--phase", required=True, metavar="start|done|fail|wait",
    help="where the step stands; wait says it waits on the owner, so its run is never stale "
    "until a later start, done or fail of the step",
  )
  inner.add_argument(
    "--waiting-on", metavar="owner|approval",
    help="with --phase wait, what the step waits on (default owner); refused with another phase",
  )
  inner.add_argument(
    "--reason", metavar="TEXT",
    help="with --phase fail, why the step failed, such as a rate limit or out of credits; "
    "refused with another phase",
  )
  inner.add_argument(
    "--verdict", metavar="pass|fail",
    help="with --phase done on a gate step, the verdict that closes the gate round; "
    "refused on any other step or phase",
  )
  inner.add_argument("--task", help="one line on what the step is doing")
  inner.add_argument("--parent", metavar="STEP", help="the step that spawned this subagent")

  inner = uadd(
    "ask", cmd_relay_run_ask,
    "ask the owner a question on a step; with --blocker the step waits on the owner; prints the question id",
  )
  inner.add_argument("run")
  inner.add_argument("--step", required=True, metavar="STEP", help="the step that asks")
  inner.add_argument("--title", required=True, help="the question, on one line")
  inner.add_argument(
    "--option", action="append", metavar="LABEL:SUMMARY",
    help="an answer the owner may pick; give 2 to 4, with distinct labels; no `|` or `;`",
  )
  inner.add_argument("--recommend", metavar="LABEL", help="the option you recommend")
  inner.add_argument(
    "--blocker", action="store_true",
    help="the slice cannot go on without it: the step waits on the owner until it is answered; "
    "without it the question is recorded and the step keeps running",
  )
  inner.add_argument("--key", metavar="AREA.TOPIC", help="a key the owner's answers can be filed under")
  inner.add_argument("--reasoning", metavar="TEXT", help="why; may hold a `|`")

  inner = uadd(
    "answer", cmd_relay_run_answer,
    "record the owner's answer to a question, or with --get read it",
  )
  inner.add_argument("run")
  inner.add_argument("question", metavar="QUESTION_ID")
  inner.add_argument("--option", metavar="LABEL", help="the option the owner picked")
  inner.add_argument("--text", help="a free-text answer, in place of an option")
  inner.add_argument("--why", help="the owner's reason")
  inner.add_argument("--by", metavar="NAME", help="who answered")
  inner.add_argument(
    "--get", action="store_true",
    help="read the question and its answer instead; no lock, no write, and no other flag",
  )

  inner = uadd("end", cmd_relay_run_end, "close a run with its outcome")
  inner.add_argument("run")
  inner.add_argument(
    "--outcome", required=True, metavar="pass|fail|stopped",
    help="how the run ended",
  )
  inner.add_argument(
    "--close-steps", metavar="done|fail",
    help="first mark every step still running with this phase",
  )
  inner.add_argument(
    "--reason", metavar="TEXT",
    help="with --close-steps fail, why each step it closes failed; refused with another "
    "--close-steps or none",
  )

  inner = uadd(
    "list", cmd_relay_run_list,
    "runs in this checkout and its sibling worktrees (no lock, no write)",
  )
  inner.add_argument("run", nargs="?", metavar="RUN", help="show only this run")
  inner.add_argument(
    "--active", action="store_true",
    help="only runs that have not ended and have had an event in the last 30 minutes "
    "or have a step waiting",
  )

  inner = uadd(
    "stats", cmd_relay_run_stats,
    "fix rounds, gate FAILs, role times and owner wait of ended runs (no lock, no write)",
  )
  inner.add_argument(
    "--since", metavar="DATE",
    help="only runs begun at or after this ISO8601 timestamp with a timezone, or a bare "
    "YYYY-MM-DD, which means 00:00Z that day",
  )

  _recursive_flag(add("stats", cmd_stats, "counts by status, size, tree and pass"))

  sp = sub.add_parser("snapshot", help="compact dashboard reads across projects")
  sp.set_defaults(func=cmd_snapshot)
  sp.add_argument("--root", dest="snapshot_roots", action="append",
                  help="project root; repeat to read several projects in order")
  _json_flags(sp)
  _recursive_flag(sp)
  sp.add_argument("--parts", default=",".join(snapshot.PARTS),
                  help="comma-separated dashboard parts: " + ",".join(snapshot.PARTS))

  _recursive_flag(add("status", cmd_status, "next item, progress, and blockers in one view"))

  sp = add("log", cmd_log, "recent history, newest first")
  sp.add_argument("--limit", type=int, default=20, help="how many entries (default 20)")
  sp.add_argument("--item", action="append", help="filter to these item ids (repeatable)")
  sp.add_argument("--action", action="append", help="filter to these actions, e.g. set, edit (repeatable)")
  sp.add_argument("--by", action="append", help="filter to actions recorded by these owners (repeatable)")
  sp.add_argument("--since", help="include entries at or after this timezone-aware ISO8601 timestamp")
  sp.add_argument("--until", help="include entries before this timezone-aware ISO8601 timestamp")

  add("tui", cmd_tui, "browse and reorder interactively (also: ui)",
      json_flag=False, aliases=("ui",))
  return p


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


def _changes_state(args: argparse.Namespace) -> bool:
  if args.command == "init":
    return True
  if not getattr(args.func, "mutates", False):
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
    if getattr(args.func, "mutates", False):
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
