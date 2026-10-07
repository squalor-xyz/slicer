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

- Goals: `slicer goals --json --lean`.
- Inventory: `slicer list --json --lean`, `slicer status --json --lean`, and `slicer stats --json --lean`.
- Slice detail: `slicer show ID --json --lean` and `slicer next --ready --json --lean`.
- Search: `slicer find topic --json --lean`.
- Roadmap prose: `slicer prose list --json --lean` and `slicer prose show goals --json --lean`.
- History: `slicer log --json --lean`. Notes on an item come back with `slicer show ID --json --lean`.
- Validation: `slicer check --json`.

Use --lean on reads; it drops empty fields and paths. Drop it only when you need the full shape.

If a CLI read or edit is missing or fails, report that and propose a roadmap item.
Do not switch to the tracking files.

Source code, ordinary project documentation, templates, and this skill stay
readable when you are implementing. A task that explicitly asks you to inspect or
repair tracking internals is the only exception.

When slicer warns that the running code belongs to another worktree, run
`PYTHONPATH=src python3 -m slicer` for this checkout.

Before handoff, read `slicer config handoff_requires_note_kind`. If nonempty,
file `slicer note ID --kind KIND --text "Describe the verified outcome"`
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
    f"`slicer note ID --kind {shlex.quote(kind)} --text \"Describe the verified outcome\"`. "
    "The note must be nonempty and belong to the current implementation attempt. "
    "Release and resume keep its association; reject then restart needs a new report. "
    "Changing attempts manually changes which reports belong to the current attempt.\n\n"
  )


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
the context with `slicer note ID --text "Ready for review: ..."`, then run
`slicer handoff ID --render --json`. A reviewer picks up the next review item with
`slicer next --status review --start --ready --render --section "Check" --json`: it claims the
item and moves it to `reviewing`, so `next` never hands it to an implementer, and the
same command resumes it later. If the review fails, run
`slicer reject ID --note "VERDICT: FAIL - reason" --render --json`: it sends the item back
to open (or `--to STATUS`), records the verdict, and clears the claim. {HANDOFF_APPLIES}

Read `slicer config handoff_requires_note_kind` before handing off. A nonempty value
requires `slicer note ID --kind KIND --text "Describe the verified outcome"` for the
current attempt. Previous-attempt and legacy unknown-attempt notes do not satisfy it;
`handoff --note` only records history. Changing attempts manually changes report association.

"""

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
  + HANDOFF_SECTION + STATE + FEEDBACK + EXITS
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
    + HANDOFF_SECTION + STATE + FEEDBACK
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
