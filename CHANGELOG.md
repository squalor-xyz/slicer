# Changelog

Notable changes to slicer are recorded here. This file follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Release slices should
update the changelog; unreleased changes belong under `[Unreleased]`.

## [Unreleased]

### Changed

- `slicer import --skeleton` lists `importance` and `urgency` (1, 2 or 3) and sets a non-default pair on the example item.
- `slicer add` names a non-empty pass in its plain-text confirmation, including a pass inherited from the previous item. `--json` is unchanged.
- `slicer next-id` prints the id the next `add` or `import` would take, without allocating it.
- `slicer import` stores prose between the outline title and the first item as the roadmap preamble. A different stored preamble is refused unless `--force`. `promote --file` rejects that prose.
- `--json --lean` omits empty and default scaffolding from a payload. `--json` alone is unchanged.
- Items can carry an optional `effort` of 1, 2, or 3. `list --sort effort` and `sort --by effort` put the lightest estimate first and unestimated items last. `next` and score sorting are unchanged.

## [1.0.0]

### Added

- Agent onboarding instructions with reusable prompts and a no-state quick start.
- JSON output for commands, including structured error envelopes for failures.
- A no-clone install path using an isolated Python environment.
- Roadmap commands for goals, status, dependencies, search, sorting, notes, and
  progress statistics.
- TUI search, filters, jump and help; item and slice note editing; queue
  reordering; and a guided roadmap wizard.
- Browser-viewable HTML roadmap rendering.

### Changed

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
