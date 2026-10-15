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
