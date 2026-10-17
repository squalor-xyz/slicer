"""Run the tests that executed the lines, or opened the files, a diff touches.

The full unittest discover stays the gate before done. When that discover is
the process entry point and passes, it writes this map for HEAD: the lines each
test executed, and the non-Python repo files each test opened, so a Markdown or
template change selects its readers. The map also records whether its tree was clean:
no uncommitted changes under `src/` or `tests/` when the discover started. A dirty tree,
or one whose probe fails, is recorded as not clean. A worktree without a map of its own
borrows one that another worktree of the repository recorded at HEAD or at an ancestor
of HEAD, the nearest one when several qualify, when that map is clean and that worktree
has no uncommitted changes under `src/` or `tests/`; it is read, never written. A
borrowed map still describes every line unchanged since its commit, so the working tree
is diffed against that commit, not HEAD. A worktree's own map is used only at the same
HEAD, whatever its clean field says. The map is
a local cache: when it is missing or stale, the caller is told to run that suite
instead of a list that only looks complete. A selected id that the tree no longer
defines is dropped from a run, and that run asks for the full suite too.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAP = ROOT / ".venv" / "affected-map.json"
DISCOVER = "python3 -m unittest discover -s tests -t tests"
NOTHING = "nothing to run"
_FULL_SHA = re.compile(r"[0-9a-f]{40}")


class GitError(Exception):
  """git could not describe this working tree."""


class LoadError(Exception):
  """A changed test module did not load, so its ids would be a guess."""


def main(argv: list[str] | None = None) -> int:
  args = _parse(argv)
  map_path = args.map if args.map is not None else DEFAULT_MAP
  if args.mode == "record":
    if args.run or args.lines or args.sha:
      print("affected: record only accepts --map", file=sys.stderr)
      return 2
    return record(map_path)
  return select(map_path, args.sha, args.lines, args.run, borrow=args.map is None)


def record(map_path: Path) -> int:
  """Trace one full discover and write the map for the current HEAD.

  A person or an agent runs this locally. CI does not, and the suite's own
  tests call `trace_suite` on a fixture instead of this.
  """
  sha = git_head(ROOT)
  if sha is None:
    return _full_suite("cannot read HEAD")
  clean = tree_is_clean(ROOT)
  suite = unittest.defaultTestLoader.discover(
    str(ROOT / "tests"), top_level_dir=str(ROOT / "tests"),
  )
  tests, reads = trace(suite, ROOT)
  write_map(map_path, sha, tests, reads, clean=clean)
  print(f"recorded {len(tests)} tests to {map_path}")
  return 0


def trace_suite(suite: unittest.TestSuite, root: Path) -> dict[str, list[str]]:
  """Lines each test executed under `src/slicer/` or `tests/`, one test at a time."""
  return trace(suite, root)[0]


def trace(
  suite: unittest.TestSuite, root: Path,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
  """Lines each test executed and the repo files it opened, one test at a time."""
  result = _TracingResult(root)
  previous = sys.gettrace()
  try:
    suite.run(result)
  finally:
    # An outer discover is tracing too. Clearing the hook here would drop the
    # rest of that test's lines, so put the previous tracer back.
    sys.settrace(previous)
  return result.lines(), result.reads()


def write_map(
  path: Path, sha: str, tests: dict[str, list[str]],
  reads: dict[str, list[str]] | None = None, *, clean: bool,
) -> None:
  """Write `{sha, clean, tests, reads}` with sorted ids and sorted lists.

  `clean` is what `tree_is_clean` said when the discover started, not the tree as it
  is after the run, so the caller passes the answer it took before the tests ran.
  """
  path.parent.mkdir(parents=True, exist_ok=True)
  opened = {} if reads is None else reads
  payload = {
    "sha": sha,
    "clean": clean,
    "tests": {test_id: sorted(tests[test_id]) for test_id in sorted(tests)},
    "reads": {test_id: sorted(opened[test_id]) for test_id in sorted(opened)},
  }
  text = json.dumps(payload, indent=2) + "\n"
  temporary = path.with_suffix(".json.tmp")
  temporary.write_text(text, encoding="utf-8")
  temporary.replace(path)


def load_map(path: Path) -> tuple[str, dict[str, list[str]], dict[str, list[str]], bool]:
  data = json.loads(path.read_text(encoding="utf-8"))
  tests = data["tests"]
  if not isinstance(data.get("sha"), str) or not isinstance(tests, dict):
    raise TypeError("map must be {sha, tests, reads}")
  if "reads" not in data:
    raise ValueError("map has no reads, so it predates file-read selection")
  opened = data["reads"]
  if not isinstance(opened, dict):
    raise TypeError("map reads must be {id: [path, ...]}")
  # A map written before the field existed was never probed, so it is not clean.
  clean = data.get("clean", False)
  if not isinstance(clean, bool):
    raise TypeError("map clean must be true or false")
  return data["sha"], _string_lists(tests, "tests"), _string_lists(opened, "reads"), clean


def _string_lists(section: dict, name: str) -> dict[str, list[str]]:
  loaded: dict[str, list[str]] = {}
  for test_id, items in section.items():
    if not isinstance(test_id, str) or not isinstance(items, list):
      raise TypeError(f"map {name} must be {{id: [path, ...]}}")
    loaded[test_id] = [str(item) for item in items]
  return loaded


def select(
  map_path: Path, sha: str | None, line_specs: list[str], run: bool, *, borrow: bool = False,
) -> int:
  """Select from `map_path`. With `borrow`, a missing map is replaced by a sibling's.

  A borrowed map may be recorded at an ancestor of HEAD; the working tree is then
  diffed against the commit the map was recorded at.
  """
  if not map_path.is_file() and not borrow:
    return _full_suite(f"{map_path} is missing")
  current = sha if sha is not None else git_head(ROOT)
  if current is None:
    return _full_suite("cannot read HEAD")
  borrowed = not map_path.is_file()
  expected = current
  if borrowed:
    found = sibling_map(current)
    if found is None:
      return _full_suite(
        f"{map_path} is missing, and no other worktree has a readable, clean map "
        f"for {current} or an ancestor of it"
      )
    map_path, expected = found
  try:
    recorded, tests, reads, clean = load_map(map_path)
  except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
    return _full_suite(f"{map_path} cannot be read ({exc})")
  if recorded != expected:
    if borrowed:
      return _full_suite(f"{map_path} changed after it was chosen, and is now recorded at {recorded}")
    return _full_suite(f"map was recorded at {recorded}, HEAD is {current}")
  # The choice in sibling_map read `clean`; a re-recording since then can have cleared it.
  if borrowed and not clean:
    return _full_suite(f"{map_path} changed after it was chosen, and is now not recorded from a clean tree")
  if borrowed:
    print(
      f"affected: no map in this worktree; using {map_path}, recorded at {recorded}; "
      f"the working tree is diffed against {recorded}",
      file=sys.stderr,
    )
  try:
    changed = lines_from_specs(line_specs) if line_specs else working_tree_lines(ROOT, recorded)
  except ValueError as exc:
    print(f"affected: {exc}", file=sys.stderr)
    return 2
  except GitError as exc:
    return _full_suite(str(exc))
  return _emit(changed, tests, reads, run)


def sibling_map(sha: str) -> tuple[Path, str] | None:
  """The map that a worktree recorded at `sha`, or at its nearest ancestor, and that commit.

  Read only: each candidate is loaded and never written. A map recorded at `sha`
  itself wins at once. Otherwise the candidate with the fewest commits between
  its recorded commit and `sha` wins, the first in `git worktree list` order on a
  tie. A map that does not load, or was not recorded from a clean tree (its `clean`
  field is false or missing), or records a commit that is neither `sha` nor one of
  its ancestors, is passed over, and so is a worktree with uncommitted changes under
  `src/` or `tests/`, whose map may describe code that is not at its recorded commit.
  A recorded value that is not a full 40-character SHA is passed over before git sees
  it, so a hand-made `HEAD~1` cannot resolve here.
  """
  nearest: tuple[int, Path, str] | None = None
  for worktree in _worktree_paths(ROOT):
    candidate = worktree / ".venv" / "affected-map.json"
    if not candidate.is_file():
      continue
    try:
      recorded, _, _, clean = load_map(candidate)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
      continue
    if not clean:
      continue
    if _FULL_SHA.fullmatch(recorded) is None:
      continue
    distance = 0 if recorded == sha else _ancestor_distance(recorded, sha)
    if distance is None or (nearest is not None and distance >= nearest[0]):
      continue
    if _has_source_changes(worktree):
      continue
    if distance == 0:
      return candidate, recorded
    nearest = (distance, candidate, recorded)
  if nearest is None:
    return None
  return nearest[1], nearest[2]


def _ancestor_distance(ancestor: str, sha: str) -> int | None:
  """How many commits lie after `ancestor` up to `sha`, or None unless it is an ancestor.

  Read only, and run in this checkout, which shares the repository's objects with
  every other worktree. A commit that git cannot read is not an ancestor.
  """
  try:
    ancestry = subprocess.run(
      ["git", "merge-base", "--is-ancestor", ancestor, sha],
      cwd=ROOT, capture_output=True, text=True, check=False,
    )
    if ancestry.returncode != 0:
      return None
    count = subprocess.run(
      ["git", "rev-list", "--count", f"{ancestor}..{sha}"],
      cwd=ROOT, capture_output=True, text=True, check=False,
    )
  except OSError:
    return None
  if count.returncode != 0 or not count.stdout.strip().isdigit():
    return None
  return int(count.stdout.strip())


def _has_source_changes(worktree: Path) -> bool:
  """True when `worktree` has uncommitted changes under `src/` or `tests/`, or git cannot say.

  Read only: `--no-optional-locks` keeps git from refreshing the index. A failed
  probe counts as changed, so its map is not trusted. `.slicer/` is out of scope.
  """
  try:
    proc = subprocess.run(
      ["git", "--no-optional-locks", "status", "--porcelain", "--", "src", "tests"],
      cwd=worktree, capture_output=True, text=True, check=False,
    )
  except OSError:
    return True
  if proc.returncode != 0:
    return True
  return bool(proc.stdout.strip())


def tree_is_clean(root: Path) -> bool:
  """True when `root` has no uncommitted changes under `src/` or `tests/`.

  A discover calls this before it runs the suite and records the answer in its map,
  so the map says what the tree was when the tests started. A failed probe is not clean.
  """
  return not _has_source_changes(root)


def _worktree_paths(root: Path) -> list[Path]:
  """Every checkout of this repository, main checkout first."""
  try:
    proc = subprocess.run(
      ["git", "worktree", "list", "--porcelain"],
      cwd=root, capture_output=True, text=True, check=False,
    )
  except OSError:
    return []
  if proc.returncode != 0:
    return []
  return [
    Path(line.removeprefix("worktree ")).resolve()
    for line in proc.stdout.splitlines()
    if line.startswith("worktree ")
  ]


def working_tree_lines(root: Path, base: str = "HEAD") -> dict[str, set[int]]:
  """Changed lines versus `base`, plus every line of an untracked `src/` or `tests/` file."""
  try:
    diff = subprocess.run(
      ["git", "diff", "-U0", base, "--"],
      cwd=root, capture_output=True, text=True, check=False,
    )
    untracked = subprocess.run(
      ["git", "ls-files", "--others", "--exclude-standard", "--", "src", "tests"],
      cwd=root, capture_output=True, text=True, check=False,
    )
  except OSError as exc:
    raise GitError(str(exc)) from exc
  if diff.returncode != 0 or untracked.returncode != 0:
    detail = (diff.stderr or untracked.stderr).strip()
    raise GitError(detail or "git diff failed")
  changed = parse_unified(diff.stdout)
  for rel in untracked.stdout.splitlines():
    rel = rel.strip().replace("\\", "/")
    if rel:
      _add_whole_file(changed, root, rel)
  return changed


def parse_unified(text: str) -> dict[str, set[int]]:
  """Old numbers of removed lines and new numbers of added lines, per path.

  A zero-context diff names the HEAD line a modification replaced, which is
  the line `record` traced. A pure insertion contributes only its new numbers.
  """
  changed: dict[str, set[int]] = {}
  old_path: str | None = None
  new_path: str | None = None
  old_no = 0
  new_no = 0
  in_hunk = False
  for line in text.splitlines():
    if line.startswith("diff --git "):
      in_hunk = False
      old_path = None
      new_path = None
      continue
    if line.startswith("--- "):
      old_path = _diff_path(line[4:])
      continue
    if line.startswith("+++ "):
      new_path = _diff_path(line[4:])
      for path in (old_path, new_path):
        if path:
          changed.setdefault(path, set())
      continue
    if line.startswith("@@"):
      header = line.split("@@", 2)[1].strip().split()
      if len(header) < 2:
        raise ValueError(f"cannot read hunk {line!r}")
      old_no = _hunk_start(header[0])
      new_no = _hunk_start(header[1])
      in_hunk = True
      continue
    if not in_hunk or line.startswith("\\"):
      continue
    if line.startswith("-"):
      if old_path:
        changed.setdefault(old_path, set()).add(old_no)
      old_no += 1
    elif line.startswith("+"):
      if new_path:
        changed.setdefault(new_path, set()).add(new_no)
      new_no += 1
    else:
      old_no += 1
      new_no += 1
  return changed


def lines_from_specs(specs: list[str]) -> dict[str, set[int]]:
  changed: dict[str, set[int]] = {}
  for spec in specs:
    path, start, end = _one_range(spec)
    changed.setdefault(path, set()).update(range(start, end + 1))
  return changed


def git_head(root: Path) -> str | None:
  try:
    proc = subprocess.run(
      ["git", "rev-parse", "HEAD"],
      cwd=root, capture_output=True, text=True, check=False,
    )
  except OSError:
    return None
  if proc.returncode != 0:
    return None
  return proc.stdout.strip() or None


def run_suite(suite: unittest.TestSuite) -> int:
  result = unittest.TextTestRunner(verbosity=1).run(suite)
  return 0 if result.wasSuccessful() else 1


class _TracingResult(unittest.TestResult):
  def __init__(self, root: Path) -> None:
    super().__init__()
    # `_relative` compares resolved paths, so resolve the root the same way.
    try:
      self.root = root.resolve()
    except OSError:
      self.root = root
    self.seen: dict[str, set[str]] = {}
    self.opened: dict[str, set[str]] = {}
    self._current: set[str] | None = None
    self._current_reads: set[str] | None = None
    self._outer: _TracingResult | None = None
    self._files: dict[str, str | None] = {}
    self._read_paths: dict[str, str | None] = {}
    self._real_files: set[str] = set()

  def startTest(self, test: unittest.TestCase) -> None:
    global _recording
    self._current = set()
    self._current_reads = set()
    _install_audit_hook()
    # A traced test can run a nested suite; give the outer one its hook back.
    self._outer = _recording
    _recording = self
    sys.settrace(self._trace)
    super().startTest(test)

  def stopTest(self, test: unittest.TestCase) -> None:
    global _recording
    sys.settrace(None)
    _recording = self._outer
    self._outer = None
    lines = self._current or set()
    reads = self._current_reads or set()
    self._current = None
    self._current_reads = None
    super().stopTest(test)
    self.seen[test.id()] = lines
    self.opened[test.id()] = reads

  def lines(self) -> dict[str, list[str]]:
    return {test_id: sorted(lines) for test_id, lines in self.seen.items()}

  def reads(self) -> dict[str, list[str]]:
    return {test_id: sorted(files) for test_id, files in self.opened.items()}

  def record_read(self, raw: object) -> None:
    """Note an `open` of a repo file by the current test, if it is one we map."""
    if self._current_reads is None or not isinstance(raw, (str, bytes)):
      return
    name = os.fsdecode(raw)
    path = name if os.path.isabs(name) else os.path.join(os.getcwd(), name)
    if path in self._read_paths:
      rel = self._read_paths[path]
    else:
      rel = self._read_relative(path)
      self._read_paths[path] = rel
    if rel is None:
      return
    # A relative name opened through a directory descriptor (rmtree does this)
    # resolves against the working directory and names nothing real. A file
    # opened for writing does not exist yet either, so only a hit is cached.
    if path not in self._real_files:
      if not os.path.isfile(path):
        return
      self._real_files.add(path)
    self._current_reads.add(rel)

  def _read_relative(self, path: str) -> str | None:
    try:
      rel = Path(path).resolve().relative_to(self.root)
    except (OSError, ValueError, RuntimeError):
      return None
    parts = rel.parts
    if not parts or parts[0] in (".git", ".venv") or "__pycache__" in parts:
      return None
    # Python files are mapped by line. An import opens them too, which would
    # make every first importer of a module a reader of it.
    if rel.suffix in (".py", ".pyc"):
      return None
    return rel.as_posix()

  def _trace(self, frame, event, arg):
    # Line events for every other file call resolve() once per line and make
    # a full discover unusable. Those lines are never recorded.
    if event == "call":
      if self._project_file(frame.f_code.co_filename) is None:
        return None
      return self._trace
    if event == "line" and self._current is not None:
      rel = self._files.get(frame.f_code.co_filename)
      if rel is not None:
        self._current.add(f"{rel}:{frame.f_lineno}")
    return self._trace

  def _project_file(self, filename: str) -> str | None:
    if filename in self._files:
      return self._files[filename]
    rel = self._relative(filename) if _could_be_project(filename) else None
    self._files[filename] = rel
    return rel

  def _relative(self, filename: str) -> str | None:
    path = Path(filename)
    if not path.is_absolute():
      path = self.root / path
    try:
      rel = path.resolve().relative_to(self.root)
    except (OSError, ValueError):
      return None
    parts = rel.parts
    if len(parts) >= 3 and parts[0] == "src" and parts[1] == "slicer":
      return rel.as_posix()
    if parts and parts[0] == "tests":
      return rel.as_posix()
    return None


def _emit(
  changed: dict[str, set[int]],
  tests: dict[str, list[str]],
  reads: dict[str, list[str]],
  run: bool,
) -> int:
  known = _mapped_paths(tests) | _read_paths(reads)
  for path in changed:
    if path.startswith("src/slicer/") and path not in known:
      return _full_suite(f"{path} is not in the map")
    if path.startswith("tests/") and not _is_test_module(path):
      return _full_suite(f"{path} is not a test module")
  selected: set[str] = set()
  for test_id, locations in tests.items():
    for location in locations:
      file_path, _, number = location.rpartition(":")
      if number.isdigit() and int(number) in changed.get(file_path, ()):
        selected.add(test_id)
        break
  for test_id, files in reads.items():
    if any(path in changed for path in files):
      selected.add(test_id)
  for path in sorted(changed):
    if not _is_test_module(path):
      continue
    try:
      selected.update(_tests_in_module(path))
    except LoadError as exc:
      return _full_suite(f"{path} could not be loaded ({exc})")
  if not selected:
    print(NOTHING)
    return 0
  ordered = sorted(selected)
  for test_id in ordered:
    print(test_id)
  if run:
    return run_ids(ordered)
  return 0


def run_ids(ids: list[str]) -> int:
  """Run the ids that load. One that does not load is named on stderr and not run.

  A rename or a deletion leaves its old id in the map. The rest still run, and the
  exit code asks for the full suite whatever the rest reports, since the renamed test
  may be one that still needs to run.
  """
  suite = unittest.TestSuite()
  dropped = 0
  for test_id in ids:
    loaded, problem = _load_id(test_id)
    if loaded is None:
      print(f"affected: {test_id} did not load, so it is not run: {problem}", file=sys.stderr)
      dropped += 1
    else:
      suite.addTest(loaded)
  status = run_suite(suite)
  if dropped:
    reason = f"{dropped} selected test id(s) did not load and were not run"
    if status:
      reason += " and the selected tests that ran failed"
    return _full_suite(reason)
  return status


def _load_id(test_id: str) -> tuple[unittest.TestSuite | None, str | None]:
  """The tests one id names, or the error that stops it from loading.

  The loader reports a missing name, and a module that fails to import, as a
  `_FailedTest` rather than raising, so such a leaf counts as a failure. A
  SyntaxError escapes the loader instead. Both keep their error text.
  """
  try:
    loaded = _loader().loadTestsFromName(test_id)
  except Exception as exc:
    return None, f"{type(exc).__name__}: {exc}"
  for leaf in _leaves(loaded):
    if leaf.__class__.__name__ == "_FailedTest":
      cause = getattr(leaf, "_exception", leaf)
      return None, f"{type(cause).__name__}: {cause}"
  return loaded, None


def _leaves(node: unittest.TestCase | unittest.TestSuite) -> list[unittest.TestCase]:
  if isinstance(node, unittest.TestSuite):
    return [leaf for item in node for leaf in _leaves(item)]
  return [node]


def _tests_in_module(path: str) -> list[str]:
  loaded, problem = _load_id(Path(path).stem)
  if loaded is None:
    raise LoadError(problem)
  return [test.id() for test in _leaves(loaded)]


def _loader() -> unittest.TestLoader:
  """A fresh loader, so failed loads do not pile up in `defaultTestLoader.errors`."""
  tests_dir = str(ROOT / "tests")
  if tests_dir not in sys.path:
    sys.path.insert(0, tests_dir)
  return unittest.TestLoader()


def _mapped_paths(tests: dict[str, list[str]]) -> set[str]:
  found: set[str] = set()
  for locations in tests.values():
    for location in locations:
      found.add(location.rpartition(":")[0])
  return found


def _read_paths(reads: dict[str, list[str]]) -> set[str]:
  return {path for files in reads.values() for path in files}


def _is_test_module(path: str) -> bool:
  file = Path(path)
  return file.parent.as_posix() == "tests" and file.name.startswith("test_") and file.suffix == ".py"


def _add_whole_file(changed: dict[str, set[int]], root: Path, rel: str) -> None:
  bucket = changed.setdefault(rel, set())
  file = root / rel
  if not file.is_file():
    return
  text = file.read_text(encoding="utf-8", errors="replace")
  bucket.update(range(1, len(text.splitlines()) + 1))


def _one_range(spec: str) -> tuple[str, int, int]:
  path, separator, span = spec.rpartition(":")
  if not separator or "-" not in span:
    raise ValueError(f"--lines must be PATH:START-END, not {spec!r}")
  start_text, end_text = span.split("-", 1)
  try:
    start, end = int(start_text), int(end_text)
  except ValueError:
    raise ValueError(f"--lines must be PATH:START-END, not {spec!r}") from None
  if not path or start < 1 or end < start:
    raise ValueError(f"--lines must be PATH:START-END, not {spec!r}")
  return path, start, end


def _diff_path(raw: str) -> str | None:
  token = raw.strip().split("\t", 1)[0]
  if token.startswith('"') and token.endswith('"'):
    token = token[1:-1]
  if token == "/dev/null":
    return None
  if token.startswith("a/") or token.startswith("b/"):
    token = token[2:]
  return token.replace("\\", "/")


def _hunk_start(spec: str) -> int:
  body = spec[1:] if spec[:1] in "+-" else spec
  return int(body.split(",", 1)[0])


def _full_suite(reason: str) -> int:
  print(f"affected: {reason}. run {DISCOVER}", file=sys.stderr)
  return 2


def _parse(argv: list[str] | None) -> argparse.Namespace:
  parser = argparse.ArgumentParser(
    prog="tests/affected.py",
    description="Run the unittest ids that executed the lines, or read the files, in a working-tree diff.",
  )
  parser.add_argument(
    "mode", nargs="?", choices=("record",),
    help="trace the suite and write .venv/affected-map.json",
  )
  parser.add_argument(
    "--run", action="store_true",
    help="run the selected tests and exit with their status; exit 2 when a selected id does not load",
  )
  parser.add_argument(
    "--lines", action="append", default=[], metavar="PATH:START-END",
    help="use this inclusive range instead of git diff (repeatable)",
  )
  parser.add_argument("--map", type=Path, help="map file (default: .venv/affected-map.json)")
  parser.add_argument("--sha", help="commit to compare with the map (default: HEAD)")
  return parser.parse_args(argv)


def publish_map(
  map_path: Path, *, ok: bool, tests: dict[str, list[str]], root: Path,
  reads: dict[str, list[str]] | None = None, clean: bool,
) -> None:
  """Write the discover map for HEAD, and only after a successful run.

  A failed run returns without creating or replacing `map_path`. `clean` is the
  `tree_is_clean` answer taken before the run started.
  """
  if not ok:
    return
  sha = git_head(root)
  if sha is None:
    return
  write_map(map_path, sha, tests, reads, clean=clean)


def entrypoint_is_full_discover(argv: list[str] | None = None) -> bool:
  """True when this process is an unfiltered discover of the tests directory.

  A `-k` filter or a `-p` pattern is a partial run, so neither qualifies. Importing this
  module does not look at argv and does not write a map.
  """
  args = list(sys.argv if argv is None else argv)
  if not args or "discover" not in args:
    return False
  if not _is_unittest_program(args[0]):
    return False
  tail = args[args.index("discover") + 1:]
  if "-k" in tail or any(_is_pattern(token) for token in tail):
    return False
  start = _option(tail, "-s", "--start-directory")
  top = _option(tail, "-t", "--top-level-directory")
  return _is_tests_dir(start) and _is_tests_dir(top)


def _is_pattern(token: str) -> bool:
  """`-p`, `-pX`, `--pattern` or `--pattern=X`."""
  if token.startswith("--"):
    return token == "--pattern" or token.startswith("--pattern=")
  return token.startswith("-p")


def _option(args: list[str], short: str, long: str) -> str | None:
  for index, token in enumerate(args):
    if token in (short, long) and index + 1 < len(args):
      return args[index + 1]
    if token.startswith(long + "="):
      return token.split("=", 1)[1]
  return None


def _is_unittest_program(program: str) -> bool:
  """The unittest entry point, including a launcher that rewrites argv[0]."""
  text = program.replace("\\", "/")
  if text.endswith("/unittest/__main__.py") or text.endswith("/unittest"):
    return True
  return text.endswith("-m unittest") or text.endswith("-m unittest.__main__")


def _could_be_project(filename: str) -> bool:
  """True when `filename` might be under `src/slicer/` or `tests/`.

  This is a string check so the tracer can ignore the standard library
  before it touches the filesystem. `_relative` still decides.
  """
  text = filename.replace("\\", "/")
  if text.startswith("./"):
    text = text[2:]
  if text.startswith("src/slicer/") or text.startswith("tests/"):
    return True
  return "/src/slicer/" in text or "/tests/" in text


def _is_tests_dir(value: str | None) -> bool:
  if not value:
    return False
  return Path(value).name == "tests"


class _NestedDiscover:
  """Marks a runner started inside an outer one so it cannot write the map."""

  def __enter__(self) -> "_NestedDiscover":
    global _discover_depth
    self._previous = _discover_depth
    if _discover_depth < 1:
      _discover_depth = 1
    return self

  def __exit__(self, *exc: object) -> None:
    global _discover_depth
    _discover_depth = self._previous


def nested_discover() -> _NestedDiscover:
  return _NestedDiscover()


def install_discover_hook(map_path: Path | None = None, root: Path | None = None) -> None:
  """Trace the outer discover once and write the map when that run passes.

  A runner started while the outer run is active does not write. Calling this
  from an import of this module is the caller's choice; import alone does not.
  """
  global _hook_installed
  if _hook_installed:
    return
  _hook_installed = True
  destination = DEFAULT_MAP if map_path is None else map_path
  base = ROOT if root is None else root
  original = unittest.TextTestRunner.run

  def run(self, test):
    global _discover_depth
    _discover_depth += 1
    nested = _discover_depth > 1
    tracer: _TracingResult | None = None
    clean = False
    previous = self.resultclass
    try:
      if not nested:
        # Probed before the suite runs, so the map says what the tests started from.
        clean = tree_is_clean(base)
        tracer = _TracingResult(base)
        self.resultclass = _traced_result(previous, tracer)
      result = original(self, test)
    finally:
      self.resultclass = previous
      _discover_depth -= 1
    if (
      tracer is not None
      and result.wasSuccessful()
      and entrypoint_is_full_discover()
    ):
      publish_map(
        destination, ok=True, tests=tracer.lines(), reads=tracer.reads(), root=base,
        clean=clean,
      )
    return result

  unittest.TextTestRunner.run = run


def _traced_result(result_class: type, tracer: _TracingResult) -> type:
  class Tracing(result_class):
    def startTest(self, test: unittest.TestCase) -> None:
      tracer.startTest(test)
      super().startTest(test)

    def stopTest(self, test: unittest.TestCase) -> None:
      super().stopTest(test)
      tracer.stopTest(test)

  return Tracing


def _install_audit_hook() -> None:
  """Add the one `open` listener this process will ever have.

  An audit hook cannot be removed, so it stays and only records while a traced
  test is current.
  """
  global _audit_installed
  if _audit_installed:
    return
  _audit_installed = True
  sys.addaudithook(_audit)


def _audit(event: str, args: tuple) -> None:
  recorder = _recording
  if event != "open" or recorder is None or not args:
    return
  try:
    recorder.record_read(args[0])
  except Exception:
    # An audit hook that raises would fail the test's own open call.
    return


_discover_depth = 0
_hook_installed = False
_audit_installed = False
_recording: _TracingResult | None = None


if __name__ == "__main__":
  raise SystemExit(main())
