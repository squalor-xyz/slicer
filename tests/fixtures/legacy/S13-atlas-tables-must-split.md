# S13 — atlas tables must split behind the `"data"` view

**Findings:** owner charter · **Size: M** · **Tree:** atlas

## Why

One table behind the `"data"` view has to become many, declared in config.
Owner charter change of 2026-04-18; design in [`../toolbox.md`](../toolbox.md).

## Files

- `atlas/src/view.rs`
- `atlas/src/layout.rs`

## Failing tests

1. `View_DeclaredLayout_SplitsIntoTables`.
2. `View_NoLayout_KeepsOneTable`.

## Implement

Read the layout from config; split on it.

**Not in this slice:** column edits; that is S14.

## Check

Green, and an undeclared layout still opens.

## Git

atlas only.
