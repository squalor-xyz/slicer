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


def format_id(prefix: str, number: int, width: int) -> str:
  return f"{prefix}{number:0{width}d}"


def parse_id(item_id: str, prefix: str) -> int | None:
  """Return the numeric part, or None if `item_id` is not of this scheme."""
  m = re.fullmatch(re.escape(prefix) + r"(\d+)", item_id)
  return int(m.group(1)) if m else None


def allocate(index, explicit: str | None = None) -> str:
  """Take `explicit` if free, else the next id above the high-water mark."""
  if explicit is not None:
    if index.get(explicit) is not None:
      raise StateError(f"id {explicit} already exists; ids are never reused")
    number = parse_id(explicit, index.id_prefix)
    if number is not None and number >= index.next_id:
      index.next_id = number + 1
    return explicit
  new_id = format_id(index.id_prefix, index.next_id, index.id_width)
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
