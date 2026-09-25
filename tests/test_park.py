"""park must read the configured parked status, not a hardcoded literal (S23)."""

from __future__ import annotations

import json
import unittest

import support

from slicer import tui
from slicer.config import Config
from slicer.errors import ConfigError


def set_statuses(repo: support.TempRepo, statuses: dict, **extra) -> None:
  path = repo.root / ".slicer/config.json"
  cfg = json.loads(path.read_text())
  cfg["statuses"] = statuses
  cfg.update(extra)
  path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class ParkCliTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.run("add", "a thing")
    return repo

  def test_Park_DefaultProject_UsesParked(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("park", "S01")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().index.require("S01").status, "parked")

  def test_Park_CustomParkedStatus_UsesIt(self) -> None:
    with self.repo() as repo:
      set_statuses(
        repo,
        {"open": "—", "done": "done", "on-hold": "on hold", "retired": "retired"},
        parked_status="on-hold",
      )
      code, _, err = repo.run("park", "S01")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().index.require("S01").status, "on-hold")

  def test_Park_ProjectWithoutAParkState_RefusesClearly(self) -> None:
    with self.repo() as repo:
      set_statuses(
        repo, {"open": "—", "done": "done", "retired": "retired"}, parked_status=""
      )
      code, _, err = repo.run("park", "S01")
      self.assertEqual(code, 2)
      self.assertIn("no parked status", err)
      # not the old, confusing "unknown status 'parked'"
      self.assertNotIn("unknown status", err)
      self.assertEqual(repo.state().index.require("S01").status, "open")


class ParkTuiTests(unittest.TestCase):
  def test_TuiParkKey_DefaultProject_Parks(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "a thing")
      state = repo.state()
      result = tui.act(state, "p", "S01")
      self.assertEqual(state.index.require("S01").status, "parked")
      self.assertIn("parked", result.message)

  def test_TuiParkKey_NoParkState_ReportsCleanly(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "a thing")
      set_statuses(
        repo, {"open": "—", "done": "done", "retired": "retired"}, parked_status=""
      )
      state = repo.state()
      result = tui.act(state, "p", "S01")
      self.assertIn("no parked status", result.message)
      self.assertEqual(state.index.require("S01").status, "open")


class ParkedStatusConfigTests(unittest.TestCase):
  BASE = {
    "version": 1,
    "id": {"prefix": "S", "width": 2},
    "open_status": "open",
    "done_status": "done",
    "sections": ["Why"],
    "done_dir": "done",
  }

  def test_Config_WithoutParkedInStatuses_DefaultsToEmpty(self) -> None:
    cfg = Config.from_dict(self.BASE | {"statuses": {"open": "—", "done": "done"}})
    self.assertEqual(cfg.parked_status, "")

  def test_Config_WithParkedInStatuses_DefaultsToParked(self) -> None:
    cfg = Config.from_dict(
      self.BASE | {"statuses": {"open": "—", "done": "done", "parked": "parked"}}
    )
    self.assertEqual(cfg.parked_status, "parked")

  def test_Config_ParkedStatusNotInStatuses_IsRejected(self) -> None:
    with self.assertRaises(ConfigError):
      Config.from_dict(
        self.BASE | {"statuses": {"open": "—", "done": "done"}, "parked_status": "parked"}
      )


if __name__ == "__main__":
  unittest.main()
