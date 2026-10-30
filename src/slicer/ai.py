"""Keep agent onboarding available wherever slicer's Python code is installed."""

from __future__ import annotations

import re
import shlex
import textwrap

from slicer.relayrun import OUTCOMES, QUESTION_VALUE_RULE


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
# `ai relay-orchestrator` and carried in their `--json`. Bump it when a rule an outer
# session, orchestrator or role follows changes (order, gate, tracking order, waiting,
# report shape), not for a rewording. Record each version's change in RELAY_CHANGES.
RELAY_CONTRACT = 11

RELAY_CHANGES = (
  (5, "Setup resolves the role lineup, orchestrator included, with "
   "`relay-roles recommend` and the owner accepts it as a whole or overrides rows; a role "
   "runs as a native subagent on the orchestrator's harness and as a headless session on another."),
  (6, 'A failed review is recorded with `slicer note ID --text "Review FAIL: ..." --render` '
   "on the started item, not `reject`."),
  (7, "A small fix round is reviewed and gated in one pass; a role's slicer step that a harness "
   "permission check denies stops the run before the commit instead of being worked around."),
  (8, "A hand-back that names the role it waits on (`waiting on: ROLE AGENT-ID`) is a wait: the outer "
   "session relays that role's report to the orchestrator and stops it only when nothing is waited on."),
  (9, "Findings carry a kind (`fix`, `owner`, `later`), and `Review FAIL` is written only after `VERDICT: "
   "FAIL`; a UI pass role runs beside the reviewer; an optional spec check runs before any worktree "
   "exists and owner answers reach the run; a `blocker` owner question waits in the ledger "
   "(`relay-run ask`, `answer`) instead of stopping the relay, and a step waiting on the owner or on "
   "approval records `--phase wait` and never goes stale; the outer session adds Approve and Land steps "
   "with ff-only landing; the ledger records the `ui` and `spec-check` roles under their own names; and "
   "a Conformance section states what a scripted orchestrator owes."),
  (10, "Roles wait in the foreground: the outer session and the orchestrator spawn in the foreground "
   "where the harness can and wait for the report in the same turn, and wait on a headless role's "
   "process in the foreground (a timed-out wait is re-issued, never a hand-back). A harness that can "
   "only spawn in the background does not end the turn while a spawned role runs and waits for the "
   "completion notice. An early hand-back is sent only for an owner question, a permission denial or "
   "a failure; `waiting on: ROLE AGENT-ID` is only for a harness that forces the turn to end. Every "
   "role runs its own shell commands in the foreground and backgrounds none, and every brief says so. "
   "A vendor limit (a rate limit or out of credits) is not a stop: the failed step is recorded with its "
   "reason (`relay-run step --phase fail --reason`) and the round reruns as a new step on a fresh agent on "
   "the role's next matching row (never breaking the reviewer's independence), with the partial diff; the "
   "outer session does the same for an orchestrator; the switch is named on the deviations line; any other "
   "failure, or no eligible row, still stops."),
  (11, "The contract states each rule once and carries its data in `--json`. Every role, the read-only "
   "ones included, uses absolute paths and `git -C` and never `cd`, and the orchestrator resumes a headless "
   "role in the slice worktree and checks the directory it names. Before each review, fix rounds included, "
   "the implementer checks its own diff (concurrency and handle lifetime, docs updated in the same diff, a "
   "test that fails when the new wiring is removed) and reports each check or `n/a`. A gate round is closed "
   "with `--verdict pass` or `--verdict fail`, and a heartbeat on a waiting step ends its wait. "
   "`slicer ai relay-orchestrator --session` is a variant for a session that orchestrates every slice "
   "itself: it begins and ends the run, records its own step, asks the owner directly and runs Approve and "
   "Land, and the owner may choose the session variant at Setup even where nesting works. A small fix round is checked and "
   "gated in one pass by the read-only role that raised its findings; a non-blocker owner question is asked "
   "in the ledger as it arises, without a wait, and read with `relay-run answer --get` before the finish "
   "step, and the session variant takes the same non-blocker question rule; a Check line marked "
   "`[AFTER LAND]` is deferred to Land step 6; a clean merge of main is regated by the one-pass small-fix "
   "check. A `blocker` owner question is a wait, not a stop. Every `fix` finding fails the gate, with no "
   "severity scale, and a minor item is tagged `later`. An error exit of the agent process falls back like a "
   "vendor limit, to a fresh agent on the role's next matching row, with `role_fallback` as the conformance "
   "key (was `vendor_limit`); with no eligible row (a project with no role matrix has none), or on any "
   "other failure, the run still stops. `stop` rules carry keys "
   "(`gate_fail_3`, `spec_wrong`, `permission_denied`, `no_eligible_row`), the stop no longer lists a "
   "blocker owner decision, and conformance splits the stop obligation from a new `blocker_wait` one. "
   "`roles` carries the gate with ledger role `gate`. The orchestrator always runs as a general-purpose "
   "agent briefed with `slicer ai relay-orchestrator`, never an agent type, and an unmatched orchestrator "
   "means the outer session orchestrates."),
)


def contract_line() -> str:
  """The marker both relay texts start with, built from `RELAY_CONTRACT`."""
  return f"relay v{RELAY_CONTRACT}"


def pin_mismatch(pinned: int | None) -> str | None:
  """The refusal message when a project pins a relay contract other than the running one.

  None when nothing is pinned or the pin equals `RELAY_CONTRACT`. slicer never serves
  an older contract's text, so a mismatch is always a refusal where the contract is
  served, and a warning in `check`. The message names both versions and the two ways out.
  """
  if pinned is None or pinned == RELAY_CONTRACT:
    return None
  return (
    f"this project pins relay v{pinned}, but this slicer serves relay v{RELAY_CONTRACT}. "
    f"Install a slicer that serves relay v{pinned}, or, once the tools that consume the "
    f"contract follow v{RELAY_CONTRACT}, run `slicer config relay_contract {RELAY_CONTRACT}`"
  )


RELAY_SETUP = (
  ('spec_check', """\
whether to run a spec check on a batch of slices before any worktree exists (see
  Roles). It is optional. The owner answers the whole batch in one sitting. The outer
  session records each answer in its slice as an `Owner decisions` paragraph with
  `slicer edit ID --section SECTION --append --text "Owner decisions: ..." --render`
  (SECTION being the section the question concerns), and commits those filings on main
  before the worktree is cut, because a worktree starts at HEAD and an uncommitted
  filing never reaches it; the owner authorizes that commit. The brief carries the
  same paragraph."""),
  ('roles', """\
which roles run. The architect is optional: the owner decides. On a slice the
  project marks as UI, a UI pass runs beside the reviewer, or the reviewer carries it
  where the project folds it in by slice size."""),
  ('split', """\
whether the implementation splits into parts. The default is one implementer.
  A split needs parts with disjoint files and their own tests; a slice too big
  for one implementer is two slices or a slivered slice."""),
  ('sliver', """\
whether to sliver the slice. The default is no. Propose slivers only when the
  slice's effort and the cut allow it (see Slivers) and when that costs less than
  one implementer. The outer session confirms the sliver list and which slivers
  run in parallel with the owner."""),
  ('role_models', """\
the agent, vendor, model and reasoning effort for each role. A role on the
  orchestrator's own harness runs as a native subagent; a role on another harness
  runs as a headless session in the slice worktree with the same brief."""),
  ('orchestrator', """\
the orchestrator: either the outer session itself (the session variant,
  `slicer ai relay-orchestrator --session`) or a spawned orchestrator with its agent,
  model and reasoning effort set explicitly on every spawn. The owner may choose the
  session variant, also where nesting works; an agent that cannot nest subagents has no
  other choice, and an orchestrator that no matrix row matches, or a project with no
  matrix, means the outer session orchestrates. A spawned orchestrator runs as a
  general-purpose agent briefed with `slicer ai relay-orchestrator`."""),
  ('environment', """\
the environment lines every brief carries: the interpreter, the shell limits
  and the scratch directory, taken from the project's own instructions."""),
  ('end_point', """\
where the run ends:
  - after the finish step,
  - after a commit on the slice branch, or
  - landed. Land only when the owner asks."""),
)

RELAY_END_OPTIONS = ("finish", "commit", "landed")

RELAY_STEPS = (
  (1, """\
Create the worktree, then start the run and record the orchestrator's step,
   with the role `orchestrator` (see Record the run below)."""),
  (2, """\
Spawn the orchestrator in the foreground where the harness can, with a brief that
   starts "First run `slicer ai relay-orchestrator` and follow it.", then the
   environment lines, the slice, the worktree and branch, the roles with agent,
   vendor, model and reasoning effort, whether the slice is a UI slice and whether
   the reviewer carries the UI pass, for a separate UI pass the skills the project's
   role matrix names for the UI pass or its vendor-neutral UI prompt, the slice's
   `Owner decisions` paragraph and any owner answer since, the end point, a temp
   directory per role, RUN and the orchestrator's step id."""),
  (3, """\
Wait for its run report in the same turn. A harness that can spawn only in the
   background (its spawn notifies on completion) does not end the outer turn while
   the orchestrator runs: wait for the completion notice. One slice runs at a time.
   The orchestrator hands back early only for an owner question, a permission denial
   or a failure, or with the last line `waiting on: ROLE AGENT-ID` when the harness
   forces its turn to end while a role runs. That last-line hand-back is a wait, not
   a report: wait for that role's report, then resume the orchestrator with the
   report pasted in. Do not stop the orchestrator for it. An owner answer given while
   the run waits goes in a note on the item in the worktree,
   `slicer note ID --text "Owner answer: ..." --render`, and in the resumed role's
   brief, not in the slice on main. A hand-back whose last line is
   `waiting on: owner QUESTION_ID` is a wait for the owner's answer to a question the
   orchestrator asked with `relay-run ask`, not a report: `slicer relay-run list RUN`
   shows the question. If the question is already answered (a dashboard recorded it),
   read it with `slicer relay-run answer RUN QUESTION_ID --get` and resume the
   orchestrator with it. Otherwise put it to the owner, record the answer with
   `slicer relay-run answer RUN QUESTION_ID --option LABEL --why TEXT --by NAME`
   (`slicer relay-run answer RUN QUESTION_ID --text TEXT` for a free-text answer),
   which ends the step's owner wait, then record the note as above and resume the
   orchestrator with the answer pasted in."""),
  (4, """\
Check the report's first line, the contract marker, and its last line,
   `nothing left running: yes`. Stop the orchestrator first when a hand-back lacks
   that last line and names no running role and no unanswered owner question, or
   names a role that has already finished and whose report the orchestrator already
   has, or a question whose answer the orchestrator was already resumed with."""),
  (5, """\
Approve, unless the end point is `finish`: end the run there with
   `slicer relay-run end RUN --outcome pass --close-steps done`. Nothing is merged or
   landed before the owner approves; the only commit before it is the slice commit
   the orchestrator's brief allows. While the owner decides, record the wait on the
   orchestrator's step with
   `slicer relay-run step RUN --step STEP --phase wait --waiting-on approval`, or
   with `--waiting-on owner` while the owner answers an owner-needed question from
   the report. A `blocker` question the orchestrator asked with `relay-run ask`
   already holds its step in that wait. Do not end the run while it waits: a waiting
   step never makes its run stale. Show the owner the gate verdict, the tests, the
   diff stat, each owner-needed question, and each `[HUMAN CHECK]` line of the
   slice's Check section. Show each `[AFTER LAND]` line the report names as
   `deferred to land:` too; it runs at Land step 6, not here. Record each human check
   the owner confirms as a note on the item in the worktree with
   `slicer note ID --text "Human check: ..." --render`. Add `--kind human-check` only
   when `note_kinds` configures that kind. A human check the owner has not confirmed is
   shown with a warning and does not block landing.
   The owner approves, or sends a fix note. On approval, record
   `slicer relay-run step RUN --step STEP --phase start` to end the wait. Record a
   fix note as a note on the item in the worktree, record `--phase start` on the step
   to end the wait, and resume the orchestrator with the note for a fix round, which
   is reviewed and gated again before Approve repeats. When the finish step already
   ran, send the item back to the project's started status with
   `slicer set ID --status STATUS --render` (`slicer config started_status` names it)
   before the fix round; after its PASS, the closing note and the finish step run
   again."""),
  (6, """\
Land when the end point is `commit` or `landed` and the owner approved: follow
   the Land section."""),
)

RELAY_LAND_INTRO = """\
Land follows Approve and covers only what the owner's end point and authorization
allow. slicer runs no git command: the outer session runs the merge, commit, push and
worktree removal itself. The end point `commit` stops after step 2 and then ends the run
as in step 8; `landed` runs every step."""

RELAY_LAND = (
  (1, """\
Rerun the slice's Check commands in the worktree, except `[AFTER LAND]` lines, which
   step 6 runs."""),
  (2, """\
Commit on the slice branch what the worktree still holds: the human-check notes,
   and the finish step's state when the orchestrator did not commit it. Then file the
   report's later notes as one more commit on the slice branch."""),
  (3, """\
When main moved, merge main into the slice branch in the worktree with slicer's
   merge drivers (`slicer setup-git` configures them); a conflict a driver leaves goes
   to the owner. A merge can make a golden stale: rebaseline it and commit the result."""),
  (4, """\
Run `slicer render` in the worktree and commit any rewritten render files."""),
  (5, """\
Run `slicer check`; exit 1 means drift: fix it, then render and check again."""),
  (6, """\
Rerun the full suite on the merged branch, then run each `[AFTER LAND]` line shown
   at Approve, now that main is merged in. A failure of either goes back to the
   orchestrator as a fix round. When the suite passes and the merge needed no code
   change beyond the merge drivers' and a rebaselined golden, the regate is the one-pass
   small-fix check on the merge diff alone, as Order says; a merge that needs a code fix
   gets a fix round and the usual gate."""),
  (7, """\
On main, merge the branch with `git merge --ff-only BRANCH`; ff-only is the
   contract's default. When main moved after step 3, go back to Land step 3."""),
  (8, """\
End the run with `slicer relay-run end RUN --outcome pass --close-steps done`,
   before the `land` command, which refuses (`run_active`) while a run on the slice is
   not ended."""),
  (9, """\
On main, run `slicer land ID --branch BRANCH --note "Describe the verified outcome"`.
   It refuses (`not_merged`) unless the branch tip is reachable from HEAD. It marks the
   slice done, renders and checks, and prints the git commands left."""),
  (10, """\
Commit `.slicer` on main with the command `land` printed, push only when the owner
   authorizes it, then remove the worktree and the branch with the commands `land`
   printed."""),
)

ORCHESTRATOR_STEPS = (
  (1, """\
The implementer runs `slicer start ID --render --strict`, red tests and
   implementation."""),
  (2, """\
Review; on a slice the project marks as UI, the UI pass runs in parallel with the
   reviewer. Then accepted findings back to the implementer for each fix round,
   then the architect, when the owner chose one, then the gate, which still decides."""),
  (3, """\
Only after `VERDICT: PASS`, the implementer runs the closing note and finish step:
   {finish_step}"""),
  (4, """\
After the finish step, commit on the slice branch only when your brief allows it."""),
)

RELAY_REPORT_MAX_LINES = 30

RELAY_REPORT_LINES = (
  ('contract', """\
Contract: {contract}"""),
  ('slice', """\
SLICE ID OUTCOME {outcomes} BRANCH NAME @ SHA"""),
  ('worktree', """\
worktree: PATH"""),
  ('rounds', """\
rounds: N, last verdict: VERDICT LINE"""),
  ('tests', """\
tests: COUNTS"""),
  ('diff_stat', """\
diff stat: STAT"""),
  ('deviations', """\
deviations accepted: LIST OR none"""),
  ('later_notes', """\
later notes: LIST OR none"""),
  ('owner_needed', """\
owner needed: N. TITLE | options: LABEL:SUMMARY; LABEL:SUMMARY | recommend: LABEL | blocker (optional) | key: AREA.TOPIC (optional) | reasoning: TEXT"""),
  ('role_steps', """\
role steps: STEP IDS"""),
  ('nothing_left_running', """\
nothing left running: yes"""),
)

RELAY_STOP = (
  ('gate_fail_3', "the same slice fails its gate three times"),
  ('spec_wrong', "the spec turns out ambiguous or wrong"),
  ('permission_denied', "a harness permission check denies a role's slicer step"),
  ('no_eligible_row', "a role's round ends in a vendor limit or an error exit and no eligible row "
   "is left to rerun it on (a project with no role matrix has none)"),
)

# What a vendor limit or an error exit does. It is a paragraph of the Stop section: the
# fallback is not a stop condition, and `no_eligible_row` in `RELAY_STOP` is where it ends.
RELAY_FALLBACK = """\
A vendor limit or an error exit is not a stop condition. A role's round that ends in a
rate limit, out of credits or an error exit of the agent process is a failed round;
classify it from the vendor's or harness's error or the process's exit, not from the
role's own account. A gate FAIL, a wrong spec or a permission denial is not an error
exit and keeps its own rule. Record the failed step with that error as its reason
(`slicer relay-run step RUN --step STEP --phase fail --reason TEXT`), then start the
same round as a new `relay-run step` on a fresh agent on the role's next matching matrix
row, with the partial diff left in the worktree. Rows only move forward: on out of
credits skip later rows on the same vendor, on a rate limit or an error exit take the
plain next matching row. The new row keeps the role for the rest of the run. The fresh
agent is never a resumed one. Independence holds on the new row as on every row: a
reviewer or gate never wrote code in the slice (in a split, in any part of it), and an
implementer never reuses the reviewer's or gate's agent. Name the switch on the report's
deviations line as `role switched: ROLE FROM to TO (REASON)`, FROM and TO being row ids.
With no eligible row left (a project with no role matrix has none), or on any other
failure, stop. When the orchestrator's own round ends in a vendor limit or an error
exit, the next matching row takes over from the ledger and the worktree: the outer
session starts a new orchestrator on it, or the owner restarts the session when the
session orchestrates itself."""

# The gate's criteria and verdict lines, as data: `RELAY_GATE` renders them and
# `relay_contract` carries the same lists in `--json`.
RELAY_GATE_CRITERIA = (
  "earlier blockers resolved with a proving test",
  "no `fix` finding",
  "no `blocker` owner finding",
  "tests green",
  "docs matching the code",
)

RELAY_GATE_VERDICTS = ("VERDICT: PASS", "VERDICT: FAIL")

# Each finding kind and what happens to it. The text continues the bullet after the kind;
# its wraps are kept in the prose and joined with single spaces in `--json`.
RELAY_FINDING_KINDS = (
  ('fix', """\
goes to the fix round."""),
  ('owner', """\
a design call for the owner, with 2 to 4 options and a preferred one.
  It goes in the run report as an owner-needed question and does not block the gate
  unless it is marked `blocker`."""),
  ('later', """\
a follow-up item with a one-line title. It goes in the report's later notes."""),
)

# What any orchestrator owes the contract, an LLM agent or a program. Each entry is a
# bullet of the Conformance section and an item of `conformance` in `--json`. It points at
# Order, Gate, Stop and Run report rather than restating their data, and the two literals
# it must name (the passing verdict and the report's last line) come from the data that
# renders those sections, through `_relay_conformance`, so those cannot drift.
RELAY_CONFORMANCE_INTRO = """\
These obligations bind any orchestrator, whether an LLM agent briefed with this text
or a program that spawns the roles, records the ledger and applies the rules in code.
The rest of this contract is worded for an agent; a program meets each obligation in its
own code. `slicer ai relay --json` and `slicer ai relay-orchestrator --json` carry the
order, the gate, the finding kinds, the run report, the outcomes, the stop rules and
these obligations as data, so a program reads them there instead of restating them;
`slicer ai relay --json` also carries the roles."""


def _relay_conformance(pass_verdict: str, last_line: str) -> tuple[tuple[str, str], ...]:
  """The obligations, with the passing verdict and the report's last line filled in."""
  items = (
    ('role_order', """\
Run the roles in Order's sequence: the implementer, the reviewer (with the UI pass
  beside it on a UI slice), the architect when the owner chose one, then the gate.
  Accepted findings go back to the implementer before the next review or gate."""),
    ('gate_rule', """\
Decide the gate on the criteria in Gate, with its verdict as the first line as Gate
  says. A FAIL, or an implementer change after a review, needs another gate."""),
    ('finding_kinds', """\
Tag every reviewer, UI pass and gate finding `fix`, `owner` or `later`, and route
  each as Gate says."""),
    ('stop_rules', """\
Stop when a stop condition in Stop holds, with OUTCOME stopped, and return the run
  report. Do not go on past it and do not ask the owner. A permission denial stops
  before the commit, with the denial's exact text as a `blocker` owner-needed question."""),
    ('blocker_wait', """\
Wait for the answer to a `blocker` owner question instead of stopping: record it with
  `relay-run ask`, then wait for the answer where the owner gives it. An orchestrator an
  outer session spawned ends its turn with `waiting on: owner QUESTION_ID` and goes on
  only from the answer the outer session records and resumes it with; a session that
  orchestrates itself waits for the owner in its own session. Stop with OUTCOME stopped
  only when the answer cannot be waited for."""),
    ('role_fallback', """\
When a role's round ends in a vendor limit (a rate limit or out of credits) or an
  error exit of the agent process, record the failed step with its reason, rerun the
  same round as a new step on a fresh agent on the role's next matching row (later rows
  on the same vendor skipped on out of credits, independence kept as Stop says) with the
  partial diff, and name the switch on the run report's deviations line. A gate FAIL, a
  wrong spec or a permission denial is not an error exit. The same fallback applies to
  an orchestrator, as Stop says. With no eligible row left, or on any other failure,
  stop as Stop says."""),
    ('ledger', """\
Record each role's round in the run ledger with `relay-run step`: a new step for every
  round, a parent step for a spawned role, and done or fail when the round ends; a gate
  round is closed with `--verdict pass` or `--verdict fail`. The
  outer session begins and ends the run. A failed record never stops the relay."""),
    ('finish_after_pass', """\
Run the closing note and the finish step only after `{pass_verdict}`. A fix round that
  follows runs them again only after its own PASS."""),
    ('run_report', """\
Return one run report, in the line order of Run report and within its line limit. Send
  it once, never an interim run report. Its first line is the contract marker and its
  last is `{last_line}`,
  written only after nothing the run started still runs."""),
    ('worktree_scope', """\
Work only in the worktree the brief names, with absolute paths and no change of
  directory, and write no product code."""),
    ('commit_scope', """\
Commit on the slice branch only when the brief allows it. Never merge, land, push or
  rebase onto main: the outer session owns those."""),
  )
  return tuple(
    (key, text.replace("{pass_verdict}", pass_verdict).replace("{last_line}", last_line))
    for key, text in items
  )


RELAY_CONFORMANCE = _relay_conformance(
  RELAY_GATE_VERDICTS[0],
  next(template for key, template in RELAY_REPORT_LINES if key == "nothing_left_running"),
)


def _relay_steps_text(steps: tuple[tuple[int, str], ...]) -> str:
  """Keep the contract's existing wraps while rendering its numbered data."""
  return "\n".join(f"{number}. {text}" for number, text in steps)


def relay_contract(finish: str = "done", *, orchestrator: bool = False, session: bool = False) -> dict:
  """Expose the same contract data as the prose, with single-line JSON text.

  `session` is the session variant of the orchestrator's contract, so it needs
  `orchestrator`; its `session` key comes last, after every key of the orchestrator.
  """
  def flat(text: str) -> str:
    return " ".join(text.split())

  if session and not orchestrator:
    raise ValueError("session is the orchestrator's variant: pass orchestrator=True with it")
  payload = {}
  if not orchestrator:
    payload["setup"] = [
      {"key": key, "text": flat(text), **({"options": list(RELAY_END_OPTIONS)} if key == "end_point" else {})}
      for key, text in RELAY_SETUP
    ]
  steps = SESSION_STEPS if session else ORCHESTRATOR_STEPS if orchestrator else RELAY_STEPS
  payload["order"] = [
    {"step": number, "text": flat(text.format(finish_step=_relay_finish_step(finish)))}
    for number, text in steps
  ]
  if not orchestrator:
    payload["land"] = [{"step": number, "text": flat(text)} for number, text in RELAY_LAND]
  payload["report"] = {
    "max_lines": RELAY_REPORT_MAX_LINES,
    "lines": [
      {"key": key, "template": template.format(contract=contract_line(), outcomes="|".join(OUTCOMES))}
      for key, template in RELAY_REPORT_LINES
    ],
  }
  payload["outcomes"] = list(OUTCOMES)
  payload["stop"] = [{"key": key, "text": text} for key, text in RELAY_STOP]
  payload["changes"] = [{"version": version, "text": text} for version, text in RELAY_CHANGES]
  # New keys go after `changes`, so every earlier key keeps its position.
  if not orchestrator:
    # `ledger_role` is the `relay-run step --role` name a role records under, or null for the
    # outer session, which records no step of its own. It comes after `text`, so the keys
    # `role` and `text` keep their positions.
    payload["roles"] = [
      {"role": role, "text": flat(text), "ledger_role": ledger}
      for role, ledger, text in RELAY_ROLES
    ]
  payload["gate"] = {"criteria": list(RELAY_GATE_CRITERIA), "verdicts": list(RELAY_GATE_VERDICTS)}
  payload["finding_kinds"] = [{"kind": kind, "text": flat(text)} for kind, text in RELAY_FINDING_KINDS]
  payload["conformance"] = [{"key": key, "text": flat(text)} for key, text in RELAY_CONFORMANCE]
  if session:
    payload["session"] = True
  return payload


# The outer session's and each role's part, as data. `RELAY_HEAD` renders each entry into
# the Roles section, and `relay_contract` carries the same text in `--json`.
RELAY_ROLES = (
  ('outer_session', None, """\
The outer session is the session the owner talks to. It runs Setup with the
owner, owns the relay run, spawns one orchestrator per slice, reads only that
orchestrator's run report, lands branches one at a time when the owner
authorizes it, files the report's later notes, and writes no product code."""),
  ('orchestrator', 'orchestrator', """\
The orchestrator runs one slice under the contract that
`slicer ai relay-orchestrator` prints. It briefs every agent, reads the reports
and reads diffs as `slicer ai relay-orchestrator` says, reruns the tests only as
Order says, and writes no product code."""),
  ('implementer', 'implementer', """\
The implementer owns one slice. It runs `slicer start ID --render --strict`,
writes the failing tests and watches them go red, implements, mutation-checks
each new test, and closes the slice. Line numbers a slice cites may have moved since
its spec was written; at start the implementer re-anchors them against the code in
the worktree."""),
  ('reviewer', 'reviewer', """\
The reviewer is read-only. It reviews correctness, test strength and direction
against `slicer goals`."""),
  ('architect', 'architect', """\
The architect is read-only. It confirms each finding was fixed and reviews the
shape."""),
  ('ui_pass', 'ui', """\
The UI pass is read-only and edits nothing. It runs beside the reviewer on a slice
the project marks as UI; the project says what counts as a UI slice. It returns
triaged suggestions, each tagged with a finding kind, and, where the project can
render, the changed views. The gate still decides."""),
  ('gate', 'gate', """\
The gate is read-only and decides. It checks the diff under review against the criteria
in Gate, gives its verdict as the first line of its report, and sends a FAIL back to the
implementer."""),
  ('spec_check', 'spec-check', """\
The spec check is optional, read-only and edits nothing. It runs before any worktree
exists, on a batch of slices the owner picks. For each slice it returns `ready` or
questions in the shape of the run report's `owner needed` line, each with 2 to 4
options, a preferred one, and `blocker` where the slice cannot go on without the
answer. The outer session spawns it and reads its report. It has no matrix row, so the
owner names its agent, model and reasoning effort at Setup like any role. It does not
re-anchor the line numbers a slice cites: the implementer does that late."""),
)

# The slice relay, shared by both finish modes. The close is appended separately
# so the handoff text can avoid naming the other finish command. It is two constants
# so the owner's role matrix can sit between Setup and Order (see relay_text).
# Slivers and Gate follow Order as constants of their own. The marker line that opens
# the text is added by relay_text.
RELAY_HEAD = """\
# Slice relay

An outer session runs the relay. For each slice it spawns one orchestrator, which runs
the slice through an implementer, a reviewer (and, on a UI slice, a UI pass), an
architect and a gate. The relay uses slicer's own commands.

## Roles

{role.outer_session}

{role.orchestrator}

The owner may choose the session variant at Setup, and an agent that cannot nest
subagents has no other choice: the outer session is also the orchestrator and follows
`slicer ai relay-orchestrator --session` itself, compacting or starting a fresh session
between slices.

{role.implementer}

In a split, one lead implementer owns the slice. It runs
`slicer start ID --render --strict`, the note and the finish step. Part
implementers change only their own files, write and run their own tests, and
run no slicer command that writes.

{role.reviewer}

{role.architect}

{role.ui_pass}

{role.gate}

{role.spec_check}

The reviewer's and the gate's independence is the rule in Stop.
The owner chooses the models.

## Setup

Before the worktree exists, the outer session reads the slice's size with
`slicer show ID --json --lean` and confirms these with the owner:

{setup}

Wait for the owner's answers before creating the worktree.

""".replace("{setup}", "\n".join(f"- {text}" for _key, text in RELAY_SETUP))
for _key, _ledger, _text in RELAY_ROLES:
  RELAY_HEAD = RELAY_HEAD.replace("{role." + _key + "}", _text)
del _key, _ledger, _text

RELAY_ORDER = """\
## Order

The outer session, for each slice:

{order}

Each slice gets a fresh orchestrator, so no orchestrator carries one slice's
context into the next.

The orchestrator runs its one slice in the worktree it was given:
`slicer start ID --render --strict`, red tests, implementation, review (on a slice
the project marks as UI, the UI pass runs in parallel with the reviewer),
accepted findings back to the implementer, then
the architect, when the owner chose one, then the gate, which still decides. Close with
`slicer note ID --text "Describe the verified outcome" --render` plus the finish step.
The orchestrator commits on the slice branch only when its brief allows it; merge
and push are the outer session's, when the owner authorizes them.

Record the run as it goes. The outer session starts it with
`slicer relay-run begin ID --worktree PATH`, where PATH is the slice worktree. It prints
the run id; keep that id as RUN. Record each role when it starts with
`slicer relay-run step RUN --role ROLE --agent AGENT --vendor VENDOR --model MODEL --reasoning REASONING --phase start --task TASK`,
which prints the step id. When the step ends, record it with
`slicer relay-run step RUN --step STEP --phase done` or `--phase fail`. A gate round is
closed with its verdict, the word of its `VERDICT:` line:
`slicer relay-run step RUN --step STEP --phase done --verdict pass`
or `--verdict fail`. A subagent adds
`--parent STEP` for the step that spawned it. The spec check runs before any
orchestrator, so the outer session records its round too, with `--role spec-check` and
no `--parent`, in a run it begins on the batch's slices, and ends that run with
`slicer relay-run end RUN --outcome pass --close-steps done` once it has read the spec
check's report (`--outcome stopped` if the owner stops it).
`slicer relay-run step RUN --step STEP --phase start` re-records a long-running step as
a heartbeat, so its run does not go stale after 30 minutes. A heartbeat on a waiting
step ends its wait: do not heartbeat a step that waits. A heartbeat passes no
`--task`, so the step keeps the task it was recorded with; each round records its own
step, without `--step`. End the run when its end point is reached, or when the relay or
the owner stops it, with `slicer relay-run end RUN --outcome pass --close-steps done`
(or `--outcome fail` or `stopped`, and `--close-steps fail` when the steps failed). It
first marks each step still running, the orchestrator's own included, and without
`--close-steps` it names those steps on stderr. For `landed`, that is after the ff-only
merge and before the `land` command; see Land. For `commit`, after the commit on the
slice branch. Never end a run while a step waits for the owner or for approval.
Recording is advisory: a failed `relay-run` call never stops the relay. A role whose
sandbox may write only inside its worktree still records: when the shared ledger is
unwritable, `relay-run` writes the worktree's own `.slicer/relay-runs.jsonl` and says so
on stderr, and `relay-run end` from a session that can write the shared ledger copies
those events into it. Making `<main>/.git/slicer` a writable root for that vendor avoids
the fallback.

In a split, the parts run in the slice worktree, in parallel only when their
files are disjoint, otherwise one after another. The orchestrator runs the full
suite on the combined diff, then one review and one gate cover the whole slice.
Accepted findings go back to the part that owns the file.

A whole-slice implementer runs the full suite once; in a split, parts run only
their own tests and the orchestrator's run on the combined diff is the one full
run. The orchestrator reruns the suite only if the diff changed after that run or
main moved.

Owner answers: Order step 3 says where an owner answer goes. While the run goes on, the
owner, or a dashboard that reads the ledger, may answer a non-blocker question with
`relay-run answer`; the outer session does so only when its harness leaves it free while
the orchestrator runs. A project that keeps a decision log also records
there a design answer the owner explicitly picked, not a default an agent assumed.

Run slices in parallel only when their files are disjoint. The outer session
starts their orchestrators together and waits for the reports without polling.
Review and land them one at a time. When main moved and the slice branch takes a merge
of main (Land step 3), rerun the suite on the merged branch. If it passes and the merge
needed no code change beyond the merge drivers' and a rebaselined golden, the regate is
the one-pass small-fix check: resume the read-only role that gave the last VERDICT on
the merge diff alone. A merge that needs a code fix gets a fix round and the usual gate.

""".replace("{order}", _relay_steps_text(RELAY_STEPS))

RELAY_LAND_SECTION = (
  "## Land\n\n" + RELAY_LAND_INTRO + "\n\n" + _relay_steps_text(RELAY_LAND) + "\n\n"
)

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

{criteria}
The first line is exactly {verdicts}.
FAIL goes back to the implementer and then the gate again.
A `fix` finding fails the gate: tag a minor item `later` instead.
Write a `Review FAIL` note only after `VERDICT: FAIL`.
Record a failed review on the started item with
`slicer note ID --text "Review FAIL: ..." --render`.
A gate that passed writes none, even when a fix round follows it (UI edits, say).
Add `--kind` only when `note_kinds` configures a kind for reviews.
Any implementer change after a review needs another gate.

Findings: every reviewer, UI pass and gate finding is tagged with one kind.

{kinds}

""".replace(
  "{criteria}", textwrap.fill(
    "Name the diff under review. The criteria are: "
    + ", ".join(RELAY_GATE_CRITERIA[:-1]) + ", and " + RELAY_GATE_CRITERIA[-1] + ".",
    width=84, break_long_words=False, break_on_hyphens=False,
  )
).replace(
  "{verdicts}", " or ".join(f"`{verdict}`" for verdict in RELAY_GATE_VERDICTS)
).replace(
  "{kinds}", "\n".join(f"- `{kind}`: {text}" for kind, text in RELAY_FINDING_KINDS)
)

RELAY_BRIEF = """\
## Every brief

Include the slice from `slicer show ID --json --lean`, the tracking rule below,
and the workspace limit: stay inside the workspace the owner named and do not
read or write outside it. Every brief carries the slice's `Owner decisions` paragraph,
when it has one, and each owner answer recorded in a note since, verbatim. The report
is a diff stat, red lines, test counts, a mutation table, the implementer's self-check
(each of the three: what it checked, or `n/a`) and deviations. Every brief
names the role's own temp directory, and no two agents share one. Every brief carries
the environment lines verbatim. Every brief says the role runs its own shell commands in
the foreground and backgrounds none, and runs long commands such as the full suite in
the foreground with a long timeout.
Reviewer, architect and gate briefs forbid git writes; a needed write stops and
goes to the owner. Every brief says that every role, the read-only ones included,
uses absolute paths and `git -C` and never `cd`. Reviewer, UI pass and gate briefs
tell the agent to tag each finding `fix`, `owner` or `later` as Gate defines, and to
give an `owner` finding its 2 to 4 options and a preferred one. The UI pass brief
says the agent is read-only, edits nothing and makes no git writes, and tells it to
load the skills the project's role matrix names for the UI pass; a vendor that
cannot load skills gets the project's vendor-neutral UI prompt instead. It asks for
each finding's kind and, where the project can render, the changed views. The
project may fold the UI pass into the reviewer by slice size, such as the reviewer
on a small slice and a separate role on a larger one; where it does, the reviewer's
brief also carries the UI pass brief, and no separate UI pass runs. The implementer
brief says cited line numbers may have moved and to re-anchor them at start. Before
each review, fix rounds included, the implementer checks its own diff for concurrency
and handle lifetime (locks, files and processes it opens are released on every path),
for docs that describe the changed behaviour updated in the same diff, and for a test
that fails when the new wiring is removed (a mutation that disconnects the call site),
and fixes what it finds. The spec-check brief says the agent is read-only, edits
nothing and makes no git writes, and asks for `ready` or `owner needed` lines for each
slice. A part brief names the part's own files and the files it must not touch. A
Check line marked `[AFTER LAND]` needs something main does not have yet, such as
another slice landed. The implementer and the gate do not run it, the finish step does
not wait for it, and the orchestrator names it on the deviations line as
`deferred to land: LINE`. The outer session shows it at Approve and runs it at Land
step 6, after main is merged in; a failure goes back as a fix round. With end point
`finish` or `commit`, it goes in the report's later notes instead.

Start each agent with the model and reasoning effort its role row names, set
explicitly rather than inherited from the session. Launch it by its row's agent: on
the orchestrator's own harness as a native subagent, on another harness as a headless
session in the slice worktree with the same brief. Give each agent a short label
`ID role`, for example `s279 implementer` or `s279 gate`, and pass the same label
as the `--task` line of `relay-run step`.

"""


RELAY_CONFORMANCE_SECTION = (
  "## Conformance\n\n" + RELAY_CONFORMANCE_INTRO + "\n\n"
  + "\n".join(f"- {text}" for _key, text in RELAY_CONFORMANCE) + "\n\n"
)


def _fill(text: str) -> str:
  """Wrap a computed sentence at 88 columns, with no space inside a backticked span broken."""
  masked = re.sub(r"`[^`]*`", lambda m: m.group(0).replace(" ", "\x00"), text)
  wrapped = textwrap.fill(masked, width=88, break_long_words=False, break_on_hyphens=False)
  return wrapped.replace("\x00", " ")


def relay_shared_sections(required_note_kind: str = "") -> str:
  """Keep the gate, briefs, stop rules and conformance identical in both relay contracts."""
  return (
    RELAY_GATE + RELAY_BRIEF + TRACKING_RULE + "\n\n"
    + _fill(
      "Reviewers get the direction sources: `slicer goals --json --lean` and the queue, "
      "`slicer list --json --lean`."
    ) + "\n\n"
    + "## Stop\n\n"
    + _fill(
      "Stop when " + ", ".join(text for _key, text in RELAY_STOP[:-1])
      + ", or " + RELAY_STOP[-1][1] + "."
    )
    + "\n\n"
    + RELAY_FALLBACK + "\n\n"
    + handoff_policy_text(required_note_kind)
    + RELAY_CONFORMANCE_SECTION
  )


# The role matrix lives in `.slicer/relay-roles.json` and changes only through
# `slicer relay-roles`. Its render is added to the relay only for a project that has it,
# so the generic text never names a vendor.
ROLES_PROPOSE = """\
Propose the lineup (agent, vendor, model and reasoning effort for each role) from the
matrix below. `slicer relay-roles recommend ID --well-specified yes|no --split yes|no`
matches the slice against the matrix and resolves each role, the orchestrator included,
to the first matching row. It reports a role no row matches as unmatched
(`(no matching row)` in its text, `unmatched` in its JSON), and the owner fills that
role by naming a row or drops it, as with an architect on a slice below size L in the
example matrix. It also lists each role's other matching rows as alternatives and says
why it chose each row. You judge whether the slice is well specified and whether it is a
part of a split slice. Pass `--split yes` for a part of a split slice and `--split no`
for a whole one. The matrix order decides which row a role gets, unless the owner names
a preferred agent (`--prefer-agent AGENT`, which a role without a matching row on that
agent ignores) or a row for a role (`--select ROLE=ROW_ID`, once per role, only for a
row that matches the slice; an unmatched role has no such row, so a row the owner names
for it goes into the lineup as named, not through `--select`); the harness the owner
launched the relay from changes nothing by itself. Show the resolved lineup once, and
let the owner accept it as a whole or override individual rows, then run `recommend`
again with those `--prefer-agent` and `--select` overrides. Accepting the lineup settles
only the agent, vendor, model and reasoning effort of each role: it does not by itself
authorize starting paid sessions, committing or landing, and the other Setup decisions
stay the owner's.

"""

ROLES_MISSING = """\
This project has no role matrix. `slicer relay-roles init` writes an example for the
owner to adjust.

"""


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
      "Close the slice with\n"
      '`slicer done ID --note "Describe the verified outcome" --render --check`.\n'
    )
  return (
    f"Contract: {contract_line()}\n\n"
    + RELAY_HEAD + matrix + RELAY_ORDER + RELAY_LAND_SECTION + RELAY_SLIVERS
    + relay_shared_sections(required_note_kind)
    + "## Close\n\n"
    + close
  )


# The orchestrator's whole contract and run report. It names the outer session's steps only
# to forbid them, except in the session variant (`ai relay-orchestrator --session`), where the
# session is the outer session too and takes those steps for itself. The bullets that differ
# are slots: `ONE_SLICE_PARTS` fills them for the one-slice orchestrator and `SESSION_PARTS`
# for the session. The shared sections stay identical to relay.
ONE_SLICE_PARTS = {
  "opening": """\
# Slice relay orchestrator

You run one slice for the outer session that spawned you. This text is your whole
contract for one slice. Your brief names the slice, the
worktree and branch, your roles with their agent, vendor, model and reasoning effort,
whether the slice is a UI slice and whether the reviewer carries the UI pass, for a
separate UI pass the skills the project's role matrix names for the UI pass or its
vendor-neutral UI prompt,
where the run ends, RUN and your own step id. When the slice is done or stopped,
return one run report.""",
  "worktree": """\
- Work only in the worktree your brief names. Use absolute paths and never change
  directory. Write no product code. Read and change tracking state only through
  slicer commands.""",
  "record": """\
- Never run `relay-run begin` or `relay-run end`, mark your own step done, land, merge,
  rebase onto main or push. The outer session owns those.""",
  "answers": """\
- Owner answers: an answer the outer session pastes in on resume is the owner's, not a
  role's report. Carry it verbatim, with the slice's `Owner decisions` paragraph, into
  the brief of the role you resume or start. The outer session has already recorded it
  as a note on the item in the worktree. That includes the answer to a question you
  asked with `relay-run ask`.""",
  "questions": """\
- Owner questions: an owner decision marked `blocker` that you cannot go on without is
  asked in the ledger, where the owner or a dashboard can answer it:
  `slicer relay-run ask RUN --step STEP --title TITLE --option LABEL:SUMMARY --option LABEL:SUMMARY --recommend LABEL --blocker --key AREA.TOPIC --reasoning TEXT`,
  where STEP is your own step id and the fields are those of the run report's
  `owner needed` line (`--recommend`, `--key` and `--reasoning` are optional). It prints
  the question id and puts your step in the owner wait. End your turn with the last line
  `waiting on: owner QUESTION_ID`. The outer session records the answer with
  `slicer relay-run answer RUN QUESTION_ID --option LABEL` (or `--text TEXT`) and
  resumes you with it pasted in; to read it again, run
  `slicer relay-run answer RUN QUESTION_ID --get`. Do not poll the ledger for the
  answer. A `blocker` is not a stop: wait for the answer as above, and return OUTCOME
  stopped only when you cannot wait for it (the harness cannot resume you, or the
  `relay-run ask` fails). Ask an owner question that does not block as soon as it
  arises, with the same command without `--blocker`. It records the question and leaves
  your step running: do not end your turn or wait for it. Before the closing note and
  the finish step, read each with `slicer relay-run answer RUN QUESTION_ID --get`; an
  answer that changes the slice goes to the implementer as a fix round first. A question
  still open then goes on the report's owner-needed line.""",
  "waiting": """\
- Waiting: Run each role in the foreground and wait for its report in the same turn: a
  native subagent where the harness offers a foreground spawn, and a headless role with
  a foreground wait on its process. That wait is one command with a long timeout; when
  it times out before the role ends, issue the same foreground wait again, and a
  timed-out wait is never a reason to hand back.
  - A harness that can spawn only in the background (its spawn notifies on completion)
    does not end your turn while a spawned role runs: wait for its completion notice.
  - End your turn before the final report only to hand back an owner question, a harness
    permission denial or a failure. A progress note or a bare "waiting on" is not one,
    and a nudge to report while a role you started still runs is not a reason to report.
  - `waiting on: ROLE AGENT-ID` is the last line of a hand-back only when the harness
    forces the turn to end while a role still runs, so the outer session can route the
    report. When you wait for the owner's answer to a question you asked, it is
    `waiting on: owner QUESTION_ID`. A role report pasted in by the outer session on
    resume is that role's report.
  - A role that reports while a command of its own still runs, or that stops waiting on
    its own background work, has not finished its round: resume it and tell it to
    finish, in the slice worktree when it is headless, as the Gate rule below says.
  - An early hand-back for an owner question, a permission denial or a failure is not a
    run report. Run no sleep loop, no monitor keepalive and no background shell job;
    nothing you started still runs when you report.""",
  "commit": """\
- After `VERDICT: PASS` and the finish step, commit on the slice branch if your
  brief allows it.""",
  "stop_return": """\
- Stop and return without asking the owner when a stop condition in Stop holds, with
  OUTCOME stopped. An `owner` finding that is not a `blocker` does not stop the relay:
  it goes in the report as a numbered owner-needed question and the slice goes on to the
  gate.""",
  "order_intro": "Run your one slice in the worktree you were given, with the roles your brief lists:",
}

SESSION_PARTS = {
  "opening": """\
# Slice relay orchestrator for a session

You run one slice for the session the owner talks to, which orchestrates every slice
itself, so this session is also the outer session. This text is your whole contract for
one slice. Setup in `slicer ai relay`, which you confirm with the owner before the
worktree exists, names the slice, the worktree and branch, your roles with their agent,
vendor, model and reasoning effort, whether the slice is a UI slice and whether the
reviewer carries the UI pass, the skills the project's role matrix names for a separate
UI pass or its vendor-neutral UI prompt, and where the run ends. When the slice is done
or stopped, give the owner one run report.""",
  "worktree": """\
- Work only in the worktree Setup names, except Approve and Land (Order step 5), which
  run on main as `slicer ai relay`'s Land section says. Use absolute paths and never
  change directory. Write no product code. Read and change tracking state only through
  slicer commands. Where a section below speaks of the outer session (resuming you,
  owning merge and land, ending a turn with `waiting on:`, or starting a new
  orchestrator), you are that outer session: wait for the owner in this session and
  record answers yourself.""",
  "record": """\
- Run record: begin the run with `slicer relay-run begin ID --worktree PATH`, where PATH
  is the slice worktree, and keep its id as RUN. Record your own step as role
  `orchestrator` with
  `slicer relay-run step RUN --role orchestrator --agent AGENT --vendor VENDOR --model MODEL --reasoning REASONING --phase start --task TASK`,
  with no `--parent`; it prints its id as STEP. End the run as the Order section of
  `slicer ai relay` says: `slicer relay-run end RUN --outcome pass --close-steps done`
  once the end point is reached, or `--outcome stopped` when the slice stops. Mark your
  own step done or failed when the slice ends.""",
  "answers": """\
- Owner answers: the owner answers in this session. Record each answer as a note on the
  item in the worktree with `slicer note ID --text "Owner answer: ..." --render`, and
  carry it verbatim, with the slice's `Owner decisions` paragraph, into the brief of
  each role you resume or start. An answer to a question you asked with `relay-run ask`
  is also recorded in the ledger:
  `slicer relay-run answer RUN QUESTION_ID --option LABEL` (or `--text TEXT`).""",
  "questions": """\
- Owner questions: put each owner question to the owner in this session, with its
  options and your preferred one. An owner decision marked `blocker` that you cannot go
  on without is also asked in the ledger, where a dashboard can answer it:
  `slicer relay-run ask RUN --step STEP --title TITLE --option LABEL:SUMMARY --option LABEL:SUMMARY --recommend LABEL --blocker --key AREA.TOPIC --reasoning TEXT`,
  where STEP is your own step id and the fields are those of the run report's
  `owner needed` line (`--recommend`, `--key` and `--reasoning` are optional). It puts
  your step in the owner wait. Wait for the owner's answer in this session, record it
  as Owner answers says, and go on. If a dashboard answered first, read the answer with
  `slicer relay-run answer RUN QUESTION_ID --get`, and do not poll the ledger for it. A
  `blocker` is not a stop: wait for the answer in this session, and end the run stopped
  only when the owner stops the slice. Ask an owner question that does not block in the
  ledger too, as soon as it arises, with the same command without `--blocker`. It
  records the question and leaves your step running: put it to the owner and go on
  without waiting. Before the closing note and the finish step, read each with
  `slicer relay-run answer RUN QUESTION_ID --get`; an answer that changes the slice goes
  to the implementer as a fix round first. A question still open then goes on the
  report's owner-needed line.""",
  "waiting": """\
- Waiting: Run each role in the foreground and wait for its report in the same turn: a
  native subagent where the harness offers a foreground spawn, and a headless role with
  a foreground wait on its process. That wait is one command with a long timeout; when
  it times out before the role ends, issue the same foreground wait again.
  - A harness that can spawn only in the background (its spawn notifies on completion)
    does not end your turn while a spawned role runs: wait for its completion notice.
  - A role that reports while a command of its own still runs, or that stops waiting on
    its own background work, has not finished its round: resume it and tell it to
    finish, in the slice worktree when it is headless, as the Gate rule below says.
  - Run no sleep loop, no monitor keepalive and no background shell job; nothing you
    started still runs when the slice ends.""",
  "commit": """\
- After `VERDICT: PASS` and the finish step, commit on the slice branch only when the
  end point Setup chose is `commit` or `landed`.""",
  "stop_return": """\
- Stop without asking the owner when a stop condition in Stop holds: end the run with
  `--outcome stopped`, and give the owner the run report. An `owner` finding that is not
  a `blocker` does not stop the relay: it goes in the report as a numbered owner-needed
  question and the slice goes on to the gate.""",
  "order_intro": "Run your one slice in the worktree Setup names, with the roles Setup chose:",
}

SESSION_STEPS = (
  ORCHESTRATOR_STEPS[0],
  ORCHESTRATOR_STEPS[1],
  ORCHESTRATOR_STEPS[2],
  (4, """\
After the finish step, commit on the slice branch only when Setup's end point is
   `commit` or `landed`."""),
  (5, """\
This session is the outer session too: run Approve and Land as Order steps 5 and 6 of
   `slicer ai relay` and its Land section say, once the owner approves. Between slices,
   compact or start a fresh session; state lives in slicer."""),
)

ORCHESTRATOR_RULES = """\
{opening}

## Rules

{worktree}
{record}
- Relay run: record a new step for every round (implement, each fix round, review, the
  UI pass as role `ui`, gate) with
  `slicer relay-run step RUN --role ROLE --agent AGENT --vendor VENDOR --model MODEL --reasoning REASONING --parent STEP --phase start --task TASK`,
  where STEP is your own step id, and mark it done or failed when the round ends. A gate
  round is closed with its verdict, `--phase done --verdict pass` or `--verdict fail`. A
  heartbeat, `slicer relay-run step RUN --step STEP --phase start`, passes no new
  `--task`. A heartbeat on a waiting step ends its wait: do not heartbeat a step
  that waits.
{tracking}\
{answers}
{questions}
{waiting}
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
  A headless role you resume is given the slice worktree as its working directory,
  and you ask it to name the directory it ran in. Check that the directory it names
  is the slice worktree before you go on.
{commit}
- A small fix round (accepted findings only, no new files) is checked and gated in one
  pass: resume the read-only role that raised those findings (the reviewer, the
  architect or the gate), which checks the new diff against them and gives the VERDICT.
  Anything larger gets a review round, then a gate round.
- A role's slicer step that a harness permission check denies: do not run it yourself
  and do not work around it. Stop before the commit with OUTCOME stopped and put the
  denial's exact text under owner needed, as a `blocker` question whose reasoning is
  that exact text, with options such as `grant:` and `skip:`.
- A role whose round ends in a vendor limit or an error exit: record its step failed
  with `--reason`, start the same round as a new step on a fresh agent on the next
  matching row and put `role switched: ROLE FROM to TO (REASON)` on the deviations line.
  Stop says what counts, how the next row is chosen and when to stop.
{stop_return}

## Order

{order_intro}

{order}

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

At most {max_lines} lines, in this order, and nothing else:

```
{report}
```

Each owner question is one line and counts toward the line limit. Number the
questions from 1 and give each 2 to 4 options.
{question_rule}
`reasoning` is the last field and takes the rest of the line. With no question, the line
is `owner needed: none`. A role switched after a vendor limit or an error exit goes on
the deviations line as `role switched: ROLE FROM to TO (REASON)`, so the report names it
without a line of its own. Write the last line only after you check that nothing you
started still runs.
"""


def _relay_finish_step(finish: str) -> str:
  """Share the finish wording between Tracking order and structured Order."""
  if finish == "handoff":
    return (
      "`slicer note ID --text \"Describe the verified outcome\" --render`\n"
      "  and then the finish step, `slicer handoff ID --render --check`."
    )
  else:
    return (
      "the finish step,\n"
      "  `slicer done ID --note \"Describe the verified outcome\" --render --check`."
    )


def orchestrator_text(
  finish: str = "done", required_note_kind: str = "", *, session: bool = False,
) -> str:
  """The orchestrator's contract, which `slicer ai relay-orchestrator` prints.

  It follows the project's finish step and report kind the way `relay_text` does, and
  opens with the same marker line. `session` gives the variant for a session that
  orchestrates every slice itself (`--session`).
  """
  step = _relay_finish_step(finish)
  # The handoff note is too long to follow "They are " on a line of 88 columns, so it wraps.
  lead = "\n  " if finish == "handoff" else " "
  tracking = (
    "- Tracking order: the closing note and the finish step run only after\n"
    f"  `VERDICT: PASS`. They are{lead}{step}\n"
    "  Every implementer brief says so; before review the implementer runs neither.\n"
  )
  parts = SESSION_PARTS if session else ONE_SLICE_PARTS
  steps = SESSION_STEPS if session else ORCHESTRATOR_STEPS
  body = ORCHESTRATOR_RULES.format(
    **parts,
    tracking=tracking,
    order=_relay_steps_text(steps).format(finish_step=step),
    max_lines=RELAY_REPORT_MAX_LINES,
    report="\n".join(line["template"] for line in relay_contract(finish)["report"]["lines"]),
    question_rule=textwrap.fill(
      QUESTION_VALUE_RULE, width=88, break_long_words=False, break_on_hyphens=False,
    ),
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
adjusts them with `set`. To file one item with its fields and spec, pass `import`
a one-item outline; `--dry-run` first. An outline takes the same keys as `add`,
`short_title` included, and the sections the project configures, not assumed
headings. For a row with no spec, use
`slicer add "Title" --importance 3 --urgency 2 --effort 1 --render --json`; add
dependencies with repeated `--depends-on ID`. To give an existing row its spec, use
`slicer promote ID --stdin --render --json` with a one-item outline for its lead and
sections.
Flag an open owner decision with `slicer set ID --add-flag needs-owner` instead of
only writing prose. Configured `hold_flags` keep it out of `next` and list it under
`held`; `status` and `list --held` show the waiting work. Record the owner's answer
with `slicer decide ID --text "Answer" --render`; it adds a `decision` note and
clears every hold flag.

"""

# What follows SPEC_GAP inside "Implement one slice". Only the full text has it.
IMPLEMENT_MORE = """\
Do not combine `--ready` and `--show`.

Projects can add statuses in `config.statuses`. `next` offers only open work and
resumes started work; parked, review, reviewing, and custom statuses `draft` or
`blocked` stay out. Configured `hold_flags` also hold items out without changing status.

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
