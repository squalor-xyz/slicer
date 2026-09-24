# S02 — restore the build gate and build the hosts

**Findings:** `G3` · ROADMAP 2 · **Size: M** `[OWNER]` · **Trees:** kernel, atlas
**Depends on:** S01 (the entry point the workflow calls)

## Why

The workflow references a script that does not exist yet, so CI is green by
omission. Read `G3` in [the pass-2 review](../../20260209-architect-review.md) first.

## Files

- `kernel/ci/gate.yml`
- `atlas/build/hosts.props`

## Failing tests

1. `Workflow_MissingScript_FailsTheJob`.

## Implement

Build the hosts, then call S01's entry point.

**Not in this slice:** the sampling path.

## Check

CI green on all three hosts.

## Git

two trees: kernel and atlas.
