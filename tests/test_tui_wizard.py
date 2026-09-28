"""Prove unfinished wizard answers cannot leak into project state."""

from __future__ import annotations

import curses
import os
from contextlib import ExitStack
import unittest
from unittest.mock import patch

import support
from slicer import jsonio, ops, outline, store, tui, tui_wizard
from slicer.config import Config
from slicer.model import Index
from test_tui import Screen


def answer(wizard, value):
  for _ in wizard.text:
    wizard.handle("KEY_BACKSPACE")
  for key in value:
    wizard.handle(key)
  wizard.handle("\n")


def fill_item(wizard, title="First", depends=""):
  for value in (title, "M", "core, ui", "F1", "3", "1", "Phase one", depends):
    answer(wizard, value)
  while wizard.mode == "field":
    wizard.handle("\n")


def draft(headings, title="", item_title="First"):
  wizard = tui_wizard.Wizard(headings)
  answer(wizard, title)
  fill_item(wizard, item_title)
  wizard.handle("n")
  return wizard


def save(view, state):
  view.wizard.mode = "review"
  view.wizard.review_at = len(view.wizard.review_rows()) - 1
  view.handle(state, "\n")


def snapshot(repo):
  return {str(p.relative_to(repo.root)): p.read_bytes()
          for p in (repo.root / ".slicer").rglob("*") if p.is_file() and p.name != "lock"}


class WizardInputTests(unittest.TestCase):
  def test_TextInput_UnicodeAndControlKeys_PreservesOnlyPrintableInput(self):
    text = ""
    for key in ("界", "é", " ", "x", "KEY_LEFT", "\x03", "KEY_RESIZE"):
      text, intent = tui_wizard.text_input(text, key)
      self.assertEqual(intent, "")
    self.assertEqual(text, "界é x")
    for key in tui_wizard.BACKSPACE:
      text, _ = tui_wizard.text_input(text, key)
    self.assertEqual(text, "界")
    for key, expected in (("\n", "accept"), ("\r", "accept"),
                          ("KEY_ENTER", "accept"), ("\x1b", "cancel"),
                          ("KEY_BTAB", "back")):
      self.assertEqual(tui_wizard.text_input(text, key), (text, expected))
    self.assertEqual(tui_wizard.text_input("", "\b"), ("", ""))

  def test_Wizard_RequiredTitleAndPriority_KeepInvalidAnswerForCorrection(self):
    wizard = tui_wizard.Wizard([])
    answer(wizard, "")
    answer(wizard, "   ")
    self.assertEqual(wizard.step, 0)
    self.assertIn("required", wizard.error)
    for value in ("First", "", "", ""):
      answer(wizard, value)
    answer(wizard, "4")
    self.assertEqual(wizard.step, 4)
    self.assertIn("1, 2 or 3", wizard.error)
    answer(wizard, "1")
    self.assertEqual(wizard.step, 5)

  def test_Wizard_BackAndReviewEditing_PreserveAnswers(self):
    wizard = draft(["Why"], title="Roadmap")
    wizard.review_at = 2  # first item's size
    wizard.handle("\n")
    self.assertEqual(wizard.text, "M")
    answer(wizard, "L")
    self.assertEqual(wizard.mode, "review")
    self.assertEqual(wizard.specs()[0].size, "L")
    wizard.review_at = 0
    wizard.handle("\n")
    answer(wizard, "Changed")
    self.assertEqual(wizard.mode, "review")
    self.assertEqual(wizard.preamble("Existing\nprose\n"), "# Changed\n\nExisting\nprose\n")
    wizard.review_at = 1
    wizard.handle("\n")
    wizard.handle("KEY_BTAB")
    self.assertEqual(wizard.mode, "review")

  def test_Wizard_SequentialBack_VisitsPreviousItemWithContentIntact(self):
    wizard = tui_wizard.Wizard(["Why"])
    answer(wizard, "")
    fill_item(wizard)
    wizard.handle("y")
    wizard.handle("KEY_BTAB")
    self.assertEqual(wizard.item_at, 0)
    self.assertEqual(wizard.section.heading, "Why")
    wizard.handle("\n")
    self.assertEqual((wizard.item_at, wizard.step), (1, 0))
    wizard.handle("KEY_BTAB")
    wizard.handle("KEY_BTAB")
    self.assertEqual(wizard.step, len(tui_wizard.FIELDS) - 1)
    self.assertEqual(wizard.items[0].values[0], "First")

  def test_Wizard_EditorFailureAndResize_KeepBodyAndStep(self):
    wizard = tui_wizard.Wizard(["Why"])
    answer(wizard, "")
    for value in ("First", "", "", "", "2", "2", "", "", ""):
      answer(wizard, value)
    self.assertEqual(wizard.handle("e"), "editor")
    wizard.editor_result("One\n\nTwo")
    wizard.editor_result(None)
    self.assertIn("unchanged", wizard.error)
    wizard.handle("KEY_RESIZE")
    self.assertEqual(wizard.section.body, "One\n\nTwo")
    self.assertIn("unchanged", wizard.error)
    wizard.handle("\n")
    self.assertEqual(wizard.mode, "another")

  def test_Wizard_DiscardConfirmation_CanResumeOrDiscard(self):
    wizard = draft([])
    wizard.handle("\x1b")
    self.assertEqual(wizard.mode, "discard")
    wizard.handle("n")
    self.assertEqual(wizard.mode, "review")
    wizard.handle("\x1b")
    wizard.handle("\x1b")
    self.assertEqual(wizard.mode, "review")
    wizard.handle("\x1b")
    self.assertEqual(wizard.handle("y"), "cancel")
    self.assertEqual(tui_wizard.Wizard([]).handle("\x1b"), "cancel")


class WizardPersistenceTests(unittest.TestCase):
  def repo(self):
    repo = support.TempRepo()
    self.addCleanup(repo.close)
    self.assertEqual(repo.run("init")[0], 0)
    return repo

  def view(self, state, title=""):
    view = tui.View.initial(state)
    view.handle(state, "w")
    view.wizard = draft(list(state.config.sections), title)
    return view

  def test_Wizard_MultipleItems_MatchesEquivalentImport(self):
    repo, imported = self.repo(), self.repo()
    state = repo.state()
    wizard = tui_wizard.Wizard(list(state.config.sections))
    answer(wizard, "")
    fill_item(wizard, "First", "Second")
    wizard.items[0].sections[0].body = "Because."
    wizard.handle("y")
    fill_item(wizard, "Second")
    view = tui.View.initial(state)
    view.wizard, view.mode = wizard, "wizard"
    save(view, state)
    self.assertIsNone(view.wizard)
    self.assertEqual(view.target, "S01")
    self.assertEqual(repo.run("check")[0], 0)
    imported.write("outline.md", "\n\n".join(
      f"## {title}\nsize: M\ntrees: core, ui\nfindings: F1\nimportance: 3\n"
      f"urgency: 1\ngroup: Phase one\n{depends}\n### Why\n{body}"
      for title, depends, body in (("First", "depends: Second", "Because."),
                                   ("Second", "", ""))))
    self.assertEqual(imported.run("import", "outline.md", "--render")[0], 0)
    expected = imported.state()
    self.assertEqual(state.index.to_dict(), expected.index.to_dict())
    self.assertEqual({k: v.to_dict() for k, v in state.slices.items()},
                     {k: v.to_dict() for k, v in expected.slices.items()})

  def test_Wizard_CancelAfterEditor_WritesNothing(self):
    repo = self.repo()
    state = repo.state()
    before = snapshot(repo)
    view = self.view(state, "Heading")
    view.wizard.items[0].sections[0].body = "Unfinished content"
    view.handle(state, "\x1b")
    view.handle(state, "y")
    self.assertIsNone(view.wizard)
    self.assertEqual(snapshot(repo), before)
    self.assertEqual(state.index.to_dict(), repo.state().index.to_dict())

  def test_Wizard_ValidationFailures_PreserveStateAndDraftForRetry(self):
    for bad_field, value in ((0, ""), (4, "0"), (7, "Missing")):
      with self.subTest(field=bad_field):
        repo = self.repo()
        state = repo.state()
        before = snapshot(repo)
        view = self.view(state, "Heading")
        original = view.wizard.items[0].values[bad_field]
        view.wizard.items[0].values[bad_field] = value
        save(view, state)
        self.assertIsNotNone(view.wizard)
        self.assertTrue(view.wizard.error)
        self.assertEqual(snapshot(repo), before)
        self.assertEqual(state.index.to_dict(), repo.state().index.to_dict())
        view.wizard.items[0].values[bad_field] = original
        save(view, state)
        self.assertIsNone(view.wizard)
        self.assertEqual(state.index.items[0].id, "S01")

  def test_Wizard_ConcurrentAddition_ReloadsBeforeDuplicateValidation(self):
    repo = self.repo()
    state = repo.state()
    view = self.view(state, "Heading")
    repo.run("add", "First")
    before = snapshot(repo)
    save(view, state)
    self.assertIn("already exists", view.wizard.error)
    self.assertEqual(snapshot(repo), before)
    view.wizard.items[0].values[0] = "Second"
    view.wizard.items[0].values[7] = "First"
    save(view, state)
    self.assertEqual([i.title for i in state.index.items], ["First", "Second"])
    self.assertEqual(state.index.items[1].depends_on, ["S01"])
    self.assertEqual(state.index.items[1].id, "S02")

  def test_Wizard_SliceOrIndexWriteFailure_PreservesHeadingAndAllowsRetry(self):
    for method in ("save_slice", "save_index"):
      with self.subTest(method=method):
        repo = self.repo()
        repo.run("prose", "edit", "preamble", "--text", "Existing prose")
        state = repo.state()
        before = snapshot(repo)
        view = self.view(state, "Heading")
        with patch.object(store.State, method, side_effect=OSError("disk full")):
          save(view, state)
        self.assertIn("disk full", view.wizard.error)
        self.assertEqual(snapshot(repo), before)
        self.assertEqual(state.index.to_dict(), repo.state().index.to_dict())
        save(view, state)
        self.assertEqual(state.index.preamble, "# Heading\n\nExisting prose")
        self.assertEqual(state.index.items[0].id, "S01")

  def test_Wizard_PostCommitFailure_ClosesDraftToPreventDuplicateSave(self):
    for target in ("slicer.render.plan", "slicer.store.State.log"):
      with self.subTest(target=target):
        repo = self.repo()
        state = repo.state()
        view = self.view(state)
        with patch(target, side_effect=OSError("disk full")):
          save(view, state)
        self.assertIsNone(view.wizard)
        self.assertIn("saved", view.message)
        self.assertIn("disk full", view.message)
        view.handle(state, "\n")
        self.assertEqual(len(repo.state().index.items), 1)

  def test_Wizard_DuplicateDraftTitles_RejectsWholeBatch(self):
    repo = self.repo()
    state = repo.state()
    view = self.view(state)
    view.wizard.add_item()
    fill_item(view.wizard)
    before = snapshot(repo)
    save(view, state)
    self.assertIn("appears twice", view.wizard.error)
    self.assertEqual(snapshot(repo), before)

  def test_Wizard_LockTimeout_KeepsDraftWithoutWriting(self):
    if store.fcntl is None:
      self.skipTest("no flock on this platform")
    repo = self.repo()
    state = repo.state()
    view = self.view(state)
    before = snapshot(repo)
    with store.project_lock(repo.root):
      with patch.dict(os.environ, {store.LOCK_TIMEOUT_ENV: "0"}):
        save(view, state)
    self.assertIn("lock timed out", view.wizard.error)
    self.assertEqual(snapshot(repo), before)
    save(view, state)
    self.assertIsNone(view.wizard)

  def test_ApplyOutline_SecondSliceWriteFails_LiveStateAndFilesRemainUnchanged(self):
    repo = self.repo()
    state = repo.state()
    before = snapshot(repo)
    index_before = state.index.to_dict()
    original = store.State.save_slice
    calls = []

    def fail_second(staged, sl):
      calls.append(sl.id)
      if len(calls) == 2:
        raise OSError("disk full")
      return original(staged, sl)

    specs = [outline.ItemSpec("First"), outline.ItemSpec("Second")]
    with patch.object(store.State, "save_slice", fail_second):
      with self.assertRaises(OSError):
        ops.apply_outline(state, specs, preamble="# Heading", promote_all=True)
    self.assertEqual(state.index.to_dict(), index_before)
    self.assertEqual(state.slices, {})
    self.assertEqual(snapshot(repo), before)
    report = ops.apply_outline(state, specs, preamble="# Heading", promote_all=True)
    self.assertEqual(report.ids, ["S01", "S02"])

  def test_Wizard_CustomStatusAndNoHeadings_CreatesEmptySlice(self):
    repo = self.repo()
    state = repo.state()
    state.config.sections = []
    state.config.statuses["todo"] = "todo"
    state.config.open_status = "todo"
    jsonio.write(state.dir / "config.json", state.config.to_dict())
    view = self.view(state)
    save(view, state)
    self.assertEqual(state.index.items[0].status, "todo")
    self.assertTrue(state.index.items[0].has_slice)
    self.assertEqual(state.slices["S01"].sections, [])
    self.assertEqual(repo.run("check")[0], 0)

  def test_ApplyOutline_RejectedLaterItem_DoesNotConsumeIdsInMemory(self):
    repo = self.repo()
    state = repo.state()
    before = state.index.to_dict()
    report = ops.apply_outline(state, [outline.ItemSpec("Good"), outline.ItemSpec(" ")],
                               preamble="# Heading", promote_all=True)
    self.assertTrue(report.problems)
    self.assertEqual(state.index.to_dict(), before)
    self.assertEqual(repo.state().index.to_dict(), before)


class WizardDrawingTests(unittest.TestCase):
  def test_Wizard_EmptyOffer_OnlyAppearsOnLaunchAndCanBeDeclined(self):
    state = store.State(support.SRC.parent, Config(), Index())
    view = tui.View.initial(state, offer_wizard=True)
    self.assertEqual(view.mode, "wizard_offer")
    screen = Screen(10, 80)
    tui.draw(screen, state, view)
    self.assertIn("Start the guided wizard", str(screen.writes))
    view.handle(state, "n")
    view.handle(state, "KEY_RESIZE")
    self.assertEqual(view.mode, "normal")
    view.handle(state, "w")
    self.assertIsNotNone(view.wizard)

  def test_Wizard_LongUnicodeAndReview_StayWithinTerminalBounds(self):
    state = store.State(support.SRC.parent, Config(), Index())
    view = tui.View.initial(state)
    view.handle(state, "w")
    view.wizard.title = "界é" * 200
    for height, width in ((10, 80), (24, 120), (5, 40)):
      tui.draw(Screen(height, width), state, view)
    view.wizard = draft(["Why"], "界" * 200)
    view.wizard.review_at = len(view.wizard.review_rows()) - 1
    screen = Screen(10, 80)
    tui.draw(screen, state, view)
    self.assertIn("Save roadmap", str(screen.writes))
    self.assertEqual(view.wizard.title, "界" * 200)


class WizardLoopTests(unittest.TestCase):
  def test_Run_UnicodeKeysAndEditor_ReturnToWizardThenSave(self):
    with support.TempRepo() as repo:
      repo.run("init")
      # Start, set heading, use Shift-Tab and return, fill fields, edit Why,
      # skip the remaining sections, review, save, and quit.
      keys = iter([*"\nCafé\n", curses.KEY_BTAB, "\n", *"界\n",
                   *"\n" * 8, "e", *"\n" * 6, "n", *"j" * 17, "\n", "q"])

      class Terminal(Screen):
        def get_wch(self):
          return next(keys)

        def redrawwin(self):
          pass

      screen = Terminal(24, 80)
      with ExitStack() as patches:
        for name in ("curs_set", "def_prog_mode", "endwin", "reset_prog_mode"):
          patches.enter_context(patch(f"curses.{name}"))
        patches.enter_context(patch("curses.wrapper", side_effect=lambda fn: fn(screen)))
        patches.enter_context(patch("curses.keyname", return_value=b"KEY_BTAB"))
        patches.enter_context(patch("slicer.tui_style.setup_palette", return_value=tui.tui_style.monochrome()))
        editor = patches.enter_context(patch("slicer.cli._via_editor", return_value="First line\nSecond line"))
        self.assertEqual(tui.run(repo.state()), 0)
        editor.assert_called_once_with("")
      state = repo.state()
      self.assertEqual(state.index.preamble, "# Café")
      self.assertEqual(state.index.items[0].title, "界")
      self.assertEqual(state.slices["S01"].sections[0].body, "First line\nSecond line")
      self.assertEqual(repo.run("check")[0], 0)
