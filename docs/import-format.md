# The format `slicer import` reads

`slicer import --from DIR` migrates a markdown tree that is **already in slicer's own
legacy format**. It is not a general importer: there is no JSON, CSV or plain-list
ingest. If your roadmap is not the shape below, load it with a loop over `slicer add`
instead — see [getting-started.md](getting-started.md#4b-from-a-plain-list).

Check your tree against this page before you start. Import is all-or-nothing: every file
must parse *and* re-emit to exactly the bytes it came from, or nothing is written.

## Layout

```
DIR/
  README.md                 the index — this exact name
  S02-some-slug.md          open, parked, later slices
  done/S01-some-slug.md     finished slices
```

Only those two levels are scanned — `DIR/*.md` and `DIR/<done_dir>/*.md`. Not recursive.
`done` is `done_dir` from your config. The index filename is hardcoded; there is no flag
to point at a different one. File names are free: the id comes from inside the file, not
from the name.

## The index

Tables are found by **two exact consecutive lines**:

```
| # | Slice | Size | Trees | Findings | Status |
|---|---|---|---|---|---|
```

Byte-exact, including the single spaces. Anything else is treated as prose and passes
through untouched. Every following line starting with `|` is a row and must split into
**exactly six cells**.

A row is an **item row** when its first cell is exactly a markdown link, `[S01](path.md)`
and nothing else. Anything else in that cell makes it a **group label row** — the
convention is `| **Phase 0 — groundwork** | | | | | |`, and the label is carried onto
every item beneath it.

Item row cells, in order:

| # | Cell | Rules |
|---|---|---|
| 1 | id link | `[ID](relative/path.md)`, exactly |
| 2 | short title | free text; the roadmap shows this, the slice file's H1 is kept separately |
| 3 | size | uppercase letters, plus optional `` `[FLAG]` `` tokens — `` M `[OWNER]` `` |
| 4 | trees | comma-separated, **unless** it starts with `all` (e.g. `all four`), which is kept as one literal |
| 5 | findings | free text |
| 6 | status | must be a configured status **label** — `—`, `done`, `parked`, `later` by default |

Prose between tables is preserved verbatim and carved into addressable blocks. A pass key
comes from the H1 heading above each table, matched as `pass <key>`:
`# Slice index — review pass 2 (2026-02-09)` gives key `2`. No match, and the key is the
table's ordinal. Text after the last table becomes the epilogue, starting at its first
`##` heading.

## Slice files

```markdown
# S02 — fail loudly on a missing key

**Findings:** F2 · **Size: S** · **Tree:** core
**Depends on:** S01 (the parser has to exist first)

## Why

A typo reads as a deliberate zero.
```

1. **Line 1** is `# <id> — <title>`. That separator is an **em dash, U+2014**, with one
   space each side. A hyphen will not do.
2. The file ends with a newline, and contains no CR anywhere — LF only.
3. Between the H1 and the first `##` heading, blank-line-separated blocks are collected.
   **Exactly one** must start with `**Findings:**`; zero or two is an error. Blocks
   before it are kept as lead (that is where `**Parked** (owner …)` notes live), blocks
   after it as notes.
4. **The meta line**, first line of that block:

   ```
   **Findings:** <text> · **Size: <SIZE>**[ `[FLAG]`…] · **Tree:** <trees>
   ```

   - The separators are **middots, U+00B7**.
   - `<SIZE>` is uppercase letters only.
   - The key is `Tree` **or** `Trees`; both are accepted and which you used is preserved.
   - `<text>` may itself contain a middot — the grammar anchors on the bold keys and
     never splits on the separator.
5. **Optionally**, a second line in that same block: `**Depends on:** S01[, S02] (note)`
   — ids comma-separated, with an optional parenthesised note. A third line is an error.
6. `## ` sections run to the next `## `, bodies verbatim. **Headings are not validated**
   against your `sections` config — off-schema headings are kept in place and merely
   counted in the import report, and a heading may repeat.

## What makes it refuse

**Parse errors abort immediately**, exit 2, nothing written:

```console
$ slicer import --from docs/slices --dry-run
slicer: docs/slices/S13-atlas-tables-must-split.md: first line is not '# <id> — <title>'
```

Also in this tier: CRLF line endings, a missing final newline, the wrong number of
`**Findings:**` blocks, extra lines in that block, an unparsable meta line, a row that is
not six cells, a duplicate slice id on disk, a missing `README.md`, and a round-trip
mismatch — which prints a unified diff of what was lost.

**Reconciliation problems** let the whole tree be read first, then refuse to write, exit
1:

```console
$ slicer import --from docs/slices --dry-run
...
warn       S09: prose says 'parked' but the index says 'done'; index wins
PROBLEM    S09: status 'done' disagrees with its location (open)
refusing to write: fix the problems above, or re-run with --dry-run to inspect
```

The problems in this tier: a duplicate id in the index, a status label not in your
config, an index row with no slice file, a slice file with no index row, a dependency on
an unknown id, and a status that disagrees with the folder the file is in (`done` in the
table but not under `done/`, or the reverse).

Warnings do not block. `--dry-run` always exits 0 when there are no problems, and writes
nothing either way.

## A minimal tree that imports

Three files. This exact example imports clean and checks green.

`roadmap/README.md`:

```markdown
# Slice index — pass 1

| # | Slice | Size | Trees | Findings | Status |
|---|---|---|---|---|---|
| **Phase 0 — groundwork** | | | | | |
| [S01](done/S01-parse-the-config.md) | Parse the config file | M | core | F1 | done |
| [S02](S02-fail-on-missing-key.md) | Fail loudly on a missing key | S | core | F2 | — |
```

`roadmap/done/S01-parse-the-config.md`:

```markdown
# S01 — parse the config file

**Findings:** F1 · **Size: M** · **Tree:** core

## Why

The loader accepts anything.

## Check

Green.

**Not in this slice:** validation; that is S02.
```

`roadmap/S02-fail-on-missing-key.md`:

```markdown
# S02 — fail loudly on a missing key

**Findings:** F2 · **Size: S** · **Tree:** core
**Depends on:** S01 (the parser has to exist first)

## Why

A typo reads as a deliberate zero.

## Check

Green.

**Not in this slice:** the config schema doc.
```

```console
$ slicer init
$ slicer import --from roadmap
index      1 passes, 1 group rows, 2 items, next id S03
status     done 1 · — 1
sizes      M 1 · S 1
slices     2 parsed, 3 round-trip byte-identical
depends    1 edges
wrote      7 files under ~/code/my-project/.slicer
$ slicer render && slicer check
check passed: 2 items, render and sync current
```

Drop the `**Not in this slice:**` lines and it still imports — you just get a warning per
slice that its scope is unbounded.

For a fuller worked example, `tests/fixtures/legacy/` in this repository is a synthetic
14-slice tree across four passes, exercising every awkward corner of the grammar above.

## After importing

`import` does not render. Run `slicer render`, then `slicer check`. The generated
markdown under `.slicer/render/` is deliberately **not** byte-identical to your old
files — it is slicer's own shape. Relative links inside imported prose are re-based so
they still resolve from their new depth.
