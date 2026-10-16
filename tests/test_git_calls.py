"""How many git processes a command starts, so the count cannot grow unnoticed.

Reads are 70-80 ms and `check` 170 ms because each git call is a subprocess. The
probes that never change within a command (is this a repo, which worktree is this)
are answered once, and `verify --landed` does not start one git call per ref tip.
"""

from __future__ import annotations

import json
import os
import unittest
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

import support

from slicer import vcs

IS_REPO = ("rev-parse", "--is-inside-work-tree")
SHOW_TOPLEVEL = ("rev-parse", "--show-toplevel")


def _kinds(calls: list[tuple[str, ...]]) -> Counter[str]:
  return Counter(" ".join(call) for call in calls)


@contextmanager
def project() -> Iterator[support.TempRepo]:
  """A committed git project on `main` with one open item."""
  repo = support.TempRepo(git=True)
  try:
    repo._git("checkout", "-q", "-b", "main")
    repo.run("init")
    repo.run("add", "one")
    repo.run("render")
    repo.commit("base")
    yield repo
  finally:
    repo.close()


class GitCallTests(unittest.TestCase):
  def test_Check_InRepoWithSibling_GitCallsBounded(self) -> None:
    with project() as repo:
      sibling = repo.root.parent / f"{repo.root.name}-sibling"
      try:
        repo._git("worktree", "add", "-q", "-b", "sibling", str(sibling))
        with support.count_git_calls() as calls:
          code, _, err = repo.run("check")
        self.assertEqual(code, 0, err)
        # 8 before the probes were memoised: is-inside-work-tree and show-toplevel ran
        # twice each. What is left is one of each, two git-common-dir (the code-location
        # hint), one `rev-parse --verify --quiet HEAD^{commit}` and the worktree list.
        self.assertLessEqual(len(calls), 6, _kinds(calls))
        self.assertEqual(calls.count(IS_REPO), 1)
        self.assertEqual(calls.count(SHOW_TOPLEVEL), 1)
      finally:
        support.remove_temp_repo(sibling)

  def test_Note_InRepo_IsRepoProbedOnce(self) -> None:
    with project() as repo:
      with support.count_git_calls() as calls:
        code, _, err = repo.run("note", "S01", "--text", "hello")
      self.assertEqual(code, 0, err)
      self.assertEqual(calls.count(IS_REPO), 1)

  def test_VerifyLanded_ManyTips_CallsDoNotScalePerTip(self) -> None:
    with project() as repo:
      for number in range(20):
        repo._git("checkout", "-q", "-b", f"branch{number}", "main")
        repo.write(f"file{number}.txt", "x\n")
        repo.commit(f"branch {number}")
      repo._git("checkout", "-q", "main")
      repo.write("main.txt", "x\n")
      repo.commit("main moves on")
      with support.count_git_calls() as calls:
        code, _, err = repo.run("verify", "--landed", "S01", "--json")
      self.assertEqual(code, 1, err)
      kinds = Counter(call[0] for call in calls)
      # 21 merge-base calls before: one per tip, and one for the checked-out commit.
      self.assertEqual(kinds["merge-base"], 0, _kinds(calls))
      # Two `log` listings name the tips outside the history. The one `show` per such
      # tip that remains (the allowlist reads one blob per call) is the documented
      # bound: 20 tips plus the index at the checked-out commit.
      self.assertEqual(kinds["log"], 2, _kinds(calls))
      self.assertLessEqual(kinds["show"], 21, _kinds(calls))


class DecorationConfigTests(unittest.TestCase):
  def test_VerifyLanded_ExcludeDecorationConfigured_TagOrRemoteTipStillOtherBranch(self) -> None:
    config = {
      "GIT_CONFIG_COUNT": "2",
      "GIT_CONFIG_KEY_0": "log.excludeDecoration", "GIT_CONFIG_VALUE_0": "refs/tags/*",
      "GIT_CONFIG_KEY_1": "log.excludeDecoration", "GIT_CONFIG_VALUE_1": "refs/remotes/*",
    }
    for how in ("annotated tag", "lightweight tag", "remote ref"):
      with self.subTest(how), project() as repo:
        repo._git("checkout", "-q", "-b", "feature")
        repo.run("done", "S01", "--render")
        repo.commit("S01 done")
        tip = repo._git("rev-parse", "HEAD").stdout.strip()
        if how == "annotated tag":
          repo._git("tag", "-a", "v1", "-m", "release")
        elif how == "lightweight tag":
          repo._git("tag", "v1")
        else:
          repo._git("update-ref", "refs/remotes/origin/feature", tip)
        repo._git("checkout", "-q", "main")
        repo._git("branch", "-D", "feature")
        with patch.dict(os.environ, config):
          code, out, err = repo.run("verify", "--landed", "S01", "--json")
        self.assertEqual((code, json.loads(out)["reason"]), (1, "other_branch"), err)


class ProbeMemoTests(unittest.TestCase):
  def test_IsRepo_NotARepoThenGitInit_IsSeenAtOnce(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      self.assertFalse(vcs.is_repo(repo.root))
      repo._git("init", "-q")
      self.assertTrue(vcs.is_repo(repo.root))

  def test_IsRepo_AfterClearCache_AsksGitAgain(self) -> None:
    with support.TempRepo(git=True) as repo:
      with support.count_git_calls() as calls:
        self.assertTrue(vcs.is_repo(repo.root))
        self.assertTrue(vcs.is_repo(repo.root))
        vcs.clear_cache()
        self.assertTrue(vcs.is_repo(repo.root))
      self.assertEqual(calls.count(IS_REPO), 2)

  def test_MergeInProgress_AfterMergeHeadAppears_IsSeen(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.write("f.txt", "x\n")
      repo.commit("base")
      self.assertFalse(vcs.merge_in_progress(repo.root))
      repo.write(".git/MERGE_HEAD", repo._git("rev-parse", "HEAD").stdout)
      self.assertTrue(vcs.merge_in_progress(repo.root))

  def test_TuiRefresh_WarmMemo_AsksGitForTheWorktreeAgain(self) -> None:
    from slicer import tui

    with project() as repo:
      state = repo.state()
      view = tui.View.initial(state)
      with support.count_git_calls() as calls:
        vcs.sibling_worktrees(repo.root)
        view.refresh(state)
      self.assertEqual(calls.count(SHOW_TOPLEVEL), 2)


if __name__ == "__main__":
  unittest.main()
