"""Read-only queries over the roadmap: what to work on next and who holds what.

These used to sit in `ops.py` beside the writers. They only read a `State`, so they
live here: `ops.py` imports this module and not the other way round, and a test pins that
this module imports none of a fixed list of writer modules and calls none of a fixed list
of write methods.
The ranking behind `next`, `next --batch` and `plan-parallel`, the path and
readiness helpers they share, and the actor and queue checks the lifecycle writers
call before they change anything are here.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import TYPE_CHECKING, Callable, Mapping

from slicer import graph, ids, paths, vcs
from slicer.config import Config
from slicer.errors import StateError
from slicer.model import Item, readiness_of

if TYPE_CHECKING:
  from slicer.store import State


def actor(state: State, override: str | None = None) -> str:
  """Who a claim or lifecycle action names; see `vcs.identity` for the order."""
  return vcs.identity(state.root, state.config.claim_owner, override)


def slivers_of(state: State, item_id: str) -> list[Item]:
  """Items whose `parent` names this one, in index order."""
  return [i for i in state.index.items if i.parent == item_id]


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


def held_flags(state: State, item: Item) -> list[str]:
  """The configured flags holding this item out of selection, in item order."""
  return [flag for flag in item.flags if flag in state.config.hold_flags]


@dataclass
class NextResult:
  item: Item | None
  blocked: list[tuple[str, list[str]]]
  held: list[dict[str, object]] = field(default_factory=list)
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
  held: list[dict[str, object]] = field(default_factory=list)
  unspecified: list[tuple[str, list[str]]] = field(default_factory=list)
  elsewhere: list[tuple[str, list[dict[str, str]]]] = field(default_factory=list)
  capped: list[dict[str, int | str]] = field(default_factory=list)
  # Custom-queue items skipped because another owner holds the claim.
  claimed_elsewhere: list[dict[str, str]] = field(default_factory=list)
  # `--disjoint` only: eligible items whose files section names no path.
  unknown_files: list[str] = field(default_factory=list)


@dataclass
class _NextPool:
  items: list[Item]
  blocked: list[tuple[str, list[str]]]
  held: list[dict[str, object]] = field(default_factory=list)
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
  prefer_ready: bool = False,
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

  With `prefer_ready`, items whose readiness is `True` move ahead of the rest
  within their group: the same tier (started before open), the same release target
  and the same planned wave, or for a custom `queue` the same claimed-or-not side.
  It never moves an item across release targets or waves. It only reorders: no item enters or leaves the pool, and a stale or
  missing record counts as not ready.
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
  held: list[dict[str, object]] = []
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
    holding = held_flags(state, item)
    if holding:
      held.append({"id": item.id, "title": item.title, "flags": holding})
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
  if prefer_ready:
    if queue is not None:
      def group(item: Item) -> tuple[int, ...]:
        return (0 if item.claim_owner else 1, 0, 0)
    else:
      def group(item: Item) -> tuple[int, ...]:
        return (
          graph.tier(state.index, cfg, item), graph.pass_rank(state.index, item),
          graph.wave_rank(state.index, item),
        )
    ranked = _ready_first(state, ranked, group)
  return _NextPool(
    items=ranked, blocked=blocked, held=held, unspecified=unspecified,
    elsewhere=skipped, capped=capped, claimed_elsewhere=claimed_elsewhere,
  )


def _ready_first(
  state: State, ranked: list[Item], group: Callable[[Item], tuple[int, ...]],
) -> list[Item]:
  """Reorder `ranked` so ready items lead inside each group, and nothing else moves.

  The members of one group keep the slots they already occupy; only who fills
  those slots changes, ready items first and each side in its old order. An item
  therefore never crosses a group, and an item of another group never shifts.
  """
  slots: dict[tuple[int, ...], list[int]] = {}
  for at, item in enumerate(ranked):
    slots.setdefault(group(item), []).append(at)
  verdict = {item.id: ready_state(state, item) is True for item in ranked}
  out = list(ranked)
  for positions in slots.values():
    members = sorted((ranked[at] for at in positions), key=lambda it: not verdict[it.id])
    for at, item in zip(positions, members):
      out[at] = item
  return out


def next_item(
  state: State, offset: int = 0,
  elsewhere: Mapping[str, list[dict[str, str]]] | None = None,
  *, review: bool = False, tree: str | None = None, size: str | None = None,
  max_attempts: int | None = None,
  flags: list[str] | None = None, no_flags: list[str] | None = None,
  queue: str | None = None,
  owner: str | None = None, start_to: str | None = None,
  prefer_ready: bool = False,
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
    queue=queue, holder=holder, cap_queue=cap_queue, prefer_ready=prefer_ready,
  )
  return NextResult(
    item=pool.items[offset] if offset < len(pool.items) else None,
    blocked=pool.blocked,
    held=pool.held,
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
  disjoint: bool = False, prefer_ready: bool = False,
) -> BatchResult:
  """Up to `count` items from the next pool, dependencies before dependents.

  The pool includes items that are still blocked. Each pick is the first item
  not yet chosen whose unmet dependencies are already in the batch, so a
  dependent follows its dependency even when the dependent ranks higher.
  Selection stops at `count`. An item left out only because the batch is full
  is not reported as blocked. An item still waiting on something outside the
  batch is, and the reported dependencies are the ones the batch did not take.

  With `disjoint`, an item is also skipped when a path its files section names is
  already named by a chosen item (paths in `overlap_ignore` never count), and an
  item whose section names no path is skipped and listed in `unknown_files`.
  Pending dependencies remain blockers even when already chosen in this mode.
  """
  if isinstance(count, bool) or not isinstance(count, int) or count < 1:
    raise StateError("next --batch must be an integer >= 1", code="usage")
  if disjoint:
    _require_files_section(state.config, "next --batch --disjoint")
  top_level = {entry.name for entry in state.root.iterdir()} if disjoint else set()
  pool = _ranked_pool(
    state, elsewhere, tree=tree, size=size, include_blocked=True,
    max_attempts=max_attempts, flags=flags, no_flags=no_flags, prefer_ready=prefer_ready,
  )
  chosen: list[Item] = []
  chosen_ids: set[str] = set()
  taken: set[str] = set()
  unknown_files: list[str] = []
  while len(chosen) < count:
    pick: Item | None = None
    for item in pool.items:
      if item.id in chosen_ids:
        continue
      unmet = [dep for dep in graph.pending(state.index, item, state.config) if disjoint or dep not in chosen_ids]
      if unmet:
        continue
      if disjoint:
        named = item_paths(state, item.id, top_level=top_level)
        if not named:
          if item.id not in unknown_files:
            unknown_files.append(item.id)
          continue
        if taken.intersection(counted_paths(state.config, named)):
          continue
      pick = item
      break
    if pick is None:
      break
    chosen.append(pick)
    chosen_ids.add(pick.id)
    if disjoint:
      taken.update(counted_paths(state.config, item_paths(state, pick.id, top_level=top_level)))
  blocked: list[tuple[str, list[str]]] = []
  for item_id, pending in pool.blocked:
    if item_id in chosen_ids:
      continue
    unmet = [dep for dep in pending if disjoint or dep not in chosen_ids]
    if unmet:
      blocked.append((item_id, unmet))
  return BatchResult(
    items=chosen,
    blocked=blocked,
    held=pool.held,
    unspecified=pool.unspecified,
    elsewhere=pool.elsewhere,
    capped=pool.capped,
    unknown_files=unknown_files,
  )


@dataclass
class ParallelPlan:
  """Parallel sets of items that can run side by side, plus the skip lists `next` reports.

  `waves` is the field and JSON key's old name for those sets; they are computed from
  dependencies and Files paths, and are not the owner's planned waves (`Item.wave`).
  `wave_conflicts` reports items planned ahead of a dependency, and changes nothing
  else. Each set is a list of `{id, title, reason}` rows; `reason` is empty for an
  item that was ready from the start. `in_flight` is the started work whose
  paths hold wave 1.
  """

  width: int
  waves: list[list[dict[str, str]]]
  in_flight: list[dict[str, str]]
  unplaced: list[dict[str, str]]
  blocked: list[tuple[str, list[str]]]
  held: list[dict[str, object]] = field(default_factory=list)
  unspecified: list[tuple[str, list[str]]] = field(default_factory=list)
  elsewhere: list[tuple[str, list[dict[str, str]]]] = field(default_factory=list)
  capped: list[dict[str, int | str]] = field(default_factory=list)
  claimed_elsewhere: list[dict[str, str]] = field(default_factory=list)
  wave_conflicts: list[dict[str, str | int]] = field(default_factory=list)

  def to_dict(self) -> dict[str, object]:
    out: dict[str, object] = {
      "width": self.width,
      "waves": [
        {"wave": at, "items": [dict(row) for row in rows]}
        for at, rows in enumerate(self.waves, start=1)
      ],
      "in_flight": [dict(row) for row in self.in_flight],
      "unplaced": [dict(row) for row in self.unplaced],
      "blocked": [{"id": item_id, "waiting_on": waiting} for item_id, waiting in self.blocked],
    }
    # Only when the owner planned an item ahead of its dependency, so a roadmap with
    # no waves gets the same JSON it always did.
    if self.wave_conflicts:
      out["wave_conflicts"] = [dict(row) for row in self.wave_conflicts]
    return out


def plan_parallel(
  state: State, width: int,
  elsewhere: Mapping[str, list[dict[str, str]]] | None = None,
  *, tree: str | None = None, size: str | None = None,
  max_attempts: int | None = None,
  flags: list[str] | None = None, no_flags: list[str] | None = None,
) -> ParallelPlan:
  """Lay the open work out in parallel sets of at most `width` items that can run in parallel.

  The owner's planned wave (`Item.wave`) is not used to place anything; a dependency
  planned in a later wave is only reported, in `wave_conflicts`.

  Started items are in flight: their paths count as taken for wave 1 only. Each
  wave then fills greedily in `next` order with items not yet placed whose
  pending dependencies are done or in an earlier wave and whose paths (less
  `overlap_ignore`) miss every path already taken in that wave. A wave's paths
  are released for the next one. If in-flight paths hold back every candidate,
  the first wave is the first one that can place something.

  An item placed after the first pass carries the reason it was held back in the
  pass before: `after: ID` (a dependency in that wave), `shares: ID PATH` (a
  path another item in that wave holds, or in-flight work for wave 1) or
  `width` (the wave was full). An item whose files section names no path is
  `unplaced` with reason `no_files`. An item still waiting on a dependency that
  is never placed is `blocked`. Held, unspecified, capped and sibling-held items
  are reported as `next` reports them. Read-only.
  """
  if isinstance(width, bool) or not isinstance(width, int) or width < 1:
    raise StateError("plan-parallel --width must be an integer >= 1", code="usage")
  cfg = state.config
  _require_files_section(cfg, "plan-parallel")
  top_level = {entry.name for entry in state.root.iterdir()}
  pool = _ranked_pool(
    state, elsewhere, tree=tree, size=size, include_blocked=True,
    max_attempts=max_attempts, flags=flags, no_flags=no_flags,
  )
  in_flight_items = [
    item for item in state.index.items if cfg.started_status and item.status == cfg.started_status
  ]
  wave_of = {item.id: 0 for item in in_flight_items}
  taken_by_flight: dict[str, str] = {}
  for item in in_flight_items:
    for path in counted_paths(cfg, item_paths(state, item.id, top_level=top_level)):
      taken_by_flight.setdefault(path, item.id)
  unplaced: list[dict[str, str]] = []
  counted: dict[str, list[str]] = {}
  remaining: list[Item] = []
  for item in pool.items:
    if item.id in wave_of:
      continue
    named = item_paths(state, item.id, top_level=top_level)
    if not named:
      unplaced.append({"id": item.id, "title": item.title, "reason": "no_files"})
      continue
    counted[item.id] = counted_paths(cfg, named)
    remaining.append(item)
  waves: list[list[dict[str, str]]] = []
  held_back: dict[str, str] = {}
  first = True
  while remaining:
    taken = dict(taken_by_flight) if first else {}
    placed: list[Item] = []
    later: list[Item] = []
    for item in remaining:
      unmet = [dep for dep in graph.pending(state.index, item, cfg) if dep not in wave_of]
      if unmet:
        held_back[item.id] = f"after: {unmet[0]}"
        later.append(item)
        continue
      clash = next(((taken[path], path) for path in counted[item.id] if path in taken), None)
      if clash is not None:
        held_back[item.id] = f"shares: {clash[0]} {clash[1]}"
        later.append(item)
        continue
      if len(placed) >= width:
        held_back[item.id] = "width"
        later.append(item)
        continue
      placed.append(item)
      for path in counted[item.id]:
        taken.setdefault(path, item.id)
    if not placed:
      if first and taken_by_flight:
        first = False
        continue
      break
    rows: list[dict[str, str]] = []
    for item in placed:
      reason = held_back.get(item.id, "")
      if not reason:
        flying = [dep for dep in item.depends_on if wave_of.get(dep) == 0]
        reason = f"after: {flying[0]}" if flying else ""
      rows.append({"id": item.id, "title": item.title, "reason": reason})
    waves.append(rows)
    for item in placed:
      wave_of[item.id] = len(waves)
    remaining = later
    first = False
  # Items left over after the last wave wait on what was never placed. A blocked
  # item the pool dropped (held by a sibling worktree, capped, unspecified) is
  # not in `remaining`; it keeps every dependency it was waiting on.
  left = {item.id for item in remaining}
  settled = {row["id"] for row in unplaced} | set(wave_of)
  blocked: list[tuple[str, list[str]]] = []
  for item_id, pending in pool.blocked:
    if item_id in settled:
      continue
    unmet = [dep for dep in pending if dep not in wave_of] if item_id in left else pending
    if unmet:
      blocked.append((item_id, unmet))
  return ParallelPlan(
    width=width,
    waves=waves,
    in_flight=[{"id": item.id, "title": item.title} for item in in_flight_items],
    unplaced=unplaced,
    blocked=blocked,
    held=pool.held,
    unspecified=pool.unspecified,
    elsewhere=pool.elsewhere,
    capped=pool.capped,
    wave_conflicts=graph.wave_conflicts(state.index, cfg),
  )


def _require_files_section(cfg: Config, what: str) -> None:
  if not cfg.files_section:
    raise StateError(
      f"{what} needs files_section: name the section that lists an item's files with "
      "`slicer config files_section NAME`",
      code="usage",
    )


def files_section_warning(state: State) -> str | None:
  """Explain a configured heading absent from every item's slice."""
  heading = state.config.files_section
  if heading and not any(
    sl is not None and sl.section(heading) is not None
    for item in state.index.items
    for sl in [state.slices.get(item.id)]
  ):
    return (
      f"no item has the configured files_section {heading!r}; "
      "run `slicer config files_section NAME` to name the files heading"
    )
  return None


def item_paths(state: State, item_id: str, *, top_level: Collection[str]) -> list[str]:
  """Paths in an item's `files_section`, using names from the project root.

  No paths when the item has no slice or section. The caller supplies the root
  names once per operation, so comparing items does not repeatedly list it.
  """
  sl = state.slices.get(item_id)
  section = sl.section(state.config.files_section) if sl is not None else None
  if section is None:
    return []
  return paths.extract(section.body, top_level=top_level)


def item_file_paths(state: State, item_id: str) -> list[str]:
  """An item's `files_section` paths as `show --json` reports them.

  The same spans `item_paths` gives `deps --overlap`, before `overlap_ignore` removes
  any, so a consumer checking a diff sees every path the Files list names. Unlike
  `path_overlap`, a missing `files_section` is not `usage`: `show` answers for every
  item, so that item has no paths. The project root is listed only when the section
  has text to read: `promote` seeds an empty Files section, which names nothing.
  """
  sl = state.slices.get(item_id)
  section = sl.section(state.config.files_section) if sl is not None else None
  if section is None or not section.body.strip():
    return []
  top_level = {entry.name for entry in state.root.iterdir()}
  return paths.extract(section.body, top_level=top_level)


def counted_paths(cfg: Config, named: list[str]) -> list[str]:
  """The paths that can overlap: those not listed in `overlap_ignore`."""
  return [path for path in named if path not in cfg.overlap_ignore]


def path_overlap(state: State, item_ids: list[str]) -> list[dict[str, object]]:
  """The paths each pair of items shares, in argument order and the first item's path order."""
  _require_files_section(state.config, "deps --overlap")
  for item_id in item_ids:
    state.index.require(item_id)
  top_level = {entry.name for entry in state.root.iterdir()}
  counted = {
    item_id: counted_paths(state.config, item_paths(state, item_id, top_level=top_level))
    for item_id in item_ids
  }
  pairs: list[dict[str, object]] = []
  for at, first in enumerate(item_ids):
    for second in item_ids[at + 1:]:
      shared = [path for path in counted[first] if path in counted[second]]
      pairs.append({"a": first, "b": second, "shared": shared})
  return pairs


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


def ready_state(state: State, item: Item) -> bool | str:
  """`item`'s readiness now: `True`, `False` or `"stale"`. Loads the slice only
  when a record exists, so an unchecked item costs no file read."""
  if item.readiness is None:
    return False
  return readiness_of(item, state.slices.get(item.id))


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
