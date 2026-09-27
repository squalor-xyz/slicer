"""Curses front end over the same operations the CLI calls.

The drawing loop is deliberately thin. Everything decidable — what a row
says, what the detail pane contains, what a key means — lives in pure
functions above it, so the behaviour is testable without a terminal.

Editing follows the same rule: `act` never launches an editor. It returns an
`EditRequest`, and the loop is the only thing that suspends curses and runs
`$EDITOR`. That keeps every decision in a function a test can call.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from slicer import graph, ops, prose, render
from slicer.errors import SlicerError
from slicer.store import State


@dataclass(frozen=True)
class Binding:
  keys: tuple[str, ...]
  label: str
  action: str
  description: str


# Both dispatch and the help overlay use this table.
BINDINGS = (
  Binding(("j", "KEY_DOWN"), "j / Down", "down", "Move down"),
  Binding(("k", "KEY_UP"), "k / Up", "up", "Move up"),
  Binding(("\t",), "Tab", "pane", "Switch queue/detail pane"),
  Binding(("/",), "/", "search", "Search item IDs and titles; Enter accepts, Esc cancels"),
  Binding(("f",), "f", "filter", "Filter items; Space toggles, Enter applies, Esc cancels"),
  Binding(("c",), "c", "clear", "Clear search and all filters (show done too)"),
  Binding(("g",), "g", "jump", "Jump to ID; reveal hidden items by clearing restrictions"),
  Binding(("?",), "?", "help", "Show help; j/k scroll, ? or Esc closes"),
  Binding(("e",), "e", "edit", "Edit selected detail field/section or prose"),
  Binding(("a",), "a", "add", "Add an item"),
  Binding(("J",), "J", "reorder_down", "Reorder down (clear restrictions first)"),
  Binding(("K",), "K", "reorder_up", "Reorder up (clear restrictions first)"),
  Binding(("s",), "s", "start", "Start item"),
  Binding(("d",), "d", "done", "Mark item done"),
  Binding(("p",), "p", "park", "Park item"),
  Binding(("u",), "u", "unpark", "Reopen item"),
  Binding(("n",), "n", "promote", "Promote item to a slice"),
  Binding(("r",), "r", "render", "Render roadmap"),
  Binding(("q", "\x1b"), "q / Esc", "quit", "Quit"),
)
HELP = "  ".join(f"{b.label} {b.action}" for b in BINDINGS)


def key_action(key: str) -> str | None:
  return next((b.action for b in BINDINGS if key in b.keys), None)


def help_lines() -> list[str]:
  return [f"{b.label:<12} {b.description}" for b in BINDINGS]

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
    active = item.status == cfg.open_status or (
      bool(cfg.started_status) and item.status == cfg.started_status
    )
    marker = "!" if pending and active else " "
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
  boundary and sections. Fields are editable even before an item is promoted."""
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
    out.append(Entry(kind="boundary", target=target, name="boundary", body=sl.boundary))
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
  out.append(PanelLine("Scope boundary", base))
  out.extend(PanelLine(line, base) for line in sl.boundary.split("\n"))
  out.append(PanelLine("", base))
  base += 1
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
  action = key_action(key)

  if action == "edit":
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

  if action == "add":
    return ActResult(
      "type a title, save to add (empty cancels)",
      EditRequest(kind="new", target="", name="new item", body=""),
    )

  if action == "render":
    expected = render.plan(state)
    diff = render.compare(expected, state.render_dir)
    written = render.write(expected, state.render_dir, diff)
    return ActResult(f"rendered {len(written)} file(s)")

  if is_prose:
    return ActResult("that key applies to a slice, not to a prose block")

  try:
    if action == "start":
      ops.start(state, target)
      return ActResult(f"{target} started")
    if action == "done":
      ops.set_status(state, target, cfg.done_status)
      return ActResult(f"{target} done")
    if action == "park":
      ops.park(state, target)
      return ActResult(f"{target} parked")
    if action == "unpark":
      ops.set_status(state, target, cfg.open_status)
      return ActResult(f"{target} reopened")
    if action == "reorder_down":
      at = state.index.position(target)
      if at + 1 < len(state.index.items):
        ops.move(state, target, after=state.index.items[at + 1].id)
        return ActResult(f"{target} moved down")
      return ActResult("already last")
    if action == "reorder_up":
      at = state.index.position(target)
      if at > 0:
        ops.move(state, target, before=state.index.items[at - 1].id)
        return ActResult(f"{target} moved up")
      return ActResult("already first")
    if action == "promote":
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
    elif request.kind == "boundary":
      ops.edit_boundary(state, request.target, body)
    elif request.kind == "field":
      kwarg, parse = FIELD_SPEC[request.name]
      ops.set_fields(state, request.target, **{kwarg: parse(body)})
    else:
      ops.edit_section(state, request.target, request.name, body)
  except SlicerError as exc:
    return str(exc)
  return f"updated {request.name}; press r to render"


FILTER_GROUPS = ("status", "tree", "pass", "importance", "urgency")


@dataclass
class Filters:
  query: str = ""
  values: dict[str, set[str]] = field(
    default_factory=lambda: {group: set() for group in FILTER_GROUPS}
  )

  @classmethod
  def initial(cls, state: State) -> Filters:
    filters = cls()
    filters.values["status"] = set(state.config.statuses) - {state.config.done_status}
    return filters

  def copy(self) -> Filters:
    return Filters(self.query, {k: set(v) for k, v in self.values.items()})

  @property
  def active(self) -> bool:
    return bool(self.query or any(self.values.values()))

  def matches(self, item) -> bool:
    needle = self.query.casefold()
    if needle and not any(needle in text.casefold() for text in (
      item.id, item.title, item.short_title
    )):
      return False
    candidates = {
      "status": {item.status}, "tree": set(item.trees) or {""},
      "pass": {item.pass_key}, "importance": {str(item.importance)},
      "urgency": {str(item.urgency)},
    }
    return all(not selected or selected & candidates[group]
               for group, selected in self.values.items())

  def summary(self) -> str:
    parts = [f"search={self.query!r}"] if self.query else []
    for group, selected in self.values.items():
      if selected:
        parts.append(f"{group}=" + ",".join(v or "(none)" for v in sorted(selected)))
    return "  ".join(parts) or "all items"


def filtered_rows(state: State, filters: Filters) -> list[Row]:
  matching = {it.id for it in state.index.items if filters.matches(it)}
  return [row for row in rows(state) if row.kind != ITEM or row.target in matching]


def filter_choices(state: State) -> list[tuple[str, str | None]]:
  """None is Any; the empty string is an item with no tree/pass."""
  items = state.index.items
  values = {
    "status": list(state.config.statuses),
    "tree": [""] + sorted({tree for it in items for tree in it.trees}),
    "pass": [""] + sorted({it.pass_key for it in items if it.pass_key}),
    "importance": ["1", "2", "3"], "urgency": ["1", "2", "3"],
  }
  return [(group, value) for group in FILTER_GROUPS for value in [None, *values[group]]]


def row_identity(row: Row) -> tuple[str, str]:
  return row.kind, row.target


def reconcile_selection(
  old: list[Row], new: list[Row], selected: tuple[str, str] | None
) -> tuple[str, str] | None:
  available = [row_identity(row) for row in new if row.selectable]
  if selected in available:
    return selected
  previous = [row_identity(row) for row in old if row.selectable]
  if selected in previous:
    at = previous.index(selected)
    for candidate in previous[at + 1:] + list(reversed(previous[:at])):
      if candidate in available:
        return candidate
  return available[0] if available else None


@dataclass
class View:
  """Session-only interaction state; no terminal calls or persisted preferences."""

  filters: Filters
  listing: list[Row] = field(default_factory=list)
  selected: tuple[str, str] | None = None
  entry_at: int = 0
  focus: str = "left"
  scroll: int = 0
  mode: str = "normal"
  message: str = "? help  / search  f filters  c show all"
  text: str = ""
  saved_query: str = ""
  saved_selection: tuple[str, str] | None = None
  draft: Filters | None = None
  choice_at: int = 0
  help_at: int = 0
  quit: bool = False

  @classmethod
  def initial(cls, state: State) -> View:
    view = cls(Filters.initial(state))
    view.refresh(state)
    return view

  @property
  def target(self) -> str:
    return self.selected[1] if self.selected else ""

  def refresh(self, state: State) -> None:
    listing = filtered_rows(state, self.filters)
    selected = reconcile_selection(self.listing, listing, self.selected)
    if selected != self.selected:
      self.entry_at = 0
    self.selected, self.listing = selected, listing
    available = entries(state, self.target) if self.selected else []
    self.entry_at = min(self.entry_at, max(0, len(available) - 1))
    if not available:
      self.focus = "left"

  def select(self, identity: tuple[str, str] | None) -> None:
    if identity != self.selected:
      self.entry_at = 0
    self.selected = identity

  def status(self, state: State) -> str:
    count = sum(row.kind == ITEM for row in self.listing)
    return f"{count}/{len(state.index.items)} items  {self.filters.summary()}"

  def handle(self, state: State, key: str) -> ActResult:
    self.refresh(state)
    if key == "KEY_RESIZE":
      return ActResult()
    if self.mode in ("search", "jump"):
      return self._prompt(state, key)
    if self.mode == "filter":
      return self._filter(state, key)
    if self.mode == "help":
      if key in ("?", "\x1b"):
        self.mode = "normal"
      elif key in ("j", "KEY_DOWN"):
        self.help_at = min(self.help_at + 1, len(help_lines()) - 1)
      elif key in ("k", "KEY_UP"):
        self.help_at = max(0, self.help_at - 1)
      return ActResult()

    action = key_action(key)
    if action == "quit":
      self.quit = True
    elif action == "help":
      self.mode, self.help_at = "help", 0
    elif action in ("search", "jump"):
      self.mode = action
      self.saved_query, self.saved_selection = self.filters.query, self.selected
      self.text = self.filters.query if action == "search" else ""
    elif action == "filter":
      self.mode, self.draft, self.choice_at = "filter", self.filters.copy(), 0
    elif action == "clear":
      self.filters = Filters()
      self.message = "search and filters cleared; showing all items"
    elif action == "pane":
      self.focus = "right" if self.focus == "left" and entries(state, self.target) else "left"
    elif action in ("up", "down"):
      delta = 1 if action == "down" else -1
      if self.focus == "right":
        available = entries(state, self.target)
        self.entry_at = max(0, min(self.entry_at + delta, len(available) - 1))
      else:
        targets = [row_identity(row) for row in self.listing if row.selectable]
        if self.selected in targets:
          at = max(0, min(targets.index(self.selected) + delta, len(targets) - 1))
          self.select(targets[at])
    elif action in ("reorder_up", "reorder_down") and self.filters.active:
      self.message = "reordering disabled while filtered; press c to clear search and filters"
    elif action:
      if self.selected or action in ("add", "render"):
        result = act(state, key, self.target, entry=self.entry_at, focus=self.focus)
        self.message = result.message or self.message
        self.refresh(state)
        return result
      self.message = "no item selected"
    self.refresh(state)
    return ActResult()

  def _prompt(self, state: State, key: str) -> ActResult:
    if key == "\x1b":
      if self.mode == "search":
        self.filters.query = self.saved_query
        self.select(self.saved_selection)
      self.mode = "normal"
      self.message = "cancelled"
    elif key in ("\n", "\r", "KEY_ENTER"):
      if self.mode == "jump" and self.text.strip():
        item = next((it for it in state.index.items
                     if it.id.casefold() == self.text.strip().casefold()), None)
        if item is None:
          self.message = f"unknown item ID: {self.text.strip()}"
        else:
          hidden = not self.filters.matches(item)
          if hidden:
            self.filters = Filters()
          self.select((ITEM, item.id))
          self.focus = "left"
          self.message = f"selected {item.id}" + (
            "; search and filters cleared to reveal item" if hidden else ""
          )
      else:
        self.message = "search applied" if self.mode == "search" else "cancelled"
      self.mode = "normal"
    elif key in ("KEY_BACKSPACE", "\x7f", "\b"):
      self.text = self.text[:-1]
    elif len(key) == 1 and key.isprintable():
      self.text += key
    if self.mode == "search":
      self.filters.query = self.text
    self.refresh(state)
    return ActResult()

  def _filter(self, state: State, key: str) -> ActResult:
    choices = filter_choices(state)
    if key == "\x1b":
      self.mode, self.draft, self.message = "normal", None, "filters unchanged"
    elif key in ("\n", "\r", "KEY_ENTER"):
      if self.draft is not None:
        self.filters = self.draft
      self.mode, self.draft, self.message = "normal", None, "filters applied"
    elif key in ("j", "KEY_DOWN"):
      self.choice_at = min(self.choice_at + 1, len(choices) - 1)
    elif key in ("k", "KEY_UP"):
      self.choice_at = max(0, self.choice_at - 1)
    elif key == " " and self.draft is not None:
      group, value = choices[self.choice_at]
      selected = self.draft.values[group]
      if value is None:
        selected.clear()
      elif value in selected:
        selected.remove(value)
      else:
        selected.add(value)
    self.refresh(state)
    return ActResult()


def draw(screen, state: State, view: View) -> None:
  """Clip every write; a resize between size lookup and drawing is harmless."""
  import curses

  view.refresh(state)
  height, width = screen.getmaxyx()
  screen.erase()

  def put(y: int, x: int, text: str, attr: int = 0, limit: int | None = None) -> None:
    room = width - x - 1
    if limit is not None:
      room = min(room, limit)
    if 0 <= y < height and 0 <= x < width and room > 0:
      try:
        screen.addnstr(y, x, text, room, attr)
      except curses.error:
        pass

  visible = max(0, height - 2)
  if view.mode in ("filter", "help"):
    title = ("Filters: j/k move, Space toggle, Enter apply, Esc cancel"
             if view.mode == "filter" else "Help: j/k scroll, ? or Esc close")
    put(0, 0, title, curses.A_BOLD)
    if view.mode == "filter":
      choices = filter_choices(state)
      lines = []
      for group, value in choices:
        selected = view.draft.values[group] if view.draft else set()
        checked = not selected if value is None else value in selected
        label = "Any" if value is None else value or "(none)"
        lines.append(f"[{'x' if checked else ' '}] {group:<11} {label}")
      selected_at = view.choice_at
      top = max(0, selected_at - max(0, visible - 2))
    else:
      lines, selected_at, top = help_lines(), -1, view.help_at
    for n, line in enumerate(lines[top:top + max(0, visible - 1)], 1):
      attr = curses.A_REVERSE if top + n - 1 == selected_at else 0
      put(n, 0, line, attr)
  else:
    left_width = max(1, width // 2 - 2)
    matching = any(row.kind == ITEM for row in view.listing)
    offset = 0 if matching else 1
    if not matching:
      put(0, 0, "No matching items", limit=left_width)
    capacity = max(0, visible - offset)
    selected_at = next((i for i, row in enumerate(view.listing)
                        if row_identity(row) == view.selected), 0)
    if selected_at < view.scroll:
      view.scroll = selected_at
    elif selected_at >= view.scroll + capacity:
      view.scroll = max(0, selected_at - capacity + 1)
    view.scroll = min(view.scroll, max(0, len(view.listing) - capacity))
    for n, row in enumerate(view.listing[view.scroll:view.scroll + capacity], offset):
      attr = curses.A_DIM if row.kind == SEPARATOR else 0
      if row_identity(row) == view.selected:
        attr = curses.A_REVERSE if view.focus == "left" else curses.A_BOLD
      put(n, 0, row.text, attr, left_width)
    lines = panel(state, view.target) if view.selected else []
    first = next((n for n, line in enumerate(lines) if line.entry == view.entry_at), 0)
    top = max(0, first - 2) if view.focus == "right" else 0
    for n, line in enumerate(lines[top:top + visible]):
      attr = curses.A_REVERSE if (
        line.entry is not None and line.entry == view.entry_at and view.focus == "right"
      ) else 0
      put(n, left_width + 2, line.text, attr)
  put(height - 2, 0, view.status(state), curses.A_DIM)
  if view.mode in ("search", "jump"):
    label = "Search" if view.mode == "search" else "Jump to ID"
    prompt = f"{label} (Enter accepts, Esc cancels): {view.text}"
    put(height - 1, 0, prompt[-max(1, width - 1):], curses.A_REVERSE)
  else:
    put(height - 1, 0, view.message, curses.A_DIM)
  screen.refresh()


def run(state: State) -> int:  # pragma: no cover - requires a terminal
  import curses

  from slicer.cli import _via_editor

  def loop(screen: "curses._CursesWindow") -> int:
    try:
      curses.curs_set(0)
    except curses.error:
      pass
    view = View.initial(state)
    while not view.quit:
      draw(screen, state, view)
      try:
        key = screen.getkey()
      except curses.error:
        continue
      result = view.handle(state, key)
      if result.edit is not None:
        curses.def_prog_mode()
        curses.endwin()
        body = _via_editor(result.edit.body)
        curses.reset_prog_mode()
        screen.redrawwin()
        view.message = (
          "editor exited non-zero; nothing changed"
          if body is None else apply_edit(state, result.edit, body)
        )
    return 0

  return curses.wrapper(loop)
