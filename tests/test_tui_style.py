"""Keep visual meaning and terminal fallbacks verifiable without a display."""

from __future__ import annotations

import curses
import unittest
from unittest.mock import patch

import support
from slicer import tui, tui_style
from slicer.config import Config
from slicer.errors import StateError
from test_tui import Screen, example, type_keys


class Terminal:
  A_NORMAL, A_BOLD, A_REVERSE, A_DIM = 0, 1, 2, 4
  COLOR_CYAN, COLOR_GREEN, COLOR_YELLOW, COLOR_RED = 6, 2, 3, 1
  COLORS, COLOR_PAIRS = 8, 8
  error = curses.error

  def __init__(self, fail: str = "", colors: bool = True) -> None:
    self.fail, self.colors = fail, colors
    self.calls = []

  def call(self, name, *args):
    self.calls.append((name, args))
    if self.fail == name:
      raise curses.error(name)

  def has_colors(self):
    self.call("has_colors")
    return self.colors

  def start_color(self):
    self.call("start_color")

  def use_default_colors(self):
    self.call("use_default_colors")

  def init_pair(self, *args):
    self.call("init_pair", *args)

  def color_pair(self, pair):
    self.call("color_pair", pair)
    return pair << 8


class StyledScreen(Screen):
  def __init__(self, height=24, width=120):
    super().__init__(height, width)
    self.styled = []

  def erase(self):
    super().erase()
    self.styled.clear()

  def addnstr(self, y, x, text, count, attr):
    super().addnstr(y, x, text, count, attr)
    self.styled.append((y, x, text, attr))


class TuiStyleTests(unittest.TestCase):
  def test_StatusRole_CustomVocabulary_UsesConfiguredRoles(self) -> None:
    cfg = Config.from_dict({})
    cfg.open_status, cfg.started_status = "todo", "working"
    cfg.done_status, cfg.parked_status = "shipped", "waiting"
    for status, blocked, expected in (
      ("todo", False, "normal"), ("todo", True, "blocked"),
      ("working", False, "started"), ("working", True, "blocked"),
      ("shipped", True, "done"), ("waiting", True, "parked"),
      ("done", False, "normal"), ("custom", True, "normal"),
    ):
      with self.subTest(status=status, blocked=blocked):
        self.assertEqual(tui_style.status_role(status, blocked, cfg), expected)

  def test_Palette_BasicColors_UsesDefaultBackground(self) -> None:
    terminal = Terminal()
    palette = tui_style.setup_palette(terminal, {})
    pairs = [args for name, args in terminal.calls if name == "init_pair"]
    self.assertEqual(pairs, [(1, 6, -1), (2, 2, -1), (3, 3, -1), (4, 1, -1)])
    self.assertEqual(palette.attr("started"), terminal.color_pair(1))
    self.assertEqual(palette.attr("success"), palette.attr("done"))
    self.assertEqual(palette.attr("priority"), terminal.color_pair(3) | terminal.A_BOLD)

  def test_Palette_NoColor_SkipsAllColorInitialization(self) -> None:
    for value in ("1", "0", " "):
      terminal = Terminal()
      self.assertEqual(tui_style.setup_palette(terminal, {"NO_COLOR": value}),
                       tui_style.monochrome(terminal))
      self.assertEqual(terminal.calls, [])
    terminal = Terminal()
    tui_style.setup_palette(terminal, {"NO_COLOR": ""})
    self.assertTrue(terminal.calls)

  def test_Palette_UnsupportedOrFailedInitialization_FallsBackCompletely(self) -> None:
    terminals = [Terminal(colors=False)]
    terminals += [Terminal(fail=name) for name in (
      "has_colors", "start_color", "use_default_colors", "init_pair", "color_pair"
    )]
    small = Terminal()
    small.COLOR_PAIRS = 4
    terminals.append(small)
    small = Terminal()
    small.COLORS = 4
    terminals.append(small)
    for terminal in terminals:
      with self.subTest(fail=terminal.fail, colors=terminal.COLORS, pairs=terminal.COLOR_PAIRS):
        self.assertEqual(tui_style.setup_palette(terminal, {}), tui_style.monochrome(terminal))

  def test_Selection_AllRoles_OverridesColorAndSurvivesMonochrome(self) -> None:
    for palette in (tui_style.setup_palette(Terminal(), {}), tui_style.monochrome(Terminal())):
      for role in palette.roles:
        self.assertEqual(palette.attr(role, selected=True, focused=True), 3)
        self.assertEqual(palette.attr(role, selected=True, focused=False), 1)

  def test_Rows_PriorityAndBlockedState_KeepTextAndQueueOrder(self) -> None:
    state = example()
    state.index.require("S02").depends_on = ["S03"]
    listing = [r for r in tui.rows(state) if r.kind == tui.ITEM]
    self.assertEqual([r.target for r in listing], [it.id for it in state.index.items])
    for row in listing:
      item = state.index.require(row.target)
      self.assertIn(f"P:{item.score}", row.text)
      self.assertEqual(row.high_priority, item.importance == 3 or item.urgency == 3)
      self.assertNotIn("\x1b", row.text)
    self.assertIn("!", listing[1].text)
    self.assertTrue(any(line.text == "score      32 (-)" for line in tui.panel(state, "S02")))

  def test_Draw_FocusAndPriority_UseRolesAndTextualMarkers(self) -> None:
    state = example()
    view = tui.View.initial(state)
    palette = tui_style.setup_palette(Terminal(), {})
    screen = StyledScreen()
    tui.draw(screen, state, view, palette)
    self.assertTrue(any(text == "> Queue" for _, _, text, _ in screen.styled))
    self.assertTrue(any(text.startswith("> ") and "S02" in text and attr == palette.focused
                        for _, x, text, attr in screen.styled if x == 0))
    self.assertTrue(any(text == "P:13" and attr == palette.attr("priority")
                        for _, _, text, attr in screen.styled))
    view.handle(state, "\t")
    tui.draw(screen, state, view, palette)
    self.assertTrue(any(text == "> Details" for _, _, text, _ in screen.styled))
    self.assertTrue(any("S02" in text and attr == palette.inactive
                        for _, x, text, attr in screen.styled if x == 0))
    self.assertTrue(any(text.startswith("> size") and attr == palette.focused
                        for _, _, text, attr in screen.styled))

  def test_Draw_Feedback_PrefixAndColorFollowExplicitSeverity(self) -> None:
    state = example()
    view = tui.View.initial(state)
    palette = tui_style.setup_palette(Terminal(), {})
    for severity, prefix in (("error", "Error: "), ("success", "OK: "), ("info", "Info: "), ("normal", "")):
      view.notify("same wording", severity)
      screen = StyledScreen()
      tui.draw(screen, state, view, palette)
      self.assertIn((22, 0, prefix + "same wording", palette.attr(severity)), screen.styled)

  def test_Canvas_WideCombiningAndControlText_StaysInBounds(self) -> None:
    screen = StyledScreen(10, 12)
    tui_style.Canvas(screen).put(0, 0, "界界e\u0301\t\nabcdef", limit=8)
    text = screen.styled[0][2]
    self.assertEqual(text, "界界e\u0301  a")
    self.assertLessEqual(tui_style.cell_width(text), 8)
    self.assertNotIn("\n", text)

  def test_Canvas_UnrelatedCursesFailure_IsNotHidden(self) -> None:
    screen = StyledScreen()
    with patch.object(screen, "addnstr", side_effect=curses.error("unrelated")):
      with self.assertRaises(curses.error):
        tui_style.Canvas(screen).put(0, 0, "text")

  def test_Draw_ResizePromptAndRecovery_PreserveSession(self) -> None:
    state = example()
    view = tui.View.initial(state)
    view.handle(state, "/")
    type_keys(view, state, "Search")
    selected, filters = view.selected, view.filters.copy()
    screen = StyledScreen(9, 79)
    tui.draw(screen, state, view)
    self.assertIn("Resize to at least 80x10", screen.styled[0][2])
    screen.height, screen.width = 24, 120
    view.handle(state, "KEY_RESIZE")
    tui.draw(screen, state, view)
    self.assertEqual(view.selected, selected)
    self.assertEqual(view.filters, filters)
    self.assertEqual(view.mode, "search")
    self.assertTrue(any("Search (Enter" in text for _, _, text, _ in screen.styled))


class TuiFeedbackTests(unittest.TestCase):
  def test_Actions_MutationsAndNoOps_ReportDifferentSeverities(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "Example")
      state = repo.state()
      for key in ("s", "d", "p", "u"):
        self.assertEqual(tui.act(state, key, "S01").severity, "success")
        self.assertEqual(tui.act(state, key, "S01").severity, "info")
      for key in ("J", "K"):
        self.assertEqual(tui.act(state, key, "S01").severity, "info")
      self.assertEqual(tui.act(state, "r", "S01").severity, "success")
      self.assertEqual(tui.act(state, "r", "S01").severity, "info")

  def test_Edit_SuccessUnchangedInvalidAndCancelled_ReportTypedResults(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "Example")
      state = repo.state()
      request = tui.EditRequest("field", "S01", "importance", "2")
      for body, expected in (("2", "info"), ("9", "error"), ("3", "success"), ("03", "info"), (None, "error")):
        with self.subTest(body=body):
          self.assertEqual(tui.apply_edit_result(state, request, body).severity, expected)
      request = tui.EditRequest("new", "", "new item", "")
      self.assertEqual(tui.apply_edit_result(state, request, "   ").severity, "info")
      request = tui.EditRequest(tui.PROSE, "preamble", "preamble", "")
      self.assertEqual(tui.apply_edit_result(state, request, "new context").severity, "success")

  def test_Errors_MessageWording_DoesNotControlSeverity(self) -> None:
    state = example()
    with patch.object(tui.ops, "start", side_effect=StateError("success")):
      result = tui.act(state, "s", "S02")
      self.assertEqual((result.message, result.severity), ("success", "error"))
    with patch.object(tui.render, "plan", side_effect=OSError("render failed")):
      self.assertEqual(tui.act(state, "r", "S02").severity, "error")

  def test_View_SearchFiltersAndJump_ResetPreviousErrorSeverity(self) -> None:
    state = example()
    view = tui.View.initial(state)
    view.handle(state, "g")
    type_keys(view, state, "S99\n")
    self.assertEqual(view.message_severity, "error")
    for keys in ("c", "/x\x1b", "f\n", "f\x1b", "gs02\n", "J"):
      if keys == "J":
        view.filters = tui.Filters.initial(state)
      view.notify("previous error", "error")
      type_keys(view, state, keys)
      self.assertEqual(view.message_severity, "info")


class TuiNotesEditTests(unittest.TestCase):
  def test_ApplyEdit_AddEditRemoveNote(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "Example")  # S01, bare
      state = repo.state()
      # add via the note_new entry
      add_req = tui.EditRequest("note_new", "S01", "add a note", "")
      self.assertEqual(tui.apply_edit_result(state, add_req, "tried X").severity, "success")
      state = repo.state()
      self.assertTrue(state.index.require("S01").notes[-1].endswith("tried X"))
      # edit that note (index 0) to new text
      body = state.index.require("S01").notes[0]
      edit_req = tui.EditRequest("note", "S01", "note 1", body, index=0)
      self.assertEqual(tui.apply_edit_result(state, edit_req, "revised").severity, "success")
      self.assertEqual(repo.state().index.require("S01").notes, ["revised"])
      # editing to empty removes it
      state = repo.state()
      rm_req = tui.EditRequest("note", "S01", "note 1", "revised", index=0)
      self.assertEqual(tui.apply_edit_result(state, rm_req, "").severity, "success")
      self.assertEqual(repo.state().index.require("S01").notes, [])

  def test_Panel_PromotedItem_ShowsNotesAfterSections(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "Example")
      repo.run("promote", "S01")
      repo.run("note", "S01", "--text", "context")
      state = repo.state()
      ents = tui.entries(state, "S01")
      panel = tui.panel(state, "S01")
      add = next(l for l in panel if l.text == "+ add a note")
      self.assertEqual(add.entry, len(ents) - 1)
      self.assertTrue(any("context" in l.text for l in panel))
