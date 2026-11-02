"""Keep Claude Code onboarding and tracking protection safe to install locally."""

from __future__ import annotations

import copy
import io
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import support
from slicer import ai, cli, jsonio


class AiHooksTests(unittest.TestCase):
  def setUp(self) -> None:
    self.repo = support.TempRepo(git=False)
    self.addCleanup(self.repo.close)
    isolated = support.isolated_discovery(self.repo.root)
    isolated.__enter__()
    self.addCleanup(isolated.__exit__, None, None, None)
    self.assertEqual(self.repo.run("init")[0], 0)
    self.target = self.repo.root / ".claude/settings.json"

  def snapshot(self) -> dict[str, bytes]:
    return {str(p.relative_to(self.repo.root)): p.read_bytes()
            for p in self.repo.root.rglob("*") if p.is_file()}

  def settings(self, value: object) -> None:
    self.repo.write(".claude/settings.json", jsonio.dumps(value))

  def install(self, *flags: str) -> tuple[int, str, str]:
    return self.repo.run("ai", "hooks", "--install", *flags)

  def conflict(self) -> dict:
    settings = copy.deepcopy(ai.hooks_settings())
    for groups in settings["hooks"].values():
      groups[0]["hooks"][0]["timeout"] = 99
    return settings

  def test_AiHooks_Print_IsTheSnippetAndWritesNothing(self) -> None:
    before = self.snapshot()
    with patch("slicer.cli.store.project_lock", side_effect=AssertionError("lock")):
      self.assertEqual(self.repo.run("ai", "hooks"), (0, jsonio.dumps(ai.hooks_settings()), ""))
    self.assertEqual(self.snapshot(), before)
    with support.TempRepo(git=False) as outside, support.isolated_discovery(outside.root):
      self.assertEqual(outside.run("ai", "hooks"), (0, jsonio.dumps(ai.hooks_settings()), ""))
      self.assertEqual(list(outside.root.iterdir()), [])

  def test_AiHooks_Snippet_KeyOrderAndCommands(self) -> None:
    settings = ai.hooks_settings()
    self.assertEqual(list(settings), ["hooks"])
    self.assertEqual(list(settings["hooks"]), ["SessionStart", "PreToolUse"])
    for event, matcher, command, timeout in (
      ("SessionStart", "startup|clear|compact",
       '[ -d "$CLAUDE_PROJECT_DIR/.slicer" ] && slicer ai getting-started || true', 30),
      ("PreToolUse", "Read|Grep|Edit|Write", "slicer ai guard", 10),
    ):
      group, = settings["hooks"][event]
      self.assertEqual(list(group), ["matcher", "hooks"])
      self.assertEqual(group, {"matcher": matcher, "hooks": [
        {"type": "command", "command": command, "timeout": timeout}]})
      self.assertEqual(list(group["hooks"][0]), ["type", "command", "timeout"])

  def test_AiHooks_GuardVersion_DocumentedInHelpAndDocs(self) -> None:
    root = Path(__file__).resolve().parents[1]
    agents = (root / "docs/agents.md").read_text().split("`slicer ai hooks`", 1)[1].split(
      "`slicer ai getting-started` prints", 1)[0]
    commands = next(line for line in (root / "docs/commands.md").read_text().splitlines()
                    if line.startswith("| `ai hooks "))
    with patch("sys.stdout", new_callable=io.StringIO) as out, \
         self.assertRaises(SystemExit) as caught:
      cli.main(["ai", "hooks", "--help"])
    self.assertEqual(caught.exception.code, 0)
    for name, text in (("agents", agents), ("commands", commands), ("help", out.getvalue())):
      with self.subTest(name=name):
        normalized = " ".join(text.split())
        self.assertIn("slicer 1.18 or later on PATH", normalized)
        self.assertIn("older slicer exits 2", normalized)
        self.assertIn("Claude Code treats exit 2 as a block", normalized)

  def test_AiHooksInstall_NoSettings_CreatesProjectSettings(self) -> None:
    before = self.snapshot()
    with patch("slicer.cli.store.project_lock", side_effect=AssertionError("lock")):
      code, out, err = self.install()
    self.assertEqual((code, err), (0, ""))
    self.assertEqual(self.target.read_text(), jsonio.dumps(ai.hooks_settings()))
    self.assertEqual(out, f"wrote {self.target}\n")
    self.assertEqual(self.snapshot(), {**before, ".claude/settings.json": self.target.read_bytes()})

  def test_AiHooksInstall_NeverWritesUnderHome(self) -> None:
    home = self.repo.root / "home"
    home.mkdir()
    with patch.dict(os.environ, {"HOME": str(home)}):
      self.assertEqual(self.install()[0], 0)
    self.assertEqual(list(home.iterdir()), [])
    self.assertTrue(self.target.is_file())

  def test_AiHooksInstall_SymlinkToHome_IsRefused(self) -> None:
    home = self.repo.root / "home"
    home.mkdir()
    (self.repo.root / ".claude").symlink_to(home, target_is_directory=True)
    with patch.dict(os.environ, {"HOME": str(home)}):
      code, out, _ = self.install("--json")
    self.assertEqual((code, json.loads(out)["error"]["code"]), (2, "state"))
    self.assertEqual(list(home.iterdir()), [])

  def test_AiHooksInstall_ExistingSettings_KeepsOtherKeysAndGroups(self) -> None:
    user = {"matcher": "Read", "hooks": [{"type": "command", "command": "my-check"}]}
    original = {"permissions": {"deny": ["Read(secret)"]}, "hooks": {
      "Stop": [{"hooks": []}], "PreToolUse": [user]}, "env": {"LOCAL": "yes"}}
    self.settings(original)
    self.assertEqual(self.install()[0], 0)
    result = jsonio.read(self.target)
    self.assertEqual(list(result), list(original))
    self.assertEqual(list(result["hooks"]), ["Stop", "PreToolUse", "SessionStart"])
    self.assertEqual(result, {**original, "hooks": {
      "Stop": original["hooks"]["Stop"],
      "PreToolUse": [user, *ai.hooks_settings()["hooks"]["PreToolUse"]],
      "SessionStart": ai.hooks_settings()["hooks"]["SessionStart"]}})

  def test_AiHooksInstall_AlreadyInstalled_WritesNothing(self) -> None:
    self.assertEqual(self.install()[0], 0)
    stamp = self.target.stat().st_mtime_ns
    before = self.snapshot()
    code, out, err = self.install("--json")
    self.assertEqual((code, err), (0, ""))
    self.assertEqual(json.loads(out)["written"], [])
    self.assertEqual(self.target.stat().st_mtime_ns, stamp)
    self.assertEqual(self.snapshot(), before)
    self.assertIn("already installed", self.install()[1])

  def test_AiHooksInstall_ConflictingSlicerGroup_RefusesAndWritesNothing(self) -> None:
    self.settings(self.conflict())
    before = self.snapshot()
    code, out, err = self.install("--json")
    self.assertEqual((code, json.loads(out)["error"]["code"]), (2, "state"))
    for text in (str(self.target), "SessionStart", "PreToolUse", "--force"):
      self.assertIn(text, err)
    self.assertEqual(self.snapshot(), before)

  def test_AiHooksInstall_Force_ReplacesTheSlicerGroupInPlace(self) -> None:
    settings = self.conflict()
    user = {"hooks": [{"type": "command", "command": "user-command"}]}
    for groups in settings["hooks"].values():
      groups[:] = [user, groups[0], user]
    self.settings(settings)
    self.assertEqual(self.install("--force")[0], 0)
    expected = {"hooks": {event: [user, groups[0], user]
                          for event, groups in ai.hooks_settings()["hooks"].items()}}
    self.assertEqual(jsonio.read(self.target), expected)

  def test_AiHooksInstall_EqualHandlers_IsAlreadyInstalled(self) -> None:
    settings = ai.hooks_settings()
    for groups in settings["hooks"].values():
      groups[0]["matcher"] = "custom-matcher"
    self.settings(settings)
    stamp = self.target.stat().st_mtime_ns
    code, out, _ = self.install("--json")
    self.assertEqual(code, 0)
    self.assertEqual(json.loads(out)["written"], [])
    self.assertEqual(jsonio.read(self.target), settings)
    self.assertEqual(self.target.stat().st_mtime_ns, stamp)

  def test_AiHooksInstall_InvalidJson_IsStateAndWritesNothing(self) -> None:
    for text in ("{bad", "[]", "null", '{"hooks": []}',
                 '{"hooks": {"PreToolUse": {}}}'):
      with self.subTest(text=text):
        self.repo.write(".claude/settings.json", text)
        before = self.snapshot()
        code, out, err = self.install("--force", "--json")
        self.assertEqual((code, json.loads(out)["error"]["code"]), (2, "state"))
        self.assertIn(str(self.target), err)
        self.assertEqual(self.snapshot(), before)

  def test_AiHooksInstall_OutsideAProject_IsRefused(self) -> None:
    with support.TempRepo(git=False) as outside, support.isolated_discovery(outside.root):
      code, out, _ = outside.run("ai", "hooks", "--install", "--json")
      self.assertEqual((code, json.loads(out)["error"]["code"]), (2, "state"))
      self.assertFalse((outside.root / ".claude").exists())

  def test_AiHooks_ForceWithoutInstall_IsUsage(self) -> None:
    before = self.snapshot()
    code, out, _ = self.repo.run("ai", "hooks", "--force", "--json")
    self.assertEqual((code, json.loads(out)["error"]["code"]), (2, "usage"))
    self.assertEqual(self.snapshot(), before)

  def test_AiHooks_Json_SettingsThenWritten(self) -> None:
    code, out, err = self.repo.run("ai", "hooks", "--json")
    self.assertEqual((code, err), (0, ""))
    self.assertEqual(list(json.loads(out)), ["settings"])
    self.assertEqual(json.loads(out), {"settings": ai.hooks_settings()})
    code, out, err = self.install("--json")
    self.assertEqual((code, err), (0, ""))
    self.assertEqual(list(json.loads(out)), ["settings", "written"])
    self.assertEqual(json.loads(out), {"settings": ai.hooks_settings(), "written": [str(self.target)]})


class AiGuardTests(unittest.TestCase):
  def setUp(self) -> None:
    self.repo = support.TempRepo(git=False)
    self.addCleanup(self.repo.close)
    isolated = support.isolated_discovery(self.repo.root)
    isolated.__enter__()
    self.addCleanup(isolated.__exit__, None, None, None)
    self.assertEqual(self.repo.run("init")[0], 0)

  def payload(self, tool: str, path: str, **extra) -> dict:
    key = "path" if tool == "Grep" else "file_path"
    return {"cwd": str(self.repo.root), "tool_name": tool,
            "tool_input": {key: str(self.repo.root / path), **extra}}

  def check(self, payload: dict, denied: bool) -> None:
    base = Path(payload.get("cwd", self.repo.root))
    self.assertEqual(ai.guard_decision(payload, base), ai.GUARD_REASON if denied else None)
    with patch("sys.stdin", io.StringIO(json.dumps(payload))), \
         patch("slicer.cli.store.project_lock", side_effect=AssertionError("lock")):
      code, out, err = self.repo.run("ai", "guard")
    self.assertEqual((code, err), (0, ""))
    if denied:
      self.assertEqual(json.loads(out), {"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny",
        "permissionDecisionReason": ai.GUARD_REASON}})
      self.assertEqual(list(json.loads(out)["hookSpecificOutput"]), [
        "hookEventName", "permissionDecision", "permissionDecisionReason"])
    else:
      self.assertEqual(out, "")

  def test_AiGuard_ReadIndex_Denies(self) -> None:
    for path in (".slicer/index.json", ".slicer/config.json", ".slicer/relay-roles.json"):
      with self.subTest(path=path):
        self.check(self.payload("Read", path), True)

  def test_AiGuard_EditSliceJson_Denies(self) -> None:
    for path in (".slicer/slices/s439.json", ".slicer/slices/done/old.json",
                 ".slicer/templates/state.json", ".slicer/prompts/state.json"):
      with self.subTest(path=path):
        self.check(self.payload("Edit", path), True)

  def test_AiGuard_WriteLog_Denies(self) -> None:
    self.check(self.payload("Write", ".slicer/log.jsonl"), True)

  def test_AiGuard_ReadRender_Denies(self) -> None:
    self.check(self.payload("Read", ".slicer/render/slices/s439.md"), True)

  def test_AiGuard_GrepSlicerDir_Denies(self) -> None:
    for path in (".slicer", ".slicer/slices", ".slicer/slices/done", ".slicer/render",
                 ".slicer/render/ROADMAP.md", ".slicer/index.json", ".slicer/log.jsonl"):
      with self.subTest(path=path):
        self.check(self.payload("Grep", path), True)

  def test_AiGuard_GrepGlob_DeniesPossibleJsonAndAllowsMarkdown(self) -> None:
    for glob in ("*.json", "**/*.json", "s[0-9]*.[j]son", "*", "config.*", "????.json",
                 "*.{md,json}", "**/*.{js,{md,json}}"):
      with self.subTest(glob=glob):
        self.check(self.payload("Grep", ".slicer/templates", glob=glob), True)
    for glob in ("*.md", "**/*.txt", "*.jsonl", "*.json.bak", "*.{md,txt}"):
      with self.subTest(glob=glob):
        self.check(self.payload("Grep", ".slicer/templates", glob=glob), False)

  def test_AiGuard_ReadTemplateOrPrompt_Allows(self) -> None:
    for tool in ("Read", "Grep", "Edit", "Write"):
      for path in (".slicer/templates/slice.md", ".slicer/prompts/review.md"):
        with self.subTest(tool=tool, path=path):
          self.check(self.payload(tool, path), False)
    for path in (".slicer/templates", ".slicer/prompts"):
      self.check(self.payload("Grep", path), False)

  def test_AiGuard_ReadSource_Allows(self) -> None:
    self.check(self.payload("Read", "src/code.py"), False)
    self.check(self.payload("Read", ".slicer-backup/index.json"), False)

  def test_AiGuard_PathWithoutEnclosingProject_Allows(self) -> None:
    with support.TempRepo(git=False) as outside, support.isolated_discovery(outside.root):
      self.check(self.payload("Read", str(outside.root / ".slicer/index.json")), False)

  def test_AiGuard_MainSessionToNestedWorktree_Denies(self) -> None:
    nested = self.repo.root / ".worktrees/target"
    self.assertEqual(self.repo.run("init", "--root", str(nested))[0], 0)
    for tool, path in (("Read", ".slicer/index.json"), ("Edit", ".slicer/slices/new.json"),
                       ("Write", ".slicer/log.jsonl"), ("Read", ".slicer/render/view.md"),
                       ("Grep", ".slicer"), ("Grep", ".slicer/slices"),
                       ("Grep", ".slicer/render")):
      with self.subTest(tool=tool, path=path):
        self.check(self.payload(tool, str(nested / path)), True)

  def test_AiGuard_NestedWorktreeSessionToMain_Denies(self) -> None:
    nested = self.repo.root / ".worktrees/target"
    self.assertEqual(self.repo.run("init", "--root", str(nested))[0], 0)
    for tool, path in (("Read", ".slicer/index.json"), ("Grep", ".slicer/slices")):
      with self.subTest(tool=tool):
        payload = self.payload(tool, path)
        payload["cwd"] = str(nested)
        self.check(payload, True)

  def test_AiGuard_UnrelatedTargetProject_DeniesStateAndAllowsOrdinaryFiles(self) -> None:
    with support.TempRepo(git=False) as other:
      self.assertEqual(other.run("init")[0], 0)
      for path, denied in ((".slicer/index.json", True), (".slicer/render/page.md", True),
                           (".slicer/templates/slice.md", False), (".slicer/prompts/review.md", False),
                           ("src/config.json", False), ("docs/guide.md", False)):
        with self.subTest(path=path):
          self.check(self.payload("Read", str(other.root / path)), denied)

  def test_AiGuard_NearestProject_KeepsNestedSourceReadable(self) -> None:
    nested = self.repo.root / ".slicer/prompts/embedded"
    self.assertEqual(self.repo.run("init", "--root", str(nested))[0], 0)
    for path, denied in (("src/config.json", False), (".slicer/templates/slice.md", False),
                         (".slicer/index.json", True)):
      with self.subTest(path=path):
        self.check(self.payload("Read", str(nested / path)), denied)

  def test_AiGuard_SymlinkTarget_UsesResolvedProject(self) -> None:
    with support.TempRepo(git=False) as other:
      self.assertEqual(other.run("init")[0], 0)
      (self.repo.root / "alias.json").symlink_to(other.root / ".slicer/index.json")
      self.check(self.payload("Read", "alias.json"), True)

  def test_AiGuard_Discovery_OnlyChecksConfigExistence(self) -> None:
    payload = self.payload("Read", ".slicer/index.json")
    with patch.object(Path, "read_text", side_effect=AssertionError("read contents")), \
         patch.object(Path, "read_bytes", side_effect=AssertionError("read contents")):
      self.assertEqual(ai.guard_decision(payload, self.repo.root), ai.GUARD_REASON)

  def test_AiGuard_UninitializedSession_JudgesTargetProject(self) -> None:
    with support.TempRepo(git=False) as outside, support.isolated_discovery(outside.root):
      payload = self.payload("Read", ".slicer/index.json")
      payload["cwd"] = str(outside.root)
      self.check(payload, True)

  def test_AiGuard_CaseVariants_DeniesTrackingPaths(self) -> None:
    for tool, path in (("Read", ".slicer/index.JSON"), ("Read", ".SLICER/index.json"),
                       ("Write", ".SLICER/LOG.JSONL"), ("Read", ".slicer/RENDER/view.md"),
                       ("Grep", ".SLICER/SLICES")):
      with self.subTest(tool=tool, path=path):
        self.check(self.payload(tool, path), True)
    self.check(self.payload("Grep", ".SLICER/templates", glob="*.JSON"), True)

  def test_AiGuard_RelativePathAndNestedCwd_UseTargetProject(self) -> None:
    payload = self.payload("Read", ".slicer/index.json")
    nested = self.repo.root / "src"
    nested.mkdir()
    payload["cwd"] = str(nested)
    payload["tool_input"]["file_path"] = "../.slicer/index.json"
    self.check(payload, True)

  def test_AiGuard_MissingCwd_UsesClaudeProjectDir(self) -> None:
    payload = self.payload("Read", ".slicer/index.json")
    del payload["cwd"]
    with support.TempRepo(git=False) as other:
      self.assertEqual(other.run("init")[0], 0)
      (other.root / "src").mkdir()
      payload["tool_input"]["file_path"] = "../.slicer/index.json"
      with patch.dict(os.environ, {"CLAUDE_PROJECT_DIR": str(other.root / "src")}), \
           patch("sys.stdin", io.StringIO(json.dumps(payload))):
        code, out, err = self.repo.run("ai", "guard")
    self.assertEqual((code, err), (0, ""))
    self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "deny")

  def test_AiGuard_MalformedStdin_AllowsSilently(self) -> None:
    for raw in ("{", "null", "[]", "42", "", '{}',
                '{"tool_name":"Read","tool_input":null}',
                '{"tool_name":"Read","tool_input":{"file_path":42}}'):
      with self.subTest(raw=raw), patch("sys.stdin", io.StringIO(raw)):
        self.assertEqual(self.repo.run("ai", "guard"), (0, "", ""))

  def test_AiGuard_OutsideAProject_AllowsSilently(self) -> None:
    with support.TempRepo(git=False) as outside, support.isolated_discovery(outside.root):
      payload = {"cwd": str(outside.root), "tool_name": "Read",
                 "tool_input": {"file_path": ".slicer/index.json"}}
      with patch("sys.stdin", io.StringIO(json.dumps(payload))):
        self.assertEqual(outside.run("ai", "guard"), (0, "", ""))

  def test_AiGuard_UnjudgedToolsAndRootGrep_Allow(self) -> None:
    for tool in ("Bash", "Glob", "Unknown"):
      self.check(self.payload(tool, ".slicer/index.json"), False)
    self.check(self.payload("Grep", "."), False)
    payload = self.payload("Grep", ".slicer")
    del payload["tool_input"]["path"]
    self.check(payload, False)

  def test_AiGuard_Reason_NamesTheCliReads(self) -> None:
    for command in ("slicer show ID --json --lean", "slicer list --json --lean",
                    "slicer log --json --lean", "slicer ai instructions"):
      self.assertIn(command, ai.GUARD_REASON)


if __name__ == "__main__":
  unittest.main()
