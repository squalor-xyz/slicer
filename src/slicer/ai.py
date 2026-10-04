"""Keep agent onboarding available wherever slicer's Python code is installed."""

from __future__ import annotations


# The implement loop and the exit-code rules. The instructions and the agent
# skill both use these sentences, so a wording change cannot land in only one.
LOOP = """\
1. Run `slicer next --ready --section "Implement" --section "Check" --json --lean`
   to get the next item and only those sections. The headings are examples;
   pass the section names the project configures (`slicer sections`).
   Read its scope, dependencies, and acceptance checks. Resolve missing or
   ambiguous specifications before changing its status. If the specification is
   trusted and needs no clarification, `slicer next --start --ready --section "Implement" --section "Check" --json --lean` may
   combine these first steps.
2. Otherwise, run `slicer start ID --render --strict --json` after reviewing the slice.
   Implement the agreed scope, update
   affected documentation, and run the slice's checks and required project tests.
   Edit a slice with `slicer edit ID --section NAME --text "Body" --render --strict --json`.
3. Review the changes and run `slicer check --json`. Fix problems before marking
   work complete; this tracking check does not replace code tests.
4. Run `slicer done ID --note "Describe the verified outcome" --render --json`,
   then `slicer check --json`. If `implement_finish` is `handoff`, run
   `slicer handoff ID --render --json` instead and do not run done.
"""

# Step 4 when implement_finish is handoff. The done command stays out of this text.
HANDOFF_STEP = """\
4. Run `slicer handoff ID --render --json`,
   then `slicer check --json`.
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
  "take a slice, implement the next slice, or drive slicer."
)

# Reads and edits of tracking state. The instructions and the skill both use
# this block, so the rule cannot land in only one of them.
TRACKING = """\
## Read and change tracking state through the CLI

Use the slicer CLI to read and to change goals, items, slices, notes, history,
and roadmap prose. This applies while reviewing a roadmap, planning work,
implementing a slice, and validating the result.

Do not open, search, parse, or edit tracking JSON, the history file, or generated roadmap and slice output to obtain or change that state.

- Goals: `slicer goals --json`.
- Inventory: `slicer list --json`, `slicer status --json`, and `slicer stats --json`.
- Slice detail: `slicer show ID --json` and `slicer next --ready --json`.
- Search: `slicer find topic --json`.
- Roadmap prose: `slicer prose list --json` and `slicer prose show goals --json`.
- History: `slicer log --json`. Notes on an item come back with `slicer show ID --json`.
- Validation: `slicer check --json`.

If a CLI read or edit is missing or fails, report that and propose a roadmap item.
Do not switch to the tracking files.

Source code, ordinary project documentation, templates, and this skill stay
readable when you are implementing. A task that explicitly asks you to inspect or
repair tracking internals is the only exception.

When slicer warns that the running code belongs to another worktree, run
`PYTHONPATH=src python3 -m slicer` for this checkout.

`slicer handoff ID --render --json` records work ready for review. Mark that work done
only after review and merge are complete.
"""


def loop_text(finish: str = "done") -> str:
  """The implement loop. `handoff` replaces step 4 and drops the done command."""
  if finish == "handoff":
    head, _step = LOOP.rsplit("4. ", 1)
    return head + HANDOFF_STEP
  return LOOP


def skill_text(finish: str = "done") -> str:
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
    f"{SPEC_GAP}\n"
    f"{EXITS}\n"
    f"{TRACKING}\n"
    "The rest of the guide is `slicer ai instructions`.\n"
  )


INSTRUCTIONS = """\
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

""" + TRACKING + """
## Implement one slice

""" + LOOP + SPEC_GAP + """
Do not combine `--ready` and `--show`.

Projects can add statuses in `config.statuses`. `next` offers only open work and
resumes started work; parked, review, reviewing, and custom statuses `draft` or
`blocked` stay out. Hold an item back with a custom status, not a flag.

`start` claims the item. `wt:NAME` marks one started in a sibling worktree;
`next` skips those and reports them in `in_work_elsewhere`. `slicer list --json`
shows every claim, and `slicer release ID` clears a claim without changing status.
Use `--owner NAME` on `start`, `next --start`, `handoff`,
`release` and `done`, or set `SLICER_CLAIM_OWNER`; `slicer log --by NAME` shows
what each did.

Re-read one slice with its title, dependencies, and scope boundary using
`slicer show ID --section "Implement" --section "Check" --context --json`.
Those headings are examples; use the section names the project configures.
Run `--help` on any command for the rest of its options.

## Hand off for review

When the implementation is ready for someone else to review, merge, or clean up, record
the context with `slicer note ID --text "Ready for review: ..."`, then run
`slicer handoff ID --render --json`. A reviewer picks up the next review item with
`slicer next --status review --start --ready --section "Check" --json`: it claims the
item and moves it to `reviewing`, so `next` never hands it to an implementer, and the
same command resumes it later. If the review fails, run
`slicer reject ID --note "VERDICT: FAIL - reason" --render --json`: it sends the item back
to open (or `--to STATUS`), records the verdict, and clears the claim. Run `done` only
after review and merge are complete.

## State and command results

Change tracking state through slicer commands. Never hand-edit tracking JSON or
generated `.slicer/render/` markdown. Do not open or hand-merge `.slicer/render/`.
When `ROADMAP.md` or `ROADMAP.html` conflicts, run `slicer render` then `slicer check`.
Use `--render` on supported mutations, or
run `slicer render` separately, and finish with `slicer check`.

""" + EXITS


def instructions_text(finish: str = "done") -> str:
  """The quick start. `handoff` uses that loop; anything else is `INSTRUCTIONS`."""
  if finish == "handoff":
    return INSTRUCTIONS.replace(LOOP, loop_text("handoff"), 1)
  return INSTRUCTIONS


INSTRUCTIONS = f"{TRACKING_RULE}\n\n" + INSTRUCTIONS
