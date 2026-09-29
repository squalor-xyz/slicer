# Changelog

Notable changes to slicer are recorded here. This file follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Release slices should
update the changelog; unreleased changes belong under `[Unreleased]`.

## [Unreleased]

### Added

- Text `slicer list` now labels its columns and reports how many matching done or retired items the default filter hides, with a `--all` hint. JSON output remains an array.
- `slicer check` also parses the backtick `slicer ...` examples written inside a live slice's sections and fails on a flag the real parser does not know, naming the slice id, section, and flag — so a removed or renamed flag (like `--require-render`) is caught before an agent copies it into a real command. Done and retired slices are not scanned, so history keeps its old flag names; a flag merely mentioned in prose is left alone.

## [1.0.0]

### Added

- `--strict`, with `--render`, keeps a mutation only when rendering succeeds. The change is rendered before it lands, and a render failure rolls the whole change back — leaving state and generated output untouched — and exits 2 with `code="render"`. `--render` alone still saves first. `done --render` already requires a successful render and now shares the same render-first path.
- `slicer next --ready` returns a bounded pickup of the next eligible item: its id, title, status, dependencies, effective score, and slice path; the slice when it has one; and the blocked list. An empty queue matches `next` (exit 2, `item: null`). `--ready` with `--show` is a usage error.
- `slicer next --ready --section NAME` repeats to return the scope boundary and those section bodies only. Omitting `--section` still returns the full slice. `--section` without `--ready` is a usage error. `--json --lean` applies to that payload.
- `slicer list --flag` filters by a free-form flag. Repeat it to match any of the named flags. The TUI filter panel offers the same flag axis, including items with no flags.
- Agent onboarding instructions with reusable prompts and a no-state quick start.
- JSON output for commands, including structured error envelopes for failures.
- A no-clone install path using an isolated Python environment.
- Roadmap commands for goals, status, dependencies, search, sorting, notes, and
  progress statistics.
- TUI search, filters, jump and help; item and slice note editing; queue
  reordering; and a guided roadmap wizard.
- Browser-viewable HTML roadmap rendering.

### Changed

- The agent loop picks up work with `slicer next --ready --section … --json --lean`. `start` and slice edits use `--render --strict`. `done` stays `--render`. `slicer ai instructions` no longer links `docs/agents.md`, and it says not to open or hand-merge `.slicer/render/`.
- `slicer import --skeleton` lists `importance` and `urgency` (1, 2 or 3) and sets a non-default pair on the example item.
- `slicer add` names a non-empty pass in its plain-text confirmation, including a pass inherited from the previous item. `--json` is unchanged.
- `slicer next-id` prints the id the next `add` or `import` would take, without allocating it.
- `slicer import` stores prose between the outline title and the first item as the roadmap preamble. A different stored preamble is refused unless `--force`. `promote --file` rejects that prose.
- `--json --lean` omits empty and default scaffolding from a payload. `--json` alone is unchanged.
- Items can carry an optional `effort` of 1, 2, or 3. `list --sort effort` and `sort --by effort` put the lightest estimate first and unestimated items last. `next` and score sorting are unchanged.
- The TUI queue opens in `slicer list` order and, like `list`, hides done and retired until asked. Parked items stay visible and sort last. `o` sorts the view by a field and direction for the session without rewriting the stored queue.
- `slicer ai skill` prints one `SKILL.md` for Claude Code, Codex, and Grok. It names the implement loop and the exit codes, and it is generated from the same sentences as `slicer ai instructions`.
- `slicer next` skips a slice whose Implement or Check is empty and names it in `unspecified`, with the `slicer edit` command that fills the section.
- `slicer ai instructions` shows a repeated `--section` plus `--context` read. The headings in that example are illustrations; use the names the project configures.
- The generated `.gitattributes` points `render/` at a `slicer-generated` merge driver so parallel branches keep the current branch's copy on merge instead of writing conflict markers into the large ROADMAP files. `slicer init` writes it; each clone defines the driver once with two `git config` lines (see the README and getting-started). `log.jsonl` still union-merges, and `slicer render` is still required so the kept files match the merged `index.json`.
- `next` reports item score and status, supports offsets, and can start the
  selected item.
- `move` supports moving an item to a chosen position or to the top of the
  queue.
- TUI queue and detail views show priority scores and status information.
- `list` omits items in the configured done status unless `--all` is set or `--status` names them. `--all` together with `--status` is a usage error.
- `list` also omits the configured retired status, and orders the remaining rows as `next` would walk them, then the other visible rows by effective score. `--sort score` stays a flat score sort.
- The PyPI distribution name is `squalor-slicer`. The `slicer` command and import are unchanged. A `v*` tag that matches `__version__` publishes to PyPI with trusted publishing.

### Fixed

- Empty `--depends-on` input now clears dependencies instead of storing a
  phantom dependency.
- Mutating commands that render no longer print a redundant render hint.
- `--note` on status commands is documented and handled as a history entry,
  distinct from a durable item note.
