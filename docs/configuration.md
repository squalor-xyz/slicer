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
  "parked_status": "parked",
  "started_status": "started",
  "review_status": "review",
  "reviewing_status": "reviewing",
  "sections": ["Why", "Files", "Failing tests", "Implement", "Check", "Git"],
  "required_sections": ["Implement", "Check"],
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
  "sync": { "targets": [] },
  "git_check": true,
  "render_driver_check": true,
  "claim_owner": ""
}
```

## Every key

| Key | Default | What it affects | Safe to change later? |
|---|---|---|---|
| `id.prefix` | `"S"` | The id scheme | **Until the first item exists**; after that, only its case, with `slicer id-prefix`. See below |
| `id.width` | `2` → `S01` | As above | **Until the first item exists** |
| `statuses` | see above | Maps status *key* → rendered *label*. Labels appear in the roadmap Status column, in `list`, and are what `migrate` parses back | Label: **yes**, re-render. Key: **no** |
| `open_status` | `"open"` | Which items `next` considers, what `unpark` returns to, the sync pointers | Only with migration |
| `done_status` | `"done"` | The `done` target, **which folder a slice lives in**, dependency satisfaction | **No — breaking** |
| `retired_status` | `"retired"` | The `remove --reason` target and the `retired/` folder | Yes if nothing is retired yet |
| `sections` | `Why, Files, Failing tests, Implement, Check, Git` | The headings `promote` seeds. Nothing validates existing slices against it | **Yes** |
| `required_sections` | `Implement, Check` | Headings `next` skips a slice for when the body is empty. A missing key means that pair. An empty list skips nothing. Each name must be in `sections` | **Yes** |
| `boundary` | `"**Not in this slice:**"` | Seeds the separate boundary field; identifies inline boundaries in older input; `check` warns when the field is empty | Yes, but noisy |
| `done_dir` | `"done"` | `.slicer/slices/done/`, and the subdirectory `migrate` reads finished slices from | **No — breaking** |
| `retired_dir` | `"retired"` | `.slicer/slices/retired/` | **No — breaking** |
| `exclude_flags` | `[]` | Flags that drop an item from the **sync pointers only**. Does not affect `next`, `list` or render. To hold an item out of `next`, give it a custom status (see [Statuses](#statuses)) | **Yes**, re-run `sync` |
| `pointers.next_format` | `"**{{id}}** {{title}}"` | The `{{next}}` substitution. `{{id}}` and `{{title}}` only | **Yes**, re-run `sync` |
| `pointers.next_empty` | `"nothing unmarked"` | `{{next}}` when nothing qualifies | **Yes** |
| `pointers.later` | see above | The `{{later}}` string | **Yes**, re-run `sync` |
| `sync.targets` | `[]` | Derived lines in documents slicer does not own | **Yes** |
| `parked_status` | `"parked"` | Which status `park` sets, and what an item returns *from*. **Empty string = the project has no park state, and `park` refuses cleanly** | Yes if nothing is parked |
| `review_status` | `"review"` | Which status `handoff` sets: a started slice whose implementation is ready for someone else to review, merge, or clean up. `next` does not offer it (find it with `list --status review`), dependents stay blocked until `done`, and the slice file stays in `slices/`. **Empty string disables `handoff`** | Yes if nothing is in review |
| `reviewing_status` | `"reviewing"` | Which status `start` sets on a review item: a reviewer has claimed it. Like review, `next` does not offer it, so an implementer never resumes a review in progress; `list` ranks it with started work, and `done` finishes it. `handoff` refuses it. **Empty string makes `start` on a review item set started, as before** | Yes if nothing is being reviewed |
| `started_status` | `"started"` | Which status `start` sets. `next` returns a started item ahead of every open one. It gets no folder — the slice file stays in `slices/`. **Empty string = the project has no start state, and `start` refuses cleanly** | Yes if nothing is started |
| `git_check` | `true` | Whether `slicer verify` cross-checks item status against `git log`. Turn it **off** for a repo split from another, where items were finished before its history began and the check can never be satisfied | **Yes** |
| `render_driver_check` | `true` | Whether `slicer verify` reminds you to configure the `slicer-generated` render merge driver (via `slicer setup-git`). Only fires when this checkout has other worktrees, so a single-worktree clone is already quiet; set **off** to silence it entirely. There is no way to force it on for a single worktree | **Yes** |
| `claim_owner` | `""` | Who `start` writes onto a claim. Empty uses the git user name, then the worktree directory name. A per-call `--owner` or the `SLICER_CLAIM_OWNER` environment variable overrides it | **Yes** |

## The ones that will surprise you

**`id.prefix` and `id.width` freeze once an id has been handed out.** Change them any
time before the first item exists and the next id uses the new scheme — `init`, look at
`S01`, decide you want `TASK-001`, edit the config, and the first `add` obeys it.

After that the index owns the scheme, because ids are never reused and renumbering would
break every commit message and review that cites one. Editing the config then does not
renumber anything, and `slicer verify` and `slicer check` report the disagreement naming
both schemes rather than letting it pass quietly. Restore the config, or start a new
project.

The one change slicer supports later is the case of the prefix: `slicer id-prefix s`
switches new ids from `S160` to `s160`, updating the index and config together. Existing
ids keep their case and no file is renamed. Because the prefix is compared without case,
old and new ids stay one scheme and are never reused.

**`done_status`, `done_dir` and `retired_dir` strand existing files.** The folder a slice
lives in is derived from its status, so changing either side of that mapping leaves
finished slices sitting in a directory slicer no longer looks at. They become invisible
to `store.load`, and `verify` starts reporting `slice file is in the wrong folder for
status`. If you must, move the directory yourself with `git mv` in the same change.

**Two statuses may not share a label**, and `retired_dir` may not equal `done_dir`. Both
are rejected at load with a `ConfigError` naming the conflict.

**`parked_status` defaults only when the status exists.** If the key is absent, it
becomes `"parked"` when `parked` is in `statuses`, else empty — and `parked` is never
injected into `statuses`, so a project that dropped it keeps no park state rather than
having it resurrected. Set `parked_status: ""` to opt out explicitly; `park` then refuses
with a clear message.

**A missing `retired_status` is back-filled.** A project initialised before `remove`
existed has no retired status; exactly that one key is added on load, so a project that
deliberately dropped some *other* status does not get it back.

**A missing `started_status` is back-filled the same way,** for a project initialised
before `start` existed. Rename it and your name is kept; set it to `""` and the project
stays without one. Note that a started item is absent from the `{{later}}` pointer unless
you add a group for it to `pointers.later.groups` — the default groups list `open`,
`parked` and `later` only.

**A missing `review_status` is back-filled the same way,** for a project initialised before
`handoff` existed, with one exception: if another status already renders as `review`, no
review status is added (labels must stay unique) and `handoff` refuses until you set
`review_status` yourself. The review status must differ from the open, done, retired,
parked and started statuses.

**A missing `reviewing_status` is back-filled by the same rule as `review_status`,** and is
left empty when the project has no review status. It must differ from every other role,
review included.

## Statuses

The key is what you type; the label is what renders.

```json
"statuses": { "open": "todo", "done": "shipped", "blocked": "blocked" },
"open_status": "open",
"done_status": "done"
```

`open_status`, `done_status`, `retired_status`, `started_status`, `review_status` and
`reviewing_status` must each name a key that exists in `statuses` (the last four may
instead be `""`, switching the feature off). Delete `parked` and `later` if you do not want them — but remove them from
`pointers.later.groups` too, or the pointer silently skips a group that can never match.

Plain `next` offers only `open_status` and resumes `started_status`. Every other
status — parked, review, reviewing, and any key you add, such as `draft` or
`blocked` — stays out of that queue. A reviewer picks review work up with
`next --status review`. A custom status is how you hold an item back; `exclude_flags`
does not.

## Sections

`sections` is only the list `promote` seeds into a new slice, in order. `required_sections` is the subset `next` treats as unspecified while a body is empty. Leave the key out and that check stays Implement and Check. Set it to `[]` and an empty section no longer holds a slice out of `next`. A name that is not in `sections` is a config error. The boundary
is a separate slice field seeded from `boundary`. Existing slices are never touched, never validated, and
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
(`render/ROADMAP.md`, `render/ROADMAP.html`, `render/slices/<ID>.md`) are fixed.

The escape hatch is `.slicer/templates/*.md` — `slice.md`, `roadmap.md` and `row.md`,
copied into your project by `init` and yours to edit. They are plain `{{key}}`
substitution with no loops, no conditionals and no eval; an unknown placeholder raises
rather than rendering empty. Anything that repeats is flattened before it reaches a
template.

## Concurrency

A mutating command holds an advisory lock on `.slicer/lock` for the length of its run, so
concurrent `slicer` processes — a team, or an agent fan-out — serialise instead of racing
into a duplicated id or a half-applied outline. A second writer waits, then fails cleanly
after a timeout (default 5s; set the `SLICER_LOCK_TIMEOUT` environment variable, in
seconds, to change it). Read-only commands never lock. Where `flock` is unavailable the
lock degrades to a no-op. The lock file is not tracked (it is gitignored) and is safe to
delete when no slicer is running.
