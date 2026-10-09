"""Error types. Every one carries a message a human can act on.

Each also carries a stable `code`. The message is for a person and may be
reworded at any time; the code is the contract an agent branches on, so it
changes only when the meaning does.
"""

from __future__ import annotations


# Hard failures: the project or the process cannot proceed. Usage and
# validation stay outside this set so they keep a different exit status.
# So does `external`: an optional outside tool such as `gh` is missing or failed,
# which the person can fix without anything being wrong with the project.
# `no_project` (`-r` found no project below the start directory) is likewise a
# usage-level miss, not a failure of any project.
# `schema_too_new` is raised by `store.load` when the on-disk schema is newer
# than this build understands.
INTERNAL_CODES = frozenset({
  "corrupt",
  "locked",
  "io",
  "config",
  "schema_too_new",
})


def is_internal(code: str) -> bool:
  """True when `code` is a hard failure rather than usage or validation."""
  return code in INTERNAL_CODES


class SlicerError(Exception):
  """Base for every error slicer raises deliberately."""

  code = "error"

  def __init__(self, message: str, *, code: str | None = None) -> None:
    super().__init__(message)
    if code is not None:
      self.code = code


class ConfigError(SlicerError):
  """`.slicer/config.json` is missing, malformed, or self-contradictory."""

  code = "config"


class StateError(SlicerError):
  """The tracking directory is missing, or an operation does not apply."""

  code = "state"


class LegacyImportError(SlicerError):
  """Legacy markdown could not be parsed, or would not round-trip.

  Not named ImportError: that shadows the builtin, and an import failure
  here is about someone's documents, not about Python modules.
  """

  code = "legacy_format"


class OutlineError(SlicerError):
  """An outline given to `slicer import` could not be parsed."""

  code = "outline"


class RenderError(SlicerError):
  """A template referenced a placeholder that does not exist."""

  code = "render"


def reject_future_schema(where: str, found: int, known: int) -> None:
  """Refuse state written by a newer slicer instead of silently downgrading it.

  A higher on-disk `version` means keys this build does not know: loading it
  would drop them, and the next save would rewrite the file at the older shape,
  losing data. Called at the parse boundary so every reader (`store.load`,
  `migrate`, …) is covered. Within 1.x, a schema bump ships a reader/migrator
  that lifts the old shape on load — a slice-schema change must bump the index
  version too — so raising the version is always paired with a reader for it.
  """
  if found > known:
    raise StateError(
      f"{where}: written by a newer slicer (schema {found}; this build knows "
      f"{known}). Upgrade slicer to open this project.",
      code="schema_too_new",
    )
