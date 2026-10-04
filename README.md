# slicer

Roadmap and slice management for the review → slice → implement → done loop.

slicer is AI-friendly: a coding agent can review a project, turn accepted findings into
an importable roadmap, and work through bounded slices using commands with JSON output.
For a new project, start with goals and acceptance criteria instead of review findings.
The AI supplies the review and planning; slicer stores, validates, prioritizes, and
renders the work. It runs locally without an AI service or API key.

Run `slicer ai instructions` for a concise agent quick start, or add `--json` for
machine-readable output. It works before initialization and reads no project state.

State is **JSON**. The markdown under `.slicer/render/` is generated output — readable,
committed, and never parsed back. Edit through the commands or the TUI, not by hand.

Stdlib Python 3.11+, no dependencies.

## Install

Into its own virtual environment from PyPI. The distribution is `squalor-slicer`
because the `slicer` name on PyPI is taken; the command it installs is still `slicer`.

```sh
python3 -m venv ~/.slicer-venv
~/.slicer-venv/bin/pip install squalor-slicer
ln -s ~/.slicer-venv/bin/slicer ~/.local/bin/slicer   # or anywhere on your PATH
```

`slicer --about` prints the version, description, source URL, and issues URL.

`slicer --help` confirms it. Other ways in — `pip --user`, pipx, uv, unreleased main — are
in [getting started](docs/getting-started.md#1-install); the editable install for working on
slicer itself is in [AGENTS.md](AGENTS.md#install).

## Use it on a project

```sh
cd any-repo
slicer init                             # creates .slicer/
slicer add "Parse the config file" --size M --tree core
slicer promote S01                      # give it a slice file from the template
slicer edit S01 --section Why --text "Load settings before starting the app"
slicer render                           # regenerate .slicer/render/
slicer check                            # the gate: exit 1 if anything drifted
```

`add` appends a roadmap row; `promote` gives it a slice file.

Got a whole roadmap to load? Write it as a markdown outline and import it in one go:

```sh
slicer import --skeleton > roadmap.md   # a template, built from your config
slicer import roadmap.md --dry-run      # validate; writes nothing
slicer import roadmap.md                # apply
```

Each `##` heading is an item, optional `key: value` lines carry its size, tree and
dependencies, and `###` sections become the slice itself — so one file can produce a
fully written roadmap. It is a one-way ramp: the file is yours to delete afterwards.

Already running this workflow by hand in markdown? `slicer migrate --from docs/slices`
converts an existing tree that is **already in slicer's legacy format**. It refuses to
write anything unless every file round-trips byte for byte, so a document slicer cannot
reproduce is never half-migrated.

## Commands

| | |
|---|---|
| `ai` | onboarding instructions for coding agents |
| `init` | create .slicer/ in a project |
| `setup-git` | print the git config that turns on the render merge driver (run once per clone) |
| `import` | add items in bulk from a markdown outline |
| `migrate` | convert an existing markdown slice tree |
| `next` | the highest-priority startable item |
| `next-id` | the id the next add would take, without allocating it |
| `id-prefix` | show the id prefix, or change its case for new ids |
| `list` | list items in next's order, omitting done and retired unless asked |
| `deps` | dependencies: unblocked items, or one item's edges |
| `find` | search items by text |
| `show` | print one slice |
| `sections` | list configured section names, marking the ones next requires |
| `add` | append a roadmap item |
| `promote` | give an item a slice file |
| `move` | reorder the queue |
| `sort` | reorder the whole queue by priority score |
| `set` | change an item's fields |
| `edit` | edit a slice section or scope boundary |
| `note` | append a dated note to an item |
| `done` | mark an item finished |
| `start` | mark an item in progress and claim it |
| `release` | clear a claim without changing status |
| `handoff` | hand a started slice to review and clear its claim |
| `reject` | send a review back with a verdict and clear its claim |
| `park` | set an item aside |
| `unpark` | return a parked item to the queue |
| `prose` | read and edit the roadmap's own prose |
| `remove` | retire an obsolete item, or purge one outright |
| `render` | regenerate .slicer/render/ |
| `sync` | rewrite derived lines in other documents |
| `verify` | check the index for consistency (and against git unless git_check is off) |
| `check` | the CI gate: render, sync and integrity |
| `goals` | show project goals and non-goals |
| `stats` | counts by status, size, tree and pass |
| `status` | next item, progress, and blockers in one view |
| `log` | recent history, newest first |
| `tui` / `ui` | browse, read, reorder and edit interactively |

Every flag, the `.slicer/` layout, removing items, and roadmap prose are in the
[command reference](docs/commands.md); keys and screens are in the [TUI manual](docs/tui.md).
Every command except `tui`/`ui` takes `--json`, failures included; the
[agent reference](docs/agents.md) has the payloads and exit codes.

**slicer never commits, pushes or tags.** Its `git` access is a read-only allowlist, and
a mutation leaves the index untouched; see [Git access](docs/commands.md#git-access).

## Documentation

- [Getting started](docs/getting-started.md) — install to CI, with worked workflows for a
  new project and for an AI code review
- [Command reference](docs/commands.md) — every command and flag, `.slicer/`, removal, prose
- [TUI manual](docs/tui.md) — browsing, editing and the roadmap wizard
- [Driving slicer from an agent](docs/agents.md) — reusable prompts, JSON contracts, exit codes
- [The outline format](docs/import.md) — what `slicer import` reads
- [Configuration](docs/configuration.md) — every `.slicer/config.json` key and default
- [Legacy format](docs/migrate-format.md) — what `slicer migrate` reads
- [ARCHITECTURE.md](ARCHITECTURE.md) — layering and invariants
- [AGENTS.md](AGENTS.md) — contributing: the dev loop, tests, and house style
- [CHANGELOG.md](CHANGELOG.md) — notable changes by release

## Tests

```sh
python3 -m unittest discover -s tests -t tests
```

No install step and no dependencies — the suite puts `src/` on `sys.path` itself.
Fixtures and the optional live-tree migrator check are described in
[AGENTS.md](AGENTS.md).

## Status

slicer manages its own roadmap: `.slicer/` in this repository is a worked
example you can read, and `.slicer/render/ROADMAP.md` is what it renders to. Run
`slicer --version` for the installed version, and see the [changelog](CHANGELOG.md).
Its own goals and non-goals: run `slicer goals`, or read them at the top of
[the roadmap](.slicer/render/ROADMAP.md). In short, slicer is a small, dependency-light,
file-based store for a project's roadmap, goals, and issues — canonical JSON that humans
and AI agents plan from, projected deterministically to markdown — and is *not* a
real-time, multi-user collaboration tool (coordination happens through git).

Apache-2.0. Stdlib Python, no dependencies, and none planned.
