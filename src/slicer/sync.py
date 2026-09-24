"""Rewrite derived lines in documents slicer does not otherwise own.

A project often repeats its queue in prose — a `Status:` line, a "Next:"
pointer. Those are projections of the index and should be generated, not
retyped. Which files, which line, and what it says are entirely config.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from slicer.config import Config, SyncTarget
from slicer.errors import ConfigError
from slicer.model import Index, Item
from slicer.render import expand


@dataclass
class SyncFinding:
  target: str
  path: str
  stale: bool
  detail: str = ""


def _eligible(index: Index, cfg: Config) -> list[Item]:
  """Items the pointers may name, honouring configured flag exclusions."""
  excluded = set(cfg.exclude_flags)
  return [it for it in index.items if not (excluded & set(it.flags))]


def next_item(index: Index, cfg: Config) -> Item | None:
  """The first open item in table order.

  Deliberately not dependency-aware: this reproduces a queue pointer that
  humans read top-down. `slicer next` is the dependency-aware command, and a
  test locks the difference so nobody reconciles them by accident.
  """
  for item in _eligible(index, cfg):
    if item.status == cfg.open_status:
      return item
  return None


def next_pointer(index: Index, cfg: Config) -> str:
  item = next_item(index, cfg)
  if item is None:
    return cfg.next_empty
  return expand(cfg.next_format, {"id": item.id, "title": item.display_title()})


def later_pointer(index: Index, cfg: Config) -> str:
  spec = cfg.later
  items = _eligible(index, cfg)
  parts: list[str] = []
  for group in spec.get("groups", []):
    ids = [it.id for it in items if it.status == group.get("status")]
    if group.get("skip_first"):
      ids = ids[1:]
    if ids:
      parts.append(group.get("prefix", "") + spec.get("item_sep", "/").join(ids))
  suffix = spec.get("suffix", "")
  if suffix:
    parts.append(suffix)
  return spec.get("group_sep", "; ").join(parts)


def render_target(index: Index, cfg: Config, target: SyncTarget) -> str:
  return expand(
    target.template,
    {
      "next": next_pointer(index, cfg),
      "later": later_pointer(index, cfg),
      "count": str(len(index.items)),
      "open": str(len(index.by_status(cfg.open_status))),
      "done": str(len(index.by_status(cfg.done_status))),
    },
  )


def apply(root: Path, index: Index, cfg: Config, *, check_only: bool) -> list[SyncFinding]:
  """Rewrite (or, with `check_only`, report) every configured target."""
  findings: list[SyncFinding] = []
  for target in cfg.sync_targets:
    path = root / target.path
    if not path.is_file():
      findings.append(SyncFinding(target.name, target.path, True, "file not found"))
      continue
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(target.match, re.M)
    matches = pattern.findall(text)
    if len(matches) != target.count:
      raise ConfigError(
        f"sync target {target.name!r}: pattern {target.match!r} matched "
        f"{len(matches)} lines in {target.path}, expected {target.count}"
      )
    line = render_target(index, cfg, target)
    # A function replacement is used verbatim, so backslashes in the rendered
    # line stay literal rather than being read as group references.
    updated = pattern.sub(lambda _m: line, text, count=target.count)
    if updated == text:
      findings.append(SyncFinding(target.name, target.path, False))
      continue
    findings.append(SyncFinding(target.name, target.path, True, "line is stale"))
    if not check_only:
      path.write_text(updated, encoding="utf-8", newline="\n")
  return findings
