# Getting started

Adding slicer to a project, from nothing to a rendered roadmap and a green CI gate.

For an AI agent, start with `slicer ai instructions` (or
`slicer ai instructions --json`). This built-in guide is the concise, canonical
onboarding; it explains the workflow and rules without requiring or reading project
state. Consult the [agent reference](agents.md) for detailed command/JSON contracts
and reusable prompts when you need them.

Console transcripts show output with the project path shortened to `~/code/my-project`.
Shell blocks are commands to run; the worked workflows use fictional example projects.

- [1. Install](#1-install)
- [2. Set it up](#2-set-it-up)
- [3. Configure before you add anything](#3-configure-before-you-add-anything)
- [Worked workflows: new project or AI code review](#worked-workflows)
- [4. Get your roadmap in](#4-get-your-roadmap-in)
- [5. Turn an item into a slice](#5-turn-an-item-into-a-slice)
- [6. The loop](#6-the-loop)
- [7. Passes, if you want them](#7-passes-if-you-want-them)
- [8. Wire it into CI](#8-wire-it-into-ci)
- [9. When something goes wrong](#9-when-something-goes-wrong)

## 1. Install

Python 3.11+, no dependencies. Install it to use slicer; clone it to change slicer.

### Install it (no clone)

Into its own virtual environment from PyPI. The distribution is `squalor-slicer`
because the `slicer` name on PyPI is taken; the command it installs is still `slicer`.

```sh
python3 -m venv ~/.slicer-venv
~/.slicer-venv/bin/pip install squalor-slicer
ln -s ~/.slicer-venv/bin/slicer ~/.local/bin/slicer   # or anywhere on your PATH
```

```console
$ slicer --help
```

Update with `~/.slicer-venv/bin/pip install -U squalor-slicer`;
uninstall by deleting the venv and the symlink.

For a single command, `pip install --user squalor-slicer` works too, with two caveats: on
macOS Homebrew and recent Debian/Ubuntu/Fedora the system Python is "externally managed"
(PEP 668) and rejects it — use the venv above — and the user scripts directory must be on
your `PATH` (`~/.local/bin` on Linux, `~/Library/Python/3.11/bin` on macOS). Uninstall with
`pip uninstall squalor-slicer`. Users of [pipx](https://pipx.pypa.io) or
[uv](https://docs.astral.sh/uv/) can instead run `pipx install squalor-slicer` or
`uv tool install squalor-slicer` for an isolated, on-PATH install in one step.

To try unreleased main instead of the latest release, replace the package name with
`git+https://github.com/squalor-xyz/slicer`.

### Develop on it (clone + editable)

To change slicer itself, use the editable contributor install in
[AGENTS.md](../AGENTS.md#install); the command then tracks your checkout.

If `slicer` is not found, check that the directory containing its executable is on
`PATH`. Contributors can run `PYTHONPATH=src python3 -m slicer --help` directly from
the slicer checkout without installing anything; see the
[contributor workflow](../AGENTS.md#working-on-the-roadmap).

## 2. Set it up

Run `init` in the root of the project you want to track:

```console
$ cd ~/code/my-project
$ slicer init
initialised ~/code/my-project/.slicer
```

That creates four things:

```
.slicer/config.json        everything project-specific
.slicer/index.json         the ordered queue — empty, next id S01
.slicer/templates/*.md     slice.md, roadmap.md, row.md — yours to edit
.slicer/slices/done/       where finished slices land
```

It deliberately does **not** create `.slicer/render/` (first `slicer render`),
`.slicer/slices/retired/` (first `slicer remove --reason`), or `.slicer/log.jsonl`
(first change). They appear when they are first needed.

`init` never overwrites an existing `index.json`. Running it twice refuses:

```console
$ slicer init
~/code/my-project/.slicer already exists; pass --force to overwrite its config and templates
```

`--force` rewrites `config.json` and the three templates. It still leaves `index.json`,
your slices and your log alone — it is for restoring a template you edited into a corner,
not for starting over.

## 3. Configure before you add anything

Most of `.slicer/config.json` is safe to change whenever you like. Two are not, and one
is a trap:

| Key | Default | Change it… |
|---|---|---|
| `id.prefix` | `"S"` | **Before your first item.** See below. |
| `id.width` | `2` → `S01` | **Before your first item.** |
| `sections` | `Why, Files, Failing tests, Implement, Check, Git` | Any time — it only affects future `promote`s. |
| `statuses` | `open: —`, `done`, `parked`, `later` | Labels any time; renaming a *key* items already use breaks them. |
| `boundary` | `**Not in this slice:**` | Any time; `""` turns off the scope-boundary warning. |

**One-way door:** `id.prefix` and `id.width` are yours to change until the first item
exists. Run `init`, look at what `S01` would be, decide you want `TASK-001` instead,
edit the config — the first `add` obeys it. Once an id has been handed out the index
owns the scheme, because ids are never reused, and `slicer check` reports a config that
disagrees rather than ignoring it. The one exception is the prefix's case:
`slicer id-prefix s` makes new ids lowercase, and existing ids keep theirs.

The full key-by-key reference, including which changes strand existing files, is in
[configuration.md](configuration.md).

## Worked workflows

slicer works with any coding agent that can read files and run commands. It does not
call an AI itself. You and the agent decide what to build; slicer turns the agreed
work into a persistent queue with dependencies, priorities, and readable slices.

Choose one example below, starting in your project's root after `slicer init` and
configuration. Each assumes an empty slicer queue, so the allocated ids are S01 and
S02. The example source paths describe proposed or fictional files, not slicer files.

### A new project: goals to a roadmap

Suppose you want a small CLI that reads a JSON settings file. Tell the agent the goal,
constraints, and acceptance criteria: Python standard library only, a required
`name` string, and a command that prints that name. Ask it to divide the work into
small slices with explicit checks and dependencies. The
[planning prompt](agents.md#plan-a-new-project) is a starting point.

After agreeing on the plan, save this outline as `roadmap.md`:

```markdown
# Settings CLI

## Load a settings file
size: S
tree: core
importance: 2
urgency: 2

### Why
The command needs a validated name from a JSON settings file.
### Files
Create settings.py and tests/test_settings.py.
### Failing tests
A valid name loads; missing, non-string, or blank names are rejected.
### Implement
Use the standard library to read JSON and validate the name field.
**Not in this slice:** command-line arguments or printing output.
### Check
Run the loader tests for valid and invalid input.
### Git
Follow the project's commit policy.

## Print the configured name
size: S
tree: cli
depends: Load a settings file
importance: 3
urgency: 2
```

The first item is a detailed slice. The second is deliberately a roadmap row: it records
future work and its dependency, but needs a specification before implementation.

### An existing project: AI review to a roadmap

Ask the agent to review the code and tests, citing file locations and concrete failure
cases for each finding. Use the [review prompt](agents.md#review-an-existing-project).
Discuss the findings, discard unsupported ones, and compare them with the existing
roadmap to avoid duplicates. Turn accepted improvements into small slices, preserving
their evidence and acceptance criteria.

Then ask the agent to convert that roadmap to the [outline format](import.md).
`slicer import --skeleton` gives it a template based on your project's configuration.
A free-form review is not directly importable, and `migrate` is only for the
[legacy markdown format](migrate-format.md).

For this fictional review, assume inspection found that `settings.py` accepts a blank
name and that the README omits the required field. Save this as `roadmap.md` instead
of the new-project example:

```markdown
# Settings review

## Reject blank configured names
size: S
tree: core
findings: R1; settings.py accepts a whitespace-only name
importance: 3
urgency: 2

### Why
A blank name currently reaches the CLI as apparently valid input.
### Files
settings.py and tests/test_settings.py.
### Failing tests
Loading a name containing only spaces must report invalid input.
### Implement
Validate the stripped name before returning settings.
**Not in this slice:** changing the configuration format or CLI output.
### Check
Run the regression test and existing settings tests.
### Git
Follow the project's commit policy.

## Document the required name
size: S
tree: docs
findings: R2; README omits the required name field
depends: Reject blank configured names
importance: 2
urgency: 2
```

For a real review, include actual paths and line references in the slice's Why section;
do not copy the fictional findings as evidence about your project.

### Import, prioritize, and work through either example

Validate the outline before applying it:

```sh
slicer import roadmap.md --dry-run
slicer import roadmap.md --render
slicer check
slicer list
slicer show S01
```

The import creates S01 with a slice and S02 as a row depending on S01. A rejected dry
run reports what to fix; resolve those problems before applying. After import, JSON
is the state: edit through slicer, not by re-importing the outline or editing generated
markdown. Importing the same titles again is refused by default.

Set the importance and urgency axes (each 1–3), then inspect the ranked queue:

```sh
slicer set S02 --importance 3 --urgency 3 --render
slicer list --status open --sort score
slicer move S02 --before S01 --render
slicer next
```

To locate an item by text rather than by filter, `slicer find PATTERN` searches ids, titles,
findings, and slice bodies (narrow with `--in title,findings,body`) and shows the matched field
and a snippet so you see why each item hit; it also takes `--json` (each result carries `match`).

`slicer status` is the "where am I / did it work" front door: it composes the next item, a
progress census, and the blocked edges into one view (and one `--json` payload), so you don't
run `next`, `stats`, and read `next`'s blocked set separately.

Default `list` is the view `next` walks: unblocked started items, then unblocked
open items, then the other visible rows, each by effective score. Done and retired
items are left out unless you pass `--all` or name them with `--status`.
`list --sort score` is a flat score sort of that same set, which can rank a blocked
item above work `next` would actually pick. `move` changes stored queue order and therefore
the rendered roadmap. To make the stored order match the flat score view in one step — instead of a
`move` per item — run `slicer sort`, which reorders the whole queue by priority score. S01 is
still next: S02 depends on it, and S01 inherits S02's
higher priority. Stored order breaks equal-score ties. For items added individually,
use `slicer set S02 --depends-on S01` to set the dependency; repeated `--depends-on`
flags replace the complete dependency list.

Read S01, agree on its scope, and begin:

```sh
slicer show S01
slicer start S01 --render
```

`start` records a claim on the item: who, and the time. `slicer list` names that owner
in the CLAIM column; an in-progress item with no claim is marked `*`. Work started or
claimed in a sibling worktree appears as `wt:NAME` (or `wt:NAME+N` for more worktrees)
when there is no local claim or started state. Other rows are `-`. `--json` carries
`claim` as `{"owner", "at"}` or null and `in_work_elsewhere` as an array of
`{"worktree", "owner"}` objects, even when a local claim takes precedence in the text
table. `slicer next` and `slicer status` skip an item a sibling worktree has in work and
report it (`skipped S03 (in work in wt:NAME)`, or `in_work_elsewhere` in JSON), unless
this checkout has it started or claimed too. This reads local Git worktrees only; it
cannot see work on another machine, and the rendered roadmap ignores it.
The owner is `--owner NAME` on
the command, otherwise the `SLICER_CLAIM_OWNER` environment variable, otherwise
`claim_owner` in config, otherwise the git user name, otherwise the worktree name, so
agents sharing one checkout can each claim under their own name. History records the
same name as `by` on start, release, handoff and done. `slicer release S01`
clears the claim and leaves the status. `done` clears it too, so a finished item is not
shown as claimed.

To pass finished implementation to someone else for review, merge, or cleanup without
marking it done, hand it off:

```console
$ slicer note S01 --text "Ready for review: branch feature/S01; suite green"
$ slicer handoff S01 --render
S01 -> review, unclaimed
```

The item moves to the `review` status and loses its claim. `next` no longer offers it,
and anything that depends on it stays blocked. A reviewer runs `slicer list --status
review`, reads the slice with `slicer show S01`, and runs `slicer start S01` to claim the
review, which returns it to started. If fixes need another look, hand it off again. Run
`slicer done S01 --render` only once review and merge are complete; slicer records the
handoff but never merges anything.

Implement S01 and run its acceptance checks and the project's tests. **Only after
those pass**, record completion:

```sh
slicer done S01 --note "Implemented and verified the settings validation" --render
slicer check
slicer next
```

S02 is now next. Since it is only a row, write its detailed specification using
[promote](#5-turn-an-item-into-a-slice) before starting it. An eligible started item
takes precedence over open items; dependencies still gate both. See the
[implementation prompt](agents.md#implement-one-slice) for handing the work to an agent.

`slicer check` verifies roadmap integrity and generated output, not your application's
correctness. Include changed `.slicer/` state and rendered markdown when preparing the
work for review, following your project's commit policy.

`slicer render` also writes `.slicer/render/ROADMAP.html` — a standalone, styled view you can
open in a browser or share without a markdown renderer. It is a checked projection like
ROADMAP.md, so `slicer check` keeps it current too.

## 4. Get your roadmap in

Choose individual items, a bulk outline, or migration of a legacy tree.

### 4a. From nothing

`slicer add` appends one roadmap row per invocation:

```console
$ slicer add "Parse the config file" --size M --tree core --findings "G1"
added S01  Parse the config file
$ slicer add "Fail loudly on a missing key" --size S --tree core --depends-on S01
added S02  Fail loudly on a missing key
```

`add` takes `--size`, `--tree` (repeatable), `--findings`, `--status`, `--pass`, `--id`,
`--importance`/`--urgency` (the priority axes, 1–3), `--depends-on` (repeatable, using
item ids), and `--short-title` for a shorter roadmap label while retaining the full
title. Omitted or empty short titles fall back to the full title. Use `set` to change
these fields later; repeated `set --depends-on` flags replace the dependency list.
An open item left at importance 2, urgency 2 and no effort ranks on nothing, so `add`
prints a stderr hint to score it and `import` lists such items under `warn`; neither
changes what is written or the exit code.
Both refuse a dependency `check` would reject — an unknown id (a comma list such as
`S01,S02` is one unknown id; repeat the flag instead), the item itself, a retired item, or
an edge that closes a cycle — and write nothing.

An item added this way is a roadmap row and nothing more. There is no slice file yet;
that is [step 5](#5-turn-an-item-into-a-slice).

```console
$ slicer list
  #  ID    STATUS  CLAIM SIZE EFFORT SCORE QUADRANT  TITLE
  1  S01   —       -     M    -      22    -         Parse the config file
  2  S02   —       -     S    -      22    -         Fail loudly on a missing key
```

The SCORE and QUADRANT columns are the priority score and quadrant (see
[step 6](#6-the-loop)); a fresh item sits at a neutral 2/2.

### 4b. In bulk, from a markdown outline

`slicer import` loads a whole roadmap from one file. Start from the template — it is
built from your own config, so the sections it suggests are the ones this project uses:

```console
$ slicer import --skeleton > roadmap.md
```

Each `##` heading is an item; `key: value` lines under it carry size, tree, findings,
status, pass, group and dependencies; `###` headings become the slice's sections. An
entry with sections gets a slice file automatically.

```console
$ slicer import roadmap.md --dry-run
source     ~/code/my-project/roadmap.md
outline    3 items, 1 with slices
status     — 2 · parked 1
depends    1 edges
nothing written; drop --dry-run to apply

$ slicer import roadmap.md
...
added      S01, S02, S03
now run `slicer render`
```

It is a **one-way ramp**: the file gets your roadmap in, and after that you manage items
with `slicer set`, `slicer edit` and the TUI. Running the same file twice is refused,
naming the collisions, so a double-apply cannot silently double your queue.

[import.md](import.md) is the full format.

### 4c. From an existing legacy markdown tree

`slicer migrate` converts a tree that is **already in slicer's own legacy format**: an
index named `README.md` containing a six-column table, and one file per slice whose first
line is `# S01 — title`. It is for projects that were running this workflow by hand
before slicer existed. For anything else — an arbitrary roadmap, an issue export, a
bullet list — write an outline and use [4b](#4b-in-bulk-from-a-markdown-outline).

[migrate-format.md](migrate-format.md) is the exact grammar, with a minimal working
example. Check yours against it before you start.

Always dry-run first. Nothing is written, and you get the full census:

```console
$ slicer migrate --from docs/slices --dry-run
source     ~/code/my-project/docs/slices
index      4 passes, 5 group rows, 14 items, next id S15
status     done 8 · — 3 · parked 2 · later 1
sizes      M 7 · S 3 · L 2 · M [OWNER] 2
slices     14 parsed, 15 round-trip byte-identical
sections   2 off-schema: Code review (same slice) 1, What landed 1
depends    1 edges
reconcile  13 title, 0 findings, 0 trees differences - both sides kept
would write .slicer/ under ~/code/my-project (nothing written)
```

`15 round-trip byte-identical` is the line that matters: 14 slices plus the index, each
parsed and re-emitted to exactly the bytes it came from. Import refuses to write
anything unless every file does that, so a document slicer cannot reproduce is never
half-migrated.

When it is clean, run it for real, then render — `migrate` does not render by default
(use `--render` to combine the steps):

```console
$ slicer migrate --from docs/slices
...
wrote      19 files under ~/code/my-project/.slicer
$ slicer render
rendered 15 file(s)
$ slicer check
check passed: 14 items, render and sync current
```

`--from` defaults to `docs/slices`. Once you trust the migration, delete the old tree —
`.slicer/render/` replaces it, and relative links inside imported prose have already
been re-based to their new depth.

## 5. Turn an item into a slice

A roadmap row says *what*. A slice says *why, which files, which failing test first*.
`promote` creates one:

```console
$ slicer promote S01
promoted S01 -> ~/code/my-project/.slicer/slices/S01.json
```

The headings come from `sections` in your config, each with an empty body, and the
scope boundary is stored separately, seeded with your `boundary` marker. The default
headings are:

```
Why · Files · Failing tests · Implement · Check · Git
```

The boundary renders after metadata and before these sections. Set it explicitly:

```sh
slicer edit S01 --boundary --text "**Not in this slice:** other work" --render
```

Boundary editing accepts the same input sources and editor as section replacement.
It cannot combine with `--section` or `--append`; empty text clears the boundary and
restores the unbounded-scope warning. `promote --boundary TEXT` sets it at creation,
overriding any boundary in a source outline.

Fill sections one at a time:

```console
$ slicer edit S01 --section Why --text "Validate settings before starting the app"
updated S01 / Why
```

`edit` and `prose edit` accept one body source: `--text`, `--file`, or `--stdin`.
Combining sources is a usage error. With none, the command opens `$EDITOR`.
In replacement mode, `--text` stores exactly the supplied argument, preserving spaces and newlines;
`--text ""` clears the body. File and stdin input strip trailing newline characters.
Quote inline text for your shell; for a value starting with a dash, use
`--text="- a bullet"`. Read a section back with `slicer show S01 --section Why`.

To append to a slice section, supply `--append` and one explicit source:

```sh
slicer edit S01 --section Why --append --text "Also report invalid values" --render
```

Append removes trailing newline characters from the old body and leading newline
characters from the new body, then joins nonempty bodies with one blank line. Other
whitespace and the source-reading rules above are preserved. Empty appended content
leaves the section and log unchanged; empty or missing sections get the new body
without a leading separator. Append without a source is a usage error.

```sh
slicer prose edit preamble --text "Current priorities" --render
```

To record an observation against an item — "tried X, it didn't work" — use `slicer note ID
--text "..."`. It appends a dated note to the item (no slice required — it works on a bare row
too), shown in `slicer show` and, once promoted, in the rendered slice; unlike `done --note`,
which only writes the log.

To fill the whole slice in one call rather than one `edit` per section, hand `promote` a
one-item outline instead — the same `##` item / `### section` shape `import` reads (see
[import.md](import.md)). The item already owns its fields, so the source is sections and
lead only:

```console
$ slicer promote S01 --file draft.md
promoted S01 -> ~/code/my-project/.slicer/slices/S01.json
```

For anything more than one section, `slicer tui` (or its alias `slicer ui`) is also faster than repeated `edit`s. See the [TUI manual](tui.md) for the full key and filter reference:
`tab` moves between the queue and the detail pane, and `e` opens `$EDITOR` on whatever is
selected. Select a note and press `e` to edit it; saving an empty body removes that note.
Select `+ add a note` and press `e` to add one. Press `J`/`K` to reorder down/up, `T` to
move to the top, and `M` to enter a position. Clear search and filters with `c` first.

The Queue and Details headings show which pane has focus. A focused selection uses
reverse/bold; the other pane retains a selection marker. `P:22` in a queue row is
the base priority score (importance × 10 + urgency), not inherited priority. An
importance or urgency of 3 emphasizes that score without changing queue order.
Configured started/done/parked statuses use cyan/green/yellow; active blocked items
retain `!` and use yellow. Other statuses stay neutral. Error feedback is red;
`OK:`, `Error:`, and `Info:` prefixes also make results readable without color.

The palette uses the terminal's default background. Unsupported terminals and any
nonempty `NO_COLOR` value select monochrome, for example `NO_COLOR=1 slicer tui`.
Keep the terminal at least 80 columns by 10 rows; smaller windows show a resize
prompt while retaining filters, search, and selection.

Both `show` and `edit` tell you when an item has not been promoted:

```console
$ slicer show S03
S03  Cache the parsed config
(no slice yet; run `slicer promote S03`)
```

## 6. The loop

```console
$ slicer next
S01  Parse the config file
     ~/code/my-project/.slicer/slices/S01.json
```

`next` is the highest-priority *startable* item: the highest effective score among items
whose dependencies are all done. Because a blocker of a critical item inherits its
priority, `next` surfaces the blocker first. Add `--json` and an agent can read it without
parsing markdown — every read command takes `--json`.

Mark it in flight before you start typing, so the queue can answer "what am I in the
middle of":

```console
$ slicer start S01
S01 -> started
```

From here `slicer next` returns S01 ahead of every open item, whatever they score —
finishing what you started beats picking up something new. The slice file does not move;
`started` is not a folder.

Implement it, then:

```console
$ slicer done S01 --note "loader now refuses a missing key"
S01 -> done
```

`done` moves the slice file into `slices/done/` with `git mv`, so the status change
stays one tracked rename:

```console
$ git status --short
 M .slicer/index.json
 M .slicer/log.jsonl
R  .slicer/slices/S01.json -> .slicer/slices/done/S01.json
```

Now re-render. `check` will tell you if you forget:

```console
$ slicer check
stale render: ROADMAP.md
check failed; run `slicer render` and `slicer sync`, then re-run
$ slicer render
rendered 1 file(s)
$ slicer check
check passed: 4 items, render and sync current
```

Or skip the separate render: every mutating command takes `--render`, so
`slicer done S01 --render` marks it done *and* re-renders in one step. The same flag works
on `add`, `set`, `move`, `edit`, `import`, and the rest. By default the change is saved
first and rendered after; add `--strict` to require the render to succeed first, so a
change that cannot be rendered is rolled back instead of landing.

**Commit `.slicer/` — all of it, including `render/`.** The markdown is generated, but
it is what people read in a diff and in a pull request, and `check` fails when it is
stale. That staleness check is the whole point: state and its rendering cannot drift.

**Set up the render merge driver once per clone.** The generated `.gitattributes` points
`render/` at a `slicer-generated` driver so a merge keeps the current branch's copy instead
of writing conflict markers into those large files. A driver name only resolves once the
clone defines it, and slicer's git allowlist cannot run `git config` for you, so run these
two lines once in each clone. `slicer setup-git` prints them (`slicer setup-git | sh` applies
them), and `slicer init` also prints them when it is run inside a git repo:

```sh
git config merge.slicer-generated.name "keep the current branch's generated files"
git config merge.slicer-generated.driver true
```

The kept copy still has to match the merged `index.json`, so re-run `slicer render` after
resolving a merge — the driver only skips the markers, it does not re-project the state.
`slicer verify` warns (it never fails) when this clone has other worktrees but has not
configured the driver, so a forgotten setup is caught before a merge writes markers rather
than after. A single-worktree clone stays quiet, and `render_driver_check: false` in config
turns the reminder off entirely.

### Priority

Every item carries an **importance** and an **urgency**, each 1–3 (default 2). Set them
with `slicer set S01 --importance 3 --urgency 2` or at `add` time, and rank the backlog
with `slicer list --sort score`. The score is `importance × 10 + urgency`, so importance
leads and urgency breaks ties. A blocker of a high-scored item inherits its score, so
`slicer next` and the sorted view surface the blockers of important work first — while the
stored queue order stays whatever you set with `move`. Priority guides what to do next; it
never silently reorders the roadmap.

`slicer log` shows the history of status changes; `slicer stats` gives counts by status,
size, tree and pass, plus overall completion (done/total, percent) and a per-tree status
breakdown so you can see how far along you are and which tree is lagging.

When setting work aside or returning it to the queue, record the reason in the log:

```sh
slicer park S02 --note "Waiting on upstream" --render
slicer unpark S02 --note "Upstream fix is available" --render
```

Notes are optional on both commands, as on `done` and `start`.

## Batch changes

Use multiple IDs to apply the same status, fields, or history note in one command:

```sh
slicer set S01 S02 --importance 3 --urgency 2 --render
slicer start S01 S02 --note "Starting related work" --render
slicer done S01 S02 --note "Implemented and verified" --render
```

`park` and `unpark` accept the same multiple-ID form. To read IDs from a file or
pipeline, use a standalone `-`:

```sh
printf 'S01\nS02\n' | slicer done - --note "Verified" --render
```

Stdin accepts whitespace-separated IDs, not JSON. Empty stdin and mixing `-` with
explicit IDs are errors. Repeated IDs are processed once, in their first-seen order.

The whole request is validated before any item changes. An unknown ID or invalid
field refuses the batch. The mutation loads state once and saves the index once;
`--render` reloads state for rendering. The command holds the existing writer lock.
Individual file writes remain atomic, but disk failures do not roll back the batch.

Status changes record one history entry per changed item; items already in the
requested status are unchanged. `set` retains its existing per-item history behavior.
Human output has one result line per item. With `--json`, one explicit ID returns
an object; multiple positional IDs or stdin return an array, even when duplicates
or stdin resolve to just one ID.

## 7. Passes, if you want them

Passes are an optional grouping — a review round, a milestone, a phase. Items with no
pass render as one flat table, which is fine for most projects.

```console
$ slicer prose add-pass 2 --heading "# Pass 2 — hardening"
$ slicer add "Rotate the signing key" --pass 2
$ slicer set S03 --pass 2
```

`slicer add` without `--pass` files the new item under the *previous* item's pass, so once
the tail of the queue is in a pass, every later `add` lands there too. Name the pass you
mean, or pass `--pass ''` to file it with no pass (`slicer set ID --pass ''` clears one
later). `import` never inherits a pass.

Once the roadmap has a pass, `slicer list` (and the `find` and `deps` rows) gains a PASS
column after CLAIM, showing each item's pass or `-`. `slicer list --pass 2` narrows the
table to one pass.

A declared pass renders even with no items yet, so you can open a group before its first
slice exists. `prose drop-pass 2` closes it, and refuses while any item is still filed
there:

```console
$ slicer prose drop-pass 2
slicer: pass '2' still has 2 item(s): S03, S05. Move them first with `slicer set <id> --pass <other>`.
```

Declared passes render first, in the order you declared them; anything still carrying no
pass forms a trailing group with no heading. So once you start using passes, file
everything, or live with that untitled group at the bottom.

Each pass carries its own prose — `pass.2.heading`, `pass.2.intro`, `pass.2.outro` —
alongside the roadmap's `preamble`, `goals`, `non_goals`, and `epilogue`. `slicer prose
list` names every block in render order; `slicer prose edit REF` changes one. See
[Roadmap prose](commands.md#roadmap-prose) in the command reference.

A pass is one milestone, and trees name subsystems. Flags are the other axis: free-form
labels an item can hold several of at once, such as `security` or `perf`. Set them with
`slicer set S01 --flag security --flag perf` (that replaces the list; `--no-flags` clears
it). `slicer list --flag security` returns items carrying that flag, and repeating
`--flag` matches any of the names. The TUI filter panel (`f`) lists each distinct flag
and `(none)` for an item with no flags. `exclude_flags` in config still only drops an
item from the sync pointers.

## Goals and non-goals

Goals and non-goals record what the project is *for*, so what belongs on the backlog can
be judged — separately from what is currently queued. They are two roadmap prose blocks:

```console
$ slicer prose edit goals --file goals.md      # or --text/--stdin/$EDITOR
$ slicer prose edit non_goals --stdin < non_goals.md
$ slicer goals                                 # print both together
$ slicer goals --json                          # {"goals": "...", "non_goals": "..."}
```

They render near the top of `ROADMAP.md`, so a reader sees direction before the queue, and
`slicer check` fails if that rendered copy drifts. Agents read direction with `slicer goals
--json` and should ask the owner to set it rather than inferring it from the backlog.

## 8. Wire it into CI

One command, one exit code:

```yaml
- name: Install slicer
  run: pip install squalor-slicer

- name: slicer check
  run: slicer check
```

This is the same no-clone install as [section 1](#1-install), run in a throwaway CI
environment where a shared venv is unnecessary. `check` composes render staleness, sync
drift and the offline integrity checks. It never shells out to git, so a shallow checkout
is fine.

`slicer verify` is the other one — it additionally compares the index against
`git log --all`, warning when an item is recorded done but no commit subject mentions
it. If you add it to CI it needs `fetch-depth: 0`, or it sees no history and skips.

## 9. When something goes wrong

Exit codes: **0** fine · **1** drift or a failed check · **2** usage, validation, or
nothing to do · **3** internal or state (`corrupt`, `locked`, `io`, `config`,
`schema_too_new`).

| What you see | What it means |
|---|---|
| `no .slicer/ found in … run \`slicer init\` in the project root` | You are outside a tracked project. slicer walks up from the working directory looking for `.slicer/config.json`, git-style. |
| `… already exists; pass --force to overwrite its config and templates` | `init` on an initialised project. `--force` rewrites config and templates only. |
| `<ID> has no slice; run \`slicer promote <ID>\` first` | `edit` on a roadmap row that has no slice file yet. |
| `<ID> already has a slice; pass --force to overwrite it` | `promote` on an already-promoted item. `--force` discards what is there. |
| `unknown status 'x'; known: [...]` | The status is not a key in `config.statuses`. |
| `id <X> already exists; ids are never reused` | `add --id` naming a claimed id. Ids are claimed for the life of the project. |
| `stale render: …` / `check failed` | Run `slicer render` (and `slicer sync` if you have sync targets). |
| `template missing; re-run \`slicer init --force\` to restore it` | A file under `.slicer/templates/` was deleted. |
| `… written by a newer slicer (schema N; this build knows M). Upgrade slicer to open this project.` | The project was saved by a newer slicer. 1.0.0 writes index schema 2 (`config.json` stays at 1): it opens a schema-1 project with no migration step, and the first change it saves stamps schema 2, after which an older build refuses the project rather than dropping claims. Upgrade every clone that shares the project. Pre-release checkouts from before this guard (S72) do not refuse; they can drop claims and rewrite the file at schema 1. |
| `Git merge in progress in …; finish or abort it before changing slicer state.` | slicer refuses state changes, including TUI saves, while this checkout has a merge to finish (`merge_in_progress`, exit 2). Read-only commands, dry runs, `render` and `sync` still work for the repair; finish or abort the merge, then retry. |
| `running code from …, but this project is … -- a different worktree of the same repo` | A stderr note, not an error: an editable install from one worktree is running against another worktree's `.slicer/`, so that checkout's `src/` edits are not what runs. Use `PYTHONPATH=src python3 -m slicer` in the checkout you are editing, or set `SLICER_NO_CODE_WARNING=1` to silence it. |
| `refusing to write: fix the problems above` | An import or migration found problems. Nothing was written; see [import.md](import.md) or [migrate-format.md](migrate-format.md). |
| merge conflict under `.slicer/` | `log.jsonl` union-merges on its own (via the generated `.gitattributes`). `index.json` is the source of truth — resolve a real overlap there by hand. For anything under `render/`, don't merge it: with the `slicer-generated` driver configured (see below) the merge keeps the current branch's copy with no markers; either way, resolve `index.json`, run `slicer render`, `git add .slicer/render`, and continue — `slicer check` catches a forgotten re-render. |

`slicer verify` is the broader health check — dangling dependencies, cycles, a dependency
on a retired item, slices in the wrong folder for their status, a slice file whose name
and contained id disagree, `has_slice` out of step with the files on disk, an id that is
not a usable filename, and a `next_id` that would reuse an id. It also cross-checks status
against `git log` unless `git_check` is off in config. It reports and never rewrites.

---

Next: [configuration.md](configuration.md) for every config key,
[import.md](import.md) for the outline format, [agents.md](agents.md) for driving
slicer from an agent, [migrate-format.md](migrate-format.md) for the legacy grammar, and
[ARCHITECTURE.md](../ARCHITECTURE.md) if you are changing slicer itself.
