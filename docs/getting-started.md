# Getting started

Adding slicer to a project, from nothing to a rendered roadmap and a green CI gate.

Every command block below is real output, with the project path shortened to
`~/code/my-project`.

- [1. Install](#1-install)
- [2. Set it up](#2-set-it-up)
- [3. Configure before you add anything](#3-configure-before-you-add-anything)
- [4. Get your roadmap in](#4-get-your-roadmap-in)
- [5. Turn an item into a slice](#5-turn-an-item-into-a-slice)
- [6. The loop](#6-the-loop)
- [7. Passes, if you want them](#7-passes-if-you-want-them)
- [8. Wire it into CI](#8-wire-it-into-ci)
- [9. When something goes wrong](#9-when-something-goes-wrong)

## 1. Install

```sh
git clone git@github.com:squalor-xyz/slicer.git
cd slicer
pipx install .          # puts `slicer` on PATH
```

Or run it uninstalled from a checkout, with `PYTHONPATH=src python3 -m slicer`.

Python 3.11+, no dependencies.

## 2. Set it up

Run `init` in the root of the project you want to track:

```console
$ cd ~/code/my-project
$ slicer init
initialised ~/code/my-project/.slicer
```

That creates four things:

```
.slicer/config.json        everything project-specific
.slicer/index.json         the ordered queue — empty, next id S01
.slicer/templates/*.md     slice.md, roadmap.md, row.md — yours to edit
.slicer/slices/done/       where finished slices land
```

It deliberately does **not** create `.slicer/render/` (first `slicer render`),
`.slicer/slices/retired/` (first `slicer remove --reason`), or `.slicer/log.jsonl`
(first change). They appear when they are first needed.

`init` never overwrites an existing `index.json`. Running it twice refuses:

```console
$ slicer init
~/code/my-project/.slicer already exists; pass --force to overwrite its config and templates
```

`--force` rewrites `config.json` and the three templates. It still leaves `index.json`,
your slices and your log alone — it is for restoring a template you edited into a corner,
not for starting over.

## 3. Configure before you add anything

Most of `.slicer/config.json` is safe to change whenever you like. Two are not, and one
is a trap:

| Key | Default | Change it… |
|---|---|---|
| `id.prefix` | `"S"` | **Before `init`, or never.** See below. |
| `id.width` | `2` → `S01` | **Before `init`, or never.** |
| `sections` | `Why, Files, Failing tests, Implement, Check, Git` | Any time — it only affects future `promote`s. |
| `statuses` | `open: —`, `done`, `parked`, `later` | Labels any time; renaming a *key* items already use breaks them. |
| `boundary` | `**Not in this slice:**` | Any time; `""` turns off the scope-boundary warning. |

**The trap:** `id.prefix` and `id.width` are read only when `index.json` is first
written. After that, id allocation reads the copy stored *in `index.json`*, so editing
them in `config.json` silently does nothing. If you want `TASK-001` instead of `S01`,
set it before you run `init`.

The full key-by-key reference, including which changes strand existing files, is in
[configuration.md](configuration.md).

## 4. Get your roadmap in

There are two ways in, and which one applies depends entirely on what you have now.

### 4a. From nothing

`slicer add` appends one roadmap row per invocation:

```console
$ slicer add "Parse the config file" --size M --tree core --findings "G1"
added S01  Parse the config file
$ slicer add "Fail loudly on a missing key" --size S --tree core
added S02  Fail loudly on a missing key
```

`add` takes `--size`, `--tree` (repeatable), `--findings`, `--status`, `--pass` and
`--id`. It does **not** take `--depends-on` or `--short-title` — `set` does, so
dependencies are a second step:

```console
$ slicer set S02 --depends-on S01
updated S02
```

An item added this way is a roadmap row and nothing more. There is no slice file yet;
that is [step 5](#5-turn-an-item-into-a-slice).

```console
$ slicer list
  1  S01   —       M  Parse the config file
  2  S02   —       S  Fail loudly on a missing key
```

### 4b. From a plain list

There is no bulk-load command. If your planned features are a list of lines, loop:

```console
$ printf '%s\n' "Cache the parsed config" "Document the config schema" > features.txt
$ while IFS= read -r title; do slicer add "$title"; done < features.txt
added S03  Cache the parsed config
added S04  Document the config schema
```

Then fill in sizes and trees with `slicer set` as you learn them. This is genuinely the
supported path today — `slicer import` is **not** a general importer, see below.

### 4c. From an existing markdown tree

`slicer import` migrates a tree that is **already in slicer's own legacy markdown
format**: an index named `README.md` containing a six-column table, and one file per
slice whose first line is `# S01 — title`. It is for projects that were running this
workflow by hand before slicer existed. It will not read an arbitrary roadmap, a GitHub
issue export, or a bullet list — for those, use [4b](#4b-from-a-plain-list).

[import-format.md](import-format.md) is the exact grammar, with a minimal working
example. Check yours against it before you start.

Always dry-run first. Nothing is written, and you get the full census:

```console
$ slicer import --from docs/slices --dry-run
source     ~/code/my-project/docs/slices
index      4 passes, 5 group rows, 14 items, next id S15
status     done 8 · — 3 · parked 2 · later 1
sizes      M 7 · S 3 · L 2 · M [OWNER] 2
slices     14 parsed, 15 round-trip byte-identical
sections   2 off-schema: Code review (same slice) 1, What landed 1
depends    1 edges
reconcile  13 title, 0 findings, 0 trees differences - both sides kept
would write .slicer/ under ~/code/my-project (nothing written)
```

`15 round-trip byte-identical` is the line that matters: 14 slices plus the index, each
parsed and re-emitted to exactly the bytes it came from. Import refuses to write
anything unless every file does that, so a document slicer cannot reproduce is never
half-migrated.

When it is clean, run it for real, then render — **`import` does not render**:

```console
$ slicer import --from docs/slices
...
wrote      19 files under ~/code/my-project/.slicer
$ slicer render
rendered 15 file(s)
$ slicer check
check passed: 14 items, render and sync current
```

`--from` defaults to `docs/slices`. Once you trust the migration, delete the old tree —
`.slicer/render/` replaces it, and relative links inside imported prose have already
been re-based to their new depth.

## 5. Turn an item into a slice

A roadmap row says *what*. A slice says *why, which files, which failing test first*.
`promote` creates one:

```console
$ slicer promote S01
promoted S01 -> ~/code/my-project/.slicer/slices/S01.json
```

The headings come from `sections` in your config, each with an empty body, and the
**last** one gets your `boundary` marker as its body. With the defaults that is:

```
Why · Files · Failing tests · Implement · Check · Git
```

with `## Git` pre-filled with `**Not in this slice:**`. Write the scope boundary there
and `slicer check` stops warning that the slice is unbounded.

Fill sections one at a time:

```console
$ slicer edit S01 --section Why --file why.md
updated S01 / Why
```

`edit` takes `--file`, `--stdin`, or nothing — in which case it opens `$EDITOR`. For
anything more than one section, `slicer tui` is faster: `tab` moves between the queue
and the detail pane, and `e` opens `$EDITOR` on whatever is selected.

Both `show` and `edit` tell you when an item has not been promoted:

```console
$ slicer show S03
S03  Cache the parsed config
(no slice yet; run `slicer promote S03`)
```

## 6. The loop

```console
$ slicer next
S01  Parse the config file
     ~/code/my-project/.slicer/slices/S01.json
```

`next` is the first open item whose dependencies are all done. Add `--json` and an agent
can read it without parsing markdown — every read command takes `--json`.

Implement it, then:

```console
$ slicer done S01 --note "loader now refuses a missing key"
S01 -> done
```

`done` moves the slice file into `slices/done/` with `git mv`, so the status change
stays one tracked rename:

```console
$ git status --short
 M .slicer/index.json
 M .slicer/log.jsonl
R  .slicer/slices/S01.json -> .slicer/slices/done/S01.json
```

Now re-render. `check` will tell you if you forget:

```console
$ slicer check
stale render: ROADMAP.md
check failed; run `slicer render` and `slicer sync`, then re-run
$ slicer render
rendered 1 file(s)
$ slicer check
check passed: 4 items, render and sync current
```

**Commit `.slicer/` — all of it, including `render/`.** The markdown is generated, but
it is what people read in a diff and in a pull request, and `check` fails when it is
stale. That staleness check is the whole point: state and its rendering cannot drift.

`slicer log` shows the history of status changes; `slicer stats` gives counts by status,
size, tree and pass.

## 7. Passes, if you want them

Passes are an optional grouping — a review round, a milestone, a phase. Items with no
pass render as one flat table, which is fine for most projects.

```console
$ slicer prose add-pass 2 --heading "# Pass 2 — hardening"
$ slicer add "Rotate the signing key" --pass 2
$ slicer set S03 --pass 2
```

A declared pass renders even with no items yet, so you can open a group before its first
slice exists. `prose drop-pass 2` closes it, and refuses while any item is still filed
there:

```console
$ slicer prose drop-pass 2
slicer: pass '2' still has 2 item(s): S03, S05. Move them first with `slicer set <id> --pass <other>`.
```

Declared passes render first, in the order you declared them; anything still carrying no
pass forms a trailing group with no heading. So once you start using passes, file
everything, or live with that untitled group at the bottom.

Each pass carries its own prose — `pass.2.heading`, `pass.2.intro`, `pass.2.outro` —
alongside the roadmap's `preamble` and `epilogue`. `slicer prose list` names every block
in render order; `slicer prose edit REF` changes one. See the README's
[Roadmap prose](../README.md#roadmap-prose) section.

## 8. Wire it into CI

One command, one exit code:

```yaml
- name: slicer check
  run: slicer check
```

`check` composes render staleness, sync drift and the offline integrity checks. It never
shells out to git, so a shallow checkout is fine.

`slicer verify` is the other one — it additionally compares the index against
`git log --all`, warning when an item is recorded done but no commit subject mentions
it. If you add it to CI it needs `fetch-depth: 0`, or it sees no history and skips.

## 9. When something goes wrong

Exit codes: **0** fine · **1** drift or a failed check · **2** usage error, or nothing
to do.

| What you see | What it means |
|---|---|
| `no .slicer/ found in … run \`slicer init\` in the project root` | You are outside a tracked project. slicer walks up from the working directory looking for `.slicer/config.json`, git-style. |
| `… already exists; pass --force to overwrite its config and templates` | `init` on an initialised project. `--force` rewrites config and templates only. |
| `<ID> has no slice; run \`slicer promote <ID>\` first` | `edit` on a roadmap row that has no slice file yet. |
| `<ID> already has a slice; pass --force to overwrite it` | `promote` on an already-promoted item. `--force` discards what is there. |
| `unknown status 'x'; known: [...]` | The status is not a key in `config.statuses`. |
| `id <X> already exists; ids are never reused` | `add --id` naming a claimed id. Ids are claimed for the life of the project. |
| `stale render: …` / `check failed` | Run `slicer render` (and `slicer sync` if you have sync targets). |
| `template missing; re-run \`slicer init --force\` to restore it` | A file under `.slicer/templates/` was deleted. |
| `refusing to write: fix the problems above` | An import found problems. Nothing was written; see [import-format.md](import-format.md). |

`slicer verify` is the broader health check — dangling dependencies, cycles, slices in
the wrong folder for their status, slices with no index row. It reports and never
rewrites.

---

Next: [configuration.md](configuration.md) for every config key,
[import-format.md](import-format.md) for the legacy markdown grammar, and
[ARCHITECTURE.md](../ARCHITECTURE.md) if you are changing slicer itself.
