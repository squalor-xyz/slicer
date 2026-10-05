"""The only place slicer talks to git, and it never writes history.

Commands are checked against an allowlist. Committing, pushing and tagging
are the owner's to do, so `commit`, `push` and `tag` cannot be reached from
here even by a caller that asks for them.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from slicer.errors import StateError

ALLOWED = frozenset({
  "rev-parse", "status", "log", "worktree", "branch", "config", "merge-file",
})

# These subcommands are allowlisted only for the exact read-only forms below.
READ_ONLY = {
  "worktree": frozenset({("list", "--porcelain")}),
  "branch": frozenset({("--all", "--format=%(refname)")}),
  "config": frozenset({
    ("--get", "user.name"),
    ("--get", "merge.slicer-generated.driver"),
    ("--get", "merge.slicer-index.driver"),
  }),
}

# `merge-file` rewrites its first file argument unless `-p` sends the result to
# stdout, so `-p` must lead. It reads three files and never touches a repository.
STDOUT_ONLY = {"merge-file": "-p"}


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
  if not args or args[0] not in ALLOWED:
    raise StateError(
      f"git {args[0] if args else ''!r} is not permitted; slicer never writes history"
    )
  exact = READ_ONLY.get(args[0])
  if exact is not None and args[1:] not in exact:
    raise StateError(
      f"git {' '.join(args)} is not permitted; slicer never writes history"
    )
  if args[0] in STDOUT_ONLY and args[1:2] != (STDOUT_ONLY[args[0]],):
    raise StateError(
      f"git {' '.join(args)} is not permitted; slicer never writes history"
    )
  return subprocess.run(
    ["git", *args], cwd=str(root), capture_output=True, text=True, check=False
  )


def is_repo(root: Path) -> bool:
  try:
    done = _run(root, "rev-parse", "--is-inside-work-tree")
  except FileNotFoundError:
    return False
  return done.returncode == 0 and done.stdout.strip() == "true"


def merge_in_progress(root: Path) -> bool:
  """Whether this checkout has a merge to finish, including linked worktrees."""
  if not is_repo(root):
    return False
  done = _run(root, "rev-parse", "-q", "--verify", "MERGE_HEAD")
  return done.returncode == 0


def require_no_merge(root: Path) -> None:
  """Refuse slicer state changes while Git is merging this checkout."""
  if merge_in_progress(root):
    raise StateError(
      f"Git merge in progress in {root}; finish or abort it before changing slicer state. "
      "`slicer render` and `slicer sync` remain available for merge repair.",
      code="merge_in_progress",
    )


def move(_root: Path, src: Path, dst: Path) -> str:
  """Rename `src` to `dst` on disk. Git is not consulted.

  The same move works in a linked worktree, in the main checkout, and in a
  directory that is not a repository. The index stays untouched. A later
  `git add` of the old path and the new path together still records one rename.
  """
  dst.parent.mkdir(parents=True, exist_ok=True)
  src.replace(dst)
  return "move"


def subjects(root: Path, limit: int = 2000) -> list[str]:
  """Recent commit subjects across all refs, for the index-versus-git check."""
  if not is_repo(root):
    return []
  done = _run(root, "log", "--all", "--no-merges", f"--max-count={limit}", "--pretty=%h %s")
  return done.stdout.splitlines() if done.returncode == 0 else []


CLAIM_OWNER_ENV = "SLICER_CLAIM_OWNER"


def _named(value: str | None, source: str) -> str:
  """A per-call owner, stripped; empty means "fall through to the next source".

  A newline is refused rather than cut: the value names who did something in the
  log, and silently keeping only its first line would record a different name.
  """
  chosen = (value or "").strip()
  if "\n" in chosen or "\r" in chosen:
    raise StateError(f"{source} must be one line", code="usage")
  return chosen


def identity(root: Path, configured: str, override: str | None = None) -> str:
  """Who a claim or a lifecycle action should name.

  A per-call `--owner`, then SLICER_CLAIM_OWNER, then `claim_owner` in config,
  then git user.name, then the worktree. The first two let several agents in one
  checkout claim under their own names.
  """
  for value, source in ((override, "--owner"), (os.environ.get(CLAIM_OWNER_ENV), CLAIM_OWNER_ENV)):
    chosen = _named(value, source)
    if chosen:
      return chosen
  chosen = configured.strip()
  if chosen:
    return chosen.splitlines()[0].strip()
  if is_repo(root):
    done = _run(root, "config", "--get", "user.name")
    if done.returncode == 0 and done.stdout.strip():
      return done.stdout.splitlines()[0].strip()
    here = _current_worktree(root)
    if here is not None and here.name:
      return here.name
  return root.name or "unknown"


def _driver_configured(root: Path, name: str) -> bool:
  try:
    done = _run(root, "config", "--get", f"merge.{name}.driver")
  except (FileNotFoundError, OSError):
    return False
  return done.returncode == 0 and done.stdout.strip() != ""


def render_driver_configured(root: Path) -> bool:
  """Whether this clone has the `slicer-generated` render merge driver set up --
  the per-clone `git config` that `slicer setup-git` prints (S109/S121). Read-only;
  an unset driver, a non-repo, or a failed query all read as not configured. The
  caller gates on `is_repo` so a non-repo is not reported as a missing driver."""
  return _driver_configured(root, "slicer-generated")


def index_driver_configured(root: Path) -> bool:
  """Whether this clone has the `slicer-index` merge driver for `index.json` set
  up. Same read-only query and the same reading of "not configured" as the render
  driver."""
  return _driver_configured(root, "slicer-index")


def merge_file(cwd: Path, base: Path, ours: Path, theirs: Path) -> tuple[int, str]:
  """Three-way merge of three files, as `(conflict count, merged text)`.

  Nothing is written: `-p` returns the result, and conflicts stay in it as the
  usual markers. A count above 127 is git's error exit, not a conflict count.
  """
  done = _run(
    cwd, "merge-file", "-p", "-L", "ours", "-L", "base", "-L", "theirs",
    str(ours), str(base), str(theirs),
  )
  if done.returncode > 127 or done.returncode < 0:
    raise StateError(
      f"git merge-file failed: {done.stderr.strip() or done.returncode}", code="io"
    )
  return done.returncode, done.stdout


def elsewhere(root: Path, item_id: str) -> list[str]:
  """Other branch and worktree names that already refer to this slice.

  `start` warns from this and still changes status. The checkout it is running
  in — its worktree, its branch, and a remote of that same branch — is not
  elsewhere. Not a git repo, or a query that fails, yields nothing.
  """
  if not item_id or not is_repo(root):
    return []
  pattern = re.compile(rf"\b{re.escape(item_id)}\b")
  current = _current_branch(root)
  here = _current_worktree(root)
  names: list[str] = []
  for path in _worktree_paths(root):
    try:
      same = here is not None and path.resolve() == here
    except OSError:
      same = False
    if same or not pattern.search(path.as_posix()):
      continue
    names.append(f"worktree {path}")
  listed = _run(root, "branch", "--all", "--format=%(refname)")
  if listed.returncode == 0:
    for ref in listed.stdout.splitlines():
      short = _other_ref(ref, current)
      if short is not None and pattern.search(short):
        names.append(f"branch {short}")
  return sorted(dict.fromkeys(names))


def _current_worktree(root: Path) -> Path | None:
  """The git worktree that contains `root`, which may sit below the toplevel."""
  done = _run(root, "rev-parse", "--show-toplevel")
  if done.returncode != 0:
    return None
  text = done.stdout.strip()
  if not text:
    return None
  try:
    return Path(text).resolve()
  except OSError:
    return None


def _current_branch(root: Path) -> str | None:
  done = _run(root, "rev-parse", "--abbrev-ref", "HEAD")
  if done.returncode != 0:
    return None
  name = done.stdout.strip()
  if not name or name == "HEAD":
    return None
  return name


def _worktree_paths(root: Path) -> list[Path]:
  done = _run(root, "worktree", "list", "--porcelain")
  if done.returncode != 0:
    return []
  paths: list[Path] = []
  for line in done.stdout.splitlines():
    if line.startswith("worktree "):
      paths.append(Path(line.removeprefix("worktree ")))
  return paths


def at_worktree_root(root: Path) -> bool:
  """True when `root` is the git worktree, not a directory inside one.

  Sibling checkouts are other roots of this repo. A project below the
  worktree root is not those checkouts, and reading them would mix in
  another project's items.
  """
  try:
    here = _current_worktree(root)
  except (FileNotFoundError, OSError):
    return False
  return here is not None and here == Path(root).resolve()


def sibling_worktrees(root: Path) -> list[Path]:
  """Other checkouts of this repo, in stable path order."""
  try:
    here = _current_worktree(root)
    if here is None:
      return []
    paths = _worktree_paths(root)
    return sorted(path for path in paths if path.resolve() != here)
  except (FileNotFoundError, OSError):
    return []


def _other_ref(ref: str, current: str | None) -> str | None:
  """The branch name to show, or None when it is this checkout's own branch.

  A remote ref drops one remote segment (`origin/feature/S02` while on
  `feature/S02`). A longer local name that merely ends with this branch stays.
  """
  if ref.startswith("refs/heads/"):
    name = ref.removeprefix("refs/heads/")
    if current is not None and name == current:
      return None
    return name
  if ref.startswith("refs/remotes/"):
    rest = ref.removeprefix("refs/remotes/")
    _remote, sep, name = rest.partition("/")
    if not sep:
      return None
    if current is not None and name == current:
      return None
    return rest
  return None


def _git_common_dir(path: Path) -> Path | None:
  """The repo's shared git directory as seen from `path`, or None when `path`
  is not in a git repo. Two worktrees of one repo report the same common dir,
  which is how `foreign_worktree` tells same-repo siblings from an unrelated
  install."""
  try:
    done = _run(path, "rev-parse", "--git-common-dir")
  except (FileNotFoundError, OSError):
    return None
  out = done.stdout.strip()
  if done.returncode != 0 or not out:
    return None
  common = Path(out)
  if not common.is_absolute():
    common = path / common
  try:
    return common.resolve()
  except OSError:
    return None


def foreign_worktree(root: Path, code_dir: Path) -> str | None:
  """A hint when slicer's own code lives in a *different worktree of the same
  repo* as the project it is acting on -- the editable-install-in-a-worktree
  trap, where edits in the project's own `src/` are not what actually runs.

  Returns None for ordinary use: code under the project (normal dev or
  `PYTHONPATH=src`), a project elsewhere in the code's own worktree, or code
  from an unrelated location such as a site-packages install (a different repo,
  or no repo at all). Best-effort and read-only; any
  failure yields None so it can never break a command.
  """
  try:
    code_dir = code_dir.resolve()
    root = root.resolve()
  except OSError:
    return None
  if code_dir == root or code_dir.is_relative_to(root):
    return None
  code_common = _git_common_dir(code_dir)
  if code_common is None or code_common != _git_common_dir(root):
    return None
  # Same repo is not enough: a project nested in the code's own worktree (a
  # subproject, or a test fixture under an ignored scratch dir) runs this
  # worktree's code, so only differing worktree toplevels are the trap.
  try:
    code_tree = _current_worktree(code_dir)
    if code_tree is None or code_tree == _current_worktree(root):
      return None
  except OSError:
    return None
  return (
    f"running code from {code_dir}, but this project is {root} -- a different "
    "worktree of the same repo, so edits in this checkout's src/ are not what "
    "runs. Use `PYTHONPATH=src python3 -m slicer` to run this checkout's code."
  )
