"""The single gate a project's CI can call: `slicer check`.

Composes render staleness, the derived-pointer check and the offline
integrity checks, so a build script has one command to run and one exit
code to read.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from slicer import ai, feedback, ops, render, store, sync, vcs, verify
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

  @property
  def stale_render_kind(self) -> str | None:
    """`roadmap_only` for the merge's leftover, `mixed` for other stale renders, else None.

    `roadmap_only` needs every stale path to be a whole-roadmap render, every stale cause
    to be `content` or `conflict_markers` (a missing file, an older render format or an
    unstamped header is not a merge), and no orphan, stale sync or problem. Any other stale
    render, including a roadmap render beside one of those, is `mixed`, so the merge line does
    not claim the case.
    """
    if not self.stale_render:
      return None
    merge_shape = (
      set(self.stale_render) <= set(render.ROADMAP_RENDER_NAMES)
      and all(
        detail["cause"] in ("content", "conflict_markers")
        for detail in self.stale_render_details
      )
      and not (self.orphan_render or self.stale_sync or self.problems)
    )
    return "roadmap_only" if merge_shape else "mixed"

  def to_dict(self) -> dict[str, object]:
    out: dict[str, object] = {
      "ok": self.ok,
      "stale_render": self.stale_render,
      "stale_render_details": self.stale_render_details,
    }
    kind = self.stale_render_kind
    if kind is not None:
      out["stale_render_kind"] = kind
    out["orphan_render"] = self.orphan_render
    out["stale_sync"] = self.stale_sync
    out["problems"] = self.problems
    out["warnings"] = self.warnings
    return out


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
  legacy = store.legacy_roles_advice(state.root)
  if legacy is not None:
    report.warnings.append(legacy)
  report.warnings.extend(_gitignore_lines(state))
  pin = ai.pin_mismatch(state.config.relay_contract)
  if pin is not None:
    report.warnings.append(pin)
  return report, expected, diff


def _gitignore_lines(state: State) -> list[str]:
  """Lines `init` writes that `.slicer/.gitignore` lacks, as one warning.

  A write in a linked worktree puts a missing line in the repo's `info/exclude` instead
  (the file is tracked), so only main can fix the file everyone shares.
  """
  try:
    missing = feedback.missing_gitignore_lines(state.root, ops.GITIGNORE_LINES)
  except (OSError, UnicodeDecodeError):
    return []  # advisory, like the lines themselves
  if not missing:
    return []
  return [
    f"{store.DIR_NAME}/{feedback.GITIGNORE_NAME} lacks {', '.join(missing)}; "
    "run `slicer setup-git --attributes` on main to add them"
  ]


def _sibling_collisions(state: State) -> list[str]:
  """New sibling ids that collide by case or have different titles here.

  An id both checkouts inherited from their merge base is one item that one side
  retitled, which merges cleanly, so only an id new on both sides can collide.
  """
  if not vcs.is_repo(state.root):
    return []
  warnings: list[str] = []
  ours_by_id = {item.id.casefold(): item for item in state.index.items}
  ours_head = vcs.resolve(state.root, "HEAD")
  for path, sibling in store.sibling_checkouts(state.root):
    inherited: set[str] | None = None  # read once per sibling, only for a possible clash
    for theirs in sibling.items:
      ours = ours_by_id.get(theirs.id.casefold())
      if ours is None or (ours.id == theirs.id and ours.title == theirs.title):
        continue
      if inherited is None:
        inherited = {value.casefold() for value in verify.inherited_ids(state, ours_head, path)}
      if theirs.id.casefold() in inherited:
        continue
      if ours.id != theirs.id:
        warnings.append(
          f"{ours.id} here and {theirs.id} in worktree {path.name} differ only in case; "
          "the slice files will collide on merge as one file on a case-insensitive filesystem. "
          "File this checkout's item under a new id before merging."
        )
      else:
        warnings.append(
          f"{theirs.id} is {ours.title!r} here but {theirs.title!r} in worktree "
          f"{path.name}; the two will collide on merge. File this checkout's item "
          f"under a new id before merging."
        )
  return warnings
