# S03 — a faulted run must refuse, not drift

**Findings:** `C1` `C2` · **Size: L** · **Tree:** ledger

## Why

A faulted run keeps answering. The reader cannot tell a real zero from a
dropped frame, which is the whole problem
[`20260105-architect-review.md`](../../20260105-architect-review.md) opens with.

## Files

- `ledger/src/session.rs`

## Failing tests

1. `Session_Faulted_RefusesNextRead`.
2. `Session_Faulted_NamesTheFault`.

## Implement

Refuse, and say which fault.

**Not in this slice:** retry policy.

## Check

Both tests red first.

## Git

ledger only.
