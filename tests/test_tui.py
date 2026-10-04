"""Exercise navigation and filtered actions without requiring a terminal."""

from __future__ import annotations

import curses
from pathlib import Path
import unittest
from unittest.mock import patch

import support
from slicer import tui, tui_style
from slicer.config import Config
from slicer.model import Index, Item
from slicer.store import State


class MemoryState(State):
  @property
  def dir(self) -> Path:
    raise AssertionError("in-memory TUI fixtures cannot access tracking files; use TempRepo for actions")


def example() -> State:
  return MemoryState(Path.cwd(), Config.from_dict({}), Index(items=[
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
    hints = ['Tab panes', 'e edit', 'a add', 's start', 'd done', '/ search',
             'f filters', 'c show all', 'g jump', 'j/k move', '? help', 'q quit', 'v view']
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
      for mode in ['normal', 'search', 'jump', 'move', 'filter', 'help']:
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
    item.notes = ["**2026-01-01** — first", "second"]
    ents = tui.entries(state, "S02")
    notes = [e for e in ents if e.kind == "note"]
    self.assertEqual([e.index for e in notes], [0, 1])
    self.assertEqual(ents[-1].kind, "note_new")
    self.assertIsNone(ents[-1].index)

  def test_Panel_AddLineTagMatchesTheLastEntry(self) -> None:
    state = example()
    item = state.index.require("S02")
    item.notes = ["a note"]
    ents = tui.entries(state, "S02")
    panel = tui.panel(state, "S02")
    self.assertTrue(any(l.role == "heading" and l.text == "Notes" for l in panel))
    add = next(l for l in panel if l.text == "+ add a note")
    self.assertEqual(add.entry, len(ents) - 1)
    # the note's body line is tagged with its own entry index
    note_entry = next(e_i for e_i, e in enumerate(ents) if e.kind == "note")
    self.assertTrue(any(l.text == "a note" and l.entry == note_entry for l in panel))


class ClaimDisplayTests(unittest.TestCase):
  def test_Panel_ClaimOwnerOrDash_IsNotAnEntry(self) -> None:
    state = example()
    state.index.require("S02").claim_owner = "Ada Lovelace"
    claimed = tui.panel(state, "S02")
    line = next(l for l in claimed if l.text.startswith("claim"))
    self.assertEqual(line.text, "claim      Ada Lovelace")
    self.assertIsNone(line.entry)
    self.assertEqual(line.role, "normal")
    plain = tui.panel(state, "S04")
    self.assertTrue(any(l.text == "claim      -" and l.entry is None for l in plain))
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

  def test_Rows_BlockedReviewAndParked_ShowMarkerWithoutAClaimColumn(self) -> None:
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
    for row in rows:
      self.assertNotIn("Ada", row.text)
      self.assertNotIn("claim", row.text)
    self.assertEqual(tui.key_action("v"), "view")
    self.assertEqual([b.action for b in tui.BINDINGS if "v" in b.keys], ["view"])


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
