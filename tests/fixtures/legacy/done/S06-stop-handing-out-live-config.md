# S06 — stop handing out live mutable config

**Findings:** `M3` · **Size: M** · **Tree:** atlas

## Why

`Config::current()` hands out a live mutable reference. Two readers disagree
about what the run was configured with. `M3` in
[the pass-2 review](../../20260209-architect-review.md).

## Files

- `atlas/src/config.rs`

## Failing tests

1. `Config_MutatedByCaller_DoesNotAffectTheRun`.

## Implement

Hand out a snapshot.

**Not in this slice:** config file format.

## Check

Green.

## Git

atlas only.
