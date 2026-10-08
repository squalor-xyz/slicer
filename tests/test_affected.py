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

import affected


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


class AffectedTests(unittest.TestCase):
  def map_file(self, tests: dict[str, list[str]], sha: str = SHA) -> Path:
    directory = Path(tempfile.mkdtemp())
    self.addCleanup(shutil.rmtree, directory)
    path = directory / "affected-map.json"
    affected.write_map(path, sha, tests)
    return path

  def test_Record_TwoTests_MapsEachTestAndSelectsTheLinesItRan(self) -> None:
    directory = Path(tempfile.mkdtemp())
    self.addCleanup(shutil.rmtree, directory)
    module = _load_sample(directory)

    class Pair(unittest.TestCase):
      def test_a(self) -> None:
        module.only_a()
        module.shared()

      def test_b(self) -> None:
        module.only_b()
        module.shared()

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Pair)
    traced = affected.trace_suite(suite, directory)
    # Python 3.14 clears a suite after it runs, so the ids come from the map.
    by_method = {test_id.rsplit(".", 1)[-1]: test_id for test_id in traced}
    only_a = set(traced[by_method["test_a"]]) - set(traced[by_method["test_b"]])
    only_b = set(traced[by_method["test_b"]]) - set(traced[by_method["test_a"]])
    shared = set(traced[by_method["test_a"]]) & set(traced[by_method["test_b"]])
    self.assertTrue(only_a)
    self.assertTrue(only_b)
    self.assertTrue(shared)
    self.assertTrue(all(line.startswith("src/slicer/sample.py:") for line in only_a | only_b | shared))
    path = self.map_file(traced)
    text = path.read_text(encoding="utf-8")
    self.assertLess(text.index('"sha"'), text.index('"tests"'))
    self.assertEqual(json.loads(text)["sha"], SHA)

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
    self.addCleanup(shutil.rmtree, directory)
    subprocess.run(["git", "init", "-q"], cwd=directory, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=directory, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=directory, check=True)
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
    self.addCleanup(shutil.rmtree, directory)
    subprocess.run(["git", "init", "-q"], cwd=directory, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=directory, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=directory, check=True)
    marker = directory / "marker.txt"
    marker.write_text("marker\n", encoding="utf-8")
    subprocess.run(["git", "add", "marker.txt"], cwd=directory, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=directory, check=True)
    return directory

  def test_DiscoverSuccess_WritesMapForHead(self) -> None:
    directory = self._git_root()
    sha = subprocess.run(
      ["git", "rev-parse", "HEAD"], cwd=directory, capture_output=True, text=True, check=True,
    ).stdout.strip()
    path = directory / "affected-map.json"
    affected.publish_map(
      path, ok=True, tests={"pkg.T.test_a": ["src/slicer/sample.py:2"]}, root=directory,
    )
    text = path.read_text(encoding="utf-8")
    self.assertLess(text.index('"sha"'), text.index('"tests"'))
    self.assertEqual(json.loads(text), {
      "sha": sha,
      "tests": {"pkg.T.test_a": ["src/slicer/sample.py:2"]},
    })
    launcher = ["python3.14 -m unittest", "discover", "-s", "tests", "-t", "tests"]
    module = ["/usr/lib/python3.14/unittest/__main__.py", "discover", "-s", "tests", "-t", "tests"]
    self.assertTrue(affected.entrypoint_is_full_discover(launcher))
    self.assertTrue(affected.entrypoint_is_full_discover(module))
    self.assertFalse(affected.entrypoint_is_full_discover(launcher + ["-k", "One"]))
    self._hook_writes_for_launcher(directory)


  def _hook_writes_for_launcher(self, directory: Path) -> None:
    """A fresh process with the launcher's argv writes that checkout's map."""
    path = directory / "hook-map.json"
    script = (
      "import json, sys, unittest\n"
      "from pathlib import Path\n"
      "import affected\n"
      "root = Path(sys.argv[1])\n"
      "destination = Path(sys.argv[2])\n"
      "sys.argv = ['python3.14 -m unittest', 'discover', '-s', 'tests', '-t', 'tests']\n"
      "affected.install_discover_hook(destination, root)\n"
      "class Tiny(unittest.TestCase):\n"
      "  def test_ok(self):\n"
      "    return None\n"
      "runner = unittest.TextTestRunner(stream=open('/dev/null', 'w'), verbosity=0)\n"
      "result = runner.run(unittest.defaultTestLoader.loadTestsFromTestCase(Tiny))\n"
      "if not result.wasSuccessful():\n"
      "  raise SystemExit(1)\n"
      "doc = json.loads(destination.read_text())\n"
      "if doc['sha'] != affected.git_head(root):\n"
      "  raise SystemExit(2)\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parent)
    proc = subprocess.run(
      [sys.executable, "-c", script, str(directory), str(path)],
      cwd=directory, env=env, capture_output=True, text=True,
    )
    self.assertEqual(proc.returncode, 0, proc.stderr)
    self.assertTrue(path.is_file())

  def test_DiscoverFailure_LeavesAnExistingMap(self) -> None:
    directory = self._git_root()
    path = directory / "affected-map.json"
    path.write_text("sentinel\n", encoding="utf-8")
    before = path.read_bytes()
    affected.publish_map(
      path, ok=False, tests={"pkg.T.test_a": ["src/slicer/sample.py:2"]}, root=directory,
    )
    self.assertEqual(path.read_bytes(), before)
    missing = directory / "missing.json"
    affected.publish_map(missing, ok=False, tests={}, root=directory)
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
