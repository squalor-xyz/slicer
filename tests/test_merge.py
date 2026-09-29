"""Parallel branches must not conflict on slicer's append-only history."""

from __future__ import annotations

import json
import unittest

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

  def test_Vcs_Allowlist_DoesNotIncludeConfig(self) -> None:
    # slicer prints the config lines; it must never run `git config` itself.
    self.assertNotIn("config", vcs.ALLOWED)


if __name__ == "__main__":
  unittest.main()
