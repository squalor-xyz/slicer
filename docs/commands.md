# Command reference

Every slicer command and flag, and the behavior that spans commands. For a guided first
run see [getting started](getting-started.md); for JSON shapes and exit codes see the
[agent reference](agents.md); for keys and screens see the [TUI manual](tui.md).

Top-level `--help` shows the project description and source and issues URLs.
`--about` prints the name, version, description, source URL, and issues URL without
requiring a subcommand or project. `--about --json` returns `name`, `version`,
`description`, `url`, and `issues`. `--version` prints only `slicer VERSION`.

## Commands

| | |
|---|---|
| `ai [instructions]` | agent quick start. `slicer ai` and `slicer ai --json` are `slicer ai instructions` with the same flags. The project can set `implement_finish` to `handoff`, making step 4 handoff only, and `handoff_requires_note_kind` to name its required current-attempt report. No project, or an unreadable config or index, prints the generic text and warns on stderr. Does not lock or write. Supports `--json`. `--rest` prints only what the skill does not already carry and does not read the project |
| `ai skill [--output PATH] [--install] [--force]` | the same loop as a `SKILL.md` for Claude Code, Codex, and Grok. Same project read and fallback as `ai instructions`. `--output` writes the printed bytes to PATH (parents created); `--install` writes them to `.claude/`, `.agents/` and `.grok/` `skills/slicer/SKILL.md` under the project root. Different existing text exits 2 (`state`) with nothing written unless `--force`; stdout lists the paths written |
| `init [--force] [--id ID]` | create `.slicer/` with config and templates; `--force` rewrites an existing config and templates only. `--id` sets the id the first `add` allocates (`S21`), and is refused when the index already has items |
| `setup-git` | print the four `git config` lines that enable the `slicer-generated` render merge driver and the `slicer-index` driver for `index.json` in this clone (`slicer setup-git \| sh` applies them); needs no project |
| `merge-index BASE OURS THEIRS` | the `slicer-index` merge driver, which Git runs with its `%O %A %B` files and a person does not. It merges items by id and object fields by name, takes `next_id` as the largest of the three, and writes the result over `OURS`. A value changed on one side takes that change, equal changes coalesce, and lists and prose are atomic. Exit 0 is a clean merge. Exit 1 leaves conflict markers in `OURS` only where both sides changed a value differently, a record was deleted on one side and edited on the other, the same new id was filed twice with different content, both sides reordered the shared items differently, or the result would break the index (two ids for one filing key, ids differing only in case, a dependency cycle or on a removed item); stderr names each. Input that is not a readable index conflicts and is never merged cleanly. It needs no project, prints nothing when the merge is clean, and is left out of `--help`'s command list |
| `import FILE [--dry-run] [--force]` | bulk-load a roadmap from a markdown outline. When a sibling git worktree's `next_id` is higher than this checkout's, the id starts above it, so two worktrees do not file different items under the same id. |
| `import --skeleton` | print an outline template built from your config |
| `migrate --from DIR [--dry-run] [--force]` | convert an existing legacy markdown tree; `--force` replaces an existing roadmap |
| `add TITLE [--key KEY] [--id/--size/--tree/--findings/--status/--pass/--importance/--urgency/--effort/--depends-on/--discovered-from/--short-title]` | append a roadmap item (no slice file yet); repeat `--depends-on ID` for multiple dependencies. `--effort` is 1–3 and optional; an open item left at importance 2, urgency 2 and no effort gets a stderr hint. When a sibling git worktree's `next_id` is higher than this checkout's, the id starts above it, so two worktrees do not file different items under the same id. An explicit `--id` is unchanged |
| `promote ID [--file/--stdin] [--boundary TEXT] [--force]` | give an item a slice file; a one-item outline fills its sections in one call. `--force` overwrites an existing slice |
| `move ID --before/--after/--to` | reorder the queue; position is the manual priority, and breaks score ties |
| `sort [--by score\|effort] [--render]` | reorder the whole queue in one step. `score` (default) persists `list --sort score`. `effort` persists `list --sort effort`: lightest estimate first, unset last |
| `next [-n N] [--max-attempts N] [--batch K] [--tree TREE] [--size SIZE] [--flag PATTERN ...] [--no-flag PATTERN ...] [--status KEY] [--start [--owner NAME] [--start-to KEY]] [--show\|--ready [--section NAME ...]] [--notes-kind KIND ...] [--path]` | one eligible item at offset N (default 0), or up to K with `--batch`, with its effective score and status. Text for an item with a slice names `slicer show ID` and does not print the slice path unless `--path` is set. `--path` is text only. JSON still includes `path`, and `--json --lean` still drops it on an item. A row with no slice does not gain a show hint. The promote hint stays on `next --ready` and `next --show`. `next --show` text prints the slice without its generated banner, so it names no source path either; `show` and the files under `.slicer/render/slices/` keep the banner. `--tree` and `--size` narrow that pool to one tree and one exact size. `--start` marks the item or the whole batch started, claiming as `--owner` when given; a failed batch start claims nothing. `--status` set to the review status draws from the review queue instead (reviewing items first, then review), and `--start` then moves the item to reviewing. Any other `--status` must name a custom status, one that is not the open, started, done, retired, parked, review or reviewing role; an unknown or role key is `usage` and lists the choices. A custom queue ranks the caller's own claims first, then unclaimed items, each by effective score; an item another owner claimed is skipped and reported (`claimed_elsewhere`: `{id, owner}`). `--start` there claims the item in place, leaving its status and `attempts` alone, and resuming your own claim writes nothing. `--start-to KEY` (only with `--start` and a custom `--status`) also moves it to any configured status except done, retired, parked, review or reviewing; moving to the started status begins one implementation attempt, and `--max-attempts` then applies. Once the status changes, read-only `next --status` for the old queue no longer finds the item. `--batch` cannot be combined with `--show`, `-n`, or `--status`. `--show` adds the full item and its slice. `--ready` returns item identity, the slice, and blocked ids. Repeat `--section` with `--ready` to return the scope boundary and those sections only. An item a sibling has claimed or in work, or in review or done while this checkout's status differs and is not done, is skipped and reported (`in_work_elsewhere`: `{id, worktree, owner, status}`), unless this checkout has it started or claimed too. The same status on both sides is not reported |
| `next-id` | the id the next `add` or `import` would take, without allocating it. When a sibling git worktree's `next_id` is higher than this checkout's, the id reported starts above it, so two worktrees do not file different items under the same id. |
| `id-prefix [NEW] [--dry-run]` | print the id prefix, or change its case for new ids (`S` to `s`), in the index and config together. Existing ids keep theirs and no file is renamed. Any change other than case is refused (`usage`), and `--dry-run` reports without writing |
| `list [--all] [--status/--tree/--pass/--flag/--discovered-from] [--in-work] [--review] [--sort score\|effort]` | the queue grouped by readiness: unblocked in-work items, then unblocked open items, then other visible rows, with parked items last. Within each group, declared pass order comes first, then descending effective score, then stored queue order for ties. Empty and undeclared pass keys share a fallback rank after declared passes; without declared passes, the existing score order applies. Pass labels are not parsed as versions: declare earlier releases before later releases and put a deferred pass last. This applies to text and JSON output and filtered views. Explicit score/effort sorts override this default; `next` selection remains score-based. The text table has a CLAIM column: the local owner, `*` for locally in-progress with no claim, `wt:NAME` for work in a sibling worktree, or `-`. `wt:NAME+N` means N more worktrees. When the roadmap uses passes, a PASS column shows each row's pass key, or `-`. `--json` includes `claim` (`{"owner", "at"}` or null) and `in_work_elsewhere` (an array of `{worktree, owner, status}`, the sibling's status key). A sibling counts when the item is claimed or in work, and when it is review or done while this checkout's status differs and is not done. The same status on both sides is omitted, as is an empty `review_status`. Text ends with an `Only in a sibling` block (`ID  worktree  title`) when a sibling has an open, unclaimed item whose id this checkout lacks; `--json` stays a bare array and does not carry it. Done and retired items are omitted unless `--all` is set or `--status` names them. `--in-work` keeps items whose status is in `config.in_work()` (started, plus reviewing when set), including a started item with no claim. `--review` keeps only `review_status`, matching that key even when its label is not the word review. Either flag replaces the default omission of done and retired. Combining either with the other, with `--status`, or with `--all` is `usage`, as is an empty role. A role that matches nothing prints `no matching items`, exits 0, and does not print the hidden-count line. Repeat `--flag` to keep an item that has any of those flags. Flags are free-form labels set with `set --flag`. `--sort score` is a flat score sort. `--sort effort` orders estimates 1–3 and puts unset items last, without writing state |
| `find PATTERN [--in FIELDS]` / `find --title-exact TITLE` | search items by text (id, title, findings and slice bodies by default); shows the matched field and a snippet. `--title-exact` instead returns every item, done or retired included, whose full stored title equals TITLE by plain string equality (case- and Unicode-normalization-sensitive; short titles are not compared), reporting `title` as the matched field; it cannot be combined with PATTERN or `--in`, and `find` needs exactly one of PATTERN or `--title-exact`. With `--json --lean`, each row is only `{id, title, status, has_slice, match}` |
| `deps [ID] [--format mermaid]` | dependencies: unblocked open items, or one item's waits-on/blocked-by/dependents; `--format mermaid` renders the graph. A dependency is met in `done_status` or any status in `satisfies_dependencies` |
| `show ID [--notes-kind KIND ...] [--section NAME ...] [--context]` | print one slice or selected sections; `--context` adds title, dependencies, and scope boundary |
| `sections` | the configured section names, one per line, with `required` on the ones `next` treats as unspecified when empty. JSON is `{sections, required}`. Reads config only: it does not lock, write, or read the index |
| `config [KEY]` | the effective config, read-only. With no key, one `key  value` line per top-level key: lists are comma-separated and nested objects print as compact JSON. With `KEY`, the bare value, one element per line for a list. A dotted `KEY` reaches into a nested object (`id.prefix`, `pointers.later`). An unknown key is `usage` and names the top-level keys. Reads config only: it does not lock, write, or read the index. JSON is the whole config, or `{key, value}` |
| `recommended-workflow` | print the generic recommended project workflow: pick up one slice, start it, do the work, check it, and finish the way the project is configured. The text is [`docs/generic-recommended-project-workflow.md`](generic-recommended-project-workflow.md), which an installed copy carries too. It does not read the project, lock, or write, and it does not warn when there is no project. JSON is `{"workflow": "..."}`. A change to the recommended workflow updates that file in the same change |
| `set ID [ID ...] --title/--short-title/--size/--tree/--findings/--status/--pass/--depends-on/--flag/--add-flag/--remove-flag/--no-flags/--group/--importance/--urgency/--effort/--no-effort/--attempts` | change fields. `--flag` replaces the list. Repeatable `--add-flag` and `--remove-flag` edit it in place and cannot be combined with `--flag` or `--no-flags`. `--no-effort` clears an estimate. `--attempts` sets the implementation attempt count and must be an integer >= 0. `add` and `set` refuse an unknown, self, retired, or cycle-closing `--depends-on` and write nothing |
| `edit ID (--section NAME / --boundary) [--text/--file/--stdin]` | edit a section or scope boundary; sections also accept `--append` |
| `note ID [--kind KIND] [--owner ACTOR] [--stale-after TIMESTAMP] [--text/--file/--stdin] [--render]` | append a dated note to any item — no slice needed (shows in `show`/`render`, unlike `done --note`) |
| `note-verify ID --note-id NOTE [--owner ACTOR]` | verify an item note as a machine actor; supports `--render` and `--strict` |
| `note-attest ID --note-id NOTE --owner NAME` | attest an item note as `human:NAME`; a workflow convention, not caller authentication; supports `--render` and `--strict` |
| `prose list / show REF / edit REF` | read and edit the roadmap's own prose |
| `prose add-pass KEY / drop-pass KEY` | open or close a pass group |
| `goals` | print the project's goals and non-goals together; supports `--json` |
| `start ID [ID ...] [--note TEXT] [--owner NAME]` | mark an item in progress and claim it (owner and time). The owner is `--owner`, otherwise `SLICER_CLAIM_OWNER`, otherwise `claim_owner` in config, otherwise the git user name, otherwise the worktree name; a blank value falls through and one with a newline is `usage`. A second start does not refresh the claim. The owner is also recorded as `by` on the history entry |
| `release ID [ID ...] [--owner NAME]` | clear a claim without changing status; `--owner` names who released it in history (`by`). An item that was in progress stays in progress and lists as `*` |
| `handoff ID [ID ...] [--note TEXT] [--owner NAME] [--render] [--strict] [--check]` | hand a started slice to review (`--owner` names who handed it off, as `by` in history): status becomes `review_status` and the claim is cleared. `next` skips review items and dependents stay blocked until `done`; a reviewer picks one up with `next --status review --start` (or `start ID`), which moves it to `reviewing_status` (also out of plain `next`). `handoff` refuses a reviewing item. `--check` with `--render` runs `slicer check` after the change lands: stdout stays the item, a failure exits 1 with the findings on stderr, and `--check` without `--render` is `usage` and writes nothing |
| `reject ID [ID ...] --note TEXT [--to STATUS] [--owner NAME]` | send a review back: the item must be in `review_status` or `reviewing_status`, and goes to the open status (or `--to` any configured status that is not done, retired, started, review or reviewing). The claim is cleared, and the required `--note` verdict is added to the item's notes and to a single `reject` history entry. Every id is checked first; a non-review item is refused (`state`) and nothing is written |
| `done ID [ID ...]` / `park ID [ID ...]` / `unpark ID [ID ...]` `[--note TEXT]` | change status; `--note` records a one-line *history* entry (for a durable note on the item, use `slicer note`); `done` moves the slice file in the worktree and leaves it unstaged, and clears a claim. It takes `--owner NAME` to name who finished it (`by` in history). `done` also takes `--check` with `--render`: after the change lands it runs `slicer check`, keeps the item on stdout, and exits 1 with the findings on stderr when the check fails. `--check` without `--render` is `usage` and writes nothing |
| `remove ID --reason "…"` | retire an obsolete item; the id stays claimed |
| `remove ID --purge` | delete outright, for something that never should have existed |
| `remove ID --purge/--reason --dry-run` | preview the removal and its fallout (dependents, id fate); write nothing |
| `remove ID ... --force` | retire or purge despite dependents, or a done item |
| `render` | regenerate `.slicer/render/` (ROADMAP.md, a browser-viewable ROADMAP.html, and one file per slice) |
| `sync [--check]` | rewrite derived lines in other documents |
| `verify` | check the index for consistency. Inside git it also warns (exit 0) when sibling worktrees lack the render or index merge driver (`render_driver_check`) and when a done item has no commit subject in the last 2000 non-merge commits (`git_check`). Render freshness is `check`'s job. See [getting started](getting-started.md#9-when-something-goes-wrong) |
| `check [--diff]` | the CI gate: render staleness (each stale file's text line and `stale_render_details` entry name its cause, including a renderer-format mismatch between the file's `Render format: N.` header and the running slicer), sync drift, integrity, and unknown flags in the backtick `slicer ...` commands of live slices (commands inside fenced code blocks are literal text and are not checked). It also warns (exit 0) when a sibling git worktree has an item under the same id with a different title, which would collide on merge. The warning does not fail the gate |
| `stats` / `log [--limit N] [--item ID] [--action A] [--by NAME] [--since TIMESTAMP] [--until TIMESTAMP]` | counts + completion % and per-tree progress; history, newest first (`--limit` defaults to 20; `--item`/`--action`/`--by` scope it; `--since` is inclusive and `--until` exclusive, requiring timezone-aware ISO8601 timestamps; all filters apply before limit; `set` records old→new values; `by` is who started, claimed, released, handed off or finished an item) |
| `status` | the front door: next item, progress census, and blockers in one view (`--json`). Text adds an `Only in a sibling` block, and JSON an `only_in_sibling` list, when a sibling worktree has an unclaimed open item this checkout lacks |
| `tui` / `ui` | browse, read, reorder and edit interactively (two names for the same command) |

For TUI keys, filters, the wizard, and display behavior, see the [TUI manual](tui.md).

History windows accept either bound or both, for example
`slicer log --since 2026-10-05T12:00:00Z --until 2026-10-05T14:00:00+02:00 --json`.
Bounds and matching history timestamps compare as instants, including explicit UTC
offsets and fractional seconds. Equal bounds give an empty result (exit 0).
Malformed or timezone-less bounds and `since > until` are `usage` (exit 2),
validated before history is read. A malformed or timezone-less stored timestamp
encountered after item/action/by filtering is `corrupt` (exit 3) when a time bound
is supplied. Original timestamp strings, existing newest-first ordering and
reverse append-order ties are preserved. Timestamp windows cannot define an exact
run boundary under clock skew.

`next --max-attempts N` accepts a positive integer and skips fresh implementation
pickups whose stored `attempts` is already N or higher. Started work can still
resume, including after release; review pickup is unaffected. This is a per-call
filter, applied before offset and batch slots, with identical read-only and
`--start` selection. Capped atomic starts load and select under the writer lock.
It does not limit direct `start ID` or set a project default.

Skipped candidates appear in queue order as `capped` entries:
`{id, attempts, max_attempts}`. Text prints each skipped item and its count/limit.
The field is omitted when empty, including in lean output. Capped items are
reported even when their dependencies are unmet. A batch cannot take a capped
blocker, so its dependents remain blocked. A wholly capped pool retains the
no-item exit code 2 and writes no item state or history.

`attempts` counts fresh implementation starts: `start` from open or a custom
queue into the configured started status increments it once. Repeated starts,
release followed by resume, review pickups, rejection, and `set --status` do
not increment it. A start → reject → restart repair records two attempts.
Stored historical values are retained and may overcount because older versions
also counted rejection. History is not authoritative enough to reconstruct
them; owners may correct a count with `slicer set ID --attempts N` before
adopting an attempt cap.

If `handoff_requires_note_kind` is nonempty, started-to-review handoff requires an
item note with that exact kind, nonempty text, and `attempt` equal to the item's
current `attempts`. File it with `slicer note ID --kind KIND --text "Describe the
verified outcome"`. Missing reports fail with code `state`, naming the item and
kind; every batch item is validated before any writes, including strict rendering.
Release and resume keep the report valid; reject then restart needs a new report.
Legacy unknown-attempt notes, wrong kinds, previous-attempt reports, and history-only
`handoff --note` do not qualify. An already unclaimed review item remains a no-op.
`done` is unaffected. Manually changing `attempts` changes report association.

Item notes keep their public `notes` array as display strings and add `note_records`
with `{id, kind, text, created_at, attempt, by, verified_by, verified_at, stale_after, trust}`; `trust` is derived and never stored. `note ID --kind KIND` labels the dated
paragraph as `**DATE** — [KIND] text`; omitting `--kind` preserves the existing display.
`note_kinds` optionally restricts explicit kinds. New notes store a UUID, UTC creation
time, and the current implementation attempt. Editing preserves creation and authorship metadata but clears verification on text changes.

Repeat `--notes-kind KIND` on `show ID` or `next --ready` (including batches) to select
the union of exact kinds in stored order. `--notes-kind ''` selects untyped notes,
including legacy slice notes. Filters change only output; an empty match still returns
the item and sections. On section-only `show`, a filter adds `notes`, `note_records`,
and `slice_notes` to the usual section payload. Ready pickups include item `notes`
and `note_records`; projected slices also include their legacy `notes`.
`next --notes-kind` without `--ready` is a usage error.

## Flags every command takes

`--root DIR` names the project root; by default it is discovered from the working
directory.

Every command except the interactive `tui`/`ui` takes `--json`, including the failures — an agent calls `slicer next
--json` rather than parsing markdown, and reads `{"error": {"code": ...}}` rather than
prose. Exit codes: `0` fine, `1` drift or a failed check, `2` usage, validation, or nothing to do, `3` internal or state (`corrupt`, `locked`, `io`, `config`, `schema_too_new`).
`--lean`, with `--json`, prints compact JSON on one line and omits empty fields, a repeated short title, and an item path.
See the [agent reference](agents.md) for every payload.

Every command that changes state — `add`, `set`, `start`, `done`, `move`, `sort`, `promote`,
`edit`, `note`, `remove`, `park`, `unpark`, `import`, `migrate`, and the `prose` edits — takes `--render`
to regenerate `.slicer/render/` in the same step, so a mutation and its render are one
command. By default the change is saved first and rendered after; add `--strict` to require
the render to succeed first, so a change that cannot be rendered is rolled back rather than
landed (this is how `done --render` already behaves). The agent loop passes
`--render --strict` on `start` and on slice edits. `done` and `handoff` take
`--check` with `--render` to run `slicer check` after the change lands.

## Picking and listing work

The sibling worktree signal comes from local `git worktree list` and each checkout's
`.slicer/index.json`. It sees worktrees on this machine only, not work on another machine.
A project that sits below the git worktree root does not read those checkouts.
A sibling row counts when that item is claimed or in work, and when its status is review
or done while this checkout's copy has a different status and is not done. An empty
`review_status` is not that extra case. Each JSON entry adds `status`, the sibling's
status key. Text still shows `wt:NAME` and keeps this checkout's status in the STATUS
column. The rendered roadmap does not read siblings.

Canonical state remains git-tracked JSON in each checkout's `.slicer/`; coordination
happens through git. Commit tracking changes with the work before removing its worktree,
when the owner authorizes committing. No automatic handoff/done reminder is planned
([s175](../.slicer/render/slices/s175.md) was retired): `git worktree remove` refuses a
worktree with modified files unless forced, and `list` shows a sibling's review or done
work as `wt:NAME`. The sibling allocation floor and collision
warning shipped in [s185](../.slicer/render/slices/s185.md): `add`, `import`, and `next-id`
use the higher of this checkout's `next_id` and the highest matching-prefix `next_id`
visible in local sibling worktrees. This does not reserve ids across simultaneous
writers or other machines, or resolve merge
conflicts. Two branches that each file something still conflict on the single
`next_id` line of `index.json`; the `slicer-index` merge driver (`slicer setup-git`
prints its config, [s191](../.slicer/render/slices/s191.md)) resolves that line to the
larger value, and [s217](../.slicer/render/slices/s217.md) widens it to merge independent
items and fields; only a true disagreement is left as a conflict.

`slicer next -n 1` returns the item after the current next item. Offsets are
nonnegative integers: `-n 0` is the same as `next`. Eligible started items come
before eligible open items; each group uses descending effective priority with
roadmap order breaking ties. Skipping an item does not complete it or unblock its
dependents. The command returns one item, including rows without slice files;
an exhausted offset exits 2 (JSON returns `item: null` and blocked details).
The text form of `slicer list` labels its columns: number, id, status, claim,
pass (only when the roadmap declares or names a pass), size, effort, score,
quadrant, and title. `find` and `deps` rows use the same columns without the header. An unset effort appears as `-`. When the
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
to run `promote` on `next --ready` and `next --show`, and does not gain a show hint. When the item has a slice, text
for `next`, `next --ready`, `next --show`, and `next --batch` names
`slicer show ID` instead of printing the slice path. `--path` adds that path
line back and does not change JSON. `--json --lean` still drops `path` on an
item. An empty queue uses the same exit 2 result as `next`.
Pass either `--ready` or `--show`. The text form of `--ready` stays the
identity, the show hint, the boundary, and the section headings.
`slicer next --batch 3 --tree core --size S` returns up to three items from that
same pool: one tree, one exact size, started work before open work, highest
effective score first. A dependent follows its dependency when the dependency
is in the batch, even when the dependent ranks higher. Items still waiting on
a dependency outside the batch are listed in `blocked`. Items omitted only
because the batch is full are not. JSON is `{"items": [...], "blocked": [...]}`,
plus `unspecified` and `in_work_elsewhere` only when those skips happened.
With `--ready`, each element is the `{item, slice}` object one `--ready` call
returns, and `blocked` stays on the batch. `--section` trims every slice the
same way and is checked before `--start`. `--start` claims the whole batch
under one lock; `--owner` sets that claim. If starting fails, no id is claimed.
An empty batch exits 2 with `items` empty and the blocked list, and it does
not lock or start. `--batch` cannot be combined with `--show`, `-n`, or
`--status`, and K must be at least 1. `--tree` and `--size` without `--batch`
narrow a single `next`, including `-n`.

`next --flag PATTERN` includes items whose stored flags match any supplied
pattern; `--no-flag PATTERN` excludes items matching any supplied pattern and
wins over inclusion. Both are repeatable, case-sensitive shell-style patterns
(`*`, `?`, `[abc]`) interpreted by slicer. Quote wildcards to prevent shell
expansion, for example `slicer next --no-flag 'risk-*' --batch 3` or
`slicer next --flag 'area-*' --flag safe --no-flag 'risk-*'`.
An explicitly empty pattern is a usage error. Items without flags pass when
inclusion is absent and fail when inclusion is supplied. Omit both filters to
keep the existing pool. `list --flag` continues to match literal flag names.

Flag filters combine with tree, size, readiness, section selection, attempt
caps, the review queue and a custom status queue. They apply before offsets,
batch capacity and skip diagnostics, including blocked, unspecified, capped,
sibling-held items and claims held by someone else.
Dependency checks and effective scores still use the full index: excluding a
blocker leaves its dependent blocked, including in a batch. Reads and `--start`
use the same filtered pool.

## Priority

Items carry an Eisenhower-style priority: an `--importance` and an `--urgency` (each 1–3),
combined into a score (importance leads). A blocker of a critical item inherits its
priority, so `slicer next` and `slicer list --sort score` surface the blockers of
important work first, while the stored queue order stays whatever `move` set.

## Filing work

Filing retries use `slicer add "Title" --key KEY`. Keys are case-sensitive,
trim surrounding whitespace, and cannot be explicitly blank. An existing key
returns its current item with `existing: true`, ignoring replacement fields
before state validation or ID allocation. A new item returns `existing: false`.
Done and retired items reserve their keys. A retry writes nothing and skips
optional rendering, even when output is stale or `--strict` is set. Keys are
checkout-local and can also be supplied as `key:` in import outlines.

## Batch changes

`done`, `start`, `release`, `park`, `unpark`, and `set` accept multiple IDs.
For example, `slicer set S01 S02 --urgency 3 --render` applies the same fields to
both items. Use `slicer done -` to read whitespace-separated IDs from stdin.
See [batch changes](getting-started.md#batch-changes) for validation and output rules.

## Discovery provenance

`slicer add "Found work" --discovered-from S01` records the existing item that
discovered it in `fields.discovered_from`. The source may have any status. An
unknown ID fails with `no_such_item` before allocation or writes. This field
changes neither dependencies nor priority, and it leaves the configured status
default in effect.

`slicer list --discovered-from S01` filters by exact source ID and composes with
status, tree, pass, and flag filters. An unknown filter ID fails with
`no_such_item`; zero matching items succeeds. Default status exclusions still apply.

Rows include `discovered from S01` in findings when provenance is present.
Empty provenance leaves row output unchanged.

## Git access

**slicer never commits, pushes or tags.** `git` access is allowlisted to
`rev-parse`, `status`, `log`, and the read-only queries
`worktree list --porcelain`, `branch --all`, `config --get` of `user.name`,
`merge.slicer-generated.driver` and `merge.slicer-index.driver`, and `merge-file -p`
(three-way merge of three files to stdout, used only by `merge-index`). A mutation does not stage. `start` uses
the branch and worktree queries to warn when another checkout already refers to the slice;
the exit code does not change, and
`next` stays silent. Writing subcommands cannot be reached from the code at all.

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

Purging also refuses when another item names it in `discovered_from`;
`--force` leaves a dangling source that `check` reports, and its ID is not reclaimed.
Retirement preserves source references.

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
goals                    project goals
non_goals                project non-goals
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
slice exists. Markdown and HTML show version-shaped keys newest first (`v1.10` above
`v1.2`, a bare `v1` below `v1.1`), then other keys in declaration order. Items with
no pass follow: those that are not done, then done items. `prose list` stays in
declaration order.

Goals and non-goals, and how agents should treat them, are covered in
[getting started](getting-started.md#goals-and-non-goals).

### Note verification and freshness

`note --owner ACTOR` stores authorship, resolving the actor as an explicit owner,
then `SLICER_CLAIM_OWNER`, configured `claim_owner`, Git user name, or worktree name.
Authorship alone leaves a note unverified. Ordinary authorship and machine
verification reject the reserved `human:` prefix, including actor fallbacks.

Use `show ID --json` to obtain the stable `note_records[].id`, then
`note-verify ID --note-id NOTE [--owner ACTOR]` for machine verification (actor
precedence applies without `--owner`), or `note-attest ID --note-id NOTE --owner NAME`
for owner attestation, which always needs an explicit name. Names are nonempty,
one-line, and without `human:`. Attestation stores
`verified_by` as `human:NAME`, retaining the original author and text. These
commands record UTC `verified_at` and log the selected note ID and actor.
Repeating the same verifier makes no writes; another verifier replaces the prior
verification. Trust derives from the verifier: absent means `unverified`, a
machine actor means `machine-confirmed`, and `human:` means `human-reviewed`.
An actual text edit through ops or the TUI clears verification, preserving other
metadata and recording the previous verifier and time in history.

`note --stale-after TIMESTAMP` accepts timezone-aware ISO8601, normalizes it to
UTC, and rejects naive or malformed input before writing. Rendering displays
stored metadata and never computes expiry against the current clock. Old item
notes load with unknown authorship, no verification, and no expiry. Legacy slice
notes remain untyped read data and cannot be verified by these commands.

Owner attestation is a workflow convention, not caller authentication: an agent
can invoke the command. No identity or access-control boundary is claimed.
