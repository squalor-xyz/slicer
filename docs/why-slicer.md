# Why slicer

Planning in loose markdown works while one person and one agent use it. Once several
people or agents share the plan, the questions change: who holds each task, what is
blocked, and whether the plan still matches the code. slicer keeps the plan as checked
state in your repository. The state is JSON under `.slicer/`, changed through commands,
and rendered to readable markdown in `.slicer/render/`.
For the concepts behind these answers, and how an item moves from queued to finished, see
[How slicer works](how-it-works.md).

- **Picking the next task.** `slicer next` hands you one item you can start now: its
  dependencies are done and its required sections are filled in. Started work comes
  first, then by pass and priority, and a blocker inherits the priority of the work it
  holds up. See [picking and listing work](commands.md#picking-and-listing-work) and
  [priority](commands.md#priority).

- **Two people or agents on one task.** `start` claims the item for its owner, and history
  records that name. `next` skips work that a sibling git worktree on this machine has
  claimed or started. `start` also warns on stderr when another branch or worktree already
  names the slice, but it does not refuse the start. See
  [picking and listing work](commands.md#picking-and-listing-work).

- **A roadmap that drifts from the code.** `slicer check` is the CI gate. It fails on stale
  generated files, sync targets that have drifted, and integrity problems. `slicer refs`
  checks the `path:line` references in slice text against the files they cite, and says
  which still hold. `slicer verify --landed ID` checks that a done item is committed in the
  history of a ref, `HEAD` by default. See
  [getting started](getting-started.md#8-wire-it-into-ci) and the
  [command reference](commands.md).

- **Merge conflicts on tracking files.** Parallel branches collide on tracking files, such
  as the `next_id` line of `index.json`. Run `slicer setup-git | sh` once per clone to turn
  on the merge drivers. The `slicer-index` driver merges independent items and fields, so
  only a real disagreement stays a conflict. `slicer-relay-roles` merges the relay role
  matrix, and `slicer-generated` keeps the current branch's copy of `render/`, which you
  then re-render. See [the loop](getting-started.md#6-the-loop) and the
  [command reference](commands.md).

- **Agents that need a contract.** Every command an agent runs takes `--json`, and a
  failure is JSON too, with a stable error `code` to branch on. Exit codes separate drift
  (1), usage, validation or an empty queue (2), and hard failures (3).
  `slicer ai instructions` prints an agent's quick start. See
  [exit codes](agents.md#exit-codes).

- **Vague specs.** A slice has named sections. `next` leaves out a slice whose required
  sections are empty, which by default are `Implement` and `Check`. `slicer sections` lists
  the sections and marks the required ones, and `required_sections` in
  `.slicer/config.json` sets them. See [configuration](configuration.md).

- **Review before done.** `slicer handoff` moves a started slice to review and frees its
  claim. A reviewer picks it up with `slicer next --status review --start`, then closes it
  with `slicer done` or sends it back to the queue with `slicer reject --note`.
  `slicer ai relay` prints an implementer, reviewer and gate workflow, with an optional
  architect, and an optional role matrix suggests the agent, model and effort for each
  role. See [the loop](getting-started.md#6-the-loop) and
  [relay roles](commands.md#relay-roles).

- **Nothing to host.** The roadmap is JSON in your repository, so it moves with branches
  and clones. slicer is standard-library Python 3.11+ with no dependencies. It runs locally
  and needs no AI service and no API key. See [install](getting-started.md#1-install).

## When slicer is not for you

slicer is not a real-time collaboration tool, and it is not a live shared service. Git
carries the coordination. State stays in each checkout's `.slicer/`, not in a database or
a home-directory store. If several people need to edit one plan at the same moment, use a
different tool. `slicer goals` prints the project's goals and non-goals.
