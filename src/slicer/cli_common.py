"""Helpers every command handler shares, split out of `cli.py` so handler modules need not import it.

Exit codes, the shared flags, output buffering for `--strict`, the `--render` wrappers
and `_state` live here, along with the small parsing and lookup helpers several command
groups use. This module imports no `cli*` module when it loads, so a handler module can
import it without a cycle. The one thing it needs from `cli.py` is the built parser, and
`_built_parser` reaches it lazily. Helpers that tests patch (`_heal_render`,
`_via_editor`, `_read_user_file`) are called as `cli_common.<name>` from other modules,
so each has a single lookup site.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from slicer import check as check_mod
from slicer import ids, model, ops, render, store, vcs
from slicer import __doc__ as _package_doc, __url__
from slicer.config import Config
from slicer.errors import ConfigError, SlicerError, StateError, is_internal


OK, DRIFT, USAGE, INTERNAL = 0, 1, 2, 3
_DESCRIPTION = _package_doc.splitlines()[0].split(" — ", 1)[1].removesuffix(".")
_ISSUES_URL = __url__ + "/issues"


def _json_flags(parser: argparse.ArgumentParser, *, suppress: bool = False) -> None:
  """`--json` and `--lean`. A suppressed default does not overwrite an earlier one.

  A flag typed before the subcommand is parsed by the parent. The copy on the
  subcommand must not reset that value when the flag is absent there.
  """
  default = argparse.SUPPRESS if suppress else False
  parser.add_argument("--json", action="store_true", default=default, help="machine-readable output")
  parser.add_argument(
    "--lean", action="store_true", default=default,
    help="with --json, compact output that omits empty fields, a repeated short title, and an item path",
  )


def _rest_flag(parser: argparse.ArgumentParser, *, suppress: bool = False) -> None:
  """`--rest` on `ai` and `ai instructions`, suppressed on the subcommand like `--json`."""
  parser.add_argument(
    "--rest", action="store_true", default=argparse.SUPPRESS if suppress else False,
    help="print only what the slicer skill does not already carry",
  )


def _recursive_flag(sp: argparse.ArgumentParser) -> argparse.ArgumentParser:
  """`-r/--recursive` on the read-only reports: one result per nested project."""
  sp.add_argument(
    "-r", "--recursive", action="store_true",
    help="report on every project at or below the start directory (--root or the working "
    "directory), one result each; reads only and never walks up",
  )
  return sp


def _emit(
  args: argparse.Namespace, payload: object, text: str, *, keep_empty: tuple[str, ...] = (),
  keep_fields: tuple[str, ...] = (),
) -> None:
  collected = getattr(args, "_collect", None)
  if getattr(args, "json", False):
    shaped = payload
    if getattr(args, "lean", False):
      shaped = model.lean(payload)
      if keep_empty and isinstance(payload, dict) and isinstance(shaped, dict):
        # Lean drops empty lists. These keys are part of the shape even then.
        shaped = {
          key: shaped.get(key, value)
          for key, value in payload.items()
          if key in shaped or key in keep_empty
        }
    if keep_fields and isinstance(payload, dict) and isinstance(shaped, dict):
      # Fixed machine contracts retain nested lists even when lean would prune them.
      shaped = {key: value if key in keep_fields else shaped[key]
                for key, value in payload.items() if key in shaped or key in keep_fields}
    if collected is not None:
      # `-r` gathers each project's result instead of printing it.
      collected.append(shaped)
    else:
      _dump_json(args, shaped)
  elif collected is not None:
    collected.append(text)
  elif text:
    _write_out(args, text)


def _dump_json(args: argparse.Namespace, obj: object) -> None:
  """Write `obj` as JSON: compact under `--lean`, indented otherwise.

  This only formats. Lean shaping of the payload itself is `_emit`'s job.
  """
  if getattr(args, "lean", False):
    _write_out(args, json.dumps(obj, ensure_ascii=False, separators=(",", ":")))
  else:
    _write_out(args, json.dumps(obj, ensure_ascii=False, indent=2))


def _write_out(args: argparse.Namespace, line: str) -> None:
  """Print, or buffer under `--strict` so no success output escapes before the
  render that gates the change has actually succeeded."""
  _defer(args, "_deferred", line, sys.stdout)


def _write_err(args: argparse.Namespace, line: str) -> None:
  """Same gate as `_write_out`, for a warning that must vanish if `--strict` rolls back."""
  _defer(args, "_deferred_err", line, sys.stderr)


def _defer(args: argparse.Namespace, attr: str, line: str, stream: object) -> None:
  if getattr(args, "_defer_output", False):
    buf = getattr(args, attr, None)
    if buf is None:
      buf = []
      setattr(args, attr, buf)
    buf.append(line)
  else:
    print(line, file=stream)


def _render_after_mutation(args: argparse.Namespace) -> int:
  """Re-read and render the just-saved state; return how many files changed."""
  state = _state(args)
  expected = render.plan(state)
  return len(render.write(expected, state.render_dir, render.compare(expected, state.render_dir)))


def _print_render_summary(args: argparse.Namespace, written: int) -> None:
  stream = sys.stderr if getattr(args, "print_id", False) else sys.stdout
  print(f"rendered {written} file(s)", file=stream)


def _heal_render(args: argparse.Namespace, *, merge_checked: bool = False) -> None:
  """Regenerate a stale or conflicted `render/` before a write, and say so on stderr.

  Best effort: a project whose state or config does not load is left for the handler to
  report, and a render that cannot be written is a warning, because the write itself can
  still land. Never during a Git merge: `merge_checked` says the caller has just refused
  one (`_begin_write`); the `next --start` sites have no such refusal and are checked here.
  """
  root = Path(args.root) if getattr(args, "root", None) else None
  try:
    if not merge_checked and vcs.merge_in_progress(store.discover(root)):
      return
    healed = ops.heal_render(store.load(root))
  except (StateError, ConfigError):
    return
  except (SlicerError, OSError) as exc:
    print(f"slicer: could not regenerate the stale render: {exc}; run `slicer render`",
          file=sys.stderr)
    return
  if healed:
    print(f"slicer: regenerated stale render files before this write: {', '.join(healed)}",
          file=sys.stderr)


def _begin_write(args: argparse.Namespace, root: Path, *, heal: bool = True) -> None:
  """The start of every locked state write: refuse during a merge, then heal `render/`."""
  vcs.require_no_merge(root)
  if heal:
    _heal_render(args, merge_checked=True)


def _mutating(fn):
  """Wrap a mutating handler so `--render` renders it, from one place.

  Default `--render` is render-*after*: the handler has already saved to disk and
  printed its own result (an object, or a JSON array for a batch of ids). If the
  follow-up render fails, the change is still committed, so we must not let the
  failure reach `main` — that would print a *second* document after the handler's
  result. Instead stdout stays the handler's single result unchanged, the render
  failure goes to stderr, and the exit is DRIFT (1): state and `render/` are now
  out of step, which is what `slicer render` resolves. The exit is DRIFT whatever
  the cause, because the mutation the user asked for did succeed; only the
  projection is stale.

  `--strict` (with `--render`) flips this to render-*first*: `_mutate_strict`
  buffers the mutation and its output, renders the proposed state, and only then
  lets the change land — the render-first policy `done` already follows, offered
  to any mutation. A render failure there rolls the whole change back and prints
  nothing. Migration uses `_migrate_strict` because it also replaces config and
  templates and may create the tracking directory.
  """
  def wrapped(args: argparse.Namespace) -> int:
    render_on = getattr(args, "render", False) and not getattr(args, "dry_run", False)
    if getattr(args, "strict", False) and not render_on:
      raise StateError("--strict has no effect without --render", code="usage")
    if getattr(args, "check", False) and not render_on:
      raise StateError("--check has no effect without --render", code="usage")
    if not render_on:
      return fn(args)
    if getattr(args, "strict", False):
      if args.command == "migrate":
        return _migrate_strict(fn, args)
      code = _mutate_strict(fn, args)
    else:
      code = fn(args)
      if code != OK or getattr(args, "_no_change", False):
        return code
      try:
        written = _render_after_mutation(args)
      except (SlicerError, OSError) as exc:
        print(f"slicer: saved, but render failed: {exc}; run `slicer render`", file=sys.stderr)
        return DRIFT
      if not getattr(args, "json", False):
        _print_render_summary(args, written)
    if code == OK and getattr(args, "check", False):
      return _post_check(args, args.command)
    return code

  # main() locks the project around a mutating command; the flag says which
  # handlers those are, so the render re-read above is inside the same lock.
  wrapped.mutates = True
  return wrapped


def _mutate_strict(fn, args: argparse.Namespace) -> int:
  """Render the proposed change before letting it land (the `--strict` policy).

  The handler runs against a staged state (begun in `_state`), so its saves and
  slice moves buffer instead of hitting disk, and its success output buffers too.
  Then the proposed state is rendered: on success the buffered writes and output
  are flushed; on failure the batch is discarded and the error propagates, so a
  change that cannot be rendered never lands and no success document is printed.
  """
  args._defer_output = True
  state = _state(args)  # loads, caches on args, and begins the staging transaction
  try:
    code = fn(args)
    if code != OK or getattr(args, "_no_change", False):
      state.discard_stage()
      # The handler already explained the refusal. Discarding the stage must
      # not also discard that report.
      _flush_deferred(args)
      return code
    expected = render.plan(state)
    # write_atomic, so a render write that fails partway restores render/ and the
    # staged state rolls back with it -- --strict lands whole or not at all.
    written = len(render.write_atomic(expected, state.render_dir, render.compare(expected, state.render_dir)))
    state.commit_stage()
  except BaseException:
    state.discard_stage()
    raise
  _flush_deferred(args)
  if not getattr(args, "json", False):
    _print_render_summary(args, written)
  return code


def _flush_deferred(args: argparse.Namespace) -> None:
  """Print the output a strict handler held back until its fate was known."""
  for line in getattr(args, "_deferred", []):
    print(line)
  for line in getattr(args, "_deferred_err", []):
    print(line, file=sys.stderr)


def _migrate_strict(fn, args: argparse.Namespace) -> int:
  """Roll back every file migrate can replace if its required render fails.

  Migrate creates config and templates as well as state, so it cannot use the
  staged State transaction used by ordinary mutations. Snapshot its exact
  target directory, never an ancestor found by project discovery.
  """
  base = Path(args.root or ".").resolve() / store.DIR_NAME
  existed = base.exists()
  directories = {
    path.relative_to(base) for path in base.rglob("*") if path.is_dir()
  } if existed else set()
  saved = {
    path.relative_to(base): path.read_bytes()
    for path in base.rglob("*") if path.is_file() and path.name != store.LOCK_NAME
  } if existed else {}
  args._defer_output = True
  try:
    code = fn(args)
    if code != OK:
      return code
    state = store.load(base.parent)
    expected = render.plan(state)
    written = len(render.write_atomic(
      expected, state.render_dir, render.compare(expected, state.render_dir)
    ))
  except BaseException:
    if not existed:
      if base.exists():
        shutil.rmtree(base)
    else:
      for path in sorted(base.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path.is_file() and path.name != store.LOCK_NAME and path.relative_to(base) not in saved:
          path.unlink()
        elif path.is_dir() and path.relative_to(base) not in directories:
          try:
            path.rmdir()
          except OSError:
            pass
      for rel, data in saved.items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    raise
  for line in getattr(args, "_deferred", []):
    print(line)
  if not getattr(args, "json", False):
    print(f"rendered {written} file(s)")
  return code


def _render_flag(
  sp: argparse.ArgumentParser, *, help: str = "render .slicer/render/ after the change",
) -> argparse.ArgumentParser:
  sp.add_argument("--render", action="store_true", help=help)
  return sp


def _check_flag(sp: argparse.ArgumentParser) -> argparse.ArgumentParser:
  """`--check` runs `slicer check` after a rendered change has landed."""
  sp.add_argument(
    "--check", action="store_true",
    help="with --render, run slicer check after the change lands; exit 1 if it fails",
  )
  return sp


def _strict_flag(sp: argparse.ArgumentParser) -> argparse.ArgumentParser:
  """`--strict` gates a mutation on its render (with `--render`): the change is
  rolled back, and nothing printed, if the render fails. Offered on the
  mutating commands, including import, migrate, remove, retire, and handoff.
  `done --render` already behaves this way and does not take the flag."""
  sp.add_argument(
    "--strict", action="store_true",
    help="with --render, require the render to succeed before the change lands",
  )
  return sp


def _render_hint(args: argparse.Namespace) -> str:
  """The `run slicer render` reminder — empty when --render already rendered."""
  return "" if getattr(args, "render", False) else "run `slicer render`"


def _state(args: argparse.Namespace) -> store.State:
  # Under strict --render the whole mutation runs against one staged state that
  # `_mutate_strict` renders and commits, so cache and stage it here and hand the
  # same instance back to the handler that mutates it. Every other path loads
  # fresh, unchanged.
  cached = getattr(args, "_state", None)
  if cached is not None:
    return cached
  state = store.load(Path(args.root) if args.root else None)
  if getattr(args, "strict", False) and getattr(args, "render", False):
    args._state = state
    state.begin_stage()
  return state


def _read_user_file(path: Path | str) -> str:
  """Read a file the user pointed us at, reporting bad bytes as a clean io error."""
  try:
    return Path(path).read_text(encoding="utf-8")
  except UnicodeDecodeError as e:
    raise StateError(f"{path}: not valid UTF-8: {e}", code="io") from None


def _nonnegative_int(value: str) -> int:
  try:
    number = int(value)
  except ValueError:
    raise argparse.ArgumentTypeError("must be a nonnegative integer") from None
  if number < 0:
    raise argparse.ArgumentTypeError("must be a nonnegative integer")
  return number


def _positive_int(value: str) -> int:
  try:
    number = int(value)
  except ValueError:
    raise argparse.ArgumentTypeError("must be an integer >= 1") from None
  if number < 1:
    raise argparse.ArgumentTypeError("must be an integer >= 1")
  return number


def _id_floor(state: store.State) -> int:
  """The highest id counter any sibling worktree has reached, so a new id clears it."""
  siblings = [index for _, index in store.sibling_ids(state.root)]
  return ids.floor_from(siblings, state.index.id_prefix)


@contextmanager
def _item_lookup(state: store.State, item_ids: list[str]) -> Iterator[None]:
  """Name `slicer catalog show` when a command's item id is a catalog record.

  Items and catalog records share one id shape on the command line, so a copied
  `C01` lands in `no_such_item`. The first id that is not a roadmap item decides:
  a catalog record gets the hint, and any other miss, or any other error, re-raises
  unchanged. The code stays `no_such_item` either way.
  """
  try:
    yield
  except StateError as exc:
    if exc.code != "no_such_item":
      raise
    missing = next((item_id for item_id in item_ids if state.index.get(item_id) is None), None)
    record = None if missing is None else state.index.catalog.get(missing)
    if record is None:
      raise
    raise StateError(
      f"no such item: {missing} is a {record.kind} catalog record, not a roadmap item; "
      f"run `slicer catalog show {missing}`",
      code="no_such_item",
    ) from None


def _undeclared_pass_line(key: str) -> str:
  """The stderr line for a pass key no `prose add-pass` declared yet."""
  return (
    f"slicer: pass {key} is not declared, so the queue orders its items by score among other "
    f"undeclared passes; declare it with `slicer prose add-pass {key} --after <pass>`."
  )


def _via_editor(initial: str) -> str | None:
  """Open $EDITOR on `initial`; None means the editor failed, so change nothing."""
  editor = os.environ.get("EDITOR", "vi")
  with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
    fh.write(initial + "\n")
    path = fh.name
  try:
    done = subprocess.run([*editor.split(), path], check=False)
    if done.returncode != 0:
      return None
    return _read_user_file(path).rstrip("\n")
  finally:
    Path(path).unlink(missing_ok=True)


def _body_from(args: argparse.Namespace, initial: str) -> str | None:
  """Choose one explicit body source, falling back to $EDITOR only if absent."""
  if sum((args.text is not None, args.file is not None, args.stdin)) > 1:
    raise StateError("choose only one of --text, --file, or --stdin", code="usage")
  if args.text is not None:
    return args.text
  if args.file:
    return _read_user_file(args.file).rstrip("\n")
  if args.stdin:
    return sys.stdin.read().rstrip("\n")
  return _via_editor(initial)


def _batch_ids(args: argparse.Namespace) -> tuple[list[str], bool]:
  raw = args.id
  if "-" in raw:
    if raw != ["-"]:
      raise StateError("use '-' alone to read ids from stdin", code="usage")
    item_ids = sys.stdin.read().split()
    if not item_ids:
      raise StateError("stdin contains no item ids", code="usage")
    return list(dict.fromkeys(item_ids)), True
  return list(dict.fromkeys(raw)), len(raw) > 1


def _emit_items(args: argparse.Namespace, items: list[model.Item], batch: bool, lines: list[str]) -> None:
  payload = [item.to_dict() for item in items]
  _emit(args, payload if batch else payload[0], "\n".join(lines))


def _template_source(args: argparse.Namespace, *, noun: str = "template") -> str:
  """The new text from exactly one of --file or --stdin, newlines untouched."""
  if args.file is not None and args.stdin:
    raise StateError("choose only one of --file or --stdin", code="usage")
  if args.file is not None:
    try:
      return Path(args.file).read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
      raise StateError(f"{args.file}: not valid UTF-8: {exc}", code="io") from None
  if args.stdin:
    try:
      if noun == "prompt" and hasattr(sys.stdin, "buffer"):
        return sys.stdin.buffer.read().decode("utf-8")
      return sys.stdin.read()
    except UnicodeDecodeError as exc:
      raise StateError(f"stdin: not valid UTF-8: {exc}", code="io") from None
  raise StateError(f"give the {noun} text with --file or --stdin", code="usage")


_BACKTICK_SPAN = re.compile(r"`([^`\n]+)`")
_FENCE_OPEN = re.compile(r"(`{3,}|~{3,})")


def _outside_fences(body: str) -> str:
  """`body` with every line of a fenced code block blanked, fence lines included.

  Lines are blanked rather than dropped so line numbers stay put. A fence closes
  on a later line of at least as many of the same character and nothing else; an
  unclosed fence runs to the end, as CommonMark renders it.
  """
  out: list[str] = []
  fence = ""
  for line in body.split("\n"):
    text = line.strip()
    if not fence:
      opened = _FENCE_OPEN.match(text)
      if opened:
        fence = opened.group(1)
        out.append("")
      else:
        out.append(line)
      continue
    out.append("")
    if text.startswith(fence) and not text.strip(fence[0]):
      fence = ""
  return "\n".join(out)


def _unknown_flags(parser: argparse.ArgumentParser, tokens: list[str]) -> list[str]:
  """The flag-looking tokens the real parser does not recognise in `tokens`.

  `parse_known_args` returns unrecognised options rather than erroring on them,
  so an unknown flag comes back in the extras. A command that fails for another
  reason -- a missing positional, an unknown subcommand, `-h` -- is not a flag
  problem, so those raise or exit and are swallowed. Output is redirected because
  argparse writes usage on the way out.
  """
  sink = io.StringIO()
  try:
    with redirect_stdout(sink), redirect_stderr(sink):
      _, extras = parser.parse_known_args(tokens)
  except (_ParserError, SystemExit):
    return []
  return [tok for tok in extras if tok.startswith("-") and tok != "-"]


_SHELL_OPERATOR_CHARS = frozenset("&|;")


def _shell_commands(command: str) -> list[list[str]]:
  """The words of each command in `command`, cut at shell control operators.

  `punctuation_chars` makes shlex split `&&`, `||`, `;` and `|` out as their own
  tokens even when glued to a word (`--lean&&echo`); a token made only of those
  characters (so `;;` and `|&` too) ends the command. Raises ValueError on
  unbalanced quotes, as `shlex.split` does.
  """
  lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
  lexer.whitespace_split = True
  lexer.commenters = ""  # like shlex.split: `#` is an ordinary character
  commands: list[list[str]] = [[]]
  for token in lexer:
    if token and set(token) <= _SHELL_OPERATOR_CHARS:
      commands.append([])
    else:
      commands[-1].append(token)
  return [words for words in commands if words]


def _slice_flag_problems(state: store.State) -> list[str]:
  """`slicer ...` examples in live slices whose flags the parser rejects.

  A span that chains commands is cut at the first shell operator (`&&`, `||`,
  `;`, `|`); each command that starts with `slicer` is checked on its own.

  A removed or renamed flag can sit in a slice's Failing tests until an agent
  copies it into a real command; `check` catches it first. Done and retired
  slices are skipped -- they keep their history, old flag names and all. Only
  backtick commands are parsed; a flag merely mentioned in prose is left alone,
  and the command is never executed. Fenced blocks are literal text to write,
  such as the lines a slice adds for a flag it introduces, so they are skipped.
  """
  cfg = state.config
  skip = {cfg.done_status}
  if cfg.retired_status:
    skip.add(cfg.retired_status)
  parser = _built_parser()
  problems: list[str] = []
  for item in state.index.items:
    if item.status in skip:
      continue
    sl = state.slices.get(item.id)
    if sl is None:
      continue
    for section in sl.sections:
      for span in _BACKTICK_SPAN.findall(_outside_fences(section.body)):
        command = span.strip()
        if not command.startswith("slicer "):
          continue
        try:
          commands = _shell_commands(command)
        except ValueError:
          continue  # unbalanced quotes -- not a flag problem
        for tokens in commands:
          if tokens[0] != "slicer":
            continue
          for flag in _unknown_flags(parser, tokens[1:]):
            problems.append(
              f"{item.id} / {section.heading}: unknown flag {flag} in `{command}`"
            )
  return problems


def _check_report(state: store.State, *, show_diff: bool = False) -> tuple[check_mod.CheckReport, list[str]]:
  """The check report and its finding lines, without the final passed or failed line."""
  report, expected, diff = check_mod.run(state)
  # A removed flag in a slice's own `slicer ...` examples is drift too.
  report.problems.extend(_slice_flag_problems(state))
  lines: list[str] = []
  for rel, detail in zip(report.stale_render, report.stale_render_details):
    lines.append(f"stale render: {rel}: {detail['detail']}")
    if show_diff:
      lines.append(render.unified(expected, state.render_dir, rel))
  if report.stale_render_kind == "roadmap_only":
    lines.append(
      "only the roadmap render is stale, as a merge leaves it; any slicer write regenerates "
      "it, or run slicer render"
    )
  lines.extend(f"orphan render: {rel}" for rel in report.orphan_render)
  lines.extend(f"stale sync: {s}" for s in report.stale_sync)
  lines.extend(f"problem: {p}" for p in report.problems)
  lines.extend(f"warn: {w}" for w in report.warnings)
  return report, lines


def _post_check(args: argparse.Namespace, verb: str) -> int:
  """Run check against the landed state. Silence means it passed."""
  state = store.load(Path(args.root) if args.root else None)
  report, lines = _check_report(state)
  if report.ok:
    return OK
  for line in lines:
    print(line, file=sys.stderr)
  print(
    f"slicer: {verb} landed, but check failed; run `slicer check` for the report",
    file=sys.stderr,
  )
  return DRIFT


def _status_tally(cfg: Config, tally: dict[str, int]) -> str:
  """A status tally for people: JSON keys by status, text shows each label."""
  return " · ".join(f"{cfg.status_label(k)} {v}" for k, v in tally.items())


def _census(state: store.State) -> dict:
  """The item census `stats` and `status` share, so they cannot disagree."""
  items = state.index.items
  by_tree_status = model.cross_counts(items, "trees", "status")
  completion = model.completion(items, state.config.done_status)
  return {
    "total": completion["total"],
    "completion": completion,
    "by_status": model.counts(items, "status"),
    "by_size": model.counts(items, "size"),
    "by_tree": model.counts(items, "trees"),
    "by_pass": model.counts(items, "pass_key"),
    "by_tree_status": by_tree_status,
  }


def _log_timestamp(value: str, where: str, *, code: str) -> datetime:
  """Compare history windows as instants without rewriting stored timestamps."""
  try:
    instant = datetime.fromisoformat(value)
    if instant.tzinfo is None or instant.utcoffset() is None:
      raise ValueError("timezone required")
    return instant.astimezone(timezone.utc)
  except (TypeError, ValueError, OverflowError):
    raise StateError(
      f"{where}: invalid timestamp {value!r}; use an ISO8601 timestamp with "
      "a timezone, for example 2026-10-05T12:00:00Z.", code=code,
    ) from None


class _ParserError(Exception):
  """Keep argparse's diagnostic context until main can choose the output format."""

  def __init__(self, parser: argparse.ArgumentParser, message: str) -> None:
    super().__init__(message)
    self.parser = parser


class _ArgumentParser(argparse.ArgumentParser):
  def __init__(self, *args: object, **kwargs: object) -> None:
    # Python 3.14 colorizes usage/help/errors by default, and honors FORCE_COLOR
    # even when output is piped -- which injects ANSI an agent then has to strip
    # and makes slicer's diagnostics differ by environment. Keep them plain and
    # deterministic. `color` is a 3.14+ argument, so only pass it there.
    if sys.version_info >= (3, 14):
      kwargs.setdefault("color", False)
    super().__init__(*args, **kwargs)  # type: ignore[arg-type]

  def error(self, message: str) -> None:
    raise _ParserError(self, message)


def _exit_for(code: str) -> int:
  """Exit status for an error code: internal/state codes are 3, the rest 2."""
  return INTERNAL if is_internal(code) else USAGE


def _built_parser() -> argparse.ArgumentParser:
  """The parser `cli.py` builds and caches. Imported when called: `cli` imports this module."""
  from slicer import cli

  return cli._cached_parser()
