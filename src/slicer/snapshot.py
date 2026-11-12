"""Give dashboard readers compact, independently revisioned views of one state."""

from __future__ import annotations

import functools
import hashlib
import os
from collections.abc import Callable
from pathlib import Path

import slicer
from slicer import check, jsonio, vcs, vocab
from slicer.config import CONFIG_NAME
from slicer.store import DIR_NAME, INDEX_NAME, SLICES_DIR, State

PARTS = vocab.PARTS

# The reply's `snapshot` key. 2: `items`, `goals` and `runs` hold what `list --json --lean`,
# `goals --json` and `relay-run list --json --active` print; 1 held reduced shapes of them.
SCHEMA = 2


def revision(value: object) -> str:
  return hashlib.sha256(jsonio.dumps(value).encode("utf-8")).hexdigest()[:12]


def _project_files(root: Path) -> list[Path]:
  return [root / DIR_NAME / CONFIG_NAME, root / DIR_NAME / INDEX_NAME]


def _slice_files(root: Path) -> list[Path]:
  """Every file under `.slicer/slices/`, however deep; none when the folder is absent."""
  return [Path(folder) / name for folder, _, names in os.walk(root / DIR_NAME / SLICES_DIR)
          for name in names]


def _sibling_files(root: Path) -> list[Path]:
  """Each other checkout's index and config: the files `store.sibling_work` reads.

  One `git worktree list` finds the checkouts; git is used to find files here, and its
  state is not read. A directory inside some other checkout has no siblings, as there.
  """
  if not vcs.at_worktree_root(root):
    return []
  return [sibling / DIR_NAME / name for sibling in vcs.sibling_worktrees(root)
          for name in (CONFIG_NAME, INDEX_NAME)]


# Part name to a function of (root, siblings) giving the files that part's value comes from;
# `siblings()` finds the other checkouts' files, once per build. A part that is not here has no
# fingerprint and is always built: `runs` depends on the clock (a run goes stale after 30
# minutes) and on the ledger found through git, and `health` reads the merge state from git.
INPUTS: dict[str, Callable[[Path, Callable[[], list[Path]]], list[Path]]] = {
  "goals": lambda root, siblings: _project_files(root),
  "deps": lambda root, siblings: _project_files(root),
  "items": lambda root, siblings: _project_files(root) + _slice_files(root) + siblings(),
  "status": lambda root, siblings: _project_files(root) + _slice_files(root) + siblings(),
}


def fingerprint(root: Path, name: str,
                siblings: Callable[[], list[Path]] | None = None) -> str | None:
  """A cheap stand-in for a part's revision, known before the project is loaded.

  Hashes the slicer version, the part name and `(path, mtime_ns, size, ino)` of every file
  the part reads, so it moves when any of them is touched or replaced. None for a part that
  is always built, and when a file cannot be examined: the part is then built, and the load
  reports the real error.
  """
  inputs = INPUTS.get(name)
  if inputs is None:
    return None
  try:
    paths = inputs(root, siblings or functools.partial(_sibling_files, root))
    files = []
    for path in sorted(set(paths), key=str):
      stat = os.stat(path)
      files.append([str(path), stat.st_mtime_ns, stat.st_size, stat.st_ino])
  except OSError:
    return None
  text = jsonio.dumps([slicer.__version__, name, files])
  return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _held(reply: dict, key: str) -> dict[str, dict[str, str]]:
  """Project root to part name to the string under `key`, for a reply the caller holds."""
  held: dict[str, dict[str, str]] = {}
  for project in reply["projects"]:
    if not isinstance(project, dict) or project.get("error") is not None:
      continue
    root, parts = project.get("root"), project.get("parts")
    if not isinstance(root, str) or not isinstance(parts, dict):
      continue
    held[root] = {name: part[key] for name, part in parts.items()
                  if isinstance(part, dict) and isinstance(part.get(key), str)}
  return held


def held_revisions(reply: dict) -> dict[str, dict[str, str]]:
  """Project root to part name to revision, from a snapshot reply the caller holds.

  A project with an error, and any entry that is not shaped like a reply's, holds nothing.
  """
  return _held(reply, "revision")


def held_fingerprints(reply: dict) -> dict[str, dict[str, str]]:
  """Project root to part name to fingerprint, held under the same rules as the revisions."""
  return _held(reply, "fingerprint")


def build(root: Path, state: Callable[[], State], parts: list[str],
          read: Callable[..., object], since: dict[str, str] | None = None,
          since_fingerprints: dict[str, str] | None = None) -> dict:
  """Reuse command readers bound to this project, without printing their views.

  `read(*argv)` gives what `slicer ARGV... --json` prints for the project; a part is that
  value as it comes, with no second copy of a command's shape.

  `state` loads the project when first called, so a part answered from its fingerprint
  costs no load. `since` maps part name to the revision the caller holds for this project
  and `since_fingerprints` to the fingerprint it held with it. A part whose fingerprint
  still matches is answered as `unchanged` without being built; otherwise it is built and
  answered `unchanged` when its revision still matches. Either way the value is dropped.
  """
  def items():
    return read("list", "--lean")  # the rows `slicer list --json --lean` prints

  def goals():
    return read("goals")  # what `slicer goals --json` prints

  def runs():
    return read("relay-run", "list", "--active")  # needs no loaded state

  def status():
    value = read("status")
    return {
      "next": value["next"]["id"] if value["next"] else None,
      "blocked": [entry["id"] for entry in value["blocked"]],
      "held": [entry["id"] for entry in value.get("held", [])],
    }

  def health():
    report, _, _ = check.run(state())
    return {"render_stale": bool(report.stale_render or report.orphan_render),
            "merge_in_progress": vcs.merge_in_progress(root)}

  def deps():
    return read("deps")  # what `slicer deps --json` prints for the project

  readers = {"items": items, "goals": goals, "runs": runs, "status": status, "health": health,
             "deps": deps}
  held = since or {}
  held_prints = since_fingerprints or {}
  siblings = functools.cache(functools.partial(_sibling_files, root))
  result = {}
  for name in parts:
    # Taken before the part is read: a file that moves during the read leaves a fingerprint
    # older than the value, which costs the next call a rebuild and never a stale answer.
    fp = fingerprint(root, name, siblings)
    if fp is not None and fp == held_prints.get(name) and isinstance(held.get(name), str):
      result[name] = {"revision": held[name], "fingerprint": fp, "unchanged": True}
      continue
    value = readers[name]()
    rev = revision(value)
    # Built either way once the fingerprint differs, since a revision hashes the value; only
    # the reply drops it.
    result[name] = ({"revision": rev, "fingerprint": fp, "unchanged": True}
                    if held.get(name) == rev
                    else {"revision": rev, "fingerprint": fp, "value": value})
  return {
    "root": str(root.resolve()),
    "revision": revision({name: part["revision"] for name, part in result.items()}),
    "parts": result, "error": None,
  }
