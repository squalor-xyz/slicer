"""The only place slicer talks to git, and it never writes history.

Commands are checked against an allowlist. Committing, pushing and tagging
are the owner's to do, so `commit`, `push` and `tag` cannot be reached from
here even by a caller that asks for them.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from slicer.errors import StateError

ALLOWED = frozenset({"rev-parse", "status", "log", "mv", "ls-files"})


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
  if not args or args[0] not in ALLOWED:
    raise StateError(
      f"git {args[0] if args else ''!r} is not permitted; slicer never writes history"
    )
  return subprocess.run(
    ["git", *args], cwd=str(root), capture_output=True, text=True, check=False
  )


def is_repo(root: Path) -> bool:
  try:
    done = _run(root, "rev-parse", "--is-inside-work-tree")
  except FileNotFoundError:
    return False
  return done.returncode == 0 and done.stdout.strip() == "true"


def move(root: Path, src: Path, dst: Path) -> str:
  """`git mv` when possible so the move stays one tracked rename."""
  dst.parent.mkdir(parents=True, exist_ok=True)
  if is_repo(root) and _tracked(root, src):
    done = _run(root, "mv", str(src.relative_to(root)), str(dst.relative_to(root)))
    if done.returncode == 0:
      return "git mv"
  src.replace(dst)
  return "move"


def _tracked(root: Path, path: Path) -> bool:
  done = _run(root, "ls-files", "--error-unmatch", str(path.relative_to(root)))
  return done.returncode == 0


def subjects(root: Path, limit: int = 2000) -> list[str]:
  """Recent commit subjects across all refs, for the index-versus-git check."""
  if not is_repo(root):
    return []
  done = _run(root, "log", "--all", "--no-merges", f"--max-count={limit}", "--pretty=%h %s")
  return done.stdout.splitlines() if done.returncode == 0 else []
