"""Keep unfinished roadmap answers separate from persisted project state.

Key transitions and editor requests are terminal-independent. Only the TUI
adapter applies a completed draft or hands a section to the external editor.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from slicer.errors import StateError
from slicer.outline import ItemSpec, SectionSpec


ENTER = ("\n", "\r", "KEY_ENTER")
BACKSPACE = ("KEY_BACKSPACE", "\x7f", "\b")
FIELDS = (
  ("Title", "Required; use this title when referring to a dependency"),
  ("Size", "Optional free text, for example S, M or L"),
  ("Trees", "Optional comma-separated tree names"),
  ("Findings", "Optional findings or references"),
  ("Importance", "1, 2 or 3; default 2"),
  ("Urgency", "1, 2 or 3; default 2"),
  ("Group", "Optional group label"),
  ("Depends", "Comma-separated exact titles; later draft items are allowed"),
  ("Effort", "1, 2 or 3; blank leaves it unset"),
)
DEFAULTS = ("", "", "", "", "2", "2", "", "", "")


def text_input(text: str, key: str) -> tuple[str, str]:
  """Return the edited value and an optional navigation intent."""
  if key in ENTER:
    return text, "accept"
  if key == "\x1b":
    return text, "cancel"
  if key == "KEY_BTAB":
    return text, "back"
  if key in BACKSPACE:
    return text[:-1], ""
  if len(key) == 1 and key.isprintable():
    return text + key, ""
  return text, ""


def csv(text: str) -> list[str]:
  return [part.strip() for part in text.split(",") if part.strip()]


@dataclass
class DraftItem:
  values: list[str] = field(default_factory=lambda: list(DEFAULTS))
  sections: list[SectionSpec] = field(default_factory=list)

  def spec(self) -> ItemSpec:
    values = [value.strip() for value in self.values]
    if not values[0]:
      raise StateError("An item title is required; edit its Title before saving.")
    for at in (4, 5):
      if values[at] not in ("1", "2", "3"):
        raise StateError(f"{FIELDS[at][0]} must be 1, 2 or 3.")
    effort = values[8]
    if effort not in ("", "1", "2", "3"):
      raise StateError("Effort must be 1, 2 or 3, or blank.")
    return ItemSpec(title=values[0], size=values[1], trees=csv(values[2]),
                    findings=values[3], importance=int(values[4]), urgency=int(values[5]),
                    group=values[6], depends=csv(values[7]),
                    effort=int(effort) if effort else None,
                    sections=[SectionSpec(s.heading, s.body) for s in self.sections])


@dataclass
class Wizard:
  headings: list[str]
  title: str = ""
  items: list[DraftItem] = field(default_factory=list)
  mode: str = "field"
  item_at: int = 0
  step: int = -1  # -1 is the document heading, otherwise field/section index
  review_at: int = 0
  reviewing: bool = False
  previous_mode: str = "field"
  error: str = ""

  @property
  def dirty(self) -> bool:
    return bool(self.title or any(
      tuple(item.values) != DEFAULTS or any(s.body for s in item.sections)
      for item in self.items
    ))

  @property
  def section(self) -> SectionSpec | None:
    if self.step < len(FIELDS) or not self.items:
      return None
    return self.items[self.item_at].sections[self.step - len(FIELDS)]

  @property
  def text(self) -> str:
    return self.title if self.step == -1 else self.items[self.item_at].values[self.step]

  def add_item(self) -> None:
    self.items.append(DraftItem(sections=[SectionSpec(h, "") for h in self.headings]))
    self.item_at, self.step = len(self.items) - 1, 0
    self.mode, self.reviewing = "field", False

  def review_rows(self) -> list[tuple[str, int, int]]:
    rows = [(f"Roadmap heading: {self.title or '(unchanged)'}", -1, -1)]
    for at, item in enumerate(self.items):
      prefix = f"Item {at + 1}"
      rows.extend((f"{prefix} · {label}: {value or '(empty)'}", at, step)
                  for step, ((label, _), value) in enumerate(zip(FIELDS, item.values)))
      rows.extend((f"{prefix} · {section.heading}: {section.body or '(empty)'}",
                   at, len(FIELDS) + step) for step, section in enumerate(item.sections))
    rows.extend([("Add another item", -2, -1), ("Save roadmap", -3, -1)])
    return rows

  def lines(self) -> tuple[list[str], int]:
    if self.mode == "discard":
      return ["Discard all wizard answers?", "y: discard   n / Esc: keep editing"], -1
    if self.mode == "another":
      return ["Add another item?", "y: add item   n / Enter: review before saving",
              "Shift-Tab: back   Esc: cancel wizard"], -1
    if self.mode == "review":
      return [row[0] for row in self.review_rows()], self.review_at
    if self.step == -1:
      return ["Roadmap heading (optional)",
              "Prepends # Title to existing preamble prose. Blank leaves it unchanged."], -1
    heading = f"Item {self.item_at + 1} of {len(self.items)}"
    if self.section is not None:
      return [heading, self.section.heading,
              "e: open $EDITOR   Enter: keep body and continue",
              *self.section.body.splitlines()], -1
    label, hint = FIELDS[self.step]
    return [heading, label, hint], -1

  def _advance(self) -> None:
    if self.reviewing:
      self.mode, self.reviewing = "review", False
    elif self.step == -1:
      if self.items:
        self.item_at, self.step = 0, 0
      else:
        self.add_item()
    elif self.step + 1 < len(FIELDS) + len(self.headings):
      self.step += 1
    elif self.item_at + 1 < len(self.items):
      self.item_at, self.step = self.item_at + 1, 0
    else:
      self.mode = "another"

  def _back(self) -> None:
    if self.reviewing:
      self.mode, self.reviewing = "review", False
    elif self.step > 0:
      self.step -= 1
    elif self.item_at > 0 and self.step == 0:
      self.item_at -= 1
      self.step = len(FIELDS) + len(self.headings) - 1
    else:
      self.step = -1

  def handle(self, key: str) -> str:
    """Return editor/save/cancel intents; never read or write project state."""
    if key == "KEY_RESIZE":
      return ""
    if self.mode == "discard":
      if key.lower() == "y":
        return "cancel"
      if key.lower() == "n" or key == "\x1b":
        self.mode = self.previous_mode
      return ""
    if key == "\x1b":
      if not self.dirty:
        return "cancel"
      self.previous_mode, self.mode = self.mode, "discard"
      return ""
    self.error = ""
    if self.mode == "review":
      rows = self.review_rows()
      if key in ("j", "KEY_DOWN", "k", "KEY_UP"):
        delta = 1 if key in ("j", "KEY_DOWN") else -1
        self.review_at = max(0, min(self.review_at + delta, len(rows) - 1))
      elif key in ENTER or key == "e":
        _, at, step = rows[self.review_at]
        if at == -3:
          return "save"
        if at == -2:
          self.add_item()
        else:
          self.item_at, self.step = max(0, at), step
          self.mode, self.reviewing = "field", True
      elif key == "KEY_BTAB":
        self.item_at = len(self.items) - 1
        self.step = len(FIELDS) + len(self.headings) - 1
        self.mode = "another"
      return ""
    if self.mode == "another":
      if key.lower() == "y":
        self.add_item()
      elif key.lower() == "n" or key in ENTER:
        self.mode, self.review_at = "review", 0
      elif key == "KEY_BTAB":
        self.mode = "field"
      return ""
    if key == "KEY_BTAB":
      self._back()
      return ""
    if self.section is not None:
      if key == "e":
        return "editor"
      if key in ENTER:
        self._advance()
      return ""
    value, intent = text_input(self.text, key)
    if self.step == -1:
      self.title = value
    else:
      self.items[self.item_at].values[self.step] = value
    if intent == "accept":
      value = value.strip()
      if self.step == 0 and not value:
        self.error = "An item title is required."
      elif self.step in (4, 5) and value not in ("1", "2", "3"):
        self.error = f"{FIELDS[self.step][0]} must be 1, 2 or 3."
      elif self.step == len(FIELDS) - 1 and value not in ("", "1", "2", "3"):
        self.error = "Effort must be 1, 2 or 3, or blank."
      else:
        self._advance()
    return ""

  def editor_result(self, body: str | None) -> None:
    if body is None:
      self.error = "Editor failed; section body unchanged. Press e to retry."
    elif self.section is not None:
      self.section.body = body

  def specs(self) -> list[ItemSpec]:
    if not self.items:
      raise StateError("Add at least one item before saving.")
    return [item.spec() for item in self.items]

  def preamble(self, existing: str) -> str | None:
    title = self.title.strip()
    if not title:
      return None
    return f"# {title}\n\n{existing}" if existing else f"# {title}"
