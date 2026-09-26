"""Keep edit input sources unambiguous and preserve explicitly supplied text."""

from __future__ import annotations

import io
import itertools
import json
import unittest
from unittest.mock import patch

import support


class EditInputTests(unittest.TestCase):
  commands = (("edit", "S01", "--section", "Why"), ("prose", "edit", "preamble"))

  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.run("add", "A thing")
    repo.run("promote", "S01", "--render")
    return repo

  def body(self, repo: support.TempRepo, command: tuple[str, ...]) -> str:
    state = repo.state()
    if command[0] == "edit":
      return state.slices["S01"].section("Why").body
    return state.index.preamble

  def snapshot(self, repo: support.TempRepo) -> dict[str, bytes]:
    return {
      str(p.relative_to(repo.root)): p.read_bytes()
      for p in (repo.root / ".slicer").rglob("*")
      if p.is_file() and p.name != "lock"
    }

  def test_Edit_InlineText_PreservesExactBodyWithoutOtherInput(self) -> None:
    for command, body in itertools.product(
      self.commands, ("one line", "", " \t ", "first\nsecond", " café\n\n")
    ):
      with self.subTest(command=command, body=body), self.repo() as repo:
        path = repo.write("previous.md", "previous body")
        self.assertEqual(repo.run(*command, "--file", str(path))[0], 0)
        with patch("slicer.cli._via_editor") as editor, patch("slicer.cli.sys.stdin") as stdin:
          code, out, err = repo.run(*command, "--text", body, "--json")
        self.assertEqual(code, 0, err)
        self.assertNotIn("error", json.loads(out))
        editor.assert_not_called()
        stdin.read.assert_not_called()
        self.assertEqual(self.body(repo, command), body)

  def test_Edit_ConflictingSources_UsageEnvelopeAndNoChanges(self) -> None:
    sources = (("--text", ""), ("--file", "missing.md"), ("--stdin",))
    combinations = list(itertools.combinations(sources, 2)) + [sources]
    for command, combination in itertools.product(self.commands, combinations):
      with self.subTest(command=command, sources=combination), self.repo() as repo:
        before = self.snapshot(repo)
        args = tuple(itertools.chain.from_iterable(combination))
        with patch("slicer.cli._via_editor") as editor, \
             patch("slicer.cli._read_user_file") as read_file, \
             patch("slicer.cli.sys.stdin") as stdin:
          code, out, err = repo.run(*command, *args, "--render", "--json")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"]["code"], "usage")
        self.assertIn("choose only one", err)
        self.assertEqual(self.snapshot(repo), before)
        editor.assert_not_called()
        read_file.assert_not_called()
        stdin.read.assert_not_called()

  def test_Edit_FileAndStdin_KeepExistingNewlineHandling(self) -> None:
    for command, source in itertools.product(self.commands, ("file", "stdin")):
      with self.subTest(command=command, source=source), self.repo() as repo:
        path = repo.write("body.md", " first\nsecond \n\n")
        args = ("--file", str(path)) if source == "file" else ("--stdin",)
        with patch("slicer.cli.sys.stdin", io.StringIO(" first\nsecond \n\n")):
          code, _, err = repo.run(*command, *args)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.body(repo, command), " first\nsecond ")

  def test_Edit_NoSource_UsesEditorWithCurrentBody(self) -> None:
    for command in self.commands:
      with self.subTest(command=command), self.repo() as repo:
        repo.run(*command, "--text", "existing")
        with patch("slicer.cli._via_editor", return_value="edited") as editor:
          code, _, err = repo.run(*command)
        self.assertEqual(code, 0, err)
        editor.assert_called_once_with("existing")
        self.assertEqual(self.body(repo, command), "edited")

  def test_Edit_EditorAborted_LeavesStateUnchanged(self) -> None:
    for command in self.commands:
      with self.subTest(command=command), self.repo() as repo:
        before = self.snapshot(repo)
        with patch("slicer.cli._via_editor", return_value=None):
          code, out, _ = repo.run(*command, "--json")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"]["code"], "editor_aborted")
        self.assertEqual(self.snapshot(repo), before)

  def test_Edit_InlineTextWithRender_KeepsCheckCurrent(self) -> None:
    for command in self.commands:
      with self.subTest(command=command), self.repo() as repo:
        code, _, err = repo.run(*command, "--text", "Inline replacement", "--render")
        self.assertEqual(code, 0, err)
        self.assertEqual(repo.run("check")[0], 0)
        target = "slices/S01.md" if command[0] == "edit" else "ROADMAP.md"
        self.assertIn("Inline replacement", repo.read(".slicer/render/" + target))

  def test_ProseEdit_IdenticalInlineText_DoesNotWriteOrLog(self) -> None:
    with self.repo() as repo:
      repo.run("prose", "edit", "preamble", "--text", "same\n")
      before = self.snapshot(repo)
      code, out, err = repo.run("prose", "edit", "preamble", "--text", "same\n", "--json")
      self.assertEqual(code, 0, err)
      self.assertFalse(json.loads(out)["changed"])
      self.assertEqual(self.snapshot(repo), before)
