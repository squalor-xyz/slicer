"""The `slicer-relay-roles` merge driver for `.slicer/relay-roles.json`.

Two branches that each run `relay-roles add` issue the same `r` id (and two that each
run `rule-add` the same `u` id), so Git's line merge can only call that a conflict, and
the owner may not repair the file by hand. The driver merges the three versions it is
handed by identity instead, the way `merge-index` does for the index: rows and rules
by id, object fields by name. A value changed on one side takes that change; equal
changes coalesce; only a value both sides changed differently is a disagreement. Row
order follows the base unless exactly one side reordered it, because the first matching
row wins.

What differs from `merge-index` is the id collision. A row or rule that both sides added
under one id with different content is two rows, not a disagreement: the side merged in
(`theirs`) takes a fresh id past every id either side issued, and its history entries
in `log.jsonl` stay as they are. The renumbered row keeps its text unchanged -- the old
id is not copied into its note; the line on stderr names the renumbering. The counters
`next_row` and `next_rule` end as the largest of the three, moved past any fresh id.

A merge that cannot be made whole is refused (`merge_conflict`, or `corrupt` when an
input is not a relay-roles matrix) and `OURS` keeps its bytes. Unlike the index, the
file is not filled with conflict markers: slicer would then be unable to read it, and
the matrix changes only through `slicer relay-roles`.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

from slicer import jsonio
from slicer.errors import StateError
# Relies on `_Merge`'s path-keyed special cases (items, catalog, notes) not matching
# `rows` or `rules`, so its id-keyed list and field walk applies to a matrix unchanged.
from slicer.mergeindex import _Merge, _same
from slicer.roles import RoleMatrix

# Sides in the order Git hands them over; `theirs` is the one merged in.
_SIDES = ("base", "ours", "theirs")
# (list key, id prefix, counter key, noun used in messages)
_KINDS = (("rows", "r", "next_row", "row"), ("rules", "u", "next_rule", "rule"))


def _say(message: str) -> None:
  print(f"slicer merge-relay-roles: {message}", file=sys.stderr)


def _load(side: str, path: Path) -> dict[str, Any]:
  """One input as the canonical matrix dict. An empty base is an empty matrix: Git
  hands over an empty file when both branches created the matrix."""
  text = path.read_text(encoding="utf-8")
  if side == "base" and not text.strip():
    return RoleMatrix().to_dict()
  try:
    doc = json.loads(text)
    if not isinstance(doc, dict):
      raise TypeError("not an object")
    found = RoleMatrix.from_dict(doc).to_dict()
    for key, prefix, counter, _ in _KINDS:
      top = max((_number(item["id"], prefix) for item in found[key]), default=0)
      if found[counter] <= top:
        raise ValueError(f"{counter} {found[counter]} is at or below an id in use")
    return found
  except (ValueError, KeyError, TypeError, AttributeError) as e:
    raise StateError(
      f"the {side} version of relay-roles.json is not a relay roles matrix ({e}); "
      "nothing was merged and the file is unchanged",
      code="corrupt",
    ) from None


def _number(item_id: str, prefix: str) -> int:
  """The number in an id such as `r12`, or 0 for an id that does not follow the scheme."""
  found = re.fullmatch(rf"{prefix}(\d+)", item_id)
  return int(found.group(1)) if found else 0


def _renumber(
  docs: list[dict[str, Any]], key: str, prefix: str, counter: str,
) -> list[tuple[str, str]]:
  """Give the rows or rules `theirs` added under an id `ours` also issued, with other
  content, a fresh id; edits `docs[2]` in place and returns the (old, new) pairs.

  `ours` issued an id when it holds it as a new row, or when its counter moved past
  it since the base: an id `ours` added and then removed is still spent, and `theirs`
  must not bring it back.

  The fresh ids start at the largest counter, which is past every id issued on any side,
  and `theirs`'s counter moves past them, so the merged counter ends past every id."""
  base, ours, theirs = docs
  known = {item["id"] for item in base[key]}
  held = {item["id"]: item for item in ours[key]}
  issued = range(base[counter], ours[counter])
  clashing = [
    item["id"] for item in theirs[key]
    if item["id"] not in known
    and (item["id"] in held or _number(item["id"], prefix) in issued)
    and not _same(item, held.get(item["id"]))
  ]
  if not clashing:
    return []
  start = max(doc[counter] for doc in docs)
  fresh = {old: f"{prefix}{start + n}" for n, old in enumerate(clashing)}
  for item in theirs[key]:
    item["id"] = fresh.get(item["id"], item["id"])
  theirs[counter] = start + len(clashing)
  return list(fresh.items())


class _RolesMerge(_Merge):
  """`merge-index`'s walk over a matrix: its lists are the rows and rules by id, and
  its counters take the larger value."""

  def run(self) -> dict[str, Any]:
    base, ours, theirs = self.docs
    out: dict[str, Any] = {}
    for key in dict.fromkeys([*ours, *theirs, *base]):
      sides = (base[key], ours[key], theirs[key])
      if key in ("rows", "rules"):
        out[key] = self._items(*sides, [key])
      elif key in ("next_row", "next_rule"):
        out[key] = max(sides)
      else:
        out[key] = self._value(*sides, [key])
    return out


def merge(base: Path, ours: Path, theirs: Path) -> None:
  """Merge `theirs` into `ours` against `base`, rewriting `ours` only on success.

  Raises `StateError` -- `merge_conflict` for a disagreement, `corrupt` for an input
  that is not a matrix, `schema_too_new` for one from a newer slicer -- and then `ours`
  is exactly as it was.
  """
  docs = [_load(side, path) for side, path in zip(_SIDES, (base, ours, theirs))]
  renumbered = [
    (noun, old, new) for key, prefix, counter, noun in _KINDS
    for old, new in _renumber(docs, key, prefix, counter)
  ]
  walk = _RolesMerge(*docs, "ours")
  merged = walk.run()
  if walk.conflicts:
    raise StateError(
      "both sides changed the same relay roles value differently: "
      + ", ".join(f"conflict at {where}" for where in walk.conflicts)
      + ". Nothing was merged and the file is unchanged; keep one side with "
      "`git checkout --ours|--theirs .slicer/relay-roles.json`, `git add` it, finish the "
      "merge, then re-apply the other side's changes (every row and rule it added or "
      "edited, not only the conflicting value) with `slicer relay-roles`.",
      code="merge_conflict",
    )
  try:
    text = jsonio.dumps(RoleMatrix.from_dict(merged).to_dict())
  except (ValueError, KeyError, TypeError) as e:
    raise StateError(
      f"the merged relay roles matrix is not valid ({e}); nothing was merged and the "
      "file is unchanged",
      code="merge_conflict",
    ) from None
  jsonio.write_text(ours, text)
  for noun, old, new in renumbered:
    _say(f"{noun} {old} was issued on both sides; the one merged in is now {new}")
