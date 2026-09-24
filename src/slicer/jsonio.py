"""Deterministic JSON on disk.

Writes go through a temp file in the same directory plus `os.replace`, so a
crash mid-write cannot leave a half-written index. Formatting is fixed
(`indent=2`, `ensure_ascii=False`, trailing newline) because `slicer check`
compares bytes and a formatting drift would read as a spurious change.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


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
  path.parent.mkdir(parents=True, exist_ok=True)
  fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".slicer-tmp-")
  try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
      fh.write(text)
    os.chmod(tmp, _default_mode())
    os.replace(tmp, path)
  except BaseException:
    Path(tmp).unlink(missing_ok=True)
    raise


def read(path: Path) -> Any:
  return json.loads(path.read_text(encoding="utf-8"))


def append_jsonl(path: Path, obj: Any) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  with path.open("a", encoding="utf-8", newline="\n") as fh:
    fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[Any]:
  if not path.exists():
    return []
  return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
