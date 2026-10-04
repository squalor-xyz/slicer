# Changelog

Notable changes to slicer are recorded here. This file follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Release slices should
update the changelog; unreleased changes belong under `[Unreleased]`.

## [Unreleased]

### Added

- Text from `next`, `next --ready`, `next --show`, and `next --batch` names `slicer show ID` when the item has a slice, instead of printing the slice path. `--path` prints that path line as well and does not change JSON. A row with no slice does not gain a show hint. The promote hint stays on `next --ready` and `next --show`.
- `slicer sections` lists the configured section names and marks the ones `next` requires. `required_sections` chooses those headings. A missing key still means Implement and Check. An empty list means an empty section does not hold a slice out of `next`.
- `slicer list --in-work` and `slicer list --review` show those queues in one step. `--in-work` is started plus reviewing when that role is set. `--review` is only the review status. The TUI `v` key cycles the same views around the unfinished set.
- `implement_finish` chooses how the agent loop finishes: `done` (the default, including when the key is missing) or `handoff`. `handoff` is a config error when `review_status` is empty. `slicer ai instructions` and `slicer ai skill` use that choice when the project can be read, and otherwise print the generic text with a stderr warning. Both start with the rule to use slicer commands instead of opening tracking files.

## [1.2.0] - 2026-10-02

### Changed

- Migrate errors name the expected token and `docs/migrate-format.md`. A round-trip that differs only by trailing blank lines says so. An index with no item rows reports that no index table was found and quotes the expected header, before each "slice file has no index row" line.
- `import` and `remove` take `--render --strict`. A failed render writes nothing, including a purge that would have deleted the slice file. `--strict` without `--render`, including with `--dry-run`, is a usage error. `done` stays render-first without the flag.
- Roadmap markdown and HTML show version-shaped pass groups newest first (`v1.10` above `v1.2`, a bare `v1` below `v1.1`), then other passes in declaration order. Items with no pass follow: those that are not done, then done items. The stored queue and `next` are unchanged.
- A status change moves a slice file in the worktree only. `git mv` is no longer used, so `done`, `retire`, and `set --status` leave the index untouched. Add the old path and the new path together to record the rename.
- Documented that plain `next` offers only open and started work, so a custom status (not `exclude_flags`) holds an item out of the queue, and that `verify`'s two git checks are warnings: an unconfigured render merge driver when sibling worktrees exist, and a done item with no commit subject in the last 2000 non-merge commits. `check` still owns render freshness.
- Agent guidance requires the slicer CLI for reading and changing goals, items, slices, notes, history, and roadmap prose, including during review and planning. A missing command is reported as a roadmap item. `slicer ai skill` now covers roadmap review and planning, and distinguishes handing work off for review from marking it done.

### Added

- Version-tag publication now creates a GitHub Release from the dated changelog after PyPI publication succeeds.
- Contributor tooling: `python3 tests/affected.py --run` selects tests that executed changed lines using a locally recorded map; a missing or stale map requires the full suite.
- Top-level `--about` prints the version, description, source URL, and issues URL without a project, with `--json` support. Help shows the project description and both URLs.
- `init --id ID` sets the id the first `add` allocates, so a project can continue an earlier sequence (`slicer init --id S21`). The id must match the configured prefix and width. On an index that already has items it is refused; use `add --id` instead. `--force` over an empty index still sets the counter.
- `next --batch K` returns up to K items from the same pool as `next`, optionally narrowed with one `--tree` and an exact `--size`. A dependent follows its dependency when that dependency is in the batch. `--start` claims the whole batch under one lock and passes `--owner` through; a failure leaves every id unclaimed. An empty batch exits 2 with `{"items": [], "blocked": [...]}` and no error object. `--batch` with `--show`, `-n`, or `--status` is a usage error, as is K < 1.
- `set --add-flag` and `set --remove-flag` edit one flag without replacing the rest of the list. They cannot be combined with `--flag` or `--no-flags`.
- Items record `attempts`. `start` from open and every `reject` increment it, and `set --attempts` sets it. The index schema is now 3. A version-2 index with no `attempts` key still loads, as 0.
- `slicer reject ID --note VERDICT [--to STATUS]` sends a review or reviewing item back to the open status (or another non-lifecycle status), clears its claim, adds the verdict to the item's notes, and logs one `reject` entry carrying it. Every id is checked before anything is written. It takes `--owner`, `--json`, `--render` and `--strict`.
- `next --status review` is the reviewer's one-call pickup: the highest-priority review item, ranked and dependency-gated like `next`, with the same `--ready`/`--section`/`--lean` payloads. `--start` claims it and moves it to `reviewing`, and a later call resumes it first. Only the configured review status is accepted.
- A `reviewing` status (`reviewing_status` in config, back-filled like `review`). `start` on a review item now moves it to `reviewing` and claims it, instead of back to `started`, so an implementer's `next` no longer resumes a review in progress. `list` ranks reviewing items with started ones, sibling worktrees count them as in work, and `handoff` refuses them. Set `reviewing_status` to `""` to keep the old behaviour.
- The STATUS column in `list`, `find` and `deps` widens to fit its longest label (`reviewing`, or a project's own status), so a long label no longer shifts the rest of its row. Tables whose labels fit in 7 characters are unchanged.
- `--owner NAME` on `start`, `next --start`, `handoff`, `release` and `done`, and a `SLICER_CLAIM_OWNER` environment variable, name who claims or acts per call, ahead of `claim_owner` and the git user. Those history entries record it as `by`, and `log --by NAME` filters on it. Other history lines are unchanged.
- `slicer id-prefix [NEW]` prints the id prefix, or changes its case for new ids (for example `S` to `s`) in the index and config together. Existing ids keep their case and no slice file is renamed. A prefix that differs by more than case is refused. It takes `--dry-run`, `--json`, `--render` and `--strict`.

### Fixed

- `set --title` keeps a short title that was never chosen separately in step with the new title. A distinct short title is left unchanged.
- `promote --file` and `import` lift a boundary paragraph out of an item's lead into the scope boundary. The same paragraph in both the lead and a section is refused, and nothing is written.
- `check` and dependency edits no longer treat an edge from a retired or done item onto a retired item as drift. An open, started, or parked item that depends on a retired item still fails.
- An explicit id whose prefix differs from the configured one only in case (`add --id s05` under `S`) now advances `next_id`, and the high-water check counts it. Allocation refuses a generated id that differs from an existing id only in case (`case_collision`) instead of writing a second slice over the first on a case-insensitive filesystem.

## [1.1.0] - 2026-09-29

### Added

- The text table from `slicer list`, and the `find` and `deps` rows, show a PASS column after CLAIM when the roadmap declares or names any pass: the item's pass key, or `-`. A project without passes keeps the table unchanged, and JSON output is unchanged.

### Changed

- The README is now a front page: install, a first run, a one-line-per-command table, and an index of the docs. The full command and flag reference, the `.slicer/` layout, removing items, and roadmap prose moved to `docs/commands.md`; the TUI manual moved to `docs/tui.md`. Each topic now has one full treatment, and the docs coverage test checks that `docs/commands.md` names every flag and the README table names every command.

### Fixed

- Slice headers render the size value outside the bold label (`**Size:** M`),
  so an empty size keeps valid emphasis instead of showing literal asterisks.
- The different-worktree warning no longer fires for a project elsewhere in the code's own worktree (a nested subproject, or a test fixture under an ignored scratch directory). It now requires the two paths to be different worktrees, not merely the same repo. The suite passes with the in-checkout `TMPDIR` that AGENTS.md documents.

## [1.0.0] - 2026-09-29

### Added

- `slicer handoff ID [ID ...]` hands started work to review: the status becomes the new `review_status` (default `review`, back-filled into existing configs unless another status already renders as `review`) and the claim is cleared, in one save with one `handoff` history entry per item. `next` does not offer review items and their dependents stay blocked until `done`; a reviewer finds them with `list --status review` and claims one with `start`. It refuses, writing nothing, an item that is not started or in review, or has no slice. `--render`, `--strict`, `--note` and batch ids work as on `start`. `review_status: ""` disables it.
- `slicer add` prints a non-blocking stderr hint when an open or started item is filed at importance 2, urgency 2 and no effort, and `slicer import` (dry run and real) names such items in `warnings`, so an unscored item is caught when it is filed rather than when the queue will not sort. Stdout, the JSON payload and the exit code are unchanged; a deliberate 2/2 with an effort estimate is quiet.
- `slicer start` records a claim on the item (owner and time). `slicer list` names the owner and marks other in-progress rows with `*`; `--json` includes `claim`. `slicer release` clears a claim without changing status, and `done` clears it too. The owner is config `claim_owner`, otherwise the git user name, otherwise the worktree name.
- `slicer start` warns on stderr when another branch or worktree name refers to that slice id. The current checkout is ignored, the exit code is unchanged, and `next` stays silent.
- Text `slicer list` now labels its columns and reports how many matching done or retired items the default filter hides, with a `--all` hint. JSON output remains an array.
- `slicer check` also parses the backtick `slicer ...` examples written inside a live slice's sections and fails on a flag the real parser does not know, naming the slice id, section, and flag — so a removed or renamed flag (like `--require-render`) is caught before an agent copies it into a real command. Done and retired slices are not scanned, so history keeps its old flag names; a flag merely mentioned in prose is left alone.
- `slicer init`, when run inside a git repo, prints the two `git config` lines that turn on the `slicer-generated` render merge driver, so the one-time per-clone setup is surfaced rather than buried in the docs. slicer still never runs `git config` itself. `--json` output is unchanged.
- slicer warns on stderr when its own code and the project it discovers are different worktrees of one repo — an editable install run from a sibling checkout, whose `src/` edits are not what runs. The note names both paths and suggests `PYTHONPATH=src python3 -m slicer`. It never touches stdout or the exit code, stays silent for ordinary use (code under the project, or an unrelated install), and is silenced by `SLICER_NO_CODE_WARNING`.
- `slicer setup-git` prints the two `git config` lines that turn on the `slicer-generated` render merge driver, so a clone (which never runs `init`) can enable it in one step — run the command, or `slicer setup-git | sh`. It needs no project, `--json` returns the commands as a list, and slicer still never runs `git config` itself.
- `slicer verify` warns (never fails) when the `slicer-generated` render merge driver is not configured in the current clone, pointing at `slicer setup-git`, so a forgotten setup is caught before a merge writes conflict markers. It fires only when the checkout has other worktrees (the parallel workflow the driver serves), so a single-worktree clone stays quiet; the new `render_driver_check` config flag (default true) turns it off entirely. The probe is a read-only `git config --get`; `slicer check` stays git-free and does not run it.
- `slicer list` marks an item that is started or claimed in a sibling git worktree with `wt:<name>` in the CLAIM column (a local claim or start still takes precedence), and `--json` gains a per-row `in_work_elsewhere` list. It reads each sibling worktree's real state (not branch names), so a merged or off-convention branch never produces a false mark; the lookup enumerates worktrees once and is limited to checkouts on this machine.
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

- `slicer next` and `slicer status` skip an item that a sibling Git worktree has started or claimed, and report it: text adds `skipped ID (in work in wt:NAME)`, JSON adds `in_work_elsewhere: [{id, worktree, owner}]` when non-empty. An item also started or claimed in this checkout is still returned. Previously `next` offered it, so parallel agents could pick the same slice. The rendered roadmap's next pointer stays local-only.
- `slicer ai instructions` asks an agent to score every item it files (importance, urgency, effort, and size, tree and findings where used) with a one-line reason each, and the agent-reference prompts ask for the same, so a new roadmap sorts immediately. The "Implement one slice" section drops its read-command catalogue in favour of `--help`, from 431 to 319 words; `ai skill` is unchanged.
- `slicer add --pass ''` now files the item with no pass. It previously fell through to the previous item's pass, so an empty `--pass` meant the same as omitting it; `set --pass ''` already cleared a pass. Omitting `--pass` still inherits.
- Docs brought up to date for 1.0.0: the README command table now lists every subcommand and flag (including `setup-git`, `init --force`, `migrate --force`, `promote --force`, `start --note`, `remove --force` and `log --limit`), enforced by a new test; the agent reference documents the `ai skill`, `init`, `setup-git` and `remove` JSON payloads and the current item shape; getting-started covers pass inheritance on `add`, the merge-in-progress guard and the worktree code warning; `ai instructions` explains claims and `release`. `add --pass`, `promote --force` and `log --limit` gained help text, and `log`'s summary now says it shows all history, not only status changes.
- `--json` status tallies are keyed by status key, not display label: `stats` `by_status` and `by_tree_status`, the `status` census, and `import`/`migrate` `by_status` now report open items under `"open"` rather than the default label `"—"`, matching each item's `status`. A relabelled status no longer changes the JSON keys. Text output still shows the labels.
- `slicer set` and `slicer add` refuse a `--depends-on` that `check` would reject, and write nothing: an id that names no item (`no_such_item`; a comma list such as `S01,S02` was previously stored as one bogus id), a self-edge, an edge that closes a cycle, or a dependency on a retired item (`state`). A refused `set` leaves every requested field unchanged. A dangling edge or cycle already in the index still reaches `check` and does not block unrelated edits. `--strict` still guarantees only the render.
- The index schema is now 2 (claims); `config.json` stays at schema 1. A schema-1 project opens with no migration step and reads as unclaimed; the first saved change stamps schema 2, after which an older slicer refuses the project (`schema_too_new`) instead of dropping claims. Read-only commands leave the file at schema 1. Pre-release checkouts from before the newer-schema guard (S72) do not refuse and can drop claims, so upgrade every clone that shares a project.
- slicer's argparse usage, help, and error text stay uncolored on Python 3.14+ (which otherwise colorizes them by default and honors `FORCE_COLOR` even into a pipe), so diagnostics are deterministic across environments and free of ANSI an agent or test would have to strip.
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
