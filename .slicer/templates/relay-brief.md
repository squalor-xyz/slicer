First run `slicer ai relay-orchestrator` and follow it.

# Orchestrator for {{id}}: {{title}}

Contract: {{contract}}

## Slice

Read `slicer show {{id}} --json --lean` end to end, including the boundary.
Slice: {{id}}, size {{size}}, effort {{effort}}.
Worktree: {{worktree}}
Branch: {{branch}}
Work only in this worktree.

## ENVIRONMENT

- Use python3.12, never python3 (python3 here is 3.9).
- Run slicer for this worktree as `PYTHONPATH={{worktree}}/src python3.12 -m slicer --root {{worktree}} ...`; in zsh define a function, not a variable.
- Every command that runs tests sets `TMPDIR={{worktree}}/.venv/tmp-<role>` (implementer, reviewer, gate, orchestrator); each agent cleans only its own directory.
- Never run `unittest discover -p`; filter with `-k '*Name*'`.
- Mutation runs use `PYTHONDONTWRITEBYTECODE=1` and clear `__pycache__`.
- The full suite, `python3.12 -m unittest discover -s {{worktree}}/tests -t {{worktree}}/tests`, runs in the foreground with a 600000 ms timeout: about 1 min alone, 25-40 min under parallel load. Iterate with `python3.12 {{worktree}}/tests/affected.py --run`.
- No `cd`: absolute paths and `git -C {{worktree}}`.
- Never read or edit `.slicer/*.json`, `.slicer/slices/`, `.slicer/log.jsonl` or `.slicer/render/`; use slicer reads.
- Follow `{{worktree}}/AGENTS.md`: two-space indent, stdlib only, invariants.

## Roles

{{roles}}

## Run

RUN={{run}}
Orchestrator step: {{step}}
Use this run and parent step when recording role steps. The outer session owns
beginning and ending the run and marking the orchestrator's step complete.

{{parallel}}

{{finish_override}}

## End point

After the gate's VERDICT: PASS and the project's finish step, commit on the slice
branch with title `{{commit_title}}`. Do not merge or push.

## Rules

- Follow the waiting rule from `slicer ai relay-orchestrator`: wait for every
  role and process to finish before reporting. A nudge is not a reason to report.
- Stop and report under the Stop section of `slicer ai relay-orchestrator`
  (an owner decision, repeated gate failures, or a contradictory specification)
  instead of guessing.
- Hand back one final run report, or a stop that needs the owner. Never hand
  back while work you started is still running.
