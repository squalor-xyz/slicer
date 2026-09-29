# Driving slicer from an agent

Start with `slicer ai instructions` for the canonical concise onboarding guide.
It is available wherever slicer is installed and works without an initialized project;
it does not read project files or change state. `slicer ai instructions --json`
returns `{"instructions": "..."}` with the same Markdown as the text output.
`--root` is accepted but unused. This page provides the detailed reference and
reusable prompts; the command is the single source for the quick start.

`slicer ai skill` prints a `SKILL.md` for that implement loop and the exit-code
rules. Claude Code, Codex, and Grok load the same file. Copy or symlink
`skills/slicer/SKILL.md` to `.claude/skills/slicer/SKILL.md`,
`.agents/skills/slicer/SKILL.md` (Codex also reads `.codex/skills/`), or
`.grok/skills/slicer/SKILL.md` (Grok also reads the Claude and `.agents` paths).
`--json` returns `{"skill": "..."}`.

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

## Reusable prompts

Adapt these to your project's instructions. They describe separate stages so you can
review findings and agree on scope before importing or implementing work.

### Plan a new project

```text
Read the project instructions. Help me turn the following goal, constraints, and
acceptance criteria into a small roadmap: [describe them here]. Ask about missing
requirements. Break the work into bounded slices with concrete checks and explicit
dependencies. Detail the first slice; later items may remain roadmap rows. Identify
proposed files as proposed, rather than claiming they already exist. Present the plan
for discussion before importing or implementing it.
```

### Review an existing project

```text
Read the project instructions, relevant source, tests, and existing roadmap. Review
the code for bugs, regressions, missing tests, and maintainability problems supported
by evidence. For each finding, cite file locations, explain the impact and a concrete
failure case or verification method, and distinguish confirmed behavior from open
questions. Identify findings already covered by roadmap items. Propose bounded
improvements with acceptance criteria; do not change code or import work yet.
```

### Convert an agreed roadmap

```text
Convert the accepted goals or review findings into a slicer markdown outline. Read
docs/import.md from the slicer documentation and obtain the target project's template
with `slicer import --skeleton`. Use unique ## item titles, supported metadata keys,
and ### slice sections. Preserve finding references, scope boundaries, acceptance
checks, and dependencies by exact item title. Propose importance and urgency values
from 1 to 3 with reasons in your response. Do not invent findings or file locations.
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
item and only those sections. The headings are examples; pass the section names the project configures. Read the scope, dependencies, relevant source, and tests. If
there is no slice or its acceptance criteria are ambiguous, resolve the specification
with me first. If `next` includes `unspecified`, fill each missing section with
`slicer edit ID --section NAME` before implementing that id. Do not invent the body.
Once the specification is clear and trusted, mark it started with
`slicer start ID --render --strict`. Implement that slice, and run its acceptance checks and
required project checks. Update affected documentation. Edit a slice with
`slicer edit ID --section NAME --text "Body" --render --strict`. Once verified, run
`slicer done ID --note "Describe the verified result"
--render` and `slicer check`. Report changes and checks, and stop after this slice.
Use commands to change tracking state; never hand-edit the index, slice JSON, or
generated markdown. Do not open or hand-merge `.slicer/render/`. When `ROADMAP.md` or `ROADMAP.html` conflicts, run `slicer render` then `slicer check`. Do not commit or publish unless separately authorized.
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
  "fields": {
    "size": "M",
    "flags": [],
    "trees": [
      "core"
    ],
    "trees_literal": false,
    "findings": "G1",
    "pass": "",
    "group": "Phase 0 — groundwork",
    "reason": "",
    "importance": 2,
    "urgency": 2,
    "effort": null
  },
  "path": "~/code/my-project/.slicer/slices/S01.json"
}
```

Two shapes recur. An **item** is the object above minus `path`; its soft fields are
nested under `fields`, the pass key is spelled `pass`, and `importance`/`urgency` (each
1–3) are the Eisenhower axes. `effort` is an optional `1`–`3` estimate, or `null` when unset; `--json --lean` omits the null. The combined score and quadrant are derived, not stored, so
they are not in the JSON — compute `importance*10 + urgency`, or read the ranking from
`list --sort score`. `--json` without `--lean` is always this full shape.

`--json --lean` omits empty strings, empty lists, empty objects, and nulls, plus
`trees_literal` when it is false, `short_title` when it equals `title`, and `path` on an
item. A missing key means empty or that default, not unknown. `has_slice: false` and the
importance and urgency numbers stay, including the default 2. `item: null` stays, so an
empty `next` is still recognisable. `path` is only in the full profile. Text output
ignores `--lean`. Error envelopes stay full.

A **slice** is
`{id, title, lead[], depends_note, findings_note, size, flags[], trees_note,
trees_plural, sections: [{heading, body}], notes[]}`.

| Command | Payload |
|---|---|
| `ai instructions` | `{instructions}` containing the generic Markdown quick start |
| `list` | array of items. Each item includes `claim`: `{"owner", "at"}` or null. Text adds a CLAIM column (owner, `*` for in-progress with no claim, otherwise `-`). A done item is never shown as claimed. Done and retired statuses are omitted unless `--all` is set or `--status` names them; `--all` together with `--status` is `usage`. Default order is the `next` sequence (unblocked started, then unblocked open, by effective score), then the other visible rows by effective score. Repeatable `--flag` keeps an item that has any named flag and combines with `--status`, `--tree`, and `--pass`. `--sort score` is that same set in flat score order. `--sort effort` orders `fields.effort` from 1 to 3 and puts null last, without writing state. `sort --by effort` persists that order |
| `show ID` | item, plus `slice` when it has one; with one `--section NAME`, `{id, section, body}`; repeat `--section` to return only those sections; add `--context` for `{id, title, depends_on, boundary, sections}` |
| `next` | item plus `path` and `effective_score`; with `--show`, also `slice` (the same object `show` returns, when the item has one); with `--ready`, `{item, slice, blocked}` where `item` is `{id, title, status, depends_on, effective_score, path}` and `slice` is included when the item has one; or `{"item": null, "blocked": [...]}`. Repeat `--section` with `--ready` and that slice is `{boundary, sections}` for those headings only. Omitting `--section` keeps the full slice. `--section` without `--ready` is `usage`. `unspecified` is present when a slice was skipped because Implement or Check is empty: `[{"id", "missing"}]`. Fill those with `slicer edit ID --section NAME`. A started item is returned ahead of every open one. `--ready` and `--show` together are `usage` |
| `next-id` | `{"id": "S02"}` and nothing else; text is the bare id. Does not allocate, lock, log, or accept `--render` |
| `add`, `set`, `start`, `done`, `park`, `unpark`, `release` | the item. `start` claims an unclaimed item (`claim.owner` and `claim.at`) and writes a non-blocking stderr warning when another branch or worktree name refers to the id (the current checkout does not count). The exit code is unchanged. `next`, including `next --start`, does not warn; `next --start` does claim. `release` clears `claim` and leaves status. `done` clears `claim` |
| `promote` | the slice (`--file`/`--stdin` fills its sections from a one-item outline) |
| `import` | `{items, promoted, by_status, ids, depends_edges, off_schema_sections, warnings, problems, preamble}`. `preamble` is the leading prose, or null when the outline has none |
| `migrate` | a similar report, plus round-trip and reconciliation counts |
| `move` | `{id, position}` |
| `sort` | `{by, moved}` |
| `edit` | `{id, section}`; boundary edits return `{id, boundary}` |
| `note` | `{id, added}` |
| `find` | array of items with `match: {field, snippet}` |
| `deps` | unblocked-item array; for `deps ID`, `{id, waits_on, blocked_by, dependents}`; mermaid format returns `{format, graph}` |
| `goals` | `{goals, non_goals}` |
| `render` | `{written: [...]}` |
| `sync` | array of `{target, path, stale, detail}` |
| `check` | `{ok, stale_render, orphan_render, stale_sync, problems, warnings}` |
| `verify` | `{checked, git, errors, findings: [{level, item, message}]}` |
| `stats` | `{total, completion, by_status, by_size, by_tree, by_pass, by_tree_status}` |
| `status` | `{next, census, blocked}` |
| `log` | array of `{when, item, action, from, to, note}`, newest first |
| `prose list` | `[{ref, lines, preview}]` |

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
| `no_such_item` | No item with that id, from any command |
| `no_slice` | The item exists but has not been promoted |
| `no_such_section` | `show --section` named a heading the slice does not have |
| `bad_promote_source` | A `promote` source is not one item, or names no sections |
| `field_in_promote_source` | A `promote` source set an item field; those belong on `add`/`set` |
| `already_exists` | `init` on an initialised project |
| `state` | The operation does not apply — unknown status, already promoted, and similar |
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
cannot be rendered must not land at all. `done --render` already behaves this way; `--strict`
extends the same render-first policy to the everyday item mutations (`add`, `set`, `move`,
`sort`, `promote`, `edit`, `note`, `start`, `park`, `unpark`, and the `prose` edits). It is
not offered on `import`, `migrate`, or `remove`, and `--strict` without `--render` is a usage
error.

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
about X?" — and see why each hit matched — before creating one.

**Create a dependent item in one call.** For example,
`slicer add "Implement the new loader" --short-title "New loader" --depends-on S01 --render`.
Repeat `--depends-on` for multiple ids. `park` and `unpark` accept `--note` to record
why work is being deferred or resumed; read those notes with `slicer log --json`, and scope to
one item with `slicer log --item ID --json` (repeat `--item` for several) or to a kind of change
with `--action set` / `--action status`. `set` entries record old→new values, so
`slicer log --item ID --action set --json` reconstructs an item's metadata history from slicer
(section/prose body edits still live in git).

**Read the project's direction before proposing work.** `slicer goals --json` returns
`{"goals": ..., "non_goals": ...}` — what the project is *for*, kept separate from the
backlog. Judge new work against it, and if it is empty or unclear, ask the owner to set it
(`slicer prose edit goals` / `non_goals`) rather than inferring direction from the queue.

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

**`slicer next` is the queue.** It considers eligible started items first, then open
items if none qualify. Within that pool it selects the highest effective score, with
stored queue order breaking ties. All dependencies must be done. A
blocker of a critical item inherits that item's priority, so `next` naturally surfaces the
blocker first; dependencies still hard-gate, so a blocked item is never returned whatever
its score. `next` also reports the item's effective score (a `^` marks a score inherited from a
dependent) and status; `slicer next --start` returns the item and marks it started in one call.
Use `slicer next --ready --section "Implement" --section "Check" --json --lean` as the
pickup: the headings are examples, so pass the section names the project configures.
That returns the item and only those sections, instead of `next` then `show`.
`slicer next --ready --json` is the bounded form of that selection. `item` carries
`id`, `title`, `status`, `depends_on`, `effective_score`, and `path`. `slice` is the
same object `show` returns, and only when the item has one. Repeat `--section` with
`--ready` and the slice is only `boundary` plus those sections, in slice order, using
the same heading match as `show --section`. An unknown heading exits 2 with
`code="no_such_section"`. A row with no slice still omits `slice` and names
`slicer promote`. Omit `--section` and the full slice stays. `--section` without
`--ready` exits 2 with `code="usage"`. `--json --lean` applies to this payload.
The text form stays identity, boundary, and headings. `blocked` is included on
every response, including when an item is returned. `unspecified` uses the same shape
as `next`. An empty or fully blocked queue exits 2 with `{"item": null, "blocked": [...]}`,
including when `--section` is set.
`--start` and `-n` still apply. `--ready` together with `--show` exits 2 with `code="usage"`.
Goals stay on `slicer goals`, the progress census stays on `slicer status`, and one
section stays on `slicer show ID --section`.
Read the slice and inspect its scope, dependencies, and acceptance checks; resolve any
ambiguity before starting it. Then run `slicer start ID --render --strict`, implement and verify
it, and use `slicer done ID --note "..." --render` followed by `slicer check`. If the
specification is already trusted and needs no clarification, `slicer next --start --ready --section "Implement" --section "Check" --json --lean` may combine fetching, reading, and starting the item.

**Read only the implementation sections.** Repeat `--section` to return just selected
sections, and add `--context` to include the item title, dependencies, and scope boundary:
`slicer show S01 --section "Failing tests" --section "Implement" --section "Check" --context --json`.
Those headings are examples; a project configures its own section names. This avoids
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
section shape `import` reads, sections and lead only.

**Ids are never reused.** Adding an item claims its id for the life of the project.

**Writers serialise.** A mutating command holds an advisory lock (`.slicer/lock`) for its
run, so a fan-out of concurrent `slicer` processes cannot mint duplicate ids or half-apply
an outline. A second writer waits, then fails with `code="locked"` (exit 3) after a
timeout; set `SLICER_LOCK_TIMEOUT` (seconds) to tune it. Read-only commands never lock.

**slicer never commits.** `git` access is allowlisted to read-only subcommands plus `mv`.
Committing is the human's.

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
