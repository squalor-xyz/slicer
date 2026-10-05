"""The `slicer-index` merge driver for `.slicer/index.json`.

Every filing moves the single `next_id` line, so two branches that each file
anything conflict there even when the ids they took differ. The right answer is
always the larger counter: an id is never reused. The driver sets that counter
to the larger of the three versions on every side and lets Git's own three-way
merge do the rest, so any other overlap stays an ordinary conflict with the
usual markers. It does not merge items or fields.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from slicer import jsonio, vcs

# The counter is a top-level key, so the writer's two-space indent identifies it.
_COUNTER = re.compile(r'^  "next_id": (\d+)(,?)$', re.MULTILINE)


def _counter(text: str) -> int | None:
  found = _COUNTER.findall(text)
  return int(found[0][0]) if len(found) == 1 else None


def _with_counter(text: str, value: int) -> str:
  return _COUNTER.sub(lambda m: f'  "next_id": {value}{m.group(2)}', text)


def merge(cwd: Path, base: Path, ours: Path, theirs: Path) -> int:
  """Merge `theirs` into `ours` against `base`, rewriting `ours`.

  Returns the number of conflict hunks left in `ours`; 0 means a clean merge.
  A side without exactly one `next_id` line is merged as it is.
  """
  texts = [path.read_text(encoding="utf-8") for path in (base, ours, theirs)]
  counters = [_counter(text) for text in texts]
  if None not in counters:
    top = max(counters)
    texts = [_with_counter(text, top) for text in texts]
  with tempfile.TemporaryDirectory(prefix="slicer-merge-") as tmp:
    sides = []
    for name, text in zip(("base", "ours", "theirs"), texts):
      side = Path(tmp) / name
      side.write_text(text, encoding="utf-8", newline="\n")
      sides.append(side)
    conflicts, merged = vcs.merge_file(cwd, *sides)
  jsonio.write_text(ours, merged)
  return conflicts
