# Contributing

This walkthrough is for changing slicer itself. To use it in your own project,
start with [Getting started](docs/getting-started.md); for roadmap terminology,
read [How slicer works](docs/how-it-works.md). If you delegate implementation,
[Working with agents](docs/working-with-agents.md) explains the human role.

slicer supports Python 3.11+ and keeps its runtime and test suite standard-library
only. [AGENTS.md](AGENTS.md) is the authority for contributor setup, project rules,
house style, temporary fixtures, checks, and evidence to record at handoff.
Discuss any proposed dependency with the maintainers before adding it.

## Choose and understand a slice

From the checkout root where you launched your session, find specified, ready work:

```sh
PYTHONPATH=src python3 -m slicer next --ready --section "Implement" --section "Check" --json --lean
PYTHONPATH=src python3 -m slicer show <ID> --json --lean
```

Read the whole slice: scope, dependencies, acceptance checks, and **Not in this
slice**. Read the named code and tests before claiming it. Resolve missing criteria
with the owner first; a row without a slice needs a specification and promotion.
Read and change tracking state through slicer commands, never by opening or editing
files under `.slicer/`.

Use the [architecture map](ARCHITECTURE.md#shape) to find the subsystem: `cli.py`
and `tui.py` handle interaction, `ops.py` owns roadmap mutations, `store.py` and
`jsonio.py` persist state, and `render.py`, `sync.py`, `check.py` and `verify.py`
handle projections and validation. Read the relevant invariants when your change
touches one; the slice's named files and their tests are the starting point.

## Isolate the work, then claim it

Create one branch and worktree per slice **before** running `start`. The worktree
starts at committed `main`; if the specification is still uncommitted, ask the
owner to authorize committing it before cutting the worktree.

The examples below stay in your launch checkout. Replace `<id>`, `<ID>` and
`<slug>` with the worktree name, slice ID and branch description; they are placeholders,
not literal shell syntax. Use a Python 3.11+ interpreter for every command.

```sh
git worktree add -b feature/<id>-<slug> .worktrees/<id> main
PYTHONPATH=.worktrees/<id>/src python3 -m slicer --root .worktrees/<id> start <ID> --render --strict
```

Keep edits inside that worktree and use its code for every slicer command. An
editable install can point at another checkout; explicit `PYTHONPATH` and `--root`
keep code and tracking state together without changing directory.

## Implement and check

Make the smallest change that meets the acceptance checks, using existing patterns.
Update the documentation that describes changed behaviour alongside the code.
[AGENTS.md](AGENTS.md#commands) has the current testing and fixture rules.

Affected tests shorten iteration by selecting tests that exercised changed lines
or read changed repository files, including documentation. Run them first:

```sh
mkdir -p .worktrees/<id>/.venv/test-tmp
TMPDIR="$PWD/.worktrees/<id>/.venv/test-tmp" python3 .worktrees/<id>/tests/affected.py --run
```

If it exits 2 (no usable map, or it cannot select tests reliably), run the full
suite. Before completion, run the full suite when the diff touches `src/` or `tests/`:
affected selection speeds feedback but does not replace regression coverage.

```sh
TMPDIR="$PWD/.worktrees/<id>/.venv/test-tmp" python3 -m unittest discover -s .worktrees/<id>/tests -t .worktrees/<id>/tests
```

For a diff touching neither `src/` nor `tests/`, the docs-only exception allows
affected tests, `slicer check` and `git diff --check` to replace the full suite
(unless affected tests exit 2, requiring the full-suite fallback above). Also perform the slice's
own acceptance checks, such as reviewing links or rendering a diagram.

```sh
PYTHONPATH=.worktrees/<id>/src python3 -m slicer --root .worktrees/<id> check
git -C .worktrees/<id> diff --check
git -C .worktrees/<id> status --short
```

These checks answer different questions. Code tests exercise behaviour;
`slicer check` validates tracking integrity and compares generated views and synced
pointers against current state. `git diff --check` catches whitespace errors in
the patch. Passing one does not imply the others pass.

## Prepare the handoff

Review the implementation, documentation, tracking changes and generated output
together. Record what changed, acceptance evidence, checks and results, and any
limitations in the review report; see [AGENTS.md](AGENTS.md#working-on-the-roadmap)
for the exact current workflow. Once acceptance passes, record the evidence and
hand off through the worktree:

```sh
PYTHONPATH=.worktrees/<id>/src python3 -m slicer --root .worktrees/<id> note <ID> --text "Ready for review: describe changes, checks and results" --render
PYTHONPATH=.worktrees/<id>/src python3 -m slicer --root .worktrees/<id> handoff <ID> --render --check
git -C .worktrees/<id> diff --check
git -C .worktrees/<id> status --short
```

This repository finishes implementation with `handoff`; never run `done` in the
worktree. For a slice relay, follow its agreed review and gate order before the
closing note. Once handoff passes, commit on the worktree branch; merging, pushing
and publishing wait for the owner (see [AGENTS.md](AGENTS.md#working-on-the-roadmap)).
Owner-authorized review, integration and marking the slice done follow
[Landing a slice](docs/landing.md).
