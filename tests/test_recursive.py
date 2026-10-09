"""`-r/--recursive`: one read-only result per slicer project at or below a directory."""

from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import support

from slicer import store
from slicer.cli import main


def mark(repo: support.TempRepo, rel: str) -> Path:
  """Make `rel` look like a project to discovery, without running init."""
  repo.write(f"{rel}/.slicer/config.json" if rel else ".slicer/config.json", "{}\n")
  return repo.root / rel


def slicer(*argv: str) -> tuple[int, str, str]:
  out, err = io.StringIO(), io.StringIO()
  with redirect_stdout(out), redirect_stderr(err):
    code = main(list(argv))
  return code, out.getvalue(), err.getvalue()


def make_project(path: Path, *titles: str) -> None:
  """A real project at `path` holding one item per title."""
  path.mkdir(parents=True, exist_ok=True)
  code, _, err = slicer("--root", str(path), "init")
  assert code == 0, err
  for title in titles:
    code, _, err = slicer("--root", str(path), "add", title)
    assert code == 0, err
  code, _, err = slicer("--root", str(path), "render")
  assert code == 0, err


def relative(repo: support.TempRepo, found: list[Path]) -> list[str]:
  return [p.relative_to(repo.root).as_posix() for p in found]


class DiscoverAllTests(unittest.TestCase):
  def test_DiscoverAll_NestedProjects_ReturnsSortedPathsIncludingStart(self) -> None:
    with support.TempRepo() as repo:
      for rel in ("", "b", "a/x", "a-b", "c/sub/deep"):
        mark(repo, rel)
      (repo.root / "plain").mkdir()
      found, unreadable = store.discover_all(repo.root)
      self.assertTrue(all(p.is_absolute() for p in found))
      self.assertEqual(relative(repo, found), [".", "a/x", "a-b", "b", "c/sub/deep"])
      self.assertEqual(unreadable, [])

  def test_DiscoverAll_StartIsNotAProject_OmitsItAndNeverWalksUp(self) -> None:
    with support.TempRepo() as repo:
      mark(repo, "")
      mark(repo, "inner/p")
      found, unreadable = store.discover_all(repo.root / "inner")
      self.assertEqual([p.relative_to(repo.root).as_posix() for p in found], ["inner/p"])

  def test_DiscoverAll_SkipsDotDirsSymlinksAndNodeModules(self) -> None:
    with support.TempRepo() as repo, support.TempRepo() as elsewhere:
      mark(repo, "kept")
      mark(repo, ".hidden/p")
      mark(repo, ".worktrees/w")
      mark(repo, "node_modules/dep")
      mark(repo, "real")
      mark(elsewhere, "")
      try:
        os.symlink(elsewhere.root, repo.root / "outside", target_is_directory=True)
        os.symlink(repo.root / "real", repo.root / "alias", target_is_directory=True)
      except OSError as exc:  # pragma: no cover - symlinks unavailable
        self.skipTest(f"cannot create symlinks: {exc}")
      found, unreadable = store.discover_all(repo.root)
      self.assertEqual(relative(repo, found), ["kept", "real"])

  def test_DiscoverAll_ProjectInsideProject_FindsBoth(self) -> None:
    with support.TempRepo() as repo:
      mark(repo, "outer")
      mark(repo, "outer/vendor/inner")
      self.assertEqual(
        relative(repo, store.discover_all(repo.root)[0]), ["outer", "outer/vendor/inner"],
      )


class RecursiveCommandTests(unittest.TestCase):
  def fixture(self) -> support.TempRepo:
    """A scratch directory hidden from any enclosing project (TMPDIR may sit inside one)."""
    repo = support.TempRepo()
    self.addCleanup(repo.close)
    isolation = support.isolated_discovery(repo.root)
    isolation.__enter__()
    self.addCleanup(isolation.__exit__, None, None, None)
    return repo

  def test_CheckRecursive_OneProjectDrifts_ExitsOneAndNamesIt(self) -> None:
    repo = self.fixture()
    make_project(repo.root / "a", "Alpha")
    make_project(repo.root / "b", "Beta")
    code, out, err = repo.run("check", "-r", "--json")
    self.assertEqual(code, 0, err)
    self.assertEqual([p["path"] for p in json.loads(out)["projects"]], ["a", "b"])
    # Adding an item without rendering leaves b's render/ stale.
    code, _, err = slicer("--root", str(repo.root / "b"), "add", "Gamma")
    self.assertEqual(code, 0, err)
    code, out, _ = repo.run("check", "-r", "--json")
    self.assertEqual(code, 1)
    by_path = {p["path"]: p for p in json.loads(out)["projects"]}
    self.assertEqual(sorted(by_path), ["a", "b"])
    self.assertEqual(by_path["a"]["exit"], 0)
    self.assertEqual(by_path["b"]["exit"], 1)
    self.assertFalse(by_path["b"]["result"]["ok"])
    self.assertTrue(by_path["a"]["result"]["ok"])
    code, out, _ = repo.run("check", "-r")
    self.assertEqual(code, 1)
    self.assertEqual(out.splitlines()[0], "== a ==")
    self.assertIn("\n\n== b ==\n", out)

  def test_NextRecursive_ReturnsOneItemPerProject(self) -> None:
    repo = self.fixture()
    make_project(repo.root / "a", "Alpha one", "Alpha two")
    make_project(repo.root / "b" / "deep", "Beta one")
    code, out, err = repo.run("next", "-r", "--json", "--lean")
    self.assertEqual(code, 0, err)
    projects = json.loads(out)["projects"]
    self.assertEqual([p["path"] for p in projects], ["a", "b/deep"])
    self.assertEqual([p["exit"] for p in projects], [0, 0])
    self.assertEqual(
      [p["result"]["title"] for p in projects], ["Alpha one", "Beta one"],
    )
    # Each result is byte-for-byte what the command prints in that project alone.
    for entry in projects:
      code, alone, err = slicer("--root", str(repo.root / entry["path"]), "next", "--json", "--lean")
      self.assertEqual(code, 0, err)
      self.assertEqual(entry["result"], json.loads(alone))
      self.assertNotIn("depends_on", entry["result"])  # --lean applies inside each result
    # Reading only: nothing was claimed.
    for rel in ("a", "b/deep"):
      state = store.load(repo.root / rel)
      self.assertEqual({i.status for i in state.index.items}, {state.config.open_status})
    code, text, _ = repo.run("next", "-r")
    self.assertEqual(code, 0)
    self.assertIn("== a ==", text)
    self.assertIn("== b/deep ==", text)

  def test_Recursive_NoProjectFound_ExitsTwoWithCode(self) -> None:
    repo = self.fixture()
    (repo.root / "empty").mkdir()
    for command in ("list", "check", "status", "stats", "next"):
      with self.subTest(command=command):
        code, out, err = repo.run(command, "-r", "--json")
        self.assertEqual(code, 2, err)
        error = json.loads(out)["error"]
        self.assertEqual(error["code"], "no_project")
        self.assertIn("slicer init", err)

  def test_Recursive_WithStart_RefusedAsUsage(self) -> None:
    repo = self.fixture()
    make_project(repo.root / "a", "Alpha")
    state_before = store.load(repo.root / "a").index.to_dict()
    code, _, err = repo.run("next", "-r", "--json")
    self.assertEqual(code, 0, err)  # the flag itself is accepted; only the combination is not
    for argv in (
      ("next", "-r", "--start"),
      ("next", "--recursive", "--start", "--render"),
      ("next", "-r", "--render"),
      ("next", "-r", "--owner", "me"),
    ):
      with self.subTest(argv=argv):
        code, out, _ = repo.run(*argv, "--json")
        self.assertEqual(code, 2)
        error = json.loads(out)["error"]
        self.assertEqual(error["code"], "usage")
        self.assertIn("read-only", error["message"])
        self.assertEqual(store.load(repo.root / "a").index.to_dict(), state_before)

  def test_Recursive_OneProjectCorrupt_ReportsItAndContinues(self) -> None:
    repo = self.fixture()
    make_project(repo.root / "a", "Alpha")
    make_project(repo.root / "b", "Beta")
    make_project(repo.root / "c", "Gamma")
    make_project(repo.root / "d")  # no items: its empty result must survive the envelope
    (repo.root / "b" / ".slicer" / "index.json").write_text("garbage{", encoding="utf-8")
    code, out, _ = repo.run("list", "-r", "--json", "--lean")
    self.assertEqual(code, 3)
    projects = json.loads(out)["projects"]
    self.assertEqual([p["path"] for p in projects], ["a", "b", "c", "d"])
    self.assertEqual([p["exit"] for p in projects], [0, 3, 0, 0])
    self.assertEqual(projects[3]["result"], [])
    self.assertEqual(projects[1]["error"]["code"], "corrupt")
    self.assertIn("index.json", projects[1]["error"]["message"])
    self.assertNotIn("result", projects[1])
    self.assertEqual(projects[0]["result"][0]["title"], "Alpha")
    self.assertEqual(projects[2]["result"][0]["title"], "Gamma")
    code, text, _ = repo.run("list", "-r")
    self.assertEqual(code, 3)
    self.assertIn("== b ==", text)
    self.assertIn("Gamma", text)

  def test_Recursive_StartInsideAProject_DoesNotWalkUp(self) -> None:
    with support.TempRepo() as repo:  # deliberately not isolated: the parent project must be visible
      make_project(repo.root / "p", "Alpha")
      inner = repo.root / "p" / "sub" / "dir"
      inner.mkdir(parents=True)
      self.assertEqual(store.discover(inner), repo.root / "p")  # the enclosing project is there to find
      for start in (inner, repo.root / "p" / "sub"):
        with self.subTest(start=start.name):
          code, out, err = slicer("--root", str(start), "list", "-r", "--json")
          self.assertEqual(code, 2, err)
          self.assertEqual(json.loads(out)["error"]["code"], "no_project")

  def test_Recursive_UnreadableDirectories_ReportedAsIoAndOthersStillRun(self) -> None:
    if os.geteuid() == 0:
      self.skipTest("root ignores directory permissions")
    repo = self.fixture()
    make_project(repo.root, "Root")  # the start is a project, so a stray fallback upward would show it
    make_project(repo.root / "a", "Alpha")
    make_project(repo.root / "b", "Beta")  # .slicer/ made unreadable below
    make_project(repo.root / "z", "Zeta")
    (repo.root / "locked").mkdir()
    for path in (repo.root / "b" / ".slicer", repo.root / "locked"):
      self.addCleanup(os.chmod, path, 0o755)
      os.chmod(path, 0)
    code, out, err = repo.run("list", "-r", "--json", "--lean")
    self.assertEqual(code, 3, err)
    projects = json.loads(out)["projects"]
    self.assertEqual([p["path"] for p in projects], [".", "a", "b", "locked", "z"])
    self.assertEqual([p["exit"] for p in projects], [0, 0, 3, 3, 0])
    self.assertEqual(projects[0]["result"][0]["title"], "Root")
    projects = projects[1:]
    for entry in (projects[1], projects[2]):
      self.assertEqual(entry["error"]["code"], "io")
      self.assertNotIn("result", entry)  # no handler ran, so nothing was read from a parent either
    self.assertEqual(projects[0]["result"][0]["title"], "Alpha")
    self.assertEqual(projects[3]["result"][0]["title"], "Zeta")
    code, text, _ = repo.run("list", "-r")
    self.assertEqual(code, 3)
    self.assertIn("== locked ==\nerror: ", text)
    self.assertIn("== b ==\nerror: ", text)

  def test_Recursive_MissingOrFileRoot_ExitsTwoWithNoProject(self) -> None:
    repo = self.fixture()
    repo.write("afile", "not a directory\n")
    for name in ("missing", "afile"):
      with self.subTest(root=name):
        code, out, err = repo.run("--root", str(repo.root / name), "list", "-r", "--json")
        self.assertEqual(code, 2, err)
        body = json.loads(out)
        self.assertNotIn("projects", body)
        self.assertEqual(body["error"]["code"], "no_project")

  def test_Recursive_OnlyAnUnreadableDirectory_ReportsItInsteadOfNoProject(self) -> None:
    if os.geteuid() == 0:
      self.skipTest("root ignores directory permissions")
    repo = self.fixture()
    (repo.root / "locked").mkdir()
    self.addCleanup(os.chmod, repo.root / "locked", 0o755)
    os.chmod(repo.root / "locked", 0)
    code, out, err = repo.run("status", "-r", "--json")
    self.assertEqual(code, 3, err)
    entry = json.loads(out)["projects"][0]
    self.assertEqual((entry["path"], entry["exit"], entry["error"]["code"]), ("locked", 3, "io"))


if __name__ == "__main__":
  unittest.main()
