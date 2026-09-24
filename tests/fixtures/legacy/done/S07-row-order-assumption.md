# S07 — remove the unordered row-number assumption

**Findings:** `M4` `M5` · **Size: M** · **Tree:** atlas

## Why

Rows come back in whatever order the store felt like. The report numbers them
anyway, so two runs of one input disagree. `M4` and `M5` in
[the pass-2 review](../../20260209-architect-review.md).

## What landed

An explicit sort key on the read path, and the report numbering derived from
it rather than from arrival order.

## Files

- `atlas/src/read.rs`

## Failing tests

1. `Read_SameInputTwice_ProducesTheSameOrder`.

## Implement

Sort explicitly.

**Not in this slice:** the index rebuild.

## Check

Green twice in a row.

## Git

atlas only.
