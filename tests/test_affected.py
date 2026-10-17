"""The affected-test map selects who ran a changed line and refuses a stale one."""

from __future__ import annotations

import ast
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import affected
from support import remove_temp_repo


DISCOVER = "python3 -m unittest discover -s tests -t tests"
SHA = "abc123"


def _load_sample(directory: Path):
  """A src/slicer module whose lines are the only ones these two tests run."""
  sample = directory / "src" / "slicer" / "sample.py"
  sample.parent.mkdir(parents=True)
  sample.write_text(
    "def only_a():\n"
    "  return 1\n"
    "\n"
    "def only_b():\n"
    "  return 2\n"
    "\n"
    "def shared():\n"
    "  return 3\n",
    encoding="utf-8",
  )
  spec = importlib.util.spec_from_file_location("affected_sample", sample)
  module = importlib.util.module_from_spec(spec)
  assert spec.loader is not None
  spec.loader.exec_module(module)
  return module


def _run(argv: list[str]) -> tuple[int, str, str]:
  out, err = io.StringIO(), io.StringIO()
  with redirect_stdout(out), redirect_stderr(err):
    code = affected.main(argv)
  return code, out.getvalue(), err.getvalue()


def _git(cwd: Path, *args: str) -> str:
  """Run git in `cwd` and return its stdout, stripped."""
  proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
  return proc.stdout.strip()


class AffectedTests(unittest.TestCase):
  def map_file(
    self,
    tests: dict[str, list[str]],
    sha: str = SHA,
    reads: dict[str, list[str]] | None = None,
  ) -> Path:
    directory = Path(tempfile.mkdtemp())
    self.addCleanup(shutil.rmtree, directory)
    path = directory / "affected-map.json"
    affected.write_map(path, sha, tests, reads, clean=True)
    return path

  def raw_map(self, document: dict) -> Path:
    """A map written by hand, so the select tests do not depend on `write_map`."""
    directory = Path(tempfile.mkdtemp())
    self.addCleanup(shutil.rmtree, directory)
    path = directory / "affected-map.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path

  def test_Record_TwoTests_MapsEachTestAndSelectsTheLinesItRan(self) -> None:
    directory = Path(tempfile.mkdtemp())
    self.addCleanup(shutil.rmtree, directory)
    module = _load_sample(directory)

    docs = directory / "docs"
    docs.mkdir()
    (docs / "alpha.md").write_text("a\n", encoding="utf-8")
    (docs / "zeta.md").write_text("z\n", encoding="utf-8")

    class Pair(unittest.TestCase):
      def test_a(self) -> None:
        module.only_a()
        module.shared()
        (docs / "zeta.md").read_text(encoding="utf-8")
        (docs / "alpha.md").read_text(encoding="utf-8")

      def test_b(self) -> None:
        module.only_b()
        module.shared()

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Pair)
    traced, reads = affected.trace(suite, directory)
    # Python 3.14 clears a suite after it runs, so the ids come from the map.
    by_method = {test_id.rsplit(".", 1)[-1]: test_id for test_id in traced}
    only_a = set(traced[by_method["test_a"]]) - set(traced[by_method["test_b"]])
    only_b = set(traced[by_method["test_b"]]) - set(traced[by_method["test_a"]])
    shared = set(traced[by_method["test_a"]]) & set(traced[by_method["test_b"]])
    self.assertTrue(only_a)
    self.assertTrue(only_b)
    self.assertTrue(shared)
    self.assertTrue(all(line.startswith("src/slicer/sample.py:") for line in only_a | only_b | shared))
    path = self.map_file(traced, reads=reads)
    text = path.read_text(encoding="utf-8")
    self.assertLess(text.index('"sha"'), text.index('"tests"'))
    self.assertLess(text.index('"tests"'), text.index('"reads"'))
    document = json.loads(text)
    self.assertEqual(document["sha"], SHA)
    self.assertEqual(document["reads"][by_method["test_a"]], ["docs/alpha.md", "docs/zeta.md"])
    self.assertEqual(document["reads"][by_method["test_b"]], [])
    self.assertEqual(list(document["reads"]), sorted(document["reads"]))

    def chosen(location: str) -> list[str]:
      file_path, line = location.rsplit(":", 1)
      code, out, err = _run([
        "--map", str(path), "--sha", SHA, "--lines", f"{file_path}:{line}-{line}",
      ])
      self.assertEqual(code, 0, err)
      self.assertNotIn(DISCOVER, err)
      return out.splitlines()

    self.assertEqual(chosen(sorted(only_a)[0]), [by_method["test_a"]])
    self.assertEqual(chosen(sorted(shared)[0]), sorted([by_method["test_a"], by_method["test_b"]]))

  @unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
  def test_Record_SymlinkedRoot_StillMapsLines(self) -> None:
    # macOS TMPDIR is under /var, a symlink to /private/var.
    directory = Path(tempfile.mkdtemp())
    self.addCleanup(shutil.rmtree, directory)
    real = directory / "real"
    real.mkdir()
    link = directory / "link"
    os.symlink(real, link, target_is_directory=True)
    module = _load_sample(link)

    class One(unittest.TestCase):
      def test_a(self) -> None:
        module.only_a()

    traced = affected.trace_suite(unittest.defaultTestLoader.loadTestsFromTestCase(One), link)
    lines = next(iter(traced.values()))
    self.assertTrue(lines)
    self.assertTrue(all(line.startswith("src/slicer/sample.py:") for line in lines))

  def test_TraceSuite_RestoresAnOuterTracer(self) -> None:
    directory = Path(tempfile.mkdtemp())
    self.addCleanup(shutil.rmtree, directory)
    module = _load_sample(directory)

    class Outer(unittest.TestCase):
      def test_wraps(self) -> None:
        module.only_a()

        class Inner(unittest.TestCase):
          def test_inner(self) -> None:
            module.only_b()

        affected.trace_suite(
          unittest.defaultTestLoader.loadTestsFromTestCase(Inner), directory,
        )
        module.shared()

    traced = affected.trace_suite(
      unittest.defaultTestLoader.loadTestsFromTestCase(Outer), directory,
    )
    lines = next(iter(traced.values()))
    self.assertIn("src/slicer/sample.py:2", lines)
    self.assertIn("src/slicer/sample.py:8", lines)

  def test_Select_ChangedTestModule_IncludesItsTestsWithoutAMapHit(self) -> None:
    path = self.map_file({"not.a.Real.test_one": ["src/slicer/ops.py:1"]})
    code, out, err = _run([
      "--map", str(path), "--sha", SHA, "--lines", "tests/test_affected.py:1-5",
    ])
    self.assertEqual(code, 0, err)
    selected = out.splitlines()
    self.assertIn(self.id(), selected)
    self.assertNotIn("not.a.Real.test_one", selected)

  def test_TraceSuite_TestReadsRepoFile_RecordsTheRead(self) -> None:
    directory = Path(tempfile.mkdtemp())
    self.addCleanup(shutil.rmtree, directory)
    outside = Path(tempfile.mkdtemp())
    self.addCleanup(shutil.rmtree, outside)
    inside = directory / "docs" / "guide.md"
    ignored = [directory / ".git" / "notes.md", directory / ".venv" / "notes.md"]
    for file in [inside, *ignored, outside / "other.md"]:
      file.parent.mkdir(parents=True, exist_ok=True)
      file.write_text("x\n", encoding="utf-8")
    opened = [inside, *ignored, outside / "other.md"]

    class Reader(unittest.TestCase):
      def test_reads(self) -> None:
        for file in opened:
          file.read_text(encoding="utf-8")
        # Opening a directory is not reading a file.
        os.close(os.open(directory / "docs", os.O_RDONLY))

      def test_reads_nothing(self) -> None:
        pass

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Reader)
    _, reads = affected.trace(suite, directory)
    by_method = {test_id.rsplit(".", 1)[-1]: test_id for test_id in reads}
    self.assertEqual(reads[by_method["test_reads"]], ["docs/guide.md"])
    self.assertEqual(reads[by_method["test_reads_nothing"]], [])

  def test_TraceSuite_ReadOutsideAnyTest_IsNotRecorded(self) -> None:
    directory = Path(tempfile.mkdtemp())
    self.addCleanup(shutil.rmtree, directory)
    inside = directory / "inside.md"
    outside = directory / "outside.md"
    for file in (inside, outside):
      file.write_text("x\n", encoding="utf-8")

    class Reader(unittest.TestCase):
      @classmethod
      def setUpClass(cls) -> None:
        outside.read_text(encoding="utf-8")

      def test_reads(self) -> None:
        inside.read_text(encoding="utf-8")

    result = affected._TracingResult(directory)
    unittest.defaultTestLoader.loadTestsFromTestCase(Reader).run(result)
    outside.read_text(encoding="utf-8")
    self.assertEqual(next(iter(result.reads().values())), ["inside.md"])

  def test_TraceSuite_FileWrittenBeforeItExists_IsRecordedForALaterReader(self) -> None:
    directory = Path(tempfile.mkdtemp())
    self.addCleanup(shutil.rmtree, directory)
    target = directory / "docs" / "new.md"
    target.parent.mkdir()

    class Pair(unittest.TestCase):
      def test_a_writes(self) -> None:
        target.write_text("x\n", encoding="utf-8")

      def test_b_reads(self) -> None:
        target.read_text(encoding="utf-8")

    _, reads = affected.trace(unittest.defaultTestLoader.loadTestsFromTestCase(Pair), directory)
    by_method = {test_id.rsplit(".", 1)[-1]: test_id for test_id in reads}
    self.assertEqual(reads[by_method["test_b_reads"]], ["docs/new.md"])

  def test_TraceSuite_NestedTraceInATest_KeepsRecordingTheOuterTest(self) -> None:
    directory = Path(tempfile.mkdtemp())
    self.addCleanup(shutil.rmtree, directory)
    for name in ("before.md", "inner.md", "after.md"):
      (directory / name).write_text("x\n", encoding="utf-8")

    class Inner(unittest.TestCase):
      def test_inner(self) -> None:
        (directory / "inner.md").read_text(encoding="utf-8")

    class Outer(unittest.TestCase):
      def test_wraps(self) -> None:
        (directory / "before.md").read_text(encoding="utf-8")
        affected.trace(unittest.defaultTestLoader.loadTestsFromTestCase(Inner), directory)
        (directory / "after.md").read_text(encoding="utf-8")

    _, reads = affected.trace(unittest.defaultTestLoader.loadTestsFromTestCase(Outer), directory)
    self.assertEqual(next(iter(reads.values())), ["after.md", "before.md"])

  def test_Select_ChangedMarkdownReadByATest_SelectsThatTest(self) -> None:
    path = self.raw_map({
      "sha": SHA,
      "tests": {"mod.T.test_reader": [], "mod.T.test_other": []},
      "reads": {"mod.T.test_reader": ["README.md"], "mod.T.test_other": ["docs/other.md"]},
    })
    code, out, err = _run(["--map", str(path), "--sha", SHA, "--lines", "README.md:1-1"])
    self.assertEqual(code, 0, err)
    self.assertEqual(out.splitlines(), ["mod.T.test_reader"])

  def test_Select_ChangedFileNoTestRead_SaysNothingToRun(self) -> None:
    path = self.raw_map({
      "sha": SHA,
      "tests": {"mod.T.test_reader": []},
      "reads": {"mod.T.test_reader": ["README.md"]},
    })
    code, out, err = _run(["--map", str(path), "--sha", SHA, "--lines", "docs/unread.md:1-1"])
    self.assertEqual(code, 0, err)
    self.assertEqual(out, "nothing to run\n")

  def test_Select_MapWithoutReads_RunsFullSuite(self) -> None:
    path = self.raw_map({"sha": SHA, "tests": {"mod.T.test_reader": ["src/slicer/ops.py:4"]}})
    code, out, err = _run(["--map", str(path), "--sha", SHA, "--lines", "README.md:1-1"])
    self.assertEqual(code, 2)
    self.assertEqual(out, "")
    self.assertIn("reads", err)
    self.assertIn(DISCOVER, err)

  def test_Select_ChangedTemplateUnderSrc_SelectsReaders(self) -> None:
    path = self.raw_map({
      "sha": SHA,
      "tests": {"mod.T.test_reader": ["src/slicer/ops.py:4"]},
      "reads": {"mod.T.test_reader": ["src/slicer/templates/page.html"]},
    })
    code, out, err = _run([
      "--map", str(path), "--sha", SHA, "--lines", "src/slicer/templates/page.html:1-1",
    ])
    self.assertEqual(code, 0, err)
    self.assertEqual(out.splitlines(), ["mod.T.test_reader"])
    code, out, err = _run([
      "--map", str(path), "--sha", SHA, "--lines", "src/slicer/templates/unread.html:1-1",
    ])
    self.assertEqual(code, 2)
    self.assertEqual(out, "")
    self.assertIn(DISCOVER, err)

  def test_Select_MissingMap_ExitsTwoAndNamesDiscover(self) -> None:
    missing = self.map_file({})
    missing.unlink()
    code, out, err = _run(["--map", str(missing), "--sha", SHA, "--lines", "docs/readme.md:1-1"])
    self.assertEqual(code, 2)
    self.assertEqual(out, "")
    self.assertIn(DISCOVER, err)

  def test_Select_UnknownSourceFile_ExitsTwoWithoutIds(self) -> None:
    path = self.map_file({"test_affected._Pair.test_a": ["src/slicer/ops.py:4"]})
    code, out, err = _run([
      "--map", str(path), "--sha", SHA, "--lines", "src/slicer/missing.py:1-3",
    ])
    self.assertEqual(code, 2)
    self.assertEqual(out, "")
    self.assertIn(DISCOVER, err)

  def test_Select_DocsChangelogAndSlicer_RunNothing(self) -> None:
    path = self.map_file({"test_affected._Pair.test_a": ["src/slicer/ops.py:4"]})
    for spec in ("docs/commands.md:1-2", "CHANGELOG.md:1-2", ".slicer/index.json:1-2"):
      with self.subTest(spec=spec):
        code, out, err = _run(["--map", str(path), "--sha", SHA, "--run", "--lines", spec])
        self.assertEqual(code, 0, err)
        self.assertEqual(out, "nothing to run\n")
        self.assertNotIn(DISCOVER, err)

  def test_Affected_Imports_StayInsideTheStandardLibrary(self) -> None:
    tree = ast.parse(Path(affected.__file__).read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
      if isinstance(node, ast.Import):
        imported.extend(alias.name.split(".")[0] for alias in node.names)
      elif isinstance(node, ast.ImportFrom) and node.module:
        imported.append(node.module.split(".")[0])
    self.assertNotIn("coverage", imported)
    self.assertNotIn("pytest", imported)
    self.assertNotIn("coverage", sys.modules)
    self.assertNotIn("pytest", sys.modules)

  def test_Select_WrongSha_ExitsTwoAndMatchingShaSelects(self) -> None:
    path = self.map_file({"mod.Class.test_a": ["src/slicer/ops.py:4"]}, sha="recorded")
    code, out, err = _run([
      "--map", str(path), "--sha", "other", "--lines", "src/slicer/ops.py:4-4",
    ])
    self.assertEqual(code, 2)
    self.assertEqual(out, "")
    self.assertIn(DISCOVER, err)
    code, out, err = _run([
      "--map", str(path), "--sha", "recorded", "--lines", "src/slicer/ops.py:4-4",
    ])
    self.assertEqual(code, 0, err)
    self.assertEqual(out.splitlines(), ["mod.Class.test_a"])

  def test_Select_SupportOrFixture_ExitsTwo(self) -> None:
    path = self.map_file({
      "test_affected._Pair.test_a": ["tests/support.py:10", "src/slicer/ops.py:4"],
    })
    for spec in ("tests/support.py:10-10", "tests/fixtures/legacy/sample.md:1-2"):
      with self.subTest(spec=spec):
        code, out, err = _run(["--map", str(path), "--sha", SHA, "--lines", spec])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn(DISCOVER, err)
        self.assertNotIn("test_a", out)

  def test_Select_UnreadableTestModule_ExitsTwo(self) -> None:
    path = self.map_file({"test_affected._Pair.test_a": ["src/slicer/ops.py:4"]})
    code, out, err = _run([
      "--map", str(path), "--sha", SHA, "--lines", "tests/test_no_such_module.py:1-1",
    ])
    self.assertEqual(code, 2)
    self.assertEqual(out, "")
    self.assertIn(DISCOVER, err)

  def test_RunSuite_PassAndFail_UseUnittestStatus(self) -> None:
    class _Ok(unittest.TestCase):
      def test_ok(self) -> None:
        pass

    class _Bad(unittest.TestCase):
      def test_bad(self) -> None:
        self.fail("no")

    loader = unittest.defaultTestLoader
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
      self.assertEqual(affected.run_suite(loader.loadTestsFromTestCase(_Ok)), 0)
      self.assertEqual(affected.run_suite(loader.loadTestsFromTestCase(_Bad)), 1)

  def test_Run_SelectedIdNoLongerDefined_DroppedAndAsksForFullSuite(self) -> None:
    module = self.id().rsplit(".", 1)[0]
    renamed = module + ".test_Run_RenamedAway_NoLongerDefined"
    live = module + ".test_Diff_Hunks_UseOldNumbersForDeletionsAndNewForAdditions"
    path = self.map_file({renamed: ["src/slicer/ops.py:4"], live: ["src/slicer/ops.py:4"]})
    code, out, err = _run(["--map", str(path), "--sha", SHA, "--run", "--lines", "src/slicer/ops.py:4-4"])
    self.assertEqual(code, 2, err)
    self.assertEqual(out.splitlines(), sorted([renamed, live]))
    self.assertIn(f"{renamed} did not load", err)
    self.assertNotIn("ERROR: ", err)
    self.assertIn("Ran 1 test", err)
    self.assertIn(DISCOVER, err)

  def test_Run_AllSelectedIdsLoad_Unchanged(self) -> None:
    module = self.id().rsplit(".", 1)[0]
    first = module + ".test_Diff_Hunks_UseOldNumbersForDeletionsAndNewForAdditions"
    second = module + ".test_Affected_Imports_StayInsideTheStandardLibrary"
    path = self.map_file({first: ["src/slicer/ops.py:4"], second: ["src/slicer/ops.py:4"]})
    code, out, err = _run(["--map", str(path), "--sha", SHA, "--run", "--lines", "src/slicer/ops.py:4-4"])
    self.assertEqual(code, 0, err)
    self.assertEqual(out.splitlines(), sorted([first, second]))
    self.assertIn("Ran 2 tests", err)
    self.assertNotIn("did not load", err)
    self.assertNotIn(DISCOVER, err)

  def test_Run_SelectedModuleFailsToImport_NamesTheErrorAndRunsTheRest(self) -> None:
    directory = Path(tempfile.mkdtemp())
    self.addCleanup(shutil.rmtree, directory)
    (directory / "affected_probe_syntax.py").write_text("def broken(:\n", encoding="utf-8")
    (directory / "affected_probe_import.py").write_text("import affected_probe_no_such_module\n", encoding="utf-8")
    self.enterContext(patch.object(sys, "path", [str(directory), *sys.path]))
    module = self.id().rsplit(".", 1)[0]
    live = module + ".test_Diff_Hunks_UseOldNumbersForDeletionsAndNewForAdditions"
    syntax = "affected_probe_syntax.C.test_a"
    importing = "affected_probe_import.C.test_a"
    path = self.map_file({
      syntax: ["src/slicer/ops.py:4"], importing: ["src/slicer/ops.py:4"], live: ["src/slicer/ops.py:4"],
    })
    code, out, err = _run(["--map", str(path), "--sha", SHA, "--run", "--lines", "src/slicer/ops.py:4-4"])
    self.assertEqual(code, 2, err)
    self.assertIn(f"{syntax} did not load", err)
    self.assertIn("SyntaxError", err)
    self.assertIn(f"{importing} did not load", err)
    self.assertIn("affected_probe_no_such_module", err)
    self.assertIn("Ran 1 test", err)
    self.assertIn(DISCOVER, err)

  def test_Run_DroppedIdAndFailingRest_SaysTheRunFailed(self) -> None:
    directory = Path(tempfile.mkdtemp())
    self.addCleanup(shutil.rmtree, directory)
    (directory / "affected_probe_fail.py").write_text(
      "import unittest\n"
      "class C(unittest.TestCase):\n"
      "  def test_a(self):\n"
      "    self.fail('probe')\n",
      encoding="utf-8",
    )
    self.enterContext(patch.object(sys, "path", [str(directory), *sys.path]))
    module = self.id().rsplit(".", 1)[0]
    renamed = module + ".test_Run_RenamedAway_NoLongerDefined"
    failing = "affected_probe_fail.C.test_a"
    path = self.map_file({renamed: ["src/slicer/ops.py:4"], failing: ["src/slicer/ops.py:4"]})
    code, out, err = _run(["--map", str(path), "--sha", SHA, "--run", "--lines", "src/slicer/ops.py:4-4"])
    self.assertEqual(code, 2, err)
    self.assertIn("FAILED (failures=1)", err)
    self.assertIn("did not load and were not run and the selected tests that ran failed", err)

  def test_Diff_Hunks_UseOldNumbersForDeletionsAndNewForAdditions(self) -> None:
    changed = affected.parse_unified(
      "diff --git a/src/slicer/ops.py b/src/slicer/ops.py\n"
      "--- a/src/slicer/ops.py\n"
      "+++ b/src/slicer/ops.py\n"
      "@@ -10 +10 @@ def keep():\n"
      "-old\n"
      "+new\n"
      "@@ -20,0 +21 @@ def keep():\n"
      "+inserted\n"
      "diff --git a/src/slicer/gone.py b/src/slicer/gone.py\n"
      "--- a/src/slicer/gone.py\n"
      "+++ /dev/null\n"
      "@@ -3 +0,0 @@\n"
      "-drop\n"
    )
    self.assertEqual(changed["src/slicer/ops.py"], {10, 21})
    self.assertEqual(changed["src/slicer/gone.py"], {3})

  def test_WorkingTree_EditedAndUntrackedLines_ComeFromGit(self) -> None:
    directory = Path(tempfile.mkdtemp())
    self.addCleanup(remove_temp_repo, directory)
    subprocess.run(["git", "init", "-q"], cwd=directory, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=directory, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=directory, check=True)
    subprocess.run(["git", "config", "maintenance.auto", "false"], cwd=directory, check=True)
    source = directory / "src" / "slicer" / "ops.py"
    source.parent.mkdir(parents=True)
    source.write_text("one\ntwo\nthree\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/slicer/ops.py"], cwd=directory, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=directory, check=True)
    source.write_text("one\nTWO\nthree\n", encoding="utf-8")
    extra = directory / "src" / "slicer" / "new.py"
    extra.write_text("a\nb\n", encoding="utf-8")
    changed = affected.working_tree_lines(directory)
    self.assertIn(2, changed["src/slicer/ops.py"])
    self.assertEqual(changed["src/slicer/new.py"], {1, 2})

  def _git_root(self) -> Path:
    directory = Path(tempfile.mkdtemp())
    self.addCleanup(remove_temp_repo, directory)
    subprocess.run(["git", "init", "-q"], cwd=directory, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=directory, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=directory, check=True)
    subprocess.run(["git", "config", "maintenance.auto", "false"], cwd=directory, check=True)
    marker = directory / "marker.txt"
    marker.write_text("marker\n", encoding="utf-8")
    subprocess.run(["git", "add", "marker.txt"], cwd=directory, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=directory, check=True)
    return directory

  def _linked_worktree(self, main: Path) -> Path:
    """A second checkout of `main`'s repository, detached at the same HEAD."""
    directory = Path(tempfile.mkdtemp())
    self.addCleanup(remove_temp_repo, directory)
    worktree = directory / "wt"
    _git(main, "worktree", "add", "-q", "--detach", str(worktree))
    return worktree

  def _at_checkout(self, worktree: Path) -> None:
    """Make a bare `affected.py` call see `worktree` as its checkout."""
    self.enterContext(patch.object(affected, "ROOT", worktree))
    self.enterContext(
      patch.object(affected, "DEFAULT_MAP", worktree / ".venv" / "affected-map.json"),
    )

  def _commit(self, repo: Path, message: str) -> str:
    """An empty commit in `repo`, returning the SHA it moves HEAD to."""
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")

  def test_Select_NoLocalMap_UsesSiblingMapForSameHead(self) -> None:
    main = self._git_root()
    sibling = main / ".venv" / "affected-map.json"
    affected.write_map(sibling, _git(main, "rev-parse", "HEAD"), {"mod.T.test_a": ["src/slicer/ops.py:4"]}, clean=True)
    before = sibling.read_bytes()
    worktree = self._linked_worktree(main)
    self._at_checkout(worktree)
    code, out, err = _run(["--lines", "src/slicer/ops.py:4-4"])
    self.assertEqual(code, 0, err)
    self.assertEqual(out.splitlines(), ["mod.T.test_a"])
    self.assertIn(str(sibling.resolve()), err)
    self.assertEqual(sibling.read_bytes(), before)
    self.assertFalse((worktree / ".venv").exists())
    code, out, err = _run(["--map", str(worktree / "absent.json"), "--lines", "src/slicer/ops.py:4-4"])
    self.assertEqual(code, 2)
    self.assertEqual(out, "")
    self.assertNotIn(str(sibling.resolve()), err)

  def test_Select_NoLocalMap_SiblingMapOtherSha_AsksForFullSuite(self) -> None:
    # The sibling records a descendant of this worktree's HEAD, which is not an
    # ancestor of HEAD, so the map is not borrowed.
    main = self._git_root()
    worktree = self._linked_worktree(main)
    _git(main, "commit", "-q", "--allow-empty", "-m", "next")
    sibling = main / ".venv" / "affected-map.json"
    affected.write_map(sibling, _git(main, "rev-parse", "HEAD"), {"mod.T.test_a": ["src/slicer/ops.py:4"]}, clean=True)
    self._at_checkout(worktree)
    code, out, err = _run(["--lines", "src/slicer/ops.py:4-4"])
    self.assertEqual(code, 2)
    self.assertEqual(out, "")
    self.assertIn(DISCOVER, err)
    self.assertIn("no other worktree has a readable, clean map", err)
    self.assertNotIn(str(sibling.resolve()), err)

  def test_Select_LocalMapPresent_IgnoresSiblings(self) -> None:
    main = self._git_root()
    head = _git(main, "rev-parse", "HEAD")
    sibling = main / ".venv" / "affected-map.json"
    affected.write_map(sibling, head, {"mod.T.test_sibling": ["src/slicer/ops.py:4"]}, clean=True)
    worktree = self._linked_worktree(main)
    affected.write_map(worktree / ".venv" / "affected-map.json", head, {"mod.T.test_local": ["src/slicer/ops.py:4"]}, clean=True)
    self._at_checkout(worktree)
    code, out, err = _run(["--lines", "src/slicer/ops.py:4-4"])
    self.assertEqual(code, 0, err)
    self.assertEqual(out.splitlines(), ["mod.T.test_local"])
    self.assertNotIn(str(sibling.resolve()), err)

  def test_Select_LocalMapRecordedDirty_StillUsedAtHead(self) -> None:
    # A worktree's own map ignores `clean`: it is used at its own HEAD either way.
    main = self._git_root()
    head = _git(main, "rev-parse", "HEAD")
    worktree = self._linked_worktree(main)
    affected.write_map(worktree / ".venv" / "affected-map.json", head,
                       {"mod.T.test_local": ["src/slicer/ops.py:4"]}, clean=False)
    self._at_checkout(worktree)
    code, out, err = _run(["--lines", "src/slicer/ops.py:4-4"])
    self.assertEqual(code, 0, err)
    self.assertEqual(out.splitlines(), ["mod.T.test_local"])

  def test_Select_NoLocalMap_SiblingDirtyUnderSrc_AsksForFullSuite(self) -> None:
    main = self._git_root()
    source = main / "src" / "slicer" / "ops.py"
    source.parent.mkdir(parents=True)
    source.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    _git(main, "add", "src/slicer/ops.py")
    _git(main, "commit", "-q", "-m", "source")
    sibling = main / ".venv" / "affected-map.json"
    affected.write_map(sibling, _git(main, "rev-parse", "HEAD"), {"mod.T.test_a": ["src/slicer/ops.py:4"]}, clean=True)
    source.write_text("one\ntwo\nthree\nFOUR\n", encoding="utf-8")
    worktree = self._linked_worktree(main)
    self._at_checkout(worktree)
    code, out, err = _run(["--lines", "src/slicer/ops.py:4-4"])
    self.assertEqual(code, 2)
    self.assertEqual(out, "")
    self.assertIn(DISCOVER, err)
    self.assertIn("no other worktree has a readable, clean map", err)
    self.assertNotIn(str(sibling.resolve()), err)

  def test_Select_NoLocalMap_SiblingDirtyOnlyUnderSlicer_StillBorrowsItsMap(self) -> None:
    main = self._git_root()
    state = main / ".slicer" / "index.json"
    state.parent.mkdir()
    state.write_text("{}\n", encoding="utf-8")
    _git(main, "add", ".slicer/index.json")
    _git(main, "commit", "-q", "-m", "state")
    sibling = main / ".venv" / "affected-map.json"
    affected.write_map(sibling, _git(main, "rev-parse", "HEAD"), {"mod.T.test_a": ["src/slicer/ops.py:4"]}, clean=True)
    state.write_text('{"edited": true}\n', encoding="utf-8")
    worktree = self._linked_worktree(main)
    self._at_checkout(worktree)
    code, out, err = _run(["--lines", "src/slicer/ops.py:4-4"])
    self.assertEqual(code, 0, err)
    self.assertEqual(out.splitlines(), ["mod.T.test_a"])
    self.assertIn(str(sibling.resolve()), err)

  def test_Select_NoLocalMap_SiblingProbeFails_AsksForFullSuite(self) -> None:
    main = self._git_root()
    head = _git(main, "rev-parse", "HEAD")
    broken = self._linked_worktree(main)
    broken_map = broken / ".venv" / "affected-map.json"
    affected.write_map(broken_map, head, {"mod.T.test_a": ["src/slicer/ops.py:4"]}, clean=True)
    index = broken / _git(broken, "rev-parse", "--git-path", "index")
    index.write_bytes(b"not an index")
    worktree = self._linked_worktree(main)
    self._at_checkout(worktree)
    code, out, err = _run(["--lines", "src/slicer/ops.py:4-4"])
    self.assertEqual(code, 2)
    self.assertEqual(out, "")
    self.assertIn("no other worktree has a readable, clean map", err)
    self.assertNotIn(str(broken_map.resolve()), err)

  def test_Select_NoLocalMap_SiblingProbeCannotRun_AsksForFullSuite(self) -> None:
    main = self._git_root()
    sibling = self._linked_worktree(main)
    affected.write_map(sibling / ".venv" / "affected-map.json", _git(main, "rev-parse", "HEAD"),
                       {"mod.T.test_a": ["src/slicer/ops.py:4"]}, clean=True)
    worktree = self._linked_worktree(main)
    self._at_checkout(worktree)
    real_run = subprocess.run

    def without_status(args, **kwargs):
      if "status" in args:
        raise OSError("git cannot run")
      return real_run(args, **kwargs)

    with patch.object(affected.subprocess, "run", side_effect=without_status):
      code, out, err = _run(["--lines", "src/slicer/ops.py:4-4"])
    self.assertEqual(code, 2)
    self.assertEqual(out, "")
    self.assertIn("no other worktree has a readable, clean map", err)

  def test_Select_NoLocalMap_TwoSiblingMaps_UsesMainCheckoutFirst(self) -> None:
    main = self._git_root()
    head = _git(main, "rev-parse", "HEAD")
    main_map = main / ".venv" / "affected-map.json"
    affected.write_map(main_map, head, {"mod.T.test_main": ["src/slicer/ops.py:4"]}, clean=True)
    other = self._linked_worktree(main)
    other_map = other / ".venv" / "affected-map.json"
    affected.write_map(other_map, head, {"mod.T.test_other": ["src/slicer/ops.py:4"]}, clean=True)
    worktree = self._linked_worktree(main)
    self._at_checkout(worktree)
    code, out, err = _run(["--lines", "src/slicer/ops.py:4-4"])
    self.assertEqual(code, 0, err)
    self.assertEqual(out.splitlines(), ["mod.T.test_main"])
    self.assertIn(str(main_map.resolve()), err)
    self.assertNotIn(str(other_map.resolve()), err)

  def test_SiblingMap_RecordedAtAncestor_IsBorrowed(self) -> None:
    main = self._git_root()
    ancestor = _git(main, "rev-parse", "HEAD")
    sibling = main / ".venv" / "affected-map.json"
    affected.write_map(sibling, ancestor, {"mod.T.test_a": ["src/slicer/ops.py:4"]}, clean=True)
    descendant = self._commit(main, "next")
    worktree = self._linked_worktree(main)
    self._at_checkout(worktree)
    self.assertEqual(affected.sibling_map(descendant), (sibling.resolve(), ancestor))

  def test_Select_AncestorMap_SelectsTestsForLinesChangedSinceRecorded(self) -> None:
    main = self._git_root()
    source = main / "src" / "slicer" / "ops.py"
    source.parent.mkdir(parents=True)
    source.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    _git(main, "add", "src/slicer/ops.py")
    _git(main, "commit", "-q", "-m", "source")
    ancestor = _git(main, "rev-parse", "HEAD")
    sibling = main / ".venv" / "affected-map.json"
    affected.write_map(sibling, ancestor, {
      "mod.T.test_four": ["src/slicer/ops.py:4"],
      "mod.T.test_one": ["src/slicer/ops.py:1"],
    }, clean=True)
    source.write_text("one\ntwo\nthree\nFOUR\n", encoding="utf-8")
    _git(main, "commit", "-q", "-am", "edit four")
    worktree = self._linked_worktree(main)
    self._at_checkout(worktree)
    self.assertNotEqual(_git(worktree, "rev-parse", "HEAD"), ancestor)
    self.assertEqual(_git(worktree, "status", "--porcelain"), "")
    code, out, err = _run([])
    self.assertEqual(code, 0, err)
    self.assertEqual(out.splitlines(), ["mod.T.test_four"])
    self.assertIn(str(sibling.resolve()), err)
    self.assertIn(f"the working tree is diffed against {ancestor}", err)

  def test_SiblingMap_RecordedAtNonAncestor_IsPassedOver(self) -> None:
    main = self._git_root()
    base = _git(main, "rev-parse", "HEAD")
    tree = _git(main, "rev-parse", "HEAD^{tree}")
    # A child of `base` on another branch: HEAD does not descend from it.
    other_branch = _git(main, "commit-tree", tree, "-p", base, "-m", "other branch")
    sibling = main / ".venv" / "affected-map.json"
    affected.write_map(sibling, other_branch, {"mod.T.test_a": ["src/slicer/ops.py:4"]}, clean=True)
    head = self._commit(main, "next")
    worktree = self._linked_worktree(main)
    self._at_checkout(worktree)
    self.assertIsNone(affected.sibling_map(head))
    code, out, err = _run(["--lines", "src/slicer/ops.py:4-4"])
    self.assertEqual(code, 2)
    self.assertEqual(out, "")
    self.assertIn(DISCOVER, err)
    self.assertNotIn(str(sibling.resolve()), err)

  def test_SiblingMap_SameHead_StillPreferred(self) -> None:
    main = self._git_root()
    ancestor_map = main / ".venv" / "affected-map.json"
    affected.write_map(ancestor_map, _git(main, "rev-parse", "HEAD"), {"mod.T.test_old": ["src/slicer/ops.py:4"]}, clean=True)
    head = self._commit(main, "next")
    holder = self._linked_worktree(main)
    head_map = holder / ".venv" / "affected-map.json"
    affected.write_map(head_map, head, {"mod.T.test_head": ["src/slicer/ops.py:4"]}, clean=True)
    worktree = self._linked_worktree(main)
    self._at_checkout(worktree)
    self.assertEqual(affected.sibling_map(head), (head_map.resolve(), head))
    code, out, err = _run(["--lines", "src/slicer/ops.py:4-4"])
    self.assertEqual(code, 0, err)
    self.assertEqual(out.splitlines(), ["mod.T.test_head"])
    self.assertIn(str(head_map.resolve()), err)
    self.assertNotIn(str(ancestor_map.resolve()), err)

  def test_SiblingMap_TwoAncestors_UsesTheNearest(self) -> None:
    main = self._git_root()
    far = _git(main, "rev-parse", "HEAD")
    far_map = main / ".venv" / "affected-map.json"
    affected.write_map(far_map, far, {"mod.T.test_far": ["src/slicer/ops.py:4"]}, clean=True)
    near = self._commit(main, "middle")
    holder = self._linked_worktree(main)
    near_map = holder / ".venv" / "affected-map.json"
    affected.write_map(near_map, near, {"mod.T.test_near": ["src/slicer/ops.py:4"]}, clean=True)
    worktree = self._linked_worktree(main)
    head = self._commit(worktree, "head")
    self._at_checkout(worktree)
    self.assertEqual(affected.sibling_map(head), (near_map.resolve(), near))

  def test_Select_NoLocalMap_SiblingAncestorDirtyUnderSrc_AsksForFullSuite(self) -> None:
    main = self._git_root()
    source = main / "src" / "slicer" / "ops.py"
    source.parent.mkdir(parents=True)
    source.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    _git(main, "add", "src/slicer/ops.py")
    _git(main, "commit", "-q", "-m", "source")
    sibling = main / ".venv" / "affected-map.json"
    affected.write_map(sibling, _git(main, "rev-parse", "HEAD"), {"mod.T.test_a": ["src/slicer/ops.py:4"]}, clean=True)
    self._commit(main, "next")
    source.write_text("one\ntwo\nthree\nFOUR\n", encoding="utf-8")
    worktree = self._linked_worktree(main)
    self._at_checkout(worktree)
    code, out, err = _run(["--lines", "src/slicer/ops.py:4-4"])
    self.assertEqual(code, 2)
    self.assertEqual(out, "")
    self.assertIn(DISCOVER, err)
    self.assertIn("no other worktree has a readable, clean map", err)
    self.assertNotIn(str(sibling.resolve()), err)

  def test_Select_OwnMapAtAncestor_AsksForFullSuite(self) -> None:
    main = self._git_root()
    worktree = self._linked_worktree(main)
    ancestor = _git(worktree, "rev-parse", "HEAD")
    affected.write_map(worktree / ".venv" / "affected-map.json", ancestor, {"mod.T.test_a": ["src/slicer/ops.py:4"]}, clean=True)
    self._commit(worktree, "next")
    self.assertNotEqual(_git(worktree, "rev-parse", "HEAD"), ancestor)
    self._at_checkout(worktree)
    code, out, err = _run(["--lines", "src/slicer/ops.py:4-4"])
    self.assertEqual(code, 2)
    self.assertEqual(out, "")
    self.assertIn(DISCOVER, err)
    self.assertIn("was recorded at", err)

  def test_SiblingMap_TwoAncestorsSameDistance_UsesFirstWorktree(self) -> None:
    main = self._git_root()
    ancestor = _git(main, "rev-parse", "HEAD")
    main_map = main / ".venv" / "affected-map.json"
    affected.write_map(main_map, ancestor, {"mod.T.test_main": ["src/slicer/ops.py:4"]}, clean=True)
    holder = self._linked_worktree(main)
    affected.write_map(holder / ".venv" / "affected-map.json", ancestor,
                       {"mod.T.test_holder": ["src/slicer/ops.py:4"]}, clean=True)
    worktree = self._linked_worktree(main)
    head = self._commit(worktree, "head")
    self._at_checkout(worktree)
    self.assertEqual(affected.sibling_map(head), (main_map.resolve(), ancestor))
    code, out, err = _run(["--lines", "src/slicer/ops.py:4-4"])
    self.assertEqual(code, 0, err)
    self.assertEqual(out.splitlines(), ["mod.T.test_main"])
    self.assertIn(str(main_map.resolve()), err)

  def test_Select_BorrowedMapChangedAfterChosen_AsksForFullSuite(self) -> None:
    main = self._git_root()
    sibling = main / ".venv" / "affected-map.json"
    affected.write_map(sibling, _git(main, "rev-parse", "HEAD"), {"mod.T.test_a": ["src/slicer/ops.py:4"]}, clean=True)
    worktree = self._linked_worktree(main)
    self._at_checkout(worktree)
    # sibling_map chose a commit that the map no longer records: a re-recording between the two reads.
    with patch.object(affected, "sibling_map", return_value=(sibling, "1" * 40)):
      code, out, err = _run(["--lines", "src/slicer/ops.py:4-4"])
    self.assertEqual(code, 2)
    self.assertEqual(out, "")
    self.assertIn("changed after it was chosen", err)
    self.assertIn(DISCOVER, err)

  def test_SiblingMap_RecordedShaNotFullHex_IsPassedOver(self) -> None:
    main = self._git_root()
    _git(main, "commit", "-q", "--allow-empty", "-m", "next")
    head = _git(main, "rev-parse", "HEAD")
    sibling = main / ".venv" / "affected-map.json"
    # "HEAD~1" would resolve in the borrowing checkout, so the value is refused before git sees it.
    affected.write_map(sibling, "HEAD~1", {"mod.T.test_a": ["src/slicer/ops.py:4"]}, clean=True)
    worktree = self._linked_worktree(main)
    self._at_checkout(worktree)
    self.assertIsNone(affected.sibling_map(head))
    code, out, err = _run(["--lines", "src/slicer/ops.py:4-4"])
    self.assertEqual(code, 2)
    self.assertEqual(out, "")
    self.assertIn(DISCOVER, err)
    self.assertNotIn(str(sibling.resolve()), err)

  def test_Select_NoLocalMap_SiblingMapRecordedDirtyThenCommitted_AsksForFullSuite(self) -> None:
    # The sibling's discover ran with src/ dirty, so its map says so. Committing that
    # change makes the sibling clean again, but the map still describes uncommitted code.
    main = self._git_root()
    source = main / "src" / "slicer" / "ops.py"
    source.parent.mkdir(parents=True)
    source.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    _git(main, "add", "src/slicer/ops.py")
    _git(main, "commit", "-q", "-m", "source")
    recorded = _git(main, "rev-parse", "HEAD")
    source.write_text("one\ntwo\nthree\nFOUR\n", encoding="utf-8")
    sibling = main / ".venv" / "affected-map.json"
    sibling.parent.mkdir(parents=True, exist_ok=True)
    sibling.write_text(json.dumps({
      "sha": recorded, "clean": False,
      "tests": {"mod.T.test_a": ["src/slicer/ops.py:4"]}, "reads": {},
    }), encoding="utf-8")
    _git(main, "commit", "-q", "-am", "edit four")
    descendant = _git(main, "rev-parse", "HEAD")
    worktree = self._linked_worktree(main)
    self._at_checkout(worktree)
    self.assertIsNone(affected.sibling_map(descendant))
    code, out, err = _run(["--lines", "src/slicer/ops.py:4-4"])
    self.assertEqual(code, 2)
    self.assertEqual(out, "")
    self.assertIn(DISCOVER, err)
    self.assertIn("no other worktree has a readable, clean map", err)
    self.assertNotIn(str(sibling.resolve()), err)

  def test_Select_NoLocalMap_SiblingMapWithoutCleanField_AsksForFullSuite(self) -> None:
    # A map written before the field existed never says that its tree was clean.
    main = self._git_root()
    head = _git(main, "rev-parse", "HEAD")
    sibling = main / ".venv" / "affected-map.json"
    sibling.parent.mkdir(parents=True, exist_ok=True)
    sibling.write_text(json.dumps({
      "sha": head, "tests": {"mod.T.test_a": ["src/slicer/ops.py:4"]}, "reads": {},
    }), encoding="utf-8")
    worktree = self._linked_worktree(main)
    self._at_checkout(worktree)
    self.assertIsNone(affected.sibling_map(head))
    code, out, err = _run(["--lines", "src/slicer/ops.py:4-4"])
    self.assertEqual(code, 2)
    self.assertEqual(out, "")
    self.assertIn(DISCOVER, err)
    self.assertIn("no other worktree has a readable, clean map", err)
    self.assertNotIn(str(sibling.resolve()), err)

  def test_Select_BorrowedMapRecordedDirtyAfterChosen_AsksForFullSuite(self) -> None:
    # A re-recording between the choice and the read can put a dirty map at the same commit.
    main = self._git_root()
    head = _git(main, "rev-parse", "HEAD")
    sibling = main / ".venv" / "affected-map.json"
    sibling.parent.mkdir(parents=True, exist_ok=True)
    sibling.write_text(json.dumps({
      "sha": head, "clean": False,
      "tests": {"mod.T.test_a": ["src/slicer/ops.py:4"]}, "reads": {},
    }), encoding="utf-8")
    worktree = self._linked_worktree(main)
    self._at_checkout(worktree)
    with patch.object(affected, "sibling_map", return_value=(sibling, head)):
      code, out, err = _run(["--lines", "src/slicer/ops.py:4-4"])
    self.assertEqual(code, 2)
    self.assertEqual(out, "")
    self.assertIn("changed after it was chosen", err)
    self.assertIn(DISCOVER, err)

  def test_WriteMap_DirtyTree_RecordsNotClean(self) -> None:
    repo = self._git_root()
    source = repo / "src" / "slicer" / "ops.py"
    source.parent.mkdir(parents=True)
    source.write_text("one\n", encoding="utf-8")
    path = repo / "affected-map.json"
    affected.write_map(path, _git(repo, "rev-parse", "HEAD"), {"mod.T.test_a": ["src/slicer/ops.py:1"]},
                       clean=affected.tree_is_clean(repo))
    self.assertIs(json.loads(path.read_text(encoding="utf-8"))["clean"], False)
    self.assertFalse(affected.load_map(path)[3])

  def test_WriteMap_CleanTree_RecordsClean(self) -> None:
    repo = self._git_root()
    head = _git(repo, "rev-parse", "HEAD")
    path = repo / "affected-map.json"
    affected.write_map(path, head, {"mod.T.test_a": ["src/slicer/ops.py:1"]},
                       clean=affected.tree_is_clean(repo))
    text = path.read_text(encoding="utf-8")
    self.assertLess(text.index('"sha"'), text.index('"clean"'))
    self.assertLess(text.index('"clean"'), text.index('"tests"'))
    self.assertIs(json.loads(text)["clean"], True)
    sha, tests, reads, clean = affected.load_map(path)
    self.assertEqual((sha, clean), (head, True))
    self.assertEqual(tests, {"mod.T.test_a": ["src/slicer/ops.py:1"]})
    self.assertEqual(reads, {})

  def test_TreeIsClean_ProbeCannotRun_IsNotClean(self) -> None:
    repo = self._git_root()
    with patch.object(affected.subprocess, "run", side_effect=OSError("git cannot run")):
      self.assertFalse(affected.tree_is_clean(repo))
    self.assertTrue(affected.tree_is_clean(repo))

  def test_Record_CleanThenDirtyTree_RecordsCleanThenNotClean(self) -> None:
    # `record` probes the tree before its suite runs; the suite here is a stub.
    repo = self._git_root()
    class Tiny(unittest.TestCase):
      def test_ok(self) -> None:
        return None
    path = repo / "affected-map.json"
    def fresh_suite(*args, **kwargs):
      # A suite runs once and is emptied, so each record call gets its own.
      return unittest.TestLoader().loadTestsFromTestCase(Tiny)
    with patch.object(affected, "ROOT", repo), \
         patch.object(unittest.defaultTestLoader, "discover", side_effect=fresh_suite):
      self.assertEqual(_run(["record", "--map", str(path)])[0], 0)
      self.assertIs(json.loads(path.read_text(encoding="utf-8"))["clean"], True)
      source = repo / "src" / "slicer" / "ops.py"
      source.parent.mkdir(parents=True)
      source.write_text("one\n", encoding="utf-8")
      self.assertEqual(_run(["record", "--map", str(path)])[0], 0)
    self.assertIs(json.loads(path.read_text(encoding="utf-8"))["clean"], False)

  def test_DiscoverSuccess_WritesMapForHead(self) -> None:
    directory = self._git_root()
    sha = subprocess.run(
      ["git", "rev-parse", "HEAD"], cwd=directory, capture_output=True, text=True, check=True,
    ).stdout.strip()
    path = directory / "affected-map.json"
    affected.publish_map(
      path, ok=True,
      tests={"pkg.T.test_b": ["src/slicer/sample.py:5"], "pkg.T.test_a": ["src/slicer/sample.py:2"]},
      reads={"pkg.T.test_b": [], "pkg.T.test_a": ["docs/a.md", "README.md"]},
      root=directory, clean=True,
    )
    text = path.read_text(encoding="utf-8")
    self.assertLess(text.index('"sha"'), text.index('"clean"'))
    self.assertLess(text.index('"clean"'), text.index('"tests"'))
    self.assertLess(text.index('"tests"'), text.index('"reads"'))
    document = json.loads(text)
    self.assertEqual(document, {
      "sha": sha,
      "clean": True,
      "tests": {"pkg.T.test_a": ["src/slicer/sample.py:2"], "pkg.T.test_b": ["src/slicer/sample.py:5"]},
      "reads": {"pkg.T.test_a": ["README.md", "docs/a.md"], "pkg.T.test_b": []},
    })
    self.assertEqual(list(document["reads"]), ["pkg.T.test_a", "pkg.T.test_b"])
    launcher = ["python3.14 -m unittest", "discover", "-s", "tests", "-t", "tests"]
    module = ["/usr/lib/python3.14/unittest/__main__.py", "discover", "-s", "tests", "-t", "tests"]
    self.assertTrue(affected.entrypoint_is_full_discover(launcher))
    self.assertTrue(affected.entrypoint_is_full_discover(module))
    self.assertFalse(affected.entrypoint_is_full_discover(launcher + ["-k", "One"]))
    for pattern in (["-p", "test_x.py"], ["--pattern", "test_x.py"], ["--pattern=test_x.py"], ["-ptest_x.py"]):
      with self.subTest(pattern=pattern):
        self.assertFalse(affected.entrypoint_is_full_discover(launcher + pattern))
    self.assertIs(self._hook_writes_for_launcher(directory)["clean"], True)


  def _hook_writes_for_launcher(self, directory: Path, dirty: bool = False) -> dict:
    """A fresh process with the launcher's argv writes that checkout's map.

    With `dirty`, an uncommitted src/ file exists before the discover starts.
    """
    path = directory / "hook-map.json"
    script = (
      "import json, sys, unittest\n"
      "from pathlib import Path\n"
      "import affected\n"
      "root = Path(sys.argv[1])\n"
      "destination = Path(sys.argv[2])\n"
      "if sys.argv[3] == 'dirty':\n"
      "  source = root / 'src' / 'slicer' / 'ops.py'\n"
      "  source.parent.mkdir(parents=True, exist_ok=True)\n"
      "  source.write_text('x = 1\\n', encoding='utf-8')\n"
      "sys.argv = ['python3.14 -m unittest', 'discover', '-s', 'tests', '-t', 'tests']\n"
      "affected.install_discover_hook(destination, root)\n"
      "class Tiny(unittest.TestCase):\n"
      "  def test_ok(self):\n"
      "    (root / 'marker.txt').read_text()\n"
      "runner = unittest.TextTestRunner(stream=open('/dev/null', 'w'), verbosity=0)\n"
      "result = runner.run(unittest.defaultTestLoader.loadTestsFromTestCase(Tiny))\n"
      "if not result.wasSuccessful():\n"
      "  raise SystemExit(1)\n"
      "doc = json.loads(destination.read_text())\n"
      "if doc['sha'] != affected.git_head(root):\n"
      "  raise SystemExit(2)\n"
      "if list(doc['reads'].values()) != [['marker.txt']]:\n"
      "  raise SystemExit(3)\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent)
    proc = subprocess.run(
      [sys.executable, "-c", script, str(directory), str(path), "dirty" if dirty else "clean"],
      cwd=directory, env=env, capture_output=True, text=True,
    )
    self.assertEqual(proc.returncode, 0, proc.stderr)
    self.assertTrue(path.is_file())
    return json.loads(path.read_text(encoding="utf-8"))

  def test_DiscoverSuccess_DirtyAtStart_RecordsNotClean(self) -> None:
    directory = self._git_root()
    document = self._hook_writes_for_launcher(directory, dirty=True)
    self.assertIs(document["clean"], False)

  def test_DiscoverFailure_LeavesAnExistingMap(self) -> None:
    directory = self._git_root()
    path = directory / "affected-map.json"
    path.write_text("sentinel\n", encoding="utf-8")
    before = path.read_bytes()
    affected.publish_map(
      path, ok=False, tests={"pkg.T.test_a": ["src/slicer/sample.py:2"]}, root=directory, clean=True,
    )
    self.assertEqual(path.read_bytes(), before)
    missing = directory / "missing.json"
    affected.publish_map(missing, ok=False, tests={}, root=directory, clean=True)
    self.assertFalse(missing.exists())

  def test_NestedDiscover_DoesNotTouchTheCheckoutMap(self) -> None:
    checkout = affected.DEFAULT_MAP
    before = checkout.read_bytes() if checkout.is_file() else None
    affected.install_discover_hook()

    class Tiny(unittest.TestCase):
      def test_ok(self) -> None:
        return None

    with affected.nested_discover():
      with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        unittest.TextTestRunner(verbosity=0).run(
          unittest.defaultTestLoader.loadTestsFromTestCase(Tiny)
        )
    after = checkout.read_bytes() if checkout.is_file() else None
    self.assertEqual(after, before)


if affected.entrypoint_is_full_discover():
  affected.install_discover_hook()


if __name__ == "__main__":
  unittest.main()
