"""`slicer config [KEY]`: the effective config, read without opening the file."""

from __future__ import annotations

import json
import unittest

import support

from slicer.config import Config


def _set_config(repo: support.TempRepo, **changes: object) -> None:
  path = repo.root / ".slicer/config.json"
  cfg = json.loads(path.read_text())
  cfg.update(changes)
  path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _tracking_bytes(repo: support.TempRepo) -> dict[str, bytes]:
  return {
    str(p.relative_to(repo.root)): p.read_bytes()
    for p in sorted((repo.root / ".slicer").rglob("*")) if p.is_file()
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


if __name__ == "__main__":
  unittest.main()
