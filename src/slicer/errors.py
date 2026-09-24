"""Error types. Every one carries a message a human can act on."""

from __future__ import annotations


class SlicerError(Exception):
  """Base for every error slicer raises deliberately."""


class ConfigError(SlicerError):
  """`.slicer/config.json` is missing, malformed, or self-contradictory."""


class StateError(SlicerError):
  """The tracking directory is missing, or an operation does not apply."""


class LegacyImportError(SlicerError):
  """Legacy markdown could not be parsed, or would not round-trip.

  Not named ImportError: that shadows the builtin, and an import failure
  here is about someone's documents, not about Python modules.
  """


class RenderError(SlicerError):
  """A template referenced a placeholder that does not exist."""
