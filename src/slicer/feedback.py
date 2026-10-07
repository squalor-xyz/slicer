"""The local feedback log, `.slicer/feedback.md`.

A use-log an agent appends to while working in slicer: a friction, a bug, a
feature idea that is not a roadmap item yet. It is gitignored, never parsed back
into roadmap state, and never rendered. Callers hold the project lock.

`feedback-report` files entries as GitHub issues and remembers which ones in a
gitignored sidecar, `.slicer/feedback-reported.json`, so the log bytes are never
rewritten. An entry is known by the sha256 of its header and body.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from slicer import jsonio, store
from slicer.errors import StateError, reject_future_schema

LOG_NAME = "feedback.md"
KINDS = ("friction", "bug", "feature")
GITIGNORE_NAME = ".gitignore"
SIDECAR_NAME = "feedback-reported.json"
SIDECAR_VERSION = 1
TITLE_LIMIT = 120
_HEADER = re.compile(r"^<!-- slicer-feedback at=(\S+) kind=(\S+?)(?: item=(.*))? -->$")


def _now() -> str:
  return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_path(root: Path) -> Path:
  return root / store.DIR_NAME / LOG_NAME


def sidecar_path(root: Path) -> Path:
  return root / store.DIR_NAME / SIDECAR_NAME


def ensure_gitignore(root: Path, name: str = LOG_NAME) -> None:
  """Make `.slicer/.gitignore` list `name`. Plain file IO: no git, no staging."""
  path = root / store.DIR_NAME / GITIGNORE_NAME
  existing = path.read_text(encoding="utf-8") if path.is_file() else ""
  if name in existing.splitlines():
    return
  if existing and not existing.endswith("\n"):
    existing += "\n"
  jsonio.write_text(path, existing + name + "\n")


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


def _parse(root: Path) -> list[tuple[str, dict[str, str]]]:
  """Each entry with the header line it was read from."""
  path = log_path(root)
  if not path.is_file():
    return []
  entries: list[tuple[str, dict[str, str]]] = []
  body: list[str] = []

  def close() -> None:
    if entries:
      entries[-1][1]["text"] = "\n".join(body).strip("\n")

  for line in path.read_bytes().decode("utf-8").splitlines():
    match = _HEADER.match(line)
    if match:
      close()
      body = []
      entry = {"at": match.group(1), "kind": match.group(2)}
      if match.group(3):
        entry["item"] = match.group(3)
      entries.append((line, entry))
    elif entries:
      body.append(line)
  close()
  return entries


def read(root: Path) -> list[dict[str, str]]:
  return [entry for _, entry in _parse(root)]


def hashed(root: Path) -> list[tuple[str, dict[str, str]]]:
  """Each entry with the sha256 of its header line and body, the key a report is stored under."""
  return [
    (hashlib.sha256(f"{header}\n{entry['text']}".encode("utf-8")).hexdigest(), entry)
    for header, entry in _parse(root)
  ]


def issue_title(entry: dict[str, str]) -> str:
  """`[kind] first line`, cut so the whole title is at most TITLE_LIMIT characters."""
  lines = entry["text"].strip().splitlines()
  title = f"[{entry['kind']}] {lines[0].strip() if lines else ''}".rstrip()
  if len(title) > TITLE_LIMIT:
    title = title[:TITLE_LIMIT - 1].rstrip() + "\u2026"
  return title


def issue_body(entry: dict[str, str]) -> str:
  head = [f"Kind: {entry['kind']}", f"At: {entry['at']}"]
  if "item" in entry:
    head.append(f"Item: {entry['item']}")
  return (
    "\n".join(head) + "\n\n" + entry["text"] + "\n\n"
    + "Filed by `slicer feedback-report` from a local `slicer feedback` entry.\n"
  )


def reported(root: Path) -> list[dict[str, object]]:
  """The sidecar's records in append order; empty when nothing was filed."""
  path = sidecar_path(root)
  if not path.is_file():
    return []
  data = jsonio.read(path)
  if not isinstance(data, dict) or not isinstance(data.get("reported"), list):
    raise StateError(f"{path}: expected {{version, reported: [...]}}", code="corrupt")
  if isinstance(data.get("version"), int):
    reject_future_schema(str(path), data["version"], SIDECAR_VERSION)
  return list(data["reported"])


def reported_hashes(root: Path, repo: str) -> set[str]:
  """Entries already filed to `repo`. GitHub names are case-insensitive, so this is too."""
  return {
    str(record.get("entry")) for record in reported(root)
    if isinstance(record, dict) and str(record.get("repo", "")).casefold() == repo.casefold()
  }


def record(root: Path, entry_hash: str, repo: str, issue: int, url: str) -> None:
  """Append one filed issue to the sidecar, gitignoring it first. Callers hold the lock."""
  ensure_gitignore(root, SIDECAR_NAME)
  records = reported(root) + [{"entry": entry_hash, "repo": repo, "issue": issue, "url": url}]
  jsonio.write(sidecar_path(root), {"version": SIDECAR_VERSION, "reported": records})


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
