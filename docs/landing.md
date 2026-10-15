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
PYTHONPATH=src python3 -m slicer done <ID> --note "Describe the verified outcome" --render --check
```

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
