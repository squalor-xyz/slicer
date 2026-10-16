"""`slicer feedback` keeps a gitignored local use-log apart from the roadmap (s230)."""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import support
from slicer import cli, feedback

LOG = ".slicer/feedback.md"
STAMP = "2026-10-06T18:07:07Z"


OLD_GITIGNORE = "feedback.md\n"  # a project made before `init` listed the sidecar, ledger and lock


def linked_project(case: unittest.TestCase) -> tuple[support.TempRepo, Path]:
  """A git project whose `.slicer/.gitignore` is old, committed, and a linked worktree of it.

  Both are inside one throwaway repo: the exclude file a test reads is that repo's own.
  """
  repo = support.TempRepo(git=True)
  case.addCleanup(repo.close)
  case.enterContext(support.isolated_discovery(repo.root))
  case.assertEqual(repo.run("init")[0], 0)
  repo.write(".slicer/.gitignore", OLD_GITIGNORE)
  (repo.root / ".slicer" / "lock").unlink(missing_ok=True)  # `commit` stages everything
  repo.commit("base")
  wt = repo.root / "siblings" / "wt"
  wt.parent.mkdir()
  made = repo._git("worktree", "add", "-q", "-b", "wt", str(wt), "HEAD")
  case.assertEqual(made.returncode, 0, made.stderr)
  return repo, wt


def run_at(root: Path, *argv: str) -> tuple[int, str, str]:
  """Run the CLI against `root`, which may be a linked worktree."""
  out, err = io.StringIO(), io.StringIO()
  with redirect_stdout(out), redirect_stderr(err):
    code = cli.main(["--root", str(root), *argv])
  return code, out.getvalue(), err.getvalue()


def exclude_lines(repo: support.TempRepo) -> list[str]:
  path = repo.root / ".git" / "info" / "exclude"
  return path.read_text(encoding="utf-8").splitlines() if path.is_file() else []


class FeedbackBase(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    self.addCleanup(repo.close)
    self.assertEqual(repo.run("init")[0], 0)
    return repo

  def add(self, repo: support.TempRepo, *argv: str) -> tuple[int, str, str]:
    with patch("slicer.feedback._now", return_value=STAMP):
      return repo.run("feedback", *argv)

  def tracked(self, repo: support.TempRepo) -> dict[str, bytes]:
    base = repo.root / ".slicer"
    return {
      str(p.relative_to(base)): p.read_bytes()
      for p in base.rglob("*")
      if p.is_file() and p.name not in (feedback.LOG_NAME, "lock", feedback.GITIGNORE_NAME)
    }


class FeedbackTests(FeedbackBase):
  def test_Append_EachKindStoresOneEntryAndKeepsEarlierBytes(self) -> None:
    repo = self.repo()
    previous = ""
    for kind in feedback.KINDS:
      code, out, err = self.add(repo, "--kind", kind, "--text", f"a {kind} note")
      self.assertEqual(code, 0, err)
      self.assertEqual(out.strip(), f"added feedback to {LOG}")
      now = repo.read(LOG)
      self.assertTrue(now.startswith(previous))
      self.assertEqual(now.count("<!-- slicer-feedback"), feedback.KINDS.index(kind) + 1)
      previous = now
    self.assertEqual(
      repo.read(LOG),
      f"<!-- slicer-feedback at={STAMP} kind=friction -->\na friction note\n\n"
      f"<!-- slicer-feedback at={STAMP} kind=bug -->\na bug note\n\n"
      f"<!-- slicer-feedback at={STAMP} kind=feature -->\na feature note\n",
    )

  def test_Append_JsonReturnsPathAndEntry(self) -> None:
    repo = self.repo()
    code, out, _ = self.add(repo, "--kind", "bug", "--text", "x", "--item", "s219", "--json")
    self.assertEqual(code, 0)
    self.assertEqual(json.loads(out), {
      "path": LOG,
      "entry": {"at": STAMP, "kind": "bug", "text": "x", "item": "s219"},
    })

  def test_Append_BodySourcesFileAndStdin(self) -> None:
    repo = self.repo()
    repo.write("note.txt", "from a file\n\nsecond paragraph\n")
    self.assertEqual(self.add(repo, "--kind", "bug", "--file", str(repo.root / "note.txt"))[0], 0)
    with patch("sys.stdin", io.StringIO("from stdin\n")):
      self.assertEqual(self.add(repo, "--kind", "bug", "--stdin")[0], 0)
    texts = [e["text"] for e in feedback.read(repo.root)]
    self.assertEqual(texts, ["from a file\n\nsecond paragraph", "from stdin"])

  def test_Append_RejectsBadInputWithoutWriting(self) -> None:
    repo = self.repo()
    cases = [
      ("--kind", "nope", "--text", "x"),
      ("--kind", "bug", "--text", ""),
      ("--kind", "bug", "--text", " \n"),
      ("--kind", "bug"),
      ("--kind", "bug", "--text", "x", "--stdin"),
      ("--text", "x"),
      ("--item", "s1"),
      ("--force",),
      ("--out", "dest.md", "--kind", "bug", "--text", "x"),
      ("--out", "dest.md", "--item", "s1"),
    ]
    for argv in cases:
      with self.subTest(argv=argv):
        code, _, err = self.add(repo, *argv)
        self.assertEqual(code, 2, err)
        self.assertFalse((repo.root / LOG).exists())
        self.assertFalse((repo.root / "dest.md").exists())

  def test_Append_RejectsBadInputLeavingAnExistingLogUnchanged(self) -> None:
    repo = self.repo()
    self.add(repo, "--kind", "bug", "--text", "keep")
    before = repo.read(LOG)
    self.assertEqual(self.add(repo, "--kind", "other", "--text", "x")[0], 2)
    self.assertEqual(repo.read(LOG), before)

  def test_Item_IsStoredVerbatimAndNotResolved(self) -> None:
    repo = self.repo()
    self.add(repo, "--kind", "bug", "--text", "a", "--item", "no-such-item")
    self.add(repo, "--kind", "bug", "--text", "b")
    self.add(repo, "--kind", "bug", "--text", "c", "--item", "")
    first, second, third = feedback.read(repo.root)
    self.assertEqual(first["item"], "no-such-item")
    self.assertNotIn("item", second)
    self.assertNotIn("item", third)
    self.assertNotIn("item=", repo.read(LOG).split("\n\n", 1)[1])

  def test_Print_MissingLogReportsNoEntries(self) -> None:
    repo = self.repo()
    code, out, _ = repo.run("feedback")
    self.assertEqual(code, 0)
    self.assertIn("no feedback entries", out)
    code, out, _ = repo.run("feedback", "--json")
    self.assertEqual(code, 0)
    self.assertEqual(json.loads(out), {"path": LOG, "entries": []})
    self.assertFalse((repo.root / LOG).exists())

  def test_Print_ShowsTheLogAndJsonEntries(self) -> None:
    repo = self.repo()
    self.add(repo, "--kind", "friction", "--text", "para one\n\npara two", "--item", "s219")
    self.add(repo, "--kind", "bug", "--text", "other")
    code, out, _ = repo.run("feedback")
    self.assertEqual(code, 0)
    self.assertEqual(out.rstrip("\n"), repo.read(LOG).rstrip("\n"))
    code, out, _ = repo.run("feedback", "--json")
    self.assertEqual(json.loads(out)["entries"], [
      {"at": STAMP, "kind": "friction", "item": "s219", "text": "para one\n\npara two"},
      {"at": STAMP, "kind": "bug", "text": "other"},
    ])


class FeedbackExportTests(FeedbackBase):
  def test_Out_WritesExactBytesInsideAndOutsideTheRepo(self) -> None:
    repo = self.repo()
    self.add(repo, "--kind", "bug", "--text", "one")
    self.add(repo, "--kind", "feature", "--text", "two")
    with tempfile.TemporaryDirectory() as outside:
      for dest in (repo.root / "exports" / "deep" / "log.md", Path(outside) / "a" / "log.md"):
        with self.subTest(dest=str(dest)):
          code, _, err = repo.run("feedback", "--out", str(dest))
          self.assertEqual(code, 0, err)
          self.assertEqual(dest.read_bytes(), (repo.root / LOG).read_bytes())

  def test_Out_ExitsTwoWhenThereIsNoLog(self) -> None:
    repo = self.repo()
    code, _, _ = repo.run("feedback", "--out", str(repo.root / "exports" / "log.md"))
    self.assertEqual(code, 2)
    self.assertFalse((repo.root / "exports").exists())

  def test_Out_RefusesTheLogItself(self) -> None:
    repo = self.repo()
    self.add(repo, "--kind", "bug", "--text", "one")
    before = repo.read(LOG)
    for dest in (repo.root / LOG, repo.root / "." / ".slicer" / "feedback.md"):
      code, _, err = repo.run("feedback", "--out", str(dest), "--force")
      self.assertEqual(code, 2, err)
    self.assertEqual(repo.read(LOG), before)

  def test_Out_DifferentExistingDestinationNeedsForce(self) -> None:
    repo = self.repo()
    self.add(repo, "--kind", "bug", "--text", "one")
    dest = repo.write("dest.md", "other")
    code, _, err = repo.run("feedback", "--out", str(dest))
    self.assertEqual(code, 2)
    self.assertIn("--force", err)
    self.assertEqual(dest.read_text(), "other")
    self.assertEqual(repo.run("feedback", "--out", str(dest), "--force")[0], 0)
    self.assertEqual(dest.read_bytes(), (repo.root / LOG).read_bytes())

  def test_Out_IdenticalDestinationIsRewritten(self) -> None:
    repo = self.repo()
    self.add(repo, "--kind", "bug", "--text", "one")
    dest = repo.root / "copy.md"
    self.assertEqual(repo.run("feedback", "--out", str(dest))[0], 0)
    self.assertEqual(repo.run("feedback", "--out", str(dest))[0], 0)
    self.assertEqual(dest.read_bytes(), (repo.root / LOG).read_bytes())


class FeedbackGitignoreTests(FeedbackBase):
  def lines(self, repo: support.TempRepo) -> list[str]:
    return repo.read(".slicer/.gitignore").splitlines()

  def test_Init_WritesTheGitignoreAndAppendIsIdempotent(self) -> None:
    repo = self.repo()
    self.assertEqual(self.lines(repo), ["feedback.md", "feedback-reported.json", "relay-runs.jsonl", "lock"])
    self.add(repo, "--kind", "bug", "--text", "x")
    self.add(repo, "--kind", "bug", "--text", "y")
    self.assertEqual(self.lines(repo), ["feedback.md", "feedback-reported.json", "relay-runs.jsonl", "lock"])

  def test_FirstAppendAddsTheLineToAnExistingGitignore(self) -> None:
    repo = self.repo()
    repo.write(".slicer/.gitignore", "scratch")
    self.add(repo, "--kind", "bug", "--text", "x")
    self.assertEqual(self.lines(repo), ["scratch", "lock", "feedback.md"])

  def test_Init_AddsTheLineAndNeverDeletesTheLog(self) -> None:
    repo = self.repo()
    self.add(repo, "--kind", "bug", "--text", "keep me")
    before = repo.read(LOG)
    repo.write(".slicer/.gitignore", "other\n")
    self.assertEqual(repo.run("init", "--force")[0], 0)
    self.assertEqual(
      self.lines(repo), ["other", "feedback.md", "feedback-reported.json", "relay-runs.jsonl", "lock"],
    )
    self.assertEqual(repo.read(LOG), before)

  def test_Append_RunsNoGitCommand(self) -> None:
    repo = self.repo()
    with patch.object(subprocess, "run", side_effect=AssertionError("git was run")):
      self.assertEqual(self.add(repo, "--kind", "bug", "--text", "x")[0], 0)

  def test_RootGitignore_ListsTheLog(self) -> None:
    root = Path(__file__).resolve().parents[1]
    self.assertIn("/.slicer/feedback.md", (root / ".gitignore").read_text().splitlines())


class LinkedWorktreeGitignoreTests(unittest.TestCase):
  def test_EnsureGitignore_LinkedWorktree_WritesInfoExclude(self) -> None:
    repo, wt = linked_project(self)
    code, _, err = run_at(wt, "feedback", "--kind", "bug", "--text", "x")
    self.assertEqual(code, 0, err)
    self.assertEqual((wt / ".slicer" / ".gitignore").read_text(encoding="utf-8"), OLD_GITIGNORE)
    self.assertEqual(
      subprocess.run(["git", "status", "--short"], cwd=wt, capture_output=True, text=True).stdout, "",
    )
    self.assertIn("**/.slicer/lock", exclude_lines(repo))
    run_at(wt, "feedback", "--kind", "bug", "--text", "y")
    self.assertEqual(exclude_lines(repo).count("**/.slicer/lock"), 1)

  def test_EnsureGitignore_LinkedWorktree_NamesTheLineItWasAskedFor(self) -> None:
    repo, wt = linked_project(self)
    feedback.ensure_gitignore(wt, feedback.SIDECAR_NAME)
    self.assertEqual((wt / ".slicer" / ".gitignore").read_text(encoding="utf-8"), OLD_GITIGNORE)
    self.assertIn("**/.slicer/feedback-reported.json", exclude_lines(repo))

  def test_EnsureGitignore_MainWorktree_EditsSlicerGitignore(self) -> None:
    repo, _ = linked_project(self)  # a linked worktree exists; the main checkout is not one
    feedback.ensure_gitignore(repo.root, "lock")
    self.assertEqual(repo.read(".slicer/.gitignore").splitlines(), ["feedback.md", "lock"])
    self.assertNotIn("**/.slicer/lock", exclude_lines(repo))

  def test_EnsureGitignore_ProjectBelowLinkedWorktreeRoot_EditsSlicerGitignore(self) -> None:
    repo, wt = linked_project(self)
    nested = wt / "sub"
    nested.mkdir()
    self.assertEqual(run_at(nested, "init")[0], 0)
    (nested / ".slicer" / ".gitignore").write_text(OLD_GITIGNORE, encoding="utf-8")
    feedback.ensure_gitignore(nested, "lock")
    self.assertEqual(
      (nested / ".slicer" / ".gitignore").read_text(encoding="utf-8").splitlines(),
      ["feedback.md", "lock"],
    )
    self.assertNotIn("**/.slicer/lock", exclude_lines(repo))

  def test_EnsureGitignore_LinkedWorktreeExcludeAlreadyHasLine_LeavesBothFiles(self) -> None:
    repo, wt = linked_project(self)
    exclude = repo.root / ".git" / "info" / "exclude"
    exclude.parent.mkdir(exist_ok=True)
    exclude.write_text("**/.slicer/lock\n", encoding="utf-8")
    feedback.ensure_gitignore(wt, "lock")
    self.assertEqual(exclude.read_text(encoding="utf-8"), "**/.slicer/lock\n")
    self.assertEqual((wt / ".slicer" / ".gitignore").read_text(encoding="utf-8"), OLD_GITIGNORE)

  def test_EnsureGitignore_ExcludeWithoutFinalNewline_KeepsEarlierLine(self) -> None:
    repo, wt = linked_project(self)
    exclude = repo.root / ".git" / "info" / "exclude"
    exclude.parent.mkdir(exist_ok=True)
    exclude.write_text("scratch", encoding="utf-8")
    feedback.ensure_gitignore(wt, "lock")
    self.assertEqual(exclude.read_text(encoding="utf-8"), "scratch\n**/.slicer/lock\n")

  def test_EnsureGitignore_ExcludeUnwritable_WriteStillSucceeds(self) -> None:
    repo, wt = linked_project(self)
    exclude = repo.root / ".git" / "info" / "exclude"
    exclude.parent.mkdir(exist_ok=True)
    exclude.unlink(missing_ok=True)
    exclude.mkdir()  # a directory: reading and replacing it both fail
    code, _, err = run_at(wt, "add", "first")
    self.assertEqual(code, 0, err)
    self.assertEqual((wt / ".slicer" / ".gitignore").read_text(encoding="utf-8"), OLD_GITIGNORE)

  def test_FeedbackRecord_LinkedWorktreeExcludeUnwritable_StillRecords(self) -> None:
    repo, wt = linked_project(self)
    exclude = repo.root / ".git" / "info" / "exclude"
    exclude.parent.mkdir(exist_ok=True)
    exclude.unlink(missing_ok=True)
    exclude.mkdir()  # a directory: reading and replacing it both fail
    feedback.record(wt, "abc", "owner/repo", 7, "https://example.invalid/7")
    self.assertEqual(feedback.reported_hashes(wt, "owner/repo"), {"abc"})
    self.assertEqual((wt / ".slicer" / ".gitignore").read_text(encoding="utf-8"), OLD_GITIGNORE)


class FeedbackIsolationTests(FeedbackBase):
  def test_Append_LeavesRoadmapStateAndRenderAlone(self) -> None:
    repo = self.repo()
    self.assertEqual(repo.run("add", "A title", "--render")[0], 0)
    before = self.tracked(repo)
    self.assertEqual(self.add(repo, "--kind", "friction", "--text", "x", "--item", "s1")[0], 0)
    self.assertEqual(repo.run("feedback")[0], 0)
    self.assertEqual(self.tracked(repo), before)
    code, _, err = repo.run("check")
    self.assertEqual(code, 0, err)


if __name__ == "__main__":
  unittest.main()
