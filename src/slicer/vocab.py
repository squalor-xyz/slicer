"""Name lists the parser prints in its help, kept where importing them costs nothing.

`build_parser` needs these for `choices=` and help text, so every process that parses a flag
reads them. They used to be defined in `ai`, `note_pack` and `snapshot`, whose imports (the
store, the checker, `dataclasses`) made `--version` pay for all of them. Those modules import
the lists from here and keep the names, so `ai.PROMPT_NAMES`, `note_pack.TRUST` and
`snapshot.PARTS` still work.
"""

from __future__ import annotations

PROMPT_NAMES = ("build-roadmap", "getting-started")
TRUST = ("unverified", "machine-confirmed", "human-reviewed")
PARTS = ("items", "goals", "runs", "status", "health", "deps")
