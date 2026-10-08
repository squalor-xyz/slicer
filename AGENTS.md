# AGENTS.md

Project guidance for slicer. The invariants are the list under "What a change must not break". Open ARCHITECTURE.md only when a change touches one of them.

## Install

This is the contributor install. To just *use* slicer, the no-clone path in the
[README](README.md#install) is simpler.

Editable, so the command tracks your worktree:

```sh
git clone git@github.com:squalor-xyz/slicer.git
cd slicer
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

To keep temporary test files inside the checkout, use an ignored directory:

```sh
mkdir -p .venv/test-tmp
TMPDIR="$PWD/.venv/test-tmp" python3 -m unittest discover -s tests -t tests
```

The full suite takes about one minute alone and longer under load. In an agent
session, run it in the background or with a timeout over 180 s. While iterating,
run `python3 tests/affected.py --run` first; run the full discover once before done.
A successful full discover writes `.venv/affected-map.json` for that HEAD.

The temporary directory does not require creating a virtual environment. Tests
that need an outside-project fixture use `support.isolated_discovery(root)` to hide
ancestor slicer configs and stop Git discovery above the fixture. Discovery within
the fixture stays real; the helper restores its patches on exit. Keep it scoped to
those tests rather than changing production discovery or using it suite-wide.

`PYTHONPATH=src python3 -m slicer` still works and is what CI runs — see
`.github/workflows/ci.yml`. That step is the only place `src/slicer/__main__.py` is
exercised, so leave it uninstalled.

If the `slicer` command is an editable install pointing at one checkout and you run it
inside a *different* worktree of the same repo, it runs that other checkout's code
against this worktree's `.slicer/`. slicer warns on stderr when it detects this; run
`PYTHONPATH=src python3 -m slicer` to exercise the worktree you are editing.
`SLICER_NO_CODE_WARNING=1` silences the warning.

`tests/fixtures/legacy/` is a synthetic 14-slice tree for a project that does not
exist. It is shaped to exercise the awkward parts of the legacy format — a middot
inside a findings value, singular and plural tree keys, a collective trees cell,
headings no schema names, prose between the tables — and the suite proves every file
round-trips through the parser byte for byte.

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

This repo tracks its own roadmap with slicer; that is also the end-to-end test.

Use `slicer ai instructions` for onboarding. The invariants named in this file are the session reading. Open ARCHITECTURE.md only when a change touches one of them, and open docs/agents.md only for the one command whose JSON contract you are changing.

To pick up existing work, run this from the checkout root (`slicer sections` lists
this project's headings):

```sh
PYTHONPATH=src python3 -m slicer next --ready --section "Implement" --section "Check" --json --lean
```

Read the returned slice's scope, dependencies, acceptance checks, relevant source
and tests before claiming. Resolve missing criteria first;
a row without a slice needs `promote` and a specification.

**Every implementation claim starts in a new worktree, one slice per worktree.**
Create it before running `start` or `next --start`; do not claim or implement the
slice on `main`. This applies even when committing and merging were not requested.

```sh
git worktree add -b feature/<id>-<slug> .worktrees/<id> main
```

A new worktree starts at `main`'s committed `HEAD`. If the slice has uncommitted
filings, ask the owner to authorize committing them before cutting the worktree.
Run the commands below against the worktree's code and state. From the launch
directory, use `PYTHONPATH=.worktrees/<id>/src python3 -m slicer --root .worktrees/<id>`,
`git -C .worktrees/<id>`, and `-s/-t .worktrees/<id>/tests`. Use that worktree's
ignored TMPDIR for test fixtures.

```sh
PYTHONPATH=src python3 -m slicer start <ID> --render --strict
# While editing, run the tests that hit this diff. If that command exits 2, run the full discover.
python3 tests/affected.py --run
python3 -m unittest discover -s tests -t tests
PYTHONPATH=src python3 -m slicer note <ID> --text "Ready for review: ..."
PYTHONPATH=src python3 -m slicer handoff <ID> --render --check
git diff --check
git status --short
```

Review code, docs and tracking state together. This project finishes with `handoff`
(`implement_finish`): hand off only after the acceptance checks pass; never run `done`
in a worktree. Once `handoff` passes, commit the slice on its worktree branch; merging,
pushing and publishing wait for the owner (a slice's Git section does not authorize them).

### Landing a slice

Only when the owner asks to merge. Review, merge and run `done` on `main`, never in
a worktree; [docs/landing.md](docs/landing.md) has the commands.

**Read and change tracking state through the slicer CLI.** Never open, search, parse
or edit `.slicer/*.json`, `.slicer/slices/`, `.slicer/log.jsonl` or `.slicer/render/`,
even when `git status` lists one. Use `slicer show ID --json --lean`,
`slicer list --json --lean` or `slicer next --json --lean`. `config.json` is the
exception: read it with `slicer config KEY` and edit it by hand as
[docs/configuration.md](docs/configuration.md) describes.

To file a specification, pass `promote` a one-item outline (`##` item / `### section`)
via `--file` or `--stdin`. It keeps the item's fields; supply sections and lead only:

```sh
slicer add "Some title"
slicer promote S07 --file draft.md --render
slicer edit S07 --section Why --file note.md --render --strict
```

For several items, run `slicer import --skeleton`, then `import FILE --dry-run`, then
`import FILE`.

`.slicer/render/` is committed. A change to state without a re-render fails CI.

## House style

Enforced by review only — there is no formatter, linter or type checker in this repo.

- **Two-space indent.** Not four. Every file.
- `from __future__ import annotations` at the top of every module.
- A module docstring that says *why* the module exists, not what it contains.
- Dataclasses with explicit `to_dict` / `from_dict`, keys in a fixed order.
- Tests are `unittest`, named `test_<Subject>_<Condition>_<Expectation>`.
- Errors name the file and line when they can, and say what to do next.
