# S14 — ledger column edits must work on layouts

**Findings:** owner charter · **Size: S** · **Tree:** ledger

## Why

Column edits still address the single-table shape S13 replaces. Design in
[`../toolbox.md`](../toolbox.md).

## Files

- `ledger/src/columns.rs`

## Failing tests

1. `Columns_EditOnLayout_RoutesToTheRightTable`.

## Implement

Route the edit through the layout.

**Not in this slice:** S13's split itself.

## Check

Green.

## Git

ledger only.
