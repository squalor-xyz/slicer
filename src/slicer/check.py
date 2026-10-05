"""The single gate a project's CI can call: `slicer check`.

Composes render staleness, the derived-pointer check and the offline
integrity checks, so a build script has one command to run and one exit
code to read.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from slicer import render, store, sync, vcs, verify
from slicer.store import State


@dataclass
class CheckReport:
  stale_render: list[str] = field(default_factory=list)
  stale_render_details: list[dict[str, object]] = field(default_factory=list)
  orphan_render: list[str] = field(default_factory=list)
  stale_sync: list[str] = field(default_factory=list)
  problems: list[str] = field(default_factory=list)
  warnings: list[str] = field(default_factory=list)

  @property
  def ok(self) -> bool:
    return not (self.stale_render or self.orphan_render or self.stale_sync or self.problems)

  def to_dict(self) -> dict[str, object]:
    return {
      "ok": self.ok,
      "stale_render": self.stale_render,
      "stale_render_details": self.stale_render_details,
      "orphan_render": self.orphan_render,
      "stale_sync": self.stale_sync,
      "problems": self.problems,
      "warnings": self.warnings,
    }


def run(state: State) -> tuple[CheckReport, dict[str, bytes], render.RenderDiff]:
  expected = render.plan(state)
  diff = render.compare(expected, state.render_dir)
  report = CheckReport()
  report.stale_render = sorted(diff.missing + diff.differing)
  by_file = {d.file: d for d in diff.details}
  report.stale_render_details = [by_file[rel].to_dict() for rel in report.stale_render]
  report.orphan_render = list(diff.orphans)

  for finding in sync.apply(state.root, state.index, state.config, check_only=True):
    if finding.stale:
      report.stale_sync.append(f"{finding.path}: {finding.detail}")

  offline = verify.offline(state)
  report.problems = [f"{f.item}: {f.message}" if f.item else f.message for f in offline.problems]
  report.warnings = [
    f"{f.item}: {f.message}" if f.item else f.message
    for f in offline.findings
    if f.level == "warn"
  ]
  report.warnings.extend(_sibling_collisions(state))
  return report, expected, diff


def _sibling_collisions(state: State) -> list[str]:
  """Ids a sibling worktree filed under a different title than this checkout did."""
  if not vcs.is_repo(state.root):
    return []
  warnings: list[str] = []
  for name, sibling in store.sibling_ids(state.root):
    for theirs in sibling.items:
      ours = state.index.get(theirs.id)
      if ours is not None and ours.title != theirs.title:
        warnings.append(
          f"{theirs.id} is {ours.title!r} here but {theirs.title!r} in worktree {name}; "
          f"the two will collide on merge. File this checkout's item under a new id "
          f"before merging."
        )
  return warnings
