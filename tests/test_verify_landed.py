"""`slicer verify --landed ID [--on REF]`: did an item's done state reach a branch?

Each case builds a throwaway git repo, commits at the points the question is
about, and asks the command from the checkout the answer is for. The report is
read from `--json` output, never from the verify module's internals.
"""

from __future__ import annotations

import json
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import support

from slicer import vcs
from slicer.errors import StateError


@contextmanager
def project(done_dir: str | None = None) -> Iterator[support.TempRepo]:
  """A git project on `main` whose first commit holds S01 done and S02 open."""
  repo = support.TempRepo(git=True)
  try:
    repo._git("checkout", "-q", "-b", "main")
    support.make_mini(repo)
    repo.run("init")
    repo.run("migrate", "--from", "docs/slices")
    if done_dir is not None:
      # A renamed done_dir strands existing files; move them the way the docs say.
      path = repo.root / ".slicer/config.json"
      cfg = json.loads(path.read_text())
      cfg["done_dir"] = done_dir
      path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
      slices = repo.root / ".slicer/slices"
      (slices / "done").rename(slices / done_dir)
      repo.run("render")
    repo.commit("base")
    yield repo
  finally:
    repo.close()


def landed(repo: support.TempRepo, item: str, *on: str) -> tuple[int, dict, str]:
  code, out, err = repo.run("verify", "--landed", item, *on, "--json")
  return code, (json.loads(out) if out.strip() else {}), err


def snapshot(repo: support.TempRepo) -> dict[str, bytes]:
  files = {
    str(p.relative_to(repo.root)): p.read_bytes()
    for p in sorted((repo.root / ".slicer").rglob("*")) if p.is_file()
  }
  files[".git/index"] = (repo.root / ".git/index").read_bytes()
  return files


class LandedTests(unittest.TestCase):
  def test_Landed_DoneCommittedOnRef_Passes(self) -> None:
    with project() as repo:
      self.assertEqual(repo.run("done", "S02", "--render")[0], 0)
      repo.commit("S02 done")
      code, report, err = landed(repo, "S02", "--on", "main")
      self.assertEqual((code, err), (0, ""))
      self.assertTrue(report["landed"])
      self.assertEqual(report["id"], "S02")
      self.assertEqual(report["reason"], "")
      head = repo._git("rev-parse", "HEAD").stdout.strip()
      self.assertEqual(report["ref_commit"], head)
      self.assertEqual(report["commit"], head)

  def test_Landed_DefaultRef_IsHead(self) -> None:
    with project() as repo:
      code, report, _ = landed(repo, "S01")
      self.assertEqual(code, 0)
      self.assertEqual(report["ref"], "HEAD")

  def test_NotLanded_DoneOnUnmergedBranch_FailsOnMainPassesOnFeature(self) -> None:
    with project() as repo:
      repo._git("checkout", "-q", "-b", "feature")
      repo.run("done", "S02", "--render")
      repo.commit("S02 done")
      code, report, _ = landed(repo, "S02", "--on", "main")
      self.assertEqual(code, 1)
      self.assertFalse(report["landed"])
      self.assertEqual(report["reason"], "other_branch")
      self.assertIn("another branch", report["message"])
      code, report, _ = landed(repo, "S02", "--on", "feature")
      self.assertEqual(code, 0)
      self.assertTrue(report["landed"])

  def test_NotLanded_DoneOnUnmergedBranch_AskedFromMain_FailsOnMain(self) -> None:
    with project() as repo:
      repo._git("checkout", "-q", "-b", "feature")
      repo.run("done", "S02", "--render")
      repo.commit("S02 done")
      repo._git("checkout", "-q", "main")
      code, report, _ = landed(repo, "S02")
      self.assertEqual((code, report["reason"]), (1, "other_branch"))

  def test_NotLanded_DoneBranchMerged_PassesOnMain(self) -> None:
    with project() as repo:
      repo._git("checkout", "-q", "-b", "feature")
      repo.run("done", "S02", "--render")
      repo.commit("S02 done")
      repo._git("checkout", "-q", "main")
      self.assertEqual(landed(repo, "S02")[0], 1)
      repo._git("merge", "--no-ff", "-q", "-m", "Merge feature", "feature")
      code, report, _ = landed(repo, "S02", "--on", "main")
      self.assertEqual(code, 0)
      self.assertTrue(report["landed"])

  def test_NotLanded_WorkingTreeOnly_Fails(self) -> None:
    with project() as repo:
      repo.run("done", "S02", "--render")
      code, report, _ = landed(repo, "S02")
      self.assertEqual(code, 1)
      self.assertEqual(report["reason"], "working_tree_only")
      self.assertIn("only in the working tree", report["message"])

  def test_NotLanded_Open_Fails(self) -> None:
    with project() as repo:
      code, report, _ = landed(repo, "S02")
      self.assertEqual(code, 1)
      self.assertEqual(report["reason"], "not_done")
      self.assertIn("not done", report["message"])

  def test_NotLanded_TextOutput_NamesTheReason(self) -> None:
    with project() as repo:
      code, out, _ = repo.run("verify", "--landed", "S02")
      self.assertEqual(code, 1)
      self.assertIn("S02", out)
      self.assertIn("not done", out)

  def test_DoneDirConfig_Respected(self) -> None:
    with project(done_dir="finished") as repo:
      self.assertTrue((repo.root / ".slicer/slices/finished/S01.json").is_file())
      code, report, err = landed(repo, "S01")
      self.assertEqual((code, err), (0, ""))
      self.assertTrue(report["landed"])
      repo.run("done", "S02", "--render")
      self.assertEqual(landed(repo, "S02")[1]["reason"], "working_tree_only")
      repo.commit("S02 done")
      self.assertEqual(landed(repo, "S02")[0], 0)

  def test_Landed_DoneItemWithoutSlice_UsesTheIndex(self) -> None:
    with project() as repo:
      repo.run("add", "an idea")
      repo.run("done", "S05", "--render")
      self.assertEqual(landed(repo, "S05")[1]["reason"], "working_tree_only")
      repo._git("checkout", "-q", "-b", "feature")
      repo.commit("S05 done")
      repo._git("checkout", "-q", "main")
      # On main the item is unknown, yet the feature branch's index records it done.
      self.assertEqual(landed(repo, "S05", "--on", "main")[1]["reason"], "other_branch")
      code, report, _ = landed(repo, "S05", "--on", "feature")
      self.assertEqual((code, report["landed"]), (0, True))

  def test_Landed_ReadsTheRefTreeNotTheWorkingTree(self) -> None:
    with project() as repo:
      repo._git("checkout", "-q", "-b", "feature")
      repo.run("done", "S02", "--render")
      repo.commit("S02 done")
      repo._git("checkout", "-q", "main")
      # The working tree is back on main, where S02 is open, yet the ref has it done.
      code, report, _ = landed(repo, "S02", "--on", "feature")
      self.assertEqual((code, report["landed"]), (0, True))

  def test_UnknownIdOrBadRef_Usage(self) -> None:
    with project() as repo:
      before = snapshot(repo)
      for args, expected in (
        (("S99",), "no_such_item"),
        (("S02", "--on", "no-such-ref"), "bad_ref"),
        (("S02", "--on=--all"), "bad_ref"),
        (("S02", "--on", ""), "bad_ref"),
      ):
        with self.subTest(args):
          code, out, _ = repo.run("verify", "--landed", *args, "--json")
          self.assertEqual(code, 2)
          self.assertEqual(json.loads(out)["error"]["code"], expected)
      self.assertEqual(snapshot(repo), before)

  def test_Landed_LaterEditsToTheDoneSlice_ReportTheCommitThatMovedIt(self) -> None:
    with project() as repo:
      repo.run("done", "S02", "--render")
      repo.commit("S02 done")
      done_commit = repo._git("rev-parse", "HEAD").stdout.strip()
      self.assertEqual(repo.run("edit", "S02", "--section", "Why", "--text", "reworded", "--render")[0], 0)
      repo.commit("reword S02")
      code, report, _ = landed(repo, "S02")
      self.assertEqual(code, 0)
      self.assertEqual(report["commit"], done_commit)

  def test_NotLanded_IndexAndSliceFolderDisagreeAtRef_Fails(self) -> None:
    with project() as repo:
      state = repo.state()
      state.index.require("S02").status = "done"  # done in the index, slice file still open
      state.index.require("S01").status = "open"  # open in the index, slice file still in done/
      state.save_index()
      repo.commit("inconsistent")
      code, report, _ = landed(repo, "S02")
      self.assertEqual((code, report["reason"]), (1, "not_done"))
      self.assertIn("slice file is missing", report["message"])
      code, report, _ = landed(repo, "S01")
      self.assertEqual((code, report["reason"]), (1, "not_done"))

  def test_Landed_ProjectBelowTheRepoTop_ReadsItsOwnFiles(self) -> None:
    with support.TempRepo(git=True) as repo:
      sub = repo.root / "sub"
      sub.mkdir()
      repo.write("sub/docs/slices/README.md", support.MINI_INDEX)
      for rel, text in support.MINI_SLICES.items():
        repo.write(f"sub/docs/slices/{rel}", text)
      # --root names the project, so the fixture's own discovery check does not apply.
      def run(*argv: str) -> tuple[int, str, str]:
        return repo.run(*argv, "--root", str(sub), allow_parent_project=True)

      run("init")
      run("migrate", "--from", "docs/slices")
      repo.commit("base")
      run("done", "S02", "--render")
      code, out, _ = run("verify", "--landed", "S02", "--json")
      self.assertEqual((code, json.loads(out)["reason"]), (1, "working_tree_only"))
      repo.commit("S02 done")
      code, out, _ = run("verify", "--landed", "S02", "--json")
      self.assertEqual((code, json.loads(out)["landed"]), (0, True))

  def test_NotLanded_ReopenedOnUnmergedBranch_IsNotDone(self) -> None:
    with project() as repo:
      repo._git("checkout", "-q", "-b", "feature")
      repo.run("done", "S02", "--render")
      repo.commit("S02 done")
      self.assertEqual(repo.run("set", "S02", "--status", "open", "--render")[0], 0)
      repo.commit("S02 reopened")
      code, report, _ = landed(repo, "S02", "--on", "main")
      self.assertEqual((code, report["reason"]), (1, "not_done"))

  def test_NotLanded_NoSliceReopenedOnUnmergedBranch_IsNotDone(self) -> None:
    with project() as repo:
      repo.run("add", "an idea")
      repo.commit("S05 filed")
      repo._git("checkout", "-q", "-b", "feature")
      repo.run("done", "S05", "--render")
      repo.commit("S05 done")
      repo.run("set", "S05", "--status", "open", "--render")
      repo.commit("S05 reopened")
      code, report, _ = landed(repo, "S05", "--on", "main")
      self.assertEqual((code, report["reason"]), (1, "not_done"))

  def test_NotLanded_SquashedThenReopened_FeatureStillDone_IsOtherBranch(self) -> None:
    with project() as repo:
      repo._git("checkout", "-q", "-b", "feature")
      repo.run("done", "S02", "--render")
      repo.commit("S02 done")
      repo._git("checkout", "-q", "main")
      repo._git("merge", "--squash", "feature")
      repo.commit("squash feature")
      repo.run("set", "S02", "--status", "open", "--render")
      repo.commit("S02 reopened on main")
      # The squash left the feature commit outside main's history, and feature still has S02 done.
      code, report, _ = landed(repo, "S02", "--on", "main")
      self.assertEqual((code, report["reason"]), (1, "other_branch"))

  def test_NotLanded_DoneOnlyOnATagOrRemoteRef_IsOtherBranch(self) -> None:
    for how in ("annotated tag", "lightweight tag", "remote ref"):
      with self.subTest(how), project() as repo:
        repo._git("checkout", "-q", "-b", "feature")
        repo.run("done", "S02", "--render")
        repo.commit("S02 done")
        tip = repo._git("rev-parse", "HEAD").stdout.strip()
        if how == "annotated tag":
          repo._git("tag", "-a", "v1", "-m", "release")
        elif how == "lightweight tag":
          repo._git("tag", "v1")
        else:
          repo._git("update-ref", "refs/remotes/origin/feature", tip)
        repo._git("checkout", "-q", "main")
        repo._git("branch", "-D", "feature")
        code, report, _ = landed(repo, "S02")
        self.assertEqual((code, report["reason"]), (1, "other_branch"))

  def test_NotLanded_MergedThenReopenedOnMain_IsNotDone(self) -> None:
    with project() as repo:
      repo._git("checkout", "-q", "-b", "feature")
      repo.run("done", "S02", "--render")
      repo.commit("S02 done")
      repo._git("checkout", "-q", "main")
      repo._git("merge", "--no-ff", "-q", "-m", "Merge feature", "feature")
      repo.run("set", "S02", "--status", "open", "--render")
      repo.commit("S02 reopened on main")
      # feature is now an ancestor of main, so its old done state is history, not another branch.
      code, report, _ = landed(repo, "S02", "--on", "main")
      self.assertEqual((code, report["reason"]), (1, "not_done"))

  def test_NotLanded_OtherTipIndexDoneButSliceFileMissing_IsNotOtherBranch(self) -> None:
    with project() as repo:
      repo._git("checkout", "-q", "-b", "feature")
      state = repo.state()
      state.index.require("S02").status = "done"  # done in the index, slice file still open
      state.save_index()
      repo.commit("inconsistent")
      repo._git("checkout", "-q", "main")
      code, report, _ = landed(repo, "S02", "--on", "main")
      self.assertEqual((code, report["reason"]), (1, "not_done"))

  def test_Landed_DoneTwice_ReportsTheLatestMove(self) -> None:
    with project() as repo:
      repo.run("done", "S02", "--render")
      repo.commit("S02 done")
      repo.run("set", "S02", "--status", "open", "--render")
      repo.commit("S02 reopened")
      repo.run("done", "S02", "--render")
      repo.commit("S02 done again")
      again = repo._git("rev-parse", "HEAD").stdout.strip()
      code, report, _ = landed(repo, "S02")
      self.assertEqual((code, report["commit"]), (0, again))

  def test_Landed_UnreadableIndexOnRef_IsAnError(self) -> None:
    for name, text, code_name in (
      ("conflict markers", "<<<<<<< ours\n=======\n>>>>>>> theirs\n", "corrupt"),
      ("future schema", json.dumps({"version": 999}), "schema_too_new"),
    ):
      with self.subTest(name), project() as repo:
        repo._git("checkout", "-q", "-b", "broken")
        repo.write(".slicer/index.json", text)
        repo.commit("damaged index")
        repo._git("checkout", "-q", "main")
        code, out, _ = repo.run("verify", "--landed", "S02", "--on", "broken", "--json")
        self.assertEqual((code, json.loads(out)["error"]["code"]), (3, code_name))

  def test_NotLanded_StashedDone_IsWorkingTreeOnly(self) -> None:
    with project() as repo:
      repo.run("done", "S02", "--render")
      repo._git("add", "-A")
      self.assertEqual(repo._git("stash").returncode, 0)
      self.assertEqual(repo._git("stash", "apply").returncode, 0)
      code, report, _ = landed(repo, "S02")
      self.assertEqual((code, report["reason"]), (1, "working_tree_only"))

  def test_NotLanded_UnreadableIndexOnOtherRef_StillAnswers(self) -> None:
    def conflicted(text: str) -> str:
      return "<<<<<<< ours\n" + text + "=======\n" + text + ">>>>>>> theirs\n"

    def future(text: str) -> str:
      return json.dumps({"version": 999})

    def no_id(text: str) -> str:
      data = json.loads(text)
      del data["items"][0]["id"]
      return json.dumps(data)

    def not_an_object(text: str) -> str:
      return "[]"

    for name, damage in (
      ("conflict markers", conflicted), ("future schema", future), ("item without id", no_id),
      ("not an object", not_an_object),
    ):
      with self.subTest(name), project() as repo:
        repo.run("add", "an idea")
        repo.commit("S05 filed")
        repo._git("checkout", "-q", "-b", "broken")
        repo.write(".slicer/index.json", damage(repo.read(".slicer/index.json")))
        repo.commit("damaged index")
        repo._git("checkout", "-q", "main")
        # A no-slice item that is known: nothing done anywhere, so not_done, not exit 3.
        code, report, _ = landed(repo, "S05")
        self.assertEqual((code, report["reason"]), (1, "not_done"))
        # An unknown id is a usage error, not exit 3.
        code, out, _ = repo.run("verify", "--landed", "S99", "--json")
        self.assertEqual((code, json.loads(out)["error"]["code"]), (2, "no_such_item"))
        # A readable branch that has it done is still found beside the broken one.
        repo._git("checkout", "-q", "-b", "readable")
        repo.run("done", "S05", "--render")
        repo.commit("S05 done")
        repo._git("checkout", "-q", "main")
        code, report, _ = landed(repo, "S05")
        self.assertEqual((code, report["reason"]), (1, "other_branch"))

  def test_BadId_IsRefused(self) -> None:
    with project() as repo:
      before = snapshot(repo)
      for bad in ("../index", "a/b", ""):
        with self.subTest(bad):
          code, out, _ = repo.run("verify", "--landed", bad, "--json")
          self.assertEqual((code, json.loads(out)["error"]["code"]), (2, "bad_id"))
      self.assertEqual(snapshot(repo), before)

  def test_Usage_NotAGitRepository_IsRefused(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      support.make_mini(repo)
      repo.run("init")
      repo.run("migrate", "--from", "docs/slices")
      code, out, _ = repo.run("verify", "--landed", "S01", "--json")
      self.assertEqual((code, json.loads(out)["error"]["code"]), (2, "not_git"))

  def test_Usage_OnWithoutLanded_IsRefused(self) -> None:
    with project() as repo:
      code, _, err = repo.run("verify", "--on", "main")
      self.assertEqual(code, 2)
      self.assertIn("--landed", err)

  def test_ReadOnly_SlicerAndIndexUnchanged(self) -> None:
    with project() as repo:
      repo._git("checkout", "-q", "-b", "feature")
      repo.run("done", "S02", "--render")
      repo.commit("S02 done")
      repo.run("add", "uncommitted idea")
      before = snapshot(repo)
      codes = []
      for args in (("S01",), ("S02", "--on", "main"), ("S02", "--on", "feature"), ("S06",)):
        for extra in ((), ("--json", "--lean")):
          codes.append(repo.run("verify", "--landed", *args, *extra)[0])
      # Every outcome is exercised (landed, not landed, usage) so none skips a write.
      self.assertEqual(codes, [0, 0, 1, 1, 0, 0, 2, 2])
      self.assertEqual(snapshot(repo), before)

  def test_Vcs_ReadForms_AreTheOnlyShowAndMergeBaseAllowed(self) -> None:
    with project() as repo:
      sha = repo._git("rev-parse", "HEAD").stdout.strip()
      shown = vcs._run(repo.root, "show", f"{sha}:./.slicer/index.json")
      self.assertEqual(shown.returncode, 0, shown.stderr)
      base = vcs._run(repo.root, "merge-base", "--is-ancestor", sha, sha)
      self.assertEqual(base.returncode, 0, base.stderr)
      for forbidden in (
        ("show", sha),
        ("show", "--stat", f"{sha}:./x"),
        ("show", f"{sha}:./a", f"{sha}:./b"),
        ("merge-base", sha, sha),
        ("merge-base", "--is-ancestor", "--all", sha),
        ("merge-base", "--octopus", sha, sha),
      ):
        with self.subTest(forbidden):
          with self.assertRaises(StateError):
            vcs._run(repo.root, *forbidden)


if __name__ == "__main__":
  unittest.main()
