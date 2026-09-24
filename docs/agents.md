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
    "reason": ""
  },
  "path": "~/code/my-project/.slicer/slices/S01.json"
}
```

Two shapes recur. An **item** is the object above minus `path`; its soft fields are
nested under `fields`, and the pass key is spelled `pass`. A **slice** is
`{id, title, lead[], depends_note, findings_note, size, flags[], trees_note,
trees_plural, sections: [{heading, body}], notes[]}`.

| Command | Payload |
|---|---|
| `list` | array of items |
| `show ID` | item, plus `slice` when it has one |
| `next` | item plus `path`; or `{"item": null, "blocked": [...]}` |
| `add`, `set`, `done`, `park`, `unpark` | the item |
| `promote` | the slice |
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
| `already_exists` | `init` on an initialised project |
| `state` | The operation does not apply — unknown status, already promoted, and similar |
| `config` | `.slicer/config.json` is missing, malformed or self-contradictory |
| `outline` | An outline given to `import` could not be parsed |
| `legacy_format` | Legacy markdown `migrate` could not read or round-trip |
| `render` | A template named a placeholder that does not exist |
| `io` | A file could not be read or written |

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

**Render after mutating, and check.** Nothing renders implicitly. `slicer render` then
`slicer check`; a non-zero check means the work is not finished.

**`slicer next` is the queue.** It returns the first open item whose dependencies are all
done. Take it, do it, `slicer done ID --note "..."`.

**One section at a time.** `slicer edit ID --section "Why" --stdin` replaces one
section's body. There is no append; read the current body with `slicer show ID --json`
first if you mean to add to it.

**Ids are never reused.** Adding an item claims its id for the life of the project.

**slicer never commits.** `git` access is allowlisted to read-only subcommands plus `mv`.
Committing is the human's.

## What is not here yet

Worth knowing before you plan around it: there is no search command, no batch status
change (`done` takes one id), no way to append to a section, no per-item history beyond
status transitions, and no dependency query beyond `next` and `verify`. These are on the
roadmap — `slicer list --json` plus your own filtering is the workaround for most of them.
