"""A claim records who started an item, and list shows that apart from open rows."""

from __future__ import annotations

import json
import os
import re
import unittest
from unittest.mock import patch

import support

from slicer import vcs
from slicer.errors import RenderError, StateError


WHEN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _owner(repo: support.TempRepo, name: str) -> None:
  path = repo.root / ".slicer/config.json"
  cfg = json.loads(path.read_text(encoding="utf-8"))
  cfg["claim_owner"] = name
  path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _line(text: str, item_id: str) -> str:
  return next(line for line in text.splitlines() if item_id in line.split())


class ClaimTests(unittest.TestCase):
  def repo(self, git: bool = False) -> support.TempRepo:
    repo = support.TempRepo(git=git)
    repo.run("init")
    repo.run("add", "a thing")
    repo.run("add", "another thing")
    return repo

  def test_Start_RecordsOwnerAndTime_AndListJsonCarriesThem(self) -> None:
    with self.repo() as repo:
      _owner(repo, "Ada")
      code, out, err = repo.run("start", "S01", "--json")
      self.assertEqual(code, 0, err)
      payload = json.loads(out)
      self.assertEqual(payload["claim"]["owner"], "Ada")
      self.assertRegex(payload["claim"]["at"], WHEN)
      at = payload["claim"]["at"]
      code, out, err = repo.run("list")
      self.assertEqual(code, 0, err)
      self.assertIn("Ada", _line(out, "S01"))
      self.assertNotIn("Ada", _line(out, "S02"))
      code, out, err = repo.run("list", "--json")
      self.assertEqual(code, 0, err)
      rows = {item["id"]: item for item in json.loads(out)}
      self.assertEqual(rows["S01"]["claim"], {"owner": "Ada", "at": at})
      self.assertIsNone(rows["S02"]["claim"])
      self.assertEqual(repo.state().index.require("S01").claim_at, at)

  def test_List_MarksStartedAndClaimedRowsApartFromOpen(self) -> None:
    with self.repo() as repo:
      _owner(repo, "Ada")
      repo.run("start", "S01")
      code, out, err = repo.run("list")
      self.assertEqual(code, 0, err)
      self.assertIn("CLAIM", out.splitlines()[0])
      self.assertIn("Ada", _line(out, "S01"))
      self.assertIn("started", _line(out, "S01"))
      open_row = _line(out, "S02")
      self.assertNotIn("Ada", open_row)
      self.assertNotIn("*", open_row)
      repo.run("release", "S01")
      code, out, err = repo.run("list")
      self.assertEqual(code, 0, err)
      released = _line(out, "S01")
      self.assertNotIn("Ada", released)
      self.assertIn("*", released)
      self.assertIn("started", released)

  def test_ReleaseAndDone_ClearTheClaim(self) -> None:
    with self.repo() as repo:
      _owner(repo, "Ada")
      repo.run("start", "S01")
      repo.run("start", "S02")
      code, out, err = repo.run("release", "S01", "--json")
      self.assertEqual(code, 0, err)
      self.assertIsNone(json.loads(out)["claim"])
      self.assertEqual(repo.state().index.require("S01").status, "started")
      code, out, err = repo.run("done", "S02", "--json")
      self.assertEqual(code, 0, err)
      self.assertIsNone(json.loads(out)["claim"])
      code, out, err = repo.run("list", "--all")
      self.assertEqual(code, 0, err)
      self.assertNotIn("Ada", out)
      code, out, err = repo.run("list", "--all", "--json")
      self.assertEqual(code, 0, err)
      for item in json.loads(out):
        self.assertIsNone(item["claim"])

  def test_Start_Again_KeepsTheOriginalClaimTime(self) -> None:
    with self.repo() as repo:
      _owner(repo, "Ada")
      repo.run("start", "S01")
      at = repo.state().index.require("S01").claim_at
      before = len(repo.state().history())
      code, _, err = repo.run("start", "S01")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.state().index.require("S01").claim_at, at)
      self.assertEqual(len(repo.state().history()), before)

  def test_Start_UsesGitUserName_WhenConfigIsEmpty(self) -> None:
    with self.repo(git=True) as repo:
      repo.commit("base")
      code, out, err = repo.run("start", "S01", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out)["claim"]["owner"], "Test")

  def test_Identity_FallsBackToTheWorktreeName(self) -> None:
    with self.repo(git=True) as repo:
      repo.commit("base")
      with patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}):
        repo._git("config", "--unset-all", "user.name")
        self.assertEqual(vcs.identity(repo.root, ""), repo.root.name)

  def test_Git_ConfigWrite_IsRefusedByTheAllowlist(self) -> None:
    with self.repo(git=True) as repo:
      read = vcs._run(repo.root, "config", "--get", "user.name")
      self.assertEqual(read.returncode, 0, read.stderr)
      with self.assertRaises(StateError):
        vcs._run(repo.root, "config", "user.name", "elsewhere")

  def test_Start_StrictRenderFailure_DoesNotClaim(self) -> None:
    with self.repo() as repo:
      _owner(repo, "Ada")
      with patch("slicer.cli.render.plan", side_effect=RenderError("boom")):
        code, out, err = repo.run("start", "S01", "--render", "--strict")
      self.assertEqual(code, 2, err)
      self.assertEqual(out, "")
      item = repo.state().index.require("S01")
      self.assertEqual(item.status, "open")
      self.assertEqual(item.claim_owner, "")

  def test_NextStart_ClaimsTheItem(self) -> None:
    with self.repo() as repo:
      _owner(repo, "Ada")
      code, out, err = repo.run("next", "--start", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out)["claim"]["owner"], "Ada")
