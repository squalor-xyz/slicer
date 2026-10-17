"""Default template text, copied into `.slicer/templates/` by `init`.

They live as files, not as compiled-in strings, so that a project can change
its slice shape without changing slicer. Nothing in the tool refers to a
section heading by name. `slicer template` shows, replaces and resets a
project's copy; the defaults here are what `reset` restores.
"""

from __future__ import annotations

from pathlib import Path

from slicer.errors import StateError

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
TEMPLATE_NAMES = ("slice.md", "roadmap.md", "row.md", "relay-brief.md")


RELAY_BRIEF_KEYS = (
  "id", "title", "size", "effort", "worktree", "branch", "run", "step",
  "roles", "finish_override", "commit_title", "parallel", "contract",
)


def default(name: str) -> str:
  return (TEMPLATE_DIR / name).read_text(encoding="utf-8")


def defaults() -> dict[str, str]:
  return {name: default(name) for name in TEMPLATE_NAMES}


def require(name: str) -> str:
  """The name, or an `unknown_template` error that lists the valid ones."""
  if name not in TEMPLATE_NAMES:
    raise StateError(
      f"{name!r} is not a template; the templates are {', '.join(TEMPLATE_NAMES)}",
      code="unknown_template",
    )
  return name
