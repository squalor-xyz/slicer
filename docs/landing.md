# Landing a slice

The steps for merging a handed-off slice into `main`, moved out of
[AGENTS.md](../AGENTS.md#landing-a-slice) because they apply only when the owner asks.

Only when the owner asks to merge. Pick up the handoff through `--root`, review
the diff, and rerun the checks:

```sh
PYTHONPATH=.worktrees/<id>/src python3 -m slicer --root .worktrees/<id> next --status review --start --ready --render --section "Check" --json --lean
```

A failed review is `slicer reject <ID> --note "VERDICT: FAIL - reason" --render`.
Otherwise commit on its branch, merge into `main` with `git merge --no-ff`, then on
`main`:

```sh
PYTHONPATH=src python3 -m slicer land <ID> --branch feature/<id>-<slug> --note "Describe the verified outcome"
```

`land --owner NAME` records the done transition under that name, with the same actor precedence as `done --owner`.

`--branch` must be an exact short local branch name other than the checked-out branch. A tag, SHA, revision expression, full `refs/heads/NAME`, unknown branch or current branch is refused (`invalid_branch`), before anything is written.

`land` refuses (`not_merged`) unless the branch's tip is reachable from `HEAD`, and refuses (`run_active`) while a relay run on the slice is neither ended nor stale; either way nothing is written. Then it runs `done --render --check` and prints the git commands left: the commit of `.slicer`, `git worktree remove` for the branch's worktree, and `git branch -d`. Each printed git command uses `git -C ROOT`, with the absolute project root shell-quoted, so it works when `slicer --root OTHER land` runs from elsewhere. slicer runs none of them. It does not run the suite or merge.

A merge leaves `.slicer/render/` stale, or holding conflict markers when the clone has no `slicer-generated` driver. Until something regenerates it, `check` on `main` fails on the stale `ROADMAP.md` and `ROADMAP.html`, and `check` names that case. The next slicer write (`done --render` when landing, or any other command that changes state) regenerates the stale render files first and names them on stderr, so no clone needs the driver or a hook to keep the render current. A committed merge that holds conflict markers heals the same way. The merge itself must be finished first: while Git has a merge in progress, writes are refused and nothing is regenerated. `slicer render` still works at that point. `index.json` is never healed; with conflict markers in it a write is refused and the message names `slicer setup-git`.

After a plain merge, run `slicer check`; if the render is stale, `slicer render` (or any slicer write) fixes
it. Review and commit any rewritten render files. To regenerate it right at the merge, `slicer setup-git --hook`
prints an optional `post-merge` script and its install command (also returned as
`{hook, install}` with `--json`). See [Git merge drivers](commands.md#git-merge-drivers)
for installation that respects `core.hooksPath` and existing hooks. slicer installs
nothing. The hook leaves rewritten files for you to commit and reports other check
failures for manual attention. Git does not run `post-merge` when a merge stops on
conflicts; run `slicer render` and `slicer check` by hand after resolving those.
Git also skips `post-merge` for `git merge --no-commit`; after committing the merge,
run `slicer render` and `slicer check` by hand, then review and commit the rewritten files.

A slice with slivers lands once: its slivers run in the parent's worktree and
branch. Merge that branch with `git merge --no-ff`, then on `main` run `done` on each
sliver and last on the parent, and commit all of it as one "mark done" commit. `done` on
the parent refuses (`slivers_open`) until every sliver is done; naming them in one call
works too. The review pickup (`next --status review`) may return a sliver or the parent;
the combined gate reviews the parent's branch once:

```sh
PYTHONPATH=src python3 -m slicer done <SLIVER> [<SLIVER> ...] <PARENT> --note "Describe the verified outcome" --render --check
```

Commit that, remove the worktree and branch, and leave `main` clean. Pushing and
publishing wait until the owner asks.

## Landing a relay slice

A slice that ran through the relay (`slicer ai relay`) lands in the order the contract's Land
section gives. The manual flow above stays as it is: ff-only is the relay default, and `--no-ff`
stays valid for the manual flow above. slicer runs no git command in either flow; the outer
session runs the merge, commit, push and worktree removal itself. Only the owner's end point and
authorization decide how far it goes: `commit` stops after step 2 and then ends the run as in step 8,
`landed` runs every step, and a push waits until the owner asks. With the end point `finish`, the
run ends at the finish step with `relay-run end` and nothing below applies.

Before anything is merged or landed, the owner approves; the only commit before that is the slice
commit the orchestrator's brief allows. While it waits, the outer session records
`relay-run step RUN --step STEP --phase wait --waiting-on approval` on the orchestrator's step
(`--waiting-on owner` while an owner question from the report waits; a `blocker` the orchestrator
asked with `relay-run ask` already holds its step in that wait, and `relay-run answer` ends it).
A waiting step never makes its run stale, so
the run stays open and is not ended while it waits. The approval shows the gate verdict, the
tests, the diff stat, each owner-needed question and each `[HUMAN CHECK]` line of the slice's
Check section, and each `[AFTER LAND]` line the report names as `deferred to land:`. On approval, the outer session records `relay-run step RUN --step STEP --phase start`
to end the wait. A fix note goes back to the orchestrator as a fix round, then through review, the
gate and approval again. When the finish step already ran, the item first goes back to the
project's started status (`slicer set <ID> --status STATUS --render`;
`slicer config started_status` names it), and after the round's PASS the closing note and the finish step run again.

Each human check the owner confirms is recorded as a note on the item in the worktree,
`slicer note ID --text "Human check: ..." --render`, with `--kind human-check` only when the
project's `note_kinds` configures that kind (the default `note_kinds` is empty and permits any
kind, so a project can also add `human-check` to its own list). A human check the owner has not
confirmed is shown with a warning and does not block landing.

1. Rerun the slice's Check commands in the worktree, except `[AFTER LAND]` lines, which step 6 runs.
2. Commit on the slice branch what the worktree still holds: the human-check notes, and the finish
   step's state when the orchestrator did not commit it. Then the report's later notes as one more
   commit.
3. When main moved, merge main into the slice branch in the worktree. The merge drivers from
   `slicer setup-git` keep the index and the render files mergeable. A merge can make a golden
   stale: rebaseline it and commit the result.
4. `slicer render` in the worktree, then commit any rewritten render files.
5. `slicer check`; exit 1 means drift.
6. Rerun the full suite on the merged branch, then run each `[AFTER LAND]` line shown at Approve,
   now that main is merged in. A failure of either goes back to the orchestrator as a fix round.
   When the suite passes and the merge needed no code change beyond the merge drivers' and a
   rebaselined golden, the regate is the one-pass small-fix check on the merge diff alone: the
   read-only role that gave the last `VERDICT` is resumed with the merge diff. A merge that needs a
   code fix gets a fix round and the usual gate. No new hand-back line or wait state is involved.
7. On main, `git merge --ff-only feature/<id>-<slug>`. It refuses when main moved after step 3; go
   back to Land step 3.
8. `slicer relay-run end RUN --outcome pass --close-steps done`, before `slicer land`: `land`
   refuses (`run_active`) while a run on the slice is not ended.
9. On main, `slicer land <ID> --branch feature/<id>-<slug> --note "Describe the verified outcome"`.
   An ff-only merge satisfies its `not_merged` check, because the branch tip is reachable from
   `HEAD`.
10. Commit `.slicer` on main with the command `land` printed, push only when the owner authorizes
    it, then remove the worktree and the branch with the commands `land` printed.

## Schema bumps

A slice bumps the index schema when its CHANGELOG entry names a new index schema or its
diff changes `SCHEMA_VERSION` in `src/slicer/model.py`. It bumps the relay-roles schema
when the diff changes `ROLES_VERSION` in `src/slicer/roles.py`. Git runs the `slicer-index`
driver (`slicer merge-index`, for `.slicer/index.json`) and the `slicer-relay-roles` driver
(`slicer merge-relay-roles`, for `.slicer/relay-roles.json`) as `slicer` from PATH, as
`slicer setup-git` configures them. An editable install puts main's code on that PATH, which
refuses a newer schema, so the merge stops with a conflict in the file. Run the merge from
the `main` checkout root with a `slicer` shim first on PATH that runs the branch's code:

```sh
shim=$(mktemp -d) && printf '#!/bin/sh\nPYTHONPATH=%s exec python3 -m slicer "$@"\n' "$PWD/.worktrees/<id>/src" > "$shim/slicer" && chmod +x "$shim/slicer" && PATH="$shim:$PATH" git merge --no-ff feature/<id>-<slug>
```

The shim changes only which `slicer` Git finds; the driver configuration stays as
`setup-git` wrote it. Once the bump is on `main`, the editable install runs the new schema,
so later merges need no shim.
