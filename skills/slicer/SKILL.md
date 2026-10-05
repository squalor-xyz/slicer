---
name: slicer
description: Drive a slicer roadmap. Use when asked to review a roadmap, plan work, take a slice, implement the next slice, or drive slicer.
---

Do not open, search, parse, or edit tracking files; use the slicer commands.

1. Run `slicer next --ready --section "Implement" --section "Check" --json --lean`
   to get the next item and only those sections. The headings are examples;
   pass the section names the project configures (`slicer sections`).
   Read its scope, dependencies, and acceptance checks. Resolve missing or
   ambiguous specifications before changing its status. If the specification is
   trusted and needs no clarification, `slicer next --start --ready --section "Implement" --section "Check" --json --lean` may
   combine these first steps.
2. Otherwise, run `slicer start ID --render --strict --json` after reviewing the slice.
   Implement the agreed scope, update
   affected documentation, and run the slice's checks and required project tests.
   Edit a slice with `slicer edit ID --section NAME --text "Body" --render --strict --json`.
3. Review the changes and run `slicer check --json`. Fix problems before marking
   work complete; this tracking check does not replace code tests.
4. Run `slicer done ID --note "Describe the verified outcome" --render --json`,
   then `slicer check --json`. If `implement_finish` is `handoff`, run
   `slicer handoff ID --render --json` instead and do not run done.

If `next` JSON includes `unspecified`, those ids are not ready. Fill each missing section with `slicer edit ID --section NAME` and run `next` again. Do not invent the section body.

Use `--json` for automation and inspect both the exit code and payload. Failures
can return an `error` object containing `code`, `message`, and `command`; branch
on the stable code rather than message wording. Human diagnostics also go to
stderr. Exit 0 means success; exit 1 is a failed check or validation report whose
details must be inspected; exit 2 means a usage or validation error, or no next item;
exit 3 means an internal or state error (`corrupt`, `locked`, `io`, `config`, `schema_too_new`).
In particular, `next` can return exit 2 with `{"item": null, "blocked": [...]}`
and no error object: inspect the blocked items rather than assuming work is done.
An empty `next --batch` is exit 2 with `{"items": [], "blocked": [...]}` and no error object.

## Read and change tracking state through the CLI

Use the slicer CLI to read and to change goals, items, slices, notes, history,
and roadmap prose. This applies while reviewing a roadmap, planning work,
implementing a slice, and validating the result.

Do not open, search, parse, or edit tracking JSON, the history file, or generated roadmap and slice output to obtain or change that state.

- Goals: `slicer goals --json --lean`.
- Inventory: `slicer list --json --lean`, `slicer status --json --lean`, and `slicer stats --json --lean`.
- Slice detail: `slicer show ID --json --lean` and `slicer next --ready --json --lean`.
- Search: `slicer find topic --json --lean`.
- Roadmap prose: `slicer prose list --json --lean` and `slicer prose show goals --json --lean`.
- History: `slicer log --json --lean`. Notes on an item come back with `slicer show ID --json --lean`.
- Validation: `slicer check --json`.

Use --lean on reads; it drops empty fields and paths. Drop it only when you need the full shape.

If a CLI read or edit is missing or fails, report that and propose a roadmap item.
Do not switch to the tracking files.

Source code, ordinary project documentation, templates, and this skill stay
readable when you are implementing. A task that explicitly asks you to inspect or
repair tracking internals is the only exception.

When slicer warns that the running code belongs to another worktree, run
`PYTHONPATH=src python3 -m slicer` for this checkout.

`slicer handoff ID --render --json` records work ready for review. Mark that work done
only after review and merge are complete.

For planning, filing, claims, and review, run `slicer ai instructions --rest`; it leaves out what this skill already says.
