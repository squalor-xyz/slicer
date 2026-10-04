"""Keep terminal capabilities separate from the meaning of visual cues."""

from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass

from slicer.config import Config


MIN_WIDTH, MIN_HEIGHT = 80, 10


def status_role(status: str, blocked: bool, config: Config) -> str:
  if blocked and status in (config.open_status, config.started_status):
    return "blocked"
  for role, value in (("started", config.started_status), ("started", config.reviewing_status),
                      ("done", config.done_status), ("parked", config.parked_status),
                      ("review", config.review_status)):
    if value and status == value:
      return role
  return "normal"


@dataclass
class Palette:
  roles: dict[str, int]
  focused: int
  inactive: int

  def attr(self, role: str = "normal", *, selected: bool = False, focused: bool = False) -> int:
    if selected:
      return self.focused if focused else self.inactive
    return self.roles.get(role, self.roles["normal"])


def monochrome(terminal=None) -> Palette:
  if terminal is None:
    import curses as terminal
  roles = {name: terminal.A_NORMAL for name in (
    "normal", "started", "done", "parked", "success", "info"
  )}
  roles.update({name: terminal.A_BOLD for name in (
    "heading", "field", "blocked", "priority", "error", "review"
  )})
  roles["dim"] = terminal.A_DIM
  return Palette(roles, terminal.A_REVERSE | terminal.A_BOLD, terminal.A_BOLD)


def setup_palette(terminal=None, environ=None) -> Palette:
  if terminal is None:
    import curses as terminal
  if environ is None:
    environ = os.environ
  mono = monochrome(terminal)
  if environ.get("NO_COLOR"):
    return mono
  try:
    if not terminal.has_colors():
      return mono
    terminal.start_color()
    if terminal.COLORS < 8 or terminal.COLOR_PAIRS < 5:
      return mono
    terminal.use_default_colors()
    colors = (terminal.COLOR_CYAN, terminal.COLOR_GREEN, terminal.COLOR_YELLOW, terminal.COLOR_RED)
    for pair, color in enumerate(colors, 1):
      terminal.init_pair(pair, color, -1)
    roles = dict(mono.roles)
    for pair, names in enumerate((
      ("heading", "started"), ("done", "success"),
      ("parked", "blocked", "priority"), ("error",),
    ), 1):
      for name in names:
        roles[name] |= terminal.color_pair(pair)
    _review_color(terminal, roles)
    return Palette(roles, mono.focused, mono.inactive)
  except terminal.error:
    return mono


def _review_color(terminal, roles: dict[str, int]) -> None:
  """Magenta is optional. A failed fifth pair keeps the four-color palette."""
  if terminal.COLOR_PAIRS < 6 or not hasattr(terminal, "COLOR_MAGENTA"):
    return
  try:
    terminal.init_pair(5, terminal.COLOR_MAGENTA, -1)
    roles["review"] |= terminal.color_pair(5)
  except terminal.error:
    return


def clipped(text: str, width: int) -> str:
  """Clip in terminal cells and replace controls that could move the cursor."""
  out = []
  used = 0
  for char in text:
    if unicodedata.category(char).startswith("C") or char in "\n\r\t":
      char = " "
    size = 0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in "WF" else 1
    if used + size > width:
      break
    if size == 0 and not out:
      continue
    out.append(char)
    used += size
  return "".join(out)


def cell_width(text: str) -> int:
  return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in "WF" else 1
             for c in text)


class Canvas:
  """One bounds/resize guard shared by every TUI drawing path."""

  def __init__(self, screen) -> None:
    self.screen = screen
    self.height, self.width = screen.getmaxyx()

  def _call(self, fn, *args) -> None:
    import curses
    if self.screen.getmaxyx() != (self.height, self.width):
      return
    try:
      fn(*args)
    except curses.error:
      # A new frame will use the resized dimensions. Other errors are defects.
      if self.screen.getmaxyx() == (self.height, self.width):
        raise

  def put(self, y: int, x: int, text: str, attr: int = 0, limit: int | None = None) -> None:
    room = self.width - x - 1
    if limit is not None:
      room = min(room, limit)
    if 0 <= y < self.height and 0 <= x < self.width and room > 0:
      text = clipped(text, room)
      if text:
        # n counts encoded bytes on some curses implementations; clipping is
        # already done in display cells, so supply enough bytes for all text.
        self._call(self.screen.addnstr, y, x, text, len(text.encode("utf-8")), attr)

  def erase(self) -> None:
    self._call(self.screen.erase)

  def refresh(self) -> None:
    self._call(self.screen.refresh)
