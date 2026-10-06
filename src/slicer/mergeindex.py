"""The `slicer-index` merge driver for `.slicer/index.json`.

Two branches that each file or edit anything overlap in the one index file even
when they touched different items or different fields of one item, and Git's line
merge can only call that a conflict. The driver merges the three versions it is
handed by identity instead: items by id, object fields by name. A value changed on
one side takes that change; equal changes coalesce; only a value both sides changed
differently conflicts, and then only at that field. Lists and prose strings are
atomic, so two different edits to one list are never guessed into one. Item order
follows the base unless exactly one side reordered it. `next_id` is the largest of
the three (S191): an id is never reused.

A merge that would leave the index inconsistent -- two ids for one filing key, ids
that differ only in case, a dependency on an item the merge removed -- conflicts at
the items involved instead of reporting success. Input that is not a readable index
is never merged silently: it conflicts as a whole.
"""

from __future__ import annotations

import difflib
import json
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from slicer import graph, ids as idscheme, jsonio, vcs
from slicer.errors import StateError
from slicer.model import Index

_MISSING: Any = object()

# The counter is a top-level key, so the writer's two-space indent identifies it.
_COUNTER = re.compile(r'^  "next_id": (\d+)(,?)$', re.MULTILINE)


class _Unreadable(Exception):
  """An input that is not an index this driver can merge by identity."""


def _same(a: Any, b: Any) -> bool:
  return a is b or (type(a) is type(b) and a == b)


def _load(text: str) -> dict[str, Any]:
  try:
    doc = json.loads(text)
  except ValueError:
    raise _Unreadable from None
  items = doc.get("items") if isinstance(doc, dict) else None
  if not isinstance(items, list):
    raise _Unreadable
  seen: set[str] = set()
  for item in items:
    if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] in seen:
      raise _Unreadable
    seen.add(item["id"])
  try:
    Index.from_dict(doc)
  except (StateError, KeyError, TypeError, ValueError, AttributeError):
    raise _Unreadable from None
  return doc


class _Merge:
  """One three-way merge. `pick` says which side a conflicting value takes, so the
  same walk yields the ours-side and theirs-side documents the markers contrast."""

  def __init__(self, base, ours, theirs, pick: str, forced: set[str] = frozenset()) -> None:
    self.docs = (base, ours, theirs)
    self.pick = pick
    self.forced = forced
    self.conflicts: list[str] = []

  def run(self) -> dict[str, Any]:
    return self._dicts(*self.docs, [], top=True)

  def _conflict(self, path: list[str], base: Any, ours: Any, theirs: Any) -> Any:
    self.conflicts.append("/".join(path))
    return {"base": base, "ours": ours, "theirs": theirs}[self.pick]

  def _value(self, base: Any, ours: Any, theirs: Any, path: list[str]) -> Any:
    if _same(ours, theirs):
      return ours
    if _same(ours, base):
      return theirs
    if _same(theirs, base):
      return ours
    if isinstance(ours, dict) and isinstance(theirs, dict) and (
      base is _MISSING or isinstance(base, dict)
    ):
      return self._dicts(base if base is not _MISSING else {}, ours, theirs, path)
    return self._conflict(path, base, ours, theirs)

  def _dicts(self, base, ours, theirs, path: list[str], top: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in dict.fromkeys([*ours, *theirs, *base]):
      sides = (base.get(key, _MISSING), ours.get(key, _MISSING), theirs.get(key, _MISSING))
      if top and key == "items" and all(isinstance(s, list) for s in sides):
        value = self._items(*sides, [*path, key])
      elif top and key == "next_id" and all(type(s) is int for s in sides):
        value = max(sides)
      else:
        value = self._value(*sides, [*path, key])
      if value is not _MISSING:
        out[key] = value
    order = self._order(list(base), list(ours), list(theirs), set(out), [*path], False)
    return {key: out[key] for key in order}

  def _items(self, base, ours, theirs, path: list[str]) -> list[dict[str, Any]]:
    by_id = [{item["id"]: item for item in side} for side in (base, ours, theirs)]
    merged: dict[str, Any] = {}
    for item_id in dict.fromkeys([*by_id[1], *by_id[2], *by_id[0]]):
      b, o, t = (side.get(item_id, _MISSING) for side in by_id)
      item_path = [*path, item_id]
      if item_id in self.forced and not _same(o, t):
        value = self._conflict(item_path, b, o, t)
      elif _same(o, t):
        value = o
      elif _same(o, b):
        value = t
      elif _same(t, b):
        value = o
      elif b is not _MISSING and o is not _MISSING and t is not _MISSING:
        value = self._dicts(b, o, t, item_path)
      else:
        # Deleted on one side and edited on the other, or the same new id filed twice.
        value = self._conflict(item_path, b, o, t)
      if value is not _MISSING:
        merged[item_id] = value
    order = self._order(list(by_id[0]), list(by_id[1]), list(by_id[2]), set(merged), path, True)
    return [merged[item_id] for item_id in order]

  def _order(self, base, ours, theirs, keep: set[str], path, may_conflict: bool) -> list[str]:
    """Merge three orderings. The ids in all three keep the base order unless exactly
    one side reordered them; an id only one side has goes after the shared id it
    followed there, ours before theirs."""
    shared = set(ours) & set(theirs) & set(base)
    base_order = [i for i in base if i in shared]
    our_order = [i for i in ours if i in shared]
    their_order = [i for i in theirs if i in shared]
    if our_order == their_order:
      skeleton = our_order
    elif our_order == base_order:
      skeleton = their_order
    elif their_order == base_order or not may_conflict:
      skeleton = our_order
    else:
      self.conflicts.append("/".join([*path, "order"]))
      skeleton = {"base": base_order, "ours": our_order, "theirs": their_order}[self.pick]

    def gaps(side: list[str]) -> dict[str | None, list[str]]:
      found: dict[str | None, list[str]] = {None: []}
      anchor = None
      for i in side:
        if i in shared:
          anchor = i
          found.setdefault(anchor, [])
        else:
          found[anchor].append(i)
      return found

    ours_gaps, theirs_gaps = gaps(ours), gaps(theirs)
    out: list[str] = []
    placed: set[str] = set()
    for anchor in (None, *skeleton):
      for i in ([anchor] if anchor is not None else []) + ours_gaps.get(anchor, []) + theirs_gaps.get(anchor, []):
        if i in keep and i not in placed:
          placed.add(i)
          out.append(i)
    return out


def _problems(doc: dict[str, Any]) -> list[tuple[str, list[str]]]:
  """What is inconsistent in a merged index, as (message, the item ids involved).
  The checks are the ones that need only the index, since the driver has no project."""
  try:
    index = Index.from_dict(doc)
  except (StateError, KeyError, TypeError, ValueError, AttributeError) as e:
    return [(f"the merged index does not load: {e}", [])]
  found: list[tuple[str, list[str]]] = []
  folded: dict[str, list[str]] = defaultdict(list)
  keys: dict[str, list[str]] = defaultdict(list)
  for item in index.items:
    folded[item.id.casefold()].append(item.id)
    if item.key:
      keys[item.key].append(item.id)
  for same in folded.values():
    if len(same) > 1:
      found.append((f"ids {' and '.join(same)} differ only in case", same))
  for key, same in keys.items():
    if len(same) > 1:
      found.append((f"filing key {key!r} is on {' and '.join(same)}", same))
  for item_id, dep in graph.dangling(index):
    found.append((f"{item_id} depends on unknown id {dep}", [item_id]))
  for item in index.items:
    if item.discovered_from and index.get(item.discovered_from) is None:
      found.append((f"{item.id} was discovered from unknown id {item.discovered_from}", [item.id]))
  for cycle in graph.cycles(index):
    found.append((f"dependency cycle {' -> '.join(cycle)}", sorted(set(cycle))))
  water = idscheme.high_water([item.id for item in index.items], index.id_prefix)
  if index.next_id < water:
    found.append((f"next_id {index.next_id} is at or below an id in use", []))
  return found


def _owners(lines: list[str]) -> list[str | None]:
  """The item id each serialized line belongs to, or None outside the items."""
  owners: list[str | None] = [None] * len(lines)
  start = None
  for n, line in enumerate(lines):
    if line == "    {\n":
      start = n
    elif start is not None and line in ("    }\n", "    },\n"):
      found = next(
        (m.group(1) for m in (re.fullmatch(r'      "id": "(.*)",\n', x) for x in lines[start:n]) if m),
        None,
      )
      owners[start:n + 1] = [found] * (n + 1 - start)
      start = None
  return owners


def _markers(ours: str, theirs: str, reasons: dict[str, str]) -> tuple[str, int]:
  """Ours and theirs differ only at the conflicts, so each difference is one block."""
  lo, lt = ours.splitlines(keepends=True), theirs.splitlines(keepends=True)
  owner_o, owner_t = _owners(lo), _owners(lt)
  out: list[str] = []
  blocks = 0
  for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, lo, lt, autojunk=False).get_opcodes():
    if tag == "equal":
      out += lo[i1:i2]
      continue
    blocks += 1
    note = "; ".join(sorted({
      reasons[who] for who in (*owner_o[i1:i2], *owner_t[j1:j2]) if who in reasons
    }))
    suffix = f" ({note})" if note else ""
    out += [f"<<<<<<< ours{suffix}\n", *lo[i1:i2], "=======\n", *lt[j1:j2], f">>>>>>> theirs{suffix}\n"]
  return "".join(out), blocks


def _whole_file(ours: str, theirs: str) -> str:
  """The visible conflict for input the driver cannot merge by identity."""
  def ended(text: str) -> str:
    return text if text.endswith("\n") or not text else text + "\n"
  return f"<<<<<<< ours\n{ended(ours)}=======\n{ended(theirs)}>>>>>>> theirs\n"


def _counter(text: str) -> int | None:
  found = _COUNTER.findall(text)
  return int(found[0][0]) if len(found) == 1 else None


def _with_counter(text: str, value: int) -> str:
  return _COUNTER.sub(lambda m: f'  "next_id": {value}{m.group(2)}', text)


def _text_merge(cwd: Path, texts: list[str]) -> tuple[int, str]:
  """S191's merge for input that is not a readable index: the counter settled to the
  largest, then Git's own line merge."""
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
    return vcs.merge_file(cwd, *sides)


def _say(message: str) -> None:
  print(f"slicer merge-index: {message}", file=sys.stderr)


def merge(cwd: Path, base: Path, ours: Path, theirs: Path) -> int:
  """Merge `theirs` into `ours` against `base`, rewriting `ours`.

  Returns the number of conflict blocks left in `ours`; 0 means a clean merge.
  """
  texts = [path.read_text(encoding="utf-8") for path in (base, ours, theirs)]
  try:
    docs = [_load(text) for text in texts]
  except _Unreadable:
    _say("an input is not a readable index; merging it as text and never as clean")
    conflicts, text = _text_merge(cwd, texts)
    if not conflicts:
      text, conflicts = _whole_file(texts[1], texts[2]), 1
    jsonio.write_text(ours, text)
    return conflicts

  first = _Merge(*docs, "ours")
  merged = first.run()
  reasons: dict[str, str] = {}
  forced: set[str] = set()
  if not first.conflicts:
    inputs = {message for doc in docs for message, _ in _problems(doc)}
    sides = [{item["id"]: item for item in doc["items"]} for doc in docs[1:]]
    for message, involved in _problems(merged):
      if message in inputs:
        continue
      _say(message)
      # An item whose two sides agree cannot be shown as a conflict.
      differing = [i for i in involved if not _same(sides[0].get(i, _MISSING), sides[1].get(i, _MISSING))]
      if not differing:
        jsonio.write_text(ours, _whole_file(texts[1], texts[2]))
        return 1
      for item_id in differing:
        forced.add(item_id)
        reasons[item_id] = message
    if not forced:
      jsonio.write_text(ours, jsonio.dumps(merged))
      return 0

  ours_side, theirs_side = _Merge(*docs, "ours", forced), _Merge(*docs, "theirs", forced)
  text, blocks = _markers(jsonio.dumps(ours_side.run()), jsonio.dumps(theirs_side.run()), reasons)
  for where in first.conflicts:
    _say(f"conflict at {where}")
  if not blocks:
    text, blocks = _whole_file(texts[1], texts[2]), 1
  jsonio.write_text(ours, text)
  return blocks
