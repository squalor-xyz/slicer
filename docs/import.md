# The outline format

`slicer import FILE` bulk-loads a roadmap from a plain markdown outline. It is the way
into an empty project: write the outline by hand, or have an agent write it, and one
command turns it into roadmap items and slices.

It is a **one-way ramp**. The outline is not a source of truth slicer keeps in step —
once the items exist, you manage them with `slicer set`, `slicer edit` and the TUI, and
the file is yours to delete.

Migrating a tree that is *already* in slicer's own legacy markdown format is a different
job, done by [`slicer migrate`](migrate-format.md).

All output below is real, with the project path shortened.

## Start from the skeleton

```console
$ slicer import --skeleton > roadmap.md
```

The skeleton is built from **your** `.slicer/config.json`, so the `###` headings it
suggests are the section list this project actually uses and the statuses it names are
the ones this project actually has.

## The shape

```markdown
# Config loader roadmap

## Parse the config file
size: M
tree: core
findings: G1
group: Phase 0 — groundwork

The loader accepts a missing key and carries on with a zero.

### Why
A typo in the config reads as a deliberate setting, and nothing says otherwise.

### Failing tests
1. `Config_MissingKey_Raises`

### Implement
Raise on a missing key, and name the key.

**Not in this slice:** the schema documentation.

## Fail loudly on a missing key
size: S
tree: core
depends: Parse the config file

## Document the config schema
size: S
tree: docs
status: parked
```

- A single `#` heading is a document title and is ignored, so a generated outline can
  carry one.
- `<!-- HTML comments -->` are stripped. The skeleton uses them for its guidance.
- **`##` opens an item.** The heading text is its title.
- **Key lines** come directly under the heading. The block ends at the first line that is
  not `key: value`.
- **Prose** after the keys and before the first `###` becomes the slice's lead — the
  paragraph that renders above the metadata line.
- **`###` opens a section** of the slice. An item with no `###` sections is a roadmap row
  and nothing more, exactly what `slicer add` produces.

## Keys

| Key | Meaning |
|---|---|
| `size` | Free text; `S`/`M`/`L` by convention |
| `tree` | Which part of the codebase. Comma-separated, or repeat the key |
| `findings` | A reference back to whatever raised this |
| `status` | Any status key your config defines. Defaults to open |
| `pass` | A pass group key, if the project uses passes |
| `group` | A phase label rendered above the item |
| `depends` | The **title** of another item, in this file or already in the roadmap |
| `importance` | `1`–`3`, how important (default 2); drives the priority score |
| `urgency` | `1`–`3`, how urgent (default 2); drives the priority score |

An unknown key is an error naming the key and the known set — a silently ignored key is
a roadmap item that quietly lost its size.

`depends` refers to titles rather than ids because ids do not exist until the outline is
applied. Both directions work: a title elsewhere in the same file, or the title of an
item already in the roadmap.

## Apply it

Dry-run first. Nothing is written and you see exactly what would land:

```console
$ slicer import roadmap.md --dry-run
source     ~/code/my-project/roadmap.md
outline    3 items, 1 with slices
status     — 2 · parked 1
depends    1 edges
nothing written; drop --dry-run to apply
```

Then for real:

```console
$ slicer import roadmap.md
source     ~/code/my-project/roadmap.md
outline    3 items, 1 with slices
status     — 2 · parked 1
depends    1 edges
added      S01, S02, S03
now run `slicer render`
```

```console
$ slicer render
rendered 2 file(s)
$ slicer check
check passed: 3 items, render and sync current
```

Import does not render by default: rendering is a separate step, and `check` tells you
when it is due. Pass `slicer import roadmap.md --render` to do both in one command.

## What import does for you

**Entries with sections get a slice file.** No separate `promote` call. The slice's
headings are your configured sections in your configured order, with the outline's
bodies filled in; any heading the outline names that your config does not is kept and
appended at the end.

**The scope boundary is added when you leave it out.** If no section body contains your
`boundary` marker, the bare marker seeds the slice’s separate boundary field, as
`promote` does. Otherwise the first matching paragraph moves out of its section into
that field. It renders after metadata and before sections, so later section edits
cannot erase it. Use `slicer edit ID --boundary --text "**Not in this slice:** other work"`
to change it.

**`status` puts the slice file in the right folder.** An entry marked `done` lands in
`.slicer/slices/done/`, so importing a roadmap that already has history does not leave
`verify` complaining.

**Dependencies resolve to ids.** `depends: Parse the config file` becomes
`depends_on: ["S01"]`.

**Passes are not inherited.** `slicer add` files a new item under the previous item's
pass; import does not, because an outline says where its own entries belong.

## When it refuses

Import is all-or-nothing. Everything is validated before a byte is written.

**A malformed outline** stops at exit 2, naming the line:

```console
$ slicer import roadmap.md
slicer: ~/code/my-project/roadmap.md:7: unknown key 'sizes'; known: size, tree, trees, findings, status, pass, group, depends, importance, urgency
```

**Anything the outline says that does not fit the project** is collected, reported
together, and refused at exit 1 with nothing written:

```console
$ slicer import roadmap.md
source     ~/code/my-project/roadmap.md
outline    3 items, 1 with slices
status     — 2 · parked 1
depends    1 edges
PROBLEM    'Parse the config file' already exists as S01; pass --force to add it anyway
PROBLEM    'Fail loudly on a missing key' already exists as S02; pass --force to add it anyway
PROBLEM    'Document the config schema' already exists as S03; pass --force to add it anyway
refusing to write: fix the problems above, or re-run with --dry-run to inspect
```

That is what running the same file twice looks like. Titles are matched against the
existing roadmap so a double-apply is caught rather than silently doubling your queue.
`--force` adds anyway, for the genuine case of two items that share a title.

The other problems in this tier: the same title twice in one outline, a `status` your
config does not define, and a `depends` that resolves to nothing.

## Driving it from an agent

Every command takes `--json`, including the failures. See [agents.md](agents.md).

```console
$ slicer import roadmap.md --json
{
  "items": 3,
  "promoted": 1,
  "by_status": {
    "—": 2,
    "parked": 1
  },
  "ids": [
    "S01",
    "S02",
    "S03"
  ],
  "depends_edges": 1,
  "off_schema_sections": {},
  "warnings": [],
  "problems": []
}
```

On a refusal the same shape comes back with `problems` populated and exit 1, so one
parser handles both.
