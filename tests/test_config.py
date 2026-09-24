"""A bad config must report, not traceback."""

from __future__ import annotations

import json
import unittest

import support


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
      self.assertEqual(code, 2)
      self.assertIn("id.width", err)

  def test_ZeroIdWidth_IsRefused(self) -> None:
    with self.repo() as repo:
      poke(repo, "config.json", id={"prefix": "S", "width": 0})
      self.assertEqual(repo.run("list")[0], 2)

  def test_EmptyIdPrefix_IsRefused(self) -> None:
    with self.repo() as repo:
      poke(repo, "config.json", id={"prefix": "", "width": 2})
      code, _, err = repo.run("list")
      self.assertEqual(code, 2)
      self.assertIn("id.prefix", err)

  def test_BadConfig_Json_CarriesTheEnvelope(self) -> None:
    with self.repo() as repo:
      poke(repo, "config.json", id={"prefix": "S", "width": -1})
      code, out, _ = repo.run("list", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out)["error"]["code"], "config")

  def test_ValidScheme_IsAccepted(self) -> None:
    with self.repo() as repo:
      poke(repo, "config.json", id={"prefix": "TASK-", "width": 3})
      self.assertEqual(repo.run("list")[0], 0)


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
      self.assertEqual(code, 2)
      self.assertIn("not a valid regular expression", err)

  def test_UncompilableMatch_FailsEveryCommandThatLoadsState(self) -> None:
    # Validation is at load, so it does not matter which command you reach for.
    with self.repo() as repo:
      poke(repo, "config.json", sync=self.target("^Status:["))
      for argv in (("list",), ("check",), ("verify",), ("next",)):
        with self.subTest(argv[0]):
          self.assertEqual(repo.run(*argv)[0], 2)

  def test_UncompilableMatch_Json_CarriesTheEnvelope(self) -> None:
    with self.repo() as repo:
      poke(repo, "config.json", sync=self.target("(unclosed"))
      code, out, _ = repo.run("list", "--json")
      self.assertEqual(code, 2)
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
      self.assertEqual(code, 2)
      self.assertIn("id_width", err)
      self.assertIn("index.json", err)

  def test_HandEditedIndexPrefix_IsRefused(self) -> None:
    with self.repo() as repo:
      poke(repo, "index.json", id_prefix="")
      self.assertEqual(repo.run("list")[0], 2)

  def test_BadIndexWidth_IsRefusedEvenWhenTheIndexIsEmpty(self) -> None:
    # The empty-index reconciliation would otherwise repair it silently, so
    # the same bad value would behave differently depending on project age.
    with support.TempRepo() as repo:
      repo.run("init")
      poke(repo, "index.json", id_width=-1)
      code, _, err = repo.run("add", "A thing")
      self.assertEqual(code, 2)
      self.assertIn("id_width", err)


if __name__ == "__main__":
  unittest.main()
