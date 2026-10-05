"""`satisfies_dependencies` picks which statuses let a dependent start (S207)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import support
from slicer import graph, tui
from slicer.config import SCHEMA_VERSION, Config
from slicer.errors import ConfigError


def _poke(repo: support.TempRepo, **changes: object) -> None:
  path = repo.root / ".slicer" / "config.json"
  data = json.loads(path.read_text(encoding="utf-8"))
  data.update(changes)
  path.write_text(json.dumps(data) + "\n", encoding="utf-8")


def _landed(repo: support.TempRepo, satisfies: list[str] | None = None) -> None:
  """A project whose run branch can finish work as `landed` before `done`."""
  statuses = json.loads((repo.root / ".slicer/config.json").read_text())["statuses"]
  changes: dict[str, object] = {"statuses": {**statuses, "landed": "landed", "waiting": "waiting"}}
  if satisfies is not None:
    changes["satisfies_dependencies"] = satisfies
  _poke(repo, **changes)


def _ids(out: str) -> list[str]:
  return [item["id"] for item in json.loads(out)]


class DefaultSatisfactionTests(unittest.TestCase):
  def test_Absent_UsesDoneStatus(self) -> None:
    self.assertEqual(Config().satisfying_statuses(), frozenset({"done"}))

  def test_Absent_UsesARenamedDoneRole(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      data = json.loads((repo.root / ".slicer/config.json").read_text())
      statuses = {k: v for k, v in data["statuses"].items() if k != "done"}
      statuses["shipped"] = "shipped"
      _poke(repo, statuses=statuses, done_status="shipped")
      self.assertEqual(repo.state().config.satisfying_statuses(), frozenset({"shipped"}))
      repo.run("add", "base")
      repo.run("add", "later", "--depends-on", "S01")
      self.assertEqual(json.loads(repo.run("deps", "S02", "--json")[1])["blocked_by"], ["S01"])
      self.assertEqual(repo.run("set", "S01", "--status", "shipped")[0], 0)
      self.assertEqual(json.loads(repo.run("deps", "S02", "--json")[1])["blocked_by"], [])

  def test_Graph_StillAcceptsOneStatusKey(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "base")
      repo.run("add", "later", "--depends-on", "S01")
      index = repo.state().index
      self.assertEqual(graph.blocked_by(index, index.require("S02"), "done"), ["S01"])
      self.assertEqual(graph.blocked_by(index, index.require("S02"), {"open"}), [])


class ConfiguredSatisfactionTests(unittest.TestCase):
  def _project(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    _landed(repo, ["done", "landed"])
    repo.run("add", "base")
    repo.run("add", "later", "--depends-on", "S01")
    return repo

  def test_LandedDependency_UnblocksNextDepsAndList(self) -> None:
    with self._project() as repo:
      self.assertEqual(json.loads(repo.run("next", "--ready", "--json")[1])["item"]["id"], "S01")
      self.assertEqual(json.loads(repo.run("deps", "S02", "--json")[1])["blocked_by"], ["S01"])
      repo.run("set", "S01", "--status", "landed")
      self.assertEqual(json.loads(repo.run("next", "--ready", "--json")[1])["item"]["id"], "S02")
      self.assertEqual(json.loads(repo.run("deps", "S02", "--json")[1])["blocked_by"], [])
      ranked = repo.state()
      order = graph.ranked_order(ranked.index, ranked.config, list(ranked.index.items))
      self.assertEqual([item.id for item in order][0], "S02")

  def test_LandedDependency_UnblocksTheTuiRowAndBatch(self) -> None:
    with self._project() as repo:
      state = repo.state()
      rows = tui.item_rows(state, [state.index.require("S02")])
      self.assertTrue(rows[0].blocked)
      repo.run("set", "S01", "--status", "landed")
      state = repo.state()
      rows = tui.item_rows(state, [state.index.require("S02")])
      self.assertFalse(rows[0].blocked)
      batch = json.loads(repo.run("next", "--batch", "2", "--json")[1])
      self.assertEqual([e["id"] for e in batch["items"]], ["S02"])
      self.assertEqual(batch["blocked"], [])

  def test_BatchWithUnfinishedDependency_StillPicksItFirst(self) -> None:
    with self._project() as repo:
      batch = json.loads(repo.run("next", "--batch", "2", "--json")[1])
      self.assertEqual([e["id"] for e in batch["items"]], ["S01", "S02"])

  def test_OtherUnfinishedStatus_StillBlocks(self) -> None:
    with self._project() as repo:
      repo.run("set", "S01", "--status", "waiting")
      self.assertEqual(json.loads(repo.run("deps", "S02", "--json")[1])["blocked_by"], ["S01"])
      repo.run("set", "S01", "--status", "done")
      self.assertEqual(json.loads(repo.run("deps", "S02", "--json")[1])["blocked_by"], [])

  def test_ExplicitList_IsAuthoritativeEvenWithoutDone(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      _landed(repo, ["landed"])
      repo.run("add", "base")
      repo.run("add", "later", "--depends-on", "S01")
      repo.run("set", "S01", "--status", "done")
      self.assertEqual(json.loads(repo.run("deps", "S02", "--json")[1])["blocked_by"], ["S01"])

  def test_DuplicateEntries_DoNotChangeBehavior(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      _landed(repo, ["landed", "landed", "done"])
      self.assertEqual(repo.state().config.satisfying_statuses(), frozenset({"landed", "done"}))

  def test_LandedSlice_StaysInItsFolderAndOutOfInWork(self) -> None:
    with self._project() as repo:
      repo.run("promote", "S01")
      before = repo.state().slice_path("S01")
      repo.run("set", "S01", "--status", "landed")
      state = repo.state()
      self.assertEqual(state.slice_path("S01"), before)
      self.assertTrue(Path(before).is_file())
      self.assertEqual(_ids(repo.run("list", "--in-work", "--json")[1]), [])
      repo.run("render")
      self.assertEqual(repo.run("check")[0], 0)

  def test_VerifyCommitSubjectCheck_AppliesOnlyToDone(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      _landed(repo, ["done", "landed"])
      repo.run("add", "base")
      repo.run("add", "control")
      repo.run("set", "S01", "--status", "landed")
      repo.run("set", "S02", "--status", "done")
      repo.commit("unrelated")
      out = repo.run("verify")[1]
      self.assertIn("S02", out)  # done with no commit subject is still reported
      self.assertNotIn("S01", out)


class DependencyValidationTests(unittest.TestCase):
  def _project(self, satisfies: list[str] | None) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    _landed(repo, satisfies)
    for title in ("a", "b", "c"):
      repo.run("add", title)
    return repo

  def test_SelfDependencyAndCycle_StillRefused(self) -> None:
    with self._project(["done", "landed"]) as repo:
      self.assertNotEqual(repo.run("set", "S01", "--depends-on", "S01")[0], 0)
      self.assertEqual(repo.run("set", "S02", "--depends-on", "S01")[0], 0)
      self.assertNotEqual(repo.run("set", "S01", "--depends-on", "S02")[0], 0)
      self.assertNotEqual(repo.run("set", "S01", "--depends-on", "S99")[0], 0)

  def test_RetiredDependency_RefusedUnlessRetiredSatisfies(self) -> None:
    with self._project(["done"]) as repo:
      repo.run("remove", "S01", "--reason", "gone")
      code, _, err = repo.run("set", "S02", "--depends-on", "S01")
      self.assertNotEqual(code, 0)
      self.assertIn("retired", err)
    with self._project(["done", "retired"]) as repo:
      repo.run("remove", "S01", "--reason", "gone")
      code, _, err = repo.run("set", "S02", "--depends-on", "S01")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(repo.run("deps", "S02", "--json")[1])["blocked_by"], [])
      repo.run("render")
      self.assertEqual(repo.run("check")[0], 0)
      self.assertEqual(repo.run("verify")[0], 0)

  def test_OfflineCheck_FlagsRetiredEdgeOnlyWhenRetiredDoesNotSatisfy(self) -> None:
    with self._project(["done", "retired"]) as repo:
      repo.run("set", "S02", "--depends-on", "S01")
      self.assertEqual(repo.run("remove", "S01", "--reason", "gone", "--force")[0], 0)
      self.assertNotIn("retired and can never be done", repo.run("verify")[1])
      _poke(repo, satisfies_dependencies=["done"])
      self.assertIn("retired and can never be done", repo.run("verify")[1])


class ConfigShapeTests(unittest.TestCase):
  def _config(self, value: object) -> None:
    data = Config().to_dict()
    data["satisfies_dependencies"] = value
    Config.from_dict(data)

  def test_BadShapes_AreConfigErrors(self) -> None:
    for bad in ("done", [], ["done", 3], ["done", ""], ["nope"], {"done": True}, 7):
      with self.subTest(value=bad), self.assertRaises(ConfigError):
        self._config(bad)

  def test_UnknownKey_NamesTheKeyAndTheKnownOnes(self) -> None:
    with self.assertRaises(ConfigError) as caught:
      self._config(["done", "nope"])
    self.assertIn("nope", str(caught.exception))
    self.assertIn("known keys", str(caught.exception))

  def test_Validated_ThroughTheCommandLine(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      _poke(repo, satisfies_dependencies=[])
      code, _, err = repo.run("list")
      self.assertEqual(code, 3)
      self.assertIn("satisfies_dependencies", err)


class SchemaTests(unittest.TestCase):
  def test_AbsentPolicy_RoundTripsWithoutTheKey(self) -> None:
    self.assertNotIn("satisfies_dependencies", Config().to_dict())

  def test_Written_AdvancesTheSchemaAndKeepsTheList(self) -> None:
    cfg = Config(satisfies_dependencies=["done"])
    data = cfg.to_dict()
    self.assertEqual(data["version"], SCHEMA_VERSION)
    self.assertEqual(data["satisfies_dependencies"], ["done"])
    self.assertEqual(Config.from_dict(data).satisfies_dependencies, ["done"])

  def test_OlderConfig_LoadsWithTheHistoricalDefaultAndIsNotRestamped(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      path = repo.root / ".slicer/config.json"
      data = json.loads(path.read_text())
      data["version"] = 2
      path.write_text(json.dumps(data) + "\n")
      before = path.read_bytes()
      self.assertEqual(repo.run("list")[0], 0)
      self.assertEqual(repo.state().config.satisfying_statuses(), frozenset({"done"}))
      self.assertEqual(path.read_bytes(), before)

  def test_NewerConfig_IsRefused(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      _poke(repo, version=SCHEMA_VERSION + 1)
      code, _, err = repo.run("list")
      self.assertEqual(code, 3)
      self.assertIn("newer", err.lower())


if __name__ == "__main__":
  unittest.main()
