"""Exercise navigation and filtered actions without requiring a terminal."""

from __future__ import annotations

import curses
import io
import re
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

import support
from slicer import model, relayrun, roles, store
from slicer import tui, tui_style, tui_wizard
from slicer.cli import main
from slicer.errors import StateError
from slicer.config import Config
from slicer.model import Index, Item
from slicer.store import State


class MemoryState(State):
  @property
  def dir(self) -> Path:
    raise AssertionError("in-memory TUI fixtures cannot access tracking files; use TempRepo for actions")

  def template(self, name: str) -> str:
    # The drift check plans the render; with no tracking files there is no render to plan.
    raise StateError("in-memory TUI fixtures have no render templates")


def example() -> State:
  return MemoryState(Path(__file__).resolve().parent, Config.from_dict({}), Index(items=[
    Item('S01', 'Finished setup', 'done', trees=['alpha'], importance=3, urgency=1),
    Item('S02', 'Search full title', 'open', short_title='Find things',
         trees=['alpha', 'beta'], pass_key='one', importance=3, urgency=2),
    Item('S03', 'Deferred work', 'parked', trees=['beta'], importance=1, urgency=3),
    Item('S04', 'Current work', 'started', pass_key='two', importance=2, urgency=2),
  ], preamble='Project context'))


def item_ids(view: tui.View) -> list[str]:
  return [r.target for r in view.listing if r.kind == tui.ITEM]


class TuiSortTests(unittest.TestCase):
  def test_InitialOrder_MatchesListRank(self) -> None:
    state = example()
    state.index.items[1].status = "started"
    state.index.items[3].status = "open"
    view = tui.View.initial(state)
    self.assertEqual(item_ids(view), ["S02", "S04", "S03"])

  def test_Picker_OffersEveryField_AndReorders(self) -> None:
    state = example()
    view = tui.View.initial(state)
    view.handle(state, "o")
    labels = [label for _, label in tui.SORT_FIELDS]
    self.assertEqual(labels, [
      "ranked order", "ID", "title", "status", "size", "importance",
      "urgency", "effective score", "effort",
    ])
    view.choice_at = next(i for i, (name, _) in enumerate(tui.SORT_FIELDS) if name == "id")
    view.handle(state, " ")
    view.handle(state, "\n")
    self.assertEqual(item_ids(view), ["S02", "S03", "S04"])
    self.assertEqual(view.sort_field, "id")
    self.assertFalse(view.sort_desc)

  def test_Direction_BlanksLast_AndTiesKeepStoredOrder(self) -> None:
    state = example()
    state.index.items[0].size = "L"
    state.index.items[1].size = ""
    state.index.items[1].short_title = ""
    state.index.items[1].title = "Same"
    state.index.items[2].size = "S"
    state.index.items[2].title = "Same"
    state.index.items[3].size = "M"
    state.index.items[1].effort = None
    state.index.items[2].effort = 1
    state.index.items[3].effort = 3
    view = tui.View(tui.Filters())
    view.sort_field = "effort"
    view.sort_desc = False
    view.refresh(state)
    self.assertEqual(item_ids(view), ["S03", "S04", "S01", "S02"])
    view.sort_desc = True
    view.refresh(state)
    self.assertEqual(item_ids(view), ["S04", "S03", "S01", "S02"])
    view.sort_field = "size"
    view.sort_desc = False
    view.refresh(state)
    self.assertEqual(item_ids(view)[-1], "S02")
    view.sort_desc = True
    view.refresh(state)
    self.assertEqual(item_ids(view)[-1], "S02")
    view.sort_field = "title"
    view.sort_desc = True
    view.refresh(state)
    titles = item_ids(view)
    same = [item_id for item_id in titles if state.index.require(item_id).display_title() == "Same"]
    self.assertEqual(same, ["S02", "S03"])

  def test_Sort_KeepsSelectionAndFilters_ClearDoesNotResetSort(self) -> None:
    state = example()
    view = tui.View.initial(state)
    view.filters.values["status"] = {"parked"}
    view.refresh(state)
    view.select((tui.ITEM, "S03"))
    view.handle(state, "o")
    view.handle(state, " ")
    view.choice_at = next(i for i, (name, _) in enumerate(tui.SORT_FIELDS) if name == "id")
    view.handle(state, "\n")
    self.assertEqual(view.target, "S03")
    self.assertTrue(view.filters.active)
    self.assertEqual(view.sort_field, "id")
    view.handle(state, "c")
    self.assertFalse(view.filters.active)
    self.assertEqual(view.sort_field, "id")
    self.assertEqual(item_ids(view)[0], "S01")

  def test_Sort_IsSessionOnly(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "B")
      repo.run("add", "A")
      before = repo.read(".slicer/index.json")
      state = repo.state()
      view = tui.View.initial(state)
      view.handle(state, "o")
      view.handle(state, " ")
      view.choice_at = next(i for i, (name, _) in enumerate(tui.SORT_FIELDS) if name == "title")
      view.handle(state, "\n")
      self.assertEqual(item_ids(view), ["S02", "S01"])
      self.assertEqual(repo.read(".slicer/index.json"), before)
      fresh = tui.View.initial(repo.state())
      self.assertEqual(fresh.sort_field, "ranked")
      self.assertEqual(item_ids(fresh), ["S01", "S02"])

  def test_Reorder_StaysOnStoredOrder_AndStillRefusesWhileFiltered(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "First")
      repo.run("add", "Second")
      state = repo.state()
      view = tui.View.initial(state)
      view.filters.values["status"] = {"open"}
      view.refresh(state)
      before = [item.id for item in state.index.items]
      view.handle(state, "J")
      self.assertEqual([item.id for item in state.index.items], before)
      self.assertIn("reordering disabled", view.message)
      view.handle(state, "c")
      view.sort_field = "id"
      view.sort_desc = True
      view.refresh(state)
      view.select((tui.ITEM, "S01"))
      view.handle(state, "J")
      self.assertEqual([item.id for item in repo.state().index.items], ["S02", "S01"])


def type_keys(view: tui.View, state: State, text: str) -> None:
  for key in text:
    view.handle(state, key)


class TuiFilteringTests(unittest.TestCase):
  def test_InitialView_Defaults_HidesDoneAndRetired(self) -> None:
    state = example()
    state.index.items.append(Item('S05', 'Old idea', 'retired', importance=3, urgency=3))
    view = tui.View.initial(state)
    self.assertEqual(item_ids(view), ['S04', 'S02', 'S03'])
    self.assertNotIn('S05', item_ids(view))
    self.assertTrue(view.status(state).startswith('3/5 items'))
    self.assertEqual([r.target for r in tui.rows(state) if r.kind == tui.ITEM],
                     ['S04', 'S02', 'S05', 'S01', 'S03'])
    view.handle(state, 'c')
    self.assertEqual(item_ids(view), ['S04', 'S02', 'S05', 'S01', 'S03'])
    self.assertFalse(view.filters.active)
    self.assertEqual(item_ids(tui.View.initial(state)), ['S04', 'S02', 'S03'])

  def test_InitialView_CustomDoneStatus_UsesConfiguration(self) -> None:
    state = example()
    state.config.statuses = {'todo': '-', 'shipped': 'done', 'waiting': 'waiting'}
    state.config.done_status = 'shipped'
    for item, status in zip(state.index.items, ['shipped', 'todo', 'waiting', 'todo']):
      item.status = status
    self.assertEqual(item_ids(tui.View.initial(state)), ['S02', 'S04', 'S03'])
    self.assertIn(('status', 'waiting'), tui.filter_choices(state))
    self.assertNotIn(('status', 'open'), tui.filter_choices(state))

  def test_Filters_MultipleGroups_UseOrWithinAndAcross(self) -> None:
    state = example()
    view = tui.View(tui.Filters())
    view.filters.values.update(status={'open', 'parked'}, tree={'alpha', 'beta'},
                               importance={'1', '3'}, urgency={'2', '3'})
    view.refresh(state)
    self.assertEqual(item_ids(view), ['S02', 'S03'])
    view.filters.values['pass'] = {'one'}
    view.refresh(state)
    self.assertEqual(item_ids(view), ['S02'])
    self.assertTrue(view.listing[0].text.lstrip().startswith('1'))

  def test_Filters_Flag_ListsDistinctValuesAndFiltersTheQueue(self) -> None:
    state = example()
    state.index.items[0].flags = ["security"]
    state.index.items[1].flags = ["security", "perf"]
    state.index.items[2].flags = ["perf"]
    choices = tui.filter_choices(state)
    flags = [value for group, value in choices if group == "flag"]
    self.assertEqual(flags, [None, "", "perf", "security"])

    view = tui.View(tui.Filters())
    view.filters.values["flag"] = {"security", "perf"}
    view.refresh(state)
    self.assertEqual(item_ids(view), ["S02", "S01", "S03"])
    view.filters.values["flag"] = {""}
    view.refresh(state)
    self.assertEqual(item_ids(view), ["S04"])
    view.filters.values.update(flag={"security"}, tree={"alpha"})
    view.refresh(state)
    self.assertEqual(item_ids(view), ["S02", "S01"])

    view = tui.View.initial(state)
    view.handle(state, "f")
    view.choice_at = tui.filter_choices(state).index(("flag", "security"))
    view.handle(state, " ")
    view.handle(state, "\n")
    self.assertEqual(item_ids(view), ["S02"])

  def test_Filters_MissingTreeAndPass_AreSelectable(self) -> None:
    state = example()
    view = tui.View(tui.Filters())
    view.filters.values['tree'] = {''}
    view.refresh(state)
    self.assertEqual(item_ids(view), ['S04'])
    view.filters.values['tree'].clear()
    view.filters.values['pass'] = {''}
    view.refresh(state)
    self.assertEqual(item_ids(view), ['S01', 'S03'])
    self.assertIn(('tree', ''), tui.filter_choices(state))
    self.assertIn(('pass', ''), tui.filter_choices(state))

  def test_Filters_Priority_UsesAssignedAxesNotInheritedScore(self) -> None:
    state = example()
    state.index.items[1].depends_on = ['S03']
    view = tui.View(tui.Filters())
    view.filters.values['importance'] = {'3'}
    view.refresh(state)
    self.assertEqual(item_ids(view), ['S02', 'S01'])
    view.filters.values['urgency'] = {'2'}
    view.refresh(state)
    self.assertEqual(item_ids(view), ['S02'])

  def test_Search_IncrementalLiteralAndCaseInsensitive_MatchesAllTitleForms(self) -> None:
    state = example()
    view = tui.View.initial(state)
    for query in ['FULL', 'find', 's02']:
      view.handle(state, 'c')
      view.handle(state, '/')
      type_keys(view, state, query)
      self.assertEqual(item_ids(view), ['S02'])
      view.handle(state, '\n')
    view.handle(state, 'c')
    view.handle(state, '/')
    type_keys(view, state, '.*')
    self.assertEqual(item_ids(view), [])
    self.assertTrue(any(r.kind == tui.PROSE for r in view.listing))
    self.assertTrue(view.status(state).startswith('0/4 items'))

  def test_Search_Cancel_RestoresQueryAndSelection(self) -> None:
    state = example()
    view = tui.View.initial(state)
    view.handle(state, 'j')
    previous = view.selected
    view.handle(state, '/')
    type_keys(view, state, 'deferred')
    self.assertEqual(view.target, 'S03')
    view.handle(state, '\x1b')
    self.assertEqual(view.selected, previous)
    self.assertEqual(view.filters.query, '')
    self.assertFalse(view.quit)
    view.handle(state, '/')
    type_keys(view, state, 'find')
    for _ in range(4):
      view.handle(state, 'KEY_BACKSPACE')
    view.handle(state, '\n')
    self.assertEqual(item_ids(view), ['S04', 'S02', 'S03'])

  def test_FilterPanel_ApplyCancelAndAny_AreIndependentOfLiveFilters(self) -> None:
    state = example()
    view = tui.View.initial(state)
    view.handle(state, 'f')
    view.choice_at = tui.filter_choices(state).index(('status', None))
    view.handle(state, ' ')
    self.assertEqual(item_ids(view), ['S04', 'S02', 'S03'])
    view.handle(state, '\x1b')
    self.assertEqual(item_ids(view), ['S04', 'S02', 'S03'])
    view.handle(state, 'f')
    view.handle(state, ' ')
    view.choice_at = tui.filter_choices(state).index(('status', 'done'))
    view.handle(state, ' ')
    view.handle(state, '\n')
    self.assertEqual(item_ids(view), ['S01'])
    view.handle(state, 'f')
    view.choice_at = tui.filter_choices(state).index(('status', 'done'))
    view.handle(state, ' ')
    view.handle(state, '\n')
    self.assertEqual(item_ids(view), ['S04', 'S02', 'S01', 'S03'])


class TuiNavigationTests(unittest.TestCase):
  def test_Jump_VisibleAndHiddenIds_SelectAndReveal(self) -> None:
    state = example()
    view = tui.View.initial(state)
    view.handle(state, 'g')
    type_keys(view, state, ' s04 ')
    view.handle(state, '\n')
    self.assertEqual(view.target, 'S04')
    self.assertTrue(view.filters.active)
    view.handle(state, 'g')
    type_keys(view, state, 's01')
    view.handle(state, '\n')
    self.assertEqual(view.target, 'S01')
    self.assertFalse(view.filters.active)
    self.assertIn('cleared', view.message)

  def test_Jump_UnknownEmptyAndCancelled_LeaveViewUnchanged(self) -> None:
    state = example()
    view = tui.View.initial(state)
    previous = view.selected
    for query, end in [('S99', '\n'), ('', '\n'), ('S01', '\x1b')]:
      view.handle(state, 'g')
      type_keys(view, state, query)
      view.handle(state, end)
      self.assertEqual(view.selected, previous)
      self.assertTrue(view.filters.active)
    self.assertFalse(view.quit)

  def test_Selection_DeletedTarget_PrefersFollowingThenPreceding(self) -> None:
    old = [tui.Row(tui.ITEM, key, key) for key in ('a', 'b', 'c')]
    self.assertEqual(tui.reconcile_selection(old, [old[0], old[2]], (tui.ITEM, 'b')),
                     (tui.ITEM, 'c'))
    self.assertEqual(tui.reconcile_selection(old, [old[0]], (tui.ITEM, 'b')),
                     (tui.ITEM, 'a'))
    self.assertIsNone(tui.reconcile_selection(old, [], (tui.ITEM, 'b')))

  def test_StatusAction_ItemDisappears_SelectsNextAndResetsDetail(self) -> None:
    with support.TempRepo() as repo:
      repo.run('init')
      repo.run('add', 'First')
      repo.run('add', 'Second')
      state = repo.state()
      view = tui.View.initial(state)
      view.entry_at = 3
      view.handle(state, 'd')
      self.assertEqual(repo.state().index.require('S01').status, 'done')
      self.assertEqual(view.target, 'S02')
      self.assertEqual(view.entry_at, 0)
      self.assertEqual(item_ids(view), ['S02'])

  def test_Reorder_Filtered_IsBlockedUntilClear(self) -> None:
    with support.TempRepo() as repo:
      repo.run('init')
      repo.run('add', 'First')
      repo.run('add', 'Second')
      state = repo.state()
      view = tui.View.initial(state)
      view.handle(state, 'J')
      self.assertEqual([it.id for it in state.index.items], ['S01', 'S02'])
      self.assertIn('press c', view.message)
      view.handle(state, 'c')
      view.handle(state, 'J')
      self.assertEqual([it.id for it in state.index.items], ['S02', 'S01'])
      self.assertEqual(view.target, 'S01')

  def test_Reorder_MoveToTop_PlacesItemFirst(self) -> None:
    with support.TempRepo() as repo:
      repo.run('init')
      for title in ('First', 'Second', 'Third'):
        repo.run('add', title)
      state = repo.state()
      view = tui.View.initial(state)
      view.handle(state, 'c')  # reordering is blocked while the default filter is active
      view.select((tui.ITEM, 'S03'))
      view.handle(state, 'T')
      self.assertEqual([it.id for it in state.index.items], ['S03', 'S01', 'S02'])
      self.assertEqual(view.target, 'S03')
      self.assertIn('moved to top', view.message)
      # Already-first is a no-op, not a spurious move.
      view.handle(state, 'T')
      self.assertEqual([it.id for it in state.index.items], ['S03', 'S01', 'S02'])
      self.assertEqual(view.message, 'already first')

  def test_MoveToPosition_Prompt_MovesItemThere(self) -> None:
    with support.TempRepo() as repo:
      repo.run('init')
      for title in ('First', 'Second', 'Third', 'Fourth'):
        repo.run('add', title)
      state = repo.state()
      view = tui.View.initial(state)
      view.handle(state, 'c')
      view.select((tui.ITEM, 'S01'))
      view.handle(state, 'M')
      self.assertEqual(view.mode, 'move')
      type_keys(view, state, '3')
      view.handle(state, '\n')
      self.assertEqual(view.mode, 'normal')
      self.assertEqual([it.id for it in state.index.items], ['S02', 'S03', 'S01', 'S04'])
      self.assertEqual(view.target, 'S01')
      self.assertIn('moved to 3', view.message)

  def test_MoveToPosition_CancelFilteredAndBadRange_DoNoHarm(self) -> None:
    with support.TempRepo() as repo:
      repo.run('init')
      for title in ('First', 'Second'):
        repo.run('add', title)
      state = repo.state()
      view = tui.View.initial(state)
      # Blocked while the default (status) filter is active.
      view.handle(state, 'M')
      self.assertEqual(view.mode, 'normal')
      self.assertIn('press c', view.message)
      view.handle(state, 'c')
      view.select((tui.ITEM, 'S01'))
      # Esc cancels without moving anything.
      view.handle(state, 'M')
      type_keys(view, state, '2')
      view.handle(state, '\x1b')
      self.assertEqual(view.mode, 'normal')
      self.assertEqual([it.id for it in state.index.items], ['S01', 'S02'])
      self.assertEqual(view.message, 'cancelled')
      # Out-of-range surfaces ops.move's error and leaves the queue untouched.
      view.handle(state, 'M')
      type_keys(view, state, '9')
      view.handle(state, '\n')
      self.assertEqual([it.id for it in state.index.items], ['S01', 'S02'])
      self.assertEqual(view.message_severity, 'error')
      self.assertIn('out of range', view.message)
      # Moving to the current position is a no-op: no save, no log entry.
      log_before = repo.read('.slicer/log.jsonl')
      view.handle(state, 'M')
      type_keys(view, state, '1')  # S01 is already first
      view.handle(state, '\n')
      self.assertEqual([it.id for it in state.index.items], ['S01', 'S02'])
      self.assertEqual(view.message, 'already at 1')
      self.assertNotEqual(view.message_severity, 'success')
      self.assertEqual(repo.read('.slicer/log.jsonl'), log_before)

  def test_EditTitle_Field_MovesAnUnchosenShortTitle(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "Old title")
      state = repo.state()
      request = tui.EditRequest(kind="field", target="S01", name="title", body="Old title")
      result = tui.apply_edit_result(state, request, "New title")
      self.assertEqual(result.severity, "success")
      item = repo.state().index.require("S01")
      self.assertEqual((item.title, item.short_title), ("New title", "New title"))

  def test_EditPriority_Field_AdjustsScore(self) -> None:
    with support.TempRepo() as repo:
      repo.run('init')
      repo.run('add', 'Item')
      state = repo.state()
      self.assertEqual(state.index.require('S01').score, 22)
      request = tui.EditRequest(kind='field', target='S01', name='importance', body='2')
      result = tui.apply_edit_result(state, request, '3')
      self.assertEqual(result.severity, 'success')
      self.assertEqual(repo.state().index.require('S01').score, 32)
      request = tui.EditRequest(kind='field', target='S01', name='urgency', body='2')
      tui.apply_edit_result(state, request, '3')
      self.assertEqual(repo.state().index.require('S01').score, 33)

  def test_ModalKeys_NormalActions_AreNotDispatched(self) -> None:
    state = example()
    for opening in ['/', 'g', 'f', '?']:
      view = tui.View.initial(state)
      view.handle(state, opening)
      with patch.object(tui, 'act', side_effect=AssertionError('normal action dispatched')):
        type_keys(view, state, 'qdasrJKTM')
      self.assertFalse(view.quit)
      self.assertNotEqual(view.mode, 'normal')
      view.handle(state, '\x1b')
      self.assertEqual(view.mode, 'normal')

  def test_NoSelection_GlobalControls_StillWork(self) -> None:
    state = example()
    state.index.items.clear()
    with patch.object(tui, 'filtered_rows', return_value=[]):
      view = tui.View.initial(state)
      view.handle(state, 'd')
      self.assertEqual(view.message, 'no item selected')
      result = view.handle(state, 'a')
      self.assertEqual(result.edit.kind, 'new')
      for key in ['f', '\x1b', '/', '\x1b', '?', '\x1b', 'c', 'q']:
        view.handle(state, key)
      self.assertTrue(view.quit)

  def test_Help_ArrowKeysScrollAndEscape_LeaveSelectionUnchanged(self) -> None:
    state = example()
    view = tui.View.initial(state)
    previous = view.selected
    view.handle(state, '?')
    view.handle(state, 'KEY_DOWN')
    self.assertEqual(view.help_at, 1)
    view.handle(state, 'KEY_UP')
    self.assertEqual(view.help_at, 0)
    view.handle(state, '?')
    self.assertEqual(view.selected, previous)
    self.assertEqual(view.mode, 'normal')


class Screen:
  def __init__(self, height: int, width: int, resizing: bool = False) -> None:
    self.height, self.width, self.resizing = height, width, resizing
    self.writes = []

  def getmaxyx(self):
    return self.height, self.width

  def erase(self):
    self.writes.clear()

  def refresh(self):
    pass

  def addnstr(self, y, x, text, count, attr):
    assert 0 <= y < self.height and 0 <= x < self.width
    assert tui_style.cell_width(text) < self.width - x
    assert count >= len(text)
    if self.resizing:
      self.height = max(0, self.height - 1)
      self.width = max(0, self.width - 1)
      raise curses.error('resized during drawing')
    self.writes.append((y, text[:count]))


class TuiDrawingTests(unittest.TestCase):
  def test_Draw_NormalView_CommonShortcutsPersistWithFeedback(self) -> None:
    hints = ['Tab panes', 'e edit', 'a add', 's start', 'd done', 'h handoff',
             '/ search', 'f filters', 'c show all', 'g jump', 'j/k move', '? help', 'q quit', 'v view', 'b board']
    state = example()
    for height, width, rows in [(10, 80, 2), (24, 160, 1)]:
      for focus in ['left', 'right']:
        for severity in ['success', 'error']:
          with self.subTest(size=(height, width), focus=focus, severity=severity):
            view = tui.View.initial(state)
            view.focus = focus
            view.notify('action feedback', severity)
            screen = Screen(height, width)
            tui.draw(screen, state, view)
            strip = [(y, text) for y, text in screen.writes if y >= height - rows]
            self.assertEqual(len(strip), rows)
            text = '  '.join(line for _, line in strip)
            for hint in hints:
              self.assertIn(hint, text)
            self.assertIn((height - rows - 1, view.feedback()), screen.writes)
            # The status line is clipped to the screen like every other line.
            self.assertIn((height - rows - 2, view.status(state)[:width - 1]), screen.writes)

  def test_Shortcuts_BindingLabelChanges_AreReflectedInHints(self) -> None:
    bindings = tuple(tui.Binding(('x',), 'x', b.action, b.description)
                     if b.action == 'edit' else b for b in tui.BINDINGS)
    with patch.object(tui, 'BINDINGS', bindings):
      self.assertIn('x edit', ' '.join(tui.shortcut_lines(80)))
      self.assertEqual(tui.key_action('x'), 'edit')
    for actions, _ in tui.SHORTCUTS:
      for action in actions:
        binding = next(b for b in tui.BINDINGS if b.action == action)
        self.assertEqual(tui.key_action(binding.keys[0]), action)

  def test_Draw_ModalViews_DoNotAdvertiseNormalShortcuts(self) -> None:
    state = example()
    for mode in ['search', 'jump', 'move', 'filter', 'help']:
      with self.subTest(mode=mode):
        view = tui.View.initial(state)
        view.mode, view.draft = mode, view.filters.copy()
        screen = Screen(10, 80)
        tui.draw(screen, state, view)
        text = '\n'.join(text for _, text in screen.writes)
        self.assertNotIn('Tab panes', text)
        self.assertIn({'search': 'Search (Enter accepts, Esc cancels)',
                       'jump': 'Jump to ID (Enter accepts, Esc cancels)',
                       'move': 'Move to position (Enter accepts, Esc cancels)',
                       'filter': 'Space toggle, Enter apply, Esc cancel',
                       'help': 'Help: j/k scroll, ? or Esc close'}[mode], text)

  def test_Draw_ShortcutRows_LeaveSelectedQueueAndDetailVisible(self) -> None:
    state = example()
    view = tui.View.initial(state)
    for _ in range(len(view.listing)):
      view.handle(state, 'j')
    screen = Screen(10, 80)
    tui.draw(screen, state, view)
    self.assertTrue(any(text.startswith('> ') for y, text in screen.writes if 1 <= y <= 5))
    self.assertGreater(view.scroll, 0)
    view.select((tui.ITEM, 'S02'))
    view.refresh(state)
    view.focus = 'right'
    view.entry_at = len(tui.entries(state, view.target)) - 1
    tui.draw(screen, state, view)
    selected = tui.entries(state, view.target)[view.entry_at].name
    self.assertTrue(any(text.startswith('> ') and selected in text
                        for y, text in screen.writes if 1 <= y <= 5))

  def test_Draw_NoMatchesAndResize_PreserveHintsAndSession(self) -> None:
    state = example()
    view = tui.View.initial(state)
    view.handle(state, '/')
    type_keys(view, state, 'no match')
    view.handle(state, '\n')
    screen = Screen(10, 80)
    tui.draw(screen, state, view)
    text = '\n'.join(text for _, text in screen.writes)
    self.assertIn('No matching items', text)
    self.assertIn('Tab panes', text)
    screen.height, screen.width = 9, 79
    view.handle(state, 'KEY_RESIZE')
    tui.draw(screen, state, view)
    text = '\n'.join(text for _, text in screen.writes)
    self.assertIn('Resize to at least 80x10', text)
    self.assertNotIn('Tab panes', text)
    screen.height, screen.width = 10, 80
    tui.draw(screen, state, view)
    text = '\n'.join(text for _, text in screen.writes)
    self.assertIn('No matching items', text)
    self.assertIn('Tab panes', text)

  def test_Draw_SmallAndResizedScreens_AllModesStayInBounds(self) -> None:
    state = example()
    for height, width in [(0, 0), (1, 1), (2, 8), (5, 24), (24, 100)]:
      for mode in ['normal', 'search', 'jump', 'move', 'filter', 'help', 'board']:
        for resizing in [False, True]:
          with self.subTest(size=(height, width), mode=mode, resizing=resizing):
            view = tui.View.initial(state)
            view.mode, view.draft = mode, view.filters.copy()
            view.handle(state, 'KEY_RESIZE')
            tui.draw(Screen(height, width, resizing), state, view)

  def test_Draw_NoMatches_ShowsMessageProseCountsAndPrompt(self) -> None:
    state = example()
    view = tui.View.initial(state)
    view.handle(state, '/')
    type_keys(view, state, 'no match')
    screen = Screen(24, 120)
    tui.draw(screen, state, view)
    output = '\n'.join(text for _, text in screen.writes)
    self.assertIn('No matching items', output)
    self.assertIn('preamble', output)
    self.assertIn('0/4 items', output)
    self.assertIn('Search (Enter accepts, Esc cancels): no match', output)


class NotesPanelTests(unittest.TestCase):
  def test_Entries_IncludeANotePerNotePlusATrailingAddEntry(self) -> None:
    state = example()
    item = state.index.require("S02")
    item.note_records = [model.NoteRecord(str(i), "", text, "", None)
                         for i, text in enumerate(["**2026-01-01** — first", "second"])]
    ents = tui.entries(state, "S02")
    notes = [e for e in ents if e.kind == "note"]
    self.assertEqual([e.index for e in notes], [0, 1])
    self.assertEqual(ents[-1].kind, "note_new")
    self.assertIsNone(ents[-1].index)

  def test_Panel_AddLineTagMatchesTheLastEntry(self) -> None:
    state = example()
    item = state.index.require("S02")
    item.note_records = [model.NoteRecord("n1", "", "a note", "", None)]
    ents = tui.entries(state, "S02")
    panel = tui.panel(state, "S02")
    self.assertTrue(any(l.role == "heading" and l.text == "Notes" for l in panel))
    add = next(l for l in panel if l.text == "+ add a note")
    self.assertEqual(add.entry, len(ents) - 1)
    # the note's body line is tagged with its own entry index
    note_entry = next(e_i for e_i, e in enumerate(ents) if e.kind == "note")
    self.assertTrue(any(l.text == "a note" and l.entry == note_entry for l in panel))


class SiblingReviewDisplayTests(unittest.TestCase):
  def siblings(self) -> dict[str, list[dict[str, str]]]:
    return {"S02": [{"worktree": "other", "owner": "", "status": "review"}]}

  def test_View_SiblingReview_PresetsFiltersAndRefreshShareOneSnapshot(self) -> None:
    state = example()
    with patch.object(store, "sibling_work", return_value=(self.siblings(), [])) as discover:
      view = tui.View.initial(state)
      discover.assert_called_once_with(state.root, state.index)
      self.assertIn("S02", item_ids(view))
      view.handle(state, "v")
      self.assertNotIn("S02", item_ids(view))
      view.handle(state, "v")
      self.assertEqual(item_ids(view), ["S02"])
      view.select((tui.ITEM, "S02"))
      screen = Screen(24, 100)
      discover.reset_mock()
      tui.draw(screen, state, view)
      discover.assert_called_once_with(state.root, state.index)
      self.assertIn("status          review", "\n".join(text for _, text in screen.writes))
      discover.return_value = ({}, [])
      view.refresh(state)
      self.assertNotIn("S02", item_ids(view))
      self.assertEqual(state.index.require("S02").status, "open")

  def test_Presentation_CustomReviewLabel_StylesAlignsAndSortsWithoutWrites(self) -> None:
    state = example()
    state.config.statuses["review"] = "ready-for-review"
    siblings = self.siblings()
    state.index.require("S02").depends_on = ["S04"]
    before = state.index.to_dict()
    items = [state.index.require(i) for i in ("S02", "S04")]
    with patch.object(store, "sibling_work", side_effect=AssertionError("pure helper did IO")):
      rows = tui.item_rows(state, items, siblings)
      self.assertEqual(rows[0].status, "review")
      self.assertIn("ready-for-review", rows[0].text)
      self.assertEqual(rows[0].text.index("P:"), rows[1].text.index("P:"))
      self.assertEqual(tui_style.status_role(rows[0].status, rows[0].blocked, state.config), "review")
      line = next(l for l in tui.panel(state, "S02", siblings) if l.text.startswith("status"))
      self.assertEqual(line.text, "status          ready-for-review")
      self.assertEqual(line.role, "review")
      state.config.statuses["review"] = "zzz"
      self.assertEqual([i.id for i in tui.sort_items(state, items, "status", False, siblings)],
                       ["S04", "S02"])
      self.assertEqual([i.id for i in tui.sort_items(state, items, "status", True, siblings)],
                       ["S02", "S04"])
      for selected, expected in (({"review"}, ["S02"]), ({"open"}, ["S02"]),
                                 ({"started", "reviewing"}, ["S04"])):
        filters = tui.Filters()
        filters.values["status"] = selected
        self.assertEqual([r.item_id for r in tui.filtered_rows(state, filters, elsewhere=siblings)
                          if r.kind == tui.ITEM], expected)
    self.assertEqual(state.index.to_dict(), before)

  def test_DisplayedStatus_NonOpenAndOtherSiblingKeys_KeepStoredStatus(self) -> None:
    state = example()
    item = state.index.require("S02")
    for status in ("open", "started", "parked", "review", "later"):
      for sibling_status in ("review", "started", "reviewing", "done"):
        with self.subTest(status=status, sibling_status=sibling_status):
          item.status = status
          siblings = [{"status": sibling_status}]
          expected = "review" if status == "open" and sibling_status == "review" else status
          self.assertEqual(store.displayed_status(state.config, item, siblings), expected)
    item.status = "open"
    state.config.review_status = ""
    self.assertEqual(store.displayed_status(state.config, item, [{"status": "review"}]), "open")


class ClaimDisplayTests(unittest.TestCase):
  def test_Panel_ClaimOwnerOrDash_IsNotAnEntry(self) -> None:
    state = example()
    state.index.require("S02").claim_owner = "Ada Lovelace"
    claimed = tui.panel(state, "S02")
    line = next(l for l in claimed if l.text.startswith("claim"))
    self.assertEqual(line.text, "claim           Ada Lovelace")
    self.assertIsNone(line.entry)
    self.assertEqual(line.role, "normal")
    plain = tui.panel(state, "S04")
    self.assertTrue(any(l.text == "claim           -" and l.entry is None for l in plain))
    self.assertNotIn("claim", [e.name for e in tui.entries(state, "S02")])

  def test_Rows_LongestVisibleLabel_KeepsColumnsAligned(self) -> None:
    state = example()
    state.index.require("S04").status = "reviewing"
    state.index.require("S02").status = "started"
    rows = tui.item_rows(state, list(state.index.items))
    by_id = {r.target: r for r in rows}
    wide = by_id["S02"].text.index("P:")
    self.assertEqual(by_id["S04"].text.index("P:"), wide)
    self.assertLess(by_id["S04"].text.index("reviewing"), wide)
    short = tui.item_rows(state, [state.index.require("S02")])
    self.assertEqual(wide - short[0].text.index("P:"), len("reviewing") - 7)

  def test_Rows_BlockedReviewAndParked_ShowMarkerAndClaimOwner(self) -> None:
    state = example()
    state.index.require("S02").claim_owner = "Ada Lovelace"
    state.index.require("S03").depends_on = ["S02"]
    state.index.items.append(Item(
      "S05", "Ready for review", "review", depends_on=["S02"], claim_owner="Ada Lovelace",
    ))
    state.index.require("S04").status = "reviewing"
    rows = tui.item_rows(state, [
      state.index.require(i) for i in ("S02", "S03", "S04", "S05")
    ])
    by_id = {r.target: r for r in rows}
    self.assertIn("!", by_id["S03"].text)
    self.assertIn("!", by_id["S05"].text)
    self.assertNotIn("!", by_id["S02"].text)
    self.assertNotIn("!", by_id["S04"].text)
    self.assertIn("Ada Lov…", by_id["S02"].text)
    self.assertIn("Ada Lov…", by_id["S05"].text)
    line = next(l for l in tui.panel(state, "S02") if l.text.startswith("claim"))
    self.assertEqual(line.text, "claim           Ada Lovelace")
    self.assertEqual(by_id["S03"].text.split()[3], "-")
    for row in rows:
      self.assertNotIn("claim", row.text)
    self.assertEqual(tui.key_action("v"), "view")
    self.assertEqual([b.action for b in tui.BINDINGS if "v" in b.keys], ["view"])


def _list_row(text: str, item_id: str) -> str:
  return next(line for line in text.splitlines() if item_id in line.split())


def _cli(root: Path, *args: str) -> int:
  """Run one command in-process against `root`, as the worktree-claims tests do."""
  with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
    return main(["--root", str(root), *args])


class SiblingClaimQueueTests(unittest.TestCase):
  def test_ItemRows_SiblingClaim_ShowsWtName(self) -> None:
    state = example()
    siblings = {"S02": [{"worktree": "other", "owner": "", "status": "started"}]}
    row = tui.item_rows(state, [state.index.require("S02")], siblings)[0]
    self.assertEqual(row.text.split()[3], "wt:other")

  def test_ItemRows_TwoSiblings_ShowsPlusCount(self) -> None:
    state = example()
    siblings = {"S02": [{"worktree": "a", "owner": "", "status": "started"},
                        {"worktree": "b", "owner": "", "status": "started"}]}
    row = tui.item_rows(state, [state.index.require("S02")], siblings)[0]
    self.assertEqual(row.text.split()[3], "wt:a+1")

  def test_ItemRows_LocalStartedNoClaim_ShowsStar(self) -> None:
    state = example()
    row = tui.item_rows(state, [state.index.require("S04")])[0]
    self.assertEqual(row.text.split()[3], "*")

  def test_ItemRows_LongSiblingClaim_CappedAtEightCells(self) -> None:
    state = example()
    item = state.index.require("S02")
    siblings = {"S02": [{"worktree": "other-long-name", "owner": "", "status": "started"}]}
    self.assertEqual(store.claim_cell(state.config, item, siblings["S02"]), "wt:other-long-name")
    row = tui.item_rows(state, [item], siblings)[0]
    self.assertEqual(row.text.split()[3], "wt:othe…")
    # The column is sized to the capped cell, so the score starts where an 8-cell claim ends.
    self.assertEqual(row.text.index("P:"), 31)
    aligned = tui.item_rows(state, [item, state.index.require("S04")], siblings)
    self.assertEqual([r.text.index("P:") for r in aligned], [31, 31])

  def test_ItemRows_NarrowPane_DropsClaimAndSizeColumns(self) -> None:
    state = example()
    item = state.index.require("S02")
    item.size = "M"
    siblings = {"S02": [{"worktree": "other", "owner": "", "status": "started"}]}
    narrow = tui.item_rows(state, [item], siblings, pane_width=tui.NARROW_PANE - 1)[0]
    at_threshold = tui.item_rows(state, [item], siblings, pane_width=tui.NARROW_PANE)[0]
    self.assertNotIn("M", narrow.text.split())
    self.assertNotIn("wt:other", narrow.text)
    self.assertIn("M", at_threshold.text.split())
    self.assertIn("wt:other", at_threshold.text)
    self.assertEqual(tui.item_rows(state, [item], siblings)[0].text, at_threshold.text)

  def test_FilteredRows_SiblingOnlyItem_ListedAfterSeparatorAndNotSelectable(self) -> None:
    state = example()
    only = [{"id": "S09", "title": "Sibling only", "worktree": "other", "status": "open"}]
    rows = tui.filtered_rows(state, tui.Filters(), only_sibling=only)
    self.assertIn("only in a sibling", rows[-2].text)
    self.assertEqual(rows[-2].kind, tui.SEPARATOR)
    self.assertIn("S09", rows[-1].text)
    self.assertIn("other", rows[-1].text)
    self.assertIn("Sibling only", rows[-1].text)
    self.assertFalse(rows[-2].selectable)
    self.assertFalse(rows[-1].selectable)
    hidden = tui.filtered_rows(state, tui.Filters(query="no such text"), only_sibling=only)
    self.assertIn("S09", hidden[-1].text)
    with patch.object(store, "sibling_work", return_value=({}, only)):
      view = tui.View.initial(state)
      self.assertIn("S09", view.listing[-1].text)
      for _ in range(len(view.listing) + 3):
        view.handle(state, "j")
      last = next(row for row in reversed(view.listing) if row.selectable)
      self.assertEqual(view.selected, (last.kind, last.target))

  def test_ClaimCell_SameOutputAsList(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      for title in ("one", "two", "three", "four"):
        repo.run("add", title)
      repo.commit("base")
      sibling = repo.root / "siblings" / "sib"
      sibling.parent.mkdir()
      made = repo._git("worktree", "add", "-q", "-b", "sib", str(sibling), "HEAD")
      self.assertEqual(made.returncode, 0, made.stderr)
      self.assertEqual(_cli(sibling, "start", "S01"), 0)
      self.assertEqual(repo.run("set", "S02", "--status", "started")[0], 0)
      self.assertEqual(repo.run("start", "S03", "--owner", "Ada")[0], 0)
      code, out, err = repo.run("list")
      self.assertEqual(code, 0, err)
      view = tui.View.initial(repo.state())
      rows = {row.target: row for row in view.listing if row.kind == tui.ITEM}
      ids = ["S01", "S02", "S03", "S04"]
      self.assertEqual({i: rows[i].text.split()[3] for i in ids},
                       {i: _list_row(out, i).split()[3] for i in ids})
      self.assertEqual({i: rows[i].text.split()[2] for i in ids},
                       {i: _list_row(out, i).split()[2] for i in ids})
      self.assertEqual(sorted({rows[i].text.split()[3] for i in ids}),
                       ["*", "-", "Ada", "wt:sib"])
      self.assertEqual({rows[i].text.index("P:") for i in ids}, {rows["S01"].text.index("P:")})

  def test_Draw_SiblingClaimAt80Columns_DropsClaimAndSizeKeepsTitle(self) -> None:
    state = example()
    state.index.require("S02").size = "M"
    siblings = {"S02": [{"worktree": "other-long-name", "owner": "", "status": "started"}]}
    with patch.object(store, "sibling_work", return_value=(siblings, [])):
      view = tui.View.initial(state)
      screen = Screen(24, 80)
      tui.draw(screen, state, view)
    line = next(text for _, text in screen.writes if "S02" in text.split())
    self.assertNotIn("wt:", line)  # a queue pane under 50 cells drops the claim column
    self.assertNotIn("M", line.split())  # and the size column
    self.assertIn("Find thing", line)  # the title keeps at least 10 cells at 80 columns

  def test_Draw_SiblingClaimAt110Columns_ShowsCappedClaim(self) -> None:
    state = example()
    state.index.require("S02").size = "M"
    siblings = {"S02": [{"worktree": "other-long-name", "owner": "", "status": "started"}]}
    with patch.object(store, "sibling_work", return_value=(siblings, [])):
      view = tui.View.initial(state)
      screen = Screen(24, 110)
      tui.draw(screen, state, view)
    line = next(text for _, text in screen.writes if "S02" in text.split())
    self.assertIn("wt:othe…", line)
    self.assertIn("M", line.split())

  def test_Draw_SiblingOnlyBlockAt80Columns_StillShows(self) -> None:
    state = example()
    only = [{"id": "S09", "title": "Sibling only", "worktree": "other", "status": "open"}]
    with patch.object(store, "sibling_work", return_value=({}, only)):
      view = tui.View.initial(state)
      screen = Screen(24, 80)
      tui.draw(screen, state, view)
    text = "\n".join(line for _, line in screen.writes)
    self.assertIn("only in a sibling", text)
    self.assertIn("S09", text.split())

  def test_Draw_SiblingOnlyBlock_LongQueue_VisibleAfterLastRow(self) -> None:
    state = example()
    only = [{"id": "S09", "title": "Sibling only", "worktree": "other", "status": "open"}]
    with patch.object(store, "sibling_work", return_value=({}, only)):
      view = tui.View.initial(state)
      for _ in range(len(view.listing)):
        view.handle(state, "j")
      screen = Screen(12, 110)
      tui.draw(screen, state, view)
    text = "\n".join(line for _, line in screen.writes)
    self.assertIn("only in a sibling", text)
    self.assertIn("S09", text.split())


class QueueViewTests(unittest.TestCase):
  def test_View_CyclesInWorkAndReview_AndReturnsToTheInitialSet(self) -> None:
    state = example()
    view = tui.View.initial(state)
    view.filters.query = "Find"
    view.filters.values["status"] = {"parked"}
    view.handle(state, "v")
    self.assertEqual(view.view_preset, "in-work")
    self.assertEqual(view.filters.values["status"], state.config.in_work())
    self.assertEqual(view.filters.query, "Find")
    self.assertIn("view=in-work", view.status(state))
    self.assertEqual(item_ids(view), [])  # the query does not match the started title
    view.filters.query = ""
    view.handle(state, "v")
    self.assertEqual(view.view_preset, "review")
    self.assertEqual(view.filters.values["status"], {state.config.review_status})
    self.assertIn("view=review", view.status(state))
    view.handle(state, "v")
    self.assertEqual(view.view_preset, "")
    self.assertEqual(
      view.filters.values["status"], tui.Filters.initial(state).values["status"],
    )
    self.assertNotIn("view=", view.status(state))

  def test_View_FilterApplyDropsTheName_AndShowsThePresetChecked(self) -> None:
    state = example()
    view = tui.View.initial(state)
    view.handle(state, "v")
    view.handle(state, "f")
    screen = Screen(24, 80)
    tui.draw(screen, state, view)
    text = "\n".join(line for _, line in screen.writes)
    self.assertIn("[x] status      started", text)
    self.assertIn("[x] status      reviewing", text)
    self.assertIn("[ ] status      open", text)
    view.handle(state, "\n")
    self.assertEqual(view.view_preset, "")
    self.assertEqual(view.filters.values["status"], state.config.in_work())
    self.assertNotIn("view=", view.status(state))

  def test_View_ClearAndHiddenJump_DropThePreset(self) -> None:
    state = example()
    view = tui.View.initial(state)
    view.handle(state, "v")
    view.handle(state, "c")
    self.assertEqual(view.view_preset, "")
    self.assertFalse(view.filters.values["status"])
    view.handle(state, "v")
    view.handle(state, "g")
    type_keys(view, state, "S02")
    view.handle(state, "\n")
    self.assertEqual(view.view_preset, "")
    self.assertIn("cleared", view.message)
    self.assertEqual(view.target, "S02")

  def test_View_SkipsADisabledRole_AndSaysSoWhenBothAreUnavailable(self) -> None:
    state = example()
    view = tui.View.initial(state)
    state.config.review_status = ""
    view.handle(state, "v")
    self.assertEqual(view.view_preset, "in-work")
    view.handle(state, "v")
    self.assertEqual(view.view_preset, "")
    state.config.started_status = ""
    state.config.reviewing_status = ""
    state.config.review_status = "review"
    view.handle(state, "v")
    self.assertEqual(view.view_preset, "review")
    view.handle(state, "v")
    self.assertEqual(view.view_preset, "")
    state.config.review_status = ""
    before = set(view.filters.values["status"])
    view.handle(state, "v")
    self.assertIn("no in-work or review status", view.message)
    self.assertEqual(view.view_preset, "")
    self.assertEqual(view.filters.values["status"], before)

  def test_View_DisablesReorder_AndHelpListsTheKey(self) -> None:
    state = example()
    view = tui.View.initial(state)
    view.handle(state, "v")
    view.handle(state, "J")
    self.assertIn("reordering disabled", view.message)
    view.handle(state, "K")
    self.assertIn("reordering disabled", view.message)
    self.assertTrue(any(line.startswith("v ") and "Cycle" in line for line in tui.help_lines()))


class TuiStatusLineTests(unittest.TestCase):
  def rendered_repo(self, repo: support.TempRepo) -> None:
    repo.run('init')
    repo.run('add', 'First')
    repo.run('add', 'Second')
    repo.run('promote', 'S01')
    repo.run('render')

  def add_note_in_tui(self, state: State, view: tui.View) -> None:
    view.select((tui.ITEM, 'S01'))
    view.handle(state, '\t')
    view.entry_at = next(i for i, e in enumerate(tui.entries(state, 'S01')) if e.kind == 'note_new')
    request = view.handle(state, 'e').edit
    view.accept(tui.apply_edit_result(state, request, 'checked by hand'))
    view.refresh(state)

  def test_Status_ShowsDonePercent(self) -> None:
    state = example()  # one of four items is done
    view = tui.View.initial(state)
    self.assertIn('25% done', view.status(state))

  def test_Status_AfterNoteEdit_ShowsRenderStale(self) -> None:
    with support.TempRepo() as repo:
      self.rendered_repo(repo)
      state = repo.state()
      view = tui.View.initial(state)
      self.assertNotIn('render stale', view.status(state))
      self.add_note_in_tui(state, view)
      self.assertIn('press r to render', view.message)
      self.assertIn('render stale', view.status(state))

  def test_Status_AfterRenderKey_ClearsStale(self) -> None:
    with support.TempRepo() as repo:
      self.rendered_repo(repo)
      state = repo.state()
      view = tui.View.initial(state)
      self.add_note_in_tui(state, view)
      self.assertIn('render stale', view.status(state))
      view.handle(state, 'r')
      self.assertIn('rendered', view.message)
      self.assertNotIn('render stale', view.status(state))

  def test_Status_AfterHandoff_ShowsRenderStale(self) -> None:
    with support.TempRepo() as repo:
      self.rendered_repo(repo)
      repo.run('start', 'S01')
      repo.run('render')
      state = repo.state()
      view = tui.View.initial(state)
      self.assertNotIn('render stale', view.status(state))
      view.select((tui.ITEM, 'S01'))
      view.handle(state, 'h')
      type_keys(view, state, 'ready for review')
      view.handle(state, '\n')
      self.assertIn('S01 handed off', view.message)
      self.assertIn('render stale', view.status(state))

  def test_Status_AfterWizardSave_NotRenderStale(self) -> None:
    with support.TempRepo() as repo:
      repo.run('init')
      repo.run('render')
      state = repo.state()
      view = tui.View.initial(state)
      self.assertNotIn('render stale', view.status(state))
      # The same key sequence test_tui_wizard.draft sends; that module imports this one.
      wizard = tui_wizard.Wizard(list(state.config.sections))
      for value in ('', 'First', 'M', 'core, ui', 'F1', '3', '1', 'Phase one', ''):
        for _ in wizard.text:
          wizard.handle('KEY_BACKSPACE')
        for key in value:
          wizard.handle(key)
        wizard.handle('\n')
      while wizard.mode == 'field':
        wizard.handle('\n')
      wizard.handle('n')
      view.wizard, view.mode = wizard, 'wizard'
      view.wizard.mode = 'review'
      view.wizard.review_at = len(view.wizard.review_rows()) - 1
      view.handle(state, '\n')
      self.assertIsNone(view.wizard)
      self.assertIn('rendered roadmap', view.message)
      view.refresh(state)
      self.assertNotIn('render stale', view.status(state))

  def test_Status_AfterWizardSave_RenderFails_ShowsRenderStale(self) -> None:
    with support.TempRepo() as repo:
      repo.run('init')
      repo.run('render')
      state = repo.state()
      view = tui.View.initial(state)
      self.assertNotIn('render stale', view.status(state))
      # The same key sequence test_tui_wizard.draft sends; that module imports this one.
      wizard = tui_wizard.Wizard(list(state.config.sections))
      for value in ('', 'First', 'M', 'core, ui', 'F1', '3', '1', 'Phase one', ''):
        for _ in wizard.text:
          wizard.handle('KEY_BACKSPACE')
        for key in value:
          wizard.handle(key)
        wizard.handle('\n')
      while wizard.mode == 'field':
        wizard.handle('\n')
      wizard.handle('n')
      view.wizard, view.mode = wizard, 'wizard'
      view.wizard.mode = 'review'
      view.wizard.review_at = len(view.wizard.review_rows()) - 1
      with patch.object(tui, 'act', return_value=tui.ActResult('render unavailable', severity='error')) as act:
        view.handle(state, '\n')
      act.assert_called_once_with(state, 'r', '')
      self.assertIsNone(view.wizard)
      self.assertIn('render failed: render unavailable', view.message)
      view.refresh(state)
      self.assertIn('render stale', view.status(state))

  def test_Status_AfterMove_ShowsRenderStale(self) -> None:
    with support.TempRepo() as repo:
      self.rendered_repo(repo)
      state = repo.state()
      view = tui.View.initial(state)
      self.assertNotIn('render stale', view.status(state))
      view.handle(state, 'c')
      view.select((tui.ITEM, 'S01'))
      view.handle(state, 'M')
      type_keys(view, state, '2')
      view.handle(state, '\n')
      self.assertEqual([item.id for item in state.index.items], ['S02', 'S01'])
      self.assertIn('S01 moved to 2', view.message)
      view.refresh(state)
      self.assertIn('render stale', view.status(state))

  def test_Refresh_NavigationKey_DoesNotRecomputeDrift(self) -> None:
    with support.TempRepo() as repo:
      self.rendered_repo(repo)
      state = repo.state()
      with patch.object(tui.render, 'compare', wraps=tui.render.compare) as compare:
        view = tui.View.initial(state)
        self.assertEqual(compare.call_count, 1)
        view.handle(state, 'j')
        view.handle(state, 'k')
        view.handle(state, 'KEY_DOWN')
        view.refresh(state)
        self.assertEqual(compare.call_count, 1)


class ReviewKeyTests(unittest.TestCase):
  def repo_with_started(self, repo: support.TempRepo, *titles: str) -> None:
    repo.run('init')
    for title in titles:
      repo.run('add', title)
    repo.run('promote', 'S01')
    repo.run('start', 'S01')

  def test_Handoff_NoteOrEmptyNote_HandsOffTheSelectedItemOnly(self) -> None:
    for note in ('', 'ready for review'):
      with self.subTest(note=note), support.TempRepo() as repo:
        self.repo_with_started(repo, 'First', 'Second')
        state = repo.state()
        view = tui.View.initial(state)
        self.assertEqual(view.target, 'S01')
        with patch.object(tui.ops, 'handoff', wraps=tui.ops.handoff) as op:
          view.handle(state, 'h')
          self.assertEqual(view.mode, 'note')
          type_keys(view, state, note)
          view.handle(state, '\n')
        op.assert_called_once_with(state, 'S01', note=note)
        fresh = repo.state()
        self.assertEqual(fresh.index.require('S01').status, 'review')
        self.assertEqual(fresh.index.require('S02').status, 'open')
        self.assertEqual(view.mode, 'normal')
        self.assertIn('S01 handed off', view.message)
        entry = fresh.history()[-1]
        self.assertEqual((entry.item, entry.action), ('S01', 'handoff'))
        if note:
          self.assertIn(note, entry.note)

  def test_Handoff_Cancel_WritesNothing(self) -> None:
    with support.TempRepo() as repo:
      self.repo_with_started(repo, 'First')
      before = repo.read('.slicer/index.json'), repo.read('.slicer/log.jsonl')
      state = repo.state()
      view = tui.View.initial(state)
      view.handle(state, 'h')
      type_keys(view, state, 'never saved')
      view.handle(state, '\x1b')
      self.assertEqual(view.mode, 'normal')
      self.assertIn('cancelled', view.message)
      self.assertEqual(before, (repo.read('.slicer/index.json'), repo.read('.slicer/log.jsonl')))

  def test_Reject_RequiresANote_AndSendsTheSelectedItemBack(self) -> None:
    with support.TempRepo() as repo:
      self.repo_with_started(repo, 'First', 'Second')
      repo.run('handoff', 'S01')
      before = repo.read('.slicer/index.json'), repo.read('.slicer/log.jsonl')
      state = repo.state()
      view = tui.View.initial(state)
      view.select((tui.ITEM, 'S01'))
      view.handle(state, 'x')
      self.assertEqual(view.mode, 'note')
      view.handle(state, '\n')
      self.assertEqual(view.mode, 'normal')
      self.assertIn('cancelled', view.message)
      self.assertEqual(before, (repo.read('.slicer/index.json'), repo.read('.slicer/log.jsonl')))
      with patch.object(tui.ops, 'reject', wraps=tui.ops.reject) as op:
        view.handle(state, 'x')
        type_keys(view, state, 'VERDICT: FAIL - no test')
        view.handle(state, '\n')
      op.assert_called_once_with(state, 'S01', note='VERDICT: FAIL - no test')
      fresh = repo.state()
      self.assertEqual(fresh.index.require('S01').status, 'open')
      self.assertEqual(fresh.index.require('S02').status, 'open')
      self.assertTrue(any('VERDICT: FAIL' in n for n in fresh.index.require('S01').notes))
      self.assertIn('S01 rejected', view.message)

  def test_Release_ClearsTheClaimAndKeepsTheStatus(self) -> None:
    with support.TempRepo() as repo:
      self.repo_with_started(repo, 'First', 'Second')
      state = repo.state()
      self.assertTrue(state.index.require('S01').claim_owner)
      view = tui.View.initial(state)
      with patch.object(tui.ops, 'release', wraps=tui.ops.release) as op:
        view.handle(state, 'l')
      op.assert_called_once_with(state, 'S01')
      fresh = repo.state().index.require('S01')
      self.assertEqual((fresh.status, fresh.claim_owner), ('started', ''))
      self.assertIn('S01 released', view.message)
      view.handle(state, 'l')
      self.assertIn('no claim', view.message)

  def test_Keys_OnAProseRow_ApplyToASliceOnly(self) -> None:
    state = example()
    view = tui.View.initial(state)
    view.select((tui.PROSE, 'preamble'))
    for key in 'hxl':
      view.handle(state, key)
      self.assertEqual(view.mode, 'normal')
      self.assertIn('applies to a slice', view.message)

  def test_Help_ListsAllThreeKeys_AndTheStripOnlyHandoff(self) -> None:
    lines = tui.help_lines()
    for key, text in (('h', 'review'), ('x', 'note is required'), ('l', 'claim')):
      self.assertTrue(any(l.startswith(f'{key} ') and text in l for l in lines), key)
    for width, rows in ((80, 2), (160, 1)):
      strip = tui.shortcut_lines(width)
      text = '  '.join(strip)
      self.assertEqual(len(strip), rows, width)
      self.assertIn('h handoff', text)
      self.assertIn('v view', text)
      self.assertNotIn('x reject', text)
      self.assertNotIn('l release', text)

  def test_Reorder_WhileFilteredOrViewPreset_StaysDisabled(self) -> None:
    state = example()
    view = tui.View.initial(state)
    view.handle(state, 'v')
    for key in 'JK':
      view.handle(state, key)
      self.assertIn('reordering disabled', view.message)
    view.handle(state, 'c')
    view.filters.query = 'Current'
    for key in 'JK':
      view.message = ''
      view.handle(state, key)
      self.assertIn('reordering disabled', view.message)

  def test_Draw_NotePrompt_NamesWhetherTheNoteIsRequired(self) -> None:
    state = example()
    for action, label in (('handoff', 'Handoff note, optional'), ('reject', 'Reject verdict, required')):
      view = tui.View.initial(state)
      view.mode, view.note_action, view.text = 'note', action, 'abc'
      screen = Screen(10, 80)
      tui.draw(screen, state, view)
      text = '\n'.join(t for _, t in screen.writes)
      self.assertIn(f'{label} (Enter accepts, Esc cancels): abc', text)
      self.assertNotIn('Tab panes', text)


def sliced_example() -> State:
  """`example()` plus a slice on S02 with a lead, a boundary and one section."""
  state = example()
  state.slices["S02"] = model.Slice(
    "S02", "Search full title", lead=["First lead paragraph.", "Second lead paragraph."],
    boundary="**Not in this slice:** nothing.",
    sections=[model.Section("Why", "Because.")],
  )
  state.index.require("S02").has_slice = True
  return state


class DetailFieldsTests(unittest.TestCase):
  def test_Panel_SliceWithLead_ShowsLeadBeforeBoundary(self) -> None:
    state = sliced_example()
    panel = tui.panel(state, "S02")
    texts = [l.text for l in panel]
    first, second = texts.index("First lead paragraph."), texts.index("Second lead paragraph.")
    boundary = texts.index("Scope boundary")
    self.assertLess(first, second)
    self.assertLess(second, boundary)
    ents = tui.entries(state, "S02")
    lead = next(i for i, e in enumerate(ents) if e.name == "lead")
    self.assertEqual(ents[lead].kind, "lead")
    self.assertEqual(ents[lead].body, "First lead paragraph.\n\nSecond lead paragraph.")
    self.assertEqual(ents[lead + 1].kind, "boundary")
    self.assertEqual(panel[first].entry, lead)
    self.assertEqual(panel[second].entry, lead)

  def test_Panel_SliceWithoutLead_StillOffersALeadEntry(self) -> None:
    state = sliced_example()
    state.slices["S02"].lead = []
    panel = tui.panel(state, "S02")
    ents = tui.entries(state, "S02")
    lead = next(i for i, e in enumerate(ents) if e.name == "lead")
    self.assertTrue(any(l.entry == lead and l.text == "Lead" for l in panel))

  def test_Panel_ItemWithCites_ResolvesKindAndTitle(self) -> None:
    state = sliced_example()
    records = state.index.catalog.records
    records.append(model.CatalogRecord("C01", "goal", "Ship the thing"))
    records.append(model.CatalogRecord("C02", "decision", "Use JSON", status="retired"))
    state.index.require("S02").cites = ["C01", "C02", "C99"]
    line = next(l for l in tui.panel(state, "S02") if l.text.startswith("cites"))
    self.assertEqual(
      line.text, "cites           C01 goal: Ship the thing, C02 decision: Use JSON (retired), C99")
    self.assertIsNone(line.entry)

  def test_Panel_ShowsPassFlagsGroupAttemptsDiscoveredFrom(self) -> None:
    def pad(label: str) -> str:
      return label.ljust(16)

    state = sliced_example()
    item = state.index.require("S02")
    item.flags = ["risky", "ui"]
    item.group = "alpha"
    item.attempts = 3
    item.discovered_from = "S01"
    texts = [l.text for l in tui.panel(state, "S02")]
    for expected in ("%sone" % pad("pass"), "%srisky, ui" % pad("flags"), "%salpha" % pad("group"),
                     "%s3" % pad("attempts"), "%sS01" % pad("discovered from"),
                     "%sFind things" % pad("short title")):
      self.assertIn(expected, texts)
    # Every value starts in the same column, whatever the label's length.
    texts = [l.text for l in tui.panel(state, "S02")]
    values = {"pass": "one", "discovered from": "S01", "short title": "Find things"}

    def column(label: str) -> int:
      return next(t for t in texts if t.startswith(label)).index(values[label])

    self.assertEqual(column("discovered from"), column("pass"))
    self.assertEqual(column("short title"), column("pass"))
    plain = [l.text for l in tui.panel(state, "S04")]
    for expected in ("%s-" % pad("flags"), "%s-" % pad("group"), "%s-" % pad("short title"),
                     "%s-" % pad("cites"), "%s0" % pad("attempts"), "%s-" % pad("discovered from")):
      self.assertIn(expected, plain)

  def test_Panel_TypedNote_ShowsKindAndTrust(self) -> None:
    state = sliced_example()
    item = state.index.require("S02")
    item.note_records = [
      model.NoteRecord("n1", "verification", "ran the suite", "", None,
                       verified_by="human:Ada", verified_at="2026-10-14"),
      model.NoteRecord("n2", "", "plain note", "", None),
    ]
    texts = [l.text for l in tui.panel(state, "S02")]
    self.assertIn("[verification] ran the suite", texts)
    self.assertIn("trust: human-reviewed; verified_by: human:Ada; verified_at: 2026-10-14", texts)
    self.assertIn("plain note", texts)

  def test_Entries_IndexesStayAlignedWithPanel(self) -> None:
    for sliced in (True, False):
      state = sliced_example() if sliced else example()
      state.index.require("S02").note_records = [
        model.NoteRecord("n1", "", "one", "", None), model.NoteRecord("n2", "", "two", "", None)]
      ents = tui.entries(state, "S02")
      panel = tui.panel(state, "S02")
      tagged = sorted({l.entry for l in panel if l.entry is not None})
      self.assertEqual(tagged, list(range(len(ents))))
      expected_first_line = {
        "field": lambda e: e.name, "lead": lambda e: "Lead", "boundary": lambda e: "Scope boundary",
        "section": lambda e: f"## {e.name}", "note": lambda e: e.body, "note_new": lambda e: "+ add a note",
      }
      for i, entry in enumerate(ents):
        first = next(l for l in panel if l.entry == i)
        want = expected_first_line[entry.kind](entry)
        if entry.kind == "field":
          self.assertTrue(first.text.startswith(want), (first.text, want))
        else:
          self.assertEqual(first.text, want)

  def test_ApplyEdit_Lead_SplitsParagraphsLikeEditLead(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "Item")
      repo.run("promote", "S01")
      state = repo.state()
      request = tui.EditRequest(kind="lead", target="S01", name="lead", body="")
      result = tui.apply_edit_result(state, request, "one\nstill one\n\n\ntwo\n")
      self.assertEqual(result.severity, "success")
      self.assertEqual(repo.state().slices["S01"].lead, ["one\nstill one", "two"])
      request = tui.EditRequest(kind="lead", target="S01", name="lead", body="one\nstill one\n\ntwo")
      result = tui.apply_edit_result(repo.state(), request, "")
      self.assertEqual(result.severity, "success")
      self.assertEqual(repo.state().slices["S01"].lead, [])

  def test_ApplyEdit_Lead_NoSlice_ReportsError(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "Item")
      request = tui.EditRequest(kind="lead", target="S01", name="lead", body="")
      result = tui.apply_edit_result(repo.state(), request, "text")
      self.assertEqual(result.severity, "error")
      self.assertIn("no slice", result.message)

  def test_ApplyEdit_Flags_CommaListReplacesFlags(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "Item")
      state = repo.state()
      request = tui.EditRequest(kind="field", target="S01", name="flags", body="")
      result = tui.apply_edit_result(state, request, "a, b ,, c")
      self.assertEqual(result.severity, "success")
      self.assertEqual(repo.state().index.require("S01").flags, ["a", "b", "c"])
      request = tui.EditRequest(kind="field", target="S01", name="flags", body="a, b, c")
      tui.apply_edit_result(repo.state(), request, "z")
      self.assertEqual(repo.state().index.require("S01").flags, ["z"])
      request = tui.EditRequest(kind="field", target="S01", name="flags", body="z")
      tui.apply_edit_result(repo.state(), request, "")
      self.assertEqual(repo.state().index.require("S01").flags, [])

  def test_ApplyEdit_Pass_SetsPass(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "Item")
      request = tui.EditRequest(kind="field", target="S01", name="pass", body="")
      result = tui.apply_edit_result(repo.state(), request, " v2 ")
      self.assertEqual(result.severity, "success")
      self.assertEqual(repo.state().index.require("S01").pass_key, "v2")

  def test_ApplyEdit_ShortTitle_Sets(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "A long item title")
      request = tui.EditRequest(kind="field", target="S01", name="short title", body="A long item title")
      result = tui.apply_edit_result(repo.state(), request, "Long one")
      self.assertEqual(result.severity, "success")
      item = repo.state().index.require("S01")
      self.assertEqual((item.title, item.short_title), ("A long item title", "Long one"))


NOW = datetime(2026, 10, 14, 12, 12, 4, tzinfo=timezone.utc)
SPIN = "|/-\\"


def board_step(step_id: str, role: str, phase: str = "start", *, parent: str = "",
               begun: str = "2026-10-14T12:10:00Z", agent: str = "Claude Code",
               vendor: str = "Anthropic", model: str = "sonnet", reasoning: str = "high",
               task: str = "") -> relayrun.Step:
  return relayrun.Step(
    id=step_id, role=role, agent=agent, vendor=vendor, model=model, reasoning=reasoning,
    phase=phase, task=task, parent=parent, begun_at=begun, updated_at=begun,
  )


def board_run(*steps: relayrun.Step, run_id: str = "run-1", state: str = "active",
              outcome: str = "") -> relayrun.Run:
  return relayrun.Run(
    id=run_id, items=["s283"], worktree="s283", begun_at="2026-10-14T12:00:00Z",
    updated_at="2026-10-14T12:10:00Z", state=state, outcome=outcome, steps=list(steps),
  )


def pipeline_run() -> relayrun.Run:
  return board_run(
    board_step("1", "implementer", "done", begun="2026-10-14T12:00:00Z"),
    board_step("2", "reviewer", task="review the diff"),
  )


def board_text(lines: list[tui.PanelLine]) -> str:
  return "\n".join(line.text for line in lines)


def row_for_role(lines: list[tui.PanelLine], role: str) -> str:
  return next(line.text for line in lines if re.match(rf"\s*(?:[├└] )?\[.\] {role}(\s|$)", line.text))


def nested_run(*steps: relayrun.Step, **kwargs) -> relayrun.Run:
  """An orchestrator step 1 with the given steps; pass `parent="1"` on those that sit under it."""
  return board_run(board_step("1", "orchestrator", begun="2026-10-14T12:00:00Z"), *steps, **kwargs)


def line_with(lines: list[tui.PanelLine], text: str) -> tui.PanelLine | None:
  return next((line for line in lines if text in line.text), None)


class CellAttrScreen(Screen):
  """A `Screen` that also keeps every cell's attribute, as a terminal does: a later write wins."""

  def __init__(self, height: int, width: int) -> None:
    super().__init__(height, width)
    self.calls: list[tuple[int, int, str, int]] = []
    self.attrs: dict[tuple[int, int], int] = {}

  def erase(self):
    super().erase()
    self.calls.clear()
    self.attrs.clear()

  def addnstr(self, y, x, text, count, attr):
    super().addnstr(y, x, text, count, attr)
    self.calls.append((y, x, text, attr))
    cell = x
    for char in text:
      for offset in range(tui_style.cell_width(char)):
        self.attrs[(y, cell + offset)] = attr
      cell += tui_style.cell_width(char)


class RelayBoardLinesTests(unittest.TestCase):
  def test_BoardLines_OneRun_ShowsPipelineWithActiveRoleSpinner(self) -> None:
    lines = tui.board_lines([pipeline_run()], 0, NOW, 100)
    text = board_text(lines)
    self.assertIn("s283", lines[0].text)
    self.assertIn("active", lines[0].text)
    self.assertIn("implementer > [reviewer] > gate", text)
    reviewer = row_for_role(lines, "reviewer")
    self.assertTrue(reviewer.lstrip().startswith("[|]"), reviewer)
    for cell in ("Claude Code", "Anthropic/sonnet high", "2m04s"):
      self.assertIn(cell, reviewer)
    self.assertIn("review the diff", text)
    implementer = row_for_role(lines, "implementer")
    self.assertTrue(implementer.lstrip().startswith("[+]"), implementer)
    self.assertIn("12m04s", lines[0].text)
    self.assertIn("waiting", row_for_role(lines, "gate"))
    self.assertNotIn("architect", text)

  def test_BoardLines_ArchitectPresent_LeadsThePipeline(self) -> None:
    run = board_run(board_step("1", "architect", "done"), board_step("2", "implementer"))
    text = board_text(tui.board_lines([run], 0, NOW, 100))
    self.assertIn("architect > [implementer] > reviewer > gate", text)

  def test_BoardLines_TickAdvancesSpinnerFrame(self) -> None:
    frames = []
    for tick in range(5):
      lines = tui.board_lines([pipeline_run()], tick, NOW, 100)
      frames.append(row_for_role(lines, "reviewer").lstrip()[:3])
    self.assertEqual(frames, [f"[{c}]" for c in "|/-\\|"])
    first = tui.board_lines([pipeline_run()], 0, NOW, 100)
    again = tui.board_lines([pipeline_run()], 4, NOW, 100)
    self.assertEqual([line.text.replace("[|]", "[x]") for line in first],
                     [line.text.replace("[|]", "[x]") for line in again])

  def test_BoardLines_SubagentsCounted(self) -> None:
    run = board_run(
      board_step("1", "implementer", "done"),
      board_step("2", "reviewer"),
      board_step("3", "reviewer", parent="2"),
      board_step("4", "reviewer", "done", parent="2"),
      board_step("5", "reviewer", parent="3"),
      board_step("6", "implementer", "done", parent="1"),
    )
    lines = tui.board_lines([run], 0, NOW, 120)
    self.assertIn("2 subagents", row_for_role(lines, "reviewer"))
    self.assertIn("1 subagent", row_for_role(lines, "implementer"))
    self.assertNotIn("subagents", row_for_role(lines, "implementer"))
    self.assertNotIn("subagent", row_for_role(lines, "gate"))

  def test_BoardLines_StaleRunDimmedAndLabelled(self) -> None:
    lines = tui.board_lines([pipeline_run()], 0, NOW, 100)
    self.assertNotEqual({line.role for line in lines}, {"dim"})
    run = pipeline_run()
    run.state = "stale"
    lines = tui.board_lines([run], 3, NOW, 100)
    self.assertIn("stale", lines[0].text)
    self.assertTrue(lines[0].text.startswith("~ "), lines[0].text)
    # Only the freshness line stands out: the run has not been heard from for a while.
    self.assertEqual({line.role for line in lines if "last heard" not in line.text}, {"dim"})
    self.assertEqual(line_with(lines, "last heard").role, "error")
    text = board_text(lines)
    for frame in SPIN:
      self.assertNotIn(f"[{frame}]", text)
    # The clock stops at the last event: a dead run does not keep counting.
    self.assertIn("0s", row_for_role(lines, "reviewer"))

  def test_BoardLines_EndedRun_LabelsItsOutcome(self) -> None:
    run = board_run(board_step("1", "implementer", "done"), state="ended", outcome="pass")
    lines = tui.board_lines([run], 0, NOW, 100)
    self.assertIn("pass", lines[0].text)
    self.assertIn("ended 2m04s ago", lines[0].text)
    self.assertEqual(len(lines), 1)
    self.assertEqual({line.role for line in lines}, {"dim"})

  def test_BoardLines_EndedPassItemNotDone_ShowsStatusAndOpenGlyph(self) -> None:
    run = board_run(state="ended", outcome="pass")
    text = tui.board_lines([run], 0, NOW, 100,
                           statuses={"s283": "Awaiting review"}, done_label="Shipped")[0].text
    self.assertEqual(text.split("  ")[:2], ["○ s283", "pass · Awaiting review"])

  def test_BoardLines_EndedPassItemDone_ShowsCheck(self) -> None:
    run = board_run(state="ended", outcome="pass")
    text = tui.board_lines([run], 0, NOW, 100,
                           statuses={"s283": "Shipped"}, done_label="Shipped")[0].text
    self.assertEqual(text.split("  ")[:2], ["✓ s283", "pass · Shipped"])

  def test_BoardLines_EndedNarrow_KeepsStatusCell(self) -> None:
    run = board_run(state="ended", outcome="pass")
    text = tui.board_lines([run], 0, NOW, 24, titles={"s283": "A long slice title"},
                           statuses={"s283": "Open"}, done_label="Shipped")[0].text
    self.assertEqual(text, "○ s283  pass · Open")

  def test_BoardLines_EndedNarrowWidth_NoElapsedWithoutOutcome(self) -> None:
    run = board_run(board_step("1", "implementer", "done"), state="ended", outcome="pass")
    run.items = ["s1", "s2"]
    labelled = {"statuses": {"s1": "review", "s2": "done"}, "done_label": "Shipped"}
    # At 30 the outcome cell does not fit beside the head. The elapsed cell after it would fit,
    # but cells drop from the right, whole, so it must not show without the outcome.
    self.assertEqual(tui.board_lines([run], 0, NOW, 30, **labelled)[0].text, "○ s1, s2")
    for width in range(8, 61):
      text = tui.board_lines([run], 0, NOW, width, **labelled)[0].text
      if "10m00s" in text:
        self.assertIn("pass", text, (width, text))

  def test_BoardLines_EndedWideWidth_KeepsAllCells(self) -> None:
    run = board_run(board_step("1", "implementer", "done"), state="ended", outcome="pass")
    text = tui.board_lines([run], 0, NOW, 80, statuses={"s283": "Open"}, done_label="Shipped")[0].text
    self.assertEqual(text, "○ s283  pass · Open  10m00s  ended 2m04s ago  impl 1 · review 0 · gate 0")
    run.items = ["s1", "s2"]
    text = tui.board_lines([run], 0, NOW, 100, statuses={"s1": "review", "s2": "done"},
                           done_label="Shipped")[0].text
    expected = "○ s1, s2  pass · s1 review, s2 done  10m00s  ended 2m04s ago  impl 1 · review 0 · gate 0"
    self.assertEqual(text, expected)

  def test_BoardLines_EndedMultiItemRun_PairsStatusWithId(self) -> None:
    run = board_run(state="ended", outcome="pass")
    run.items = ["s1", "s2"]
    text = tui.board_lines([run], 0, NOW, 100,
                           statuses={"s1": "review", "s2": "done"}, done_label="Shipped")[0].text
    self.assertEqual(text.split("  ")[:2], ["○ s1, s2", "pass · s1 review, s2 done"])

  def test_BoardLines_EndedSingleItemRun_KeepsBareLabel(self) -> None:
    run = board_run(state="ended", outcome="pass")
    text = tui.board_lines([run], 0, NOW, 100,
                           statuses={"s283": "review"}, done_label="Shipped")[0].text
    self.assertEqual(text.split("  ")[:2], ["○ s283", "pass · review"])

  def test_BoardLines_EndedMultiItemRunMissingAStatus_OmitsThatItem(self) -> None:
    run = board_run(state="ended", outcome="pass")
    run.items = ["s1", "s2"]
    text = tui.board_lines([run], 0, NOW, 100,
                           statuses={"s2": "done"}, done_label="Shipped")[0].text
    self.assertEqual(text.split("  ")[:2], ["○ s1, s2", "pass · s2 done"])

  def test_BoardLines_FailedStep_UsesTheErrorRole(self) -> None:
    run = board_run(board_step("1", "implementer", "fail"))
    lines = tui.board_lines([run], 0, NOW, 100)
    row = next(line for line in lines if re.match(r"\s*\[.\] implementer\b", line.text))
    self.assertTrue(row.text.lstrip().startswith("[!]"), row.text)
    self.assertEqual(row.role, "error")

  def test_BoardLines_RolesOutsideThePipeline_FollowItInOneOtherLine(self) -> None:
    run = board_run(
      board_step("1", "orchestrator", begun="2026-10-14T12:00:00Z"),
      board_step("2", "implementer", "done"),
      board_step("3", "implementer-part-2"),
      board_step("4", "implementer", "done", parent="2"),
    )
    lines = tui.board_lines([run], 0, NOW, 100)
    other = [line.text for line in lines if line.text.lstrip().startswith("other")]
    self.assertEqual(len(other), 1)
    self.assertNotIn("orchestrator", other[0])
    self.assertIn("implementer-part-2", other[0])
    self.assertEqual(len([line for line in lines if "orchestrator" in line.text and "[|]" in line.text]), 1)
    self.assertIn("[|]", other[0])
    texts = [line.text for line in lines]
    self.assertGreater(texts.index(other_line := other[0]), max(
      i for i, text in enumerate(texts) if re.match(r"\s*(?:[├└] )?\[.\] (implementer|reviewer|gate)\b", text)))

  def test_BoardLines_OnlyOtherRoles_StillDraws(self) -> None:
    run = board_run(board_step("1", "orchestrator"))
    text = board_text(tui.board_lines([run], 0, NOW, 100))
    self.assertIn("implementer > reviewer > gate", text)
    self.assertIn("orchestrator", text)

  def test_BoardLines_UiAndSpecCheckSteps_NameTheirRoleInTheOtherLine(self) -> None:
    run = board_run(
      board_step("1", "orchestrator", begun="2026-10-14T12:00:00Z"),
      board_step("2", "ui", "done"),
      board_step("3", "spec-check", "done"),
    )
    lines = tui.board_lines([run], 0, NOW, 100)
    other = [line.text for line in lines if line.text.lstrip().startswith("other")]
    self.assertEqual(len(other), 1)
    self.assertIn("ui [", other[0])
    self.assertIn("spec-check [", other[0])

  def test_BoardLines_NarrowWidth_ClipsWithoutSplittingCells(self) -> None:
    long_model = "claude-sonnet-5-5-with-a-very-long-suffix"
    run = board_run(
      board_step("1", "implementer", "done", model=long_model, task="x" * 200,
                 agent="Claude Code"),
      board_step("2", "reviewer", model=long_model, task="y" * 200),
      board_step("3", "reviewer", parent="2"),
    )
    run.worktree = "w" * 120
    lines = tui.board_lines([run], 0, NOW, 80)
    for line in lines:
      self.assertLessEqual(tui_style.cell_width(line.text), 80, line.text)
    reviewer = row_for_role(lines, "reviewer")
    for cell in ("reviewer", "2m04s", "1 subagent"):
      self.assertIn(cell, reviewer)
    # A cell that does not fit is dropped whole, never cut.
    self.assertNotIn("claude-sonnet", reviewer)
    self.assertNotIn("Anthropic", reviewer)
    wide = row_for_role(tui.board_lines([run], 0, NOW, 200), "reviewer")
    self.assertIn(f"Anthropic/{long_model} high", wide)
    tasks = [line.text for line in lines if "yyyy" in line.text]
    self.assertEqual(len(tasks), 1)
    self.assertTrue(tasks[0].endswith("..."), tasks[0])

  def test_BoardLines_RolesUnderOrchestrator_FillTheLane(self) -> None:
    run = nested_run(board_step("2", "implementer", parent="1"))
    lines = tui.board_lines([run], 0, NOW, 100)
    row = row_for_role(lines, "implementer")
    self.assertNotIn("waiting", row)
    self.assertTrue(row.lstrip().lstrip("├└ ").startswith("[|]"), row)
    self.assertIn("[implementer] > reviewer > gate", board_text(lines))
    texts = [line.text for line in lines]
    root = next(n for n, text in enumerate(texts) if text.lstrip().startswith("└ orchestrator"))
    self.assertLess(root, texts.index(row))
    self.assertTrue(row.lstrip().startswith("├"), row)
    self.assertTrue(row_for_role(lines, "gate").lstrip().startswith("└"))
    self.assertFalse([text for text in texts if text.lstrip().startswith("other")])

  def test_BoardLines_HelperUnderImplementer_StaysASubagent(self) -> None:
    run = nested_run(
      board_step("2", "implementer", parent="1"),
      board_step("3", "implementer", "done", parent="2"),
    )
    lines = tui.board_lines([run], 0, NOW, 100)
    rows = [line.text for line in lines if re.match(r"\s*(?:[├└] )?\[.\] implementer(\s|$)", line.text)]
    self.assertEqual(len(rows), 1, rows)
    self.assertIn("1 subagent", rows[0])
    self.assertNotIn("×", rows[0])
    self.assertNotIn("round 2", board_text(lines))

  def test_BoardLines_Header_ShowsTitleAndOrchestratorModel(self) -> None:
    run = nested_run(board_step("2", "implementer", parent="1"))
    titles = {"s283": "Relay board nested lanes"}
    lines = tui.board_lines([run], 0, NOW, 100, titles=titles)
    self.assertIn("s283", lines[0].text)
    self.assertIn("Relay board nested lanes", lines[0].text)
    self.assertIn("orchestrator sonnet/high", lines[1].text)
    self.assertNotIn("wt ", lines[1].text)
    self.assertNotIn("s283  s283", lines[0].text)
    run.worktree = ".worktrees/s283-board"
    lines = tui.board_lines([run], 0, NOW, 100, titles=titles)
    self.assertIn("wt s283-board", lines[1].text)
    self.assertNotIn("s283-board", lines[0].text)
    cut = tui.board_lines([run], 0, NOW, 40, titles={"s283": "t" * 80})
    self.assertIn("t...  active", cut[0].text)
    self.assertLessEqual(tui_style.cell_width(cut[0].text), 40)
    self.assertIsNone(line_with(tui.board_lines([run], 0, NOW, 100), "Relay board"))

  def test_BoardLines_GateFail_ShowsRoundAndFailLine(self) -> None:
    run = board_run(
      board_step("1", "implementer", "done"),
      board_step("2", "gate", "fail", task="missing test for the nested lane"),
      board_step("3", "implementer", "done"),
      board_step("4", "gate"),
    )
    lines = tui.board_lines([run], 0, NOW, 100)
    pipeline = line_with(lines, "implementer > ")
    self.assertTrue(pipeline.text.endswith("round 2"), pipeline.text)
    self.assertLessEqual(tui_style.cell_width(pipeline.text), 100)
    self.assertIn("×2", row_for_role(lines, "gate"))
    self.assertIn("×2", row_for_role(lines, "implementer"))
    self.assertNotIn("×", row_for_role(lines, "reviewer"))
    fail = line_with(lines, "gate FAIL")
    self.assertIsNotNone(fail)
    self.assertEqual(fail.role, "error")
    self.assertIn("✗ gate FAIL → implementer round 2", fail.text)
    self.assertIn("missing test for the nested lane", fail.text)
    texts = [line.text for line in lines]
    self.assertEqual(texts.index(fail.text), texts.index(row_for_role(lines, "gate")) + 1)
    # A gate that is still failed says why once, in the FAIL line, not again under its row.
    last = board_run(board_step("1", "implementer", "done"),
                     board_step("2", "gate", "fail", task="missing test"))
    lines = tui.board_lines([last], 0, NOW, 100)
    self.assertEqual(len([line for line in lines if "missing test" in line.text]), 1)
    self.assertTrue(row_for_role(lines, "gate").lstrip().startswith("[!]"))
    self.assertIn("implementer round 2: missing test", line_with(lines, "gate FAIL").text)

  def test_BoardLines_LastHeard_EscalatesNearStale(self) -> None:
    def role_at(updated: str, state: str = "active") -> str:
      run = pipeline_run()
      run.updated_at, run.state = updated, state
      line = line_with(tui.board_lines([run], 0, NOW, 100), "last heard")
      self.assertIsNotNone(line)
      return line.role

    self.assertEqual(role_at("2026-10-14T12:10:00Z"), "dim")
    half = relayrun.STALE_AFTER / 2
    just_under = relayrun.stamp(NOW - half + timedelta(seconds=1))
    just_over = relayrun.stamp(NOW - half - timedelta(seconds=1))
    self.assertEqual(role_at(just_under), "dim")
    self.assertEqual(role_at(just_over), "started")
    self.assertEqual(role_at("2026-10-14T11:30:00Z", "stale"), "error")
    run = pipeline_run()
    self.assertIn("last heard 2m04s ago", line_with(tui.board_lines([run], 0, NOW, 100), "last heard").text)

  def test_BoardLines_NarrowWidth_NoLineExceedsWidth(self) -> None:
    long_model = "claude-sonnet-5-5-with-a-very-long-suffix"
    nested = nested_run(
      board_step("2", "implementer", "done", parent="1", model=long_model, task="x" * 200),
      board_step("3", "gate", "fail", parent="1", model=long_model, task="g" * 200),
      board_step("4", "implementer", parent="1", model=long_model, task="y" * 200),
      board_step("5", "reviewer", parent="4"),
      run_id="run-1",
    )
    nested.worktree = "w" * 120
    flat = board_run(board_step("1", "reviewer", model=long_model, task="z" * 200), run_id="run-2")
    flat.items = ["s283", "s284", "s285"]
    ended = board_run(board_step("1", "implementer", "done"), run_id="run-3", state="ended", outcome="pass")
    ended.items = ["s990"]
    titles = {"s283": "T" * 150, "s990": "U" * 150}
    for width in (40, 80):
      lines = tui.board_lines([nested, flat, ended], 0, NOW, width, titles=titles)
      for line in lines:
        self.assertLessEqual(tui_style.cell_width(line.text), width, line.text)
      for needle in ("gate FAIL", "last heard", "orchestrator", "UUUU...  pass"):
        self.assertIsNotNone(line_with(lines, needle), (width, needle))
      # A cell that does not fit is dropped whole: "round 2" needs 41 cells beside the pipeline.
      self.assertEqual(line_with(lines, "round 2") is not None, width == 80)
      self.assertEqual(line_with(lines, "ended 2m04s ago") is not None, width == 80)
      self.assertTrue(any(line.text.endswith("...") for line in lines))

  def test_BoardLines_NoRuns_SaysNothingRunningAndNamesRelayRun(self) -> None:
    lines = tui.board_lines([], 0, NOW, 80)
    text = board_text(lines)
    self.assertIn("Nothing running", text)
    self.assertIn("slicer relay-run begin", text)
    for line in lines:
      self.assertLessEqual(tui_style.cell_width(line.text), 80)

  def test_BoardLines_Pipeline_SpansColorEachRoleByState(self) -> None:
    line = line_with(tui.board_lines([pipeline_run()], 0, NOW, 100), "implementer > ")
    spans = [(tui_style.cell_slice(line.text, start, end), role) for start, end, role in line.spans]
    self.assertEqual(spans, [("implementer", "done"), ("[reviewer]", "started"), ("gate", "dim")])
    # Spans count cells. A line cut to the panel width keeps the cells that remain: the gate's span is past the cut.
    cut = line_with(tui.board_lines([pipeline_run()], 0, NOW, 20), "implementer > ")
    self.assertEqual(cut.text, "  implementer > [rev")
    cut_spans = [(tui_style.cell_slice(cut.text, start, end), role) for start, end, role in cut.spans]
    self.assertEqual(cut_spans, [("implementer", "done"), ("[rev", "started"), ("", "dim")])
    # A stale lane stays dimmed throughout, so its pipeline has no spans.
    stale = pipeline_run()
    stale.state = "stale"
    self.assertEqual(line_with(tui.board_lines([stale], 0, NOW, 100), "implementer > ").spans, ())

  def test_BoardLines_Pipeline_FailedRoleSpanIsError(self) -> None:
    run = board_run(board_step("1", "implementer", "done"),
                    board_step("2", "gate", "fail", task="missing test"))
    line = line_with(tui.board_lines([run], 0, NOW, 100), "implementer > ")
    spans = [(tui_style.cell_slice(line.text, start, end), role) for start, end, role in line.spans]
    # The gate's last step failed, so its role is the error role, as its lane row's [!] is.
    self.assertEqual(spans, [("implementer", "done"), ("reviewer", "dim"), ("gate", "error")])

  def test_BoardLines_EndedRun_OutcomeSpan(self) -> None:
    labelled = {"statuses": {"s283": "Awaiting review"}, "done_label": "Shipped"}
    for outcome, role in (("pass", "done"), ("fail", "error")):
      line = tui.board_lines([board_run(state="ended", outcome=outcome)], 0, NOW, 100, **labelled)[0]
      spans = [(tui_style.cell_slice(line.text, start, end), span_role) for start, end, span_role in line.spans]
      self.assertEqual(spans, [(f"{outcome} · Awaiting review", role)], outcome)
      self.assertEqual(line.role, "dim")
    stopped = tui.board_lines([board_run(state="ended", outcome="stopped")], 0, NOW, 100)[0]
    self.assertEqual(stopped.spans, ())
    # At 14 cells the outcome cell does not fit beside the head, so there is no cell to color.
    narrow = tui.board_lines([board_run(state="ended", outcome="pass")], 0, NOW, 14, **labelled)[0]
    self.assertNotIn("pass", narrow.text)
    self.assertEqual(narrow.spans, ())


class PlaceCellsTests(unittest.TestCase):
  def test_Place_DefaultDropsACellThatDoesNotFit_KeepsALaterOneThatFits(self) -> None:
    # Lane headers and step lines rely on this: a cell that does not fit is skipped,
    # and a later one that fits still goes in.
    self.assertEqual(tui._place(["a", "bbbb", "cc"], 5), ("a  cc", {0: 0, 2: 3}))

  def test_Place_Ordered_DropsEveryCellAfterOneThatDoesNotFit(self) -> None:
    self.assertEqual(tui._place(["a", "bbbb", "cc"], 5, ordered=True), ("a", {0: 0}))


class RelayBoardElapsedTests(unittest.TestCase):
  def test_ElapsedText_Spans_UseTheShortestUnitPair(self) -> None:
    start = "2026-10-14T12:00:00Z"
    for seconds, text in ((0, "0s"), (59, "59s"), (60, "1m00s"), (3599, "59m59s"),
                          (3600, "1h00m"), (3720, "1h02m"), (-5, "0s")):
      at = datetime.fromtimestamp(datetime(2026, 10, 14, 12, tzinfo=timezone.utc).timestamp() + seconds,
                                  timezone.utc)
      self.assertEqual(tui.elapsed_text(start, at), text, seconds)


class RelayBoardViewTests(unittest.TestCase):
  def board(self, *results) -> tuple[tui.View, State, list]:
    state = example()
    view = tui.View.initial(state)
    calls: list = []
    outcomes = list(results)

    def fake(root, now=None, *, siblings=True, active=False):
      calls.append((root, siblings, active))
      outcome = outcomes[min(len(calls), len(outcomes)) - 1]
      if isinstance(outcome, Exception):
        raise outcome
      return outcome

    patcher = patch.object(relayrun, "list_runs", fake)
    patcher.start()
    self.addCleanup(patcher.stop)
    return view, state, calls

  def test_Handle_TickOutsideBoard_IsIgnored(self) -> None:
    view, state, calls = self.board([pipeline_run()])
    before = (view.mode, view.selected, view.message, view.board_tick)
    result = view.handle(state, "tick")
    self.assertEqual((view.mode, view.selected, view.message, view.board_tick), before)
    self.assertEqual((result.message, result.edit), ("", None))
    self.assertEqual(calls, [])
    view.mode = "help"
    view.handle(state, "tick")
    self.assertEqual((view.mode, view.board_tick, calls), ("help", 0, []))

  def test_Handle_B_OpensTheBoardAndLoadsRunsOnce(self) -> None:
    view, state, calls = self.board([pipeline_run()])
    view.handle(state, "b")
    self.assertEqual(view.mode, "board")
    self.assertEqual(len(calls), 1)
    self.assertEqual([run.id for run in view.board_runs], ["run-1"])
    self.assertEqual(view.board_error, "")
    self.assertEqual(tui.key_action("b"), "board")
    self.assertTrue(any(line.startswith("b ") and "relay" in line.lower() for line in tui.help_lines()))

  def test_Handle_BoardTick_AdvancesSpinnerButRefreshesDataOnASlowerCadence(self) -> None:
    view, state, calls = self.board([pipeline_run()])
    view.handle(state, "b")
    for n in range(1, tui.BOARD_REFRESH_TICKS):
      view.handle(state, "tick")
      self.assertEqual((view.board_tick, len(calls)), (n, 1))
    view.handle(state, "tick")
    self.assertEqual((view.board_tick, len(calls)), (tui.BOARD_REFRESH_TICKS, 2))
    for _ in range(tui.BOARD_REFRESH_TICKS - 1):
      view.handle(state, "tick")
    self.assertEqual(len(calls), 2)
    view.handle(state, "tick")
    self.assertEqual(len(calls), 3)
    self.assertEqual(calls[0], (state.root, True, False))

  def test_Handle_BoardTick_UsesNoStoreRefresh(self) -> None:
    view, state, _ = self.board([pipeline_run()])
    view.handle(state, "b")
    with patch.object(store, "sibling_work", side_effect=AssertionError("refreshed")):
      view.handle(state, "tick")
      tui.draw(Screen(24, 100), state, view)

  def test_Draw_EndedPassItemDone_ShowsCheckAndConfiguredStatusLabel(self) -> None:
    state = example()
    state.config.statuses["shipped"] = "Delivered"
    state.config.done_status = "shipped"
    state.index.require("S01").status = "shipped"
    run = board_run(state="ended", outcome="pass")
    run.items = ["S01"]
    view = tui.View.initial(state)
    view.mode = "board"
    view.board_runs = [run]
    screen = Screen(24, 160)
    with patch.object(relayrun, "utcnow", return_value=NOW):
      tui.draw(screen, state, view)
    summary = next(text for y, text in screen.writes if y == 1)
    self.assertEqual(summary.split("  ")[:3],
                     ["✓ S01", "Finished setup", "pass · Delivered"])

  def test_Handle_CorruptLedger_KeepsLastFrameAndShowsOneLine(self) -> None:
    broken = StateError("/p/.slicer/relay-runs.jsonl:3: not valid JSON (x).\nfix it", code="corrupt")
    view, state, calls = self.board([pipeline_run()], broken, [])
    view.handle(state, "b")
    for _ in range(tui.BOARD_REFRESH_TICKS):
      view.handle(state, "tick")
    self.assertEqual(len(calls), 2)
    self.assertEqual(view.mode, "board")
    self.assertEqual([run.id for run in view.board_runs], ["run-1"])
    self.assertIn("relay-runs.jsonl:3", view.board_error)
    self.assertNotIn("\n", view.board_error)
    screen = Screen(24, 100)
    tui.draw(screen, state, view)
    output = "\n".join(text for _, text in screen.writes)
    self.assertIn("relay-runs.jsonl:3", output)
    self.assertIn("s283", output)
    for _ in range(tui.BOARD_REFRESH_TICKS):
      view.handle(state, "tick")
    self.assertEqual((view.board_runs, view.board_error), ([], ""))

  def test_Handle_BoardReopened_NeverShowsTheEarlierFrame(self) -> None:
    broken = StateError("relay-runs.jsonl:1: bad", code="corrupt")
    view, state, _ = self.board([pipeline_run()], broken)
    view.handle(state, "b")
    self.assertEqual(len(view.board_runs), 1)
    view.handle(state, "b")
    view.handle(state, "b")
    self.assertEqual(view.board_runs, [])
    self.assertIn("relay-runs.jsonl:1", view.board_error)
    view, state, _ = self.board([pipeline_run()])
    view.board_error = "old error"
    view.handle(state, "b")
    self.assertEqual(view.board_error, "")

  def test_Pipeline_FollowsTheRelayRoleNames(self) -> None:
    self.assertEqual(set(tui.RELAY_PIPELINE) | {"architect", "orchestrator"}, set(roles.ROLE_NAMES))
    self.assertEqual(tui.RELAY_PIPELINE, ("implementer", "reviewer", "gate"))

  def test_BoardLines_Orchestrator_IsTheParentNotAWorkerStage(self) -> None:
    run = nested_run(board_step("2", "implementer", parent="1"))
    lines = tui.board_lines([run], 0, NOW, 100)
    texts = [line.text for line in lines]
    pipeline = next(text for text in texts if " > " in text)
    self.assertNotIn("orchestrator", pipeline)
    self.assertFalse([t for t in texts if re.match(r"\s*(?:[├└] )?\[.\] orchestrator", t)])
    self.assertFalse([t for t in texts if "orchestrator" in t and "waiting" in t])
    self.assertEqual(len([t for t in texts if t.lstrip().startswith("└ orchestrator")]), 1)
    self.assertEqual(len([t for t in texts if re.match(r"\s*[├└] .*waiting", t)]), 2)

  def test_Handle_BoardOpen_EndedRunsPastTheWindowLeaveTheBoard(self) -> None:
    ended = board_run(board_step("1", "implementer", "done"), run_id="run-0",
                      state="ended", outcome="pass")
    stale = board_run(board_step("1", "implementer"), run_id="run-2", state="stale")
    ended_at = datetime(2026, 10, 14, 12, 10, 0, tzinfo=timezone.utc)  # the run's last event
    for later, kept in ((tui.BOARD_RECENT_ENDED + timedelta(seconds=1), False),
                        (tui.BOARD_RECENT_ENDED, True)):
      view, state, _ = self.board([ended, pipeline_run(), stale])
      with patch.object(relayrun, "utcnow", lambda: ended_at + later):
        view.handle(state, "b")
      ids = ["run-1", "run-2"] + (["run-0"] if kept else [])
      self.assertEqual([run.id for run in view.board_runs], ids, later)

  def test_LoadBoard_KeepsRecentlyEndedRuns(self) -> None:
    def ended(run_id: str, updated: str, outcome: str = "pass") -> relayrun.Run:
      run = board_run(
        board_step("1", "implementer", "done"), board_step("2", "implementer", "done"),
        board_step("3", "reviewer", "done"), board_step("4", "gate", "done"),
        run_id=run_id, state="ended", outcome=outcome,
      )
      run.updated_at = updated
      return run

    recent = ended("run-a", "2026-10-14T12:02:04Z")
    newer = ended("run-b", "2026-10-14T12:09:04Z", "fail")
    old = ended("run-c", "2026-10-14T10:12:04Z")
    active = pipeline_run()
    view, state, _ = self.board([recent, old, active, newer])
    with patch.object(relayrun, "utcnow", lambda: NOW):
      view.handle(state, "b")
    self.assertEqual([run.id for run in view.board_runs], ["run-1", "run-b", "run-a"])
    lines = tui.board_lines(view.board_runs, 0, NOW, 100, titles={"s283": "Relay board"})
    summary = [line for line in lines if "ended 10m00s ago" in line.text]
    self.assertEqual(len(summary), 1)
    self.assertIn("pass", summary[0].text)
    self.assertIn("impl 2 · review 1 · gate 1", summary[0].text)
    self.assertIn("Relay board", summary[0].text)
    self.assertEqual(summary[0].role, "dim")
    self.assertTrue(summary[0].text.startswith("○ s283"), summary[0].text)
    failed = line_with(lines, "ended 3m00s ago")
    self.assertTrue(failed.text.startswith("✗ s283"), failed.text)
    self.assertLess(lines.index(failed), lines.index(summary[0]))
    self.assertNotIn("run-c", board_text(lines))
    self.assertEqual(tui.BOARD_RECENT_ENDED, timedelta(hours=1))

  def test_Handle_BoardKeys_CloseScrollAndStayReadOnly(self) -> None:
    for closer in ("b", "\x1b", "q"):
      view, state, _ = self.board([])
      view.handle(state, "b")
      view.handle(state, closer)
      self.assertEqual((view.mode, view.quit), ("normal", False), closer)
    view, state, _ = self.board([])
    view.handle(state, "b")
    selected, items = view.selected, [dict(i.__dict__) for i in state.index.items]
    for key in "sdhxlpunrawewJKTMfvo/":
      view.handle(state, key)
    self.assertEqual(view.mode, "board")
    self.assertEqual((view.selected, [dict(i.__dict__) for i in state.index.items]), (selected, items))
    view.handle(state, "j")
    view.handle(state, "j")
    view.handle(state, "k")
    self.assertEqual(view.board_at, 1)
    view.handle(state, "k")
    view.handle(state, "k")
    self.assertEqual(view.board_at, 0)

  def test_Draw_Board_ShowsLanesScrolledAndClipped(self) -> None:
    long = board_run(board_step("1", "implementer", task="t" * 200), run_id="run-2")
    view, state, _ = self.board([pipeline_run(), long])
    view.handle(state, "b")
    screen = Screen(24, 80)
    with patch.object(relayrun, "utcnow", lambda: NOW):
      tui.draw(screen, state, view)
    expected = tui.board_lines(view.board_runs, 0, NOW, 79)
    output = "\n".join(text for _, text in screen.writes)
    self.assertIn("Relay board", output)
    self.assertNotIn("Tab panes", output)
    for n, line in enumerate(expected[:20], 1):
      if line.text:  # a blank separator draws nothing
        self.assertIn((n, line.text), screen.writes)
    self.assertTrue(any(text.endswith("...") for _, text in screen.writes))
    # A short screen scrolls: j moves the first line shown, and the end of the board stops it.
    small = Screen(10, 80)
    with patch.object(relayrun, "utcnow", lambda: NOW):
      tui.draw(small, state, view)
      first = [t for y, t in small.writes if y == 1]
      view.handle(state, "j")
      tui.draw(small, state, view)
      self.assertEqual(view.board_at, 1)
      self.assertEqual([t for y, t in small.writes if y == 1], [expected[1].text])
      self.assertNotEqual([t for y, t in small.writes if y == 1], first)
      for _ in range(40):
        view.handle(state, "j")
      tui.draw(small, state, view)
    visible = 10 - 3
    self.assertEqual(view.board_at, len(expected) - visible)
    self.assertIn((visible, expected[-1].text), small.writes)
    self.assertLessEqual(max(y for y, _ in small.writes), 9)

  def test_Draw_Board_TitlesComeFromTheRoadmap(self) -> None:
    run = board_run(board_step("1", "implementer"))
    run.items = ["S02"]
    view, state, _ = self.board([run])
    view.handle(state, "b")
    screen = Screen(24, 100)
    with patch.object(relayrun, "utcnow", lambda: NOW):
      tui.draw(screen, state, view)
    header = next(text for y, text in screen.writes if y == 1)
    self.assertIn("S02  Search full title  active", header)

  def test_Draw_PanelLineSpans_PaintsCells(self) -> None:
    palette = tui_style.Palette({"normal": 1, "dim": 2, "done": 3, "started": 4, "error": 5}, 6, 7)
    line = tui.PanelLine("宽ab cd", role="dim", spans=((0, 2, "done"), (3, 5, "started")))
    view, state, _ = self.board([])
    view.handle(state, "b")
    screen = CellAttrScreen(24, 80)
    with patch.object(tui, "board_lines", return_value=[line]):
      tui.draw(screen, state, view, palette)
    self.assertIn((1, "宽ab cd"), screen.writes)
    # 宽 is two cells wide, so cells 3 and 4 hold b and the space, not the characters at indexes 3 and 4.
    self.assertEqual([screen.attrs[(1, cell)] for cell in range(7)],
                     [palette.attr("done"), palette.attr("done"), palette.attr("dim"),
                      palette.attr("started"), palette.attr("started"), palette.attr("dim"),
                      palette.attr("dim")])
    view.board_runs = [pipeline_run()]
    screen = CellAttrScreen(24, 80)
    with patch.object(relayrun, "utcnow", lambda: NOW):
      tui.draw(screen, state, view, palette)
    row, text = next((y, t) for y, t in screen.writes if "implementer > " in t)

    def attrs(word: str) -> set[int]:
      start = text.index(word)
      return {screen.attrs[(row, cell)] for cell in range(start, start + len(word))}

    self.assertEqual(attrs("implementer"), {palette.attr("done")})
    self.assertEqual(attrs("[reviewer]"), {palette.attr("started")})
    self.assertEqual(attrs("gate"), {palette.attr("dim")})
    self.assertEqual(attrs("round 1"), {palette.attr("normal")})

  def test_PanelLine_NoSpans_DrawsAsBefore(self) -> None:
    self.assertEqual(tui.PanelLine("plain").spans, ())
    palette = tui_style.Palette({"normal": 1, "dim": 2, "error": 5}, 6, 7)
    lines = [tui.PanelLine("first line", role="error"), tui.PanelLine("second line", role="dim")]
    view, state, _ = self.board([])
    view.handle(state, "b")
    screen = CellAttrScreen(24, 80)
    with patch.object(tui, "board_lines", return_value=lines):
      tui.draw(screen, state, view, palette)
    # One write per line, from the left edge, in the line's own role.
    self.assertEqual([call for call in screen.calls if call[0] in (1, 2)],
                     [(1, 0, "first line", palette.attr("error")),
                      (2, 0, "second line", palette.attr("dim"))])


class RelayBoardLoopTests(unittest.TestCase):
  def test_Run_BoardOpen_WakesOnATimerAndOtherViewsBlock(self) -> None:
    script = iter(["b", curses.error, curses.error, "b", curses.error, "q"])
    timeouts: list[int] = []
    frames: list[str] = []

    class Terminal(Screen):
      def timeout(self, ms):
        timeouts.append(ms)

      def get_wch(self):
        text = "\n".join(t for _, t in self.writes)
        frames.append(next((c for c in SPIN if f"[{c}]" in text), ""))
        key = next(script)
        if key is curses.error:
          raise curses.error("no input")
        return key

    calls = []

    def fake(root, now=None, *, siblings=True, active=False):
      calls.append(root)
      return [pipeline_run()]

    with patch("curses.curs_set"), \
        patch("curses.wrapper", side_effect=lambda fn: fn(Terminal(24, 100))), \
        patch("slicer.tui_style.setup_palette", return_value=tui_style.monochrome()), \
        patch.object(relayrun, "list_runs", fake):
      self.assertEqual(tui.run(example()), 0)
    # Blocking, then 250 ms while the board is open (tick, tick, the closing key), then blocking.
    self.assertEqual(timeouts, [-1, tui.BOARD_TICK_MS, tui.BOARD_TICK_MS, tui.BOARD_TICK_MS, -1, -1])
    # Each timed-out read advances the spinner one frame; a timed-out read elsewhere does nothing.
    self.assertEqual(frames, ["", "|", "/", "-", "", ""])
    self.assertEqual(len(calls), 1)


def linked_example() -> State:
  """`example()` where S02 waits on S03 (parked) and S01 (done), and S04 waits on S02."""
  state = example()
  state.index.require("S02").depends_on = ["S03", "S01"]
  state.index.require("S04").depends_on = ["S02"]
  return state


def texts(lines: list[tui.PanelLine]) -> list[str]:
  return [line.text for line in lines]


class DetailPaneTests(unittest.TestCase):
  def history_repo(self, repo: support.TempRepo) -> None:
    repo.run("init")
    repo.run("add", "First")
    repo.run("add", "Second")
    repo.run("promote", "S01")
    repo.run("promote", "S02")
    repo.run("start", "S01")
    repo.run("start", "S02")
    repo.run("handoff", "S01", "--note", "ready")
    repo.run("reject", "S01", "--note", "VERDICT: FAIL - no test")

  def test_DepsLines_BlockedItem_NamesPendingBlockers(self) -> None:
    state = linked_example()
    lines = texts(tui.deps_lines(state, "S02"))
    self.assertEqual(lines[0], "S02  Find things")
    self.assertIn("waits on        S03 (blocked), S01", lines)
    self.assertIn("blocked by      S03", lines)
    # An item with nothing pending says so instead of leaving the line out.
    free = texts(tui.deps_lines(state, "S01"))
    self.assertIn("waits on        -", free)
    self.assertIn("blocked by      -", free)

  def test_DepsLines_ShowsDependents(self) -> None:
    state = linked_example()
    self.assertIn("depended on by  S04", texts(tui.deps_lines(state, "S02")))
    self.assertIn("depended on by  S02", texts(tui.deps_lines(state, "S03")))
    self.assertIn("depended on by  -", texts(tui.deps_lines(state, "S04")))

  def test_DepsLines_OpenSliver_NamesTheSliverHoldingItsParent(self) -> None:
    state = linked_example()
    state.index.items.append(Item("S05", "Sliver of S03", "open", parent="S03"))
    lines = texts(tui.deps_lines(state, "S03"))
    self.assertIn("blocked by      S05", lines)
    self.assertIn("held by slivers S05", lines)

  def test_HistoryLines_FiltersToItemNewestFirst(self) -> None:
    with support.TempRepo() as repo:
      self.history_repo(repo)
      state = repo.state()
      lines = texts(tui.history_lines(state, "S01", 50))
      self.assertEqual([line.split()[2] for line in lines], ["reject", "handoff", "status", "promote", "add"])
      self.assertTrue(all(line.split()[1] == "S01" for line in lines))
      # The same lines `slicer log --item S01` prints.
      code, out, _ = repo.run("log", "--item", "S01")
      self.assertEqual(code, 0)
      self.assertEqual(lines, out.splitlines())
      self.assertIn("VERDICT: FAIL - no test", lines[0])

  def test_HistoryLines_UnchangedLog_ReadsOnce(self) -> None:
    with support.TempRepo() as repo:
      self.history_repo(repo)
      state = repo.state()
      with patch.object(State, "history", autospec=True, side_effect=State.history) as read:
        tui.history_lines(state, "S01")
        tui.history_lines(state, "S01")
        self.assertEqual(read.call_count, 1)

  def test_HistoryLines_AppendedEntry_ShowsNewEntry(self) -> None:
    with support.TempRepo() as repo:
      self.history_repo(repo)
      state = repo.state()
      with patch.object(State, "history", autospec=True, side_effect=State.history) as read:
        tui.history_lines(state, "S01")
        tui.history_lines(state, "S01")
        self.assertEqual(read.call_count, 1)
        repo.run("start", "S01")
        lines = texts(tui.history_lines(state, "S01"))
      _, out, _ = repo.run("log", "--item", "S01")
      self.assertEqual(lines, out.splitlines())

  def test_HistoryLines_RespectsLimit(self) -> None:
    with support.TempRepo() as repo:
      self.history_repo(repo)
      state = repo.state()
      lines = texts(tui.history_lines(state, "S01", 2))
      self.assertEqual([line.split()[2] for line in lines], ["reject", "handoff"])

  def test_HistoryLines_EntryWithActorAndNote_UsesTheLogFormat(self) -> None:
    entries = [
      model.LogEntry("2026-10-17T10:00:00Z", "S01", "reject", "review", "open", "VERDICT: FAIL", "ci"),
      model.LogEntry("2026-10-17T09:00:00Z", "S01", "add", "", "open"),
    ]
    with support.TempRepo() as repo:
      self.history_repo(repo)
      with patch.object(State, "history", return_value=entries):
        self.assertEqual(texts(tui.history_lines(repo.state(), "S01", 5)), [
          "2026-10-17T10:00:00Z  S01   reject   review -> open  VERDICT: FAIL  by ci",
          "2026-10-17T09:00:00Z  S01   add      - -> open",
        ])

  def test_HistoryLines_NoHistory_SaysSo(self) -> None:
    with support.TempRepo() as repo:
      self.history_repo(repo)
      self.assertEqual(texts(tui.history_lines(repo.state(), "S99", 50)), ["no history for S99"])

  def test_HistoryLines_MalformedLog_ShowsErrorInsteadOfRaising(self) -> None:
    with support.TempRepo() as repo:
      self.history_repo(repo)
      with open(repo.root / ".slicer" / "log.jsonl", "a", encoding="utf-8") as log:
        log.write("{bad\n")
      state = repo.state()
      lines = tui.history_lines(state, "S01", 50)
      self.assertEqual([line.role for line in lines], ["error"])
      self.assertTrue(lines[0].text.startswith("log.jsonl:"), lines[0].text)
      self.assertIn("not valid JSON", lines[0].text)
      self.assertNotIn("\n", lines[0].text)
      # A frame draws it rather than ending the session.
      view = tui.View.initial(state)
      view.select((tui.ITEM, "S01"))
      view.detail = "history"
      screen = Screen(24, 160)
      tui.draw(screen, state, view)
      self.assertTrue(any("not valid JSON" in text for _, text in screen.writes))

  def test_Refresh_ReadOnlyPaneShown_ReturnsFocusToTheQueue(self) -> None:
    state = linked_example()
    view = tui.View.initial(state)
    view.detail = "deps"
    view.select((tui.PROSE, "preamble"))
    view.handle(state, "\t")
    self.assertEqual(view.focus, "right")
    # The selection moves to an item, which shows the dependencies pane: nothing there to focus.
    view.select((tui.ITEM, "S02"))
    view.refresh(state)
    self.assertEqual(view.shown, "deps")
    self.assertEqual(view.focus, "left")

  def test_Handle_DetailKey_CyclesDetailsDepsHistory(self) -> None:
    state = linked_example()
    view = tui.View.initial(state)
    view.select((tui.ITEM, "S02"))
    self.assertEqual(view.detail, "details")
    view.handle(state, "i")
    self.assertEqual(view.detail, "deps")
    view.handle(state, "i")
    self.assertEqual(view.detail, "history")
    view.handle(state, "i")
    self.assertEqual(view.detail, "details")

  def test_Handle_EditKeyInReadOnlyPanes_SaysReadOnlyAndEditsNothing(self) -> None:
    state = linked_example()
    for detail in ("deps", "history"):
      with self.subTest(detail=detail):
        view = tui.View.initial(state)
        view.select((tui.ITEM, "S02"))
        view.detail = detail
        result = view.handle(state, "e")
        self.assertIsNone(result.edit)
        self.assertIn("read-only", view.message)
        # Only the history pane can take focus for scrolling.
        view.handle(state, "\t")
        self.assertEqual(view.focus, "right" if detail == "history" else "left")
    # Back in the details pane, tab and e work as before.
    view = tui.View.initial(state)
    view.select((tui.ITEM, "S02"))
    view.handle(state, "\t")
    self.assertEqual(view.focus, "right")
    self.assertIsNotNone(view.handle(state, "e").edit)

  def test_Handle_HistoryPaneFocused_DownScrollsHistory(self) -> None:
    for key in ("j", "KEY_DOWN"):
      with self.subTest(key=key):
        state = example()
        view = tui.View.initial(state)
        view.detail = "history"
        view.handle(state, "\t")
        view.handle(state, key)
        self.assertEqual(view.history_at, 1)

  def test_Handle_HistoryPaneFocused_UpScrollsHistory(self) -> None:
    for key, start, expected in (("k", 2, 1), ("KEY_UP", 2, 1), ("k", 0, 0)):
      with self.subTest(key=key, start=start):
        state = example()
        view = tui.View.initial(state)
        view.detail = "history"
        view.history_at = start
        view.handle(state, "\t")
        view.handle(state, key)
        self.assertEqual(view.history_at, expected)

  def test_Handle_SelectOtherItem_ResetsHistoryScroll(self) -> None:
    state = example()
    view = tui.View.initial(state)
    view.detail = "history"
    view.history_at = 7
    view.handle(state, "j")
    self.assertEqual(view.history_at, 0)

  def test_Refresh_SelectOtherItem_ResetsHistoryScroll(self) -> None:
    state = example()
    view = tui.View.initial(state)
    view.history_at = 7
    view.filters.query = "Search full title"
    view.refresh(state)
    self.assertEqual(view.history_at, 0)

  def test_Handle_CycleDetail_ResetsHistoryScroll(self) -> None:
    state = example()
    view = tui.View.initial(state)
    view.detail = "history"
    view.history_at = 7
    view.handle(state, "i")
    self.assertEqual(view.history_at, 0)

  def test_Handle_FocusedHistoryEdit_RemainsReadOnly(self) -> None:
    state = example()
    view = tui.View.initial(state)
    view.detail = "history"
    view.handle(state, "\t")
    self.assertEqual(view.focus, "right")
    with patch.object(tui, "act", side_effect=AssertionError("edit dispatched")):
      self.assertIsNone(view.handle(state, "e").edit)
    self.assertIn("history pane is read-only", view.message)

  def test_Draw_HistoryScrolled_ShowsOlderEntries(self) -> None:
    with support.TempRepo() as repo:
      self.history_repo(repo)
      state = repo.state()
      view = tui.View.initial(state)
      view.select((tui.ITEM, "S02"))
      view.detail = "history"
      view.history_at = 25
      entries = [model.LogEntry(f"2026-10-17T10:00:{n:02d}Z", "S02", "add", "", "open")
                 for n in range(40)]
      screen = Screen(16, 200)
      with patch.object(State, "history", return_value=entries):
        tui.draw(screen, state, view)
      self.assertIn((1, "  " + entries[14].text()), screen.writes)

  def test_Draw_HistoryBeyondEnd_ClampsToLastPage(self) -> None:
    with support.TempRepo() as repo:
      self.history_repo(repo)
      state = repo.state()
      view = tui.View.initial(state)
      view.select((tui.ITEM, "S02"))
      view.detail = "history"
      view.history_at = 999
      entries = [model.LogEntry(f"2026-10-17T10:00:{n:02d}Z", "S02", "add", "", "open")
                 for n in range(40)]
      screen = Screen(16, 200)
      visible = screen.height - len(tui.shortcut_lines(screen.width)) - 3
      with patch.object(State, "history", return_value=entries):
        tui.draw(screen, state, view)
      self.assertIn((1, "  " + entries[visible - 1].text()), screen.writes)

  def test_Handle_DetailKeyLeavingDetails_ReturnsFocusToTheQueue(self) -> None:
    state = linked_example()
    view = tui.View.initial(state)
    view.select((tui.ITEM, "S02"))
    view.handle(state, "\t")
    self.assertEqual(view.focus, "right")
    view.handle(state, "i")
    self.assertEqual(view.focus, "left")

  def test_Handle_DetailKeyOnProseRow_DoesNotCycle(self) -> None:
    state = example()
    view = tui.View.initial(state)
    view.select((tui.PROSE, "preamble"))
    view.handle(state, "i")
    self.assertEqual(view.detail, "details")
    self.assertIn("applies to a slice", view.message)

  def test_Draw_DepsPane_ShowsDependencyLinesUnderItsHeading(self) -> None:
    state = linked_example()
    view = tui.View.initial(state)
    view.select((tui.ITEM, "S02"))
    view.handle(state, "i")
    screen = Screen(24, 100)
    tui.draw(screen, state, view)
    shown = [text for _, text in screen.writes]
    self.assertIn("  Dependencies", shown)
    self.assertIn("  blocked by      S03", shown)
    self.assertNotIn("  Details", shown)
    self.assertNotIn("  status          open", shown)

  def test_Draw_HistoryPane_ShowsLogLinesUnderItsHeading(self) -> None:
    with support.TempRepo() as repo:
      self.history_repo(repo)
      state = repo.state()
      view = tui.View.initial(state)
      view.select((tui.ITEM, "S01"))
      view.detail = "history"
      screen = Screen(24, 160)
      tui.draw(screen, state, view)
      shown = [text for _, text in screen.writes]
      self.assertIn("  History", shown)
      newest = texts(tui.history_lines(state, "S01"))[0]
      # The pane clips a long line at the screen edge, so compare the part that fits.
      self.assertTrue(any(t.startswith("  " + newest[:60]) for t in shown), newest)

  def test_Help_ListsDetailKey(self) -> None:
    self.assertEqual(tui.key_action("i"), "detail")
    self.assertTrue(any(l.startswith("i ") and "dependencies" in l and "history" in l
                        for l in tui.help_lines()))
    self.assertIn("i detail", "  ".join(tui.shortcut_lines(160)))


class RolesTests(unittest.TestCase):
  def setup_roles(self, repo, *, matrix=True):
    repo.run("init")
    repo.run("add", "Role selection", "--size", "S", "--effort", "1")
    repo.run("promote", "S01")
    if matrix:
      repo.run("relay-roles", "init")
    state = repo.state()
    view = tui.View.initial(state)
    view.select((tui.ITEM, "S01"))
    return state, view

  def frame(self, state, view):
    screen = Screen(200, 500)
    tui.draw(screen, state, view)
    return [text for y, text in screen.writes if 0 < y < 198]

  def answer(self, state, view, value):
    for key in value + "\n":
      view.handle(state, key)

  def test_Roles_NoMatrix_NamesRelayRolesInit(self):
    with support.TempRepo() as repo:
      state, view = self.setup_roles(repo, matrix=False)
      _, _, error = repo.run("relay-roles", "show")
      view.handle(state, "R")
      self.assertEqual(view.message, error.strip().removeprefix("slicer: "))

  def test_Roles_Overlay_MatchesRelayRolesShow(self):
    with support.TempRepo() as repo:
      state, view = self.setup_roles(repo)
      _, output, _ = repo.run("relay-roles", "show")
      view.handle(state, "R")
      self.assertEqual(self.frame(state, view), [line for line in output.splitlines() if line])

  def test_Roles_Recommend_SameRowsAsCli(self):
    for well, split in (("yes", "no"), ("no", "yes")):
      with self.subTest(well=well, split=split), support.TempRepo() as repo:
        state, view = self.setup_roles(repo)
        _, output, _ = repo.run("relay-roles", "recommend", "S01",
                                "--well-specified", well, "--split", split)
        view.handle(state, "R")
        view.handle(state, "\n")
        self.answer(state, view, well)
        self.answer(state, view, split)
        self.assertEqual(self.frame(state, view), output.splitlines())

  def test_Roles_Recommend_MatrixErrorThenRetry_ShowsRows(self):
    with support.TempRepo() as repo:
      state, view = self.setup_roles(repo)
      _, output, _ = repo.run("relay-roles", "recommend", "S01",
                              "--well-specified", "yes", "--split", "no")
      view.handle(state, "R")
      view.handle(state, "\n")
      self.answer(state, view, "yes")
      with patch.object(store, "read_roles", side_effect=StateError("invalid relay matrix")):
        self.answer(state, view, "no")
      self.assertEqual(view.message, "invalid relay matrix")
      self.assertEqual(view.roles_prompt.question, "well-specified")
      self.answer(state, view, "yes")
      self.answer(state, view, "no")
      self.assertEqual(self.frame(state, view), output.splitlines())

  def test_Roles_MatrixClose_ClearsMessage(self):
    for key in ("R", "q", "\x1b"):
      with self.subTest(key=key), support.TempRepo() as repo:
        state, view = self.setup_roles(repo)
        view.handle(state, "R")
        view.handle(state, key)
        self.assertEqual((view.mode, view.message), ("normal", ""))

  def test_Roles_Recommend_ProseRow_Refused(self):
    with support.TempRepo() as repo:
      state, view = self.setup_roles(repo)
      repo.run("prose", "set", "preamble", "--text", "Context")
      state = repo.state()
      view.select((tui.PROSE, "preamble"))
      view.handle(state, "R")
      view.handle(state, "\n")
      self.assertEqual((view.mode, view.message),
                       ("normal", "that key applies to a slice, not to a prose block"))

  def test_Roles_Esc_WritesNothing(self):
    with support.TempRepo() as repo:
      state, view = self.setup_roles(repo)
      def snapshot():
        return {p.relative_to(repo.root): p.read_bytes()
                for p in repo.root.rglob("*") if p.is_file()}
      before = snapshot()
      for keys in (("R",), ("R", "\n"), ("R", "\n", "yes\n"),
                   ("R", "\n", "yes\n", "no\n")):
        with patch.object(store, "project_lock", side_effect=AssertionError("read took a lock")):
          for chunk in keys:
            for key in chunk:
              view.handle(state, key)
          self.assertIn(view.mode, ("roles", "roles_prompt"))
          view.handle(state, "\x1b")
        self.assertEqual((view.mode, view.quit, snapshot()), ("normal", False, before))


class RelayRequestTests(unittest.TestCase):
  def setup_request(self, repo):
    repo.run("init")
    for title in ("First", "Second", "Third"):
      repo.run("add", title, "--size", "S", "--effort", "1")
    for target in ("S01", "S02", "S03"):
      repo.run("promote", target)
    repo.run("relay-roles", "init")
    state = repo.state()
    return state, tui.View.initial(state)

  def frame(self, state, view):
    screen = Screen(200, 500)
    tui.draw(screen, state, view)
    return [text for y, text in screen.writes if 0 < y < 198]

  def answer(self, state, view, value):
    for key in value + "\n":
      view.handle(state, key)

  def test_Marks_ToggleAndSurviveRefresh(self):
    with support.TempRepo() as repo:
      state, view = self.setup_request(repo)
      view.handle(state, "m")
      view.refresh(repo.state())
      self.assertTrue(any("[x]" in line and "S01" in line for line in self.frame(state, view)))
      view.handle(state, "m")
      self.assertFalse(any("[x]" in line for line in self.frame(state, view)))
      view.handle(state, "m")
      self.assertFalse(any("[x]" in line for line in self.frame(state, tui.View.initial(state))))

  def test_Marks_ProseRowNotMarkable(self):
    with support.TempRepo() as repo:
      state, view = self.setup_request(repo)
      repo.run("prose", "set", "preamble", "--text", "Context")
      state = repo.state()
      view.refresh(state)
      view.select((tui.PROSE, "preamble"))
      view.handle(state, "m")
      self.assertEqual(view.message, "that key applies to a slice, not to a prose block")
      self.assertFalse(any("[x]" in line for line in self.frame(state, view)))

  def test_RelayRequest_RangeInQueueOrder(self):
    for marked, expected in ((("S03", "S01", "S02"), "run S01..S03 as slice relays"),
                             (("S03", "S01"), "run S01, S03 as slice relays"),
                             ((), "run S01 as slice relays")):
      with self.subTest(marked=marked), support.TempRepo() as repo:
        state, view = self.setup_request(repo)
        for target in marked:
          view.select((tui.ITEM, target))
          view.handle(state, "m")
        view.select((tui.ITEM, "S01"))
        view.handle(state, "Q")
        targets = sorted(marked) if marked else ["S01"]
        output = [expected]
        for target in targets:
          self.answer(state, view, "yes")
          self.answer(state, view, "no")
          _, lines, _ = repo.run("relay-roles", "recommend", target,
                                 "--well-specified", "yes", "--split", "no")
          output += [target] + lines.splitlines()
        self.assertEqual(self.frame(state, view), output)
        view.handle(state, "\x1b")
        self.assertEqual(view.mode, "normal")

  def test_RelayRequest_CanonicalOrderIgnoresSessionSort(self):
    with support.TempRepo() as repo:
      state, view = self.setup_request(repo)
      state.index.get("S03").importance = 3
      for field, descending in (("ranked", True), ("title", False), ("ranked", False)):
        with self.subTest(field=field, descending=descending):
          view.sort_field, view.sort_desc = field, descending
          view.marks = {"S01", "S03"}
          view.refresh(state)
          view.handle(state, "Q")
          output = self.frame(state, view)
          view.handle(state, "\x1b")
          self.assertEqual(output, ["run S03, S01 as slice relays"])

  def test_RelayRequest_CanonicalGapPreventsRange(self):
    with support.TempRepo() as repo:
      state, view = self.setup_request(repo)
      state.index.get("S03").importance = 3
      view.marks = {"S02", "S03"}
      view.handle(state, "Q")
      self.assertEqual(self.frame(state, view), ["run S03, S02 as slice relays"])

  def test_RelayRequest_HiddenStoredGapPreventsRange(self):
    with support.TempRepo() as repo:
      state, view = self.setup_request(repo)
      repo.run("add", "Fourth", "--size", "S", "--effort", "1")
      repo.run("promote", "S04")
      state = repo.state()
      state.index.get("S02").status = "done"
      for field, marked, expected in (
        ("ranked", {"S01", "S03"}, "run S01, S03 as slice relays"),
        ("title", {"S01", "S04"}, "run S01, S04 as slice relays"),
      ):
        with self.subTest(field=field):
          view.sort_field, view.sort_desc = field, False
          view.marks = marked
          view.refresh(state)
          view.handle(state, "Q")
          output = self.frame(state, view)
          view.handle(state, "\x1b")
          self.assertEqual(output, [expected])

  def test_Marks_HiddenByFilterSurviveRefreshAndRequest(self):
    with support.TempRepo() as repo:
      state, view = self.setup_request(repo)
      view.handle(state, "m")
      view.filters.query = "Second"
      view.refresh(state)
      view.handle(state, "Q")
      self.assertEqual(self.frame(state, view), ["run S01 as slice relays"])
      view.handle(state, "\x1b")
      view.filters.query = ""
      view.refresh(state)
      self.assertTrue(any("[x]" in line and "S01" in line for line in self.frame(state, view)))

  def test_RelayRequest_MarkedItemDone_LeftOut(self):
    for status in ("done", "retired"):
      with self.subTest(status=status), support.TempRepo() as repo:
        state, view = self.setup_request(repo)
        for target in ("S01", "S03"):
          view.select((tui.ITEM, target))
          view.handle(state, "m")
        state.index.get("S03").status = status
        view.refresh(state)
        view.select((tui.ITEM, "S01"))
        view.handle(state, "Q")
        self.answer(state, view, "yes")
        self.answer(state, view, "no")
        _, lines, _ = repo.run("relay-roles", "recommend", "S01",
                               "--well-specified", "yes", "--split", "no")
        self.assertEqual(self.frame(state, view),
                         ["run S01 as slice relays", "S01", *lines.splitlines()])
        view.handle(state, "\x1b")
        self.assertEqual(view.mode, "normal")

  def test_RelayRequest_OnlyFinishedMarks_RequestsTheSelectedItem(self):
    for status in ("done", "retired"):
      with self.subTest(status=status), support.TempRepo() as repo:
        state, view = self.setup_request(repo)
        view.select((tui.ITEM, "S03"))
        view.handle(state, "m")
        state.index.get("S03").status = status
        view.refresh(state)
        view.select((tui.ITEM, "S02"))
        view.handle(state, "Q")
        self.answer(state, view, "yes")
        self.answer(state, view, "no")
        _, lines, _ = repo.run("relay-roles", "recommend", "S02",
                               "--well-specified", "yes", "--split", "no")
        self.assertEqual(self.frame(state, view),
                         ["run S02 as slice relays", "S02", *lines.splitlines()])
        view.handle(state, "\x1b")
        self.assertEqual(view.mode, "normal")

  def test_RelayRequest_ReopenedMark_ReturnsToTheRequest(self):
    with support.TempRepo() as repo:
      state, view = self.setup_request(repo)
      view.select((tui.ITEM, "S03"))
      view.handle(state, "m")
      state.index.get("S03").status = "done"
      view.refresh(state)
      state.index.get("S03").status = "open"
      view.refresh(state)
      view.select((tui.ITEM, "S02"))
      view.handle(state, "Q")
      self.answer(state, view, "yes")
      self.answer(state, view, "no")
      _, lines, _ = repo.run("relay-roles", "recommend", "S03",
                             "--well-specified", "yes", "--split", "no")
      self.assertEqual(self.frame(state, view),
                       ["run S03 as slice relays", "S03", *lines.splitlines()])
      view.handle(state, "\x1b")

  def test_RelayRequest_DroppedMarks_NotifyNamesIds(self):
    for status in ("done", "retired"):
      with self.subTest(status=status), support.TempRepo() as repo:
        state, view = self.setup_request(repo)
        for target in ("S01", "S03"):
          view.select((tui.ITEM, target))
          view.handle(state, "m")
        state.index.get("S03").status = status
        view.refresh(state)
        view.select((tui.ITEM, "S01"))
        view.handle(state, "Q")
        self.assertEqual((self.frame(state, view), view.message),
                         (["run S01 as slice relays"], f"left out: S03 ({status})"))
        view.handle(state, "\x1b")
        self.assertEqual(view.mode, "normal")

  def test_RelayRequest_SelectedUnmarkedDone_LeftOut(self):
    for status in ("done", "retired"):
      with self.subTest(status=status), support.TempRepo() as repo:
        state, view = self.setup_request(repo)
        state.index.get("S02").status = status
        view.filters.values["status"] = {status}
        view.refresh(state)
        view.select((tui.ITEM, "S02"))
        view.handle(state, "Q")
        self.assertEqual((view.mode, view.roles_prompt, view.message),
                         ("normal", None, f"nothing to request; left out: S02 ({status})"))

  def test_RelayRequest_ParkedMark_StaysRequested(self):
    for status in ("parked", "review", "reviewing"):
      with self.subTest(status=status), support.TempRepo() as repo:
        state, view = self.setup_request(repo)
        for target in ("S01", "S02", "S03"):
          view.select((tui.ITEM, target))
          view.handle(state, "m")
        state.index.get("S02").status = "done"
        state.index.get("S03").status = status
        view.refresh(state)
        view.select((tui.ITEM, "S01"))
        view.handle(state, "Q")
        self.assertEqual((sorted(view.request_targets), view.mode, view.message),
                         (["S01", "S03"], "request", "left out: S02 (done)"))
        view.handle(state, "\x1b")
        self.assertEqual(view.mode, "normal")

  def test_RelayRequest_OnlyFinishedMarksOnProseRow_SaysSliceOnly(self):
    with support.TempRepo() as repo:
      state, view = self.setup_request(repo)
      view.select((tui.ITEM, "S03"))
      view.handle(state, "m")
      repo.run("prose", "set", "preamble", "--text", "Context")
      state = repo.state()
      state.index.get("S03").status = "done"
      view.refresh(state)
      view.select((tui.PROSE, "preamble"))
      view.handle(state, "Q")
      self.assertEqual((view.mode, view.message),
                       ("normal", "that key applies to a slice, not to a prose block; "
                                  "left out: S03 (done)"))

  def test_Marks_FixedColumnAlignsQueueRows(self):
    with support.TempRepo() as repo:
      state, view = self.setup_request(repo)
      view.handle(state, "m")
      rows = [row.text for row in view.listing if row.kind == tui.ITEM]
      self.assertEqual([row.index(target) for row, target in zip(rows, ("S01", "S02", "S03"))],
                       [rows[0].index("S01")] * 3)

  def test_ItemRows_MarkedRowWideNumber_NoDigitShows(self):
    state = example()
    state.index = Index(items=[Item(f"S{n:04d}", f"Item {n}", "open") for n in range(1, 1002)])
    view = tui.View.initial(state)
    view.marks = {item.id for item in state.index.items}
    view.refresh(state)
    marked = [row for row in view.listing if row.kind == tui.ITEM]
    view.marks = set()
    view.refresh(state)
    plain = [row for row in view.listing if row.kind == tui.ITEM]
    self.assertEqual((len(marked), len(plain)), (1001, 1001))
    for n, (row, bare) in enumerate(zip(marked, plain), 1):
      number = f"{n:>4}"  # 1001 rows: every row's number slot is four cells wide
      with self.subTest(n=n):
        self.assertEqual(row.text[:len(number)], "[x]".rjust(len(number)))
        self.assertEqual(row.text[len(number):], bare.text[len(number):])

  def test_ItemRows_Over999_Aligned(self):
    state = example()
    state.index = Index(items=[Item(f"S{n:04d}", f"Item {n}", "open") for n in range(1, 1002)])
    for marks in (None, {"S0999", "S1000", "S1001"}):
      with self.subTest(marked=marks is not None):
        rows = tui.item_rows(state, state.index.items, marks=marks)
        self.assertEqual(len(rows), 1001)
        columns = {row.text.index(row.target) for row in rows}
        self.assertEqual(columns, {rows[0].text.index(rows[0].target)})

  def test_RelayRequest_HeadingMatchesPromptState(self):
    with support.TempRepo() as repo:
      state, view = self.setup_request(repo)
      view.handle(state, "Q")
      screen = Screen(200, 500)
      tui.draw(screen, state, view)
      self.assertEqual([text for y, text in screen.writes if y == 0],
                       ["Relay request: Esc cancels"])
      self.answer(state, view, "yes")
      self.answer(state, view, "no")
      screen = Screen(200, 500)
      tui.draw(screen, state, view)
      self.assertEqual([text for y, text in screen.writes if y == 0],
                       ["Relay request: j/k scroll, Q, q or Esc close"])

  def test_RelayRequest_UnpromotedItem_SaysPromoteFirst(self):
    with support.TempRepo() as repo:
      state, view = self.setup_request(repo)
      repo.run("add", "Unpromoted")
      state = repo.state()
      view.refresh(state)
      view.select((tui.ITEM, "S04"))
      view.handle(state, "Q")
      self.assertEqual((view.mode, view.message), ("normal", "S04: promote first (press n)"))

  def test_RelayRequest_WritesNothing(self):
    with support.TempRepo() as repo:
      state, view = self.setup_request(repo)
      def snapshot():
        return {p.relative_to(repo.root): p.read_bytes()
                for p in repo.root.rglob("*") if p.is_file()}
      before = snapshot()
      with patch.object(store, "project_lock", side_effect=AssertionError("read took a lock")):
        view.handle(state, "m")
        view.handle(state, "Q")
        self.answer(state, view, "yes")
        self.answer(state, view, "no")
        self.assertIn("run S01 as slice relays", self.frame(state, view))
        view.handle(state, "\x1b")
      self.assertEqual(snapshot(), before)


class QueueColumnsConfigTests(unittest.TestCase):
  """`tui.columns` picks which of claim, size and score the queue row shows, and their order."""

  def sized(self, columns: list[str] | None = None) -> tuple[State, Item]:
    state = example()
    if columns is not None:
      state.config.tui_columns = columns
    item = state.index.require("S02")
    item.size = "M"
    item.claim_owner = "Ada"
    return state, item

  def test_ItemRows_DefaultColumns_MatchS277Layout(self) -> None:
    state, item = self.sized()
    self.assertEqual(state.config.tui_columns, ["claim", "size", "score"])
    row = tui.item_rows(state, [item, state.index.require("S04")])
    self.assertEqual(row[0].text, "  1  S02   —       Ada   M  P:32 Find things")
    self.assertEqual(row[1].text, "  2  S04   started *        P:22 Current work")
    self.assertEqual(row[0].priority_at, row[0].text.index("P:"))

  def test_ItemRows_ColumnsConfigOmitsClaim_NoClaimCell(self) -> None:
    state, item = self.sized(["size", "score"])
    row = tui.item_rows(state, [item])[0]
    self.assertEqual(row.text, "  1  S02   —       M  P:32 Find things")
    self.assertNotIn("Ada", row.text)

  def test_ItemRows_ColumnsConfigOrder_Respected(self) -> None:
    state, item = self.sized(["score", "size", "claim"])
    row = tui.item_rows(state, [item])[0]
    self.assertEqual(row.text, "  1  S02   —       P:32 M  Ada   Find things")

  def test_ItemRows_ColumnsConfigEmpty_ShowsOnlyIdStatusAndTitle(self) -> None:
    state, item = self.sized([])
    row = tui.item_rows(state, [item])[0]
    self.assertEqual(row.text, "  1  S02   —       Find things")
    self.assertFalse(row.high_priority)

  def test_ItemRows_ColumnsConfigOmitsClaim_ClaimColumnWidthNotReserved(self) -> None:
    state, item = self.sized(["score"])
    other = state.index.require("S04")
    rows = tui.item_rows(state, [item, other], {"S02": [{"worktree": "x", "owner": "", "status": "started"}]})
    self.assertEqual([r.text for r in rows], [
      "  1  S02   —       P:32 Find things", "  2  S04   started P:22 Current work",
    ])

  def test_ItemRows_ScoreMoved_PriorityAtFollowsTheScoreCell(self) -> None:
    state, item = self.sized(["score", "claim"])
    row = tui.item_rows(state, [item])[0]
    self.assertEqual(row.text, "  1  S02   —       P:32 Ada   Find things")
    self.assertEqual(row.text[row.priority_at:row.priority_at + 4], "P:32")
    self.assertTrue(row.high_priority)

  def test_ItemRows_NarrowPaneAndNoScoreColumn_ShowsOnlyIdStatusAndTitle(self) -> None:
    state, item = self.sized(["size", "claim"])
    row = tui.item_rows(state, [item], pane_width=tui.NARROW_PANE - 1)[0]
    self.assertEqual(row.text, "  1  S02   —       Find things")


class CatalogTests(unittest.TestCase):
  def setup_catalog(self, repo):
    repo.run("init")
    repo.run("add", "Catalog consumer", "--effort", "1")
    repo.run("catalog", "add", "--kind", "goal", "Keep it simple")
    state = repo.state()
    view = tui.View.initial(state)
    view.select((tui.ITEM, "S01"))
    return state, view

  def snapshot(self, repo):
    return {p.relative_to(repo.root): p.read_bytes()
            for p in repo.root.rglob("*") if p.is_file()}

  def frame(self, state, view, height=80):
    screen = Screen(height, 160)
    tui.draw(screen, state, view)
    return [text for y, text in screen.writes if 0 < y < height - 5]

  def test_Catalog_ListsByKindActiveThenRetired(self):
    state = example()
    state.index.catalog.records = [
      model.CatalogRecord("C03", "constraint", "Bounded"),
      model.CatalogRecord("C01", "goal", "Old goal", status="retired"),
      model.CatalogRecord("C02", "goal", "Current goal"),
    ]
    view = tui.View.initial(state)
    view.handle(state, "C")
    self.assertEqual(self.frame(state, view), [
      "Goals", "> [ ] C02  Current goal", "  [ ] C01  Old goal  retired",
      "Constraints", "  [ ] C03  Bounded",
    ])
    view.handle(state, "j")
    self.assertIn("> [ ] C01  Old goal  retired", self.frame(state, view))
    view.handle(state, "KEY_UP")
    self.assertIn("> [ ] C02  Current goal", self.frame(state, view))
    for key in ("C", "q", "\x1b"):
      view.handle(state, key)
      self.assertEqual((view.mode, view.quit), ("normal", False))
      view.handle(state, "C")

  def test_Catalog_LongList_ScrollsToKeepSelectionVisible(self):
    state = example()
    state.index.catalog.records = [
      model.CatalogRecord(f"C{n:02}", "goal", f"Goal {n}")
      for n in range(1, 16)
    ]
    view = tui.View.initial(state)
    view.handle(state, "C")
    for key, selected in (("j", 15), ("k", 1)):
      with self.subTest(direction=key):
        for _ in range(14):
          view.handle(state, key)
        screen = Screen(12, 160)
        tui.draw(screen, state, view)
        lines = [text for y, text in screen.writes if 0 < y < screen.height - 2]
        self.assertIn(f"> [ ] C{selected:02}  Goal {selected}", lines)

  def test_Catalog_SpaceCites_ThenUncites(self):
    with support.TempRepo() as repo:
      state, view = self.setup_catalog(repo)
      before = len(state.history())
      view.handle(state, "C")
      view.handle(state, " ")
      fresh = repo.state()
      self.assertEqual((fresh.index.require("S01").cites,
                        [(e.item, e.action) for e in fresh.history()[before:]]),
                       (["C01"], [("S01", "cite")]))
      self.assertIn("> [x] C01  Keep it simple", self.frame(state, view))
      view.handle(state, " ")
      fresh = repo.state()
      self.assertEqual((fresh.index.require("S01").cites,
                        [(e.item, e.action) for e in fresh.history()[before:]]),
                       ([], [("S01", "cite"), ("S01", "uncite")]))
      view.handle(state, "q")
      view.handle(state, "r")
      self.assertEqual(repo.run("check")[0], 0)

  def test_Catalog_DuringMerge_RefusesWrite(self):
    with support.TempRepo() as repo:
      state, view = self.setup_catalog(repo)
      before = self.snapshot(repo)
      view.handle(state, "C")
      with patch.object(tui.vcs, "merge_in_progress", return_value=True):
        view.handle(state, " ")
      self.assertIn("merge", view.message)
      self.assertEqual((view.message_severity, self.snapshot(repo)), ("error", before))

  def test_Catalog_ProseRowSelected_SaysNotAnItem(self):
    with support.TempRepo() as repo:
      state, view = self.setup_catalog(repo)
      repo.run("prose", "set", "preamble", "--text", "Context")
      state = repo.state()
      view.select((tui.PROSE, "preamble"))
      before = self.snapshot(repo)
      view.handle(state, "C")
      view.handle(state, " ")
      self.assertIn("not an item", view.message)
      self.assertEqual(self.snapshot(repo), before)
