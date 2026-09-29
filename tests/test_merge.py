"""Parallel branches must not conflict on slicer's append-only history."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import support

from slicer import store, vcs


class GitattributesTests(unittest.TestCase):
  def test_Init_WritesGitattributes_MarkingLogAsUnionMerge(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      text = repo.read(f".slicer/{store.GITATTRIBUTES_NAME}")
      self.assertIn("log.jsonl merge=union", text)


class LogOrderTests(unittest.TestCase):
  def test_Log_UnionInterleavedTimestamps_DisplayNewestFirst(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "one")
      repo.run("add", "two")
      # A union merge can drop an older entry after newer ones in file order.
      old = json.dumps(
        {"when": "2020-01-01T00:00:00Z", "item": "S99",
         "action": "edit", "from": "", "to": "", "note": "OLD"}
      )
      with open(repo.root / ".slicer" / store.LOG_NAME, "a", encoding="utf-8") as fh:
        fh.write(old + "\n")
      _, out, _ = repo.run("log", "--json")
      whens = [e["when"] for e in json.loads(out)]
      self.assertEqual(whens, sorted(whens, reverse=True))
      self.assertEqual(whens[-1], "2020-01-01T00:00:00Z")


class RenderDriverGitattributesTests(unittest.TestCase):
  def test_Init_WritesGitattributes_PointingRenderAtTheKeepDriver(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      text = repo.read(f".slicer/{store.GITATTRIBUTES_NAME}")
      self.assertIn("render/ROADMAP.md merge=slicer-generated", text)
      self.assertIn("render/ROADMAP.html merge=slicer-generated", text)
      self.assertIn("render/slices/*.md merge=slicer-generated", text)
      # log.jsonl still union-merges; the render driver does not replace it.
      self.assertIn("log.jsonl merge=union", text)


class RenderMergeDriverTests(unittest.TestCase):
  """The slicer-generated driver keeps generated render/ files on merge, so
  parallel branches leave no conflict markers in them (S109)."""

  def _configure_driver(self, repo: support.TempRepo) -> None:
    # A driver named in .gitattributes resolves only once the clone defines it.
    repo._git("config", "merge.slicer-generated.name", "keep the current branch's generated files")
    repo._git("config", "merge.slicer-generated.driver", "true")

  def _diverge_and_merge(self, repo: support.TempRepo) -> str:
    """Two branches each render a different ROADMAP; return the base branch."""
    repo.run("init")
    self._configure_driver(repo)
    repo.run("add", "base", "--render")
    repo.commit("base")  # commits .gitattributes so the driver applies on merge
    base = repo._git("branch", "--show-current").stdout.strip()
    repo._git("checkout", "-q", "-b", "feature")
    repo.run("add", "from feature", "--render")
    repo.commit("feature")
    repo._git("checkout", "-q", base)
    repo.run("add", "from mainline", "--render")
    repo.commit("mainline")
    # index.json legitimately conflicts (both added an item); render must not.
    repo._git("merge", "--no-edit", "feature")
    return base

  def test_ParallelBranches_ChangingRenderFiles_MergeWithoutConflictMarkers(self) -> None:
    with support.TempRepo(git=True) as repo:
      self._diverge_and_merge(repo)
      roadmap = repo.read(".slicer/render/ROADMAP.md")
      html = repo.read(".slicer/render/ROADMAP.html")
      self.assertNotIn("<<<<<<<", roadmap)
      self.assertNotIn("<<<<<<<", html)
      # The current branch's copy is kept, not a blend of both sides.
      self.assertIn("from mainline", roadmap)
      self.assertNotIn("from feature", roadmap)

  def test_AfterDriverMerge_ResolveIndexAndReRender_CheckPasses(self) -> None:
    with support.TempRepo(git=True) as repo:
      self._diverge_and_merge(repo)
      # Resolve the one real conflict (index.json) by keeping our side, then
      # re-render so the kept files match the index again.
      repo._git("checkout", "--ours", ".slicer/index.json")
      repo._git("add", ".slicer/index.json")
      self.assertEqual(repo.run("render")[0], 0)
      code, out, _ = repo.run("check")
      self.assertEqual(code, 0, out)


class UnionMergeTests(unittest.TestCase):
  def test_ParallelBranches_AppendingHistory_MergeTheLogWithoutConflict(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      repo.run("add", "base")
      repo.commit("base")  # commits .gitattributes so union applies on merge
      base = repo._git("branch", "--show-current").stdout.strip()
      repo._git("checkout", "-q", "-b", "feature")
      repo.run("add", "from feature")
      repo.commit("feature")
      repo._git("checkout", "-q", base)
      repo.run("add", "from mainline")
      repo.commit("mainline")
      # index.json legitimately conflicts (both added an item); the log must not.
      repo._git("merge", "--no-edit", "feature")
      log = repo.read(f".slicer/{store.LOG_NAME}")
      self.assertNotIn("<<<<<<<", log)
      self.assertIn("from feature", log)
      self.assertIn("from mainline", log)


class RenderDriverSetupHintTests(unittest.TestCase):
  """`slicer init` surfaces the one-time, per-clone git config that turns on the
  render merge driver, and slicer never runs `git config` itself (S118)."""

  NAME_LINE = 'git config merge.slicer-generated.name "keep the current branch\'s generated files"'
  DRIVER_LINE = "git config merge.slicer-generated.driver true"

  def test_Init_InGitRepo_PrintsMergeDriverConfigLines(self) -> None:
    with support.TempRepo(git=True) as repo:
      code, out, err = repo.run("init")
      self.assertEqual(code, 0, err)
      self.assertIn(self.NAME_LINE, out)
      self.assertIn(self.DRIVER_LINE, out)

  def test_Init_Json_OmitsTheHintAndStaysTwoKeys(self) -> None:
    with support.TempRepo(git=True) as repo:
      code, out, _ = repo.run("init", "--json")
      self.assertEqual(code, 0)
      doc = json.loads(out)  # one clean document, no hint appended
      self.assertEqual(set(doc), {"root", "dir"})
      self.assertNotIn("git config", out)

  def test_Init_OutsideGitRepo_OmitsTheHint(self) -> None:
    with support.TempRepo() as repo:  # not a git repo
      code, out, _ = repo.run("init")
      self.assertEqual(code, 0)
      self.assertNotIn("git config", out)

  def test_Vcs_Config_AllowsOnlyReadOnlyGets(self) -> None:
    # config is allowlisted only for read-only `--get`s: the git user (S114, for
    # claims) and the render driver (S123, to warn when it is unset). slicer must
    # still never *write* config, so no other form is permitted.
    self.assertEqual(
      vcs.READ_ONLY.get("config"),
      frozenset({("--get", "user.name"), ("--get", "merge.slicer-generated.driver")}),
    )


class SetupGitCommandTests(unittest.TestCase):
  """`slicer setup-git` prints (never runs) the per-clone merge-driver config,
  works with no project, and never loads or locks state (S121)."""

  NAME = "git config merge.slicer-generated.name"
  DRIVER = "git config merge.slicer-generated.driver true"

  def test_SetupGit_PrintsBothConfigLines(self) -> None:
    with support.TempRepo() as repo:
      code, out, err = repo.run("setup-git")
      self.assertEqual((code, err), (0, ""))
      self.assertIn(self.NAME, out)
      self.assertIn(self.DRIVER, out)

  def test_SetupGit_Json_ReturnsTheCommandsAsAList(self) -> None:
    with support.TempRepo() as repo:
      text = repo.run("setup-git")[1]
      code, out, _ = repo.run("setup-git", "--json")
      self.assertEqual(code, 0)
      commands = json.loads(out)
      self.assertEqual(commands, text.splitlines())
      self.assertEqual(len(commands), 2)
      # --lean must not choke on a list of plain strings.
      self.assertEqual(json.loads(repo.run("setup-git", "--json", "--lean")[1]), commands)

  def test_SetupGit_NeedsNoProject_NeverLoadsOrLocks(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      with patch("slicer.cli.store.discover", side_effect=AssertionError("discover")), \
           patch("slicer.cli.store.load", side_effect=AssertionError("load")), \
           patch("slicer.cli.store.project_lock", side_effect=AssertionError("lock")):
        code, out, err = repo.run("setup-git")
      self.assertEqual((code, err), (0, ""))
      self.assertIn(self.DRIVER, out)
      self.assertEqual(list(repo.root.iterdir()), [])  # created nothing


class RenderDriverConfiguredWarningTests(unittest.TestCase):
  """`slicer verify` warns (never fails) when the render merge driver is not
  configured in this clone, pointing at `slicer setup-git` (S123)."""

  MISSING = "render merge driver not configured"

  def _configure_driver(self, repo: support.TempRepo) -> None:
    repo._git("config", "merge.slicer-generated.driver", "true")

  def _set_git_check(self, repo: support.TempRepo, value: bool) -> None:
    path = repo.root / ".slicer" / "config.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg["git_check"] = value
    path.write_text(json.dumps(cfg) + "\n", encoding="utf-8")

  def test_Verify_DriverNotConfigured_WarnsAndPointsAtSetupGit(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      code, out, _ = repo.run("verify")
      self.assertEqual(code, 0)  # a warning never fails verify
      self.assertIn(self.MISSING, out)
      self.assertIn("setup-git", out)

  def test_Verify_DriverConfigured_NoWarning(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      self._configure_driver(repo)
      code, out, _ = repo.run("verify")
      self.assertEqual(code, 0)
      self.assertNotIn(self.MISSING, out)

  def test_Verify_OutsideGitRepo_NoDriverWarning(self) -> None:
    with support.TempRepo() as repo:  # not a git repo
      repo.run("init")
      code, out, _ = repo.run("verify")
      self.assertEqual(code, 0)
      self.assertNotIn(self.MISSING, out)

  def test_Verify_DriverWarning_IsIndependentOfGitCheck(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      self._set_git_check(repo, False)
      code, out, _ = repo.run("verify")
      self.assertEqual(code, 0)
      self.assertIn(self.MISSING, out)  # still warns with the history cross-check off

  def test_Check_StaysGitFree_NoDriverWarning(self) -> None:
    # The driver probe is a git query and must not leak into the git-free CI gate,
    # even though the driver is unconfigured here.
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      repo.run("render")  # so check is clean and any failure would be the warning
      code, out, _ = repo.run("check")
      self.assertEqual(code, 0, out)
      self.assertNotIn(self.MISSING, out)


if __name__ == "__main__":
  unittest.main()
