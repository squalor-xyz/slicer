"""Canonical in-memory state. Dataclasses only — no I/O lives here.

Every `to_dict` writes keys in a fixed order so that serialising twice
produces identical bytes; `slicer check` compares bytes, and a dict whose
key order wandered would make that check lie.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from slicer.errors import StateError

SCHEMA_VERSION = 1


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

  def section(self, heading: str) -> Section | None:
    for s in self.sections:
      if s.heading == heading:
        return s
    return None

  def boundary(self, marker: str) -> dict[str, str] | None:
    """Locate the scope-boundary paragraph, wherever it sits.

    It is not a section and not always last: it closes `## Implement` in
    some slices and follows `## Git` in others. Callers get a pointer, not
    a copy — the text stays in the section body that owns it.
    """
    for s in self.sections:
      for para in s.body.split("\n\n"):
        if para.startswith(marker):
          return {"section": s.heading, "text": para[len(marker):].strip()}
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
      "sections": [s.to_dict() for s in self.sections],
      "notes": list(self.notes),
    }

  @staticmethod
  def from_dict(d: Mapping[str, Any]) -> "Slice":
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
      sections=[Section.from_dict(s) for s in d.get("sections", [])],
      notes=list(d.get("notes", [])),
    )


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
  pass_key: str = ""
  group: str = ""
  reason: str = ""
  depends_on: list[str] = field(default_factory=list)
  # Eisenhower axes, 1-3, defaulting to a neutral 2 so unscored items interleave
  # rather than sinking or floating. Base score is importance-first (below).
  importance: int = 2
  urgency: int = 2

  def display_title(self) -> str:
    return self.short_title or self.title

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
      "fields": {
        "size": self.size,
        "flags": list(self.flags),
        "trees": list(self.trees),
        "trees_literal": self.trees_literal,
        "findings": self.findings,
        "pass": self.pass_key,
        "group": self.group,
        "reason": self.reason,
        "importance": self.importance,
        "urgency": self.urgency,
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
      size=f.get("size", ""),
      flags=list(f.get("flags", [])),
      trees=list(f.get("trees", [])),
      trees_literal=bool(f.get("trees_literal", False)),
      findings=f.get("findings", ""),
      pass_key=f.get("pass", ""),
      group=f.get("group", ""),
      reason=f.get("reason", ""),
      importance=int(f.get("importance", 2)),
      urgency=int(f.get("urgency", 2)),
    )


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
      "items": [it.to_dict() for it in self.items],
      "epilogue": self.epilogue,
    }

  @staticmethod
  def from_dict(d: Mapping[str, Any]) -> "Index":
    return Index(
      version=int(d.get("version", SCHEMA_VERSION)),
      id_prefix=d.get("id_prefix", "S"),
      id_width=int(d.get("id_width", 2)),
      next_id=int(d.get("next_id", 1)),
      preamble=d.get("preamble", ""),
      passes=[PassInfo.from_dict(p) for p in d.get("passes", [])],
      items=[Item.from_dict(i) for i in d.get("items", [])],
      epilogue=d.get("epilogue", ""),
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

  def to_dict(self) -> dict[str, Any]:
    return {
      "when": self.when,
      "item": self.item,
      "action": self.action,
      "from": self.frm,
      "to": self.to,
      "note": self.note,
    }

  @staticmethod
  def from_dict(d: Mapping[str, Any]) -> "LogEntry":
    return LogEntry(
      when=d["when"],
      item=d["item"],
      action=d["action"],
      frm=d.get("from", ""),
      to=d.get("to", ""),
      note=d.get("note", ""),
    )


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
