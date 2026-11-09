"""A write finds `.slicer/render/` stale or conflicted and regenerates it first (s417).

`render/` is generated output that is never parsed back, so whenever `index.json`
loads cleanly a write can rebuild it. A merge in a clone without the merge drivers
leaves the roadmap render holding conflict markers or one side's rows; the next write
heals it, says so on stderr, and needs no per-clone setup. `index.json` is another
matter: with conflict markers in it nothing can be rebuilt, so the write is refused.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import unittest
from unittest.mock import patch

import support
from test_tui_wizard import draft, save

from slicer import render, tui
from slicer.errors import StateError

HEAL_LINE = "slicer: regenerated stale render files before this write: "
MARKERS = "<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> feature\n"


def _render_bytes(repo: support.TempRepo) -> dict[str, bytes]:
  base = repo.root / ".slicer" / "render"
  return {p.relative_to(base).as_posix(): p.read_bytes() for p in sorted(base.rglob("*")) if p.is_file()}


def _mtimes(repo: support.TempRepo) -> dict[str, int]:
  base = repo.root / ".slicer" / "render"
  return {p.relative_to(base).as_posix(): p.stat().st_mtime_ns for p in sorted(base.rglob("*")) if p.is_file()}


def _heal_lines(err: str) -> list[str]:
  return [line for line in err.splitlines() if line.startswith("slicer: regenerated")]


def _conflicted_merge(repo: support.TempRepo) -> None:
  """Merge two branches that each started a neighbouring item, in a clone with no drivers.

  `index.json` merges cleanly (the items are far apart in it); the two adjacent roadmap
  rows conflict, so ROADMAP.md and ROADMAP.html hold markers and the merge is left open.
  """
  repo.run("init")
  for title in ("one", "two", "three"):
    repo.run("add", title, "--effort", "1", "--render")
  repo.commit("base")
  base = repo._git("branch", "--show-current").stdout.strip()
  repo._git("checkout", "-q", "-b", "feature")
  repo.run("start", "S01", "--render")
  repo.commit("feature")
  repo._git("checkout", "-q", base)
  repo.run("start", "S02", "--render")
  repo.commit("main")
  merged = repo._git("merge", "--no-edit", "feature")
  if "CONFLICT (content): Merge conflict in .slicer/render/ROADMAP.md" not in merged.stdout:
    raise AssertionError(merged.stdout + merged.stderr)
  if "<<<<<<<" in repo.read(".slicer/index.json"):
    raise AssertionError("index.json conflicted; the fixture needs it to merge cleanly")


class RenderHealTests(unittest.TestCase):
  def repo(self, *, git: bool = False) -> support.TempRepo:
    repo = support.TempRepo(git=git)
    repo.run("init")
    repo.run("add", "a thing", "--effort", "1")
    repo.run("promote", "S01")
    for section in ("Implement", "Check"):  # so `next --start` finds the item ready
      repo.run("edit", "S01", "--section", section, "--text", "Do it.")
    repo.run("render")
    return repo

  def _tamper(self, repo: support.TempRepo, rel: str, text: str | None = None) -> None:
    path = repo.root / ".slicer/render" / rel
    path.write_text(text if text is not None else path.read_text(encoding="utf-8") + "tampered\n",
                    encoding="utf-8", newline="\n")

  def test_RenderHeal_MergeConflictMarkersInRender_WriteRegeneratesAndSaysSo(self) -> None:
    with support.TempRepo(git=True) as repo:
      _conflicted_merge(repo)
      # What the merged state renders to, before the write: the heal builds this.
      expected = render.plan(repo.state())
      repo.commit("merge, markers and all")
      self.assertIn("<<<<<<<", repo.read(".slicer/render/ROADMAP.md"))
      code, _, err = repo.run("note", "S01", "--text", "x")
      self.assertEqual(code, 0, err)
      for rel in ("ROADMAP.md", "ROADMAP.html"):
        self.assertEqual((repo.root / ".slicer/render" / rel).read_bytes(), expected[rel], rel)
      self.assertEqual(_heal_lines(err), [HEAL_LINE + "ROADMAP.html, ROADMAP.md"], err)
      self.assertEqual(err.count("ROADMAP.md"), 1, err)
      # The note itself is in state, and the render now matches a fresh `slicer render`.
      self.assertEqual(repo.run("render")[2], "")

  def test_RenderHeal_StaleRenderOnly_WriteWithoutRenderFlag_HealsPreexistingStaleness(self) -> None:
    with self.repo() as repo:
      before = render.plan(repo.state())
      self._tamper(repo, "ROADMAP.md")
      code, _, err = repo.run("set", "S01", "--size", "L")
      self.assertEqual(code, 0, err)
      self.assertEqual(_heal_lines(err), [HEAL_LINE + "ROADMAP.md"], err)
      # Healed from the state before the write; the write's own change is not rendered
      # without --render, as today.
      self.assertEqual((repo.root / ".slicer/render/ROADMAP.md").read_bytes(), before["ROADMAP.md"])
      self.assertNotEqual(render.plan(repo.state())["ROADMAP.md"], before["ROADMAP.md"])
      code, out, _ = repo.run("check", "--json")
      self.assertEqual(code, 1)
      # The write's own change is what is stale now, not the pre-existing staleness.
      self.assertIn("ROADMAP.md", json.loads(out)["stale_render"])

  def test_RenderHeal_FreshRender_WritesNothingExtra(self) -> None:
    with self.repo() as repo:
      files, times = _render_bytes(repo), _mtimes(repo)
      code, _, err = repo.run("note", "S01", "--text", "x")
      self.assertEqual(code, 0, err)
      self.assertEqual(err, "")
      self.assertEqual(_mtimes(repo), times)
      self.assertEqual(_render_bytes(repo), files)

  def test_RenderHeal_NoRenderDirectory_WriteDoesNotCreateOne(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      render_dir = repo.root / ".slicer" / "render"
      if render_dir.exists():
        import shutil
        shutil.rmtree(render_dir)
      code, _, err = repo.run("add", "a thing", "--effort", "1")
      self.assertEqual(code, 0, err)
      self.assertEqual(err, "")
      self.assertFalse(render_dir.exists())

  def test_RenderHeal_ConflictedIndex_RefusesAndNamesSetupGit(self) -> None:
    with self.repo() as repo:
      index = repo.root / ".slicer/index.json"
      index.write_text(MARKERS, encoding="utf-8", newline="\n")
      self._tamper(repo, "ROADMAP.md")
      files, before = _render_bytes(repo), index.read_bytes()
      code, out, err = repo.run("note", "S01", "--text", "x", "--json")
      self.assertEqual(code, 3, err)
      error = json.loads(out)["error"]
      self.assertEqual(error["code"], "corrupt")
      self.assertIn("slicer setup-git", error["message"])
      self.assertIn("conflict markers", error["message"])
      self.assertEqual(index.read_bytes(), before)
      self.assertEqual(_render_bytes(repo), files)
      self.assertEqual(_heal_lines(err), [])

  def test_RenderHeal_ConflictedIndex_HintSaysResolveByHand(self) -> None:
    with self.repo() as repo:
      (repo.root / ".slicer/index.json").write_text(MARKERS, encoding="utf-8", newline="\n")
      _, out, _ = repo.run("note", "S01", "--text", "x", "--json")
      message = json.loads(out)["error"]["message"]
      self.assertIn("Resolve them by hand", message)
      self.assertNotIn("--ours", message)

  def test_RenderHeal_ConflictedRelayRoles_HintKeepsOneSideNeverByHand(self) -> None:
    with self.repo() as repo:
      (repo.root / ".slicer/relay-roles.json").write_text(MARKERS, encoding="utf-8", newline="\n")
      code, out, _ = repo.run("relay-roles", "show", "--json")
      self.assertEqual(code, 3)
      error = json.loads(out)["error"]
      self.assertEqual(error["code"], "corrupt")
      self.assertIn("conflict markers", error["message"])
      self.assertIn("git checkout --ours|--theirs .slicer/relay-roles.json", error["message"])
      self.assertIn("Never edit relay-roles.json by hand", error["message"])
      self.assertIn("slicer setup-git", error["message"])
      self.assertNotIn("Resolve them by hand", error["message"])

  def test_RenderHeal_ConflictMarkersInOtherJson_HintHasNoAdvice(self) -> None:
    with self.repo() as repo:
      path = repo.root / ".slicer/slices/S01.json"
      path.write_text(MARKERS, encoding="utf-8", newline="\n")
      code, out, _ = repo.run("show", "S01", "--json")
      message = json.loads(out)["error"]["message"]
      self.assertEqual(code, 3)
      self.assertTrue(message.endswith("it holds merge conflict markers"), message)

  def test_RenderHeal_BadConfig_HandlerReportsAndHealStaysSilent(self) -> None:
    with self.repo() as repo:
      config = json.loads(repo.read(".slicer/config.json"))
      config["open_status"] = "nope"
      repo.write(".slicer/config.json", json.dumps(config))
      code, _, err = repo.run("note", "S01", "--text", "x")
      self.assertEqual(code, 3)
      self.assertEqual(err, "slicer: open_status 'nope' is not in statuses\n")

  def test_RenderHeal_DuringMerge_DoesNotHeal(self) -> None:
    with support.TempRepo(git=True) as repo:
      _conflicted_merge(repo)
      files = _render_bytes(repo)
      self.assertIn(b"<<<<<<<", files["ROADMAP.md"])
      code, out, err = repo.run("note", "S01", "--text", "x", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out)["error"]["code"], "merge_in_progress")
      self.assertEqual(_heal_lines(err), [])
      self.assertEqual(_render_bytes(repo), files)
      # `next --start` is not refused during a merge, so only the heal's own check holds it.
      code, _, err = repo.run("next", "--start", "--max-attempts", "3")
      self.assertEqual(code, 0, err)
      self.assertEqual(_heal_lines(err), [])
      self.assertEqual(_render_bytes(repo), files)

  def test_RenderHeal_ReadOnlyCommands_NeverWrite(self) -> None:
    with self.repo() as repo:
      self._tamper(repo, "ROADMAP.md", MARKERS)
      files, times = _render_bytes(repo), _mtimes(repo)
      for command in (("list",), ("show", "S01"), ("check",), ("status",), ("find", "thing"),
                      ("next",), ("verify",), ("log",)):
        with self.subTest(command=command):
          _, _, err = repo.run(*command)
          self.assertEqual(_heal_lines(err), [])
          self.assertEqual(_render_bytes(repo), files)
          self.assertEqual(_mtimes(repo), times)

  def test_RenderHeal_OtherWritePaths_Heal(self) -> None:
    for command in (
      ("next", "--start"),
      ("next", "--start", "--render"),
      ("next", "--start", "--batch", "1"),
      ("config", "implement_finish", "handoff"),
    ):
      with self.subTest(command=command), self.repo() as repo:
        self._tamper(repo, "ROADMAP.md", MARKERS)
        code, _, err = repo.run(*command)
        self.assertEqual(code, 0, err)
        self.assertEqual(_heal_lines(err), [HEAL_LINE + "ROADMAP.md"], err)
        self.assertNotIn(b"<<<<<<<", (repo.root / ".slicer/render/ROADMAP.md").read_bytes())

  def test_RenderHeal_OrphanRender_IsRemoved(self) -> None:
    with self.repo() as repo:
      orphan = repo.root / ".slicer/render/slices/S99.md"
      orphan.write_text(render.banner("x") + "\n\nstale\n", encoding="utf-8")
      code, _, err = repo.run("note", "S01", "--text", "x")
      self.assertEqual(code, 0, err)
      self.assertFalse(orphan.exists())
      self.assertEqual(_heal_lines(err), [HEAL_LINE + "slices/S99.md"], err)

  def test_Check_ConflictMarkersInRender_AreStale(self) -> None:
    with self.repo() as repo:
      path = repo.root / ".slicer/render/ROADMAP.md"
      text = path.read_text(encoding="utf-8")
      # Markers on the first line leave no banner to read a render format from.
      path.write_text("<<<<<<< HEAD\n" + text + "=======\nother\n>>>>>>> feature\n",
                      encoding="utf-8", newline="\n")
      code, out, _ = repo.run("check", "--json")
      report = json.loads(out)
      self.assertEqual(code, 1)
      self.assertEqual(report["stale_render"], ["ROADMAP.md"])
      detail = report["stale_render_details"][0]
      self.assertEqual(detail["cause"], "conflict_markers")
      self.assertIn("conflict markers", detail["detail"])
      self.assertEqual(report["stale_render_kind"], "roadmap_only")
      code, out, _ = repo.run("check")
      self.assertIn("holds merge conflict markers", out)
      self.assertIn("any slicer write regenerates it", out)


TUI_HEAL = "regenerated stale render files before this write: "


class TuiRenderHealTests(unittest.TestCase):
  """A TUI write heals `render/` as a CLI write does, and says so in the status line (s429)."""

  def repo(self, *, git: bool = False) -> support.TempRepo:
    repo = support.TempRepo(git=git)
    repo.run("init")
    repo.run("add", "a thing", "--effort", "1")
    repo.run("add", "another", "--effort", "1")
    repo.run("render")
    return repo

  def _tamper(self, repo: support.TempRepo) -> None:
    path = repo.root / ".slicer/render/ROADMAP.md"
    path.write_text(path.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8", newline="\n")

  def _assert_healed(self, repo: support.TempRepo, before: dict[str, bytes], message: str) -> None:
    self.assertIn(TUI_HEAL + "ROADMAP.md", message)
    # Healed from the state before the write, as the CLI does; the write's own change waits for `r`.
    self.assertEqual((repo.root / ".slicer/render/ROADMAP.md").read_bytes(), before["ROADMAP.md"])

  def test_Tui_WriteWithStaleRender_HealsAndSaysSo(self) -> None:
    with self.repo() as repo:
      before = render.plan(repo.state())
      self._tamper(repo)
      state = repo.state()
      err = io.StringIO()
      with contextlib.redirect_stderr(err):
        result = tui.apply_edit_result(state, tui.EditRequest("note_new", "S01", "add a note", ""), "tried X")
      self.assertEqual(result.severity, "success", result.message)
      self.assertTrue(repo.state().index.require("S01").notes[-1].endswith("tried X"))
      self._assert_healed(repo, before, result.message)
      self.assertEqual(err.getvalue(), "")
      view = tui.View.initial(state)
      view.accept(result)
      self.assertIn(TUI_HEAL + "ROADMAP.md", view.feedback())

  def test_Tui_FreshRender_SaysNothingExtra(self) -> None:
    with self.repo() as repo:
      files, times = _render_bytes(repo), _mtimes(repo)
      result = tui.apply_edit_result(
        repo.state(), tui.EditRequest("note_new", "S01", "add a note", ""), "tried X")
      self.assertEqual(result.message, "added note; press r to render")
      self.assertEqual((_render_bytes(repo), _mtimes(repo)), (files, times))

  def test_Tui_KeyWriteWithStaleRender_HealsAndSaysSo(self) -> None:
    with self.repo() as repo:
      before = render.plan(repo.state())
      self._tamper(repo)
      state = repo.state()
      result = tui.act(state, "s", "S01")
      self.assertEqual(result.severity, "success", result.message)
      self.assertTrue(result.message.startswith("S01 started"), result.message)
      self._assert_healed(repo, before, result.message)

  def _prompt_view(self, repo: support.TempRepo, *setup: tuple[str, ...]) -> tuple[object, tui.View]:
    """Run CLI `setup` commands, render, tamper with the render and open a view on S01."""
    for command in setup:
      repo.run(*command)
    repo.run("render")
    self._tamper(repo)
    state = repo.state()
    view = tui.View.initial(state)
    view.filters = tui.Filters()  # a move is refused while a filter is active
    view.refresh(state)
    view.select((tui.ITEM, "S01"))
    return state, view

  def test_Tui_PromptWritesWithStaleRender_HealAndSaySo(self) -> None:
    for name, setup, keys in (
      ("move", (), "M2\n"),
      ("handoff", (("promote", "S01"), ("start", "S01")), "h\n"),
      ("catalog", (("catalog", "add", "--kind", "goal", "Keep it simple"),), "C "),
    ):
      with self.subTest(prompt=name), self.repo() as repo:
        state, view = self._prompt_view(repo, *setup)
        before = render.plan(repo.state())
        for key in keys:
          view.handle(state, key)
        self._assert_healed(repo, before, view.message)

  def test_Tui_RetractWithStaleRender_HealsAndSaysSo(self) -> None:
    with self.repo() as repo:
      state, view = self._prompt_view(repo, ("note", "S01", "--text", "wrong"))
      before = render.plan(repo.state())
      record = state.index.require("S01").note_records[0]
      view.accept(tui.ActResult("give a reason", retract=("S01", record.id)))
      for key in "typo\n":
        view.handle(state, key)
      self.assertTrue(repo.state().index.require("S01").note_records[0].retracted)
      self._assert_healed(repo, before, view.message)

  def test_Tui_WizardSaveWithStaleRender_HealsAndSaysSo(self) -> None:
    with self.repo() as repo:
      state = repo.state()
      view = tui.View.initial(state)
      wizard = draft(list(state.config.sections))
      view.wizard, view.mode = wizard, "wizard"
      self._tamper(repo)
      save(view, state)
      self.assertEqual(view.mode, "normal", view.message)
      self.assertIn(TUI_HEAL + "ROADMAP.md", view.message)
      self.assertTrue(view.message.startswith("saved 1 item(s) and slices"), view.message)
      self.assertEqual(repo.run("check")[0], 0)  # the wizard's own render follows the heal

  def test_Tui_WizardSaveThatRaisesAfterHeal_StillSaysItHealed(self) -> None:
    with self.repo() as repo:
      state = repo.state()
      view = tui.View.initial(state)
      view.wizard, view.mode = draft(list(state.config.sections)), "wizard"
      self._tamper(repo)
      with patch("slicer.ops.apply_outline", side_effect=StateError("boom")):
        save(view, state)
      self.assertIsNotNone(view.wizard)
      self.assertTrue(view.wizard.error.startswith("boom; "), view.wizard.error)
      self.assertIn(TUI_HEAL + "ROADMAP.md", view.wizard.error)

  def _stale_state(self, repo: support.TempRepo) -> object:
    """S01 promoted with a note, S02 beside it, the render current, then ROADMAP.md tampered with."""
    repo.run("promote", "S01")
    repo.run("note", "S01", "--text", "first")
    repo.run("render")
    self._tamper(repo)
    return repo.state()

  def test_Tui_EveryKeyWrite_WithStaleRender_HealsAndSaysSo(self) -> None:
    for key, target in (("l", "S01"), ("J", "S01"), ("K", "S02"), ("T", "S02"), ("n", "S02"),
                        ("p", "S01"), ("d", "S01")):
      with self.subTest(key=key), self.repo() as repo:
        state = self._stale_state(repo)
        result = tui.act(state, key, target)
        self.assertNotEqual(result.severity, "error", result.message)
        self.assertIn(TUI_HEAL + "ROADMAP.md", result.message)

  def test_Tui_EveryEditKind_WithStaleRender_HealsAndSaysSo(self) -> None:
    section = "Why"
    for request, body in (
      (tui.EditRequest("new", "", "new item", ""), "a third thing"),
      (tui.EditRequest(tui.PROSE, "preamble", "preamble", ""), "new context"),
      (tui.EditRequest("lead", "S01", "lead", ""), "A lead."),
      (tui.EditRequest("boundary", "S01", "boundary", ""), "Not in this slice: x."),
      (tui.EditRequest("field", "S01", "size", ""), "L"),
      (tui.EditRequest("note", "S01", "note 1", "first", index=0), "revised"),
      (tui.EditRequest("note_new", "S01", "add a note", ""), "second"),
      (tui.EditRequest("section", "S01", section, ""), "Because."),
    ):
      with self.subTest(kind=request.kind), self.repo() as repo:
        state = self._stale_state(repo)
        result = tui.apply_edit_result(state, request, body)
        self.assertEqual(result.severity, "success", result.message)
        self.assertIn(TUI_HEAL + "ROADMAP.md", result.message)

  def test_Tui_WriteDuringMerge_DoesNotHeal(self) -> None:
    with support.TempRepo(git=True) as repo:
      _conflicted_merge(repo)
      files = _render_bytes(repo)
      state = repo.state()
      result = tui.apply_edit_result(state, tui.EditRequest("note_new", "S01", "add a note", ""), "x")
      self.assertEqual(result.severity, "error", result.message)
      self.assertNotIn(TUI_HEAL, result.message)
      self.assertEqual(_render_bytes(repo), files)

  def test_Tui_HealThatCannotWrite_StillLandsTheWriteAndWarns(self) -> None:
    with self.repo() as repo:
      self._tamper(repo)
      state = repo.state()
      with patch("slicer.render.write_atomic", side_effect=OSError("disk full")):
        result = tui.apply_edit_result(state, tui.EditRequest("note_new", "S01", "add a note", ""), "x")
      self.assertEqual(result.severity, "success", result.message)
      self.assertIn("could not regenerate the stale render: disk full", result.message)
      self.assertTrue(repo.state().index.require("S01").notes[-1].endswith("x"))


if __name__ == "__main__":
  unittest.main()
