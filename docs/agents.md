# Driving slicer from an agent

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
Read the project instructions. Run `slicer next --json` and `slicer show ID --json`
using the returned id. Read the scope, dependencies, relevant source, and tests. If
there is no slice or its acceptance criteria are ambiguous, resolve the specification
with me first. Otherwise mark it started with `slicer start ID --render`, implement
that slice, and run its acceptance checks and required project checks. Update affected
documentation. Once verified, run `slicer done ID --note "Describe the verified result"
--render` and `slicer check`. Report changes and checks, and stop after this slice.
Use commands to change tracking state; never hand-edit the index, slice JSON, or
generated markdown. Do not commit or publish unless separately authorized.
```

## Everything takes `--json`

Every subcommand except `tui` accepts `--json` and writes a single JSON document to
stdout. Read commands return data; write commands return what they changed.

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
    "urgency": 2
  },
  "path": "~/code/my-project/.slicer/slices/S01.json"
}
```

Two shapes recur. An **item** is the object above minus `path`; its soft fields are
nested under `fields`, the pass key is spelled `pass`, and `importance`/`urgency` (each
1–3) are the Eisenhower axes. The combined score and quadrant are derived, not stored, so
they are not in the JSON — compute `importance*10 + urgency`, or read the ranking from
`list --sort score`. A **slice** is
`{id, title, lead[], depends_note, findings_note, size, flags[], trees_note,
trees_plural, sections: [{heading, body}], notes[]}`.

| Command | Payload |
|---|---|
| `list` | array of items |
| `show ID` | item, plus `slice` when it has one; with `--section NAME`, `{id, section, body}` |
| `next` | item plus `path`; or `{"item": null, "blocked": [...]}`. A started item is returned ahead of every open one |
| `add`, `set`, `start`, `done`, `park`, `unpark` | the item |
| `promote` | the slice (`--file`/`--stdin` fills its sections from a one-item outline) |
| `import` | `{items, promoted, by_status, ids, depends_edges, off_schema_sections, warnings, problems}` |
| `migrate` | a similar report, plus round-trip and reconciliation counts |
| `move` | `{id, position}` |
| `edit` | `{id, section}` |
| `render` | `{written: [...]}` |
| `check` | `{ok, stale_render, orphan_render, stale_sync, problems, warnings}` |
| `verify` | `{checked, git, errors, findings: [{level, item, message}]}` |
| `stats` | `{total, by_status, by_size, by_tree, by_pass}` |
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
| `2` | Usage, or nothing to do | Read the envelope — **unless** the payload has no `error` key |

Exit 1 belongs to `check`, `verify`, `sync --check`, `import` and `migrate`. Inspect
each command's report: `check` has drift lists and `problems`; `verify` has `errors`
and `findings`; `sync --check` returns an array with `stale` on each target; import and
migration validation reports have `problems`. Do not assume
that every failed report has the same keys. An error envelope, when present, still
takes precedence over interpreting the payload as a normal report.

**One case to special-case:** `slicer next` exits **2** when nothing is runnable, with
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

**Create a dependent item in one call.** For example,
`slicer add "Implement the new loader" --short-title "New loader" --depends-on S01 --render`.
Repeat `--depends-on` for multiple ids. `park` and `unpark` accept `--note` to record
why work is being deferred or resumed; read those notes with `slicer log --json`.

**Never edit `.slicer/*.json`.** Use the commands. The index, the slice files and
`.slicer/render/` have to agree, and `slicer check` is what proves they do.

**Render after mutating, and check.** No command renders implicitly, but every mutating
command takes `--render` to do it in the same step (`slicer done S01 --render`). Otherwise
run `slicer render` then `slicer check`; a non-zero check means the work is not finished.

**`slicer next` is the queue.** It considers eligible started items first, then open
items if none qualify. Within that pool it selects the highest effective score, with
stored queue order breaking ties. All dependencies must be done. A
blocker of a critical item inherits that item's priority, so `next` naturally surfaces the
blocker first; dependencies still hard-gate, so a blocked item is never returned whatever
its score. Read the slice, mark it started, implement and verify it, then use
`slicer done ID --note "..." --render` and `slicer check`.

**One section at a time.** `slicer edit ID --section "Why" --stdin` replaces one
section's body, and `slicer show ID --section "Why"` reads that one body back. For short edits use
`slicer edit ID --section "Why" --text "Updated explanation" --render`; roadmap prose
supports the same source, such as `slicer prose edit preamble --text "Current work"`.
In replacement mode, inline text is exact, including whitespace and newlines;
`--text ""` clears the body.
Choose only one of `--text`, `--file`, and `--stdin`; conflicting sources return exit 2
and `code="usage"` with `--json`. Omitting all sources opens the editor. File and stdin
sources retain their existing trailing-newline stripping. Quote text for the shell,
or pass it as one argument when invoking without a shell.

To add to a section:

```sh
slicer edit ID --section "Why" --append --text "New finding" --render
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
an outline. A second writer waits, then fails with `code="locked"` (exit 2) after a
timeout; set `SLICER_LOCK_TIMEOUT` (seconds) to tune it. Read-only commands never lock.

**slicer never commits.** `git` access is allowlisted to read-only subcommands plus `mv`.
Committing is the human's.

## What is not here yet

Worth knowing before you plan around it: there is no search command, no per-item history beyond
status transitions, and no dependency query beyond `next` and `verify`. These are on the
roadmap — `slicer list --json` plus your own filtering is the workaround for most of them.

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
