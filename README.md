# slicer

Roadmap and slice management for the review → slice → implement → done loop.

State is **JSON**. The markdown under `.slicer/render/` is generated output — readable,
committed, and never parsed back. Edit through the commands or the TUI, not by hand.

Stdlib Python 3.11+, no dependencies.

## Install

```sh
git clone git@github.com:squalor-xyz/slicer.git
cd slicer
pipx install .                      # puts `slicer` on PATH
PYTHONPATH=src python3 -m slicer --help   # or run it uninstalled
```

## Use it on a project

```sh
cd any-repo
slicer init                             # creates .slicer/
slicer add "Parse the config file" --size M --tree core
slicer promote S01                      # give it a slice file from the template
slicer edit S01 --section Why           # opens $EDITOR
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

**→ [docs/getting-started.md](docs/getting-started.md)** walks through all of this with
real output. [docs/import.md](docs/import.md) is the outline format;
[docs/agents.md](docs/agents.md) is how to drive slicer from an AI agent;
[docs/migrate-format.md](docs/migrate-format.md) is the legacy grammar; and
[docs/configuration.md](docs/configuration.md) is every config key.

## Commands

| | |
|---|---|
| `init` | create `.slicer/` with config and templates |
| `import FILE [--dry-run] [--force]` | bulk-load a roadmap from a markdown outline |
| `import --skeleton` | print an outline template built from your config |
| `migrate --from DIR [--dry-run]` | convert an existing legacy markdown tree |
| `add TITLE [--size/--tree/--findings/--status/--pass/--importance/--urgency]` | append a roadmap item (no slice file yet) |
| `promote ID [--file/--stdin]` | give an item a slice file; a one-item outline fills its sections in one call |
| `move ID --before/--after/--to` | reorder the queue; position is the manual priority, and breaks score ties |
| `next` | the highest-priority startable item (highest effective score; dependencies still gate) |
| `list [--status/--tree/--pass] [--sort score]` | filter the queue, or rank it by priority score |
| `show ID [--section NAME]` | print one slice, or just one section's body |
| `set ID --title/--size/--tree/--findings/--status/--pass/--depends-on/--flag/--group/--importance/--urgency` | change fields |
| `edit ID --section NAME [--file/--stdin]` | replace one section (or open `$EDITOR`) |
| `prose list / show REF / edit REF` | read and edit the roadmap's own prose |
| `prose add-pass KEY / drop-pass KEY` | open or close a pass group |
| `done ID` / `park ID` / `unpark ID` | change status; `done` moves the file with `git mv` |
| `remove ID --reason "…"` | retire an obsolete item; the id stays claimed |
| `remove ID --purge` | delete outright, for something that never should have existed |
| `render` | regenerate `.slicer/render/` |
| `sync [--check]` | rewrite derived lines in other documents |
| `verify` | check the index for consistency, and against `git log` (unless `git_check` is off) |
| `check [--diff]` | the CI gate: render staleness, sync drift, integrity |
| `stats` / `log` | counts, and the history of status changes |
| `tui` | browse, read, reorder and edit interactively |

In the TUI, `tab` moves between the queue and the detail pane, `e` opens `$EDITOR` on
whatever is selected there — an item field (size, trees, findings, depends, importance,
urgency), a slice section, or a prose block — and `a` adds a new item.

Every command takes `--json`, including the failures — an agent calls `slicer next
--json` rather than parsing markdown, and reads `{"error": {"code": ...}}` rather than
prose. Exit codes: `0` fine, `1` drift or a failed check, `2` usage or nothing to do.
See [docs/agents.md](docs/agents.md).

Every command that changes state — `add`, `set`, `done`, `move`, `promote`, `edit`,
`remove`, `park`, `unpark`, `import`, `migrate`, and the `prose` edits — takes `--render`
to regenerate `.slicer/render/` in the same step, so a mutation and its render are one
command.

Items carry an Eisenhower-style priority: an `--importance` and an `--urgency` (each 1–3),
combined into a score (importance leads). A blocker of a critical item inherits its
priority, so `slicer next` and `slicer list --sort score` surface the blockers of
important work first, while the stored queue order stays whatever `move` set.

**slicer never commits, pushes or tags.** `git` access is allowlisted to
`rev-parse`, `status`, `log`, `mv` and `ls-files`; the writing subcommands cannot be
reached from the code at all.

## What lives in `.slicer/`

```
config.json          paths, statuses, fields, templates, sync targets
index.json           the ordered queue: ids, status, metadata, dependencies
slices/<ID>.json     one open slice: title, lead, sections (ordered list)
slices/done/<ID>.json    finished slices
slices/retired/<ID>.json obsolete slices, with the reason on the item
templates/*.md       render templates, yours to edit
render/              GENERATED markdown - ROADMAP.md and one file per slice
log.jsonl            append-only history of status changes
```

Sections are an ordered **list**, not a map: real slices carry headings no schema names,
sometimes more than once, and their order is part of the document.

## Removing an item

Removal is two different acts, so `remove` has two modes.

`remove ID --reason "superseded by S30"` **retires** it: the status becomes `retired`, the
slice moves to `.slicer/slices/retired/`, and the row keeps rendering with the reason
beside it. The id stays claimed. This is for something that existed and was cited — a
commit or a review that names it must still resolve to something that explains itself.

`remove ID --purge` **deletes** it: the item and its slice file go. This is for a mistyped
`add`. The id comes back only when it was the most recently allocated *and* no commit
subject mentions it — that is undoing an allocation, not reusing an identifier. Any
earlier id stays burned, and the output says which happened and why.

Both refuse when another item depends on it, or when it is `done`; `--force` overrides and
names the rule it overrode. After a forced purge, `slicer check` reports the dangling
dependency it left behind.

Retiring needs a status to move into. A tracking directory created before `remove` existed
gains a `retired` status automatically on load — only that one key, so a project that
dropped some other status does not get it back.

## Roadmap prose

A roadmap carries text that belongs to no slice: an opening note, a heading and prose
around each pass group, and a closing section. `slicer prose` addresses those blocks:

```
preamble                 the opening note
pass.<key>.heading       the group's markdown heading
pass.<key>.intro         prose above the group's table
pass.<key>.outro         prose below it
epilogue                 the closing section
```

`slicer prose list` names every block in the order it renders. `edit` takes `--file`,
`--stdin` or opens `$EDITOR`. A pass group is opened with `prose add-pass 6 --heading
"# ..."` and closed with `drop-pass`, which refuses while any item is still filed under
it. Items are filed with `slicer add --pass 6` or moved with `slicer set <id> --pass 6`.

A declared pass renders even with no items yet, so a group can be opened before its first
slice exists.

## Configuration

Everything project-specific is in `.slicer/config.json` — status vocabulary and how each
one renders, the section list `promote` seeds, the scope-boundary marker, which flags
exclude an item from derived pointers, and the `sync` targets. Nothing is compiled into
the tool, so slicer works on a repo with no review protocol at all.

Every key, its default, and which ones are unsafe to change once items exist:
[docs/configuration.md](docs/configuration.md). Two to know up front — `id.prefix` and
`id.width` are read only when `index.json` is first written, so changing them later does
nothing.

## Tests

```sh
python3 -m unittest discover -s tests -t tests
```

No install step and no dependencies — the suite puts `src/` on `sys.path` itself.

`tests/fixtures/legacy/` is a synthetic 14-slice tree for a project that does not
exist. It is shaped to exercise the awkward parts of the legacy format — a middot
inside a findings value, singular and plural tree keys, a collective trees cell,
headings no schema names, prose between the tables — and the suite proves every file
round-trips through the parser byte for byte.

Set `SLICER_LEGACY_TREE=/path/to/docs/slices` to additionally prove the migrator
against a live markdown tree of your own. That test is skipped when the variable is
unset, and it is the only way to exercise the migrator against real, messily
hand-written markdown — worth running before changing `legacy.py` or `migrator.py`.

## Status

0.1.0, and slicer manages its own roadmap: `.slicer/` in this repository is a worked
example you can read, and `.slicer/render/ROADMAP.md` is what it renders to.

[docs/getting-started.md](docs/getting-started.md) is the walkthrough, and
[docs/agents.md](docs/agents.md) covers driving slicer from an agent.
[ARCHITECTURE.md](ARCHITECTURE.md) explains the layering and the invariants.
[AGENTS.md](AGENTS.md) has the commands and the house style.

Apache-2.0. Stdlib Python, no dependencies, and none planned.
