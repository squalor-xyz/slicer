# S09 — kill the eleven dead assertions

**Parked** (owner 2026-03-14): do not take this yet.

**Findings:** `T1` `T2` · **Size: M** · **Tree:** beacon

## Why

Eleven assertions compare a value to itself. See
[`../../20260314-architect-review.md`](../../20260314-architect-review.md).

## Files

- `beacon/tests/report.rs`

## Failing tests

Mutation: break the renderer, assert the suite goes red.

## Implement

Delete or fix each one.

**Not in this slice:** S10.

## Check

Suite still green after the mutation is reverted.

## Git

beacon only.
