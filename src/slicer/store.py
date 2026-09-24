"""Locating and reading a `.slicer/` tracking directory.

Discovery walks up from the working directory, so any command works from a
subdirectory of a project, the way git does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from slicer import jsonio
from slicer.config import CONFIG_NAME, Config
from slicer.errors import StateError
from slicer.model import Index, LogEntry, Slice

DIR_NAME = ".slicer"
INDEX_NAME = "index.json"
LOG_NAME = "log.jsonl"
SLICES_DIR = "slices"
RENDER_DIR = "render"
TEMPLATES_DIR = "templates"


def discover(start: Path | None = None) -> Path:
  """Return the repo root holding `.slicer/`, walking up from `start`."""
  here = (start or Path.cwd()).resolve()
  for candidate in [here, *here.parents]:
    if (candidate / DIR_NAME / CONFIG_NAME).is_file():
      return candidate
  raise StateError(
    f"no {DIR_NAME}/ found in {here} or any parent; run `slicer init` in the project root"
  )


@dataclass
class State:
  """Everything on disk for one project, loaded."""

  root: Path
  config: Config
  index: Index
  slices: dict[str, Slice] = field(default_factory=dict)

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
    return folder / f"{item_id}.json"

  def find_slice_file(self, item_id: str) -> Path | None:
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
    jsonio.write(self.dir / INDEX_NAME, self.index.to_dict())

  def save_slice(self, sl: Slice) -> Path:
    path = self.slice_path(sl.id)
    jsonio.write(path, sl.to_dict())
    self.slices[sl.id] = sl
    return path

  def log(self, entry: LogEntry) -> None:
    jsonio.append_jsonl(self.dir / LOG_NAME, entry.to_dict())

  def history(self) -> list[LogEntry]:
    return [LogEntry.from_dict(d) for d in jsonio.read_jsonl(self.dir / LOG_NAME)]


def load(root: Path | None = None) -> State:
  base = discover(root)
  sdir = base / DIR_NAME
  config = Config.load(sdir / CONFIG_NAME)
  index_path = sdir / INDEX_NAME
  if not index_path.is_file():
    raise StateError(f"{index_path}: not found; run `slicer init` first")
  index = Index.from_dict(jsonio.read(index_path))
  slices: dict[str, Slice] = {}
  slices_root = sdir / SLICES_DIR
  for folder in (
    slices_root,
    slices_root / config.done_dir,
    slices_root / config.retired_dir,
  ):
    if not folder.is_dir():
      continue
    for path in sorted(folder.glob("*.json")):
      sl = Slice.from_dict(jsonio.read(path))
      slices[sl.id] = sl
  return State(root=base, config=config, index=index, slices=slices)
