"""Canonical in-memory state. Dataclasses only — no I/O lives here.

Every `to_dict` writes keys in a fixed order so that serialising twice
produces identical bytes; `slicer check` compares bytes, and a dict whose
key order wandered would make that check lie.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from slicer.errors import StateError, reject_future_schema

SCHEMA_VERSION = 5


@dataclass
class Section:
  """One `## heading` block of a slice, body verbatim."""

  heading: str
  body: str

  def to_dict(self) -> dict[str, Any]:
    return {"heading": self.heading, "body": self.body}

  @staticmethod
  def from_dict(d: Mapping[str, Any]) -> "Section":
    return Section(heading=d["heading"], body=d.get("body", ""))


def extract_boundary(
  sections: list[Section], marker: str, lead: list[str] | None = None,
) -> str:
  """Lift the first inline boundary out of a lead, then out of section bodies.

  An empty marker lifts nothing. `lead` defaults to empty so callers that only
  have sections, including the migrator, keep the old scan.
  """
  if not marker:
    return ""
  if lead:
    for at, paragraph in enumerate(lead):
      if paragraph.startswith(marker):
        del lead[at]
        return paragraph
  for section in sections:
    paragraphs = section.body.split("\n\n")
    for at, paragraph in enumerate(paragraphs):
      if paragraph.startswith(marker):
        del paragraphs[at]
        section.body = "\n\n".join(paragraphs)
        return paragraph
  return ""


@dataclass
class Slice:
  """The prose of one slice. Sections are an ordered list, never a map.

  Slices in the wild carry headings no schema names, sometimes more than
  once (`## Pick (landed)` appears twice across this repo's done slices).
  A map would lose both their order and the repeats.
  """

  id: str
  title: str
  lead: list[str] = field(default_factory=list)
  depends_note: str | None = None
  sections: list[Section] = field(default_factory=list)
  notes: list[str] = field(default_factory=list)
  findings_note: str = ""
  size: str = ""
  flags: list[str] = field(default_factory=list)
  trees_note: str = ""
  trees_plural: bool = False
  boundary: str = ""

  def section(self, heading: str) -> Section | None:
    for s in self.sections:
      if s.heading == heading:
        return s
    return None

  def to_dict(self) -> dict[str, Any]:
    return {
      "id": self.id,
      "title": self.title,
      "lead": list(self.lead),
      "depends_note": self.depends_note,
      "findings_note": self.findings_note,
      "size": self.size,
      "flags": list(self.flags),
      "trees_note": self.trees_note,
      "trees_plural": self.trees_plural,
      "boundary": self.boundary,
      "sections": [s.to_dict() for s in self.sections],
      "notes": list(self.notes),
    }

  @staticmethod
  def from_dict(d: Mapping[str, Any], *, boundary_marker: str = "") -> "Slice":
    sections = [Section.from_dict(s) for s in d.get("sections", [])]
    boundary = d["boundary"] if "boundary" in d else extract_boundary(sections, boundary_marker)
    if not isinstance(boundary, str):
      raise TypeError("boundary must be a string")
    return Slice(
      id=d["id"],
      title=d["title"],
      lead=list(d.get("lead", [])),
      depends_note=d.get("depends_note"),
      findings_note=d.get("findings_note", ""),
      size=d.get("size", ""),
      flags=list(d.get("flags", [])),
      trees_note=d.get("trees_note", ""),
      trees_plural=bool(d.get("trees_plural", False)),
      boundary=boundary,
      sections=sections,
      notes=list(d.get("notes", [])),
    )


def is_unscored(importance: int, urgency: int, effort: int | None) -> bool:
  """True when importance and urgency are the default 2 and effort is unset.

  A deliberate 2/2 with an effort estimate is scored; only the untouched
  defaults are, because they leave the queue with nothing to sort on.
  """
  return importance == 2 and urgency == 2 and effort is None


@dataclass
class NoteRecord:
  """Keep note identity and provenance independent of its display paragraph."""

  id: str
  kind: str
  text: str
  created_at: str
  attempt: int | None

  def display(self) -> str:
    if not self.kind:
      return self.text
    before, separator, after = self.text.partition(" — ")
    label = f"[{self.kind}] "
    return before + separator + label + after if separator else label + self.text

  def to_dict(self) -> dict[str, Any]:
    return {"id": self.id, "kind": self.kind, "text": self.text,
            "created_at": self.created_at, "attempt": self.attempt}

  @staticmethod
  def from_dict(d: Mapping[str, Any]) -> "NoteRecord":
    if not isinstance(d, Mapping) or any(
      not isinstance(d.get(k), str) for k in ("id", "kind", "text", "created_at")
    ) or not d["id"]:
      raise StateError("note record must have string id, kind, text and created_at", code="corrupt")
    attempt = d.get("attempt")
    if attempt is not None:
      attempt = _attempts(attempt)
    return NoteRecord(d["id"], d["kind"], d["text"], d["created_at"], attempt)


@dataclass
class Item:
  """One roadmap entry. May exist with no slice file (`has_slice=False`).

  `title` and `short_title` are two fields, not one with a discrepancy:
  the index cell and the slice H1 differ in 35 of this repo's 49 slices,
  deliberately — the index is scannable, the file is explicit.
  """

  id: str
  title: str
  status: str
  has_slice: bool = False
  short_title: str = ""
  size: str = ""
  flags: list[str] = field(default_factory=list)
  trees: list[str] = field(default_factory=list)
  trees_literal: bool = False
  findings: str = ""
  discovered_from: str = ""
  pass_key: str = ""
  group: str = ""
  reason: str = ""
  depends_on: list[str] = field(default_factory=list)
  # Structured item notes; legacy slice notes remain strings.
  note_records: list[NoteRecord] = field(default_factory=list)
  # Eisenhower axes, 1-3, defaulting to a neutral 2 so unscored items interleave
  # rather than sinking or floating. Base score is importance-first (below).
  importance: int = 2
  urgency: int = 2
  # Optional implementation weight, 1-3. Unset stays out of priority.
  effort: int | None = None
  # Starts from open, plus rejected reviews. Restart-safe; the log is not.
  attempts: int = 0
  # Who has this item, and when that was recorded. Empty means unclaimed.
  # The time is stored on the item so render never invents one.
  claim_owner: str = ""
  claim_at: str = ""

  @property
  def notes(self) -> list[str]:
    return [record.display() for record in self.note_records]

  def persisted_dict(self) -> dict[str, Any]:
    data = self.to_dict()
    del data["notes"]
    return data

  def display_title(self) -> str:
    return self.short_title or self.title

  @property
  def unscored(self) -> bool:
    """Every priority field still at its default, so the item ranks on nothing."""
    return is_unscored(self.importance, self.urgency, self.effort)

  @property
  def score(self) -> int:
    """Base priority: importance leads, urgency breaks ties. 11..33."""
    return self.importance * 10 + self.urgency

  @property
  def quadrant(self) -> str:
    """The Eisenhower quadrant, or '-' when either axis is the neutral 2.

    The four quadrants are a 2x2 (high/low); a 1-3 axis has no clean high or
    low at 2, so the label appears only once both axes are decisively 1 or 3.
    That keeps the default 2/2 honestly unlabelled rather than calling every
    unscored item 'do-now'. The score still orders it.
    """
    if self.importance == 2 or self.urgency == 2:
      return "-"
    important, urgent = self.importance == 3, self.urgency == 3
    if important and urgent:
      return "do-now"
    if important:
      return "schedule"
    if urgent:
      return "delegate"
    return "drop"

  def to_dict(self) -> dict[str, Any]:
    return {
      "id": self.id,
      "title": self.title,
      "short_title": self.short_title,
      "status": self.status,
      "has_slice": self.has_slice,
      "depends_on": list(self.depends_on),
      "notes": list(self.notes),
      "note_records": [n.to_dict() for n in self.note_records],
      "claim": (
        {"owner": self.claim_owner, "at": self.claim_at} if self.claim_owner else None
      ),
      "fields": {
        "size": self.size,
        "flags": list(self.flags),
        "trees": list(self.trees),
        "trees_literal": self.trees_literal,
        "findings": self.findings,
        "discovered_from": self.discovered_from,
        "pass": self.pass_key,
        "group": self.group,
        "reason": self.reason,
        "importance": self.importance,
        "urgency": self.urgency,
        "effort": self.effort,
        "attempts": self.attempts,
      },
    }

  @staticmethod
  def from_dict(d: Mapping[str, Any]) -> "Item":
    f = d.get("fields", {})
    return Item(
      id=d["id"],
      title=d["title"],
      short_title=d.get("short_title", ""),
      status=d["status"],
      has_slice=bool(d.get("has_slice", False)),
      depends_on=list(d.get("depends_on", [])),
      note_records=(
        [NoteRecord.from_dict(n) for n in d["note_records"]] if "note_records" in d else
        [NoteRecord(f"{d['id']}:legacy:{i}", "", text, "", None)
         for i, text in enumerate(d.get("notes", []))]
      ),
      claim_owner=_claim_owner(d.get("claim")),
      claim_at=_claim_at(d.get("claim")),
      size=f.get("size", ""),
      flags=list(f.get("flags", [])),
      trees=list(f.get("trees", [])),
      trees_literal=bool(f.get("trees_literal", False)),
      findings=f.get("findings", ""),
      discovered_from=_discovered_from(f.get("discovered_from", "")),
      pass_key=f.get("pass", ""),
      group=f.get("group", ""),
      reason=f.get("reason", ""),
      importance=int(f.get("importance", 2)),
      urgency=int(f.get("urgency", 2)),
      effort=_optional_effort(f["effort"]) if "effort" in f else None,
      attempts=_attempts(f["attempts"]) if "attempts" in f else 0,
    )


def _discovered_from(value: object) -> str:
  """Historical items have no source; stored references must be strings."""
  if not isinstance(value, str):
    raise StateError("discovered_from must be a string", code="corrupt")
  return value


def _claim_parts(value: object) -> tuple[str, str]:
  """Owner and time from a stored claim. Missing is unclaimed."""
  if value is None:
    return "", ""
  if not isinstance(value, Mapping):
    raise StateError(f"claim must be an object, not {value!r}", code="corrupt")
  owner = value.get("owner", "")
  at = value.get("at", "")
  if not isinstance(owner, str) or not isinstance(at, str):
    raise StateError("claim owner and at must be strings", code="corrupt")
  if bool(owner) != bool(at):
    raise StateError("claim needs both an owner and a time", code="corrupt")
  return owner, at


def _claim_owner(value: object) -> str:
  return _claim_parts(value)[0]


def _claim_at(value: object) -> str:
  return _claim_parts(value)[1]


def _attempts(value: object) -> int:
  """Load a stored attempt count. Missing is handled by the caller."""
  if isinstance(value, bool) or not isinstance(value, int) or value < 0:
    raise StateError(
      f"attempts must be a non-negative integer, not {value!r}", code="corrupt",
    )
  return value


def _optional_effort(value: object) -> int | None:
  """Load a stored effort. Missing is handled by the caller; null is unset."""
  if value is None:
    return None
  try:
    n = int(value)  # type: ignore[arg-type]
  except (TypeError, ValueError):
    raise StateError(f"effort must be a number 1-3, not {value!r}", code="corrupt") from None
  if not 1 <= n <= 3:
    raise StateError(f"effort must be 1, 2 or 3, not {n}", code="corrupt")
  return n


def effort_rank(item: Item) -> tuple[int, int]:
  """Assigned effort ascending, then unset. A stable sort keeps tie order."""
  if item.effort is None:
    return (1, 0)
  return (0, item.effort)


@dataclass
class PassInfo:
  """A grouping heading plus the prose that surrounds its table.

  slicer does not model review passes as a concept — this only carries the
  author's own words through so that rendering cannot silently drop them.
  """

  key: str
  heading: str = ""
  intro: str = ""
  outro: str = ""

  def to_dict(self) -> dict[str, Any]:
    return {"key": self.key, "heading": self.heading, "intro": self.intro, "outro": self.outro}

  @staticmethod
  def from_dict(d: Mapping[str, Any]) -> "PassInfo":
    return PassInfo(
      key=d["key"], heading=d.get("heading", ""), intro=d.get("intro", ""), outro=d.get("outro", "")
    )


@dataclass
class Index:
  """The ordered queue. Position in `items` is the priority."""

  next_id: int = 1
  id_prefix: str = "S"
  id_width: int = 2
  items: list[Item] = field(default_factory=list)
  passes: list[PassInfo] = field(default_factory=list)
  preamble: str = ""
  epilogue: str = ""
  goals: str = ""
  non_goals: str = ""
  version: int = SCHEMA_VERSION

  def get(self, item_id: str) -> Item | None:
    for it in self.items:
      if it.id == item_id:
        return it
    return None

  def require(self, item_id: str) -> Item:
    it = self.get(item_id)
    if it is None:
      raise StateError(f"no such item: {item_id}", code="no_such_item")
    return it

  def position(self, item_id: str) -> int:
    for i, it in enumerate(self.items):
      if it.id == item_id:
        return i
    raise StateError(f"no such item: {item_id}", code="no_such_item")

  def by_status(self, status: str) -> list[Item]:
    return [it for it in self.items if it.status == status]

  def pass_info(self, key: str) -> PassInfo | None:
    for p in self.passes:
      if p.key == key:
        return p
    return None

  def pass_keys(self) -> list[str]:
    """Declared pass order, then any pass only an item mentions.

    Both the renderer and the prose commands ask here, so they cannot
    disagree about which passes exist. Declared-but-empty passes come
    first because a pass is usually opened before it has any slices;
    undeclared ones are appended rather than dropped, so an item can
    never become invisible by naming a pass nobody declared.
    """
    keys = [p.key for p in self.passes]
    for item in self.items:
      if item.pass_key not in keys:
        keys.append(item.pass_key)
    return keys

  def to_dict(self) -> dict[str, Any]:
    return {
      "version": self.version,
      "id_prefix": self.id_prefix,
      "id_width": self.id_width,
      "next_id": self.next_id,
      "preamble": self.preamble,
      "passes": [p.to_dict() for p in self.passes],
      "items": [it.persisted_dict() for it in self.items],
      "epilogue": self.epilogue,
      "goals": self.goals,
      "non_goals": self.non_goals,
    }

  @staticmethod
  def from_dict(d: Mapping[str, Any]) -> "Index":
    version = int(d.get("version", SCHEMA_VERSION))
    reject_future_schema("index.json", version, SCHEMA_VERSION)
    return Index(
      version=version,
      id_prefix=d.get("id_prefix", "S"),
      id_width=int(d.get("id_width", 2)),
      next_id=int(d.get("next_id", 1)),
      preamble=d.get("preamble", ""),
      passes=[PassInfo.from_dict(p) for p in d.get("passes", [])],
      items=[Item.from_dict(i) for i in d.get("items", [])],
      epilogue=d.get("epilogue", ""),
      goals=d.get("goals", ""),
      non_goals=d.get("non_goals", ""),
    )


@dataclass
class LogEntry:
  """One recorded transition. Append-only; slicer never rewrites history."""

  when: str
  item: str
  action: str
  frm: str = ""
  to: str = ""
  note: str = ""
  # Who acted, on lifecycle actions (start, claim, handoff, release, done).
  # Written only when set, so every other line keeps its old shape.
  by: str = ""

  def to_dict(self) -> dict[str, Any]:
    out = {
      "when": self.when,
      "item": self.item,
      "action": self.action,
      "from": self.frm,
      "to": self.to,
      "note": self.note,
    }
    if self.by:
      out["by"] = self.by
    return out

  @staticmethod
  def from_dict(d: Mapping[str, Any]) -> "LogEntry":
    return LogEntry(
      when=d["when"],
      item=d["item"],
      action=d["action"],
      frm=d.get("from", ""),
      to=d.get("to", ""),
      note=d.get("note", ""),
      by=d.get("by", ""),
    )


def lean(payload: Any) -> Any:
  """A smaller copy of a JSON payload for `--json --lean`.

  Files on disk keep the full `to_dict` shape. This only drops values that
  say nothing: empty strings, lists, and dicts, nulls other than the
  `item: null` sentinel, `trees_literal: false`, a `short_title` that
  repeats `title`, `attempts` when it is 0, and `path` on an item. Other
  false booleans and every other number stay, so `has_slice: false` and a
  default score are still visible.
  """
  if isinstance(payload, list):
    return [lean(item) for item in payload]
  if not isinstance(payload, dict):
    return payload
  item = "id" in payload and "title" in payload
  title = payload.get("title")
  out: dict[str, Any] = {}
  for key, value in payload.items():
    if item and key == "path":
      continue
    if key == "trees_literal" and value is False:
      continue
    if key == "short_title" and value == title:
      continue
    if key == "attempts" and value == 0:
      continue
    cleaned = lean(value)
    if cleaned == "" or cleaned == [] or cleaned == {}:
      continue
    if cleaned is None and key != "item":
      continue
    out[key] = cleaned
  return out


def counts(items: Iterable[Item], key: str) -> dict[str, int]:
  """Tally items by a named attribute, for `stats`. List values count once each."""
  out: dict[str, int] = {}
  for it in items:
    value = getattr(it, key)
    values = value if isinstance(value, list) else [value]
    for v in values:
      if v == "":
        continue
      out[str(v)] = out.get(str(v), 0) + 1
  return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def cross_counts(items: Iterable[Item], row_key: str, col_key: str) -> dict[str, dict[str, int]]:
  """Tally items by two attributes, e.g. status within each tree. A list-valued
  row (like `trees`) counts under each of its values; empty rows are skipped.
  Rows are name-sorted for a deterministic render."""
  out: dict[str, dict[str, int]] = {}
  for it in items:
    value = getattr(it, row_key)
    rows = value if isinstance(value, list) else [value]
    col = str(getattr(it, col_key))
    for r in rows:
      if r == "":
        continue
      bucket = out.setdefault(str(r), {})
      bucket[col] = bucket.get(col, 0) + 1
  return {r: out[r] for r in sorted(out)}
