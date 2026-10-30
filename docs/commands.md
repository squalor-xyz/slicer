# Command reference

Every slicer command and flag, and the behavior that spans commands. For a guided first
run see [getting started](getting-started.md); for JSON shapes and exit codes see the
[agent reference](agents.md); for keys and screens see the [TUI manual](tui.md).

Top-level `--help` shows the project description and source and issues URLs.
`--about` prints the name, version, description, source URL, and issues URL without
requiring a subcommand or project. `--about --json` returns `name`, `version`,
`description`, `url`, and `issues`. `--version` prints only `slicer VERSION`.

## Commands

| | |
|---|---|
| `ai [instructions]` | agent quick start. `slicer ai` and `slicer ai --json` are `slicer ai instructions` with the same flags. The project can set `implement_finish` to `handoff`, making step 4 handoff only, and `handoff_requires_note_kind` to name its required current-attempt report. No project, or an unreadable config or index, prints the generic text and warns on stderr. Does not lock or write. Supports `--json`. `--rest` prints only what the skill does not already carry and does not read the project |
| `ai skill [--output PATH] [--install] [--force]` | the same loop as a `SKILL.md` for Claude Code, Codex, and Grok. Same project read and fallback as `ai instructions`. `--output` writes the printed bytes to PATH (parents created); `--install` writes them to `.claude/`, `.agents/` and `.grok/` `skills/slicer/SKILL.md` under the project root. Different existing text exits 2 (`state`) with nothing written unless `--force`; stdout lists the paths written |
| `ai relay` | the slice relay: an outer session (the one the owner talks to) spawns one orchestrator per slice, and the orchestrator runs the slice through an implementer, reviewer, architect and gate. The first line is the contract marker `Contract: relay v10` (the number is `ai.RELAY_CONTRACT`, bumped when a rule an outer session, orchestrator or role follows changes, not for a rewording). A project that pins another version with `relay_contract` is refused with `contract_mismatch` (exit 2, no text; the same for `ai relay-orchestrator` and `ai relay-brief`, never for `--init-roles`), and `check` warns; `pinned` in the JSON is the pin or `null` (see [agents.md](agents.md)). Setup also asks for the orchestrator's agent, model and reasoning effort (agent type `relay-orchestrator` where the harness has one, otherwise a general-purpose agent briefed with `slicer ai relay-orchestrator`) and the environment lines every brief carries; Order gives the outer session's steps (worktree, `relay-run begin`, the orchestrator step, a brief that starts "First run `slicer ai relay-orchestrator` and follow it.", a check of the report's first and last line (a hand-back that names the role it waits on is relayed, not stopped), Approve (a wait recorded with `relay-run step --phase wait --waiting-on approval`, the owner shown the gate verdict, tests, diff stat, owner questions, `[HUMAN CHECK]` lines and `[AFTER LAND]` lines, each confirmed human check recorded as a note, a fix note sent to a fix round), Land), and each slice gets a fresh orchestrator. Setup asks whether the implementation splits into parts and whether to sliver the slice (default no); a `## Slivers` section between Order and Gate gives the orchestrator the rules (eligibility by effort, filing with `add --parent ID` on main, order in the parent's worktree, parallel runs, escalation). A split has one lead implementer who owns the tracking steps, parts with disjoint files and their own tests, and one review and one gate for the combined diff. Same project read and fallback as `ai instructions`. Handoff finish closes with `handoff` and that text does not name the `done` command (`--phase done` is a relay-run phase); otherwise the close is `done`. Order names the `relay-run` recording steps: `begin` when the run starts, `step` for each role and subagent as it starts and ends, and `end` when the run's end point is reached or the relay or the owner stops it (for a landed slice after the ff-only merge and before `slicer land`, never while a step waits), so `relay-run list` shows the run without extra prompting. Recording is advisory, and a failed `relay-run` call never stops the relay. A project whose `.slicer/relay-roles.json` exists gets a `## Roles matrix` section after Setup holding the matrix as Markdown (what `relay-roles show` prints), and Setup gets the propose line naming agent, vendor, model and reasoning effort and the `relay-roles recommend` command, which resolves each role (the orchestrator included) to its first matching row, reports a role no row matches as unmatched (`(no matching row)` in the text, `unmatched` in the JSON) and lists its alternatives; the line tells the outer session to show that lineup once, let the owner accept it as a whole or override rows (`--prefer-agent`, `--select`), and not to treat acceptance as authority to start paid sessions, commit or land (Setup's other questions stay the owner's). Every `ai relay` also routes a role on the orchestrator's own harness as a native subagent and a role on another harness as a headless session in the slice worktree with the same brief; without the file, Setup names `relay-roles init`. A leftover `.slicer/relay-roles.md` is ignored with a stderr warning. A corrupt matrix is `corrupt` and one from a newer slicer is `schema_too_new` (exit 3). No project, or an unreadable config or index, prints the generic text and warns on stderr. Does not lock or write, except `--init-roles` below. `--json` returns `{contract, pinned, relay, setup, order, land, report, outcomes, stop, changes, roles, gate, finding_kinds, conformance}` in that order, with the same text in `relay`. `setup` lists `{key, text}` for the eight Setup bullets (keys `spec_check`, `roles`, `split`, `sliver`, `role_models`, `orchestrator`, `environment`, `end_point`); `end_point` also carries `options: ["finish", "commit", "landed"]`. `order` is a list of `{step, text}`. `order` holds outer-session steps 1–6. `land` is a list of `{step, text}` for the `## Land` section's ten steps: the relay landing order, ff-only by default (see [Landing a relay slice](landing.md#landing-a-relay-slice)). `report` is `{max_lines: 30, lines: [{key, template}]}` with line keys `contract`, `slice`, `worktree`, `rounds`, `tests`, `diff_stat`, `deviations`, `later_notes`, `owner_needed`, `role_steps`, `nothing_left_running`, in that order. `outcomes` is `["pass", "fail", "stopped"]`, shared with `relay-run end --outcome`; `stop` lists the three stop conditions as strings. All `text` values and stop strings join prose line breaks with single spaces. `roles`, `gate`, `finding_kinds` and `conformance` carry the Roles section's paragraphs, the gate criteria and verdict lines, the finding kinds and the Conformance obligations (what any orchestrator, agent or program, owes the contract). `--lean` preserves all contract lists, even empty ones; see [agents.md](agents.md) |
| `ai relay-brief ID --worktree PATH --branch NAME --run RUN --step N --well-specified yes\|no --split yes\|no [--prefer-agent AGENT] [--select ROLE=ROW_ID ...] [--parallel ID ...]` | print the slice's orchestrator brief from `relay-brief.md`: identity, accepted role recommendations (orchestrator included), run and parent step, parallel IDs and commit title. The selection flags follow `relay-roles recommend`. A handoff project's override starts before work and files the rendered closing note and handoff after PASS. The built-in ENVIRONMENT block is empty; put project-specific commands in the template with `template set`. Missing project copies fall back to the built-in template. Read-only; does not spawn agents or record runs. `--json` returns `{brief, roles, contract}`; see [agents.md](agents.md) |
| `ai relay-orchestrator` | the orchestrator's self-contained contract for one slice: its rules and run report. Order holds only the orchestrator's steps; Gate, Every brief and Stop share their text with `ai relay`, so no second command or filtering is needed. The rules (worktree only, no product code, no `relay-run begin` or `end`, a relay-run step per round, the closing note and the finish step only after `VERDICT: PASS`, waiting in the foreground without sleep loops (a role is run and waited on in the foreground, a timed-out wait is re-issued, no role backgrounds a shell command, and an early hand-back is for an owner question, a permission denial or a failure only; a turn a harness forces to end before the final report ends with a `waiting on: ROLE AGENT-ID` line), thin reads, disputes, a small fix round checked and gated in one pass by the read-only role that raised its findings, owner questions that do not block asked in the ledger as they arise, a permission-denied slicer step that stops the run before the commit, the gate, stop conditions) and the run report it returns (at most 30 lines, starting with the same `Contract: relay v10` line as `ai relay` and ending `nothing left running: yes`). The finish step (`handoff` or `done`) and the report kind follow the project's `implement_finish` and `handoff_requires_note_kind`, as for `ai relay`. No project, or an unreadable config or index, prints the generic text and warns on stderr. Does not lock or write. `--json` returns `{contract, pinned, orchestrator, order, report, outcomes, stop, changes, gate, finding_kinds, conformance}` in that order (no `roles`: its text has no Roles section). `order` is a list of `{step, text}`. `order` holds orchestrator steps 1–4 with the configured finish step. `report` is `{max_lines: 30, lines: [{key, template}]}` with line keys `contract`, `slice`, `worktree`, `rounds`, `tests`, `diff_stat`, `deviations`, `later_notes`, `owner_needed`, `role_steps`, `nothing_left_running`, in that order. `outcomes` is `["pass", "fail", "stopped"]`, shared with `relay-run end --outcome`; `stop` lists the three stop conditions as strings. All `text` values and stop strings join prose line breaks with single spaces. `--lean` preserves all contract lists, even empty ones; see [agents.md](agents.md) |
| `ai relay --init-roles [--force]` | the same as `relay-roles init --render`: write the example matrix to `.slicer/relay-roles.json` and render it, under the project lock. Needs a project (exit 2, code `state`, without one). An existing file exits 2 with code `state` and nothing is written unless `--force` replaces it; `--force` without `--init-roles` is `usage`. `--json` returns `{"written": [PATH]}` |
| `ai relay-roles` | the same as `relay-roles show`. Needs a project. Without `.slicer/relay-roles.json` it exits 2 with code `no_relay_roles` and names `relay-roles init`. Does not lock or write. `--json` returns `{"relay_roles": TEXT, "rows": [...], "rules": [...]}` |
| `init [--force] [--id-prefix PREFIX] [--id-width WIDTH] [--id ID] [--relay-roles]` | create `.slicer/` with config and templates; choose an ID prefix and minimum digit width (defaults `S` and `2`). `--force` rewrites config and templates, retaining omitted scheme settings and the counter; scheme changes require an empty queue. `--id` sets the ID the first `add` allocates (`S21` or `sp-21`), must match the scheme, and is refused when the index already has items. `--relay-roles` also writes the example matrix to `.slicer/relay-roles.json` and renders it, as `relay-roles init --render` does, under the project lock; an existing matrix is kept, never replaced (not even with `--force`), and the output says so, with `relay-roles init --force` as the way to replace it. `--json` then adds `relay_roles: PATH`. Without the flag, the text output adds a line naming `slicer relay-roles init` while no matrix exists, before the git setup hint, and `--json` stays `{root, dir}` |
| `setup-git [--attributes \| --hook]` | print the six `git config` lines that enable the merge drivers: the optional `slicer-generated` render driver (slicer regenerates a stale `render/` on the next write anyway), the `slicer-index` driver for `index.json` and the `slicer-relay-roles` driver for `relay-roles.json` in this clone (`slicer setup-git \| sh` applies them); needs no project. `--attributes` also refreshes `.slicer/.gitattributes` from the current template, so an existing project picks up new template lines without `init`: under the project lock it writes the file only when its bytes differ, creates it when missing, and also adds any line `init` writes to `.slicer/.gitignore` (`feedback.md`, `feedback-reported.json`, `relay-runs.jsonl`, `lock`) that file lacks, in `init`'s order after the lines it already holds. It touches no other `.slicer/` file. Run it on main: it writes the tracked `.slicer/.gitignore` even in a linked worktree, where an ordinary write would not. It needs a project (exit 2, code `state`, without one), and refuses during a merge (`merge_in_progress`). Stdout is still only the `git config` lines; the report `wrote PATH` or `unchanged PATH` goes to stderr, followed by `added LINES to PATH` when `.slicer/.gitignore` gained lines (the `--json` payload does not name them). `--json` returns `{commands, attributes: {path, changed}}` with the absolute path. `--hook` instead prints an optional post-merge shell script with an install command in a comment; needs no project and writes nothing. It runs `slicer render` only when `check --json` reports `roadmap_only`, asks you to commit the rewritten files, and otherwise reports a failed check for manual attention. `--json` returns `{hook, install}`. See [Git merge drivers](#git-merge-drivers) |
| `merge-index BASE OURS THEIRS` | the `slicer-index` merge driver, which Git runs with its `%O %A %B` files and a person does not. It merges items by id and object fields by name, takes `next_id` as the largest of the three, and writes the result over `OURS`. A value changed on one side takes that change, equal changes coalesce, lists and prose are atomic, except an item's notes: those merge as the union by note id, ordered by creation time, with notes that share a time kept yours first then theirs, and a note removed on one side stays removed only if the other side left it unchanged. Exit 0 is a clean merge. Exit 1 leaves conflict markers in `OURS` only where both sides changed a value differently, a record was deleted on one side and edited on the other, the same new id was filed twice with different content, both sides reordered the shared items differently, both sides changed one note differently or one removed a note the other edited, or the result would break the index (two ids for one filing key, ids differing only in case, a dependency cycle or on a removed item); stderr names each. Input that is not a readable index conflicts and is never merged cleanly. It needs no project, prints nothing when the merge is clean, and is left out of `--help`'s command list |
| `merge-relay-roles BASE OURS THEIRS` | the `slicer-relay-roles` merge driver, which Git runs with its `%O %A %B` files and a person does not. It merges the rows and the rules by id and their fields by name: a value changed on one side takes that change, equal changes coalesce, and `next_row` and `next_rule` take the largest of the three, so the counters never issue an id twice; an id that only the merged-in side issued and then removed can be kept by a row your side added under it (its history then shows an add and a remove for that id before your row). Row order follows the base unless one side reordered it. A row or rule that both sides issued under one id with different content (two branches that each ran `relay-roles add` or `rule-add`, or `THEIRS` taking an id `OURS` issued and then removed) is kept twice: `OURS` keeps the id and the one merged in gets a fresh id past every id either side issued, with its text unchanged (the old id is not copied into its note). The renumbering is recorded only on stderr: the driver writes no `log.jsonl` entry and rewrites none, so history keeps the original ids and two `add` entries can name the same id. A row added identically on both sides is kept once, and an empty `BASE` (both branches created the file) is an empty matrix. Exit 0 wrote the result over `OURS`. Otherwise `OURS` is left exactly as it was and the command exits 2 with code `merge_conflict` when both sides changed the same value differently, one side removed a row or rule the other edited, both reordered the rows differently, or the result is not a valid matrix (stderr names each place, such as `rows/r3/model`); or with code `corrupt` (exit 3) or `schema_too_new` when an input is not a relay roles matrix (including a counter at or below an id in use) or comes from a newer slicer. Git reports a normal conflict, and slicer refuses writes while the merge is unfinished, so keep one side with `git checkout --ours|--theirs .slicer/relay-roles.json`, `git add` it, finish the merge, then re-apply the other side's changes (every row and rule it added or edited, not only the conflicting value) with `slicer relay-roles`. It needs no project, prints nothing when the merge is clean, and is left out of `--help`'s command list |
| `import FILE [--dry-run] [--force]` | bulk-load a roadmap from a markdown outline. A one-item outline files one item with its fields (`short_title` included) and its spec in one command; run it with `--dry-run` first. When a sibling git worktree's `next_id` is higher than this checkout's, the id starts above it, so two worktrees do not file different items under the same id. After the import is written, each undeclared pass key the new items name gets one stderr line naming the key and the `prose add-pass` command; a dry run or a refused outline prints none. |
| `import --skeleton` | print an outline template built from your config |
| `migrate --from DIR [--dry-run] [--force]` | convert an existing legacy markdown tree; `--force` replaces an existing roadmap |
| `add TITLE [--print-id] [--key KEY] [--id/--size/--tree/--findings/--status/--pass/--importance/--urgency/--effort/--depends-on/--discovered-from/--parent/--short-title]` | append a roadmap item (no slice file yet); `--print-id` writes only the allocated id and a newline, including for a `--key` retry; it cannot combine with `--json` (exit 2, `usage`, nothing written by the command itself; see [Git merge drivers](#git-merge-drivers) for the stale-render heal that precedes any write), and sends the `--render` summary to stderr. Repeat `--depends-on ID` for multiple dependencies. `--effort` is 1–3 and optional; an open item left at importance 2, urgency 2 and no effort gets a stderr hint. An item filed under a pass that no `prose add-pass` declared (by `--pass`, or inherited from the item before) gets one stderr line naming the key and the command that declares it; the JSON and the exit code do not change, and nothing is refused. When a sibling git worktree's `next_id` is higher than this checkout's, the id starts above it, so two worktrees do not file different items under the same id. An explicit `--id` is taken as given, unless a sibling git worktree has filed that id as an item of its own (one its merge base does not have): then it is refused with `id_taken_elsewhere` (exit 2), nothing is written, and the message names the worktree and its title. An id both checkouts inherited from their merge base is `already_exists` here, as before. `next-id`, or leaving out `--id`, gives a free id |
| `promote ID [--file/--stdin] [--boundary TEXT] [--force]` | give an item a slice file; a one-item outline fills its sections in one call. `--force` overwrites an existing slice |
| `move ID --before/--after/--to` | reorder the queue; position is the manual priority, and breaks score ties |
| `sort [--by score\|effort] [--render]` | reorder the whole queue in one step. `score` (default) persists `list --sort score`. `effort` persists `list --sort effort`: lightest estimate first, unset last |
| `next [-r] [-n N] [--max-attempts N] [--batch K [--disjoint]] [--tree TREE] [--size SIZE] [--flag PATTERN ...] [--no-flag PATTERN ...] [--status KEY] [--start [--owner NAME] [--start-to KEY] [--render [--strict]]] [--show\|--ready [--section NAME ...]] [--notes-kind KIND ...] [--min-trust TIER] [--notes-budget N] [--path]` | one eligible item at offset N (default 0), or up to K with `--batch`, with its effective score and status. Text for an item with a slice names `slicer show ID` and does not print the slice path unless `--path` is set. `--path` is text only. JSON still includes `path`, and `--json --lean` still drops it on an item. A row with no slice does not gain a show hint. The promote hint stays on `next --ready` and `next --show`. `-r`/`--recursive` returns one next item per project and never ranks across projects; with `--start`, `--render`, `--owner` or `--start-to` it is `usage`, because `-r` is read-only. The envelope is described under `list`. `next --show` text prints the slice without its generated banner, so it names no source path either; `show` and the files under `.slicer/render/slices/` keep the banner. `--tree` and `--size` narrow that pool to one tree and one exact size. `--start` marks the item or the whole batch started, claiming as `--owner` when given; a failed batch start claims nothing. `--render` (only with `--start`; otherwise `usage`) renders `.slicer/render/` in the same step, as on the other mutating commands, and `--strict` rolls the claim back if the render fails. A pickup that changes nothing, such as resuming your own claim, does not render. Plain `next` and `next --start` without `--render` are unchanged. `--status` set to the review status draws from the review queue instead (reviewing items first, then review), and `--start` then moves the item to reviewing. Any other `--status` must name a custom status, one that is not the open, started, done, retired, parked, review or reviewing role; an unknown or role key is `usage` and lists the choices. A custom queue ranks the caller's own claims first, then unclaimed items, each by effective score; an item another owner claimed is skipped and reported (`claimed_elsewhere`: `{id, owner}`). `--start` there claims the item in place, leaving its status and `attempts` alone, and resuming your own claim writes nothing. `--start-to KEY` (only with `--start` and a custom `--status`) also moves it to any configured status except done, retired, parked, review or reviewing; moving to the started status begins one implementation attempt, and `--max-attempts` then applies. Once the status changes, read-only `next --status` for the old queue no longer finds the item. `--batch` cannot be combined with `--show`, `-n`, or `--status`. `--disjoint` (only with `--batch`, and only when `files_section` is configured; otherwise `usage`) also skips an item whose backticked paths in that section are already named by a chosen item (paths in `overlap_ignore` never count), and skips an item whose section names no path, listing it under `unknown_files`. An unmet dependency already in the disjoint batch still blocks its dependent, reported in `blocked`. It warns on stderr when no item has the configured heading. `--show` adds the full item and its slice. `--ready` returns item identity, the slice, and blocked ids, and sorts items whose `ready` is `true` (see `ready`) ahead of the others in the same group: the same tier (started before open, as always) and the same declared pass. It never moves an item across passes or ahead of another pass's items, so a ready item in a later pass still comes after the unready items of an earlier pass; items in no pass are reordered only among themselves. It never drops an item for lacking a record, and a stale record counts as not ready. `-n` and `--batch` use that order. A custom `--status` queue has no passes: claimed items still come first, and ready ones lead inside each side. Repeat `--section` with `--ready` to return the scope boundary and those sections only. An item a sibling has claimed or in work, or in review or done while this checkout's status differs and is not done, is skipped and reported (`in_work_elsewhere`: `{id, worktree, owner, status}`), unless this checkout has it started or claimed too. The same status on both sides is not reported |
| `plan-parallel [--width N] [--tree TREE] [--size SIZE] [--max-attempts N] [--flag PATTERN ...] [--no-flag PATTERN ...]` | the whole parallel path, where `next --batch K --disjoint` gives only the first step: waves of at most N items (default 4) that can run side by side. Started items are in flight; their `files_section` paths hold wave 1 only. Each wave fills greedily in `next` order with items whose pending dependencies are done or in an earlier wave and whose paths (less `overlap_ignore`) miss every path already taken in that wave; the next wave starts with the paths released, and the plan stops when nothing more can be placed. Each item placed after the first pass names why it waited (every item after wave 1, and a wave-1 item that in-flight paths held back from the first pass): `after: ID` (a dependency in the wave before), `shares: ID PATH` (a path that wave held) or `width` (the wave was full). Text is one block per wave, `ID  short title  reason`. An item whose section names no path is listed under `unplaced` with reason `no_files`; one still waiting on a dependency that is never placed is `blocked`. Held, unspecified, capped and sibling-held items are reported as `next` reports them. Needs a configured `files_section`, else `usage`; warns on stderr when no item has that heading. An empty plan exits 2. Does not lock, claim, start or write |
| `next-id` | the id the next `add` or `import` would take, without allocating it. When a sibling git worktree's `next_id` is higher than this checkout's, the id reported starts above it, so two worktrees do not file different items under the same id. |
| `id-prefix [NEW] [--dry-run]` | print the id prefix, or change its case for new ids (`S` to `s`), in the index and config together. Existing ids keep theirs and no file is renamed. Any change other than case is refused (`usage`), and `--dry-run` reports without writing |
| `list [-r] [--all] [--envelope] [--status/--tree/--pass/--flag/--discovered-from/--parent] [--in-work] [--review] [--held] [--sort score\|effort]` | the queue grouped by readiness: unblocked in-work items, then unblocked open items, then other visible rows, with parked items last. Within each group, declared pass order comes first, then descending effective score, then stored queue order for ties. Empty passes merge into that order by effective score; named undeclared passes follow declared passes; without declared passes, the existing score order applies. Pass labels are not parsed as versions: declare earlier releases before later releases and put a deferred pass last. This applies to text and JSON output and filtered views. Explicit score/effort sorts override this default; `next` uses the same ranking for its eligible pool. The text table has a CLAIM column: the local owner, `*` for locally in-progress with no claim, `wt:NAME` for work in a sibling worktree, or `-`. `wt:NAME+N` means N more worktrees. When the roadmap uses passes, a PASS column shows each row's pass key, or `-`. `--json` includes `claim` (`{"owner", "at"}` or null) and `in_work_elsewhere` (an array of `{worktree, owner, status}`, the sibling's status key). A sibling counts when the item is claimed or in work, and when it is review or done while this checkout's status differs and is not done. The same status on both sides is omitted, as is an empty `review_status`. Text ends with an `Only in a sibling` block (`ID  worktree  title`) when a sibling has an open, unclaimed item whose id this checkout lacks; `--json` stays a bare array by default. Opt in with `--json --envelope` to receive `{items: [...], only_in_sibling: [...]}`. Both keys stay present even when empty, including under `--lean`. Filters and sorts apply to `items` exactly as in the array; sibling-only rows match `status --json` and are independent of those filters. `--envelope` without `--json` is `usage`. Done and retired items are omitted unless `--all` is set or `--status` names them. `--in-work` keeps items whose status is in `config.in_work()` (started, plus reviewing when set), including a started item with no claim. `--review` keeps stored review items and open items with a sibling in review, matching the configured review key even when its label is not the word review. `--status` naming that key includes the same rows; the open filter also keeps the open rows. Either flag replaces the default omission of done and retired. Combining either with the other, with `--status`, or with `--all` is `usage`, as is an empty role. A role that matches nothing prints `no matching items`, exits 0, and does not print the hidden-count line. Repeat `--flag` to keep an item that has any of those flags. Flags are free-form labels set with `set --flag`. `--sort score` is a flat score sort. `--sort effort` orders estimates 1–3 and puts unset items last, without writing state. `-r`/`--recursive` reports on every project at or below the start directory (`--root` or the working directory; it never walks up) instead of one. It reads only and takes no lock. Text prints a `== PATH ==` header per project (`.` for the start directory) and a blank line between them; `--json` returns `{"projects": [{"path", "exit", "result"}]}`, with `error` in place of `result` for a project that fails to load. See [agents.md](agents.md#every-project-under-a-directory--r). Run from a directory above two projects, `list -r` prints each project's own queue. The projects are found by a walk down that skips dot-directories below the start (the start itself is always walked), `node_modules` and symlinked directories, and keeps descending inside a project, so a project nested in a project is reported too. A directory it cannot list or examine gets its own entry with exit 3 and code `io`, and no command runs there. The exit is the highest any project returned (`check -r` is 1 when any drifts, 3 when any is corrupt or locked); no project found is exit 2 with code `no_project`. One failing project never stops the others. Each JSON row adds `ready` (`true`, `false` or `"stale"`; see `ready`) |
| `find PATTERN [--in FIELDS]` / `find --title-exact TITLE` | search items by text (id, title, findings and slice bodies by default); shows the matched field and a snippet. `--title-exact` instead returns every item, done or retired included, whose full stored title equals TITLE by plain string equality (case- and Unicode-normalization-sensitive; short titles are not compared), reporting `title` as the matched field; it cannot be combined with PATTERN or `--in`, and `find` needs exactly one of PATTERN or `--title-exact`. With `--json --lean`, each row is only `{id, title, status, has_slice, match}` |
| `deps [ID] [--format mermaid] [--overlap ID ID ...]` | dependencies: unblocked open items, or one item's waits-on/blocked-by/dependents; `--format mermaid` renders the graph. `--overlap A B [C ...]` prints the paths each pair shares in the `files_section`, minus `overlap_ignore` (`-` for none); it warns on stderr when no item has the configured heading. It needs two ids and a configured `files_section`, and cannot be combined with ID or `--format`. A dependency is met in `done_status` or any status in `satisfies_dependencies` |
| `show ID [--notes-kind KIND ...] [--min-trust TIER] [--notes-budget N] [--section NAME ...] [--context]` | print one slice or selected sections; `--context` adds title, dependencies, and scope boundary. An item with slivers lists them (id, status, title) after the slice. JSON adds `ready` (`true`, `false` or `"stale"`; see `ready`), and text adds a `Ready:` line once a check is recorded |
| `sections` | the configured section names, one per line, with `required` on the ones `next` treats as unspecified when empty. JSON is `{sections, required}`. Reads config only: it does not lock, write, or read the index |
| `config [KEY [VALUE \| --unset]] [--render [--strict] [--check]]` | the effective config, and the one way to change it. With no key, one `key  value` line per top-level key: lists are comma-separated and nested objects print as compact JSON. With `KEY`, the bare value, one element per line for a list; a settable key that is unset (`relay_contract`, `satisfies_dependencies`) reads `(unset)`, and `--json` gives `{key, value: null}`. A dotted `KEY` reaches into a nested object (`id.prefix`, `pointers.later`, `tui.columns`). An unknown key is `usage` and names the top-level keys. A read reads config only: it does not lock, write, or read the index, and `--render`, `--strict` and `--check` are `usage` on it. JSON is the whole config, or `{key, value}`. With `VALUE`, it patches only that key in `config.json` under the project lock (refused during a merge), reading `VALUE` as the key's type: a string literally, a whole number, `true` or `false`, and JSON for a list or an object (`sync.targets` is a JSON list). A write may reach one level into `id`, `pointers`, `sync` and `tui`, or a top-level key; `statuses` and `pointers.later` are set whole. Once the index has items, a write that would change the effective id width, `id` as a whole included, is refused with `state` (the index owns the scheme), as `init --force` refuses it. The patched config must load as `Config.load` would, or nothing is written: a value that does not fit the key, or a config `validate` rejects, is `bad_config_value` (exit 2, message from validation). An unknown key is `usage`, as are `version` and any write that would change the id prefix, `id` as a whole included (the message names `slicer id-prefix`). `--unset` removes the key so its default applies again; with a `VALUE`, or without a `KEY`, it is `usage`. A write that leaves the file unchanged logs nothing. Otherwise it logs one `config` entry named `config.KEY` with the old and new effective values. `--render` and `--strict` behave as for other mutations, and `--check` runs `check` after the render. JSON for a write is `{key, value, previous}` |
| `template list` / `template show NAME [--default]` / `template set NAME (--file FILE \| --stdin) [--render [--strict] [--check]]` / `template reset NAME [--render [--strict] [--check]]` | the way to read and change the render and relay brief templates `.slicer/templates/slice.md`, `roadmap.md`, `row.md` and `relay-brief.md`; any other `NAME` is `unknown_template`, and its message lists the valid names. `list` gives one line per name, `default`, `customized` or `missing`; a project created before `relay-brief.md` existed lists it `missing` and `show relay-brief.md` fails until `reset relay-brief.md` writes it, while `ai relay-brief` uses the built-in text meanwhile; JSON is a list of `{name, customized, missing}`, where a missing file counts as the default (`customized` false, `missing` true). `show` prints the project's file byte for byte, with no newline added, and `--default` prints the built-in template instead and needs no project; JSON is `{name, text}`. A `show` of a deleted file fails with the message that names `template reset`. `set` replaces the whole file with the exact bytes of FILE or of stdin (exactly one is required, else `usage`; a file that is not UTF-8 is `io`). Before writing it expands the text once against stand-in data, so a placeholder the renderer does not know is found even in a project with no slice or no item, and a row template is also rendered over the project's items, which finds a row whose cell count differs from the table header. A template that fails either check is `bad_template` (exit 2), the message names the template and the first problem, and nothing is written, with `--strict` or without. Only the built-in templates stand in for the other templates, so a project that lost one can still set the rest. `reset` writes the built-in text, and recreates a deleted file. Both take the project lock, are refused during a merge, and log one `template` entry named `template.NAME` with `from` and `to` each `default`, `custom` or `missing`. Text equal to what the file already holds changes nothing, logs nothing, renders nothing under `--render`, and reports `changed: false` (a stale `render/` is still regenerated first, as for any write; see [Git merge drivers](#git-merge-drivers)). A write accepts `--render`, `--strict` and `--check` as for other mutations; JSON for a write is `{name, changed, customized}`. `list` and `show` take no lock and read no index. The set of placeholders is not changed by any of this |
| `recommended-workflow` | print the generic recommended project workflow: pick up one slice, start it, do the work, check it, and finish the way the project is configured. The text is [`docs/generic-recommended-project-workflow.md`](generic-recommended-project-workflow.md), which an installed copy carries too. It does not read the project, lock, or write, and it does not warn when there is no project. JSON is `{"workflow": "..."}`. A change to the recommended workflow updates that file in the same change |
| `set ID [ID ...] --title/--short-title/--size/--tree/--findings/--status/--pass/--depends-on/--flag/--add-flag/--remove-flag/--no-flags/--group/--parent/--discovered-from/--importance/--urgency/--effort/--no-effort/--attempts` | change fields. `--flag` replaces the list. Repeatable `--add-flag` and `--remove-flag` edit it in place and cannot be combined with `--flag` or `--no-flags`. `--no-effort` clears an estimate. `--attempts` sets the implementation attempt count and must be an integer >= 0. `add` and `set` refuse an unknown, self, retired, or cycle-closing `--depends-on` and write nothing. `--parent ID` marks the item a sliver of ID; `--parent ''` clears it (see [Slivers](#slivers)). `--discovered-from ID` sets the source item, as `add --discovered-from` does, and `--discovered-from ''` clears it; an unknown ID refuses with `no_such_item` and writes nothing. `--pass KEY` that no `prose add-pass` declared gets one stderr line per key, however many ids it names; `--pass ''` is silent. |
| `edit ID (--section NAME / --boundary / --lead) [--text/--file/--stdin]` | edit a section, scope boundary or lead; sections also accept `--append`. `--lead` replaces all of the lead: the body splits into paragraphs on blank lines, the way `promote` splits a lead, and an empty body clears it. `--lead` cannot combine with `--section`, `--boundary` or `--append` |
| `refs [ID ...] [--fix] [--render [--strict]]` | check the `path:N` and `path:A-B` references in slice text, each optionally `path:~N`, and say which still hold. A reference's anchor is the nearest backticked identifier before it on the same line, for example `` `OpenAsync` (`Open.cs:60`) ``. An anchor belongs only to the first reference after it: a later reference on the line with no identifier of its own between it and the previous reference is `unanchored`. A qualified anchor such as `store.discover_all` is searched by its last component. A path resolves relative to the project root, else by unique basename (the walk skips dot-directories, `node_modules` and symlinks). A path needs a file extension, and a bare name with several dots must end in a known one, so `api.example.com:443` is not a reference; neither is a match followed by another `:NN`, as in `config.yaml:12:30`. The status of each reference is `ok` (the anchor is on the cited line, or anywhere inside a cited range), `unanchored` (no anchor, in range), `missing_file`, `ambiguous_path`, `out_of_range` (also when a range, shifted to follow its anchor, would run past the end of the file), `moved` (the anchor is not on the cited line or inside the range, and occurs once as a whole word or exactly one occurrence is nearest the first cited line), `ambiguous_symbol` or `symbol_gone`. It scans the lead, the boundary, every section and the item's notes, except examples: a fenced code block is skipped (it opens on a line of three or more backticks or tildes, up to three spaces in, whose backtick info string holds no backtick, and closes on a line of the same character at least as long with no text after the run; a fence that never closes runs to the end of its text), and so is every line containing the exact marker `<!-- example -->`. Each lead paragraph (a blank line ends one), the boundary, each section and each note is scanned on its own, so no fence carries past its text. Skipped lines still count toward line numbers. With no IDs it scans items that have a slice and are neither done nor retired; a named ID is scanned whatever its status, and an item with no slice has only its notes scanned. Without `--fix` it is a read: no lock and no write. `--fix` takes the writer lock and rewrites only `moved` references, changing only the numbers: a `~` and the width of a range stay, and a range shifts by the same amount as its first line. Each rewritten text is saved through `edit`'s ops and logs the same `edit` history entry (`lead`, `boundary` or the heading). Notes are reported and never rewritten, nor is a section that repeats an earlier heading. `--render` and `--strict` apply only with `--fix`. Exit 0 when every reference is `ok` or an in-range `unanchored`; exit 1 (drift) for anything else, after `--fix` counting what is still wrong; exit 2 for an unknown id (`no_such_item`) or `--render`/`--strict` without `--fix` (`usage`). `check` does not look at references |
| `decide ID [--text TEXT/--file PATH/--stdin] [--render]` | record an owner answer as a built-in `decision` note and remove all configured hold flags. Exactly one body source is required; a blank answer or an item with no hold flag is `usage`. Works even when `note_kinds` excludes `decision`; supports `--strict` |
| `ready ID --by NAME [--note TEXT]` | record that a spec check passed: who ran it, when, an optional one-line note, and a digest of the slice as it is now (title, lead, scope boundary and every section, in order). Running it again refreshes the record. An item with no slice is `no_slice`, an unknown ID `no_such_item`, a missing or blank `--by` `usage`, and a newline in `--by` or `--note` `newline_in_field`; nothing is written on a refusal. It logs one `ready` history entry with `by` and the note. `show --json` and `list --json` report `ready`: `true` while the slice still matches the digest, `"stale"` once any part of it changed (a section, the lead, the boundary or the title; editing it back to the recorded text makes it `true` again), `false` when no check was recorded. Notes, size, pass, effort, trees, flags and dependencies are not part of the spec and leave it `true`. The record itself is `readiness` (`by`, `at`, `note`, `digest`), present only once recorded. `show` text adds a `Ready:` line once a check is recorded. It is a record, not a gate: `start`, `next` and `handoff` do not refuse an item for lacking one, and the render does not change. Open decision-log records that cite the slice are not read |
| `note ID [--kind KIND] [--owner ACTOR] [--stale-after TIMESTAMP] [--text/--file/--stdin] [--render]` | append a dated note to any item — no slice needed (shows in `show`/`render`, unlike `done --note`) |
| `feedback [--kind KIND [--item ID] (--text/--file/--stdin)] [--out PATH [--force]]` | append a UTC-stamped friction, bug, or feature note to the gitignored `.slicer/feedback.md`; no flag prints the log; `--out` copies its exact bytes. `KIND` is `friction`, `bug`, or `feature`; `--item` is stored as written and never looked up. Not a roadmap write, so no `--render` |
| `feedback-report (--dry-run/--yes) [--repo OWNER/REPO] [--kind KIND]... [--item ID]` | file each unreported entry of `.slicer/feedback.md` as one GitHub issue with `gh issue create`. `--dry-run` prints the issues and calls nothing; `--yes` files them, and exactly one of the two is required. The destination is `--repo`, else config `issues_repo`; neither is `usage`, and nothing is inferred from a git remote. `--kind` is repeatable and defaults to `bug` and `feature`, so friction stays local unless asked for; `--item` keeps entries stored with exactly that string. The title is `[kind] first line`, at most 120 characters. Filed entries are recorded by hash and repo in the gitignored `.slicer/feedback-reported.json` and skipped next time; the log itself is never rewritten. A missing or failing `gh` is code `external` (exit 2). Not a roadmap write, so no `--render` |
| `issues-pull [--repo OWNER/REPO] [--limit N] [--dry-run] [--render [--strict]]` | file each open GitHub issue as one open roadmap row with no slice, through `gh issue list --repo OWNER/REPO --state open --limit N --json number,title,url`. The issue body is never requested. Each row's filing key is `github:OWNER/REPO#NUMBER` and its findings is the issue URL; it lands unscored (importance 2, urgency 2, no effort) with an empty pass, in ascending issue number. A rerun reuses a row with that key in any status and does not refresh its title; when nothing is new, nothing is written or rendered. The source is `--repo`, else config `issues_repo`; neither is `usage`. `--limit` defaults to 30 and must be 1-100; no further pages are read. `--dry-run` still calls `gh` but writes nothing and takes no lock. Every issue is checked before any row is added: a blank title is `blank_title`, a title with a newline is `newline_in_field`, and a number that is not a positive integer, a URL that is not this repo's issue URL, or a repeated number is `external`; nothing is written. `.slicer/lock` is not held while `gh` runs. A missing or failing `gh` is `external` (exit 2). `--render` and `--strict` behave as for `add` |
| `note-verify ID --note-id NOTE [--owner ACTOR]` | verify an item note as a machine actor; supports `--render` and `--strict` |
| `note-attest ID --note-id NOTE --owner NAME` | attest an item note as `human:NAME`; a workflow convention, not caller authentication; supports `--render` and `--strict` |
| `note-retract ID --note-id NOTE --reason TEXT [--owner ACTOR]` | retract an item note as wrong without deleting it: its text stays and the record gains `retracted_by`, `retracted_at` and `retracted_reason`. The render shows `[retracted]`, the TUI dims the note, `next --ready` note packing omits it with reason `retracted`, and it no longer satisfies `handoff_requires_note_kind`. An unknown note, a blank or multi-line reason, and an already-retracted note are `usage` and write nothing. Logs a `note-retract` line; supports `--render` and `--strict` |
| `prose list / show REF / edit REF` | read and edit the roadmap's own prose |
| `prose add-pass KEY / drop-pass KEY` | open or close a pass group; `add-pass` reports how many existing items already use KEY |
| `goals [--retired]` | print the project's goals and non-goals prose together, plus active goal and non-goal records; `--retired` includes retired ones. JSON keeps `goals` and `non_goals` and adds `records.goal` and `records.non_goal` |
| `catalog` | add, list, show, edit, retire, and move project-knowledge records (goals, non-goals, requirements, constraints, decisions, assumptions). See [Catalog](#catalog) |
| `relay-roles show` | print the slice-relay role matrix as Markdown, with row ids in an `ID` column and rule ids before each rule, the same bytes as `.slicer/render/RELAY-ROLES.md` without its banner. Needs a project. Without `.slicer/relay-roles.json` it exits 2 with code `no_relay_roles`. Does not lock or write. See [Relay roles](#relay-roles) |
| `relay-roles recommend ID --well-specified yes\|no --split yes\|no [--prefer-agent AGENT] [--select ROLE=ROW_ID]...` | for each role in order (implementer, reviewer, gate, architect, orchestrator), print the selected row, or `(no matching row)`. The row is the explicit `--select` for the role when given; otherwise the first matching row whose agent equals `--prefer-agent` (compared trimmed, ignoring case); otherwise the first row, in matrix order, whose every condition matches the slice. `--select` is repeatable, once per role, and must name a row of that role that matches the slice; both flags are optional, apply to this call only, and the harness you launched from has no effect. One line per role: role, row id, agent, vendor, model, reasoning, and the reason in brackets (`override`, `preferred_agent` or `matrix_order`); every other matching row follows indented as `alternative` with the same cells. A nonempty list condition must contain the slice's size, effort, importance or urgency; an empty one matches anything, and an unset size or effort matches only an empty condition. A row's `well_specified` and `split` are yes, no or any, and `any` matches both answers. You judge both answers, so both flags are required; slicer does not work them out from the slice text. A row's `sliver` condition needs no flag: slicer reads it from the slice, which is a sliver when it has a `parent`. The JSON payload names the value it used. An unknown ID exits 2 with code `no_such_item`, a project without a matrix with `no_relay_roles`, a value other than yes or no, a malformed selector, an unknown role, two `--select` for one role, a row of another role, a row that does not match the slice or a blank `--prefer-agent` with `bad_role_field` (the message names the rows that do match), and a `--select` row id the matrix lacks with `unknown_role_row`. Does not lock or write, launches nothing and stores nothing. `--json` returns `{"id", "well_specified", "split", "sliver", "roles": [{"role", "row", "alternatives", "selected_by"}], "unmatched": [...]}` (`unmatched` lists every role whose `row` is `null`), and `--lean` keeps that full shape, null rows and empty `alternatives` included, and only makes the output compact |
| `relay-roles init [--force] [--replace-legacy]` | write the example matrix to `.slicer/relay-roles.json`: eleven rows (implementer for a sliver first, then for a whole slice three times, for a split part and as an alternative `r9`, reviewer, gate, architect, then the orchestrator twice (`r10`, `r11`); the sliver row is `r8` so the earlier ids keep their meaning), all on the agent `Claude Code` except `r9` and `r11` on `Codex`, and eight rules: the split rule (parts have disjoint files and their own tests; a slice too big for one implementer is two slices or a slivered slice), then three sliver rules (sliver only at effort 1, or at effort 2 when every sliver is effort 1; at most 3 slivers in parallel; a sliver that fails its gate twice moves to the next model up). An existing file exits 2 with code `state` unless `--force` replaces it; `--force` numbers the new example rows and rules after every id the old matrix issued (so it never reissues one, even for a row removed earlier), and a corrupt file starts again at `r1` and `u1`. `--replace-legacy` also removes a leftover `.slicer/relay-roles.md`, only after the JSON is written; when the JSON already exists it removes just that file and keeps the matrix (and with no leftover file it is the `state` error). A path that is not a file is `io`. Takes the project lock; honours `--render` and `--strict` |
| `relay-roles add --role ROLE [--size S,M] [--effort 1,2] [--importance 1-3,...] [--urgency 1-3,...] [--well-specified yes\|no\|any] [--split yes\|no\|any] [--sliver yes\|no\|any] [--agent A] [--vendor V] [--model M] [--reasoning R] [--note N]` | append a row with the next id (`r12`, ... in a fresh example; an id is never reused). `ROLE` is implementer, reviewer, gate, architect or orchestrator. The conditions say which slices the row applies to; an empty one matches anything. A bad value exits 2 with code `bad_role_field`. Prints the row |
| `relay-roles set ID [--role ROLE] [--size ...] [--effort ...] [--importance ...] [--urgency ...] [--well-specified ...] [--split ...] [--sliver ...] [--agent ...] [--vendor ...] [--model ...] [--reasoning ...] [--note ...]` | change only the fields given. For a condition, an empty value or `any` clears it. An unknown ID exits 2 with code `unknown_role_row`. A call that changes nothing writes nothing (a stale `render/` is still regenerated first, as for any write; see [Git merge drivers](#git-merge-drivers)) |
| `relay-roles remove ID` | drop a row. The id is not reused |
| `relay-roles move ID (--before ID \| --last)` | reorder a row. Choose exactly one placement |
| `relay-roles rule-add TEXT` | append a rule with the next id (`u9` in a fresh example matrix, ...). Blank text exits 2 with `blank_title`, text with a newline with `newline_in_field` |
| `relay-roles rule-edit ID TEXT` | replace a rule's text. An unknown ID exits 2 with code `unknown_role_rule` |
| `relay-roles rule-remove ID` | drop a rule |
| `relay-run begin ID [ID ...] [--worktree PATH]` | start a slice-relay run on these items and print its id (`run-YYYYMMDD-HHMMSS-xxxx`). `--worktree` is recorded by its directory name; the default is the checkout whose ledger the run goes in. Appends to the local ledger only, under the project lock; no roadmap state, history or render changes. A blank item, or one with whitespace, exits 2 with code `bad_run_field`. See [Relay run ledger](#relay-run-ledger) |
| `relay-run step RUN --role R --agent A [--vendor V] [--model M] [--reasoning E] --phase start\|done\|fail\|wait [--waiting-on owner\|approval] [--reason TEXT] [--task TEXT] [--parent STEP] [--step STEP]` | record an agent's step in a run and print its step id (`1`, `2`, ...; a step the fallback records in the checkout's own file gets a prefixed id, see [Sandboxes](#sandboxes)). A new step needs `--role` and `--agent`. `--role` is one of implementer, reviewer, gate, architect, orchestrator, `ui` (the UI pass) or `spec-check` (the spec check); the last two have no matrix row, and `relay-roles` refuses them. `--parent` nests a subagent under the step that spawned it, and counts it as a subagent. `--step STEP` updates a step already in the run instead (for example `--step 1 --phase done`): only the fields given change, and `--parent` is refused there. A `--task` on an update must match the task the step was recorded with, except that a step recorded without one can be given its first; a different one is refused, and a new task needs a new step without `--step`. An unknown `RUN` exits 2 with code `unknown_run`, an ended one with `run_ended`; a phase outside the four, a `--waiting-on` other than `owner` or `approval`, a role outside those seven, a role or agent missing on a new step, a `--parent` or `--step` not in the run, a `--task` on an update that differs from the task its step was recorded with, or a value with a newline is `bad_run_field`. Nothing is appended when it refuses. `relay-run step RUN --step N --phase start` re-records a step that is still running and acts as a heartbeat, so a step that takes longer than 30 minutes does not make its run go stale. A heartbeat passes no `--task`, so the step keeps the task it was recorded with. `--phase wait` says the step waits on the owner, for an approval or an answer: `--waiting-on` names which (`owner`, the default, or `approval`), and a run with a waiting step is never stale, however long it waits. Any later `--phase start`, `done` or `fail` on that step ends the wait; the run's 30 minutes then count from that event. `--waiting-on` with another phase exits 2 with code `usage`, and writes nothing. `--reason TEXT` goes with `--phase fail` and says why the step failed, such as a classified vendor limit (a rate limit or out of credits), after which the relay reruns the round on the role's next matching row; `relay-run list` shows it after the step and `--json` adds `reason` as the last key of every step (`""` when none); `--reason` with another phase exits 2 with code `usage`, and writes nothing. See [Waiting](#waiting-on-the-owner) |
| `relay-run ask RUN --step STEP --title TITLE --option LABEL:SUMMARY --option LABEL:SUMMARY [--option ...] [--recommend LABEL] [--blocker] [--key AREA.TOPIC] [--reasoning TEXT]` | ask the owner a question on a step of the run, and print its id (`q1`, `q2`, ...). With `--blocker` the step goes into the owner wait (phase `wait`, `waiting_on` `owner`), so the run is never stale while it waits, and the orchestrator ends its turn with `waiting on: owner QUESTION_ID`. Without it the question is only recorded: the step keeps running, nothing waits, and the orchestrator goes on. The fields are those of the run report's `owner needed` line, in its order: 2 to 4 options with distinct labels (the label ends at the first `:`), `--recommend` naming one of them, `--blocker` when the slice cannot go on without the answer, an `AREA.TOPIC` key without whitespace, and `--reasoning`. No value but `--reasoning` holds a `|`, no label or summary holds a `;` (the run report's value rule), and every value is one line. `--step` must be a step of the run that is still running or waiting on the owner (a `--blocker` question on a step waiting on an `approval` is `bad_run_field`: end that wait first, while a question without `--blocker` leaves that wait as it is); a `--blocker` question on a step already waiting on the owner with an unanswered blocker is `question_open`, and any number of questions without `--blocker` may be open at once. Any other refusal of a value is `bad_run_field`, an unknown run `unknown_run`, an ended one `run_ended`; nothing is appended when it refuses. See [Asking the owner](#asking-the-owner) |
| `relay-run answer RUN QUESTION_ID (--option LABEL \| --text TEXT) [--why TEXT] [--by NAME]` | record the owner's answer: one of the question's options, or free text, never both (`usage`, exit 2). The first answer stands: an answered question is `question_answered`, an id the run never asked is `unknown_question`, an `--option` that is not one of its labels or a blank `--text` is `bad_run_field`, an ended run `run_ended`. The answer to a `--blocker` question ends the step's owner wait (a step waiting on an `approval`, or on another unanswered blocker, keeps that wait, and a step already done or failed keeps its phase), and the run's 30 minutes count from it. The answer to a question asked without `--blocker` records the answer and leaves the step's phase and wait alone. Prints the question id. `relay-run answer RUN QUESTION_ID --get` reads the question instead, with no lock and no write, on a run that has ended too, and takes no other flag (`usage`): the text is the chosen label or the free text, then `why: ...` when there is one, or `unanswered`; `--json` gives `{run, step, question}`. A `--text` or `--why` may span lines: `--get` prints every line of it, and `--json` keeps the newlines. See [Asking the owner](#asking-the-owner) |
| `relay-run end RUN --outcome pass\|fail\|stopped [--close-steps done\|fail] [--reason TEXT]` | close a run. The same refusals as `step`: `unknown_run`, `run_ended`, and `bad_run_field` for another outcome or another `--close-steps` phase. `--reason TEXT` goes with `--close-steps fail`: each step it closes gets that reason, as `relay-run step --phase fail --reason` gives one step, and `relay-run list` shows it; with `--close-steps done` or none, or with a newline in it, it is `bad_run_field` and writes nothing. The run and its end event carry no reason. `--close-steps done` (or `fail`) first marks every step still running or waiting with that phase, one step event each that names only the phase (and the reason, when given), then writes the end event, all under the one ledger lock; the text then ends `; closed steps: 1, 4`. A write that fails stops before the end event, so the run is not ended. Without it, a run that ends with steps still running or waiting prints `slicer: ended RUN with running steps: 1, 4` on stderr and leaves them as they are. Steps of other runs are never touched. When the shared ledger is writable it first copies the run's events from the local ledgers into it and its text then reads `ended RUN OUTCOME; absorbed N events from local ledgers` (see [Sandboxes](#sandboxes)); a write the sandbox refuses goes to this checkout's `.slicer/relay-runs.jsonl` with one stderr line |
| `relay-run list [RUN] [--active]` | the runs in the shared ledger and in this checkout's and each sibling worktree's own `.slicer/relay-runs.jsonl`, folded together, oldest first. `RUN` keeps only that run; an unknown id exits 2 with code `unknown_run`, naming the ledger paths searched. A run is `active`, `stale` (no event for more than 30 minutes, not ended and with no step waiting) or `ended`; `--active` keeps only the first, so it keeps a waiting run; with `RUN`, a known ended or stale run gives an empty list and exit 0. Reads only: no lock, no write. A damaged shared ledger here is exit 3 with code `corrupt` naming the file and line; a damaged local `.slicer/relay-runs.jsonl`, this checkout's or a sibling's, is skipped. A last line with no newline yet is an append in progress and is ignored. `--json` returns `{"runs": [...]}`, and `--lean` only compacts it (empty fields stay). In the text a waiting step's phase reads `wait:OWNER`, then how long it has waited (see [Waiting on the owner](#waiting-on-the-owner)) |
| `start ID [ID ...] [--note TEXT] [--owner NAME]` | mark an item in progress and claim it (owner and time). The owner is `--owner`, otherwise `SLICER_CLAIM_OWNER`, otherwise `claim_owner` in config, otherwise the git user name, otherwise the worktree name; a blank value falls through and one with a newline is `usage`. A second start does not refresh the claim. The owner is also recorded as `by` on the history entry |
| `release ID [ID ...] [--owner NAME]` | clear a claim without changing status; `--owner` names who released it in history (`by`). An item that was in progress stays in progress and lists as `*` |
| `handoff ID [ID ...] [--note TEXT] [--owner NAME] [--render] [--strict] [--check]` | hand a started slice to review (`--owner` names who handed it off, as `by` in history): status becomes `review_status` and the claim is cleared. `next` skips review items and dependents stay blocked until `done`; a reviewer picks one up with `next --status review --start` (or `start ID`), which moves it to `reviewing_status` (also out of plain `next`). `handoff` refuses a reviewing item. `--check` with `--render` runs `slicer check` after the change lands: stdout stays the item, a failure exits 1 with the findings on stderr, and `--check` without `--render` is `usage` and writes nothing |
| `reject ID [ID ...] --note TEXT [--to STATUS] [--owner NAME]` | send a review back: the item must be in `review_status` or `reviewing_status`, and goes to the open status (or `--to` any configured status that is not done, retired, started, review or reviewing). The claim is cleared, and the required `--note` verdict is added to the item's notes and to a single `reject` history entry. Every id is checked first; a non-review item is refused (`state`) and nothing is written |
| `done ID [ID ...]` / `park ID [ID ...]` / `unpark ID [ID ...]` `[--note TEXT]` | change status; `--note` records a one-line *history* entry (for a durable note on the item, use `slicer note`); `done` moves the slice file in the worktree and leaves it unstaged, and clears a claim. It takes `--owner NAME` to name who finished it (`by` in history). `done` also takes `--check` with `--render`: after the change lands it runs `slicer check`, keeps the item on stdout, and exits 1 with the findings on stderr when the check fails. `--check` without `--render` is `usage` and writes nothing |
| `land ID --branch BRANCH --note TEXT [--owner NAME]` | land a merged slice on the branch that merged it. It refuses, before writing anything, unless the project is in a git repository and `BRANCH` is an exact short local branch name other than the checked-out branch (`invalid_branch`), when its tip is not reachable from `HEAD` (`not_merged`), or when a relay run that is neither ended nor stale names `ID` (`run_active`, naming the run). Then it runs `done ID --note TEXT --render` and `slicer check` and prints the git commands left for the person landing: `git -C ROOT add -A .slicer && git -C ROOT commit -m "ID done"`, `git -C ROOT worktree remove PATH` when another checkout has `BRANCH` checked out, and `git -C ROOT branch -d BRANCH`, with `ROOT` the absolute, shell-quoted project root. `--owner NAME` records the done transition as `done --owner` does. slicer runs none of them. One item only; slivers and a parent land with `done`. A failing check exits 1, after the output, as for `done --check` |
| `remove ID --reason "…"` | retire an obsolete item; the id stays claimed |
| `remove ID --purge` | delete outright, for something that never should have existed |
| `remove ID --purge/--reason --dry-run` | preview the removal and its fallout (dependents, id fate); write nothing |
| `remove ID ... --force` | retire or purge despite dependents, or a done item |
| `retire ID --reason "…"` | retire an obsolete item, exactly as `remove ID --reason` does: the same status, slice move, history entry, JSON payload, `--dry-run` preview and error codes (`command` is `retire` in an error envelope). `--force` overrides the same guards. It has no `--purge`; `remove ID --purge` deletes an item |
| `render` | regenerate `.slicer/render/` (ROADMAP.md, a browser-viewable ROADMAP.html, and one file per slice) |
| `sync [--check]` | rewrite derived lines in other documents |
| `verify` | check the index for consistency. Inside git it also warns (exit 0) when sibling worktrees lack the render, index or relay-roles merge driver (`render_driver_check`; the relay-roles one only in a project with a relay role matrix) and when a done item has no commit subject in the last 2000 non-merge commits (`git_check`). In any project it also warns (exit 0) for each undeclared pass key that holds an item not done or retired, as `check` does. Render freshness is `check`'s job. `verify --landed ID [--on REF]` is a different question and writes nothing: is the item's done state committed in REF's history (default `HEAD`)? It reads REF's index and done slice file with `git show`, so neither the working tree nor an unmerged branch can satisfy it. Exit 0 when landed. Exit 1 and a reason when not: `other_branch` (a branch, remote branch or tag outside REF's history has the item done right now), `working_tree_only` (only the working tree has it done), else `not_done`. Exit 2, with no report, for an unknown id (`no_such_item`), an unusable id (`bad_id`), a ref that names no commit (`bad_ref`), a project outside a git repository (`not_git`) or `--on` without `--landed` (`usage`). An index that cannot be read at REF is exit 3; on any other ref it is skipped. See [Troubleshooting](#troubleshooting) |
| `check [-r] [--diff]` | the CI gate: render staleness (each stale file's text line and `stale_render_details` entry name its cause, including a renderer-format mismatch between the file's `Render format: N.` header and the running slicer), sync drift, integrity, and unknown flags in the backtick `slicer ...` commands of live slices (commands inside fenced code blocks are literal text and are not checked). It also warns (exit 0) when a sibling git worktree has an item under the same id with a different title, which would collide on merge. Only an id that is new on both sides counts: an id already in the index at the two checkouts' merge base is one item that one side retitled, which merges cleanly. With no merge base or no readable index there, every differing id warns. The warning does not fail the gate. It also warns (exit 0) for each pass key that no `prose add-pass` declared and that holds an item not done or retired, naming the key, the count of such items and the `prose add-pass` command; a key whose items are all done or retired does not warn. It also warns (exit 0) when `.slicer/.gitignore` lacks a line `init` writes, naming the lines and `slicer setup-git --attributes` on main as the fix; a linked worktree's own writes put such a line in the repo's `info/exclude` instead (see Concurrency in the configuration guide), so only main can repair the tracked file, and the warning stays until it does. It also warns (exit 0) when the project's `relay_contract` pin differs from the relay contract this slicer serves, naming both versions; with no pin or an equal pin there is no such warning. A config that does not load exits 3 with code `config`, and that includes an unreadable `relay_contract` pin (outside 1 to 9999, or not a whole number): `check` does not salvage the pin the way `ai relay` does, so the gate fails rather than warns. `-r`/`--recursive` runs the gate in every project below the start directory and exits with the worst result; see `list` |
| `stats [-r]` / `log [--limit N] [--item ID] [--action A] [--by NAME] [--since TIMESTAMP] [--until TIMESTAMP]` | counts + completion % and per-tree progress (`-r`/`--recursive` gives one census per project, not a combined one; see `list`); history, newest first (`--limit` defaults to 20; `--item`/`--action`/`--by` scope it; `--since` is inclusive and `--until` exclusive, requiring timezone-aware ISO8601 timestamps; all filters apply before limit; `set` records old→new values; `by` is who started, claimed, released, handed off or finished an item) |
| `snapshot --json [--root PATH ...] [-r] [--parts items,goals,runs,status,health]` | compact dashboard reads for one or several projects in one process. Roots retain argument order; `-r`/`--recursive` walks each root with the same discovery rules as `list -r`. Each selected part has a content revision; each project has a revision over its part revisions. Defaults to all five parts. Read-only, without the writer lock. A failing project has an error and does not stop the others. JSON only; see [agents.md](agents.md#dashboard-snapshot) for the fixed schema |
| `status [-r]` | the front door: next item, progress census, and blockers in one view (`--json`). Text adds an `Only in a sibling` block, and JSON an `only_in_sibling` list, when a sibling worktree has an unclaimed open item this checkout lacks. `-r`/`--recursive` gives one such view per project; see `list` |
| `tui` / `ui` | browse, read, reorder and edit interactively (two names for the same command) |

For TUI keys, filters, the wizard, and display behavior, see the [TUI manual](tui.md).

History windows accept either bound or both, for example
`slicer log --since 2026-10-05T12:00:00Z --until 2026-10-05T14:00:00+02:00 --json`.
Bounds and matching history timestamps compare as instants, including explicit UTC
offsets and fractional seconds. Equal bounds give an empty result (exit 0).
Malformed or timezone-less bounds and `since > until` are `usage` (exit 2),
validated before history is read. A malformed or timezone-less stored timestamp
encountered after item/action/by filtering is `corrupt` (exit 3) when a time bound
is supplied. Original timestamp strings, existing newest-first ordering and
reverse append-order ties are preserved. Timestamp windows cannot define an exact
run boundary under clock skew.

`next --max-attempts N` accepts a positive integer and skips fresh implementation
pickups whose stored `attempts` is already N or higher. Started work can still
resume, including after release; review pickup is unaffected. This is a per-call
filter, applied before offset and batch slots, with identical read-only and
`--start` selection. Capped atomic starts load and select under the writer lock.
It does not limit direct `start ID` or set a project default.

Skipped candidates appear in queue order as `capped` entries:
`{id, attempts, max_attempts}`. Text prints each skipped item and its count/limit.
The field is omitted when empty, including in lean output. Capped items are
reported even when their dependencies are unmet. A batch cannot take a capped
blocker, so its dependents remain blocked. A wholly capped pool retains the
no-item exit code 2 and writes no item state or history.

`attempts` counts fresh implementation starts: `start` from open or a custom
queue into the configured started status increments it once. Repeated starts,
release followed by resume, review pickups, rejection, and `set --status` do
not increment it. A start → reject → restart repair records two attempts.
Stored historical values are retained and may overcount because older versions
also counted rejection. History is not authoritative enough to reconstruct
them; owners may correct a count with `slicer set ID --attempts N` before
adopting an attempt cap.

If `handoff_requires_note_kind` is nonempty, started-to-review handoff requires an
item note with that exact kind, nonempty text, and `attempt` equal to the item's
current `attempts`. File it with `slicer note ID --kind KIND --text "Describe the
verified outcome" --render`. Missing reports fail with code `state`, naming the item and
kind; every batch item is validated before any writes, including strict rendering.
Release and resume keep the report valid; reject then restart needs a new report.
Legacy unknown-attempt notes, wrong kinds, previous-attempt reports, and history-only
`handoff --note` do not qualify. An already unclaimed review item remains a no-op.
`done` is unaffected. Manually changing `attempts` changes report association.

Item notes keep their public `notes` array as display strings and add `note_records`
with `{id, kind, text, created_at, attempt, by, verified_by, verified_at, stale_after, retracted_by, retracted_at, retracted_reason, trust}`; `trust` is derived and never stored. `note ID --kind KIND` labels the dated
paragraph as `**DATE** — [KIND] text`; omitting `--kind` preserves the existing display.
`note_kinds` optionally restricts explicit kinds. New notes store a UUID, UTC creation
time, and the current implementation attempt. Editing preserves creation and authorship metadata but clears verification on text changes.
`note-retract` marks a note wrong: it keeps the text, sets `retracted_by`, `retracted_at`
and `retracted_reason` (one nonblank line), and the note stays in every read. A retracted
note renders as `[retracted]` before any kind label, is left out of `next --ready` note
packs with reason `retracted`, and does not satisfy `handoff_requires_note_kind`. No command
undoes a retraction.

Repeat `--notes-kind KIND` on `show ID` or `next --ready` (including batches) to select
the union of exact kinds in stored order. `--notes-kind ''` selects untyped notes,
including legacy slice notes. Filters change only output; an empty match still returns
the item and sections. On section-only `show`, a filter adds `notes`, `note_records`,
and `slice_notes` to the usual section payload. Ready pickups include item `notes`
and `note_records`; projected slices also include their legacy `notes`.
`next --notes-kind` without `--ready` is a usage error.

Opt into a current note pack with `show ID --min-trust machine-confirmed` or
`next --ready --notes-budget 2000`. `--min-trust` accepts `unverified`,
`machine-confirmed`, and `human-reviewed`; `--notes-budget` is a nonnegative
integer. Either option enables packing, with omitted trust defaulting to
`unverified` and omitted budget unlimited. Both require `--ready` on `next`,
including batches. Invalid options fail before `--start` claims anything.

Ordinary reads keep complete audit notes, including expired notes. A kind filter
alone preserves stored order and does not filter expiry. Packing combines the
union of requested kinds with minimum derived verification trust, excluding notes
whose `stale_after` is at or before one UTC read time captured for the invocation.
It ranks higher trust first, then newer creation times (unknown dates last), with
stored order breaking ties. Authorship alone does not confer trust.

Budgets cover whole display strings, counting Python Unicode characters and one
newline between selected notes, across both item and legacy slice notes. Oversized
notes are skipped so later fitting notes can still be selected. Metadata, manifests,
sections, and other output are outside this budget. Each batch item has its own
budget, sharing the invocation's read time. Legacy slice notes remain untyped and
unverified; their stable read-only manifest IDs are `slice-legacy-1`,
`slice-legacy-2`, etc., based on original order.

The requested `note_pack` accompanies item `notes` and `note_records`, including
section/context projections and each ready batch entry's `item`. It contains
`selected_ids` in selection order, `omitted` entries `{id, reason}` in original
order (item notes followed by legacy slice notes), `characters`, `budget` (null
for unlimited), and `min_trust`. Omission reasons have precedence `retracted`,
`kind`, `expired`, `trust`, `budget`. Lean output preserves the entire manifest, including
empty arrays. Selected legacy text stays in `slice.notes`, or `slice_notes` on
section-only show output. Text output also includes the manifest.

Packing never promotes verification, hides the item or its sections, or writes
state. Generated documents remain complete, deterministic, and independent of
wall-clock expiry.

## Flags every command takes

`--root DIR` names the project root; by default it is discovered from the working
directory.

Every command except the interactive `tui`/`ui` and the git merge drivers `merge-index` and `merge-relay-roles` takes `--json`, including the failures — an agent calls `slicer next
--json` rather than parsing markdown, and reads `{"error": {"code": ...}}` rather than
prose. Exit codes: `0` fine, `1` drift or a failed check, `2` usage, validation, or nothing to do, `3` internal or state (`corrupt`, `locked`, `io`, `config`, `schema_too_new`).
`--lean`, with `--json`, prints compact JSON on one line and omits empty fields, a repeated short title, and an item path.
See the [agent reference](agents.md) for every payload.

Every command that changes state — `add`, `set`, `start`, `release`, `done`, `handoff`, `reject`,
`decide`, `move`, `sort`, `promote`, `edit`, `note`, `remove`, `retire`, `park`, `unpark`, `import`,
`migrate`, and the `prose` edits — takes `--render`
to regenerate `.slicer/render/` in the same step, so a mutation and its render are one
command. `land` changes state too but takes no `--render`; it renders through `done` (see its row
in the command table). By default the change is saved first and rendered after; add `--strict` to require
the render to succeed first, so a change that cannot be rendered is rolled back rather than
landed (this is how `done --render` already behaves). The agent loop passes
`--render --strict` on `start` and on slice edits. `done` and `handoff` take
`--check` with `--render` to run `slicer check` after the change lands.

## Picking and listing work

Items carrying a configured `hold_flags` value (default `needs-owner`) keep their
status but stay out of `next` and `next --batch`. JSON includes `held` when
nonempty, with `{id, title, flags}` per held candidate; `flags` contains the
matching hold flags. Text adds a Held block. `status` reports the same candidates;
`list --held` filters its usual view by hold flags. `hold_flags: []` disables holding.
A dependency on a held item still blocks the dependent.

File an open owner decision with `slicer set ID --add-flag needs-owner`.
`slicer decide ID --text "The owner's answer"` appends a dated `decision` note
and removes every configured hold flag, preserving other flags and the item's
status. Use exactly one of `--text`, `--file PATH` or `--stdin`; blank answers
are refused. `--render` and `--strict` work as for other mutations. An item with
no hold flag gets `usage`, naming the configured flags. The built-in `decision`
kind is written even when `note_kinds` restricts explicit note kinds.

The sibling worktree signal comes from local `git worktree list` and each checkout's
`.slicer/index.json`. It sees worktrees on this machine only, not work on another machine.
A project that sits below the git worktree root does not read those checkouts.
A sibling row counts when that item is claimed or in work, and when its status is review
or done while this checkout's copy has a different status and is not done. An empty
`review_status` is not that extra case. Each JSON entry adds `status`, the sibling's
status key. Text keeps the CLAIM cell's existing precedence and `wt:NAME` signal.
For a stored-open item with a sibling reporting this checkout's configured review key,
the STATUS column in `list`, `find`, and unblocked `deps` shows this checkout's review
label. It stays open in state and JSON, and appears under both open and review filters
(`--review` or `--status REVIEW`). Other local and sibling statuses keep the stored
label; the in-work filter does not gain sibling-only work. The rendered roadmap does
not read siblings.

Canonical state remains git-tracked JSON in each checkout's `.slicer/`; coordination
happens through git. Commit tracking changes with the work before removing its worktree,
when the owner authorizes committing. No automatic handoff/done reminder is planned
([s175](../.slicer/render/slices/s175.md) was retired): `git worktree remove` refuses a
worktree with modified files unless forced, and `list` shows a sibling's review or done
work as `wt:NAME`. The sibling allocation floor and collision
warning shipped in [s185](../.slicer/render/slices/s185.md): `add`, `import`, and `next-id`
use the higher of this checkout's `next_id` and the highest matching-prefix `next_id`
visible in local sibling worktrees. This does not reserve ids across simultaneous
writers or other machines, or resolve merge
conflicts. Two branches that each file something still conflict on the single
`next_id` line of `index.json`; the `slicer-index` merge driver (`slicer setup-git`
prints its config, [s191](../.slicer/render/slices/s191.md)) resolves that line to the
larger value, and [s217](../.slicer/render/slices/s217.md) widens it to merge independent
items and fields; only a true disagreement is left as a conflict.

`slicer next -n 1` returns the item after the current next item. Offsets are
nonnegative integers: `-n 0` is the same as `next`. Eligible started items come
before eligible open items; each group uses descending effective priority with
roadmap order breaking ties. Skipping an item does not complete it or unblock its
dependents. The command returns one item, including rows without slice files;
an exhausted offset exits 2 (JSON returns `item: null` and blocked details).
The text form of `slicer list` labels its columns: number, id, status, claim,
pass (only when the roadmap declares or names a pass), size, effort, score,
quadrant, and title. `find` and `deps` rows use the same columns without the header. An unset
effort appears as `-`. A `!` right after the row number marks an item that waits on a
dependency not yet met (in `done_status` or `satisfies_dependencies`), as the TUI queue
does; it takes a space the row already has, so the columns do not move. `deps` lists only
unblocked items, so its rows never carry it. When the
default status filter hides done or retired items, a final line counts the
matching hidden rows and points to `--all`. `--all` and `--status` suppress that
notice; `--json` remains an array of the visible item records.
`slicer next --ready` is a bounded pickup of that same item: `id`, `title`,
`status`, `depends_on`, `effective_score`, `path`, the slice when the item has
one, and every blocked id. The agent loop uses
`slicer next --ready --section "Implement" --section "Check" --json --lean`.
The headings are examples; pass the section names the project configures.
Repeat `--section NAME` with `--ready` to keep the
scope boundary and those section bodies; omit it and the slice stays complete.
`--section` without `--ready` is a usage error. A row with no slice still says
to run `promote` on `next --ready` and `next --show`, and does not gain a show hint. When the item has a slice, text
for `next`, `next --ready`, `next --show`, and `next --batch` names
`slicer show ID` instead of printing the slice path. `--path` adds that path
line back and does not change JSON. `--json --lean` still drops `path` on an
item. An empty queue uses the same exit 2 result as `next`.
Pass either `--ready` or `--show`. The text form of `--ready` stays the
identity, the show hint, the boundary, and the section headings.
`slicer next --batch 3 --tree core --size S` returns up to three items from that
same pool: one tree, one exact size, in the same readiness, pass and score order
as default `list`. A dependent follows its dependency when the dependency
is in the batch, even when the dependent ranks higher. Items still waiting on
a dependency outside the batch are listed in `blocked`. Items omitted only
because the batch is full are not. JSON is `{"items": [...], "blocked": [...]}`,
plus `unspecified` and `in_work_elsewhere` only when those skips happened.
`--disjoint` takes the batch greedily in that order and drops any item whose
`files_section` paths meet a chosen item's, so the batch can run in parallel
worktrees; it adds `unknown_files` (ids) for items skipped because the section
names no path. With `--disjoint`, an unmet dependency remains a blocker even
when it is already in the batch: the dependent is left out and listed in `blocked`,
naming that dependency. Plain `--batch` still includes dependents after dependencies.
Both disjoint selection and `deps --overlap` warn on stderr when no item has the
configured `files_section` heading; JSON stdout stays unchanged. `slicer deps --overlap S01 S02` shows what two items share.
`slicer plan-parallel` carries that selection forward wave by wave; see its row.
With `--ready`, each element is the `{item, slice}` object one `--ready` call
returns, and `blocked` stays on the batch. `--section` trims every slice the
same way and is checked before `--start`. `--start` claims the whole batch
under one lock; `--owner` sets that claim. If starting fails, no id is claimed.
An empty batch exits 2 with `items` empty and the blocked list, and it does
not lock or start. `--batch` cannot be combined with `--show`, `-n`, or
`--status`, and K must be at least 1. `--tree` and `--size` without `--batch`
narrow a single `next`, including `-n`.

`next --flag PATTERN` includes items whose stored flags match any supplied
pattern; `--no-flag PATTERN` excludes items matching any supplied pattern and
wins over inclusion. Both are repeatable, case-sensitive shell-style patterns
(`*`, `?`, `[abc]`) interpreted by slicer. Quote wildcards to prevent shell
expansion, for example `slicer next --no-flag 'risk-*' --batch 3` or
`slicer next --flag 'area-*' --flag safe --no-flag 'risk-*'`.
An explicitly empty pattern is a usage error. Items without flags pass when
inclusion is absent and fail when inclusion is supplied. Omit both filters to
keep the existing pool. `list --flag` continues to match literal flag names.

Flag filters combine with tree, size, readiness, section selection, attempt
caps, the review queue and a custom status queue. They apply before offsets,
batch capacity and skip diagnostics, including blocked, unspecified, capped,
sibling-held items and claims held by someone else.
Dependency checks and effective scores still use the full index: excluding a
blocker leaves its dependent blocked, including in a batch. Reads and `--start`
use the same filtered pool.

## Priority

Items carry an Eisenhower-style priority: an `--importance` and an `--urgency` (each 1–3),
combined into a score of `importance × 10 + urgency` (importance leads). A blocker of a critical item inherits its
priority, so `slicer next` and `slicer list --sort score` surface the blockers of
important work first, while the stored queue order stays whatever `move` set.

## Filing work

Filing retries use `slicer add "Title" --key KEY`. Keys are case-sensitive,
trim surrounding whitespace, and cannot be explicitly blank. An existing key
returns its current item with `existing: true`, ignoring replacement fields
before state validation or ID allocation. A new item returns `existing: false`.
Done and retired items reserve their keys. A retry writes nothing itself and skips
optional rendering, even when output is stale or `--strict` is set; the stale-render heal
that precedes every write still runs (see [Git merge drivers](#git-merge-drivers)). Keys are
checkout-local and can also be supplied as `key:` in import outlines.

## Batch changes

Use multiple IDs to apply the same status, fields, or history note in one command:

```sh
slicer set S01 S02 --importance 3 --urgency 2 --render
slicer start S01 S02 --note "Starting related work" --render
slicer done S01 S02 --note "Implemented and verified" --render
```

`park` and `unpark` accept the same multiple-ID form. To read IDs from a file or
pipeline, use a standalone `-`:

```sh
printf 'S01\nS02\n' | slicer done - --note "Verified" --render
```

Stdin accepts whitespace-separated IDs, not JSON. Empty stdin and mixing `-` with
explicit IDs are errors. Repeated IDs are processed once, in their first-seen order.

The whole request is validated before any item changes. An unknown ID or invalid
field refuses the batch. The mutation loads state once and saves the index once;
`--render` reloads state for rendering. The command holds the existing writer lock.
Individual file writes remain atomic, but disk failures do not roll back the batch.

Status changes record one history entry per changed item; items already in the
requested status are unchanged. `set` retains its existing per-item history behavior.
Human output has one result line per item. With `--json`, one explicit ID returns
an object; multiple positional IDs or stdin return an array, even when duplicates
or stdin resolve to just one ID.

## Discovery provenance

`slicer add "Found work" --discovered-from S01` records the existing item that
discovered it in `fields.discovered_from`. `slicer set S02 --discovered-from S01`
sets it on an item already filed, and `--discovered-from ''` clears it. The source
may have any status. An unknown ID fails with `no_such_item` before allocation or
writes. This field
changes neither dependencies nor priority, and it leaves the configured status
default in effect.

`slicer list --discovered-from S01` filters by exact source ID and composes with
status, tree, pass, and flag filters. An unknown filter ID fails with
`no_such_item`; zero matching items succeeds. Default status exclusions still apply.

Rows include `discovered from S01` in findings when provenance is present.
Empty provenance leaves row output unchanged.

## Slivers

`slicer add "Part one" --parent S01` (or `slicer set S02 --parent S01`) records, in
`fields.parent`, the larger slice an item is a sliver of; `--parent ''` clears it. The
parent may have any status. It is one level deep: an item with a parent cannot be a
parent (`parent_nested`), and an item cannot be its own parent (`parent_self`). An
unknown ID fails with `no_such_item`. All of these write nothing. A parent is a
reference, not a dependency: it is never written into `depends_on` and changes no
score. It does hold the parent while a sliver is open. A sliver short of handed off,
being reviewed, done or retired counts as an unmet dependency of its parent wherever
readiness is computed: `next` (including `--batch`) offers the slivers and lists the
parent in `blocked`, `list` marks it `!`, and `deps S01` names the slivers
(`held by slivers`). When `implement_finish` is `done`, only a done or retired sliver
releases its parent in `next`, since that project has no handoff step. `slicer handoff S01`
refuses, with `slivers_open`, until every sliver is handed off (or being reviewed, done
or retired), whatever `implement_finish` is, and `slicer done S01` until every sliver is
done or retired; naming the slivers in the same call counts. There is no `--force`. A
retired sliver holds nothing. A sliver finishes as any item does.

`slicer list --parent S01` filters by exact parent and composes with the other
filters, as `--discovered-from` does. `slicer show S01` lists its slivers (id, status,
title), and the slice header of a sliver carries a `Parent: S01 title` line. Rows carry
`sliver of S01` in findings, and a custom row template can use `{{parent}}`.

## Git access

**slicer never commits, pushes or tags.** `git` access is allowlisted to
`rev-parse`, `status`, `log`, and the read-only queries
`worktree list --porcelain`, `branch --all`, `config --get` of `user.name`,
`merge.slicer-generated.driver`, `merge.slicer-index.driver` and
`merge.slicer-relay-roles.driver`, `merge-file -p`
(three-way merge of three files to stdout, used only by `merge-index`), `show COMMIT:./PATH`
(one file as a commit recorded it, no options) and `merge-base` with exactly
`COMMIT COMMIT`, no options (`log` and `show` are used by `verify --landed`; the
two-commit form and `show` by the sibling-collision warning in `check`). A mutation does not stage. `start` uses
the branch and worktree queries to warn when another checkout already refers to the slice;
the exit code does not change, and
`next` stays silent. Writing subcommands cannot be reached from the code at all.

## What lives in `.slicer/`

```
config.json          paths, statuses, fields, templates, sync targets
index.json           the ordered queue plus roadmap prose (preamble, goals, non-goals, epilogue)
slices/<ID>.json     one open slice: title, lead, sections (ordered list)
slices/done/<ID>.json    finished slices
slices/retired/<ID>.json obsolete slices, with the reason on the item
templates/*.md       render templates, changed with `slicer template`
relay-roles.json     the slice-relay role matrix, when the project keeps one (see Relay roles)
render/              GENERATED - ROADMAP.md, ROADMAP.html (browser-viewable), one file per slice, and RELAY-ROLES.md
log.jsonl            append-only history of status changes
relay-runs.jsonl     slice-relay ledger read in a git repo, and written there only by the sandbox fallback; the ledger itself for a project below the worktree root or outside git; gitignored (see Relay run ledger)
.gitattributes       union-merges log.jsonl, and keeps generated render/ on merge (see below)
```

Sections are an ordered **list**, not a map: real slices carry headings no schema names,
sometimes more than once, and their order is part of the document.

For merge behavior and setup, see [Git merge drivers](#git-merge-drivers).
For the separate scope boundary and its editing rules, see
[Writing a slice](#writing-a-slice).

## Removing an item

Removal is two different acts, so `remove` has two modes.

`remove ID --reason "superseded by S30"` **retires** it: the status becomes `retired`, the
slice moves to `.slicer/slices/retired/`, and the row keeps rendering with the reason
beside it. The id stays claimed. This is for something that existed and was cited — a
commit or a review that names it must still resolve to something that explains itself.

`remove ID --purge` **deletes** it: the item and its slice file go. This is for a mistyped
`add`. The id comes back only when it was the most recently allocated *and* no commit
subject mentions it — that is undoing an allocation, not reusing an identifier. Any
earlier id stays burned, and the output says which happened and why.

Purging also refuses when another item names it in `discovered_from`;
`--force` leaves a dangling source that `check` reports, and its ID is not reclaimed.
Retirement preserves source references.

Retiring or purging an item that still has slivers refuses too; `--force` overrides.
A purge that leaves a sliver naming it does not reclaim its ID, and `check` reports the
dangling parent.

Both refuse when another item depends on it, or when it is `done`; `--force` overrides and
names the rule it overrode. After a forced purge, `slicer check` reports the dangling
dependency it left behind. Add `--dry-run` to either mode to preview the outcome first — the
dependents that would dangle and whether a purge would free or burn the id — without writing;
it turns that surprise into a decision.

Retiring needs a status to move into. A tracking directory created before `remove` existed
gains a `retired` status automatically on load — only that one key, so a project that
dropped some other status does not get it back.

## Catalog

Project knowledge that is not a slice lives in `catalog` on the index: goals,
non-goals, requirements, constraints, decisions, and assumptions. Each record
has its own id (`C01`, prefix `C`), a title, one markdown body, and a status of
`active` or `retired`. The slice queue does not include them. `slicer list`,
`slicer next`, `slicer stats`, and `slicer deps` ignore them.

```
slicer catalog add --kind goal "Ship the editor" --body "One file, no service."
slicer catalog list [--kind non-goal] [--retired]
slicer catalog show C01
slicer catalog edit C01 --title "..." --body "..."
slicer catalog retire C01 --reason "..." [--successor C02]
slicer catalog move C03 --before C01
slicer catalog cite S01 C01 C02
slicer catalog uncite S01 C01
```

`--kind` accepts `non-goal` and stores `non_goal`. `add` and `edit` take the body
from `--body`, `--file`, or `--stdin`. `edit` with none of those, and no `--title`,
opens `$EDITOR` on the body. `list` shows active records in editorial order;
`--retired` appends retired records. `move` stays inside one kind. `--before` and
`--after` name another record of that kind.

Retire requires a nonempty reason. `--successor` names one later record of the
same kind. A different kind, an unknown id, or a cycle is refused and nothing is
written. A retired record's title, body, reason, and successor stay as they were.
There is no purge: the id is never reused. `slicer log --item C01` shows the
`catalog` history; the note is `add`, `edit`, `retire`, or `move`.

`slicer goals` still prints the prose blocks. It also lists goal and non-goal
records, active unless `--retired` is set. Requirements and the other kinds are
read with `slicer catalog`.

`slicer catalog cite ITEM C01 C02` appends those ids to the item. An id already
cited is skipped, and a command that changes nothing writes nothing. `slicer
catalog uncite ITEM C01` drops ids. An id the item does not cite fails, and
either command refuses the whole request before writing when any id is unknown.
`slicer show` resolves each citation to its id, kind, title, status, reason,
and successor. `slicer catalog show` includes `cited_by`, the citing item ids
in queue order, including done and retired items. History actions are `cite`
and `uncite` on the item id.

A slice header gains a `Cites:` line only when the list is nonempty. Each entry
is `C01 goal: title`. A retired assumption or decision adds `(retired)`. The
roadmap table gains no column. A row that cites a retired assumption or
decision appends `retired assumption C02` or `retired decision C04` the same
way `discovered from` is joined. Other retired kinds do not change the row.
`check` fails when a cite names nothing. `cites` stays an atomic list in the
index merge, like `depends_on`. An empty cite list leaves the roadmap and the
slice bytes unchanged.

The roadmap renders goal and non-goal records under the existing prose headings,
prose first, then active records, then retired ones. The other four kinds render
from `{{catalog}}` in the roadmap template, between non-goals and the summary.
An empty catalog leaves the roadmap bytes unchanged. A project created before
this template slot needs `{{catalog}}` in `.slicer/templates/roadmap.md` for
those four kinds to appear; goal and non-goal records render without it.

The index merge driver merges catalog records by id, the way it merges items,
and takes the larger `catalog.next_id`. Record order follows the base unless
exactly one side reordered it.

Index schema is 11: schema 8 added `catalog`, schema 9 added `fields.parent`, schema 10 the
item's `readiness` record and schema 11 note retraction fields. An index without `catalog`, `parent`,
`readiness` or the retraction fields still loads. The next save writes the object and the
new schema, which an older slicer refuses.

## Relay run ledger

A relay orchestrator records what a slice relay is doing with `slicer relay-run`, so that
`relay-run list` and the TUI can show which slices are being relayed and which agents are
running. In a git repo the records go to `<git common dir>/slicer/relay-runs.jsonl`
(`.git/slicer/relay-runs.jsonl` in a plain checkout), an append-only file with one JSON event
per line, with a lock file `relay-runs.lock` beside it. Every worktree of the repo shares
that one ledger, so `begin` in a slice worktree, `step` from another and `list` from main
all see the same runs, and `git worktree remove` does not delete them. Outside git, or for a
project below the worktree root, the ledger is `.slicer/relay-runs.jsonl` in the checkout,
as it was for every checkout before. Like `.slicer/feedback.md` it is untracked (the
checkout's file is gitignored: `init` and the first write add it to `.slicer/.gitignore`),
never rendered, never read by `check`, and not part of the roadmap state: deleting it loses
only the board. A run is monitor-only. Recording one launches nothing and does no git write.

```sh
RUN=$(slicer relay-run begin s282 --worktree .worktrees/s282)
STEP=$(slicer relay-run step "$RUN" --role implementer --agent "Claude Code" --vendor Anthropic --model sonnet --reasoning high --phase start --task "write the tests")
slicer relay-run step "$RUN" --role implementer --agent "Claude Code" --model haiku --phase start --parent "$STEP" --task "one part"
slicer relay-run step "$RUN" --step "$STEP" --phase done
slicer relay-run end "$RUN" --outcome pass --close-steps done   # also marks any step still running
slicer relay-run list --active
```

A run is its events folded together: its items, the worktree, when it began and last had an
event, and its steps in the order they were added, each with role, agent, vendor, model,
reasoning, phase, what it waits on, task, parent and times. The counts are all the steps, those still
`start`ed and those `wait`ing (neither is counted once the run has ended), the subagents (steps
with a parent) and the top-level steps per role (a subagent is counted under `subagents`, not
under its role). The state is computed when read: `ended` once the run has an outcome, `stale`
when it has had no event for more than 30 minutes (an orchestrator that died) and no step is
waiting, else `active`.

### Waiting on the owner

A run that waits for the owner's approval or answer is not dead, and a 30-minute clock would
call it stale. The orchestrator says so with `relay-run step RUN --step STEP --phase wait
[--waiting-on owner|approval]` (`owner` by default), and ends the wait with a later `--phase
start` (the heartbeat), `done` or `fail` of the same step. While any step of a run is waiting
the run is `active` however long that takes, `--active` keeps it, and `land` refuses it with
`run_active`, as it does any active run: end it with `relay-run end` first. After the wait
ends the clock counts from the event that ended it. `relay-run end` closes a waiting step as
it does a running one, with `--close-steps`, and names it on stderr without.

In `list --json` the step's `phase` is `wait` and its `waiting_on` names what it waits on (`""`
for any other phase), and `counts.waiting` counts the waiting steps, in addition to `running`,
which counts only `start`ed ones. In the text, a waiting run's line reads `active  waiting
owner` after the state, shows `0 running, 1 waiting`, and its step line shows the phase as
`wait:owner` and how long the wait has run as of now (`wait:owner 2h00m`).

`waiting_since` in the step JSON is the UTC stamp of the event that began the current wait. A
repeat of the same wait keeps it, a wait on another `waiting_on` starts a new one, and a
`start`, `done` or `fail` clears it to `""`. The board shows the same time as `waited Xm` on the
step's row. A heartbeat is a `--phase start`, so it ends a wait: do not heartbeat a step that
waits.

A wait is written to the ledger as a `start` step event with a `waiting_on` field, not as a
new phase. The ledger is shared by every worktree and every installed slicer, and an older
slicer refuses a phase it does not know as `corrupt`, which would stop it listing and writing.
It ignores the extra field instead, so it shows a waiting step as running and the run goes
stale after 30 minutes, as before. A newer slicer's `waiting_on` value that this one does not
know still counts as a wait.

### Asking the owner

An orchestrator that needs a decision it cannot go on without, a `blocker` owner question, asks
it in the ledger instead of only in its report, so the owner, the outer session or a program
reading `relay-run list --json` can answer it:

```sh
Q=$(slicer relay-run ask "$RUN" --step "$STEP" --title "Which store?" \
  --option file:"A JSON file" --option db:"A database" --recommend db --blocker \
  --key store.engine --reasoning "scales past one writer")
# the orchestrator ends its turn with:  waiting on: owner q1
slicer relay-run answer "$RUN" "$Q" --option db --why "scales" --by jon   # the outer session
slicer relay-run answer "$RUN" "$Q" --get                                # read it again
```

`ask --blocker` puts its step in the owner wait of [Waiting](#waiting-on-the-owner) and answering it ends
that wait, so the step is `start` again and the run's 30 minutes count from the answer: an
orchestrator that is never resumed shows as stale instead of as waiting for good. There is no
`--wait` and nothing to poll: the orchestrator ends its turn, and the outer session resumes it
with the answer pasted in. A step that moves on without an answer (a later `--phase start`, `done`
or `fail`) ends the wait too and leaves the question open.

A question that does not block is asked the same way without `--blocker`, as soon as it arises. It
records the question and leaves the step running, so the orchestrator does not end its turn or
wait. The owner, or a dashboard that reads `relay-run list --json`, may answer it with `relay-run
answer` while the run goes on, and the orchestrator reads it with `relay-run answer RUN QUESTION_ID
--get` before the closing note and the finish step. A question still open then goes on the run
report's owner-needed line. `relay-run list` shows only a step's latest question (the JSON
`question` and the text line below), so a dashboard with several non-blocker questions open reads
each one by its id with `relay-run answer RUN QUESTION_ID --get` (ids run `q1`, `q2`, ... in the
run); a `questions` list in the step JSON is filed for later.

In `list --json` a step's `question` is its latest question, or `null`: `{id, title, options,
recommend, blocker, key, reasoning, asked_at, answered_at, answer}`. `options` is a list of
`{label, summary}`; `recommend`, `key` and `reasoning` are `""` when not given. `asked_at` and
`answered_at` are the UTC stamps of the ask and the answer events; while the question is open
`answered_at` is `""` and `answer` is `null`, afterwards `answer` is `{option, text, why, by}`
with `option` the picked label and `text` `""`, or `text` the free text and `option` `""`. A
question is open while `answered_at` is `""`, whatever phase its step is in. Its id is `q` and a
number, one past the largest in the run; a question asked from the checkout's own file (see
[Sandboxes](#sandboxes)) carries that file's prefix like a step id, `cXXXXXXXX-q1`. The text list
shows a step's latest question on a line of its own under the step:
`      question q1 open  TITLE`, or `answered` once it is.

Both events are step events an older slicer folds as heartbeats. The ask is a `start` event with
`waiting_on` `owner` and a `question` object; the answer is a step event with no `waiting_on` and an
`answer` object naming the question (`{question, option, text, why, by}`). Neither adds an event
kind or a phase, so an older slicer reading the shared ledger lists it as before; it ignores
the objects and shows the step as running. A newer slicer's extra keys in either object are
ignored here. A `question` object without a string `id`, a string `title` and a list `options`,
or an `answer` object without a string `question`, is skipped (the event still folds as a
heartbeat) rather than refused, so one odd line cannot blank the board; an answer that names no
question this ledger holds, or one already answered, changes nothing.

A step is added to a run in the shared ledger, so in a git repo from any of its worktrees.
`list` also reads the `.slicer/relay-runs.jsonl` of this checkout and of each sibling
worktree as plain files, for runs recorded before the ledger moved and for the fallback
below, and folds every file it reads into one set of runs: events are ordered by time (at the
same second, the shared ledger first, then this checkout's file, then the siblings', then
line order), an event identical to one already folded is dropped, and a run found in more
than one file is one run. A local file that cannot be read is skipped. A step or `end` can be
added to a run that exists only in such a file. A ledger line that cannot be read (not JSON, an unknown event, or a field of the wrong
type) stops `list` and the writes with code `corrupt`, naming `relay-runs.jsonl` and the
line. A writer also refuses a last line that lacks its newline, while `list` treats it as an
append in progress. Delete the line or the file.

### Sandboxes

A relay role whose sandbox may write only inside its slice worktree (Codex's default
workspace-write sandbox) cannot write the shared ledger: for a linked worktree the git common
dir is the main checkout's `.git/`. Each `relay-run` write that is refused there, opening the
ledger or its lock lacking permission, goes to this checkout's own `.slicer/relay-runs.jsonl`
instead, under the project lock, and prints one line on stderr naming both paths. It is per
write, so a role that can write the shared ledger again goes back to it. The fallback never
edits `.slicer/.gitignore` or `info/exclude`: the file relies on the ignore rule `init` and
the first unsandboxed write set, so a sandboxed write cannot put a tracked change into the
slice's commit. When the folded files hold events for the same second, an event that creates
a step (it names a role and an agent) is placed before a phase update of that step.
A step the fallback records gets an id with a prefix: `c`, eight hex digits and `-`, as in
`c3f9a1b2e-1`, in place of the plain number the shared ledger issues. The hex digits start
SHA-256 over the checkout's resolved path, so a checkout always uses the same prefix. The prefix
is 32 bits of that hash, so two checkouts are very unlikely to share one. A new fallback step
takes one past the largest number carrying its checkout's prefix, and the shared ledger numbers
only plain numbers, so a fallback step never takes a shared-ledger id. Two fallback writers in
one checkout number under the project lock. A prefixed id
works wherever a step id does: `--step` and `--parent` take it, and `list` shows it. If the
local file is damaged the write is refused as `corrupt`, as for the shared ledger.

`relay-run end` run where the shared ledger is writable (the outer session's) first copies
the run's events from every local file it reads, this checkout's and its siblings', that the
shared ledger does not hold yet, then (with `--close-steps`) closes the steps still running, a
step recorded only in a local file included, and appends the end event; its text names the count
(`ended RUN pass; absorbed 2 events from local ledgers`). An event that would not fold (a
timestamp or phase that does not exist) is not copied, and a local file that cannot be read
is skipped; one stderr line names both, so the shared ledger stays sound. A step whose parent
no copied event holds (the parent sat in a local file that could not be read, or was not copied)
is still copied, without that parent: the shared ledger folds a step only beside its parent. One
stderr line per such step names it and the parent it lost, and `relay-run list` shows it as a
root step. A `step` that goes to the shared ledger copies the run's local events first when the
shared ledger holds no begin for the run, or when its `--parent` or the step it updates with
`--step` is held only in a local file. This also preserves the updated step's role and agent
after that local file is removed. An update of a step already in the shared ledger does not
trigger absorption.
Because the copies are in the shared ledger, `git worktree remove` loses no step. The
local files are left as they are.
If `end` itself runs sandboxed it ends the run in the local file and absorbs nothing;
a later `end` cannot repeat, so end runs from the session that owns the run.

To avoid the fallback, make the shared ledger's directory writable for the vendor, for
Codex by adding `<main>/.git/slicer` (create it first) to
`sandbox_workspace_write.writable_roots`.

## Relay roles

The slice-relay role matrix lives in `.slicer/relay-roles.json`, and only the `relay-roles`
commands change it. Never open the JSON or its render. A row names a role (implementer,
reviewer, gate, architect or orchestrator), the slice conditions it applies to, and the agent, vendor,
model and reasoning effort that run it. The agent is the harness (Claude Code, Codex CLI,
Grok Build), so one relay can mix them. Conditions are `size`, `effort`, `importance` and
`urgency` (each a list; an empty list matches anything), `well_specified` (yes, no or
any) and `split` (yes for a part of a split slice, no for a whole one, or any) and `sliver` (yes
for a sliver, an item with a `parent`; no for any other item; or any); the When
cell reads "split part" or "whole slice", "sliver" or "not a sliver", and says nothing for any.
`recommend` reads the sliver condition from the item itself. `reasoning` is free text such as low, medium, high or xhigh, named so it is not
confused with the slice's own effort score. A size is any
single-line text, kept de-duplicated in a stable order (S, M, L, then the rest). Rules are
short texts.

```sh
slicer relay-roles init --render
slicer relay-roles add --role reviewer --size L --agent "Codex CLI" --vendor OpenAI --model gpt --reasoning high --render
slicer relay-roles set r3 --model opus --effort 1,2 --render
slicer relay-roles move r7 --before r2 --render
slicer relay-roles rule-add "No cross-vendor reviewer unless the owner asks." --render
slicer relay-roles show
slicer relay-roles recommend s259 --well-specified yes --split no
```

`recommend` is how a relay picks its proposal: it matches the slice's size, effort,
importance and urgency against the rows and returns, for each role including the
orchestrator, one selected row, the reason it was selected and the other matching rows as
alternatives. By default the first matching row in matrix order is selected, so put the most
specific rows first. `--prefer-agent AGENT` takes the first matching row on that agent for each
role that has one, and `--select ROLE=ROW_ID` names a row for a role outright and wins over
both. You say whether the slice is well specified and whether it is a part of a split slice;
the owner accepts the lineup as a whole or overrides rows. The launching harness has no
effect, nothing is launched, and nothing is written.

```sh
slicer relay-roles recommend s259 --well-specified yes --split no --prefer-agent Codex --select reviewer=r4
```

Row ids (`r1`, ...) and rule ids (`u1`, ...) are never issued twice by the counters, and each change writes a
history entry (`slicer log --action relay-roles`). The one exception is across a merge: an id that only the merged-in side issued and then removed can be kept by a row the current side added under it, and its history then shows an add and a remove for that id before the row. `slicer render` writes
`.slicer/render/RELAY-ROLES.md`, and `slicer check` reports it stale when the JSON changed
without a render. `slicer ai relay` embeds the same Markdown. A project that still has the
hand-edited `.slicer/relay-roles.md` from slicer 1.9 gets the `legacy_relay_roles` warning
from `check`; `slicer relay-roles init --replace-legacy` writes the JSON and removes the
old file in one step, after the write succeeds. Once the JSON exists, the same command
removes just the old file and keeps the matrix. The Markdown table is not converted into
rows.

## Roadmap prose


A roadmap carries text that belongs to no slice: an opening note, a heading and prose
around each pass group, and a closing section. `slicer prose` addresses those blocks:

```
preamble                 the opening note
goals                    project goals
non_goals                project non-goals
pass.<key>.heading       the group's markdown heading
pass.<key>.intro         prose above the group's table
pass.<key>.outro         prose below it
epilogue                 the closing section
```

`slicer prose list` names every block in the order it renders. Both `edit` and `prose edit`
take exactly one of `--text`, `--file`, or `--stdin`, or open `$EDITOR` when none is
supplied. In replacement mode, `--text` preserves the argument exactly, including
newlines, and `--text ""` clears the body. Section editing also accepts `--append` with an
explicit source: it joins old and new text with one blank line, removing boundary
newline characters. Empty appended content leaves the body unchanged.
For example, `slicer prose edit preamble --text "Current priorities"` replaces the preamble.
For creating, assigning, and removing passes, see [Passes and flags](#passes-and-flags).

A declared pass renders even with no items yet, so a group can be opened before its first
slice exists. Markdown and HTML show version-shaped keys newest first (`v1.10` above
`v1.2`, a bare `v1` below `v1.1`), then other keys in declaration order. Items with
no pass follow: those that are not done, then done items. `prose list` stays in
declaration order.

Goals and non-goals, and how agents should treat them, are covered in
[Goals and non-goals](#goals-and-non-goals).

### Note verification and freshness

`note --owner ACTOR` stores authorship, resolving the actor as an explicit owner,
then `SLICER_CLAIM_OWNER`, configured `claim_owner`, Git user name, or worktree name.
Authorship alone leaves a note unverified. Ordinary authorship and machine
verification reject the reserved `human:` prefix, including actor fallbacks.

Use `show ID --json` to obtain the stable `note_records[].id`, then
`note-verify ID --note-id NOTE [--owner ACTOR]` for machine verification (actor
precedence applies without `--owner`), or `note-attest ID --note-id NOTE --owner NAME`
for owner attestation, which always needs an explicit name. Names are nonempty,
one-line, and without `human:`. Attestation stores
`verified_by` as `human:NAME`, retaining the original author and text. These
commands record UTC `verified_at` and log the selected note ID and actor.
Repeating the same verifier makes no writes; another verifier replaces the prior
verification. Trust derives from the verifier: absent means `unverified`, a
machine actor means `machine-confirmed`, and `human:` means `human-reviewed`.
An actual text edit through ops or the TUI clears verification, preserving other
metadata and recording the previous verifier and time in history.

`note --stale-after TIMESTAMP` accepts timezone-aware ISO8601, normalizes it to
UTC, and rejects naive or malformed input before writing. Rendering displays
stored metadata and never computes expiry against the current clock. Old item
notes load with unknown authorship, no verification, and no expiry. Legacy slice
notes remain untyped read data and cannot be verified by these commands.

Owner attestation is a workflow convention, not caller authentication: an agent
can invoke the command. No identity or access-control boundary is claimed.

## Installation options

For the standard isolated install, see [README](../README.md#install).

Update with `~/.slicer-venv/bin/pip install -U squalor-slicer`;
uninstall by deleting the venv and the symlink.

For a single command, `pip install --user squalor-slicer` works too, with two caveats: on
macOS Homebrew and recent Debian/Ubuntu/Fedora the system Python is "externally managed"
(PEP 668) and rejects it — use the [isolated venv install](../README.md#install) — and the user scripts directory must be on
your `PATH` (`~/.local/bin` on Linux, `~/Library/Python/3.11/bin` on macOS). Uninstall with
`pip uninstall squalor-slicer`. Users of [pipx](https://pipx.pypa.io) or
[uv](https://docs.astral.sh/uv/) can instead run `pipx install squalor-slicer` or
`uv tool install squalor-slicer` for an isolated, on-PATH install in one step.

To try unreleased main instead of the latest release, replace the package name with
`git+https://github.com/squalor-xyz/slicer`.

To change slicer itself, use the editable contributor install in
[AGENTS.md](../AGENTS.md#install); the command then tracks your checkout.

If `slicer` is not found, check that the directory containing its executable is on
`PATH`. Contributors can run `PYTHONPATH=src python3 -m slicer --help` directly from
the slicer checkout without installing anything; see the
[contributor workflow](../AGENTS.md#working-on-the-roadmap).

## Initialization

Run `init` in the root of the project you want to track:

```console
$ cd /path/to/project
$ slicer init
initialised /path/to/project/.slicer
relay roles: no matrix written; run `slicer relay-roles init` to write the example one
```

That creates four things:

```
.slicer/config.json        everything project-specific
.slicer/index.json         the ordered queue — empty, next id S01
.slicer/templates/*.md     slice.md, roadmap.md, row.md, relay-brief.md — changed with `slicer template`
.slicer/slices/done/       where finished slices land
```

Plain `init` deliberately does **not** create `.slicer/render/` (first `slicer render`),
`.slicer/slices/retired/` (first `slicer remove --reason`), or `.slicer/log.jsonl`
(first change). They appear when they are first needed.

`init` never overwrites an existing `index.json`. Running it twice refuses:

```console
$ slicer init
/path/to/project/.slicer already exists; pass --force to overwrite its config and templates
```

`--force` rewrites `config.json` and the four templates. It still leaves `index.json`,
your slices and your log alone — it is not for starting over. To restore one template you
changed into a corner, `slicer template reset NAME` does that without touching the config.

## Adding individual items

`slicer add` appends one roadmap row per invocation:

```console
$ slicer add "Parse the config file" --size M --tree core --findings "G1"
added S01  Parse the config file
$ slicer add "Fail loudly on a missing key" --size S --tree core --depends-on S01
added S02  Fail loudly on a missing key
```

`add` takes `--size`, `--tree` (repeatable), `--findings`, `--status`, `--pass`, `--id`,
`--importance`/`--urgency` (the priority axes, 1–3), `--depends-on` (repeatable, using
item ids), and `--short-title` for a shorter roadmap label while retaining the full
title. Omitted or empty short titles fall back to the full title. `set --title` also
updates a short title that still matches the title; a distinct short title stays put. Use `set` to change
these fields later; repeated `set --depends-on` flags replace the dependency list.
An open item left at importance 2, urgency 2 and no effort ranks on nothing, so `add`
prints a stderr hint to score it and `import` lists such items under `warn`; neither
changes what is written or the exit code.
Both refuse a dependency `check` would reject — an unknown id (a comma list such as
`S01,S02` is one unknown id; repeat the flag instead), the item itself, a retired item, or
an edge that closes a cycle — and write nothing.

An item added this way is a roadmap row and nothing more. There is no slice file yet;
that is [Writing a slice](#writing-a-slice).

```console
$ slicer list
  #  ID    STATUS  CLAIM SIZE EFFORT SCORE QUADRANT  TITLE
  1  S01   —       -     M    -      22    -         Parse the config file
  2! S02   —       -     S    -      22    -         Fail loudly on a missing key
```

The `!` after a row number marks an item that waits on an unfinished dependency, so row 2 waits on S01.

The SCORE and QUADRANT columns are the priority score and quadrant (see
[Priority](#priority)); a fresh item sits at a neutral 2/2.

## Writing a slice

A roadmap row says *what*. A slice says *why, which files, which failing test first*.
`promote` creates one:

```console
$ slicer promote S01
promoted S01 -> ~/code/my-project/.slicer/slices/S01.json
```

The headings come from `sections` in your config, each with an empty body, and the
scope boundary is stored separately, seeded with your `boundary` marker. The default
headings are:

```
Why · Files · Failing tests · Implement · Check · Git
```

The boundary renders after metadata and before these sections. Set it explicitly:

```sh
slicer edit S01 --boundary --text "**Not in this slice:** other work" --render
```

Boundary editing accepts the same input sources and editor as section replacement.
It cannot combine with `--section` or `--append`; empty text clears the boundary and
restores the unbounded-scope warning. `promote --boundary TEXT` sets it at creation,
overriding any boundary in a source outline. Section edits cannot remove it.

Fill sections one at a time:

```console
$ slicer edit S01 --section Why --text "Validate settings before starting the app"
updated S01 / Why
```

For body sources, append semantics, notes, feedback, and issue exchange, see the [command table](#commands) and [Roadmap prose](#roadmap-prose). To file a new item with its spec, `import` a [one-item outline](import.md). To give an existing row its spec, promote it with one:

```sh
slicer promote S01 --file draft.md
```

For interactive editing see the [TUI manual](tui.md#editing).

## Git merge drivers

`log.jsonl` is append-only and union-merges automatically via the generated
`.gitattributes`, keeping both sides' entries.

**A merge leaves the render current without any setup.** `render/` is generated output that
is never parsed back, so every command that takes the project lock to change roadmap state
(the mutating commands and `next --start`) first compares `render/` with what `index.json`
renders to, rewrites the stale or conflicted files (and drops orphans) when it
differs, and says so once on stderr (`slicer: regenerated stale render files before this
write: ROADMAP.html, ROADMAP.md`). A render with conflict markers is healed the same way.
**A command that writes nothing still regenerates a stale `render/` first**: a no-op, a
retry or a refusal after the lock is taken leaves state and history untouched, but not
`render/`. The read-only commands, `render` and `sync` never heal. These writes do not heal
either: `init` (also `init --relay-roles`), `ai relay --init-roles`, `setup-git --attributes`,
the `feedback` commands and the `relay-run` ledger (which write no roadmap state), and the
TUI, whose own writes leave it to its `r` key. The heal costs one render comparison per
write, and a project with no `render/` directory is left alone. It does nothing while Git
has a merge in progress (those writes are refused), and
it cannot help `index.json` or `relay-roles.json`: with markers in `index.json` a write
is refused (`corrupt`) and the message names `slicer setup-git`; with markers in
`relay-roles.json` the message gives the `git checkout --ours|--theirs` route instead of
a hand edit. So the `slicer-generated`
driver and the post-merge hook below are optional; the `slicer-index` and
`slicer-relay-roles` drivers still are the way to merge those two files.

**Set up the merge drivers once per clone.** The generated `.gitattributes` points
`render/` at a `slicer-generated` driver so a merge keeps the current branch's copy instead
of writing conflict markers into those large files (optional now, since the next write
heals `render/` anyway). A driver name only resolves once the
clone defines it, and slicer's git allowlist cannot run `git config` for you, so run these
lines once in each clone. `slicer setup-git` prints them (`slicer setup-git | sh` applies
them), and `slicer init` also prints them when it is run inside a git repo:

```sh
git config merge.slicer-generated.name "keep the current branch's generated files"
git config merge.slicer-generated.driver true
git config merge.slicer-index.name "keep the larger next_id when merging the index"
git config merge.slicer-index.driver "slicer merge-index %O %A %B"
git config merge.slicer-relay-roles.name "keep both sides' relay roles rows and rules with fresh ids"
git config merge.slicer-relay-roles.driver "slicer merge-relay-roles %O %A %B"
```

An optional `post-merge` hook repairs the roadmap render at the merge itself, rather than
at the next write, after a plain merge or merge-based `git pull`. `slicer setup-git --hook` prints the script, without installing
it or running Git. To install it yourself (this replaces an existing hook; integrate
its body by hand if you already have one):

```sh
slicer setup-git --hook > "$(git rev-parse --git-path hooks/post-merge)"
chmod +x "$(git rev-parse --git-path hooks/post-merge)"
```

`git rev-parse --git-path` respects `core.hooksPath`; the hooks directory must already
exist. The hook needs only `sh` and `slicer` on PATH. It checks `slicer check --json`
and runs `slicer render` only for `stale_render_kind: roadmap_only`, leaving the
rewritten files unstaged for you to review and commit. A current render is left alone;
other check failures say to run `slicer check`. The hook always exits 0, including
when rendering fails. Git does not run `post-merge` when a merge stops on conflicts:
after resolving them, run `slicer render` and `slicer check` by hand before committing.
Git also skips `post-merge` for `git merge --no-commit`; after committing the merge,
run `slicer render` and `slicer check` by hand, then review and commit the rewritten files.

The middle two turn on the `slicer-index` driver for `index.json`. Every filing moves the
single `next_id` line, and edits to neighbouring items or fields overlap as lines, so
two branches conflict there even when they changed different things. The driver merges
by identity instead: items by id, object fields by name. It takes the larger `next_id`,
so an id is never reused; a value only one side changed takes that change; equal changes
coalesce; new ids from both sides are kept, yours first. An item's notes are append-only
records with unique ids, so two branches that each added notes keep both, ordered by
creation time, with notes that share a time kept yours first, then theirs (a note
removed on one side stays removed only if the other side left it unchanged). Other lists
(dependencies, flags) and prose are never combined, and the shared items' order follows
whichever side reordered it, so these still conflict when both sides changed them
differently:

- one field, list or order changed two ways, or one note edited two ways;
- a note removed on one side and edited on the other;
- an item deleted on one side and edited on the other;
- the same new id filed on both sides with different content;
- a result that would break the index: two ids sharing a filing key, ids differing only
  in case, a dependency cycle, or a dependency on an item the merge removed.

Each conflict is written as markers around just that field or item, and stderr names it;
resolve it by hand, run `slicer render`, then `slicer check`. A file that is not a readable
index is never merged as clean. A project created before this driver picks up the line
`index.json merge=slicer-index` by running `slicer setup-git --attributes`, which rewrites
`.slicer/.gitattributes` from the current template; without it Git does not call the
driver. `slicer` must be on the `PATH` Git runs with.

The kept copy still has to match the merged `index.json`, so re-run `slicer render` after
resolving a merge — the driver only skips the markers, it does not re-project the state.
`slicer verify` warns (it never fails) when this clone has other worktrees but has not
configured the driver, so a forgotten setup is caught before a merge writes markers rather
than after. A single-worktree clone stays quiet, and `render_driver_check: false` in config
turns the reminder off entirely.

The last two `git config` lines turn on the `slicer-relay-roles` driver for
`relay-roles.json`. (`slicer verify` warns when it is missing, under the same sibling-worktree and
`render_driver_check` gates as the other two, and only in a project with a relay role matrix.) Two branches that
each run `relay-roles add` issue the same `r` id, and `rule-add` the same `u` id, so Git
can only call that a text conflict, and the file may not be repaired by hand. The driver
matches rows and rules by id and fields by name, so edits to different rows, rules or
fields all merge. A row or rule that both sides issued under one id with different
content (or that the other side added under an id you issued and then removed) is kept
twice: yours keeps the id and the one merged in gets a fresh id past every id either side
issued, with its text unchanged; the counters end past every issued id, so they never
issue an id twice. The reverse case is the one exception: an id that only the merged-in
side issued and then removed can be kept by a row your side added under it, and its
history then shows an add and a remove for that id before your row. The driver's stderr
line is the only record of a renumbering: it writes no `log.jsonl` entry, and history
keeps the original ids. Rows added identically on both sides are kept once. It refuses,
with code `merge_conflict`, when both sides changed one value differently, one removed a
row or rule the other edited, or both reordered the rows differently. A refusal leaves
the file as it was, and slicer refuses writes while the merge is unfinished, so: keep one
side with `git checkout --ours|--theirs .slicer/relay-roles.json`, `git add` it, finish
the merge, then re-apply the other side's changes (every row and rule it added or
edited, not only the conflicting value) with `slicer relay-roles`. A project created
before this driver takes the line `relay-roles.json merge=slicer-relay-roles` with
`slicer setup-git --attributes`.

## Passes and flags

Passes are an optional grouping — a review round, a milestone, a phase. Items with no
pass render as one flat table, which is fine for most projects.

```console
$ slicer prose add-pass 2 --heading "# Pass 2 — hardening"
$ slicer add "Rotate the signing key" --pass 2
$ slicer set S03 --pass 2
```

Remove an empty pass with `slicer prose drop-pass 2`; it refuses while any item
is still filed under it.

`slicer add` without `--pass` files the new item under the *previous* item's pass, so once
the tail of the queue is in a pass, every later `add` lands there too. Name the pass you
mean, or pass `--pass ''` to file it with no pass (`slicer set ID --pass ''` clears one
later). `import` never inherits a pass.

Once the roadmap has a pass, `slicer list` (and the `find` and `deps` rows) gains a PASS
column after CLAIM, showing each item's pass or `-`. `slicer list --pass 2` narrows the
table to one pass.

A pass is one milestone, and trees name subsystems. Flags are the other axis: free-form
labels an item can hold several of at once, such as `security` or `perf`. Set them with
`slicer set S01 --flag security --flag perf` (that replaces the list; `--no-flags` clears
it). `slicer list --flag security` returns items carrying that flag, and repeating
`--flag` matches any of the names. The TUI filter panel (`f`) lists each distinct flag
and `(none)` for an item with no flags. `exclude_flags` in config still only drops an
item from the sync pointers.

## Goals and non-goals

Goals and non-goals record what the project is *for*, so what belongs on the backlog can
be judged — separately from what is currently queued. They are two roadmap prose blocks:

```console
$ slicer prose edit goals --file goals.md      # or --text/--stdin/$EDITOR
$ slicer prose edit non_goals --stdin < non_goals.md
$ slicer goals                                 # print both together
$ slicer goals --json                          # {"goals": "...", "non_goals": "..."}
```

They render near the top of `ROADMAP.md`, so a reader sees direction before the queue, and
`slicer check` fails if that rendered copy drifts. Agents read direction with `slicer goals
--json` and should ask the owner to set it rather than inferring it from the backlog.

## Continuous integration

One command, one exit code:

```yaml
- name: Install slicer
  run: pip install squalor-slicer

- name: slicer check
  run: slicer check
```

This is the same no-clone install as [README](../README.md#install), run in a throwaway CI
environment where a shared venv is unnecessary. `check` composes render staleness, sync
drift and the offline integrity checks. In a CI clone its git reads need no history: the
clone has one worktree, so the sibling-worktree warning finds nothing to read, and the
code-location hint runs a `git rev-parse` in the installed package's directory. So a
shallow checkout is fine.

`slicer verify` is the other one — it additionally compares the index against
`git log --all`, warning when an item is recorded done but no commit subject mentions
it. If you add it to CI it needs `fetch-depth: 0`, or it sees no history and skips.

## Troubleshooting

Exit codes: **0** fine · **1** drift or a failed check · **2** usage, validation, or
nothing to do · **3** internal or state (`corrupt`, `locked`, `io`, `config`,
`schema_too_new`).

| What you see | What it means |
|---|---|
| `no .slicer/ found in … run \`slicer init\` in the project root` | You are outside a tracked project. slicer walks up from the working directory looking for `.slicer/config.json`, git-style. |
| `… already exists; pass --force to overwrite its config and templates` | `init` on an initialised project. `--force` rewrites config and templates only. |
| `<ID> has no slice; run \`slicer promote <ID>\` first` | `edit` on a roadmap row that has no slice file yet. |
| `<ID> already has a slice; pass --force to overwrite it` | `promote` on an already-promoted item. `--force` discards what is there. |
| `unknown status 'x'; known: [...]` | The status is not a key in `config.statuses`. |
| `id <X> already exists; ids are never reused` | `add --id` naming a claimed id. Ids are claimed for the life of the project. |
| `stale render: …` / `check failed` | Run `slicer render` (and `slicer sync` if you have sync targets), or run any slicer write, which regenerates a stale `render/` first. When the roadmap render is the only stale file, `check` also prints `only the roadmap render is stale, as a merge leaves it`, and a file holding merge conflict markers says so (`conflict_markers`). |
| `template missing; run \`slicer template reset NAME\` to restore it` | A file under `.slicer/templates/` was deleted. |
| `template uses {{x}}, which is not a known placeholder; nothing was written` | `slicer template set` refused text that names a placeholder the renderer does not know (`bad_template`). The file is unchanged. |
| `… written by a newer slicer (schema N; this build knows M). Upgrade slicer to open this project.` | The project was saved by a newer slicer. 1.0.0 writes index schema 2 (`config.json` stays at 1): it opens a schema-1 project with no migration step, and the first change it saves stamps schema 2, after which an older build refuses the project rather than dropping claims. Upgrade every clone that shares the project. Pre-release checkouts from before this guard (S72) do not refuse; they can drop claims and rewrite the file at schema 1. |
| `Git merge in progress in …; finish or abort it before changing slicer state.` | slicer refuses state changes, including TUI saves, while this checkout has a merge to finish (`merge_in_progress`, exit 2). Read-only commands, dry runs, `render` and `sync` still work for the repair; finish or abort the merge, then retry. |
| `running code from …, but this project is … -- a different worktree of the same repo` | A stderr note, not an error: an editable install from one worktree is running against another worktree's `.slicer/`, so that checkout's `src/` edits are not what runs. Use `PYTHONPATH=src python3 -m slicer` in the checkout you are editing, or set `SLICER_NO_CODE_WARNING=1` to silence it. |
| `refusing to write: fix the problems above` | An import or migration found problems. Nothing was written; see [import.md](import.md) or [migrate-format.md](migrate-format.md). |
| merge conflict under `.slicer/` | `log.jsonl` union-merges on its own (via the generated `.gitattributes`). `index.json` is the source of truth. With the `slicer-index` driver configured (see [Git merge drivers](#git-merge-drivers)), independent items and fields merge and `next_id` takes the larger value, so resolve only the conflicts it leaves (markers around the single field or item; stderr names them). For anything under `render/`, don't merge it: with the `slicer-generated` driver configured (see [Git merge drivers](#git-merge-drivers)) the merge keeps the current branch's copy with no markers; either way, resolve `index.json`, run `slicer render`, `git add .slicer/render`, and continue — `slicer check` catches a forgotten re-render. After a merge that is already committed with stale or conflicted `render/` files, the next slicer write regenerates them. With conflict markers in `index.json` itself a write is refused (`corrupt`); the message names `slicer setup-git`, whose `slicer-index` driver keeps a merge from leaving them. A conflict on `relay-roles.json` is either the `slicer-relay-roles` driver refusing a disagreement (`merge_conflict`, or `corrupt` when an input is not a readable matrix; the file is left as it was) or, with the driver not configured in this clone, Git's text markers in the file. Either way never edit it by hand: keep one side with `git checkout --ours|--theirs .slicer/relay-roles.json`, `git add` it, finish the merge, then re-apply the other side's changes (every row and rule it added or edited, not only the conflicting value) with `slicer relay-roles`. |

`slicer verify` is the broader health check — dangling dependencies, cycles, a dependency
on a retired item, slices in the wrong folder for their status, a slice file whose name
and contained id disagree, `has_slice` out of step with the files on disk, an id that is
not a usable filename, and a `next_id` that would reuse an id. It reports and never
rewrites. Render freshness belongs to `slicer check`, not `verify`.

Inside a git checkout it adds two checks. Both are warnings, so `verify` still exits 0:

- With `render_driver_check` on (the default), it warns when this clone has sibling
  worktrees and the `slicer-generated`, `slicer-index` or (in a project with a relay role
  matrix) `slicer-relay-roles` merge driver is not configured. A single-worktree
  clone stays quiet. Set `render_driver_check` to `false` to silence the reminder.
- With `git_check` on (the default), it warns when a done item has no commit subject
  mentioning its id. The scan is `git log --all --no-merges`, at most 2000 commits.
  Set `git_check` to `false` when the history cannot contain those commits, such as a
  repo split from another. Outside a repository, or with no history, it reports
  `git checks skipped` and stops there.

To confirm that a merge really carried an item's done state, ask for that item by id:

```sh
slicer verify --landed S07 --on main      # --on defaults to HEAD
```

It exits 0 when `main`'s own index has S07 done and, if S07 has a slice, `main`'s tree
holds the done slice file, added by a commit reachable from `main`. It reads that tree
with `git show`, not your working tree, so an uncommitted `done` or a commit still on a
feature branch does not count. When it has not landed it exits 1 and names why:
`other_branch` when some other branch, remote branch or tag outside `main`'s history has
S07 done right now (a branch that has since reopened it does not count; stashes are
ignored), `working_tree_only` when only your working tree has it done, else `not_done`.
Exit 2 means there is no answer to give: an unknown id (`no_such_item`), an unusable id
(`bad_id`), a ref that names no commit (`bad_ref`), a project outside a git repository
(`not_git`), or `--on` without `--landed`. An index that cannot be read on `main` itself is
exit 3; one that cannot be read on some other branch is skipped. `--json` returns `{id, ref, ref_commit, landed, reason, message, commit}`. It
writes nothing, and it follows `done_dir`, so scripts need not hard-code the done path.

## Review lifecycle

`start` records a claim on the item. The [command table](#commands) defines owner
precedence and the `release` command; [Picking and listing work](#picking-and-listing-work)
explains claim columns and sibling-worktree signals.

When `implement_finish` is `handoff`, this is the finish: pass the implementation to
someone else for review, merge, or cleanup without marking it done by handing it off:

```console
$ slicer note S01 --text "Ready for review: branch feature/S01; suite green" --render
$ slicer handoff S01 --render
S01 -> review, unclaimed
```

The item moves to the `review` status and loses its claim. `next` no longer offers it,
and anything that depends on it stays blocked. A reviewer runs `slicer next --status
review --start`, which returns the highest-priority review item, claims it and moves it
to `reviewing` (the same as `slicer start S01` on a review item). Reviewing is still out
of plain `next`, so no implementer picks it up as resumable work, and running the
reviewer's command again resumes it. `--ready --section NAME` trims the slice to the
sections a review needs. If the review fails, `slicer reject S01 --note "VERDICT: FAIL -
missing test"` sends it back to the open queue, unclaimed, with the verdict added to its
notes and history (`--to STATUS` picks another non-lifecycle status, such as a project's
own `blocked`). Run
`slicer done S01 --render` only once review and merge are complete; slicer records the
handoff but never merges anything.
