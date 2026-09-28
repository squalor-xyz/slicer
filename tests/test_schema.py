"""Refuse a newer-than-known on-disk schema, so an older slicer never silently
downgrades a project a newer one wrote."""

from __future__ import annotations

import json
import unittest

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

  def test_Cli_NewerSchema_ExitsInternalWithEnvelope(self) -> None:
    with self._repo() as repo:
      _set_version(repo, f"{DIR_NAME}/{INDEX_NAME}", INDEX_SCHEMA_VERSION + 1)
      code, out, err = repo.run("list", "--json")
      self.assertEqual(code, 3)  # INTERNAL — the project cannot proceed
      self.assertEqual(json.loads(out)["error"]["code"], "schema_too_new")
      self.assertIn("Upgrade slicer", err)


if __name__ == "__main__":
  unittest.main()
