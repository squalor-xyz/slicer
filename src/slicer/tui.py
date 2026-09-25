"""Curses front end over the same operations the CLI calls.

The drawing loop is deliberately thin. Everything decidable — what a row
says, what the detail pane contains, what a key means — lives in pure
functions above it, so the behaviour is testable without a terminal.

Editing follows the same rule: `act` never launches an editor. It returns an
`EditRequest`, and the loop is the only thing that suspends curses and runs
`$EDITOR`. That keeps every decision in a function a test can call.
"""

from __future__ import annotations

from dataclasses import dataclass

from slicer import graph, ops, prose, render
from slicer.errors import SlicerError
from slicer.store import State

# Every token here names a key that `act` or `run` actually handles. `n`
# promotes an existing item; `a` creates a new one.
HELP = (
  "j/k move  tab pane  e edit  a add  J/K reorder  d done  p park  u unpark  "
  "n promote  r render  q quit"
)

# Item fields editable from the detail pane, in display order: label -> the
# set_fields kwarg and how to parse the edited text back.
def _csv(text: str) -> list[str]:
  return [p.strip() for p in text.split(",") if p.strip()]


FIELD_SPEC: dict[str, tuple[str, object]] = {
  "size": ("size", lambda b: b.strip()),
  "trees": ("trees", _csv),
  "findings": ("findings", lambda b: b.strip()),
  "depends": ("depends_on", _csv),
  "importance": ("importance", lambda b: b.strip()),
  "urgency": ("urgency", lambda b: b.strip()),
}


def _field_current(item, label: str) -> str:
  if label == "trees":
    return ", ".join(item.trees)
  if label == "depends":
    return ", ".join(item.depends_on)
  if label in ("importance", "urgency"):
    return str(getattr(item, label))
  return getattr(item, label) or ""

ITEM, PROSE, SEPARATOR = "item", "prose", "separator"


@dataclass
class Row:
  kind: str
  target: str
  text: str
  status: str = ""
  blocked: bool = False

  @property
  def selectable(self) -> bool:
    return self.kind != SEPARATOR

  # Kept so existing callers and tests can read `row.item_id`.
  @property
  def item_id(self) -> str:
    return self.target if self.kind == ITEM else ""


@dataclass
class Entry:
  """A selectable, editable thing in the detail pane."""

  kind: str
  target: str
  name: str
  body: str


@dataclass
class PanelLine:
  text: str
  entry: int | None = None


@dataclass
class EditRequest:
  kind: str
  target: str
  name: str
  body: str


@dataclass
class ActResult:
  message: str = ""
  edit: EditRequest | None = None


def rows(state: State) -> list[Row]:
  """The queue, then the roadmap's own prose blocks."""
  cfg = state.config
  out: list[Row] = []
  for n, item in enumerate(state.index.items, 1):
    pending = graph.blocked_by(state.index, item, cfg.done_status)
    marker = "!" if pending and item.status == cfg.open_status else " "
    label = cfg.status_label(item.status)
    out.append(
      Row(
        kind=ITEM,
        target=item.id,
        text=f"{n:>3}{marker} {item.id:<5} {label:<7} {item.size:<2} {item.display_title()}",
        status=item.status,
        blocked=bool(pending),
      )
    )
  refs = prose.refs(state.index)
  if refs:
    out.append(Row(kind=SEPARATOR, target="", text="─ roadmap prose " + "─" * 20))
    for ref in refs:
      lines, preview = prose.summary(state.index, ref, width=30)
      out.append(Row(kind=PROSE, target=ref, text=f"     {ref:<22} {lines:>3}  {preview}"))
  return out


def row_for(state: State, target: str) -> Row | None:
  for row in rows(state):
    if row.selectable and row.target == target:
      return row
  return None


def entries(state: State, target: str) -> list[Entry]:
  """What the detail pane offers for editing: the item's fields, then its
  sections. Fields are editable even before an item is promoted."""
  if target in prose.refs(state.index):
    return [Entry(kind=PROSE, target=target, name=target, body=prose.get(state.index, target))]
  item = state.index.get(target)
  if item is None:
    return []
  out = [
    Entry(kind="field", target=target, name=label, body=_field_current(item, label))
    for label in FIELD_SPEC
  ]
  sl = state.slices.get(target)
  if sl is not None:
    out += [Entry(kind="section", target=target, name=s.heading, body=s.body) for s in sl.sections]
  return out


def panel(state: State, target: str) -> list[PanelLine]:
  """The detail pane: plain context lines, plus lines tagged with their entry."""
  if target in prose.refs(state.index):
    out = [PanelLine(target), PanelLine("")]
    for line in prose.get(state.index, target).split("\n"):
      out.append(PanelLine(line, 0))
    return out

  item = state.index.get(target)
  if item is None:
    return [PanelLine("(no such item)")]
  out = [
    PanelLine(f"{item.id}  {item.title}"),
    PanelLine(""),
    PanelLine(f"status     {state.config.status_label(item.status)}"),
  ]
  for n, label in enumerate(FIELD_SPEC):
    out.append(PanelLine(f"{label:<11}{_field_current(item, label) or '-'}", n))
  out.append(PanelLine(f"score      {item.score} ({item.quadrant})"))
  out.append(PanelLine(""))
  sl = state.slices.get(target)
  if sl is None:
    out.append(PanelLine("(no slice yet - press n to promote)"))
    return out
  base = len(FIELD_SPEC)
  for i, section in enumerate(sl.sections):
    out.append(PanelLine(f"## {section.heading}", base + i))
    for line in section.body.split("\n"):
      out.append(PanelLine(line, base + i))
    out.append(PanelLine("", base + i))
  return out


def act(
  state: State, key: str, target: str, *, entry: int | None = None, focus: str = "left"
) -> ActResult:
  """Apply a keystroke. Every branch calls the same `ops` the CLI does."""
  cfg = state.config
  is_prose = target in prose.refs(state.index)

  if key == "e":
    available = entries(state, target)
    if not available:
      return ActResult("nothing to edit here")
    if is_prose:
      chosen = available[0]
    elif focus == "right" and entry is not None and 0 <= entry < len(available):
      chosen = available[entry]
    else:
      return ActResult("select a field or section with tab, then press e")
    return ActResult(
      f"editing {chosen.name}",
      EditRequest(kind=chosen.kind, target=chosen.target, name=chosen.name, body=chosen.body),
    )

  if key == "a":
    return ActResult(
      "type a title, save to add (empty cancels)",
      EditRequest(kind="new", target="", name="new item", body=""),
    )

  if key == "r":
    expected = render.plan(state)
    diff = render.compare(expected, state.render_dir)
    written = render.write(expected, state.render_dir, diff)
    return ActResult(f"rendered {len(written)} file(s)")

  if is_prose:
    return ActResult("that key applies to a slice, not to a prose block")

  try:
    if key == "d":
      ops.set_status(state, target, cfg.done_status)
      return ActResult(f"{target} done")
    if key == "p":
      ops.park(state, target)
      return ActResult(f"{target} parked")
    if key == "u":
      ops.set_status(state, target, cfg.open_status)
      return ActResult(f"{target} reopened")
    if key == "J":
      at = state.index.position(target)
      if at + 1 < len(state.index.items):
        ops.move(state, target, after=state.index.items[at + 1].id)
        return ActResult(f"{target} moved down")
      return ActResult("already last")
    if key == "K":
      at = state.index.position(target)
      if at > 0:
        ops.move(state, target, before=state.index.items[at - 1].id)
        return ActResult(f"{target} moved up")
      return ActResult("already first")
    if key == "n":
      ops.promote(state, target)
      return ActResult(f"{target} promoted")
  except SlicerError as exc:
    return ActResult(str(exc))
  return ActResult()


def apply_edit(state: State, request: EditRequest, body: str) -> str:
  """Write back what the editor produced, through the same `ops` the CLI uses."""
  if body == request.body:
    return f"{request.name} unchanged"
  try:
    if request.kind == "new":
      title = body.strip()
      if not title:
        return "cancelled"
      return f"added {ops.add(state, title).id}"
    if request.kind == PROSE:
      ops.edit_prose(state, request.target, body)
    elif request.kind == "field":
      kwarg, parse = FIELD_SPEC[request.name]
      ops.set_fields(state, request.target, **{kwarg: parse(body)})
    else:
      ops.edit_section(state, request.target, request.name, body)
  except SlicerError as exc:
    return str(exc)
  return f"updated {request.name}; press r to render"


def run(state: State) -> int:  # pragma: no cover - requires a terminal
  import curses

  from slicer.cli import _via_editor

  def loop(screen: "curses._CursesWindow") -> int:
    curses.curs_set(0)
    selected = 0
    entry_at = 0
    focus = "left"
    scroll = 0
    message = HELP

    while True:
      screen.erase()
      height, width = screen.getmaxyx()
      left_width = max(28, width // 2 - 2)
      listing = [r for r in rows(state)]
      if not listing:
        screen.addstr(0, 0, "no items - run `slicer add` or `slicer import` first")
        screen.getch()
        return 0

      selected = max(0, min(selected, len(listing) - 1))
      while listing[selected].kind == SEPARATOR and selected + 1 < len(listing):
        selected += 1
      row = listing[selected]
      available = entries(state, row.target)
      entry_at = max(0, min(entry_at, max(0, len(available) - 1)))

      visible = height - 2
      if selected < scroll:
        scroll = selected
      elif selected >= scroll + visible:
        scroll = selected - visible + 1

      for i, item_row in enumerate(listing[scroll : scroll + visible]):
        at = scroll + i
        attr = curses.A_NORMAL
        if at == selected:
          attr = curses.A_REVERSE if focus == "left" else curses.A_BOLD
        elif item_row.kind == SEPARATOR:
          attr = curses.A_DIM
        screen.addnstr(i, 0, item_row.text.ljust(left_width)[:left_width], left_width, attr)

      lines = panel(state, row.target)
      first = next((n for n, l in enumerate(lines) if l.entry == entry_at), 0)
      top = max(0, first - 2) if focus == "right" else 0
      for i, line in enumerate(lines[top : top + visible]):
        attr = curses.A_NORMAL
        if line.entry is not None and line.entry == entry_at and focus == "right":
          attr = curses.A_REVERSE
        screen.addnstr(i, left_width + 2, line.text, max(1, width - left_width - 3), attr)

      screen.addnstr(height - 1, 0, message[: width - 1], width - 1, curses.A_DIM)
      screen.refresh()

      try:
        key = screen.getkey()
      except curses.error:
        continue

      if key in ("q", "\x1b"):
        return 0
      if key == "\t":
        focus = "right" if focus == "left" and available else "left"
        message = HELP
        continue
      if key in ("j", "KEY_DOWN"):
        if focus == "right":
          entry_at = min(entry_at + 1, max(0, len(available) - 1))
        else:
          nxt = selected + 1
          while nxt < len(listing) and listing[nxt].kind == SEPARATOR:
            nxt += 1
          if nxt < len(listing):
            selected, entry_at = nxt, 0
        continue
      if key in ("k", "KEY_UP"):
        if focus == "right":
          entry_at = max(0, entry_at - 1)
        else:
          prev = selected - 1
          while prev >= 0 and listing[prev].kind == SEPARATOR:
            prev -= 1
          if prev >= 0:
            selected, entry_at = prev, 0
        continue

      result = act(state, key, row.target, entry=entry_at, focus=focus)
      if result.edit is not None:
        curses.def_prog_mode()
        curses.endwin()
        body = _via_editor(result.edit.body)
        curses.reset_prog_mode()
        screen.redrawwin()
        message = (
          "editor exited non-zero; nothing changed"
          if body is None
          else apply_edit(state, result.edit, body)
        )
      else:
        message = result.message or HELP

  return curses.wrapper(loop)
