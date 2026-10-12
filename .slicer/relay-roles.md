# Relay roles

This file is an example. The owner edits it by hand. `slicer ai relay` embeds it in
the slice relay so the orchestrator can propose each role from it, and
`slicer ai relay-roles` prints it alone. Agents read it only through those commands.

## Matrix

| Role | When | Vendor | Model | Effort |
| --- | --- | --- | --- | --- |
| Implementer | well specified, effort 1, size S or M | Claude | haiku | xhigh |
| Implementer | well specified, effort 2, size S | Claude | haiku | xhigh |
| Implementer | anything else | Claude | sonnet | high |
| Reviewer and gate | every slice | Claude | opus | medium |
| Architect | only on a size L slice | Claude | opus | high |

## Rules

- A reviewer never wrote code in the slice it reviews.
- No cross-vendor reviewer unless the owner asks.
- Claude roles run as subagents.
- "well specified" means the slice names its files, failing tests, implementation steps and checks, and needs no open owner decision.
