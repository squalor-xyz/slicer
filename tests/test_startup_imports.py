"""Startup stays cheap: `--version` and `--help` load no command handler, and a read loads only what it uses.

Every slicer process used to import every handler, the store, the checker and the renderer
before it parsed a flag. The guard is on module names, not on time, because time is not
stable in CI. Each import test runs a fresh interpreter, since `sys.modules` in this process
is already full.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

import support

from slicer import cli

SRC = support.SRC

_SCRIPT = """
import contextlib, io, json, sys
from slicer import cli
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
  try:
    cli.main(json.loads(sys.argv[1]))
  except SystemExit:
    pass
print(json.dumps(sorted(sys.modules)))
"""

# The modules that hold command handlers: every `cli_*` module but the ones that build the
# parser or hold what the handlers share.
HANDLERS = tuple(sorted(
  f"slicer.{path.stem}" for path in (SRC / "slicer").glob("cli_*.py")
  if path.stem not in {"cli_args", "cli_common", "cli_parser"}
))

HEAVY = ("dataclasses", "inspect", "slicer.store", "slicer.check", "slicer.ops", "slicer.render",
         "slicer.cli_common")


def modules_after(argv: list[str]) -> set[str]:
  """The names in `sys.modules` after `cli.main(argv)` in a fresh interpreter."""
  env = {**os.environ, "PYTHONPATH": str(SRC), "SLICER_NO_CODE_WARNING": "1"}
  done = subprocess.run(
    [sys.executable, "-c", _SCRIPT, json.dumps(argv)],
    capture_output=True, text=True, env=env, check=False,
  )
  if done.returncode != 0:
    raise AssertionError(f"interpreter failed with {done.returncode}: {done.stderr}")
  return set(json.loads(done.stdout.splitlines()[-1]))


class StartupImportsTests(unittest.TestCase):
  def test_Handlers_FoundOnDisk(self) -> None:
    # Guards the glob above: an empty list would make the import tests pass for nothing.
    self.assertIn("slicer.cli_queue", HANDLERS)
    self.assertIn("slicer.cli_track", HANDLERS)

  def test_Version_ImportsNoHeavyModule(self) -> None:
    loaded = modules_after(["--version"])
    self.assertEqual(sorted(loaded & set(HANDLERS)), [])
    self.assertEqual(sorted(loaded & set(HEAVY)), [])

  def test_Help_ImportsNoHandlerModule(self) -> None:
    loaded = modules_after(["--help"])
    self.assertEqual(sorted(loaded & set(HANDLERS)), [])
    self.assertEqual(sorted(loaded & {"slicer.store", "slicer.check", "slicer.ops"}), [])

  def test_List_ImportsNoCheckOrRender(self) -> None:
    with support.TempRepo() as repo:
      self.assertEqual(repo.run("init")[0], 0)
      self.assertEqual(repo.run("add", "A title")[0], 0)
      loaded = modules_after(["--root", str(repo.root), "list", "--json", "--lean"])
    self.assertIn("slicer.cli_queue", loaded)  # the command did run
    self.assertEqual(sorted(loaded & {"slicer.check", "slicer.render"}), [])


# Each command's handler, as `module:function`, and whether it takes the writer lock.
EXPECTED = {
  "ai": ("cli_ai:cmd_ai_instructions", False),
  "ai instructions": ("cli_ai:cmd_ai_instructions", False),
  "ai build-roadmap": ("cli_ai:cmd_ai_prompt", False),
  "ai getting-started": ("cli_ai:cmd_ai_prompt", False),
  "ai show": ("cli_ai:cmd_ai_show", False),
  "ai set": ("cli_ai:cmd_ai_set", True),
  "ai reset": ("cli_ai:cmd_ai_reset", True),
  "ai skill": ("cli_ai:cmd_ai_skill", False),
  "ai hooks": ("cli_ai:cmd_ai_hooks", False),
  "ai guard": ("cli_ai:cmd_ai_guard", False),
  "ai relay": ("cli_ai:cmd_ai_relay", False),
  "ai relay-roles": ("cli_relay:cmd_ai_relay_roles", False),
  "ai relay-orchestrator": ("cli_ai:cmd_ai_relay_orchestrator", False),
  "ai relay-brief": ("cli_ai:cmd_ai_relay_brief", False),
  "init": ("cli_setup:cmd_init", False),
  "setup-git": ("cli_setup:cmd_setup_git", False),
  "merge-index": ("cli_setup:cmd_merge_index", False),
  "merge-relay-roles": ("cli_setup:cmd_merge_relay_roles", False),
  "merge-slice": ("cli_setup:cmd_merge_slice", False),
  "import": ("cli_track:cmd_import", True),
  "migrate": ("cli_track:cmd_migrate", True),
  "next": ("cli_queue:cmd_next", False),
  "plan-parallel": ("cli_queue:cmd_plan_parallel", False),
  "next-id": ("cli_track:cmd_next_id", False),
  "id-prefix": ("cli_track:cmd_id_prefix", True),
  "list": ("cli_queue:cmd_list", False),
  "deps": ("cli_queue:cmd_deps", False),
  "find": ("cli_queue:cmd_find", False),
  "show": ("cli_queue:cmd_show", False),
  "sections": ("cli_prose:cmd_sections", False),
  "config": ("cli_prose:cmd_config", False),
  "recommended-workflow": ("cli_ai:cmd_recommended_workflow", False),
  "add": ("cli_track:cmd_add", True),
  "promote": ("cli_track:cmd_promote", True),
  "move": ("cli_track:cmd_move", True),
  "sort": ("cli_track:cmd_sort", True),
  "set": ("cli_track:cmd_set", True),
  "edit": ("cli_track:cmd_edit", True),
  "refs": ("cli_report:cmd_refs", False),
  "decide": ("cli_track:cmd_decide", True),
  "ready": ("cli_track:cmd_ready", True),
  "note": ("cli_track:cmd_note", True),
  "feedback": ("cli_track:cmd_feedback", False),
  "feedback-report": ("cli_track:cmd_feedback_report", False),
  "issues-pull": ("cli_track:cmd_issues_pull", False),
  "note-verify": ("cli_track:cmd_note_verify", True),
  "note-attest": ("cli_track:cmd_note_verify", True),
  "note-retract": ("cli_track:cmd_note_retract", True),
  "done": ("cli_track:cmd_done", True),
  "land": ("cli_track:cmd_land", True),
  "start": ("cli_track:cmd_start", True),
  "release": ("cli_track:cmd_release", True),
  "handoff": ("cli_track:cmd_handoff", True),
  "reject": ("cli_track:cmd_reject", True),
  "park": ("cli_track:cmd_park", True),
  "unpark": ("cli_track:cmd_unpark", True),
  "prose list": ("cli_prose:cmd_prose_list", False),
  "prose show": ("cli_prose:cmd_prose_show", False),
  "prose edit": ("cli_prose:cmd_prose_edit", True),
  "prose add-pass": ("cli_prose:cmd_prose_add_pass", True),
  "prose drop-pass": ("cli_prose:cmd_prose_drop_pass", True),
  "template list": ("cli_prose:cmd_template_list", False),
  "template show": ("cli_prose:cmd_template_show", False),
  "template set": ("cli_prose:cmd_template_set", True),
  "template reset": ("cli_prose:cmd_template_reset", True),
  "changelog add": ("cli_changelog:cmd_changelog_add", True),
  "changelog assemble": ("cli_changelog:cmd_changelog_assemble", True),
  "changelog import": ("cli_changelog:cmd_changelog_import", True),
  "remove": ("cli_track:cmd_remove", True),
  "retire": ("cli_track:cmd_remove", True),
  "render": ("cli_report:cmd_render", False),
  "sync": ("cli_report:cmd_sync", False),
  "verify": ("cli_report:cmd_verify", False),
  "check": ("cli_report:cmd_check", False),
  "goals": ("cli_prose:cmd_goals", False),
  "catalog add": ("cli_prose:cmd_catalog_add", True),
  "catalog list": ("cli_prose:cmd_catalog_list", False),
  "catalog show": ("cli_prose:cmd_catalog_show", False),
  "catalog edit": ("cli_prose:cmd_catalog_edit", True),
  "catalog retire": ("cli_prose:cmd_catalog_retire", True),
  "catalog move": ("cli_prose:cmd_catalog_move", True),
  "catalog cite": ("cli_prose:cmd_catalog_cite", True),
  "catalog uncite": ("cli_prose:cmd_catalog_uncite", True),
  "relay-roles show": ("cli_relay:cmd_relay_roles_show", False),
  "relay-roles recommend": ("cli_relay:cmd_relay_roles_recommend", False),
  "relay-roles init": ("cli_relay:cmd_relay_roles_init", True),
  "relay-roles add": ("cli_relay:cmd_relay_roles_add", True),
  "relay-roles set": ("cli_relay:cmd_relay_roles_set", True),
  "relay-roles remove": ("cli_relay:cmd_relay_roles_remove", True),
  "relay-roles move": ("cli_relay:cmd_relay_roles_move", True),
  "relay-roles rule-add": ("cli_relay:cmd_relay_roles_rule_add", True),
  "relay-roles rule-edit": ("cli_relay:cmd_relay_roles_rule_edit", True),
  "relay-roles rule-remove": ("cli_relay:cmd_relay_roles_rule_remove", True),
  "relay-roles apply": ("cli_relay:cmd_relay_roles_apply", True),
  "relay-run begin": ("cli_relay:cmd_relay_run_begin", False),
  "relay-run step": ("cli_relay:cmd_relay_run_step", False),
  "relay-run ask": ("cli_relay:cmd_relay_run_ask", False),
  "relay-run answer": ("cli_relay:cmd_relay_run_answer", False),
  "relay-run end": ("cli_relay:cmd_relay_run_end", False),
  "relay-run list": ("cli_relay:cmd_relay_run_list", False),
  "relay-run stats": ("cli_relay:cmd_relay_run_stats", False),
  "stats": ("cli_report:cmd_stats", False),
  "snapshot": ("cli_report:cmd_snapshot", False),
  "status": ("cli_queue:cmd_status", False),
  "log": ("cli_report:cmd_log", False),
  "tui": ("cli_report:cmd_tui", False),
}


def handlers_in(parser, path: tuple[str, ...] = ()) -> dict[str, object]:
  """Every parser's `func` default, keyed by its command path (the first name of an alias)."""
  import argparse

  found: dict[str, object] = {}
  func = parser.get_default("func")
  if func is not None and path:
    found[" ".join(path)] = func
  for action in parser._actions:
    if isinstance(action, argparse._SubParsersAction):
      seen: set[int] = set()
      for name, sub in action.choices.items():
        if id(sub) in seen:
          continue  # an alias of a parser already visited
        seen.add(id(sub))
        found.update(handlers_in(sub, (*path, name)))
  return found


class StartupImportsHandlerTests(unittest.TestCase):
  def test_EveryCommand_ResolvesItsHandler(self) -> None:
    from slicer.cli_args import LazyHandler

    found = handlers_in(cli.build_parser())
    self.assertEqual(
      sorted(found), sorted(EXPECTED),
      "a command was added or removed: update EXPECTED, and name its handler with `_lazy(...)`",
    )
    for command, handler in found.items():
      ref, mutates = EXPECTED[command]
      with self.subTest(command=command):
        self.assertIsInstance(handler, LazyHandler)
        self.assertEqual(handler.ref, f"slicer.{ref}")
        self.assertEqual(handler.mutates, mutates)
        resolved = handler.resolve()
        self.assertTrue(callable(resolved))
        self.assertEqual(bool(getattr(resolved, "mutates", False)), mutates)


class StartupImportsVocabTests(unittest.TestCase):
  def test_Vocab_DomainModulesKeepTheListsTheParserPrints(self) -> None:
    import importlib.util

    from slicer import ai, note_pack, snapshot, vocab

    self.assertIs(ai.PROMPT_NAMES, vocab.PROMPT_NAMES)
    self.assertIs(note_pack.TRUST, vocab.TRUST)
    self.assertIs(snapshot.PARTS, vocab.PARTS)
    # Run the module body again, here. `tests/affected.py` maps a source file by the lines a
    # test runs, and `vocab` runs all of its lines when it is first imported, which is before
    # any test starts; without this no test names it and `affected.py` refuses a change to it.
    spec = importlib.util.spec_from_file_location("vocab_again", vocab.__file__)
    again = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(again)
    self.assertEqual(
      sorted(name for name in vars(again) if name.isupper()), ["PARTS", "PROMPT_NAMES", "TRUST"],
    )


if __name__ == "__main__":
  unittest.main()
