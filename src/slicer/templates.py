"""Default template text, copied into `.slicer/templates/` by `init`.

They live as files, not as compiled-in strings, so that a project can change
its slice shape without changing slicer. Nothing in the tool refers to a
section heading by name.
"""

from __future__ import annotations

from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
TEMPLATE_NAMES = ("slice.md", "roadmap.md", "row.md")


def default(name: str) -> str:
  return (TEMPLATE_DIR / name).read_text(encoding="utf-8")


def defaults() -> dict[str, str]:
  return {name: default(name) for name in TEMPLATE_NAMES}
