"""The argument parser for every command, kept apart from the entry point.

`build_parser` declares each subcommand, its flags and the handler that runs it. It is a
long table of declarations, so it has its own module: `cli.py` keeps `main`, error handling
and the cached parser, and imports `build_parser` from here. This module imports the handler
modules and `cli_common`, never `cli`, so the entry point can import it without a cycle.
"""

from __future__ import annotations

import argparse
from typing import Callable

from slicer import __url__, __version__, ai, note_pack, snapshot
from slicer.cli_ai import (
  cmd_ai_guard, cmd_ai_hooks, cmd_ai_instructions, cmd_ai_prompt, cmd_ai_relay, cmd_ai_relay_brief,
  cmd_ai_relay_orchestrator, cmd_ai_reset, cmd_ai_set, cmd_ai_show, cmd_ai_skill,
  cmd_recommended_workflow,
)
from slicer.cli_common import (
  _ArgumentParser, _DESCRIPTION, _ISSUES_URL, _check_flag, _json_flags, _mutating,
  _nonnegative_int, _positive_int, _recursive_flag, _render_flag, _rest_flag, _strict_flag,
)
from slicer.cli_prose import (
  cmd_catalog_add, cmd_catalog_cite, cmd_catalog_edit, cmd_catalog_list, cmd_catalog_move,
  cmd_catalog_retire, cmd_catalog_show, cmd_catalog_uncite, cmd_config, cmd_goals,
  cmd_prose_add_pass, cmd_prose_drop_pass, cmd_prose_edit, cmd_prose_list, cmd_prose_show,
  cmd_sections, cmd_template_list, cmd_template_reset, cmd_template_set, cmd_template_show,
)
from slicer.cli_queue import (
  cmd_deps, cmd_find, cmd_list, cmd_next, cmd_plan_parallel, cmd_show, cmd_status,
)
from slicer.cli_relay import (
  cmd_ai_relay_roles, cmd_relay_roles_add, cmd_relay_roles_init, cmd_relay_roles_move,
  cmd_relay_roles_recommend, cmd_relay_roles_remove, cmd_relay_roles_rule_add,
  cmd_relay_roles_rule_edit, cmd_relay_roles_rule_remove, cmd_relay_roles_set,
  cmd_relay_roles_show, cmd_relay_run_answer, cmd_relay_run_ask, cmd_relay_run_begin,
  cmd_relay_run_end, cmd_relay_run_list, cmd_relay_run_stats, cmd_relay_run_step,
)
from slicer.cli_report import (
  cmd_check, cmd_log, cmd_refs, cmd_render, cmd_snapshot, cmd_stats, cmd_sync, cmd_tui, cmd_verify,
)
from slicer.cli_setup import (
  cmd_init, cmd_merge_index, cmd_merge_relay_roles, cmd_setup_git,
)
from slicer.cli_track import (
  _status_cmd, cmd_add, cmd_decide, cmd_done, cmd_edit, cmd_feedback, cmd_feedback_report,
  cmd_handoff, cmd_id_prefix, cmd_import, cmd_issues_pull, cmd_land, cmd_migrate, cmd_move,
  cmd_next_id, cmd_note, cmd_note_retract, cmd_note_verify, cmd_park, cmd_promote, cmd_ready,
  cmd_reject, cmd_release, cmd_remove, cmd_set, cmd_sort, cmd_start,
)


def _make_add(
  sub: argparse._SubParsersAction, common: argparse.ArgumentParser,
) -> Callable[..., argparse.ArgumentParser]:
  """The `add` helper every section uses: a subcommand that shares `common` and, unless told otherwise, `--json`."""
  def add(name: str, fn, help_: str, *, json_flag: bool = True,
          aliases: tuple[str, ...] = ()) -> argparse.ArgumentParser:
    sp = sub.add_parser(name, help=help_, parents=[common], aliases=list(aliases))
    sp.set_defaults(func=fn)
    if json_flag:
      _json_flags(sp)
    return sp
  return add


def _add_ai(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
  """`ai` and its subcommands: the agent-facing text, skill, hooks, guard and relay."""
  sp = sub.add_parser("ai", help="agent onboarding: instructions, skill, relay, relay-roles, build-roadmap, getting-started, show, set, reset, hooks, guard, relay-orchestrator, relay-brief", parents=[common])
  # A missing subcommand is `instructions`. `--json` here is that command's
  # flag, not a second payload. The subcommand copy suppresses its default.
  _json_flags(sp)
  _rest_flag(sp)
  sp.set_defaults(func=cmd_ai_instructions)
  aisub = sp.add_subparsers(dest="ai_command", required=False)
  inner = aisub.add_parser(
    "instructions", help="print the agent quick start (no project needed)",
    description=(
      "Print the agent quick start. A project with implement_finish handoff "
      "changes step 4. No project, or a config or index that cannot be read, "
      "prints the generic text and warns on stderr. Does not lock or write."
    ),
    parents=[common],
  )
  inner.set_defaults(func=cmd_ai_instructions)
  _json_flags(inner, suppress=True)
  _rest_flag(inner, suppress=True)
  for name in ai.PROMPT_NAMES:
    help_text = (
      "print the first-session prompt for an agent (no project needed)"
      if name == "getting-started" else
      "print the prompt that builds the roadmap from the project's docs (no project needed)"
    )
    inner = aisub.add_parser(
      name, help=help_text,
      description=(
        help_text[0].upper() + help_text[1:] + ". Does not lock or write. "
        "--json returns {name, prompt, customized} in that key order."
      ),
      parents=[common],
    )
    _json_flags(inner, suppress=True)
    inner.set_defaults(func=cmd_ai_prompt, prompt_name=name)
  for name, fn in (("show", cmd_ai_show), ("set", cmd_ai_set), ("reset", cmd_ai_reset)):
    inner = aisub.add_parser(name, help=f"{name} a project prompt override", parents=[common])
    inner.set_defaults(func=fn if name == "show" else _mutating(fn))
    _json_flags(inner, suppress=True)
    inner.add_argument("name", help=", ".join(ai.PROMPT_NAMES))
    if name == "show":
      inner.add_argument("--default", action="store_true", help="print the built-in prompt")
    else:
      _check_flag(_strict_flag(_render_flag(inner)))
    if name == "set":
      inner.add_argument("--file", help="read the new text from this file, exactly")
      inner.add_argument("--stdin", action="store_true", help="read the new text from stdin")
  inner = aisub.add_parser(
    "skill", help="print or install the agent skill for Claude Code, Codex, and Grok",
    description=(
      "Print the SKILL.md that routes getting started, building a roadmap, "
      "planning and filing, implementing a slice, and a slice relay. Same project read and "
      "fallback as instructions: warns on stderr, does not lock or write. "
      "--output PATH and --install write the text it would have printed "
      "instead, and stdout lists the paths written."
    ),
    parents=[common],
  )
  inner.set_defaults(func=cmd_ai_skill)
  _json_flags(inner, suppress=True)
  inner.add_argument("--output", metavar="PATH", help="write the skill to PATH, creating parent directories")
  inner.add_argument(
    "--install", action="store_true",
    help="write it to .claude/skills/slicer/, .agents/skills/slicer/ and .grok/skills/slicer/ under the project root",
  )
  inner.add_argument(
    "--force", action="store_true", help="replace a destination that holds different text",
  )
  inner = aisub.add_parser(
    "hooks", help="print or install project-local Claude Code hooks",
    description=(
      "Print the Claude Code hooks snippet. --install merges it into the project's "
      ".claude/settings.json; stdout lists the path written. Conflicting slicer hooks "
      "are refused unless --force, which removes the other slicer handlers and keeps every "
      "other handler. Does not take a project lock. "
      "The PreToolUse guard hook needs slicer 1.18 or later on PATH: an older slicer "
      "exits 2 on the unknown subcommand, and Claude Code treats exit 2 as a block."
    ), parents=[common],
  )
  inner.set_defaults(func=cmd_ai_hooks)
  _json_flags(inner, suppress=True)
  inner.add_argument("--install", action="store_true",
                     help="merge hooks into .claude/settings.json under the project root")
  inner.add_argument("--force", action="store_true",
                     help="remove other slicer handlers from conflicting groups (needs --install)")
  inner = aisub.add_parser(
    "guard", help="judge a Claude Code PreToolUse payload from stdin",
    description=(
      "Read a PreToolUse JSON object from stdin and deny explicit tracking paths. "
      "Allows malformed input silently. Stdout is the hook protocol, not --json."
    ), parents=[common],
  )
  inner.set_defaults(func=cmd_ai_guard)
  inner = aisub.add_parser(
    "relay", help="print the slice relay, or write the example role matrix with --init-roles",
    description=(
      "Print the slice relay: an outer session spawns one orchestrator per slice, which "
      "runs the slice through an implementer, a reviewer, an architect and a gate. The text "
      "opens with the `Contract: relay vN` line; `ai relay-orchestrator` prints the "
      "orchestrator's part. A project whose config pins a different relay contract "
      "(`relay_contract`) is refused with code contract_mismatch (`--init-roles` is not). "
      "A project with implement_finish handoff "
      "closes with handoff. A project whose .slicer/relay-roles.json exists gets a "
      "## Roles matrix section after Setup holding the matrix as Markdown, and Setup "
      "proposes roles from it; without the file, Setup names `slicer relay-roles init`. "
      "A leftover .slicer/relay-roles.md is ignored with a stderr warning. No project, or "
      "a config or index that cannot be read, prints the generic text and warns on "
      "stderr. Does not lock or write, except --init-roles, which is "
      "`relay-roles init --render` under the project lock."
    ),
    parents=[common],
  )
  inner.add_argument(
    "--init-roles", action="store_true",
    help="write the example role matrix to .slicer/relay-roles.json and render it",
  )
  inner.add_argument(
    "--force", action="store_true",
    help="with --init-roles, replace an existing .slicer/relay-roles.json",
  )
  inner.set_defaults(func=cmd_ai_relay)
  _json_flags(inner, suppress=True)
  inner = aisub.add_parser(
    "relay-roles", help="print the project's relay role matrix (no lock, no write)",
    description=(
      "Print the role matrix the slice relay embeds, the same as `slicer relay-roles show`. "
      "Needs a project. Without .slicer/relay-roles.json it exits 2 with code "
      "no_relay_roles. Does not lock or write."
    ),
    parents=[common],
  )
  inner.set_defaults(func=cmd_ai_relay_roles)
  _json_flags(inner, suppress=True)
  inner = aisub.add_parser(
    "relay-orchestrator", help="print the contract of the orchestrator the slice relay spawns",
    description=(
      "Print the orchestrator's own contract for one slice and its run report shape, "
      "which `slicer ai relay` tells the outer session to put first in the orchestrator's "
      "brief. Both texts open with the same `Contract: relay vN` line. The finish step and "
      "the report kind follow the project's implement_finish and "
      "handoff_requires_note_kind, as for `ai relay`. No project, or a config or index that "
      "cannot be read, prints the generic text and warns on stderr. A project whose config "
      "pins a different relay contract (`relay_contract`) is refused with code "
      "contract_mismatch. Does not lock or write. "
      "`--session` prints the variant for a session the owner talks to that orchestrates "
      "every slice itself: it begins and ends the run, records its own step, asks the owner "
      "directly and runs Approve and Land, with no hand-back. `--json` returns "
      "{contract, pinned, orchestrator, ...}, and with `--session` also `session: true`."
    ),
    parents=[common],
  )
  inner.add_argument(
    "--session", action="store_true",
    help="print the variant for a session that orchestrates every slice itself",
  )
  inner.set_defaults(func=cmd_ai_relay_orchestrator)
  _json_flags(inner, suppress=True)

  inner = aisub.add_parser(
    "relay-brief", help="print a slice-specific orchestrator brief (no lock, no write)",
    description=(
      "Print a slice-specific orchestrator brief. A project whose config pins a different "
      "relay contract (`relay_contract`) is refused with code contract_mismatch."
    ),
    parents=[common],
  )
  inner.add_argument("id")
  for flag in ("worktree", "branch", "run", "step", "well-specified", "split"):
    inner.add_argument("--" + flag, required=True)
  inner.add_argument("--parallel", action="append", metavar="ID")
  inner.add_argument("--prefer-agent", metavar="AGENT")
  inner.add_argument("--select", action="append", metavar="ROLE=ROW_ID")
  inner.set_defaults(func=cmd_ai_relay_brief)
  _json_flags(inner, suppress=True)


def _add_setup(add) -> None:
  """`init`, `setup-git` and the two merge drivers."""
  sp = add("init", cmd_init, "create .slicer/ in a project")
  sp.add_argument("--force", action="store_true", help="overwrite an existing config and templates")
  sp.add_argument("--id-prefix", help="prefix for new ids (default S; --force retains the existing prefix)")
  sp.add_argument("--id-width", type=int, help="minimum digits in new ids (default 2; --force retains the existing width)")
  sp.add_argument("--id", help="id the first add allocates, matching the scheme, such as S21 or sp-21")
  sp.add_argument(
    "--relay-roles", action="store_true",
    help="also write the example relay roles matrix (see `relay-roles init`)",
  )

  sp = add("setup-git", cmd_setup_git,
      "print the git config for the merge drivers (once per clone; the render one is optional)")
  modes = sp.add_mutually_exclusive_group()
  modes.add_argument("--hook", action="store_true",
      help="print an optional post-merge hook and its install command; writes nothing")
  modes.add_argument("--attributes", action="store_true",
      help="also refresh .slicer/.gitattributes from the current template and add any "
           "missing .slicer/.gitignore lines")

  # Git calls this, not a person, so it stays out of the command list.
  sp = add("merge-index", cmd_merge_index, argparse.SUPPRESS, json_flag=False)
  sp.add_argument("base", help="the common ancestor's index.json (Git's %%O)")
  sp.add_argument("ours", help="this side's index.json, rewritten with the result (%%A)")
  sp.add_argument("theirs", help="the other side's index.json (%%B)")

  sp = add("merge-relay-roles", cmd_merge_relay_roles, argparse.SUPPRESS, json_flag=False)
  sp.add_argument("base", help="the common ancestor's relay-roles.json (Git's %%O)")
  sp.add_argument("ours", help="this side's relay-roles.json, rewritten with the result (%%A)")
  sp.add_argument("theirs", help="the other side's relay-roles.json (%%B)")


def _add_import(add) -> None:
  """`import` and `migrate`."""
  sp = _strict_flag(_render_flag(add("import", _mutating(cmd_import), "add items in bulk from a markdown outline")))
  sp.add_argument("file", nargs="?", help="the outline file")
  sp.add_argument("--skeleton", action="store_true", help="print a template and exit")
  sp.add_argument("--dry-run", action="store_true", help="report only; write nothing")
  sp.add_argument("--force", action="store_true", help="add even when a title already exists")
  # Caught in the handler so a script written against the old `import
  # --from DIR` gets told where that moved, rather than a bare argparse error.
  sp.add_argument("--from", dest="legacy_from", default=None, help=argparse.SUPPRESS)

  sp = _strict_flag(_render_flag(add("migrate", _mutating(cmd_migrate), "convert an existing markdown slice tree")))
  sp.add_argument("--from", dest="source", default="docs/slices", help="the legacy directory")
  sp.add_argument("--dry-run", action="store_true", help="report only; write nothing")
  sp.add_argument("--force", action="store_true", help="replace an existing roadmap")


def _add_next(add) -> None:
  """`next`, `plan-parallel`, `next-id` and `id-prefix`."""
  sp = _strict_flag(_render_flag(add("next", cmd_next, "the highest-priority startable item")))
  _recursive_flag(sp)
  sp.add_argument("-n", type=_nonnegative_int, default=None, metavar="N",
                  help="skip N currently eligible items (default 0); return one item")
  sp.add_argument("--max-attempts", type=_positive_int, default=None, metavar="N",
                  help="skip fresh implementation pickups at N attempts; allow started resumes")
  sp.add_argument("--batch", type=_positive_int, default=None, metavar="K",
                  help="return up to K items; a dependent follows its dependency")
  sp.add_argument("--disjoint", action="store_true",
                  help="with --batch, skip an item whose files_section paths overlap a chosen item's")
  sp.add_argument("--tree", help="only items in this tree (one tree; not repeatable)")
  sp.add_argument("--size", help="only items of this exact size")
  sp.add_argument("--flag", action="append", metavar="PATTERN",
                  help="include any matching flag (repeatable, case-sensitive); quote patterns: --flag 'risk-*'")
  sp.add_argument("--no-flag", action="append", metavar="PATTERN",
                  help="exclude any matching flag, overriding --flag (repeatable); quote patterns: --no-flag 'risk-*'")
  sp.add_argument("--start", action="store_true", help="mark the returned item or batch started")
  sp.add_argument("--show", action="store_true",
                  help="also include the item's full slice, as `show` returns it")
  sp.add_argument("--notes-kind", action="append", help="select exact note kinds (repeatable; requires --ready)")
  sp.add_argument("--min-trust", choices=note_pack.TRUST,
                  help="pack current notes at or above this verification tier")
  sp.add_argument("--notes-budget", type=int,
                  help="pack whole current notes within this nonnegative character budget")
  sp.add_argument("--ready", action="store_true",
                  help="bounded pickup: item identity, its slice, and blocked ids; "
                       "ready-checked items come first, within their pass; "
                       "does not filter, so an item's ready can be false")
  sp.add_argument("--section", action="append",
                  help="with --ready, return only this section (repeatable)")
  sp.add_argument("--owner", help="with --start, who to claim as (overrides SLICER_CLAIM_OWNER and claim_owner)")
  sp.add_argument("--status", help="draw from this queue instead: the review status or a custom status")
  sp.add_argument("--start-to", metavar="KEY",
                  help="with --start and a custom --status, move the claimed item to this status")
  sp.add_argument("--path", action="store_true",
                  help="also print the slice file path (text only; JSON is unchanged)")

  sp = add("plan-parallel", cmd_plan_parallel,
           "waves of items that can run in parallel, from dependencies and files_section paths")
  sp.add_argument("--width", type=_positive_int, default=4, metavar="N",
                  help="at most N items per wave (default 4)")
  sp.add_argument("--max-attempts", type=_positive_int, default=None, metavar="N",
                  help="skip fresh implementation pickups at N attempts; started work stays in flight")
  sp.add_argument("--tree", help="only items in this tree (one tree; not repeatable)")
  sp.add_argument("--size", help="only items of this exact size")
  sp.add_argument("--flag", action="append", metavar="PATTERN",
                  help="include any matching flag (repeatable, case-sensitive); quote patterns: --flag 'risk-*'")
  sp.add_argument("--no-flag", action="append", metavar="PATTERN",
                  help="exclude any matching flag, overriding --flag (repeatable); quote patterns: --no-flag 'risk-*'")

  sp = add("next-id", cmd_next_id, "the id the next add would take, without allocating it")

  sp = _strict_flag(_render_flag(add(
    "id-prefix", _mutating(cmd_id_prefix),
    "show the id prefix, or change its case for new ids",
  )))
  sp.add_argument("prefix", nargs="?", help="the new prefix; only its case may differ")
  sp.add_argument("--dry-run", action="store_true", help="report only; write nothing")


def _add_browse(add) -> None:
  """`list`, `deps`, `find`, `show`, `sections`, `config` and `recommended-workflow`."""
  sp = _recursive_flag(add("list", cmd_list, "list by readiness, pass and score; empty passes merge by score; omit done and retired unless asked"))
  sp.add_argument("--envelope", action="store_true",
                  help="with --json, wrap items and sibling-only rows in an object")
  sp.add_argument("--all", action="store_true",
                  help="include done and retired items (default: omit them)")
  sp.add_argument("--status", action="append",
                  help="filter by status (repeatable); replaces the default of omitting done and retired")
  sp.add_argument("--in-work", action="store_true",
                  help="only started items, plus reviewing when that status is set")
  sp.add_argument("--review", action="store_true",
                  help="only items in the review status")
  sp.add_argument("--held", action="store_true", help="only items carrying a configured hold flag")
  sp.add_argument("--discovered-from", help="filter by exact source item ID")
  sp.add_argument("--parent", help="only slivers of this item ID")
  sp.add_argument("--tree", action="append", help="filter by tree (repeatable)")
  sp.add_argument("--pass", dest="pass_key", help="filter by pass")
  sp.add_argument("--flag", action="append",
                  help="filter by flag (repeatable; an item matches if it has any of them)")
  sp.add_argument("--sort", choices=["score", "effort"],
                  help="score: flat priority, highest first; effort: lightest estimate first, unset last")

  sp = add("deps", cmd_deps, "dependencies: unblocked items, or one item's edges")
  sp.add_argument("id", nargs="?")
  sp.add_argument("--format", choices=["mermaid"], help="render the dependency graph")
  sp.add_argument(
    "--overlap", nargs="+", metavar="ID",
    help="print the paths each pair of these items shares in the files_section "
    "(needs at least two ids)",
  )

  sp = add("find", cmd_find, "search items by text")
  sp.add_argument("pattern", nargs="?")
  sp.add_argument(
    "--in", dest="fields",
    help="comma-separated fields to search: id,title,short_title,findings,body (default: all)",
  )
  sp.add_argument(
    "--title-exact", metavar="TITLE",
    help="match items whose full stored title equals TITLE exactly (case- and "
    "Unicode-sensitive); replaces PATTERN and --in",
  )

  sp = add("show", cmd_show, "print one slice")
  sp.add_argument("id")
  sp.add_argument(
    "--section", action="append",
    help="print only this section's body; repeat to select several",
  )
  sp.add_argument("--notes-kind", action="append", help="select exact note kinds (repeatable; empty selects untyped)")
  sp.add_argument("--min-trust", choices=note_pack.TRUST,
                  help="pack current notes at or above this verification tier")
  sp.add_argument("--notes-budget", type=int,
                  help="pack whole current notes within this nonnegative character budget")
  sp.add_argument("--context", action="store_true",
                  help="with --section, include title, dependencies, and scope boundary")

  add("sections", cmd_sections,
      "list configured section names, marking the ones next requires")

  sp = _check_flag(_strict_flag(_render_flag(add(
    "config", cmd_config,
    "print the effective config, or one value by dotted key; set or unset one key",
  ))))
  sp.add_argument("key", nargs="?", help="a top-level key or a dotted path such as id.width")
  sp.add_argument("value", nargs="?", help="the new value; JSON for a list or an object")
  sp.add_argument("--unset", action="store_true",
                  help="remove the key from config.json so its default applies again")

  add("recommended-workflow", cmd_recommended_workflow,
      "print the generic recommended project workflow")


def _add_item_writes(add) -> None:
  """`add`, `promote`, `move`, `sort`, `set`, `edit`, `refs`, `decide` and `ready`."""
  sp = _strict_flag(_render_flag(add("add", _mutating(cmd_add), "append a roadmap item")))
  sp.add_argument("title")
  sp.add_argument("--depends-on", action="append", help="dependency id (repeatable)")
  sp.add_argument("--short-title", help="short title for the roadmap row")
  sp.add_argument("--discovered-from", help="existing item ID that discovered this work")
  sp.add_argument("--parent", help="existing item ID this item is a sliver of")
  sp.add_argument("--id", help="use this id instead of the next free one")
  sp.add_argument("--print-id", action="store_true", help="print only the filed item's id")
  sp.add_argument("--key", help="case-sensitive filing identity; retries return the existing item")
  sp.add_argument("--size")
  sp.add_argument("--tree", action="append")
  sp.add_argument("--findings")
  sp.add_argument("--status")
  sp.add_argument("--pass", dest="pass_key",
                  help="file the item under this pass group (default: the previous item's pass; '' for none)")
  sp.add_argument("--importance", type=int, help="1-3; how important (default 2)")
  sp.add_argument("--urgency", type=int, help="1-3; how urgent (default 2)")
  sp.add_argument("--effort", type=int, help="1-3; optional estimate, omit to leave unset")

  sp = _strict_flag(_render_flag(add("promote", _mutating(cmd_promote), "give an item a slice file")))
  sp.add_argument("id")
  sp.add_argument("--force", action="store_true", help="overwrite an existing slice")
  sp.add_argument("--file", help="a one-item outline whose sections fill the slice")
  sp.add_argument("--stdin", action="store_true", help="read that outline from stdin")
  sp.add_argument("--boundary", help="full scope-boundary paragraph; overrides source/default; empty clears")

  sp = _strict_flag(_render_flag(add("move", _mutating(cmd_move), "reorder the queue")))
  sp.add_argument("id")
  sp.add_argument("--before")
  sp.add_argument("--after")
  sp.add_argument("--to", type=int)

  sp = _strict_flag(_render_flag(add("sort", _mutating(cmd_sort), "reorder the whole queue by priority score")))
  sp.add_argument("--by", choices=["score", "effort"], default="score",
                  help="score (default) or effort: lightest estimate first, unset last")

  sp = _strict_flag(_render_flag(add("set", _mutating(cmd_set), "change an item's fields")))
  sp.add_argument("id", nargs="+", help="item ids, or - alone to read whitespace-separated ids from stdin")
  sp.add_argument("--title", help="also updates a short title that still matches the title")
  sp.add_argument("--short-title", dest="short_title")
  sp.add_argument("--status")
  sp.add_argument("--size")
  sp.add_argument("--tree", action="append")
  sp.add_argument("--findings")
  sp.add_argument("--depends-on", dest="depends_on", action="append",
                  help="dependency id (repeatable; replaces the list)")
  sp.add_argument("--pass", dest="pass_key", help="move the item to this pass group")
  sp.add_argument("--flag", dest="flag", action="append", help="set a flag (repeatable; replaces the list)")
  sp.add_argument("--add-flag", action="append", help="add a flag without replacing the list (repeatable)")
  sp.add_argument("--remove-flag", action="append", help="remove a flag without replacing the list (repeatable)")
  sp.add_argument("--no-flags", dest="no_flags", action="store_true", help="clear all flags")
  sp.add_argument("--group", help="the phase-label group; --group '' clears it")
  sp.add_argument("--parent", help="existing item ID this item is a sliver of; --parent '' clears it")
  sp.add_argument("--discovered-from", help="existing item ID that discovered this work; --discovered-from '' clears it")
  sp.add_argument("--importance", type=int, help="1-3")
  sp.add_argument("--urgency", type=int, help="1-3")
  sp.add_argument("--effort", type=int, help="1-3; optional estimate")
  sp.add_argument("--no-effort", action="store_true", help="clear the effort estimate")
  sp.add_argument("--attempts", type=int, help="implementation attempt count; an integer >= 0")

  sp = _strict_flag(_render_flag(add("edit", _mutating(cmd_edit), "edit a slice section, scope boundary or lead")))
  sp.add_argument("id")
  sp.add_argument("--section", help="section to edit; cannot combine with --boundary or --lead")
  sp.add_argument("--boundary", action="store_true", help="replace the full scope-boundary paragraph; cannot combine with --section or --lead")
  sp.add_argument("--lead", action="store_true", help="replace the lead paragraphs (blank-line separated; empty clears); cannot combine with --section, --boundary, or --append")
  sp.add_argument("--append", action="store_true", help="append with a blank line; requires --text, --file, or --stdin; empty input leaves the body unchanged")
  sp.add_argument("--text", help="inline body (exact in replacement mode); empty text clears unless appending; cannot combine with --file/--stdin")
  sp.add_argument("--file")
  sp.add_argument("--stdin", action="store_true")

  sp = _strict_flag(_render_flag(add("refs", cmd_refs,
    "check file:line references in slice text; --fix rewrites the moved ones")))
  sp.add_argument("id", nargs="*", help="item ids to scan, whatever their status (default: every open slice)")
  sp.add_argument("--fix", action="store_true", help="rewrite moved references in the lead, boundary and sections; notes are only reported")

  sp = _strict_flag(_render_flag(add("decide", _mutating(cmd_decide), "record an owner decision and clear hold flags")))
  sp.add_argument("id")
  sp.add_argument("--text", help="inline answer; cannot combine with --file/--stdin")
  sp.add_argument("--file", help="read the answer from this file; cannot combine with --text/--stdin")
  sp.add_argument("--stdin", action="store_true", help="read the answer from stdin; cannot combine with --text/--file")

  sp = add("ready", _mutating(cmd_ready), "record that a slice's spec check passed")
  sp.add_argument("id")
  sp.add_argument("--by", required=True, help="who ran the spec check, one line")
  sp.add_argument("--note", help="one line kept with the verdict")


def _add_notes_and_feedback(add) -> None:
  """`note`, `feedback`, `feedback-report`, `issues-pull` and the note verdict commands."""
  sp = _strict_flag(_render_flag(add("note", _mutating(cmd_note), "append a dated note to an item")))
  sp.add_argument("id")
  sp.add_argument("--kind", help="optional note kind, validated against note_kinds")
  sp.add_argument("--owner", help="note author; uses the claim actor precedence")
  sp.add_argument("--stale-after", default="", help="timezone-aware ISO8601 expiry; normalized to UTC")
  sp.add_argument("--text", help="inline note; cannot combine with --file/--stdin")
  sp.add_argument("--file")
  sp.add_argument("--stdin", action="store_true")

  sp = add("feedback", cmd_feedback, "keep a local log of frictions, bugs, and feature ideas")
  sp.add_argument("--kind", help="friction, bug, or feature; appends an entry")
  sp.add_argument("--item", help="optional item id, stored as written and not looked up")
  sp.add_argument("--text", help="inline body; cannot combine with --file/--stdin")
  sp.add_argument("--file")
  sp.add_argument("--stdin", action="store_true")
  sp.add_argument("--out", help="copy the log's exact bytes to PATH instead of appending")
  sp.add_argument("--force", action="store_true", help="with --out, replace a different existing file")

  sp = add("feedback-report", cmd_feedback_report,
    "file bug and feature entries from the feedback log as GitHub issues with gh")
  sp.add_argument("--repo", help="OWNER/REPO to file to; defaults to config issues_repo")
  sp.add_argument("--kind", action="append",
    help="repeatable; friction, bug, or feature (default: bug and feature)")
  sp.add_argument("--item", help="only entries stored with exactly this item string")
  sp.add_argument("--dry-run", action="store_true", help="print the issues and call nothing")
  sp.add_argument("--yes", action="store_true", help="file the issues with `gh issue create`")

  sp = _strict_flag(_render_flag(add("issues-pull", cmd_issues_pull,
    "file a repository's open GitHub issues as unscored roadmap rows with gh")))
  sp.add_argument("--repo", help="OWNER/REPO to read; defaults to config issues_repo")
  sp.add_argument("--limit", type=int, default=30, help="most issues to read, 1-100 (default 30)")
  sp.add_argument("--dry-run", action="store_true", help="list the issues and write nothing")

  for command in ("note-verify", "note-attest"):
    sp = _strict_flag(_render_flag(add(command, _mutating(cmd_note_verify),
      "record note verification (workflow attestation, not caller authentication)")))
    sp.add_argument("id")
    sp.add_argument("--note-id", required=True, help="stable item-note ID from show --json")
    if command == "note-attest":
      sp.add_argument("--owner", required=True, help="owner name; stored as human:NAME")
    else:
      sp.add_argument("--owner", help="verifying actor; uses the claim actor precedence; no human: prefix")

  sp = _strict_flag(_render_flag(add("note-retract", _mutating(cmd_note_retract),
    "retract an item note as wrong; its text and who, when and why stay on the record")))
  sp.add_argument("id")
  sp.add_argument("--note-id", required=True, help="stable item-note ID from show --json")
  sp.add_argument("--reason", required=True, help="one line saying why the note is wrong")
  sp.add_argument("--owner", help="retracting actor; uses the claim actor precedence; no human: prefix")


def _add_lifecycle(add) -> None:
  """`done`, `land`, `start`, `release`, `handoff`, `reject`, `park` and `unpark`."""
  sp = _check_flag(_render_flag(add("done", cmd_done, "mark an item finished")))
  sp.add_argument("id", nargs="+", help="item ids, or - alone to read whitespace-separated ids from stdin")
  sp.add_argument("--note", help="one line recorded in history (see `slicer note` for a durable note on the item)")
  sp.add_argument("--owner", help="who to record (overrides SLICER_CLAIM_OWNER and claim_owner)")

  sp = _render_flag(
    add("land", cmd_land, "mark a merged slice done and print the git commands left"),
    help="accepted for symmetry with done; land always renders and checks",
  )
  sp.add_argument("id", help="the item to land")
  sp.add_argument("--branch", required=True, help="the slice's branch; it must be merged into HEAD")
  sp.add_argument("--note", required=True, help="one line recorded in history, as for `done`")
  sp.add_argument("--owner", help="who to record (overrides SLICER_CLAIM_OWNER and claim_owner)")

  sp = _strict_flag(_render_flag(add("start", _mutating(cmd_start), "mark an item in progress and claim it")))
  sp.add_argument("id", nargs="+", help="item ids, or - alone to read whitespace-separated ids from stdin")
  sp.add_argument("--note", help="one line recorded in history (see `slicer note` for a durable note on the item)")
  sp.add_argument("--owner", help="who to record (overrides SLICER_CLAIM_OWNER and claim_owner)")

  sp = _strict_flag(_render_flag(add("release", _mutating(cmd_release), "clear a claim without changing status")))
  sp.add_argument("id", nargs="+", help="item ids, or - alone to read whitespace-separated ids from stdin")
  sp.add_argument("--owner", help="who to record (overrides SLICER_CLAIM_OWNER and claim_owner)")

  sp = _check_flag(_strict_flag(_render_flag(add("handoff", _mutating(cmd_handoff), "hand a started slice to review and clear its claim"))))
  sp.add_argument("id", nargs="+", help="item ids, or - alone to read whitespace-separated ids from stdin")
  sp.add_argument("--note", help="one line recorded in history (see `slicer note` for a durable note on the item)")
  sp.add_argument("--owner", help="who to record (overrides SLICER_CLAIM_OWNER and claim_owner)")

  sp = _strict_flag(_render_flag(add("reject", _mutating(cmd_reject), "send a review back with a verdict and clear its claim")))
  sp.add_argument("id", nargs="+", help="item ids, or - alone to read whitespace-separated ids from stdin")
  sp.add_argument("--note", required=True, help="the verdict: added to the item's notes and its history entry")
  sp.add_argument("--to", help="the status to send it back to (default: the open status)")
  sp.add_argument("--owner", help="who to record (overrides SLICER_CLAIM_OWNER and claim_owner)")

  sp = _strict_flag(_render_flag(add("park", _mutating(cmd_park), "set an item aside")))
  sp.add_argument("id", nargs="+", help="item ids, or - alone to read whitespace-separated ids from stdin")
  sp.add_argument("--note", help="one line recorded in history (see `slicer note` for a durable note on the item)")

  sp = _strict_flag(_render_flag(add("unpark", _mutating(_status_cmd("open_status")), "return a parked item to the queue")))
  sp.add_argument("id", nargs="+", help="item ids, or - alone to read whitespace-separated ids from stdin")
  sp.add_argument("--note", help="one line recorded in history (see `slicer note` for a durable note on the item)")


def _add_prose_and_template(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
  """`prose` and `template`."""
  sp = sub.add_parser("prose", help="read and edit the roadmap's own prose", parents=[common])
  psub = sp.add_subparsers(dest="prose_command", required=True)

  def padd(name: str, fn, help_: str) -> argparse.ArgumentParser:
    inner = psub.add_parser(name, help=help_, parents=[common])
    inner.set_defaults(func=fn)
    _json_flags(inner)
    return inner

  padd("list", cmd_prose_list, "every addressable block, in declaration order")
  padd("show", cmd_prose_show, "print one block").add_argument("ref")

  inner = _strict_flag(_render_flag(padd("edit", _mutating(cmd_prose_edit), "replace one block")))
  inner.add_argument("ref")
  inner.add_argument("--text", help="inline body, preserved exactly; empty text clears it; cannot combine with --file/--stdin")
  inner.add_argument("--file")
  inner.add_argument("--stdin", action="store_true")

  inner = _strict_flag(_render_flag(padd("add-pass", _mutating(cmd_prose_add_pass), "declare a new pass group")))
  inner.add_argument("key")
  inner.add_argument("--heading", help="the markdown heading for the group")
  inner.add_argument("--after", help="insert after this pass instead of at the end")

  _strict_flag(_render_flag(padd("drop-pass", _mutating(cmd_prose_drop_pass), "remove an empty pass group"))).add_argument("key")

  sp = sub.add_parser("template", help="show, replace and reset the render templates", parents=[common])
  tsub = sp.add_subparsers(dest="template_command", required=True)

  def tadd(name: str, fn, help_: str, *, write: bool = False) -> argparse.ArgumentParser:
    inner = tsub.add_parser(name, help=help_, parents=[common])
    inner.set_defaults(func=_mutating(fn) if write else fn)
    _json_flags(inner)
    if write:
      _check_flag(_strict_flag(_render_flag(inner)))
    return inner

  tadd("list", cmd_template_list, "name each template and say whether it differs from the default")
  inner = tadd("show", cmd_template_show, "print a template byte for byte")
  inner.add_argument("name", help="slice.md, roadmap.md, row.md or relay-brief.md")
  inner.add_argument("--default", action="store_true", help="print the built-in template, not the project's")
  inner = tadd("set", cmd_template_set, "replace a template with new text", write=True)
  inner.add_argument("name", help="slice.md, roadmap.md, row.md or relay-brief.md")
  inner.add_argument("--file", help="read the new text from this file, exactly")
  inner.add_argument("--stdin", action="store_true", help="read the new text from stdin")
  tadd("reset", cmd_template_reset, "restore a template to the built-in text",
       write=True).add_argument("name", help="slice.md, roadmap.md, row.md or relay-brief.md")


def _add_remove(add) -> None:
  """`remove` and `retire`."""
  sp = _strict_flag(_render_flag(add("remove", _mutating(cmd_remove), "retire an obsolete item, or purge one outright")))
  sp.add_argument("id")
  mode = sp.add_mutually_exclusive_group(required=True)
  mode.add_argument("--reason", help="retire it, recording why; the id stays claimed")
  mode.add_argument(
    "--purge", action="store_true", help="delete it outright, for something that never should have existed"
  )
  sp.add_argument("--force", action="store_true", help="override the dependents and done guards")
  sp.add_argument("--dry-run", action="store_true", help="preview the removal and its fallout; write nothing")

  # `retire` is `remove --reason` under the name of the status it sets. Its parser has no
  # --purge, and the flag's default keeps cmd_remove on the retire path.
  sp = _strict_flag(_render_flag(add("retire", _mutating(cmd_remove), "retire an obsolete item, as `remove --reason` does")))
  sp.add_argument("id")
  sp.add_argument("--reason", required=True, help="retire it, recording why; the id stays claimed")
  sp.add_argument("--force", action="store_true", help="override the dependents and done guards")
  sp.add_argument("--dry-run", action="store_true", help="preview the retirement and its fallout; write nothing")
  sp.set_defaults(purge=False)


def _add_checks(add) -> None:
  """`render`, `sync`, `verify`, `check` and `goals`."""
  add("render", cmd_render, "regenerate .slicer/render/")

  sp = add("sync", cmd_sync, "rewrite derived lines in other documents")
  sp.add_argument("--check", action="store_true", help="report drift instead of writing")

  sp = add("verify", cmd_verify, "check the index for consistency (and against git unless git_check is off)")
  sp.add_argument(
    "--landed", metavar="ID",
    help="instead, check that ID's done state is committed in REF's history; exit 1 when it is not",
  )
  sp.add_argument("--on", metavar="REF", help="with --landed, the branch, tag or commit to check (default HEAD)")

  sp = _recursive_flag(add("check", cmd_check, "the CI gate: render, sync and integrity"))
  sp.add_argument("--diff", action="store_true", help="show a diff for each stale file")

  sp = add("goals", cmd_goals, "show project goals and non-goals, and their records")
  sp.add_argument("--retired", action="store_true", help="include retired goal and non-goal records")


def _add_catalog(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
  """`catalog` and its subcommands."""
  sp = sub.add_parser("catalog", help="project-knowledge records", parents=[common])
  csub = sp.add_subparsers(dest="catalog_command", required=True)

  def cadd(name: str, fn, help_: str, *, mutate: bool = False) -> argparse.ArgumentParser:
    inner = csub.add_parser(name, help=help_, parents=[common])
    inner.set_defaults(func=_mutating(fn) if mutate else fn)
    _json_flags(inner)
    if mutate:
      return _strict_flag(_render_flag(inner))
    return inner

  inner = cadd("add", cmd_catalog_add, "add one record", mutate=True)
  inner.add_argument("--kind", required=True, help="goal, non-goal, requirement, constraint, decision, or assumption")
  inner.add_argument("title")
  inner.add_argument("--body", help="record body; cannot combine with --file or --stdin")
  inner.add_argument("--file")
  inner.add_argument("--stdin", action="store_true")

  inner = cadd("list", cmd_catalog_list, "list records")
  inner.add_argument("--kind", help="limit to one kind; non-goal and non_goal are the same")
  inner.add_argument("--retired", action="store_true", help="append retired records after the active ones")

  cadd("show", cmd_catalog_show, "print one record").add_argument("id")

  inner = cadd("edit", cmd_catalog_edit, "edit an active record's title or body", mutate=True)
  inner.add_argument("id")
  inner.add_argument("--title", help="replacement title")
  inner.add_argument("--body", help="replacement body; cannot combine with --file or --stdin")
  inner.add_argument("--file")
  inner.add_argument("--stdin", action="store_true")

  inner = cadd("retire", cmd_catalog_retire, "retire a record and keep its id", mutate=True)
  inner.add_argument("id")
  inner.add_argument("--reason", required=True, help="why it is no longer in force")
  inner.add_argument("--successor", help="a later record of the same kind")

  inner = cadd("move", cmd_catalog_move, "reorder a record within its kind", mutate=True)
  inner.add_argument("id")
  place = inner.add_mutually_exclusive_group(required=True)
  place.add_argument("--before", metavar="ID")
  place.add_argument("--after", metavar="ID")

  inner = cadd("cite", cmd_catalog_cite, "cite catalog records from a roadmap item", mutate=True)
  inner.add_argument("item")
  inner.add_argument("ids", nargs="+", metavar="ID")

  inner = cadd("uncite", cmd_catalog_uncite, "drop catalog citations from a roadmap item", mutate=True)
  inner.add_argument("item")
  inner.add_argument("ids", nargs="+", metavar="ID")


def _add_relay_roles(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
  """`relay-roles` and its subcommands."""
  sp = sub.add_parser(
    "relay-roles", help="the slice-relay role matrix: rows, conditions and rules",
    description=(
      "The slice-relay role matrix lives in .slicer/relay-roles.json and changes only "
      "through these commands. Each row names a role, the slice conditions it applies to, "
      "and the agent, vendor, model and reasoning effort that run it. slicer renders the "
      "matrix to .slicer/render/RELAY-ROLES.md."
    ),
    parents=[common],
  )
  rsub = sp.add_subparsers(dest="relay_roles_command", required=True)

  def radd(name: str, fn, help_: str, *, mutate: bool = False) -> argparse.ArgumentParser:
    inner = rsub.add_parser(name, help=help_, parents=[common])
    inner.set_defaults(func=_mutating(fn) if mutate else fn)
    _json_flags(inner)
    if mutate:
      return _strict_flag(_render_flag(inner))
    return inner

  def row_flags(inner: argparse.ArgumentParser) -> None:
    inner.add_argument("--size", help="sizes the row applies to, comma-separated such as S,M (any text); empty or any for every size")
    inner.add_argument("--effort", help="slice effort scores 1-3, comma-separated; empty for any")
    inner.add_argument("--importance", help="slice importance scores 1-3, comma-separated; empty for any")
    inner.add_argument("--urgency", help="slice urgency scores 1-3, comma-separated; empty for any")
    inner.add_argument("--well-specified", help="yes, no or any")
    inner.add_argument("--split", help="yes for a part of a split slice, no for a whole one, or any")
    inner.add_argument("--sliver", help="yes for a sliver (an item with a parent), no for any other item, or any")
    inner.add_argument("--agent", help="the harness that runs the role, such as Claude Code")
    inner.add_argument("--vendor", help="the model vendor")
    inner.add_argument("--model", help="the model")
    inner.add_argument("--reasoning", help="reasoning effort such as low, medium, high or xhigh")
    inner.add_argument("--note", help="one line shown in the Note column")

  radd("show", cmd_relay_roles_show, "print the matrix as Markdown (no lock, no write)")

  inner = radd(
    "recommend", cmd_relay_roles_recommend,
    "propose each role's row, and the other rows that match, for a slice from its size, "
    "effort, importance and urgency (no lock, no write)",
  )
  inner.add_argument("id")
  inner.add_argument(
    "--well-specified", required=True, metavar="yes|no",
    help="your judgement of whether the slice is well specified; rows with well_specified null match either",
  )
  inner.add_argument(
    "--split", required=True, metavar="yes|no",
    help="yes for a part of a split slice, no for a whole one; rows with split null match either",
  )
  inner.add_argument(
    "--prefer-agent", metavar="AGENT",
    help="choose each role's first matching row on this agent (compared trimmed, ignoring case) "
    "when the role has one; a role without one keeps its first matching row",
  )
  inner.add_argument(
    "--select", action="append", metavar="ROLE=ROW_ID",
    help="use this row for the role, over the preference and the matrix order; repeat for "
    "other roles. The row must be a row of that role that matches the slice",
  )

  inner = radd("init", cmd_relay_roles_init, "write the example matrix", mutate=True)
  inner.add_argument("--force", action="store_true", help="replace an existing relay-roles.json")
  inner.add_argument(
    "--replace-legacy", action="store_true",
    help="also remove a leftover relay-roles.md once the JSON is written",
  )

  inner = radd("add", cmd_relay_roles_add, "add a row", mutate=True)
  inner.add_argument("--role", required=True, help="implementer, reviewer, gate, architect or orchestrator")
  row_flags(inner)

  inner = radd("set", cmd_relay_roles_set, "change only the fields given on a row", mutate=True)
  inner.add_argument("id")
  inner.add_argument("--role", help="implementer, reviewer, gate, architect or orchestrator")
  row_flags(inner)

  radd("remove", cmd_relay_roles_remove, "drop a row", mutate=True).add_argument("id")

  inner = radd("move", cmd_relay_roles_move, "reorder a row", mutate=True)
  inner.add_argument("id")
  inner.add_argument("--before", metavar="ID", help="place the row before this row")
  inner.add_argument("--last", action="store_true", help="place the row at the end")

  radd("rule-add", cmd_relay_roles_rule_add, "add a rule", mutate=True).add_argument("text")

  inner = radd("rule-edit", cmd_relay_roles_rule_edit, "replace a rule's text", mutate=True)
  inner.add_argument("id")
  inner.add_argument("text")

  radd("rule-remove", cmd_relay_roles_rule_remove, "drop a rule", mutate=True).add_argument("id")


def _add_relay_run(sub: argparse._SubParsersAction, common: argparse.ArgumentParser) -> None:
  """`relay-run` and its subcommands."""
  sp = sub.add_parser(
    "relay-run", help="the local ledger of slice-relay runs and their agent steps",
    description=(
      "A relay orchestrator records each run and each role step here so `relay-run list` "
      "and the TUI can show what is running. In a git repo the ledger is "
      "<git common dir>/slicer/relay-runs.jsonl, shared by every worktree and kept when one "
      "is removed; elsewhere it is .slicer/relay-runs.jsonl. Append-only and disposable: "
      "never rendered, never read by check."
    ),
    parents=[common],
  )
  usub = sp.add_subparsers(dest="relay_run_command", required=True)

  def uadd(name: str, fn, help_: str) -> argparse.ArgumentParser:
    inner = usub.add_parser(name, help=help_, parents=[common])
    inner.set_defaults(func=fn)
    _json_flags(inner)
    return inner

  inner = uadd("begin", cmd_relay_run_begin, "start a run on one or more items and print its id")
  inner.add_argument("items", nargs="+", metavar="ID", help="the roadmap items the run relays")
  inner.add_argument(
    "--worktree", metavar="PATH",
    help="the worktree the work happens in, recorded by its directory name "
    "(default: this checkout)",
  )

  inner = uadd(
    "step", cmd_relay_run_step,
    "record an agent's step, or with --step a later phase of one; prints the step id",
  )
  inner.add_argument("run")
  inner.add_argument(
    "--step", metavar="STEP",
    help="update this step of the run instead of adding one; only the fields given change",
  )
  inner.add_argument(
    "--role", help="implementer, reviewer, gate, architect, orchestrator, ui or spec-check; "
    "ui and spec-check have no matrix row",
  )
  inner.add_argument("--agent", help="the harness that runs the role, such as Claude Code")
  inner.add_argument("--vendor", help="the model vendor")
  inner.add_argument("--model", help="the model")
  inner.add_argument("--reasoning", help="reasoning effort such as low, medium, high or xhigh")
  inner.add_argument(
    "--phase", required=True, metavar="start|done|fail|wait",
    help="where the step stands; wait says it waits on the owner, so its run is never stale "
    "until a later start, done or fail of the step",
  )
  inner.add_argument(
    "--waiting-on", metavar="owner|approval",
    help="with --phase wait, what the step waits on (default owner); refused with another phase",
  )
  inner.add_argument(
    "--reason", metavar="TEXT",
    help="with --phase fail, why the step failed, such as a rate limit or out of credits; "
    "refused with another phase",
  )
  inner.add_argument(
    "--verdict", metavar="pass|fail",
    help="with --phase done on a gate step, the verdict that closes the gate round; "
    "refused on any other step or phase",
  )
  inner.add_argument("--task", help="one line on what the step is doing")
  inner.add_argument("--parent", metavar="STEP", help="the step that spawned this subagent")

  inner = uadd(
    "ask", cmd_relay_run_ask,
    "ask the owner a question on a step; with --blocker the step waits on the owner; prints the question id",
  )
  inner.add_argument("run")
  inner.add_argument("--step", required=True, metavar="STEP", help="the step that asks")
  inner.add_argument("--title", required=True, help="the question, on one line")
  inner.add_argument(
    "--option", action="append", metavar="LABEL:SUMMARY",
    help="an answer the owner may pick; give 2 to 4, with distinct labels; no `|` or `;`",
  )
  inner.add_argument("--recommend", metavar="LABEL", help="the option you recommend")
  inner.add_argument(
    "--blocker", action="store_true",
    help="the slice cannot go on without it: the step waits on the owner until it is answered; "
    "without it the question is recorded and the step keeps running",
  )
  inner.add_argument("--key", metavar="AREA.TOPIC", help="a key the owner's answers can be filed under")
  inner.add_argument("--reasoning", metavar="TEXT", help="why; may hold a `|`")

  inner = uadd(
    "answer", cmd_relay_run_answer,
    "record the owner's answer to a question, or with --get read it",
  )
  inner.add_argument("run")
  inner.add_argument("question", metavar="QUESTION_ID")
  inner.add_argument("--option", metavar="LABEL", help="the option the owner picked")
  inner.add_argument("--text", help="a free-text answer, in place of an option")
  inner.add_argument("--why", help="the owner's reason")
  inner.add_argument("--by", metavar="NAME", help="who answered")
  inner.add_argument(
    "--get", action="store_true",
    help="read the question and its answer instead; no lock, no write, and no other flag",
  )

  inner = uadd("end", cmd_relay_run_end, "close a run with its outcome")
  inner.add_argument("run")
  inner.add_argument(
    "--outcome", required=True, metavar="pass|fail|stopped",
    help="how the run ended",
  )
  inner.add_argument(
    "--close-steps", metavar="done|fail",
    help="first mark every step still running with this phase",
  )
  inner.add_argument(
    "--reason", metavar="TEXT",
    help="with --close-steps fail, why each step it closes failed; refused with another "
    "--close-steps or none",
  )

  inner = uadd(
    "list", cmd_relay_run_list,
    "runs in this checkout and its sibling worktrees (no lock, no write)",
  )
  inner.add_argument("run", nargs="?", metavar="RUN", help="show only this run")
  inner.add_argument(
    "--active", action="store_true",
    help="only runs that have not ended and have had an event in the last 30 minutes "
    "or have a step waiting",
  )

  inner = uadd(
    "stats", cmd_relay_run_stats,
    "fix rounds, gate FAILs, role times and owner wait of ended runs (no lock, no write)",
  )
  inner.add_argument(
    "--since", metavar="DATE",
    help="only runs begun at or after this ISO8601 timestamp with a timezone, or a bare "
    "YYYY-MM-DD, which means 00:00Z that day",
  )


def _add_reports(sub: argparse._SubParsersAction, add) -> None:
  """`stats`, `snapshot`, `status`, `log` and `tui`."""
  _recursive_flag(add("stats", cmd_stats, "counts by status, size, tree and pass"))

  sp = sub.add_parser("snapshot", help="compact dashboard reads across projects")
  sp.set_defaults(func=cmd_snapshot)
  sp.add_argument("--root", dest="snapshot_roots", action="append",
                  help="project root; repeat to read several projects in order")
  _json_flags(sp)
  _recursive_flag(sp)
  sp.add_argument("--parts", default=",".join(snapshot.PARTS),
                  help="comma-separated dashboard parts: " + ",".join(snapshot.PARTS))

  _recursive_flag(add("status", cmd_status, "next item, progress, and blockers in one view"))

  sp = add("log", cmd_log, "recent history, newest first")
  sp.add_argument("--limit", type=int, default=20, help="how many entries (default 20)")
  sp.add_argument("--item", action="append", help="filter to these item ids (repeatable)")
  sp.add_argument("--action", action="append", help="filter to these actions, e.g. set, edit (repeatable)")
  sp.add_argument("--by", action="append", help="filter to actions recorded by these owners (repeatable)")
  sp.add_argument("--since", help="include entries at or after this timezone-aware ISO8601 timestamp")
  sp.add_argument("--until", help="include entries before this timezone-aware ISO8601 timestamp")

  add("tui", cmd_tui, "browse and reorder interactively (also: ui)",
      json_flag=False, aliases=("ui",))


def build_parser() -> argparse.ArgumentParser:
  # --root is accepted on both sides of the subcommand, because both
  # `slicer --root x next` and `slicer next --root x` are natural to type.
  # The subcommand copy suppresses its default so that, when it is absent, it
  # does not overwrite a value already parsed from before the subcommand.
  common = argparse.ArgumentParser(add_help=False)
  common.add_argument(
    "--root",
    default=argparse.SUPPRESS,
    help="project root (default: discovered from the working directory)",
  )
  p = _ArgumentParser(
    prog="slicer", description=_DESCRIPTION,
    epilog=f"Source: {__url__}\nIssues: {_ISSUES_URL}",
    formatter_class=argparse.RawDescriptionHelpFormatter,
  )
  p.add_argument(
    "--root", default=None, help="project root (default: discovered from the working directory)"
  )
  p.add_argument("--version", action="version", version=f"slicer {__version__}")
  p.add_argument("--about", action="store_true", help="show version, description, source and issues URLs")
  sub = p.add_subparsers(dest="command", required=True)

  add = _make_add(sub, common)

  _add_ai(sub, common)
  _add_setup(add)
  _add_import(add)
  _add_next(add)
  _add_browse(add)
  _add_item_writes(add)
  _add_notes_and_feedback(add)
  _add_lifecycle(add)
  _add_prose_and_template(sub, common)
  _add_remove(add)
  _add_checks(add)
  _add_catalog(sub, common)
  _add_relay_roles(sub, common)
  _add_relay_run(sub, common)
  _add_reports(sub, add)
  return p
