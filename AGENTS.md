# AGENTS.md

Project guidance for slicer. Read [ARCHITECTURE.md](ARCHITECTURE.md) before changing
anything non-trivial; it explains the invariants this file only names.

## Install

Editable, so the command tracks your worktree:

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
ln -s "$PWD/.venv/bin/slicer" ~/.local/bin/slicer   # if ~/.local/bin is on PATH
```

## Commands

```sh
python3 -m unittest discover -s tests -t tests   # the suite
slicer check                                     # the gate; exit 1 means drift
slicer --help
```

The suite needs no install: `tests/support.py` puts `src/` on `sys.path` itself and
calls `main()` in-process, so it runs from a clean checkout with nothing but Python
3.11+.

`PYTHONPATH=src python3 -m slicer` still works and is what CI runs — see
`.github/workflows/ci.yml`. That step is the only place `src/slicer/__main__.py` is
exercised, so leave it uninstalled.

Optional: `SLICER_LEGACY_TREE=/path/to/docs/slices` additionally proves the migrator
against a live markdown tree. The test is skipped when the variable is unset. The
committed fixture under `tests/fixtures/legacy/` is synthetic, so this is the only way
to exercise the migrator against real, messily hand-written markdown — do it before
touching `legacy.py` or `migrator.py`.

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
  `legacy.py` and `outline.py` are pure parsers: text in, dataclasses out, no I/O.
- **The git allowlist in `vcs.py` stays closed.** slicer does not commit, push or tag.
- **Import and migration stay all-or-nothing**: every problem is collected and the
  whole file refused before anything is written.
- **Error messages are for people; `SlicerError.code` is the contract.** Reword a
  message freely; change a code only when the meaning changes.

## Working on the roadmap

This repo tracks its own roadmap with slicer. That is the point — it is also the
end-to-end test.

**Never hand-edit `.slicer/*.json`.** Use the commands, then re-render:

```sh
slicer add "Some title"
slicer edit S07 --section Why --file note.md
slicer done S07
slicer render
slicer check
```

Every mutating command takes `--render`, which folds the separate `render` step into the
mutation — `slicer done S07 --render` is the two middle steps in one.

To file a fully-specified slice in one step rather than a `promote` plus one `edit` per
section, hand `promote` a one-item outline (the same `##` item / `### section` shape
`import` reads) via `--file` or `--stdin`. The item keeps its own fields, so the source is
sections and lead only:

```sh
slicer add "Some title"
slicer promote S07 --file draft.md --render
slicer show S07 --section Why    # read one section back
```

Several items at once go through an outline, which is also how the agent-surface items
were filed:

```sh
slicer import --skeleton > /tmp/draft.md
slicer import /tmp/draft.md --dry-run
slicer import /tmp/draft.md
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
