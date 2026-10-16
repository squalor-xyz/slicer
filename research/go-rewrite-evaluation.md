# Should slicer be rewritten in Go?

Research and review of slicer at commit `67b9b1f` (branch `research/go-rewrite-eval`,
worktree `.worktrees/go-eval`). Date: 2026-10-15.

Unless a statement is labelled **general knowledge** or cites a public URL, it comes from
a file and line in this repository or from a command run during this review.

## 1. Verdict

**Stay in Python. Do not rewrite or partially port to Go now.** Fix the measured hot
spots and the documentation drift in Python first.

Reasons:

1. **Speed is not a problem for this workload.** On the real roadmap (287 slice files,
   290 render files), read commands take 70–80 ms and `check` takes 170 ms wall time.
   Interpreter startup is about 10 ms of that. Go would save roughly 60–160 ms per call.
   A coding agent spends seconds per turn, and a person does not notice 80 ms. The
   costly cases are git subprocess fan-out, which Go would not remove, and an eagerly
   imported CLI, which lazy imports would fix in Python.
2. **A port is a large, risky rewrite.** It covers 17,463 lines of source and 30,006
   lines of tests (2,188 test methods). The tests call `main()` in-process, so none of
   them carry over to Go unchanged. The byte-for-byte invariants are the hardest part to
   port. They depend on Python's `json` formatting, on Python regex lookarounds, on
   `difflib.SequenceMatcher`, and on `unicodedata`. Go's standard library has none of
   them in the same form.
3. **Go breaks the owner's stdlib-only rule.** The TUI needs a third-party library
   (tcell or Bubble Tea). NFKD normalization and East Asian width need
   `golang.org/x/text`, which is not in the stdlib. Subcommand parsing at this scale
   needs either a dependency or a hand-written argparse replacement.
4. **The dogfooded agent workflow relies on running from source with no build step.**
   Worktree relays run `PYTHONPATH=src python3 -m slicer`. Go would add a build step
   per worktree, and every agent would have to work in a second toolchain.

Revisit Go only if the goals change toward one of these: a native Windows TUI, users who
have no Python, or latency-critical embedding such as an editor hook that runs on every
keystroke.

## 2. Current state

### Size (`wc -l src/slicer/*.py`, `tests/*.py`)

| Module | Lines | Notes |
|---|---:|---|
| `cli.py` | 4,133 | 188 top-level functions; `build_parser` alone is about 689 lines (starts `cli.py:3248`) |
| `ops.py` | 3,033 | 133 top-level functions; mixes queries (`_ranked_pool`, `next_item`, `outline_report`) with mutations |
| `tui.py` | 1,602 | curses; `draw` is about 145 lines |
| `model.py` | 860 | |
| `render.py` | 769 | |
| `store.py` | 761 | |
| `ai.py` | 537 | |
| `verify.py` | 490 | |
| `config.py` | 450 | |
| `vcs.py` | 443 | |
| `mergeindex.py` | 437 | merge driver for `index.json` |
| other 23 modules | 3,948 | |
| **total `src/slicer`** | **17,463** | |
| **`tests/*.py`** | **30,006** | 2,188 `def test_` methods across about 85 test modules |

Stdlib modules in use (count of importing modules): `pathlib` 19, `dataclasses` 19,
`re` 17, `typing` 11, `json` 9, `os` 8, `datetime` 6, then `subprocess`, `shlex`,
`unicodedata`, `difflib`, `argparse`, `hashlib`, `html`, `fnmatch`, `uuid`, and others.
`pyproject.toml` has `dependencies = []`.

### Architecture and invariants

The layering is documented in `ARCHITECTURE.md`: errors → model, config, jsonio, ids →
store → graph, vcs, github, prose, legacy, outline → ops, render, sync, verify, migrator →
check → cli and tui. The invariants in `AGENTS.md` ("What a change must not break") are:

- JSON is the state, and render output is never parsed back.
- Output is deterministic, with fixed key order and fixed `jsonio` formatting.
- The code is stdlib-only.
- Nothing project-specific is hardcoded.
- Every mutation goes through `ops.py`.
- The git allowlist is closed.
- Import and migration are all-or-nothing.
- `SlicerError.code` is the contract.

The code matches these invariants well:

- `jsonio.dumps` is a single line, `json.dumps(obj, ensure_ascii=False, indent=2) + "\n"`
  (`jsonio.py:20-21`).
- `vcs._run` enforces the allowlist plus exact or shaped argument forms
  (`vcs.py:17-80`).
- `render.write` touches only changed files (`render.py:707-718`).

### Test suite

- `tests/support.py:109-124` runs `slicer.cli.main` in-process. Only the CI step
  (`.github/workflows/ci.yml`) exercises `python -m slicer` as a process.
- `AGENTS.md` says the full suite takes "about one minute alone". I did not run it, as
  instructed.
- `tests/affected.py` (806 lines) selects tests from a recorded coverage map.
- CI runs Python 3.11–3.14 on Ubuntu, followed by `slicer check`.

### Measured performance

**Measurement conditions.** Commands were timed with `/usr/bin/time -p`, three runs
each, Python 3.12.15 on macOS (darwin 24.6), commit `67b9b1f`. These timings were taken
in the main checkout before I moved to this worktree; the code is identical. The
permission system then blocked further execution in the worktree, so I could not run
`-X importtime` or repeat the timings there.

| Command | Wall time (3 runs) |
|---|---|
| `python3.12 -c pass` | 0.01 / 0.01 / 0.01 s |
| `slicer --version` | 0.07 / 0.07 / 0.08 s |
| `slicer list --json --lean` | 0.08 / 0.07 / 0.07 s |
| `slicer status --json --lean` | 0.07 / 0.08 / 0.07 s |
| `slicer render` (nothing to write) | 0.12 / 0.12 / 0.12 s |
| `slicer check` | 0.17 / 0.17 / 0.17 s |

`--version` takes about 70 ms and does no project work, so about 60 ms is slicer's fixed
startup. Two causes are visible in the code:

- `cli.py:28-54` imports every module eagerly, including `migrator`, `legacy`,
  `mergeindex`, `mergeroles`, `github`, `refs`, `relayrun` and `roles`.
- `build_parser` runs on every process start. `cli.py:4033-4041` caches it only for
  repeated in-process calls, and its docstring estimates about 17 ms for the build.

Hot spots, from reading the code:

- **Every command loads every slice.** `store.load` parses each JSON file in `slices/`,
  `slices/done/` and `slices/retired/` (`store.py:745-756`). That is 287 files today
  (`git ls-files .slicer/slices | wc -l`), and the count grows with each finished slice.
- **`check` plans and compares about 290 render files.** It runs `render.plan` and then
  `render.compare` (`check.py:42-43`, `render.py:646-704`), which reads every generated
  file. The cost grows linearly with the roadmap but is still small.
- **`check` spawns git subprocesses** through the sibling-worktree collision check
  (`check.py:61,74-85`):
  - `is_repo` (1 spawn);
  - `resolve` (2 spawns: `vcs.py:131-136` calls `is_repo` again);
  - `store.sibling_checkouts`, which calls `at_worktree_root` (1 spawn, `show-toplevel`)
    and `sibling_worktrees` (`show-toplevel` again plus `worktree list`, 2 spawns,
    `vcs.py:340-363`, `store.py:589-614`).

  That is 6 spawns minimum. Each sibling that holds a retitled id adds about 4 more
  (`verify.py:369-387`). This repository had 4 worktrees during the review
  (`git worktree list | wc -l`).
- **Every mutating command checks for a merge twice.** `require_no_merge` runs before
  and after taking the lock (`cli.py:4098-4099`, `cli.py:4111-4112`). Each call runs
  `is_repo` plus `rev-parse MERGE_HEAD` (`vcs.py:90-96`), so 4 git spawns per write.
- **The worst case is `verify --landed`.** `_done_on_another_tip` loops over up to 1,000
  ref tips. Each tip costs one `merge-base --is-ancestor` spawn, one `show` spawn, and
  sometimes a second `show` (`verify.py:471-489`, `vcs.py:164-179`). In a repository
  with many branches or tags, this means thousands of git processes. Go would make the
  same number of calls.
- **The TUI imports curses lazily** (`tui.py:1554`, `tui_style.py:40,53,123`).
  `ARCHITECTURE.md` describes the deliberate `cli`→`tui` lazy cycle. I measured nothing
  in the TUI; it would need an interactive terminal.

## 3. What Go would and would not buy

### Single-binary distribution

**Today.** `pip install squalor-slicer` into a venv plus a symlink (`README.md`
"Install"), or pipx or uv. Python 3.11+ must be present.

**With Go.** One static binary per OS and architecture, with no interpreter needed
(general knowledge). This is the strongest argument for Go, mainly for Windows and for
users without Python. Today Windows gets no TUI, because curses is POSIX-only in CPython
(general knowledge), and gets no write lock, because `flock` is POSIX-only and the lock
becomes a no-op (`store.py:26`).

**Cheaper middle ground.** Stdlib `zipapp` builds a single `.pyz` file but still
requires an installed interpreter ("can be run on any machine with the appropriate
interpreter available", https://docs.python.org/3/library/zipapp.html). Slicer has no
dependencies and no C extensions, so `zipapp` works with no extra tooling.

### Startup and runtime speed

**General knowledge:** a Go CLI usually starts in a few milliseconds. Go could bring
`list` from about 75 ms to about 5–15 ms, and `check` from about 170 ms to perhaps
20–40 ms, depending on git spawns. Those estimates are not measured. Each git subprocess
costs the same from either language. For agent-driven use, where each tool call already
waits seconds on the model, and for interactive use under 100 ms, the gain is not
material. It would matter only for a hook that runs at a high rate.

### Typing and concurrency

- **Typing.** Go types would catch some errors at compile time. Slicer already uses
  dataclasses with explicit `to_dict`/`from_dict`, but nothing type-checks them, and
  AGENTS.md says "there is no formatter, linter or type checker in this repo". Adding
  mypy or pyright as an optional development check would capture much of Go's typing
  benefit. It would still be a tool dependency, so that choice belongs to the owner.
- **Concurrency.** Little would benefit. Writes are serialized by an advisory lock
  (`store.py:159-214`), and the work is I/O on a few hundred small files. The git
  fan-out for siblings and ref tips could run in parallel. Removing the redundant calls
  helps more.

### TUI library options in Go

| Library | License | Notes |
|---|---|---|
| tcell v3 | Apache-2.0 | Low-level, similar to curses. README: "Modern Windows is supported." (https://github.com/gdamore/tcell) |
| Bubble Tea v2 | MIT | Elm-style framework; module path `charm.land/bubbletea/v2`; usually paired with Bubbles and Lip Gloss (https://github.com/charmbracelet/bubbletea) |

Both are FLOSS. Both would break the stdlib-only invariant: the dependencies compile into
the binary, but they are still dependencies to carry and audit. `tui.py`, `tui_wizard.py`
and `tui_style.py` total 1,997 lines, with 136 tests in `test_tui.py` and
`test_tui_wizard.py` combined. All of that would be redesigned rather than translated.

### Cost of a port

**Volume.** About 17.5k lines of source plus 30k lines of tests. The tests call
`main()` in-process (`tests/support.py:116-117`), so they cannot run against a Go binary.
They would first need conversion to subprocess or golden-file tests. That conversion is
useful in its own right, and it is the prerequisite for any port.

**Deterministic JSON.** Python writes `indent=2`, `ensure_ascii=False`, item separator
`","` and key separator `": "`, and keeps insertion order
(https://docs.python.org/3/library/json.html). Go `encoding/json` (v1) differs in
several ways (https://pkg.go.dev/encoding/json):

- **HTML escaping.** By default it escapes `<`, `>`, `&`, U+2028 and U+2029 as `<`
  and similar. This is avoidable only through `Encoder.SetEscapeHTML(false)`;
  `MarshalIndent` does not offer it. **General knowledge, unverified here:** v1 escapes
  U+2028 and U+2029 even with HTML escaping off.
- **Map keys.** They are sorted. Structs keep field order, but `mergeindex.py` works on
  generic decoded dicts and relies on insertion order (`mergeindex.py:52-54,102-123`).
  A Go version needs an ordered JSON representation.
- **Nil slices.** A nil slice encodes as `null`; Python writes `[]`.
- **Invalid UTF-8.** It is replaced with U+FFFD.
- **v2 differs again.** `encoding/json/v2` marshals maps "in a non-deterministic order".

Every one of these must be matched exactly, or the first Go-written save rewrites every
file and `check` fails. Floats appear not to occur in the state; a grep of `float` in
`model.py`, `graph.py` and `config.py` found only a comment at `model.py:248`.

**Regex.** Go's RE2 syntax has no lookahead or lookbehind, and `\b` is ASCII-only
(https://pkg.go.dev/regexp/syntax). Rewrites are needed at:

- `render.py:268`: `(?<!\\)\|`
- `migrator.py:35`: `(?!https?://|#|/)`
- `refs.py:57`: `(?=[^`]*$)`
- `refs.py:267`: `(?<!\w)…(?!\w)`

Unicode-sensitive `\b` appears at `vcs.py:284` and `migrator.py:31`.

**Unicode.** `ids.py:171` uses NFKD normalization, and `tui_style.py:98-111` uses
`category`, `combining` and `east_asian_width`. Go needs `golang.org/x/text` for these,
which is not stdlib (BSD-3-Clause, https://pkg.go.dev/golang.org/x/text/unicode/norm).
Slugs would have to match exactly, or ids and filenames would change.

**Diffs.** `mergeindex.py:342` builds conflict markers from
`difflib.SequenceMatcher(..., autojunk=False).get_opcodes()`. `render.py:764-768` and
`legacy.py:267-269` use `unified_diff`. Go has no exported diff package in the stdlib
(general knowledge), so the merge driver's marker layout would need a reimplementation
that matches Python's matching heuristics.

**CLI parsing.** argparse drives about 50 commands with nested groups (`ai`, `template`,
`catalog`, `prose`, `relay-roles`, `relay-run`). Go's `flag` package has no subcommands
(general knowledge), so this means either Cobra or similar as a dependency, or a
hand-built parser. The help and usage text that agents read would change.

**Lost velocity.** The roadmap is active: recent commits file and land s303 through s306,
and several relay agents were running in parallel during this review. A port means a
feature freeze or two codebases kept in parity, possibly for months.

**Owner preferences.** `AGENTS.md` makes stdlib-only an invariant, and the global
guidance prefers stdlib and asks before any dependency. Go needs at least a TUI library
and `x/text`.

### Effect on the agent-driven, dogfooded workflow

- **Build step.** Agents and relays run `PYTHONPATH=<worktree>/src python3 -m slicer`
  (`AGENTS.md`), so they need no build. Go needs `go build` or `go run` per worktree.
  Fast builds are cached (general knowledge), but they are still a step that the relay
  briefs, skill and docs would all have to learn.
- **Foreign-worktree warning.** `vcs.foreign_worktree` (`vcs.py:409-443`) detects an
  editable install running code from another worktree. With Go binaries the mismatch
  becomes "which binary is on PATH", which needs a different detection.
- **Test selection.** `tests/affected.py` and its coverage map would be thrown away.
  `go test` caching by package is a partial substitute (general knowledge).
- **Model fluency.** Agents write both languages well (general knowledge). Unchanged
  code reviewed in a known language counts for more than that.

## 4. Alternatives short of a rewrite

Ranked by payoff per effort.

1. **Cut redundant git spawns.**
   - Memoize `is_repo` and `_current_worktree` per root for the life of the process.
     They are pure reads within one command (`vcs.py:82-87,305-316`).
   - Skip the pre-lock `require_no_merge` when the post-lock check will run anyway, or
     keep it and reuse the `is_repo` result (`cli.py:4098-4112`).
   - Bound the ref-tip loop in `verify --landed` (`verify.py:478-489`), for example by
     filtering tips with fewer calls.
   - Any new git form needs the owner's approval, because the allowlist stays closed.
2. **Lazy imports in `cli.py`.** Import `migrator`, `legacy`, `mergeindex`,
   `mergeroles`, `github`, `refs`, `relayrun` and `roles` inside the handlers that use
   them. Measure first with
   `python3.12 -X importtime -c "import slicer.cli"`, which I could not run here.
3. **Load slices lazily.** Load done and retired slices only for commands that need
   them (`render`, `check`, `show`, `find`). Most reads need the index plus open slices.
   This changes a `State` contract, so it is a design decision for the owner.
4. **Faster parser startup.** Build only the selected subparser. This is possible with
   argparse, but the change is fiddly. A lazy subparser table keyed by `argv[1]` is
   simpler.
5. **zipapp release artifact.** A single-file `slicer.pyz` built with the stdlib
   `zipapp`, needing nothing but Python 3.11+. This is small and fits stdlib-only.
6. **PyInstaller or Nuitka.** These produce a true single binary with no Python on the
   target. They are build-time FLOSS tools only (licenses to be confirmed before use).
   **General knowledge:** PyInstaller one-file binaries unpack at start, which makes
   startup slower, not faster.
7. **Port only a hot path.** No candidate qualifies. The slow parts are process
   startup and git fan-out, not CPU-bound Python. The merge driver is the most
   self-contained module but also the most byte-sensitive, so it is the worst piece to
   port alone.

## 5. Code review

Findings are ranked by severity. None is a data-loss bug.

### Medium

1. **Docs say `check` never runs git, but it does.** `ARCHITECTURE.md:266-267` says
   "`check.run` … never shells out to git, so it is safe to run anywhere". The same
   claim appears in `docs/getting-started.md:911` and `.github/workflows/ci.yml:34`.
   In the code, `check.run` calls `_sibling_collisions` (`check.py:61`), which runs
   `vcs.is_repo`, `vcs.resolve`, `store.sibling_checkouts` and possibly
   `verify.inherited_ids`, all git subprocesses (`check.py:74-85`, `verify.py:377-385`).
   The collision paragraph in `ARCHITECTURE.md` itself states that `check` uses
   `merge-base` and `show`, so the document contradicts itself. CI stays safe in
   practice because a shallow CI clone has no siblings. Still, the gate's "safe
   anywhere" property is now "safe if git behaves", and the docs should say which.
2. **The documented git allowlist does not match `vcs.ALLOWED`.**
   `ARCHITECTURE.md:195-196` lists `mv` and `ls-files`. `vcs.ALLOWED` (`vcs.py:17-20`)
   has neither, and `vcs.move` no longer calls git (`vcs.py:105-115`). The drift is in
   the safe direction, but this paragraph documents a security invariant and should be
   exact.
3. **`cli.py` and `ops.py` are too large.**
   - `cli.py` has 4,133 lines and 188 top-level functions; its 689-line `build_parser`
     is the largest unit in the codebase.
   - `ops.py` has 3,033 lines and 133 functions. It is "the single mutation path" but
     also holds ranking and reporting queries (`_ranked_pool` about 120 lines,
     `outline_report` about 123 lines).

   The size raises review cost for every relay and the chance of merge conflicts
   between parallel slices.

### Low

4. **The same condition raises two different error codes.** `ops.edit_section` raises
   "has no slice" with the default code `state` (`ops.py:1880`). `ops.edit_boundary`
   and `ops.edit_lead` raise the same message with code `no_slice` (`ops.py:2013`,
   `ops.py:2025`). The CLI pre-checks with `no_slice` (`cli.py:1977`), so only
   non-CLI callers such as the TUI see `state`. Under "the code is the contract", the
   two should agree. Many other `StateError`s rely on the default `state`. That is
   deliberate for some cases (`tests/test_errors.py:57` pins unknown status as
   `state`), but the line between `state` and a specific code is not written down.
5. **A user-input error can be reported as an internal error.**
   `Config.status_for_label` raises `ConfigError` (`config.py:201-205`), which is
   `config`, an internal code with exit 3 (`errors.py:20-26`), for an unknown label.
   Its only caller is the migrator, which catches the error with a bare
   `except Exception` (`migrator.py:253-256`). This works today, but the broad catch
   would also swallow real bugs. It should catch `SlicerError`.
6. **Writes are atomic but not durable.** `jsonio.write_bytes` uses `mkstemp` and
   `os.replace` but never calls `fsync` on the file or the directory
   (`jsonio.py:44-55`). **General knowledge:** on some filesystems a power loss can
   leave the renamed file empty. `render.write` writes each file in place without a
   temp file (`render.py:713`). `write_atomic` covers rollback on errors
   (`render.py:721-746`), but not crashes.
7. **Git probes repeat within one command** (see §2): `is_repo` runs again inside
   `resolve`, `merge_in_progress` and `subjects`, and `show-toplevel` runs twice per
   sibling scan. This is the cheapest latency win available.
8. **The startup warning catches everything.** `_warn_code_mismatch` swallows every
   exception (`cli.py:4055-4059`) and runs `store.discover` a second time. This is
   acceptable for an advisory warning, but it hides defects in `foreign_worktree`.

### Test gaps (from reading only; the suite was not run)

- No test covers process-level latency or import cost, so the eager imports in
  `cli.py:28-54` could grow unnoticed.
- `python -m slicer` runs only in CI (`AGENTS.md`). Subprocess behavior such as signals
  and exit codes through `SystemExit` is otherwise untested.
- I found no test that pins the git-spawn count for `check` or for mutating commands.
  A fake `vcs._run` counter would guard finding 7.

## 6. Suggestions and future direction

Ranked by payoff per effort. Sizes use the project's S/M/L.

| # | Suggestion | Size | Payoff |
|---|---|---|---|
| 1 | Fix the doc drift: `check` uses git (ARCHITECTURE, getting-started, ci.yml comment) and the allowlist lists `mv`/`ls-files` | S | Trust in the invariants that agents read |
| 2 | Memoize git probes per process, remove the duplicate `require_no_merge` spawns, and bound the `verify --landed` tip loop; add a spawn-count test | S–M | Faster `check`, writes and `--landed`; protects against regressions |
| 3 | Lazy-import rarely used modules in `cli.py` (and consider building only the selected subparser); measure with `-X importtime` and add a coarse startup budget test | S | Perhaps a large share of the ~60 ms fixed cost (unmeasured) |
| 4 | Make error codes consistent (`no_slice` in `edit_section`, a narrower catch in the migrator) and document when `state` is the right code | S | A cleaner agent contract |
| 5 | Split `cli.py` (parser tables and handlers per command group) and separate queries from mutations in `ops.py` | M–L | Lower review cost and fewer relay merge conflicts |
| 6 | Load done and retired slices lazily | M | Keeps commands flat as the roadmap grows; changes the `State` contract (owner decision) |
| 7 | Ship a stdlib `zipapp` artifact alongside the PyPI release | S | Single-file distribution with no new dependency |
| 8 | Add `fsync` to `jsonio` writes (file, then directory) | S | Crash durability for state |
| 9 | Optional: add a static type check (mypy or pyright) to development only, not runtime | S–M | Much of Go's typing benefit; it is a tool dependency, so the owner decides |
| 10 | Convert part of the suite into subprocess golden tests (command, state → stdout and file bytes) | M | Exercises the real process, and is the prerequisite if a port ever happens |

**Future direction.** Keep slicer a stdlib Python tool whose contract is its JSON state,
its rendered bytes and its error codes. If a Go version ever becomes worth it, for
Windows-native use or users without Python, the order should be:

1. Write the golden subprocess tests (#10) so the contract is language-neutral.
2. Pin the JSON and slug edge cases listed in §3.
3. Port the read-only commands first, behind the same tests.
4. Port the merge drivers and the TUI last.

## Verification and limits

- **Measured:** `wc -l` on `src/slicer` and `tests`; the test-method count via `grep`;
  CLI wall times (main checkout, same commit, before the move to this worktree);
  `git worktree list`; `git ls-files` counts.
- **Not run:**
  - the test suite (instructed not to);
  - `-X importtime`, slicer CLI reads such as `stats` and `goals`, and repeated timings
    in this worktree (blocked by the permission classifier);
  - anything in the TUI.
- **Roadmap goals not consulted.** I did not read the roadmap's goals or non-goals
  through the CLI, so the verdict does not weigh any recorded goal about Windows or
  distribution.
- **External facts** come from the cited pages, fetched 2026-10-15. Items marked
  "general knowledge" were not checked against a source in this session.
