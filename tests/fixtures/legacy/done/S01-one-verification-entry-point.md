# S01 — one verification entry point that can actually fail

**Findings:** `G1` `G2` · **Size: M** · **Tree:** kernel

## Why

The gate runs three scripts and none of them can fail the build. A guard that
cannot fire is not a guard. See [`../../20260105-architect-review.md`](../../20260105-architect-review.md).

## Files

- `kernel/scripts/verify.sh`
- `kernel/ci/gate.yml`

## Failing tests

1. `Gate_GuardTrips_ExitsNonZero` — trip a guard, assert the exit code.

## Implement

One entry point. Every guard reports through it.

**Not in this slice:** the host build; that is S02.

## Check

`./verify.sh` red, then green.

## Git

kernel only.
