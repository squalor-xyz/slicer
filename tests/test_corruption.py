"""A corrupt or mis-encoded file must report, not crash with a traceback."""

from __future__ import annotations

import json
import unittest

import support


def state_file(repo: support.TempRepo, rel: str, content) -> None:
  path = repo.root / ".slicer" / rel
  if isinstance(content, bytes):
    path.write_bytes(content)
  else:
    path.write_text(content, encoding="utf-8")


class CorruptStateTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.run("add", "A thing")
    repo.run("promote", "S01")
    return repo

  def envelope(self, repo: support.TempRepo, *argv: str) -> dict:
    code, out, err = repo.run(*argv, "--json")
    self.assertEqual(code, 2, f"expected clean failure; stderr was {err!r}")
    return json.loads(out)["error"]

  def test_CorruptIndex_IsReportedNotCrashed(self) -> None:
    with self.repo() as repo:
      state_file(repo, "index.json", "garbage{")
      error = self.envelope(repo, "list")
      self.assertEqual(error["code"], "corrupt")
      self.assertIn("index.json", error["message"])

  def test_CorruptSlice_IsReported(self) -> None:
    with self.repo() as repo:
      state_file(repo, "slices/S01.json", "not json")
      error = self.envelope(repo, "list")
      self.assertEqual(error["code"], "corrupt")
      self.assertIn("S01.json", error["message"])

  def test_CorruptLog_IsReportedNamingTheLine(self) -> None:
    with self.repo() as repo:
      state_file(repo, "log.jsonl", '{"ok": 1}\ngarbage{\n')
      error = self.envelope(repo, "log")
      self.assertEqual(error["code"], "corrupt")
      self.assertIn("log.jsonl:2", error["message"])

  def test_CorruptConfig_IsReported(self) -> None:
    with self.repo() as repo:
      state_file(repo, "config.json", "garbage{")
      error = self.envelope(repo, "list")
      self.assertEqual(error["code"], "config")

  def test_InvalidUtf8Index_IsReported(self) -> None:
    with self.repo() as repo:
      state_file(repo, "index.json", b"\xff\xfe not utf-8")
      self.assertEqual(self.envelope(repo, "list")["code"], "corrupt")

  def test_InvalidUtf8Config_IsReported(self) -> None:
    with self.repo() as repo:
      state_file(repo, "config.json", b"\xff\xfe")
      self.assertEqual(self.envelope(repo, "list")["code"], "config")

  def test_SliceMissingARequiredKey_IsReportedNamingTheFile(self) -> None:
    with self.repo() as repo:
      # valid JSON, wrong shape: a section with no heading
      state_file(repo, "slices/S01.json", json.dumps({"id": "S01", "sections": [{"body": "x"}]}))
      error = self.envelope(repo, "list")
      self.assertEqual(error["code"], "corrupt")
      self.assertIn("S01.json", error["message"])

  def test_MergeConflictMarkerInIndex_IsReported(self) -> None:
    # The headline case: a committed .slicer/ conflicted on a merge.
    with self.repo() as repo:
      good = (repo.root / ".slicer/index.json").read_text()
      state_file(repo, "index.json", f"<<<<<<< HEAD\n{good}=======\n{good}>>>>>>> branch\n")
      error = self.envelope(repo, "list")
      self.assertEqual(error["code"], "corrupt")

  def test_CorruptState_WithoutJson_WritesNothingToStdout(self) -> None:
    with self.repo() as repo:
      state_file(repo, "index.json", "garbage{")
      code, out, err = repo.run("list")
      self.assertEqual(code, 2)
      self.assertEqual(out, "")
      self.assertIn("index.json", err)


class BadUserFileTests(unittest.TestCase):
  """A file the user hands us is io, not corrupt state."""

  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    return repo

  def test_ImportBadEncoding_IsAnIoError(self) -> None:
    with self.repo() as repo:
      (repo.root / "r.md").write_bytes(b"## \xff bad\n")
      code, out, _ = repo.run("import", "r.md", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out)["error"]["code"], "io")

  def test_EditBadEncodingFile_IsAnIoError(self) -> None:
    with self.repo() as repo:
      repo.run("add", "A thing")
      repo.run("promote", "S01")
      (repo.root / "body.md").write_bytes(b"\xff\xfe")
      code, out, _ = repo.run("edit", "S01", "--section", "Why", "--file", "body.md", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out)["error"]["code"], "io")


if __name__ == "__main__":
  unittest.main()
