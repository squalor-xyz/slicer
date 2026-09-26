"""The machine-readable failure envelope, for agents driving slicer."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import support


class ErrorEnvelopeTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.run("add", "A thing")
    return repo

  def envelope(self, repo: support.TempRepo, *argv: str) -> dict:
    code, out, err = repo.run(*argv, "--json")
    self.assertEqual(code, 2, f"expected a usage failure; stderr was {err!r}")
    return json.loads(out)["error"]

  def test_Failure_JsonFlag_PutsAnEnvelopeOnStdout(self) -> None:
    with self.repo() as repo:
      error = self.envelope(repo, "show", "S99")
      self.assertEqual(error["code"], "no_such_item")
      self.assertIn("S99", error["message"])
      self.assertEqual(error["command"], "show")

  def test_Failure_JsonFlag_StillWritesTheHumanLineToStderr(self) -> None:
    with self.repo() as repo:
      _, _, err = repo.run("show", "S99", "--json")
      self.assertIn("slicer: no such item: S99", err)

  def test_Failure_WithoutJson_WritesNothingToStdout(self) -> None:
    with self.repo() as repo:
      _, out, err = repo.run("show", "S99")
      self.assertEqual(out, "")
      self.assertIn("no such item", err)

  def test_UnknownItem_EveryPath_ReportsTheSameCode(self) -> None:
    # `Index.require` and the `ops.*` guards used to raise different
    # exceptions with different wording for the identical condition.
    with self.repo() as repo:
      for argv in (("show", "S99"), ("promote", "S99"), ("done", "S99"),
                   ("start", "S99"),
                   ("set", "S99", "--size", "M"), ("edit", "S99", "--section", "Why", "--stdin")):
        with self.subTest(argv[0]):
          self.assertEqual(self.envelope(repo, *argv)["code"], "no_such_item")

  def test_UnknownStatus_CarriesTheStateCode(self) -> None:
    with self.repo() as repo:
      error = self.envelope(repo, "set", "S01", "--status", "nonsense")
      self.assertEqual(error["code"], "state")
      self.assertIn("nonsense", error["message"])

  def test_OutsideAProject_CarriesTheStateCode(self) -> None:
    with support.TempRepo() as repo:
      code, out, _ = repo.run("list", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out)["error"]["code"], "state")

  def test_MissingEditFile_IsAnEnvelopeNotATraceback(self) -> None:
    with self.repo() as repo:
      repo.run("promote", "S01")
      error = self.envelope(repo, "edit", "S01", "--section", "Why", "--file", "/nope/missing.md")
      self.assertEqual(error["code"], "io")

  def test_MalformedOutline_CarriesTheOutlineCode(self) -> None:
    with self.repo() as repo:
      repo.write("r.md", "## A thing\nnonsense: x\n")
      self.assertEqual(self.envelope(repo, "import", "r.md")["code"], "outline")

  def test_AlreadyPromoted_CarriesTheStateCode(self) -> None:
    with self.repo() as repo:
      repo.run("promote", "S01")
      self.assertEqual(self.envelope(repo, "promote", "S01")["code"], "state")

  def test_EveryEnvelope_HasTheSameKeys(self) -> None:
    with self.repo() as repo:
      for argv in (("show", "S99"), ("set", "S01", "--status", "no"), ("promote", "S99")):
        with self.subTest(argv[0]):
          self.assertEqual(sorted(self.envelope(repo, *argv)), ["code", "command", "message"])


class ParserErrorTests(unittest.TestCase):
  cases = (
    (("edit", "--json"), "edit"),
    (("list", "--unknown", "--json"), "list"),
    (("list", "--sort", "invalid", "--json"), "list"),
    (("log", "--limit", "invalid", "--json"), "log"),
    (("show", "S01", "--root", "--json"), "show"),
    (("remove", "S01", "--purge", "--reason", "x", "--json"), "remove"),
    (("remove", "S01", "--json"), "remove"),
    (("prose", "edit", "--json"), "prose"),
    (("prose", "show", "preamble", "--unknown", "--json"), "prose"),
    (("prose", "unknown", "--json"), "prose"),
    (("unknown", "--json"), None),
    (("--json",), None),
    (("--root", "--json"), None),
    (("--json", "list"), "list"),
    (("tui", "--json"), "tui"),
  )

  def run_main(self, argv: tuple[str, ...]) -> tuple[int, str, str]:
    from slicer.cli import main
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
      code = main(list(argv))
    return code, out.getvalue(), err.getvalue()

  def test_ParserFailure_Json_EnvelopeAndDiagnosticsWithoutStateAccess(self) -> None:
    for argv, command in self.cases:
      with self.subTest(argv=argv), patch("slicer.cli.store.load") as load, \
           patch("slicer.cli.store.project_lock") as lock:
        code, out, err = self.run_main(argv)
        self.assertEqual(code, 2)
        error = json.loads(out)["error"]
        self.assertEqual(set(error), {"code", "message", "command"})
        self.assertEqual(error["code"], "usage")
        self.assertEqual(error["command"], command)
        self.assertTrue(error["message"])
        self.assertIn("usage: slicer", err)
        self.assertIn(error["message"], err)
        self.assertEqual(err.count(": error:"), 1)
        load.assert_not_called()
        lock.assert_not_called()

  def test_ParserFailure_ModuleInvocation_MatchesMain(self) -> None:
    env = dict(os.environ, PYTHONPATH=str(support.SRC))
    for argv, _ in self.cases:
      with self.subTest(argv=argv):
        expected = self.run_main(argv)
        result = subprocess.run(
          [sys.executable, "-m", "slicer", *argv], env=env,
          capture_output=True, text=True, check=False,
        )
        self.assertEqual((result.returncode, result.stdout, result.stderr), expected)

  def test_ParserFailure_NoJsonOrLiteralJson_LeavesStdoutEmpty(self) -> None:
    for argv in (("edit",), ("unknown",), ("list", "--", "--json"),
                 ("list", "--unknown=--json")):
      with self.subTest(argv=argv):
        code, out, err = self.run_main(argv)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("usage: slicer", err)
        self.assertIn(": error:", err)

  def test_Parser_HelpWithOrWithoutJson_RemainsSuccessfulHumanHelp(self) -> None:
    for argv in (("--help",), ("--json", "--help"), ("edit", "--help"),
                 ("edit", "--json", "--help"), ("prose", "edit", "--json", "--help")):
      with self.subTest(argv=argv):
        result = subprocess.run(
          [sys.executable, "-m", "slicer", *argv],
          env=dict(os.environ, PYTHONPATH=str(support.SRC)),
          capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.stdout.startswith("usage: slicer"))
        self.assertEqual(result.stderr, "")

  def test_ParserFailure_MutatingCommand_LeavesTrackingUnchanged(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "Example")
      before = {str(p): p.read_bytes() for p in (repo.root / ".slicer").rglob("*") if p.is_file()}
      code, _, _ = repo.run("remove", "S01", "--purge", "--reason", "x", "--json")
      self.assertEqual(code, 2)
      after = {str(p): p.read_bytes() for p in (repo.root / ".slicer").rglob("*") if p.is_file()}
      self.assertEqual(after, before)

  def test_Parser_ValidJsonAndLiteralArgument_PreserveSuccess(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      code, out, err = repo.run("add", "--json", "--", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out)["title"], "--json")


class DriftIsNotAnErrorTests(unittest.TestCase):
  """Exit 1 means drift, and keeps its own report rather than an envelope."""

  def test_Check_StaleRender_ReportsTheCheckPayloadNotAnEnvelope(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "A thing")
      code, out, _ = repo.run("check", "--json")
      self.assertEqual(code, 1)
      payload = json.loads(out)
      self.assertNotIn("error", payload)
      self.assertFalse(payload["ok"])

  def test_Next_NothingToDo_ReportsAnEmptyResultNotAnEnvelope(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      code, out, _ = repo.run("next", "--json")
      self.assertEqual(code, 2)
      payload = json.loads(out)
      self.assertNotIn("error", payload)
      self.assertIsNone(payload["item"])


if __name__ == "__main__":
  unittest.main()
