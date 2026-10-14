"""`slicer config [KEY [VALUE]]`: the effective config, read and written without opening the file."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

import support

from slicer import store
from slicer.config import Config
from slicer.errors import RenderError


def _set_config(repo: support.TempRepo, **changes: object) -> None:
  path = repo.root / ".slicer/config.json"
  cfg = json.loads(path.read_text())
  cfg.update(changes)
  path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _tracking_bytes(repo: support.TempRepo) -> dict[str, bytes]:
  return {
    str(p.relative_to(repo.root)): p.read_bytes()
    for p in sorted((repo.root / ".slicer").rglob("*")) if p.is_file() and p.name != "lock"
  }


class ConfigCommandTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.run("add", "one")
    return repo

  def test_Config_Json_IsTheWholeEffectiveConfig(self) -> None:
    with self.repo() as repo:
      code, out, err = repo.run("config", "--json")
      self.assertEqual(code, 0, err)
      expected = Config.load(repo.root / ".slicer/config.json").to_dict()
      self.assertEqual(json.loads(out), expected)
      for key in ("implement_finish", "done_status", "review_status", "id", "pointers", "sync"):
        self.assertIn(key, expected)

  def test_Config_Key_PrintsTheBareValue(self) -> None:
    with self.repo() as repo:
      code, out, _ = repo.run("config", "implement_finish")
      self.assertEqual((code, out), (0, "done\n"))
      _set_config(repo, implement_finish="handoff")
      code, out, _ = repo.run("config", "implement_finish")
      self.assertEqual((code, out), (0, "handoff\n"))

  def test_Config_KeyJson_IsKeyAndValueOnly(self) -> None:
    with self.repo() as repo:
      _set_config(repo, implement_finish="handoff")
      code, out, _ = repo.run("config", "implement_finish", "--json")
      self.assertEqual(code, 0)
      self.assertEqual(json.loads(out), {"key": "implement_finish", "value": "handoff"})

  def test_Config_DottedKey_ReachesIntoANestedObject(self) -> None:
    with self.repo() as repo:
      _, out, _ = repo.run("config", "id.prefix")
      self.assertEqual(out, "S\n")
      _, out, _ = repo.run("config", "pointers.next_empty")
      self.assertEqual(out, "nothing unmarked\n")

  def test_Config_ListValue_PrintsOneElementPerLine(self) -> None:
    with self.repo() as repo:
      _, out, _ = repo.run("config", "required_sections")
      self.assertEqual(out.splitlines(), Config.load(
        repo.root / ".slicer/config.json").to_dict()["required_sections"])
      self.assertGreater(len(out.splitlines()), 1)

  def test_Config_UnknownKey_IsUsageAndNamesTheKeys(self) -> None:
    with self.repo() as repo:
      for key in ("nope", "implement_finish.x", "id.nope"):
        with self.subTest(key=key):
          code, out, err = repo.run("config", key, "--json")
          self.assertEqual(code, 2)
          error = json.loads(out)["error"]
          self.assertEqual(error["code"], "usage")
          self.assertIn("implement_finish", error["message"])
          self.assertIn("done_status", error["message"])

  def test_Config_NoKeyText_IsOneLinePerTopLevelKey(self) -> None:
    with self.repo() as repo:
      code, out, _ = repo.run("config")
      self.assertEqual(code, 0)
      data = Config.load(repo.root / ".slicer/config.json").to_dict()
      lines = out.splitlines()
      self.assertEqual([line.split()[0] for line in lines], list(data))
      by_key = {line.split()[0]: line for line in lines}
      self.assertTrue(by_key["implement_finish"].endswith("  done"))
      self.assertTrue(by_key["required_sections"].endswith("  Implement, Check"))
      self.assertTrue(by_key["id"].endswith('  {"prefix":"S","width":2}'))

  def test_Config_Run_WritesNothing(self) -> None:
    with self.repo() as repo:
      before = _tracking_bytes(repo)
      for args in (("config",), ("config", "implement_finish"), ("config", "--json")):
        code, _, err = repo.run(*args)
        self.assertEqual(code, 0, err)
      self.assertEqual(_tracking_bytes(repo), before)

  def test_Config_IndexMissing_StillReads(self) -> None:
    with self.repo() as repo:
      (repo.root / ".slicer/index.json").unlink()
      code, out, err = repo.run("config", "implement_finish")
      self.assertEqual((code, out), (0, "done\n"), err)

  def test_Config_UnparseableConfig_FailsLikeSections(self) -> None:
    with self.repo() as repo:
      (repo.root / ".slicer/config.json").write_text("{not json", encoding="utf-8")
      want, want_out, _ = repo.run("sections", "--json")
      code, out, _ = repo.run("config", "--json")
      self.assertNotEqual(want, 0)
      self.assertEqual(code, want)
      self.assertEqual(json.loads(out)["error"]["code"], json.loads(want_out)["error"]["code"])

  def test_Config_OutsideAnyProject_FailsLikeSections(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      want, want_out, _ = repo.run("sections", "--json")
      code, out, _ = repo.run("config", "--json")
      self.assertNotEqual(want, 0)
      self.assertEqual(code, want)
      self.assertEqual(json.loads(out)["error"]["code"], json.loads(want_out)["error"]["code"])


def _raw(repo: support.TempRepo) -> dict:
  return json.loads(repo.read(".slicer/config.json"))


def _error(out: str) -> dict:
  return json.loads(out)["error"]


class ConfigSetTests(unittest.TestCase):
  def repo(self, *, items: bool = True) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    if items:
      repo.run("add", "one")
    return repo

  def test_ConfigSet_Value_ChangesOnlyThatKey(self) -> None:
    with self.repo() as repo:
      _set_config(repo, implement_finish="done")
      before = _raw(repo)
      before.pop("implement_finish")
      raw_before = repo.read(".slicer/config.json")
      code, out, err = repo.run("config", "implement_finish", "handoff")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.run("config", "implement_finish")[1], "handoff\n")
      after = _raw(repo)
      self.assertEqual(after.pop("implement_finish"), "handoff")
      self.assertEqual(after, before)
      # Key order survives: the changed key stays where it was.
      self.assertEqual(list(_raw(repo)), list(json.loads(raw_before)))

  def test_ConfigSet_AbsentKey_IsNotBackfilledIntoOthers(self) -> None:
    with self.repo() as repo:
      path = repo.root / ".slicer/config.json"
      raw = json.loads(path.read_text())
      for gone in ("reviewing_status", "issues_repo", "render_driver_check"):
        raw.pop(gone)
      raw["statuses"].pop("retired", None)
      path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
      expected = dict(raw, claim_owner="X")
      code, _, err = repo.run("config", "claim_owner", "X")
      self.assertEqual(code, 0, err)
      self.assertEqual(_raw(repo), expected)

  def test_ConfigSet_DottedKey_PatchesInsideTheObject(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("config", "pointers.next_empty", "nothing here")
      self.assertEqual(code, 0, err)
      raw = _raw(repo)
      self.assertEqual(raw["pointers"]["next_empty"], "nothing here")
      self.assertEqual(raw["pointers"]["next_format"], "**{{id}}** {{title}}")
      self.assertEqual(raw["id"], {"prefix": "S", "width": 2})

  def test_ConfigSet_TypedValues_AreCoercedByTheKeysType(self) -> None:
    with self.repo(items=False) as repo:
      cases = [
        (("id.width", "3"), ("id", "width"), 3),
        (("git_check", "false"), ("git_check",), False),
        (("git_check", "true"), ("git_check",), True),
        (("sections", '["Why","Implement","Check"]'), ("sections",), ["Why", "Implement", "Check"]),
        (("required_sections", '["Check"]'), ("required_sections",), ["Check"]),
        (("claim_owner", "123"), ("claim_owner",), "123"),
        (("claim_owner", "true"), ("claim_owner",), "true"),
        (("boundary", '["x"]'), ("boundary",), '["x"]'),
        (("satisfies_dependencies", '["done","parked"]'), ("satisfies_dependencies",),
         ["done", "parked"]),
        (("sync.targets", "[]"), ("sync", "targets"), []),
      ]
      for args, path, want in cases:
        with self.subTest(args=args):
          code, _, err = repo.run("config", *args)
          self.assertEqual(code, 0, err)
          value = _raw(repo)
          for part in path:
            value = value[part]
          self.assertEqual(value, want)
          self.assertIs(type(value), type(want))

  def test_ConfigSet_ObjectValue_TakesJson(self) -> None:
    with self.repo() as repo:
      statuses = {"open": "—", "done": "done", "parked": "parked", "later": "later",
                  "retired": "retired", "started": "started", "review": "review",
                  "reviewing": "reviewing", "blocked": "blocked"}
      code, _, err = repo.run("config", "statuses", json.dumps(statuses))
      self.assertEqual(code, 0, err)
      self.assertEqual(_raw(repo)["statuses"], statuses)
      self.assertEqual(json.loads(repo.run("config", "statuses.blocked", "--json")[1])["value"],
                       "blocked")

  def test_ConfigSet_SyncTargets_IsAJsonList(self) -> None:
    with self.repo() as repo:
      target = {"name": "t", "path": "README.md", "match": "^x$", "count": 1, "template": "x"}
      code, _, err = repo.run("config", "sync.targets", json.dumps([target]))
      self.assertEqual(code, 0, err)
      self.assertEqual(_raw(repo)["sync"]["targets"], [target])
      code, out, _ = repo.run("config", "sync.targets", '[{"name":"t"}]', "--json")
      self.assertEqual(code, 2)
      self.assertEqual(_error(out)["code"], "bad_config_value")

  def test_ConfigSet_UncoercibleValue_IsBadConfigValueAndWritesNothing(self) -> None:
    with self.repo() as repo:
      before = _tracking_bytes(repo)
      cases = [
        ("git_check", "maybe"), ("id.width", "x"), ("id.width", "3.5"), ("id.width", "true"),
        ("sections", "Why"), ("sections", "[1]"), ("sections", "{not json"),
        ("statuses", "[]"), ("statuses", '{"open": 1}'), ("exclude_flags", '{"a": 1}'),
        ("sync.targets", '["x"]'), ("pointers.later", "[]"),
      ]
      for key, value in cases:
        with self.subTest(key=key, value=value):
          code, out, _ = repo.run("config", key, value, "--json")
          self.assertEqual(code, 2)
          self.assertEqual(_error(out)["code"], "bad_config_value")
          self.assertIn(key, _error(out)["message"])
          self.assertEqual(_tracking_bytes(repo), before)

  def test_ConfigSet_InvalidResult_IsRefusedWithValidatesMessage(self) -> None:
    with self.repo() as repo:
      before = _tracking_bytes(repo)
      code, out, _ = repo.run("config", "done_status", "finished", "--json")
      self.assertEqual(code, 2)
      error = _error(out)
      self.assertEqual(error["code"], "bad_config_value")
      self.assertIn("done_status 'finished' is not in statuses", error["message"])
      self.assertEqual(_tracking_bytes(repo), before)
      # The same refusal Config.validate gives, for the other example.
      repo.run("config", "review_status", "")
      before = _tracking_bytes(repo)
      code, out, _ = repo.run("config", "implement_finish", "handoff", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(_error(out)["code"], "bad_config_value")
      self.assertIn("needs a review_status", _error(out)["message"])
      self.assertEqual(_tracking_bytes(repo), before)

  def test_ConfigSet_UnknownOrReservedKey_IsUsage(self) -> None:
    with self.repo() as repo:
      before = _tracking_bytes(repo)
      for key in ("nope", "implement_finish.x", "id.nope", "statuses.open", "id.prefix", "version"):
        with self.subTest(key=key):
          code, out, _ = repo.run("config", key, "2", "--json")
          self.assertEqual(code, 2)
          error = _error(out)
          self.assertEqual(error["code"], "usage")
          if key == "id.prefix":
            self.assertIn("slicer id-prefix", error["message"])
          elif key == "version":
            self.assertIn("version", error["message"])
          else:
            self.assertIn("implement_finish", error["message"])
            self.assertIn("done_status", error["message"])
          self.assertEqual(_tracking_bytes(repo), before)

  def test_ConfigSet_WholeIdObject_CannotChangeThePrefix(self) -> None:
    with self.repo() as repo:
      repo.run("id-prefix", "s")
      before = _tracking_bytes(repo)
      cases = [
        ("id", '{"prefix":"Q","width":2}'),
        ("id", '{"width":2}'),
        ("id", "--unset"),
      ]
      for key, value in cases:
        with self.subTest(key=key, value=value):
          code, out, _ = repo.run("config", key, value, "--json")
          self.assertEqual(code, 2)
          error = _error(out)
          self.assertEqual(error["code"], "usage")
          self.assertIn("slicer id-prefix", error["message"])
          self.assertEqual(_tracking_bytes(repo), before)
      # The same prefix and width is still a valid write.
      code, _, err = repo.run("config", "id", '{"prefix":"s","width":2}')
      self.assertEqual(code, 0, err)
      self.assertEqual(_raw(repo)["id"], {"prefix": "s", "width": 2})

  def test_ConfigSet_IdWidthWithItems_IsRefusedByEveryRoute(self) -> None:
    with self.repo() as repo:
      before = _tracking_bytes(repo)
      for key, value in (("id.width", "3"), ("id", '{"prefix":"S","width":3}')):
        with self.subTest(key=key, value=value):
          code, out, _ = repo.run("config", key, value, "--json")
          self.assertEqual(code, 2)
          error = _error(out)
          self.assertEqual(error["code"], "state")
          self.assertIn("id.width", error["message"])
          self.assertIn("before the first item", error["message"])
          self.assertEqual(_tracking_bytes(repo), before)
      # Writing the width the scheme already has changes nothing, so it is not refused.
      code, _, err = repo.run("config", "id.width", "2")
      self.assertEqual(code, 0, err)

  def test_ConfigSet_IdWidthWithItemsAndNonDefaultWidth_UnsetIsRefused(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init", "--id-width", "3")
      repo.run("add", "one")
      before = _tracking_bytes(repo)
      code, out, _ = repo.run("config", "id.width", "--unset", "--json")
      self.assertEqual((code, _error(out)["code"]), (2, "state"))
      code, out, _ = repo.run("config", "id.width", "3", "--json")
      self.assertEqual(code, 0)
      self.assertEqual(_tracking_bytes(repo), before)

  def test_ConfigSet_IdWidthWithEmptyIndex_IsAllowed(self) -> None:
    with self.repo(items=False) as repo:
      code, _, err = repo.run("config", "id.width", "3")
      self.assertEqual(code, 0, err)
      self.assertEqual(_raw(repo)["id"]["width"], 3)
      code, _, err = repo.run("config", "id", '{"prefix":"S","width":4}')
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.run("add", "first")[0], 0)
      self.assertEqual(repo.run("next-id")[1], "S0002\n")

  def test_ConfigUnset_EmptyObjectAlreadyInFile_ChangesNothing(self) -> None:
    with self.repo() as repo:
      path = repo.root / ".slicer/config.json"
      raw = json.loads(path.read_text())
      raw["sync"] = {}
      path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
      before = _tracking_bytes(repo)
      code, out, err = repo.run("config", "sync.targets", "--unset")
      self.assertEqual(code, 0, err)
      self.assertEqual(out, "sync.targets is already []\n")
      self.assertEqual(_tracking_bytes(repo), before)

  def test_ConfigUnset_Key_RemovesItSoTheDefaultApplies(self) -> None:
    with self.repo() as repo:
      repo.run("config", "satisfies_dependencies", '["done","parked"]')
      self.assertEqual(repo.run("config", "satisfies_dependencies")[1], "done\nparked\n")
      code, _, err = repo.run("config", "satisfies_dependencies", "--unset")
      self.assertEqual(code, 0, err)
      self.assertNotIn("satisfies_dependencies", _raw(repo))
      code, out, _ = repo.run("config", "satisfies_dependencies", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(_error(out)["code"], "usage")
      repo.run("config", "implement_finish", "handoff")
      code, _, err = repo.run("config", "implement_finish", "--unset")
      self.assertEqual(code, 0, err)
      self.assertNotIn("implement_finish", _raw(repo))
      self.assertEqual(repo.run("config", "implement_finish")[1], "done\n")

  def test_ConfigUnset_NestedKey_PrunesAnObjectItEmptied(self) -> None:
    with self.repo() as repo:
      path = repo.root / ".slicer/config.json"
      raw = json.loads(path.read_text())
      raw.pop("pointers")
      path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
      before = path.read_bytes()
      repo.run("config", "pointers.next_empty", "none")
      self.assertEqual(_raw(repo)["pointers"], {"next_empty": "none"})
      repo.run("config", "pointers.next_empty", "--unset")
      self.assertEqual(path.read_bytes(), before)

  def test_ConfigSet_ThenUnset_LeavesTheFileByteIdentical(self) -> None:
    with self.repo() as repo:
      path = repo.root / ".slicer/config.json"
      raw = json.loads(path.read_text())
      raw.pop("claim_owner")
      path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
      before = path.read_bytes()
      self.assertEqual(repo.run("config", "claim_owner", "X")[0], 0)
      self.assertNotEqual(path.read_bytes(), before)
      self.assertEqual(repo.run("config", "claim_owner", "--unset")[0], 0)
      self.assertEqual(path.read_bytes(), before)

  def test_ConfigUnset_WithValueOrWithoutKey_IsUsage(self) -> None:
    with self.repo() as repo:
      before = _tracking_bytes(repo)
      for args in (("implement_finish", "handoff", "--unset"), ("--unset",)):
        with self.subTest(args=args):
          code, out, _ = repo.run("config", *args, "--json")
          self.assertEqual(code, 2)
          self.assertEqual(_error(out)["code"], "usage")
      self.assertEqual(_tracking_bytes(repo), before)

  def test_ConfigRead_WithRenderOrStrict_IsUsage(self) -> None:
    with self.repo() as repo:
      for flag in ("--render", "--strict"):
        with self.subTest(flag=flag):
          code, out, _ = repo.run("config", "implement_finish", flag, "--json")
          self.assertEqual(code, 2)
          self.assertEqual(_error(out)["code"], "usage")

  def test_ConfigSet_SameValueAlreadyInTheFile_WritesAndLogsNothing(self) -> None:
    with self.repo() as repo:
      _set_config(repo, claim_owner="X")
      before = _tracking_bytes(repo)
      code, out, err = repo.run("config", "claim_owner", "X", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out), {"key": "claim_owner", "value": "X", "previous": "X"})
      self.assertEqual(_tracking_bytes(repo), before)
      path = repo.root / ".slicer/config.json"
      raw = json.loads(path.read_text())
      raw.pop("issues_repo")
      path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
      before = _tracking_bytes(repo)
      code, _, err = repo.run("config", "issues_repo", "--unset")
      self.assertEqual(code, 0, err)
      self.assertEqual(_tracking_bytes(repo), before)

  def test_ConfigSet_NoChangeWithRender_RendersNothing(self) -> None:
    with self.repo() as repo:
      _set_config(repo, claim_owner="X")
      for flags in (("--render",), ("--render", "--strict")):
        with self.subTest(flags=flags):
          code, out, err = repo.run("config", "claim_owner", "X", *flags)
          self.assertEqual(code, 0, err)
          self.assertEqual(out, "claim_owner is already X\n")

  def test_ConfigSet_LockHeld_WriteIsLockedAndReadStillWorks(self) -> None:
    if store.fcntl is None:  # pragma: no cover - POSIX only
      self.skipTest("no flock on this platform")
    with self.repo() as repo:
      before = _tracking_bytes(repo)
      with store.project_lock(repo.root), patch.dict(os.environ, {"SLICER_LOCK_TIMEOUT": "0"}):
        code, out, _ = repo.run("config", "implement_finish", "handoff", "--json")
        self.assertEqual((code, _error(out)["code"]), (3, "locked"))
        code, out, _ = repo.run("config", "implement_finish", "--unset", "--json")
        self.assertEqual((code, _error(out)["code"]), (3, "locked"))
        self.assertEqual(repo.run("config", "implement_finish"), (0, "done\n", ""))
        self.assertEqual(repo.run("config")[0], 0)
      self.assertEqual(_tracking_bytes(repo), before)

  def test_ConfigSet_Write_LogsOneEntryWithOldAndNewValue(self) -> None:
    with self.repo(items=False) as repo:
      repo.run("config", "id.width", "3")
      _, out, _ = repo.run("log", "--action", "config", "--json")
      entries = json.loads(out)
      self.assertEqual(len(entries), 1)
      entry = entries[0]
      self.assertEqual((entry["item"], entry["action"]), ("config.id.width", "config"))
      self.assertEqual((entry["from"], entry["to"]), ("2", "3"))
      repo.run("config", "implement_finish", "handoff")
      repo.run("config", "implement_finish", "--unset")
      _, out, _ = repo.run("log", "--action", "config", "--json")
      pairs = [(e["item"], e["from"], e["to"]) for e in json.loads(out)]
      self.assertEqual(sorted(pairs), sorted([
        ("config.id.width", "2", "3"),
        ("config.implement_finish", "done", "handoff"),
        ("config.implement_finish", "handoff", "done"),
      ]))

  def test_ConfigSet_Json_IsKeyValueAndPrevious(self) -> None:
    with self.repo(items=False) as repo:
      code, out, _ = repo.run("config", "implement_finish", "handoff", "--json")
      self.assertEqual(code, 0)
      self.assertEqual(json.loads(out),
                       {"key": "implement_finish", "value": "handoff", "previous": "done"})
      code, out, _ = repo.run("config", "id.width", "4", "--json")
      self.assertEqual(json.loads(out), {"key": "id.width", "value": 4, "previous": 2})
      code, out, _ = repo.run("config", "implement_finish", "--unset", "--json")
      self.assertEqual(json.loads(out),
                       {"key": "implement_finish", "value": "done", "previous": "handoff"})

  def test_ConfigSet_Text_SaysWhatChanged(self) -> None:
    with self.repo() as repo:
      code, out, _ = repo.run("config", "implement_finish", "handoff")
      self.assertEqual((code, out), (0, "implement_finish: done -> handoff\n"))
      code, out, _ = repo.run("config", "claim_owner", "X")
      self.assertEqual(out, 'claim_owner: "" -> X\n')
      code, out, _ = repo.run("config", "claim_owner", "--unset")
      self.assertEqual(out, 'claim_owner: X -> "" (default)\n')
      self.assertEqual(repo.run("config", "exclude_flags", '["a"]')[1], "exclude_flags: [] -> a\n")
      self.assertEqual(
        repo.run("config", "exclude_flags", "--unset")[1], "exclude_flags: a -> [] (default)\n")
      self.assertEqual(
        repo.run("config", "satisfies_dependencies", '["done"]')[1],
        "satisfies_dependencies: (unset) -> done\n")
      self.assertEqual(
        repo.run("config", "satisfies_dependencies", "--unset")[1],
        "satisfies_dependencies: done -> (unset)\n")

  def test_ConfigSet_StatusLabelWithRender_RendersAndCheckIsClean(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      self.assertEqual(repo.run("check")[0], 0)
      code, out, err = repo.run("config", "statuses", json.dumps(
        {"open": "todo", "done": "done", "parked": "parked", "later": "later"}),
        "--render", "--strict")
      self.assertEqual(code, 0, err)
      self.assertIn("rendered", out)
      self.assertIn("todo", repo.read(".slicer/render/ROADMAP.md"))
      self.assertEqual(repo.run("check")[0], 0)

  def test_ConfigSet_BoundaryWithRenderStrict_RendersAndCheckIsClean(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      code, out, err = repo.run("config", "boundary", "**Out of scope:**", "--render", "--strict")
      self.assertEqual(code, 0, err)
      self.assertIn("rendered", out)
      self.assertEqual(repo.run("check")[0], 0)

  def test_ConfigSet_StatusLabelWithoutRender_CheckReportsDrift(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      code, _, err = repo.run("config", "statuses", json.dumps(
        {"open": "todo", "done": "done", "parked": "parked", "later": "later"}))
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.run("check")[0], 1)

  def test_ConfigSet_StrictRenderFailure_WritesNothing(self) -> None:
    with self.repo() as repo:
      before = _tracking_bytes(repo)
      with patch("slicer.cli.render.plan", side_effect=RenderError("boom")):
        code, out, _ = repo.run("config", "boundary", "x", "--render", "--strict")
      self.assertEqual(code, 2)
      self.assertEqual(out, "")
      self.assertEqual(_tracking_bytes(repo), before)

  def test_ConfigSet_Write_ReadsBackThroughConfigLoad(self) -> None:
    with self.repo(items=False) as repo:
      repo.run("config", "id.width", "3")
      self.assertEqual(Config.load(repo.root / ".slicer/config.json").id_width, 3)

  def test_ConfigSet_MergeInProgress_IsRefused(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      (repo.root / ".git" / "MERGE_HEAD").write_text("0" * 40 + "\n")
      before = _tracking_bytes(repo)
      code, out, _ = repo.run("config", "implement_finish", "handoff", "--json")
      self.assertEqual((code, _error(out)["code"]), (2, "merge_in_progress"))
      self.assertEqual(_tracking_bytes(repo), before)


if __name__ == "__main__":
  unittest.main()
