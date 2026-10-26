# Driving slicer from an agent

For the owner's decisions, progress inspection, and integration responsibilities,
read [Working with agents](working-with-agents.md). This page is the agent and
JSON reference; the generated commands below remain authoritative for the workflow.

Start with `slicer ai instructions` for the canonical concise onboarding guide.
It is available wherever slicer is installed and works without an initialized project.
A readable project whose `implement_finish` is `handoff` changes step 4 to handoff;
otherwise step 4 stays done, including when that key is missing. A nonempty
`handoff_requires_note_kind` also adds the project's report requirement. No project, or a
config or index that cannot be read, prints the generic text, warns on stderr, and
exits 0. It does not lock or write. `slicer ai instructions --json`
returns `{"instructions": "..."}` with the same Markdown as the text output.
`--root` selects the project. This page provides the detailed reference and
reusable prompts; the command is the single source for the quick start.

`slicer ai skill` prints a `SKILL.md` for that implement loop and the exit-code
rules, using the same finish choice and the same fallback. Claude Code, Codex, and Grok load the same file.
`slicer ai skill --install` writes it to `.claude/skills/slicer/SKILL.md`,
`.agents/skills/slicer/SKILL.md` (Codex reads `.agents/skills/`, so there is no `.codex` copy),
and `.grok/skills/slicer/SKILL.md` under the project root; it never touches the home directory.
Use this instead of copying this repo's generic `skills/slicer/SKILL.md`: a project that
finishes with `handoff` needs the adapted text. `--output PATH` writes one file, creating
parent directories, and the two flags combine. The file holds exactly the bytes the command
prints, so `slicer ai skill | diff - PATH` is a drift check for a given slicer version and
finish mode. An existing destination with the same text is rewritten; one with different text
exits 2 with code `state` and nothing is written, unless `--force`. `--install` needs a
project, and a project whose config or index cannot be read is refused rather than given the
generic text. Without either flag nothing is written. `--json` returns `{"skill": "..."}`, plus
`written` (the absolute paths) when a write flag is given. The skill ends by pointing at
`slicer ai instructions --rest`, which prints only the parts of the guide the skill
leaves out, so an agent that loaded the skill does not read the same text twice. The skill
also says that a slice relay request goes to `slicer ai relay`, and that its Setup is confirmed
with the owner before the worktree is created. It says to read the role matrix with
`slicer relay-roles show` and change it with the other `relay-roles` commands, never from
`.slicer/relay-roles.json`.

`slicer ai relay` prints the slice relay. It starts with the line `Contract: relay v8`, the
contract version (`ai.RELAY_CONTRACT`, bumped when a rule an outer session, orchestrator or role
follows changes). The agents repo's `relay-orchestrator` role carries an `Expects:` line that its
doctor checks against that marker. The text describes an
outer session, the one the owner talks to, that spawns one orchestrator per slice; the
orchestrator runs the slice through an implementer, a reviewer (and, on a UI slice, a UI pass), an architect and a gate. The
orchestrator's own contract and run report come from `slicer ai relay-orchestrator`, which
opens with the same marker line; the outer session starts the orchestrator's brief with "First
run `slicer ai relay-orchestrator` and follow it." and checks the report's first line and its
last line, `nothing left running: yes`. A hand-back without that line that names the role it
waits on (`waiting on: ROLE AGENT-ID`) is a wait: the outer session waits for that role's report
and resumes the orchestrator with it pasted in, and stops the orchestrator only when the hand-back names
no running role, or a finished role whose report the orchestrator already has. Its Setup section, before Order,
asks the owner which roles run (the architect is optional; on a UI slice a UI pass runs beside the reviewer, or the reviewer carries it), the agent, vendor, model and
reasoning effort per role, the orchestrator's agent, model and reasoning effort (agent type
`relay-orchestrator` where the harness has one, otherwise a general-purpose agent briefed with
`slicer ai relay-orchestrator`), the environment lines every brief carries, whether the implementation splits into parts, whether to sliver
the slice (the default is no), and where the run ends (after the finish step, after a commit
on the slice branch, or landed). A split needs
parts with disjoint files and their own tests. One lead implementer runs `start`, the note
and the finish step; part implementers change only their own files and run no slicer command
that writes; the orchestrator runs the full suite on the combined diff, and one review and
one gate cover the whole slice. A slice too big for one implementer is two slices or a
slivered slice. A `## Slivers` section, between Order and Gate, tells the orchestrator when
and how to sliver a slice: eligibility (effort 1 at any size, effort 2 only when every sliver
is effort 1, effort 3 never), filing (on main before the worktree is cut, with
`add --parent ID` or one `import` file; each sliver is size S, effort 1, with the parent's
tree and pass), order (slivers run in the parent's worktree, each through implement, review,
gate and handoff, then one combined gate and the parent's own handoff), parallel runs
(disjoint files and their own tests, each with its own temp directory, the full suite on the
combined diff before the combined gate) and escalation (the matrix rule). The owner confirms
the sliver plan at Setup. It uses
the same finish choice and the same fallback as `slicer ai instructions`. Handoff finish
closes with handoff, and that text does not name done. `--json` returns
`{"contract": "relay v8", "relay": "..."}` with the same Markdown as the text output. It does not lock or write, except `--init-roles`, which
is `relay-roles init --render` under the project lock.

A failed relay review is recorded on the started item with
`slicer note ID --text "Review FAIL: ..." --render`, and only after `VERDICT: FAIL`: a
gate that passed writes no `Review FAIL` note, even when a fix round follows it (for UI
edits, say). Add `--kind` only when `note_kinds` configures a kind for reviews. The
closing note and finish step still wait for `VERDICT: PASS`.

The UI pass is a contract role beside the reviewer. The Roles section of `slicer ai relay`
defines it: read-only, edits nothing, runs in parallel with the reviewer on a slice the
project marks as UI, and returns triaged findings and, where the project can render, the
changed views. Step 2 of the orchestrator's `order` (text and `--json`) and the Every brief
section of both contracts carry the rest: the brief tells the agent to load the skills the
project's role matrix names for the UI pass, gives a vendor that cannot load skills the
project's vendor-neutral UI prompt, and lets the project fold the UI pass into the reviewer
by slice size. The Setup `roles` bullet (text and `--json`) names the UI pass, and the
orchestrator's brief carries whether the slice is a UI slice, whether the reviewer carries
the UI pass, and the skills or vendor-neutral prompt for a separate UI pass (relay Order
step 2 and the orchestrator's opening paragraph). The gate still decides. The contract names no project's skills or rules.

The spec check is an optional contract role that runs before any worktree exists, on a batch
of slices the owner picks. The Roles section of `slicer ai relay` defines it: read-only, edits
nothing, and returns for each slice `ready` or questions in the shape of the run report's
`owner needed` line (2 to 4 options, a preferred one, `blocker` where the slice cannot go on
without the answer). The outer session spawns it and reads its report; it has no matrix row, so the
owner names its agent, model and reasoning effort at Setup like any role. The first Setup bullet, `spec_check` (text and `--json`), has the owner
answer the whole batch in one sitting. The outer session records each answer in its slice as an
`Owner decisions` paragraph with `slicer edit ID --section SECTION --append --text "Owner
decisions: ..." --render` (SECTION being the section the question concerns) and commits those filings on main before the worktree is cut, because a
worktree starts at HEAD and an uncommitted filing never reaches it; the owner authorizes that
commit. Every brief (both contracts) carries the slice's `Owner decisions` paragraph and each
owner answer recorded in a note since, verbatim. An answer the owner gives after the worktree
exists goes in a note on the item in the worktree, `slicer note ID --text "Owner answer: ..."
--render`, run by the outer session, which then resumes the orchestrator with the answer pasted in;
the orchestrator carries it into the resumed role's brief (relay Order step 3 and its Owner
answers paragraph, and the orchestrator's `Owner answers` rule). A project that keeps a decision
log also records there a design answer the owner explicitly picked. The implementer re-anchors
the line numbers a slice cites late, at start, since they may have moved; the spec check does not.
The spec check is named in contract text only: it is not in the role matrix or in
`relay-roles recommend`. The contract marker stays `relay v8`.

Approve and Land are the outer session's last steps in `slicer ai relay`, after the report
check. Approve (step 5 of `order`) has the owner decide before anything is merged or landed (the only commit before it is the slice commit the orchestrator's brief allows; with the end point `finish` the run ends there instead):
the outer session records the wait on the orchestrator's step with `slicer relay-run step RUN
--step STEP --phase wait --waiting-on approval` (`--waiting-on owner` for an owner question),
never ends the run while it waits, shows the gate verdict, tests, diff stat, owner questions
and each `[HUMAN CHECK]` line of the slice's Check section, and sends an owner fix note to a
fix round (after the finish step, the item first goes back to `started_status` with `slicer set ID --status STATUS --render`, and the closing note and finish step run again after its PASS). On approval, the step records `--phase start` to end the wait. A human check the owner confirms is recorded as a note on the item in the worktree
(`--kind human-check` only when `note_kinds` configures that kind); an unconfirmed one is shown
with a warning and does not block landing. The `## Land` section (the `land` list in `--json`)
is the landing order: rerun the Check commands, commit, merge main into the branch when main
moved, `slicer render`, `slicer check`, rerun the suite, `git merge --ff-only` on main (the
contract default; `--no-ff` stays valid for the manual flow in [landing.md](landing.md)), end the
run with `relay-run end`, then `slicer land`, then commit, push only when the owner authorizes it,
and remove the worktree and branch. The run ends before `land` because `land` refuses
`run_active` while a run is not ended. slicer runs no git command; the outer session runs them.

Every reviewer, UI pass and gate finding carries one kind, set out in the Gate section
and in every brief for those roles. A `fix` finding goes to the fix round. An `owner`
finding is a design call with 2 to 4 options and a recommendation; it does not block the
gate unless it is marked `blocker`. A `later` finding is a follow-up item with a one-line
title. The gate passes only with no new `fix` finding of medium severity or worse and no
`blocker` owner finding. The run report's `owner needed` entries are numbered questions, one
line each: `owner needed: N. TITLE | options: LABEL:SUMMARY; LABEL:SUMMARY | recommend: LABEL |
blocker (optional) | key: AREA.TOPIC (optional) | reasoning: TEXT`, or `owner needed: none`.
Each question has 2 to 4 options, no value but `reasoning` holds a `|`, no SUMMARY holds a
`;`, and `reasoning` is last so it takes the rest of the line. The fields are the ones the planned
`relay-run ask` takes, so a consumer parses one shape. Each question counts toward the
report's 30 lines. A non-blocker `owner` finding does not stop the orchestrator; a `blocker`
one stops it with `OUTCOME stopped`, and the Stop condition is an owner decision marked
`blocker`. A permission denial stops the run as a `blocker` question whose reasoning is the
denial's exact text.

The `## Conformance` section, at the end of both contracts (before Close in `ai relay`, before
the run report in `ai relay-orchestrator`), says what any orchestrator owes the contract, whether
an LLM agent briefed with that text or a program that spawns the roles, records the ledger and
applies the rules in code: run the roles in Order's sequence, decide the gate on its criteria with
the verdict as the first line, tag every finding `fix`, `owner` or `later`, stop on a stop
condition, or on a harness permission denial of a role's slicer step (before the commit, with the
denial's exact text as a `blocker` question), with `OUTCOME stopped`, record each round in the run
ledger (the outer session begins and ends the run), run the closing note and the finish step only
after `VERDICT: PASS`, return one run report once, never an interim one, ending with `nothing left
running: yes`, work only in the worktree the brief names, and commit on the slice branch only when
the brief allows it, never merging, landing, pushing or rebasing. A program reads these as data
from `ai relay --json` or `ai relay-orchestrator --json` (`roles`, `order`, `gate`,
`finding_kinds`, `report`, `outcomes`, `stop`, `conformance`) instead of restating the contract in
its own docs. Checking the contract version against the one a program was written for is not an
obligation here; the project pin (s387) owns that. The contract marker stays `relay v8` with this
section; the version bump comes with the rest of the v9 changes.

The role matrix is `.slicer/relay-roles.json`, versioned state that only `slicer relay-roles`
changes (see [commands.md](commands.md#relay-roles)); agents and people never open the JSON
or its render. `slicer ai relay` adds a `## Roles matrix` section after Setup that holds the
matrix as Markdown, and Setup gets one line proposing each role's agent, vendor, model and
reasoning effort from it and names
`slicer relay-roles recommend ID --well-specified yes|no --split yes|no`, which does the
matching; it tells the orchestrator to pass `--split yes` for a part of a split slice.
Contract v5 makes that command the resolver of the whole lineup, the orchestrator included:
it returns each role's selected row, why it was selected (`selected_by`) and the other
matching rows as `alternatives`. The matrix order decides unless the owner names a preferred
agent (`--prefer-agent AGENT`) or a row for a role (`--select ROLE=ROW_ID`, once per role).
The outer session shows the resolved lineup once, and the owner accepts it as a whole or
overrides individual rows; accepting it settles only the agent, vendor, model and reasoning
effort of each role, and does not by itself authorize starting paid sessions, committing or
landing, or answer the other Setup questions. The harness the owner launched the relay from
changes nothing by itself. Routing is unchanged: a role on the orchestrator's own harness runs
as a native subagent, and a role on another harness runs as a headless session in the slice
worktree with the same brief. The
example matrix from `relay-roles init` has its whole-slice implementer rows on `--split no`,
a per-part implementer row (haiku, xhigh) on `--split yes`, and a sliver row (haiku, xhigh,
listed first among the implementers) that matches an item with a parent; `recommend` infers the
sliver from the item's parent, so there is no flag for it. A Codex implementer alternative (`r9`,
listed after the Claude implementer rows) matches any item. Without the file, Setup
names `slicer relay-roles init` instead.
`slicer ai relay-roles` is `slicer relay-roles show`; `--json` returns the Markdown under
`relay_roles` and the structured `rows` and `rules`. A missing file is exit 2 with code
`no_relay_roles`; a malformed one is `corrupt` and one from a newer slicer is
`schema_too_new` (exit 3). A leftover hand-edited `.slicer/relay-roles.md` is ignored: `ai relay`
warns on stderr and `check` adds the `legacy_relay_roles` warning. When the config or index
cannot be read, the relay is the generic text and the matrix is not read.

The matrix holds `orchestrator` rows from relay-roles schema 3. A schema 1 or 2
matrix loads without a write and its orchestrator is `unmatched` until a row is added with
`relay-roles add --role orchestrator`; the next mutation stamps schema 3, which an older slicer
refuses (`schema_too_new`). The example matrix from `relay-roles init` has a Claude Code orchestrator
row (`r10`, the default) and a Codex one (`r11`), so `recommend` resolves the orchestrator on a fresh
project. Its architect row (`r7`) matches size L only, so a smaller slice has no architect row.

Companion launch tooling (agents repo, not this repository). `relay v8` only selects rows; it
launches nothing, inspects no installed harness or credential, and has not been verified to
launch in either direction between harnesses. Either Codex or Claude Code may be the outer
entry point, so the agents repo's tooling must, in both directions, keep the shared briefs and
run reports, set the model and reasoning effort explicitly from the selected row, carry the
worktree and sandbox limits into a headless session, handle completion and cancellation, and
record the run and its steps with `relay-run`. Doing that means inspecting that repository in an
authorized workspace and trying each direction; this repository's selection layer makes no
claim about it. A consumer of `relay-roles recommend --json` should read `roles` by `role`
name, not by position past the fourth entry, and should treat an unknown `selected_by` as
`matrix_order`.

slicer is built to be called by a coding agent rather than hand-edited. The agent runs
commands; slicer owns the files. Nothing an agent needs requires reading or writing
`.slicer/*.json` directly — and doing so is how state gets corrupted, because the index,
the slice files and the rendered markdown have to agree.

slicer is AI-friendly and tool-neutral: it runs locally, without an AI service or API
key. The agent reviews and implements; slicer manages the agreed work. Start with the
[worked workflows](#worked-workflows) for a new project or an
existing codebase, or the [contributor loop](../AGENTS.md#working-on-the-roadmap) when
changing slicer itself.

For automation, use JSON responses and inspect both the exit code and the payload.
The command and error contracts are described below the prompts.

Before handing off, read `slicer config handoff_requires_note_kind`. If nonempty,
file `slicer note ID --kind KIND --text "Describe the verified outcome" --render` during the
current implementation attempt. Project-aware instructions and skill output name
that required kind. Wrong-kind, previous-attempt, legacy unknown-attempt, and
history-only handoff notes cannot satisfy the policy. Missing reports fail with
code `state` before any batch writes. Release and resume preserve the association;
reject then restart requires a new report. Manually changing `attempts` changes
which reports match. Note read filters do not affect this check. `done` is unchanged.
`ai instructions --rest` stays generic and does not read project policy.

## Reusable prompts

Adapt these to your project's instructions. They describe separate stages so you can
review findings and agree on scope before importing or implementing work.

### Plan a new project

```text
Read the project instructions. Help me turn the following goal, constraints, and
acceptance criteria into a small roadmap: [describe them here]. Ask about missing
requirements. Break the work into bounded slices with concrete checks and explicit
dependencies. Propose importance, urgency, and effort (1-3) for every item, with a
one-line reason each. Detail the first slice; later items may remain roadmap rows. Identify
proposed files as proposed, rather than claiming they already exist. Present the plan
for discussion before importing or implementing it.
```

### Review an existing project

```text
Read the project instructions, relevant source, and tests. Read the roadmap with
`slicer goals`, `slicer list`, `slicer show`, and `slicer prose list`. Do not open
tracking JSON, the history file, or generated roadmap output. Review
the code for bugs, regressions, missing tests, and maintainability problems supported
by evidence. For each finding, cite file locations, explain the impact and a concrete
failure case or verification method, and distinguish confirmed behavior from open
questions. Identify findings already covered by roadmap items. Propose bounded
improvements with acceptance criteria and proposed importance, urgency, and effort
(1-3) with reasons; do not change code or import work yet.
```

### Convert an agreed roadmap

```text
Convert the accepted goals or review findings into a slicer markdown outline. Read
docs/import.md from the slicer documentation and obtain the target project's template
with `slicer import --skeleton`. Use unique ## item titles, supported metadata keys,
and ### slice sections. Preserve finding references, scope boundaries, acceptance
checks, and dependencies by exact item title. Set importance, urgency, and effort
(1-3) on every item, and size, tree, and findings where the project uses them; give a
one-line reason for each score in your response. Do not invent findings or file locations.
Save the outline as roadmap.md and run `slicer import roadmap.md --dry-run --json`.
Resolve validation errors and show me the outline and result before applying it.
```

If the target project has no `.slicer/` yet, follow [Initialization](commands.md#initialization)
and [Configuration](configuration.md) first. Once the outline is agreed, apply it with
`slicer import roadmap.md --render`, then run `slicer check`. Import is a one-time
transfer; subsequent edits use slicer commands. Keep references to review evidence in
the slices rather than relying on the temporary outline as the only record.

### Implement one slice

```text
Read the project instructions. Run `slicer next --ready --section "Implement" --section "Check" --json --lean` to get the next
item and only those sections. The headings are examples; pass the section names the project configures. Run `slicer sections` for this project's names. Read the scope, dependencies, relevant source, and tests. If
there is no slice or its acceptance criteria are ambiguous, resolve the specification
with me first. If `next` includes `unspecified`, fill each missing section with
`slicer edit ID --section NAME` before implementing that id. Do not invent the body.
Once the specification is clear and trusted, mark it started with
`slicer start ID --render --strict`. Implement that slice, and run its acceptance checks and
required project checks. Update affected documentation. Edit a slice with
`slicer edit ID --section NAME --text "Body" --render --strict`. Once verified, run
step 4 of `slicer ai instructions`. That is
`slicer done ID --note "Describe the verified outcome" --render` unless `implement_finish`
is `handoff`, in which case it is `slicer handoff ID --render`. Then run `slicer check`.
When step 4 was handoff, run done only after review and merge. Report changes and
checks, and stop after this slice.
Use the slicer CLI to read and to change goals, items, slices, notes, history, and
roadmap prose, including during review and planning. Do not open, search, parse, or
edit tracking JSON, the history file, or generated roadmap and slice output to obtain
or change that state. If a command is missing or fails, report it and propose a
roadmap item. Do not open or hand-merge generated roadmap files. When `ROADMAP.md` or
`ROADMAP.html` conflicts, run `slicer render` then `slicer check`. Do not commit or
publish unless separately authorized.
```

## Everything takes `--json`

Every subcommand except the interactive `tui`/`ui` and the git merge drivers `merge-index`
and `merge-relay-roles` accepts `--json` and writes a single JSON document to stdout. Read
commands return data; write commands return what they changed.

```console
$ slicer next --json
{
  "id": "S01",
  "title": "Parse the config file",
  "short_title": "Parse the config file",
  "status": "open",
  "has_slice": true,
  "depends_on": [],
  "notes": [],
  "note_records": [],
  "claim": null,
  "fields": {
    "size": "M",
    "flags": [],
    "trees": [
      "core"
    ],
    "trees_literal": false,
    "findings": "G1",
    "discovered_from": "",
    "parent": "",
    "pass": "",
    "group": "Phase 0 — groundwork",
    "reason": "",
    "importance": 2,
    "urgency": 2,
    "effort": null,
    "attempts": 0
  },
  "path": "~/code/my-project/.slicer/slices/S01.json",
  "effective_score": 22
}
```

Item `notes` remains a list of display strings. Additive `note_records` carries
`{id, kind, text, created_at, attempt, by, verified_by, verified_at, stale_after, trust}` in that key order (`trust` is derived, read-only, never stored); `text` is the complete dated
paragraph before adding the kind label. Historical strings retain their exact text,
get stable item-local positional IDs, empty `created_at`, and null `attempt` on reads.
The next index save persists those records under the current index schema (note records arrived in schema 4). New notes use stored UUIDs,
UTC ISO8601 timestamps, and the item's current attempt count.

Use `note ID --kind KIND --text TEXT` for a typed note; omitted kind is untyped.
`note_kinds` is an optional whitelist. Repeat `--notes-kind KIND` on `show` or
`next --ready` to select the union of exact kinds in stored order; `--notes-kind ''`
selects untyped notes and legacy slice strings. Filters never change state or hide
items, and work with `--lean`, `--section`, and ready batches. Ready item payloads
include `notes` and `note_records`; section-only show payloads add those fields and
`slice_notes` when a filter is supplied. Lean output omits empty matches as usual.
`next --notes-kind` without `--ready` returns `usage`.

For bounded pickup context, add `--min-trust TIER` and/or `--notes-budget N`
to `show` or `next --ready`, including batches. Tiers are `unverified`,
`machine-confirmed`, and `human-reviewed`; omitted trust means unverified and
omitted budget means unlimited. A budget must be a nonnegative integer. Either
option enables expiry filtering at one captured UTC read time; ordinary and
kind-only reads keep expired audit notes. Kinds compose by OR, then minimum trust
applies. Ranking is higher derived verification trust, newer known creation time,
then stored order. Authorship alone never raises trust.

Whole display strings share the character budget, including one newline between
selected strings; count Unicode code points, skip oversized notes, and continue.
Each batch item gets its own budget. Legacy slice notes are untyped/unverified and
use read-only IDs `slice-legacy-N` (one-based original order).

Requested packs always expose `note_pack` beside item `notes`/`note_records` (on
`item` for ready entries), even under lean and when empty:

```json
{"selected_ids": [], "omitted": [], "characters": 0, "budget": null, "min_trust": "unverified"}
```

`omitted` entries are `{id, reason}`; reason precedence is `kind`, `expired`,
`trust`, `budget`. Selected IDs follow ranking order; omissions follow original
item-note then slice-note order. The budget covers display text only, excluding
manifest/metadata/sections. Section/context projections expose the same manifest
and selected notes; legacy strings stay in the slice-note projection. Packing does
not hide items/sections, promote trust, save state, or change generated documents.
`next` packing without `--ready`, invalid trust, or a negative budget is `usage`
before any `--start` writes.

Filing identity is `fields.key`, a case-sensitive string (empty when absent).
`add --key KEY` trims surrounding whitespace and rejects explicit blank input
(`blank_key`). It returns the item plus `existing: false` on creation or
`existing: true` for a retry, even if replacement payload fields differ or are
invalid. Done and retired items reserve their keys. Retries leave state, IDs,
history, slices and renders untouched, even with `--render --strict`.
Keys belong to this checkout; no cross-branch or machine synchronization occurs.

Two shapes recur. An **item** is the object above minus `path` and `effective_score`; its soft fields are
nested under `fields`, the pass key is spelled `pass`, and `importance`/`urgency` (each
1–3) are the Eisenhower axes. `effort` is an optional `1`–`3` estimate, or `null` when unset; `--json --lean` omits the null. `attempts` counts fresh implementation starts from open or a custom queue into the configured started status. Resumes, review claims, rejection, and `set --status` do not increment it. Start → reject → restart records two attempts. Historical values are preserved and may overcount; history is not authoritative enough to reconstruct them. Owners may correct them with `set ID --attempts N` before adopting a cap. The index schema is 9, and `--json --lean` omits a 0. The combined score and quadrant are derived, not stored, so
they are not in the JSON — compute `importance*10 + urgency`, or read the ranking from
`list --sort score`. `--json` without `--lean` is always this full shape.

`--json --lean` omits empty strings, empty lists, empty objects, and nulls, plus
`trees_literal` when it is false, `short_title` when it equals `title`, `attempts` when it is 0, and `path` on an
item. A missing key means empty or that default, not unknown. `has_slice: false` and the
importance and urgency numbers stay, including the default 2. `item: null` stays, so an
empty `next` is still recognisable. `path` is only in the full profile. Text output
ignores `--lean`. `find` is the exception to "the same items, fewer empty fields": its lean rows are only `{id, title, status, has_slice, match}`. The `list --envelope` keys `items` and `only_in_sibling` stay present even when empty. Error envelopes stay full. Lean output is compact, one line with no indentation; full --json stays indented.

A **slice** is
`{id, title, lead[], depends_note, findings_note, size, flags[], trees_note,
trees_plural, sections: [{heading, body}], notes[]}`.

| Command | Payload |
|---|---|
| `ai instructions` | `{instructions}`. `--rest` returns only what the skill does not carry, the same text for every project. Bare `slicer ai` and `slicer ai --json` are this command. Generic Markdown, or the handoff loop when the project sets `implement_finish` to `handoff`; a nonempty `handoff_requires_note_kind` adds the required report kind and filing command. No project, or an unreadable config or index, is the generic text, a stderr warning, and exit 0. No lock or write |
| `ai skill` | `{skill}` containing the `SKILL.md` text, chosen the same way. The handoff skill's step 4 is only handoff, and that text does not contain the done command. With `--output` or `--install` it is `{skill, written}`, `written` being the absolute paths written, in order |
| `ai relay` | `{contract, relay, setup, order, land, report, outcomes, stop, changes, roles, gate, finding_kinds, conformance}` (keys in that order): `setup` lists `{key, text}` for the eight Setup bullets (keys `spec_check`, `roles`, `split`, `sliver`, `role_models`, `orchestrator`, `environment`, `end_point`); `end_point` also carries `options: ["finish", "commit", "landed"]`. `order` is a list of `{step, text}`. `order` holds outer-session steps 1–6; step 5 is Approve (the wait record, what the owner sees, the human-check notes and the fix note) and step 6 points at Land. `land` is a list of `{step, text}` for the ten steps of the `## Land` section, in order (rerun the checks, commit, merge main when it moved, render, check, rerun the suite, `git merge --ff-only`, `relay-run end`, `slicer land`, then commit, push when authorized, remove the worktree and branch); `ai relay-orchestrator` has no `land`. `report` is `{max_lines: 30, lines: [{key, template}]}` with line keys `contract`, `slice`, `worktree`, `rounds`, `tests`, `diff_stat`, `deviations`, `later_notes`, `owner_needed`, `role_steps`, `nothing_left_running`, in that order. `outcomes` is `["pass", "fail", "stopped"]`, shared with `relay-run end --outcome`; `stop` lists the three stop conditions as strings. `changes` lists `{version, text}` in ascending version order, from 5 through the current contract version; each `text` is one nonempty line recording what that version changed. All `text` values and stop strings join prose line breaks with single spaces. `--lean` preserves all contract lists, even empty ones. `roles` (`ai relay` only) is a list of `{role, text}` for the seven role paragraphs of the Roles section, in prose order (`outer_session`, `orchestrator`, `implementer`, `reviewer`, `architect`, `ui_pass`, `spec_check`); the nesting fallback, the split paragraph and the models sentence stay prose. `gate` is `{criteria: [...], verdicts: ["VERDICT: PASS", "VERDICT: FAIL"]}`: the five gate criteria and the two first lines the gate may take, the lists the Gate section renders. `finding_kinds` is a list of `{kind, text}` for `fix`, `owner` and `later`, the Gate section's bullets. `conformance` is a list of `{key, text}` for the nine obligations of the Conformance section, with keys `role_order`, `gate_rule`, `finding_kinds`, `stop_rules`, `ledger`, `finish_after_pass`, `run_report`, `worktree_scope` and `commit_scope`. Each new list is built from the data that renders its prose, and the text agrees with it. The new keys come after `changes`, so every earlier key keeps its position. A `check_command` (s379) and a pin of the contract version (s387) are not part of the payload yet; `contract` is the version. `contract` is `"relay v8"`, the version on the text's first line (`Contract: relay v8`), and `relay` is the same Markdown as the text. Chosen the same way as `ai instructions`: the handoff close when the project sets `implement_finish` to `handoff` (that text does not name `done`), otherwise the done close. A nonempty `handoff_requires_note_kind` adds the required report kind and filing command. A project with `.slicer/relay-roles.json` gets a `## Roles matrix` section after Setup holding the matrix as Markdown, and Setup gets the propose line and the `relay-roles recommend` command; a project without it gets the `relay-roles init` line in Setup. A leftover `.slicer/relay-roles.md` is ignored with a stderr warning. A corrupt matrix is `corrupt` and a newer one `schema_too_new` (exit 3). No project, or an unreadable config or index, is the generic text, a stderr warning, and exit 0. No lock or write, except `--init-roles` (below) |
| `ai relay-orchestrator` | `{contract, orchestrator, order, report, outcomes, stop, changes, gate, finding_kinds, conformance}` (keys in that order): `order` is a list of `{step, text}`. `order` holds orchestrator steps 1–4 with the configured finish step. `report` is `{max_lines: 30, lines: [{key, template}]}` with line keys `contract`, `slice`, `worktree`, `rounds`, `tests`, `diff_stat`, `deviations`, `later_notes`, `owner_needed`, `role_steps`, `nothing_left_running`, in that order. `outcomes` is `["pass", "fail", "stopped"]`, shared with `relay-run end --outcome`; `stop` lists the three stop conditions as strings. `changes` lists `{version, text}` in ascending version order, from 5 through the current contract version; each `text` is one nonempty line recording what that version changed. All `text` values and stop strings join prose line breaks with single spaces. `--lean` preserves all contract lists, even empty ones. `gate` is `{criteria: [...], verdicts: ["VERDICT: PASS", "VERDICT: FAIL"]}`: the five gate criteria and the two first lines the gate may take, the lists the Gate section renders. `finding_kinds` is a list of `{kind, text}` for `fix`, `owner` and `later`, the Gate section's bullets. `conformance` is a list of `{key, text}` for the nine obligations of the Conformance section, with keys `role_order`, `gate_rule`, `finding_kinds`, `stop_rules`, `ledger`, `finish_after_pass`, `run_report`, `worktree_scope` and `commit_scope`. Each new list is built from the data that renders its prose, and the text agrees with it. The new keys come after `changes`, so every earlier key keeps its position. A `check_command` (s379) and a pin of the contract version (s387) are not part of the payload yet; `contract` is the version. `ai relay-orchestrator` has no `roles`, as it has no `setup` or `land`: its text has no Roles section. `contract` is `"relay v8"` as in `ai relay`, and `orchestrator` is the orchestrator's self-contained contract and run report as Markdown, with the same marker as its first line. Its Order holds only the orchestrator's steps; Gate, Every brief and Stop share their text with `ai relay`. The orchestrator does not run `ai relay` to read its rules; a program orchestrator reads the `roles` it needs from `ai relay --json`. The finish step and report kind come from the project the way `ai relay` reads them (`handoff` or `done`, and `handoff_requires_note_kind`). No project, or an unreadable config or index, is the generic text, a stderr warning, and exit 0. No lock or write; `--lean` changes nothing |
| `ai relay-brief ID --worktree PATH --branch NAME --run RUN --step N --well-specified yes\|no --split yes\|no [--prefer-agent AGENT] [--select ROLE=ROW_ID ...] [--parallel ID ...]` | `{brief, roles, contract}`: `brief` is the same text as stdout, expanded from the project's `relay-brief.md` (built-in fallback for projects without it); `roles` is the full five-role array from `relay-roles recommend`, including its rows, alternatives, null unmatched rows and selection reasons; `contract` is the live relay marker (`"relay v8"`). `--lean` only compacts this shape. Requires a specified slice and a role matrix; unknown item is `no_such_item`, an item with no slice is `no_slice`, an empty required section is `unspecified`, absent matrix is `no_relay_roles`. Selection flags have the same meaning and validation as `recommend`. Parallel IDs are validated, deduplicated and exclude the slice itself; no file overlaps are computed. For `handoff`, the brief requires start before work, then a rendered closing note (with the configured required kind, if any) and handoff only after PASS; `done` projects have no override. End point is a commit titled `ID: TITLE`. No lock, write, agent spawn or run recording |
| `ai relay --init-roles` | `{written}`, a one-element list `[PATH]` holding the absolute path of `.slicer/relay-roles.json`, when it writes the example matrix and renders it. Needs a project; an existing file is `state` (exit 2) unless `--force` is given, and `--force` without `--init-roles` is `usage` |
| `ai relay-roles` / `relay-roles show` | `{relay_roles, rows, rules}`: the Markdown render without its banner, then each row as `{id, role, when: {size, effort, importance, urgency, well_specified, split, sliver}, agent, vendor, model, reasoning, note}` and each rule as `{id, text}`. `--lean` keeps `rows` and `rules` even when empty. Needs a project, and reads without a lock or a write. A missing file is exit 2 with code `no_relay_roles` |
| `relay-roles recommend` | `{id, well_specified, split, sliver, roles, unmatched}`: the two answers as booleans, `sliver`, the boolean slicer infers (true when the item has a `parent`, so it is a sliver), `roles` always five `{role, row, alternatives, selected_by}` entries in the order implementer, reviewer, gate, architect, orchestrator, and `unmatched`, every role whose `row` is `null` (`[]` when none). `row` is the selected row as in `show`; `alternatives` holds the full rows of every other row that matches the slice, in matrix order (`[]` when none); `selected_by` is `override` (a `--select` for the role), `preferred_agent` (the first matching row whose agent equals `--prefer-agent`), `matrix_order` (the first matching row) or `unmatched`. `--lean` keeps the full shape (a `null` row, an empty `alternatives` and an empty `unmatched` stay) and only makes the output compact. Both `--well-specified` and `--split` are required (yes or no; anything else is `bad_role_field`, a missing flag is `usage`). `--prefer-agent AGENT` and repeatable `--select ROLE=ROW_ID` are optional and apply to this call only. A malformed selector, an unknown role, a role selected twice, a row of another role, a row whose conditions do not match the slice or a blank agent is `bad_role_field`; a row id the matrix does not hold is `unknown_role_row`. An unknown item is `no_such_item` and a project without the file is `no_relay_roles` (both exit 2). Reads without a lock or a write, launches nothing and probes no credentials, and stores nothing. Added in `relay v5`; a consumer that read the first four `roles` entries keeps working |
| `relay-roles init` | `{written: [PATH]}`, plus `removed: [PATH]` when `--replace-legacy` deleted `.slicer/relay-roles.md`. `written` is empty when the matrix already existed and only the old file was removed. An existing file is `state` (exit 2) unless `--force` (or `--replace-legacy` with a leftover `.md`); a path that is not a file is `io` (exit 3) |
| `relay-roles add` / `set` / `remove` / `move` | the row, as in `show`. `remove` returns the row it dropped. A `set` or `move` that changes nothing returns the row and writes nothing |
| `relay-roles rule-add` / `rule-edit` / `rule-remove` | the rule `{id, text}` |
| `relay-run begin` | `{run}`, the run as in `list`; the text is only its id |
| `relay-run step` | `{run, step}`: the run as in `list` and the step just added or updated; the text is only the step id. `--phase wait` makes the step's `phase` `wait` with `waiting_on` `owner` (the default) or `approval`; `--waiting-on` with another phase is `usage` (exit 2) |
| `relay-run end` | `{run, closed_steps}`: `run` has `state: "ended"` and the `outcome`, and `closed_steps` lists the ids of the steps `--close-steps done\|fail` just marked (`[]` without it or when none was running). The text is `ended RUN OUTCOME`, plus `; absorbed N events from local ledgers` when it copied that many events from local files into the shared ledger, plus `; closed steps: 1, 4` when it closed any. When steps are still running after the end (no `--close-steps`), one stderr line `slicer: ended RUN with running steps: 1, 4` names them; stdout and the exit code are unchanged. A `--close-steps` phase other than `done` or `fail` is `bad_run_field` (exit 2) and writes nothing |
| `relay-run list [RUN]` | `{runs}`, oldest first by `begun_at`, empty when there is no ledger. `RUN` keeps only that run in the same `{runs}` shape; an unknown id is `unknown_run` (exit 2), naming the ledger paths searched. `--active` is applied after the id is found, so a known ended or stale run gives `{runs: []}` and exit 0. It reads the repo-wide ledger (`<git common dir>/slicer/relay-runs.jsonl`) and the per-worktree `.slicer/relay-runs.jsonl` files (legacy runs, and the events a sandboxed role could only write there), folds the events of every file into one run ordered by time, and lists a run once. Each run is `{id, items, worktree, begun_at, updated_at, state, outcome, counts, steps}`: `state` is `active`, `stale` or `ended` (a run with a waiting step is never `stale`, see [Waiting on the owner](commands.md#waiting-on-the-owner)), `outcome` is `pass`, `fail`, `stopped` or `""` while unended, and `counts` is `{steps, running, waiting, subagents, roles}`: `steps` counts every step, `running` the steps whose phase is `start` and is 0 once the run has an outcome, `waiting` the steps whose phase is `wait` (also 0 once the run has an outcome), `subagents` the steps that have a parent, and `roles` maps each role to the number of top-level steps (no parent) with it, so a subagent is counted only in `subagents`. A caller finds a step's subagents with `Run.subagents_of(step_id)`. A step is `{id, role, agent, vendor, model, reasoning, phase, waiting_on, task, parent, begun_at, updated_at}`: `id` and `parent` are strings (`parent` is `""` for a top-level step); a step the shared ledger issued has a plain number as `id`, and one the fallback recorded in the checkout's own file has `c` plus eight hex digits, `-` and a number (see [Sandboxes](commands.md#sandboxes)), `phase` is `start`, `done`, `fail` or `wait` (`waiting_on` is then `owner` or `approval`, and `""` for the other phases), and times are UTC `YYYY-MM-DDTHH:MM:SSZ`. `--lean` only compacts the output, so every key stays. `slicer.relayrun.list_runs(root, now=None, siblings=True, active=False)` returns the same runs as objects with `to_dict()`, for callers such as the TUI; `siblings` adds the local ledgers of the sibling worktrees, the shared ledger and this checkout's file are always read. `relay-run begin`, `step` and `end` write the checkout's own `.slicer/relay-runs.jsonl` when the shared ledger or its lock refuses the write with a permission error (a sandbox), print one stderr line naming both paths, and leave the `--json` payload as it is |
| `init` | `{root, dir}`; with `--relay-roles`, also `relay_roles: PATH`, the absolute path of `.slicer/relay-roles.json`, whether this call wrote the example or kept an existing file |
| `setup-git` | array of the six `git config` command strings; needs no project |
| `setup-git --hook` | `{hook, install}`: the post-merge shell script (starting with `#!/bin/sh` and ending with a newline) and its install command, using `git rev-parse --git-path hooks/post-merge` to respect `core.hooksPath`. Needs no project and writes nothing. Mutually exclusive with `--attributes`; together they fail with `usage`. The hook runs `slicer render` only for `check --json` exit 1 with top-level `stale_render_kind: roadmap_only`, leaves rewritten files for the person to commit, reports other failures with `slicer check`, and always exits 0 |
| `setup-git --attributes` | `{commands, attributes}`: `commands` is the same array, and `attributes` is `{path, changed}`, the absolute path of `.slicer/.gitattributes` and whether this call wrote it (`false` when the bytes already matched). Needs a project; `--lean` keeps `changed: false`. Text stdout is the command lines alone, and the `wrote` or `unchanged` report is on stderr, followed by `added LINES to PATH` when it also added lines to `.slicer/.gitignore` (the JSON does not list them) |
| `merge-index` | no payload and no `--json`; Git runs it as a merge driver with `%O %A %B`. Exit 0 wrote the merged index to `OURS`; exit 1 left conflict markers there |
| `merge-relay-roles` | no payload and no `--json`; Git runs it as a merge driver with `%O %A %B`. Exit 0 wrote the merged matrix to `OURS`, with a stderr line per renumbered row or rule. A refusal leaves `OURS` untouched and uses the usual error shape: `merge_conflict` (exit 2) for a disagreement, `corrupt` (exit 3) or `schema_too_new` for an unreadable input |
| `list` | array of items by default; `--envelope` with `--json` returns `{items: [...], only_in_sibling: [...]}`, with both keys always present, including under `--lean`. `items` equals the default array for the same filters, sorts and lean profile. `only_in_sibling` matches the list in `status --json` (or `[]` when absent), independently of item filters and sorts. `--envelope` without `--json` exits 2 with code `usage`. Each item includes `claim`: `{"owner", "at"}` or null, plus derived `in_work_elsewhere`: an array of `{worktree, owner, status}` objects (`status` is the sibling's status key), or `[]`. A sibling counts when the item is claimed or in work, and when it is review or done while this checkout's status differs and is not done. The same status on both sides is omitted, as is an empty `review_status`. Text adds a CLAIM column (local owner, `*` for local in-progress with no claim, `wt:NAME` for sibling work, `wt:NAME+N` for multiple siblings, otherwise `-`), and, when the roadmap declares or names any pass, a PASS column after it (the pass key, or `-`); JSON carries the pass as `fields.pass` either way. Local claim or started state takes precedence in text; JSON still lists all siblings. A done item is never shown as locally claimed. Sibling detection uses only this machine's Git worktrees and their `.slicer/index.json` files, and only when this project is the git worktree root. Text ends with an `Only in a sibling` block when a sibling has an open, unclaimed item whose id this checkout lacks; the default JSON array does not carry it (use `list --json --envelope` or read `only_in_sibling` from `status --json`). Done and retired statuses are omitted unless `--all` is set or `--status` names them; `--all` together with `--status` is `usage`. `--in-work` keeps statuses in `config.in_work()` (started, plus reviewing when set), including a started item whose claim was released. `--review` keeps stored review items and stored-open items whose siblings report the configured review key; reviewing alone does not qualify. `--status` naming the review key includes the same rows. The open filter still includes those open items. Text shows this checkout's review label; JSON status stays open and `in_work_elsewhere` retains the sibling key. Either flag replaces the default omission of done and retired. Combining either with the other, with `--status`, or with `--all` is `usage`, as is an empty role. A role that matches nothing is `[]` in JSON and `no matching items` in text, exit 0, with no hidden-count line. Other filters and sorts still apply. An item that is in work only in a sibling worktree stays out of the in-work view; a sibling handoff adds an open item to the review view. Default order preserves readiness groups (unblocked in-work, unblocked open, other visible rows, then parked). Within each group, declared pass order precedes descending effective score, with stored queue order breaking ties. Empty passes merge by effective score; named undeclared passes follow declared passes. Without declared passes, score ordering is unchanged. `next` uses the same ranking for its eligible pool. Repeatable `--flag` keeps an item that has any named flag and combines with `--status`, `--tree`, and `--pass`. `--sort score` is that same set in flat score order. `--sort effort` orders `fields.effort` from 1 to 3 and puts null last, without writing state. `sort --by effort` persists that order |
| `show ID` | item, plus `slice` when it has one, plus `slivers`: the items whose `fields.parent` names this one, in index order, each `{id, status, title}` (`[]` when none; `--lean` drops it). Text lists them under a `Slivers:` line after the slice, and a sliver's slice header carries a `Parent: ID title` line. Also `citations`: the catalog records named by `fields.cites`, in cite order, each `{id, kind, title, status, reason, successor}`. `--lean` drops an empty `citations` list. Also `paths`: the paths `deps --overlap` starts from, before `overlap_ignore` removes any. They come from the backticked spans in `files_section`: each path once, in first-named order; a leading `./` and one `:LINE` or `:LINE-LINE` suffix are dropped; a directory keeps its trailing slash; a span with a space or a character outside letters, digits and `._@+-/` is not a path; a span with a slash needs a file extension, a trailing slash, or a first component that is a project root entry; a slash-less name needs a file extension and a match to a project root entry. `[]` when the item has no slice, no such section, or `files_section` is unset; `--lean` drops an empty list. The key follows `slice` when there is one, else comes before `citations`. With one `--section NAME`, `{id, section, body}`; repeat `--section` to return only those sections; add `--context` for `{id, title, depends_on, boundary, sections}` |
| `sections` | `{sections, required}`. Text is one configured section per line, with `required` on a row whose heading is in `required`. Reads config only: no index, lock, or write. A missing `required_sections` key reports Implement and Check |
| `config` | with no key, the whole effective config (the same object `config.json` loads to, including `implement_finish`, `done_status` and `review_status`); with `KEY`, exactly `{key, value}`. A dotted key reaches into a nested object. An unknown key is `usage`, and the message names the top-level keys. A read reads config only: no index, lock, or write. An unreadable config or a directory outside any project fails as `sections` does. `--lean` drops an empty `value`. With a `VALUE` or `--unset`, `config` is a write: it patches only that key in `config.json` under the writer lock, reading `VALUE` as the key's type (a string literally, a whole number, `true` or `false`, JSON for a list or an object), and writes nothing unless the patched config loads. It prints `{key, value, previous}`, the effective value now and before (`null` for an optional key that is absent). A value that does not fit the key, or a config that fails validation, is `bad_config_value`; an unknown key, `version`, a write that would change the id prefix (use `id-prefix`; the whole `id` object included), `--unset` with a `VALUE` or without a `KEY`, and `--render`, `--strict` or `--check` on a read are `usage`. A write that would change `id.width` once the index has items is `state` (exit 2) and writes nothing. A write logs one `config` entry named `config.KEY`; one that changes nothing logs nothing. `--render`, `--strict` and `--check` behave as for other mutations |
| `template list` | array of `{name, customized, missing}`, one per template in the order `slice.md`, `roadmap.md`, `row.md`, `relay-brief.md`. `customized` is false when the project's file has the built-in text byte for byte or is missing, and true when it differs; `missing` says the file is not there, and a missing built-in template counts as the default until `set` or `reset` writes it. A project created before `relay-brief.md` existed has no such file, so it shows `missing` there and `template show relay-brief.md` fails until `template reset relay-brief.md` writes it; `ai relay-brief` uses the built-in text meanwhile. Reads the files only: no index, lock, or write |
| `template show NAME` | `{name, text}`, the project's file as stored, or with `--default` the built-in text. Text mode prints the bytes with no newline added. `--default` needs no project. A deleted file is a `state` error whose message names `slicer template reset NAME`. A `NAME` that is not a template is `unknown_template`. No lock or write |
| `template set NAME` / `template reset NAME` | `{name, changed, customized}`. `set` takes the whole new text from exactly one of `--file` or `--stdin` (neither, or both, is `usage`; non-UTF-8 is `io`) and keeps its bytes. It expands the text against stand-in data and renders a row template over the project's items before it writes, so text with an unknown placeholder, or a row whose cell count differs from the table header, is `bad_template` (exit 2) and writes nothing, also under `--strict`. `reset` writes the built-in text, recreating a deleted file. Both take the writer lock and accept `--render`, `--strict` and `--check`. Text already in the file gives `changed: false`: nothing written, logged or rendered. A change logs one `template` entry named `template.NAME` with `from` and `to` each `default`, `custom` or `missing` |
| `recommended-workflow` | `{"workflow": "..."}`, the text of `docs/generic-recommended-project-workflow.md`; text is the same document. It reads no project, so it prints the same thing everywhere, and it takes no lock and writes nothing. If the file cannot be read, exit 3 with code `io` naming the path |
| `next` | `held` is included when nonempty on every form, including empty queues and batches: `[{id, title, flags}]`, with only matching configured hold flags. Held candidates are excluded from selection without changing status; dependencies on them remain unmet. `hold_flags` defaults to `["needs-owner"]`; `[]` disables holding. Otherwise item plus `path` and `effective_score`. Text names `slicer show ID` when the item has a slice and omits the slice path unless `--path` is set; `--path` does not change this JSON, and `--lean` still drops `path` on an item. A row with no slice does not gain a show hint. The promote hint stays on `next --ready` and `next --show`. `next --show` text prints the slice without its generated banner, so it names no source path either; `show` and the files under `.slicer/render/slices/` keep the banner. With `--show`, also `slice` (the same object `show` returns, when the item has one); with `--ready`, `{item, slice, blocked}` where `item` is `{id, title, status, depends_on, attempts, notes, note_records, effective_score, path}` and `slice` is included when the item has one; or `{"item": null, "blocked": [...]}`. Repeat `--section` with `--ready` and that slice is `{boundary, notes, sections}` for those headings only. Omitting `--section` keeps the full slice. `--section` without `--ready` is `usage`. `unspecified` is present when a slice was skipped because a required section is empty (`required_sections`, or Implement and Check when that key is absent): `[{"id", "missing"}]`. Fill those with `slicer edit ID --section NAME`. `in_work_elsewhere` is present when an eligible item was skipped because a sibling Git worktree has it claimed or in work, or in review or done while this checkout's status differs and is not done: `[{"id", "worktree", "owner", "status"}]` (`status` is the sibling's status key), one row per worktree. The same status on both sides is not reported. An item also started or claimed in this checkout is not skipped. A started item is returned ahead of every open one. `--ready` and `--show` together are `usage`. `--status review` (the configured review status) uses the review queue with the same payload: reviewing items first, then review; `--start` then moves the item to reviewing. Any other `--status` must be a custom status (not a lifecycle role) or it is `usage`, with the choices named. A custom queue ranks your own claims first, then unclaimed items; `--start` claims in place (no status change, no attempt) and `--start-to KEY` also moves the item to a configured status other than done, retired, parked, review or reviewing, counting one attempt when that is the started status. `claimed_elsewhere` is present when an item was skipped because another owner claimed it: `[{"id", "owner"}]`. After `--start-to` changes the status, read-only `next --status` for the old queue no longer returns the item. `--batch K` returns `{"items": [...], "blocked": [...]}` from that same pool, optionally narrowed by one `--tree` and an exact `--size`. Each element is the plain `next` object; with `--ready`, each is `{item, slice}` (so `attempts` stays on `item`) and `blocked` stays on the batch. `--section` trims every slice the same way. A dependent is included after its dependency when that dependency is in the batch. Items still waiting on something outside the batch are `blocked`; items left out only because K was reached are not. `unspecified` and `in_work_elsewhere` appear only when non-empty, in the same shapes as `next`. `--batch K --disjoint` also takes the batch greedily in queue order, skipping an item whose `files_section` paths (backticked repo paths, less `overlap_ignore`) meet a chosen item's; `unknown_files` (`[id, ...]`, only when non-empty) lists items skipped because that section names no path. An unmet dependency remains a blocker even when already chosen in a disjoint batch: its dependent is excluded and listed in `blocked`, naming that dependency. Plain `--batch` keeps including dependents after dependencies. It warns on stderr when no item has the configured heading, leaving JSON stdout unchanged. It needs `--batch` and a configured `files_section`, else `usage`. An empty batch is exit 2 with `{"items": [], "blocked": [...]}` and no error object. `--start --render` (with `--strict` if wanted) renders in the same step and leaves the payload unchanged; `--render` without `--start` is `usage`. `--start` claims every returned id under one lock and passes `--owner` through; a failure claims none, and an empty batch does not start. Capped atomic selection locks before loading even when the resulting batch is empty. `--batch` with `--show`, `-n` (including `-n 0`), or `--status` is `usage`, as is K < 1. `--tree` and `--size` without `--batch` narrow a single `next` |
| `plan-parallel` | `{width, waves, in_flight, unplaced, blocked}`. `waves` is `[{wave, items: [{id, title, reason}]}]`, numbered from 1 in run order. `reason` is why the item waited past the wave before: `after: ID` (a dependency in that wave), `shares: ID PATH` (a counted `files_section` path that wave held; `ID` may be in-flight work, which holds wave 1 only) or `width` (that wave was full); it is `""` for an item ready from the start, except a first-wave item that depends on in-flight work, which reads `after: ID`. `in_flight` is `[{id, title}]` for started items. `unplaced` is `[{id, title, reason: "no_files"}]` for items whose files section names no path. `blocked` is `[{id, waiting_on}]` for items waiting on a dependency that is never placed, including a blocked item that a sibling worktree has in work. `held`, `capped`, `unspecified` and `in_work_elsewhere` appear only when non-empty (in that order, after the five main keys), in the shapes `next` uses. With `--lean`, empty `reason` strings are dropped but the five main keys stay. Needs a configured `files_section` (else `usage`). An empty plan (no waves) exits 2 with the same shape and no error object. Reads only: no lock, claim or write |
| `next-id` | `{"id": "S02"}` and nothing else; text is the bare id. Does not allocate, lock, log, or accept `--render`. When a sibling git worktree's `next_id` is higher, the id starts above it, as `add` and `import` do |
| `id-prefix` | with no argument, `{"prefix": "S"}`; with `NEW`, `{from, to, changed, next_id}`, plus `dry_run: true` under `--dry-run`. A prefix that is not the current one in another case is `usage`, and nothing is written |
| `add`, `set`, `start`, `done`, `park`, `unpark`, `release`, `handoff`, `reject` | the item. `reject` needs `--note` (the verdict), refuses (`state`, nothing written) an item that is not in review or reviewing, and refuses (`usage`) a `--to` that is not a configured status or is a lifecycle role; it clears `claim` and logs one `reject` entry carrying the verdict. `handoff` sets `review_status`, clears `claim`, and refuses (`state`, nothing written) an item that is not started or in review, or has no slice; one already in review with no claim is a no-op. `start` on a review item (or a reviewing one whose claim was released) sets `reviewing_status` instead of started, so `next` never returns it. `start` claims an unclaimed item (`claim.owner` and `claim.at`) and writes a non-blocking stderr warning when another branch or worktree name refers to the id (the current checkout does not count). The exit code is unchanged. `next`, including `next --start`, does not warn; `next --start` does claim. `release` clears `claim` and leaves status. `done` clears `claim`. `done --render --check` and `handoff --render --check` run `slicer check` after the change lands. Stdout stays the item. For `add`, `--print-id` prints only the id, including on a keyed retry; it cannot combine with `--json` (`usage`, nothing written), and sends the `--render` summary to stderr. The JSON payload is unchanged. A failing check exits 1 and writes the findings to stderr, ending with "slicer: VERB landed, but check failed; run slicer check for the report". `--check` without `--render` is `usage` and writes nothing. `add` of an open or started item left at importance 2, urgency 2 and no effort writes a non-blocking stderr hint to score it; the payload and exit code are unchanged. `add` takes its id above any sibling git worktree's `next_id`, so two worktrees do not file different items under one id; an explicit `--id` is unchanged, except that it is refused with `id_taken_elsewhere` (exit 2, nothing written) when a sibling worktree filed that id as an item of its own |
| `land` | `{item, branch, tip, worktree, commands}`: the item as `done` returns it, the `--branch` as given, its tip as a full commit name, the absolute path of the other checkout that has the branch checked out (`null` when none), and `commands`, the git commands left, in the order to run them (the commit, then `git worktree remove PATH` when `worktree` is set, then `git branch -d BRANCH`). Text ends with the same commands, one per line. Each printed git command is anchored with `git -C ROOT` (absolute, shell-quoted). `--owner NAME` uses the same actor precedence and done attribution as `done --owner`. The refusals are `invalid_branch` (not an exact short local branch name, the checked-out branch, or a project that is not in a git repository), `not_merged` and `run_active`, all exit 2 with nothing written. Like `done --render --check`, a failing check exits 1 with the findings on stderr after this output |
| `promote` | the slice (`--file`/`--stdin` fills its sections from a one-item outline) |
| `import` | `{items, created, reused, entries, promoted, by_status, ids, depends_edges, off_schema_sections, warnings, problems, preamble}`. `entries` contains `{title, id, existing}` for each requested title in input order; `ids` covers all entries and `items` is the input count. `created` and `reused` count new and reused records; `promoted` counts new slice files, `by_status` counts mapped records at their actual status, and `depends_edges` counts new edges only. An all-existing import writes nothing and bypasses optional rendering. `warnings` names new open or started items left unscored (importance 2, urgency 2, no effort). `preamble` is the leading prose, or null when the outline has none. Ids start above any sibling git worktree's `next_id`, in `--dry-run` and in the real run alike |
| `migrate` | a similar report, plus round-trip and reconciliation counts |
| `remove` | retire: the item plus `mode: "retire"`; purge: `{id, mode, id_freed, reason, file_removed}`; `--dry-run`: `{id, mode, dry_run, blockers, file, id_freed, reason}` and nothing is written |
| `retire` | the same as `remove ID --reason`: the item plus `mode: "retire"`, the `--dry-run` payload and the error codes of `remove`; in an error envelope `command` is `retire`. No purge payload, since `retire` has no `--purge` |
| `move` | `{id, position}` |
| `sort` | `{by, moved}` |
| `edit` | `{id, section}`; boundary edits return `{id, boundary}`; lead edits return `{id, lead}`, a list of paragraphs |
| `refs` | `{fix, items, references, counts, fixed, drift}`. `items` is the ids scanned, in index order, or in the order named. `counts` has every status key (`ok`, `unanchored`, `missing_file`, `ambiguous_path`, `out_of_range`, `moved`, `ambiguous_symbol`, `symbol_gone`), zeros included. `drift` is the number of references that are neither `ok` nor an in-range `unanchored` and were not fixed; `fixed` is how many `--fix` rewrote. Each reference is `{id, where, line, ref, path, file, start, end, approx, anchor, status, found, to, paths, lines, fixed}`: `where` is `lead`, `boundary`, `section HEADING` or `note N` (1-based) and `line` counts within it; `ref` is the reference as written, `path` the path part and `file` the root-relative file it resolved to; `start` and `end` are the cited lines (`end` is null for one line); `anchor` is the backticked identifier or null; `found` is the new first line and `to` the rewritten reference when `status` is `moved`; `paths` lists the matches for `ambiguous_path` and `lines` the occurrences for `ambiguous_symbol`; `fixed` is true when `--fix` rewrote it. A fixed reference keeps `status: moved`. Under `--lean`, null and empty values drop out but `items` and `references` stay. Without `--fix` nothing is written. Exit 0 when `drift` is 0, 1 otherwise; an unknown id is `no_such_item` and `--render` or `--strict` without `--fix` is `usage`, both exit 2 |
| `note` | `{id, added}` |
| `decide` | the updated item. Exactly one of `--text`, `--file` or `--stdin` supplies a nonblank owner answer. Appends a `decision` note even under a restrictive `note_kinds`, clears every configured hold flag, and preserves other flags and status. No hold flag is `usage`, naming `hold_flags`; blank or conflicting inputs are `usage`. Supports `--render` and `--strict` |
| `feedback` | append: `{path, entry}` with `at`, `kind`, `text`, and `item` only when given; print: `{path, entries}` (empty array when no log); `--out`: `{path, out}` |
| `feedback-report` | `{repo, dry_run, issues, unmarked}`; each issue has `entry` (sha256 of the entry's header and body), `kind`, `at`, `item` only when stored, and `title`, plus `body` under `--dry-run` or `issue` and `url` after `--yes`. `unmarked` lists issues filed for entries that changed while they were sent, which are not recorded and may be filed again. Both lists stay present under `--lean`. Nothing to file is exit 0 with empty lists |
| `issues-pull` | `{repo, dry_run, created, reused, items}`. `created` and `reused` are id lists; `created` is empty under `--dry-run`, which creates nothing. Each item has `number`, `title` (as stored for a reused row), `key` (`github:OWNER/REPO#NUMBER`), `id` (null for a new row under `--dry-run`), and `existing`. Both lists stay present under `--lean`. Nothing new is exit 0 |
| `note-verify`, `note-attest` | `{id, note_record, trust}`; repeating the same verifier is a no-op (no write, no render) |
| `find` | array of items with `match: {field, snippet}`; with `--lean`, each row is only `{id, title, status, has_slice, match}` |
| `deps` | unblocked-item array; for `deps ID`, `{id, waits_on, blocked_by, dependents}`; mermaid format returns `{format, graph}`; `deps --overlap A B [C ...]` returns `{overlap: [{a, b, shared}]}` with one entry per pair in argument order and `shared` the paths both items name in `files_section`, less `overlap_ignore`. Fewer than two ids, an ID or `--format` alongside, or no `files_section` is `usage` |
| `goals` | `{goals, non_goals, records}`. `records` is `{goal, non_goal}`, active records unless `--retired`. Requirements, constraints, decisions, and assumptions are read with `slicer catalog`, not here |
| `catalog list` / `catalog show ID` | `list` is an array of records. `show` is one record plus `cited_by`, the citing item ids in queue order, including done and retired items. `cite` and `uncite` return the item; a cite that changes nothing sets `changed` false and writes nothing |
| `render` | `{written: [...]}` |
| `sync` | array of `{target, path, stale, detail}` |
| `check` | `{ok, stale_render, stale_render_details, stale_render_kind, orphan_render, stale_sync, problems, warnings}`. `stale_render` stays the sorted list of paths; `stale_render_details` has one `{file, cause, detail}` per path in the same order. `cause` is `missing`, `renderer_format`, `unknown_provenance` (an unstamped legacy banner or an unreadable format), or `content`; `renderer_format` also carries `rendered_format` and `running_format`, and means the renderer's layout differs, so state or templates may also have changed. Run `slicer render` for any of them. `warnings` also names an id that a sibling git worktree holds under a different title when the id is new on both sides, not in their merge base (it would collide on merge), each undeclared pass key that holds an item not done or retired (with the count and the `prose add-pass` command), one line naming what `.slicer/.gitignore` lacks of the lines `init` writes (with `slicer setup-git --attributes` as the fix, to run on main), and a leftover hand-edited `.slicer/relay-roles.md` as `legacy_relay_roles` (nothing reads it any more; the message names `relay-roles init --replace-legacy`, which removes the file and keeps an existing matrix); neither changes `ok` or the exit code. `stale_render_kind` is present only when a render is stale: `roadmap_only` when every stale file is `ROADMAP.md` or `ROADMAP.html` with cause `content`, and nothing else fails (no orphan, stale sync or problem), which is what a merge leaves; the text output then adds a line naming `slicer render`, or `done --render` when landing. `mixed` is any other stale render, including a roadmap render stale beside an orphan, stale sync or problem, and a roadmap render whose cause is `missing`, `renderer_format` or `unknown_provenance`. The key is additive and changes neither `ok` nor the exit code |
| `verify` | `{checked, git, errors, findings: [{level, item, message}]}`. `verify --landed ID [--on REF]` returns `{id, ref, ref_commit, landed, reason, message, commit}` instead. `ref` is `HEAD` unless `--on` names a branch, tag or commit; `ref_commit` is what it resolved to. `landed` is true only when `ref`'s own index has the item done and, for an item with a slice, `ref`'s tree holds the done slice file (under `done_dir`), created by a commit reachable from `ref`; `commit` is that commit (empty for an item with no slice, or when none is found). `reason` is empty when landed, else `other_branch` (the tip of some other branch, remote branch or tag outside `ref`'s history has the item done now, in its own index and, for a slice item, its done slice file; a branch that reopened it does not count, and stashes are ignored), `working_tree_only` (done in the working tree and on no such tip) or `not_done` (everything else, including an index that says done at `ref` with no done slice file there). Exit 0 when landed, 1 when not. An unknown id is `no_such_item`, an unusable id is `bad_id`, a `--on` that names no commit is `bad_ref`, a project outside a git repository is `not_git`, and `--on` without `--landed` is `usage`; all five exit 2 and print no report. An unreadable index at `ref` is exit 3 (`corrupt` or `schema_too_new`); on any other ref it is skipped as no evidence. It reads `ref`'s tree with `git show`, never the working tree, and writes nothing |
| `stats` | `{total, completion, by_status, by_size, by_tree, by_pass, by_tree_status}`. `by_status` and `by_tree_status` are keyed by status key (`open`, `done`, …), the same value as an item's `status`, never by its configured display label; text output shows the labels. `status --json` carries the same `census`, and `import`/`migrate` key their `by_status` the same way |
| `status` | `{next, census, blocked}`, plus `held` when nonempty (same candidates and shape as `next`, a Held block in text), plus `unspecified` and `in_work_elsewhere` when `next` would skip items, in the same shapes, and `only_in_sibling` (`[{id, title, worktree, status}]`, worktree-path then id order) when a sibling worktree has an open, unclaimed item whose id this checkout lacks. A done, review, claimed or in-work sibling row is not in it, and `check` does not fail on it. Text adds an `Only in a sibling` block |
| `log` | array of `{when, item, action, from, to, note}`, newest first. Start, claim, release, handoff and done entries add `by`: who acted, resolved as `--owner`, then `SLICER_CLAIM_OWNER`, then `claim_owner`, then the git user, then the worktree name. `--by NAME` keeps only those. Optional timezone-aware ISO8601 `--since` (inclusive) and `--until` (exclusive) compare instants before limit, preserving original `when` strings |
| `prose list` | `[{ref, lines, preview}]` |

Bound pickups with repeatable `next --flag PATTERN` and `--no-flag PATTERN`.
Patterns match stored flags with case-sensitive shell syntax (`*`, `?`, `[abc]`);
quote them, for example `next --no-flag 'risk-*' --batch 3 --ready --json`.
Any inclusion match keeps an item; any exclusion match removes it, even when
inclusion matches. Untagged items pass without inclusion and fail with it.
Empty patterns are `usage`. `list --flag` still uses literal names.

Filters apply before offsets, batch capacity and skip diagnostics, and combine
with tree/size, ready sections, attempt caps, review selection and a custom
status queue. Excluded items appear in neither candidates nor diagnostics
(`blocked`, `held`, `unspecified`, `capped`, `in_work_elsewhere`, `claimed_elsewhere`). Dependency checks and effective scores retain
the full index, so a filtered-out blocker still blocks its dependent, even in
a batch. Read and `--start` use the same pool; no filters preserve selection.

For unattended implementation, pass `next --max-attempts N` (positive integer).
Fresh pickups at or above N are excluded before offset or batch selection;
started attempts still resume, including after release, and review claims are
unaffected. Read-only selection and atomic `--start` use the same filter; capped
starts load and select while holding the writer lock. The limit applies only to
this call, not direct `start ID`.

Single, ready, show, and batch results add `capped` only when nonempty:
`[{"id": "S01", "attempts": 2, "max_attempts": 2}]`, in queue order.
This report is independent of dependency blockers. An excluded batch blocker
never counts as completed, so dependents stay blocked. If all candidates are
capped, the usual empty result and exit 2 remain, with no error object and no
item-state or history writes. Lean output omits an empty `capped` array.

## Every project under a directory (`-r`)

`list`, `check`, `status`, `stats` and `next` take `-r`/`--recursive`. It finds every project
at or below the start directory (`--root`, or the working directory; it never walks up) and runs
the command once in each, reading only. It takes no lock. `next -r` with `--start`, `--render`,
`--owner` or `--start-to` is `usage` and writes nothing. The walk skips dot-directories,
`node_modules` and symlinked directories, and keeps descending inside a project. Only directory
names below the start are skipped: an explicit start directory is walked even if it is itself a
dot-directory.

```console
$ slicer check -r --json --lean
{"projects":[{"path":".","exit":0,"result":{"ok":true,...}},{"path":"tools/api","exit":1,"result":{"ok":false,...}}]}
```

- `path` is POSIX and relative to the start directory (`.` for the start directory itself);
  projects come in sorted path order.
- `result` is exactly what the command prints without `-r`, including under `--lean`. A project
  that fails to load has `error` (`{"code", "message"}`) in place of `result`, and its `exit` is
  the code's usual exit (3 for `corrupt`, `locked`, `io`, `config`, `schema_too_new`; 2 otherwise).
  One failing project never stops the others.
- The process exit is the highest `exit` of any project. `next -r` returns one next item per
  project and never ranks across projects.
- Text output is a `== PATH ==` header and that project's normal text, with a blank line between
  projects. A project that fails prints `error: MESSAGE` under its header on stdout. Nothing
  per project goes to stderr, in text or JSON.
- A directory slicer cannot examine is reported, not skipped, because it may hold a project: one
  whose `.slicer/config.json` cannot be checked (for example an unreadable `.slicer/`), or that
  cannot be listed. It gets its own entry, in the same sorted order, with `"exit": 3` and
  `"error": {"code": "io", ...}`. No command runs there, and the other projects still run.
- No project at or below the start directory is exit 2 with the error envelope, code `no_project`.

## Dashboard snapshot

`slicer snapshot --json [--root PATH]... [-r] [--parts items,goals,runs,status,health]`
combines dashboard reads in one process. It loads each project once, reads only, and takes
no writer lock. With no root, it discovers the current project. Repeated roots retain their
argument order (a root before `snapshot` also participates). `-r` walks each root using the
same discovery, exclusions and sorted path order described above; it never walks upward.

```json
{
  "snapshot": 1,
  "slicer": "VERSION",
  "contract": 7,
  "projects": [{
    "root": "ABS_PATH",
    "revision": "0123456789ab",
    "parts": {
      "items": {"revision": "0123456789ab", "value": []},
      "goals": {"revision": "0123456789ab", "value": {"goals": "", "non_goals": "", "records": []}},
      "runs": {"revision": "0123456789ab", "value": []},
      "status": {"revision": "0123456789ab", "value": {"next": null, "blocked": [], "held": []}},
      "health": {"revision": "0123456789ab", "value": {"render_stale": false, "merge_in_progress": false}}
    },
    "error": null
  }]
}
```

`slicer` is the running package version; `contract` is the running `ai.RELAY_CONTRACT`.
The default part order is `items`, `goals`, `runs`, `status`, `health`. `--parts` selects only
its comma-separated names, in that order. Unknown or empty names are `usage` (exit 2),
with a diagnostic on stderr and no stdout. Without `--json`, snapshot is `usage` too.
The fixed schema retains nulls and empty containers even with `--lean`.

- `items`: default `list` rows in the same order, excluding done and retired. Each row has
  `id`, `short_title`, `status`, `claim` (owner string or null), `pass`, `size`, `effort`,
  `score` (base importance/urgency score), `quadrant`, `held`, `blocked`. Hold flags and
  unmet dependencies use the existing configured rules.
- `goals`: the goals and non-goals prose plus a flat `records` array of `{id, kind, title}`,
  using the active records from `goals` in goal/non-goal order.
- `runs`: only active relay runs, in `relay-run list` order. Each has `id`, `items`,
  `worktree`, `state`, `updated_at`, and `steps`. Each step has `id`, `role`, `phase`, `task`,
  `model`. Ended and stale runs are omitted; a waiting run is active, and its waiting step has `phase` `wait`.
- `status`: `next` is the id `next` would offer, or null; `blocked` and `held` are arrays of
  ids from the existing status reader's skipped candidates.
- `health`: `render_stale` covers missing, differing or orphan render files from `check`;
  `merge_in_progress` comes from the checkout's Git merge state.

Each part's revision is the first 12 hexadecimal digits of SHA-256 over its `value` encoded
as UTF-8 using canonical `jsonio.dumps` (two-space indent, Unicode unescaped, trailing
newline). The project revision hashes the ordered object mapping part names to their
revisions in the same way. Unchanged reads print identical bytes while the values stay
unchanged, including the active-run view.

A project that fails has `revision: null`, `parts: {}` and
`error: {code, message, command: "snapshot"}`. Unreadable recursive directories also get an
error entry. Other projects still print; the process exit is the highest error exit among
them (2 for usage/validation, 3 for internal/state errors), or 0 with no errors. Recursive
roots containing no project produce a `no_project` entry. There is no text form,
`--if-changed` or persistent server mode.

## Failures are JSON too

With `--json`, a failure puts an envelope on **stdout** and still writes the human line
to stderr. An agent never has to parse prose:

```console
$ slicer show S99 --json
{
  "error": {
    "code": "no_such_item",
    "message": "no such item: S99",
    "command": "show"
  }
}
```

Argument-parser failures also return exit 2 and this envelope with `code="usage"`,
while retaining argparse’s usage text and diagnostic on stderr. This covers missing
arguments, unknown options, invalid values and conflicting options, including nested
commands. `command` is the recognized top-level command (`"prose"` for nested prose
commands), or `null` when none was recognized.

For parser failures, an exact `--json` token anywhere before the first `--` requests
the envelope, even if that flag’s position is invalid. This does not make the command
valid. Tokens after `--` are literal arguments. Help stays human-readable and exits
successfully, including with `--json`; successful command syntax is unchanged.

**`code` is the contract; `message` is not.** Messages get reworded; codes change only
when the meaning does. Branch on the code. A refusal takes its specific code when this table names one for its condition, such as `no_slice` for an item with no slice, whichever caller reaches it (`ai relay-brief` included); the plain `state` code is for the rest.

| Code | Means |
|---|---|
| `no_such_item` | No item with that id, from any command, including a `--depends-on`, `--discovered-from` or `--parent` id |
| `parent_self` | `set --parent` named the item itself |
| `parent_nested` | `add --parent` or `set --parent` would make a second level: the parent is itself a sliver, or the item being given a parent already has slivers. Exit 2, nothing written |
| `slivers_open` | `handoff` or `done` named a parent while a sliver is short of what that finish needs: for `handoff` every sliver must be handed off, being reviewed, done or retired, whatever `implement_finish` is; for `done`, done or retired. A sliver named in the same call counts. The message lists each short sliver with its status. Exit 2, nothing written; there is no `--force`. A sliver's own finish is unaffected |
| `no_slice` | The item exists but has not been promoted |
| `unspecified` | `ai relay-brief` named an item whose slice has an empty required section. Exit 2, nothing written |
| `no_such_section` | `show --section` named a heading the slice does not have |
| `bad_promote_source` | A `promote` source is not one item, or names no sections |
| `field_in_promote_source` | A `promote` source set an item field; those belong on `add`/`set` |
| `already_exists` | `init` on an initialised project |
| `state` | The operation does not apply — unknown status, already promoted, a self, cycle, or retired dependency, and similar |
| `config` | `.slicer/config.json` is missing, malformed or self-contradictory |
| `bad_config_value` | `config KEY VALUE` was given a value that does not fit the key's type (not a whole number, not `true` or `false`, not the JSON list or object the key takes), or one that would leave the config invalid; the message is the one `Config.validate` gives. Exit 2, and nothing is written |
| `outline` | An outline given to `import` could not be parsed |
| `legacy_format` | Legacy markdown `migrate` could not read or round-trip |
| `render` | A render failed: a template named a placeholder that does not exist, or a generated table row does not match its header. `template set` refuses both earlier, as `bad_template` |
| `bad_template` | `template set` was given text that would not render: it names a placeholder the renderer does not know, or a row template whose cell count differs from the table header. The message names the template and the first problem. Exit 2, and nothing is written. Chosen over `render` so an agent can tell its own bad input from a project whose existing state cannot render |
| `unknown_template` | A template `NAME` is not `slice.md`, `roadmap.md`, `row.md` or `relay-brief.md`; the message lists them. Exit 2 |
| `io` | A file could not be read or written |
| `locked` | Another slicer held the writer lock past the timeout; retry, or clear a stale `.slicer/lock` |
| `corrupt` | A tracking file (index, a slice, the log) is not valid JSON/UTF-8 or is the wrong shape |
| `schema_too_new` | `index.json` or `config.json` was written by a newer slicer; upgrade slicer to open the project |
| `bad_id` | An id is unusable as a filename, or a config prefix would produce one |
| `case_collision` | An id differs from an existing one only in case, including an id newly filed in a sibling git worktree. Exit 2, nothing written; a sibling refusal names its worktree, id and title, and suggests `slicer next-id` or leaving out `--id` |
| `id_taken_elsewhere` | `add --id` names an id that a sibling git worktree has filed as an item of its own, not one both checkouts inherited from their merge base. Exit 2, nothing written; the message names the worktree and its title, and suggests `slicer next-id` or leaving out `--id` |
| `already_exists` | `init` or `migrate` over a project that already has state |
| `blank_title` | A title is empty or whitespace; also a `relay-roles` rule's text |
| `newline_in_field` | A one-line field (title, size, findings, tree, flag) contains a newline; also a `relay-roles` rule's text, or a row's agent, vendor, model, reasoning, note or size |
| `editor_aborted` | `$EDITOR` exited non-zero; nothing changed |
| `wrong_command` | e.g. `import --from` (that flag belongs to `migrate`) |
| `no_relay_roles` | `relay-roles` (including `recommend`) or `ai relay-roles` found no `.slicer/relay-roles.json`. Exit 2; run `slicer relay-roles init` to write the example matrix |
| `unknown_role_row` | A `relay-roles` command named a row id that is not in the matrix. Exit 2, and nothing is written; `relay-roles show` lists the ids |
| `unknown_role_rule` | A `relay-roles` command named a rule id that is not in the matrix. Exit 2, and nothing is written |
| `unknown_run` | `relay-run step` or `end` named a run that is not in the ledger this checkout writes to or in any checkout's own `.slicer/relay-runs.jsonl` (the message names its path: the shared one under the git common dir in a git repo, else `.slicer/relay-runs.jsonl`). With `relay-run list RUN`, the id is absent from all listed ledgers (shared and legacy per-worktree); the message names the paths searched. Exit 2, and nothing is written; `relay-run list` shows the runs |
| `run_ended` | `relay-run step` or `end` named a run that already has an outcome. Exit 2, and nothing is written |
| `invalid_branch` | `land --branch` requires an exact short local branch name other than the checked-out branch. Tags, SHAs, revision expressions, full `refs/heads/NAME` and unknown names are refused. Exit 2, nothing written |
| `not_merged` | `land` was given a `--branch` whose tip is not an ancestor of `HEAD`. Exit 2, and nothing is written; merge the branch first |
| `run_active` | `land` found a relay run, not ended and not stale (a run waiting on the owner is neither), that names the item. Exit 2, and nothing is written; the message names the run, and `relay-run end` closes it |
| `bad_run_field` | `relay-run` was given a phase outside start, done, fail and wait, a `--waiting-on` outside owner and approval, an outcome outside pass, fail and stopped, a blank item, a blank `--worktree` or one that resolves to an empty name (such as `/`), a new step without `--role` or `--agent`, a `--parent` or `--step` that is not in the run, an update whose `--task` differs from the task its step was recorded with, unless that step had none (record a new step without `--step`), or a value with a newline. Exit 2, and nothing is written |
| `bad_role_field` | `relay-roles add` or `set` was given a role outside implementer, reviewer, gate, architect and orchestrator, `relay-roles recommend` was given a bad `--select` or `--prefer-agent` (see that command), an effort, importance or urgency outside 1-3, or a `--well-specified`, `--split` or `--sliver` other than yes, no or any (yes or no for `recommend`). Exit 2, and nothing is written |
| `merge_conflict` | The `merge-relay-roles` driver found a disagreement it will not guess at: both sides changed one value differently, one removed a row or rule the other edited, or both reordered the rows differently. Exit 2; the message names each place and `relay-roles.json` is left as it was |
| `no_project` | `-r` found no `.slicer/config.json` at or below the start directory. Exit 2; `-r` never walks up, so run `slicer init` or start higher |
| `external` | An optional external tool failed: `feedback-report` or `issues-pull` found no `gh`, `gh` exited nonzero (often not authenticated), or it printed output slicer cannot use (no issue URL, or an issue list that is not valid). Exit 2. For `feedback-report`, earlier issues in the batch are already recorded and the message names the entry that stopped; `issues-pull` writes nothing |
| `usage` | Missing arguments, unknown options, invalid values, conflicting options, or other invocation errors |

## Exit codes

| Code | Meaning | What an agent should do |
|---|---|---|
| `0` | Fine | Continue |
| `1` | Drift, or a failed check | Read the payload; this is a report, not an error envelope |
| `2` | Usage, validation, or nothing to do | Read the envelope — **unless** the payload has no `error` key |
| `3` | Internal or state (`corrupt`, `locked`, `io`, `config`, `schema_too_new`) | The project or the process cannot proceed; read the envelope and stop |

Exit 3 is a hard failure. Exit 2 is usage, validation, or an empty queue, so status
alone separates them.

Exit 1 belongs to `check`, `verify` (and `verify --landed`, when the item has not landed), `refs` (a reference that no longer holds), `sync --check`, `import` and `migrate`. Inspect
each command's report: `check` has drift lists and `problems`; `verify` has `errors`
and `findings`; `refs` has `drift` and `references`; `sync --check` returns an array with `stale` on each target; import and
migration validation reports have `problems`. Do not assume
that every failed report has the same keys. An error envelope, when present, still
takes precedence over interpreting the payload as a normal report.

Exit 1 also covers a mutating command's `--render` step failing *after* the change was
saved: the change is committed but `render/` is now stale. stdout keeps the command's
single normal result (an object, or an array for a batch of ids) — never a second
document — and the render failure is reported on stderr. The exit is 1 whatever the
cause, because the mutation itself succeeded and only the projection is stale. Re-run
`slicer render` (after fixing the cause) to resolve it.

That save-then-render order is the default. Add `--strict` (with `--render`) to require
the render to succeed *first*: the mutation is rendered before it is allowed to land, and a
render failure rolls the whole change back — nothing is written, and nothing is printed to
stdout — exiting **2** with `{"error": {"code": "render"}}`. Use it when a change that
cannot be rendered must not land at all. `done --render` already behaves this way and does
not take `--strict`. The flag is offered on the other mutating commands except `land`,
including `add`, `set`, `move`, `sort`, `promote`, `edit`, `note`, `start`, `handoff`,
`retire`, `park`, `unpark`, the `prose` edits, `import`, `migrate`, and `remove`. `--strict`
without `--render` is a usage error, including together with `--dry-run`. `--strict`
guarantees only the render; it does not re-run `check`. Dependency integrity is enforced by
`add` and `set` themselves, with or without `--strict`.

**One case to special-case:** `slicer next`, including `--show` and `--ready`, exits **2** when nothing is runnable, with
`{"item": null, "blocked": [...]}`. That is a normal empty queue, not a failure. Test for
the `error` key rather than assuming exit 2 means something went wrong.

## Getting a roadmap in

`slicer import FILE` reads a markdown outline — the bulk path, and the one an agent
should produce. Write the outline, dry-run it, apply it. See [import.md](import.md) for
the format.

```console
$ slicer import --skeleton > roadmap.md     # template, built from this project's config
$ slicer import roadmap.md --dry-run --json # validate; writes nothing
$ slicer import roadmap.md --json           # apply
$ slicer render --json
$ slicer check --json
```

Import validates everything before writing anything, so a rejected outline leaves the
project exactly as it was. `--dry-run` and the real run report the same census, which
means an agent can decide from the dry run alone.

## Rules for an agent working in a slicer project

**Re-prioritize in one step.** After changing scores with `set`, `slicer sort` reorders the
whole queue by priority score at once (persisting the `list --sort score` order) instead of a
`move` per item.

**Plan a batch with the dependency graph.** `slicer deps --json` lists every unblocked open
item (what you can start now); `slicer deps ID --json` gives what an item waits on, which of
those still block it, and what depends on it; `slicer deps --format mermaid` prints a
PR-reviewable graph. Reach for these instead of reconstructing the graph from `list --json`.

**Before adding, search for duplicates.** `slicer find "topic" --json` matches item ids,
titles, findings, and slice bodies (narrow with `--in title,findings,body`) and reports the
matched field and a snippet (`match` in JSON), so you can answer "is there already an item
about X?" — and see why each hit matched — before creating one. To dedupe on one known title,
`slicer find --title-exact "Full title" --json` returns only items whose stored title equals it
character for character (case and Unicode form matter; no PATTERN or `--in`).

**Create a dependent item in one call.** For example,
`slicer add "Implement the new loader" --short-title "New loader" --importance 3 --urgency 2 --effort 2 --depends-on S01 --render`.
Score what you file rather than leaving the 2/2 defaults, and say why in your reply.
`add --discovered-from ID` records an existing source item in
`fields.discovered_from`, with any status allowed. `set ID --discovered-from ID`
sets it on a filed item and `--discovered-from ''` clears it. `list --discovered-from ID`
filters by exact source and combines with other filters; an unknown ID is
`no_such_item`, and zero matches succeeds. Outline input uses the single-value
`discovered_from: ID` key and refuses unknown or prospective sources before
writing the batch. Provenance neither blocks pickup nor changes scores.
Missing historical fields load empty; `--lean` omits empty provenance. Source
retirement keeps references valid; purge needs `--force` when referenced and
then integrity validation reports the dangling source. Index saves stamp schema 9 (provenance arrived in 5, filing keys in 6)
so older readers refuse rather than lose provenance.

**Slivers.** A sliver is a small item carved out of a larger slice. `add --parent ID` and
`set ID --parent ID` store the larger item's ID in `fields.parent` (`set ID --parent ''`
clears it); `list --parent ID` returns an item's slivers, composing with the other filters,
and `show ID` lists them under `slivers`. The link is one level deep: a parent cannot have
a parent (`parent_nested`) and an item cannot be its own parent (`parent_self`); an unknown
ID is `no_such_item`. The parent may have any status. `parent` is a reference, not a
dependency: it is never written into `depends_on` and does not change scores. It does hold
the parent while slivers are open. In `next` (including `--batch`, `--ready` and the review
queue), `list`'s `!` marker, `deps` and ranking, each sliver short of handed off, being
reviewed, done or retired waits ahead of its parent like an unmet dependency (when
`implement_finish` is `done`, only a done or retired sliver releases the parent here,
since that project has no handoff step, so `next` never offers a parent whose `done`
would refuse): the slivers
are offered, and the parent appears in `blocked` as `{"id": parent, "waiting_on": [...]}`
with the sliver ids after any real dependencies (`deps ID` returns them in `blocked_by`
too, and its text adds a `held by slivers` line; `waits_on` stays `depends_on`). A
retired sliver does not hold its parent. `handoff` refuses a parent whose slivers are not
all handed off (or being reviewed, done or retired), whatever `implement_finish` is, and
`done` one whose slivers are not all done or retired, with `slivers_open` and no
`--force`; a sliver in the same call counts. A sliver finishes as any item does.
`set --status` stays an escape hatch that does not run the sliver check, like the other
finish guards it skips.
`remove` and `remove --purge` refuse an item that still has slivers unless `--force`, and a
purged parent's ID is not reclaimed while a sliver names it. In an outline, `parent:` takes
a title (of another entry in the same file, filed earlier or later, or of an existing
item) as `depends:` does, else an existing item ID; `check` and `verify` report an unknown parent, a nested parent and a self-parent as
errors. Index saves stamp schema 9 so older readers refuse rather than lose the link.

Repeat `--depends-on` for multiple ids. `add` and `set` refuse, before writing anything,
an id that names no item (`no_such_item`, including a comma list passed as one value)
and a self-edge, a new cycle, or a dependency on a retired item unless `satisfies_dependencies` lists the retired status (`state`). A dangling edge
or cycle already in the index is left for `check` to report. `park` and `unpark` accept `--note` to record
why work is being deferred or resumed; read those notes with `slicer log --json`, and scope to
one item with `slicer log --item ID --json` (repeat `--item` for several) or to a kind of change
with `--action set` / `--action status`. `set` entries record old→new values, so
`slicer log --item ID --action set --json` reconstructs an item's metadata history from slicer
(section/prose body edits still live in git).

For a time window, pass `--since 2026-10-05T12:00:00Z` and/or
`--until 2026-10-05T13:00:00Z`. Explicit UTC offsets and fractional seconds work.
Item/action/by filters and the time window apply before `--limit`; output ordering
and append-order ties remain unchanged. Equal bounds or a window with no entries
return an empty array with exit 0. Invalid or timezone-less bounds and `since > until`
return `usage` (exit 2) before reading history. An invalid or timezone-less stored
timestamp encountered during time filtering returns `corrupt` (exit 3), naming the
history file and item. Timestamp windows cannot define an exact run boundary under
clock skew; history has no per-entry cursor.

**Read the project's direction before proposing work.** `slicer goals --json` returns
`{"goals": ..., "non_goals": ..., "records": {"goal": ..., "non_goal": ...}}` — the prose
plus goal and non-goal records, kept separate from the backlog. Requirements, constraints,
decisions, and assumptions are `slicer catalog list`. Judge new work against them, and if
the prose is empty or unclear, ask the owner to set it (`slicer prose edit goals` /
`non_goals`) rather than inferring direction from the queue. A citation on an item is a
reference (`slicer catalog cite`), not a dependency.

**Never edit `.slicer/*.json`.** Use the commands. The index, the slice files and
`.slicer/render/` have to agree, and `slicer check` is what proves they do.

**Preview a destructive remove.** `slicer remove ID --purge --dry-run --json` reports what would
be deleted, which dependents would dangle, and whether the id would be freed or stay burned —
without writing. Check it before a real `--purge` (`--dry-run` works for `--reason` retires too).

**Render after mutating, and check.** No command renders implicitly, but every mutating
command takes `--render` to do it in the same step (`slicer done S01 --render`), except
`land`, which renders through `done` and takes no flag. Otherwise
run `slicer render` then `slicer check`; a non-zero check means the work is not finished.

**After a rebase or merge, resolve `index.json`, then re-render.** `.slicer/log.jsonl`
union-merges on its own; `index.json` is the source of truth (resolve real overlaps by hand);
never hand-merge `render/` — run `slicer render` and `slicer check` so the generated markdown
matches the resolved index.

**Serialize integration into a shared checkout.** Only one actor should run `git merge`,
resolve conflicts, and finish the merge there at a time. S114's proposed slice claims
would coordinate authoring in separate worktrees; they do not reserve the integration
checkout. While Git has a merge in progress, slicer refuses state changes, including
TUI saves, with `merge_in_progress` (CLI exit 2). Read-only commands, dry runs,
`slicer render`, and `slicer sync` remain available to inspect and repair the merge.
Finish or abort the merge before changing slicer state again. The guard does not
serialize Git commands.

**`slicer next` is the queue.** It considers eligible started items first, then open
items if none qualify. Within each group it follows default `list` priority:
declared pass order, descending effective score, and stored queue-order ties.
Empty passes merge by effective score; named undeclared passes follow declared passes. All dependencies must be done, or in a status the
project lists in `satisfies_dependencies`. A
blocker of a critical item inherits that item's priority, so `next` naturally surfaces the
blocker first; dependencies still hard-gate, so a blocked item is never returned whatever
its score. `next` also reports the item's effective score (a `^` marks a score inherited from a
dependent) and status; `slicer next --start` returns the item and marks it started in one call.
Use `slicer next --ready --section "Implement" --section "Check" --json --lean` as the
pickup: the headings are examples, so pass the section names the project configures.
Run `slicer sections` for this project's names.
That returns the item and only those sections, instead of `next` then `show`.
`slicer next --ready --json` is the bounded form of that selection. `item` carries
`id`, `title`, `status`, `depends_on`, `effective_score`, and `path`. `slice` is the
same object `show` returns, and only when the item has one. Repeat `--section` with
`--ready` and the slice is only `boundary` plus those sections, in slice order, using
the same heading match as `show --section`. An unknown heading exits 2 with
`code="no_such_section"`. A row with no slice still omits `slice` and names
`slicer promote`. Omit `--section` and the full slice stays. `--section` without
`--ready` exits 2 with `code="usage"`. `--json --lean` applies to this payload.
The text form stays identity, a `slicer show ID` hint when the item has a slice, the boundary, and the headings. It does not print the slice path unless `--path` is set. `--path` is text only: JSON still includes `path`, and `--json --lean` still drops it on an item. A row with no slice keeps the promote hint on this text and does not gain a show hint. `blocked` is included on
every response, including when an item is returned. `unspecified` and
`in_work_elsewhere` use the same shapes as `next`. An empty or fully blocked queue exits 2 with `{"item": null, "blocked": [...]}`,
including when `--section` is set.
`--start` and `-n` still apply. `--ready` together with `--show` exits 2 with `code="usage"`.
Goals stay on `slicer goals`, the progress census stays on `slicer status`, and one
section stays on `slicer show ID --section`.
Read the slice and inspect its scope, dependencies, and acceptance checks; resolve any
ambiguity before starting it. Then run `slicer start ID --render --strict`, implement and verify
it, and finish with `slicer done ID --note "..." --render` followed by `slicer check`, the
default finish. If `implement_finish` is `handoff`, use the handoff command from
`slicer ai instructions` instead. If the
specification is already trusted and needs no clarification, `slicer next --start --ready --render --section "Implement" --section "Check" --json --lean` may combine fetching, reading, and starting the item.

**Read only the implementation sections.** Repeat `--section` to return just selected
sections, and add `--context` to include the item title, dependencies, and scope boundary:
`slicer show S01 --section "Failing tests" --section "Implement" --section "Check" --context --json`.
Those headings are examples; a project configures its own section names. Run
`slicer sections` for this project's names. This avoids
loading unrelated slice prose. The payload shapes stay in the command table above.

For edits, `slicer edit ID --section "Why" --stdin` replaces one section's body, and
`slicer show ID --section "Why"` reads that one body back. For short edits use
`slicer edit ID --section "Why" --text "Updated explanation" --render --strict`; roadmap prose
supports the same source, such as `slicer prose edit preamble --text "Current work"`.
In replacement mode, inline text is exact, including whitespace and newlines;
`--text ""` clears the body.
Choose only one of `--text`, `--file`, and `--stdin`; conflicting sources return exit 2
and `code="usage"` with `--json`. Omitting all sources opens the editor. File and stdin
sources retain their existing trailing-newline stripping. Quote text for the shell,
or pass it as one argument when invoking without a shell.

To leave a durable observation on an item — what you tried, why something was deferred —
`slicer note ID --text "..."` appends a dated note to the item (no slice needed; works on a bare
row), visible in `slicer show` and, once promoted, the rendered slice. This differs from
`done --note`, which records only to the log.

Flag open owner decisions with `slicer set ID --add-flag needs-owner`; resolve them with
`slicer decide ID --text "Answer" --render`. `list --held` finds flagged work.

Item note records contain fixed-order `id`, `kind`, `text`, `created_at`, `attempt`,
`by`, `verified_by`, `verified_at`, and `stale_after`; empty metadata means absent.
`notes` remains the compatible display-string view. `note --owner ACTOR` uses the
existing actor precedence to record authorship without verification.
`--stale-after` takes timezone-aware ISO8601 and stores UTC; render does not
calculate freshness using the clock.

Read stable IDs with `show ID --json`. Use
`note-verify ID --note-id NOTE [--owner ACTOR]` for machine verification and
`note-attest ID --note-id NOTE --owner NAME` for owner attestation. Verification
without `--owner` uses the actor precedence; attestation always needs an explicit
nonempty name. Ordinary authorship (including actor fallbacks) and machine
verification reject the reserved `human:` prefix. Attestation stores `human:NAME` as
verifier and preserves authorship and text. The returned `trust` is derived:
`unverified`, `machine-confirmed`, or `human-reviewed`. Repeating the same
verifier is a no-op; a different verifier replaces it. Actual text edits clear
verification and log the previous verifier/time. Unknown note IDs fail with
`usage` before writes; legacy slice notes cannot be attested.

Attestation is a workflow convention, not caller authentication. An agent can
invoke the owner command; metadata alone does not establish an identity boundary.
New index saves stamp the current schema (9; note metadata arrived in 7) so older readers refuse rather than erase metadata.

To add to a section:

```sh
slicer edit ID --section "Why" --append --text "New finding" --render --strict
```

Append requires exactly one of `--text`, `--file`, or `--stdin`; it never opens the editor. Trailing newline characters in the old body and
leading newline characters in the new body are removed, then nonempty bodies are joined
with two newlines. Other whitespace and source-reading rules are preserved. Empty
appended content changes neither the section nor the log; an empty or missing section
is filled without a leading separator. Prose editing does not support append.

The scope boundary is independent of section bodies. Use
`slicer edit ID --boundary --text "**Not in this slice:** other work" --render`
to replace its full paragraph; file, stdin and editor input also work. This target
cannot combine with `--section` or `--append`. Empty text clears it.
`show ID --json` exposes it as `slice.boundary`; boundary edits return
`{"id": "ID", "boundary": "full paragraph"}`. Promotion accepts `--boundary TEXT`
to override the source/default. Older JSON is converted in memory on load and the
field is persisted on the next slice save; read commands do not rewrite JSON.

To fill a whole slice at once, hand `promote`
a one-item outline: `slicer promote ID --file draft.md` — the same `##` item / `###`
section shape `import` reads, sections and lead only. A lead paragraph that starts
with the project's boundary marker is stored as the scope boundary; put it in the
lead or in one section, not both.

**Ids are never reused.** Adding an item claims its id for the life of the project.

**Writers serialise.** A mutating command holds an advisory lock (`.slicer/lock`) for its
run, so a fan-out of concurrent `slicer` processes cannot mint duplicate ids or half-apply
an outline. A second writer waits, then fails with `code="locked"` (exit 3) after a
timeout; set `SLICER_LOCK_TIMEOUT` (seconds) to tune it. Read-only commands never lock.

**slicer never commits.** `git` access is an allowlist of read-only subcommands, and a
mutation leaves the index untouched. Committing is the human's. A status change moves
the slice file in the worktree only. To record that move as a rename, add the old path
and the new path together. `git add -u` stages only the deletion.

## History and dependencies

Use `slicer log --item ID --json` to inspect an item's recorded changes. `set` history
entries include old-to-new field values; `--action set` narrows the log to metadata changes.
For a durable observation on any item, including one without a slice, use `slicer note ID`;
`done --note` records a history entry only. Use `slicer deps --json` for unblocked work,
`deps ID --json` for an item's edges, and `deps --format mermaid` for a graph.

## Batch mutations

`done`, `start`, `park`, `unpark`, and `set` accept multiple IDs, for example
`slicer set S01 S02 --urgency 3 --render`. Flags apply equally to all selected items.
Use `slicer done - --json` for whitespace-separated IDs on stdin; convert JSON query
output to IDs before piping it in. Do not mix `-` with positional IDs.

Batches deduplicate in input order and validate every ID and supplied field before
writing. Invalid requests change nothing; I/O failures have no multi-file rollback.
One explicit ID retains the JSON object response. Multiple positional IDs and stdin
return an array, including when only one unique ID remains. See
[batch changes](commands.md#batch-changes) for examples and history semantics.

## Worked workflows

slicer works with any coding agent that can read files and run commands. It does not
call an AI itself. You and the agent decide what to build; slicer turns the agreed
work into a persistent queue with dependencies, priorities, and readable slices.

Choose one example below, starting in your project's root after `slicer init` and
configuration. Each assumes an empty slicer queue, so the allocated ids are S01 and
S02. The example source paths describe proposed or fictional files, not slicer files.

### A new project: goals to a roadmap

Suppose you want a small CLI that reads a JSON settings file. Tell the agent the goal,
constraints, and acceptance criteria: Python standard library only, a required
`name` string, and a command that prints that name. Ask it to divide the work into
small slices with explicit checks and dependencies. The
[planning prompt](#plan-a-new-project) is a starting point.

After agreeing on the plan, save this outline as `roadmap.md`:

```markdown
# Settings CLI

## Load a settings file
size: S
tree: core
importance: 2
urgency: 2

### Why
The command needs a validated name from a JSON settings file.
### Files
Create settings.py and tests/test_settings.py.
### Failing tests
A valid name loads; missing, non-string, or blank names are rejected.
### Implement
Use the standard library to read JSON and validate the name field.
**Not in this slice:** command-line arguments or printing output.
### Check
Run the loader tests for valid and invalid input.
### Git
Follow the project's commit policy.

## Print the configured name
size: S
tree: cli
depends: Load a settings file
importance: 3
urgency: 2
```

The first item is a detailed slice. The second is deliberately a roadmap row: it records
future work and its dependency, but needs a specification before implementation.

### An existing project: AI review to a roadmap

Ask the agent to review the code and tests, citing file locations and concrete failure
cases for each finding. Use the [review prompt](#review-an-existing-project).
Discuss the findings, discard unsupported ones, and compare them with the existing
roadmap to avoid duplicates. Turn accepted improvements into small slices, preserving
their evidence and acceptance criteria.

Then ask the agent to convert that roadmap to the [outline format](import.md).
`slicer import --skeleton` gives it a template based on your project's configuration.
A free-form review is not directly importable, and `migrate` is only for the
[legacy markdown format](migrate-format.md).

For this fictional review, assume inspection found that `settings.py` accepts a blank
name and that the README omits the required field. Save this as `roadmap.md` instead
of the new-project example:

```markdown
# Settings review

## Reject blank configured names
size: S
tree: core
findings: R1; settings.py accepts a whitespace-only name
importance: 3
urgency: 2

### Why
A blank name currently reaches the CLI as apparently valid input.
### Files
settings.py and tests/test_settings.py.
### Failing tests
Loading a name containing only spaces must report invalid input.
### Implement
Validate the stripped name before returning settings.
**Not in this slice:** changing the configuration format or CLI output.
### Check
Run the regression test and existing settings tests.
### Git
Follow the project's commit policy.

## Document the required name
size: S
tree: docs
findings: R2; README omits the required name field
depends: Reject blank configured names
importance: 2
urgency: 2
```

For a real review, include actual paths and line references in the slice's Why section;
do not copy the fictional findings as evidence about your project.

### Import, prioritize, and work through either example

Validate the outline before applying it:

```sh
slicer import roadmap.md --dry-run
slicer import roadmap.md --render
slicer check
slicer list
slicer show S01
```

The import creates S01 with a slice and S02 as a row depending on S01. A rejected dry
run reports what to fix; resolve those problems before applying. After import, JSON
is the state: edit through slicer, not by re-importing the outline or editing generated
markdown. Importing the same titles again is refused by default.

Continue with [picking work](commands.md#picking-and-listing-work) and the [implementation prompt](#implement-one-slice).
