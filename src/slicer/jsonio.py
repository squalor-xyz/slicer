"""Deterministic JSON on disk.

Writes go through a temp file in the same directory plus `os.replace`, so a
crash mid-write cannot leave a half-written index. The temporary file is fsynced
before the rename (with F_FULLFSYNC on macOS, where fsync does not reach the
drive's cache). The directory is fsynced after the rename, except on Windows,
where a directory cannot be opened for fsync; an `EINVAL` or `ENOTSUP` from that
fsync is ignored, and any other error is raised. So a power loss should not
leave an empty state file. Formatting is fixed (`indent=2`, `ensure_ascii=False`,
trailing newline) because `slicer check` compares bytes and a formatting drift
would read as a spurious change.
"""

from __future__ import annotations

import errno
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from slicer.errors import StateError


def dumps(obj: Any) -> str:
  return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def write(path: Path, obj: Any) -> None:
  write_text(path, dumps(obj))


def _default_mode() -> int:
  """The mode a plain `open(...,"w")` would have produced, honouring umask.

  `mkstemp` deliberately creates 0600 files; these are ordinary tracked
  project files, so they should look like every other file in the repo.
  """
  umask = os.umask(0)
  os.umask(umask)
  return 0o666 & ~umask


def write_text(path: Path, text: str) -> None:
  write_bytes(path, text.encode("utf-8"))


def write_bytes(path: Path, data: bytes) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".slicer-tmp-")
  try:
    with os.fdopen(fd, "wb") as fh:
      fh.write(data)
      fh.flush()
      os.fsync(fh.fileno())
      if sys.platform == "darwin":
        _full_fsync_darwin(fh.fileno())
    os.chmod(tmp, _default_mode())
    os.replace(tmp, path)
  except BaseException:
    Path(tmp).unlink(missing_ok=True)
    raise
  _fsync_dir(path.parent)


def _full_fsync_darwin(fd: int) -> None:
  """On macOS `fsync` stops short of the drive's cache; `F_FULLFSYNC` writes it out."""
  import fcntl  # imported here: fcntl is not available on Windows

  fcntl.fcntl(fd, fcntl.F_FULLFSYNC)


def _fsync_dir(directory: Path) -> None:
  """Persist the rename: the new directory entry is durable only once the directory is synced."""
  if os.name == "nt":  # a directory cannot be opened for fsync on Windows
    return
  fd = os.open(directory, os.O_RDONLY)
  try:
    os.fsync(fd)
  except OSError as exc:
    # A filesystem that cannot sync a directory fails with EINVAL or ENOTSUP. The rename has
    # already happened, so any other failure is raised for the caller to see.
    if exc.errno not in (errno.EINVAL, errno.ENOTSUP):
      raise
  finally:
    os.close(fd)


def read(path: Path) -> Any:
  try:
    return json.loads(path.read_text(encoding="utf-8"))
  except (json.JSONDecodeError, UnicodeDecodeError) as e:
    note = "; it holds merge conflict markers" if has_conflict_markers(path) else ""
    raise StateError(f"{path}: not valid JSON: {e}{note}", code="corrupt") from None


def has_conflict_markers(path: Path) -> bool:
  """Whether the file holds a Git conflict: an opening and a closing marker line."""
  try:
    lines = path.read_text(encoding="utf-8").splitlines()
  except (OSError, UnicodeDecodeError):
    return False
  return any(line.startswith("<<<<<<< ") for line in lines) and any(
    line.startswith(">>>>>>> ") for line in lines
  )


def append_jsonl(path: Path, obj: Any) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  with path.open("a", encoding="utf-8", newline="\n") as fh:
    fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[Any]:
  if not path.exists():
    return []
  try:
    text = path.read_text(encoding="utf-8")
  except UnicodeDecodeError as e:
    raise StateError(f"{path}: not valid UTF-8: {e}", code="corrupt") from None
  out: list[Any] = []
  for n, line in enumerate(text.splitlines(), 1):
    if not line.strip():
      continue
    try:
      out.append(json.loads(line))
    except json.JSONDecodeError as e:
      raise StateError(f"{path}:{n}: not valid JSON: {e}", code="corrupt") from None
  return out
