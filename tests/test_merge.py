"""Parallel branches must not conflict on slicer's append-only history."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import support

from slicer import ops, store, tui, vcs


def start_merge(repo: support.TempRepo) -> None:
  """Leave a real, conflict-free Git merge awaiting its commit."""
  repo.run("init")
  repo.run("add", "base", "--render")
  repo.commit("base")
  base = repo._git("branch", "--show-current").stdout.strip()
  repo._git("checkout", "-q", "-b", "feature")
  repo.write("feature.txt", "feature\n")
  repo.commit("feature")
  repo._git("checkout", "-q", base)
  merged = repo._git("merge", "--no-commit", "--no-ff", "feature")
  if merged.returncode:
    raise AssertionError(merged.stderr)


class MergeGuardTests(unittest.TestCase):
  def test_MergeInProgress_CliRefusesStateWritesWithoutChangingFiles(self) -> None:
    with support.TempRepo(git=True) as repo:
      start_merge(repo)
      self.assertTrue(vcs.merge_in_progress(repo.root))
      before = repo.read(".slicer/index.json")
      for command in (("add", "blocked"), ("init", "--force"), ("done", "S01")):
        with self.subTest(command=command):
          code, out, err = repo.run(*command, "--json")
          self.assertEqual(code, 2)
          self.assertEqual(json.loads(out)["error"]["code"], "merge_in_progress")
          self.assertIn("finish or abort", err)
          self.assertEqual(repo.read(".slicer/index.json"), before)

  def test_MergeInProgress_ReadsPreviewsAndRenderRemainAvailable(self) -> None:
    with support.TempRepo(git=True) as repo:
      start_merge(repo)
      repo.write("draft.md", "## Preview only\n")
      for command in (("show", "S01"), ("import", "--skeleton"),
                      ("import", "draft.md", "--dry-run"),
                      ("remove", "S01", "--purge", "--dry-run"),
                      ("render",), ("sync", "--check")):
        with self.subTest(command=command):
          code, _, err = repo.run(*command)
          self.assertEqual(code, 0, err)
          self.assertNotIn("merge in progress", err)

  def test_NoMerge_GuardIsSilentAndMutationSucceeds(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      self.assertFalse(vcs.merge_in_progress(repo.root))
      # Scored, so the S140 unscored hint stays out of the stderr this checks.
      code, _, err = repo.run("add", "allowed", "--effort", "1")
      self.assertEqual(code, 0, err)
      self.assertEqual(err, "")

  def test_LinkedWorktree_MergeStateIsLocalToThatCheckout(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.write("base.txt", "base\n")
      repo.commit("base")
      linked = repo.root / "linked"
      added = repo._git("worktree", "add", "-q", "-b", "linked", str(linked))
      self.assertEqual(added.returncode, 0, added.stderr)
      repo._git("-C", str(linked), "checkout", "-q", "-b", "feature")
      (linked / "feature.txt").write_text("feature\n", encoding="utf-8")
      repo._git("-C", str(linked), "add", "feature.txt")
      repo._git("-C", str(linked), "commit", "-q", "-m", "feature")
      repo._git("-C", str(linked), "checkout", "-q", "linked")
      merged = repo._git("-C", str(linked), "merge", "--no-commit", "--no-ff", "feature")
      self.assertEqual(merged.returncode, 0, merged.stderr)
      self.assertTrue(vcs.merge_in_progress(linked))
      self.assertFalse(vcs.merge_in_progress(repo.root))

  def test_MergeInProgress_TuiSaveIsRefusedButRenderWorks(self) -> None:
    with support.TempRepo(git=True) as repo:
      start_merge(repo)
      state = repo.state()
      before = repo.read(".slicer/index.json")
      result = tui.act(state, "s", "S01")
      self.assertEqual(result.severity, "error")
      self.assertIn("merge in progress", result.message)
      edit = tui.EditRequest("new", "", "new item", "")
      result = tui.apply_edit_result(state, edit, "blocked")
      self.assertEqual(result.severity, "error")
      self.assertEqual(repo.read(".slicer/index.json"), before)
      self.assertNotEqual(tui.act(state, "r", "").severity, "error")

  def test_MergeInProgress_TuiWizardSaveIsRefusedBeforeWriting(self) -> None:
    with support.TempRepo(git=True) as repo:
      start_merge(repo)
      state = repo.state()
      view = tui.View.initial(state)
      view._open_wizard(state)
      view.wizard.add_item()
      view.wizard.items[0].values[0] = "blocked"
      before = repo.read(".slicer/index.json")
      view._save_wizard(state)
      self.assertIn("merge in progress", view.wizard.error)
      self.assertEqual(repo.read(".slicer/index.json"), before)

  def test_MergeInProgress_SetupGitAttributesIsRefusedAndWritesNothing(self) -> None:
    with support.TempRepo(git=True) as repo:
      start_merge(repo)
      stale = ops.GITATTRIBUTES.replace(SetupGitAttributesTests.RELAY_LINE, "")
      repo.write(f".slicer/{store.GITATTRIBUTES_NAME}", stale)
      code, out, _ = repo.run("setup-git", "--attributes", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out)["error"]["code"], "merge_in_progress")
      self.assertEqual(repo.read(f".slicer/{store.GITATTRIBUTES_NAME}"), stale)



class GitattributesTests(unittest.TestCase):
  def test_Init_WritesGitattributes_MarkingLogAsUnionMerge(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      text = repo.read(f".slicer/{store.GITATTRIBUTES_NAME}")
      self.assertIn("log.jsonl merge=union", text)
      # index.json takes the slicer-index driver, which settles a next_id conflict.
      self.assertIn("\nindex.json merge=slicer-index\n", text)


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
      self.assertIn("render/RELAY-ROLES.md merge=slicer-generated", text)
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
    # not a git repo, even when TMPDIR sits inside one
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      code, out, _ = repo.run("init")
      self.assertEqual(code, 0)
      self.assertNotIn("git config", out)

  def test_Vcs_Config_AllowsOnlyReadOnlyGets(self) -> None:
    # config is allowlisted only for read-only `--get`s: the git user (S114, for
    # claims) and the two merge drivers (S123, S191, to warn when unset). slicer must
    # still never *write* config, so no other form is permitted.
    self.assertEqual(
      vcs.READ_ONLY.get("config"),
      frozenset({
        ("--get", "user.name"),
        ("--get", "merge.slicer-generated.driver"),
        ("--get", "merge.slicer-index.driver"),
      }),
    )


class SetupGitCommandTests(unittest.TestCase):
  """`slicer setup-git` prints (never runs) the per-clone merge-driver config,
  works with no project, and never loads or locks state (S121)."""

  NAME = "git config merge.slicer-generated.name"
  DRIVER = "git config merge.slicer-generated.driver true"
  INDEX_DRIVER = 'git config merge.slicer-index.driver "slicer merge-index %O %A %B"'

  def test_SetupGit_PrintsBothConfigLines(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      code, out, err = repo.run("setup-git")
      self.assertEqual((code, err), (0, ""))
      self.assertIn(self.NAME, out)
      self.assertIn(self.DRIVER, out)
      self.assertIn(self.INDEX_DRIVER, out)

  def test_SetupGit_Attributes_MapIndexJsonToSlicerIndex(self) -> None:
    # setup-git prints the driver config; the attribute that names the driver is the
    # file `init` writes, so check what Git itself resolves for the index.
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      self.assertIn(self.INDEX_DRIVER, repo.run("setup-git")[1])
      found = repo._git("check-attr", "merge", "--", ".slicer/index.json")
      self.assertEqual(found.stdout.strip(), ".slicer/index.json: merge: slicer-index")

  def test_SetupGit_Json_ReturnsTheCommandsAsAList(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      text = repo.run("setup-git")[1]
      code, out, _ = repo.run("setup-git", "--json")
      self.assertEqual(code, 0)
      commands = json.loads(out)
      self.assertEqual(commands, text.splitlines())
      self.assertEqual(len(commands), 4)
      # --lean must not choke on a list of plain strings.
      self.assertEqual(json.loads(repo.run("setup-git", "--json", "--lean")[1]), commands)

  def test_SetupGit_NeedsNoProject_NeverLoadsOrLocks(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      with patch("slicer.cli.store.discover", side_effect=AssertionError("discover")), \
           patch("slicer.cli.store.load", side_effect=AssertionError("load")), \
           patch("slicer.cli.store.project_lock", side_effect=AssertionError("lock")):
        # Discovery is mocked here; bypass only the test harness's guard.
        code, out, err = repo.run("setup-git", allow_parent_project=True)
      self.assertEqual((code, err), (0, ""))
      self.assertIn(self.DRIVER, out)
      self.assertEqual(list(repo.root.iterdir()), [])  # created nothing


class SetupGitAttributesTests(unittest.TestCase):
  """`slicer setup-git --attributes` refreshes `.slicer/.gitattributes` from the
  template under the project lock and writes nothing else. Without the flag,
  setup-git stays a read-only print (S260)."""

  ATTRS = store.GITATTRIBUTES_NAME
  RELAY_LINE = "render/RELAY-ROLES.md merge=slicer-generated\n"
  OLD_NS = 10**18  # an mtime no write in these tests produces

  def _stale_project(self, repo: support.TempRepo) -> None:
    """An initialised project whose .gitattributes predates the RELAY-ROLES line."""
    repo.run("init")
    repo.write(f".slicer/{self.ATTRS}", ops.GITATTRIBUTES.replace(self.RELAY_LINE, ""))

  def _age(self, repo: support.TempRepo) -> None:
    """Date every .slicer/ file, so a rewrite shows as a new mtime."""
    for path in self._files(repo):
      os.utime(path, ns=(self.OLD_NS, self.OLD_NS))

  def _files(self, repo: support.TempRepo) -> list[Path]:
    """Files under .slicer/, minus the lock file, which the lock itself creates."""
    base = repo.root / store.DIR_NAME
    return [p for p in sorted(base.rglob("*")) if p.is_file() and p.name != store.LOCK_NAME]

  def _snapshot(self, repo: support.TempRepo) -> dict[str, tuple[bytes, int]]:
    base = repo.root / store.DIR_NAME
    return {
      path.relative_to(base).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
      for path in self._files(repo)
    }

  def _without_attrs(self, snapshot: dict[str, tuple[bytes, int]]) -> dict[str, tuple[bytes, int]]:
    return {name: value for name, value in snapshot.items() if name != self.ATTRS}

  def _attrs_path(self, repo: support.TempRepo) -> str:
    return str(repo.root / store.DIR_NAME / self.ATTRS)

  def test_SetupGitAttributes_StaleFile_RewritesToTemplateAndTouchesNoOtherFile(self) -> None:
    with support.TempRepo() as repo:
      self._stale_project(repo)
      self._age(repo)
      before = self._snapshot(repo)
      code, _, err = repo.run("setup-git", "--attributes")
      self.assertEqual(code, 0, err)
      after = self._snapshot(repo)
      self.assertEqual(after[self.ATTRS][0], ops.GITATTRIBUTES.encode("utf-8"))
      self.assertEqual(self._without_attrs(after), self._without_attrs(before))

  def test_SetupGitAttributes_CurrentFile_IsLeftByteIdenticalAndReportedUnchanged(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      self._age(repo)
      before = self._snapshot(repo)
      code, out, err = repo.run("setup-git", "--attributes", "--json", "--lean")
      self.assertEqual(code, 0, err)
      self.assertEqual(
        json.loads(out)["attributes"], {"path": self._attrs_path(repo), "changed": False},
      )
      # Same bytes and same mtime: nothing was written, not even identical text.
      self.assertEqual(self._snapshot(repo), before)

  def test_SetupGitAttributes_MissingFile_IsCreatedFromTemplate(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      (repo.root / ".slicer" / self.ATTRS).unlink()
      code, out, err = repo.run("setup-git", "--attributes", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.read(f".slicer/{self.ATTRS}"), ops.GITATTRIBUTES)
      self.assertEqual(
        json.loads(out)["attributes"], {"path": self._attrs_path(repo), "changed": True},
      )

  def test_SetupGitAttributes_Json_ReturnsCommandsAndAttributesObject(self) -> None:
    with support.TempRepo() as repo:
      self._stale_project(repo)
      code, out, err = repo.run("setup-git", "--attributes", "--json")
      self.assertEqual(code, 0, err)
      payload = json.loads(out)
      self.assertEqual(set(payload), {"commands", "attributes"})
      self.assertEqual(payload["commands"], repo.run("setup-git")[1].splitlines())
      self.assertEqual(
        payload["attributes"], {"path": self._attrs_path(repo), "changed": True},
      )

  def test_SetupGitAttributes_Text_StdoutIsPlainSetupGitAndReportGoesToStderr(self) -> None:
    with support.TempRepo() as repo:
      self._stale_project(repo)
      plain = repo.run("setup-git")[1]
      code, out, err = repo.run("setup-git", "--attributes")
      self.assertEqual(code, 0, err)
      self.assertEqual(out, plain)  # `| sh` still receives only the git config lines
      self.assertIn(f"wrote {self._attrs_path(repo)}", err)
      code, out, err = repo.run("setup-git", "--attributes")
      self.assertEqual(out, plain)
      self.assertIn(f"unchanged {self._attrs_path(repo)}", err)

  def test_SetupGitAttributes_OutsideProject_ExitsTwoWithNoProjectErrorAndWritesNothing(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      code, out, _ = repo.run("setup-git", "--attributes", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out)["error"]["code"], "state")
      self.assertEqual(list(repo.root.iterdir()), [])

  def test_SetupGitAttributes_LockHeldByAnotherWriter_FailsLockedAndLeavesFileAlone(self) -> None:
    if store.fcntl is None:  # pragma: no cover - POSIX only
      self.skipTest("no flock on this platform")
    with support.TempRepo() as repo:
      self._stale_project(repo)
      stale = repo.read(f".slicer/{self.ATTRS}")
      fd = os.open(repo.root / store.DIR_NAME / store.LOCK_NAME, os.O_CREAT | os.O_RDWR, 0o666)
      store.fcntl.flock(fd, store.fcntl.LOCK_EX)
      try:
        with patch.dict(os.environ, {store.LOCK_TIMEOUT_ENV: "0.2"}):
          code, out, _ = repo.run("setup-git", "--attributes", "--json")
      finally:
        store.fcntl.flock(fd, store.fcntl.LOCK_UN)
        os.close(fd)
      self.assertEqual(code, 3)
      self.assertEqual(json.loads(out)["error"]["code"], "locked")
      self.assertEqual(repo.read(f".slicer/{self.ATTRS}"), stale)

  def test_SetupGit_NoFlag_LeavesStaleAttributesAlone(self) -> None:
    # Green before the flag exists: it pins that plain setup-git never writes.
    with support.TempRepo() as repo:
      self._stale_project(repo)
      self._age(repo)
      before = self._snapshot(repo)
      code, _, err = repo.run("setup-git")
      self.assertEqual((code, err), (0, ""))
      self.assertEqual(self._snapshot(repo), before)


class RenderDriverConfiguredWarningTests(unittest.TestCase):
  """`slicer verify` warns (never fails) when the render merge driver is not
  configured, but only when this checkout has sibling worktrees -- the parallel
  workflow the driver serves -- and `render_driver_check` is on (S123, S125)."""

  MISSING = "render merge driver not configured"
  INDEX_MISSING = "index merge driver not configured"

  def _configure_driver(self, repo: support.TempRepo) -> None:
    repo._git("config", "merge.slicer-generated.driver", "true")

  def _add_sibling(self, repo: support.TempRepo) -> None:
    repo.commit("base")  # `git worktree add` needs a commit
    repo._git("worktree", "add", str(repo.root / "sib"), "-b", "sib")

  def _set_config(self, repo: support.TempRepo, **changes: object) -> None:
    path = repo.root / ".slicer" / "config.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg.update(changes)
    path.write_text(json.dumps(cfg) + "\n", encoding="utf-8")

  def test_Verify_SiblingWorktreeDriverUnset_WarnsAndPointsAtSetupGit(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      self._add_sibling(repo)
      code, out, _ = repo.run("verify")
      self.assertEqual(code, 0)  # a warning never fails verify
      self.assertIn(self.MISSING, out)
      self.assertIn("setup-git", out)

  def test_Verify_SingleWorktree_StaysQuiet(self) -> None:
    # The solo case: one worktree, driver unset -> no nag.
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      repo.commit("base")
      code, out, _ = repo.run("verify")
      self.assertEqual(code, 0)
      self.assertNotIn(self.MISSING, out)

  def test_Verify_RenderDriverCheckOff_SilencesEvenWithSibling(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      self._set_config(repo, render_driver_check=False)
      self._add_sibling(repo)
      code, out, _ = repo.run("verify")
      self.assertEqual(code, 0)
      self.assertNotIn(self.MISSING, out)

  def test_Verify_DriverConfigured_NoWarning(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      self._add_sibling(repo)
      self._configure_driver(repo)
      code, out, _ = repo.run("verify")
      self.assertEqual(code, 0)
      self.assertNotIn(self.MISSING, out)

  def test_Verify_IndexDriverUnset_WarnsEvenWithRenderDriverSet(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      self._add_sibling(repo)
      self._configure_driver(repo)
      code, out, _ = repo.run("verify")
      self.assertEqual(code, 0)
      self.assertIn(self.INDEX_MISSING, out)
      self.assertIn("setup-git", out)
      repo._git("config", "merge.slicer-index.driver", "slicer merge-index %O %A %B")
      self.assertNotIn(self.INDEX_MISSING, repo.run("verify")[1])

  def test_Verify_IndexDriverCheck_FollowsTheSameGates(self) -> None:
    with support.TempRepo(git=True) as repo:  # single worktree
      repo.run("init")
      repo.commit("base")
      self.assertNotIn(self.INDEX_MISSING, repo.run("verify")[1])
    with support.TempRepo(git=True) as repo:  # check off
      repo.run("init")
      self._set_config(repo, render_driver_check=False)
      self._add_sibling(repo)
      self.assertNotIn(self.INDEX_MISSING, repo.run("verify")[1])

  def test_Verify_OutsideGitRepo_NoDriverWarning(self) -> None:
    with support.TempRepo() as repo:  # not a git repo
      repo.run("init")
      code, out, _ = repo.run("verify")
      self.assertEqual(code, 0)
      self.assertNotIn(self.MISSING, out)

  def test_Verify_DriverWarning_IsIndependentOfGitCheck(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      self._set_config(repo, git_check=False)
      self._add_sibling(repo)
      code, out, _ = repo.run("verify")
      self.assertEqual(code, 0)
      self.assertIn(self.MISSING, out)  # still warns with the history cross-check off

  def test_Check_StaysGitFree_NoDriverWarning(self) -> None:
    # The driver probe is a git query and must not leak into the git-free CI gate,
    # even with a sibling worktree and the driver unset.
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      self._add_sibling(repo)
      repo.run("render")  # so check is clean and any failure would be the warning
      code, out, _ = repo.run("check")
      self.assertEqual(code, 0, out)
      self.assertNotIn(self.MISSING, out)


if __name__ == "__main__":
  unittest.main()
