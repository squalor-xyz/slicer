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
- You and every role run their own shell commands in the foreground and
  background none. Run long commands such as the full suite in the
  foreground with a long timeout.
- Stop and report under the Stop section of `slicer ai relay-orchestrator`
  (repeated gate failures or a wrong specification) instead of guessing. A
  `blocker` owner question is not a stop: ask it and wait as the Owner
  questions rule says.
- Hand back one final run report, or a stop that needs the owner. Never hand
  back while work you started is still running.
