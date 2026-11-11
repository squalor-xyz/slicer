# Human checks to do

Checks that a person runs by hand. They never block a landing: a slice that passes its gates and checks lands, and its human checks are added here (owner decision 2026-11-11, adj 281). The owner usually tests after a release, and anything found becomes a fix slice.

Tick a box when the check is done, or delete the line. Write `-> SNNN` after a check that found a problem, naming the fix slice.

## s455 (run ork-20261111-213808-1149, 2026-11-11)

- [ ] The handoff note gives before and after timings for `--version` and `list --json --lean`, measured best of five as in Why; `--version` is under 20 ms or the note says what is left and why.
