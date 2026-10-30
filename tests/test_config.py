"""A bad config must report, not traceback."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import support
from slicer.config import Config
from slicer.errors import ConfigError


def poke(repo: support.TempRepo, name: str, **changes: object) -> None:
  path = repo.root / ".slicer" / name
  data = json.loads(path.read_text())
  data.update(changes)
  path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class ConfigValidationTests(unittest.TestCase):
  def repo(self, with_item: bool = True) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    if with_item:
      repo.run("add", "A thing")
    return repo

  def test_NegativeIdWidth_IsRefusedNotATraceback(self) -> None:
    # Was: ValueError: Invalid format specifier '0-1d' for object of type 'int'
    with self.repo() as repo:
      poke(repo, "config.json", id={"prefix": "S", "width": -1})
      code, _, err = repo.run("add", "another")
      self.assertEqual(code, 3)
      self.assertIn("id.width", err)

  def test_ZeroIdWidth_IsRefused(self) -> None:
    with self.repo() as repo:
      poke(repo, "config.json", id={"prefix": "S", "width": 0})
      self.assertEqual(repo.run("list")[0], 3)

  def test_EmptyIdPrefix_IsRefused(self) -> None:
    with self.repo() as repo:
      poke(repo, "config.json", id={"prefix": "", "width": 2})
      code, _, err = repo.run("list")
      self.assertEqual(code, 3)
      self.assertIn("id.prefix", err)

  def test_BadConfig_Json_CarriesTheEnvelope(self) -> None:
    with self.repo() as repo:
      poke(repo, "config.json", id={"prefix": "S", "width": -1})
      code, out, _ = repo.run("list", "--json")
      self.assertEqual(code, 3)
      self.assertEqual(json.loads(out)["error"]["code"], "config")

  def test_ValidScheme_IsAccepted(self) -> None:
    with self.repo() as repo:
      poke(repo, "config.json", id={"prefix": "TASK-", "width": 3})
      self.assertEqual(repo.run("list")[0], 0)

  def test_RequiredSectionOutsideSections_IsAConfigError(self) -> None:
    with self.repo() as repo:
      poke(repo, "config.json", required_sections=["Nope"])
      code, _, err = repo.run("list")
      self.assertEqual(code, 3)
      self.assertIn("required_sections", err)
      self.assertIn("Nope", err)

  def test_RepeatedRequiredSection_IsAConfigError(self) -> None:
    with self.repo() as repo:
      poke(repo, "config.json", required_sections=["Check", "Check"])
      code, _, err = repo.run("list")
      self.assertEqual(code, 3)
      self.assertIn("repeats", err)

  def test_MissingImplementFinish_StaysDone(self) -> None:
    with self.repo() as repo:
      path = repo.root / ".slicer/config.json"
      data = json.loads(path.read_text())
      data["version"] = 1
      data.pop("implement_finish", None)
      path.write_text(json.dumps(data) + "\n")
      self.assertEqual(repo.state().config.implement_finish, "done")
      self.assertEqual(repo.state().config.version, 1)

  def test_HandoffWithoutReviewStatus_IsAConfigError(self) -> None:
    with self.repo() as repo:
      poke(repo, "config.json", implement_finish="handoff", review_status="")
      code, _, err = repo.run("list")
      self.assertEqual(code, 3)
      self.assertIn("implement_finish", err)
      self.assertIn("review_status", err)

  def test_ImplementFinish_MustBeDoneOrHandoff(self) -> None:
    with self.repo() as repo:
      poke(repo, "config.json", implement_finish="ship")
      code, _, err = repo.run("list")
      self.assertEqual(code, 3)
      self.assertIn("done", err)
      self.assertIn("handoff", err)
      poke(repo, "config.json", implement_finish=["done"])
      code, _, err = repo.run("list")
      self.assertEqual(code, 3)
      self.assertIn("implement_finish", err)

  def test_RequiredSections_MustBeAListOfNames(self) -> None:
    with self.repo() as repo:
      poke(repo, "config.json", required_sections="Check")
      code, _, err = repo.run("list")
      self.assertEqual(code, 3)
      self.assertIn("list of section names", err)


class SectionsCommandTests(unittest.TestCase):
  def test_Sections_MarksRequiredRows_AndReturnsJson(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      code, text, err = repo.run("sections")
      self.assertEqual((code, err), (0, ""))
      self.assertIn("Implement  required", text.splitlines())
      self.assertIn("Check  required", text.splitlines())
      self.assertIn("Why", text.splitlines())
      payload = json.loads(repo.run("sections", "--json")[1])
      self.assertEqual(payload["required"], ["Implement", "Check"])
      self.assertIn("Why", payload["sections"])
      self.assertEqual(payload["sections"][:1], ["Why"])

  def test_Sections_MissingKey_StillReportsTheHistoricalPair(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      path = repo.root / ".slicer/config.json"
      data = json.loads(path.read_text())
      del data["required_sections"]
      path.write_text(json.dumps(data) + "\n")
      payload = json.loads(repo.run("sections", "--json")[1])
      self.assertEqual(payload["required"], ["Implement", "Check"])

  def test_Sections_DoesNotReadTheIndexLockOrWrite(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "A thing")
      (repo.root / ".slicer/index.json").write_text("{", encoding="utf-8")
      before = {p.relative_to(repo.root): p.read_bytes()
                for p in repo.root.rglob("*") if p.is_file()}
      with patch("slicer.cli.store.project_lock", side_effect=AssertionError("lock")), \
           patch("slicer.store.read_index", side_effect=AssertionError("index")):
        code, out, err = repo.run("sections", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out)["required"], ["Implement", "Check"])
      after = {p.relative_to(repo.root): p.read_bytes()
               for p in repo.root.rglob("*") if p.is_file()}
      self.assertEqual(after, before)


class SyncPatternTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.run("add", "A thing")
    return repo

  def target(self, match: str) -> dict:
    return {
      "targets": [
        {"name": "plan", "path": "plan.md", "match": match, "count": 1,
         "template": "Status: {{next}}"}
      ]
    }

  def test_UncompilableMatch_IsAConfigErrorNotATraceback(self) -> None:
    # Was: re.PatternError: unterminated character set at position 8
    with self.repo() as repo:
      poke(repo, "config.json", sync=self.target("^Status:["))
      repo.write("plan.md", "Status: x\n")
      code, _, err = repo.run("sync")
      self.assertEqual(code, 3)
      self.assertIn("not a valid regular expression", err)

  def test_UncompilableMatch_FailsEveryCommandThatLoadsState(self) -> None:
    # Validation is at load, so it does not matter which command you reach for.
    with self.repo() as repo:
      poke(repo, "config.json", sync=self.target("^Status:["))
      for argv in (("list",), ("check",), ("verify",), ("next",)):
        with self.subTest(argv[0]):
          self.assertEqual(repo.run(*argv)[0], 3)

  def test_UncompilableMatch_Json_CarriesTheEnvelope(self) -> None:
    with self.repo() as repo:
      poke(repo, "config.json", sync=self.target("(unclosed"))
      code, out, _ = repo.run("list", "--json")
      self.assertEqual(code, 3)
      error = json.loads(out)["error"]
      self.assertEqual(error["code"], "config")
      self.assertIn("plan", error["message"])

  def test_ValidMatch_IsAccepted(self) -> None:
    with self.repo() as repo:
      poke(repo, "config.json", sync=self.target("^Status:.*$"))
      self.assertEqual(repo.run("list")[0], 0)


class IndexSchemeValidationTests(unittest.TestCase):
  """`format_id` reads the index's copy, so the config check is not enough."""

  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.run("add", "A thing")
    return repo

  def test_HandEditedIndexWidth_IsRefusedNamingTheFile(self) -> None:
    with self.repo() as repo:
      poke(repo, "index.json", id_width=-1)
      code, _, err = repo.run("add", "another")
      self.assertEqual(code, 3)
      self.assertIn("id_width", err)
      self.assertIn("index.json", err)

  def test_HandEditedIndexPrefix_IsRefused(self) -> None:
    with self.repo() as repo:
      poke(repo, "index.json", id_prefix="")
      self.assertEqual(repo.run("list")[0], 3)

  def test_BadIndexWidth_IsRefusedEvenWhenTheIndexIsEmpty(self) -> None:
    # The empty-index reconciliation would otherwise repair it silently, so
    # the same bad value would behave differently depending on project age.
    with support.TempRepo() as repo:
      repo.run("init")
      poke(repo, "index.json", id_width=-1)
      code, _, err = repo.run("add", "A thing")
      self.assertEqual(code, 3)
      self.assertIn("id_width", err)


class RelayContractConfigTests(unittest.TestCase):
  def test_Config_RelayContractNotAPositiveInt_Refused(self) -> None:
    for bad in (0, -1, 8.5, "8", True, [8]):
      with self.subTest(bad=bad), support.TempRepo() as repo:
        repo.run("init")
        poke(repo, "config.json", relay_contract=bad)
        code, _, err = repo.run("list")
        self.assertEqual(code, 3)
        self.assertIn("relay_contract", err)

  def test_Config_RelayContractSet_LoadsAndSurvivesAnUnrelatedWrite(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      poke(repo, "config.json", relay_contract=7)
      self.assertEqual(repo.run("config", "relay_contract")[1], "7\n")
      self.assertEqual(repo.run("config", "claim_owner", "X")[0], 0)
      self.assertEqual(json.loads((repo.root / ".slicer/config.json").read_text())["relay_contract"], 7)


class CheckCommandConfigTests(unittest.TestCase):
  def test_Config_CheckCommand_DefaultsEmptyAndRoundTrips(self) -> None:
    self.assertEqual(Config.from_dict({}).check_command, "")
    cfg = Config.from_dict({"check_command": "make test"})
    self.assertEqual(cfg.to_dict()["check_command"], "make test")
    self.assertEqual(Config.from_dict(cfg.to_dict()).check_command, "make test")

  def test_Config_CheckCommandNotString_IsConfigError(self) -> None:
    for bad in (5, ["make test"], None):
      with self.subTest(bad=bad):
        with self.assertRaises(ConfigError) as caught:
          Config.from_dict({"check_command": bad})
        self.assertIn("check_command", str(caught.exception))


class TuiColumnsConfigTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.run("add", "A thing")
    return repo

  def test_Config_TuiColumnsUnknownName_Refused(self) -> None:
    with self.repo() as repo:
      poke(repo, "config.json", tui={"columns": ["claim", "owner"]})
      code, _, err = repo.run("list")
      self.assertEqual(code, 3)
      self.assertIn("tui.columns", err)
      self.assertIn("'owner'", err)
      self.assertIn("claim, size, score", err)

  def test_Config_TuiColumnsRepeatedName_Refused(self) -> None:
    with self.repo() as repo:
      poke(repo, "config.json", tui={"columns": ["score", "claim", "score"]})
      code, _, err = repo.run("list")
      self.assertEqual(code, 3)
      self.assertIn("tui.columns repeats 'score'", err)

  def test_Config_TuiColumnsNotAListOfStrings_Refused(self) -> None:
    for bad in ("score", [1], {"score": 1}, None):
      with self.subTest(bad=bad), self.repo() as repo:
        poke(repo, "config.json", tui={"columns": bad})
        code, _, err = repo.run("list")
        self.assertEqual(code, 3)
        self.assertIn("tui.columns must be a list", err)

  def test_Config_TuiNotAnObject_Refused(self) -> None:
    with self.repo() as repo:
      poke(repo, "config.json", tui=["score"])
      code, _, err = repo.run("list")
      self.assertEqual(code, 3)
      self.assertIn("tui must be an object", err)

  def test_Config_TuiColumnsEmptyList_Accepted(self) -> None:
    with self.repo() as repo:
      poke(repo, "config.json", tui={"columns": []})
      self.assertEqual(repo.run("list")[0], 0)
      code, out, err = repo.run("config", "tui.columns")
      self.assertEqual((code, out), (0, ""), err)

  def test_Config_TuiColumnsMissing_IsTheS277Default(self) -> None:
    with self.repo() as repo:
      path = repo.root / ".slicer" / "config.json"
      data = json.loads(path.read_text())
      data.pop("tui")
      path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
      self.assertEqual(repo.run("config", "tui.columns")[1], "claim\nsize\nscore\n")

  def test_Config_TuiColumnsSetThroughTheCli_ReadsBackAndUnsets(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("config", "tui.columns", '["score"]')
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.run("config", "tui.columns")[1], "score\n")
      self.assertEqual(json.loads((repo.root / ".slicer/config.json").read_text())["tui"],
                       {"columns": ["score"]})
      code, _, err = repo.run("config", "tui.columns", '["bogus"]')
      self.assertEqual(code, 2)
      self.assertIn("tui.columns", err)
      self.assertEqual(repo.run("config", "tui.columns")[1], "score\n")
      self.assertEqual(repo.run("config", "tui.columns", "--unset")[0], 0)
      self.assertEqual(repo.run("config", "tui.columns")[1], "claim\nsize\nscore\n")


if __name__ == "__main__":
  unittest.main()
