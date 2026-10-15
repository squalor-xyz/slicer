"""Integrity checks, offline and against version control.

The protocol this tool serves says the index is a claim and history is the
fact. `verify` is where that comparison stops being a manual chore — but it
reports, it never rewrites: a mismatch needs a human to say which side is
wrong.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from slicer import graph, ids, vcs
from slicer.errors import StateError
from slicer.model import CATALOG_ACTIVE, CATALOG_RETIRED, Index, successor_cycles
from slicer.store import INDEX_NAME, ROLES_NAME, State


@dataclass
class Finding:
  level: str
  item: str
  message: str

  def to_dict(self) -> dict[str, str]:
    return {"level": self.level, "item": self.item, "message": self.message}


@dataclass
class VerifyReport:
  findings: list[Finding] = field(default_factory=list)
  checked: int = 0
  git: bool = False

  @property
  def problems(self) -> list[Finding]:
    return [f for f in self.findings if f.level == "error"]

  def to_dict(self) -> dict[str, object]:
    return {
      "checked": self.checked,
      "git": self.git,
      "errors": len(self.problems),
      "findings": [f.to_dict() for f in self.findings],
    }


@dataclass
class LandedReport:
  """The answer to "is this item's done state reachable from REF?".

  `reason` is the stable word an agent branches on (`not_done`,
  `working_tree_only`, `other_branch`); it is empty when the item landed.
  `commit` is the commit that put the done slice file in place, when one is known.
  """

  id: str
  ref: str
  ref_commit: str
  landed: bool
  reason: str = ""
  message: str = ""
  commit: str = ""

  def to_dict(self) -> dict[str, object]:
    return {
      "id": self.id,
      "ref": self.ref,
      "ref_commit": self.ref_commit,
      "landed": self.landed,
      "reason": self.reason,
      "message": self.message,
      "commit": self.commit,
    }


def offline(state: State) -> VerifyReport:
  """Everything checkable without touching git."""
  cfg = state.config
  index = state.index
  report = VerifyReport(checked=len(index.items))

  if index.items and (index.id_prefix, index.id_width) != (cfg.id_prefix, cfg.id_width):
    case_only = (
      index.id_width == cfg.id_width
      and index.id_prefix.casefold() == cfg.id_prefix.casefold()
    )
    fix = (
      f"Restore the config, or run `slicer id-prefix {cfg.id_prefix}` to change the case "
      f"of the prefix in both."
      if case_only else "Restore the config, or start a new project."
    )
    report.findings.append(
      Finding(
        "error",
        "",
        f"id scheme in config ({cfg.id_prefix!r} width {cfg.id_width}) does not match "
        f"the index ({index.id_prefix!r} width {index.id_width}); the index wins, "
        f"because ids are never reused. {fix}",
      )
    )

  water = ids.high_water([it.id for it in index.items], index.id_prefix)
  if index.next_id < water:
    report.findings.append(
      Finding(
        "error",
        "",
        f"next_id is {index.next_id}, at or below an id already in use "
        f"(it should be at least {water}); the next `add` would reuse an id. "
        f"Set next_id to {water} in index.json.",
      )
    )

  seen: set[str] = set()
  keys: set[str] = set()
  for item in index.items:
    if item.id in seen:
      report.findings.append(Finding("error", item.id, "duplicate id in the index"))
    seen.add(item.id)
    if item.key:
      if item.key in keys:
        report.findings.append(Finding("error", item.id, f"duplicate filing key {item.key!r}"))
      keys.add(item.key)
    if not ids.is_valid(item.id):
      # An error, not a warning: with the path boundary enforced, nothing can
      # promote, move or retire this item, so the project really is broken.
      report.findings.append(
        Finding(
          "error",
          item.id,
          "id cannot be used as a filename; it must start with a letter or digit "
          "and contain only letters, digits, '.', '-' and '_'. An id is never "
          "renamed, so the fix is `slicer remove --purge` and add it again",
        )
      )
      # Everything below needs the id as a path; one finding says enough.
      continue
    if item.status not in cfg.statuses:
      report.findings.append(Finding("error", item.id, f"unknown status {item.status!r}"))
    path = state.find_slice_file(item.id)
    if item.has_slice and path is None:
      report.findings.append(Finding("error", item.id, "marked as having a slice, but no file exists"))
    if not item.has_slice and path is not None:
      report.findings.append(
        Finding("error", item.id, "has a slice file on disk but is not marked as having a slice")
      )
    if path is not None and path != state.slice_path(item.id):
      report.findings.append(
        Finding("error", item.id, f"slice file is in the wrong folder for status {item.status!r}")
      )
    sl = state.slices.get(item.id)
    if sl is not None and cfg.boundary and not sl.boundary:
      report.findings.append(
        Finding("warn", item.id, f"no {cfg.boundary} boundary; scope is unbounded")
      )

  for item in index.items:
    if item.discovered_from and index.get(item.discovered_from) is None:
      report.findings.append(Finding(
        "error", item.id, f"discovered_from unknown id {item.discovered_from}",
      ))
    if item.parent:
      parent = index.get(item.parent)
      if item.parent == item.id:
        report.findings.append(Finding("error", item.id, "parent is itself"))
      elif parent is None:
        report.findings.append(Finding("error", item.id, f"parent unknown id {item.parent}"))
      elif parent.parent:
        report.findings.append(Finding(
          "error", item.id,
          f"parent {parent.id} has a parent ({parent.parent}); a sliver cannot have slivers",
        ))
    for record_id in item.cites:
      if index.catalog.get(record_id) is None:
        report.findings.append(Finding(
          "error", item.id, f"cites unknown id {record_id}",
        ))

  for item_id, dep in graph.dangling(index):
    report.findings.append(Finding("error", item_id, f"depends on unknown id {dep}"))
  if cfg.retired_status and cfg.retired_status not in cfg.satisfying_statuses():
    # A retired or done dependent will not be started, so an edge onto a
    # retired item is history. Live work is still an error.
    settled = {status for status in (cfg.retired_status, cfg.done_status) if status}
    for item in index.items:
      if item.status in settled:
        continue
      for dep in item.depends_on:
        other = index.get(dep)
        if other is not None and other.status == cfg.retired_status:
          report.findings.append(
            Finding(
              "error",
              item.id,
              f"depends on {dep}, which is retired and can never be done -- this item "
              f"can never be started; drop the dependency or restore {dep}",
            )
          )
  for cycle in graph.cycles(index):
    report.findings.append(Finding("error", cycle[0], "dependency cycle: " + " -> ".join(cycle)))

  for sid in sorted(set(state.slices) - seen):
    report.findings.append(Finding("error", sid, "slice file has no index row"))

  for sid, path in sorted(state.slice_files.items()):
    if path.stem != sid:
      report.findings.append(
        Finding("error", sid, f"slice file {path.name} contains id {sid!r}; the name and the id disagree")
      )

  report.findings.extend(_catalog_findings(index))
  return report


def _catalog_findings(index) -> list[Finding]:
  """Catalog ids, successors, and retire reasons. Citations are checked with the items."""
  catalog = index.catalog
  findings: list[Finding] = []
  if catalog.id_prefix.casefold() == index.id_prefix.casefold():
    findings.append(Finding(
      "error", "",
      f"catalog id prefix {catalog.id_prefix!r} matches the item prefix "
      f"{index.id_prefix!r}; catalog ids would collide with item ids",
    ))
  folded: dict[str, list[str]] = {}
  item_ids = {item.id.casefold(): item.id for item in index.items}
  for record in catalog.records:
    folded.setdefault(record.id.casefold(), []).append(record.id)
    clash = item_ids.get(record.id.casefold())
    if clash is not None:
      findings.append(Finding(
        "error", record.id, f"catalog id collides with item {clash}",
      ))
    if record.successor:
      other = catalog.get(record.successor)
      if other is None:
        findings.append(Finding(
          "error", record.id, f"successor unknown id {record.successor}",
        ))
      elif other.kind != record.kind:
        findings.append(Finding(
          "error", record.id,
          f"successor {other.id} is a {other.kind}; a successor has to be a {record.kind}",
        ))
    if record.status == CATALOG_RETIRED and not record.reason.strip():
      findings.append(Finding("error", record.id, "retired catalog record has a blank reason"))
    if record.status == CATALOG_ACTIVE and record.reason.strip():
      findings.append(Finding("error", record.id, "active catalog record has a retire reason"))
  for same in folded.values():
    if len(set(same)) == 1 and len(same) > 1:
      findings.append(Finding("error", same[0], "duplicate catalog id"))
    elif len(same) > 1:
      findings.append(Finding(
        "error", same[0], f"catalog ids {' and '.join(same)} differ only in case",
      ))
  for cycle in successor_cycles(catalog.records):
    findings.append(Finding(
      "error", cycle[0], "successor cycle: " + " -> ".join(cycle),
    ))
  water = ids.high_water([record.id for record in catalog.records], catalog.id_prefix)
  if catalog.next_id < water:
    findings.append(Finding(
      "error", "",
      f"catalog next_id is {catalog.next_id}, at or below an id already in use "
      f"(it should be at least {water}); the next catalog add would reuse an id",
    ))
  return findings


def against_git(state: State) -> VerifyReport:
  """Compare each item's recorded status with what history mentions."""
  cfg = state.config
  report = VerifyReport(checked=len(state.index.items))
  # Clone setup, not history: independent of git_check. Warn once here rather than
  # let an unconfigured merge driver write conflict markers on the next merge: the
  # render one for render/, the index one for a next_id-only conflict in index.json,
  # and the relay-roles one for a same-id conflict in relay-roles.json.
  # Only when this checkout has sibling worktrees -- the parallel workflow the drivers
  # serve -- so a solo, single-worktree clone stays quiet. `render_driver_check` off
  # silences all three.
  if cfg.render_driver_check and vcs.is_repo(state.root) and vcs.sibling_worktrees(state.root):
    if not vcs.render_driver_configured(state.root):
      report.findings.append(Finding(
        "warn", "",
        "render merge driver not configured in this clone; run `slicer setup-git` so "
        "merges keep render/ instead of writing conflict markers",
      ))
    if not vcs.index_driver_configured(state.root):
      report.findings.append(Finding(
        "warn", "",
        "index merge driver not configured in this clone; run `slicer setup-git` so "
        "a merge that conflicts only on the id counter keeps the larger one",
      ))
    # Only a project with a matrix can conflict on relay-roles.json. Test the file, not
    # `state.roles`: loading the matrix would turn a malformed one into a `corrupt` error.
    if (state.dir / ROLES_NAME).exists() and not vcs.relay_roles_driver_configured(state.root):
      report.findings.append(Finding(
        "warn", "",
        "relay-roles merge driver not configured in this clone; run `slicer setup-git` so "
        "merges keep both sides' rows and rules with fresh ids instead of writing conflict "
        "markers into relay-roles.json",
      ))
  if not cfg.git_check:
    return report
  subjects = vcs.subjects(state.root)
  report.git = bool(subjects)
  if not subjects:
    report.findings.append(Finding("info", "", "not a git repository, or no history; git checks skipped"))
    return report

  # Only the "done but never committed" direction is kept. The inverse --
  # "open but a commit mentions it" -- fires on every commit named after a
  # slice before the item is marked done, and on any roadmap-maintenance
  # commit that references an id, so it is noise in exactly the workflow the
  # tool encourages. Telling "mentions" from "completes" needs content
  # history, which is S20; until then this direction is dropped.
  for item in state.index.items:
    if item.status != cfg.done_status:
      continue
    if not any(re.search(rf"\b{re.escape(item.id)}\b", s) for s in subjects):
      report.findings.append(
        Finding("warn", item.id, "recorded done, but no commit subject mentions it")
      )
  return report


def index_at(state: State, commit: str) -> Index | None:
  """The index as `commit` recorded it; None when that commit has no index.

  An index that cannot be read raises, so the caller decides whether that is fatal.
  """
  text = vcs.show_file(state.root, commit, _relative(state, state.dir / INDEX_NAME))
  if text is None:
    return None
  try:
    return Index.from_dict(json.loads(text))
  except (ValueError, KeyError, TypeError, AttributeError) as exc:
    raise StateError(
      f"the index at {commit[:12]} cannot be read ({exc}); check that commit with `git show`",
      code="corrupt",
    ) from exc


def _relative(state: State, path: Path) -> str:
  return path.relative_to(state.root).as_posix()


def landed(state: State, item_id: str, ref: str) -> LandedReport:
  """Whether `item_id` is done in the tree `ref` points at, and committed there.

  Read-only. `ref`'s own index and done slice file decide, read with `git show`, so
  the working tree and unmerged branches cannot satisfy it. A pass needs the item
  done in `ref`'s index and, for an item with a slice, the done slice file in
  `ref`'s tree, created by a commit in `ref`'s history.

  When it has not landed, `reason` says why, in this order: `other_branch` when the
  tip of some other branch, remote branch or tag outside `ref`'s history has the item
  done now; `not_done` when `ref`'s index says done but the slice file is missing
  there; `working_tree_only` when only the working tree has it done; else
  `not_done`. A branch that once had it done and has since reopened it does not
  count. An unusable id, an unknown id, or a ref that names no commit is a
  `usage`-class error, not a failed report. An index that cannot be read on a ref
  other than `ref` is skipped; on `ref` itself it is an error.
  """
  ids.require_valid(item_id)
  root = state.root
  if not vcs.is_repo(root):
    raise StateError(
      f"{root} is not inside a git repository; `--landed` reads commits and has nothing to read",
      code="not_git",
    )
  commit = vcs.resolve(root, ref)
  if commit is None:
    raise StateError(
      f"{ref!r} does not name a commit; pass a branch, tag or commit with `--on`",
      code="bad_ref",
    )
  there_index = index_at(state, commit)
  here = state.index.get(item_id)
  there = there_index.get(item_id) if there_index is not None else None

  done = state.config.done_status
  slice_path = _relative(state, state.done_dir / f"{item_id}.json")

  def report(landed_: bool, reason: str, message: str, introduced: str = "") -> LandedReport:
    return LandedReport(item_id, ref, commit, landed_, reason, message, introduced)

  if there is not None and there.status == done:
    if not there.has_slice:
      return report(True, "", f"{item_id} is done in the index at {ref} ({commit[:12]})")
    if vcs.show_file(root, commit, slice_path) is not None:
      added = vcs.adding_commits(root, commit, slice_path)
      return report(
        True, "", f"{item_id} is done and committed on {ref} ({commit[:12]})",
        added[0] if added else "",
      )
  if _done_on_another_tip(state, item_id, slice_path, commit):
    return report(
      False, "other_branch",
      f"{item_id} is done on another branch, not on {ref}; merge that branch into {ref} "
      "and check again",
    )
  if there is not None and there.status == done:
    # Done in the index at REF, yet its slice file is not in the done folder there.
    return report(
      False, "not_done",
      f"{item_id} is not done: the index at {ref} says done but its slice file is "
      "missing from the done folder there; run `slicer verify` and fix the folder",
    )
  if here is not None and here.status == done:
    return report(
      False, "working_tree_only",
      f"{item_id} is done only in the working tree; commit it, and merge it into {ref} "
      "if that is another branch",
    )
  known = here or there
  if known is None:
    raise StateError(
      f"no such item: {item_id}; run `slicer list` for the ids in this project",
      code="no_such_item",
    )
  return report(False, "not_done", f"{item_id} is not done (status {known.status})")


def _done_on_another_tip(state: State, item_id: str, slice_path: str, commit: str) -> bool:
  """Whether a branch, remote branch or tag outside `commit`'s history has the item done.

  Each such tip's own index decides, and for an item with a slice its done slice file
  too. A tip whose index cannot be read is skipped: it is no evidence either way.
  """
  root = state.root
  for tip in vcs.ref_tips(root):
    if vcs.is_ancestor(root, tip, commit):
      continue
    try:
      seen = index_at(state, tip)
    except StateError:
      continue
    recorded = seen.get(item_id) if seen is not None else None
    if recorded is None or recorded.status != state.config.done_status:
      continue
    if not recorded.has_slice or vcs.show_file(root, tip, slice_path) is not None:
      return True
  return False
