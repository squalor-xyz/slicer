"""`slicer feedback` keeps a gitignored local use-log apart from the roadmap (s230)."""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import support
from slicer import feedback

LOG = ".slicer/feedback.md"
STAMP = "2026-10-06T18:07:07Z"


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
    self.assertEqual(self.lines(repo), ["feedback.md"])
    self.add(repo, "--kind", "bug", "--text", "x")
    self.add(repo, "--kind", "bug", "--text", "y")
    self.assertEqual(self.lines(repo), ["feedback.md"])

  def test_FirstAppendAddsTheLineToAnExistingGitignore(self) -> None:
    repo = self.repo()
    repo.write(".slicer/.gitignore", "scratch")
    self.add(repo, "--kind", "bug", "--text", "x")
    self.assertEqual(self.lines(repo), ["scratch", "feedback.md"])

  def test_Init_AddsTheLineAndNeverDeletesTheLog(self) -> None:
    repo = self.repo()
    self.add(repo, "--kind", "bug", "--text", "keep me")
    before = repo.read(LOG)
    repo.write(".slicer/.gitignore", "other\n")
    self.assertEqual(repo.run("init", "--force")[0], 0)
    self.assertEqual(self.lines(repo), ["other", "feedback.md"])
    self.assertEqual(repo.read(LOG), before)

  def test_Append_RunsNoGitCommand(self) -> None:
    repo = self.repo()
    with patch.object(subprocess, "run", side_effect=AssertionError("git was run")):
      self.assertEqual(self.add(repo, "--kind", "bug", "--text", "x")[0], 0)

  def test_RootGitignore_ListsTheLog(self) -> None:
    root = Path(__file__).resolve().parents[1]
    self.assertIn("/.slicer/feedback.md", (root / ".gitignore").read_text().splitlines())


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
