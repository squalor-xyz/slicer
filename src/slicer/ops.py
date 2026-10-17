"""Command semantics, shared by the CLI and the TUI.

Every mutation goes through here so the two front ends cannot drift apart,
and so each one records the same log entry.
"""

from __future__ import annotations

import errno
import json
import os
import re
import shlex
from contextlib import ExitStack, contextmanager
from uuid import uuid4
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Callable, Iterator, Mapping

from slicer import (
  feedback, graph, ids, jsonio, outline, prose, refs, relayrun, render, roles, store, templates,
  vcs, verify,
)
from slicer.config import CONFIG_NAME, Config
from slicer.errors import ConfigError, RenderError, StateError
from slicer.model import (
  CATALOG_ACTIVE, CATALOG_RETIRED, CatalogRecord, Index, Item, LogEntry, NoteRecord,
  PassInfo, Section, Slice, canonical_kind, effort_rank, extract_boundary, is_unscored,
  successor_would_cycle,
)
from slicer.roles import RoleMatrix, RoleRow, RoleRule, RoleWhen
from slicer.store import State


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
render/RELAY-ROLES.md merge=slicer-generated

# index.json is merged by the slicer-index driver, which matches items by id and
# fields by name: independent edits merge, next_id takes the larger value so ids are
# never reused, and only a true disagreement stays a conflict. Also defined once per clone:
#   git config merge.slicer-index.name "keep the larger next_id when merging the index"
#   git config merge.slicer-index.driver "slicer merge-index %O %A %B"
index.json merge=slicer-index

# relay-roles.json is merged by the slicer-relay-roles driver, which matches rows and
# rules by id: independent edits merge, a row or rule both sides added under one id
# keeps both with a fresh id for the side merged in, the id counters move past every id
# issued, and a same-field disagreement is refused with the file left as it was. Also
# defined once per clone:
#   git config merge.slicer-relay-roles.name "keep both sides' relay roles rows and rules with fresh ids"
#   git config merge.slicer-relay-roles.driver "slicer merge-relay-roles %O %A %B"
relay-roles.json merge=slicer-relay-roles
"""


# The lines `init` writes to `.slicer/.gitignore`: files slicer makes that git must not track.
GITIGNORE_LINES = (
  feedback.LOG_NAME, feedback.SIDECAR_NAME, relayrun.LEDGER_NAME, store.LOCK_NAME,
)


def write_gitattributes(root: Path) -> tuple[Path, bool]:
  """Write `.slicer/.gitattributes` from the template, and only when its bytes differ.

  `init` and `setup-git --attributes` share it, so a project that predates a template
  line can take it without `init --force`, which also rewrites config and templates.
  Returns the path and whether this call changed it. The caller holds the project lock.
  """
  path = root / store.DIR_NAME / store.GITATTRIBUTES_NAME
  data = GITATTRIBUTES.encode("utf-8")
  try:
    current: bytes | None = path.read_bytes()
  except FileNotFoundError:
    current = None
  if current == data:
    return path, False
  jsonio.write_bytes(path, data)
  return path, True


def init_project(
  root: Path, *, force: bool = False, id_prefix: str | None = None,
  id_width: int | None = None, starting_id: str | None = None,
) -> Path:
  """Create tracking files only after the proposed id scheme is validated.

  Forced initialization resets config and templates, but retains an existing
  index's scheme and counter unless explicitly changed on an empty queue.
  """
  base = root / store.DIR_NAME
  if (base / CONFIG_NAME).exists() and not force:
    raise StateError(
      f"{base} already exists; pass --force to overwrite its config and templates",
      code="already_exists",
    )
  index_path = base / store.INDEX_NAME
  existing = Index.from_dict(jsonio.read(index_path)) if index_path.is_file() else None
  cfg = Config()
  if existing is not None:
    cfg.id_prefix, cfg.id_width = existing.id_prefix, existing.id_width
  if id_prefix is not None:
    cfg.id_prefix = id_prefix
  if id_width is not None:
    cfg.id_width = id_width
  try:
    # Loading supplies compatibility defaults for the bootstrap config's
    # status vocabulary; validate the same representation readers will see.
    Config.from_dict(cfg.to_dict())
  except ConfigError as exc:
    # Invalid CLI choices are usage errors, not an unreadable stored config.
    if id_prefix is not None or id_width is not None:
      raise StateError(str(exc), code="usage") from exc
    raise
  scheme_changed = existing is not None and (
    cfg.id_prefix, cfg.id_width
  ) != (existing.id_prefix, existing.id_width)
  if scheme_changed and existing.items:
    raise StateError(
      f"{base} already has items; its id prefix and width cannot change. "
      "Use `id-prefix` for a case-only change, or initialize a new queue",
      code="state",
    )
  next_id = None
  if starting_id is not None:
    next_id = ids.starting_number(starting_id, cfg.id_prefix, cfg.id_width)
    if existing is not None and existing.items:
      raise StateError(
        f"{base} already has items; a starting id would move the counter over live ids. "
        "Use `add --id` instead",
        code="state",
      )
  index = existing if existing is not None else Index()
  index.id_prefix, index.id_width = cfg.id_prefix, cfg.id_width
  if next_id is not None:
    index.next_id = next_id
  jsonio.write(base / CONFIG_NAME, cfg.to_dict())
  if existing is None or scheme_changed or next_id is not None:
    jsonio.write(index_path, index.to_dict())
  for name, content in templates.defaults().items():
    jsonio.write_text(base / store.TEMPLATES_DIR / name, content)
  write_gitattributes(root)
  feedback.add_gitignore_lines(root, GITIGNORE_LINES)
  (base / store.SLICES_DIR / cfg.done_dir).mkdir(parents=True, exist_ok=True)
  return base


def _now() -> str:
  return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _record(
  state: State, item: str, action: str, frm: str = "", to: str = "", note: str = "", by: str = ""
) -> None:
  state.log(LogEntry(when=_now(), item=item, action=action, frm=frm, to=to, note=note, by=by))


def actor(state: State, override: str | None = None) -> str:
  """Who a claim or lifecycle action names; see `vcs.identity` for the order."""
  return vcs.identity(state.root, state.config.claim_owner, override)


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
      elif (
        state.config.retired_status
        and state.config.retired_status not in state.config.satisfying_statuses()
        and after.require(dep).status == state.config.retired_status
        and after.require(item_id).status not in {
          status for status in (state.config.retired_status, state.config.done_status) if status
        }
      ):
        problems.append(f"{item_id}: {dep} is retired and can never be done; restore it or drop the dependency")
      elif dep not in old:
        loop = graph.path(after, dep, item_id)
        if loop is not None:
          problems.append(f"{item_id}: depending on {dep} closes a cycle: " + " -> ".join([item_id] + loop))
  if problems:
    raise StateError("; ".join(problems) + ". Nothing was changed.", code=code)


def filing_key(value: object) -> str:
  """Normalize an explicitly supplied filing identity."""
  if value is None:
    return ""
  if not isinstance(value, str) or not value.strip():
    raise StateError("key cannot be blank; omit it for an unkeyed item", code="blank_key")
  return value.strip()


def keyed_item(state: State, key: str) -> Item | None:
  """Look up identity across every status in this checkout."""
  return next((item for item in state.index.items if key and item.key == key), None)


def slivers_of(state: State, item_id: str) -> list[Item]:
  """Items whose `parent` names this one, in index order."""
  return [i for i in state.index.items if i.parent == item_id]


def _check_parent(state: State, item_id: str | None, parent: str) -> None:
  """Refuse a parent that would break the one-level rule. Empty clears, so it passes.

  `item_id` is None while an item is being filed: it has no id yet and no slivers.
  """
  if not parent:
    return
  if parent == item_id:
    raise StateError(
      f"{item_id} cannot be its own parent; give it the slice it is a sliver of, "
      f"or clear it with `set {item_id} --parent ''`", code="parent_self",
    )
  target = state.index.require(parent)
  if target.parent:
    raise StateError(
      f"{parent} is itself a sliver of {target.parent}, so it cannot be a parent; "
      f"give {item_id or 'the new item'} {target.parent} as its parent instead, or clear "
      f"{parent}'s parent with `set {parent} --parent ''`", code="parent_nested",
    )
  kids = slivers_of(state, item_id) if item_id else []
  if kids:
    raise StateError(
      f"{item_id} has slivers ({', '.join(k.id for k in kids)}), so it cannot be a sliver "
      f"itself; clear their parents with `set {' '.join(k.id for k in kids)} --parent ''` "
      "first, or leave it without a parent", code="parent_nested",
    )


def _refuse_id_taken_elsewhere(state: State, item_id: str) -> None:
  """Refuse an explicit id that a sibling checkout filed itself, not inherited.

  Two checkouts that each file a new item under one id collide on merge, and
  `check` warns only after the write. Refusing here keeps the write from
  happening. An id both checkouts inherited from their merge base is one item,
  not a clash, so it is left to `ids.allocate`, which refuses it as present.
  """
  if not vcs.is_repo(state.root):
    return
  ours_head = vcs.resolve(state.root, "HEAD")
  for path, sibling in store.sibling_checkouts(state.root):
    folded = item_id.casefold()
    theirs = next((it for it in sibling.items if it.id.casefold() == folded), None)
    if theirs is None:
      continue
    inherited = {value.casefold() for value in verify.inherited_ids(state, ours_head, path)}
    if folded in inherited:
      continue
    if theirs.id != item_id:
      raise StateError(
        f"id {item_id} differs only in case from {theirs.id}, filed in worktree "
        f"{path.name} as {theirs.title!r}; on a case-insensitive filesystem they "
        "would be the same file. Use `slicer next-id` for a free id, or leave out --id",
        code="case_collision",
      )
    raise StateError(
      f"id {item_id} is already filed in worktree {path.name} as {theirs.title!r}, and this "
      "checkout did not inherit it from their merge base, so the two would collide on merge. "
      "Use `slicer next-id` for a free id, or leave out --id",
      code="id_taken_elsewhere",
    )


def add(
  state: State, title: str, *, item_id: str | None = None, id_floor: int = 0,
  **fields: object,
) -> Item:
  """Append a roadmap entry. It has no slice file until it is promoted."""
  cfg = state.config
  key = filing_key(fields.get("key"))
  existing = keyed_item(state, key)
  if existing is not None:
    return existing
  _reject_bad_text(title=title, **fields)
  discovered_from = fields.get("discovered_from") or ""
  if discovered_from:
    state.index.require(discovered_from)
  parent = str(fields.get("parent") or "")
  _check_parent(state, None, parent)
  if item_id is not None:
    _refuse_id_taken_elsewhere(state, item_id)
  new_id = ids.allocate(state.index, item_id, floor=id_floor)
  # An omitted pass (None) inherits the previous item's; an explicit empty one
  # files the item with no pass, the same meaning `set --pass ''` has.
  pass_key = fields.get("pass_key")
  if pass_key is None:
    pass_key = state.index.items[-1].pass_key if state.index.items else ""
  item = Item(
    id=new_id,
    key=key,
    title=title,
    short_title=str(fields.get("short_title") or title),
    status=str(fields.get("status") or cfg.open_status),
    has_slice=False,
    size=str(fields.get("size") or ""),
    trees=_clean(list(fields.get("trees") or [])),
    findings=str(fields.get("findings") or ""),
    discovered_from=discovered_from,
    parent=parent,
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


def _refuse_catalog_prefix(index: Index) -> None:
  """Catalog ids and item ids are different sequences and must not share a prefix."""
  if index.catalog.id_prefix.casefold() == index.id_prefix.casefold():
    raise StateError(
      f"catalog id prefix {index.catalog.id_prefix!r} matches the item prefix "
      f"{index.id_prefix!r}; catalog ids would collide with item ids",
      code="state",
    )


def _refuse_catalog_case(index: Index, new_id: str) -> None:
  folded = new_id.casefold()
  clash = next((item.id for item in index.items if item.id.casefold() == folded), None)
  if clash is None:
    clash = next(
      (record.id for record in index.catalog.records if record.id.casefold() == folded),
      None,
    )
  if clash is not None:
    raise StateError(
      f"id {new_id} differs from {clash} only in case; on a case-insensitive "
      f"filesystem they would be the same file",
      code="case_collision",
    )


def _allocate_catalog(index: Index, *, floor: int = 0) -> str:
  """The next catalog id. The counter only moves forward, and an id is never reused."""
  _refuse_catalog_prefix(index)
  catalog = index.catalog
  number = max(catalog.next_id, floor)
  new_id = ids.format_id(catalog.id_prefix, number, catalog.id_width)
  ids.require_valid(new_id)
  if catalog.get(new_id) is not None:
    raise StateError(
      f"next_id ({number}) would reuse the existing catalog id {new_id}; the "
      f"index is inconsistent -- run `slicer verify`",
      code="corrupt",
    )
  _refuse_catalog_case(index, new_id)
  catalog.next_id = number + 1
  return new_id


def _active_record(index: Index, record_id: str) -> CatalogRecord:
  record = index.catalog.require(record_id)
  if record.status != CATALOG_ACTIVE:
    raise StateError(
      f"{record.id} is retired; a retired record stays as it was", code="state",
    )
  return record


def catalog_add(
  state: State, kind: str, title: str, body: str = "", *, id_floor: int = 0,
) -> CatalogRecord:
  """Append one catalog record. `kind` accepts `non-goal` as well as `non_goal`."""
  stored = canonical_kind(kind)
  _reject_bad_text(title=title)
  record = CatalogRecord(
    id=_allocate_catalog(state.index, floor=id_floor),
    kind=stored,
    title=title,
    body=body,
  )
  state.index.catalog.records.append(record)
  state.save_index()
  _record(state, record.id, "catalog", note="add", to=title)
  return record


def catalog_edit(
  state: State, record_id: str, *, title: str | None = None, body: str | None = None,
) -> CatalogRecord:
  """Replace the title, the body, or both. A retired record is frozen."""
  record = _active_record(state.index, record_id)
  if title is None and body is None:
    return record
  _reject_bad_text(title=title)
  changed: list[str] = []
  if title is not None and title != record.title:
    record.title = title
    changed.append("title")
  if body is not None and body != record.body:
    record.body = body
    changed.append("body")
  if not changed:
    return record
  state.save_index()
  _record(state, record.id, "catalog", note="edit", to=",".join(changed))
  return record


def catalog_retire(
  state: State, record_id: str, reason: str, successor: str = "",
) -> CatalogRecord:
  """Retire a record in place. The id stays, and the statement freezes."""
  record = _active_record(state.index, record_id)
  if not str(reason).strip():
    raise StateError("a retire reason cannot be blank", code="usage")
  if "\n" in reason:
    raise StateError("reason cannot contain a newline", code="newline_in_field")
  successor = successor.strip()
  if successor:
    other = state.index.catalog.require(successor)
    if other.kind != record.kind:
      raise StateError(
        f"{other.id} is a {other.kind}; a successor has to be a {record.kind}",
        code="state",
      )
    if successor_would_cycle(state.index.catalog.records, record.id, successor):
      raise StateError(
        f"successor {successor} would cycle back to {record.id}", code="state",
      )
  record.status = CATALOG_RETIRED
  record.reason = reason.strip()
  record.successor = successor
  state.save_index()
  _record(state, record.id, "catalog", frm=CATALOG_ACTIVE, to=CATALOG_RETIRED, note="retire")
  return record


def catalog_move(
  state: State, record_id: str, *, before: str | None = None, after: str | None = None,
) -> CatalogRecord:
  """Move a record within its kind. Other kinds keep their relative order."""
  if (before is None) == (after is None):
    raise StateError("choose exactly one of --before or --after", code="usage")
  catalog = state.index.catalog
  record = catalog.require(record_id)
  anchor_id = before if before is not None else after
  if anchor_id is None:
    raise StateError("choose exactly one of --before or --after", code="usage")
  anchor = catalog.require(anchor_id)
  if record.id == anchor.id:
    raise StateError("a record cannot be moved relative to itself", code="usage")
  if record.kind != anchor.kind:
    raise StateError(
      f"{record.id} is a {record.kind} and {anchor.id} is a {anchor.kind}; "
      f"move stays inside one kind",
      code="state",
    )
  kind_ids = [item.id for item in catalog.records if item.kind == record.kind]
  old_at = kind_ids.index(record.id) + 1
  rest = [item for item in catalog.records if item.id != record.id]
  index = next(i for i, item in enumerate(rest) if item.id == anchor.id)
  insert_at = index if before is not None else index + 1
  rest.insert(insert_at, record)
  new_at = [item.id for item in rest if item.kind == record.kind].index(record.id) + 1
  if [item.id for item in rest] == [item.id for item in catalog.records]:
    return record
  catalog.records = rest
  state.save_index()
  _record(state, record.id, "catalog", frm=str(old_at), to=str(new_at), note="move")
  return record


def _require_records(state: State, record_ids: list[str]) -> None:
  """Refuse before any write when one id is not a catalog record."""
  for record_id in record_ids:
    state.index.catalog.require(record_id)


def catalog_cite(state: State, item_id: str, record_ids: list[str]) -> Item:
  """Append catalog ids the item does not already cite. An unknown id writes nothing."""
  item = state.index.require(item_id)
  _require_records(state, record_ids)
  seen = set(item.cites)
  added: list[str] = []
  for record_id in record_ids:
    if record_id in seen:
      continue
    seen.add(record_id)
    added.append(record_id)
  if not added:
    return item
  item.cites.extend(added)
  state.save_index()
  _record(state, item.id, "cite", to=",".join(added))
  return item


def catalog_uncite(state: State, item_id: str, record_ids: list[str]) -> Item:
  """Drop cited ids. An unknown id, or one the item does not cite, writes nothing."""
  item = state.index.require(item_id)
  _require_records(state, record_ids)
  missing = [record_id for record_id in record_ids if record_id not in item.cites]
  if missing:
    raise StateError(f"{item.id} does not cite {missing[0]}", code="state")
  dropping = set(record_ids)
  item.cites = [record_id for record_id in item.cites if record_id not in dropping]
  state.save_index()
  _record(state, item.id, "uncite", frm=",".join(record_ids))
  return item


def issue_key(repo: str, number: int) -> str:
  """The filing key `issues-pull` gives the row for one GitHub issue."""
  return f"github:{repo}#{number}"


def pull_issues(
  state: State, repo: str, issues: list[dict[str, object]], *, write: bool,
) -> list[dict[str, object]]:
  """File one open row per issue whose key is new; reuse the rest in any status.

  Every issue is checked before anything is added, so a bad one refuses the
  whole pull. With `write` false nothing is added and a new row's `item` is
  None. Rows land unscored and with no pass, in ascending issue number, and
  findings is the issue URL; the issue body never reaches this function.
  """
  seen: set[int] = set()
  checked: list[tuple[int, str, str]] = []
  for issue in issues:
    number, title, url = issue["number"], issue["title"], issue["url"]
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
      raise StateError(f"gh listed an issue whose number {number!r} is not a positive integer; "
        "nothing was filed", code="external")
    if number in seen:
      raise StateError(f"gh listed issue #{number} twice; nothing was filed", code="external")
    seen.add(number)
    if url != f"https://github.com/{repo}/issues/{number}":
      raise StateError(f"issue #{number} has URL {url!r}, not an issue URL on {repo}; "
        "nothing was filed", code="external")
    if not isinstance(title, str):
      raise StateError(f"issue #{number} has a title that is not text; nothing was filed",
        code="external")
    try:
      _reject_bad_text(title=title.strip(), findings=url)
    except StateError as exc:
      raise StateError(f"issue #{number}: {exc}; nothing was filed", code=exc.code) from None
    checked.append((number, title.strip(), url))
  pulled: list[dict[str, object]] = []
  for number, title, url in sorted(checked):
    key = issue_key(repo, number)
    item = keyed_item(state, key)
    existing = item is not None
    if item is None and write:
      item = add(state, title, key=key, findings=url, pass_key="")
    pulled.append({"number": number, "title": item.title if existing else title,
                   "key": key, "item": item, "existing": existing})
  return pulled


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
  "key": "",
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


def set_id_prefix(state: State, new: str) -> tuple[str, bool]:
  """Change the prefix new ids get, in the index and the config together.

  Only the case can change (`ids.check_prefix_change`), so every existing id
  stays in the scheme and none is renamed. Returns the old prefix and whether
  anything was written. Running it again when the config lags the index (a
  hand edit, or a crash between the two writes) brings the config back in step.
  """
  ids.check_prefix_change(state.index, new)
  old = state.index.id_prefix
  if old == new and state.config.id_prefix == new:
    return old, False
  state.index.id_prefix = new
  state.save_index()
  state.save_config_prefix(new)
  _record(state, "*", "id_prefix", frm=old, to=new)
  return old, True


# ---- config ----------------------------------------------------------------

CONFIG_ACTION = "config"
# The fixed-shape objects a dotted key reaches into. A free-form object
# (`statuses`, `pointers.later`) is set whole, as JSON.
_CONFIG_OBJECTS = ("id", "pointers", "sync")
# `sync.targets` holds objects; every other list in the config holds strings.
_CONFIG_OBJECT_LISTS = ("sync.targets",)
_UNSET_TEXT = "(unset)"


@dataclass
class ConfigChange:
  key: str
  previous: object
  value: object
  changed: bool


def _config_schema() -> dict:
  """Every settable key with its default, in config.json's own shape.

  `satisfies_dependencies` is omitted from `to_dict()` while unset, so it is added
  here as the list it takes.
  """
  defaults = Config().to_dict()
  defaults["satisfies_dependencies"] = []
  return defaults


def _config_default(key: str) -> object:
  """The default at a dotted key, or a `usage` error naming the keys that exist."""
  schema = _config_schema()
  parts = key.split(".")
  if key == "version":
    raise StateError(
      "version is the schema number slicer writes; it is not set through `slicer config`",
      code="usage",
    )
  if key == "id.prefix":
    raise StateError(
      "id.prefix is set with `slicer id-prefix`, which also renames the index "
      "and refuses anything but a change of case",
      code="usage",
    )
  node: object = schema
  for depth, part in enumerate(parts):
    nested = depth == 0 or (depth == 1 and parts[0] in _CONFIG_OBJECTS)
    if not nested or not isinstance(node, dict) or part not in node:
      raise StateError(
        f"{key!r} is not a config key that can be set; the top-level keys are "
        f"{', '.join(schema)}",
        code="usage",
      )
    node = node[part]
  return node


def _bad_config_value(key: str, why: str) -> StateError:
  return StateError(f"{key}: {why}", code="bad_config_value")


def _coerce_config_value(key: str, text: str, default: object) -> object:
  """The text as the type the key has by default. A string is taken literally."""
  if isinstance(default, bool):
    if text not in ("true", "false"):
      raise _bad_config_value(key, f"{text!r} is not true or false")
    return text == "true"
  if isinstance(default, int):
    if not re.fullmatch(r"[+-]?\d+", text):
      raise _bad_config_value(key, f"{text!r} is not a whole number")
    return int(text)
  if isinstance(default, str):
    return text
  try:
    value = json.loads(text)
  except ValueError as exc:
    raise _bad_config_value(key, f"expected JSON ({exc})") from None
  if isinstance(default, list):
    element = dict if key in _CONFIG_OBJECT_LISTS else str
    if not isinstance(value, list) or any(not isinstance(v, element) for v in value):
      what = "objects" if element is dict else "strings"
      raise _bad_config_value(key, f"expected a JSON list of {what}")
    return value
  if not isinstance(value, dict):
    raise _bad_config_value(key, "expected a JSON object")
  if default and all(isinstance(v, str) for v in default.values()) and any(
    not isinstance(v, str) for v in value.values()
  ):
    raise _bad_config_value(key, "expected a JSON object whose values are strings")
  return value


def _config_at(data: object, parts: list[str]) -> object:
  for part in parts:
    if not isinstance(data, dict) or part not in data:
      return None
    data = data[part]
  return data


def _config_value_text(value: object) -> str:
  if value is None:
    return _UNSET_TEXT
  if isinstance(value, str):
    return value
  return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _guard_id_scheme(state: State, new: Config) -> None:
  """Refuse a write that changes the id scheme by any route, whole-object ones included.

  `id.prefix` is `slicer id-prefix`'s, which also updates the index. Once the index has
  items it owns the width too, as `init --force` enforces. Both are checked on the
  loaded result, so setting or unsetting the whole `id` object cannot get around them.
  """
  old = state.config
  if new.id_prefix != old.id_prefix:
    raise StateError(
      "this write would change id.prefix; set it with `slicer id-prefix`, which also "
      "updates the index and refuses anything but a change of case",
      code="usage",
    )
  if new.id_width != old.id_width and state.index.items:
    raise StateError(
      "this write would change id.width, but the index already has items and owns the id "
      "scheme; ids are never reused or renumbered. Set the width before the first item, "
      "or start a new queue",
      code="state",
    )


def config_set(state: State, key: str, value: str | None, *, unset: bool = False) -> ConfigChange:
  """Set one config key from text, or remove it so the default applies again.

  The file is patched, not rewritten, so only that key changes. The patched
  copy must load the way `Config.load` would, or nothing is written. A write that
  leaves the file as it was changes nothing and logs nothing. `previous` and
  `value` are effective values: what the key meant before, and what it means now.
  """
  default = _config_default(key)
  parts = key.split(".")
  new_value = None if unset else _coerce_config_value(key, value or "", default)
  before = state.config_raw()
  patched = deepcopy(before)
  parent = patched
  for part in parts[:-1]:
    child = parent.get(part)
    if not isinstance(child, dict):
      if unset:
        break
      child = parent[part] = {}
    parent = child
  else:
    if unset:
      # Prune an emptied object only when this call emptied it.
      if parts[-1] in parent:
        del parent[parts[-1]]
        if len(parts) > 1 and not parent:
          patched.pop(parts[0])
    else:
      parent[parts[-1]] = new_value
  try:
    config = Config.from_dict(patched)
  except ConfigError as exc:
    raise _bad_config_value(key, f"the config would no longer be valid: {exc}") from None
  except (KeyError, TypeError, ValueError, AttributeError) as exc:
    raise _bad_config_value(key, f"the config would no longer load: {exc!r}") from None
  _guard_id_scheme(state, config)
  previous = _config_at(state.config.to_dict(), parts)
  now = _config_at(config.to_dict(), parts)
  if patched == before:
    return ConfigChange(key, previous, now, False)
  state.save_config_raw(patched)
  state.config = config
  _record(
    state, f"{CONFIG_ACTION}.{key}", CONFIG_ACTION,
    frm=_config_value_text(previous), to=_config_value_text(now),
  )
  return ConfigChange(key, previous, now, True)


# ---- templates -------------------------------------------------------------

TEMPLATE_ACTION = "template"


@dataclass
class TemplateChange:
  name: str
  changed: bool
  customized: bool


def _template_kind(text: str | None, name: str) -> str:
  """How a template reads in history: absent, the built-in text, or the project's own."""
  if text is None:
    return "missing"
  return "default" if text == templates.default(name) else "custom"


def template_set(state: State, name: str, text: str) -> TemplateChange:
  """Replace one project template, refusing text that names an unknown placeholder.

  The proposed text is expanded before anything is staged, so a refusal writes
  nothing. Text equal to what the file already holds changes nothing and logs
  nothing. `customized` is whether the file now differs from the built-in text.
  """
  templates.require(name)
  try:
    render.validate_template(name, text, state.config, state.index)
  except RenderError as exc:
    raise StateError(f"{name}: {exc}; nothing was written", code="bad_template") from None
  before = state.template_exact(name)
  customized = text != templates.default(name)
  if before == text:
    return TemplateChange(name, False, customized)
  state.save_template(name, text)
  _record(
    state, f"{TEMPLATE_ACTION}.{name}", TEMPLATE_ACTION,
    frm=_template_kind(before, name), to=_template_kind(text, name),
  )
  return TemplateChange(name, True, customized)


def template_reset(state: State, name: str) -> TemplateChange:
  """Restore one template to the built-in text, recreating it if it was deleted."""
  templates.require(name)
  return template_set(state, name, templates.default(name))


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


def _merged_flags(current: list[str], remove: list[str], add: list[str]) -> list[str]:
  """Keep `current` order, drop `remove`, then append new flags from `add`."""
  dropped = set(remove)
  merged = [flag for flag in current if flag not in dropped]
  for flag in add:
    if flag not in merged:
      merged.append(flag)
  return merged


def set_fields_many(
  state: State, item_ids: list[str], *,
  add_flags: list[str] | None = None, remove_flags: list[str] | None = None,
  **fields: object,
) -> list[Item]:
  """Validate the request before saving once; omitted fields stay unchanged."""
  adjusting = add_flags is not None or remove_flags is not None
  for flag in (add_flags or []) + (remove_flags or []):
    if "\n" in str(flag):
      raise StateError("a flag cannot contain a newline", code="newline_in_field")
  add_flags = list(_clean(add_flags or []))
  remove_flags = list(_clean(remove_flags or []))
  items = _batch_items(state, item_ids)
  known = {"title", "short_title", "status", "size", "trees", "findings", "pass_key", "depends_on", "flags", "group", "parent", "importance", "urgency", "effort", "attempts"}
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
    elif key == "attempts" and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
      raise StateError(f"attempts must be an integer >= 0, not {value!r}", code="usage")
    values[key] = _clean(value)
  if "depends_on" in values:
    _check_depends(state, {item.id: list(values["depends_on"]) for item in items})
  if "parent" in values:
    for item in items:
      _check_parent(state, item.id, values["parent"])

  transitions = []
  for item in items:
    previous = item.status
    changes = []
    # A short title that still equals the title was never chosen. Keep it in
    # step, per item, so a batch does not turn the follow-on into an explicit
    # short-title edit for the next id.
    item_values = dict(values)
    if (
      "title" in item_values
      and "short_title" not in values
      and item.short_title == item.title
    ):
      item_values["short_title"] = item_values["title"]
    if adjusting:
      item_values["flags"] = _merged_flags(item.flags, remove_flags, add_flags)
    for key, value in item_values.items():
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
  # An add/remove that matches every item's list is a no-op: no save, no log.
  # A plain `set` of the same value still records the field names.
  if adjusting and not values and not any(changes for _, _, _, changes in transitions):
    return items
  state.save_index()
  for item, previous, moved, changes in transitions:
    if adjusting and not values and not changes:
      continue
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


def _check_slivers(
  state: State, items: list[Item], needed: frozenset[str], *, verb: str, remedy: str,
) -> None:
  """Refuse a parent's finish while a sliver is short of `needed`.

  A sliver named in the same call counts: the call finishes it too, or fails whole.
  Nothing is changed. There is no force; finish the slivers first.
  """
  in_call = {item.id for item in items}
  problems = []
  for item in items:
    short = [k for k in slivers_of(state, item.id)
             if k.status not in needed and k.id not in in_call]
    if short:
      listed = ", ".join(f"{k.id} ({k.status})" for k in short)
      problems.append(
        f"{item.id} cannot be {verb} while its slivers are unfinished: {listed}; "
        f"{remedy.format(ids=' '.join(k.id for k in short))}")
  if problems:
    raise StateError("; ".join(problems) + ". Nothing was changed.", code="slivers_open")


def set_status_many(
  state: State, item_ids: list[str], status: str, *, note: str = "", by: str = ""
) -> list[Item]:
  """Move a batch to one status, preserving no-op and per-item history semantics.

  Finishing clears a claim in the same save. A done item is never claimed.
  """
  items = _batch_items(state, item_ids)
  if status not in state.config.statuses:
    raise StateError(f"unknown status {status!r}; known: {sorted(state.config.statuses)}")
  finishing = status == state.config.done_status
  if finishing:
    _check_slivers(
      state, [item for item in items if item.status != status], state.config.finished_statuses(),
      verb="marked done", remedy="finish them first with `slicer done {ids}`, or name them in this call",
    )
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
        _record(state, item.id, "status", frm=previous, to=status, note=note, by=by)
  return items


def done_and_render(
  state: State, item_ids: list[str], *, note: str = "", by: str = ""
) -> tuple[list[Item], int]:
  """Render the proposed done state before moving slices or saving the index."""
  items, written = render_gated(
    state, lambda: set_status_many(state, item_ids, state.config.done_status, note=note, by=by)
  )
  return items, written  # type: ignore[return-value]


def set_status(state: State, item_id: str, status: str, *, note: str = "") -> Item:
  return set_status_many(state, [item_id], status, note=note)[0]


def done(state: State, item_id: str, *, note: str = "", by: str = "") -> Item:
  return set_status_many(state, [item_id], state.config.done_status, note=note, by=by)[0]


def park(state: State, item_id: str, *, note: str = "") -> Item:
  cfg = state.config
  if not cfg.parked_status:
    raise StateError("this project declares no parked status; set parked_status in config")
  return set_status(state, item_id, cfg.parked_status, note=note)


def unpark(state: State, item_id: str) -> Item:
  return set_status(state, item_id, state.config.open_status)


def _fresh_implementation_start(state: State, previous: str, target: str) -> bool:
  """Count queue-to-implementation starts, never resumes or review claims."""
  cfg = state.config
  roles = {cfg.started_status, cfg.review_status, cfg.reviewing_status,
           cfg.done_status, cfg.retired_status, cfg.parked_status} - {""}
  return (
    target == cfg.started_status and previous != target
    and (previous == cfg.open_status or previous not in roles)
    and previous not in {cfg.review_status, cfg.reviewing_status}
  )


def start_many(
  state: State, item_ids: list[str], *, note: str = "", owner: str | None = None
) -> list[Item]:
  """Mark items started and claim any that do not already have an owner.

  A review item (or a reviewing one whose claim was released) goes to
  `reviewing_status` instead, when the project has one. The claim time is taken once here and stored on the item. A later render
  does not refresh it. Starting something already started and claimed writes
  nothing, so a repeated `start` stays a silent no-op.
  """
  cfg = state.config
  if not cfg.started_status:
    raise StateError("this project declares no started status; set started_status in config")
  items = _batch_items(state, item_ids)
  owner = actor(state, owner)
  # A reviewer claiming review work moves it to reviewing, not started, so an
  # implementer's `next` never resumes a review in progress.
  reviewable = {cfg.review_status, cfg.reviewing_status} - {""}
  at = _now()
  changed: list[tuple[Item, str, bool]] = []
  for item in items:
    previous = item.status
    status = (
      cfg.reviewing_status if cfg.reviewing_status and previous in reviewable
      else cfg.started_status
    )
    status_changed = previous != status
    if item.claim_owner:
      claim_changed = False
    else:
      item.claim_owner = owner
      item.claim_at = at
      claim_changed = True
    if not status_changed and not claim_changed:
      continue
    if _fresh_implementation_start(state, previous, status):
      item.attempts += 1
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
      _record(state, item.id, "status", frm=previous, to=item.status, note=note, by=owner)
    else:
      _record(state, item.id, "claim", to=item.claim_owner, note=note, by=owner)
  return items


def start(state: State, item_id: str, *, note: str = "", owner: str | None = None) -> Item:
  return start_many(state, [item_id], note=note, owner=owner)[0]


def _lifecycle_roles(cfg: Config) -> set[str]:
  return {cfg.open_status, cfg.started_status, cfg.done_status, cfg.retired_status,
          cfg.parked_status, cfg.review_status, cfg.reviewing_status} - {""}


def custom_queue(cfg: Config, key: str) -> str:
  """A status key `next --status` may draw from: configured, and no lifecycle role."""
  allowed = sorted(set(cfg.statuses) - _lifecycle_roles(cfg))
  if key not in allowed:
    choices = sorted([*allowed, *([cfg.review_status] if cfg.review_status else [])])
    raise StateError(f"next --status {key!r}: choose one of {choices}", code="usage")
  return key


def check_start_to(cfg: Config, to: str | None) -> None:
  """A claim may move to any configured status except a finished, parked or review one."""
  if to is None:
    return
  barred = {cfg.done_status, cfg.retired_status, cfg.parked_status,
            cfg.review_status, cfg.reviewing_status} - {""}
  if to not in cfg.statuses or to in barred:
    allowed = sorted(set(cfg.statuses) - barred)
    raise StateError(f"cannot start to {to!r}; choose one of {allowed}", code="usage")


def claim_in_queue(
  state: State, item_id: str, *, queue: str, owner: str | None = None,
  to: str | None = None, note: str = "",
) -> Item:
  """Claim an item of a custom queue in place, or move it to `to` as well.

  A claim alone leaves the status and the attempt count as they are. Moving to
  the started role begins one implementation attempt. Resuming an item the
  actor already holds writes nothing. Every check runs before anything is
  changed, so a refusal leaves the state as it was.
  """
  cfg = state.config
  custom_queue(cfg, queue)
  check_start_to(cfg, to)
  item = state.index.require(item_id)
  by = actor(state, owner)
  if item.status != queue:
    raise StateError(
      f"{item.id} is {cfg.status_label(item.status)!r}, not in {queue!r}", code="state")
  if item.claim_owner and item.claim_owner != by:
    raise StateError(f"{item.id} is claimed by {item.claim_owner}", code="state")
  previous = item.status
  status = to if to is not None else previous
  status_changed = status != previous
  claim_changed = not item.claim_owner
  if not status_changed and not claim_changed:
    return item
  if claim_changed:
    item.claim_owner = by
    item.claim_at = _now()
  if _fresh_implementation_start(state, previous, status):
    item.attempts += 1
  if status_changed:
    item.status = status
    _relocate_slice(state, item.id)
  state.save_index()
  if status_changed:
    _record(state, item.id, "status", frm=previous, to=status, note=note, by=by)
  else:
    _record(state, item.id, "claim", to=by, note=note, by=by)
  return item


def release_many(state: State, item_ids: list[str], *, owner: str | None = None) -> list[Item]:
  """Drop claims without changing status. An item with no claim is left alone."""
  items = _batch_items(state, item_ids)
  by = actor(state, owner)
  released: list[tuple[Item, str]] = []
  for item in items:
    if not item.claim_owner:
      continue
    held_by = item.claim_owner
    _clear_claim(item)
    released.append((item, held_by))
  if not released:
    return items
  state.save_index()
  for item, held_by in released:
    _record(state, item.id, "release", frm=held_by, to="", by=by)
  return items


def release(state: State, item_id: str, *, owner: str | None = None) -> Item:
  return release_many(state, [item_id], owner=owner)[0]


def handoff_many(
  state: State, item_ids: list[str], *, note: str = "", owner: str | None = None
) -> list[Item]:
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
  by = actor(state, owner)
  problems: list[str] = []
  for item in items:
    if cfg.reviewing_status and item.status == cfg.reviewing_status:
      problems.append(
        f"{item.id} is already being reviewed; finish it with `slicer done {item.id}`, "
        f"or send it back with `slicer set {item.id} --status {cfg.started_status}`"
      )
    elif item.status not in (cfg.started_status, cfg.review_status):
      problems.append(
        f"{item.id} is {cfg.status_label(item.status)!r}, not started; "
        f"only started work can be handed off (run `slicer start {item.id}` first)"
      )
    elif not item.has_slice:
      problems.append(f"{item.id} has no slice to review; run `slicer promote {item.id}` first")
    if (item.status == cfg.started_status and cfg.handoff_requires_note_kind
        and not any(record.kind == cfg.handoff_requires_note_kind
                    and record.text.strip() and record.attempt == item.attempts
                    for record in item.note_records)):
      problems.append(
        f"{item.id} needs a nonempty {cfg.handoff_requires_note_kind!r} note for "
        f"current attempt {item.attempts}; run `slicer note {item.id} "
        f"--kind {shlex.quote(cfg.handoff_requires_note_kind)} --text \"Describe the verified outcome\"`"
      )
  if problems:
    raise StateError("; ".join(problems) + ". Nothing was changed.", code="state")
  _check_slivers(
    state, [item for item in items if not (item.status == cfg.review_status and not item.claim_owner)],
    cfg.handed_off_statuses(), verb="handed off",
    remedy="hand them off first with `slicer handoff {ids}`, or name them in this call",
  )
  changed: list[tuple[Item, str, str]] = []
  for item in items:
    if item.status == cfg.review_status and not item.claim_owner:
      continue
    previous, held_by = item.status, item.claim_owner
    item.status = cfg.review_status
    _clear_claim(item)
    changed.append((item, previous, held_by))
  if not changed:
    return items
  for item, previous, _owner in changed:
    if previous != item.status:
      _relocate_slice(state, item.id)
  state.save_index()
  for item, previous, held_by in changed:
    detail = f"claim {held_by} cleared" if held_by else ""
    _record(state, item.id, "handoff", frm=previous, to=item.status,
            note="; ".join(part for part in (note, detail) if part), by=by)
  return items


def handoff(state: State, item_id: str, *, note: str = "", owner: str | None = None) -> Item:
  return handoff_many(state, [item_id], note=note, owner=owner)[0]


def reject_many(
  state: State, item_ids: list[str], *, note: str, to: str | None = None,
  owner: str | None = None,
) -> list[Item]:
  """Send failed reviews back, with the verdict recorded, in one save.

  The counterpart of `handoff`. The verdict becomes a dated note on the item
  and the note of a single `reject` history entry, and the reviewer's claim is
  cleared so the item goes back to the queue unclaimed. The target defaults to
  the open status; it may be any other configured status that is not a
  lifecycle role (a project's own `blocked`, say). Every id is checked before
  anything is written.
  """
  cfg = state.config
  verdict = note.strip()
  if not verdict:
    raise StateError("a reject needs a verdict; pass --note", code="usage")
  target = to if to is not None else cfg.open_status
  roles = {cfg.done_status, cfg.retired_status, cfg.started_status,
           cfg.review_status, cfg.reviewing_status} - {""}
  if target not in cfg.statuses or target in roles:
    allowed = sorted(set(cfg.statuses) - roles)
    raise StateError(
      f"cannot reject to {target!r}; choose one of {allowed}", code="usage"
    )
  reviewable = {cfg.review_status, cfg.reviewing_status} - {""}
  items = _batch_items(state, item_ids)
  by = actor(state, owner)
  problems = [
    f"{item.id} is {cfg.status_label(item.status)!r}, not in review; only review work can be rejected"
    for item in items if item.status not in reviewable
  ]
  if problems:
    raise StateError("; ".join(problems) + ". Nothing was changed.", code="state")
  changed: list[tuple[Item, str]] = []
  for item in items:
    previous = item.status
    item.status = target
    _clear_claim(item)
    _append_note(item, verdict, by=by)
    changed.append((item, previous))
  for item, previous in changed:
    if previous != item.status:
      _relocate_slice(state, item.id)
  state.save_index()
  for item, previous in changed:
    _record(state, item.id, "reject", frm=previous, to=item.status, note=verdict, by=by)
  return items


def reject(state: State, item_id: str, *, note: str, to: str | None = None,
           owner: str | None = None) -> Item:
  return reject_many(state, [item_id], note=note, to=to, owner=owner)[0]


@dataclass
class NextResult:
  item: Item | None
  blocked: list[tuple[str, list[str]]]
  unspecified: list[tuple[str, list[str]]] = field(default_factory=list)
  # Eligible items skipped because a sibling worktree has them in work.
  elsewhere: list[tuple[str, list[dict[str, str]]]] = field(default_factory=list)
  capped: list[dict[str, int | str]] = field(default_factory=list)
  # Custom-queue items skipped because another owner holds the claim.
  claimed_elsewhere: list[dict[str, str]] = field(default_factory=list)


@dataclass
class BatchResult:
  """Up to K items from one filtered pool, plus the same skip lists as `next`."""

  items: list[Item]
  blocked: list[tuple[str, list[str]]]
  unspecified: list[tuple[str, list[str]]] = field(default_factory=list)
  elsewhere: list[tuple[str, list[dict[str, str]]]] = field(default_factory=list)
  capped: list[dict[str, int | str]] = field(default_factory=list)
  # Custom-queue items skipped because another owner holds the claim.
  claimed_elsewhere: list[dict[str, str]] = field(default_factory=list)


@dataclass
class _NextPool:
  items: list[Item]
  blocked: list[tuple[str, list[str]]]
  unspecified: list[tuple[str, list[str]]] = field(default_factory=list)
  elsewhere: list[tuple[str, list[dict[str, str]]]] = field(default_factory=list)
  capped: list[dict[str, int | str]] = field(default_factory=list)
  # Custom-queue items skipped because another owner holds the claim.
  claimed_elsewhere: list[dict[str, str]] = field(default_factory=list)


def _ranked_pool(
  state: State,
  elsewhere: Mapping[str, list[dict[str, str]]] | None,
  *,
  review: bool = False,
  tree: str | None = None,
  size: str | None = None,
  include_blocked: bool = False,
  max_attempts: int | None = None,
  flags: list[str] | None = None, no_flags: list[str] | None = None,
  queue: str | None = None,
  holder: str = "",
  cap_queue: bool = False,
) -> _NextPool:
  """Open or started work in next's order, after tree, size, and skip rules.

  Started items precede open ones. Each group uses the shared list ranking,
  including declared passes, empty-pass score merging, and queue-order ties.
  With `review`, reviewing items precede review items by those same rules.
  A custom `queue` still ranks the caller's claims before unclaimed items,
  each by effective score. `tree` keeps an item when that tree is one of
  its trees. `size` keeps an exact size match. Omit either and it does not
  narrow the pool. `flags` requires any case-sensitive shell pattern match;
  any `no_flags` match excludes the item before skip diagnostics. Dependency
  checks and effective scores still use the complete index.

  A blocked item stays out unless `include_blocked`, which is how a batch can
  take a dependency before the item that waits on it. Unspecified slices and
  work held in another checkout stay out either way. A blocked item is not
  also reported as elsewhere. The caller decides which sibling rows count.
  `max_attempts` excludes fresh implementation attempts at the limit, but
  keeps started resumes and review claims. Capped rows are reported even
  when blocked, and never enter the batch dependency plan.

  With `queue`, the pool is that custom status instead. Items claimed by
  `holder` come first, then unclaimed ones, each by effective score. That
  queue does not use pass order. An item
  another owner claimed is skipped and reported as claimed elsewhere. The
  attempt cap applies there only when `cap_queue` says the pickup will start
  an implementation attempt.
  """
  if max_attempts is not None and (isinstance(max_attempts, bool)
      or not isinstance(max_attempts, int) or max_attempts < 1):
    raise StateError("next --max-attempts must be an integer >= 1", code="usage")
  for pattern in (flags or []) + (no_flags or []):
    if pattern == "":
      raise StateError("next flag patterns cannot be empty", code="usage")
  cfg = state.config
  # A whitelist, so parked, done, retired and any project-specific status stay
  # out. An empty started_status means the project has no start state, and the
  # set is just the open one.
  if queue is not None:
    active = {queue}
  elif review:
    active = {cfg.review_status, cfg.reviewing_status} - {""}
  else:
    active = {cfg.open_status}
    if cfg.started_status:
      active.add(cfg.started_status)
  blocked: list[tuple[str, list[str]]] = []
  unspecified: list[tuple[str, list[str]]] = []
  skipped: list[tuple[str, list[dict[str, str]]]] = []
  capped: list[dict[str, int | str]] = []
  claimed_elsewhere: list[dict[str, str]] = []
  started: list[Item] = []
  candidates: list[Item] = []
  eligible: set[str] = set()
  for item in state.index.items:
    if item.status not in active:
      continue
    if tree is not None and tree not in item.trees:
      continue
    if size is not None and item.size != size:
      continue
    if flags and not any(fnmatchcase(flag, pattern) for flag in item.flags for pattern in flags):
      continue
    if no_flags and any(fnmatchcase(flag, pattern) for flag in item.flags for pattern in no_flags):
      continue
    pending = graph.pending(state.index, item, cfg)
    if pending:
      blocked.append((item.id, pending))
    missing = _missing_spec(state, item)
    if missing:
      unspecified.append((item.id, missing))
    cap_applies = cap_queue if queue is not None else not review
    at_cap = (max_attempts is not None and cap_applies
              and item.status != cfg.started_status and item.attempts >= max_attempts)
    if at_cap:
      capped.append({"id": item.id, "attempts": item.attempts, "max_attempts": max_attempts})
    if at_cap or missing or (pending and not include_blocked):
      continue
    if queue is not None and item.claim_owner and item.claim_owner != holder:
      claimed_elsewhere.append({"id": item.id, "owner": item.claim_owner})
      continue
    local = bool(item.claim_owner) or item.status in cfg.in_work()
    if elsewhere and item.id in elsewhere and not local:
      # Pending already recorded this item as blocked. Do not also skip it
      # as elsewhere, and do not offer it in the pool.
      if not pending:
        skipped.append((item.id, elsewhere[item.id]))
      continue
    if queue is not None:
      (started if item.claim_owner else candidates).append(item)
    eligible.add(item.id)
  if queue is not None:
    eff = graph.effective_scores(state.index)
    ranked = (sorted(started, key=lambda it: -eff[it.id])
              + sorted(candidates, key=lambda it: -eff[it.id]))
  else:
    hidden = {cfg.done_status, cfg.retired_status} - {""}
    visible = [it for it in state.index.items if it.status not in hidden]
    # Rank before eligibility exclusions: removing an unspecified or sibling-held
    # named row must not relocate empty-pass rows in the default list order.
    ranked = [it for it in graph.ranked_order(state.index, cfg, visible) if it.id in eligible]
  return _NextPool(
    items=ranked, blocked=blocked, unspecified=unspecified,
    elsewhere=skipped, capped=capped, claimed_elsewhere=claimed_elsewhere,
  )


def next_item(
  state: State, offset: int = 0,
  elsewhere: Mapping[str, list[dict[str, str]]] | None = None,
  *, review: bool = False, tree: str | None = None, size: str | None = None,
  max_attempts: int | None = None,
  flags: list[str] | None = None, no_flags: list[str] | None = None,
  queue: str | None = None,
  owner: str | None = None, start_to: str | None = None,
) -> NextResult:
  """The first eligible item in the shared list priority order.

  Started items precede open items, with shared list ranking in each group.
  With `review`, the pool is the review queue instead: reviewing items (a
  review someone already holds) precede review items, by the same rules.
  Offset skips currently eligible items without simulating their completion.
  `tree` and `size` narrow that pool; omit them and the pool is unchanged.

  Dependencies still hard-gate what is startable -- a blocked item is never
  returned, whatever its score or status. Pass order and effective score only
  order the items that can actually be picked up. Stored queue order breaks ties.

  `elsewhere` maps item ids to sibling rows (`store.in_work_elsewhere`):
  claimed, in work, or review and done when this checkout differs and is not
  done. Such an item is skipped and reported, unless this checkout has it
  started or claimed too: local work wins, as it does in `list`. The caller
  supplies the map, so this stays free of git.

  `queue` names a custom status to draw from; see `_ranked_pool`. The holder is
  `owner` or the usual actor, so a read-only call ranks the caller's own claims.
  """
  if offset < 0:
    raise StateError("next offset must be a nonnegative integer", code="usage")
  holder = ""
  cap_queue = False
  if queue is not None:
    custom_queue(state.config, queue)
    check_start_to(state.config, start_to)
    holder = actor(state, owner)
    cap_queue = bool(start_to) and start_to == state.config.started_status
  pool = _ranked_pool(
    state, elsewhere, review=review, tree=tree, size=size, max_attempts=max_attempts,
    flags=flags, no_flags=no_flags,
    queue=queue, holder=holder, cap_queue=cap_queue,
  )
  return NextResult(
    item=pool.items[offset] if offset < len(pool.items) else None,
    blocked=pool.blocked,
    unspecified=pool.unspecified,
    elsewhere=pool.elsewhere,
    capped=pool.capped,
    claimed_elsewhere=pool.claimed_elsewhere,
  )


def next_batch(
  state: State, count: int,
  elsewhere: Mapping[str, list[dict[str, str]]] | None = None,
  *, tree: str | None = None, size: str | None = None,
  max_attempts: int | None = None,
  flags: list[str] | None = None, no_flags: list[str] | None = None,
) -> BatchResult:
  """Up to `count` items from the next pool, dependencies before dependents.

  The pool includes items that are still blocked. Each pick is the first item
  not yet chosen whose unmet dependencies are already in the batch, so a
  dependent follows its dependency even when the dependent ranks higher.
  Selection stops at `count`. An item left out only because the batch is full
  is not reported as blocked. An item still waiting on something outside the
  batch is, and the reported dependencies are the ones the batch did not take.
  """
  if isinstance(count, bool) or not isinstance(count, int) or count < 1:
    raise StateError("next --batch must be an integer >= 1", code="usage")
  pool = _ranked_pool(
    state, elsewhere, tree=tree, size=size, include_blocked=True,
    max_attempts=max_attempts, flags=flags, no_flags=no_flags,
  )
  chosen: list[Item] = []
  chosen_ids: set[str] = set()
  while len(chosen) < count:
    pick: Item | None = None
    for item in pool.items:
      if item.id in chosen_ids:
        continue
      unmet = [dep for dep in graph.pending(state.index, item, state.config) if dep not in chosen_ids]
      if not unmet:
        pick = item
        break
    if pick is None:
      break
    chosen.append(pick)
    chosen_ids.add(pick.id)
  blocked: list[tuple[str, list[str]]] = []
  for item_id, pending in pool.blocked:
    if item_id in chosen_ids:
      continue
    unmet = [dep for dep in pending if dep not in chosen_ids]
    if unmet:
      blocked.append((item_id, unmet))
  return BatchResult(
    items=chosen,
    blocked=blocked,
    unspecified=pool.unspecified,
    elsewhere=pool.elsewhere,
    capped=pool.capped,
  )


def _missing_spec(state: State, item: Item) -> list[str]:
  """Required headings with no text. A row with no slice is not this case."""
  sl = state.slices.get(item.id)
  if sl is None:
    return []
  missing: list[str] = []
  for heading in state.config.required_sections:
    section = sl.section(heading)
    if section is None or not section.body.strip():
      missing.append(heading)
  return missing


def edit_section(state: State, item_id: str, heading: str, body: str, *, append: bool = False) -> Slice:
  sl = state.slices.get(item_id)
  if sl is None:
    raise StateError(f"{item_id} has no slice; run `slicer promote {item_id}` first", code="no_slice")
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


def _dated(text: str) -> str:
  """An item note as `note` writes it: the date in bold, then the text."""
  return f"**{_now()[:10]}** — {text}"


def _append_note(item: Item, text: str, kind: str = "", by: str = "", stale_after: str = "") -> None:
  created_at = _now()
  item.note_records.append(NoteRecord(str(uuid4()), kind,
    f"**{created_at[:10]}** — {text}", created_at, item.attempts,
    by=by, stale_after=stale_after))


def _note_actor(value: str) -> str:
  value = value.strip()
  if not value or value.startswith("human:") or "\n" in value or "\r" in value:
    raise StateError("note actor must be a nonempty, one-line name without the reserved human: prefix", code="usage")
  return value


def _note_expiry(value: str) -> str:
  if not value:
    return ""
  try:
    instant = datetime.fromisoformat(value)
    if instant.utcoffset() is None:
      raise ValueError("timezone required")
    return instant.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
  except (ValueError, OverflowError):
    raise StateError("--stale-after must be a timezone-aware ISO8601 timestamp", code="usage") from None


def add_note(state: State, item_id: str, text: str, kind: str | None = None, *,
             owner: str | None = None, stale_after: str = "") -> Item:
  """Append a dated note to the item itself — no slice required — visible in
  `show` and (once promoted) the rendered slice."""
  item = state.index.require(item_id)
  text = text.strip()
  if not text:
    raise StateError("a note cannot be blank", code="usage")
  if kind is not None:
    if not isinstance(kind, str) or not kind.strip():
      raise StateError("a note kind cannot be blank", code="usage")
    if state.config.note_kinds and kind not in state.config.note_kinds:
      raise StateError(f"unknown note kind {kind!r}; choose from {state.config.note_kinds}", code="usage")
  by = _note_actor(actor(state, owner))
  expiry = _note_expiry(stale_after)
  _append_note(item, text, kind or "", by, expiry)
  state.save_index()
  _record(state, item_id, "note", note=text.splitlines()[0][:60], by=by)
  return item


def verify_note(state: State, item_id: str, note_id: str, *, owner: str | None,
                attest: bool = False) -> tuple[NoteRecord, bool]:
  """Record workflow verification; this does not authenticate the caller.

  Attestation needs an explicit owner. Machine verification falls back to the
  usual actor precedence. Returns the record and whether anything was written."""
  item = state.index.require(item_id)
  record = next((n for n in item.note_records if n.id == note_id), None)
  if record is None:
    raise StateError(f"{item_id} has no item note {note_id!r}; use show --json to find its ID", code="usage")
  if owner is not None and not owner.strip():
    raise StateError("--owner cannot be blank", code="usage")
  if attest and owner is None:
    raise StateError("note-attest needs an explicit --owner name", code="usage")
  name = _note_actor(owner if attest else actor(state, owner))
  verifier = f"human:{name}" if attest else name
  if record.verified_by == verifier:
    return record, False
  previous = record.verified_by
  record.verified_by = verifier
  record.verified_at = _now()
  state.save_index()
  _record(state, item_id, "note-attest" if attest else "note-verify",
    frm=previous, to=verifier, note=record.id, by=verifier)
  return record, True


def _note_index(item: Item, index: int) -> None:
  if not 0 <= index < len(item.notes):
    raise StateError(f"{item.id} has no note {index}", code="usage")


def set_note(state: State, item_id: str, index: int, text: str) -> Item:
  """Replace one note verbatim (the caller edited the full dated paragraph)."""
  item = state.index.require(item_id)
  _note_index(item, index)
  record = item.note_records[index]
  if record.text == text:
    return item
  previous_verifier, previous_time = record.verified_by, record.verified_at
  record.text = text
  record.verified_by = ""
  record.verified_at = ""
  state.save_index()
  _record(state, item_id, "note", note=f"edit {record.id}; cleared verified_by={previous_verifier}; verified_at={previous_time}")
  return item


def remove_note(state: State, item_id: str, index: int) -> Item:
  """Drop one note."""
  item = state.index.require(item_id)
  _note_index(item, index)
  del item.note_records[index]
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


def edit_lead(state: State, item_id: str, lead: list[str]) -> Slice:
  """Replace the lead paragraphs, independently of the sections."""
  state.index.require(item_id)
  sl = state.slices.get(item_id)
  if sl is None:
    raise StateError(f"{item_id} has no slice; run `slicer promote {item_id}` first", code="no_slice")
  sl.lead = list(lead)
  state.save_slice(sl)
  _record(state, item_id, "edit", note="lead")
  return sl


def fix_refs(state: State, located: list[refs.Located]) -> int:
  """Rewrite the `moved` references in `located` and mark them `fixed`.

  Only the numbers change: the lead, the boundary and the sections are saved
  through their own edit ops, so each logs the history entry an ordinary edit
  does. Notes are never rewritten, and neither is a section that shares its
  heading with an earlier one, because `edit_section` finds a section by
  heading and would overwrite the first. Returns how many references it fixed.
  """
  fixed = 0
  for item_id in dict.fromkeys(loc.item for loc in located):
    sl = state.slices.get(item_id)
    if sl is None:
      continue
    groups: dict[tuple[str, int], list[refs.Located]] = {}
    for loc in located:
      if loc.item == item_id and loc.finding.status == refs.MOVED and loc.place.rewritable:
        groups.setdefault((loc.place.kind, loc.place.index), []).append(loc)
    done: list[refs.Located] = []
    lead = list(sl.lead)
    lead_moved = False
    for (kind, at), group in groups.items():
      if kind == "lead":
        lead[at] = refs.rewrite(lead[at], group)
        lead_moved = True
      elif kind == "boundary":
        edit_boundary(state, item_id, refs.rewrite(sl.boundary, group))
      else:  # a section: the lead, the boundary and sections are the rewritable places
        section = sl.sections[at]
        if sl.section(section.heading) is not section:
          continue
        edit_section(state, item_id, section.heading, refs.rewrite(section.body, group))
      done += group
    if lead_moved:
      edit_lead(state, item_id, lead)
    for loc in done:
      loc.fixed = True
    fixed += len(done)
  return fixed


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


def _blockers(state: State, item_id: str, *, purge: bool = False) -> list[str]:
  """Reasons not to remove this item, in the order worth reading."""
  item = state.index.get(item_id)
  if item is None:
    raise StateError(f"no such item: {item_id}", code="no_such_item")
  reasons: list[str] = []
  citing = dependents(state, item_id)
  if citing:
    reasons.append(f"{', '.join(citing)} depend(s) on it")
  kids = [i.id for i in slivers_of(state, item_id)]
  if kids:
    reasons.append(f"{', '.join(kids)} {'is a sliver' if len(kids) == 1 else 'are slivers'} of it")
  if purge:
    sources = [i.id for i in state.index.items if i.discovered_from == item_id]
    if sources:
      reasons.append(f"{', '.join(sources)} discovered from it")
  if item.status == state.config.done_status:
    reasons.append("it is done; removing it hides landed work")
  return reasons


def _guard(state: State, item_id: str, action: str, force: bool) -> None:
  reasons = _blockers(state, item_id, purge=action == "purge")
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
  if any(i.discovered_from == item_id for i in state.index.items):
    return False, f"{item_id} is referenced by discovered_from"
  if slivers_of(state, item_id):
    return False, f"{item_id} is referenced by parent"
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
  blockers = _blockers(state, item_id, purge=purge)
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
    state.delete_slice(path)
    removed = True

  _record(state, item_id, "purge", frm=item.status, note=why)
  return PurgeResult(id=item_id, id_freed=freed, reason=why, file_removed=removed)


@dataclass
class OutlineReport:
  """What an outline would do, or did. Mirrors the migrate report's shape."""

  items: int = 0
  created: int = 0
  reused: int = 0
  entries: list[dict[str, object]] = field(default_factory=list)
  promoted: int = 0
  by_status: dict[str, int] = field(default_factory=dict)
  ids: list[str] = field(default_factory=list)
  depends_edges: int = 0
  off_schema_sections: dict[str, int] = field(default_factory=dict)
  warnings: list[str] = field(default_factory=list)
  problems: list[str] = field(default_factory=list)
  preamble: str | None = None
  # Resolved dependency titles or existing IDs per new spec (keyed by `id(spec)`), so applying
  # saves exactly the edges the census counted. Not part of the JSON report.
  depends: dict[int, list[str]] = field(default_factory=dict, repr=False)
  # Resolved parent id per new spec (keyed by `id(spec)`), set once ids are allocated.
  parents: dict[int, str] = field(default_factory=dict, repr=False)

  def to_dict(self) -> dict[str, object]:
    return {
      "items": self.items,
      "created": self.created,
      "reused": self.reused,
      "entries": self.entries,
      "promoted": self.promoted,
      "by_status": self.by_status,
      "ids": self.ids,
      "depends_edges": self.depends_edges,
      "off_schema_sections": self.off_schema_sections,
      "warnings": self.warnings,
      "problems": self.problems,
      "preamble": self.preamble,
    }


def _allocate_outline_ids(index: Index, count: int, floor: int = 0) -> list[str]:
  """Allocate one id per outline entry against the supplied index."""
  return [ids.allocate(index, floor=floor) for _ in range(count)]


def _existing_titles(index: Index) -> dict[str, str]:
  """Item id by title and by short title, the names `depends:` already matches."""
  titles = {it.title: it.id for it in index.items}
  titles.update({it.display_title(): it.id for it in index.items})
  return titles


def _existing_item_named(index: Index, value: str) -> Item | None:
  """The existing item named by a `depends:` or `parent:` value: a title first, then an ID."""
  titles = _existing_titles(index)
  return index.get(titles[value]) if value in titles else index.get(value)


def _outline_parent_problems(
  spec: object, specs: list[object], reused: dict[int, Item | None], index: Index,
) -> list[str]:
  """What is wrong with a spec's `parent:`, or nothing.

  Resolved like `depends:`: a known title wins, whether it names another entry
  of the outline (filed before or after it) or an existing item; otherwise the
  value must be an existing item ID. Either way the target must not itself be a
  sliver, so the outline stays one level deep.
  """
  value = spec.parent
  if not value:
    return []
  nested = (f"{spec.title!r}: parent {value!r} is itself a sliver of %s; "
            "a sliver cannot have slivers, so name the parent of that sliver instead")
  match = next((s for s in specs if s.title == value), None)
  if match is not None:
    if match is spec:
      return [f"{spec.title!r}: parent cannot be the entry itself; name the slice it is a sliver of"]
    existing = reused[id(match)]
    grandparent = existing.parent if existing is not None else match.parent
    return [nested % grandparent] if grandparent else []
  target = _existing_item_named(index, value)
  if target is None:
    return [f"{spec.title!r}: parent {value!r} is neither a title in this outline or the "
            "roadmap nor an existing item ID"]
  return [nested % target.parent] if target.parent else []


def _resolve_outline_depends(
  raw: list[str], known: set[str], index: Index,
) -> tuple[list[str], list[str]]:
  """Turn raw `depends:` values into titles or existing IDs, plus the unmatched references.

  A whole value that is a known title wins, so a title may contain commas.
  Any other value is split on commas, as it always was, and each piece must be
  a known title or, as `parent:` allows, the ID of an existing item. An ID is
  kept as it is: `apply` passes a value through when no title key matches it,
  and an ID that is not a known title is never such a key. Order is declared
  order across lines; nothing is deduplicated.
  """
  resolved: list[str] = []
  unknown: list[str] = []
  for value in raw:
    value = value.strip()
    pieces = [value] if value in known else outline.split_list(value)
    for piece in pieces:
      if piece in known or _existing_item_named(index, piece) is not None:
        resolved.append(piece)
      else:
        unknown.append(piece)
  return resolved, unknown


def outline_report(
  state: State, specs: list[object], *, force: bool = False, promote_all: bool = False,
  preamble: str | None = None, replace_preamble: bool = False, id_floor: int = 0,
) -> OutlineReport:
  """What this outline says, and everything wrong with it. Writes nothing.

  `apply_outline` calls this first and refuses when `problems` is non-empty,
  and `--dry-run` calls it alone, so the census a person reads before
  applying is the same one produced by applying.
  """
  cfg = state.config
  report = OutlineReport()
  report.items = len(specs)
  reused = {id(spec): keyed_item(state, spec.key) for spec in specs}
  new_specs = [spec for spec in specs if reused[id(spec)] is None]
  report.created = len(new_specs)
  report.reused = len(specs) - report.created
  report.promoted = sum(1 for spec in new_specs if promote_all or spec.has_slice)
  known = {s.title for s in specs} | {it.title for it in state.index.items}
  known |= {it.display_title() for it in state.index.items}
  unknown_deps: dict[int, list[str]] = {}
  for spec in new_specs:
    report.depends[id(spec)], unknown_deps[id(spec)] = _resolve_outline_depends(
      spec.depends, known, state.index)
  for spec in specs:
    item = reused[id(spec)]
    status = item.status if item is not None else spec.status or cfg.open_status
    report.by_status[status] = report.by_status.get(status, 0) + 1
    if item is not None:
      continue
    report.depends_edges += len(report.depends[id(spec)])
    for section in spec.sections:
      if section.heading not in cfg.sections:
        report.off_schema_sections[section.heading] = (
          report.off_schema_sections.get(section.heading, 0) + 1
        )
  problems: list[str] = []

  keys: set[str] = set()
  for spec in specs:
    if spec.key:
      if spec.key in keys:
        problems.append(f"key {spec.key!r} appears twice in the outline")
      keys.add(spec.key)
  titles = [s.title for s in specs]
  seen: set[str] = set()
  for title in titles:
    if title in seen:
      problems.append(f"{title!r} appears twice in the outline")
    seen.add(title)

  if not force:
    existing = {it.display_title(): it.id for it in state.index.items}
    existing.update({it.title: it.id for it in state.index.items})
    for title in dict.fromkeys(s.title for s in new_specs):
      if title in existing:
        problems.append(
          f"{title!r} already exists as {existing[title]}; pass --force to add it anyway"
        )

  for spec in new_specs:
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
    if spec.discovered_from and state.index.get(spec.discovered_from) is None:
      problems.append(
        f"{spec.title!r}: discovered_from unknown id {spec.discovered_from!r}; "
        "use an existing item ID"
      )
    problems.extend(_outline_parent_problems(spec, specs, reused, state.index))
    for dep in unknown_deps[id(spec)]:
      problems.append(
        f"{spec.title!r}: depends on {dep!r}, which is neither a title in the outline or roadmap "
        "nor an existing item ID"
      )
    heading = _boundary_in_both(spec.lead, spec.sections, cfg.boundary)
    if heading:
      problems.append(
        f"{spec.title!r}: boundary paragraph in the lead and in section {heading!r}"
      )
  active = {cfg.open_status, cfg.started_status}
  unscored = [
    spec.title for spec in new_specs
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
    report.created and preamble is not None and stored_preamble and stored_preamble != preamble
    and not force and not replace_preamble
  ):
    problems.append("preamble differs from the one already stored; pass --force to replace it")
  report.preamble = preamble
  report.problems = problems
  if not problems:
    draft = deepcopy(state.index)
    for spec in specs:
      item = reused[id(spec)]
      mapped_id = item.id if item is not None else ids.allocate(draft, floor=id_floor)
      report.ids.append(mapped_id)
      report.entries.append({"title": spec.title, "id": mapped_id, "existing": item is not None})
    by_title = _existing_titles(state.index)
    by_title.update({spec.title: mapped for spec, mapped in zip(specs, report.ids)})
    for spec in new_specs:
      if spec.parent:
        report.parents[id(spec)] = by_title.get(spec.parent, spec.parent)
  return report


class OutlineCommittedError(StateError):
  """The index was saved; callers must not retry adding the same batch."""

  def __init__(self, report: OutlineReport, error: OSError) -> None:
    super().__init__(f"items saved, but history could not be written: {error}", code="io")
    self.report = report


def apply_outline(
  state: State, specs: list[object], *, force: bool = False,
  preamble: str | None = None, promote_all: bool = False,
  replace_preamble: bool = False, id_floor: int = 0,
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
    replace_preamble=replace_preamble, id_floor=id_floor,
  )
  if report.problems or not report.created:
    return report

  # Publish the in-memory draft only after the index is saved. A failed
  # interactive save must leave the caller able to correct and retry it.
  original = state
  state = State(state.root, cfg, deepcopy(state.index), dict(state.slices),
                dict(state.slice_files))
  # A strict import renders this draft before anything hits disk. The copy
  # has to share the caller's stage, or save_slice writes immediately.
  if original._stage is not None:
    state._stage = original._stage
  if preamble is not None:
    state.index.preamble = preamble

  # Titles resolve to ids only once every entry has one, so allocate first.
  by_title: dict[str, str] = {it.title: it.id for it in state.index.items}
  by_title.update({it.display_title(): it.id for it in state.index.items})
  allocated = list(zip(specs, report.ids))
  _allocate_outline_ids(state.index, report.created, id_floor)
  for spec, new_id in allocated:
    by_title[spec.title] = new_id

  # Build every item and slice against the staged index before writing.
  pending: list[Slice] = []
  for spec, new_id in allocated:
    if state.index.get(new_id) is not None:
      continue
    status = spec.status or cfg.open_status
    item = Item(
      id=new_id,
      key=spec.key,
      title=spec.title,
      short_title=spec.title,
      status=status,
      size=spec.size,
      trees=list(spec.trees),
      findings=spec.findings,
      discovered_from=spec.discovered_from,
      parent=report.parents.get(id(spec), ""),
      # Never inherited from the previous item: an outline says where its own
      # entries belong, and silently filing them under the tail item's pass
      # would be wrong in exactly the case bulk loading is for.
      pass_key=spec.pass_key,
      group=spec.group,
      depends_on=[by_title.get(d, d) for d in report.depends[id(spec)]],
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
    # A staged save recorded the path and wrote nothing. Unlink would delete
    # a file that was already there.
    if state._stage is None:
      for path in written:
        path.unlink(missing_ok=True)
    raise
  original.index, original.slices = state.index, state.slices
  try:
    for (spec, new_id), entry in zip(allocated, report.entries):
      if entry["existing"]:
        continue
      _record(state, new_id, "add", to=spec.status or cfg.open_status, note=spec.title)
  except OSError as exc:
    raise OutlineCommittedError(report, exc) from exc
  return report


def _boundary_in_both(lead: list[str], sections: list[object], marker: str) -> str:
  """The section heading that also carries a boundary, when the lead does too."""
  if not marker or not any(paragraph.startswith(marker) for paragraph in lead):
    return ""
  for section in sections:
    for paragraph in section.body.split("\n\n"):
      if paragraph.startswith(marker):
        return section.heading
  return ""


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
  heading = _boundary_in_both(spec.lead, sections, cfg.boundary)
  if heading:
    raise StateError(
      f"{item.id}: boundary paragraph in the lead and in section {heading!r}",
      code="bad_promote_source",
    )
  boundary = extract_boundary(sections, cfg.boundary, spec.lead) or cfg.boundary
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


# ---- relay role matrix ----------------------------------------------------
# Every change to `.slicer/relay-roles.json` goes through these. Each validates
# before it mutates, so a refused call leaves memory and disk as they were.

ROLES_ACTION = "relay-roles"


def _record_role(
  state: State, suffix: str, note: str, *, frm: str = "", to: str = "",
) -> None:
  """History for a matrix edit, under `relay-roles.r1` or `relay-roles.u1` (`relay-roles` for init)."""
  item = f"{ROLES_ACTION}.{suffix}" if suffix else ROLES_ACTION
  _record(state, item, ROLES_ACTION, frm=frm, to=to, note=note)


def _require_roles(state: State) -> RoleMatrix:
  matrix = state.roles
  if matrix is None:
    raise StateError(
      f"{state.dir / store.ROLES_NAME} does not exist; run `slicer relay-roles init` "
      "to write the example matrix",
      code="no_relay_roles",
    )
  return matrix


def _bad_role_field(message: str) -> StateError:
  return StateError(message, code="bad_role_field")


def _parse_sizes(values: list[str]) -> list[str]:
  """Sizes are free text: any nonblank single-line value, de-duplicated and ordered."""
  sizes: list[str] = []
  for value in values:
    _reject_bad_role_text(size=value)
    if value.strip():
      sizes.append(value.strip())
  return roles.normalize_sizes(sizes)


def _parse_scores(name: str, values: list[str | int]) -> list[int]:
  out: list[int] = []
  for value in values:
    try:
      n = int(value)
    except (TypeError, ValueError):
      raise _bad_role_field(f"{name} must be 1, 2 or 3, not {value!r}") from None
    if n not in roles.SCORES:
      raise _bad_role_field(f"{name} must be 1, 2 or 3, not {n}")
    if n not in out:
      out.append(n)
  return sorted(out)


def _parse_yes_no_any(flag: str, value: str) -> bool | None:
  choices = {"yes": True, "no": False, "any": None}
  if value not in choices:
    raise _bad_role_field(f"{flag} must be yes, no or any, not {value!r}")
  return choices[value]


def parse_yes_no(flag: str, value: str) -> bool:
  """A required yes or no answer, such as `relay-roles recommend --split`."""
  choices = {"yes": True, "no": False}
  if value not in choices:
    raise _bad_role_field(f"{flag} must be yes or no, not {value!r}")
  return choices[value]


def _check_role(role: str) -> str:
  if role not in roles.ROLE_NAMES:
    raise _bad_role_field(f"role {role!r} is not one of {', '.join(roles.ROLE_NAMES)}")
  return role


def _reject_bad_role_text(**fields: str | None) -> None:
  for name, value in fields.items():
    if value is not None and "\n" in value:
      raise StateError(f"{name} cannot contain a newline", code="newline_in_field")


def _rule_text(text: str) -> str:
  if not text.strip():
    raise StateError("a rule cannot be blank", code="blank_title")
  _reject_bad_role_text(rule=text)
  return text.strip()


def _allocate_role_id(prefix: str, number: int, taken: set[str]) -> str:
  new_id = f"{prefix}{number}"
  if new_id in taken:
    raise StateError(
      f"next_{'row' if prefix == 'r' else 'rule'} ({number}) would reuse the existing id "
      f"{new_id}; relay-roles.json is inconsistent",
      code="corrupt",
    )
  return new_id


def roles_init(
  state: State, *, force: bool = False, replace_legacy: bool = False,
) -> tuple[list[Path], list[Path]]:
  """Write the example matrix, or just drop the old Markdown file when a matrix exists.

  Returns the paths written and the paths removed. The JSON is saved before the
  Markdown file is deleted, so a failed write leaves the Markdown file where it was.
  `--force` replaces a matrix and numbers the example rows and rules from its id
  counters, so no row or rule id is ever issued twice; a corrupt matrix has no
  counters, so the example starts at `r1` and `u1`.
  """
  path = state.dir / store.ROLES_NAME
  legacy = state.dir / store.LEGACY_ROLES_NAME
  if path.exists() and not path.is_file():
    raise StateError(f"{path} is not a file", code="io")
  has_legacy = legacy.is_file()
  if path.exists() and not force:
    if not (replace_legacy and has_legacy):
      raise StateError(
        f"{path} already exists; pass --force to replace it. Nothing was written.",
        code="state",
      )
    # The matrix stays; only the file it replaced goes.
    state.delete_file(legacy)
    _record_role(state, "", "remove-legacy")
    return [], [legacy]
  old = None
  if path.exists():
    try:
      old = state.roles
    except StateError as exc:
      if exc.code != "corrupt":
        raise
  fresh = (
    roles.skeleton(first_row=old.next_row, first_rule=old.next_rule)
    if old is not None else roles.skeleton()
  )
  removed = [legacy] if replace_legacy and has_legacy else []
  state.roles = fresh
  state.save_roles()
  for old_file in removed:
    state.delete_file(old_file)
  _record_role(state, "", "init")
  return [path], removed


def role_add(
  state: State, role: str, *, size: list[str] | None = None,
  effort: list[str | int] | None = None, importance: list[str | int] | None = None,
  urgency: list[str | int] | None = None, well_specified: str | None = None,
  split: str | None = None, sliver: str | None = None, agent: str = "", vendor: str = "",
  model: str = "", reasoning: str = "", note: str = "",
) -> RoleRow:
  """Append a row with the next id. The counter only moves forward."""
  matrix = _require_roles(state)
  _check_role(role)
  when = RoleWhen(
    size=_parse_sizes(size or []),
    effort=_parse_scores("effort", effort or []),
    importance=_parse_scores("importance", importance or []),
    urgency=_parse_scores("urgency", urgency or []),
    well_specified=_parse_yes_no_any("well-specified", well_specified or "any"),
    split=_parse_yes_no_any("split", split or "any"),
    sliver=_parse_yes_no_any("sliver", sliver or "any"),
  )
  _reject_bad_role_text(agent=agent, vendor=vendor, model=model, reasoning=reasoning, note=note)
  row = RoleRow(
    id=_allocate_role_id("r", matrix.next_row, {r.id for r in matrix.rows}),
    role=role, when=when, agent=agent, vendor=vendor, model=model, reasoning=reasoning,
    note=note,
  )
  matrix.next_row += 1
  matrix.rows.append(row)
  state.save_roles()
  _record_role(state, row.id, "add", to=role)
  return row


def role_set(
  state: State, row_id: str, *, role: str | None = None, size: list[str] | None = None,
  effort: list[str | int] | None = None, importance: list[str | int] | None = None,
  urgency: list[str | int] | None = None, well_specified: str | None = None,
  split: str | None = None, sliver: str | None = None, agent: str | None = None,
  vendor: str | None = None, model: str | None = None, reasoning: str | None = None,
  note: str | None = None,
) -> tuple[RoleRow, list[str]]:
  """Change only the fields given. Returns the row and the names that changed."""
  row = _require_roles(state).row(row_id)
  if role is not None:
    _check_role(role)
  when = RoleWhen.from_dict(row.when.to_dict())
  if size is not None:
    when.size = _parse_sizes(size)
  if effort is not None:
    when.effort = _parse_scores("effort", effort)
  if importance is not None:
    when.importance = _parse_scores("importance", importance)
  if urgency is not None:
    when.urgency = _parse_scores("urgency", urgency)
  if well_specified is not None:
    when.well_specified = _parse_yes_no_any("well-specified", well_specified)
  if split is not None:
    when.split = _parse_yes_no_any("split", split)
  if sliver is not None:
    when.sliver = _parse_yes_no_any("sliver", sliver)
  texts = {"agent": agent, "vendor": vendor, "model": model, "reasoning": reasoning, "note": note}
  _reject_bad_role_text(**texts)
  wanted: dict[str, object] = {"role": role, **texts}
  changed = [
    name for name, value in wanted.items() if value is not None and value != getattr(row, name)
  ]
  when_changed = [
    name for name, value in when.to_dict().items() if value != row.when.to_dict()[name]
  ]
  if not changed and not when_changed:
    return row, []
  for name in changed:
    setattr(row, name, wanted[name])
  row.when = when
  changed += when_changed
  state.save_roles()
  _record_role(state, row.id, "set", to=",".join(changed))
  return row, changed


def role_remove(state: State, row_id: str) -> RoleRow:
  matrix = _require_roles(state)
  row = matrix.row(row_id)
  matrix.rows.remove(row)
  state.save_roles()
  _record_role(state, row.id, "remove", to=row.role)
  return row


def role_move(
  state: State, row_id: str, *, before: str | None = None, last: bool = False,
) -> tuple[RoleRow, bool]:
  """Place a row before another, or at the end. Returns the row and whether it moved."""
  if (before is None) == (not last):
    raise StateError("choose exactly one of --before or --last", code="usage")
  matrix = _require_roles(state)
  row = matrix.row(row_id)
  anchor = matrix.row(before) if before is not None else None
  if anchor is not None and anchor.id == row.id:
    raise StateError("a row cannot be moved before itself", code="usage")
  old_order = [r.id for r in matrix.rows]
  rest = [r for r in matrix.rows if r.id != row.id]
  at = len(rest) if anchor is None else [r.id for r in rest].index(anchor.id)
  rest.insert(at, row)
  if [r.id for r in rest] == old_order:
    return row, False
  matrix.rows = rest
  state.save_roles()
  _record_role(
    state, row.id, "move", frm=str(old_order.index(row.id) + 1),
    to=str([r.id for r in rest].index(row.id) + 1),
  )
  return row, True


def rule_add(state: State, text: str) -> RoleRule:
  matrix = _require_roles(state)
  text = _rule_text(text)
  rule = RoleRule(
    id=_allocate_role_id("u", matrix.next_rule, {r.id for r in matrix.rules}), text=text,
  )
  matrix.next_rule += 1
  matrix.rules.append(rule)
  state.save_roles()
  _record_role(state, rule.id, "rule-add")
  return rule


def rule_edit(state: State, rule_id: str, text: str) -> tuple[RoleRule, bool]:
  rule = _require_roles(state).rule(rule_id)
  text = _rule_text(text)
  if text == rule.text:
    return rule, False
  rule.text = text
  state.save_roles()
  _record_role(state, rule.id, "rule-edit")
  return rule, True


def rule_remove(state: State, rule_id: str) -> RoleRule:
  matrix = _require_roles(state)
  rule = matrix.rule(rule_id)
  matrix.rules.remove(rule)
  state.save_roles()
  _record_role(state, rule.id, "rule-remove")
  return rule


# ---- relay run ledger -----------------------------------------------------
# The ledger (`<git common dir>/slicer/relay-runs.jsonl`, or `.slicer/relay-runs.jsonl`
# outside git) is an untracked, append-only log, not roadmap state: nothing here touches
# the index, history or render. Each call validates before it appends, so a refused call
# leaves the ledger as it was, and holds the ledger lock across the append.
#
# A role whose sandbox may write only inside its slice worktree cannot write the common
# dir. A write the shared ledger or its lock refuses goes to the checkout's own
# `.slicer/relay-runs.jsonl` under the project lock instead, one write at a time; the
# readers fold both, and `relay_run_end` copies a run's local events into the shared ledger.

@dataclass
class LedgerReport:
  """What a relay-run write did beyond the write, for the CLI to tell the caller."""

  fell_back_from: Path | None = None  # the shared ledger it could not write
  wrote_to: Path | None = None  # the checkout's own file that took the events instead
  absorbed: int = 0  # local events copied into the shared ledger
  unreadable: list[Path] = field(default_factory=list)  # local ledgers skipped as damaged
  invalid: int = 0  # local events not copied because they would not fold


def _denied(exc: OSError) -> bool:
  """A sandbox or a read-only mount said no, as opposed to a ledger that is broken."""
  return isinstance(exc, PermissionError) or exc.errno in (errno.EPERM, errno.EACCES, errno.EROFS)


class Ledger:
  """Where this call's events go: the shared ledger, or after a refusal the local file."""

  def __init__(self, root: Path, report: LedgerReport, stack: ExitStack, shared: bool) -> None:
    self.root = root
    self.report = report
    self.stack = stack
    self.can_fall_back = shared
    self.fallback = False

  def fall_back(self) -> None:
    """Switch to the checkout's own file, serialised by the project lock."""
    if self.fallback:
      return
    self.stack.enter_context(store.project_lock(self.root))
    self.fallback = True
    self.report.fell_back_from = relayrun.ledger_path(self.root)
    self.report.wrote_to = relayrun.legacy_ledger_path(self.root)

  def append_shared(self, event: dict[str, object], *, absorbing: bool = False) -> bool:
    """Append to the shared ledger. False once it is refused and the call is local.

    The shared ledger is read strictly, so an event for a run it holds no begin of would make
    it `corrupt` once the file that does is gone. The run's local events are copied first.
    """
    if self.fallback:
      return False
    if self.can_fall_back and not absorbing and event["event"] != "begin":
      shared = relayrun.ledger_path(self.root)
      run_id = str(event["run"])
      if not any(e["event"] == "begin" and e["run"] == run_id for _, e in relayrun.read_events(shared)):
        _absorb(self.root, self, run_id)
        if self.fallback:
          return False
    try:
      relayrun.append(self.root, event)
      return True
    except OSError as exc:
      if not (self.can_fall_back and _denied(exc)):
        raise
    self.fall_back()
    return False

  def append(self, event: dict[str, object]) -> None:
    self.append_planned(lambda prefix: event)

  def append_planned(self, plan: Callable[[str], dict[str, object]]) -> dict[str, object]:
    """Append the event `plan` builds for whichever ledger this call writes to, and return it.

    `plan` takes the step-id prefix of that ledger (`""` for the shared one). It runs under the
    lock of the ledger it is for: the shared lock, or the project lock once the write has fallen
    back. After a refused shared write it runs again, so a step is numbered from what the
    checkout's own file holds when the step is written.
    """
    if not self.fallback:
      event = plan("")
      if self.append_shared(event):
        return event
    event = plan(relayrun.fallback_step_prefix(self.root))
    local = relayrun.legacy_ledger_path(self.root)
    relayrun.read_events(local)  # a damaged file is refused before anything is added to it
    relayrun.append(self.root, event, path=local)
    return event


@contextmanager
def ledger_lock(root: Path, report: LedgerReport | None = None) -> Iterator[Ledger]:
  """Serialise appenders to the ledger `root` writes to, and say where an event goes.

  The shared ledger is written from every worktree, and worktrees do not share
  `.slicer/lock`, so it has a lock file beside it, taken like `store.project_lock`. A ledger
  inside the checkout is guarded by the project lock as before. When the lock file cannot be
  opened because the sandbox refuses writes there, the yielded `Ledger` is already on the
  checkout's own file under the project lock.
  """
  common = relayrun.common_ledger_path(root)
  with ExitStack() as stack:
    ledger = Ledger(root, report if report is not None else LedgerReport(), stack, common is not None)
    if common is None:
      stack.enter_context(store.project_lock(root))
    elif store.fcntl is not None:
      try:
        stack.enter_context(store.file_lock(common.with_name(relayrun.COMMON_LOCK_NAME)))
      except OSError as exc:
        if not _denied(exc):
          raise
        ledger.fall_back()
    yield ledger


def _open_run(root: Path, run_id: str, now: datetime) -> relayrun.Run:
  """The run, in any ledger `root` reads, that a step or end may extend."""
  run = next((r for r in relayrun.load_all(root, now) if r.id == run_id), None)
  if run is None:
    raise StateError(
      f"no relay run {run_id!r} in {relayrun.ledger_path(root)} or the checkouts' own "
      f"{relayrun.LEDGER_NAME}; `relay-run begin` makes one and `relay-run list` shows them. "
      "In a git repo every worktree shares that ledger",
      code="unknown_run",
    )
  if run.state == "ended":
    raise StateError(
      f"relay run {run_id} ended with outcome {run.outcome}; begin a new run instead",
      code="run_ended",
    )
  return run


def _absorb(root: Path, ledger: Ledger, run_id: str) -> None:
  """Copy the run's events from the local ledgers into the shared one, those not yet there."""
  have = {relayrun.event_key(event) for _, event in relayrun.read_events(relayrun.ledger_path(root))}
  events, damaged, invalid = relayrun.local_run_events(root, run_id)
  ledger.report.unreadable = damaged
  ledger.report.invalid = invalid
  for event in events:
    key = relayrun.event_key(event)
    if key in have:
      continue
    if not ledger.append_shared(event, absorbing=True):
      return
    have.add(key)
    ledger.report.absorbed += 1


def relay_run_begin(
  root: Path, items: list[str], worktree: str | None = None, *, now: datetime | None = None,
  report: LedgerReport | None = None,
) -> relayrun.Run:
  """Start a run on these items. `worktree` defaults to the checkout it is recorded in."""
  now = now or relayrun.utcnow()
  items = [relayrun.one_line("item", item, required=True) for item in items]
  if not items:
    raise relayrun.bad_field("a run needs at least one item")
  if any(re.search(r"\s", item) for item in items):
    raise relayrun.bad_field("an item id may not contain whitespace")
  if worktree is not None:
    relayrun.one_line("worktree", worktree, required=True)
  name = Path(worktree).resolve().name if worktree is not None else Path(root).resolve().name
  name = relayrun.one_line("worktree", name, required=True)
  with ledger_lock(root, report) as ledger:
    # A damaged ledger is refused before anything is added to it.
    taken = {run.id for run in relayrun.load_all(root, now)}
    while True:
      run_id = f"run-{now.astimezone(timezone.utc):%Y%m%d-%H%M%S}-{uuid4().hex[:4]}"
      if run_id not in taken:
        break
    ledger.append(relayrun.begin_event(run_id, now, items, name))
    return next(r for r in relayrun.load_all(root, now) if r.id == run_id)


def relay_run_step(
  root: Path, run_id: str, *, phase: str, step: str | None = None, role: str = "",
  agent: str = "", vendor: str = "", model: str = "", reasoning: str = "", task: str = "",
  parent: str = "", now: datetime | None = None, report: LedgerReport | None = None,
) -> tuple[relayrun.Run, relayrun.Step]:
  """Record an agent's step, or with `step` a later phase of one already recorded.

  A new step needs a role and an agent. `parent` nests a subagent under the step that
  spawned it. An update names only what changes; the rest stays as recorded. A task that
  differs from the one the step was recorded with is refused, though a step recorded
  without one may take its first: a new task is a new step. A new step's id is a plain
  number in the shared ledger and carries `relayrun.fallback_step_prefix` in the checkout's
  own file, so the two writers never issue the same id.
  """
  now = now or relayrun.utcnow()
  relayrun.choice("phase", phase, relayrun.PHASES)
  fields = {
    "role": relayrun.one_line("role", role), "agent": relayrun.one_line("agent", agent),
    "vendor": relayrun.one_line("vendor", vendor), "model": relayrun.one_line("model", model),
    "reasoning": relayrun.one_line("reasoning", reasoning), "task": relayrun.one_line("task", task),
  }
  parent = relayrun.one_line("parent", parent)

  def plan(prefix: str) -> dict[str, object]:
    """The event for this call, numbered with `prefix` and checked against the run as it is."""
    run = _open_run(root, run_id, now)
    if step is None:
      for name in ("role", "agent"):
        if not fields[name]:
          raise relayrun.bad_field(f"a new step needs --{name}")
      if parent and run.step(parent) is None:
        raise relayrun.bad_field(f"parent step {parent!r} is not in run {run_id}")
      step_id = relayrun.next_step_id(run, prefix)
    else:
      recorded = run.step(step)
      if recorded is None:
        raise relayrun.bad_field(f"step {step!r} is not in run {run_id}")
      if parent:
        raise relayrun.bad_field("a step's parent is set when it is created, not on update")
      if fields["task"] and recorded.task and fields["task"] != recorded.task:
        raise relayrun.bad_field(
          f"step {step} is recorded with task {recorded.task!r}, not {fields['task']!r}. "
          "Record a new step without --step for another task, or leave out --task to keep this one"
        )
      step_id = step
    return relayrun.step_event(run_id, now, step_id, phase, parent, **fields)

  with ledger_lock(root, report) as ledger:
    written = ledger.append_planned(plan)
    run = _open_run(root, run_id, now)
    return run, run.step(str(written["step"]))


def relay_run_end(
  root: Path, run_id: str, outcome: str, *, now: datetime | None = None,
  report: LedgerReport | None = None,
) -> relayrun.Run:
  """Close the run. When the shared ledger can be written, the run's events from the local
  ledgers are copied into it first, so removing a worktree afterwards loses none of them."""
  now = now or relayrun.utcnow()
  relayrun.choice("outcome", outcome, relayrun.OUTCOMES)
  with ledger_lock(root, report) as ledger:
    _open_run(root, run_id, now)
    if ledger.can_fall_back and not ledger.fallback:
      _absorb(root, ledger, run_id)
    ledger.append(relayrun.end_event(run_id, now, outcome))
    return next(r for r in relayrun.load_all(root, now) if r.id == run_id)
