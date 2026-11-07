"""The single gate a project's CI can call: `slicer check`.

Composes render staleness, the derived-pointer check and the offline
integrity checks, so a build script has one command to run and one exit
code to read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from slicer import ai, feedback, jsonio, ops, render, store, sync, vcs, verify
from slicer.store import State

_BACKTICK_SPAN = re.compile(r"`[^`\n]+`")
_HUMAN_CHECK = "[HUMAN CHECK]"
_FENCE_OPEN = re.compile(r"(`{3,}|~{3,})")


def fence_lines(body: str) -> list[tuple[str, str]]:
  """Each line of `body` with its kind: `text`, `fence` (an opening or closing line) or `code`.

  A fence closes on a later line of at least as many of the same character and nothing
  else; an unclosed fence runs to the end, as CommonMark renders it. `check` and
  `cli_common._outside_fences` share this one parser.
  """
  out: list[tuple[str, str]] = []
  fence = ""
  for line in body.split("\n"):
    text = line.strip()
    if not fence:
      opened = _FENCE_OPEN.match(text)
      if opened:
        fence = opened.group(1)
        out.append((line, "fence"))
      else:
        out.append((line, "text"))
    elif text.startswith(fence) and not text.strip(fence[0]):
      fence = ""
      out.append((line, "fence"))
    else:
      out.append((line, "code"))
  return out


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
  report.warnings.extend(_prompt_warnings(state))
  report.warnings.extend(_vague_acceptance_warnings(state))
  pin = ai.pin_mismatch(state.config.relay_contract)
  if pin is not None:
    report.warnings.append(pin)
  return report, expected, diff


def vague_acceptance(state: State, item_id: str) -> list[str]:
  """Advisory lines for one live slice whose Check or Failing tests give nothing to run.

  Text only: a Check needs a line holding a backticked command or `[HUMAN CHECK]`, or a
  non-blank line inside a fenced code block, and Failing tests needs a backticked name. Whether a command states its expected result is
  not judged. A missing or empty section is the empty-section rule's business, not this
  one's, and done and retired slices keep their history as written.
  """
  cfg = state.config
  skip = {cfg.done_status}
  if cfg.retired_status:
    skip.add(cfg.retired_status)
  item = state.index.get(item_id)
  sl = state.slices.get(item_id)
  if item is None or sl is None or item.status in skip:
    return []
  lines: list[str] = []
  check = sl.section("Check")
  if check is not None and check.body.strip() and not any(
    (kind == "code" and line.strip())
    or _BACKTICK_SPAN.search(line)
    or _HUMAN_CHECK in line
    for line, kind in fence_lines(check.body)
  ):
    lines.append(
      f"{item_id} / Check: no command or {_HUMAN_CHECK} line; say how to tell the slice is done"
    )
  tests = sl.section("Failing tests")
  if tests is not None and tests.body.strip() and not _BACKTICK_SPAN.search(tests.body):
    lines.append(f"{item_id} / Failing tests: no backticked test name")
  return lines


def _vague_acceptance_warnings(state: State) -> list[str]:
  return [line for item in state.index.items for line in vague_acceptance(state, item.id)]


def _prompt_warnings(state: State) -> list[str]:
  """Prompt files merge as plain text; warn about typos and unresolved conflicts.

  Dotfiles (Finder's `.DS_Store`, editor swap files, `jsonio`'s temporary files) are
  never prompts and are skipped.
  """
  warnings = []
  known = {f"{name}.md": name for name in ai.PROMPT_NAMES}
  for path in sorted(state.prompts_dir.glob("*")):
    if path.name.startswith(".") or not path.is_file():
      continue
    name = known.get(path.name)
    if name is None:
      warnings.append(
        f"{path}: unknown prompt file; choose {', '.join(ai.PROMPT_NAMES)}; "
        "use `slicer ai show NAME` and `slicer ai set NAME --file FILE`"
      )
    elif jsonio.has_conflict_markers(path):
      warnings.append(
        f"{path}: prompt holds Git conflict markers; inspect with `slicer ai show {name}` "
        f"and replace with `slicer ai set {name} --file FILE`"
      )
  return warnings


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
