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
from datetime import datetime, timedelta

from slicer import graph, model, ops, outline, prose, relayrun, render, roles, store, tui_style, tui_wizard, vcs
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
  Binding(("v",), "v", "view", "Cycle the unfinished, in-work, and review queues"),
  Binding(("b",), "b", "board", "Relay board: live slice-relay runs; j/k or arrows scroll, b, q or Esc closes"),
  Binding(("o",), "o", "sort", "Sort the view; j/k choose a field, Space flips direction, Enter applies"),
  Binding(("c",), "c", "clear", "Clear search and all filters (show done and retired too)"),
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
  Binding(("h",), "h", "handoff", "Hand the item to review; the note is optional"),
  Binding(("x",), "x", "reject", "Send a review back to open; a note is required"),
  Binding(("l",), "l", "release", "Release the item's claim and keep its status"),
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
  (("start",), "start"), (("done",), "done"), (("handoff",), "handoff"), (("search",), "search"),
  (("filter",), "filters"), (("sort",), "sort"), (("clear",), "show all"), (("jump",), "jump"),
  (("down", "up"), "move"), (("help",), "help"), (("quit",), "quit"),
  (("view",), "view"), (("board",), "board"),
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


def _effort_edit(body: str) -> object:
  """Blank clears effort. Anything else is validated as 1, 2 or 3 on set."""
  text = body.strip()
  if not text:
    return None
  return text


FIELD_SPEC: dict[str, tuple[str, object]] = {
  "title": ("title", lambda b: b.strip()),
  "size": ("size", lambda b: b.strip()),
  "trees": ("trees", _csv),
  "findings": ("findings", lambda b: b.strip()),
  "depends": ("depends_on", _csv),
  "importance": ("importance", lambda b: b.strip()),
  "urgency": ("urgency", lambda b: b.strip()),
  "effort": ("effort", _effort_edit),
  "short title": ("short_title", lambda b: b.strip()),
  "pass": ("pass_key", lambda b: b.strip()),
  "flags": ("flags", _csv),
}


def _field_current(item, label: str) -> str:
  if label == "trees":
    return ", ".join(item.trees)
  if label == "depends":
    return ", ".join(item.depends_on)
  if label == "flags":
    return ", ".join(item.flags)
  if label in ("importance", "urgency"):
    return str(getattr(item, label))
  return getattr(item, FIELD_SPEC[label][0]) or ""

# One label column for every detail line, so values align whatever the label.
LABEL_WIDTH = 16


def _labelled(label: str, value: object) -> str:
  return f"{label:<{LABEL_WIDTH}}{value}"


ITEM, PROSE, SEPARATOR = "item", "prose", "separator"
# An item only in a sibling worktree: listed for awareness, never selected or edited.
SIBLING = "sibling"
# The queue shows at most this many cells of a claim; the detail pane is not capped.
CLAIM_CELL_CAP = 8
# Below this queue-pane width the claim and size columns go, so the title keeps room.
NARROW_PANE = 50


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
    return self.kind not in (SEPARATOR, SIBLING)

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


SORT_FIELDS = (
  ("ranked", "ranked order"),
  ("id", "ID"),
  ("title", "title"),
  ("status", "status"),
  ("size", "size"),
  ("importance", "importance"),
  ("urgency", "urgency"),
  ("score", "effective score"),
  ("effort", "effort"),
)


def sort_items(
  state: State, items: list, field: str, descending: bool,
  elsewhere: dict[str, list[dict[str, str]]] | None = None,
) -> list:
  """Order a visible subset. Blanks stay last. Ties keep stored queue order."""
  if field == "ranked":
    return graph.ranked_order(state.index, state.config, items, descending=descending)
  place = {it.id: n for n, it in enumerate(state.index.items)}
  eff = graph.effective_scores(state.index)

  def raw(item):
    if field == "id":
      return item.id
    if field == "title":
      return item.display_title()
    if field == "status":
      return state.config.status_label(store.displayed_status(
        state.config, item, (elsewhere or {}).get(item.id, []),
      ))
    if field == "size":
      return item.size
    if field == "importance":
      return item.importance
    if field == "urgency":
      return item.urgency
    if field == "score":
      return eff[item.id]
    return item.effort

  def key(item):
    value = raw(item)
    blank = value is None or value == ""
    if blank:
      directed = ""
    elif isinstance(value, int) and descending:
      directed = -value
    elif isinstance(value, str) and descending:
      directed = tuple(-ord(c) for c in value)
    else:
      directed = value
    return (1 if blank else 0, directed, place[item.id])

  return sorted(items, key=key)


def _queue_claim(cell: str) -> str:
  """The claim cell as the queue shows it: cut to CLAIM_CELL_CAP cells, the last one `…`."""
  if tui_style.cell_width(cell) <= CLAIM_CELL_CAP:
    return cell
  return tui_style.clipped(cell, CLAIM_CELL_CAP - 1) + "…"


def item_rows(
  state: State, items: list,
  elsewhere: dict[str, list[dict[str, str]]] | None = None,
  *, pane_width: int | None = None,
) -> list[Row]:
  """Queue rows in the given order. The number is the position in that view.

  `pane_width` is the queue pane's width when the caller knows it. Below
  NARROW_PANE the claim and size columns are left out, so the title keeps room
  (sibling-only rows still list); None keeps both, as a wide pane does.
  """
  cfg = state.config
  elsewhere = elsewhere or {}
  narrow = pane_width is not None and pane_width < NARROW_PANE
  # At least the historical 7, wider for the longest visible label (`reviewing`).
  status_w = max([7, *(len(cfg.status_label(store.displayed_status(
    cfg, item, elsewhere.get(item.id, []),
  ))) for item in items)])
  claims = {} if narrow else {item.id: _queue_claim(store.claim_cell(cfg, item, elsewhere.get(item.id)))
                              for item in items}
  # The same CLAIM column `slicer list` prints: at least its header, as wide as the longest cell.
  claim_w = max([len("CLAIM"), *(len(claim) for claim in claims.values())])
  out: list[Row] = []
  for n, item in enumerate(items, 1):
    pending = graph.pending(state.index, item, cfg)
    marker = "!" if pending else " "
    status = store.displayed_status(cfg, item, elsewhere.get(item.id, []))
    label = cfg.status_label(status)
    columns = "" if narrow else f"{claims[item.id]:<{claim_w}} {item.size:<2} "
    prefix = f"{n:>3}{marker} {item.id:<5} {label:<{status_w}} {columns}"
    out.append(
      Row(
        kind=ITEM,
        target=item.id,
        text=f"{prefix}P:{item.score} {item.display_title()}",
        status=status,
        blocked=bool(pending),
        priority_at=len(prefix),
        high_priority=item.importance == 3 or item.urgency == 3,
      )
    )
  return out


def rows(state: State) -> list[Row]:
  """The queue in ranked order, then the roadmap's own prose blocks."""
  out = item_rows(state, graph.ranked_order(state.index, state.config, list(state.index.items)))
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
    out.append(Entry(kind="lead", target=target, name="lead", body="\n\n".join(sl.lead)))
    out.append(Entry(kind="boundary", target=target, name="boundary", body=sl.boundary))
    out += [Entry(kind="section", target=target, name=s.heading, body=s.body) for s in sl.sections]
  out += [Entry(kind="note", target=target, name=f"note {i + 1}", body=n.text, index=i)
          for i, n in enumerate(item.note_records)]
  out.append(Entry(kind="note_new", target=target, name="add a note", body=""))
  return out


def panel(
  state: State, target: str,
  elsewhere: dict[str, list[dict[str, str]]] | None = None,
) -> list[PanelLine]:
  """The detail pane: plain context lines, plus lines tagged with their entry."""
  if target in prose.refs(state.index):
    out = [PanelLine(target, role="heading"), PanelLine("")]
    for line in prose.get(state.index, target).split("\n"):
      out.append(PanelLine(line, 0))
    return out

  item = state.index.get(target)
  if item is None:
    return [PanelLine("(no such item)")]
  status = store.displayed_status(state.config, item, (elsewhere or {}).get(item.id, []))
  out = [
    PanelLine(f"{item.id}  {item.title}", role="heading"),
    PanelLine(""),
    PanelLine(_labelled("status", state.config.status_label(status)),
              role=tui_style.status_role(status, bool(graph.pending(
                state.index, item, state.config)), state.config)),
    PanelLine(_labelled("claim", item.claim_owner or "-")),
  ]
  for n, label in enumerate(FIELD_SPEC):
    role = "priority" if label in ("importance", "urgency") and getattr(item, label) == 3 else "field"
    out.append(PanelLine(_labelled(label, _field_current(item, label) or "-"), n, role))
  # Read-only context: recorded by slicer, not edited here.
  cites = render.cite_header(state.index, item).removeprefix("Cites: ")
  out.append(PanelLine(_labelled("group", item.group or "-")))
  out.append(PanelLine(_labelled("attempts", item.attempts)))
  out.append(PanelLine(_labelled("discovered from", item.discovered_from or "-")))
  out.append(PanelLine(_labelled("parent", item.parent or "-")))
  out.append(PanelLine(_labelled("cites", cites or "-")))
  out.append(PanelLine(_labelled("score", f"{item.score} ({item.quadrant})"), role=(
    "priority" if item.importance == 3 or item.urgency == 3 else "normal")))
  out.append(PanelLine(""))
  sl = state.slices.get(target)
  if sl is None:
    out.append(PanelLine("(no slice yet - press n to promote)"))
    note_base = len(FIELD_SPEC)
  else:
    base = len(FIELD_SPEC)
    out.append(PanelLine("Lead", base, "heading"))
    for paragraph in sl.lead:
      out.extend(PanelLine(line, base) for line in paragraph.split("\n"))
      out.append(PanelLine("", base))
    if not sl.lead:
      out.append(PanelLine("", base))
    base += 1
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
  for i, note in enumerate(item.rendered_notes):
    idx = note_base + i
    for line in note.split("\n"):
      out.append(PanelLine(line, idx))
    out.append(PanelLine("", idx))
  out.append(PanelLine("+ add a note", note_base + len(item.notes)))
  return out


def render_drifted(state: State) -> bool:
  """Whether `.slicer/render/` differs from what `r` would write. A plan or read that
  fails counts as drifted: the render cannot be confirmed current."""
  try:
    return render.compare(render.plan(state), state.render_dir).stale
  except (SlicerError, OSError):
    return True


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
      vcs.require_no_merge(state.root)
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
    if action == "release":
      held = state.index.require(target).claim_owner
      vcs.require_no_merge(state.root)
      ops.release(state, target)
      return ActResult(f"{target} released", severity="success") if held else ActResult(
        f"{target} has no claim"
      )
    if action == "reorder_down":
      at = state.index.position(target)
      if at + 1 < len(state.index.items):
        vcs.require_no_merge(state.root)
        ops.move(state, target, after=state.index.items[at + 1].id)
        return ActResult(f"{target} moved down", severity="success")
      return ActResult("already last")
    if action == "reorder_up":
      at = state.index.position(target)
      if at > 0:
        vcs.require_no_merge(state.root)
        ops.move(state, target, before=state.index.items[at - 1].id)
        return ActResult(f"{target} moved up", severity="success")
      return ActResult("already first")
    if action == "reorder_top":
      if state.index.position(target) > 0:
        vcs.require_no_merge(state.root)
        ops.move(state, target, to=1)
        return ActResult(f"{target} moved to top", severity="success")
      return ActResult("already first")
    if action == "promote":
      vcs.require_no_merge(state.root)
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
      vcs.require_no_merge(state.root)
      return ActResult(f"added {ops.add(state, title).id}", severity="success")
    if request.kind == PROSE:
      vcs.require_no_merge(state.root)
      ops.edit_prose(state, request.target, body)
    elif request.kind == "lead":
      vcs.require_no_merge(state.root)
      # The blank-line split `edit --lead` uses; an empty body clears the lead.
      ops.edit_lead(state, request.target, outline.blocks(body.split("\n")))
    elif request.kind == "boundary":
      vcs.require_no_merge(state.root)
      ops.edit_boundary(state, request.target, body)
    elif request.kind == "field":
      kwarg, parse = FIELD_SPEC[request.name]
      item = state.index.require(request.target)
      before = item.to_dict()
      vcs.require_no_merge(state.root)
      ops.set_fields(state, request.target, **{kwarg: parse(body)})
      if item.to_dict() == before:
        return ActResult(f"{request.name} unchanged")
    elif request.kind == "note":
      if body.strip():
        vcs.require_no_merge(state.root)
        ops.set_note(state, request.target, request.index, body)
        return ActResult("updated note; press r to render", severity="success")
      vcs.require_no_merge(state.root)
      ops.remove_note(state, request.target, request.index)
      return ActResult("note removed; press r to render", severity="success")
    elif request.kind == "note_new":
      if not body.strip():
        return ActResult("cancelled")
      vcs.require_no_merge(state.root)
      ops.add_note(state, request.target, body)
      return ActResult("added note; press r to render", severity="success")
    else:
      vcs.require_no_merge(state.root)
      ops.edit_section(state, request.target, request.name, body)
  except (SlicerError, OSError) as exc:
    return ActResult(str(exc), severity="error")
  return ActResult(f"updated {request.name}; press r to render", severity="success")


def apply_edit(state: State, request: EditRequest, body: str) -> str:
  """Preserve the string interface for callers that do not draw feedback."""
  return apply_edit_result(state, request, body).message


FILTER_GROUPS = ("status", "tree", "pass", "importance", "urgency", "flag")


@dataclass
class Filters:
  query: str = ""
  values: dict[str, set[str]] = field(
    default_factory=lambda: {group: set() for group in FILTER_GROUPS}
  )

  @classmethod
  def initial(cls, state: State) -> Filters:
    filters = cls()
    hidden = {state.config.done_status}
    if state.config.retired_status:
      hidden.add(state.config.retired_status)
    filters.values["status"] = set(state.config.statuses) - hidden
    return filters

  def copy(self) -> Filters:
    return Filters(self.query, {k: set(v) for k, v in self.values.items()})

  @property
  def active(self) -> bool:
    return bool(self.query or any(self.values.values()))

  def matches(self, item, displayed_status: str | None = None) -> bool:
    needle = self.query.casefold()
    if needle and not any(needle in text.casefold() for text in (
      item.id, item.title, item.short_title
    )):
      return False
    candidates = {
      "status": {item.status, displayed_status or item.status}, "tree": set(item.trees) or {""},
      "pass": {item.pass_key}, "importance": {str(item.importance)},
      "urgency": {str(item.urgency)},
      "flag": set(item.flags) or {""},
    }
    return all(not selected or selected & candidates[group]
               for group, selected in self.values.items())

  def summary(self) -> str:
    parts = [f"search={self.query!r}"] if self.query else []
    for group, selected in self.values.items():
      if selected:
        parts.append(f"{group}=" + ",".join(v or "(none)" for v in sorted(selected)))
    return "  ".join(parts) or "all items"


def sibling_only_rows(only_sibling: list[dict[str, str]]) -> list[Row]:
  """The `Only in a sibling` block as `slicer list` lists it: a separator, then
  `ID  worktree  title` rows that cannot be selected. Empty when there are none."""
  if not only_sibling:
    return []
  id_w = max(len(row["id"]) for row in only_sibling)
  tree_w = max(len(row["worktree"]) for row in only_sibling)
  out = [Row(kind=SEPARATOR, target="", text="─ only in a sibling " + "─" * 20)]
  for row in only_sibling:
    out.append(Row(
      kind=SIBLING, target=row["id"],
      text=f"     {row['id']:<{id_w}}  {row['worktree']:<{tree_w}}  {row['title']}",
    ))
  return out


def filtered_rows(
  state: State, filters: Filters, sort_field: str = "ranked", descending: bool = True,
  elsewhere: dict[str, list[dict[str, str]]] | None = None,
  only_sibling: list[dict[str, str]] | None = None,
  *, pane_width: int | None = None,
) -> list[Row]:
  elsewhere = elsewhere or {}
  visible = [it for it in state.index.items if filters.matches(
    it, store.displayed_status(state.config, it, elsewhere.get(it.id, [])),
  )]
  ordered = sort_items(state, visible, sort_field, descending, elsewhere)
  out = item_rows(state, ordered, elsewhere, pane_width=pane_width)
  refs = prose.refs(state.index)
  if refs:
    out.append(Row(kind=SEPARATOR, target="", text="─ roadmap prose " + "─" * 20))
    for ref in refs:
      lines, preview = prose.summary(state.index, ref, width=30)
      out.append(Row(kind=PROSE, target=ref, text=f"     {ref:<22} {lines:>3}  {preview}"))
  # Like `list`, the sibling-only block ignores the item filters and comes last.
  out.extend(sibling_only_rows(only_sibling or []))
  return out


def filter_choices(state: State) -> list[tuple[str, str | None]]:
  """None is Any; the empty string is an item with no tree, pass, or flag."""
  items = state.index.items
  values = {
    "status": list(state.config.statuses),
    "tree": [""] + sorted({tree for it in items for tree in it.trees}),
    "pass": [""] + sorted({it.pass_key for it in items if it.pass_key}),
    "importance": ["1", "2", "3"], "urgency": ["1", "2", "3"],
    "flag": [""] + sorted({flag for it in items for flag in it.flags}),
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
  # "" is the unfinished stop. "in-work" and "review" name the status preset.
  view_preset: str = ""
  sort_field: str = "ranked"
  sort_desc: bool = True
  sort_draft_desc: bool = True
  listing: list[Row] = field(default_factory=list)
  elsewhere: dict[str, list[dict[str, str]]] = field(default_factory=dict)
  only_sibling: list[dict[str, str]] = field(default_factory=list)
  # The queue pane's width as the last draw saw it; None until the first draw.
  pane_width: int | None = None
  selected: tuple[str, str] | None = None
  entry_at: int = 0
  focus: str = "left"
  scroll: int = 0
  mode: str = "normal"
  # "handoff" or "reject" while the note prompt is open.
  note_action: str = ""
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
  # The relay board: runs as of the last load, frames since it opened, the tick of that load,
  # the first line shown, and the one-line reason the last load failed ("" when it did not).
  board_runs: list[relayrun.Run] = field(default_factory=list)
  board_tick: int = 0
  board_loaded: int = 0
  board_at: int = 0
  board_error: str = ""
  # Whether the generated render is stale, as of the last drift check. The check is a full
  # render plan and compare, so it runs once on open and after each action that can write
  # state (`drift_due`), never on navigation.
  render_stale: bool = False
  drift_due: bool = True

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
    # The session outlives every command; re-ask git about this checkout once per reload.
    vcs.clear_cache()
    if self.drift_due:
      self.render_stale, self.drift_due = render_drifted(state), False
    # One read of the sibling worktrees feeds both the claim cells and the sibling-only block.
    self.elsewhere, self.only_sibling = store.sibling_work(state.root, state.index)
    listing = filtered_rows(
      state, self.filters, self.sort_field, self.sort_desc, self.elsewhere, self.only_sibling,
      pane_width=self.pane_width,
    )
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
    # Set conservatively: results that write nothing (editor requests, no-op messages) also
    # cost one plan and compare. Navigation keys never reach here, so they never do.
    self.drift_due = True
    if result.message:
      self.notify(result.message, result.severity)

  def feedback(self) -> str:
    prefix = {"info": "Info: ", "success": "OK: ", "error": "Error: "}.get(self.message_severity, "")
    return prefix + self.message

  def status(self, state: State) -> str:
    count = sum(row.kind == ITEM for row in self.listing)
    done = model.completion(state.index.items, state.config.done_status)["percent"]
    stale = "  render stale" if self.render_stale else ""
    direction = "desc" if self.sort_desc else "asc"
    label = dict(SORT_FIELDS)[self.sort_field]
    summary = self.filters.summary()
    view = f"view={self.view_preset}  " if self.view_preset else ""
    return (f"{count}/{len(state.index.items)} items  {done}% done{stale}  "
            f"sort={label} {direction}  {view}{summary}")

  def _load_board(self, state: State) -> None:
    """Read the runs; on a damaged ledger keep the last good frame and say why."""
    self.board_loaded = self.board_tick
    try:
      runs = relayrun.list_runs(state.root)
    except (SlicerError, OSError) as exc:
      self.board_error = (str(exc).splitlines() or [type(exc).__name__])[0]
      return
    self.board_error = ""
    now = relayrun.utcnow()
    live = [run for run in runs if run.state != "ended"]
    recent = [
      run for run in runs
      if run.state == "ended" and now - _parse_stamp(run.updated_at) <= BOARD_RECENT_ENDED
    ]
    recent.sort(key=lambda run: (run.updated_at, run.id), reverse=True)
    self.board_runs = live + recent

  def handle(self, state: State, key: str) -> ActResult:
    if key == "tick":  # a timer wake-up: only the open board has anything to do with it
      if self.mode == "board":
        self.board_tick += 1
        if self.board_tick - self.board_loaded >= BOARD_REFRESH_TICKS:
          self._load_board(state)
      return ActResult()
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
    if self.mode == "note":
      return self._note(state, key)
    if self.mode == "filter":
      return self._filter(state, key)
    if self.mode == "sort":
      return self._sort(state, key)
    if self.mode == "board":
      if key in ("b", "q", "\x1b"):
        self.mode = "normal"
      elif key in ("j", "KEY_DOWN"):
        self.board_at += 1
      elif key in ("k", "KEY_UP"):
        self.board_at = max(0, self.board_at - 1)
      return ActResult()
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
    elif action == "board":
      self.mode, self.board_tick, self.board_at = "board", 0, 0
      self.board_runs = []  # never show a frame from an earlier opening; the load resets the error
      self._load_board(state)
    elif action in ("search", "jump"):
      self.mode = action
      self.saved_query, self.saved_selection = self.filters.query, self.selected
      self.text = self.filters.query if action == "search" else ""
    elif action == "filter":
      self.mode, self.draft, self.choice_at = "filter", self.filters.copy(), 0
    elif action == "sort":
      self.mode = "sort"
      self.sort_draft_desc = self.sort_desc
      self.choice_at = next(i for i, (name, _) in enumerate(SORT_FIELDS) if name == self.sort_field)
    elif action == "view":
      self._cycle_view(state)
    elif action == "clear":
      self.filters = Filters()
      self.view_preset = ""
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
    elif action in ("reorder_up", "reorder_down", "reorder_top", "move_to") and (
      self.filters.active or self.view_preset
    ):
      self.notify("reordering disabled while filtered; press c to clear search and filters")
    elif action == "move_to":
      if self.selected:
        self.mode, self.text = "move", ""
      else:
        self.notify("no item selected")
    elif action in ("handoff", "reject"):
      if not self.selected:
        self.notify("no item selected")
      elif self.selected[0] != ITEM:
        self.notify("that key applies to a slice, not to a prose block")
      else:
        self.mode, self.text, self.note_action = "note", "", action
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
      vcs.require_no_merge(state.root)
      with store.project_lock(state.root):
        vcs.require_no_merge(state.root)
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
        self.filters, self.focus, self.view_preset = Filters(), "left", ""
        self.refresh(state)
        self.select((ITEM, report.ids[0]))
        outcome = act(state, "r", "")
        # Flag after the render, success or failure: a refresh before it would compare the
        # new index against the old render, and nothing else reruns the check here.
        self.drift_due = True
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
            self.view_preset = ""
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
          vcs.require_no_merge(state.root)
          landed = ops.move(state, self.target, to=int(text))
          self.drift_due = True
          self.notify(f"{self.target} moved to {landed}", "success")
        except (SlicerError, OSError) as exc:
          self.notify(str(exc), "error")
      self.refresh(state)
    elif key in ("KEY_BACKSPACE", "\x7f", "\b"):
      self.text = self.text[:-1]
    elif len(key) == 1 and key.isdigit():
      self.text += key
    return ActResult()

  def _note(self, state: State, key: str) -> ActResult:
    """Collect a note, then hand off or reject through the same ops the CLI uses.
    A handoff note is optional; a reject needs one, so an empty one cancels."""
    if key == "\x1b":
      self.mode = "normal"
      self.notify("cancelled")
    elif key in ("\n", "\r", "KEY_ENTER"):
      self.mode = "normal"
      note = self.text.strip()
      if self.note_action == "reject" and not note:
        self.notify("cancelled")
      elif not self.selected:
        self.notify("no item selected", "error")
      else:
        try:
          vcs.require_no_merge(state.root)
          if self.note_action == "handoff":
            ops.handoff(state, self.target, note=note)
            self.notify(f"{self.target} handed off for review", "success")
          else:
            ops.reject(state, self.target, note=note)
            self.notify(f"{self.target} rejected", "success")
          self.drift_due = True
        except (SlicerError, OSError) as exc:
          self.notify(str(exc), "error")
      self.refresh(state)
    else:
      self.text, _ = tui_wizard.text_input(self.text, key)
    return ActResult()

  def _sort(self, state: State, key: str) -> ActResult:
    """Pick a view order. Enter applies it; nothing is written to the queue."""
    if key == "\x1b":
      self.mode = "normal"
      self.notify("sort unchanged")
    elif key in ("\n", "\r", "KEY_ENTER"):
      self.sort_field = SORT_FIELDS[self.choice_at][0]
      self.sort_desc = self.sort_draft_desc
      self.mode = "normal"
      direction = "descending" if self.sort_desc else "ascending"
      self.notify(f"sorted by {dict(SORT_FIELDS)[self.sort_field]}, {direction}")
    elif key in ("j", "KEY_DOWN"):
      self.choice_at = min(self.choice_at + 1, len(SORT_FIELDS) - 1)
    elif key in ("k", "KEY_UP"):
      self.choice_at = max(0, self.choice_at - 1)
    elif key == " ":
      self.sort_draft_desc = not self.sort_draft_desc
    self.refresh(state)
    return ActResult()

  def _filter(self, state: State, key: str) -> ActResult:
    choices = filter_choices(state)
    if key == "\x1b":
      self.mode, self.draft = "normal", None
      self.notify("filters unchanged")
    elif key in ("\n", "\r", "KEY_ENTER"):
      if self.draft is not None:
        self.filters = self.draft
      self.view_preset = ""
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

  def _view_stops(self, state: State) -> list[str]:
    """The cycle `v` walks. Empty roles are skipped, not shown as empty queues."""
    stops = [""]
    if state.config.in_work():
      stops.append("in-work")
    if state.config.review_status:
      stops.append("review")
    return stops

  def _apply_view(self, state: State, name: str) -> None:
    """Replace only the status filter. Search and the other groups stay."""
    self.view_preset = name
    if name == "":
      self.filters.values["status"] = set(Filters.initial(state).values["status"])
    elif name == "in-work":
      self.filters.values["status"] = set(state.config.in_work())
    else:
      self.filters.values["status"] = {state.config.review_status}

  def _cycle_view(self, state: State) -> None:
    stops = self._view_stops(state)
    if len(stops) == 1:
      self.notify("no in-work or review status is configured")
      return
    at = stops.index(self.view_preset) if self.view_preset in stops else 0
    name = stops[(at + 1) % len(stops)]
    self._apply_view(state, name)
    self.notify("showing unfinished items" if name == "" else f"view={name}")


# The relay board. `board_lines` is pure so tests need no terminal: the spinner frame comes
# from `tick` and the elapsed times from `now`.
BOARD_TICK_MS = 250
# Runs are re-read every this many ticks (2 s): `list_runs` shells out to git on every call,
# and the spinner needs a frame four times a second, not fresh data.
BOARD_REFRESH_TICKS = 8
# A run that ended no longer than this ago stays on the board as one summary line.
BOARD_RECENT_ENDED = timedelta(hours=1)
SPINNER = "|/-\\"
# The relay's own roles (`slicer ai relay`). An architect leads the pipeline when a run has one.
RELAY_PIPELINE = tuple(role for role in roles.ROLE_NAMES if role != "architect")
ROLE_WIDTH = 11


def _parse_stamp(text: str) -> datetime:
  return datetime.fromisoformat(text)


def elapsed_text(start: str, end: datetime) -> str:
  seconds = max(0, int((end - _parse_stamp(start)).total_seconds()))
  if seconds < 60:
    return f"{seconds}s"
  if seconds < 3600:
    return f"{seconds // 60}m{seconds % 60:02d}s"
  return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"


def _fit(cells: list[str], width: int) -> str:
  """Join cells with two spaces. The first is clipped to fit; a later one that does not fit is dropped."""
  if not cells:
    return ""
  text = tui_style.clipped(cells[0], width)
  for cell in cells[1:]:
    if cell and tui_style.cell_width(text + "  " + cell) <= width:
      text += "  " + cell
  return text.rstrip()


def _ellipsis(text: str, width: int) -> str:
  if tui_style.cell_width(text) <= width:
    return text
  return tui_style.clipped(text, max(0, width - 3)) + "..."


def _marker(step: relayrun.Step, run: relayrun.Run, tick: int) -> tuple[str, str]:
  """The one-character cue and its style role for a step as of this frame."""
  if step.phase == "done":
    return "+", "done"
  if step.phase == "fail":
    return "!", "error"
  if run.state == "active":
    return SPINNER[tick % len(SPINNER)], "started"
  return "~", "dim"  # started, but nothing is running it


def _lane(run: relayrun.Run) -> dict[str, list[relayrun.Step]]:
  """Each role's steps in the lane, oldest first; the role that moved last comes last.

  A step is in the lane when nothing spawned it or the orchestrator did. A step that any
  other role spawned is that role's subagent: it is counted on its parent's row, not drawn.
  """
  lane: dict[str, list[relayrun.Step]] = {}
  for step in run.steps:
    parent = run.step(step.parent) if step.parent else None
    if step.parent and (parent is None or parent.role != "orchestrator"):
      continue
    steps = lane.pop(step.role, [])  # a later step moves its role to the end
    steps.append(step)
    lane[step.role] = steps
  return lane


def _step_elapsed(step: relayrun.Step, run: relayrun.Run, now: datetime) -> str:
  running = step.phase == "start" and run.state == "active"
  end = now if running else _parse_stamp(step.updated_at)
  return elapsed_text(step.begun_at, end)


def _model_effort(step: relayrun.Step) -> str:
  return "/".join(part for part in (step.model, step.reasoning) if part)


def _title_cell(head: str, title: str, tail: list[str], width: int) -> str:
  """The title, cut with `...` to the room left beside `head` and as many `tail` cells as fit.

  The first two tail cells always keep their room; the later ones keep it only when the
  title can still be at least a few cells wide, and are dropped by `_fit` otherwise.
  """
  if not title:
    return ""
  for kept in range(len(tail), min(2, len(tail)) - 1, -1):
    room = width - tui_style.cell_width(head) - 2 - sum(tui_style.cell_width(c) + 2 for c in tail[:kept])
    if room >= 8:
      return _ellipsis(title, room)
  return ""


def _lane_header(run: relayrun.Run, lane: dict[str, list[relayrun.Step]], titles: dict[str, str],
                 now: datetime, width: int) -> list[PanelLine]:
  live = run.state == "active"
  head = f"{'●' if live else '~'} {', '.join(run.items)}"
  tail = [run.state, elapsed_text(run.begun_at, now if live else _parse_stamp(run.updated_at))]
  title = _title_cell(head, titles.get(run.items[0], "") if run.items else "", tail, width)
  lines = [PanelLine(_fit([head, title] + tail, width), role="heading" if live else "dim")]
  orchestrator = lane.get("orchestrator")
  worktree = run.worktree.rstrip("/").rsplit("/", 1)[-1]
  # The freshness comes first: on a narrow screen it is the cell worth keeping.
  cells = [
    f"last heard {elapsed_text(run.updated_at, now)} ago",
    f"orchestrator {_model_effort(orchestrator[-1])}".rstrip() if orchestrator else "",
    f"wt {worktree}" if worktree and worktree.casefold() != ", ".join(run.items).casefold() else "",
  ]
  cells = [cell for cell in cells if cell]
  cells[0] = "  " + cells[0]
  if run.state == "stale":
    role = "error"
  elif live and now - _parse_stamp(run.updated_at) > relayrun.STALE_AFTER / 2:
    role = "started"
  else:
    role = "dim"
  lines.append(PanelLine(_fit(cells, width), role=role))
  return lines


def _ended_line(run: relayrun.Run, lane: dict[str, list[relayrun.Step]], titles: dict[str, str],
                now: datetime, width: int) -> PanelLine:
  glyph = {"pass": "✓", "fail": "✗"}.get(run.outcome, "~")
  head = f"{glyph} {', '.join(run.items)}"
  tail = [
    run.outcome, elapsed_text(run.begun_at, _parse_stamp(run.updated_at)),
    f"ended {elapsed_text(run.updated_at, now)} ago",
    " · ".join(f"{label} {len(lane.get(role, ()))}"
               for label, role in (("impl", "implementer"), ("review", "reviewer"), ("gate", "gate"))),
  ]
  title = _title_cell(head, titles.get(run.items[0], "") if run.items else "", tail, width)
  return PanelLine(_fit([head, title] + tail, width), role="dim")


def _gate_fail_lines(run: relayrun.Run, lane: dict[str, list[relayrun.Step]], indent: str,
                     width: int) -> list[PanelLine]:
  """One line per failed gate step: the round the failure sent the work back to, and why."""
  in_lane = {id(step) for steps in lane.values() for step in steps}
  rounds = 0
  lines = []
  for step in run.steps:
    if id(step) not in in_lane:
      continue
    if step.role == "implementer":
      rounds += 1
    elif step.role == "gate" and step.phase == "fail":
      why = f": {step.task}" if step.task else ""
      lines.append(PanelLine(
        _ellipsis(f"{indent}✗ gate FAIL → implementer round {rounds + 1}{why}", width), role="error"))
  return lines


def board_lines(runs: list[relayrun.Run], tick: int, now: datetime, width: int,
                titles: dict[str, str] | None = None) -> list[PanelLine]:
  """One lane per live run, one summary line per ended run.

  A lane is the slice (its title comes from `titles`, item id to title), a freshness line, the
  pipeline with its round, and a row per role, under the orchestrator when the run has one.
  `width` is the cells available. A row drops a cell that does not fit rather than cutting it.
  Only a running step in an active run animates; a stale lane is dimmed throughout, bar the
  freshness line, which turns to the error role.
  """
  if not runs:
    return [
      PanelLine("Nothing running.", role="info"),
      PanelLine(tui_style.clipped("An orchestrator records a relay with `slicer relay-run begin`", width), role="dim"),
      PanelLine(tui_style.clipped("and `relay-run step`; see docs/commands.md#relay-run-ledger.", width), role="dim"),
    ]
  titles = titles or {}
  lines: list[PanelLine] = []
  for run in runs:
    if lines:
      lines.append(PanelLine(""))
    lane = _lane(run)
    if run.state == "ended":
      lines.append(_ended_line(run, lane, titles, now, width))
      continue
    live = run.state == "active"
    dim = "normal" if live else "dim"
    lines.extend(_lane_header(run, lane, titles, now, width))
    names = (("architect",) if "architect" in lane else ()) + RELAY_PIPELINE
    parts = [
      f"[{name}]" if name in lane and lane[name][-1].phase == "start" and live else name
      for name in names
    ]
    pipeline = "  " + " > ".join(parts)
    rounds = f"round {len(lane['implementer'])}" if "implementer" in lane else ""
    gap = width - tui_style.cell_width(pipeline) - tui_style.cell_width(rounds)
    lines.append(PanelLine(
      pipeline + " " * gap + rounds if rounds and gap >= 2 else _fit([pipeline], width), role=dim))
    orchestrator = lane.get("orchestrator")
    if orchestrator:
      step = orchestrator[-1]
      marker, role = _marker(step, run, tick)
      lines.append(PanelLine(_fit([
        f"  └ orchestrator {_model_effort(step)}".rstrip(), f"[{marker}]",
        _step_elapsed(step, run, now),
      ], width), role=role if live else "dim"))
    for at, name in enumerate(names):
      last = at == len(names) - 1
      # A row under the orchestrator hangs on a tree; a flat run keeps its plain indent.
      prefix = ("    └ " if last else "    ├ ") if orchestrator else "  "
      below = ("          " if last else "    │     ") if orchestrator else "        "
      steps = lane.get(name)
      if steps is None:
        lines.append(PanelLine(_fit([f"{prefix}[ ] {name:<{ROLE_WIDTH}}  waiting"], width), role="dim"))
        continue
      step = steps[-1]
      marker, role = _marker(step, run, tick)
      role = role if live else "dim"
      subagents = len(run.subagents_of(step.id))
      model = "/".join(part for part in (step.vendor, step.model) if part)
      lines.append(PanelLine(_fit([
        f"{prefix}[{marker}] {name:<{ROLE_WIDTH}}",
        f"×{len(steps)}" if len(steps) > 1 else "",
        _step_elapsed(step, run, now),
        f"{subagents} subagent{'s' if subagents != 1 else ''}" if subagents else "",
        step.agent,
        f"{model} {step.reasoning}".strip(),
      ], width), role=role))
      if step.task and not (name == "gate" and step.phase == "fail"):  # the FAIL line carries it
        lines.append(PanelLine(_ellipsis(f"{below}{step.task}", width), role=role))
      if name == "gate":
        lines.extend(_gate_fail_lines(run, lane, below, width))
    others = [steps[-1] for name, steps in lane.items() if name not in names and name != "orchestrator"]
    if others:
      cells = ["  other:"]
      for step in others:
        marker, _ = _marker(step, run, tick)
        cells.append(f"{step.role} [{marker}] {_step_elapsed(step, run, now)}")
      lines.append(PanelLine(_fit(cells, width), role=dim))
  return lines


def draw(screen, state: State, view: View, palette: tui_style.Palette | None = None) -> None:
  """Render semantic cues through one bounded terminal drawing surface."""
  palette = palette or tui_style.monochrome()
  canvas = tui_style.Canvas(screen)
  height, width = canvas.height, canvas.width
  # The queue rows are built for the pane this frame draws, so the size column can drop.
  view.pane_width = width // 2 - 2
  if view.mode != "board":  # the board redraws on a timer and shows nothing from the roadmap
    view.refresh(state)
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
  if view.mode == "board":
    put(0, 0, "Relay board: j/k or arrows scroll, b, q or Esc close", palette.attr("heading"))
    titles = {item.id: item.title for item in state.index.items}
    lines = board_lines(view.board_runs, view.board_tick, relayrun.utcnow(), width - 1, titles)
    view.board_at = max(0, min(view.board_at, len(lines) - visible))
    for n, line in enumerate(lines[view.board_at:view.board_at + visible], 1):
      put(n, 0, line.text, palette.attr(line.role))
  elif view.mode in ("filter", "help", "sort"):
    title = {
      "filter": "Filters: j/k move, Space toggle, Enter apply, Esc cancel",
      "sort": "Sort: j/k choose field, Space flips direction, Enter applies, Esc cancels",
      "help": "Help: j/k scroll, ? or Esc close",
    }[view.mode]
    put(0, 0, title, palette.attr("heading"))
    if view.mode == "sort":
      direction = "descending" if view.sort_draft_desc else "ascending"
      lines = [
        f"{label:<16} {direction if n == view.choice_at else ''}"
        for n, (_, label) in enumerate(SORT_FIELDS)
      ]
      selected_at = view.choice_at
      top = max(0, selected_at - visible + 1)
    elif view.mode == "filter":
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
    # Scrolling follows selectable rows, so the trailing sibling-only rows need this too.
    last = max((i for i, row in enumerate(view.listing) if row.selectable), default=-1)
    if selected_at == last:
      view.scroll = max(view.scroll, len(view.listing) - capacity)
    view.scroll = min(view.scroll, max(0, len(view.listing) - capacity))
    for n, row in enumerate(view.listing[view.scroll:view.scroll + capacity], offset):
      selected = row_identity(row) == view.selected
      role = ("dim" if row.kind in (SEPARATOR, SIBLING)
              else tui_style.status_role(row.status, row.blocked, state.config))
      attr = palette.attr(role, selected=selected, focused=view.focus == "left")
      marker = "> " if selected else "  "
      put(n, 0, marker + row.text, attr, left_width)
      if row.kind == ITEM and row.high_priority:
        x = 2 + tui_style.cell_width(tui_style.clipped(row.text[:row.priority_at], left_width))
        put(n, x, row.text[row.priority_at:row.priority_at + 4],
            palette.attr("priority", selected=selected, focused=view.focus == "left"), left_width - x)
    lines = panel(state, view.target, view.elsewhere) if view.selected else []
    first = next((n for n, line in enumerate(lines) if line.entry == view.entry_at), 0)
    top = max(0, first - 2) if view.focus == "right" else 0
    for n, line in enumerate(lines[top:top + visible], 1):
      selected = line.entry is not None and line.entry == view.entry_at
      attr = palette.attr(line.role, selected=selected, focused=view.focus == "right")
      put(n, right_x, ("> " if selected else "  ") + line.text, attr)
  put(status_y, 0, view.status(state), palette.attr("dim"))
  if view.mode in ("search", "jump", "move", "note"):
    label = {
      "search": "Search", "jump": "Jump to ID", "move": "Move to position",
      "note": "Handoff note, optional" if view.note_action == "handoff"
      else "Reject verdict, required",
    }[view.mode]
    prompt = f"{label} (Enter accepts, Esc cancels): {view.text}"
    put(status_y + 1, 0, prompt[-max(1, width - 1):], palette.attr(selected=True, focused=True))
  elif view.mode == "board" and view.board_error:
    put(status_y + 1, 0, "Error: relay ledger: " + view.board_error, palette.attr("error"))
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
      # Only the board animates, so only it wakes on a timer; everything else blocks.
      screen.timeout(BOARD_TICK_MS if view.mode == "board" else -1)
      try:
        key = screen.get_wch()
        if isinstance(key, int):
          key = curses.keyname(key).decode("ascii")
      except curses.error:
        if view.mode != "board":
          continue
        key = "tick"
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
