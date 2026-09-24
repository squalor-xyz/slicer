"""Error types. Every one carries a message a human can act on.

Each also carries a stable `code`. The message is for a person and may be
reworded at any time; the code is the contract an agent branches on, so it
changes only when the meaning does.
"""

from __future__ import annotations


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
