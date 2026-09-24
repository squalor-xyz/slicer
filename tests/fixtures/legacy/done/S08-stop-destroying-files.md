# S08 — stop destroying files silently

**Findings:** `M6` · **Size: S** · **Tree:** beacon

Found while reading S07; filed separately because it is a different tree.

## Why

The writer truncates the destination before it knows the source is readable.
Filed as `M6` in [the pass-2 review](../../20260209-architect-review.md).

## Files

- `beacon/src/write.rs`

## Failing tests

1. `Write_UnreadableSource_LeavesTheDestination`.

## Implement

Read first, write second.

**Not in this slice:** atomic rename.

## Check

Green.

## Git

beacon only.
