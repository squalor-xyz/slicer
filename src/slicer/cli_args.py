"""What the parser needs before any command runs, kept light so `--version` and `--help` start fast.

`build_parser` declares every subcommand, but a process runs one. This module holds the parts
of the command line that building the parser touches: the exit codes, the parser class and
its error, the shared flag helpers, and `LazyHandler`, which names a handler by module and
function and imports it when the command runs. It imports nothing but `errors` and the
package's own constants, so it never loads `dataclasses`, the store, the checker or the
renderer. `cli_common` re-exports these names, so a handler module imports them from there as
before.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Callable  # not `typing`: importing it alone costs several ms

from slicer import __doc__ as _package_doc, __url__
from slicer.errors import is_internal


OK, DRIFT, USAGE, INTERNAL = 0, 1, 2, 3
_DESCRIPTION = _package_doc.splitlines()[0].split(" — ", 1)[1].removesuffix(".")
_ISSUES_URL = __url__ + "/issues"


def _exit_for(code: str) -> int:
  """Exit status for an error code: internal/state codes are 3, the rest 2."""
  return INTERNAL if is_internal(code) else USAGE


class LazyHandler:
  """A command handler named by `module:function`, imported when the command first runs.

  `set_defaults(func=...)` takes one of these, so building the parser imports no handler
  module. `mutates` is known up front, because `main` asks it before the handler is
  imported: it says the command takes the project lock. `wrap` is for a handler that the
  parser wrapped in `cli_common._mutating`: the wrapper is applied on first use.
  """

  def __init__(self, ref: str, *, mutates: bool = False, wrap: bool = False) -> None:
    self.ref = ref
    self.mutates = mutates or wrap
    self._wrap = wrap
    self._resolved: Callable[[argparse.Namespace], int] | None = None

  def mutating(self) -> LazyHandler:
    """This handler under `cli_common._mutating`, so `--render` and `--strict` apply."""
    return LazyHandler(self.ref, wrap=True)

  def resolve(self) -> Callable[[argparse.Namespace], int]:
    """Import the module and return the handler (wrapped, if it is). Cached."""
    if self._resolved is None:
      module, _, name = self.ref.partition(":")
      handler = getattr(importlib.import_module(module), name)
      if self._wrap:
        from slicer.cli_common import _mutating

        handler = _mutating(handler)
      self._resolved = handler
    return self._resolved

  def __call__(self, args: argparse.Namespace) -> int:
    return self.resolve()(args)

  def __repr__(self) -> str:
    return f"LazyHandler({self.ref!r}{', wrap=True' if self._wrap else ''})"


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


class _PassAlias(argparse.Action):
  """`--pass`, the deprecated spelling of `--release-target`.

  It stores the same value; `main` prints the deprecation note once for the run,
  so a command that takes the flag twice, or a recursive one, still says it once.
  """

  def __call__(self, parser, namespace, values, option_string=None) -> None:
    setattr(namespace, self.dest, values)
    namespace.pass_alias_used = True


PASS_ALIAS_NOTE = (
  "slicer: --pass is deprecated; use --release-target (same value). "
  "The `pass` JSON field and `by_pass` stay as aliases of `release_target` and `by_release_target`."
)


def _release_target_flags(sp: argparse.ArgumentParser, *, help_: str) -> None:
  """`--release-target`, plus `--pass` as its deprecated alias, both into `pass_key`."""
  sp.add_argument("--release-target", dest="pass_key", help=help_)
  sp.add_argument("--pass", dest="pass_key", action=_PassAlias, help=argparse.SUPPRESS)


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
