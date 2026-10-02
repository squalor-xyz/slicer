"""slicer — roadmap and slice manager.

State is JSON; markdown under `.slicer/render/` is generated output only.
Nothing in this package may hardcode a path belonging to any one project:
everything project-specific lives in `.slicer/config.json`.
"""

from __future__ import annotations

__version__ = "1.2.0"
__url__ = "https://github.com/squalor-xyz/slicer"
