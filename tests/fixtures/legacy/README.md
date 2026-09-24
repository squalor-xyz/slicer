Current pass is **4**: [`20260420-pass4-architect-review.md`](../../20260420-pass4-architect-review.md)
(atlas, then ledger). Passes 1–3 below are history. Parked beacon capture stays parked.

# Slice index — review pass 1 (2026-01-05)

Findings: [`20260105-architect-review.md`](../../20260105-architect-review.md).
Standing rules: [`../review-protocol.md`](../review-protocol.md). **Read that first.**

One file per slice on purpose. Read the protocol, this index, and **one** slice file.
Do not load them all.

## Baseline

kernel `1a2b3c4` · ledger `5d6e7f8` (`v0.3.0`) · atlas `9a8b7c6` · beacon `4d5e6f7` ·
**212 cases**

Confirm before you start. If it does not match, stop and ask.

| # | Slice | Size | Trees | Findings | Status |
|---|---|---|---|---|---|
| **Phase 0 — make verification possible** | | | | | |
| [S01](done/S01-one-verification-entry-point.md) | One verification entry point | M | kernel | `G1` `G2` | done |
| [S02](done/S02-restore-the-build-gate.md) | Restore the build gate | M `[OWNER]` | kernel, atlas | `G3` · ROADMAP 2 | done |
| **Phase 1 — silent wrong data** | | | | | |
| [S03](done/S03-faulted-run-must-refuse.md) | A faulted run must refuse | L | ledger | `C1` `C2` | done |
| [S04](done/S04-never-discard-samples.md) | Never discard measured samples | S | ledger | `C3` | done |

Pass 1 rows are all **done**.

---

# Slice index — review pass 2 (2026-02-09)

Findings: [`20260209-architect-review.md`](../../20260209-architect-review.md). IDs continue at **S05**.

Baseline: kernel `2b3c4d5` (the pass-2 review commit). Confirm `git log` before starting.

| # | Slice | Size | Trees | Findings | Status |
|---|---|---|---|---|---|
| **Phase 2 — store and tool correctness** | | | | | |
| [S05](done/S05-one-transaction-idiom.md) | One transaction idiom | M | ledger, atlas | `M1` `M2` | done |
| [S06](done/S06-stop-handing-out-live-config.md) | Stop handing out live config | M | atlas | `M3` | done |
| [S07](done/S07-row-order-assumption.md) | Remove the row-order assumption | M | atlas | `M4` `M5` | done |
| [S08](done/S08-stop-destroying-files.md) | Stop destroying files silently | S | beacon | `M6` | done |

Pass 2 rows are all **done**.

---

# Slice index — review pass 3 (2026-03-14)

Findings: [`20260314-architect-review.md`](../../20260314-architect-review.md). IDs continue at **S09**.
Parked beacon capture stays parked.

| # | Slice | Size | Trees | Findings | Status |
|---|---|---|---|---|---|
| **Phase 3 — tests that can fail** | | | | | |
| [S09](S09-kill-dead-assertions.md) | Kill the dead assertions | M | beacon | `T1` `T2` | parked |
| [S10](S10-one-variable-per-test.md) | One variable per differential test | M | beacon | `T3` | parked |
| [S11](S11-retire-tautologies.md) | Retire the remaining tautologies | L | all four | `T4` `T5` `T6` | later |

Not slices: the human-check ledger (still open); operator addresses; lasso.

Parked rows are **not** the next slice. Unpark only when the owner asks.

---

# Slice index — review pass 4 (2026-04-20)

Findings: [`20260420-pass4-architect-review.md`](../../20260420-pass4-architect-review.md). IDs continue at **S12**.
Owner: **atlas, then ledger.**

Pass 4 implement order is **this table**, not numeric ID.

| # | Slice | Size | Trees | Findings | Status |
|---|---|---|---|---|---|
| **Phase 4 — docs, licensing, human checks** | | | | | |
| [S12](S12-docs-true-and-spdx-complete.md) | Docs true; SPDX complete | M `[OWNER]` | kernel | `D1`–`D4` | — |
| [S13](S13-atlas-tables-must-split.md) | atlas tables must split behind the data view | M | atlas | owner charter | — |
| [S14](S14-ledger-column-edits-on-layouts.md) | ledger column edits must work on layouts | S | ledger | owner charter | — |

Not slices: goldens `[OWNER]`; lasso; host CI feed. Toolbox design: [`../toolbox.md`](../toolbox.md).

## If you only get through three

**S12, S13, S14.** Owner charter change (2026-04-18): atlas gains a config-declared
multi-table layout. Next unmarked in table order is **S13**.

## Dependencies

Most slices are independent. These are not:

- **S01 before S02.** S02's workflow calls the script S01 writes.
- **S03 before S04.** Both touch the sampling path; S03 establishes the fault
  contract the other builds on.
- **S09 before S11.** Do not retire a tautology in a file whose assertion idiom
  is still broken.

Everything else can be taken in any order. **Do not fan out across slices** — one at a
time, to green.

## Size

`S` = one file or one narrow behaviour. `M` = several files, or one tree plus its
tests. `L` = multiple trees, a public signature change, or blocked on an owner
decision — expect to split it and say so.
