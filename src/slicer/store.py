"""Locating and reading a `.slicer/` tracking directory.

Discovery walks up from the working directory, so any command works from a
subdirectory of a project, the way git does.
"""

from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from slicer import ids, jsonio, vcs
from slicer.config import CONFIG_NAME, Config
from slicer.errors import SlicerError, StateError
from slicer.model import SCHEMA_VERSION, Index, LogEntry, Slice

# flock is POSIX-only; on a platform without it the lock degrades to a no-op
# rather than crashing, so a Windows user still gets a working (if unguarded)
# tool. This is why every use is `if fcntl is not None`.
try:
  import fcntl
except ImportError:  # pragma: no cover - exercised only off POSIX
  fcntl = None  # type: ignore[assignment]

DIR_NAME = ".slicer"
INDEX_NAME = "index.json"
LOG_NAME = "log.jsonl"
GITATTRIBUTES_NAME = ".gitattributes"
LOCK_NAME = "lock"
SLICES_DIR = "slices"
RENDER_DIR = "render"
TEMPLATES_DIR = "templates"

# How long a second writer waits for the lock before giving up. Overridable so
# tests need not sit through the real wait.
LOCK_TIMEOUT_ENV = "SLICER_LOCK_TIMEOUT"
DEFAULT_LOCK_TIMEOUT = 5.0


def discover(start: Path | None = None) -> Path:
  """Return the repo root holding `.slicer/`, walking up from `start`."""
  here = (start or Path.cwd()).resolve()
  for candidate in [here, *here.parents]:
    if (candidate / DIR_NAME / CONFIG_NAME).is_file():
      return candidate
  raise StateError(
    f"no {DIR_NAME}/ found in {here} or any parent; run `slicer init` in the project root"
  )


def _lock_timeout() -> float:
  raw = os.environ.get(LOCK_TIMEOUT_ENV)
  if raw is None:
    return DEFAULT_LOCK_TIMEOUT
  try:
    return max(0.0, float(raw))
  except ValueError:
    return DEFAULT_LOCK_TIMEOUT


@contextmanager
def project_lock(start: Path | None = None, *, timeout: float | None = None) -> Iterator[None]:
  """Serialise writers on `.slicer/lock` so two mutations cannot interleave.

  `jsonio` makes each file write atomic, but a mutation spans several files; two
  writers racing can mint the same id or half-apply an outline. An advisory
  `flock` around the whole mutation is the cheap, honest fix. It is exclusive
  and best-effort: a second writer waits up to `timeout` and then fails cleanly
  rather than clobbering, and where `flock` is unavailable it is a no-op.
  """
  if fcntl is None:
    yield
    return
  try:
    root = discover(start)
  except StateError:
    # No project here yet -- a create command like `migrate`, or a mistake the
    # command will report itself. Nothing to serialise against, so don't lock.
    yield
    return
  lock_path = root / DIR_NAME / LOCK_NAME
  if timeout is None:
    timeout = _lock_timeout()
  fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o666)
  try:
    deadline = time.monotonic() + timeout
    announced = False
    while True:
      try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        break
      except OSError:
        if time.monotonic() >= deadline:
          raise StateError(
            "another slicer is writing this project; the lock timed out after "
            f"{timeout:g}s. Wait for it to finish, or remove {lock_path} if no "
            "slicer is running.",
            code="locked",
          ) from None
        if not announced:
          print("waiting for another slicer to finish...", file=sys.stderr)
          announced = True
        time.sleep(0.05)
    try:
      yield
    finally:
      fcntl.flock(fd, fcntl.LOCK_UN)
  finally:
    os.close(fd)


@dataclass
class _Stage:
  """Buffered writes for a `State.staged()` transaction.

  A staged mutation records what it would persist here instead of touching
  disk, so a caller can render the proposed state first and either let the
  whole batch land or drop it -- leaving disk untouched -- if the render fails.
  """

  index: bool = False
  slices: dict[str, Slice] = field(default_factory=dict)
  logs: list[LogEntry] = field(default_factory=list)
  moves: list[tuple[Path, Path]] = field(default_factory=list)


@dataclass
class State:
  """Everything on disk for one project, loaded."""

  root: Path
  config: Config
  index: Index
  slices: dict[str, Slice] = field(default_factory=dict)
  # Where each loaded slice was read from, keyed by its *contained* id, so
  # verify can tell a file whose id disagrees with its filename.
  slice_files: dict[str, Path] = field(default_factory=dict)
  # Set only between begin_stage/commit_stage (see `staged`). While present,
  # every persistence side effect buffers here instead of hitting disk, so a
  # mutation can be rendered before it is allowed to land.
  _stage: "_Stage | None" = field(default=None, init=False, repr=False, compare=False)

  @property
  def dir(self) -> Path:
    return self.root / DIR_NAME

  @property
  def slices_dir(self) -> Path:
    return self.dir / SLICES_DIR

  @property
  def done_dir(self) -> Path:
    return self.slices_dir / self.config.done_dir

  @property
  def retired_dir(self) -> Path:
    return self.slices_dir / self.config.retired_dir

  def folders(self) -> tuple[Path, ...]:
    """Every directory a slice file may live in, open first."""
    return (self.slices_dir, self.done_dir, self.retired_dir)

  @property
  def render_dir(self) -> Path:
    return self.dir / RENDER_DIR

  @property
  def templates_dir(self) -> Path:
    return self.dir / TEMPLATES_DIR

  def slice_path(self, item_id: str) -> Path:
    """Where a slice's JSON lives, following its recorded status.

    The folder is the status made visible on disk: open slices sit at the
    top, finished ones under `done/`, retired ones under `retired/`.
    """
    item = self.index.get(item_id)
    status = item.status if item is not None else self.config.open_status
    if status == self.config.done_status:
      folder = self.done_dir
    elif status == self.config.retired_status:
      folder = self.retired_dir
    else:
      folder = self.slices_dir
    # `require_valid` is the containment proof: it admits no separator and no
    # `..`, so the id is always a single component and the join cannot leave
    # `folder`. A resolve-and-compare here would only add symlink checking on
    # slicer's own directory, at roughly sixty times the cost, on a path
    # `verify` walks once per item.
    ids.require_valid(item_id)
    return folder / f"{item_id}.json"

  def find_slice_file(self, item_id: str) -> Path | None:
    # A read, so it reports absence rather than raising: `verify` exists to
    # describe broken state, and must not die on the state it is describing.
    # The write side (`slice_path`) is where an unusable id is refused.
    if not ids.is_valid(item_id):
      return None
    for folder in self.folders():
      candidate = folder / f"{item_id}.json"
      if candidate.is_file():
        return candidate
    return None

  def template(self, name: str) -> str:
    path = self.templates_dir / name
    if not path.is_file():
      raise StateError(f"{path}: template missing; re-run `slicer init --force` to restore it")
    return path.read_text(encoding="utf-8")

  def save_index(self) -> None:
    # Stamp the schema this build writes so an older slicer refuses the file
    # instead of dropping claim fields it does not know.
    self.index.version = SCHEMA_VERSION
    if self._stage is not None:
      # The index is one object; a later save overwrites the same in-memory
      # state, so a single flag stands in for any number of saves.
      self._stage.index = True
      return
    jsonio.write(self.dir / INDEX_NAME, self.index.to_dict())

  def save_slice(self, sl: Slice) -> Path:
    path = self.slice_path(sl.id)
    self.slices[sl.id] = sl
    if self._stage is not None:
      self._stage.slices[sl.id] = sl
      return path
    jsonio.write(path, sl.to_dict())
    return path

  def log(self, entry: LogEntry) -> None:
    if self._stage is not None:
      self._stage.logs.append(entry)
      return
    jsonio.append_jsonl(self.dir / LOG_NAME, entry.to_dict())

  def move_slice(self, src: Path, dst: Path) -> None:
    """Rename a slice file to follow its item's status, buffering when staged.

    The folder is the status made visible on disk, so a status change renames
    the file. Routing it through here (rather than `vcs.move` directly) lets a
    staged mutation defer the rename until its render has succeeded.
    """
    if self._stage is not None:
      self._stage.moves.append((src, dst))
      return
    vcs.move(self.root, src, dst)

  @contextmanager
  def staged(self) -> Iterator[None]:
    """Buffer every persistence side effect, flushing only on a clean exit.

    A mutation runs against the in-memory index and slices as usual, but its
    saves, history lines and slice-file moves are held back until the block
    exits without raising. That lets a caller render the proposed state first
    and abandon the whole batch -- leaving disk untouched -- if render fails.
    Not re-entrant: a nested exit would flush the outer batch early.
    """
    self.begin_stage()
    try:
      yield
    except BaseException:
      self.discard_stage()
      raise
    self.commit_stage()

  def begin_stage(self) -> None:
    if self._stage is not None:
      raise StateError("a staged mutation is already in progress", code="state")
    self._stage = _Stage()

  def discard_stage(self) -> None:
    """Drop every buffered write. Disk was never touched, so nothing to undo."""
    self._stage = None

  def commit_stage(self) -> None:
    """Flush buffered writes, in the order the unstaged paths use."""
    stage = self._stage
    self._stage = None
    if stage is None:
      return
    # Moves and slice bodies before the index that names them, history last, so
    # a crash mid-flush lands where an unstaged mutation would have left it.
    for src, dst in stage.moves:
      vcs.move(self.root, src, dst)
    for sl in stage.slices.values():
      jsonio.write(self.slice_path(sl.id), sl.to_dict())
    if stage.index:
      jsonio.write(self.dir / INDEX_NAME, self.index.to_dict())
    for entry in stage.logs:
      jsonio.append_jsonl(self.dir / LOG_NAME, entry.to_dict())

  def history(self) -> list[LogEntry]:
    path = self.dir / LOG_NAME
    return [_from_dict(path, LogEntry.from_dict, d) for d in jsonio.read_jsonl(path)]


def _from_dict(path: Path, loader, data):
  """Build a dataclass from parsed JSON, naming the file if it is the wrong shape.

  `jsonio.read` already reports bad JSON and encoding; this catches a file
  that is valid JSON but the wrong shape -- a hand-edit that dropped a
  required key, say -- so it reports rather than tracebacking with a bare
  KeyError from deep inside a from_dict.
  """
  try:
    return loader(data)
  except (KeyError, TypeError, ValueError) as e:
    raise StateError(f"{path}: not usable, a required field is missing or malformed: {e}", code="corrupt") from None


def read_index(root: Path) -> Index:
  """Read only a worktree's index, without discovering or loading slice files."""
  path = root / DIR_NAME / INDEX_NAME
  return _from_dict(path, Index.from_dict, jsonio.read(path))


def in_work_elsewhere(root: Path) -> dict[str, list[dict[str, str]]]:
  """Started or claimed items in sibling worktrees, ordered by worktree path."""
  found: dict[str, list[dict[str, str]]] = {}
  for sibling in vcs.sibling_worktrees(root):
    try:
      config = Config.load(sibling / DIR_NAME / CONFIG_NAME)
      index = read_index(sibling)
    except (OSError, SlicerError, AttributeError, KeyError, TypeError, ValueError):
      continue
    for item in index.items:
      if item.claim_owner or (config.started_status and item.status == config.started_status):
        found.setdefault(item.id, []).append({
          "worktree": sibling.name, "owner": item.claim_owner,
        })
  return found


def load(root: Path | None = None) -> State:
  base = discover(root)
  sdir = base / DIR_NAME
  # Config.load and Index.from_dict refuse a newer-than-known schema at the parse
  # boundary (code schema_too_new), so every reader — here and `migrate` — is
  # covered without a save ever running against a file this build cannot read.
  config = Config.load(sdir / CONFIG_NAME)
  index_path = sdir / INDEX_NAME
  if not index_path.is_file():
    raise StateError(f"{index_path}: not found; run `slicer init` first")
  index = read_index(base)
  # `ids.format_id` reads the index's copy of the scheme, not the config's, so
  # validating the config alone leaves a hand-edited index able to crash the
  # formatter. Checked before the reconciliation below, so the same bad value
  # does not get silently repaired when the index happens to be empty.
  if not index.id_prefix:
    raise StateError(f"{index_path}: id_prefix may not be empty", code="config")
  if index.id_width < 1:
    raise StateError(
      f"{index_path}: id_width must be at least 1, not {index.id_width}", code="config"
    )
  # The id scheme lives in the index because allocation must not depend on a
  # config someone edited after ids were handed out. Until the first item
  # exists there is nothing to be inconsistent with, so a changed config still
  # counts -- otherwise `init`, look, change your mind is a dead end. This
  # read-time adjustment is idempotent, and it reaches
  # disk only on the next save. Past that point `verify` reports the mismatch.
  if not index.items and (index.id_prefix, index.id_width) != (
    config.id_prefix,
    config.id_width,
  ):
    index.id_prefix = config.id_prefix
    index.id_width = config.id_width
  slices: dict[str, Slice] = {}
  slice_files: dict[str, Path] = {}
  slices_root = sdir / SLICES_DIR
  for folder in (
    slices_root,
    slices_root / config.done_dir,
    slices_root / config.retired_dir,
  ):
    if not folder.is_dir():
      continue
    for path in sorted(folder.glob("*.json")):
      sl = _from_dict(
        path, lambda d: Slice.from_dict(d, boundary_marker=config.boundary), jsonio.read(path)
      )
      slices[sl.id] = sl
      slice_files[sl.id] = path
  return State(root=base, config=config, index=index, slices=slices, slice_files=slice_files)
