"""Tracking-read commands: `next`, `plan-parallel`, `list`, `find`, `deps`, `show` and `status`.

They only read the roadmap (the one write among them is `next --start`, which claims an item
through `ops`), and the ranking itself is in `queries.py`. Each handler is attached to its
subparser in `cli_parser`; shared helpers come from `cli_common`, and nothing here imports
`cli` or `cli_track`. Tests patch `datetime` here for the note-pack clock, and
`_heal_render` is called as `cli_common._heal_render`.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

from slicer import cli_common, graph, model, note_pack, ops, queries, render, store
from slicer.cli_common import OK, USAGE, _census, _emit, _item_lookup, _mutating, _state, _status_tally
from slicer.config import Config
from slicer.errors import StateError


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
  """The parallel sets of work that can run side by side. Read-only, like `next`."""
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
  for row in plan.wave_conflicts:
    lines.append(
      f"wave conflict: {row['id']} (wave {row['wave']}) depends on "
      f"{row['depends_on']} (wave {row['depends_wave']})"
    )
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
  """The TARGET column's width, or None when the roadmap uses no release targets
  at all (none declared and none named by an item), so such a project keeps the
  table without the column."""
  if not any(index.pass_keys()):
    return None
  return max([len("TARGET"), *(len(i.pass_key or "-") for i in items)])


def _wave_width(index: model.Index, items: list[model.Item]) -> int | None:
  """The WAVE column's width, or None until some item has a planned wave, so a
  roadmap that plans none keeps the table it always had."""
  if not any(i.wave is not None for i in index.items):
    return None
  return max([len("WAVE"), *(len(str(i.wave)) if i.wave is not None else 1 for i in items)])


def _item_rows(
  state: store.State, items: list[model.Item], *,
  claim_w: int | None = None,
  pass_w: int | None = None,
  status_w: int | None = None,
  elsewhere: dict[str, list[dict[str, str]]] | None = None,
  wave_w: int | None = None,
) -> list[str]:
  """The shared queue-listing row format used by `list`, `find` and `deps`.

  `^` marks an effective score lifted above the item's own by a dependent, so a
  blocker of a critical item reads at that item's priority. `!` after the row
  number marks an item that waits on an unfinished dependency, as the TUI queue
  does; it takes the space the row leaves there, so the columns do not move. The
  claim column names the owner, or `*` when the item is in progress and unclaimed.
  The target column appears only when the roadmap uses release targets, and the
  wave column only when some item has a planned wave.
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
  if wave_w is None:
    wave_w = _wave_width(state.index, items)
  rows = []
  for n, item in enumerate(items, 1):
    score = str(eff[item.id]) + ("^" if eff[item.id] > item.score else "")
    effort = "-" if item.effort is None else str(item.effort)
    pass_cell = "" if pass_w is None else f"{item.pass_key or '-':<{pass_w}} "
    if wave_w is not None:
      pass_cell += f"{'-' if item.wave is None else item.wave!s:<{wave_w}} "
    label = cfg.status_label(store.displayed_status(cfg, item, elsewhere.get(item.id, [])))
    mark = "!" if graph.pending(state.index, item, cfg) else " "
    rows.append(
      f"{n:>3}{mark} {item.id:<5} {label:<{status_w}} "
      f"{store.claim_cell(cfg, item, elsewhere.get(item.id)):<{claim_w}} {pass_cell}{item.size:<4} "
      f"{effort:<6} {score:<5} {item.quadrant:<9} {item.display_title()}"
    )
  return rows


def _blocked_legend(state: store.State, items: list[model.Item]) -> list[str]:
  """The line that says what `!` means, or nothing when no printed row carries it.

  It names each marked item with the dependencies it still waits on, in row order,
  so a reader need not run `show` on every marked row.
  """
  waits = [
    f"{item.id} ({', '.join(pending)})"
    for item in items
    if (pending := graph.pending(state.index, item, state.config))
  ]
  if not waits:
    return []
  return [f"! waits on a dependency not yet met: {', '.join(waits)}"]


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
  if args.wave is not None:
    # Validation only: `valid_wave` is pure and writes nothing.
    wave = ops.valid_wave(args.wave)
    if wave is None:
      raise StateError("--wave needs an integer >= 1 to filter by", code="usage")
    items = [i for i in items if i.wave == wave]
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
  wave_w = _wave_width(state.index, items)
  pass_head = "" if pass_w is None else f"{'TARGET':<{pass_w}} "
  if wave_w is not None:
    pass_head += f"{'WAVE':<{wave_w}} "
  header = (
    f"{'#':>3}  {'ID':<5} {'STATUS':<{status_w}} {'CLAIM':<{claim_w}} {pass_head}{'SIZE':<4} "
    f"{'EFFORT':<6} {'SCORE':<5} {'QUADRANT':<9} TITLE"
  )
  lines = [header, *_item_rows(
    state, items, claim_w=claim_w, pass_w=pass_w, status_w=status_w, elsewhere=elsewhere,
    wave_w=wave_w,
  )]
  if not items:
    lines = ["no matching items"]
  lines.extend(_blocked_legend(state, items))
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
  text = "\n".join([
    *(f"{row}\n      matched in {f}: {s}" for row, (_, f, s) in zip(rows, hits)),
    *_blocked_legend(state, [i for i, _, _ in hits]),
  ]) or "no matching items"
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
