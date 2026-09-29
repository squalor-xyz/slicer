"""Command semantics, shared by the CLI and the TUI.

Every mutation goes through here so the two front ends cannot drift apart,
and so each one records the same log entry.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from slicer import graph, ids, outline, prose, render, vcs
from slicer.errors import StateError
from slicer.model import Index, Item, LogEntry, PassInfo, Section, Slice, effort_rank, extract_boundary, is_unscored
from slicer.store import State


def _now() -> str:
  return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _record(state: State, item: str, action: str, frm: str = "", to: str = "", note: str = "") -> None:
  state.log(LogEntry(when=_now(), item=item, action=action, frm=frm, to=to, note=note))


def _fmt(value: object) -> str:
  """Render a field value compactly for a one-line history note."""
  if isinstance(value, list):
    return ",".join(value) or "-"
  return str(value) if value not in (None, "") else "-"


# Fields that land on one line of a roadmap table cell. A newline in any of
# them ends the table early, so they are refused on the way in rather than
# mangled on the way out.
ONE_LINE_FIELDS = (
  "title",
  "short_title",
  "size",
  "findings",
  "pass_key",
  "group",
)


def _clean(value: object) -> object:
  """Drop empty/whitespace entries from a list field, so `--depends-on ""`
  (and the like) clears to `[]` rather than storing a phantom `['']`."""
  if isinstance(value, list):
    return [v for v in value if str(v).strip()]
  return value


def _reject_bad_text(**fields: object) -> None:
  """Refuse text that cannot survive a roadmap row. Call before mutating.

  `add` allocates an id before it builds the item, and `set_fields` mutates in
  place, so anything raised half way through leaves state inconsistent in
  memory. Everything is checked up front for that reason.
  """
  title = fields.get("title")
  if title is not None and not str(title).strip():
    raise StateError("a title cannot be blank", code="blank_title")
  for key in ONE_LINE_FIELDS:
    value = fields.get(key)
    if value is not None and "\n" in str(value):
      raise StateError(f"{key} cannot contain a newline", code="newline_in_field")
  for tree in fields.get("trees") or []:
    if "\n" in str(tree):
      raise StateError("a tree name cannot contain a newline", code="newline_in_field")
  for flag in fields.get("flags") or []:
    if "\n" in str(flag):
      raise StateError("a flag cannot contain a newline", code="newline_in_field")


def _valid_score(name: str, value: object) -> int:
  """An Eisenhower axis is an integer 1-3. Reject anything else, naming it."""
  try:
    n = int(value)  # type: ignore[arg-type]
  except (TypeError, ValueError):
    raise StateError(f"{name} must be a number 1-3, not {value!r}", code="state") from None
  if not 1 <= n <= 3:
    raise StateError(f"{name} must be 1, 2 or 3, not {n}", code="state")
  return n


def _check_depends(state: State, proposed: dict[str, list[str]], new: Item | None = None) -> None:
  """Refuse dependency lists that would leave state `check` rejects.

  `proposed` maps each item id being changed to its new `depends_on`; `new` is
  an item `add` has not appended yet. Every problem is collected before
  anything is written, the all-or-nothing rule import already follows. Edges
  the change does not touch are left alone, so a dangling edge or cycle that
  predates it still reaches `check` instead of blocking unrelated edits.
  """
  index = state.index
  after = replace(index, items=[
    replace(it, depends_on=proposed[it.id]) if it.id in proposed else it
    for it in index.items
  ] + ([new] if new is not None else []))
  problems: list[str] = []
  code = "state"
  dangling = {(item_id, dep) for item_id, dep in graph.dangling(after) if item_id in proposed}
  for item_id, deps in proposed.items():
    old = [] if new is not None and item_id == new.id else index.require(item_id).depends_on
    for dep in deps:
      if (item_id, dep) in dangling:
        code = "no_such_item"
        hint = ""
        if "," in dep or " " in dep:
          hint = "; --depends-on takes one id and is repeatable (--depends-on S1 --depends-on S2)"
        problems.append(f"{item_id}: depends on unknown id {dep!r}{hint}")
      elif dep == item_id:
        problems.append(f"{item_id}: cannot depend on itself")
      elif state.config.retired_status and after.require(dep).status == state.config.retired_status:
        problems.append(f"{item_id}: {dep} is retired and can never be done; restore it or drop the dependency")
      elif dep not in old:
        loop = graph.path(after, dep, item_id)
        if loop is not None:
          problems.append(f"{item_id}: depending on {dep} closes a cycle: " + " -> ".join([item_id] + loop))
  if problems:
    raise StateError("; ".join(problems) + ". Nothing was changed.", code=code)


def add(state: State, title: str, *, item_id: str | None = None, **fields: object) -> Item:
  """Append a roadmap entry. It has no slice file until it is promoted."""
  cfg = state.config
  _reject_bad_text(title=title, **fields)
  new_id = ids.allocate(state.index, item_id)
  # An omitted pass (None) inherits the previous item's; an explicit empty one
  # files the item with no pass, the same meaning `set --pass ''` has.
  pass_key = fields.get("pass_key")
  if pass_key is None:
    pass_key = state.index.items[-1].pass_key if state.index.items else ""
  item = Item(
    id=new_id,
    title=title,
    short_title=str(fields.get("short_title") or title),
    status=str(fields.get("status") or cfg.open_status),
    has_slice=False,
    size=str(fields.get("size") or ""),
    trees=_clean(list(fields.get("trees") or [])),
    findings=str(fields.get("findings") or ""),
    pass_key=str(pass_key),
    depends_on=_clean(list(fields.get("depends_on") or [])),
    importance=_valid_score("importance", fields["importance"]) if fields.get("importance") is not None else 2,
    urgency=_valid_score("urgency", fields["urgency"]) if fields.get("urgency") is not None else 2,
    effort=_valid_score("effort", fields["effort"]) if fields.get("effort") is not None else None,
  )
  if item.status not in cfg.statuses:
    raise StateError(f"unknown status {item.status!r}; known: {sorted(cfg.statuses)}")
  if item.depends_on:
    _check_depends(state, {item.id: item.depends_on}, new=item)
  state.index.items.append(item)
  state.save_index()
  _record(state, item.id, "add", to=item.status, note=title)
  return item


def promote(
  state: State,
  item_id: str,
  *,
  force: bool = False,
  source: str | None = None,
  source_path: str = "<promote>",
  boundary: str | None = None,
) -> Slice:
  """Give an item a slice file with the project's configured sections.

  With no `source` the sections are seeded empty, as they always were. With a
  `source` -- a one-item outline, the same shape `import` reads -- the entry's
  `###` sections and lead prose fill the slice in one call, so a fully-specified
  slice needs one command instead of a `promote` plus one `edit` per section.
  """
  cfg = state.config
  item = state.index.get(item_id)
  if item is None:
    raise StateError(f"no such item: {item_id}", code="no_such_item")
  if item.has_slice and not force:
    raise StateError(f"{item_id} already has a slice; pass --force to overwrite it")
  if source is not None:
    sl = _slice_from_source(source, item, cfg, source_path)
  else:
    sections = [Section(heading=h, body="") for h in cfg.sections]
    sl = Slice(
      id=item.id,
      title=item.title,
      findings_note=item.findings,
      size=item.size,
      flags=list(item.flags),
      trees_note=", ".join(item.trees),
      trees_plural=len(item.trees) > 1,
      sections=sections,
      boundary=cfg.boundary,
    )
  if boundary is not None:
    sl.boundary = boundary
  item.has_slice = True
  state.save_slice(sl)
  state.save_index()
  _record(state, item.id, "promote")
  return sl


def _slice_from_source(source: str, item: Item, cfg: object, path: str) -> Slice:
  """Parse a one-item outline into a populated slice for `promote`.

  The source reuses the outline grammar -- `##` names the item, `###` its
  sections -- so there is no second slice format to learn or maintain. The item
  already owns its title and fields, so the source is sections and lead only:
  its `##` title is ignored, and any item-level key is refused rather than
  silently dropped, pointing the writer at `add`/`set` where those belong.
  """
  parsed = outline.parse(source, path=path)
  if parsed.preamble:
    raise StateError(
      f"{path}: leading prose is the roadmap preamble, not part of one slice; "
      "set it with `slicer prose edit preamble`",
      code="bad_promote_source",
    )
  specs = parsed.items
  if len(specs) != 1:
    raise StateError(
      f"{path}: a promote source is one item, but found {len(specs)}; "
      "an item is a '## ' heading and its sections are '### '",
      code="bad_promote_source",
    )
  spec = specs[0]
  _reject_promote_source_keys(spec, path)
  if not spec.has_slice:
    raise StateError(
      f"{path}: the source has no sections; write them as '### ' headings, "
      "or omit --file/--stdin to seed empty ones",
      code="bad_promote_source",
    )
  return _slice_from_spec(spec, item, cfg)


# Item fields an outline entry can carry, paired with their neutral default. A
# promote source names none of them -- the item already has them -- so any that
# is set is a mistake worth naming rather than dropping.
_SOURCE_FIELD_DEFAULTS = {
  "size": "",
  "trees": [],
  "findings": "",
  "pass_key": "",
  "group": "",
  "status": "",
  "depends": [],
  "importance": 2,
  "urgency": 2,
  "effort": None,
}


def _reject_promote_source_keys(spec: object, path: str) -> None:
  for name, default in _SOURCE_FIELD_DEFAULTS.items():
    if getattr(spec, name) != default:
      raise StateError(
        f"{path}: a promote source cannot set {name!r}; the item already owns "
        "its fields -- set them with `add` or `set`",
        code="field_in_promote_source",
      )


def move(
  state: State,
  item_id: str,
  *,
  before: str | None = None,
  after: str | None = None,
  to: int | None = None,
) -> int:
  """Reorder the queue. Position is priority; nothing else changes."""
  index = state.index
  at = index.position(item_id)
  if before == item_id or after == item_id:
    return at + 1  # moving an item relative to itself is a no-op
  item = index.items.pop(at)
  if before is not None:
    target = index.position(before)
  elif after is not None:
    target = index.position(after) + 1
  elif to is not None:
    last = len(index.items) + 1  # 1-based position over the full queue (item is popped)
    if not 1 <= to <= last:
      index.items.insert(at, item)
      raise StateError(f"position {to} is out of range 1..{last}", code="usage")
    target = to - 1
  else:
    index.items.insert(at, item)
    raise StateError("move needs --before, --after or --to")
  index.items.insert(target, item)
  state.save_index()
  _record(state, item_id, "move", frm=str(at + 1), to=str(target + 1))
  return target + 1


def sort_queue(state: State, by: str = "score") -> int:
  """Reorder the whole queue by effective score, matching `list --sort score`.

  Stable, so equal scores keep their current order; overwrites the manual move
  order with priority order. Returns how many items changed position.
  """
  index = state.index
  before = [it.id for it in index.items]
  if by == "effort":
    index.items.sort(key=effort_rank)
  else:
    eff = graph.effective_scores(index)
    index.items.sort(key=lambda it: eff[it.id], reverse=True)
  moved = sum(a != it.id for a, it in zip(before, index.items))
  if moved:
    state.save_index()
    _record(state, "*", "sort", note=f"by {by} ({moved} moved)")
  return moved


def _relocate_slice(state: State, item_id: str) -> None:
  """Move a slice file to wherever its item's status now says it belongs.

  The folder is the status made visible on disk, so every path that changes a
  status has to come through here. It logs nothing: the caller owns the
  history, because `set` and `done` record the same move differently.
  """
  src = state.find_slice_file(item_id)
  if src is None:
    return
  dst = state.slice_path(item_id)
  if src != dst:
    state.move_slice(src, dst)


def _sync_slice(state: State, item: Item) -> None:
  """Push the fields a slice duplicates from its item back down onto it.

  `promote` seeds these five from the item and nothing kept them in step
  afterwards, so editing the roadmap row left the rendered slice showing the
  old value. `short_title` is not among them: the index cell and the slice H1
  differ deliberately, and only the H1 is the slice's own title.

  Only an explicit `set` syncs. `migrate` allows an imported index and slice
  to disagree and says so in its report; reconciling those is not this.
  """
  sl = state.slices.get(item.id)
  if sl is None:
    return
  wanted = {
    "title": item.title,
    "findings_note": item.findings,
    "size": item.size,
    "flags": list(item.flags),
    "trees_note": ", ".join(item.trees),
    "trees_plural": len(item.trees) > 1,
  }
  if all(getattr(sl, key) == value for key, value in wanted.items()):
    return
  for key, value in wanted.items():
    setattr(sl, key, value)
  state.save_slice(sl)


def _batch_items(state: State, item_ids: list[str]) -> list[Item]:
  if not item_ids:
    raise StateError("provide at least one item id", code="usage")
  return [state.index.require(item_id) for item_id in dict.fromkeys(item_ids)]


def set_fields_many(state: State, item_ids: list[str], **fields: object) -> list[Item]:
  """Validate the request before saving once; omitted fields stay unchanged."""
  items = _batch_items(state, item_ids)
  known = {"title", "short_title", "status", "size", "trees", "findings", "pass_key", "depends_on", "flags", "group", "importance", "urgency", "effort"}
  _reject_bad_text(**fields)
  values = {}
  for key, value in fields.items():
    if key not in known:
      raise StateError(f"unknown field {key!r}; known: {sorted(known)}")
    if value is None and key != "effort":
      raise StateError(f"{key} cannot be null", code="state")
    if key == "status" and value not in state.config.statuses:
      raise StateError(f"unknown status {value!r}; known: {sorted(state.config.statuses)}")
    elif key in ("importance", "urgency", "effort") and value is not None:
      value = _valid_score(key, value)
    values[key] = _clean(value)
  if "depends_on" in values:
    _check_depends(state, {item.id: list(values["depends_on"]) for item in items})

  transitions = []
  for item in items:
    previous = item.status
    changes = []
    for key, value in values.items():
      old = getattr(item, key)
      new = list(value) if isinstance(value, list) else value
      if old != new:
        changes.append(f"{key} {_fmt(old)}→{_fmt(new)}")
      setattr(item, key, new)
    if item.status == state.config.done_status and item.claim_owner:
      _clear_claim(item)
    moved = item.status != previous
    if moved:
      _relocate_slice(state, item.id)
    _sync_slice(state, item)
    transitions.append((item, previous, moved, changes))
  state.save_index()
  for item, previous, moved, changes in transitions:
    # Record what changed, not just which fields, so metadata history replays
    # from slicer; a no-op set falls back to the field names it was asked to set.
    note = "; ".join(changes) if changes else ",".join(sorted(values))
    _record(state, item.id, "set", frm=previous if moved else "",
            to=item.status if moved else "", note=note)
  return items


def set_fields(state: State, item_id: str, **fields: object) -> Item:
  return set_fields_many(state, [item_id], **fields)[0]


def render_gated(state: State, mutate: Callable[[], object]) -> tuple[object, int]:
  """Run `mutate`, render the proposed state, and only then let the change land.

  The mutation runs inside a staging transaction, so its saves and slice moves
  are buffered while the proposed state is rendered. A render failure discards
  the whole batch -- leaving both state and `render/` untouched -- and re-raises,
  so a change that cannot be rendered never lands. This is the shared way any
  mutation opts into "render must succeed first"; `done --render` uses it, and so
  does the CLI's `--strict`. Returns the mutation's result and the number of
  render files written.
  """
  with state.staged():
    result = mutate()
    expected = render.plan(state)
    # write_atomic, not write: a render write that fails partway restores the
    # render tree, so state (staged) and render/ roll back together as one unit.
    written = len(render.write_atomic(expected, state.render_dir, render.compare(expected, state.render_dir)))
  return result, written


def _clear_claim(item: Item) -> None:
  item.claim_owner = ""
  item.claim_at = ""


def set_status_many(state: State, item_ids: list[str], status: str, *, note: str = "") -> list[Item]:
  """Move a batch to one status, preserving no-op and per-item history semantics.

  Finishing clears a claim in the same save. A done item is never claimed.
  """
  items = _batch_items(state, item_ids)
  if status not in state.config.statuses:
    raise StateError(f"unknown status {status!r}; known: {sorted(state.config.statuses)}")
  finishing = status == state.config.done_status
  transitions = []
  for item in items:
    previous = item.status
    status_changed = previous != status
    clear = finishing and bool(item.claim_owner)
    if not status_changed and not clear:
      continue
    if status_changed:
      item.status = status
    if clear:
      _clear_claim(item)
    transitions.append((item, previous, status_changed))
  if transitions:
    for item, _previous, status_changed in transitions:
      if status_changed:
        _relocate_slice(state, item.id)
    state.save_index()
    for item, previous, status_changed in transitions:
      if status_changed:
        _record(state, item.id, "status", frm=previous, to=status, note=note)
  return items


def done_and_render(state: State, item_ids: list[str], *, note: str = "") -> tuple[list[Item], int]:
  """Render the proposed done state before moving slices or saving the index."""
  items, written = render_gated(
    state, lambda: set_status_many(state, item_ids, state.config.done_status, note=note)
  )
  return items, written  # type: ignore[return-value]


def set_status(state: State, item_id: str, status: str, *, note: str = "") -> Item:
  return set_status_many(state, [item_id], status, note=note)[0]


def done(state: State, item_id: str, *, note: str = "") -> Item:
  return set_status(state, item_id, state.config.done_status, note=note)


def park(state: State, item_id: str, *, note: str = "") -> Item:
  cfg = state.config
  if not cfg.parked_status:
    raise StateError("this project declares no parked status; set parked_status in config")
  return set_status(state, item_id, cfg.parked_status, note=note)


def unpark(state: State, item_id: str) -> Item:
  return set_status(state, item_id, state.config.open_status)


def start_many(state: State, item_ids: list[str], *, note: str = "") -> list[Item]:
  """Mark items started and claim any that do not already have an owner.

  The claim time is taken once here and stored on the item. A later render
  does not refresh it. Starting something already started and claimed writes
  nothing, so a repeated `start` stays a silent no-op.
  """
  cfg = state.config
  if not cfg.started_status:
    raise StateError("this project declares no started status; set started_status in config")
  items = _batch_items(state, item_ids)
  status = cfg.started_status
  owner = vcs.identity(state.root, cfg.claim_owner)
  at = _now()
  changed: list[tuple[Item, str, bool]] = []
  for item in items:
    previous = item.status
    status_changed = previous != status
    if item.claim_owner:
      claim_changed = False
    else:
      item.claim_owner = owner
      item.claim_at = at
      claim_changed = True
    if not status_changed and not claim_changed:
      continue
    if status_changed:
      item.status = status
    changed.append((item, previous, status_changed))
  if not changed:
    return items
  for item, _previous, status_changed in changed:
    if status_changed:
      _relocate_slice(state, item.id)
  state.save_index()
  for item, previous, status_changed in changed:
    if status_changed:
      _record(state, item.id, "status", frm=previous, to=status, note=note)
    else:
      _record(state, item.id, "claim", to=item.claim_owner, note=note)
  return items


def start(state: State, item_id: str, *, note: str = "") -> Item:
  return start_many(state, [item_id], note=note)[0]


def release_many(state: State, item_ids: list[str]) -> list[Item]:
  """Drop claims without changing status. An item with no claim is left alone."""
  items = _batch_items(state, item_ids)
  released: list[tuple[Item, str]] = []
  for item in items:
    if not item.claim_owner:
      continue
    owner = item.claim_owner
    _clear_claim(item)
    released.append((item, owner))
  if not released:
    return items
  state.save_index()
  for item, owner in released:
    _record(state, item.id, "release", frm=owner, to="")
  return items


def release(state: State, item_id: str) -> Item:
  return release_many(state, [item_id])[0]


def handoff_many(state: State, item_ids: list[str], *, note: str = "") -> list[Item]:
  """Mark started slices ready for review and clear their claims in one save.

  Review sits between implementation and completion: the item leaves `next`
  (which offers only open and started work) but stays unfinished, so its
  dependents stay blocked until `done`. A reviewer claims it with `start`.
  Every id is checked before anything is written. An item already in review
  with no claim is a no-op.
  """
  cfg = state.config
  if not cfg.review_status:
    raise StateError("this project declares no review status; set review_status in config")
  items = _batch_items(state, item_ids)
  problems: list[str] = []
  for item in items:
    if item.status not in (cfg.started_status, cfg.review_status):
      problems.append(
        f"{item.id} is {cfg.status_label(item.status)!r}, not started; "
        f"only started work can be handed off (run `slicer start {item.id}` first)"
      )
    elif not item.has_slice:
      problems.append(f"{item.id} has no slice to review; run `slicer promote {item.id}` first")
  if problems:
    raise StateError("; ".join(problems) + ". Nothing was changed.", code="state")
  changed: list[tuple[Item, str, str]] = []
  for item in items:
    if item.status == cfg.review_status and not item.claim_owner:
      continue
    previous, owner = item.status, item.claim_owner
    item.status = cfg.review_status
    _clear_claim(item)
    changed.append((item, previous, owner))
  if not changed:
    return items
  for item, previous, _owner in changed:
    if previous != item.status:
      _relocate_slice(state, item.id)
  state.save_index()
  for item, previous, owner in changed:
    detail = f"claim {owner} cleared" if owner else ""
    _record(state, item.id, "handoff", frm=previous, to=item.status,
            note="; ".join(part for part in (note, detail) if part))
  return items


def handoff(state: State, item_id: str, *, note: str = "") -> Item:
  return handoff_many(state, [item_id], note=note)[0]


@dataclass
class NextResult:
  item: Item | None
  blocked: list[tuple[str, list[str]]]
  unspecified: list[tuple[str, list[str]]] = field(default_factory=list)
  # Eligible items skipped because a sibling worktree has them in work.
  elsewhere: list[tuple[str, list[dict[str, str]]]] = field(default_factory=list)


def next_item(
  state: State, offset: int = 0,
  elsewhere: Mapping[str, list[dict[str, str]]] | None = None,
) -> NextResult:
  """The most critical startable item: highest effective score, unblocked.

  Started items precede open items, with effective score ordering each group.
  Offset skips currently eligible items without simulating their completion.

  Dependencies still hard-gate what is startable -- a blocked item is never
  returned, whatever its score or status -- so the score only orders the items
  that can actually be picked up. Stable sorting preserves manual queue order
  for ties.

  `elsewhere` maps item ids to the sibling worktrees that have them started or
  claimed (`store.in_work_elsewhere`). Such an item is skipped and reported,
  unless this checkout has it started or claimed too: local work wins, as it
  does in `list`. The caller supplies the map, so this stays free of git.
  """
  if offset < 0:
    raise StateError("next offset must be a nonnegative integer", code="usage")
  cfg = state.config
  # A whitelist, so parked, done, retired and any project-specific status stay
  # out. An empty started_status means the project has no start state, and the
  # set is just the open one.
  active = {cfg.open_status}
  if cfg.started_status:
    active.add(cfg.started_status)
  blocked: list[tuple[str, list[str]]] = []
  unspecified: list[tuple[str, list[str]]] = []
  skipped: list[tuple[str, list[dict[str, str]]]] = []
  started: list[Item] = []
  candidates: list[Item] = []
  for item in state.index.items:
    if item.status not in active:
      continue
    pending = graph.blocked_by(state.index, item, cfg.done_status)
    if pending:
      blocked.append((item.id, pending))
    missing = _missing_spec(state, item)
    if missing:
      unspecified.append((item.id, missing))
    if pending or missing:
      continue
    local = bool(item.claim_owner) or item.status == cfg.started_status
    if elsewhere and item.id in elsewhere and not local:
      skipped.append((item.id, elsewhere[item.id]))
      continue
    (started if item.status == cfg.started_status else candidates).append(item)
  if not started and not candidates:
    return NextResult(item=None, blocked=blocked, unspecified=unspecified, elsewhere=skipped)
  eff = graph.effective_scores(state.index)
  pool = (sorted(started, key=lambda it: -eff[it.id])
          + sorted(candidates, key=lambda it: -eff[it.id]))
  return NextResult(
    item=pool[offset] if offset < len(pool) else None,
    blocked=blocked,
    unspecified=unspecified,
    elsewhere=skipped,
  )


def _missing_spec(state: State, item: Item) -> list[str]:
  """Implement and Check headings with no text. A row with no slice is not this case."""
  sl = state.slices.get(item.id)
  if sl is None:
    return []
  missing: list[str] = []
  for heading in ("Implement", "Check"):
    section = sl.section(heading)
    if section is None or not section.body.strip():
      missing.append(heading)
  return missing


def edit_section(state: State, item_id: str, heading: str, body: str, *, append: bool = False) -> Slice:
  sl = state.slices.get(item_id)
  if sl is None:
    raise StateError(f"{item_id} has no slice; run `slicer promote {item_id}` first")
  section = sl.section(heading)
  if append:
    body = body.lstrip("\n")
    if not body:
      return sl
    previous = section.body.rstrip("\n") if section else ""
    if previous:
      body = previous + "\n\n" + body
  if section is None:
    sl.sections.append(Section(heading=heading, body=body))
  else:
    section.body = body
  state.save_slice(sl)
  _record(state, item_id, "edit", note=heading)
  return sl


def add_note(state: State, item_id: str, text: str) -> Item:
  """Append a dated note to the item itself — no slice required — visible in
  `show` and (once promoted) the rendered slice."""
  item = state.index.require(item_id)
  text = text.strip()
  if not text:
    raise StateError("a note cannot be blank", code="usage")
  item.notes.append(f"**{_now()[:10]}** — {text}")
  state.save_index()
  _record(state, item_id, "note", note=text.splitlines()[0][:60])
  return item


def _note_index(item: Item, index: int) -> None:
  if not 0 <= index < len(item.notes):
    raise StateError(f"{item.id} has no note {index}", code="usage")


def set_note(state: State, item_id: str, index: int, text: str) -> Item:
  """Replace one note verbatim (the caller edited the full dated paragraph)."""
  item = state.index.require(item_id)
  _note_index(item, index)
  item.notes[index] = text
  state.save_index()
  _record(state, item_id, "note", note="edit")
  return item


def remove_note(state: State, item_id: str, index: int) -> Item:
  """Drop one note."""
  item = state.index.require(item_id)
  _note_index(item, index)
  del item.notes[index]
  state.save_index()
  _record(state, item_id, "note", note="remove")
  return item


def edit_boundary(state: State, item_id: str, body: str) -> Slice:
  """Change scope independently of section edits."""
  state.index.require(item_id)
  sl = state.slices.get(item_id)
  if sl is None:
    raise StateError(f"{item_id} has no slice; run `slicer promote {item_id}` first", code="no_slice")
  sl.boundary = body
  state.save_slice(sl)
  _record(state, item_id, "edit", note="boundary")
  return sl


def edit_prose(state: State, ref: str, text: str) -> str:
  """Replace one roadmap prose block. Returns the text now stored."""
  prose.put(state.index, ref, text)
  state.save_index()
  _record(state, ref, "prose", note="edit")
  return prose.get(state.index, ref)


def add_pass(
  state: State, key: str, *, heading: str = "", after: str | None = None
) -> PassInfo:
  """Declare a new pass group, so items can be filed under it.

  A pass is normally opened before it has any slices, which is why the
  renderer takes its order from the declared list rather than from the
  items.
  """
  if state.index.pass_info(key) is not None:
    raise StateError(f"pass {key!r} already exists")
  info = PassInfo(key=key, heading=heading)
  if after is None:
    state.index.passes.append(info)
  else:
    if state.index.pass_info(after) is None:
      known = ", ".join(p.key for p in state.index.passes) or "none"
      raise StateError(f"no pass {after!r} to insert after; declared passes: {known}")
    at = [p.key for p in state.index.passes].index(after)
    state.index.passes.insert(at + 1, info)
  state.save_index()
  _record(state, f"pass.{key}", "prose", to=key, note="add-pass")
  return info


def drop_pass(state: State, key: str) -> None:
  """Remove an empty pass group. Refuses while anything still points at it."""
  if state.index.pass_info(key) is None:
    known = ", ".join(p.key for p in state.index.passes) or "none"
    raise StateError(f"no pass {key!r}; declared passes: {known}")
  members = [i.id for i in state.index.items if i.pass_key == key]
  if members:
    shown = ", ".join(members[:5]) + ("\u2026" if len(members) > 5 else "")
    raise StateError(
      f"pass {key!r} still has {len(members)} item(s): {shown}. "
      f"Move them first with `slicer set <id> --pass <other>`."
    )
  state.index.passes = [p for p in state.index.passes if p.key != key]
  state.save_index()
  _record(state, f"pass.{key}", "prose", frm=key, note="drop-pass")


@dataclass
class PurgeResult:
  """What a purge did, and whether the id came back."""

  id: str
  id_freed: bool
  reason: str
  file_removed: bool


def dependents(state: State, item_id: str) -> list[str]:
  """Items whose `depends_on` names this one."""
  return [i.id for i in state.index.items if item_id in i.depends_on]


def _blockers(state: State, item_id: str) -> list[str]:
  """Reasons not to remove this item, in the order worth reading."""
  item = state.index.get(item_id)
  if item is None:
    raise StateError(f"no such item: {item_id}", code="no_such_item")
  reasons: list[str] = []
  citing = dependents(state, item_id)
  if citing:
    reasons.append(f"{', '.join(citing)} depend(s) on it")
  if item.status == state.config.done_status:
    reasons.append("it is done; removing it hides landed work")
  return reasons


def _guard(state: State, item_id: str, action: str, force: bool) -> None:
  reasons = _blockers(state, item_id)
  if reasons and not force:
    raise StateError(f"cannot {action} {item_id}: " + "; ".join(reasons) + ". Pass --force.")


def retire(state: State, item_id: str, *, reason: str, force: bool = False) -> Item:
  """Mark an item obsolete, keeping its id and saying why.

  The id stays claimed and the row keeps rendering, because a commit or a
  review that cites it must still resolve to something that explains itself.
  """
  if not reason.strip():
    raise StateError("retiring needs a reason; pass --reason")
  cfg = state.config
  if not cfg.retired_status:
    raise StateError("this project declares no retired status; set retired_status in config")
  _guard(state, item_id, "retire", force)
  item = state.index.require(item_id)
  previous = item.status
  item.reason = reason.strip()

  item.status = cfg.retired_status
  _relocate_slice(state, item_id)
  state.save_index()
  _record(state, item_id, "retire", frm=previous, to=item.status, note=item.reason)
  return item


def _reclaim_check(state: State, item_id: str) -> tuple[bool, str]:
  """Whether purging `item_id` frees its id, and why. Read-only; independent of
  whether the item is still in the index, so a preview and the real purge agree."""
  number = ids.parse_id(item_id, state.index.id_prefix)
  if number is None:
    return False, f"{item_id} is not an allocated id"
  if number + 1 != state.index.next_id:
    return False, f"{item_id} is not the most recent id"
  cited = [s for s in vcs.subjects(state.root) if re.search(rf"\b{re.escape(item_id)}\b", s)]
  if cited:
    return False, f"{item_id} is named in {len(cited)} commit subject(s)"
  return True, "it was the most recent id and nothing refers to it"


@dataclass
class RemovePreview:
  """What `remove --dry-run` would do, computed without writing anything."""

  id: str
  mode: str
  blockers: list[str]
  file: str | None
  id_freed: bool = False
  reason: str = ""


def remove_preview(state: State, item_id: str, *, purge: bool) -> RemovePreview:
  """Report the effect of a purge/retire — dependents, id fate, slice file —
  without mutating anything, so a destructive removal is a decision not a surprise."""
  state.index.require(item_id)
  path = state.find_slice_file(item_id)
  blockers = _blockers(state, item_id)
  freed, why = _reclaim_check(state, item_id) if purge else (False, "")
  return RemovePreview(
    id=item_id, mode="purge" if purge else "retire", blockers=blockers,
    file=str(path) if path else None, id_freed=freed, reason=why,
  )


def purge(state: State, item_id: str, *, force: bool = False) -> PurgeResult:
  """Delete an item outright, for something that should never have existed.

  The id is only reclaimed when this was the last one allocated and nothing
  refers to it. Anything else keeps its id burned: reusing an identifier that
  has been published would make an old citation resolve to the wrong slice.
  """
  _guard(state, item_id, "purge", force)
  item = state.index.require(item_id)

  path = state.find_slice_file(item_id)

  state.index.items = [i for i in state.index.items if i.id != item_id]

  freed, why = _reclaim_check(state, item_id)
  if freed:
    state.index.next_id = ids.parse_id(item_id, state.index.id_prefix)

  # Index first, then the file: a crash between leaves at worst an orphan slice
  # file, never an index still naming a slice that has already been deleted.
  state.save_index()
  removed = False
  if path is not None:
    path.unlink()
    removed = True

  _record(state, item_id, "purge", frm=item.status, note=why)
  return PurgeResult(id=item_id, id_freed=freed, reason=why, file_removed=removed)


@dataclass
class OutlineReport:
  """What an outline would do, or did. Mirrors the migrate report's shape."""

  items: int = 0
  promoted: int = 0
  by_status: dict[str, int] = field(default_factory=dict)
  ids: list[str] = field(default_factory=list)
  depends_edges: int = 0
  off_schema_sections: dict[str, int] = field(default_factory=dict)
  warnings: list[str] = field(default_factory=list)
  problems: list[str] = field(default_factory=list)
  preamble: str | None = None

  def to_dict(self) -> dict[str, object]:
    return {
      "items": self.items,
      "promoted": self.promoted,
      "by_status": self.by_status,
      "ids": self.ids,
      "depends_edges": self.depends_edges,
      "off_schema_sections": self.off_schema_sections,
      "warnings": self.warnings,
      "problems": self.problems,
      "preamble": self.preamble,
    }


def _allocate_outline_ids(index: Index, count: int) -> list[str]:
  """Allocate one id per outline entry against the supplied index."""
  return [ids.allocate(index) for _ in range(count)]


def outline_report(
  state: State, specs: list[object], *, force: bool = False, promote_all: bool = False,
  preamble: str | None = None, replace_preamble: bool = False,
) -> OutlineReport:
  """What this outline says, and everything wrong with it. Writes nothing.

  `apply_outline` calls this first and refuses when `problems` is non-empty,
  and `--dry-run` calls it alone, so the census a person reads before
  applying is the same one produced by applying.
  """
  cfg = state.config
  report = OutlineReport()
  report.items = len(specs)
  report.promoted = sum(1 for spec in specs if promote_all or spec.has_slice)
  for spec in specs:
    status = spec.status or cfg.open_status
    report.by_status[status] = report.by_status.get(status, 0) + 1
    report.depends_edges += len(spec.depends)
    for section in spec.sections:
      if section.heading not in cfg.sections:
        report.off_schema_sections[section.heading] = (
          report.off_schema_sections.get(section.heading, 0) + 1
        )
  problems: list[str] = []

  titles = [s.title for s in specs]
  seen: set[str] = set()
  for title in titles:
    if title in seen:
      problems.append(f"{title!r} appears twice in the outline")
    seen.add(title)

  if not force:
    existing = {it.display_title(): it.id for it in state.index.items}
    existing.update({it.title: it.id for it in state.index.items})
    for title in dict.fromkeys(titles):
      if title in existing:
        problems.append(
          f"{title!r} already exists as {existing[title]}; pass --force to add it anyway"
        )

  known = set(titles) | {it.title for it in state.index.items}
  known |= {it.display_title() for it in state.index.items}
  for spec in specs:
    try:
      _reject_bad_text(title=spec.title, size=spec.size, findings=spec.findings,
                       pass_key=spec.pass_key, group=spec.group, trees=spec.trees)
      _valid_score("importance", spec.importance)
      _valid_score("urgency", spec.urgency)
    except StateError as exc:
      problems.append(f"{spec.title!r}: {exc}")
    if spec.status and spec.status not in cfg.statuses:
      problems.append(
        f"{spec.title!r}: unknown status {spec.status!r}; known: {sorted(cfg.statuses)}"
      )
    for dep in spec.depends:
      if dep not in known:
        problems.append(f"{spec.title!r}: depends on {dep!r}, which is not in the outline or the index")
  active = {cfg.open_status, cfg.started_status}
  unscored = [
    spec.title for spec in specs
    if (spec.status or cfg.open_status) in active
    and is_unscored(spec.importance, spec.urgency, spec.effort)
  ]
  if unscored:
    report.warnings.append(
      f"{len(unscored)} item(s) unscored (importance 2, urgency 2, no effort), so they rank "
      "on nothing; add importance:, urgency: and effort: keys: "
      + ", ".join(repr(t) for t in unscored)
    )
  stored_preamble = state.index.preamble
  if (
    preamble is not None and stored_preamble and stored_preamble != preamble
    and not force and not replace_preamble
  ):
    problems.append("preamble differs from the one already stored; pass --force to replace it")
  report.preamble = preamble
  report.problems = problems
  if not problems:
    report.ids = _allocate_outline_ids(deepcopy(state.index), len(specs))
  return report


class OutlineCommittedError(StateError):
  """The index was saved; callers must not retry adding the same batch."""

  def __init__(self, report: OutlineReport, error: OSError) -> None:
    super().__init__(f"items saved, but history could not be written: {error}", code="io")
    self.report = report


def apply_outline(
  state: State, specs: list[object], *, force: bool = False,
  preamble: str | None = None, promote_all: bool = False,
  replace_preamble: bool = False,
) -> OutlineReport:
  """Append every entry in a parsed outline, or write nothing at all.

  Validation failures write nothing. Items and an optional preamble update
  are staged together; slice/index write failures leave caller state intact.
  `promote_all` also creates slices for specs with no sections or lead.
  History follows the index commit, so a history failure raises
  OutlineCommittedError: the batch is saved and must not be retried.
  """
  cfg = state.config
  report = outline_report(
    state, specs, force=force, promote_all=promote_all, preamble=preamble,
    replace_preamble=replace_preamble,
  )
  if report.problems:
    return report

  # Publish the in-memory draft only after the index is saved. A failed
  # interactive save must leave the caller able to correct and retry it.
  original = state
  state = State(state.root, cfg, deepcopy(state.index), dict(state.slices),
                dict(state.slice_files))
  if preamble is not None:
    state.index.preamble = preamble

  # Titles resolve to ids only once every entry has one, so allocate first.
  by_title: dict[str, str] = {it.title: it.id for it in state.index.items}
  by_title.update({it.display_title(): it.id for it in state.index.items})
  allocated = list(zip(specs, _allocate_outline_ids(state.index, len(specs))))
  for spec, new_id in allocated:
    by_title[spec.title] = new_id

  # Build every item and slice against the staged index before writing.
  pending: list[Slice] = []
  for spec, new_id in allocated:
    status = spec.status or cfg.open_status
    item = Item(
      id=new_id,
      title=spec.title,
      short_title=spec.title,
      status=status,
      size=spec.size,
      trees=list(spec.trees),
      findings=spec.findings,
      # Never inherited from the previous item: an outline says where its own
      # entries belong, and silently filing them under the tail item's pass
      # would be wrong in exactly the case bulk loading is for.
      pass_key=spec.pass_key,
      group=spec.group,
      depends_on=[by_title[d] for d in spec.depends],
      importance=spec.importance,
      urgency=spec.urgency,
      effort=spec.effort,
    )
    state.index.items.append(item)

    if promote_all or spec.has_slice:
      item.has_slice = True
      pending.append(_slice_from_spec(spec, item, cfg))

  # Flush: slice files first, the index last, so a crash never leaves the index
  # naming a slice that is not there. If a slice write fails, unwind the ones
  # already written and leave the index untouched -- all or nothing.
  written: list[Path] = []
  try:
    for sl in pending:
      written.append(state.save_slice(sl))
    state.save_index()
  except BaseException:
    for path in written:
      path.unlink(missing_ok=True)
    raise
  original.index, original.slices = state.index, state.slices
  try:
    for spec, new_id in allocated:
      _record(state, new_id, "add", to=spec.status or cfg.open_status, note=spec.title)
  except OSError as exc:
    raise OutlineCommittedError(report, exc) from exc
  return report


def _slice_from_spec(spec: object, item: Item, cfg: object) -> Slice:
  """Build a slice from an outline entry, filling in the configured sections.

  Configured headings the outline did not name are kept, empty, so a
  slice created this way has the same shape as a promoted one.
  """
  supplied = {s.heading: s.body for s in spec.sections}
  # Configured headings first, in the project's own order, so a slice made
  # from an outline has the same shape as a promoted one however the outline
  # happened to order them. Headings the config does not name follow.
  order = [h for h in cfg.sections]
  order += [s.heading for s in spec.sections if s.heading not in cfg.sections]
  sections = [Section(heading=h, body=supplied.get(h, "")) for h in order]
  boundary = extract_boundary(sections, cfg.boundary) or cfg.boundary
  return Slice(
    id=item.id,
    title=item.title,
    lead=list(spec.lead),
    findings_note=item.findings,
    size=item.size,
    flags=list(item.flags),
    trees_note=", ".join(item.trees),
    trees_plural=len(item.trees) > 1,
    sections=sections,
    boundary=boundary,
  )
