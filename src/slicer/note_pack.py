"""Bound pickup context without changing the complete note audit trail."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from slicer.errors import StateError
from slicer.model import NoteRecord


TRUST = ("unverified", "machine-confirmed", "human-reviewed")


@dataclass
class Candidate:
  id: str
  kind: str
  text: str
  trust: str
  created_at: str
  stale_after: str
  record: NoteRecord | None


def _instant(value: str) -> datetime | None:
  try:
    result = datetime.fromisoformat(value)
  except ValueError:
    return None
  return result.astimezone(timezone.utc) if result.tzinfo is not None else None


def _rank(candidate: Candidate) -> tuple[int, bool, datetime]:
  created = _instant(candidate.created_at)
  return (TRUST.index(candidate.trust), created is not None,
          created or datetime.min.replace(tzinfo=timezone.utc))


def select(
  records: list[NoteRecord], slice_notes: list[str], *, now: datetime,
  kinds: list[str] | None = None, min_trust: str = "unverified",
  budget: int | None = None,
) -> tuple[list[NoteRecord], list[str], dict[str, object]]:
  """Select whole display notes; the caller supplies the invocation's clock."""
  if min_trust not in TRUST:
    raise StateError(f"unknown minimum trust {min_trust!r}; choose {', '.join(TRUST)}", code="usage")
  if budget is not None and budget < 0:
    raise StateError("--notes-budget must be a nonnegative integer", code="usage")
  candidates = [Candidate(n.id, n.kind, n.display(), n.trust,
    n.created_at, n.stale_after, n) for n in records]
  candidates.extend(Candidate(f"slice-legacy-{i}", "", text, "unverified", "", "", None)
    for i, text in enumerate(slice_notes, 1))
  reasons: dict[str, str] = {}
  eligible = []
  for candidate in candidates:
    if kinds is not None and candidate.kind not in kinds:
      reasons[candidate.id] = "kind"
      continue
    expiry = _instant(candidate.stale_after) if candidate.stale_after else None
    if candidate.stale_after and expiry is None:
      raise StateError(f"note {candidate.id} has invalid stale_after; repair its timestamp", code="corrupt")
    if expiry is not None and expiry <= now:
      reasons[candidate.id] = "expired"
    elif TRUST.index(candidate.trust) < TRUST.index(min_trust):
      reasons[candidate.id] = "trust"
    else:
      eligible.append(candidate)
  eligible.sort(key=_rank, reverse=True)
  selected = []
  characters = 0
  for candidate in eligible:
    cost = len(candidate.text) + (1 if selected else 0)
    if budget is not None and characters + cost > budget:
      reasons[candidate.id] = "budget"
      continue
    selected.append(candidate)
    characters += cost
  manifest = {
    "selected_ids": [n.id for n in selected],
    "omitted": [{"id": n.id, "reason": reasons[n.id]} for n in candidates if n.id in reasons],
    "characters": characters,
    "budget": budget,
    "min_trust": min_trust,
  }
  return ([n.record for n in selected if n.record is not None],
          [n.text for n in selected if n.record is None], manifest)
