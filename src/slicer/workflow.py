"""The generic recommended project workflow, printed from the docs file.

`docs/generic-recommended-project-workflow.md` is the one edited source.
`src/slicer/generic-recommended-project-workflow.md` is a symlink to it, so an
editable checkout and an installed wheel both read it from the package.
"""

from __future__ import annotations

from pathlib import Path

from slicer.errors import StateError

WORKFLOW_PATH = Path(__file__).with_name("generic-recommended-project-workflow.md")


def text(path: Path | None = None) -> str:
  """The workflow document, read from `path` or the packaged file."""
  path = WORKFLOW_PATH if path is None else path
  try:
    return path.read_text(encoding="utf-8")
  except (OSError, UnicodeDecodeError) as exc:
    raise StateError(f"cannot read the recommended workflow at {path}: {exc}", code="io") from None
