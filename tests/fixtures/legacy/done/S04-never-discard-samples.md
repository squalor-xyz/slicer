# S04 — never discard a measured sample

**Follow-on from S03.** Take S03 first; this reuses its fault contract.

**Findings:** `C3` · **Size: S** · **Tree:** ledger

## Why

The decimator drops the tail of every buffer it cannot fill. Measured data is
never the thing you throw away. Standing rules: [`../review-protocol.md`](../review-protocol.md).

## Files

- `ledger/src/decimate.rs`

## Failing tests

1. `Decimate_PartialBuffer_KeepsEverySample`.

## Implement

Keep the partial buffer; report the short count.

**Not in this slice:** resampling.

## Check

Green.

## Git

ledger only.
