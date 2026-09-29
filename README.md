# slicer

Roadmap and slice management for the review → slice → implement → done loop.

slicer is AI-friendly: a coding agent can review a project, turn accepted findings into
an importable roadmap, and work through bounded slices using commands with JSON output.
For a new project, start with goals and acceptance criteria instead of review findings.
The AI supplies the review and planning; slicer stores, validates, prioritizes, and
renders the work. It runs locally without an AI service or API key.

Run `slicer ai instructions` for a concise agent quick start, or add `--json` for
machine-readable output. It works before initialization and reads no project state.

Start with the [worked workflows](docs/getting-started.md#worked-workflows), use the
[agent prompts](docs/agents.md#reusable-prompts), or follow the
[contributor workflow](AGENTS.md#working-on-the-roadmap) to work on slicer itself.
See the [changelog](CHANGELOG.md) for notable changes by release.

State is **JSON**. The markdown under `.slicer/render/` is generated output — readable,
committed, and never parsed back. Edit through the commands or the TUI, not by hand.

Stdlib Python 3.11+, no dependencies.

## Install

Python 3.11+, no dependencies. Install it to use slicer; clone it to change slicer.

### Install it (no clone)

Into its own virtual environment from PyPI. The distribution is `squalor-slicer`
because the `slicer` name on PyPI is taken; the command it installs is still `slicer`.

```sh
python3 -m venv ~/.slicer-venv
~/.slicer-venv/bin/pip install squalor-slicer
ln -s ~/.slicer-venv/bin/slicer ~/.local/bin/slicer   # or anywhere on your PATH
```

`slicer --help` confirms it. Update with
`~/.slicer-venv/bin/pip install -U squalor-slicer`; uninstall by
deleting the venv and the symlink.

If you prefer a single command, `pip install --user squalor-slicer` also works, with two
caveats: on macOS Homebrew and recent Debian/Ubuntu/Fedora the system Python is
"externally managed" (PEP 668) and rejects it — use the venv above instead — and the user
scripts directory must be on your `PATH` (`~/.local/bin` on Linux,
`~/Library/Python/3.11/bin` on macOS). Uninstall with `pip uninstall squalor-slicer`. If
you already use [pipx](https://pipx.pypa.io) or [uv](https://docs.astral.sh/uv/),
`pipx install squalor-slicer` or `uv tool install squalor-slicer` install it isolated and
on your `PATH` in one step.

To try unreleased main instead of the latest release, replace the package name with
`git+https://github.com/squalor-xyz/slicer`.

### Develop on it (clone + editable)

To hack on slicer itself, use an editable install so the command tracks your checkout:

```sh
git clone git@github.com:squalor-xyz/slicer.git
cd slicer
python3 -m venv .venv
.venv/bin/pip install -e .
ln -s "$PWD/.venv/bin/slicer" ~/.local/bin/slicer   # or anywhere on your PATH
```

The install is editable, so `slicer` follows the checkout. `slicer --help` confirms it.

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

**→ [docs/getting-started.md](docs/getting-started.md)** walks through all of this with
real output. [docs/import.md](docs/import.md) is the outline format;
[docs/agents.md](docs/agents.md) is how to drive slicer from an AI agent;
[docs/migrate-format.md](docs/migrate-format.md) is the legacy grammar; and
[docs/configuration.md](docs/configuration.md) is every config key.

## Commands

| | |
|---|---|
| `ai instructions` | agent quick start, available without a project; supports `--json` |
| `ai skill` | the same loop and exit rules as a `SKILL.md` for Claude Code, Codex, and Grok |
| `init [--force]` | create `.slicer/` with config and templates; `--force` rewrites an existing config and templates only |
| `setup-git` | print the two `git config` lines that enable the `slicer-generated` render merge driver in this clone (`slicer setup-git \| sh` applies them); needs no project |
| `import FILE [--dry-run] [--force]` | bulk-load a roadmap from a markdown outline |
| `import --skeleton` | print an outline template built from your config |
| `migrate --from DIR [--dry-run] [--force]` | convert an existing legacy markdown tree; `--force` replaces an existing roadmap |
| `add TITLE [--id/--size/--tree/--findings/--status/--pass/--importance/--urgency/--effort/--depends-on/--short-title]` | append a roadmap item (no slice file yet); repeat `--depends-on ID` for multiple dependencies. `--effort` is 1–3 and optional; an open item left at importance 2, urgency 2 and no effort gets a stderr hint |
| `promote ID [--file/--stdin] [--boundary TEXT] [--force]` | give an item a slice file; a one-item outline fills its sections in one call. `--force` overwrites an existing slice |
| `move ID --before/--after/--to` | reorder the queue; position is the manual priority, and breaks score ties |
| `sort [--by score\|effort] [--render]` | reorder the whole queue in one step. `score` (default) persists `list --sort score`. `effort` persists `list --sort effort`: lightest estimate first, unset last |
| `next [-n N] [--start] [--show\|--ready [--section NAME ...]]` | one eligible item at offset N (default 0), with its effective score and status; `--start` marks it started. `--show` adds the full item and its slice. `--ready` returns item identity, the slice, and blocked ids. Repeat `--section` with `--ready` to return the scope boundary and those sections only. An item started or claimed in a sibling Git worktree is skipped and reported (`in_work_elsewhere`), unless this checkout has it too |
| `next-id` | the id the next `add` or `import` would take, without allocating it |
| `list [--all] [--status/--tree/--pass/--flag] [--sort score\|effort]` | the queue in `next`'s order: unblocked started, then unblocked open, then the other visible rows, each by effective score. The text table has a CLAIM column: the local owner, `*` for locally in-progress with no claim, `wt:NAME` for work in a sibling worktree, or `-`. `wt:NAME+N` means N more worktrees. `--json` includes `claim` (`{"owner", "at"}` or null) and `in_work_elsewhere` (an array of `{worktree, owner}`). Done and retired items are omitted unless `--all` is set or `--status` names them. Repeat `--flag` to keep an item that has any of those flags. Flags are free-form labels set with `set --flag`. `--sort score` is a flat score sort. `--sort effort` orders estimates 1–3 and puts unset items last, without writing state |
| `find PATTERN [--in FIELDS]` | search items by text (id, title, findings and slice bodies by default); shows the matched field and a snippet |
| `deps [ID] [--format mermaid]` | dependencies: unblocked open items, or one item's waits-on/blocked-by/dependents; `--format mermaid` renders the graph |
| `show ID [--section NAME ...] [--context]` | print one slice or selected sections; `--context` adds title, dependencies, and scope boundary |
| `set ID [ID ...] --title/--short-title/--size/--tree/--findings/--status/--pass/--depends-on/--flag/--no-flags/--group/--importance/--urgency/--effort/--no-effort` | change fields. `--no-effort` clears an estimate. `add` and `set` refuse an unknown, self, retired, or cycle-closing `--depends-on` and write nothing |
| `edit ID (--section NAME / --boundary) [--text/--file/--stdin]` | edit a section or scope boundary; sections also accept `--append` |
| `note ID [--text/--file/--stdin] [--render]` | append a dated note to any item — no slice needed (shows in `show`/`render`, unlike `done --note`) |
| `prose list / show REF / edit REF` | read and edit the roadmap's own prose |
| `prose add-pass KEY / drop-pass KEY` | open or close a pass group |
| `goals` | print the project's goals and non-goals together; supports `--json` |
| `start ID [ID ...] [--note TEXT]` | mark an item in progress and claim it (owner and time). The owner is `claim_owner` in config, otherwise the git user name, otherwise the worktree name. A second start does not refresh the claim |
| `release ID [ID ...]` | clear a claim without changing status. An item that was in progress stays in progress and lists as `*` |
| `handoff ID [ID ...] [--note TEXT]` | hand a started slice to review: status becomes `review_status` and the claim is cleared. `next` skips review items and dependents stay blocked until `done`; a reviewer finds them with `list --status review` and claims one with `start` |
| `done ID [ID ...]` / `park ID [ID ...]` / `unpark ID [ID ...]` `[--note TEXT]` | change status; `--note` records a one-line *history* entry (for a durable note on the item, use `slicer note`); `done` moves the file with `git mv` and clears a claim |
| `remove ID --reason "…"` | retire an obsolete item; the id stays claimed |
| `remove ID --purge` | delete outright, for something that never should have existed |
| `remove ID --purge/--reason --dry-run` | preview the removal and its fallout (dependents, id fate); write nothing |
| `remove ID ... --force` | retire or purge despite dependents, or a done item |
| `render` | regenerate `.slicer/render/` (ROADMAP.md, a browser-viewable ROADMAP.html, and one file per slice) |
| `sync [--check]` | rewrite derived lines in other documents |
| `verify` | check the index for consistency, and against `git log` (unless `git_check` is off) |
| `check [--diff]` | the CI gate: render staleness, sync drift, integrity |
| `stats` / `log [--limit N] [--item ID] [--action A]` | counts + completion % and per-tree progress; history, newest first (`--limit` defaults to 20; `--item`/`--action` scope it; `set` records old→new values) |
| `status` | the front door: next item, progress census, and blockers in one view (`--json`) |
| `tui` / `ui` | browse, read, reorder and edit interactively (two names for the same command) |

For TUI keys, filters, the wizard, and display behavior, see the [TUI manual](docs/tui.md).

The sibling worktree signal comes from local `git worktree list` and each checkout's
`.slicer/index.json`. It sees worktrees on this machine only, not work on another machine.

`slicer next -n 1` returns the item after the current next item. Offsets are
nonnegative integers: `-n 0` is the same as `next`. Eligible started items come
before eligible open items; each group uses descending effective priority with
roadmap order breaking ties. Skipping an item does not complete it or unblock its
dependents. The command returns one item, including rows without slice files;
an exhausted offset exits 2 (JSON returns `item: null` and blocked details).
The text form of `slicer list` labels its columns: number, id, status, size,
effort, score, quadrant, and title. An unset effort appears as `-`. When the
default status filter hides done or retired items, a final line counts the
matching hidden rows and points to `--all`. `--all` and `--status` suppress that
notice; `--json` remains an array of the visible item records.
`slicer next --ready` is a bounded pickup of that same item: `id`, `title`,
`status`, `depends_on`, `effective_score`, `path`, the slice when the item has
one, and every blocked id. The agent loop uses
`slicer next --ready --section "Implement" --section "Check" --json --lean`.
The headings are examples; pass the section names the project configures.
Repeat `--section NAME` with `--ready` to keep the
scope boundary and those section bodies; omit it and the slice stays complete.
`--section` without `--ready` is a usage error. A row with no slice still says
to run `promote`. An empty queue uses the same exit 2 result as `next`.
Pass either `--ready` or `--show`. The text form of `--ready` stays the
identity, the boundary, and the section headings.

Every command except the interactive `tui`/`ui` takes `--json`, including the failures — an agent calls `slicer next
--json` rather than parsing markdown, and reads `{"error": {"code": ...}}` rather than
prose. Exit codes: `0` fine, `1` drift or a failed check, `2` usage, validation, or nothing to do, `3` internal or state (`corrupt`, `locked`, `io`, `config`, `schema_too_new`).
See [docs/agents.md](docs/agents.md).

Every command that changes state — `add`, `set`, `start`, `done`, `move`, `sort`, `promote`,
`edit`, `note`, `remove`, `park`, `unpark`, `import`, `migrate`, and the `prose` edits — takes `--render`
to regenerate `.slicer/render/` in the same step, so a mutation and its render are one
command. By default the change is saved first and rendered after; add `--strict` to require
the render to succeed first, so a change that cannot be rendered is rolled back rather than
landed (this is how `done --render` already behaves). The agent loop passes
`--render --strict` on `start` and on slice edits. `done` stays `--render`.

Items carry an Eisenhower-style priority: an `--importance` and an `--urgency` (each 1–3),
combined into a score (importance leads). A blocker of a critical item inherits its
priority, so `slicer next` and `slicer list --sort score` surface the blockers of
important work first, while the stored queue order stays whatever `move` set.

**slicer never commits, pushes or tags.** `git` access is allowlisted to
`rev-parse`, `status`, `log`, `mv`, `ls-files`, and the read-only queries
`worktree list --porcelain`, `branch --all`, and `config --get user.name`. `start` uses
the branch and worktree queries to warn when another checkout already refers to the slice;
the exit code does not change, and
`next` stays silent. Writing subcommands cannot be reached from the code at all.

### Batch changes

`done`, `start`, `release`, `park`, `unpark`, and `set` accept multiple IDs.
For example, `slicer set S01 S02 --urgency 3 --render` applies the same fields to
both items. Use `slicer done -` to read whitespace-separated IDs from stdin.
See [batch changes](docs/getting-started.md#batch-changes) for validation and output rules.

## What lives in `.slicer/`

```
config.json          paths, statuses, fields, templates, sync targets
index.json           the ordered queue plus roadmap prose (preamble, goals, non-goals, epilogue)
slices/<ID>.json     one open slice: title, lead, sections (ordered list)
slices/done/<ID>.json    finished slices
slices/retired/<ID>.json obsolete slices, with the reason on the item
templates/*.md       render templates, yours to edit
render/              GENERATED - ROADMAP.md, ROADMAP.html (browser-viewable), and one file per slice
log.jsonl            append-only history of status changes
.gitattributes       union-merges log.jsonl, and keeps generated render/ on merge (see below)
```

Sections are an ordered **list**, not a map: real slices carry headings no schema names,
sometimes more than once, and their order is part of the document.

Landing work on parallel branches touches these files: `log.jsonl` is append-only and
union-merges automatically (via the generated `.gitattributes`), so both sides' entries
survive without a conflict. `index.json` is the source of truth — a genuine overlap there is
yours to resolve. Anything under `render/` is a projection of `index.json`, so after resolving
a merge just re-run `slicer render` (and `slicer check` will flag it if you forget) rather than
merging the generated markdown by hand.

The `.gitattributes` also points `render/` at a `slicer-generated` merge driver that keeps the
current branch's copy instead of writing conflict markers — but a driver name only resolves
once the clone defines it. Run `slicer setup-git` to print the two lines (or `slicer setup-git
| sh` to apply them), once per clone — slicer's git allowlist cannot run `git config` for you:

```sh
git config merge.slicer-generated.name "keep the current branch's generated files"
git config merge.slicer-generated.driver true
```

Either way, re-run `slicer render` after resolving `index.json` so the kept files match it.

The scope boundary is a separate field on each slice. It renders after metadata and
before sections, so section edits cannot remove it. Use `slicer edit ID --boundary`
with `--text`, `--file`, `--stdin`, or the editor to change the full paragraph. Empty
text clears it. `promote --boundary TEXT` overrides the source/default boundary.

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
dependency it left behind. Add `--dry-run` to either mode to preview the outcome first — the
dependents that would dangle and whether a purge would free or burn the id — without writing;
it turns that surprise into a decision.

Retiring needs a status to move into. A tracking directory created before `remove` existed
gains a `retired` status automatically on load — only that one key, so a project that
dropped some other status does not get it back.

## Roadmap prose

A roadmap carries text that belongs to no slice: an opening note, a heading and prose
around each pass group, and a closing section. `slicer prose` addresses those blocks:

```
preamble                 the opening note
goals                    project goals (see below)
non_goals                project non-goals (see below)
pass.<key>.heading       the group's markdown heading
pass.<key>.intro         prose above the group's table
pass.<key>.outro         prose below it
epilogue                 the closing section
```

`slicer prose list` names every block in the order it renders. Both `edit` and `prose edit`
take exactly one of `--text`, `--file`, or `--stdin`, or open `$EDITOR` when none is
supplied. In replacement mode, `--text` preserves the argument exactly, including
newlines, and `--text ""` clears the body. Section editing also accepts `--append` with an
explicit source: it joins old and new text with one blank line, removing boundary
newline characters. Empty appended content leaves the body unchanged.
For example, `slicer prose edit preamble --text "Current priorities"` replaces the preamble.
A pass group is opened with `prose add-pass 6 --heading
"# ..."` and closed with `drop-pass`, which refuses while any item is still filed under
it. Items are filed with `slicer add --pass 6` or moved with `slicer set <id> --pass 6`.

A declared pass renders even with no items yet, so a group can be opened before its first
slice exists.

## Goals and non-goals

The backlog says what is queued; **goals and non-goals** say what the project is *for*, so
humans and AI agents can judge what belongs on the backlog at all. They are two roadmap
prose blocks (`goals`, `non_goals`), edited like any other prose and rendered near the top
of `ROADMAP.md`:

```
slicer goals              print both, or slicer goals --json for agents
slicer prose edit goals   record or revise them (--text/--file/--stdin/$EDITOR)
slicer prose edit non_goals
```

`slicer check` keeps the rendered copy current. Set direction with the owner — do not
infer it from the backlog.

slicer's own goals and non-goals: run `slicer goals`, or read them at the top of
[the roadmap](.slicer/render/ROADMAP.md). In short, slicer is a small, dependency-light,
file-based store for a project's roadmap, goals, and issues — canonical JSON that humans
and AI agents plan from, projected deterministically to markdown — and is *not* a
real-time, multi-user collaboration tool (coordination happens through git).

## Configuration

Everything project-specific is in `.slicer/config.json` — status vocabulary and how each
one renders, the section list `promote` seeds, the scope-boundary marker, which flags
exclude an item from derived pointers, and the `sync` targets. Nothing is compiled into
the tool, so slicer works on a repo with no review protocol at all.

Every key, its default, and which ones are unsafe to change once items exist:
[docs/configuration.md](docs/configuration.md). Two to know up front — `id.prefix` and
`id.width` can change before the first item exists. After allocation the index owns the
scheme; changing the config does not renumber items, and a mismatch fails `check`.

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

slicer manages its own roadmap: `.slicer/` in this repository is a worked
example you can read, and `.slicer/render/ROADMAP.md` is what it renders to. Run
`slicer --version` for the installed version, and see the [changelog](CHANGELOG.md).

[docs/getting-started.md](docs/getting-started.md) is the walkthrough, and
[docs/agents.md](docs/agents.md) covers driving slicer from an agent.
[ARCHITECTURE.md](ARCHITECTURE.md) explains the layering and the invariants.
[AGENTS.md](AGENTS.md) has the commands and the house style.

Apache-2.0. Stdlib Python, no dependencies, and none planned.
