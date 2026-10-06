"""The local feedback log, `.slicer/feedback.md`.

A use-log an agent appends to while working in slicer: a friction, a bug, a
feature idea that is not a roadmap item yet. It is gitignored, never parsed back
into roadmap state, and never rendered. Callers hold the project lock.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from slicer import jsonio, store
from slicer.errors import StateError

LOG_NAME = "feedback.md"
KINDS = ("friction", "bug", "feature")
GITIGNORE_NAME = ".gitignore"
_HEADER = re.compile(r"^<!-- slicer-feedback at=(\S+) kind=(\S+?)(?: item=(.*))? -->$")


def _now() -> str:
  return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_path(root: Path) -> Path:
  return root / store.DIR_NAME / LOG_NAME


def ensure_gitignore(root: Path) -> None:
  """Make `.slicer/.gitignore` list the log. Plain file IO: no git, no staging."""
  path = root / store.DIR_NAME / GITIGNORE_NAME
  existing = path.read_text(encoding="utf-8") if path.is_file() else ""
  if LOG_NAME in existing.splitlines():
    return
  if existing and not existing.endswith("\n"):
    existing += "\n"
  jsonio.write_text(path, existing + LOG_NAME + "\n")


def append(root: Path, kind: str, text: str, item: str | None) -> dict[str, str]:
  if kind not in KINDS:
    raise StateError(f"unknown feedback kind {kind!r}; choose one of {', '.join(KINDS)}", code="usage")
  text = text.rstrip("\n")
  if not text.strip():
    raise StateError("feedback text is empty", code="usage")
  ensure_gitignore(root)
  entry = {"at": _now(), "kind": kind, "text": text}
  header = f"<!-- slicer-feedback at={entry['at']} kind={kind}"
  if item:
    entry["item"] = item
    header += f" item={item}"
  path = log_path(root)
  before = path.read_bytes().decode("utf-8") if path.is_file() else ""
  if before:
    # Keep earlier bytes as they are; only separate the new entry from them.
    before += ("" if before.endswith("\n") else "\n") + "\n"
  jsonio.write_text(path, f"{before}{header} -->\n{text}\n")
  return entry


def read(root: Path) -> list[dict[str, str]]:
  path = log_path(root)
  if not path.is_file():
    return []
  entries: list[dict[str, str]] = []
  body: list[str] = []

  def close() -> None:
    if entries:
      entries[-1]["text"] = "\n".join(body).strip("\n")

  for line in path.read_bytes().decode("utf-8").splitlines():
    match = _HEADER.match(line)
    if match:
      close()
      body = []
      entry = {"at": match.group(1), "kind": match.group(2)}
      if match.group(3):
        entry["item"] = match.group(3)
      entries.append(entry)
    elif entries:
      body.append(line)
  close()
  return entries


def export(root: Path, dest: Path, *, force: bool) -> None:
  """Copy the log's exact bytes to `dest`, which may be anywhere."""
  source = log_path(root)
  if not source.is_file():
    raise StateError(f"no feedback log at {source}; nothing to write", code="usage")
  data = source.read_bytes()
  if dest.resolve() == source.resolve():
    raise StateError("--out cannot be the feedback log itself", code="state")
  if dest.exists() and not force:
    if dest.is_dir() or dest.read_bytes() != data:
      raise StateError(f"{dest} already exists with different content; pass --force to replace it",
        code="state")
  jsonio.write_bytes(dest, data)
