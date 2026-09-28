---
name: slicer
description: Drive a slicer roadmap. Use when asked to take a slice, implement the next slice, or drive slicer.
---

1. Run `slicer next --show --json` to get the next item and its slice together.
   Read its scope, dependencies, and acceptance checks. Resolve missing or
   ambiguous specifications before changing its status. If the specification is
   trusted and needs no clarification, `slicer next --start --show --json` may
   combine these first steps.
2. Otherwise, run `slicer start ID --render --json` after reviewing the slice.
   Implement the agreed scope, update
   affected documentation, and run the slice's checks and required project tests.
3. Review the changes and run `slicer check --json`. Fix problems before marking
   work complete; this tracking check does not replace code tests.
4. Run `slicer done ID --note "Describe the verified outcome" --render --json`,
   then `slicer check --json`. Report results and any remaining limitations.

If `next` JSON includes `unspecified`, those ids are not ready. Fill each missing section with `slicer edit ID --section NAME` and run `next` again. Do not invent the section body.

Use `--json` for automation and inspect both the exit code and payload. Failures
can return an `error` object containing `code`, `message`, and `command`; branch
on the stable code rather than message wording. Human diagnostics also go to
stderr. Exit 0 means success; exit 1 is a failed check or validation report whose
details must be inspected; exit 2 means a usage or validation error, or no next item;
exit 3 means an internal or state error (`corrupt`, `locked`, `io`, `config`, `schema_too_new`).
In particular, `next` can return exit 2 with `{"item": null, "blocked": [...]}`
and no error object: inspect the blocked items rather than assuming work is done.

The rest of the guide is `slicer ai instructions`.
