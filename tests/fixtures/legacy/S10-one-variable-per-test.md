# S10 — one variable per differential test

**Parked** (owner 2026-03-14): unpark only when the owner asks.

**Findings:** `T3` · **Size: M** · **Tree:** beacon

## Why

Differential tests vary three inputs at once, so a failure names nothing. `T3` in
[`20260314-architect-review.md`](../../20260314-architect-review.md).

## Files

- `beacon/tests/diff.rs`

## Failing tests

One variable per case.

## Implement

Split each case.

**Not in this slice:** the golden files.

## Check

Green.

## Git

beacon only.
