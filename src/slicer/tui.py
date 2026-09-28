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

from slicer import graph, ops, prose, render, store, tui_style, tui_wizard
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
  Binding(("e",), "e", "edit", "Edit selected detail field/section/note or prose (empty removes a note)"),
  Binding(("a",), "a", "add", "Add an item"),
  Binding(("w",), "w", "wizard", "Create roadmap items with the guided wizard"),
  Binding(("J",), "J", "reorder_down", "Reorder down (clear restrictions first)"),
  Binding(("K",), "K", "reorder_up", "Reorder up (clear restrictions first)"),
  Binding(("T",), "T", "reorder_top", "Move to top (clear restrictions first)"),
  Binding(("M",), "M", "move_to", "Move to a position (clear restrictions first)"),
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


# Common actions in display order; key labels come from the dispatch table.
SHORTCUTS = (
  (("pane",), "panes"), (("edit",), "edit"), (("add",), "add"),
  (("start",), "start"), (("done",), "done"), (("search",), "search"),
  (("filter",), "filters"), (("clear",), "show all"), (("jump",), "jump"),
  (("down", "up"), "move"), (("help",), "help"), (("quit",), "quit"),
)


def shortcut_lines(width: int) -> list[str]:
  """Pack complete hints into terminal rows without splitting a key/action pair."""
  labels = {b.action: b.label.split(" / ")[0] for b in BINDINGS}
  lines: list[str] = []
  for actions, description in SHORTCUTS:
    hint = f"{'/'.join(labels[action] for action in actions)} {description}"
    if lines and tui_style.cell_width(lines[-1] + "  " + hint) < width:
      lines[-1] += "  " + hint
    else:
      lines.append(hint)
  return lines

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
  priority_at: int = 0
  high_priority: bool = False

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
  index: int | None = None  # which note, for kind == "note"


@dataclass
class PanelLine:
  text: str
  entry: int | None = None
  role: str = "normal"


@dataclass
class EditRequest:
  kind: str
  target: str
  name: str
  body: str
  index: int | None = None  # which note, for kind == "note"


@dataclass
class ActResult:
  message: str = ""
  edit: EditRequest | None = None
  severity: str = "info"


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
    prefix = f"{n:>3}{marker} {item.id:<5} {label:<7} {item.size:<2} "
    out.append(
      Row(
        kind=ITEM,
        target=item.id,
        text=f"{prefix}P:{item.score} {item.display_title()}",
        status=item.status,
        blocked=bool(pending),
        priority_at=len(prefix),
        high_priority=item.importance == 3 or item.urgency == 3,
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
  out += [Entry(kind="note", target=target, name=f"note {i + 1}", body=n, index=i)
          for i, n in enumerate(item.notes)]
  out.append(Entry(kind="note_new", target=target, name="add a note", body=""))
  return out


def panel(state: State, target: str) -> list[PanelLine]:
  """The detail pane: plain context lines, plus lines tagged with their entry."""
  if target in prose.refs(state.index):
    out = [PanelLine(target, role="heading"), PanelLine("")]
    for line in prose.get(state.index, target).split("\n"):
      out.append(PanelLine(line, 0))
    return out

  item = state.index.get(target)
  if item is None:
    return [PanelLine("(no such item)")]
  out = [
    PanelLine(f"{item.id}  {item.title}", role="heading"),
    PanelLine(""),
    PanelLine(f"status     {state.config.status_label(item.status)}",
              role=tui_style.status_role(item.status, bool(graph.blocked_by(
                state.index, item, state.config.done_status)), state.config)),
  ]
  for n, label in enumerate(FIELD_SPEC):
    role = "priority" if label in ("importance", "urgency") and getattr(item, label) == 3 else "field"
    out.append(PanelLine(f"{label:<11}{_field_current(item, label) or '-'}", n, role))
  out.append(PanelLine(f"score      {item.score} ({item.quadrant})", role=(
    "priority" if item.importance == 3 or item.urgency == 3 else "normal")))
  out.append(PanelLine(""))
  sl = state.slices.get(target)
  if sl is None:
    out.append(PanelLine("(no slice yet - press n to promote)"))
    note_base = len(FIELD_SPEC)
  else:
    base = len(FIELD_SPEC)
    out.append(PanelLine("Scope boundary", base, "heading"))
    out.extend(PanelLine(line, base) for line in sl.boundary.split("\n"))
    out.append(PanelLine("", base))
    base += 1
    for i, section in enumerate(sl.sections):
      out.append(PanelLine(f"## {section.heading}", base + i, "heading"))
      for line in section.body.split("\n"):
        out.append(PanelLine(line, base + i))
      out.append(PanelLine("", base + i))
    note_base = base + len(sl.sections)
  # Notes live on any item: `e` edits one (empty removes it), the last line adds one.
  out.append(PanelLine(""))
  out.append(PanelLine("Notes", role="heading"))
  for i, note in enumerate(item.notes):
    idx = note_base + i
    for line in note.split("\n"):
      out.append(PanelLine(line, idx))
    out.append(PanelLine("", idx))
  out.append(PanelLine("+ add a note", note_base + len(item.notes)))
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
      EditRequest(kind=chosen.kind, target=chosen.target, name=chosen.name,
                  body=chosen.body, index=chosen.index),
    )

  if action == "add":
    return ActResult(
      "type a title, save to add (empty cancels)",
      EditRequest(kind="new", target="", name="new item", body=""),
    )

  if action == "render":
    try:
      expected = render.plan(state)
      diff = render.compare(expected, state.render_dir)
      written = render.write(expected, state.render_dir, diff)
      return ActResult(f"rendered {len(written)} file(s)",
                       severity="success" if written else "info")
    except (SlicerError, OSError) as exc:
      return ActResult(str(exc), severity="error")

  if is_prose:
    return ActResult("that key applies to a slice, not to a prose block")

  try:
    if action in ("start", "done", "park", "unpark"):
      item = state.index.require(target)
      previous = item.status
      if action == "start":
        ops.start(state, target)
        message = f"{target} started"
      elif action == "done":
        ops.set_status(state, target, cfg.done_status)
        message = f"{target} done"
      elif action == "park":
        ops.park(state, target)
        message = f"{target} parked"
      else:
        ops.set_status(state, target, cfg.open_status)
        message = f"{target} reopened"
      return ActResult(message, severity="success" if item.status != previous else "info")
    if action == "reorder_down":
      at = state.index.position(target)
      if at + 1 < len(state.index.items):
        ops.move(state, target, after=state.index.items[at + 1].id)
        return ActResult(f"{target} moved down", severity="success")
      return ActResult("already last")
    if action == "reorder_up":
      at = state.index.position(target)
      if at > 0:
        ops.move(state, target, before=state.index.items[at - 1].id)
        return ActResult(f"{target} moved up", severity="success")
      return ActResult("already first")
    if action == "reorder_top":
      if state.index.position(target) > 0:
        ops.move(state, target, to=1)
        return ActResult(f"{target} moved to top", severity="success")
      return ActResult("already first")
    if action == "promote":
      ops.promote(state, target)
      return ActResult(f"{target} promoted", severity="success")
  except (SlicerError, OSError) as exc:
    return ActResult(str(exc), severity="error")
  return ActResult()


def apply_edit_result(state: State, request: EditRequest, body: str | None) -> ActResult:
  """Carry editor outcomes explicitly instead of classifying message wording."""
  if body is None:
    return ActResult("editor exited non-zero; nothing changed", severity="error")
  if body == request.body:
    return ActResult(f"{request.name} unchanged")
  try:
    if request.kind == "new":
      title = body.strip()
      if not title:
        return ActResult("cancelled")
      return ActResult(f"added {ops.add(state, title).id}", severity="success")
    if request.kind == PROSE:
      ops.edit_prose(state, request.target, body)
    elif request.kind == "boundary":
      ops.edit_boundary(state, request.target, body)
    elif request.kind == "field":
      kwarg, parse = FIELD_SPEC[request.name]
      item = state.index.require(request.target)
      before = item.to_dict()
      ops.set_fields(state, request.target, **{kwarg: parse(body)})
      if item.to_dict() == before:
        return ActResult(f"{request.name} unchanged")
    elif request.kind == "note":
      if body.strip():
        ops.set_note(state, request.target, request.index, body)
        return ActResult("updated note; press r to render", severity="success")
      ops.remove_note(state, request.target, request.index)
      return ActResult("note removed; press r to render", severity="success")
    elif request.kind == "note_new":
      if not body.strip():
        return ActResult("cancelled")
      ops.add_note(state, request.target, body)
      return ActResult("added note; press r to render", severity="success")
    else:
      ops.edit_section(state, request.target, request.name, body)
  except (SlicerError, OSError) as exc:
    return ActResult(str(exc), severity="error")
  return ActResult(f"updated {request.name}; press r to render", severity="success")


def apply_edit(state: State, request: EditRequest, body: str) -> str:
  """Preserve the string interface for callers that do not draw feedback."""
  return apply_edit_result(state, request, body).message


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
  message: str = ""
  message_severity: str = "normal"
  text: str = ""
  saved_query: str = ""
  saved_selection: tuple[str, str] | None = None
  draft: Filters | None = None
  choice_at: int = 0
  help_at: int = 0
  quit: bool = False
  wizard: tui_wizard.Wizard | None = None

  @classmethod
  def initial(cls, state: State, *, offer_wizard: bool = False) -> View:
    view = cls(Filters.initial(state))
    view.refresh(state)
    if offer_wizard and not state.index.items:
      view.mode = "wizard_offer"
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

  def notify(self, message: str, severity: str = "info") -> None:
    self.message, self.message_severity = message, severity

  def accept(self, result: ActResult) -> None:
    if result.message:
      self.notify(result.message, result.severity)

  def feedback(self) -> str:
    prefix = {"info": "Info: ", "success": "OK: ", "error": "Error: "}.get(self.message_severity, "")
    return prefix + self.message

  def status(self, state: State) -> str:
    count = sum(row.kind == ITEM for row in self.listing)
    return f"{count}/{len(state.index.items)} items  {self.filters.summary()}"

  def handle(self, state: State, key: str) -> ActResult:
    self.refresh(state)
    if key == "KEY_RESIZE":
      return ActResult()
    if self.mode == "wizard_offer":
      if key in ("w", "y", *tui_wizard.ENTER):
        self._open_wizard(state)
      elif key in ("n", "b", "\x1b"):
        self.mode = "normal"
      return ActResult()
    if self.wizard is not None:
      intent = self.wizard.handle(key)
      if intent == "cancel":
        self.wizard, self.mode = None, "normal"
        self.notify("wizard cancelled; nothing saved")
      elif intent == "editor":
        section = self.wizard.section
        return ActResult(edit=EditRequest("wizard", "", section.heading, section.body))
      elif intent == "save":
        self._save_wizard(state)
      return ActResult()
    if self.mode in ("search", "jump"):
      return self._prompt(state, key)
    if self.mode == "move":
      return self._move(state, key)
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
    elif action == "wizard":
      self._open_wizard(state)
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
      self.notify("search and filters cleared; showing all items")
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
    elif action in ("reorder_up", "reorder_down", "reorder_top", "move_to") and self.filters.active:
      self.notify("reordering disabled while filtered; press c to clear search and filters")
    elif action == "move_to":
      if self.selected:
        self.mode, self.text = "move", ""
      else:
        self.notify("no item selected")
    elif action:
      if self.selected or action in ("add", "render"):
        result = act(state, key, self.target, entry=self.entry_at, focus=self.focus)
        self.accept(result)
        self.refresh(state)
        return result
      self.notify("no item selected")
    self.refresh(state)
    return ActResult()

  def _open_wizard(self, state: State) -> None:
    self.wizard = tui_wizard.Wizard(list(state.config.sections))
    self.mode = "wizard"

  def _save_wizard(self, state: State) -> None:
    wizard = self.wizard
    if wizard is None:
      return
    try:
      specs = wizard.specs()
      with store.project_lock(state.root):
        fresh = store.load(state.root)
        warning = ""
        try:
          report = ops.apply_outline(
            fresh, specs, promote_all=True,
            preamble=wizard.preamble(fresh.index.preamble),
            # The wizard composes the new preamble from the stored one.
            replace_preamble=True,
          )
        except ops.OutlineCommittedError as exc:
          report, warning = exc.report, str(exc)
        if report.problems:
          wizard.error = "; ".join(report.problems)
          return
        # The index is committed. Close the draft before rendering so a later
        # failure cannot invite the user to add the same items a second time.
        state.config, state.index = fresh.config, fresh.index
        state.slices, state.slice_files = fresh.slices, fresh.slice_files
        self.wizard, self.mode = None, "normal"
        self.filters, self.focus = Filters(), "left"
        self.refresh(state)
        self.select((ITEM, report.ids[0]))
        outcome = act(state, "r", "")
        if outcome.severity == "error":
          warning = "; ".join(filter(None, (warning, f"render failed: {outcome.message}; press r to retry")))
        self.notify(f"saved {len(report.ids)} item(s) and slices" +
                    (f"; {warning}" if warning else "; rendered roadmap"),
                    "error" if warning else "success")
    except (SlicerError, OSError) as exc:
      wizard.error = str(exc)

  def _prompt(self, state: State, key: str) -> ActResult:
    if key == "\x1b":
      if self.mode == "search":
        self.filters.query = self.saved_query
        self.select(self.saved_selection)
      self.mode = "normal"
      self.notify("cancelled")
    elif key in ("\n", "\r", "KEY_ENTER"):
      if self.mode == "jump" and self.text.strip():
        item = next((it for it in state.index.items
                     if it.id.casefold() == self.text.strip().casefold()), None)
        if item is None:
          self.notify(f"unknown item ID: {self.text.strip()}", "error")
        else:
          hidden = not self.filters.matches(item)
          if hidden:
            self.filters = Filters()
          self.select((ITEM, item.id))
          self.focus = "left"
          self.notify(f"selected {item.id}" + (
            "; search and filters cleared to reveal item" if hidden else ""
          ))
      else:
        self.notify("search applied" if self.mode == "search" else "cancelled")
      self.mode = "normal"
    else:
      self.text, _ = tui_wizard.text_input(self.text, key)
    if self.mode == "search":
      self.filters.query = self.text
    self.refresh(state)
    return ActResult()

  def _move(self, state: State, key: str) -> ActResult:
    """Collect a target position, then reorder through the same ops.move the CLI
    uses. Only digits are accepted; out-of-range values surface ops.move's error."""
    if key == "\x1b":
      self.mode = "normal"
      self.notify("cancelled")
    elif key in ("\n", "\r", "KEY_ENTER"):
      self.mode = "normal"
      text = self.text.strip()
      if not text:
        self.notify("cancelled")
      elif not self.selected:
        self.notify("no item selected", "error")
      elif int(text) == state.index.position(self.target) + 1:
        self.notify(f"already at {text}")  # no-op: don't move, save or log
      else:
        try:
          landed = ops.move(state, self.target, to=int(text))
          self.notify(f"{self.target} moved to {landed}", "success")
        except (SlicerError, OSError) as exc:
          self.notify(str(exc), "error")
      self.refresh(state)
    elif key in ("KEY_BACKSPACE", "\x7f", "\b"):
      self.text = self.text[:-1]
    elif len(key) == 1 and key.isdigit():
      self.text += key
    return ActResult()

  def _filter(self, state: State, key: str) -> ActResult:
    choices = filter_choices(state)
    if key == "\x1b":
      self.mode, self.draft = "normal", None
      self.notify("filters unchanged")
    elif key in ("\n", "\r", "KEY_ENTER"):
      if self.draft is not None:
        self.filters = self.draft
      self.mode, self.draft = "normal", None
      self.notify("filters applied")
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


def draw(screen, state: State, view: View, palette: tui_style.Palette | None = None) -> None:
  """Render semantic cues through one bounded terminal drawing surface."""
  palette = palette or tui_style.monochrome()
  view.refresh(state)
  canvas = tui_style.Canvas(screen)
  height, width = canvas.height, canvas.width
  canvas.erase()
  put = canvas.put
  if width < tui_style.MIN_WIDTH or height < tui_style.MIN_HEIGHT:
    put(0, 0, f"Resize to at least {tui_style.MIN_WIDTH}x{tui_style.MIN_HEIGHT}", palette.attr("heading"))
    put(1, 0, "Session preserved; q quits from the normal view.")
    canvas.refresh()
    return

  if view.mode == "wizard_offer":
    put(0, 0, "Create a roadmap", palette.attr("heading"))
    put(2, 0, "This roadmap has no items. Start the guided wizard?")
    put(4, 0, "Enter / w / y: start wizard   b / n / Esc: browse", palette.attr("info"))
    canvas.refresh()
    return
  if view.wizard is not None:
    wizard = view.wizard
    put(0, 0, "Roadmap wizard" + (" · Review" if wizard.mode == "review" else ""),
        palette.attr("heading"))
    lines, selected_at = wizard.lines()
    capacity = height - 4
    top = max(0, selected_at - capacity + 1)
    for y, line in enumerate(lines[top:top + capacity], 1):
      selected = top + y - 1 == selected_at
      put(y, 0, ("> " if selected else "  ") + line,
          palette.attr(selected=selected, focused=True))
    if wizard.mode == "field" and wizard.section is None:
      value = wizard.text[-(width - 4):]
      while value and tui_style.cell_width(value) > width - 4:
        value = value[1:]
      put(height - 3, 0, "> " + value + "_", palette.attr(selected=True, focused=True))
    put(height - 2, 0, wizard.error, palette.attr("error"))
    hint = ("Up/Down: choose   Enter: edit or apply selection   Esc: cancel"
            if wizard.mode == "review" else "Enter: continue   Shift-Tab: back   Esc: cancel")
    put(height - 1, 0, hint, palette.attr("info"))
    canvas.refresh()
    return

  # Status and feedback retain their own rows above the normal-view shortcuts.
  shortcuts = shortcut_lines(width) if view.mode == "normal" else []
  status_y = height - 2 - len(shortcuts)
  visible = status_y - 1
  if view.mode in ("filter", "help"):
    title = ("Filters: j/k move, Space toggle, Enter apply, Esc cancel"
             if view.mode == "filter" else "Help: j/k scroll, ? or Esc close")
    put(0, 0, title, palette.attr("heading"))
    if view.mode == "filter":
      choices = filter_choices(state)
      lines = []
      for group, value in choices:
        selected = view.draft.values[group] if view.draft else set()
        checked = not selected if value is None else value in selected
        label = "Any" if value is None else value or "(none)"
        lines.append(f"[{'x' if checked else ' '}] {group:<11} {label}")
      selected_at = view.choice_at
      top = max(0, selected_at - visible + 1)
    else:
      lines, selected_at, top = help_lines(), -1, view.help_at
    for n, line in enumerate(lines[top:top + visible], 1):
      selected = top + n - 1 == selected_at
      put(n, 0, ("> " if selected else "  ") + line,
          palette.attr(selected=selected, focused=True))
  else:
    left_width = width // 2 - 2
    right_x = left_width + 2
    put(0, 0, ("> " if view.focus == "left" else "  ") + "Queue", palette.attr("heading"), left_width)
    put(0, right_x, ("> " if view.focus == "right" else "  ") + "Details", palette.attr("heading"))
    matching = any(row.kind == ITEM for row in view.listing)
    offset = 1 if matching else 2
    if not matching:
      put(1, 0, "No matching items", palette.attr("info"), left_width)
    capacity = visible - (offset - 1)
    selected_at = next((i for i, row in enumerate(view.listing)
                        if row_identity(row) == view.selected), 0)
    if selected_at < view.scroll:
      view.scroll = selected_at
    elif selected_at >= view.scroll + capacity:
      view.scroll = max(0, selected_at - capacity + 1)
    view.scroll = min(view.scroll, max(0, len(view.listing) - capacity))
    for n, row in enumerate(view.listing[view.scroll:view.scroll + capacity], offset):
      selected = row_identity(row) == view.selected
      role = "dim" if row.kind == SEPARATOR else tui_style.status_role(row.status, row.blocked, state.config)
      attr = palette.attr(role, selected=selected, focused=view.focus == "left")
      marker = "> " if selected else "  "
      put(n, 0, marker + row.text, attr, left_width)
      if row.kind == ITEM and row.high_priority:
        x = 2 + tui_style.cell_width(tui_style.clipped(row.text[:row.priority_at], left_width))
        put(n, x, row.text[row.priority_at:row.priority_at + 4],
            palette.attr("priority", selected=selected, focused=view.focus == "left"), left_width - x)
    lines = panel(state, view.target) if view.selected else []
    first = next((n for n, line in enumerate(lines) if line.entry == view.entry_at), 0)
    top = max(0, first - 2) if view.focus == "right" else 0
    for n, line in enumerate(lines[top:top + visible], 1):
      selected = line.entry is not None and line.entry == view.entry_at
      attr = palette.attr(line.role, selected=selected, focused=view.focus == "right")
      put(n, right_x, ("> " if selected else "  ") + line.text, attr)
  put(status_y, 0, view.status(state), palette.attr("dim"))
  if view.mode in ("search", "jump", "move"):
    label = {"search": "Search", "jump": "Jump to ID", "move": "Move to position"}[view.mode]
    prompt = f"{label} (Enter accepts, Esc cancels): {view.text}"
    put(status_y + 1, 0, prompt[-max(1, width - 1):], palette.attr(selected=True, focused=True))
  else:
    put(status_y + 1, 0, view.feedback(), palette.attr(view.message_severity))
  for y, line in enumerate(shortcuts, status_y + 2):
    put(y, 0, line, palette.attr("dim"))
  canvas.refresh()


def run(state: State) -> int:  # pragma: no cover - requires a terminal
  import curses

  from slicer.cli import _via_editor

  def loop(screen: "curses._CursesWindow") -> int:
    try:
      curses.curs_set(0)
    except curses.error:
      pass
    palette = tui_style.setup_palette()
    view = View.initial(state, offer_wizard=True)
    while not view.quit:
      draw(screen, state, view, palette)
      try:
        key = screen.get_wch()
        if isinstance(key, int):
          key = curses.keyname(key).decode("ascii")
      except curses.error:
        continue
      height, width = screen.getmaxyx()
      if view.wizard is not None and (width < tui_style.MIN_WIDTH or height < tui_style.MIN_HEIGHT):
        continue
      result = view.handle(state, key)
      if result.edit is not None:
        curses.def_prog_mode()
        curses.endwin()
        try:
          body = _via_editor(result.edit.body)
        except (SlicerError, OSError) as exc:
          if result.edit.kind == "wizard" and view.wizard is not None:
            view.wizard.error = f"Editor failed; body unchanged: {exc}"
          outcome = ActResult(str(exc), severity="error")
        else:
          if result.edit.kind == "wizard" and view.wizard is not None:
            view.wizard.editor_result(body)
            outcome = ActResult()
          else:
            outcome = apply_edit_result(state, result.edit, body)
        finally:
          curses.reset_prog_mode()
          screen.redrawwin()
        view.accept(outcome)
    return 0

  return curses.wrapper(loop)
