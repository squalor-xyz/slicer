"""Integrity checks, offline and against version control.

The protocol this tool serves says the index is a claim and history is the
fact. `verify` is where that comparison stops being a manual chore — but it
reports, it never rewrites: a mismatch needs a human to say which side is
wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from slicer import graph, ids, vcs
from slicer.store import State


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


def offline(state: State) -> VerifyReport:
  """Everything checkable without touching git."""
  cfg = state.config
  index = state.index
  report = VerifyReport(checked=len(index.items))

  if index.items and (index.id_prefix, index.id_width) != (cfg.id_prefix, cfg.id_width):
    report.findings.append(
      Finding(
        "error",
        "",
        f"id scheme in config ({cfg.id_prefix!r} width {cfg.id_width}) does not match "
        f"the index ({index.id_prefix!r} width {index.id_width}); the index wins, "
        f"because ids are never reused. Restore the config, or start a new project.",
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
  for item in index.items:
    if item.id in seen:
      report.findings.append(Finding("error", item.id, "duplicate id in the index"))
    seen.add(item.id)
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

  for item_id, dep in graph.dangling(index):
    report.findings.append(Finding("error", item_id, f"depends on unknown id {dep}"))
  if cfg.retired_status:
    for item in index.items:
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

  return report


def against_git(state: State) -> VerifyReport:
  """Compare each item's recorded status with what history mentions."""
  cfg = state.config
  report = VerifyReport(checked=len(state.index.items))
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
