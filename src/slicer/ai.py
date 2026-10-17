"""Keep agent onboarding available wherever slicer's Python code is installed."""

from __future__ import annotations

import shlex


# The implement loop and the exit-code rules. The instructions and the agent
# skill both use these sentences, so a wording change cannot land in only one.
LOOP = """\
1. Run `slicer next --ready --section "Implement" --section "Check" --json --lean`
   to get the next item and only those sections. The headings are examples;
   pass the section names the project configures (`slicer sections`).
   Read its scope, dependencies, and acceptance checks. Resolve missing or
   ambiguous specifications before changing its status. If the specification is
   trusted and needs no clarification, `slicer next --start --ready --render --section "Implement" --section "Check" --json --lean` may
   combine them.
2. Otherwise, run `slicer start ID --render --strict --json` after reviewing the slice.
   Implement the agreed scope, update
   affected documentation, and run the slice's checks and required project tests.
   Edit a slice with `slicer edit ID --section NAME --text "Body" --render --strict --json`.
3. Review the changes. Fix problems before marking work complete. The tracking
   check in step 4 does not replace code tests.
4. Run `slicer done ID --note "Describe the verified outcome" --render --check --json`.
   Exit 1 means it landed but the tracking check failed: fix what stderr names,
   then run `slicer check --json`. If `implement_finish` is `handoff`, run
   `slicer handoff ID --render --check --json` instead and do not run done.
"""

# Step 4 when implement_finish is handoff. The done command stays out of this text.
HANDOFF_STEP = """\
4. Run `slicer handoff ID --render --check --json`. Exit 1 means it landed but
   the tracking check failed: fix what stderr names, then run `slicer check --json`.
"""

# The one line both outputs lead with. The full tracking section still follows.
TRACKING_RULE = (
  "Do not open, search, parse, or edit tracking files; use the slicer commands."
)

EXITS = """\
Use `--json` for automation and inspect both the exit code and payload. Failures
can return an `error` object containing `code`, `message`, and `command`; branch
on the stable code rather than message wording. Human diagnostics also go to
stderr. Exit 0 means success; exit 1 is a failed check or validation report whose
details must be inspected; exit 2 means a usage or validation error, or no next item;
exit 3 means an internal or state error (`corrupt`, `locked`, `io`, `config`, `schema_too_new`).
In particular, `next` can return exit 2 with `{"item": null, "blocked": [...]}`
and no error object: inspect the blocked items rather than assuming work is done.
An empty `next --batch` is exit 2 with `{"items": [], "blocked": [...]}` and no error object.
"""

SPEC_GAP = """\
If `next` JSON includes `unspecified`, those ids are not ready. Fill each missing section with `slicer edit ID --section NAME` and run `next` again. Do not invent the section body.
"""

SKILL_DESCRIPTION = (
  "Drive a slicer roadmap. Use when asked to review a roadmap, plan work, "
  "take a slice, implement the next slice, or drive slicer. "
  "Read and change roadmap state only through slicer commands, never by opening .slicer/ files."
)

# Reads and edits of tracking state. The instructions and the skill both use
# this block, so the rule cannot land in only one of them.
TRACKING = """\
## Read and change tracking state through the CLI

Use the slicer CLI to read and to change goals, items, slices, notes, history,
and roadmap prose. This applies while reviewing a roadmap, planning work,
implementing a slice, and validating the result.

Do not open, search, parse, or edit tracking JSON, the history file, or generated roadmap and slice output to obtain or change that state.

- Goals: `slicer goals --json --lean` returns the prose plus goal and non-goal records.
- Catalog: `slicer catalog list --json --lean` reads requirements, constraints, decisions, and assumptions.
- Inventory: `slicer list --json --lean`, `slicer status --json --lean`, and `slicer stats --json --lean`.
  The text form of `list` and `find` marks an item that waits on a dependency not yet met with `!` after the row number; `slicer show ID --json --lean` names its `depends_on`.
- Slice detail: `slicer show ID --json --lean` and `slicer next --ready --json --lean`.
- Search: `slicer find topic --json --lean`.
- Roadmap prose: `slicer prose list --json --lean` and `slicer prose show goals --json --lean`.
- History: `slicer log --json --lean`. Notes on an item come back with `slicer show ID --json --lean`.
- Validation: `slicer check --json`.

Use --lean on reads; it drops empty fields and paths. Drop it only when you need the full shape.

If a CLI read or edit is missing or fails, report that and propose a roadmap item.
Do not switch to the tracking files.

Source code, ordinary project documentation, templates, and this skill stay
readable when you are implementing. Change a render template with
`slicer template set NAME --stdin` or `slicer template reset NAME`, never by
editing the file. A task that explicitly asks you to inspect or
repair tracking internals is the only exception.

When slicer warns that the running code belongs to another worktree, run
`PYTHONPATH=src python3 -m slicer` for this checkout.

Before handoff, read `slicer config handoff_requires_note_kind`. If nonempty,
file `slicer note ID --kind KIND --text "Describe the verified outcome" --render`
for this implementation attempt. A report from a previous attempt cannot satisfy it.
"""

# Finish closers. They follow implement_finish, so they stay out of TRACKING,
# which both modes share. Done mode must not tell the reader to wait for review.
DONE_CLOSER = (
  "Step 4 is the finish. Commit or publish only when the project instructions authorize it.\n"
)

HANDOFF_CLOSER = (
  "`slicer handoff ID --render --json` records work ready for review. Mark that work done\n"
  "only after review and merge are complete.\n"
)

# The hand-off section applies only when the project finishes by handoff. Done mode
# keeps that qualification and does not say to wait for review and merge.
HANDOFF_APPLIES = (
  "This section applies when `implement_finish` is `handoff`. "
  "When step 4 is done, that command is the finish."
)

HANDOFF_REVIEW_SENTENCE = "Run done only after review and merge are complete."


def loop_text(finish: str = "done") -> str:
  """The implement loop. `handoff` replaces step 4 and drops the done command."""
  if finish == "handoff":
    head, _step = LOOP.rsplit("4. ", 1)
    return head + HANDOFF_STEP
  return LOOP


def handoff_policy_text(kind: str) -> str:
  """Expose the configured report kind without guessing from note prose."""
  if not kind:
    return ""
  return (
    f"This project sets handoff_requires_note_kind to {kind!r}. Before handoff, run "
    f"`slicer note ID --kind {shlex.quote(kind)} --text \"Describe the verified outcome\" --render`. "
    "The note must be nonempty and belong to the current implementation attempt. "
    "Release and resume keep its association; reject then restart needs a new report. "
    "Changing attempts manually changes which reports belong to the current attempt.\n\n"
  )


# The version of the relay contract, printed as the first line of `ai relay` and
# `ai relay-orchestrator` and carried in their `--json`. The agents repo's
# `relay-orchestrator` role says the same number, so a mismatch between slicer and
# the role files shows. Bump it when a rule an outer session, orchestrator or role
# follows changes (order, gate, tracking order, waiting, report shape), not for a
# rewording. 5: Setup resolves the role lineup, orchestrator included, with
# `relay-roles recommend` and the owner accepts it as a whole or overrides rows; a role
# runs as a native subagent on the orchestrator's harness and as a headless session on another.
RELAY_CONTRACT = 5


def contract_line() -> str:
  """The marker both relay texts start with, built from `RELAY_CONTRACT`."""
  return f"relay v{RELAY_CONTRACT}"


# The slice relay, shared by both finish modes. The close is appended separately
# so the handoff text can avoid naming the other finish command. It is two constants
# so the owner's role matrix can sit between Setup and Order (see relay_text).
# Slivers and Gate follow Order as constants of their own. The marker line that opens
# the text is added by relay_text.
RELAY_HEAD = """\
# Slice relay

An outer session runs the relay. For each slice it spawns one orchestrator, which
runs the slice through an implementer, a reviewer, an architect and a gate. The
relay uses slicer's own commands.

## Roles

The outer session is the session the owner talks to. It runs Setup with the
owner, owns the relay run, spawns one orchestrator per slice, reads only that
orchestrator's run report, lands branches one at a time when the owner
authorizes it, files the report's later notes, and writes no product code.

The orchestrator runs one slice under the contract that
`slicer ai relay-orchestrator` prints. It briefs every agent, reads the reports
and reads diffs as `slicer ai relay-orchestrator` says, reruns the tests only as
Order says, and writes no product code.

Where the agent cannot nest subagents, the outer session is also the
orchestrator: it follows that contract itself and compacts or starts a fresh
session between slices.

The implementer owns one slice. It runs `slicer start ID --render --strict`,
writes the failing tests and watches them go red, implements, mutation-checks
each new test, and closes the slice.

In a split, one lead implementer owns the slice. It runs
`slicer start ID --render --strict`, the note and the finish step. Part
implementers change only their own files, write and run their own tests, and
run no slicer command that writes.

The reviewer is read-only. It reviews correctness, test strength and direction
against `slicer goals`.

The architect is read-only. It confirms each finding was fixed and reviews the
shape.

A reviewer never wrote code in this slice. In a split, that holds for every part.
The owner chooses the models.

## Setup

Before the worktree exists, the outer session reads the slice's size with
`slicer show ID --json --lean` and confirms these with the owner:

- which roles run. The architect is optional: the owner decides.
- whether the implementation splits into parts. The default is one implementer.
  A split needs parts with disjoint files and their own tests; a slice too big
  for one implementer is two slices or a slivered slice.
- whether to sliver the slice. The default is no. Propose slivers only when the
  slice's effort and the cut allow it (see Slivers) and when that costs less than
  one implementer. The outer session confirms the sliver list and which slivers
  run in parallel with the owner.
- the agent, vendor, model and reasoning effort for each role. A role on the
  orchestrator's own harness runs as a native subagent; a role on another harness
  runs as a headless session in the slice worktree with the same brief.
- the orchestrator's agent, model and reasoning effort, set explicitly on every
  spawn. It runs as the agent type `relay-orchestrator` where the harness has one
  installed, otherwise as a general-purpose agent briefed with
  `slicer ai relay-orchestrator`.
- the environment lines every brief carries: the interpreter, the shell limits
  and the scratch directory, taken from the project's own instructions.
- where the run ends:
  - after the finish step,
  - after a commit on the slice branch, or
  - landed. Land only when the owner asks.

Wait for the owner's answers before creating the worktree.

"""

RELAY_ORDER = """\
## Order

The outer session, for each slice:

1. Create the worktree, then start the run and record the orchestrator's step,
   with the role `orchestrator` (see Record the run below).
2. Spawn the orchestrator with a brief that starts "First run
   `slicer ai relay-orchestrator` and follow it.", then the environment lines, the
   slice, the worktree and branch, the roles with agent, vendor, model and
   reasoning effort, the end point, a temp directory per role, RUN and the
   orchestrator's step id.
3. Wait for its run report. One slice runs in the foreground.
4. Check the report's first line, the contract marker, and its last line,
   `nothing left running: yes`. Without that last line, stop the orchestrator
   first.
5. Mark the orchestrator step, end the run, file the report's later notes as one
   more commit on the slice branch, then land when the owner authorizes it.

Each slice gets a fresh orchestrator, so no orchestrator carries one slice's
context into the next.

The orchestrator runs its one slice in the worktree it was given:
`slicer start ID --render --strict`, red tests, implementation, review,
accepted findings back to the implementer, then
the architect, when the owner chose one, then the gate. Close with
`slicer note ID --text "Describe the verified outcome" --render` plus the finish step.
The orchestrator commits on the slice branch only when its brief allows it; merge
and push are the outer session's, when the owner authorizes them.

Record the run as it goes. The outer session starts it with
`slicer relay-run begin ID --worktree PATH`, where PATH is the slice worktree.
It prints the run id; keep that id as RUN. Record each role when it starts with
`slicer relay-run step RUN --role ROLE --agent AGENT --vendor VENDOR --model MODEL --reasoning REASONING --phase start --task TASK`,
which prints the step id. When the step ends, record it with
`slicer relay-run step RUN --step STEP --phase done` or `--phase fail`.
A subagent adds `--parent STEP` for the step that spawned it.
`slicer relay-run step RUN --step STEP --phase start`
re-records a long-running step as a heartbeat, so its run does not go stale
after 30 minutes. A heartbeat passes no `--task`, so the step keeps the task it
was recorded with; each round records its own step, without `--step`.
After the finish step, or when the relay stops, end the run with
`slicer relay-run end RUN --outcome pass` (or `fail` or `stopped`), after marking
each step done or failed.
Recording is advisory: a failed `relay-run` call never stops the relay.
A role whose sandbox may write only inside its worktree still records: when the
shared ledger is unwritable, `relay-run` writes the worktree's own
`.slicer/relay-runs.jsonl` and says so on stderr, and `relay-run end` from a
session that can write the shared ledger copies those events into it. Making
`<main>/.git/slicer` a writable root for that vendor avoids the fallback.

In a split, the parts run in the slice worktree, in parallel only when their
files are disjoint, otherwise one after another. The orchestrator runs the full
suite on the combined diff, then one review and one gate cover the whole slice.
Accepted findings go back to the part that owns the file.

A whole-slice implementer runs the full suite once; in a split, parts run only
their own tests and the orchestrator's run on the combined diff is the one full
run. The orchestrator reruns the suite only if the diff changed after that run or
main moved.

Run slices in parallel only when their files are disjoint. The outer session
starts their orchestrators together and waits for the reports without polling.
Review and land them one at a time, and rerun the suite on the slice branch merged
with the new main when main moved.

"""

RELAY_SLIVERS = """\
## Slivers

A sliver is a small item carved from a slice and linked to it by `parent`, so a
cheaper model can implement it. The orchestrator may sliver a slice at its
discretion, only when that costs less than one implementer, and only after the
owner confirms the sliver plan at Setup.

- Eligibility: effort 1 at any size; effort 2 only when every sliver comes out at
  effort 1; effort 3 never.
- Filing: on main before the worktree is cut, with `slicer add TITLE --parent ID`
  or one `slicer import` file. Each sliver gets its own spec, size S, effort 1,
  with the parent's tree and pass.
- Order: slivers run in the parent's worktree. Each goes implement, review, gate,
  handoff; a project that closes slices another way uses that step in place of
  handoff. Then the parent gets one combined gate on the whole diff and its own
  handoff.
- Parallel: slivers with disjoint files and their own tests may run in parallel as
  the matrix rule allows, each with its own temp directory. Slivers that share
  files run one after another. The orchestrator runs the full suite on the combined
  diff before the combined gate.
- Escalation: follow the matrix rule for a sliver that fails its gate twice or
  whose diff falls short of the orchestrator's check. Without a matrix, move it
  to the next model up.

"""

RELAY_GATE = """\
## Gate

Name the diff under review. The criteria are: earlier blockers resolved with a
proving test, no new issue of medium severity or worse, tests green, and
docs matching the code. The first line is exactly `VERDICT: PASS` or `VERDICT: FAIL`.
FAIL goes back to the implementer and then the gate again. A failed review is
`slicer reject ID --note "VERDICT: FAIL - reason"`. Any implementer change after
a review needs another gate.

"""

RELAY_BRIEF = """\
## Every brief

Include the slice from `slicer show ID --json --lean`, the tracking rule below,
and the workspace limit: stay inside the workspace the owner named and do not
read or write outside it. The report is a diff stat, red lines, test counts, a
mutation table and deviations. Every brief names the role's own temp directory,
and no two agents share one. Every brief carries the environment lines verbatim.
Reviewer, architect and gate briefs forbid git writes; a needed write stops and
goes to the owner. A part brief names the part's own files and the files it must
not touch.

Start each agent with the model and reasoning effort its role row names, set
explicitly rather than inherited from the session. Launch it by its row's agent: on
the orchestrator's own harness as a native subagent, on another harness as a headless
session in the slice worktree with the same brief. Give each agent a short label
`ID role`, for example `s279 implementer` or `s279 gate`, and pass the same label
as the `--task` line of `relay-run step`.

"""


def relay_shared_sections(required_note_kind: str = "") -> str:
  """Keep the gate, briefs and stop rules identical in both relay contracts."""
  return (
    RELAY_GATE + RELAY_BRIEF + TRACKING_RULE + "\n\n"
    + "Reviewers get the direction sources: `slicer goals --json --lean` and the queue, "
    + "`slicer list --json --lean`.\n\n"
    + "## Stop\n\n"
    + "Stop when the same slice fails its gate three times, the spec turns out "
    + "ambiguous or wrong, or an owner decision is needed.\n\n"
    + handoff_policy_text(required_note_kind)
  )


# The role matrix lives in `.slicer/relay-roles.json` and changes only through
# `slicer relay-roles`. Its render is added to the relay only for a project that has it,
# so the generic text never names a vendor.
ROLES_PROPOSE = (
  "Propose the lineup (agent, vendor, model and reasoning effort for each role) from the matrix below. "
  "`slicer relay-roles recommend ID --well-specified yes|no --split yes|no` matches the "
  "slice against the matrix and resolves each role, the orchestrator included, to the first "
  "matching row. It reports a role no row matches as unmatched (`(no matching row)` in its "
  "text, `unmatched` in its JSON), and the owner fills that role by naming a row or drops it, "
  "as with an architect on a slice below size L in the example matrix. It also lists each "
  "role's other matching rows as alternatives and says why it chose each row. You judge "
  "whether the slice is well specified and whether it is a part of a split "
  "slice. Pass `--split yes` for a part of a split slice and `--split no` for a whole one. "
  "The matrix order decides which row a role gets, unless the owner names a preferred agent "
  "(`--prefer-agent AGENT`, which a role without a matching row on that agent ignores) or a "
  "row for a role (`--select ROLE=ROW_ID`, once per role, only for a row that matches the "
  "slice; an unmatched role has no such row, so a row the owner names for it goes into the "
  "lineup as named, not through `--select`); the harness "
  "the owner launched the relay from changes nothing by itself. "
  "Show the resolved lineup once, and let the owner accept it as a whole or override "
  "individual rows, then run `recommend` again with those `--prefer-agent` and `--select` "
  "overrides. Accepting the lineup "
  "settles only the agent, vendor, model and reasoning effort of each role: it does not by "
  "itself authorize starting paid sessions, committing or landing, and the other Setup "
  "decisions stay the owner's.\n\n"
)

ROLES_MISSING = (
  "This project has no role matrix. `slicer relay-roles init` writes an example "
  "for the owner to adjust.\n\n"
)


def relay_text(
  finish: str = "done", required_note_kind: str = "", *,
  roles: str | None = None, in_project: bool = False,
) -> str:
  """The slice relay. `handoff` closes with handoff and does not name done.

  `roles` is the project's role matrix as Markdown. `in_project` without `roles`
  means the project has no matrix yet. Both are left unset for the generic text.
  """
  if roles is not None:
    matrix = ROLES_PROPOSE + "## Roles matrix\n\n" + roles.rstrip("\n") + "\n\n"
  elif in_project:
    matrix = ROLES_MISSING
  else:
    matrix = ""
  if finish == "handoff":
    close = "Close the slice with `slicer handoff ID --render --check`.\n"
  else:
    close = (
      "Close the slice with "
      '`slicer done ID --note "Describe the verified outcome" --render --check`.\n'
    )
  return (
    f"Contract: {contract_line()}\n\n"
    + RELAY_HEAD + matrix + RELAY_ORDER + RELAY_SLIVERS
    + relay_shared_sections(required_note_kind)
    + "## Close\n\n"
    + close
  )


# The orchestrator's whole one-slice contract and run report. It names the outer
# session's steps only to forbid them; the shared sections stay identical to relay.
ORCHESTRATOR_RULES = """\
# Slice relay orchestrator

You run one slice for the outer session that spawned you. This text is your whole
contract for one slice. Your brief names the slice, the
worktree and branch, your roles with their agent, vendor, model and reasoning effort,
where the run ends, RUN and your own step id. When the slice is done or stopped,
return one run report.

## Rules

- Work only in the worktree your brief names. Use absolute paths and never change
  directory. Write no product code. Read and change tracking state only through
  slicer commands.
- Never run `relay-run begin` or `relay-run end`, mark your own step done, land, merge,
  rebase onto main or push. The outer session owns those.
- Relay run: record a new step for every round (implement, each fix round, review,
  gate) with
  `slicer relay-run step RUN --role ROLE --agent AGENT --vendor VENDOR --model MODEL --reasoning REASONING --parent STEP --phase start --task TASK`,
  where STEP is your own step id, and mark it done or failed when the round ends. A
  heartbeat, `slicer relay-run step RUN --step STEP --phase start`, passes no new
  `--task`.
{tracking}\
- Waiting: a spawn may run in the background with no foreground option. When a
  role's result is needed, end the turn and let its report wake you. A nudge to
  report while a role you started still runs is not a reason to report. A role
  that reports while a command of its own still runs, or that stops waiting on its
  own background work, has not finished its round: resume it and tell it to
  finish. Send the final report once, never an interim one; before ending a turn
  without it, send the outer session one line saying what you wait on. Run no
  sleep loop, no monitor keepalive and no background shell job; nothing you
  started still runs when you report. Every role brief says to run long commands
  such as the full suite in the foreground with a long timeout.
- Read thin: read the implementer's report and the diff stat. Read the full diff or
  the cited lines only for a finding the reviewer and the architect disagree on, a
  finding the implementer disputes, a deviation, or a diff from a role the role
  matrix marks for your own read. That read is a scope-and-honesty check before
  review, not a correctness review.
- Disputes: a reproduction the reviewer ran beats the implementer's "does not
  reproduce". A dispute inside the slice goes back to the implementer; one outside
  it goes in the report as a later note.
- Gate: after FAIL, resume the agent that raised the blockers. Start a fresh
  reviewer only when that agent is gone or the fixes rewrote much of the diff.
- After `VERDICT: PASS` and the finish step, commit on the slice branch if your
  brief allows it.
- Stop and return without asking the owner when the same slice fails its gate three
  times, the spec is ambiguous or wrong, or an owner decision is needed. A design
  choice the reviewer marks as the owner's goes under "Owner needed" in the report.

## Order

Run your one slice in the worktree you were given, with the roles your brief lists:

1. The implementer runs `slicer start ID --render --strict`, red tests and implementation.
2. Review, then accepted findings back to the implementer for each fix round,
   then the architect, when the owner chose one, then the gate.
3. Only after `VERDICT: PASS`, the implementer runs the closing note and finish step:
   {finish_step}
4. After the finish step, commit on the slice branch only when your brief allows it.

Recording is advisory: a failed `relay-run` call never stops the relay.

In a split, the parts run in the slice worktree, in parallel only when their
files are disjoint, otherwise one after another. The orchestrator runs the full
suite on the combined diff, then one review and one gate cover the whole slice.
Accepted findings go back to the part that owns the file.

A whole-slice implementer runs the full suite once; in a split, parts run only
their own tests and the orchestrator's run on the combined diff is the one full
run. The orchestrator reruns the suite only if the diff changed after that run or
main moved.

{shared_sections}\
## Run report

At most 30 lines, in this order, and nothing else:

```
Contract: {contract}
SLICE ID OUTCOME pass|fail|stopped BRANCH NAME @ SHA
worktree: PATH
rounds: N, last verdict: VERDICT LINE
tests: COUNTS
diff stat: STAT
deviations accepted: LIST OR none
later notes: LIST OR none
owner needed: LIST OR none
role steps: STEP IDS
nothing left running: yes
```

Write the last line only after you check that nothing you started still runs.
"""


def orchestrator_text(finish: str = "done", required_note_kind: str = "") -> str:
  """The orchestrator's contract, which `slicer ai relay-orchestrator` prints.

  It follows the project's finish step and report kind the way `relay_text` does, and
  opens with the same marker line.
  """
  if finish == "handoff":
    step = (
      "`slicer note ID --text \"Describe the verified outcome\" --render`\n"
      "  and then the finish step, `slicer handoff ID --render --check`."
    )
  else:
    step = (
      "the finish step,\n"
      "  `slicer done ID --note \"Describe the verified outcome\" --render --check`."
    )
  # The handoff note is too long to follow "They are " on a line of 88 columns, so it wraps.
  lead = "\n  " if finish == "handoff" else " "
  tracking = (
    "- Tracking order: the closing note and the finish step run only after\n"
    f"  `VERDICT: PASS`. They are{lead}{step}\n"
    "  Every implementer brief says so; before review the implementer runs neither.\n"
  )
  body = ORCHESTRATOR_RULES.format(
    tracking=tracking, contract=contract_line(), finish_step=step,
    shared_sections=relay_shared_sections(required_note_kind),
  )
  return f"Contract: {contract_line()}\n\n" + body


def skill_text(finish: str = "done", required_note_kind: str = "") -> str:
  """The SKILL.md Claude Code, Codex, and Grok all load.

  `finish` is the project's `implement_finish` when that project can be read.
  The committed file is the generic `done` text.
  """
  return (
    "---\n"
    "name: slicer\n"
    f"description: {SKILL_DESCRIPTION}\n"
    "---\n"
    "\n"
    f"{TRACKING_RULE}\n"
    "\n"
    f"{loop_text(finish)}\n"
    f"{handoff_policy_text(required_note_kind)}"
    f"{SPEC_GAP}\n"
    f"{EXITS}\n"
    f"{TRACKING}\n"
    f"{HANDOFF_CLOSER if finish == 'handoff' else DONE_CLOSER}\n"
    "When the owner asks for a slice relay, run `slicer ai relay` and confirm its "
    "Setup with the owner before creating the worktree. Read the role matrix with "
    "`slicer relay-roles show` and change it with the other `relay-roles` commands, "
    "never from `.slicer/relay-roles.json`.\n"
    "For planning, filing, claims, and review, run `slicer ai instructions --rest`; "
    "it leaves out what this skill already says.\n"
  )


INTRO = """\
# Getting started with slicer

slicer is a local roadmap and slice manager. You supply the reasoning, planning,
and implementation; slicer stores, prioritizes, validates, and renders the agreed
work. It does not call an AI service. It also works before a project is initialized.

## Read the project first

Read the project's instructions (such as AGENTS.md), overview, relevant source,
and tests before acting. Follow its constraints and required checks. Read the
recorded product direction with `slicer goals --json`; ask about missing goals,
scope, or acceptance criteria; do not infer product direction from the backlog.
Only perform work the user has authorized. Commit or publish only when authorized;
slicer itself never commits, pushes, or tags.

"""

PLAN = """\
## Plan and record agreed work

For a new project, agree on goals and acceptance criteria. For an existing
project, review the code and roadmap, cite evidence, and check for duplicate work.
Break accepted work into bounded slices with dependencies and concrete checks.

If tracking is needed and does not exist, initialize it with `slicer init` after
agreeing to do so. Use `slicer import --skeleton` for the project's outline shape.
Validate an outline with `slicer import roadmap.md --dry-run --json`; resolve all
reported problems before applying agreed work with
`slicer import roadmap.md --render --json`. Then run `slicer check --json`.

Score every item you file instead of leaving the defaults, which rank nothing:
importance and urgency (1-3), effort (1-3), and size, tree, and findings where the
project uses them. Give a one-line reason for each score in your reply; the owner
adjusts them with `set`. For one item, use
`slicer add "Title" --importance 3 --urgency 2 --effort 1 --render --json`; an
outline takes the same keys. Add dependencies with repeated `--depends-on ID`.
A row alone is not a specification: use
`slicer promote ID --stdin --render --json` with a one-item outline to supply its
lead and sections. Use the project's configured sections, not assumed headings.

"""

# What follows SPEC_GAP inside "Implement one slice". Only the full text has it.
IMPLEMENT_MORE = """\
Do not combine `--ready` and `--show`.

Projects can add statuses in `config.statuses`. `next` offers only open work and
resumes started work; parked, review, reviewing, and custom statuses `draft` or
`blocked` stay out. Hold an item back with a custom status, not a flag.

`start` claims the item. `wt:NAME` marks sibling work, including a sibling
handoff, and `next` skips it (`in_work_elsewhere`). `slicer list --json` shows
every claim, and `slicer release ID` clears a claim without changing status.
Use `--owner NAME` on `start`, `next --start`, `handoff`,
`release` and `done`, or set `SLICER_CLAIM_OWNER`; `slicer log --by NAME` shows
what each did.

Re-read one slice with
`slicer show ID --section "Implement" --section "Check" --context --json`.
Those headings are examples; use the section names the project configures.
Run `--help` on any command for the rest of its options.

"""

HANDOFF_SECTION = f"""\
## Hand off for review

When the implementation is ready for someone else to review, merge, or clean up, record
the context with `slicer note ID --text "Ready for review: ..." --render`, then run
`slicer handoff ID --render --json`. A reviewer picks up the next review item with
`slicer next --status review --start --ready --render --section "Check" --json`: it claims the
item and moves it to `reviewing`, so `next` never hands it to an implementer, and the
same command resumes it later. If the review fails, run
`slicer reject ID --note "VERDICT: FAIL - reason" --render --json`: it sends the item back
to open (or `--to STATUS`), records the verdict, and clears the claim. {HANDOFF_APPLIES}

Read `slicer config handoff_requires_note_kind` before handing off. A nonempty value
requires `slicer note ID --kind KIND --text "Describe the verified outcome" --render` for the
current attempt. Previous-attempt and legacy unknown-attempt notes do not satisfy it;
`handoff --note` only records history. Changing attempts manually changes report association.

To run one slice through implementer, reviewer, architect and gate agents, print
the slice relay with `slicer ai relay`. Read the project's role matrix with
`slicer relay-roles show`, and change it only with the other `relay-roles` commands.

"""

BETWEEN_SLICES = (
  "## Between slices\n\n"
  "After the finish step and before claiming the next slice, compact or start a fresh "
  "session; state lives in slicer.\n\n"
)

STATE = """\
## State and command results

Change tracking state through slicer commands. Never hand-edit tracking JSON or
generated `.slicer/render/` markdown. Do not open or hand-merge `.slicer/render/`.
When `ROADMAP.md` or `ROADMAP.html` conflicts, run `slicer render` then `slicer check`.
Use `--render` on supported mutations, or
run `slicer render` separately, and finish with `slicer check`.

"""

FEEDBACK = """\
## Local feedback

When slicer itself gets in your way, record it without filing a roadmap item:
`slicer feedback --kind friction --text "What happened"` (`bug` and `feature` are
the other kinds). The log is local and gitignored. Preview what would go upstream
with `slicer feedback-report --dry-run`. Run it with `--yes` only when the owner
asks: that files GitHub issues through `gh`, which is a publish. A missing or
unauthenticated `gh` is code `external`. Open issues come back as roadmap rows
with `slicer issues-pull`: preview with `--dry-run`, and pull only when the owner
asks, because it files roadmap rows.

"""

IMPLEMENT_HEADING = "## Implement one slice\n\n"

INSTRUCTIONS = (
  f"{TRACKING_RULE}\n\n"
  + INTRO + PLAN + TRACKING + "\n"
  + IMPLEMENT_HEADING + LOOP + SPEC_GAP + "\n" + IMPLEMENT_MORE
  + HANDOFF_SECTION + BETWEEN_SLICES + STATE + FEEDBACK + EXITS
  + "\n" + DONE_CLOSER
)

REST_LEAD = (
  "This is the part of the guide the slicer skill does not carry. "
  "The skill has the tracking rule, the implement loop, and the exit codes."
)


def rest_text() -> str:
  """What `INSTRUCTIONS` says beyond the skill, for an agent that loaded the skill."""
  body = (
    REST_LEAD + "\n\n" + INTRO + PLAN + IMPLEMENT_HEADING + IMPLEMENT_MORE
    + HANDOFF_SECTION + BETWEEN_SLICES + STATE + FEEDBACK
  )
  return body.rstrip("\n") + "\n"


def instructions_text(finish: str = "done", required_note_kind: str = "") -> str:
  """The quick start. `handoff` uses that loop and the handoff closer."""
  text = INSTRUCTIONS
  if finish == "handoff":
    text = text.replace(LOOP, loop_text("handoff"), 1)
    text = text.replace(DONE_CLOSER, HANDOFF_CLOSER, 1)
    text = text.replace(HANDOFF_APPLIES, f"{HANDOFF_APPLIES} {HANDOFF_REVIEW_SENTENCE}", 1)
  if required_note_kind:
    text = text.replace(IMPLEMENT_HEADING, handoff_policy_text(required_note_kind) + IMPLEMENT_HEADING, 1)
  return text
