"""Per-project configuration: `.slicer/config.json`.

Everything that varies between projects lives here — status words, field
names, section headings, render templates, sync targets. Nothing in this
package may hardcode a path or a vocabulary belonging to one repository.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from slicer.errors import ConfigError

CONFIG_NAME = "config.json"


@dataclass
class SyncTarget:
  """One derived line in a document slicer does not otherwise own."""

  name: str
  path: str
  match: str
  template: str
  count: int = 1

  def to_dict(self) -> dict[str, Any]:
    return {
      "name": self.name,
      "path": self.path,
      "match": self.match,
      "count": self.count,
      "template": self.template,
    }

  @staticmethod
  def from_dict(d: Mapping[str, Any]) -> "SyncTarget":
    for key in ("name", "path", "match", "template"):
      if key not in d:
        raise ConfigError(f"sync target is missing '{key}'")
    return SyncTarget(
      name=d["name"],
      path=d["path"],
      match=d["match"],
      template=d["template"],
      count=int(d.get("count", 1)),
    )


DEFAULT_STATUSES: dict[str, str] = {
  "open": "—",
  "done": "done",
  "parked": "parked",
  "later": "later",
}

DEFAULT_SECTIONS = ["Why", "Files", "Failing tests", "Implement", "Check", "Git"]

DEFAULT_LATER = {
  "group_sep": "; ",
  "item_sep": "/",
  "groups": [
    {"status": "open", "skip_first": True, "prefix": ""},
    {"status": "parked", "skip_first": False, "prefix": "parked "},
    {"status": "later", "skip_first": False, "prefix": "later "},
  ],
  "suffix": "",
}


@dataclass
class Config:
  version: int = 1
  id_prefix: str = "S"
  id_width: int = 2
  statuses: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_STATUSES))
  open_status: str = "open"
  done_status: str = "done"
  retired_status: str = "retired"
  sections: list[str] = field(default_factory=lambda: list(DEFAULT_SECTIONS))
  boundary: str = "**Not in this slice:**"
  done_dir: str = "done"
  retired_dir: str = "retired"
  exclude_flags: list[str] = field(default_factory=list)
  next_format: str = "**{{id}}** {{title}}"
  next_empty: str = "nothing unmarked"
  later: dict[str, Any] = field(default_factory=lambda: json.loads(json.dumps(DEFAULT_LATER)))
  sync_targets: list[SyncTarget] = field(default_factory=list)

  def status_label(self, status: str) -> str:
    return self.statuses.get(status, status)

  def status_for_label(self, label: str) -> str:
    for key, rendered in self.statuses.items():
      if rendered == label:
        return key
    raise ConfigError(f"unknown status {label!r}; known: {sorted(self.statuses.values())}")

  def validate(self) -> None:
    if self.open_status not in self.statuses:
      raise ConfigError(f"open_status {self.open_status!r} is not in statuses")
    if self.done_status not in self.statuses:
      raise ConfigError(f"done_status {self.done_status!r} is not in statuses")
    if self.retired_status and self.retired_status not in self.statuses:
      raise ConfigError(f"retired_status {self.retired_status!r} is not in statuses")
    if len({self.done_dir, self.retired_dir, ""}) != 3:
      raise ConfigError("done_dir and retired_dir must differ, and neither may be empty")
    if len(set(self.statuses.values())) != len(self.statuses):
      raise ConfigError("two statuses render to the same label; they would be indistinguishable")
    if not self.id_prefix:
      raise ConfigError("id.prefix may not be empty")
    if self.id_width < 1:
      raise ConfigError(f"id.width must be at least 1, not {self.id_width}")
    for target in self.sync_targets:
      try:
        re.compile(target.match)
      except re.error as exc:
        raise ConfigError(
          f"sync target {target.name!r}: match {target.match!r} is not a valid "
          f"regular expression: {exc}"
        ) from None

  def to_dict(self) -> dict[str, Any]:
    return {
      "version": self.version,
      "id": {"prefix": self.id_prefix, "width": self.id_width},
      "statuses": dict(self.statuses),
      "open_status": self.open_status,
      "done_status": self.done_status,
      "retired_status": self.retired_status,
      "sections": list(self.sections),
      "boundary": self.boundary,
      "done_dir": self.done_dir,
      "retired_dir": self.retired_dir,
      "exclude_flags": list(self.exclude_flags),
      "pointers": {
        "next_format": self.next_format,
        "next_empty": self.next_empty,
        "later": self.later,
      },
      "sync": {"targets": [t.to_dict() for t in self.sync_targets]},
    }

  @staticmethod
  def from_dict(d: Mapping[str, Any]) -> "Config":
    ident = d.get("id", {})
    pointers = d.get("pointers", {})
    # A config written before `remove` existed lists no retired status, and
    # validate() would reject it. Add that one key, and only that one, so an
    # older tracking directory keeps working without being edited -- while a
    # project that deliberately dropped some other status does not get it back.
    statuses = dict(d.get("statuses", DEFAULT_STATUSES))
    retired = d.get("retired_status", "retired")
    if retired and retired not in statuses:
      statuses[retired] = retired
    cfg = Config(
      version=int(d.get("version", 1)),
      id_prefix=ident.get("prefix", "S"),
      id_width=int(ident.get("width", 2)),
      statuses=statuses,
      open_status=d.get("open_status", "open"),
      done_status=d.get("done_status", "done"),
      retired_status=retired,
      sections=list(d.get("sections", DEFAULT_SECTIONS)),
      boundary=d.get("boundary", "**Not in this slice:**"),
      done_dir=d.get("done_dir", "done"),
      retired_dir=d.get("retired_dir", "retired"),
      exclude_flags=list(d.get("exclude_flags", [])),
      next_format=pointers.get("next_format", "**{{id}}** {{title}}"),
      next_empty=pointers.get("next_empty", "nothing unmarked"),
      later=pointers.get("later", json.loads(json.dumps(DEFAULT_LATER))),
      sync_targets=[SyncTarget.from_dict(t) for t in d.get("sync", {}).get("targets", [])],
    )
    cfg.validate()
    return cfg

  @staticmethod
  def load(path: Path) -> "Config":
    try:
      raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
      raise ConfigError(f"{path}: not found; run `slicer init` first") from None
    except json.JSONDecodeError as e:
      raise ConfigError(f"{path}: invalid JSON: {e}") from None
    return Config.from_dict(raw)
