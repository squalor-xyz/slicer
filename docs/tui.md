# TUI manual

## Opening and ordering

In the TUI, the queue opens in the same ranked order as `slicer list`: eligible
started items, then eligible open items, then the other visible rows, with parked
items last. Within each group, declared
pass order precedes effective score, with stored queue-order ties. Empty passes
merge by score; named undeclared passes follow declared passes. Ascending reverses
readiness groups, pass direction, and score direction, keeping queue-order ties.
Done and retired stay hidden until
you clear the filter or ask for them. `o` sorts that view by ranked order, ID, title, status, size,
importance, urgency, effective score, or effort, ascending or descending. The
choice lasts for the session and does not rewrite the stored queue. `J`, `K`,
`T`, and `M` still move items in stored order, and only when no filter or
search is active.

## Editing

`tab` moves between the queue and the detail pane, `e` opens `$EDITOR` on
whatever is selected there — an item field (title, size, trees, findings, depends, importance,
urgency, effort, short title, pass, flags), a slice's lead, a slice section, its scope boundary, a note (edit it, or empty to remove; the
`+ add a note` line adds one; an edit that changes the text clears its verification), or a prose block — `s` starts the selected item and `a` adds a
new one. Press `w` for the guided roadmap wizard; an empty roadmap offers it once
when the TUI starts.

The lead is a list of paragraphs: separate them with a blank line, as `slicer edit --lead`
does, and save an empty body to clear it. Flags are a comma list that replaces the item's
flags; an empty body clears them. Pass and short title take plain text. Every edit calls
the same `ops` function the CLI does.

The detail pane shows the same fields as `slicer show ID`: the claim, the editable fields
above, the group, attempts, the item it was discovered from, and the catalog records it
cites (`C01 goal: title`, with `(retired)` after a retired decision or assumption, as the
slice header marks them). Those four lines are read-only; change them with the CLI. A slice
shows its lead before its scope boundary. Each note shows its kind and, once it has an
author, a verifier or an expiry, a line with its trust tier and that metadata.

## The wizard

The wizard collects an optional roadmap heading and each item's title, size, trees,
findings, importance, urgency, group, and dependencies (comma-separated exact titles,
including later draft items). Enter advances and Shift-Tab goes back. Each configured
section offers `e` to open `$EDITOR`, or Enter to leave its body as it is. Every item
gets a slice, even when its section bodies are empty.

After adding items, review the answers with Up/Down and Enter to revisit a field or
section, add another item, or select **Save roadmap**. Esc asks before discarding draft
answers. Nothing is saved until the final save; validation errors keep the draft for
correction. A supplied heading is prepended to existing roadmap preamble prose; a blank
heading leaves it unchanged. Saving generates the normal roadmap output without a
separate outline file. The project must already be initialized with `slicer init`.

## View controls and filters

The main TUI screen keeps common shortcuts visible below the status and feedback
lines: pane switching, editing, adding, starting/completing items, `h handoff`, search, filters,
show all, jump, movement, help, quit, and `v view`. Hints use one row when they fit or two at
80 columns, and stay visible after actions. These are fixed defaults; `?` opens
the complete shortcut list. Prompts and overlays show their own instructions.

The TUI initially hides the project's configured done status. View controls:

| Key | Action |
|---|---|
| `/` | Search IDs, full titles, and short titles as you type; Enter accepts, Esc cancels |
| `f` | Filter by status, tree, pass, importance, and urgency. Opening it shows the current view's statuses checked; applying it drops the view name and keeps the checks |
| `v` | Cycle the status preset: unfinished, then in-work, then review, then the initial unfinished set. Search and the other filters stay. The status line shows `view=in-work` or `view=review`. A disabled role is skipped. If neither role is configured, `v` does nothing and says so. `J` and `K` stay disabled while a preset is active |
| `c` | Clear search and all filters, including the default hide-done filter and any view preset |
| `g` | Jump to an ID; hidden targets are revealed by clearing search and filters |
| `h` | Hand the selected started item to review. A prompt takes an optional note; Enter with nothing typed still hands off, Esc cancels and writes nothing |
| `x` | Reject the selected item from review back to open. A prompt takes the verdict, which is required: Enter with nothing typed cancels, and Esc cancels |
| `l` | Release the selected item's claim and leave its status as it is |
| `J` / `K` | Reorder the selected item down / up |
| `T` | Move the selected item to the top |
| `M` | Move the selected item to a numbered position |
| `b` | Open the [relay board](#relay-board); `b`, `q` or Esc closes it |
| `?` | Open help; arrows or `j/k` scroll, `?` or Esc closes |

In the filter panel, arrows or `j/k` navigate, Space toggles choices, Enter applies,
and Esc cancels. Each group offers Any; tree and pass also offer `(none)`. Multiple
choices within a group match any selected value; different groups must all match.
Importance and urgency use the item's assigned values (1–3), not inherited priority.
Search is case-insensitive literal text and combines with the filters.

The status line shows matching/total item counts, the project's done percentage (the
figure `slicer stats` prints) and active restrictions. It adds `render stale` when
`.slicer/render/` differs from what `r` would write. The check runs when the TUI opens
and after each action that can write state, not on navigation; `r` clears the flag. A
render that cannot be planned or read also shows as stale. Roadmap
prose stays accessible below the items and is excluded from those counts. Filters
last only for this session. `J/K` reorder down/up, `T` moves to the top and `M` moves to
a numbered position; reordering requires clearing all restrictions with `c`. Filtering
itself preserves queue order.

An item stored as open whose sibling reports the configured review key appears in
both open and review status filters, including the review preset. The unfinished
preset also keeps it; the in-work preset does not gain it. The queue and detail pane
show this checkout's review label and review styling, and status sorting uses that
visible label. Sibling data is refreshed with the view, only at the Git worktree root.
Browsing writes no tracking state; lifecycle actions continue to use stored status.

## Relay board

`b` opens a read-only board of the slice relays in flight. It shows the runs that
[`slicer relay-run`](commands.md#relay-run-ledger) records in this checkout's ledger and in each
sibling worktree's, so an orchestrator in one worktree is visible from another. Starting,
stopping and steering a run stay outside the TUI.

Each active run is a lane, drawn as a tree from the steps the ledger records:

```
● s294  Relay board shows nested runs as a tree wi...  active  50m00s
  last heard 1m00s ago  orchestrator opus/high  wt s294-board
  implementer > reviewer > [gate]                                  round 2
  └ orchestrator opus/high  [|]  50m00s
    ├ [+] implementer  ×2  9m00s  1 subagent  Claude Code  Anthropic/sonnet high
    │     fix the mutation finding
    ├ [+] reviewer     ×2  11m00s  Claude Code  Anthropic/opus high
    │     re-review
    └ [|] gate         ×2  7m00s  Claude Code  Anthropic/sonnet medium
          gate again
          ✗ gate FAIL → implementer round 2: mutation stayed green in tui.py

● s275  Some other slice title  active  25m00s
  last heard 17m00s ago
  implementer > [reviewer] > gate                                  round 1
  [+] implementer  0s  Claude Code  Anthropic/sonnet high
        implement s275
  [|] reviewer     17m00s  Claude Code  Anthropic/opus high
        review the diff
  [ ] gate         waiting

✓ s290  Pass vanished as soon as it ended  pass  50m00s  ended 40m00s ago  impl 1 · review 1 · gate 1
```

The header names the items, the first item's title from the roadmap (cut with `...`), the run's
state and its age. The glyph is `●` for an active run and `~` for a stale one. The next line
says how long since the run last recorded an event (`last heard`), then the orchestrator's
`model/effort` and the worktree (`wt NAME`, only when its name differs from the item id). `last
heard` is dim; it turns to the warning color once the gap passes half the 30-minute stale
cutoff, and to the error color once the run is stale. The line after is the pipeline; the
running role is in brackets, an `architect` leads it when the run has one, and `round N` at the
right edge is the number of implementer steps in the lane.

A step fills a role's row when nothing spawned it or the orchestrator did, which is how a nested
relay records its roles. When the run has an orchestrator step it is the root of the tree and
the role rows hang under it with `├` and `└`; without one the rows keep the flat layout above.
A step spawned by any other role is that role's subagent: it is counted on its parent's row
(`1 subagent`), not drawn. Each row shows: `[|]` is a running step and spins (`| / - \`), `[+]`
is done, `[!]` failed, `[~]` started but not running (the run is stale or ended), and `[ ]` has
not begun. The row carries `×N` when the role has more than one step in the lane (rounds),
elapsed time, the number of subagents the latest step spawned, its agent, `vendor/model` and
reasoning, and its task underneath. Each failed gate step adds a `✗ gate FAIL → implementer
round N` line under the gate, with the gate's task as the reason. Roles outside the pipeline,
such as a split part's role, share one `other:` line after the rows.

A stale run (no event for 30 minutes) is dimmed, labelled `stale`, does not animate, and stops
counting time at its last event; only its `last heard` line stands out. A run that ended within
the last hour stays on the board after the active ones, newest first, as one dimmed summary
line: `✓` for a pass, `✗` for a fail and `~` for a stopped run, then the outcome, how long it
ran, `ended N ago` and the number of implementer, reviewer and gate steps. After an hour it
leaves the board. A line that is too narrow drops cells from the right, whole, instead of
cutting one (the title is cut with `...`, and `round N` is the first to go); a long task ends in
`...`. `j`/`k` or the arrow keys scroll when the lanes outrun the screen.

The board redraws four times a second and re-reads the ledgers every two seconds. Only the
board wakes on a timer: in every other view the TUI still blocks on input. If this checkout's
ledger is damaged (`corrupt`), the board keeps the last frame and shows one `Error:` line until
a read succeeds; delete or fix the ledger line it names. Colors follow the display rules
below. In monochrome (`NO_COLOR=1`) the cues are the glyphs (`●`, `✓`, `✗`, `~`), the markers
(`[+]`, `[!]`, `[~]`), the `[role]` brackets, the spinner, the `stale` label, and bold and dim.

## Display and color

Queue and Details headings mark the focused pane with `>`. Focused selections use
reverse/bold; inactive selections retain a marker and bold text. Queue rows show
`P:22`-style base priority scores (importance × 10 + urgency); an axis of 3 emphasizes
the score without reordering items. The detail pane retains the score and quadrant.
The status column is as wide as the longest visible label, and at least seven
characters, so `reviewing` stays aligned with `started`. Every blocked row shows
`!`, including review and parked. The detail pane shows the claim owner, or `-`
when the item has none. The queue row has a CLAIM column after the status, with the
cell `slicer list` prints: the owner, `*` for work in progress with no claim,
`wt:NAME` (`wt:NAME+N` for N more worktrees) for work in a sibling worktree, or `-`.
The queue shows at most eight cells of a claim: a longer one is cut and ends in `…`,
so `wt:other-long-name` reads `wt:othe…`. The column is as wide as the longest shown
cell, and at least the five cells of its header. The detail pane is not cut: it shows
the local owner in full. In a queue pane under 50 cells wide, a terminal narrower than
104 columns, the claim and size columns are both left out so the title keeps room; a
sibling claim then shows only as a sibling handoff's `review` status, and the
sibling-only block still lists unseen items.
The queue ends with an `Only in a sibling` block of open, unclaimed items that this
checkout does not have, one `ID  worktree  title` row each. Those rows are read-only:
`j` and `k` skip them, no key acts on them, and filters do not hide them.

When supported, cyan marks headings/started work, green marks done/success, yellow
marks parked/blocked work and high priority, magenta marks review, and red marks
errors. With no spare color pair, review is bold. Labels and `!` blocked markers
remain visible without color. Feedback uses `OK:`, `Error:`, and `Info:` prefixes;
unchanged actions and cancellations are informational. Set `NO_COLOR=1` for
monochrome; unsupported terminals also fall back automatically.
Below 80 columns or 10 rows, the TUI shows a resize prompt and preserves the session.
