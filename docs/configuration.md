# Configuration

Everything project-specific lives in `.slicer/config.json`. Nothing is compiled into the
tool, which is what lets slicer work on a repository with no review protocol at all.

`slicer init` writes the defaults below. Edit the file directly — unlike `index.json`
and the slice files, the config is yours to hand-edit.

## The default file

```json
{
  "version": 1,
  "id": { "prefix": "S", "width": 2 },
  "statuses": { "open": "—", "done": "done", "parked": "parked", "later": "later" },
  "open_status": "open",
  "done_status": "done",
  "retired_status": "retired",
  "sections": ["Why", "Files", "Failing tests", "Implement", "Check", "Git"],
  "boundary": "**Not in this slice:**",
  "done_dir": "done",
  "retired_dir": "retired",
  "exclude_flags": [],
  "pointers": {
    "next_format": "**{{id}}** {{title}}",
    "next_empty": "nothing unmarked",
    "later": {
      "group_sep": "; ",
      "item_sep": "/",
      "groups": [
        { "status": "open",   "skip_first": true,  "prefix": "" },
        { "status": "parked", "skip_first": false, "prefix": "parked " },
        { "status": "later",  "skip_first": false, "prefix": "later " }
      ],
      "suffix": ""
    }
  },
  "sync": { "targets": [] }
}
```

## Every key

| Key | Default | What it affects | Safe to change later? |
|---|---|---|---|
| `id.prefix` | `"S"` | The id scheme | **Until the first item exists.** See below |
| `id.width` | `2` → `S01` | As above | **Until the first item exists** |
| `statuses` | see above | Maps status *key* → rendered *label*. Labels appear in the roadmap Status column, in `list`, and are what `migrate` parses back | Label: **yes**, re-render. Key: **no** |
| `open_status` | `"open"` | Which items `next` considers, what `unpark` returns to, the sync pointers | Only with migration |
| `done_status` | `"done"` | The `done` target, **which folder a slice lives in**, dependency satisfaction | **No — breaking** |
| `retired_status` | `"retired"` | The `remove --reason` target and the `retired/` folder | Yes if nothing is retired yet |
| `sections` | `Why, Files, Failing tests, Implement, Check, Git` | The headings `promote` seeds. Nothing validates existing slices against it | **Yes** |
| `boundary` | `"**Not in this slice:**"` | Seeded into the last section by `promote`; `check` warns per slice that lacks it | Yes, but noisy |
| `done_dir` | `"done"` | `.slicer/slices/done/`, and the subdirectory `migrate` reads finished slices from | **No — breaking** |
| `retired_dir` | `"retired"` | `.slicer/slices/retired/` | **No — breaking** |
| `exclude_flags` | `[]` | Flags that drop an item from the **sync pointers only**. Does not affect `next`, `list` or render | **Yes**, re-run `sync` |
| `pointers.next_format` | `"**{{id}}** {{title}}"` | The `{{next}}` substitution. `{{id}}` and `{{title}}` only | **Yes**, re-run `sync` |
| `pointers.next_empty` | `"nothing unmarked"` | `{{next}}` when nothing qualifies | **Yes** |
| `pointers.later` | see above | The `{{later}}` string | **Yes**, re-run `sync` |
| `sync.targets` | `[]` | Derived lines in documents slicer does not own | **Yes** |

## The ones that will surprise you

**`id.prefix` and `id.width` freeze once an id has been handed out.** Change them any
time before the first item exists and the next id uses the new scheme — `init`, look at
`S01`, decide you want `TASK-001`, edit the config, and the first `add` obeys it.

After that the index owns the scheme, because ids are never reused and renumbering would
break every commit message and review that cites one. Editing the config then does not
renumber anything, and `slicer verify` and `slicer check` report the disagreement naming
both schemes rather than letting it pass quietly. Restore the config, or start a new
project.

**`done_status`, `done_dir` and `retired_dir` strand existing files.** The folder a slice
lives in is derived from its status, so changing either side of that mapping leaves
finished slices sitting in a directory slicer no longer looks at. They become invisible
to `store.load`, and `verify` starts reporting `slice file is in the wrong folder for
status`. If you must, move the directory yourself with `git mv` in the same change.

**Two statuses may not share a label**, and `retired_dir` may not equal `done_dir`. Both
are rejected at load with a `ConfigError` naming the conflict.

**A missing `retired_status` is back-filled.** A project initialised before `remove`
existed has no retired status; exactly that one key is added on load, so a project that
deliberately dropped some *other* status does not get it back.

## Statuses

The key is what you type; the label is what renders.

```json
"statuses": { "open": "todo", "done": "shipped", "blocked": "blocked" },
"open_status": "open",
"done_status": "done"
```

`open_status`, `done_status` and `retired_status` must each name a key that exists in
`statuses`. Delete `parked` and `later` if you do not want them — but remove them from
`pointers.later.groups` too, or the pointer silently skips a group that can never match.

## Sections

`sections` is only the list `promote` seeds into a new slice, in order, with the last one
receiving `boundary` as its body. Existing slices are never touched, never validated, and
may carry headings that appear nowhere here — both `import` and `migrate` count those as "off-schema" and
keeps them exactly where they were.

Set `boundary` to `""` to turn off the "scope is unbounded" warning entirely.

## Sync targets

A project often repeats its queue in prose — a `Status:` line in a plan, a "Next:"
pointer in a README. Those are projections of the index, and `slicer sync` regenerates
them:

```json
"sync": {
  "targets": [
    {
      "name": "plan",
      "path": "docs/implementation-plan.md",
      "match": "^Status:.*$",
      "count": 1,
      "template": "Status: {{count}} items, {{done}} done. Next: {{next}}. Later: {{later}}."
    }
  ]
}
```

- `path` is relative to the project root.
- `match` is a regex applied with `re.M`.
- `count` is how many lines it **must** match. A mismatch is a hard `ConfigError` from
  both `sync` and `check` — not a warning. That is deliberate: a pattern that has
  silently stopped matching is worse than a loud failure.
- `template` may use `{{next}}`, `{{later}}`, `{{count}}`, `{{open}}` and `{{done}}`.

A target whose file does not exist is reported stale, not an error. `slicer sync --check`
reports drift without writing; `slicer check` includes it.

## Not configurable

The tracking directory name (`.slicer`) and the render output paths
(`render/ROADMAP.md`, `render/slices/<ID>.md`) are fixed.

The escape hatch is `.slicer/templates/*.md` — `slice.md`, `roadmap.md` and `row.md`,
copied into your project by `init` and yours to edit. They are plain `{{key}}`
substitution with no loops, no conditionals and no eval; an unknown placeholder raises
rather than rendering empty. Anything that repeats is flattened before it reaches a
template.
