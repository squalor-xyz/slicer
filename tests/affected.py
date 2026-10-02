"""Run the tests that executed the lines a working-tree diff touches.

The full unittest discover stays the gate before done. This map is a local
cache: when it is missing or stale, the caller is told to run that suite
instead of a list that only looks complete.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAP = ROOT / ".venv" / "affected-map.json"
DISCOVER = "python3 -m unittest discover -s tests -t tests"
NOTHING = "nothing to run"


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
  return select(map_path, args.sha, args.lines, args.run)


def record(map_path: Path) -> int:
  """Trace one full discover and write the map for the current HEAD.

  A person or an agent runs this locally. CI does not, and the suite's own
  tests call `trace_suite` on a fixture instead of this.
  """
  sha = git_head(ROOT)
  if sha is None:
    return _full_suite("cannot read HEAD")
  suite = unittest.defaultTestLoader.discover(
    str(ROOT / "tests"), top_level_dir=str(ROOT / "tests"),
  )
  tests = trace_suite(suite, ROOT)
  write_map(map_path, sha, tests)
  print(f"recorded {len(tests)} tests to {map_path}")
  return 0


def trace_suite(suite: unittest.TestSuite, root: Path) -> dict[str, list[str]]:
  """Lines each test executed under `src/slicer/` or `tests/`, one test at a time."""
  result = _TracingResult(root)
  try:
    suite.run(result)
  finally:
    sys.settrace(None)
  return {test_id: sorted(lines) for test_id, lines in result.seen.items()}


def write_map(path: Path, sha: str, tests: dict[str, list[str]]) -> None:
  """Write `{sha, tests}` with sorted ids and line lists."""
  path.parent.mkdir(parents=True, exist_ok=True)
  payload = {
    "sha": sha,
    "tests": {test_id: list(tests[test_id]) for test_id in sorted(tests)},
  }
  text = json.dumps(payload, indent=2) + "\n"
  temporary = path.with_suffix(".json.tmp")
  temporary.write_text(text, encoding="utf-8")
  temporary.replace(path)


def load_map(path: Path) -> tuple[str, dict[str, list[str]]]:
  data = json.loads(path.read_text(encoding="utf-8"))
  tests = data["tests"]
  if not isinstance(data.get("sha"), str) or not isinstance(tests, dict):
    raise TypeError("map must be {sha, tests}")
  loaded: dict[str, list[str]] = {}
  for test_id, lines in tests.items():
    if not isinstance(test_id, str) or not isinstance(lines, list):
      raise TypeError("map tests must be {id: [path:line, ...]}")
    loaded[test_id] = [str(line) for line in lines]
  return data["sha"], loaded


def select(map_path: Path, sha: str | None, line_specs: list[str], run: bool) -> int:
  if not map_path.is_file():
    return _full_suite(f"{map_path} is missing")
  try:
    recorded, tests = load_map(map_path)
  except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
    return _full_suite(f"{map_path} cannot be read ({exc})")
  current = sha if sha is not None else git_head(ROOT)
  if current is None:
    return _full_suite("cannot read HEAD")
  if current != recorded:
    return _full_suite(f"map was recorded at {recorded}, HEAD is {current}")
  try:
    changed = lines_from_specs(line_specs) if line_specs else working_tree_lines(ROOT)
  except ValueError as exc:
    print(f"affected: {exc}", file=sys.stderr)
    return 2
  except GitError as exc:
    return _full_suite(str(exc))
  return _emit(changed, tests, run)


def working_tree_lines(root: Path) -> dict[str, set[int]]:
  """Changed lines versus HEAD, plus every line of an untracked `src/` or `tests/` file."""
  try:
    diff = subprocess.run(
      ["git", "diff", "-U0", "HEAD", "--"],
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
    self.root = root
    self.seen: dict[str, set[str]] = {}
    self._current: set[str] | None = None

  def startTest(self, test: unittest.TestCase) -> None:
    self._current = set()
    sys.settrace(self._trace)
    super().startTest(test)

  def stopTest(self, test: unittest.TestCase) -> None:
    sys.settrace(None)
    lines = self._current or set()
    self._current = None
    super().stopTest(test)
    self.seen[test.id()] = lines

  def _trace(self, frame, event, arg):
    if event == "line" and self._current is not None:
      rel = self._relative(frame.f_code.co_filename)
      if rel is not None:
        self._current.add(f"{rel}:{frame.f_lineno}")
    return self._trace

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


def _emit(changed: dict[str, set[int]], tests: dict[str, list[str]], run: bool) -> int:
  known = _mapped_paths(tests)
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
  suite = unittest.TestSuite()
  for test_id in ids:
    suite.addTest(_loader().loadTestsFromName(test_id))
  return run_suite(suite)


def _tests_in_module(path: str) -> list[str]:
  try:
    suite = _loader().loadTestsFromName(Path(path).stem)
  except Exception as exc:
    raise LoadError(str(exc)) from exc
  found: list[str] = []

  def walk(node: unittest.TestCase | unittest.TestSuite) -> None:
    if isinstance(node, unittest.TestSuite):
      for item in node:
        walk(item)
      return
    if node.__class__.__name__ == "_FailedTest":
      raise LoadError(str(getattr(node, "_exception", node.id())))
    found.append(node.id())

  walk(suite)
  return found


def _loader() -> unittest.TestLoader:
  tests_dir = str(ROOT / "tests")
  if tests_dir not in sys.path:
    sys.path.insert(0, tests_dir)
  return unittest.defaultTestLoader


def _mapped_paths(tests: dict[str, list[str]]) -> set[str]:
  found: set[str] = set()
  for locations in tests.values():
    for location in locations:
      found.add(location.rpartition(":")[0])
  return found


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
    description="Run the unittest ids that executed the lines in a working-tree diff.",
  )
  parser.add_argument(
    "mode", nargs="?", choices=("record",),
    help="trace the suite and write .venv/affected-map.json",
  )
  parser.add_argument("--run", action="store_true", help="run the selected tests and exit with their status")
  parser.add_argument(
    "--lines", action="append", default=[], metavar="PATH:START-END",
    help="use this inclusive range instead of git diff (repeatable)",
  )
  parser.add_argument("--map", type=Path, help="map file (default: .venv/affected-map.json)")
  parser.add_argument("--sha", help="commit to compare with the map (default: HEAD)")
  return parser.parse_args(argv)


if __name__ == "__main__":
  raise SystemExit(main())
