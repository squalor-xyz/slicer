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
    self.assertTrue(summary[0].text.startswith("✓ s283"), summary[0].text)
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
