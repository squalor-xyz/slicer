# AGENTS.md

Project guidance for slicer. Read [ARCHITECTURE.md](ARCHITECTURE.md) before changing
anything non-trivial; it explains the invariants this file only names.

## Commands

```sh
python3 -m unittest discover -s tests -t tests   # the suite
PYTHONPATH=src python3 -m slicer check           # the gate; exit 1 means drift
PYTHONPATH=src python3 -m slicer --help          # run it uninstalled
```

No install step. `tests/support.py` puts `src/` on `sys.path` itself, so the suite runs
from a clean checkout with nothing but Python 3.11+.

Optional: `SLICER_LEGACY_TREE=/path/to/docs/slices` additionally proves the importer
against a live markdown tree. The test is skipped when the variable is unset. The
committed fixture under `tests/fixtures/legacy/` is synthetic, so this is the only way
to exercise the importer against real, messily hand-written markdown — do it before
touching `legacy.py` or `importer.py`.

To run one test:

```sh
python3 -m unittest discover -s tests -t tests -k '*RoundTrips*'
```

## What a change must not break

- **JSON is state; `.slicer/render/` is generated output that is never parsed back.**
- **Output is deterministic.** `check` compares bytes. Fixed key order in every
  `to_dict`, fixed `jsonio` formatting, no timestamps in `render`.
- **Stdlib only.** `dependencies = []` and it stays that way. Python 3.11+.
- **Nothing in `src/slicer/` may hardcode a path, status or heading belonging to one
  project.** It goes in `.slicer/config.json`.
- **All mutations go through `ops.py`**, never straight into `cli.py` or `tui.py`.
- **The git allowlist in `vcs.py` stays closed.** slicer does not commit, push or tag.
- **Import stays all-or-nothing**: the round-trip proof runs before anything is written.

## Working on the roadmap

This repo tracks its own roadmap with slicer. That is the point — it is also the
end-to-end test.

**Never hand-edit `.slicer/*.json`.** Use the commands, then re-render:

```sh
PYTHONPATH=src python3 -m slicer add "Some title"
PYTHONPATH=src python3 -m slicer edit S07 --section Why --file note.md
PYTHONPATH=src python3 -m slicer done S07
PYTHONPATH=src python3 -m slicer render
PYTHONPATH=src python3 -m slicer check
```

`.slicer/render/` is committed. A change to state without a re-render fails CI.

## House style

Enforced by review only — there is no formatter, linter or type checker in this repo.

- **Two-space indent.** Not four. Every file.
- `from __future__ import annotations` at the top of every module.
- A module docstring that says *why* the module exists, not what it contains.
- Dataclasses with explicit `to_dict` / `from_dict`, keys in a fixed order.
- Tests are `unittest`, named `test_<Subject>_<Condition>_<Expectation>`.
- Errors name the file and line when they can, and say what to do next.
