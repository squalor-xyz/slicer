# Changelog

Notable changes to slicer are recorded here. This file follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Release slices should
update the changelog; unreleased changes belong under `[Unreleased]`.

## [Unreleased]

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

### Fixed

- Empty `--depends-on` input now clears dependencies instead of storing a
  phantom dependency.
- Mutating commands that render no longer print a redundant render hint.
- `--note` on status commands is documented and handled as a history entry,
  distinct from a durable item note.
