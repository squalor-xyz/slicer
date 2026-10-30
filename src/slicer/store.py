"""Locating and reading a `.slicer/` tracking directory.

Discovery walks up from the working directory, so any command works from a
subdirectory of a project, the way git does.
"""

from __future__ import annotations

import copy
import os
import re
import stat
import sys
import time
from collections.abc import Callable, Mapping, MutableMapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from slicer import ids, jsonio, vcs
from slicer.config import CONFIG_NAME, Config
from slicer.errors import SlicerError, StateError
from slicer.model import SCHEMA_VERSION, Index, Item, LogEntry, Slice
from slicer.roles import RoleMatrix

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
ROLES_NAME = "relay-roles.json"
# What to do when a merge left Git conflict markers in a state file, appended to the
# `corrupt` error. Both name `slicer setup-git`, whose drivers keep a merge from leaving them.
INDEX_CONFLICT_HINT = (
  "Resolve them by hand; `slicer setup-git` prints the merge drivers that keep a merge "
  "from leaving them"
)
ROLES_CONFLICT_HINT = (
  f"Never edit {ROLES_NAME} by hand: keep one side with "
  f"`git checkout --ours|--theirs .slicer/{ROLES_NAME}`, `git add` it, finish the merge, then "
  "re-apply the other side's changes with `slicer relay-roles`; `slicer setup-git` prints the "
  "merge driver that keeps a merge from leaving markers"
)
# The hand-edited matrix that ROLES_NAME replaced. Nothing reads it any more.
LEGACY_ROLES_NAME = "relay-roles.md"
GITATTRIBUTES_NAME = ".gitattributes"
LOCK_NAME = "lock"
SLICES_DIR = "slices"
RENDER_DIR = "render"
TEMPLATES_DIR = "templates"

# How long a second writer waits for the lock before giving up. Overridable so
# tests need not sit through the real wait.
LOCK_TIMEOUT_ENV = "SLICER_LOCK_TIMEOUT"
DEFAULT_LOCK_TIMEOUT = 5.0


def _template_missing(path: Path, name: str) -> str:
  return f"{path}: template missing; run `slicer template reset {name}` to restore it"


def read_template_exact(root: Path, name: str) -> str | None:
  """One project template without loading the project; None when the file is missing."""
  path = root / DIR_NAME / TEMPLATES_DIR / name
  try:
    return path.read_bytes().decode("utf-8")
  except FileNotFoundError:
    return None
  except UnicodeDecodeError as exc:
    raise StateError(f"{path}: not valid UTF-8: {exc}", code="io") from None


def require_template_text(root: Path, name: str) -> str:
  """`read_template_exact`, but a missing file is the `template missing` error."""
  text = read_template_exact(root, name)
  if text is None:
    raise StateError(_template_missing(root / DIR_NAME / TEMPLATES_DIR / name, name))
  return text


def discover(start: Path | None = None) -> Path:
  """Return the repo root holding `.slicer/`, walking up from `start`."""
  here = (start or Path.cwd()).resolve()
  for candidate in [here, *here.parents]:
    if (candidate / DIR_NAME / CONFIG_NAME).is_file():
      return candidate
  raise StateError(
    f"no {DIR_NAME}/ found in {here} or any parent; run `slicer init` in the project root"
  )


def _holds_config(directory: Path) -> bool:
  """True when `directory/.slicer/config.json` is a file; an unreadable path raises.

  `Path.is_file` swallows permission errors on some Python versions and raises on
  others, so `discover_all` probes with `os.stat` to behave the same on all of them.
  """
  try:
    return stat.S_ISREG(os.stat(directory / DIR_NAME / CONFIG_NAME).st_mode)
  except (FileNotFoundError, NotADirectoryError):
    return False


def by_relative_parts(top: Path):
  """The sort key shared by projects and unreadable directories under `top`."""
  def key(item: Path | tuple[Path, OSError]) -> tuple[str, ...]:
    path = item[0] if isinstance(item, tuple) else item
    return path.relative_to(top).parts
  return key


def discover_all(start: Path | None = None) -> tuple[list[Path], list[tuple[Path, OSError]]]:
  """Return every project root at or below `start`, never walking up.

  `discover` answers "which project am I in"; a monorepo or a folder of repos
  needs "which projects are here". A directory is a project when it holds
  `.slicer/config.json`. The walk keeps descending inside a project, so a project
  nested in another is found too. It skips dot-directories below `start`
  (`.git`, `.slicer`, `.worktrees`, `.venv`), `node_modules` and symlinked
  directories: a worktree or a link would otherwise report the same project twice.
  The result is absolute and sorted by path relative to `start`, so output is
  deterministic.

  Returns `(found, unreadable)`. A directory the walk cannot list, or whose
  `.slicer/config.json` cannot be examined for a reason other than being absent,
  may hold a project, so it is not silently dropped: `unreadable` holds
  `(directory, error)` for it, sorted the same way. A start that is missing or
  not a directory yields neither. Nothing is printed here.
  """
  top = (start or Path.cwd()).resolve()
  found: list[Path] = []
  failed: list[tuple[Path, OSError]] = []

  def walk_failed(exc: OSError) -> None:
    if isinstance(exc, (FileNotFoundError, NotADirectoryError)):
      return  # absent or not a directory (also a directory removed mid-walk): no project
    failed.append((Path(exc.filename) if exc.filename else top, exc))

  # followlinks=False lists a symlinked directory but never enters it.
  for here, dirnames, _ in os.walk(top, followlinks=False, onerror=walk_failed):
    dirnames[:] = sorted(
      name for name in dirnames if not name.startswith(".") and name != "node_modules"
    )
    candidate = Path(here)
    try:
      if _holds_config(candidate):
        found.append(candidate)
    except OSError as exc:
      failed.append((candidate, exc))
  key = by_relative_parts(top)
  return sorted(found, key=key), sorted(failed, key=key)


def _lock_timeout() -> float:
  raw = os.environ.get(LOCK_TIMEOUT_ENV)
  if raw is None:
    return DEFAULT_LOCK_TIMEOUT
  try:
    return max(0.0, float(raw))
  except ValueError:
    return DEFAULT_LOCK_TIMEOUT


@contextmanager
def file_lock(path: Path, *, timeout: float | None = None) -> Iterator[None]:
  """Take an exclusive flock on a file, waiting up to the configured timeout.

  Where `flock` is unavailable this is a no-op. Lock files may live in a shared
  directory that does not exist yet, so create the parent before opening the file.
  """
  if fcntl is None:
    yield
    return
  if timeout is None:
    timeout = _lock_timeout()
  path.parent.mkdir(parents=True, exist_ok=True)
  fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o666)
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
            f"{timeout:g}s. Wait for it to finish, or remove {path} if no "
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
  with file_lock(lock_path, timeout=timeout):
    # `feedback` imports this module, hence the local import.
    from slicer import feedback

    # Every writer lists the lock, so a project made before `init` listed the lock gets the
    # line on its next write. The line is advisory: an unreadable or unwritable
    # .slicer/.gitignore must not block the write, so the error is ignored here.
    try:
      feedback.ensure_gitignore(root, LOCK_NAME)
    except (OSError, UnicodeDecodeError):
      pass
    yield


_UNLOADED = object()


@dataclass
class _Stage:
  """Buffered writes for a `State.staged()` transaction.

  A staged mutation records what it would persist here instead of touching
  disk, so a caller can render the proposed state first and either let the
  whole batch land or drop it -- leaving disk untouched -- if the render fails.
  """

  index: bool = False
  roles: bool = False
  slices: dict[str, Slice] = field(default_factory=dict)
  logs: list[LogEntry] = field(default_factory=list)
  moves: list[tuple[Path, Path]] = field(default_factory=list)
  # Files to unlink after the index write. A purge records the path here so a
  # failed render never deletes a file the index still names.
  deletes: list[Path] = field(default_factory=list)
  config: dict | None = None
  templates: dict[str, str] = field(default_factory=dict)


class LazySlices(MutableMapping[str, Slice]):
  """Defer historical bodies while preserving eager dict order on enumeration."""

  def __init__(
    self, files: dict[str, Path], boundary: str,
    expects_body: Callable[[str], bool] | None = None,
  ) -> None:
    self._files = files
    self._boundary = boundary
    # True for an id the index marks `has_slice`. Only such an id can live in a
    # misnamed file, so only its miss is worth a full load; a row-only or unknown
    # id stays lazy and a corrupt historical body never blocks it. None means
    # every miss forces the load.
    self._expects_body = expects_body
    self._read: dict[str, Slice] = {}
    self._cache: dict[str, Slice] = {}
    self._paths: dict[str, Path] = {}
    self._writes: dict[str, Slice] = {}
    self._deleted: set[str] = set()
    self._loaded = False

  def _read_one(self, stem: str) -> Slice:
    if stem not in self._read:
      path = self._files[stem]
      try:
        data = jsonio.read(path)
      except FileNotFoundError:
        # The path map was built at load; a file moved or removed since then
        # is a corrupt tree, not a raw OS error.
        raise StateError(
          f"{path}: slice file is gone; it was moved or removed after the roadmap loaded"
          " (reload and run `slicer verify`)", code="corrupt"
        ) from None
      self._read[stem] = _from_dict(
        path, lambda d: Slice.from_dict(d, boundary_marker=self._boundary), data
      )
    return self._read[stem]

  def _load_all(self) -> None:
    if self._loaded:
      return
    cache: dict[str, Slice] = {}
    paths: dict[str, Path] = {}
    for stem, path in self._files.items():
      sl = self._read_one(stem)
      cache[sl.id] = sl
      paths[sl.id] = path
    cache.update(self._writes)
    for sid in self._deleted:
      cache.pop(sid, None)
    self._cache, self._paths = cache, paths
    self._loaded = True

  def __getitem__(self, sid: str) -> Slice:
    if sid in self._deleted:
      raise KeyError(sid)
    if sid in self._cache:
      return self._cache[sid]
    if not self._loaded and sid in self._files:
      sl = self._read_one(sid)
      if sl.id != sid:
        self._load_all()
      else:
        self._cache[sid] = sl
        self._paths[sid] = self._files[sid]
    if (sid not in self._cache and not self._loaded
        and (self._expects_body is None or self._expects_body(sid))):
      # A has_slice id with no file of its stem may live in a misnamed file;
      # one full load settles it.
      self._load_all()
    return self._cache[sid]

  def __setitem__(self, sid: str, sl: Slice) -> None:
    self._deleted.discard(sid)
    self._writes[sid] = sl
    self._cache[sid] = sl

  def __delitem__(self, sid: str) -> None:
    self[sid]
    self._deleted.add(sid)
    self._writes.pop(sid, None)
    del self._cache[sid]

  def __iter__(self) -> Iterator[str]:
    self._load_all()
    return iter(self._cache)

  def __len__(self) -> int:
    self._load_all()
    return len(self._cache)

  def keys(self):
    self._load_all()
    return self._cache.keys()

  def values(self):
    self._load_all()
    return self._cache.values()

  def items(self):
    self._load_all()
    return self._cache.items()

  def relocated(self, src: Path, dst: Path) -> None:
    """Follow a completed rename without parsing the body it moved."""
    self._files[src.stem] = dst
    if src.stem in self._read:
      self._paths[self._read[src.stem].id] = dst

  def copy(self) -> LazySlices:
    """A draft owns its discovered paths, cache and edits."""
    other = LazySlices(self._files.copy(), self._boundary, self._expects_body)
    other._read = self._read.copy()
    other._cache = self._cache.copy()
    other._paths = self._paths.copy()
    other._writes = self._writes.copy()
    other._deleted = self._deleted.copy()
    other._loaded = self._loaded
    return other


class SliceFiles(Mapping[str, Path]):
  """Keep verifier paths in step with the bodies that establish contained ids."""

  def __init__(self, slices: LazySlices) -> None:
    self._slices = slices

  def __getitem__(self, sid: str) -> Path:
    if not self._slices._loaded:
      self._slices[sid]
    return self._slices._paths[sid]

  def __iter__(self) -> Iterator[str]:
    self._slices._load_all()
    return iter(self._slices._paths)

  def __len__(self) -> int:
    self._slices._load_all()
    return len(self._slices._paths)


@dataclass
class State:
  """Project metadata with slice bodies read only when requested."""

  root: Path
  config: Config
  index: Index
  slices: MutableMapping[str, Slice] = field(default_factory=dict)
  # Where each loaded slice was read from, keyed by its *contained* id, so
  # verify can tell a file whose id disagrees with its filename.
  slice_files: Mapping[str, Path] = field(default_factory=dict)
  # Set only between begin_stage/commit_stage (see `staged`). While present,
  # every persistence side effect buffers here instead of hitting disk, so a
  # mutation can be rendered before it is allowed to land.
  _stage: "_Stage | None" = field(default=None, init=False, repr=False, compare=False)
  # The relay role matrix, read on first use: most commands never need it.
  _roles: "RoleMatrix | None | object" = field(
    default=_UNLOADED, init=False, repr=False, compare=False,
  )

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
    """A project template, or the text a staged change would give it."""
    if self._stage is not None and name in self._stage.templates:
      return self._stage.templates[name]
    path = self.templates_dir / name
    if not path.is_file():
      raise StateError(_template_missing(path, name))
    return path.read_text(encoding="utf-8")

  def template_exact(self, name: str) -> str | None:
    """A template's text as stored, newlines untranslated, or None when it is missing.

    `template` reads like the renderer does; this is for comparing against a
    default or printing the file, where CRLF must stay CRLF.
    """
    if self._stage is not None and name in self._stage.templates:
      return self._stage.templates[name]
    return read_template_exact(self.root, name)

  def save_template(self, name: str, text: str) -> None:
    """Write a template, or hold it until the staged change commits."""
    if self._stage is not None:
      self._stage.templates[name] = text
      return
    jsonio.write_text(self.templates_dir / name, text)

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

  @property
  def roles(self) -> RoleMatrix | None:
    """The relay role matrix, or None when the project has none."""
    if self._roles is _UNLOADED:
      self._roles = read_roles(self.root)
    return self._roles  # type: ignore[return-value]

  @roles.setter
  def roles(self, matrix: RoleMatrix | None) -> None:
    self._roles = matrix

  def save_roles(self) -> None:
    matrix = self.roles
    if matrix is None:
      raise StateError("there is no relay role matrix to save", code="state")
    if self._stage is not None:
      self._stage.roles = True
      return
    jsonio.write(self.dir / ROLES_NAME, matrix.to_dict())

  def delete_file(self, path: Path) -> None:
    """Unlink a file after the writes that replace it, when this change is staged."""
    if self._stage is not None:
      self._stage.deletes.append(path)
      return
    path.unlink()

  def save_slice(self, sl: Slice) -> Path:
    path = self.slice_path(sl.id)
    self.slices[sl.id] = sl
    if self._stage is not None:
      self._stage.slices[sl.id] = sl
      return path
    jsonio.write(path, sl.to_dict())
    return path

  def config_raw(self) -> dict:
    """A copy of config.json as written, or as this staged change last held it.

    Not `Config.to_dict()`: loading back-fills statuses and roles the file may
    not spell out, and a one-key change should not write those into someone's
    config.
    """
    held = self._stage.config if self._stage is not None else None
    return copy.deepcopy(held if held is not None else jsonio.read(self.dir / CONFIG_NAME))

  def save_config_raw(self, raw: dict) -> None:
    """Write config.json from `raw`, or hold it until the staged change commits."""
    if self._stage is not None:
      self._stage.config = raw
      return
    jsonio.write(self.dir / CONFIG_NAME, raw)

  def save_config_prefix(self, prefix: str) -> None:
    """Write a new id prefix into config.json, touching no other key."""
    raw = self.config_raw()
    ident = raw.get("id")
    raw["id"] = (dict(ident) if isinstance(ident, dict) else {}) | {"prefix": prefix}
    self.save_config_raw(raw)
    self.config.id_prefix = prefix

  def log(self, entry: LogEntry) -> None:
    if self._stage is not None:
      self._stage.logs.append(entry)
      return
    jsonio.append_jsonl(self.dir / LOG_NAME, entry.to_dict())

  def move_slice(self, src: Path, dst: Path) -> None:
    """Rename a slice file to follow its item's status, buffering when staged.

    The folder is the status made visible on disk, so a status change renames
    the file in the worktree only. The git index is left untouched. Routing it
    through here (rather than `vcs.move` directly) lets a staged mutation defer
    the rename until its render has succeeded.
    """
    if self._stage is not None:
      self._stage.moves.append((src, dst))
      return
    vcs.move(self.root, src, dst)
    if isinstance(self.slices, LazySlices):
      self.slices.relocated(src, dst)

  def delete_slice(self, path: Path) -> None:
    """Unlink a slice file, after the index write when this change is staged.

    Purge saves the index first and deletes the file second. Buffering the
    unlink keeps that order, and lets a failed render drop the delete.
    """
    self.delete_file(path)

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
      if isinstance(self.slices, LazySlices):
        self.slices.relocated(src, dst)
    for sl in stage.slices.values():
      jsonio.write(self.slice_path(sl.id), sl.to_dict())
    if stage.index:
      jsonio.write(self.dir / INDEX_NAME, self.index.to_dict())
    if stage.roles and self.roles is not None:
      jsonio.write(self.dir / ROLES_NAME, self.roles.to_dict())
    # After the index, matching an unstaged purge: a crash here leaves an
    # orphan file, not a row whose slice was already deleted.
    for path in stage.deletes:
      path.unlink()
    # After the index, which owns the id scheme: a crash between the two leaves
    # a mismatch `verify` reports and re-running `id-prefix` repairs.
    if stage.config is not None:
      jsonio.write(self.dir / CONFIG_NAME, stage.config)
    # Nothing names a template, so no write has to precede it; history stays last.
    for name, text in stage.templates.items():
      jsonio.write_text(self.templates_dir / name, text)
    try:
      for entry in stage.logs:
        jsonio.append_jsonl(self.dir / LOG_NAME, entry.to_dict())
    except OSError as exc:
      raise StateError(
        f"saved, but history could not be written: {exc}", code="io",
      ) from exc

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


def read_roles(root: Path) -> RoleMatrix | None:
  """Read only the relay role matrix. A missing file is None; a bad one is `corrupt`."""
  path = root / DIR_NAME / ROLES_NAME
  if not path.exists():
    return None
  try:
    data = _read_state_json(path, ROLES_CONFLICT_HINT)
  except OSError as exc:
    raise StateError(f"{path} cannot be read: {exc}", code="io") from exc
  return _from_dict(path, RoleMatrix.from_dict, data)


def legacy_roles_advice(root: Path) -> str | None:
  """What to do about a leftover `relay-roles.md`, or None when there is none.

  Nothing reads that file any more. The same command removes it either way, but once
  the JSON exists it keeps the matrix someone built instead of writing the example.
  """
  base = root / DIR_NAME
  if not (base / LEGACY_ROLES_NAME).is_file():
    return None
  if (base / ROLES_NAME).exists():
    return (
      f"legacy_relay_roles: {DIR_NAME}/{LEGACY_ROLES_NAME} is ignored; the matrix is "
      f"{DIR_NAME}/{ROLES_NAME}. Run `slicer relay-roles init --replace-legacy` to remove "
      "the old file and keep the matrix."
    )
  return (
    f"legacy_relay_roles: {DIR_NAME}/{LEGACY_ROLES_NAME} is ignored; the matrix is now "
    f"{DIR_NAME}/{ROLES_NAME}. Run `slicer relay-roles init --replace-legacy` to write "
    "the example matrix and remove the old file."
  )


def _read_state_json(path: Path, conflict_hint: str) -> Any:
  """`jsonio.read`, with the file's own advice added when a merge left conflict markers in it."""
  try:
    return jsonio.read(path)
  except StateError as exc:
    if exc.code == "corrupt" and jsonio.has_conflict_markers(path):
      raise StateError(f"{exc}. {conflict_hint}", code="corrupt") from None
    raise


def read_index(root: Path) -> Index:
  """Read only a worktree's index, without discovering or loading slice files."""
  path = root / DIR_NAME / INDEX_NAME
  return _from_dict(path, Index.from_dict, _read_state_json(path, INDEX_CONFLICT_HINT))


def _elsewhere_counts(
  config: Config, item: Item, local: Item | None, local_done: str,
) -> bool:
  """Whether one sibling row is work this checkout should see.

  Claimed and in-work rows always count. Review and done count only when this
  checkout's copy has a different status and is not itself done. An empty
  review status is not that extra case.
  """
  if item.claim_owner or item.status in config.in_work():
    return True
  if local is None or local.status == local_done or local.status == item.status:
    return False
  if item.status == config.done_status:
    return True
  return bool(config.review_status) and item.status == config.review_status


def _read_siblings(root: Path) -> list[tuple[Path, Index]]:
  """Each sibling checkout with its index, skipping any that cannot be read."""
  found: list[tuple[Path, Index]] = []
  for sibling in vcs.sibling_worktrees(root):
    try:
      found.append((sibling, read_index(sibling)))
    except (OSError, SlicerError, AttributeError, KeyError, TypeError, ValueError):
      continue
  return found


def sibling_ids(root: Path) -> list[tuple[str, Index]]:
  """`(worktree name, index)` for every readable sibling checkout.

  Like `in_work_elsewhere`, this reads nothing when `root` is a directory
  inside some other checkout, because that checkout's siblings are not this
  project's.
  """
  return [(path.name, index) for path, index in sibling_checkouts(root)]


def sibling_checkouts(root: Path) -> list[tuple[Path, Index]]:
  """`(path, index)` for every readable sibling checkout, under `sibling_ids`' rule."""
  if not vcs.at_worktree_root(root):
    return []
  return _read_siblings(root)


def _id_order(item_id: str) -> list[tuple[int, int, str]]:
  """Sort key that puts `s9` before `s10`: digit runs compare as numbers."""
  return [
    (1, int(part), "") if part.isdigit() else (0, 0, part.casefold())
    for part in re.split(r"(\d+)", item_id) if part
  ]


def displayed_status(config: Config, item: Item, siblings: list[dict[str, str]]) -> str:
  """Expose a sibling handoff without changing this checkout's stored status."""
  if (item.status == config.open_status and config.review_status
      and any(row["status"] == config.review_status for row in siblings)):
    return config.review_status
  return item.status


def claim_cell(
  config: Config, item: Item, siblings: list[dict[str, str]] | None = None,
) -> str:
  """The CLAIM cell: the owner when claimed, `*` when in progress without one,
  `wt:NAME` for sibling work (`wt:NAME+N` for N more), otherwise `-`.

  A finished item's local claim is hidden, but sibling work remains visible.
  `list` and the TUI queue both print this cell, so their columns agree.
  """
  if item.status != config.done_status and item.claim_owner:
    return item.claim_owner
  if item.status in config.in_work():
    return "*"
  if siblings:
    extra = f"+{len(siblings) - 1}" if len(siblings) > 1 else ""
    return f"wt:{siblings[0]['worktree']}{extra}"
  return "-"


def in_work_elsewhere(root: Path, index: Index) -> dict[str, list[dict[str, str]]]:
  """Sibling work this checkout has not reached, ordered by worktree path.

  Each entry is `{worktree, owner, status}` with the sibling's status key.
  `index` is this checkout's copy, used to tell a real handoff or done from
  the same state already here.
  """
  return sibling_work(root, index)[0]


def only_in_sibling(root: Path, index: Index) -> list[dict[str, str]]:
  """Open sibling items whose id this checkout does not have.

  Each row is `{id, title, worktree, status}`, ordered by worktree path and then
  id. Claimed and in-work rows are left to `in_work_elsewhere`, and a sibling's
  done or review rows are not new work.
  """
  return sibling_work(root, index)[1]


def sibling_work(
  root: Path, index: Index,
) -> tuple[dict[str, list[dict[str, str]]], list[dict[str, str]]]:
  """`(in_work_elsewhere, only_in_sibling)` from one read of the sibling worktrees.

  `list` and `status` need both, and each worktree enumeration and index read is
  a git call, so they share this pass instead of making it twice.
  """
  # A directory inside some other checkout is not that checkout's project.
  # Reading its worktrees would treat another index's done items as this one.
  if not vcs.at_worktree_root(root):
    return {}, []
  local_done = Config.load(root / DIR_NAME / CONFIG_NAME).done_status
  local_by_id = {item.id: item for item in index.items}
  found: dict[str, list[dict[str, str]]] = {}
  unseen: list[dict[str, str]] = []
  for sibling, sibling_index in _read_siblings(root):
    try:
      config = Config.load(sibling / DIR_NAME / CONFIG_NAME)
    except (OSError, SlicerError, AttributeError, KeyError, TypeError, ValueError):
      continue
    fresh = []
    for item in sibling_index.items:
      if item.id not in local_by_id:
        if item.status == config.open_status and not item.claim_owner and item.status not in config.in_work():
          fresh.append(item)
      if not _elsewhere_counts(config, item, local_by_id.get(item.id), local_done):
        continue
      found.setdefault(item.id, []).append({
        "worktree": sibling.name,
        "owner": item.claim_owner,
        "status": item.status,
      })
    unseen.extend(
      {"id": item.id, "title": item.title, "worktree": sibling.name, "status": item.status}
      for item in sorted(fresh, key=lambda item: _id_order(item.id))
    )
  return found, unseen


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
  files: dict[str, Path] = {}
  slices_root = sdir / SLICES_DIR
  for folder in (
    slices_root,
    slices_root / config.done_dir,
    slices_root / config.retired_dir,
  ):
    if not folder.is_dir():
      continue
    for path in sorted(folder.glob("*.json")):
      files[path.stem] = path
  def expects_body(sid: str) -> bool:
    item = index.get(sid)
    return item is not None and item.has_slice

  slices = LazySlices(files, config.boundary, expects_body)
  return State(root=base, config=config, index=index, slices=slices,
               slice_files=SliceFiles(slices))
