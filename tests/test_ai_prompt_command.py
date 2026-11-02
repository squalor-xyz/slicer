"""Project prompts must preserve exact text and refuse changes without partial writes."""

from __future__ import annotations

import io
import json
import os
import unittest
from unittest.mock import patch

import support
from slicer import ai, ops, store
from slicer.errors import RenderError, StateError

NAME = "getting-started"
TEXT = "# Project facts\r\nUse python3.12; keep {{literal}}"


def _tracking_bytes(repo: support.TempRepo) -> dict[str, bytes]:
  return {str(p.relative_to(repo.root)): p.read_bytes()
          for p in sorted((repo.root / ".slicer").rglob("*"))
          if p.is_file() and p.name != "lock"}


def _set(repo: support.TempRepo, text: str = TEXT, *flags: str, name: str = NAME):
  with patch("sys.stdin", io.StringIO(text)):
    return repo.run("ai", "set", name, "--stdin", *flags)


class AiPromptCommandTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    self.assertEqual(repo.run("init")[0], 0)
    return repo

  def path(self, repo: support.TempRepo, name: str = NAME):
    return repo.root / ".slicer/prompts" / f"{name}.md"

  def seed(self, repo: support.TempRepo, name: str = NAME, text: str = TEXT):
    path = self.path(repo, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return path

  def entries(self, repo: support.TempRepo):
    code, out, err = repo.run("log", "--action", "prompt", "--json")
    self.assertEqual(code, 0, err)
    return json.loads(out)

  def error(self, result, code: int, kind: str):
    actual, out, err = result
    self.assertEqual(actual, code, err)
    payload = json.loads(out)["error"]
    self.assertEqual(payload["code"], kind)
    return payload["message"]

  def test_AiSet_Stdin_WritesTheOverrideAndLogsOneEntry(self):
    with self.repo() as repo:
      code, out, err = _set(repo, TEXT, "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(self.path(repo).read_bytes(), TEXT.encode())
      self.assertEqual(list(json.loads(out)), ["name", "changed", "customized"])
      self.assertEqual(json.loads(out), {"name": NAME, "changed": True, "customized": True})
      entries = self.entries(repo)
      self.assertEqual(len(entries), 1)
      self.assertEqual([entries[0][k] for k in ("action", "item", "from", "to")],
                       ["prompt", f"prompt.{NAME}", "default", "custom"])

  def test_AiSet_BufferedStdin_PreservesUtf8AndCrLf(self):
    with self.repo() as repo:
      text = TEXT + " — café"
      with io.TextIOWrapper(io.BytesIO(text.encode("utf-8"))) as stdin, \
           patch("sys.stdin", stdin):
        self.assertEqual(repo.run("ai", "set", NAME, "--stdin")[0], 0)
      self.assertEqual(self.path(repo).read_bytes(), text.encode("utf-8"))

  def test_AiSet_File_ReadsBytesExactly(self):
    with self.repo() as repo:
      src = repo.root / "source.md"
      src.write_bytes(TEXT.encode())
      self.assertEqual(repo.run("ai", "set", NAME, "--file", str(src))[0], 0)
      self.assertEqual(self.path(repo).read_bytes(), TEXT.encode())

  def test_AiSet_SameTextAsTheOverride_WritesAndLogsNothing(self):
    with self.repo() as repo:
      path = self.seed(repo)
      before, mtime = _tracking_bytes(repo), path.stat().st_mtime_ns
      code, out, err = _set(repo, TEXT, "--json", "--render", "--strict")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out), {"name": NAME, "changed": False, "customized": True})
      self.assertEqual((_tracking_bytes(repo), path.stat().st_mtime_ns), (before, mtime))

  def test_AiSet_BuiltInText_RemovesTheOverride(self):
    with self.repo() as repo:
      self.seed(repo)
      code, out, err = _set(repo, ai.prompt_text(NAME), "--json", "--render", "--strict")
      self.assertEqual(code, 0, err)
      self.assertFalse(self.path(repo).exists())
      self.assertEqual(json.loads(out), {"name": NAME, "changed": True, "customized": False})
      self.assertEqual(self.entries(repo)[0]["to"], "default")
      before = _tracking_bytes(repo)
      self.assertEqual(_set(repo, ai.prompt_text(NAME))[1], f"{NAME} is already the default\n")
      self.assertEqual(_tracking_bytes(repo), before)

  def test_AiSet_EmptyOrBlankText_IsBadPromptAndWritesNothing(self):
    with self.repo() as repo:
      self.seed(repo)
      before = _tracking_bytes(repo)
      for text in ("", " \r\n\t"):
        message = self.error(_set(repo, text, "--json"), 2, "bad_prompt")
        self.assertIn(f"slicer ai reset {NAME}", message)
        self.assertEqual(_tracking_bytes(repo), before)

  def test_AiSet_NeitherOrBothSources_IsUsage(self):
    with self.repo() as repo:
      src = repo.write("source.md", TEXT)
      before = _tracking_bytes(repo)
      for flags in ((), ("--file", str(src), "--stdin")):
        self.error(repo.run("ai", "set", NAME, *flags, "--json"), 2, "usage")
        self.assertEqual(_tracking_bytes(repo), before)

  def test_AiSet_NonUtf8Stdin_IsIoAndWritesNothing(self):
    class BadStdin:
      def read(self):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    with self.repo() as repo:
      before = _tracking_bytes(repo)
      with patch("sys.stdin", BadStdin()):
        self.error(repo.run("ai", "set", NAME, "--stdin", "--json"), 3, "io")
      self.assertEqual(_tracking_bytes(repo), before)

  def test_AiSet_NonUtf8OnBufferedStdin_IsIoAndWritesNothing(self):
    with self.repo() as repo:
      before = _tracking_bytes(repo)
      # latin-1 decodes every byte, so only the raw-buffer path can refuse these.
      with io.TextIOWrapper(io.BytesIO(b"# facts \xff\xfe\n"), encoding="latin-1") as stdin, \
           patch("sys.stdin", stdin):
        message = self.error(repo.run("ai", "set", NAME, "--stdin", "--json"), 3, "io")
      self.assertIn("stdin", message)
      self.assertFalse(self.path(repo).exists())
      self.assertEqual(_tracking_bytes(repo), before)

  def test_AiSet_NonUtf8OrMissingFile_IsIoAndWritesNothing(self):
    with self.repo() as repo:
      src = repo.root / "bad.md"
      before = _tracking_bytes(repo)
      for data in (None, b"\xff"):
        if data is not None:
          src.write_bytes(data)
        self.error(repo.run("ai", "set", NAME, "--file", str(src), "--json"), 3, "io")
        self.assertEqual(_tracking_bytes(repo), before)

  def test_AiPromptCommands_UnknownName_IsUnknownPromptAndListsTheNames(self):
    with self.repo() as repo:
      before = _tracking_bytes(repo)
      for cmd in ("show", "set", "reset"):
        for name in ("nope", "../escape", "instructions", "skill", "relay"):
          message = self.error(repo.run("ai", cmd, name, "--json"), 2, "unknown_prompt")
          for known in ai.PROMPT_NAMES:
            self.assertIn(known, message)
      self.assertEqual(_tracking_bytes(repo), before)

  def test_AiReset_Override_DeletesItAndLogsCustomToDefault(self):
    with self.repo() as repo:
      self.seed(repo)
      code, out, err = repo.run("ai", "reset", NAME, "--json", "--render", "--strict", "--check")
      self.assertEqual(code, 0, err)
      self.assertFalse(self.path(repo).exists())
      self.assertEqual(json.loads(out), {"name": NAME, "changed": True, "customized": False})
      entry, = self.entries(repo)
      self.assertEqual([entry[k] for k in ("item", "action", "from", "to")],
                       [f"prompt.{NAME}", "prompt", "custom", "default"])

  def test_AiReset_NoOverride_ChangesNothing(self):
    with self.repo() as repo:
      before = _tracking_bytes(repo)
      code, out, err = repo.run("ai", "reset", NAME, "--json", "--render", "--strict")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out), {"name": NAME, "changed": False, "customized": False})
      self.assertEqual(_tracking_bytes(repo), before)
      self.assertFalse(self.path(repo).parent.exists())

  def test_AiShow_Override_PrintsItByteForByte(self):
    with self.repo() as repo:
      self.seed(repo)
      self.assertEqual(repo.run("ai", "show", NAME), (0, TEXT, ""))

  def test_AiShow_Default_PrintsTheBuiltInWithoutAProject(self):
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      self.assertEqual(repo.run("ai", "show", NAME, "--default"), (0, ai.prompt_text(NAME), ""))
    with self.repo() as repo:
      self.seed(repo)
      self.assertEqual(repo.run("ai", "show", NAME, "--default"), (0, ai.prompt_text(NAME), ""))

  def test_AiShow_NoOverride_PrintsTheBuiltIn(self):
    with self.repo() as repo:
      self.assertEqual(repo.run("ai", "show", NAME), (0, ai.prompt_text(NAME), ""))

  def test_AiShow_Json_IsNameTextCustomized(self):
    with self.repo() as repo:
      self.seed(repo)
      for flags, text, customized in (((), TEXT, True), (("--default",), ai.prompt_text(NAME), False)):
        code, out, err = repo.run("ai", "show", NAME, *flags, "--json")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(list(json.loads(out)), ["name", "text", "customized"])
        self.assertEqual(json.loads(out), {"name": NAME, "text": text, "customized": customized})

  def test_AiGettingStarted_WithOverride_PrintsTheOverride(self):
    self.check_prompt(NAME)

  def test_AiBuildRoadmap_WithOverride_PrintsTheOverride(self):
    self.check_prompt("build-roadmap")

  def check_prompt(self, name):
    with self.repo() as repo:
      self.seed(repo, name)
      self.assertEqual(repo.run("ai", name), (0, TEXT, ""))
      code, out, err = repo.run("ai", name, "--json")
      self.assertEqual((code, err), (0, ""))
      self.assertEqual(list(json.loads(out)), ["name", "prompt", "customized"])
      self.assertEqual(json.loads(out), {"name": name, "prompt": TEXT, "customized": True})

  def test_AiPrompt_OutsideAProject_PrintsTheBuiltInWithoutWarning(self):
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      for name in ai.PROMPT_NAMES:
        self.assertEqual(repo.run("ai", name), (0, ai.prompt_text(name), ""))
        self.assertEqual(repo.run("ai", "show", name), (0, ai.prompt_text(name), ""))

  def test_AiPrompt_UnreadableConfig_FallsBackWithoutWarning(self):
    with self.repo() as repo:
      self.seed(repo)
      with patch("slicer.config.Config.load", side_effect=StateError("broken", code="config")):
        for cmd in (("ai", NAME), ("ai", "show", NAME)):
          self.assertEqual(repo.run(*cmd), (0, ai.prompt_text(NAME), ""))

  def test_AiPrompt_UnreadableOverride_IsIo(self):
    with self.repo() as repo:
      path = self.seed(repo)
      path.write_bytes(b"\xff")
      for cmd in (("ai", NAME), ("ai", "show", NAME)):
        self.assertIn(str(path), self.error(repo.run(*cmd, "--json"), 3, "io"))

  def test_AiSet_StrictRenderFailure_LeavesTheFileAndLogAlone(self):
    with self.repo() as repo:
      self.seed(repo)
      before = _tracking_bytes(repo)
      for cmd, text in (("set", "new"), ("set", ai.prompt_text(NAME)), ("reset", "")):
        with patch("slicer.render.plan", side_effect=RenderError("boom")):
          result = (_set(repo, text, "--render", "--strict") if cmd == "set" else
                    repo.run("ai", "reset", NAME, "--render", "--strict"))
        self.assertEqual(result[0:2], (2, ""))
        self.assertEqual(_tracking_bytes(repo), before)

  def test_AiSet_DuringAMerge_IsRefused(self):
    with self.repo() as repo:
      before = _tracking_bytes(repo)
      with patch("slicer.vcs.merge_in_progress", return_value=True):
        for cmd in ("set", "reset"):
          self.error(repo.run("ai", cmd, NAME, "--json"), 2, "merge_in_progress")
      self.assertEqual(_tracking_bytes(repo), before)

  def test_AiWrites_LockHeld_RefusesWritesButAllowsReads(self):
    if store.fcntl is None:
      self.skipTest("no flock")
    with self.repo() as repo:
      self.seed(repo)
      before = _tracking_bytes(repo)
      with store.project_lock(repo.root), patch.dict(os.environ, {"SLICER_LOCK_TIMEOUT": "0"}):
        self.error(_set(repo, "new", "--json"), 3, "locked")
        self.error(repo.run("ai", "reset", NAME, "--json"), 3, "locked")
        self.assertEqual(repo.run("ai", "show", NAME), (0, TEXT, ""))
      self.assertEqual(_tracking_bytes(repo), before)
      self.assertEqual(_set(repo, "new")[0], 0)

  def test_AiWrites_StrictOrCheckWithoutRender_IsUsage(self):
    with self.repo() as repo:
      before = _tracking_bytes(repo)
      for cmd in ("set", "reset"):
        for flag in ("--strict", "--check"):
          result = (_set(repo, TEXT, flag, "--json") if cmd == "set" else
                    repo.run("ai", cmd, NAME, flag, "--json"))
          self.error(result, 2, "usage")
      self.assertEqual(_tracking_bytes(repo), before)

  def test_AiWrites_Text_DescribesTheChange(self):
    with self.repo() as repo:
      self.assertEqual(_set(repo)[1], f"replaced {NAME}\n")
      self.assertEqual(_set(repo)[1], f"{NAME} is already that text\n")
      self.assertEqual(repo.run("ai", "reset", NAME)[1], f"reset {NAME}\n")
      self.assertEqual(repo.run("ai", "reset", NAME)[1], f"{NAME} is already the default\n")

  def test_Check_UnknownPromptFile_Warns(self):
    self.check_warning("other.md", "hello", "unknown")

  def test_Check_PromptWithConflictMarkers_Warns(self):
    self.check_warning(f"{NAME}.md", "<<<<<<< ours\none\n=======\ntwo\n>>>>>>> theirs\n", "conflict")

  def test_Check_DotfileInPrompts_IsIgnored(self):
    with self.repo() as repo:
      self.seed(repo)
      for dotfile in (".DS_Store", ".getting-started.md.swp"):
        (self.path(repo).parent / dotfile).write_text("x")
      repo.run("render")
      code, out, err = repo.run("check", "--json")
      self.assertEqual(code, 0, err)
      report = json.loads(out)
      self.assertTrue(report["ok"])
      warning = "\n".join(report["warnings"])
      for dotfile in (".DS_Store", ".getting-started.md.swp"):
        self.assertNotIn(dotfile, warning)

  def check_warning(self, filename, text, word):
    with self.repo() as repo:
      self.seed(repo)
      (self.path(repo).parent / filename).write_text(text)
      repo.run("render")
      code, out, err = repo.run("check", "--json")
      self.assertEqual(code, 0, err)
      report = json.loads(out)
      self.assertTrue(report["ok"])
      warning = "\n".join(report["warnings"])
      for fragment in (word, filename, "slicer ai show", "slicer ai set", "--file FILE"):
        self.assertIn(fragment, warning)

  def test_Init_CreatesNoPromptsDirectory(self):
    with self.repo() as repo:
      self.assertFalse(self.path(repo).parent.exists())

  def test_InitForce_KeepsExistingOverrides(self):
    with self.repo() as repo:
      self.seed(repo)
      self.assertEqual(repo.run("init", "--force")[0], 0)
      self.assertEqual(self.path(repo).read_bytes(), TEXT.encode())

  def test_PromptOps_UnknownName_IsUnknownPrompt(self):
    with self.repo() as repo:
      for name in ("../escape", "instructions"):
        for fn, args in ((ops.prompt_set, (name, TEXT)), (ops.prompt_reset, (name,))):
          with self.assertRaises(StateError) as caught:
            fn(repo.state(), *args)
          self.assertEqual(caught.exception.code, "unknown_prompt")

  def test_PromptStage_ReadsBufferedWritesAndDeletion(self):
    with self.repo() as repo:
      state = repo.state()
      state.begin_stage()
      state.save_prompt(NAME, TEXT)
      self.assertEqual(state.prompt_exact(NAME), TEXT)
      self.assertFalse(self.path(repo).exists())
      state.commit_stage()
      self.assertEqual(self.path(repo).read_bytes(), TEXT.encode())
      state.begin_stage()
      state.delete_prompt(NAME)
      self.assertIsNone(state.prompt_exact(NAME))
      self.assertTrue(self.path(repo).exists())
      state.discard_stage()
      self.assertEqual(state.prompt_exact(NAME), TEXT)
      state.begin_stage()
      state.delete_prompt(NAME)
      self.path(repo).unlink()
      state.commit_stage()
      self.assertFalse(self.path(repo).exists())
