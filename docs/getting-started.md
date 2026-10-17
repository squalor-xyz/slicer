# Getting started

Track one small piece of work from an idea to a checked, readable roadmap. You will
make a fictional **Greeting demo**: a Python script that prints `Hello, reader!`.
You write and check the script yourself; slicer records the plan and its progress.
No AI agent is needed. [How slicer works](how-it-works.md) explains items, slices,
and the lifecycle if those terms are new to you.

## Choose your command

You need Python 3.11 or newer. Follow [Install](../README.md#install), then run
`slicer --help` to confirm the command is available. If you are contributing to
slicer, use the [contributor install](../AGENTS.md#install) instead. Without an
install, contributors can define this shell function, replacing the absolute path
with their slicer checkout; it works from the demo directory too:

```sh
slicer() { PYTHONPATH=/absolute/path/to/slicer/src python3 -m slicer "$@"; }
```

The remaining commands assume `python3` is Python 3.11+ and `slicer` is available
through one of these choices. [Other installation options](commands.md#installation-options)
are reference material.

## Initialize the demo

Use a fresh directory so the example IDs and default section names match:

```sh
mkdir greeting-demo
cd greeting-demo
slicer init
slicer sections
slicer config implement_finish
```

`init` creates the local `.slicer/` store with an empty queue. `sections` lists
this project's specification headings and marks required ones. In this new project,
**Implement** and **Check** are required; the finish policy prints `done`.
Existing projects can configure different headings and policies: read their
instructions, such as `AGENTS.md`, and run these two discovery commands before working.
Choose any [ID scheme or configuration](configuration.md#the-ones-that-will-surprise-you)
before adding your first item.

## Add and specify the task

```sh
slicer add "Print a greeting" --size S --tree cli --importance 2 --urgency 1 --effort 1
slicer promote S01
slicer next --ready
```

`add` records the idea as row **S01**. `promote` creates its specification with
empty sections. `next --ready` will not yet offer it: the required bodies are
empty. A row alone is not an implementation plan.

Fill the sections using the names you just discovered. These are the defaults
in this fresh demo, rather than headings required by every project:

```sh
slicer edit S01 --section Why --text "Give a reader a working greeting command."
slicer edit S01 --section Files --text "Create greeting.py."
slicer edit S01 --section "Failing tests" \
  --text "Before greeting.py exists, running it must fail."
slicer edit S01 --section Implement --text "Write greeting.py to print Hello, reader!"
slicer edit S01 --section Check --text "Run greeting.py and check its exact greeting."
slicer edit S01 --section Git --text "This throwaway demo needs no commit."
slicer edit S01 --boundary \
  --text "**Not in this slice:** arguments, packaging, or other greetings."
slicer show S01
slicer next --ready
```

`show` lets you review the scope and checks. `next --ready` now offers S01 with its
specification: the required bodies are present and no dependency blocks it.
slicer checks readiness of the plan, not whether the plan is good; read it before starting.

## Start the work

```console
$ slicer start S01 --render
S01 -> started
rendered 3 file(s)
```

`start` records that S01 is in progress and claims it; `--render` updates the
readable roadmap. `slicer next` now resumes S01 ahead of open work.

Run the planned failing check yourself:

```sh
python3 greeting.py
```

Python reports that `greeting.py` does not exist and exits nonzero. Now create it:

```sh
cat > greeting.py <<'PY'
print("Hello, reader!")
PY
python3 greeting.py
test "$(python3 greeting.py)" = "Hello, reader!"
```

The script prints `Hello, reader!`. The shell `test` prints nothing and exits 0
when the greeting matches (inspect with `echo $?` immediately afterwards).
These are your application checks; slicer does not run or judge them.

## Finish and verify

For this demo the default finish, `done`, records completion after your checks pass:

```console
$ slicer done S01 --note "Greeting checked" --render
S01 -> done
rendered 2 file(s)
$ slicer check
check passed: 1 items, render and sync current
```

`done` clears the claim and moves the specification into the done folder.
`--render` refreshes the roadmap in the same step. `check` verifies roadmap integrity
and that generated output is current; it does not test `greeting.py`.

```sh
slicer show S01
slicer list --all
slicer render
slicer check
```

`show` re-displays the specification; `list --all` shows `done` in S01's STATUS
column. The explicit `render` is safe to repeat; it writes the
readable `.slicer/render/ROADMAP.md` and standalone `.slicer/render/ROADMAP.html`.
Open the HTML in a browser to view your roadmap. Manage state through slicer commands;
the generated pages are projections. In a real repository, include changed tracking
state and generated output in review according to the project's commit policy.
slicer never commits, pushes, or tags.

### Handoff alternative

In an existing project, read its instructions and run `slicer config implement_finish`.
When `implement_finish` is `handoff`, **replace the done step above** with:

```console
$ slicer handoff S01 --render
S01 -> review, unclaimed
rendered 2 file(s)
$ slicer check
check passed: 1 items, render and sync current
```

For a separate fresh run of this demo, set `slicer config implement_finish handoff`
after `init`, then follow the same steps up to this alternative. Do not run `done`
first. If project instructions require a review note, record it before handoff;
`slicer config handoff_requires_note_kind` shows any required kind.

S01 is now in review and unclaimed. `slicer list --all` shows `review` in its
STATUS column; `slicer show S01` re-displays the specification. Plain `next` does not offer it, and dependent
work stays blocked by default. The reviewer and owner follow their project's review
and integration steps; only then is `done` recorded. See
[review lifecycle](commands.md#review-lifecycle) for pickup and rejection commands.

## Continue in your own project

Use the [TUI](tui.md) with `slicer tui` if you prefer interactive editing.
[Working with agents](working-with-agents.md) explains owner decisions and relay
inspection. [AI assistance](agents.md) is optional; `slicer ai instructions` provides agent
onboarding. For a larger plan, use an [outline import](import.md), or consult the
[command reference](commands.md) as needed.

## Moved reference sections

These signposts preserve links from earlier versions of this guide.

- <a id="1-install"></a> [Installation](../README.md#install)
- <a id="install-it-no-clone"></a> [Installation options](commands.md#installation-options)
- <a id="develop-on-it-clone--editable"></a> [Contributor install](../AGENTS.md#install)
- <a id="2-set-it-up"></a> [Initialization](commands.md#initialization)
- <a id="3-configure-before-you-add-anything"></a> [Configuration](configuration.md#the-ones-that-will-surprise-you)
- <a id="worked-workflows"></a> [Agent-assisted workflows](agents.md#worked-workflows)
- <a id="a-new-project-goals-to-a-roadmap"></a> [Planning a new project](agents.md#a-new-project-goals-to-a-roadmap)
- <a id="an-existing-project-ai-review-to-a-roadmap"></a> [Reviewing an existing project](agents.md#an-existing-project-ai-review-to-a-roadmap)
- <a id="import-prioritize-and-work-through-either-example"></a> [Import and picking work](agents.md#import-prioritize-and-work-through-either-example)
- <a id="4-get-your-roadmap-in"></a> [Filing work](commands.md#adding-individual-items)
- <a id="4a-from-nothing"></a> [Adding individual items](commands.md#adding-individual-items)
- <a id="4b-in-bulk-from-a-markdown-outline"></a> [Outline import](import.md)
- <a id="4c-from-an-existing-legacy-markdown-tree"></a> [Legacy migration](migrate-format.md)
- <a id="5-turn-an-item-into-a-slice"></a> [Writing a slice](commands.md#writing-a-slice)
- <a id="6-the-loop"></a> [Lifecycle reference](commands.md#review-lifecycle)
- <a id="priority"></a> [Priority](commands.md#priority)
- <a id="relay-roles"></a> [Relay roles](commands.md#relay-roles) — the `.slicer/relay-roles.json` matrix is managed through commands.
- <a id="batch-changes"></a> [Batch changes](commands.md#batch-changes)
- <a id="7-passes-if-you-want-them"></a> [Passes and flags](commands.md#passes-and-flags)
- <a id="goals-and-non-goals"></a> [Goals and non-goals](commands.md#goals-and-non-goals)
- <a id="8-wire-it-into-ci"></a> [Continuous integration](commands.md#continuous-integration)
- <a id="9-when-something-goes-wrong"></a> [Troubleshooting](commands.md#troubleshooting)
