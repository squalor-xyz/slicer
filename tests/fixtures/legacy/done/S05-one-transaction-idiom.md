# S05 — one transaction idiom, schema changes inside it

**Findings:** `M1` `M2` · **Size: M** · **Trees:** ledger, atlas

## Why

Schema changes run outside the transaction, so a failure halfway leaves a
store that matches no schema version. See
[`20260209-architect-review.md`](../../20260209-architect-review.md).

## Files

- `ledger/src/store.rs`
- `atlas/src/migrate.rs`

## Failing tests

1. `Migrate_FailsMidway_RollsBackTheSchema`.

## Implement

One idiom: open, change, commit. Nothing outside.

**Not in this slice:** the column editor.

## Code review (same slice)

Reviewed alongside the implementation, because the rollback path has no
caller a test can reach from outside.

## Check

Green, and the store still opens.

## Git

two trees.
