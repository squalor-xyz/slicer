"""The `slicer-slice` merge driver for slice JSON under `.slicer/slices/`.

Two branches that each `slicer edit` a slice overlap in one file even when they touched
different sections, and Git's line merge calls that a conflict that the person has to
repair by hand. The driver merges the three versions it is handed by identity instead,
the way `merge-index` does for the index: object fields by name, sections by heading and
occurrence (a heading may repeat within one slice, so the second `Pick (landed)` is its
own section). A value changed on one side takes that change; equal changes coalesce; only
a value both sides changed differently is a disagreement. Section order follows the base
unless exactly one side reordered it.

A slice's `notes` are strings with no id, so they merge as a union by exact text: the
base lines both sides kept, in base order, then the lines only ours added, then the lines
only theirs added. A line one side removed stays removed only if the other side kept it
unchanged, so a union never drops a line one side added and an edited line is a removal
plus an addition. Equal lines count by occurrence. The `note_records` that carry ids live
in `index.json`, which `merge-index` already merges.

A merge that cannot be made whole is refused (`merge_conflict`, or `corrupt` when an input
is not a slice) and `OURS` keeps its bytes. Unlike the index, the file is not filled with
conflict markers: slicer would then be unable to read it, and a slice changes through
`slicer edit`.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from slicer import jsonio
from slicer.errors import StateError
from slicer.mergeindex import _MISSING, _Merge, _same
from slicer.model import Slice

# Sides in the order Git hands them over; `theirs` is the one merged in.
_SIDES = ("base", "ours", "theirs")
# Fields of a slice file that must be strings.
_STRINGS = ("id", "title", "findings_note", "size", "trees_note", "boundary")


def _check(doc: Any) -> None:
  """Raise ValueError unless `doc` has the shape `Slice.to_dict` writes. `Slice.from_dict`
  accepts anything that iterates, so a list of numbers would otherwise merge."""
  if not isinstance(doc, dict):
    raise ValueError("not an object")
  for key in _STRINGS:
    if key in doc and not isinstance(doc[key], str):
      raise ValueError(f"{key} is not a string")
  if not doc.get("id") or "title" not in doc:
    raise ValueError("id and title are required")
  if doc.get("depends_note") is not None and not isinstance(doc.get("depends_note"), str):
    raise ValueError("depends_note is not a string")
  if "trees_plural" in doc and not isinstance(doc["trees_plural"], bool):
    raise ValueError("trees_plural is not a boolean")
  for key in ("lead", "flags", "notes"):
    value = doc.get(key, [])
    if not isinstance(value, list) or not all(isinstance(line, str) for line in value):
      raise ValueError(f"{key} is not a list of strings")
  sections = doc.get("sections", [])
  if not isinstance(sections, list) or not all(
    isinstance(s, dict) and isinstance(s.get("heading"), str) and isinstance(s.get("body", ""), str)
    for s in sections
  ):
    raise ValueError("sections is not a list of heading and body")


def _load(side: str, path: Path) -> dict[str, Any]:
  """One input as the canonical slice dict. An empty base is an empty slice: Git hands
  over an empty file when both branches created the slice."""
  text = path.read_text(encoding="utf-8")
  if side == "base" and not text.strip():
    return Slice(id="", title="").to_dict()
  try:
    doc = json.loads(text)
    _check(doc)
    return Slice.from_dict(doc).to_dict()
  except (ValueError, KeyError, TypeError, AttributeError) as e:
    raise StateError(
      f"the {side} version of the slice file is not a slice ({e}); "
      "nothing was merged and the file is unchanged",
      code="corrupt",
    ) from None


def _occurrences(names: list[str]) -> list[tuple[str, int]]:
  """Each name with how many equal names came before it: the identity of a section
  heading or a note line, so a repeated one is matched by position among its equals."""
  seen: Counter[str] = Counter()
  keys = []
  for name in names:
    keys.append((name, seen[name]))
    seen[name] += 1
  return keys


def _label(key: tuple[str, int]) -> str:
  """A section as a conflict names it: `Implement`, or `Pick (landed)[2]` for a repeat."""
  heading, at = key
  return heading if at == 0 else f"{heading}[{at + 1}]"


class _SliceMerge(_Merge):
  """`merge-index`'s field walk over one slice, with sections matched by heading and
  occurrence and notes merged as a union."""

  def run(self) -> dict[str, Any]:
    base, ours, theirs = self.docs
    out: dict[str, Any] = {}
    for key in ours:
      sides = (base[key], ours[key], theirs[key])
      if key == "sections":
        out[key] = self._sections(*sides)
      elif key == "notes":
        out[key] = _notes(*sides)
      else:
        out[key] = self._value(*sides, [key])
    return out

  def _sections(self, base: list, ours: list, theirs: list) -> list[dict[str, Any]]:
    keyed = [
      dict(zip(_occurrences([s["heading"] for s in side]), side)) for side in (base, ours, theirs)
    ]
    merged: dict[tuple[str, int], dict[str, Any]] = {}
    for key in dict.fromkeys([*keyed[1], *keyed[2], *keyed[0]]):
      b, o, t = (side.get(key, _MISSING) for side in keyed)
      where = ["sections", _label(key)]
      if _same(o, t):
        value = o
      elif _same(o, b):
        value = t
      elif _same(t, b):
        value = o
      elif b is not _MISSING and o is not _MISSING and t is not _MISSING:
        # Same heading on all three, so only the body can differ.
        body = self._value(b["body"], o["body"], t["body"], [*where, "body"])
        value = {"heading": key[0], "body": body}
      else:
        # Removed on one side and edited on the other, or one new heading added twice.
        value = self._conflict(where, b, o, t)
      if value is not _MISSING:
        merged[key] = value
    order = self._order(list(keyed[0]), list(keyed[1]), list(keyed[2]), set(merged), ["sections"], True)
    return [merged[key] for key in order]


def _notes(base: list[str], ours: list[str], theirs: list[str]) -> list[str]:
  """Union by exact text: the base lines both sides kept, then the lines only ours
  added, then the lines only theirs added."""
  if _same(ours, theirs) or _same(theirs, base):
    return ours
  if _same(ours, base):
    return theirs
  b, o, t = (_occurrences(side) for side in (base, ours, theirs))
  in_base, in_ours, in_theirs = set(b), set(o), set(t)
  keys = (
    [k for k in b if k in in_ours and k in in_theirs]
    + [k for k in o if k not in in_base]
    + [k for k in t if k not in in_base and k not in in_ours]
  )
  return [text for text, _ in keys]


def merge(base: Path, ours: Path, theirs: Path) -> None:
  """Merge `theirs` into `ours` against `base`, rewriting `ours` only on success.

  Raises `StateError` -- `merge_conflict` for a disagreement, `corrupt` for an input
  that is not a slice -- and then `ours` is exactly as it was.
  """
  docs = [_load(side, path) for side, path in zip(_SIDES, (base, ours, theirs))]
  walk = _SliceMerge(*docs, "ours")
  merged = walk.run()
  if walk.conflicts:
    raise StateError(
      "both sides changed the same slice value differently: "
      + ", ".join(f"conflict at {where}" for where in walk.conflicts)
      + ". Nothing was merged and the file is unchanged; keep one side with "
      "`git checkout --ours|--theirs` on the slice file, `git add` it, finish the merge, "
      "then re-apply the other side's changes (every section and field it changed, not "
      "only the conflicting value) with `slicer edit`.",
      code="merge_conflict",
    )
  try:
    text = jsonio.dumps(Slice.from_dict(merged).to_dict())
  except (ValueError, KeyError, TypeError) as e:
    raise StateError(
      f"the merged slice is not valid ({e}); nothing was merged and the file is unchanged",
      code="merge_conflict",
    ) from None
  jsonio.write_text(ours, text)
