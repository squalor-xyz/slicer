"""Shared test scaffolding: a throwaway repo and an in-process CLI runner."""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
  sys.path.insert(0, str(SRC))

FIXTURES = Path(__file__).resolve().parent / "fixtures"
LEGACY = FIXTURES / "legacy"


class TempRepo:
  """A temporary directory, optionally a git repo, with a CLI runner.

  Commands run in-process so the suite stays fast and so real exit codes
  are exercised rather than a subprocess's.
  """

  def __init__(self, git: bool = False) -> None:
    self._tmp = tempfile.TemporaryDirectory(prefix="slicer-test-")
    self.root = Path(self._tmp.name).resolve()
    if git:
      self._git("init", "-q")
      self._git("config", "user.email", "test@example.invalid")
      self._git("config", "user.name", "Test")

  def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
      ["git", *args], cwd=str(self.root), capture_output=True, text=True, check=False
    )

  def commit(self, message: str) -> None:
    self._git("add", "-A")
    self._git("commit", "-q", "-m", message)

  def write(self, relpath: str, text: str) -> Path:
    path = self.root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path

  def read(self, relpath: str) -> str:
    return (self.root / relpath).read_text(encoding="utf-8")

  def copy_legacy(self, into: str = "docs/slices") -> Path:
    dst = self.root / into
    shutil.copytree(LEGACY, dst)
    (dst / "status-line.txt").unlink(missing_ok=True)
    return dst

  def run(self, *argv: str) -> tuple[int, str, str]:
    from slicer.cli import main

    out, err = io.StringIO(), io.StringIO()
    argv = ("--root", str(self.root)) + argv
    with redirect_stdout(out), redirect_stderr(err):
      code = main(list(argv))
    return code, out.getvalue(), err.getvalue()

  def state(self):
    from slicer import store

    return store.load(self.root)

  def close(self) -> None:
    self._tmp.cleanup()

  def __enter__(self) -> "TempRepo":
    return self

  def __exit__(self, *exc: object) -> None:
    self.close()


MINI_INDEX = """Mini index preamble.

# Slice index — review pass 1 (2026-01-01)

Findings: nowhere. Baseline prose that must survive.

| # | Slice | Size | Trees | Findings | Status |
|---|---|---|---|---|---|
| **Phase 0 — groundwork** | | | | | |
| [S01](done/S01-first-thing.md) | First thing | M `[OWNER]` | alpha | `F1` F2 | done |
| [S02](S02-second-thing.md) | Second thing | S | alpha, beta | F3 | — |

Outro prose for pass 1.

---

# Slice index — review pass 2 (2026-02-02)

| # | Slice | Size | Trees | Findings | Status |
|---|---|---|---|---|---|
| [S03](S03-third-thing.md) | Third thing | L | all four | owner ask | parked |
| [S04](S04-fourth-thing.md) | Fourth thing | M | beta | F9 | later |

Not slices: nothing at all.

## Dependencies

S02 depends on S01 because the prose says so.
"""

MINI_SLICES = {
  "done/S01-first-thing.md": (
    "# S01 — first thing, spelled out at length\n\n"
    "**Findings:** `F1` · F2 · **Size: M** `[OWNER]` · **Tree:** alpha\n\n"
    "## Why\n\nBecause.\n\n## Files\n\n- alpha/one.py\n\n"
    "## Failing tests\n\n`test_one`\n\n## Implement\n\nDo it.\n\n"
    "**Not in this slice:** the second thing.\n\n## Check\n\nGreen.\n\n## Git\n\nalpha only.\n"
  ),
  "S02-second-thing.md": (
    "# S02 — second thing, spelled out at length\n\n"
    "**Findings:** F3 · **Size: S** · **Trees:** alpha, beta\n"
    "**Depends on:** S01 (the first thing lays the groundwork)\n\n"
    "## Why\n\nBecause also.\n\n## Files\n\n- beta/two.py\n\n"
    "## Failing tests\n\n`test_two`\n\n## Implement\n\nDo that.\n\n"
    "**Not in this slice:** the third thing.\n\n## Check\n\nGreen.\n\n## Git\n\nboth trees.\n"
  ),
  "S03-third-thing.md": (
    "# S03 — third thing, spelled out at length\n\n"
    "**Parked** (owner 2026-02-02): do not take this yet.\n\n"
    "**Findings:** owner ask · **Size: L** · **Tree:** all four\n\n"
    "## Why\n\nBecause later.\n\n## What landed\n\nNothing yet, but this heading is off-schema.\n\n"
    "## Check\n\nNone.\n\n## Git\n\nnone.\n\n**Not in this slice:** everything else.\n"
  ),
  "S04-fourth-thing.md": (
    "# S04 — fourth thing, spelled out at length\n\n"
    "**Status: later** (owner 2026-02-02). Not now.\n\n"
    "**Findings:** F9 · **Size: M** · **Tree:** beta\n\n"
    "## Why\n\nBecause eventually.\n\n## Check\n\nNone.\n\n"
    "**Not in this slice:** all of it.\n"
  ),
}


def make_mini(repo: TempRepo, into: str = "docs/slices") -> Path:
  repo.write(f"{into}/README.md", MINI_INDEX)
  for rel, text in MINI_SLICES.items():
    repo.write(f"{into}/{rel}", text)
  return repo.root / into
