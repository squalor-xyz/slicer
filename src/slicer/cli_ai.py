"""`ai` commands: the agent-facing text the project serves, and the files that install it.

Onboarding, the relay, the skill, the prompt overrides, the hooks and the guard all print or
install text from `ai.py`, which imports no state module; these handlers add the project's
policy to it. Each handler is attached to its subparser in `cli_parser`; shared helpers come
from `cli_common`, and nothing here imports `cli`.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from pathlib import Path

from slicer import ai, jsonio, ops, store, workflow
from slicer import roles as role_matrix
from slicer.cli_common import OK, _dump_json, _emit, _state, _template_source
from slicer.config import CONFIG_NAME, RELAY_CONTRACT_MAX, Config
from slicer.errors import SlicerError, StateError


def _salvage_relay_pin(project: Path) -> int | None:
  """The raw `relay_contract` of a project whose config does not load, best effort.

  A pin that fails validation must not unpin the project, so where the contract is served
  a config that cannot be loaded still has its pin read straight from the JSON, with
  nothing but `json`. A usable pin (a whole number from 1 to `RELAY_CONTRACT_MAX`) is
  returned for the usual comparison. A key that is there, not null and not such a number is
  refused with `contract_mismatch`: the pin was written and cannot be read, and
  `config --unset` cannot load the file either, so the message says to fix it by hand. No
  key, a null, or a file that cannot be read or parsed is None, as before.
  """
  try:
    raw = json.loads((project / store.DIR_NAME / CONFIG_NAME).read_text(encoding="utf-8"))
  except (OSError, ValueError):
    return None
  if not isinstance(raw, dict) or raw.get("relay_contract") is None:
    return None
  pinned = raw["relay_contract"]
  if (isinstance(pinned, int) and not isinstance(pinned, bool)
      and 1 <= pinned <= RELAY_CONTRACT_MAX):
    return pinned
  raise StateError(
    f"this project's relay_contract pin in {store.DIR_NAME}/{CONFIG_NAME} could not be read "
    f"({json.dumps(pinned)} is not a whole number from 1 to {RELAY_CONTRACT_MAX}), so "
    "slicer will not serve the relay contract. Fix the file by hand: "
    "`slicer config relay_contract --unset` cannot load it either",
    code="contract_mismatch",
  )


def _ai_project(
  args: argparse.Namespace, *, strict: bool = False, relay: bool = False,
) -> tuple[Path | None, str, str, int | None, str]:
  """The project root, finish and report policy, relay pin and check command when config and index load.

  Read-only: no lock and no write. A miss warns and keeps the generic text, and the
  root comes back None. `strict` is for callers that write the text somewhere: a project
  that is found but cannot be read raises instead, so a handoff project never gets the
  generic `done` skill. Not finding a project still falls back. `relay` is for the
  commands that serve the relay contract: when the config does not load they still
  recover the pin (`_salvage_relay_pin`) instead of serving the generic text unpinned.
  """
  root = Path(args.root).resolve() if getattr(args, "root", None) else None
  project = None
  try:
    project = store.discover(root)
    cfg = Config.load(project / store.DIR_NAME / CONFIG_NAME)
    store.read_index(project)
  except (OSError, SlicerError) as exc:
    if strict and project is not None:
      if isinstance(exc, SlicerError):
        raise
      raise StateError(str(exc), code="io") from exc
    pinned = _salvage_relay_pin(project) if relay and project is not None else None
    if ai.pin_mismatch(pinned) is None:  # else the caller refuses, and the guide is not printed
      print(f"{exc}; printing the generic guide", file=sys.stderr)
    return None, "done", "", pinned, ""
  return (project, cfg.implement_finish, cfg.handoff_requires_note_kind, cfg.relay_contract,
          cfg.check_command)


def _ai_policy(args: argparse.Namespace, *, strict: bool = False) -> tuple[str, str]:
  """The finish and report policy that `_ai_project` reads, without the root."""
  _project, finish, required, _pinned, _check = _ai_project(args, strict=strict)
  return finish, required


def _require_relay_pin(pinned: int | None) -> None:
  """Refuse to serve the relay contract to a project that pins another version."""
  message = ai.pin_mismatch(pinned)
  if message is not None:
    raise StateError(message, code="contract_mismatch")


def cmd_ai_instructions(args: argparse.Namespace) -> int:
  # `--rest` is the guide minus the skill, which never reads the project.
  text = ai.rest_text() if args.rest else ai.instructions_text(*_ai_policy(args))
  _emit(args, {"instructions": text}, text.rstrip("\n"))
  return OK


def _ai_prompt_text(args: argparse.Namespace, name: str) -> tuple[str, bool]:
  """Read project overrides only when its config is usable, silently falling back."""
  default = ai.prompt_text(name)
  if getattr(args, "default", False):
    return default, False
  try:
    root = store.discover(Path(args.root).resolve() if args.root else None)
    Config.load(root / store.DIR_NAME / CONFIG_NAME)
  except (OSError, SlicerError):
    return default, False
  override = store.read_prompt_exact(root, name)
  return (default, False) if override is None else (override, True)


def cmd_ai_prompt(args: argparse.Namespace) -> int:
  text, customized = _ai_prompt_text(args, args.prompt_name)
  if args.json:
    _emit(args, {"name": args.prompt_name, "prompt": text, "customized": customized}, "")
  else:
    sys.stdout.write(text)
  return OK


def cmd_ai_show(args: argparse.Namespace) -> int:
  name = ops.require_prompt(args.name)
  text, customized = _ai_prompt_text(args, name)
  if args.json:
    _emit(args, {"name": name, "text": text, "customized": customized}, "")
  else:
    sys.stdout.write(text)
  return OK


def _prompt_write(args: argparse.Namespace, change: ops.PromptChange) -> int:
  if not change.changed:
    args._no_change = True
    text = f"{change.name} is already " + ("that text" if change.customized else "the default")
  else:
    verb = "reset" if not change.customized else "replaced"
    text = f"{verb} {change.name}"
  _emit(args, {"name": change.name, "changed": change.changed,
               "customized": change.customized}, text)
  return OK


def cmd_ai_set(args: argparse.Namespace) -> int:
  name = ops.require_prompt(args.name)
  text = _template_source(args, noun="prompt")
  return _prompt_write(args, ops.prompt_set(_state(args), name, text))


def cmd_ai_reset(args: argparse.Namespace) -> int:
  name = ops.require_prompt(args.name)
  return _prompt_write(args, ops.prompt_reset(_state(args), name))


def cmd_ai_relay(args: argparse.Namespace) -> int:
  """Print the slice relay. Same read-only policy as `ai instructions`, except
  `--init-roles`, which writes the example role matrix and renders."""
  if args.force and not args.init_roles:
    raise StateError("--force needs --init-roles", code="usage")
  if args.init_roles:
    return _init_roles(args)
  project, finish, required, pinned, check_command = _ai_project(args, relay=True)
  _require_relay_pin(pinned)
  roles = None
  if project is not None:
    # Read first: an unreadable matrix must fail with its own error, not after the advice.
    matrix = store.read_roles(project)
    legacy = store.legacy_roles_advice(project)
    if legacy is not None:
      print(f"slicer: {legacy}", file=sys.stderr)
    if matrix is not None:
      # The section sits under `## Roles matrix`, so its headings start one level down.
      roles = role_matrix.render_markdown(matrix, level=2)
  text = ai.relay_text(finish, required, roles=roles, in_project=project is not None)
  _emit(args, {"contract": ai.contract_line(), "pinned": pinned, "check_command": check_command,
               "relay": text, **ai.relay_contract(finish)},
        text.rstrip("\n"), keep_empty=("pinned", "check_command"),
        keep_fields=("setup", "order", "report", "outcomes", "stop", "changes", "roles"))
  return OK


def cmd_ai_relay_orchestrator(args: argparse.Namespace) -> int:
  """Print the orchestrator's contract. Read-only, with the project's finish step and
  report kind the way `ai relay` reads them; outside a project it is the generic text."""
  _project, finish, required, pinned, _check = _ai_project(args, relay=True)
  _require_relay_pin(pinned)
  text = ai.orchestrator_text(finish, required, session=args.session)
  _emit(args, {"contract": ai.contract_line(), "pinned": pinned, "orchestrator": text,
               **ai.relay_contract(finish, orchestrator=True, session=args.session)},
        text.rstrip("\n"), keep_empty=("pinned",),
        keep_fields=("order", "report", "outcomes", "stop", "changes"))
  return OK


def cmd_ai_relay_brief(args: argparse.Namespace) -> int:
  try:
    state = _state(args)
  except SlicerError as exc:
    # A config that does not load still carries its pin; the load error stands otherwise.
    try:
      root = store.discover(Path(args.root).resolve() if args.root else None)
    except SlicerError:
      raise exc from None
    _require_relay_pin(_salvage_relay_pin(root))
    raise
  _require_relay_pin(state.config.relay_contract)
  payload = ops.relay_brief(
    state, args.id, worktree=args.worktree, branch=args.branch,
    run=args.run, step=args.step, parallel=args.parallel or [],
    well_specified=ops.parse_yes_no("well-specified", args.well_specified),
    split=ops.parse_yes_no("split", args.split), prefer_agent=args.prefer_agent,
    select=role_matrix.parse_selectors(args.select or []),
  )
  if args.json:
    _dump_json(args, payload)
  else:
    print(payload["brief"], end="")
  return OK


def _init_roles(args: argparse.Namespace) -> int:
  """`ai relay --init-roles`: `relay-roles init --render` under the project lock."""
  root = Path(args.root).resolve() if args.root else None
  with store.project_lock(root):
    state = store.load(root)
    (written, _removed), _rendered = ops.render_gated(
      state, lambda: ops.roles_init(state, force=args.force),
    )
  _emit(args, {"written": [str(p) for p in written]}, "".join(f"wrote {p}" for p in written))
  return OK


def cmd_recommended_workflow(args: argparse.Namespace) -> int:
  """The generic workflow document. Reads no project, takes no lock, writes nothing."""
  doc = workflow.text()
  _emit(args, {"workflow": doc}, doc.rstrip("\n"))
  return OK


# Where a project's agent skill is installed. Codex reads `.agents/skills/`, so
# there is no `.codex` copy; nothing goes in the home directory.
SKILL_INSTALL_PATHS = (
  ".claude/skills/slicer/SKILL.md",
  ".agents/skills/slicer/SKILL.md",
  ".grok/skills/slicer/SKILL.md",
)


def cmd_ai_skill(args: argparse.Namespace) -> int:
  writing = args.output is not None or args.install
  if args.force and not writing:
    raise StateError("--force needs --output or --install", code="usage")
  root = Path(args.root or ".").resolve()
  project = store.discover(root) if args.install else None  # --install needs a project
  text = ai.skill_text(*_ai_policy(args, strict=writing))
  if not writing:
    _emit(args, {"skill": text}, text.rstrip("\n"))
    return OK
  targets = [root / args.output] if args.output is not None else []
  if project is not None:
    targets += [project / rel for rel in SKILL_INSTALL_PATHS]
  # Same bytes the command prints, so a redirected copy and an installed one agree.
  content = text.rstrip("\n") + "\n"
  written = _write_skill(targets, content, force=args.force)
  _emit(args, {"skill": text, "written": written}, "\n".join(f"wrote {p}" for p in written))
  return OK


def _write_skill(targets: list[Path], content: str, *, force: bool) -> list[str]:
  """Write `content` to every target, or to none when any one is refused."""
  wanted = content.encode("utf-8")
  unique = list(dict.fromkeys(path.resolve() for path in targets))
  blocked: list[str] = []
  for path in unique:
    if path.is_dir():
      raise StateError(f"{path} is a directory; nothing was written", code="state")
    if path.exists() and not force:
      try:
        same = path.read_bytes() == wanted
      except OSError:
        same = False
      if not same:
        blocked.append(str(path))
  if blocked:
    raise StateError(
      f"different text already exists at {', '.join(blocked)}; pass --force to replace "
      "it. Nothing was written.",
      code="state",
    )
  for path in unique:
    try:
      jsonio.write_text(path, content)
    except OSError as exc:
      raise StateError(f"{path}: {exc}", code="io") from exc
  return [str(path) for path in unique]


def _is_slicer_hook_handler(handler: object) -> bool:
  return (isinstance(handler, dict) and isinstance(handler.get("command"), str)
          and any(command in handler["command"]
                  for command in ("slicer ai getting-started", "slicer ai guard")))


def _merge_hook_groups(groups: list, wanted: dict, *, force: bool) -> bool:
  """Put the snippet group into one event's groups; say whether other slicer groups conflict.

  A conflicting group holds a slicer handler but is not the snippet's handler list.
  Without `force` the groups stay as they were and the caller refuses. With it each
  conflicting group loses only its slicer handlers, and the snippet group takes the
  first one's place unless an exact group already exists.
  """
  def handlers(group: object) -> object:
    return group.get("hooks", []) if isinstance(group, dict) else []

  exact = any(handlers(group) == wanted["hooks"] for group in groups)
  conflicting = [i for i, group in enumerate(groups)
                 if handlers(group) != wanted["hooks"] and isinstance(handlers(group), list)
                 and any(_is_slicer_hook_handler(h) for h in handlers(group))]
  if not conflicting:
    if not exact:
      groups.append(copy.deepcopy(wanted))
    return False
  if force:
    merged: list = []
    for i, group in enumerate(groups):
      if i in conflicting:
        kept = [h for h in group["hooks"] if not _is_slicer_hook_handler(h)]
        if kept:
          merged.append({**group, "hooks": kept})
      else:
        merged.append(group)
      if i == conflicting[0] and not exact:
        merged.append(copy.deepcopy(wanted))
    groups[:] = merged
  return True


def cmd_ai_hooks(args: argparse.Namespace) -> int:
  if args.force and not args.install:
    raise StateError("--force needs --install", code="usage")
  snippet = ai.hooks_settings()
  if not args.install:
    _emit(args, {"settings": snippet}, jsonio.dumps(snippet).rstrip("\n"))
    return OK
  project = store.discover(Path(args.root or "."))
  target = project / ".claude/settings.json"
  if target.parent.is_symlink() or target.is_symlink():
    raise StateError(f"{target}: refusing a symlink settings path; nothing was written")
  if target.exists() and not target.is_file():
    raise StateError(f"{target}: not a regular file; nothing was written", code="state")
  try:
    existing = jsonio.read(target) if target.exists() else {}
  except StateError as exc:
    raise StateError(f"{target}: {exc}; nothing was written", code="state") from None
  if not isinstance(existing, dict) or not isinstance(existing.get("hooks", {}), dict):
    raise StateError(f"{target}: settings and hooks must be JSON objects; nothing was written")
  settings = copy.deepcopy(existing)
  hooks = settings.setdefault("hooks", {})
  conflicts: list[str] = []
  for event, wanted in snippet["hooks"].items():
    groups = hooks.setdefault(event, [])
    if not isinstance(groups, list):
      raise StateError(f"{target}: hooks.{event} must be a list; nothing was written")
    if _merge_hook_groups(groups, wanted[0], force=args.force):
      conflicts.append(event)
  if conflicts and not args.force:
    raise StateError(
      f"different slicer hooks already exist at {target} ({', '.join(dict.fromkeys(conflicts))}); "
      "pass --force to remove the other slicer handlers. Nothing was written.", code="state",
    )
  written: list[str] = []
  if settings != existing:
    jsonio.write(target, settings)
    written.append(str(target))
  _emit(args, {"settings": snippet, "written": written},
        f"wrote {target}" if written else f"already installed at {target}")
  return OK


def cmd_ai_guard(args: argparse.Namespace) -> int:
  try:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
      return OK
    start = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or args.root or "."
    if not isinstance(start, str):
      return OK
    root = Path(start)
    reason = ai.guard_decision(payload, root)
  except (ValueError, OSError, RuntimeError, SlicerError):
    return OK
  if reason is not None:
    print(jsonio.dumps({"hookSpecificOutput": {
      "hookEventName": "PreToolUse", "permissionDecision": "deny",
      "permissionDecisionReason": reason,
    }}), end="")
  return OK
