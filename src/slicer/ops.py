"""Command semantics, shared by the CLI and the TUI.

Every mutation goes through here so the two front ends cannot drift apart,
and so each one records the same log entry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from slicer import graph, ids, prose, vcs
from slicer.errors import StateError
from slicer.model import Item, LogEntry, PassInfo, Section, Slice
from slicer.store import State


def _now() -> str:
  return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _record(state: State, item: str, action: str, frm: str = "", to: str = "", note: str = "") -> None:
  state.log(LogEntry(when=_now(), item=item, action=action, frm=frm, to=to, note=note))


def add(state: State, title: str, *, item_id: str | None = None, **fields: object) -> Item:
  """Append a roadmap entry. It has no slice file until it is promoted."""
  cfg = state.config
  new_id = ids.allocate(state.index, item_id)
  item = Item(
    id=new_id,
    title=title,
    short_title=str(fields.get("short_title") or title),
    status=str(fields.get("status") or cfg.open_status),
    has_slice=False,
    size=str(fields.get("size") or ""),
    trees=list(fields.get("trees") or []),
    findings=str(fields.get("findings") or ""),
    pass_key=str(fields.get("pass_key") or (state.index.items[-1].pass_key if state.index.items else "")),
    depends_on=list(fields.get("depends_on") or []),
  )
  if item.status not in cfg.statuses:
    raise StateError(f"unknown status {item.status!r}; known: {sorted(cfg.statuses)}")
  state.index.items.append(item)
  state.save_index()
  _record(state, item.id, "add", to=item.status, note=title)
  return item


def promote(state: State, item_id: str, *, force: bool = False) -> Slice:
  """Give an item a slice file with the project's configured sections."""
  cfg = state.config
  item = state.index.get(item_id)
  if item is None:
    raise StateError(f"no such item: {item_id}")
  if item.has_slice and not force:
    raise StateError(f"{item_id} already has a slice; pass --force to overwrite it")
  sections = [Section(heading=h, body="") for h in cfg.sections]
  if sections and cfg.boundary:
    sections[-1].body = f"{cfg.boundary}"
  sl = Slice(
    id=item.id,
    title=item.title,
    findings_note=item.findings,
    size=item.size,
    flags=list(item.flags),
    trees_note=", ".join(item.trees),
    trees_plural=len(item.trees) > 1,
    sections=sections,
  )
  item.has_slice = True
  state.save_slice(sl)
  state.save_index()
  _record(state, item.id, "promote")
  return sl


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
  item = index.items.pop(at)
  if before is not None:
    target = index.position(before)
  elif after is not None:
    target = index.position(after) + 1
  elif to is not None:
    target = max(0, min(to - 1, len(index.items)))
  else:
    index.items.insert(at, item)
    raise StateError("move needs --before, --after or --to")
  index.items.insert(target, item)
  state.save_index()
  _record(state, item_id, "move", frm=str(at + 1), to=str(target + 1))
  return target + 1


def set_fields(state: State, item_id: str, **fields: object) -> Item:
  cfg = state.config
  item = state.index.get(item_id)
  if item is None:
    raise StateError(f"no such item: {item_id}")
  known = {"title", "short_title", "status", "size", "trees", "findings", "pass_key", "depends_on", "flags"}
  for key, value in fields.items():
    if value is None:
      continue
    if key not in known:
      raise StateError(f"unknown field {key!r}; known: {sorted(known)}")
    if key == "status" and value not in cfg.statuses:
      raise StateError(f"unknown status {value!r}; known: {sorted(cfg.statuses)}")
    setattr(item, key, value)
  state.save_index()
  _record(state, item_id, "set", note=",".join(sorted(k for k, v in fields.items() if v is not None)))
  return item


def set_status(state: State, item_id: str, status: str, *, note: str = "") -> Item:
  """Change status, moving the slice file when it crosses the done boundary."""
  cfg = state.config
  item = state.index.get(item_id)
  if item is None:
    raise StateError(f"no such item: {item_id}")
  if status not in cfg.statuses:
    raise StateError(f"unknown status {status!r}; known: {sorted(cfg.statuses)}")
  previous = item.status
  if previous == status:
    return item

  src = state.find_slice_file(item_id)
  item.status = status
  if src is not None:
    dst = state.slice_path(item_id)
    if src != dst:
      vcs.move(state.root, src, dst)
  state.save_index()
  _record(state, item_id, "status", frm=previous, to=status, note=note)
  return item


def done(state: State, item_id: str, *, note: str = "") -> Item:
  return set_status(state, item_id, state.config.done_status, note=note)


def park(state: State, item_id: str, status: str = "parked") -> Item:
  return set_status(state, item_id, status)


def unpark(state: State, item_id: str) -> Item:
  return set_status(state, item_id, state.config.open_status)


@dataclass
class NextResult:
  item: Item | None
  blocked: list[tuple[str, list[str]]]


def next_item(state: State) -> NextResult:
  """First open item whose dependencies are all finished."""
  cfg = state.config
  blocked: list[tuple[str, list[str]]] = []
  for item in state.index.items:
    if item.status != cfg.open_status:
      continue
    pending = graph.blocked_by(state.index, item, cfg.done_status)
    if pending:
      blocked.append((item.id, pending))
      continue
    return NextResult(item=item, blocked=blocked)
  return NextResult(item=None, blocked=blocked)


def edit_section(state: State, item_id: str, heading: str, body: str) -> Slice:
  sl = state.slices.get(item_id)
  if sl is None:
    raise StateError(f"{item_id} has no slice; run `slicer promote {item_id}` first")
  section = sl.section(heading)
  if section is None:
    sl.sections.append(Section(heading=heading, body=body))
  else:
    section.body = body
  state.save_slice(sl)
  _record(state, item_id, "edit", note=heading)
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
    raise StateError(f"no such item: {item_id}")
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

  src = state.find_slice_file(item_id)
  item.status = cfg.retired_status
  if src is not None:
    dst = state.slice_path(item_id)
    if src != dst:
      vcs.move(state.root, src, dst)
  state.save_index()
  _record(state, item_id, "retire", frm=previous, to=item.status, note=item.reason)
  return item


def purge(state: State, item_id: str, *, force: bool = False) -> PurgeResult:
  """Delete an item outright, for something that should never have existed.

  The id is only reclaimed when this was the last one allocated and nothing
  refers to it. Anything else keeps its id burned: reusing an identifier that
  has been published would make an old citation resolve to the wrong slice.
  """
  _guard(state, item_id, "purge", force)
  item = state.index.require(item_id)

  path = state.find_slice_file(item_id)
  removed = False
  if path is not None:
    path.unlink()
    removed = True

  state.index.items = [i for i in state.index.items if i.id != item_id]

  number = ids.parse_id(item_id, state.index.id_prefix)
  freed, why = False, ""
  if number is None:
    why = f"{item_id} is not an allocated id"
  elif number + 1 != state.index.next_id:
    why = f"{item_id} is not the most recent id"
  else:
    cited = [s for s in vcs.subjects(state.root) if re.search(rf"\b{re.escape(item_id)}\b", s)]
    if cited:
      why = f"{item_id} is named in {len(cited)} commit subject(s)"
    else:
      state.index.next_id = number
      freed, why = True, "it was the most recent id and nothing refers to it"

  state.save_index()
  _record(state, item_id, "purge", frm=item.status, note=why)
  return PurgeResult(id=item_id, id_freed=freed, reason=why, file_removed=removed)
