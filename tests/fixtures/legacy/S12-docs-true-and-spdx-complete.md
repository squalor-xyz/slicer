# S12 — docs true, SPDX complete, the human-check ledger

**Findings:** `D1`–`D4` · **Size: M** `[OWNER]` · **Tree:** kernel

## Why

The docs claim determinism the code does not provide, and three files carry
no SPDX header. Owner call on the wording: see
[`../toolbox.md`](../toolbox.md).

## Files

- `kernel/README.md`
- `kernel/LICENSE`

## Failing tests

1. `Docs_EveryClaim_HasATest`.

## Implement

Make the docs true, or make the code true.

**Not in this slice:** the human-check ledger itself.

## Check

SPDX complete.

## Git

kernel only.
