# Recommended project workflow

This is the recommended workflow for any project that uses slicer. `slicer
recommended-workflow` prints this file, so it reads the same in a clone and in an
installed copy, and it does not read the project it runs in. A change to the
recommended workflow updates this file in the same change.

`slicer ai instructions` is the agent quick start and can vary with a project's
configuration. This file does not.

## Before you start

- Read the project's own instructions first, such as its `AGENTS.md` or contributor
  guide. Follow the tests and constraints they name.
- Work one slice at a time. Isolate it, review it, then integrate it.

## Tracking state

- Read and change goals, items, slices, notes, history, and roadmap prose with slicer
  commands. Do not hand-edit the tracking JSON or the generated `.slicer/render/`
  output.
- When generated render files conflict, do not merge them by hand. Run `slicer render`,
  then `slicer check`.

## Pick up one slice

1. Run `slicer sections` to see the section names the project configures, and which ones
   `next` requires.
2. Run `slicer next --ready` with those section names (`--section NAME`, repeated) to
   get the next item and only those sections.
3. Read the scope, dependencies, and checks before you start. If the specification is
   missing or ambiguous, resolve it first. A row with no slice needs `slicer promote`
   and a written specification before anyone implements it.
4. Start it: `slicer start ID --render --strict`.

## Do the work

- Implement the slice as written, and update the documentation it names.
- Run the tests the project documents, then run `slicer check`.

## Finish

Follow step 4 of `slicer ai instructions`. That is
`slicer done ID --note "Describe the verified outcome" --render` unless
`implement_finish` is `handoff`, in which case it is `slicer handoff ID --render`. Then
run `slicer check`.

When step 4 was handoff, run `done` only after review and merge are complete.

## Commits and publishing

Commit or publish only when the project's instructions or the owner ask for it. slicer
itself never commits, pushes, or tags.
