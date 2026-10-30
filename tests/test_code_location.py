"""slicer warns when its own code and the project it acts on are different
worktrees of one repo -- the editable-install-in-a-worktree trap (S120)."""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

import support

from slicer import cli, vcs


class ForeignWorktreeTests(unittest.TestCase):
  def test_CodeUnderProject_NoWarning(self) -> None:
    with support.TempRepo(git=True) as repo:
      self.assertIsNone(vcs.foreign_worktree(repo.root, repo.root / "src"))

  def test_UnrelatedNonRepoCode_NoWarning(self) -> None:
    # Code from a location that is not in the project's repo -- e.g. a
    # site-packages install -- must not warn.
    with support.TempRepo(git=True) as repo, support.TempRepo() as other:
      self.assertIsNone(vcs.foreign_worktree(repo.root, other.root))

  def test_ProjectNestedInCodeWorktree_NoWarning(self) -> None:
    # A project elsewhere in the code's own worktree -- a subproject, or a
    # test fixture under an ignored scratch dir (S129) -- runs this worktree's
    # code, so it is not the sibling-worktree trap.
    with support.TempRepo(git=True) as repo:
      nested = repo.root / ".venv" / "test-tmp" / "fixture"
      nested.mkdir(parents=True)
      code = repo.root / "src"
      code.mkdir()
      self.assertIsNone(vcs.foreign_worktree(nested, code))

  def test_SiblingWorktreeSameRepo_Warns(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      repo.commit("base")  # `git worktree add` needs a commit
      sibling = repo.root / "sib"
      repo._git("worktree", "add", str(sibling), "-b", "sib")
      # project is the sibling worktree; code lives in the main checkout.
      message = vcs.foreign_worktree(sibling, repo.root)
      self.assertIsNotNone(message)
      self.assertIn(str(repo.root.resolve()), message)
      self.assertIn(str(sibling.resolve()), message)
      self.assertIn("PYTHONPATH=src", message)

  def test_GitDirSetForAnotherRepo_NoWarning(self) -> None:
    # Git run with GIT_DIR (or GIT_COMMON_DIR) pointing at the project's repo answers
    # for that repo from slicer's code dir too, so the two looked like worktrees of one
    # repo (S394).
    with support.TempRepo(git=True) as project, support.TempRepo(git=True) as code:
      for name in ("GIT_DIR", "GIT_COMMON_DIR"):
        with self.subTest(name=name), patch.dict(os.environ, {name: str(project.root / ".git")}):
          self.assertIsNone(vcs.foreign_worktree(project.root, code.root))

  def test_SiblingWorktreeGitEnvSet_Warns(self) -> None:
    # The same sibling-worktree trap with the merge driver's variables set to the
    # shared repo. Stripping them from the code probe must not hide a real trap.
    with support.TempRepo(git=True) as repo:
      repo.write("README.md", "base\n")
      repo.commit("base")  # `git worktree add` needs a commit
      sibling = repo.root / "sib"
      repo._git("worktree", "add", str(sibling), "-b", "sib")
      for name, value in (("GIT_DIR", repo.root / ".git"), ("GIT_WORK_TREE", sibling)):
        with self.subTest(name=name), patch.dict(os.environ, {name: str(value)}):
          message = vcs.foreign_worktree(sibling, repo.root)
          self.assertIsNotNone(message)
          self.assertIn(str(repo.root.resolve()), message)

  def test_GitDirIsLinkedWorktreeGitdir_UnrelatedCode_NoWarning(self) -> None:
    # In a linked worktree GIT_DIR names that worktree's own gitdir, not the repo's `.git`.
    # Code from an unrelated repo is still not the trap.
    with support.TempRepo(git=True) as repo, support.TempRepo(git=True) as other:
      repo.write("README.md", "base\n")
      repo.commit("base")  # `git worktree add` needs a commit
      sibling = repo.root / "sib"
      repo._git("worktree", "add", str(sibling), "-b", "sib")
      gitdir = repo.root / ".git" / "worktrees" / "sib"
      with patch.dict(os.environ, {"GIT_DIR": str(gitdir)}):
        self.assertIsNone(vcs.foreign_worktree(sibling, other.root))

  def test_GitDirIsLinkedWorktreeGitdir_SiblingTrap_Warns(self) -> None:
    # Code in the main checkout, project in its linked worktree, GIT_DIR naming that
    # worktree's gitdir: still the sibling-worktree trap.
    with support.TempRepo(git=True) as repo:
      repo.write("README.md", "base\n")
      repo.commit("base")  # `git worktree add` needs a commit
      sibling = repo.root / "sib"
      repo._git("worktree", "add", str(sibling), "-b", "sib")
      gitdir = repo.root / ".git" / "worktrees" / "sib"
      with patch.dict(os.environ, {"GIT_DIR": str(gitdir)}):
        message = vcs.foreign_worktree(sibling, repo.root)
        self.assertIsNotNone(message)
        self.assertIn(str(sibling.resolve()), message)


class CodeMismatchWarningTests(unittest.TestCase):
  def _repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    return repo

  def test_Main_Mismatch_WarnsOnStderrAndLeavesStdoutClean(self) -> None:
    with self._repo() as repo:
      with patch.object(vcs, "foreign_worktree", return_value="code is over there"):
        code, out, err = repo.run("list", "--json")
      self.assertEqual(code, 0)
      self.assertIn("slicer: code is over there", err)
      json.loads(out)  # stdout is still exactly the command's JSON payload

  def test_Main_EnvVar_SilencesTheWarning(self) -> None:
    with self._repo() as repo:
      with patch.dict(os.environ, {cli._CODE_WARNING_ENV: "1"}), \
           patch.object(vcs, "foreign_worktree", return_value="should be hidden") as ft:
        code, _, err = repo.run("list")
      self.assertEqual(code, 0)
      self.assertNotIn("should be hidden", err)
      ft.assert_not_called()  # silenced before the probe even runs

  def test_Main_NoProject_StaysSilent(self) -> None:
    # Outside any project, discovery fails and nothing is printed.
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):  # no `init`
      code, _, err = repo.run("list")
      self.assertNotIn("different worktree", err)


if __name__ == "__main__":
  unittest.main()
