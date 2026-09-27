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


def type_keys(view: tui.View, state: State, text: str) -> None:
  for key in text:
    view.handle(state, key)


class TuiFilteringTests(unittest.TestCase):
  def test_InitialView_Defaults_HidesOnlyDone(self) -> None:
    state = example()
    view = tui.View.initial(state)
    self.assertEqual(item_ids(view), ['S02', 'S03', 'S04'])
    self.assertTrue(view.status(state).startswith('3/4 items'))
    self.assertEqual(len([r for r in tui.rows(state) if r.kind == tui.ITEM]), 4)
    view.handle(state, 'c')
    self.assertEqual(item_ids(view), ['S01', 'S02', 'S03', 'S04'])
    self.assertFalse(view.filters.active)
    self.assertEqual(item_ids(tui.View.initial(state)), ['S02', 'S03', 'S04'])

  def test_InitialView_CustomDoneStatus_UsesConfiguration(self) -> None:
    state = example()
    state.config.statuses = {'todo': '-', 'shipped': 'done', 'waiting': 'waiting'}
    state.config.done_status = 'shipped'
    for item, status in zip(state.index.items, ['shipped', 'todo', 'waiting', 'todo']):
      item.status = status
    self.assertEqual(item_ids(tui.View.initial(state)), ['S02', 'S03', 'S04'])
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
    self.assertTrue(view.listing[0].text.lstrip().startswith('2'))

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
    self.assertEqual(item_ids(view), ['S01', 'S02'])
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
    type_keys(view, state, 'current')
    self.assertEqual(view.target, 'S04')
    view.handle(state, '\x1b')
    self.assertEqual(view.selected, previous)
    self.assertEqual(view.filters.query, '')
    self.assertFalse(view.quit)
    view.handle(state, '/')
    type_keys(view, state, 'find')
    for _ in range(4):
      view.handle(state, 'KEY_BACKSPACE')
    view.handle(state, '\n')
    self.assertEqual(item_ids(view), ['S02', 'S03', 'S04'])

  def test_FilterPanel_ApplyCancelAndAny_AreIndependentOfLiveFilters(self) -> None:
    state = example()
    view = tui.View.initial(state)
    view.handle(state, 'f')
    view.choice_at = tui.filter_choices(state).index(('status', None))
    view.handle(state, ' ')
    self.assertEqual(item_ids(view), ['S02', 'S03', 'S04'])
    view.handle(state, '\x1b')
    self.assertEqual(item_ids(view), ['S02', 'S03', 'S04'])
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
    self.assertEqual(item_ids(view), ['S01', 'S02', 'S03', 'S04'])


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
    with patch.object(tui, 'rows', return_value=[]):
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
             'f filters', 'c show all', 'g jump', 'j/k move', '? help', 'q quit']
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
            self.assertIn((height - rows - 2, view.status(state)), screen.writes)

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
