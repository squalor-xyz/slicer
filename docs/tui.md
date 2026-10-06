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
urgency), a slice section, its scope boundary, a note (edit it, or empty to remove; the
`+ add a note` line adds one; an edit that changes the text clears its verification), or a prose block — `s` starts the selected item and `a` adds a
new one. Press `w` for the guided roadmap wizard; an empty roadmap offers it once
when the TUI starts.

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
| `?` | Open help; arrows or `j/k` scroll, `?` or Esc closes |

In the filter panel, arrows or `j/k` navigate, Space toggles choices, Enter applies,
and Esc cancels. Each group offers Any; tree and pass also offer `(none)`. Multiple
choices within a group match any selected value; different groups must all match.
Importance and urgency use the item's assigned values (1–3), not inherited priority.
Search is case-insensitive literal text and combines with the filters.

The status line shows matching/total item counts and active restrictions. Roadmap
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

## Display and color

Queue and Details headings mark the focused pane with `>`. Focused selections use
reverse/bold; inactive selections retain a marker and bold text. Queue rows show
`P:22`-style base priority scores (importance × 10 + urgency); an axis of 3 emphasizes
the score without reordering items. The detail pane retains the score and quadrant.
The status column is as wide as the longest visible label, and at least seven
characters, so `reviewing` stays aligned with `started`. Every blocked row shows
`!`, including review and parked. The detail pane shows the claim owner, or `-`
when the item has none. The queue row has no claim column.

When supported, cyan marks headings/started work, green marks done/success, yellow
marks parked/blocked work and high priority, magenta marks review, and red marks
errors. With no spare color pair, review is bold. Labels and `!` blocked markers
remain visible without color. Feedback uses `OK:`, `Error:`, and `Info:` prefixes;
unchanged actions and cancellations are informational. Set `NO_COLOR=1` for
monochrome; unsupported terminals also fall back automatically.
Below 80 columns or 10 rows, the TUI shows a resize prompt and preserves the session.
