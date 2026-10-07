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

from slicer import github, ids
from slicer.errors import ConfigError, reject_future_schema

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
# What `next` treats as unspecified when the body is empty. A config written
# before this key existed keeps that historical pair.
DEFAULT_REQUIRED_SECTIONS = ["Implement", "Check"]

def _claim_owner(value: object) -> str:
  if not isinstance(value, str):
    raise ConfigError("claim_owner must be a string")
  return value


def _required_sections(d: Mapping[str, Any]) -> list[str]:
  """Missing key keeps the historical Implement and Check check. No schema bump."""
  if "required_sections" not in d:
    return list(DEFAULT_REQUIRED_SECTIONS)
  raw = d["required_sections"]
  if not isinstance(raw, list) or any(not isinstance(name, str) for name in raw):
    raise ConfigError("required_sections must be a list of section names")
  return list(raw)


def _implement_finish(d: Mapping[str, Any]) -> str:
  """Missing key stays done. No schema bump."""
  if "implement_finish" not in d:
    return "done"
  raw = d["implement_finish"]
  if not isinstance(raw, str):
    raise ConfigError("implement_finish must be 'done' or 'handoff'")
  return raw


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


# The config-file schema version this build writes and fully understands. A
# higher number on disk means a newer slicer wrote it; `store.load` refuses it
# rather than dropping the keys this build does not know. Bump only alongside a
# reader that lifts the older shape.
SCHEMA_VERSION = 5


@dataclass
class Config:
  version: int = SCHEMA_VERSION
  note_kinds: list[str] = field(default_factory=list)
  handoff_requires_note_kind: str = ""
  id_prefix: str = "S"
  id_width: int = 2
  statuses: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_STATUSES))
  open_status: str = "open"
  done_status: str = "done"
  retired_status: str = "retired"
  parked_status: str = "parked"
  started_status: str = "started"
  # Where `handoff` puts a started slice that is ready for someone else to
  # review, merge, or clean up. Empty disables `handoff`.
  review_status: str = "review"
  # Where `start` puts a review item a reviewer has claimed. It keeps a review in
  # progress out of `next`, which would otherwise resume it as started work.
  # Empty makes `start` on a review item move it to started, as before.
  reviewing_status: str = "reviewing"
  sections: list[str] = field(default_factory=lambda: list(DEFAULT_SECTIONS))
  # Headings `next` skips a slice for when the body is empty. Empty means
  # `next` does not skip a slice for an empty section.
  required_sections: list[str] = field(default_factory=lambda: list(DEFAULT_REQUIRED_SECTIONS))
  boundary: str = "**Not in this slice:**"
  done_dir: str = "done"
  retired_dir: str = "retired"
  exclude_flags: list[str] = field(default_factory=list)
  next_format: str = "**{{id}}** {{title}}"
  next_empty: str = "nothing unmarked"
  later: dict[str, Any] = field(default_factory=lambda: json.loads(json.dumps(DEFAULT_LATER)))
  sync_targets: list[SyncTarget] = field(default_factory=list)
  # `verify`'s git cross-check. Off for projects whose history cannot satisfy
  # it -- a repo split from another, where items were finished before it began.
  git_check: bool = True
  # `verify`'s render-merge-driver reminder. On by default, but only fires when
  # this checkout has sibling worktrees (the workflow the driver serves). Off
  # silences it entirely, for a project that does not use the driver.
  render_driver_check: bool = True
  # Who `start` records when it claims an item. Empty means git user.name,
  # then the worktree directory name.
  claim_owner: str = ""
  # `handoff` needs a review status to move the item to. Missing key stays `done`.
  implement_finish: str = "done"
  # Statuses that let a dependent start. None (the key absent) means `done_status`
  # alone. An explicit list is authoritative: it is not widened to include done.
  satisfies_dependencies: list[str] | None = None
  # Where `feedback-report` files issues, as OWNER/REPO. Empty means no default:
  # the command needs `--repo`, and nothing is inferred from a git remote.
  issues_repo: str = ""

  def in_work(self) -> set[str]:
    """Statuses that mean someone is on an item: started, or reviewing."""
    return {self.started_status, self.reviewing_status} - {""}

  def satisfying_statuses(self) -> frozenset[str]:
    """Statuses a dependency may be in for its dependents to proceed."""
    if self.satisfies_dependencies is None:
      return frozenset({self.done_status})
    return frozenset(self.satisfies_dependencies)

  def status_label(self, status: str) -> str:
    return self.statuses.get(status, status)

  def status_for_label(self, label: str) -> str:
    for key, rendered in self.statuses.items():
      if rendered == label:
        return key
    raise ConfigError(f"unknown status {label!r}; known: {sorted(self.statuses.values())}")

  def validate(self) -> None:
    if not isinstance(self.note_kinds, list) or any(
      not isinstance(kind, str) or not kind.strip() for kind in self.note_kinds
    ):
      raise ConfigError("note_kinds must be a list of nonempty strings")
    if not isinstance(self.handoff_requires_note_kind, str):
      raise ConfigError("handoff_requires_note_kind must be a string")
    if self.handoff_requires_note_kind and not self.handoff_requires_note_kind.strip():
      raise ConfigError("handoff_requires_note_kind must be empty or a nonempty note kind")
    if (self.handoff_requires_note_kind and self.note_kinds
        and self.handoff_requires_note_kind not in self.note_kinds):
      raise ConfigError(
        f"handoff_requires_note_kind {self.handoff_requires_note_kind!r} is not in note_kinds; "
        "add the required kind to the whitelist"
      )
    if not isinstance(self.issues_repo, str):
      raise ConfigError("issues_repo must be a string")
    if self.issues_repo and not github.valid_repo(self.issues_repo):
      raise ConfigError(
        f"issues_repo {self.issues_repo!r} is not OWNER/REPO; use letters, digits, '.', '_' or '-' "
        "in each part, or leave it empty"
      )
    if self.satisfies_dependencies is not None:
      listed = self.satisfies_dependencies
      if not isinstance(listed, list) or not listed or any(
        not isinstance(status, str) for status in listed
      ):
        raise ConfigError("satisfies_dependencies must be a nonempty list of status keys")
      unknown = sorted({status for status in listed if status not in self.statuses})
      if unknown:
        raise ConfigError(
          f"satisfies_dependencies names unknown status {unknown}; "
          f"known keys: {sorted(self.statuses)}"
        )
    if self.open_status not in self.statuses:
      raise ConfigError(f"open_status {self.open_status!r} is not in statuses")
    if self.done_status not in self.statuses:
      raise ConfigError(f"done_status {self.done_status!r} is not in statuses")
    if self.retired_status and self.retired_status not in self.statuses:
      raise ConfigError(f"retired_status {self.retired_status!r} is not in statuses")
    if self.parked_status and self.parked_status not in self.statuses:
      raise ConfigError(f"parked_status {self.parked_status!r} is not in statuses")
    if self.started_status and self.started_status not in self.statuses:
      raise ConfigError(f"started_status {self.started_status!r} is not in statuses")
    if self.review_status:
      if self.review_status not in self.statuses:
        raise ConfigError(f"review_status {self.review_status!r} is not in statuses")
      others = {self.open_status, self.done_status, self.retired_status,
                self.parked_status, self.started_status}
      if self.review_status in others:
        raise ConfigError(
          f"review_status {self.review_status!r} is already another role's status; "
          "give review its own status"
        )
    if self.reviewing_status:
      if self.reviewing_status not in self.statuses:
        raise ConfigError(f"reviewing_status {self.reviewing_status!r} is not in statuses")
      others = {self.open_status, self.done_status, self.retired_status,
                self.parked_status, self.started_status, self.review_status}
      if self.reviewing_status in others:
        raise ConfigError(
          f"reviewing_status {self.reviewing_status!r} is already another role's status; "
          "give reviewing its own status"
        )
    seen_required: set[str] = set()
    for name in self.required_sections:
      if name not in self.sections:
        raise ConfigError(
          f"required_sections entry {name!r} is not in sections; "
          f"known: {list(self.sections)}"
        )
      if name in seen_required:
        raise ConfigError(f"required_sections repeats {name!r}")
      seen_required.add(name)
    if self.implement_finish not in ("done", "handoff"):
      raise ConfigError("implement_finish must be 'done' or 'handoff'")
    if self.implement_finish == "handoff" and not self.review_status:
      raise ConfigError(
        "implement_finish 'handoff' needs a review_status; "
        "an empty review_status disables handoff"
      )
    if len({self.done_dir, self.retired_dir, ""}) != 3:
      raise ConfigError("done_dir and retired_dir must differ, and neither may be empty")
    if len(set(self.statuses.values())) != len(self.statuses):
      raise ConfigError("two statuses render to the same label; they would be indistinguishable")
    if not self.id_prefix:
      raise ConfigError("id.prefix may not be empty")
    # The prefix is the front of every generated id, so a hostile one makes
    # every id a path. Checked against a sample rather than the prefix alone,
    # because a prefix legitimately ends in a separator-ish character.
    if not ids.is_valid(f"{self.id_prefix}{0:0{max(self.id_width, 1)}d}"):
      raise ConfigError(
        f"id.prefix {self.id_prefix!r} would produce ids that are not usable as "
        f"filenames; use letters, digits, '.', '-' and '_'"
      )
    if self.id_width < 1:
      raise ConfigError(f"id.width must be at least 1, not {self.id_width}")
    if "\n" in self.claim_owner or "\r" in self.claim_owner:
      raise ConfigError("claim_owner must be a single line")
    for target in self.sync_targets:
      try:
        re.compile(target.match)
      except re.error as exc:
        raise ConfigError(
          f"sync target {target.name!r}: match {target.match!r} is not a valid "
          f"regular expression: {exc}"
        ) from None

  def to_dict(self) -> dict[str, Any]:
    out = self._base_dict()
    if self.satisfies_dependencies is not None:
      out["satisfies_dependencies"] = list(self.satisfies_dependencies)
    return out

  def _base_dict(self) -> dict[str, Any]:
    return {
      "version": SCHEMA_VERSION,
      "note_kinds": list(self.note_kinds),
      "handoff_requires_note_kind": self.handoff_requires_note_kind,
      "id": {"prefix": self.id_prefix, "width": self.id_width},
      "statuses": dict(self.statuses),
      "open_status": self.open_status,
      "done_status": self.done_status,
      "retired_status": self.retired_status,
      "parked_status": self.parked_status,
      "started_status": self.started_status,
      "review_status": self.review_status,
      "reviewing_status": self.reviewing_status,
      "sections": list(self.sections),
      "required_sections": list(self.required_sections),
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
      "git_check": self.git_check,
      "render_driver_check": self.render_driver_check,
      "claim_owner": self.claim_owner,
      "implement_finish": self.implement_finish,
      "issues_repo": self.issues_repo,
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
    # `parked` was always a default status, so it is not injected into statuses:
    # a project that dropped it means to have no park state. Default the field
    # to "parked" only when the status is actually present, else empty -- an
    # empty parked_status makes `park` refuse cleanly instead of naming a status
    # the project never had.
    parked = d.get("parked_status")
    if parked is None:
      parked = "parked" if "parked" in statuses else ""
    # Back-filled the way `retired` is, and for the same reason: a config
    # written before `start` existed names no started status, and validate()
    # would reject it. Only that one key is added, so a project that renamed
    # it keeps its own name and one that dropped it stays without.
    started = d.get("started_status", "started")
    if started and started not in statuses:
      statuses[started] = started
    # Back-filled like `started`, with one difference: a config written before
    # `handoff` existed may already render some other status as "review", and
    # two statuses may not share a label. Such a project gets no review status
    # (`handoff` refuses cleanly) rather than a config that no longer loads.
    review = d.get("review_status")
    if review is None:
      review = "review"
      if review not in statuses and review in statuses.values():
        review = ""
    if review and review not in statuses:
      statuses[review] = review
    # Back-filled like `review`, for a config written before reviewing existed.
    # A project without review has nothing to review, so it gets none either.
    reviewing = d.get("reviewing_status")
    if reviewing is None:
      reviewing = "reviewing" if review else ""
      if reviewing and reviewing not in statuses and reviewing in statuses.values():
        reviewing = ""
    if reviewing and reviewing not in statuses:
      statuses[reviewing] = reviewing
    cfg = Config(
      version=int(d.get("version", SCHEMA_VERSION)),
      note_kinds=d.get("note_kinds", []),
      handoff_requires_note_kind=d.get("handoff_requires_note_kind", ""),
      id_prefix=ident.get("prefix", "S"),
      id_width=int(ident.get("width", 2)),
      statuses=statuses,
      open_status=d.get("open_status", "open"),
      done_status=d.get("done_status", "done"),
      retired_status=retired,
      parked_status=parked,
      started_status=started,
      review_status=review,
      reviewing_status=reviewing,
      sections=list(d.get("sections", DEFAULT_SECTIONS)),
      required_sections=_required_sections(d),
      boundary=d.get("boundary", "**Not in this slice:**"),
      done_dir=d.get("done_dir", "done"),
      retired_dir=d.get("retired_dir", "retired"),
      exclude_flags=list(d.get("exclude_flags", [])),
      next_format=pointers.get("next_format", "**{{id}}** {{title}}"),
      next_empty=pointers.get("next_empty", "nothing unmarked"),
      later=pointers.get("later", json.loads(json.dumps(DEFAULT_LATER))),
      sync_targets=[SyncTarget.from_dict(t) for t in d.get("sync", {}).get("targets", [])],
      git_check=bool(d.get("git_check", True)),
      render_driver_check=bool(d.get("render_driver_check", True)),
      claim_owner=_claim_owner(d.get("claim_owner", "")),
      implement_finish=_implement_finish(d),
      satisfies_dependencies=d.get("satisfies_dependencies"),
      issues_repo=d.get("issues_repo", ""),
    )
    cfg.validate()
    return cfg

  @staticmethod
  def load(path: Path) -> "Config":
    try:
      raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
      raise ConfigError(f"{path}: not found; run `slicer init` first") from None
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
      raise ConfigError(f"{path}: invalid JSON: {e}") from None
    # Refuse a newer schema before parsing or validating, so a config only a
    # newer slicer understands is reported as "upgrade slicer", not as invalid.
    if isinstance(raw, dict) and isinstance(raw.get("version"), int):
      reject_future_schema(str(path), raw["version"], SCHEMA_VERSION)
    try:
      return Config.from_dict(raw)
    except (KeyError, TypeError, ValueError) as e:
      raise ConfigError(f"{path}: not a usable config: {e}") from None
