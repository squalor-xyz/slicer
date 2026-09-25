"""Slice ids and slugs.

Ids are unique for the life of a project and are never reused: a commit
message or a review that cites an id must resolve to exactly one slice
forever. Allocation therefore reads a stored high-water mark rather than
counting the items that happen to exist now.
"""

from __future__ import annotations

import re
import unicodedata

from slicer.errors import StateError


# An id becomes a filename and a markdown link target, so it has to be safe as
# both. Anything outside this set has escaped a directory or broken a link at
# some point: `../x` wrote outside the project, `S/1` hid a slice in a folder
# nothing scans, `S|1` and `S]1` broke the roadmap's Slice cell.
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def is_valid(item_id: str) -> bool:
  return bool(ID_RE.match(item_id)) and ".." not in item_id


def require_valid(item_id: str) -> None:
  """Refuse an id that cannot safely be a filename. Raises, never returns."""
  if not is_valid(item_id):
    raise StateError(
      f"{item_id!r} is not a usable id: an id must start with a letter or digit "
      f"and contain only letters, digits, '.', '-' and '_', and never '..'",
      code="bad_id",
    )


def format_id(prefix: str, number: int, width: int) -> str:
  return f"{prefix}{number:0{width}d}"


def parse_id(item_id: str, prefix: str) -> int | None:
  """Return the numeric part, or None if `item_id` is not of this scheme."""
  m = re.fullmatch(re.escape(prefix) + r"(\d+)", item_id)
  return int(m.group(1)) if m else None


def allocate(index, explicit: str | None = None) -> str:
  """Take `explicit` if free, else the next id above the high-water mark."""
  if explicit is not None:
    require_valid(explicit)
    if index.get(explicit) is not None:
      raise StateError(
        f"id {explicit} already exists; ids are never reused", code="already_exists"
      )
    # Two ids differing only in case are two rows in the index and one file on
    # a case-insensitive filesystem, where the second slice silently overwrites
    # the first. Say that, rather than "already exists", which is not true.
    clash = next((it.id for it in index.items if it.id.casefold() == explicit.casefold()), None)
    if clash is not None:
      raise StateError(
        f"id {explicit} differs from {clash} only in case; on a case-insensitive "
        f"filesystem they would be the same file",
        code="case_collision",
      )
    number = parse_id(explicit, index.id_prefix)
    if number is not None and number >= index.next_id:
      index.next_id = number + 1
    return explicit
  new_id = format_id(index.id_prefix, index.next_id, index.id_width)
  # A hostile prefix makes every generated id traversing, so the generated
  # side needs the rule too, not just the explicit one.
  require_valid(new_id)
  index.next_id += 1
  return new_id


def high_water(ids: list[str], prefix: str) -> int:
  """One above the largest numeric id seen, so import never reuses."""
  numbers = [n for n in (parse_id(i, prefix) for i in ids) if n is not None]
  return max(numbers) + 1 if numbers else 1


def slug(title: str, limit: int = 60) -> str:
  """A filesystem-safe, lowercase, hyphenated form of a title."""
  decomposed = unicodedata.normalize("NFKD", title)
  ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
  cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-").lower()
  if len(cleaned) <= limit:
    return cleaned or "untitled"
  cut = cleaned[:limit]
  # Prefer a whole word over a truncated one, but never return nothing.
  return (cut.rsplit("-", 1)[0] if "-" in cut else cut) or "untitled"
