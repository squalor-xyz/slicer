"""The checks before a slice is marked done on the branch that merged it, and the git
commands left for the person landing.

Landing by hand was the same sequence every time, and two of its checks are easy to get
wrong: whether the slice's branch really is merged into HEAD, and whether a relay run on
the slice is still going. This module makes both with the read-only git calls `vcs` already
allows, refuses before anything is written, and then marks the slice done through `ops`.
slicer still commits, removes and deletes nothing: it only prints the commands.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

from slicer import ops, relayrun, vcs
from slicer.errors import StateError
from slicer.model import Item
from slicer.store import DIR_NAME, State


@dataclass
class Landing:
  """What `land` did and what is left for the person landing."""

  item: Item
  branch: str
  tip: str
  worktree: Path | None
  commands: list[str]
  written: int

  def to_dict(self) -> dict[str, object]:
    return {
      "item": self.item.to_dict(),
      "branch": self.branch,
      "tip": self.tip,
      "worktree": str(self.worktree) if self.worktree is not None else None,
      "commands": list(self.commands),
    }


def merged_tip(state: State, branch: str) -> str:
  """The full commit name of `branch`, once it is known to be merged into HEAD.

  Refuses with `usage` when `branch` names no commit (or this is not a git repo) and with
  `not_merged` when its tip is not reachable from HEAD.
  """
  tip = vcs.resolve(state.root, branch)
  if tip is None:
    raise StateError(
      f"{branch!r} names no commit in this repository; pass the slice's branch with --branch",
      code="usage",
    )
  head = vcs.resolve(state.root, "HEAD")
  if head is None or not vcs.is_ancestor(state.root, tip, head):
    raise StateError(
      f"{branch} is not merged into HEAD; merge it first, then run land again",
      code="not_merged",
    )
  return tip


def refuse_active_run(state: State, item_id: str) -> None:
  """Refuse with `run_active` when a relay run that has not ended names `item_id`."""
  for run in relayrun.list_runs(state.root, active=True):
    if item_id in run.items:
      raise StateError(
        f"relay run {run.id} is still active on {item_id}; end it with "
        f"`slicer relay-run end {run.id} --outcome ...`, then run land again",
        code="run_active",
      )


def commands_left(item_id: str, branch: str, worktree: Path | None) -> list[str]:
  """The git commands that finish a landing, in the order to run them."""
  lines = [f'git add -A {DIR_NAME} && git commit -m "{item_id} done"']
  if worktree is not None:
    lines.append(f"git worktree remove {shlex.quote(str(worktree))}")
  lines.append(f"git branch -d {shlex.quote(branch)}")
  return lines


def land(state: State, item_id: str, branch: str, *, note: str, by: str = "") -> Landing:
  """Check `branch` is merged and no run is active on `item_id`, then mark it done and render.

  Every refusal comes before the first write. The render is gated like `done --render`.
  """
  tip = merged_tip(state, branch)
  refuse_active_run(state, item_id)
  worktree = vcs.worktree_on_branch(state.root, branch)
  items, written = ops.done_and_render(state, [item_id], note=note, by=by)
  return Landing(
    item=items[0], branch=branch, tip=tip, worktree=worktree,
    commands=commands_left(item_id, branch, worktree), written=written,
  )
