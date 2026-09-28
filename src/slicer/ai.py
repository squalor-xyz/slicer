"""Keep agent onboarding available wherever slicer's Python code is installed."""

from __future__ import annotations


# The implement loop and the exit-code rules. The instructions and the agent
# skill both use these sentences, so a wording change cannot land in only one.
LOOP = """\
1. Run `slicer next --show --json` to get the next item and its slice together.
   Read its scope, dependencies, and acceptance checks. Resolve missing or
   ambiguous specifications before implementation.
2. Run `slicer start ID --render --json`. Implement the agreed scope, update
   affected documentation, and run the slice's checks and required project tests.
3. Review the changes and run `slicer check --json`. Fix problems before marking
   work complete; this tracking check does not replace code tests.
4. Run `slicer done ID --note "Describe the verified outcome" --render --json`,
   then `slicer check --json`. Report results and any remaining limitations.
"""

EXITS = """\
Use `--json` for automation and inspect both the exit code and payload. Failures
can return an `error` object containing `code`, `message`, and `command`; branch
on the stable code rather than message wording. Human diagnostics also go to
stderr. Exit 0 means success; exit 1 is a failed check or validation report whose
details must be inspected; exit 2 means a usage or validation error, or no next item;
exit 3 means an internal or state error (`corrupt`, `locked`, `io`, `config`, `schema_too_new`).
In particular, `next` can return exit 2 with `{"item": null, "blocked": [...]}`
and no error object: inspect the blocked items rather than assuming work is done.
"""

SPEC_GAP = """\
If `next` JSON includes `unspecified`, those ids are not ready. Fill each missing section with `slicer edit ID --section NAME` and run `next` again. Do not invent the section body.
"""

SKILL_DESCRIPTION = (
  "Drive a slicer roadmap. Use when asked to take a slice, implement the next "
  "slice, or drive slicer."
)


def skill_text() -> str:
  """The SKILL.md Claude Code, Codex, and Grok all load."""
  return (
    "---\n"
    "name: slicer\n"
    f"description: {SKILL_DESCRIPTION}\n"
    "---\n"
    "\n"
    f"{LOOP}\n"
    f"{SPEC_GAP}\n"
    f"{EXITS}\n"
    "The rest of the guide is `slicer ai instructions`.\n"
  )


INSTRUCTIONS = """\
# Getting started with slicer

slicer is a local roadmap and slice manager. You supply the reasoning, planning,
and implementation; slicer stores, prioritizes, validates, and renders the agreed
work. It does not call an AI service. This guide is generic and reads no project
state, so it also works before a project is initialized.

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

For one item, use `slicer add "Title" --render --json`. Add dependencies with
repeated `--depends-on ID`. A row alone is not a specification: use
`slicer promote ID --stdin --render --json` with a one-item outline to supply its
lead and sections. Use the project's configured sections, not assumed headings.

## Implement one slice

""" + LOOP + SPEC_GAP + """
`next` resumes eligible started work before open work; dependencies gate both.
Within that pool, effective priority includes priority inherited from dependents.
Use `slicer list --json` (omits done and retired, ordered as `next` walks the queue and then the other visible rows by score; `--all` or `--status` includes the hidden statuses), `slicer list --sort score --json` (same rows, flat effective-score order), `slicer stats --json`,
and `slicer log --json` to inspect the roadmap. Use `slicer show ID --section NAME`
to read one section, and `slicer edit ID --section NAME --text "Body" --render` to
replace it. To return only selected section bodies plus the title, dependencies,
and scope boundary, repeat `--section` and add `--context`:
`slicer show ID --section "Implement" --section "Check" --context --json`.
Those headings are examples; use the section names the project configures.
Use `--help` on a command for supported options.

## State and command results

Change tracking state through slicer commands. Never hand-edit tracking JSON or
generated `.slicer/render/` markdown. Use `--render` on supported mutations, or
run `slicer render` separately, and finish with `slicer check`.

""" + EXITS + """
Full agent reference and reusable prompts:
https://github.com/squalor-xyz/slicer/blob/main/docs/agents.md
"""
