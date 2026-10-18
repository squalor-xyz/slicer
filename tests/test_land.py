"""`slicer land ID --branch BRANCH --note TEXT` checks, marks done, and prints what is left (s327).

Every test builds a fixture that is its own git repo (the suite refuses writes into this
repository's common dir). The slice's branch lives in a sibling worktree outside the
fixture, as an implementer's does. `land` must refuse before it writes anything, so the
refusal tests compare the whole `.slicer/` tree before and after.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import support  # noqa: F401 -- puts src/ on sys.path
from slicer import vcs

BRANCH = "feature/s1-thing"
NOTE = "Verified outcome"


class LandCase(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo(git=True)
    self.addCleanup(repo.close)
    self.enterContext(support.isolated_discovery(repo.root))
    self.assertEqual(repo.run("init")[0], 0)
    self.assertEqual(repo.run("add", "one")[0], 0)
    repo.commit("base")
    return repo

  def worktree(self, repo: support.TempRepo, branch: str = BRANCH, name: str = "wt") -> Path:
    """A sibling checkout, in a directory called `name`, on a new `branch` with one commit."""
    parent = tempfile.TemporaryDirectory(prefix="slicer-land-wt-")
    self.addCleanup(parent.cleanup)
    path = Path(parent.name).resolve() / name
    made = repo._git("worktree", "add", "-q", "-b", branch, str(path), "HEAD")
    self.assertEqual(made.returncode, 0, made.stderr)
    (path / "work.txt").write_text("work\n", encoding="utf-8")
    for args in (("add", "work.txt"), ("-c", "user.email=t@example.invalid",
                                       "-c", "user.name=T", "commit", "-q", "-m", "work")):
      done = repo._git("-C", str(path), *args)
      self.assertEqual(done.returncode, 0, done.stderr)
    return path

  def merge(self, repo: support.TempRepo, branch: str = BRANCH) -> None:
    done = repo._git("merge", "-q", "--no-ff", "-m", f"Merge {branch}", branch)
    self.assertEqual(done.returncode, 0, done.stderr)

  def snapshot(self, repo: support.TempRepo) -> dict[str, bytes]:
    base = repo.root / ".slicer"
    return {str(p.relative_to(base)): p.read_bytes() for p in sorted(base.rglob("*")) if p.is_file()}

  def land(self, repo: support.TempRepo, *extra: str, branch: str = BRANCH) -> tuple[int, str, str]:
    return repo.run("land", "S01", "--branch", branch, "--note", NOTE, *extra)

  def status(self, repo: support.TempRepo) -> str:
    return json.loads(repo.run("show", "S01", "--json", "--lean")[1])["status"]

  def refused(self, repo: support.TempRepo, code: int, error: str, *extra: str,
              branch: str = BRANCH) -> dict:
    before = self.snapshot(repo)
    got, out, err = self.land(repo, "--json", *extra, branch=branch)
    self.assertEqual(got, code, f"{out} {err}")
    payload = json.loads(out)["error"]
    self.assertEqual(payload["code"], error)
    self.assertEqual(self.snapshot(repo), before, "a refused land wrote something")
    return payload


class LandTests(LandCase):
  def test_Land_NotARepo_InvalidBranchNamesRepository(self) -> None:
    repo = support.TempRepo()
    self.addCleanup(repo.close)
    self.enterContext(support.isolated_discovery(repo.root))
    self.assertEqual(repo.run("init")[0], 0)
    self.assertEqual(repo.run("add", "one")[0], 0)
    payload = self.refused(repo, 2, "invalid_branch")
    self.assertIn("not a git repository", payload["message"])
    self.assertIn(str(repo.root), payload["message"])

  def test_Land_BranchNotMerged_NotMergedAndNothingWritten(self) -> None:
    repo = self.repo()
    self.worktree(repo)  # a commit the branch holds and HEAD does not
    payload = self.refused(repo, 2, "not_merged")
    self.assertIn(BRANCH, payload["message"])
    self.assertEqual(self.status(repo), "open")

  def test_Land_ActiveRelayRun_RunActive(self) -> None:
    repo = self.repo()
    self.worktree(repo)
    self.merge(repo)
    run = json.loads(repo.run("relay-run", "begin", "S01", "--json")[1])["run"]["id"]
    payload = self.refused(repo, 2, "run_active")
    self.assertIn(run, payload["message"])
    self.assertEqual(self.status(repo), "open")

  def test_Land_RunActiveOnAnotherItem_StillLands(self) -> None:
    repo = self.repo()
    self.assertEqual(repo.run("add", "two")[0], 0)
    self.worktree(repo)
    self.merge(repo)
    self.assertEqual(repo.run("relay-run", "begin", "S02", "--json")[0], 0)
    code, out, err = self.land(repo)
    self.assertEqual((code, err), (0, ""), out)
    self.assertEqual(self.status(repo), "done")

  def test_Land_EndedRelayRun_StillLands(self) -> None:
    repo = self.repo()
    self.worktree(repo)
    self.merge(repo)
    run = json.loads(repo.run("relay-run", "begin", "S01", "--json")[1])["run"]["id"]
    self.assertEqual(repo.run("relay-run", "end", run, "--outcome", "pass")[0], 0)
    code, out, err = self.land(repo)
    self.assertEqual((code, err), (0, ""), out)
    self.assertEqual(self.status(repo), "done")

  def test_Land_Merged_MarksDoneAndRenders(self) -> None:
    repo = self.repo()
    self.worktree(repo)
    self.merge(repo)
    code, out, err = self.land(repo)
    self.assertEqual((code, err), (0, ""), out)
    self.assertEqual(self.status(repo), "done")
    log = json.loads(repo.run("log", "--json", "--lean")[1])
    self.assertIn(NOTE, json.dumps(log))
    # The render is current and check passes: the same effect as `done --render --check`.
    self.assertEqual(repo.run("check")[0], 0)
    self.assertIn("rendered", out)

  def test_Land_Merged_JsonPayloadNamesItemBranchAndCommands(self) -> None:
    repo = self.repo()
    path = self.worktree(repo)
    self.merge(repo)
    code, out, err = self.land(repo, "--json")
    self.assertEqual((code, err), (0, ""), out)
    payload = json.loads(out)
    self.assertEqual(list(payload), ["item", "branch", "tip", "worktree", "commands"])
    self.assertEqual((payload["item"]["id"], payload["item"]["status"]), ("S01", "done"))
    self.assertEqual(payload["branch"], BRANCH)
    self.assertEqual(payload["tip"], vcs.resolve(repo.root, BRANCH))
    self.assertEqual(payload["worktree"], str(path))
    self.assertEqual(payload["commands"], [
      f'git -C {repo.root} add -A .slicer && git -C {repo.root} commit -m "S01 done"',
      f"git -C {repo.root} worktree remove {path}",
      f"git -C {repo.root} branch -d {BRANCH}",
    ])

  def test_Land_PrintsCommitWorktreeRemoveAndBranchDelete(self) -> None:
    repo = self.repo()
    path = self.worktree(repo)
    self.merge(repo)
    code, out, err = self.land(repo)
    self.assertEqual((code, err), (0, ""), out)
    self.assertEqual(out.splitlines()[-3:], [
      f'git -C {repo.root} add -A .slicer && git -C {repo.root} commit -m "S01 done"',
      f"git -C {repo.root} worktree remove {path}",
      f"git -C {repo.root} branch -d {BRANCH}",
    ])

  def test_Land_NoWorktreeOnBranch_OmitsWorktreeRemove(self) -> None:
    repo = self.repo()
    path = self.worktree(repo)
    self.merge(repo)
    # The branch outlives its checkout: remove the worktree, keep the branch.
    self.assertEqual(repo._git("worktree", "remove", "--force", str(path)).returncode, 0)
    code, out, err = self.land(repo, "--json")
    self.assertEqual((code, err), (0, ""), out)
    payload = json.loads(out)
    self.assertIsNone(payload["worktree"])
    self.assertEqual(payload["commands"], [
      f'git -C {repo.root} add -A .slicer && git -C {repo.root} commit -m "S01 done"',
      f"git -C {repo.root} branch -d {BRANCH}",
    ])

  def test_WorktreeOnBranch_OtherCheckoutListedFirst_NamesTheOneOnTheBranch(self) -> None:
    # Git's listing order is its own, so the order is fixed here rather than hoped for.
    listing = (
      "worktree /repo\nHEAD aaa\nbranch refs/heads/main\n\n"
      "worktree /other\nHEAD bbb\nbranch refs/heads/feature/other\n\n"
      "worktree /detached\nHEAD ccc\ndetached\n\n"
      "worktree /slice\nHEAD ddd\nbranch refs/heads/feature/s1-thing\n"
    )
    done = subprocess.CompletedProcess([], 0, stdout=listing, stderr="")
    with patch.object(vcs, "_run", return_value=done), \
         patch.object(vcs, "_current_worktree", return_value=Path("/repo")):
      self.assertEqual(vcs.worktree_on_branch(Path("/repo"), BRANCH), Path("/slice"))
      self.assertEqual(vcs.worktree_on_branch(Path("/repo"), "refs/heads/" + BRANCH), Path("/slice"))
      self.assertIsNone(vcs.worktree_on_branch(Path("/repo"), "feature/s1"))
      self.assertIsNone(vcs.worktree_on_branch(Path("/repo"), "main"))  # this checkout's own

  def test_Land_OtherWorktreeOnAnotherBranch_RemovesTheOneOnTheBranch(self) -> None:
    # Git lists checkouts in an order of its own; naming them both ways round puts the
    # slice's checkout before the other one in one of the two runs.
    for slice_dir, other_dir in (("a", "z"), ("z", "a")):
      with self.subTest(slice_dir=slice_dir):
        repo = self.repo()
        self.worktree(repo, "feature/other", other_dir)
        path = self.worktree(repo, BRANCH, slice_dir)
        self.merge(repo)
        code, out, err = self.land(repo, "--json")
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(json.loads(out)["worktree"], str(path))

  def test_Land_BranchIsCurrentHead_Refuses(self) -> None:
    repo = self.repo()
    here = repo._git("branch", "--show-current").stdout.strip()
    self.refused(repo, 2, "invalid_branch", branch=here)

  def test_Land_BranchIsCurrentHeadWithSameNamedTag_Refuses(self) -> None:
    repo = self.repo()
    here = repo._git("branch", "--show-current").stdout.strip()
    self.assertEqual(repo._git("tag", here).returncode, 0)
    self.refused(repo, 2, "invalid_branch", branch=here)

  def test_Land_TagNotBranch_Refuses(self) -> None:
    repo = self.repo()
    self.assertEqual(repo._git("tag", "land-tag").returncode, 0)
    self.refused(repo, 2, "invalid_branch", branch="land-tag")

  def test_Land_OtherRoot_PrintsRootAnchoredAdd(self) -> None:
    repo = self.repo()
    self.worktree(repo)
    self.merge(repo)
    # TempRepo.run passes --root while the process stays in the launch checkout.
    code, out, err = self.land(repo, "--json")
    self.assertEqual((code, err), (0, ""), out)
    command = json.loads(out)["commands"][0]
    self.assertEqual(shlex.split(command)[:6],
                     ["git", "-C", str(repo.root), "add", "-A", ".slicer"])

  def test_Land_Owner_RecordedOnDone(self) -> None:
    repo = self.repo()
    self.worktree(repo)
    self.merge(repo)
    code, out, err = self.land(repo, "--owner", "Landing Owner")
    self.assertEqual((code, err), (0, ""), out)
    log = json.loads(repo.run("log", "--json", "--lean")[1])
    done = [entry for entry in log if entry["action"] == "status"]
    self.assertEqual([entry["by"] for entry in done], ["Landing Owner"])

  def test_Land_UnknownBranch_InvalidBranch(self) -> None:
    repo = self.repo()
    payload = self.refused(repo, 2, "invalid_branch", branch="feature/nope")
    self.assertIn("feature/nope", payload["message"])
    self.assertEqual(self.status(repo), "open")

  def test_Land_NoteMissing_Usage(self) -> None:
    repo = self.repo()
    code, out, err = repo.run("land", "S01", "--branch", BRANCH, "--json")
    self.assertEqual(code, 2, f"{out} {err}")
    self.assertIn("--note", json.loads(out)["error"]["message"])
    self.assertEqual(self.status(repo), "open")


if __name__ == "__main__":
  unittest.main()
