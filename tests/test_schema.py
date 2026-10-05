"""Refuse a newer-than-known on-disk schema, so an older slicer never silently
downgrades a project a newer one wrote."""

from __future__ import annotations

import json
import unittest
from unittest import mock

import support
from slicer import model, store
from slicer.config import CONFIG_NAME, Config
from slicer.config import SCHEMA_VERSION as CONFIG_SCHEMA_VERSION
from slicer.errors import SlicerError
from slicer.model import SCHEMA_VERSION as INDEX_SCHEMA_VERSION
from slicer.store import DIR_NAME, INDEX_NAME


def _set_version(repo: support.TempRepo, relpath: str, version: int) -> str:
  path = repo.root / relpath
  data = json.loads(path.read_text(encoding="utf-8"))
  data["version"] = version
  path.write_text(json.dumps(data, indent=2), encoding="utf-8")
  return relpath


class SchemaGuardTests(unittest.TestCase):
  def _repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    return repo

  def test_Index_NewerSchema_IsRefusedAndFileLeftUntouched(self) -> None:
    with self._repo() as repo:
      rel = _set_version(repo, f"{DIR_NAME}/{INDEX_NAME}", INDEX_SCHEMA_VERSION + 1)
      before = (repo.root / rel).read_bytes()
      with self.assertRaises(SlicerError) as cm:
        store.load(repo.root)
      self.assertEqual(cm.exception.code, "schema_too_new")
      self.assertEqual((repo.root / rel).read_bytes(), before)  # load wrote nothing

  def test_Config_NewerSchema_IsRefused(self) -> None:
    with self._repo() as repo:
      _set_version(repo, f"{DIR_NAME}/{CONFIG_NAME}", CONFIG_SCHEMA_VERSION + 1)
      with self.assertRaises(SlicerError) as cm:
        store.load(repo.root)
      self.assertEqual(cm.exception.code, "schema_too_new")

  def test_CurrentSchema_StillLoads(self) -> None:
    with self._repo() as repo:
      state = store.load(repo.root)  # written at exactly SCHEMA_VERSION
      self.assertEqual(state.index.version, INDEX_SCHEMA_VERSION)
      self.assertEqual(state.config.version, CONFIG_SCHEMA_VERSION)

  def test_ParsePrimitives_NewerSchema_RefuseForEveryReader(self) -> None:
    # The guard lives in the parse primitives, so callers that read state
    # directly (e.g. `migrate`) are refused too, not only store.load.
    with self.assertRaises(SlicerError) as cm:
      model.Index.from_dict({"version": INDEX_SCHEMA_VERSION + 1})
    self.assertEqual(cm.exception.code, "schema_too_new")

  def test_Config_NewerSchema_RefusedBeforeValidation(self) -> None:
    # A newer config that this build would also find invalid must still say
    # "upgrade slicer" (schema_too_new), not report itself as a config error.
    with support.TempRepo() as repo:
      repo.run("init")
      path = repo.root / f"{DIR_NAME}/{CONFIG_NAME}"
      path.write_text(
        json.dumps({"version": CONFIG_SCHEMA_VERSION + 1, "id": {"width": "wide"}}),
        encoding="utf-8",
      )
      with self.assertRaises(SlicerError) as cm:
        Config.load(path)
      self.assertEqual(cm.exception.code, "schema_too_new")

  def test_Index_Version2WithoutAttempts_LoadsAsZeroUntilSave(self) -> None:
    with self._repo() as repo:
      repo.run("add", "One")
      path = repo.root / DIR_NAME / INDEX_NAME
      data = json.loads(path.read_text(encoding="utf-8"))
      data["version"] = 2
      for item in data["items"]:
        item["fields"].pop("attempts", None)
      path.write_text(json.dumps(data, indent=2), encoding="utf-8")
      before = path.read_bytes()
      state = store.load(repo.root)
      self.assertEqual(state.index.version, 2)
      self.assertTrue(all(item.attempts == 0 for item in state.index.items))
      self.assertEqual(path.read_bytes(), before)
      code, _, err = repo.run("set", "S01", "--findings", "noted")
      self.assertEqual(code, 0, err)
      saved = json.loads(path.read_text(encoding="utf-8"))
      self.assertEqual(saved["version"], INDEX_SCHEMA_VERSION)
      self.assertTrue(all(item["fields"]["attempts"] == 0 for item in saved["items"]))

  def test_Index_BadAttempts_IsCorrupt(self) -> None:
    with self._repo() as repo:
      repo.run("add", "One")
      path = repo.root / DIR_NAME / INDEX_NAME
      data = json.loads(path.read_text(encoding="utf-8"))
      data["items"][0]["fields"]["attempts"] = True
      path.write_text(json.dumps(data), encoding="utf-8")
      code, out, _ = repo.run("list", "--json")
      self.assertEqual(code, 3)
      self.assertEqual(json.loads(out)["error"]["code"], "corrupt")

  def test_Cli_FutureVersion_ExitsSchemaTooNew(self) -> None:
    with self._repo() as repo:
      _set_version(repo, f"{DIR_NAME}/{INDEX_NAME}", INDEX_SCHEMA_VERSION + 1)
      code, out, _ = repo.run("list", "--json")
      self.assertEqual(code, 3)
      self.assertEqual(json.loads(out)["error"]["code"], "schema_too_new")

  def test_Cli_NewerSchema_ExitsInternalWithEnvelope(self) -> None:
    with self._repo() as repo:
      _set_version(repo, f"{DIR_NAME}/{INDEX_NAME}", INDEX_SCHEMA_VERSION + 1)
      code, out, err = repo.run("list", "--json")
      self.assertEqual(code, 3)  # INTERNAL — the project cannot proceed
      self.assertEqual(json.loads(out)["error"]["code"], "schema_too_new")
      self.assertIn("Upgrade slicer", err)


_OUTLINE = """# Roadmap

## Alpha

### Why

Because.

## Beta
"""


def _lower_index_to_v1(repo: support.TempRepo) -> None:
  """Rewrite index.json at the shape a schema-1 build wrote: version 1, no claim keys."""
  path = repo.root / DIR_NAME / INDEX_NAME
  data = json.loads(path.read_text(encoding="utf-8"))
  data["version"] = 1
  for item in data["items"]:
    item.pop("claim", None)
  path.write_text(json.dumps(data, indent=2), encoding="utf-8")


class SchemaUpgradeTests(unittest.TestCase):
  """The v1 -> v2 index upgrade: no migration step, and no way back once saved."""

  def _v1_repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.write("outline.md", _OUTLINE)
    code, _, err = repo.run("import", "outline.md", "--render")
    self.assertEqual(code, 0, err)
    _lower_index_to_v1(repo)
    return repo

  def test_Upgrade_V1Index_LoadsUnclaimedAndReadsWriteNothing(self) -> None:
    with self._v1_repo() as repo:
      path = repo.root / DIR_NAME / INDEX_NAME
      before = path.read_bytes()
      state = store.load(repo.root)
      self.assertEqual(state.index.version, 1)
      self.assertEqual([it.claim_owner for it in state.index.items], ["", ""])
      code, out, err = repo.run("list", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual([row.get("claim") for row in json.loads(out)], [None, None])
      self.assertEqual(path.read_bytes(), before)  # a read never re-stamps

  def test_Upgrade_V1Index_NextSaveStampsIndexOnly(self) -> None:
    with self._v1_repo() as repo:
      code, _, err = repo.run("start", "S01", "--render")
      self.assertEqual(code, 0, err)
      index = json.loads((repo.root / DIR_NAME / INDEX_NAME).read_text(encoding="utf-8"))
      self.assertEqual(index["version"], INDEX_SCHEMA_VERSION)
      self.assertIsNotNone(index["items"][0]["claim"])
      # config.json carries its own schema version, which this upgrade leaves alone.
      config = json.loads((repo.root / DIR_NAME / CONFIG_NAME).read_text(encoding="utf-8"))
      self.assertEqual(config["version"], CONFIG_SCHEMA_VERSION)

  def test_Upgrade_AfterSaveAndRender_CheckAndVerifyAreClean(self) -> None:
    with self._v1_repo() as repo:
      code, _, err = repo.run("start", "S01", "--render")
      self.assertEqual(code, 0, err)
      for command in ("check", "verify"):
        code, out, err = repo.run(command)
        self.assertEqual(code, 0, f"{command}: {out}{err}")

  def test_Downgrade_SchemaOneBuild_RefusesTheUpgradedProject(self) -> None:
    with self._v1_repo() as repo:
      code, _, err = repo.run("start", "S01", "--render")
      self.assertEqual(code, 0, err)
      path = repo.root / DIR_NAME / INDEX_NAME
      before = path.read_bytes()
      # Stand in for a schema-1 build: same guard, older known version.
      with mock.patch.object(model, "SCHEMA_VERSION", 1):
        with self.assertRaises(SlicerError) as cm:
          store.load(repo.root)
      self.assertEqual(cm.exception.code, "schema_too_new")
      self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
  unittest.main()
