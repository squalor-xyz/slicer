"""`slicer recommended-workflow`: the generic workflow, printed from the docs file."""

from __future__ import annotations

import io
import json
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import support

from slicer import workflow
from slicer.cli import main

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "generic-recommended-project-workflow.md"


def _set_config(repo: support.TempRepo, **changes: object) -> None:
  path = repo.root / ".slicer/config.json"
  cfg = json.loads(path.read_text())
  cfg.update(changes)
  path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _listing(repo: support.TempRepo) -> dict[str, bytes]:
  return {
    str(p.relative_to(repo.root)): p.read_bytes()
    for p in sorted(repo.root.rglob("*")) if p.is_file()
  }


class RecommendedWorkflowTests(unittest.TestCase):
  def test_RecommendedWorkflow_PrintsTheDocsFile(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      before = _listing(repo)
      code, out, err = repo.run("recommended-workflow")
      self.assertEqual(code, 0)
      self.assertEqual(err, "")
      self.assertEqual(out, DOC.read_text(encoding="utf-8"))
      self.assertEqual(_listing(repo), before)

  def test_RecommendedWorkflow_JsonMatchesTheText(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      code, out, err = repo.run("recommended-workflow", "--json")
      self.assertEqual((code, err), (0, ""))
      payload = json.loads(out)
      self.assertEqual(payload, {"workflow": DOC.read_text(encoding="utf-8")})
      _, text, _ = repo.run("recommended-workflow")
      self.assertEqual(payload["workflow"].rstrip("\n"), text.rstrip("\n"))

  def test_RecommendedWorkflow_IgnoresTheProject(self) -> None:
    with support.TempRepo() as bare, support.isolated_discovery(bare.root):
      _, expected, _ = bare.run("recommended-workflow")
    for finish in ("done", "handoff"):
      with self.subTest(implement_finish=finish), support.TempRepo() as repo:
        repo.run("init")
        _set_config(repo, implement_finish=finish)
        before = _listing(repo)
        code, out, _ = repo.run("recommended-workflow")
        self.assertEqual(code, 0)
        self.assertEqual(out, expected)
        self.assertEqual(_listing(repo), before)

  def test_RecommendedWorkflow_StatesTheGenericGuidelines(self) -> None:
    text = DOC.read_text(encoding="utf-8")
    for needle in (
      "slicer sections",
      "slicer next --ready",
      "slicer start ID --render --strict",
      "slicer check",
      "implement_finish",
      "slicer handoff ID --render",
      "slicer done ID",
      "step 4 of `slicer ai instructions`",
      "updates this file in the same change",
    ):
      self.assertIn(needle, text)
    for needle in ("tests/affected.py", ".worktrees/", "python3 -m unittest discover"):
      self.assertNotIn(needle, text)

  def test_RecommendedWorkflow_MissingFile_IsIo(self) -> None:
    missing = ROOT / "no-such-dir" / "workflow.md"
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      before = _listing(repo)
      with patch.object(workflow, "WORKFLOW_PATH", missing):
        code, out, _ = repo.run("recommended-workflow", "--json")
      self.assertEqual(code, 3)
      error = json.loads(out)["error"]
      self.assertEqual(error["code"], "io")
      self.assertIn(str(missing), error["message"])
      self.assertEqual(_listing(repo), before)

  def test_Help_ListsRecommendedWorkflow(self) -> None:
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
      main(["--help"])
    self.assertIn("recommended-workflow", out.getvalue())

  def test_Packaging_WorkflowFile_IsDeclaredAndIsTheDocsText(self) -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = pyproject["tool"]["setuptools"]["package-data"]["slicer"]
    self.assertIn(workflow.WORKFLOW_PATH.name, package_data)
    self.assertEqual(workflow.text(), DOC.read_text(encoding="utf-8"))


if __name__ == "__main__":
  unittest.main()
