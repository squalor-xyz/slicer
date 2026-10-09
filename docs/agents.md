# Driving slicer from an agent

Start with `slicer ai instructions` for the canonical concise onboarding guide.
It is available wherever slicer is installed and works without an initialized project.
A readable project whose `implement_finish` is `handoff` changes step 4 to handoff;
otherwise step 4 stays done, including when that key is missing. A nonempty
`handoff_requires_note_kind` also adds the project's report requirement. No project, or a
config or index that cannot be read, prints the generic text, warns on stderr, and
exits 0. It does not lock or write. `slicer ai instructions --json`
returns `{"instructions": "..."}` with the same Markdown as the text output.
`--root` selects the project. This page provides the detailed reference and
reusable prompts; the command is the single source for the quick start.

`slicer ai skill` prints a `SKILL.md` for that implement loop and the exit-code
rules, using the same finish choice and the same fallback. Claude Code, Codex, and Grok load the same file.
`slicer ai skill --install` writes it to `.claude/skills/slicer/SKILL.md`,
`.agents/skills/slicer/SKILL.md` (Codex reads `.agents/skills/`, so there is no `.codex` copy),
and `.grok/skills/slicer/SKILL.md` under the project root; it never touches the home directory.
Use this instead of copying this repo's generic `skills/slicer/SKILL.md`: a project that
finishes with `handoff` needs the adapted text. `--output PATH` writes one file, creating
parent directories, and the two flags combine. The file holds exactly the bytes the command
prints, so `slicer ai skill | diff - PATH` is a drift check for a given slicer version and
finish mode. An existing destination with the same text is rewritten; one with different text
exits 2 with code `state` and nothing is written, unless `--force`. `--install` needs a
project, and a project whose config or index cannot be read is refused rather than given the
generic text. Without either flag nothing is written. `--json` returns `{"skill": "..."}`, plus
`written` (the absolute paths) when a write flag is given. The skill ends by pointing at
`slicer ai instructions --rest`, which prints only the parts of the guide the skill
leaves out, so an agent that loaded the skill does not read the same text twice.

slicer is built to be called by a coding agent rather than hand-edited. The agent runs
commands; slicer owns the files. Nothing an agent needs requires reading or writing
`.slicer/*.json` directly — and doing so is how state gets corrupted, because the index,
the slice files and the rendered markdown have to agree.

slicer is AI-friendly and tool-neutral: it runs locally, without an AI service or API
key. The agent reviews and implements; slicer manages the agreed work. Start with the
[worked workflows](getting-started.md#worked-workflows) for a new project or an
existing codebase, or the [contributor loop](../AGENTS.md#working-on-the-roadmap) when
changing slicer itself.

For automation, use JSON responses and inspect both the exit code and the payload.
The command and error contracts are described below the prompts.

Before handing off, read `slicer config handoff_requires_note_kind`. If nonempty,
file `slicer note ID --kind KIND --text "Describe the verified outcome"` during the
current implementation attempt. Project-aware instructions and skill output name
that required kind. Wrong-kind, previous-attempt, legacy unknown-attempt, and
history-only handoff notes cannot satisfy the policy. Missing reports fail with
code `state` before any batch writes. Release and resume preserve the association;
reject then restart requires a new report. Manually changing `attempts` changes
which reports match. Note read filters do not affect this check. `done` is unchanged.
`ai instructions --rest` stays generic and does not read project policy.

## Reusable prompts

Adapt these to your project's instructions. They describe separate stages so you can
review findings and agree on scope before importing or implementing work.

### Plan a new project

```text
Read the project instructions. Help me turn the following goal, constraints, and
acceptance criteria into a small roadmap: [describe them here]. Ask about missing
requirements. Break the work into bounded slices with concrete checks and explicit
dependencies. Propose importance, urgency, and effort (1-3) for every item, with a
one-line reason each. Detail the first slice; later items may remain roadmap rows. Identify
proposed files as proposed, rather than claiming they already exist. Present the plan
for discussion before importing or implementing it.
```

### Review an existing project

```text
Read the project instructions, relevant source, and tests. Read the roadmap with
`slicer goals`, `slicer list`, `slicer show`, and `slicer prose list`. Do not open
tracking JSON, the history file, or generated roadmap output. Review
the code for bugs, regressions, missing tests, and maintainability problems supported
by evidence. For each finding, cite file locations, explain the impact and a concrete
failure case or verification method, and distinguish confirmed behavior from open
questions. Identify findings already covered by roadmap items. Propose bounded
improvements with acceptance criteria and proposed importance, urgency, and effort
(1-3) with reasons; do not change code or import work yet.
```

### Convert an agreed roadmap

```text
Convert the accepted goals or review findings into a slicer markdown outline. Read
docs/import.md from the slicer documentation and obtain the target project's template
with `slicer import --skeleton`. Use unique ## item titles, supported metadata keys,
and ### slice sections. Preserve finding references, scope boundaries, acceptance
checks, and dependencies by exact item title. Set importance, urgency, and effort
(1-3) on every item, and size, tree, and findings where the project uses them; give a
one-line reason for each score in your response. Do not invent findings or file locations.
Save the outline as roadmap.md and run `slicer import roadmap.md --dry-run --json`.
Resolve validation errors and show me the outline and result before applying it.
```

If the target project has no `.slicer/` yet, initialize and configure it first as in
the getting-started guide. Once the outline is agreed, apply it with
`slicer import roadmap.md --render`, then run `slicer check`. Import is a one-time
transfer; subsequent edits use slicer commands. Keep references to review evidence in
the slices rather than relying on the temporary outline as the only record.

### Implement one slice

```text
Read the project instructions. Run `slicer next --ready --section "Implement" --section "Check" --json --lean` to get the next
item and only those sections. The headings are examples; pass the section names the project configures. Run `slicer sections` for this project's names. Read the scope, dependencies, relevant source, and tests. If
there is no slice or its acceptance criteria are ambiguous, resolve the specification
with me first. If `next` includes `unspecified`, fill each missing section with
`slicer edit ID --section NAME` before implementing that id. Do not invent the body.
Once the specification is clear and trusted, mark it started with
`slicer start ID --render --strict`. Implement that slice, and run its acceptance checks and
required project checks. Update affected documentation. Edit a slice with
`slicer edit ID --section NAME --text "Body" --render --strict`. Once verified, run
step 4 of `slicer ai instructions`. That is
`slicer done ID --note "Describe the verified outcome" --render` unless `implement_finish`
is `handoff`, in which case it is `slicer handoff ID --render`. Then run `slicer check`.
When step 4 was handoff, run done only after review and merge. Report changes and
checks, and stop after this slice.
Use the slicer CLI to read and to change goals, items, slices, notes, history, and
roadmap prose, including during review and planning. Do not open, search, parse, or
edit tracking JSON, the history file, or generated roadmap and slice output to obtain
or change that state. If a command is missing or fails, report it and propose a
roadmap item. Do not open or hand-merge generated roadmap files. When `ROADMAP.md` or
`ROADMAP.html` conflicts, run `slicer render` then `slicer check`. Do not commit or
publish unless separately authorized.
```

## Everything takes `--json`

Every subcommand except the interactive `tui`/`ui` accepts `--json` and writes a single JSON
document to stdout. Read commands return data; write commands return what they changed.

```console
$ slicer next --json
{
  "id": "S01",
  "title": "Parse the config file",
  "short_title": "Parse the config file",
  "status": "open",
  "has_slice": true,
  "depends_on": [],
  "notes": [],
  "note_records": [],
  "claim": null,
  "fields": {
    "size": "M",
    "flags": [],
    "trees": [
      "core"
    ],
    "trees_literal": false,
    "findings": "G1",
    "discovered_from": "",
    "pass": "",
    "group": "Phase 0 — groundwork",
    "reason": "",
    "importance": 2,
    "urgency": 2,
    "effort": null,
    "attempts": 0
  },
  "path": "~/code/my-project/.slicer/slices/S01.json",
  "effective_score": 22
}
```

Item `notes` remains a list of display strings. Additive `note_records` carries
`{id, kind, text, created_at, attempt, by, verified_by, verified_at, stale_after, trust}` in that key order (`trust` is derived, read-only, never stored); `text` is the complete dated
paragraph before adding the kind label. Historical strings retain their exact text,
get stable item-local positional IDs, empty `created_at`, and null `attempt` on reads.
The next index save persists those records under the current index schema (note records arrived in schema 4). New notes use stored UUIDs,
UTC ISO8601 timestamps, and the item's current attempt count.

Use `note ID --kind KIND --text TEXT` for a typed note; omitted kind is untyped.
`note_kinds` is an optional whitelist. Repeat `--notes-kind KIND` on `show` or
`next --ready` to select the union of exact kinds in stored order; `--notes-kind ''`
selects untyped notes and legacy slice strings. Filters never change state or hide
items, and work with `--lean`, `--section`, and ready batches. Ready item payloads
include `notes` and `note_records`; section-only show payloads add those fields and
`slice_notes` when a filter is supplied. Lean output omits empty matches as usual.
`next --notes-kind` without `--ready` returns `usage`.

For bounded pickup context, add `--min-trust TIER` and/or `--notes-budget N`
to `show` or `next --ready`, including batches. Tiers are `unverified`,
`machine-confirmed`, and `human-reviewed`; omitted trust means unverified and
omitted budget means unlimited. A budget must be a nonnegative integer. Either
option enables expiry filtering at one captured UTC read time; ordinary and
kind-only reads keep expired audit notes. Kinds compose by OR, then minimum trust
applies. Ranking is higher derived verification trust, newer known creation time,
then stored order. Authorship alone never raises trust.

Whole display strings share the character budget, including one newline between
selected strings; count Unicode code points, skip oversized notes, and continue.
Each batch item gets its own budget. Legacy slice notes are untyped/unverified and
use read-only IDs `slice-legacy-N` (one-based original order).

Requested packs always expose `note_pack` beside item `notes`/`note_records` (on
`item` for ready entries), even under lean and when empty:

```json
{"selected_ids": [], "omitted": [], "characters": 0, "budget": null, "min_trust": "unverified"}
```

`omitted` entries are `{id, reason}`; reason precedence is `kind`, `expired`,
`trust`, `budget`. Selected IDs follow ranking order; omissions follow original
item-note then slice-note order. The budget covers display text only, excluding
manifest/metadata/sections. Section/context projections expose the same manifest
and selected notes; legacy strings stay in the slice-note projection. Packing does
not hide items/sections, promote trust, save state, or change generated documents.
`next` packing without `--ready`, invalid trust, or a negative budget is `usage`
before any `--start` writes.

Filing identity is `fields.key`, a case-sensitive string (empty when absent).
`add --key KEY` trims surrounding whitespace and rejects explicit blank input
(`blank_key`). It returns the item plus `existing: false` on creation or
`existing: true` for a retry, even if replacement payload fields differ or are
invalid. Done and retired items reserve their keys. Retries leave state, IDs,
history, slices and renders untouched, even with `--render --strict`.
Keys belong to this checkout; no cross-branch or machine synchronization occurs.

Two shapes recur. An **item** is the object above minus `path` and `effective_score`; its soft fields are
nested under `fields`, the pass key is spelled `pass`, and `importance`/`urgency` (each
1–3) are the Eisenhower axes. `effort` is an optional `1`–`3` estimate, or `null` when unset; `--json --lean` omits the null. `attempts` counts fresh implementation starts from open or a custom queue into the configured started status. Resumes, review claims, rejection, and `set --status` do not increment it. Start → reject → restart records two attempts. Historical values are preserved and may overcount; history is not authoritative enough to reconstruct them. Owners may correct them with `set ID --attempts N` before adopting a cap. The index schema is 5, and `--json --lean` omits a 0. The combined score and quadrant are derived, not stored, so
they are not in the JSON — compute `importance*10 + urgency`, or read the ranking from
`list --sort score`. `--json` without `--lean` is always this full shape.

`--json --lean` omits empty strings, empty lists, empty objects, and nulls, plus
`trees_literal` when it is false, `short_title` when it equals `title`, `attempts` when it is 0, and `path` on an
item. A missing key means empty or that default, not unknown. `has_slice: false` and the
importance and urgency numbers stay, including the default 2. `item: null` stays, so an
empty `next` is still recognisable. `path` is only in the full profile. Text output
ignores `--lean`. `find` is the exception to "the same items, fewer empty fields": its lean rows are only `{id, title, status, has_slice, match}`. The `list --envelope` keys `items` and `only_in_sibling` stay present even when empty. Error envelopes stay full. Lean output is compact, one line with no indentation; full --json stays indented.

A **slice** is
`{id, title, lead[], depends_note, findings_note, size, flags[], trees_note,
trees_plural, sections: [{heading, body}], notes[]}`.

| Command | Payload |
|---|---|
| `ai instructions` | `{instructions}`. `--rest` returns only what the skill does not carry, the same text for every project. Bare `slicer ai` and `slicer ai --json` are this command. Generic Markdown, or the handoff loop when the project sets `implement_finish` to `handoff`; a nonempty `handoff_requires_note_kind` adds the required report kind and filing command. No project, or an unreadable config or index, is the generic text, a stderr warning, and exit 0. No lock or write |
| `ai skill` | `{skill}` containing the `SKILL.md` text, chosen the same way. The handoff skill's step 4 is only handoff, and that text does not contain the done command. With `--output` or `--install` it is `{skill, written}`, `written` being the absolute paths written, in order |
| `init` | `{root, dir}` |
| `setup-git` | array of the `git config` command strings; needs no project |
| `merge-index` | no payload and no `--json`; Git runs it as a merge driver with `%O %A %B`. Exit 0 wrote the merged index to `OURS`; exit 1 left conflict markers there |
| `list` | array of items by default; `--envelope` with `--json` returns `{items: [...], only_in_sibling: [...]}`, with both keys always present, including under `--lean`. `items` equals the default array for the same filters, sorts and lean profile. `only_in_sibling` matches the list in `status --json` (or `[]` when absent), independently of item filters and sorts. `--envelope` without `--json` exits 2 with code `usage`. Each item includes `claim`: `{"owner", "at"}` or null, plus derived `in_work_elsewhere`: an array of `{worktree, owner, status}` objects (`status` is the sibling's status key), or `[]`. A sibling counts when the item is claimed or in work, and when it is review or done while this checkout's status differs and is not done. The same status on both sides is omitted, as is an empty `review_status`. Text adds a CLAIM column (local owner, `*` for local in-progress with no claim, `wt:NAME` for sibling work, `wt:NAME+N` for multiple siblings, otherwise `-`), and, when the roadmap declares or names any pass, a PASS column after it (the pass key, or `-`); JSON carries the pass as `fields.pass` either way. Local claim or started state takes precedence in text; JSON still lists all siblings. A done item is never shown as locally claimed. Sibling detection uses only this machine's Git worktrees and their `.slicer/index.json` files, and only when this project is the git worktree root. Text ends with an `Only in a sibling` block when a sibling has an open, unclaimed item whose id this checkout lacks; the default JSON array does not carry it (use `list --json --envelope` or read `only_in_sibling` from `status --json`). Done and retired statuses are omitted unless `--all` is set or `--status` names them; `--all` together with `--status` is `usage`. `--in-work` keeps statuses in `config.in_work()` (started, plus reviewing when set), including a started item whose claim was released. `--review` keeps stored review items and stored-open items whose siblings report the configured review key; reviewing alone does not qualify. `--status` naming the review key includes the same rows. The open filter still includes those open items. Text shows this checkout's review label; JSON status stays open and `in_work_elsewhere` retains the sibling key. Either flag replaces the default omission of done and retired. Combining either with the other, with `--status`, or with `--all` is `usage`, as is an empty role. A role that matches nothing is `[]` in JSON and `no matching items` in text, exit 0, with no hidden-count line. Other filters and sorts still apply. An item that is in work only in a sibling worktree stays out of the in-work view; a sibling handoff adds an open item to the review view. Default order preserves readiness groups (unblocked in-work, unblocked open, other visible rows, then parked). Within each group, declared pass order precedes descending effective score, with stored queue order breaking ties. Empty passes merge by effective score; named undeclared passes follow declared passes. Without declared passes, score ordering is unchanged. `next` uses the same ranking for its eligible pool. Repeatable `--flag` keeps an item that has any named flag and combines with `--status`, `--tree`, and `--pass`. `--sort score` is that same set in flat score order. `--sort effort` orders `fields.effort` from 1 to 3 and puts null last, without writing state. `sort --by effort` persists that order |
| `show ID` | item, plus `slice` when it has one, plus `citations`: the catalog records named by `fields.cites`, in cite order, each `{id, kind, title, status, reason, successor}`. `--lean` drops an empty `citations` list. With one `--section NAME`, `{id, section, body}`; repeat `--section` to return only those sections; add `--context` for `{id, title, depends_on, boundary, sections}` |
| `sections` | `{sections, required}`. Text is one configured section per line, with `required` on a row whose heading is in `required`. Reads config only: no index, lock, or write. A missing `required_sections` key reports Implement and Check |
| `config` | with no key, the whole effective config (the same object `config.json` loads to, including `implement_finish`, `done_status` and `review_status`); with `KEY`, exactly `{key, value}`. A dotted key reaches into a nested object. An unknown key is `usage`, and the message names the top-level keys. Reads config only: no index, lock, or write. An unreadable config or a directory outside any project fails as `sections` does. `--lean` drops an empty `value` |
| `recommended-workflow` | `{"workflow": "..."}`, the text of `docs/generic-recommended-project-workflow.md`; text is the same document. It reads no project, so it prints the same thing everywhere, and it takes no lock and writes nothing. If the file cannot be read, exit 3 with code `io` naming the path |
| `next` | item plus `path` and `effective_score`. Text names `slicer show ID` when the item has a slice and omits the slice path unless `--path` is set; `--path` does not change this JSON, and `--lean` still drops `path` on an item. A row with no slice does not gain a show hint. The promote hint stays on `next --ready` and `next --show`. `next --show` text prints the slice without its generated banner, so it names no source path either; `show` and the files under `.slicer/render/slices/` keep the banner. With `--show`, also `slice` (the same object `show` returns, when the item has one); with `--ready`, `{item, slice, blocked}` where `item` is `{id, title, status, depends_on, attempts, notes, note_records, effective_score, path}` and `slice` is included when the item has one; or `{"item": null, "blocked": [...]}`. Repeat `--section` with `--ready` and that slice is `{boundary, notes, sections}` for those headings only. Omitting `--section` keeps the full slice. `--section` without `--ready` is `usage`. `unspecified` is present when a slice was skipped because a required section is empty (`required_sections`, or Implement and Check when that key is absent): `[{"id", "missing"}]`. Fill those with `slicer edit ID --section NAME`. `in_work_elsewhere` is present when an eligible item was skipped because a sibling Git worktree has it claimed or in work, or in review or done while this checkout's status differs and is not done: `[{"id", "worktree", "owner", "status"}]` (`status` is the sibling's status key), one row per worktree. The same status on both sides is not reported. An item also started or claimed in this checkout is not skipped. A started item is returned ahead of every open one. `--ready` and `--show` together are `usage`. `--status review` (the configured review status) uses the review queue with the same payload: reviewing items first, then review; `--start` then moves the item to reviewing. Any other `--status` must be a custom status (not a lifecycle role) or it is `usage`, with the choices named. A custom queue ranks your own claims first, then unclaimed items; `--start` claims in place (no status change, no attempt) and `--start-to KEY` also moves the item to a configured status other than done, retired, parked, review or reviewing, counting one attempt when that is the started status. `claimed_elsewhere` is present when an item was skipped because another owner claimed it: `[{"id", "owner"}]`. After `--start-to` changes the status, read-only `next --status` for the old queue no longer returns the item. `--batch K` returns `{"items": [...], "blocked": [...]}` from that same pool, optionally narrowed by one `--tree` and an exact `--size`. Each element is the plain `next` object; with `--ready`, each is `{item, slice}` (so `attempts` stays on `item`) and `blocked` stays on the batch. `--section` trims every slice the same way. A dependent is included after its dependency when that dependency is in the batch. Items still waiting on something outside the batch are `blocked`; items left out only because K was reached are not. `unspecified` and `in_work_elsewhere` appear only when non-empty, in the same shapes as `next`. An empty batch is exit 2 with `{"items": [], "blocked": [...]}` and no error object. `--start --render` (with `--strict` if wanted) renders in the same step and leaves the payload unchanged; `--render` without `--start` is `usage`. `--start` claims every returned id under one lock and passes `--owner` through; a failure claims none, and an empty batch does not start. Capped atomic selection locks before loading even when the resulting batch is empty. `--batch` with `--show`, `-n` (including `-n 0`), or `--status` is `usage`, as is K < 1. `--tree` and `--size` without `--batch` narrow a single `next` |
| `next-id` | `{"id": "S02"}` and nothing else; text is the bare id. Does not allocate, lock, log, or accept `--render`. When a sibling git worktree's `next_id` is higher, the id starts above it, as `add` and `import` do |
| `id-prefix` | with no argument, `{"prefix": "S"}`; with `NEW`, `{from, to, changed, next_id}`, plus `dry_run: true` under `--dry-run`. A prefix that is not the current one in another case is `usage`, and nothing is written |
| `add`, `set`, `start`, `done`, `park`, `unpark`, `release`, `handoff`, `reject` | the item. `reject` needs `--note` (the verdict), refuses (`state`, nothing written) an item that is not in review or reviewing, and refuses (`usage`) a `--to` that is not a configured status or is a lifecycle role; it clears `claim` and logs one `reject` entry carrying the verdict. `handoff` sets `review_status`, clears `claim`, and refuses (`state`, nothing written) an item that is not started or in review, or has no slice; one already in review with no claim is a no-op. `start` on a review item (or a reviewing one whose claim was released) sets `reviewing_status` instead of started, so `next` never returns it. `start` claims an unclaimed item (`claim.owner` and `claim.at`) and writes a non-blocking stderr warning when another branch or worktree name refers to the id (the current checkout does not count). The exit code is unchanged. `next`, including `next --start`, does not warn; `next --start` does claim. `release` clears `claim` and leaves status. `done` clears `claim`. `done --render --check` and `handoff --render --check` run `slicer check` after the change lands. Stdout stays the item. A failing check exits 1 and writes the findings to stderr, ending with "slicer: VERB landed, but check failed; run slicer check for the report". `--check` without `--render` is `usage` and writes nothing. `add` of an open or started item left at importance 2, urgency 2 and no effort writes a non-blocking stderr hint to score it; the payload and exit code are unchanged. `add` takes its id above any sibling git worktree's `next_id`, so two worktrees do not file different items under one id; an explicit `--id` is unchanged |
| `promote` | the slice (`--file`/`--stdin` fills its sections from a one-item outline) |
| `import` | `{items, created, reused, entries, promoted, by_status, ids, depends_edges, off_schema_sections, warnings, problems, preamble}`. `entries` contains `{title, id, existing}` for each requested title in input order; `ids` covers all entries and `items` is the input count. `created` and `reused` count new and reused records; `promoted` counts new slice files, `by_status` counts mapped records at their actual status, and `depends_edges` counts new edges only. An all-existing import writes nothing and bypasses optional rendering. `warnings` names new open or started items left unscored (importance 2, urgency 2, no effort). `preamble` is the leading prose, or null when the outline has none. Ids start above any sibling git worktree's `next_id`, in `--dry-run` and in the real run alike |
| `migrate` | a similar report, plus round-trip and reconciliation counts |
| `remove` | retire: the item plus `mode: "retire"`; purge: `{id, mode, id_freed, reason, file_removed}`; `--dry-run`: `{id, mode, dry_run, blockers, file, id_freed, reason}` and nothing is written |
| `move` | `{id, position}` |
| `sort` | `{by, moved}` |
| `edit` | `{id, section}`; boundary edits return `{id, boundary}` |
| `note` | `{id, added}` |
| `feedback` | append: `{path, entry}` with `at`, `kind`, `text`, and `item` only when given; print: `{path, entries}` (empty array when no log); `--out`: `{path, out}` |
| `feedback-report` | `{repo, dry_run, issues, unmarked}`; each issue has `entry` (sha256 of the entry's header and body), `kind`, `at`, `item` only when stored, and `title`, plus `body` under `--dry-run` or `issue` and `url` after `--yes`. `unmarked` lists issues filed for entries that changed while they were sent, which are not recorded and may be filed again. Both lists stay present under `--lean`. Nothing to file is exit 0 with empty lists |
| `issues-pull` | `{repo, dry_run, created, reused, items}`. `created` and `reused` are id lists; `created` is empty under `--dry-run`, which creates nothing. Each item has `number`, `title` (as stored for a reused row), `key` (`github:OWNER/REPO#NUMBER`), `id` (null for a new row under `--dry-run`), and `existing`. Both lists stay present under `--lean`. Nothing new is exit 0 |
| `note-verify`, `note-attest` | `{id, note_record, trust}`; repeating the same verifier is a no-op (no write, no render) |
| `find` | array of items with `match: {field, snippet}`; with `--lean`, each row is only `{id, title, status, has_slice, match}` |
| `deps` | unblocked-item array; for `deps ID`, `{id, waits_on, blocked_by, dependents}`; mermaid format returns `{format, graph}` |
| `goals` | `{goals, non_goals, records}`. `records` is `{goal, non_goal}`, active records unless `--retired`. Requirements, constraints, decisions, and assumptions are read with `slicer catalog`, not here |
| `catalog list` / `catalog show ID` | `list` is an array of records. `show` is one record plus `cited_by`, the citing item ids in queue order, including done and retired items. `cite` and `uncite` return the item; a cite that changes nothing sets `changed` false and writes nothing |
| `render` | `{written: [...]}` |
| `sync` | array of `{target, path, stale, detail}` |
| `check` | `{ok, stale_render, stale_render_details, orphan_render, stale_sync, problems, warnings}`. `stale_render` stays the sorted list of paths; `stale_render_details` has one `{file, cause, detail}` per path in the same order. `cause` is `missing`, `renderer_format`, `unknown_provenance` (an unstamped legacy banner or an unreadable format), or `content`; `renderer_format` also carries `rendered_format` and `running_format`, and means the renderer's layout differs, so state or templates may also have changed. Run `slicer render` for any of them. `warnings` also names an id that a sibling git worktree holds under a different title (it would collide on merge); it does not change `ok` or the exit code |
| `verify` | `{checked, git, errors, findings: [{level, item, message}]}` |
| `stats` | `{total, completion, by_status, by_size, by_tree, by_pass, by_tree_status}`. `by_status` and `by_tree_status` are keyed by status key (`open`, `done`, …), the same value as an item's `status`, never by its configured display label; text output shows the labels. `status --json` carries the same `census`, and `import`/`migrate` key their `by_status` the same way |
| `status` | `{next, census, blocked}`, plus `unspecified` and `in_work_elsewhere` when `next` would skip items, in the same shapes, and `only_in_sibling` (`[{id, title, worktree, status}]`, worktree-path then id order) when a sibling worktree has an open, unclaimed item whose id this checkout lacks. A done, review, claimed or in-work sibling row is not in it, and `check` does not fail on it. Text adds an `Only in a sibling` block |
| `log` | array of `{when, item, action, from, to, note}`, newest first. Start, claim, release, handoff and done entries add `by`: who acted, resolved as `--owner`, then `SLICER_CLAIM_OWNER`, then `claim_owner`, then the git user, then the worktree name. `--by NAME` keeps only those. Optional timezone-aware ISO8601 `--since` (inclusive) and `--until` (exclusive) compare instants before limit, preserving original `when` strings |
| `prose list` | `[{ref, lines, preview}]` |

Bound pickups with repeatable `next --flag PATTERN` and `--no-flag PATTERN`.
Patterns match stored flags with case-sensitive shell syntax (`*`, `?`, `[abc]`);
quote them, for example `next --no-flag 'risk-*' --batch 3 --ready --json`.
Any inclusion match keeps an item; any exclusion match removes it, even when
inclusion matches. Untagged items pass without inclusion and fail with it.
Empty patterns are `usage`. `list --flag` still uses literal names.

Filters apply before offsets, batch capacity and skip diagnostics, and combine
with tree/size, ready sections, attempt caps, review selection and a custom
status queue. Excluded items appear in neither candidates nor diagnostics
(`blocked`, `unspecified`, `capped`, `in_work_elsewhere`, `claimed_elsewhere`). Dependency checks and effective scores retain
the full index, so a filtered-out blocker still blocks its dependent, even in
a batch. Read and `--start` use the same pool; no filters preserve selection.

For unattended implementation, pass `next --max-attempts N` (positive integer).
Fresh pickups at or above N are excluded before offset or batch selection;
started attempts still resume, including after release, and review claims are
unaffected. Read-only selection and atomic `--start` use the same filter; capped
starts load and select while holding the writer lock. The limit applies only to
this call, not direct `start ID`.

Single, ready, show, and batch results add `capped` only when nonempty:
`[{"id": "S01", "attempts": 2, "max_attempts": 2}]`, in queue order.
This report is independent of dependency blockers. An excluded batch blocker
never counts as completed, so dependents stay blocked. If all candidates are
capped, the usual empty result and exit 2 remain, with no error object and no
item-state or history writes. Lean output omits an empty `capped` array.

## Every project under a directory (`-r`)

`list`, `check`, `status`, `stats` and `next` take `-r`/`--recursive`. It finds every project
at or below the start directory (`--root`, or the working directory; it never walks up) and runs
the command once in each, reading only. It takes no lock. `next -r` with `--start`, `--render`,
`--owner` or `--start-to` is `usage` and writes nothing. The walk skips dot-directories,
`node_modules` and symlinked directories, and keeps descending inside a project. Only directory
names below the start are skipped: an explicit start directory is walked even if it is itself a
dot-directory.

```console
$ slicer check -r --json --lean
{"projects":[{"path":".","exit":0,"result":{"ok":true,...}},{"path":"tools/api","exit":1,"result":{"ok":false,...}}]}
```

- `path` is POSIX and relative to the start directory (`.` for the start directory itself);
  projects come in sorted path order.
- `result` is exactly what the command prints without `-r`, including under `--lean`. A project
  that fails to load has `error` (`{"code", "message"}`) in place of `result`, and its `exit` is
  the code's usual exit (3 for `corrupt`, `locked`, `io`, `config`, `schema_too_new`; 2 otherwise).
  One failing project never stops the others.
- The process exit is the highest `exit` of any project. `next -r` returns one next item per
  project and never ranks across projects.
- Text output is a `== PATH ==` header and that project's normal text, with a blank line between
  projects. A project that fails prints `error: MESSAGE` under its header on stdout. Nothing
  per project goes to stderr, in text or JSON.
- A directory slicer cannot examine is reported, not skipped, because it may hold a project: one
  whose `.slicer/config.json` cannot be checked (for example an unreadable `.slicer/`), or that
  cannot be listed. It gets its own entry, in the same sorted order, with `"exit": 3` and
  `"error": {"code": "io", ...}`. No command runs there, and the other projects still run.
- No project at or below the start directory is exit 2 with the error envelope, code `no_project`.

## Failures are JSON too

With `--json`, a failure puts an envelope on **stdout** and still writes the human line
to stderr. An agent never has to parse prose:

```console
$ slicer show S99 --json
{
  "error": {
    "code": "no_such_item",
    "message": "no such item: S99",
    "command": "show"
  }
}
```

Argument-parser failures also return exit 2 and this envelope with `code="usage"`,
while retaining argparse’s usage text and diagnostic on stderr. This covers missing
arguments, unknown options, invalid values and conflicting options, including nested
commands. `command` is the recognized top-level command (`"prose"` for nested prose
commands), or `null` when none was recognized.

For parser failures, an exact `--json` token anywhere before the first `--` requests
the envelope, even if that flag’s position is invalid. This does not make the command
valid. Tokens after `--` are literal arguments. Help stays human-readable and exits
successfully, including with `--json`; successful command syntax is unchanged.

**`code` is the contract; `message` is not.** Messages get reworded; codes change only
when the meaning does. Branch on the code.

| Code | Means |
|---|---|
| `no_such_item` | No item with that id, from any command, including a `--depends-on` id |
| `no_slice` | The item exists but has not been promoted |
| `no_such_section` | `show --section` named a heading the slice does not have |
| `bad_promote_source` | A `promote` source is not one item, or names no sections |
| `field_in_promote_source` | A `promote` source set an item field; those belong on `add`/`set` |
| `already_exists` | `init` on an initialised project |
| `state` | The operation does not apply — unknown status, already promoted, a self, cycle, or retired dependency, and similar |
| `config` | `.slicer/config.json` is missing, malformed or self-contradictory |
| `outline` | An outline given to `import` could not be parsed |
| `legacy_format` | Legacy markdown `migrate` could not read or round-trip |
| `render` | A template named a placeholder that does not exist |
| `io` | A file could not be read or written |
| `locked` | Another slicer held the writer lock past the timeout; retry, or clear a stale `.slicer/lock` |
| `corrupt` | A tracking file (index, a slice, the log) is not valid JSON/UTF-8 or is the wrong shape |
| `schema_too_new` | `index.json` or `config.json` was written by a newer slicer; upgrade slicer to open the project |
| `bad_id` | An id is unusable as a filename, or a config prefix would produce one |
| `case_collision` | An id differs from an existing one only in case |
| `already_exists` | `init` or `migrate` over a project that already has state |
| `blank_title` | A title is empty or whitespace |
| `newline_in_field` | A one-line field (title, size, findings, tree, flag) contains a newline |
| `editor_aborted` | `$EDITOR` exited non-zero; nothing changed |
| `wrong_command` | e.g. `import --from` (that flag belongs to `migrate`) |
| `no_project` | `-r` found no `.slicer/config.json` at or below the start directory. Exit 2; `-r` never walks up, so run `slicer init` or start higher |
| `external` | An optional external tool failed: `feedback-report` or `issues-pull` found no `gh`, `gh` exited nonzero (often not authenticated), or it printed output slicer cannot use (no issue URL, or an issue list that is not valid). Exit 2. For `feedback-report`, earlier issues in the batch are already recorded and the message names the entry that stopped; `issues-pull` writes nothing |
| `usage` | Missing arguments, unknown options, invalid values, conflicting options, or other invocation errors |

## Exit codes

| Code | Meaning | What an agent should do |
|---|---|---|
| `0` | Fine | Continue |
| `1` | Drift, or a failed check | Read the payload; this is a report, not an error envelope |
| `2` | Usage, validation, or nothing to do | Read the envelope — **unless** the payload has no `error` key |
| `3` | Internal or state (`corrupt`, `locked`, `io`, `config`, `schema_too_new`) | The project or the process cannot proceed; read the envelope and stop |

Exit 3 is a hard failure. Exit 2 is usage, validation, or an empty queue, so status
alone separates them.

Exit 1 belongs to `check`, `verify`, `sync --check`, `import` and `migrate`. Inspect
each command's report: `check` has drift lists and `problems`; `verify` has `errors`
and `findings`; `sync --check` returns an array with `stale` on each target; import and
migration validation reports have `problems`. Do not assume
that every failed report has the same keys. An error envelope, when present, still
takes precedence over interpreting the payload as a normal report.

Exit 1 also covers a mutating command's `--render` step failing *after* the change was
saved: the change is committed but `render/` is now stale. stdout keeps the command's
single normal result (an object, or an array for a batch of ids) — never a second
document — and the render failure is reported on stderr. The exit is 1 whatever the
cause, because the mutation itself succeeded and only the projection is stale. Re-run
`slicer render` (after fixing the cause) to resolve it.

That save-then-render order is the default. Add `--strict` (with `--render`) to require
the render to succeed *first*: the mutation is rendered before it is allowed to land, and a
render failure rolls the whole change back — nothing is written, and nothing is printed to
stdout — exiting **2** with `{"error": {"code": "render"}}`. Use it when a change that
cannot be rendered must not land at all. `done --render` already behaves this way and does
not take `--strict`. The flag is offered on the other mutating commands, including `add`,
`set`, `move`, `sort`, `promote`, `edit`, `note`, `start`, `park`, `unpark`, the `prose`
edits, `import`, `migrate`, and `remove`. `--strict` without `--render` is a usage error,
including together with `--dry-run`. `--strict` guarantees only the render; it does not
re-run `check`. Dependency
integrity is enforced by `add` and `set` themselves, with or without `--strict`.

**One case to special-case:** `slicer next`, including `--show` and `--ready`, exits **2** when nothing is runnable, with
`{"item": null, "blocked": [...]}`. That is a normal empty queue, not a failure. Test for
the `error` key rather than assuming exit 2 means something went wrong.

## Getting a roadmap in

`slicer import FILE` reads a markdown outline — the bulk path, and the one an agent
should produce. Write the outline, dry-run it, apply it. See [import.md](import.md) for
the format.

```console
$ slicer import --skeleton > roadmap.md     # template, built from this project's config
$ slicer import roadmap.md --dry-run --json # validate; writes nothing
$ slicer import roadmap.md --json           # apply
$ slicer render --json
$ slicer check --json
```

Import validates everything before writing anything, so a rejected outline leaves the
project exactly as it was. `--dry-run` and the real run report the same census, which
means an agent can decide from the dry run alone.

## Rules for an agent working in a slicer project

**Re-prioritize in one step.** After changing scores with `set`, `slicer sort` reorders the
whole queue by priority score at once (persisting the `list --sort score` order) instead of a
`move` per item.

**Plan a batch with the dependency graph.** `slicer deps --json` lists every unblocked open
item (what you can start now); `slicer deps ID --json` gives what an item waits on, which of
those still block it, and what depends on it; `slicer deps --format mermaid` prints a
PR-reviewable graph. Reach for these instead of reconstructing the graph from `list --json`.

**Before adding, search for duplicates.** `slicer find "topic" --json` matches item ids,
titles, findings, and slice bodies (narrow with `--in title,findings,body`) and reports the
matched field and a snippet (`match` in JSON), so you can answer "is there already an item
about X?" — and see why each hit matched — before creating one. To dedupe on one known title,
`slicer find --title-exact "Full title" --json` returns only items whose stored title equals it
character for character (case and Unicode form matter; no PATTERN or `--in`).

**Create a dependent item in one call.** For example,
`slicer add "Implement the new loader" --short-title "New loader" --importance 3 --urgency 2 --effort 2 --depends-on S01 --render`.
Score what you file rather than leaving the 2/2 defaults, and say why in your reply.
`add --discovered-from ID` records an existing source item in
`fields.discovered_from`, with any status allowed. `list --discovered-from ID`
filters by exact source and combines with other filters; an unknown ID is
`no_such_item`, and zero matches succeeds. Outline input uses the single-value
`discovered_from: ID` key and refuses unknown or prospective sources before
writing the batch. Provenance neither blocks pickup nor changes scores.
Missing historical fields load empty; `--lean` omits empty provenance. Source
retirement keeps references valid; purge needs `--force` when referenced and
then integrity validation reports the dangling source. Index saves stamp schema 7 (provenance arrived in 5, filing keys in 6)
so older readers refuse rather than lose provenance.

Repeat `--depends-on` for multiple ids. `add` and `set` refuse, before writing anything,
an id that names no item (`no_such_item`, including a comma list passed as one value)
and a self-edge, a new cycle, or a dependency on a retired item unless `satisfies_dependencies` lists the retired status (`state`). A dangling edge
or cycle already in the index is left for `check` to report. `park` and `unpark` accept `--note` to record
why work is being deferred or resumed; read those notes with `slicer log --json`, and scope to
one item with `slicer log --item ID --json` (repeat `--item` for several) or to a kind of change
with `--action set` / `--action status`. `set` entries record old→new values, so
`slicer log --item ID --action set --json` reconstructs an item's metadata history from slicer
(section/prose body edits still live in git).

For a time window, pass `--since 2026-10-05T12:00:00Z` and/or
`--until 2026-10-05T13:00:00Z`. Explicit UTC offsets and fractional seconds work.
Item/action/by filters and the time window apply before `--limit`; output ordering
and append-order ties remain unchanged. Equal bounds or a window with no entries
return an empty array with exit 0. Invalid or timezone-less bounds and `since > until`
return `usage` (exit 2) before reading history. An invalid or timezone-less stored
timestamp encountered during time filtering returns `corrupt` (exit 3), naming the
history file and item. Timestamp windows cannot define an exact run boundary under
clock skew; history has no per-entry cursor.

**Read the project's direction before proposing work.** `slicer goals --json` returns
`{"goals": ..., "non_goals": ..., "records": {"goal": ..., "non_goal": ...}}` — the prose
plus goal and non-goal records, kept separate from the backlog. Requirements, constraints,
decisions, and assumptions are `slicer catalog list`. Judge new work against them, and if
the prose is empty or unclear, ask the owner to set it (`slicer prose edit goals` /
`non_goals`) rather than inferring direction from the queue. A citation on an item is a
reference (`slicer catalog cite`), not a dependency.

**Never edit `.slicer/*.json`.** Use the commands. The index, the slice files and
`.slicer/render/` have to agree, and `slicer check` is what proves they do.

**Preview a destructive remove.** `slicer remove ID --purge --dry-run --json` reports what would
be deleted, which dependents would dangle, and whether the id would be freed or stay burned —
without writing. Check it before a real `--purge` (`--dry-run` works for `--reason` retires too).

**Render after mutating, and check.** No command renders implicitly, but every mutating
command takes `--render` to do it in the same step (`slicer done S01 --render`). Otherwise
run `slicer render` then `slicer check`; a non-zero check means the work is not finished.

**After a rebase or merge, resolve `index.json`, then re-render.** `.slicer/log.jsonl`
union-merges on its own; `index.json` is the source of truth (resolve real overlaps by hand);
never hand-merge `render/` — run `slicer render` and `slicer check` so the generated markdown
matches the resolved index.

**Serialize integration into a shared checkout.** Only one actor should run `git merge`,
resolve conflicts, and finish the merge there at a time. S114's proposed slice claims
would coordinate authoring in separate worktrees; they do not reserve the integration
checkout. While Git has a merge in progress, slicer refuses state changes, including
TUI saves, with `merge_in_progress` (CLI exit 2). Read-only commands, dry runs,
`slicer render`, and `slicer sync` remain available to inspect and repair the merge.
Finish or abort the merge before changing slicer state again. The guard does not
serialize Git commands.

**`slicer next` is the queue.** It considers eligible started items first, then open
items if none qualify. Within each group it follows default `list` priority:
declared pass order, descending effective score, and stored queue-order ties.
Empty passes merge by effective score; named undeclared passes follow declared passes. All dependencies must be done, or in a status the
project lists in `satisfies_dependencies`. A
blocker of a critical item inherits that item's priority, so `next` naturally surfaces the
blocker first; dependencies still hard-gate, so a blocked item is never returned whatever
its score. `next` also reports the item's effective score (a `^` marks a score inherited from a
dependent) and status; `slicer next --start` returns the item and marks it started in one call.
Use `slicer next --ready --section "Implement" --section "Check" --json --lean` as the
pickup: the headings are examples, so pass the section names the project configures.
Run `slicer sections` for this project's names.
That returns the item and only those sections, instead of `next` then `show`.
`slicer next --ready --json` is the bounded form of that selection. `item` carries
`id`, `title`, `status`, `depends_on`, `effective_score`, and `path`. `slice` is the
same object `show` returns, and only when the item has one. Repeat `--section` with
`--ready` and the slice is only `boundary` plus those sections, in slice order, using
the same heading match as `show --section`. An unknown heading exits 2 with
`code="no_such_section"`. A row with no slice still omits `slice` and names
`slicer promote`. Omit `--section` and the full slice stays. `--section` without
`--ready` exits 2 with `code="usage"`. `--json --lean` applies to this payload.
The text form stays identity, a `slicer show ID` hint when the item has a slice, the boundary, and the headings. It does not print the slice path unless `--path` is set. `--path` is text only: JSON still includes `path`, and `--json --lean` still drops it on an item. A row with no slice keeps the promote hint on this text and does not gain a show hint. `blocked` is included on
every response, including when an item is returned. `unspecified` and
`in_work_elsewhere` use the same shapes as `next`. An empty or fully blocked queue exits 2 with `{"item": null, "blocked": [...]}`,
including when `--section` is set.
`--start` and `-n` still apply. `--ready` together with `--show` exits 2 with `code="usage"`.
Goals stay on `slicer goals`, the progress census stays on `slicer status`, and one
section stays on `slicer show ID --section`.
Read the slice and inspect its scope, dependencies, and acceptance checks; resolve any
ambiguity before starting it. Then run `slicer start ID --render --strict`, implement and verify
it, and finish with `slicer done ID --note "..." --render` followed by `slicer check`, the
default finish. If `implement_finish` is `handoff`, use the handoff command from
`slicer ai instructions` instead. If the
specification is already trusted and needs no clarification, `slicer next --start --ready --render --section "Implement" --section "Check" --json --lean` may combine fetching, reading, and starting the item.

**Read only the implementation sections.** Repeat `--section` to return just selected
sections, and add `--context` to include the item title, dependencies, and scope boundary:
`slicer show S01 --section "Failing tests" --section "Implement" --section "Check" --context --json`.
Those headings are examples; a project configures its own section names. Run
`slicer sections` for this project's names. This avoids
loading unrelated slice prose. The payload shapes stay in the command table above.

For edits, `slicer edit ID --section "Why" --stdin` replaces one section's body, and
`slicer show ID --section "Why"` reads that one body back. For short edits use
`slicer edit ID --section "Why" --text "Updated explanation" --render --strict`; roadmap prose
supports the same source, such as `slicer prose edit preamble --text "Current work"`.
In replacement mode, inline text is exact, including whitespace and newlines;
`--text ""` clears the body.
Choose only one of `--text`, `--file`, and `--stdin`; conflicting sources return exit 2
and `code="usage"` with `--json`. Omitting all sources opens the editor. File and stdin
sources retain their existing trailing-newline stripping. Quote text for the shell,
or pass it as one argument when invoking without a shell.

To leave a durable observation on an item — what you tried, why something was deferred —
`slicer note ID --text "..."` appends a dated note to the item (no slice needed; works on a bare
row), visible in `slicer show` and, once promoted, the rendered slice. This differs from
`done --note`, which records only to the log.

Item note records contain fixed-order `id`, `kind`, `text`, `created_at`, `attempt`,
`by`, `verified_by`, `verified_at`, and `stale_after`; empty metadata means absent.
`notes` remains the compatible display-string view. `note --owner ACTOR` uses the
existing actor precedence to record authorship without verification.
`--stale-after` takes timezone-aware ISO8601 and stores UTC; render does not
calculate freshness using the clock.

Read stable IDs with `show ID --json`. Use
`note-verify ID --note-id NOTE [--owner ACTOR]` for machine verification and
`note-attest ID --note-id NOTE --owner NAME` for owner attestation. Verification
without `--owner` uses the actor precedence; attestation always needs an explicit
nonempty name. Ordinary authorship (including actor fallbacks) and machine
verification reject the reserved `human:` prefix. Attestation stores `human:NAME` as
verifier and preserves authorship and text. The returned `trust` is derived:
`unverified`, `machine-confirmed`, or `human-reviewed`. Repeating the same
verifier is a no-op; a different verifier replaces it. Actual text edits clear
verification and log the previous verifier/time. Unknown note IDs fail with
`usage` before writes; legacy slice notes cannot be attested.

Attestation is a workflow convention, not caller authentication. An agent can
invoke the owner command; metadata alone does not establish an identity boundary.
New index saves stamp schema 7 so older readers refuse rather than erase metadata.

To add to a section:

```sh
slicer edit ID --section "Why" --append --text "New finding" --render --strict
```

Append requires exactly one of `--text`, `--file`, or `--stdin`; it never opens the editor. Trailing newline characters in the old body and
leading newline characters in the new body are removed, then nonempty bodies are joined
with two newlines. Other whitespace and source-reading rules are preserved. Empty
appended content changes neither the section nor the log; an empty or missing section
is filled without a leading separator. Prose editing does not support append.

The scope boundary is independent of section bodies. Use
`slicer edit ID --boundary --text "**Not in this slice:** other work" --render`
to replace its full paragraph; file, stdin and editor input also work. This target
cannot combine with `--section` or `--append`. Empty text clears it.
`show ID --json` exposes it as `slice.boundary`; boundary edits return
`{"id": "ID", "boundary": "full paragraph"}`. Promotion accepts `--boundary TEXT`
to override the source/default. Older JSON is converted in memory on load and the
field is persisted on the next slice save; read commands do not rewrite JSON.

To fill a whole slice at once, hand `promote`
a one-item outline: `slicer promote ID --file draft.md` — the same `##` item / `###`
section shape `import` reads, sections and lead only. A lead paragraph that starts
with the project's boundary marker is stored as the scope boundary; put it in the
lead or in one section, not both.

**Ids are never reused.** Adding an item claims its id for the life of the project.

**Writers serialise.** A mutating command holds an advisory lock (`.slicer/lock`) for its
run, so a fan-out of concurrent `slicer` processes cannot mint duplicate ids or half-apply
an outline. A second writer waits, then fails with `code="locked"` (exit 3) after a
timeout; set `SLICER_LOCK_TIMEOUT` (seconds) to tune it. Read-only commands never lock.

**slicer never commits.** `git` access is an allowlist of read-only subcommands, and a
mutation leaves the index untouched. Committing is the human's. A status change moves
the slice file in the worktree only. To record that move as a rename, add the old path
and the new path together. `git add -u` stages only the deletion.

## History and dependencies

Use `slicer log --item ID --json` to inspect an item's recorded changes. `set` history
entries include old-to-new field values; `--action set` narrows the log to metadata changes.
For a durable observation on any item, including one without a slice, use `slicer note ID`;
`done --note` records a history entry only. Use `slicer deps --json` for unblocked work,
`deps ID --json` for an item's edges, and `deps --format mermaid` for a graph.

## Batch mutations

`done`, `start`, `park`, `unpark`, and `set` accept multiple IDs, for example
`slicer set S01 S02 --urgency 3 --render`. Flags apply equally to all selected items.
Use `slicer done - --json` for whitespace-separated IDs on stdin; convert JSON query
output to IDs before piping it in. Do not mix `-` with positional IDs.

Batches deduplicate in input order and validate every ID and supplied field before
writing. Invalid requests change nothing; I/O failures have no multi-file rollback.
One explicit ID retains the JSON object response. Multiple positional IDs and stdin
return an array, including when only one unique ID remains. See
[batch changes](getting-started.md#batch-changes) for examples and history semantics.
