# Driving slicer from an agent

slicer is built to be called by a coding agent rather than hand-edited. The agent runs
commands; slicer owns the files. Nothing an agent needs requires reading or writing
`.slicer/*.json` directly — and doing so is how state gets corrupted, because the index,
the slice files and the rendered markdown have to agree.

Three properties make this work: every command speaks JSON, every failure speaks JSON,
and every exit code means one thing.

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
| `usage` | The invocation is missing something it needs |

## Exit codes

| Code | Meaning | What an agent should do |
|---|---|---|
| `0` | Fine | Continue |
| `1` | Drift, or a failed check | Read the payload; this is a report, not an error envelope |
| `2` | Usage, or nothing to do | Read the envelope — **unless** the payload has no `error` key |

Exit 1 belongs to `check`, `verify`, `sync --check`, `import` and `migrate`. They
return their normal report with `problems` populated, not an error envelope, because the
command worked and the answer was "no".

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

**Never edit `.slicer/*.json`.** Use the commands. The index, the slice files and
`.slicer/render/` have to agree, and `slicer check` is what proves they do.

**Render after mutating, and check.** No command renders implicitly, but every mutating
command takes `--render` to do it in the same step (`slicer done S01 --render`). Otherwise
run `slicer render` then `slicer check`; a non-zero check means the work is not finished.

**`slicer next` is the queue.** It returns the highest-priority *startable* item — the
one with the highest effective score among items whose dependencies are all done. A
blocker of a critical item inherits that item's priority, so `next` naturally surfaces the
blocker first; dependencies still hard-gate, so a blocked item is never returned whatever
its score. Take it, do it, `slicer done ID --note "..."`.

**One section at a time.** `slicer edit ID --section "Why" --stdin` replaces one
section's body, and `slicer show ID --section "Why"` reads that one body back (no append
yet, so read before you mean to add to it). To fill a whole slice at once, hand `promote`
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

Worth knowing before you plan around it: there is no search command, no batch status
change (`done` takes one id), no way to append to a section, no per-item history beyond
status transitions, and no dependency query beyond `next` and `verify`. These are on the
roadmap — `slicer list --json` plus your own filtering is the workaround for most of them.
