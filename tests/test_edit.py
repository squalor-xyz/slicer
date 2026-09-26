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

  def test_Append_ExplicitSources_JoinsAndLogsWithCurrentRender(self) -> None:
    command = self.commands[0]
    for source in ("text", "file", "stdin"):
      with self.subTest(source=source), self.repo() as repo:
        repo.run(*command, "--text", "existing\n\n\n")
        incoming = "\n\n café\nsecond \n\n"
        path = repo.write("append.md", incoming)
        args = {"text": ("--text", incoming), "file": ("--file", str(path)),
                "stdin": ("--stdin",)}[source]
        before = repo.read(".slicer/log.jsonl").splitlines()
        with patch("slicer.cli.sys.stdin", io.StringIO(incoming)), \
             patch("slicer.cli._via_editor") as editor:
          code, out, err = repo.run(*command, "--append", *args, "--render", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), {"id": "S01", "section": "Why"})
        editor.assert_not_called()
        suffix = "\n\n" if source == "text" else ""
        self.assertEqual(self.body(repo, command), "existing\n\n café\nsecond " + suffix)
        after = repo.read(".slicer/log.jsonl").splitlines()
        self.assertEqual(len(after), len(before) + 1)
        self.assertEqual(json.loads(after[-1])["action"], "edit")
        self.assertEqual(repo.run("check")[0], 0)

  def test_Append_EmptyOrMissingSection_FillsWithoutSeparator(self) -> None:
    for heading, previous in (("Why", ""), ("Why", "\n\n"), ("New section", None)):
      with self.subTest(heading=heading, previous=previous), self.repo() as repo:
        command = ("edit", "S01", "--section", heading)
        if previous is not None:
          repo.run(*command, "--text", previous)
        code, _, err = repo.run(*command, "--append", "--text", "\nnew\n")
        self.assertEqual(code, 0, err)
        self.assertEqual(repo.state().slices["S01"].section(heading).body, "new\n")

  def test_Append_EmptyInput_DoesNotWriteOrLog(self) -> None:
    for heading, source, incoming in itertools.product(
      ("Why", "New section"), ("text", "file", "stdin"), ("", "\n\n")
    ):
      with self.subTest(heading=heading, source=source, incoming=incoming), self.repo() as repo:
        repo.run(*self.commands[0], "--text", "existing\n\n")
        path = repo.write("empty.md", incoming)
        args = {"text": ("--text", incoming), "file": ("--file", str(path)),
                "stdin": ("--stdin",)}[source]
        before = self.snapshot(repo)
        with patch("slicer.cli.sys.stdin", io.StringIO(incoming)):
          code, _, err = repo.run("edit", "S01", "--section", heading, "--append", *args)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.snapshot(repo), before)

  def test_Append_Spaces_PreservesContent(self) -> None:
    with self.repo() as repo:
      repo.run(*self.commands[0], "--text", "old \n")
      code, _, err = repo.run(*self.commands[0], "--append", "--text", " \t ")
      self.assertEqual(code, 0, err)
      self.assertEqual(self.body(repo, self.commands[0]), "old \n\n \t ")

  def test_Append_MissingOrConflictingSources_UsageWithoutChanges(self) -> None:
    sources = (("--text", ""), ("--file", "missing.md"), ("--stdin",))
    combinations = [()] + list(itertools.combinations(sources, 2)) + [sources]
    for combination in combinations:
      with self.subTest(sources=combination), self.repo() as repo:
        before = self.snapshot(repo)
        args = tuple(itertools.chain.from_iterable(combination))
        with patch("slicer.cli._via_editor") as editor, \
             patch("slicer.cli._read_user_file") as read_file, \
             patch("slicer.cli.sys.stdin") as stdin:
          code, out, _ = repo.run(*self.commands[0], "--append", *args, "--render", "--json")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"]["code"], "usage")
        self.assertEqual(self.snapshot(repo), before)
        editor.assert_not_called()
        read_file.assert_not_called()
        stdin.read.assert_not_called()
