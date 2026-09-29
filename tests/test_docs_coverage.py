"""The README command table covers the real parser, so a new command or flag
cannot ship undocumented (S127)."""

from __future__ import annotations

import argparse
import re
import unittest
from pathlib import Path

import support  # noqa: F401 -- puts src/ on sys.path
from slicer import cli

README = Path(__file__).resolve().parents[1] / "README.md"
# Flags every command shares, documented once rather than per row.
COMMON = {"--help", "--root", "--json", "--lean", "--render", "--strict"}


def _subcommands() -> dict[str, argparse.ArgumentParser]:
  parser = cli.build_parser()
  action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
  return dict(action.choices)


def _rows() -> list[str]:
  """Each command-table row, first cell and description joined."""
  return [line for line in README.read_text(encoding="utf-8").splitlines() if line.startswith("| `")]


def _rows_for(name: str) -> list[str]:
  # A row names a command at the start of a backtick span, alone or after a `/`
  # in a combined row such as `done ID` / `park ID`.
  pattern = re.compile(rf"`{re.escape(name)}(?:[ `]|$)")
  return [row for row in _rows() if pattern.search(row.split(" | ")[0])]


class ReadmeCommandTableTests(unittest.TestCase):
  def test_EverySubcommand_HasACommandTableRow(self) -> None:
    missing = sorted(name for name in _subcommands() if not _rows_for(name))
    self.assertEqual(missing, [], "add a README command-table row for each")

  def test_EveryFlag_IsNamedInItsCommandsRow(self) -> None:
    missing = {}
    for name, sub in _subcommands().items():
      flags = {
        o for a in sub._actions if a.help is not argparse.SUPPRESS
        for o in a.option_strings if o.startswith("--")
      } - COMMON  # a suppressed flag (like `import --from`) only redirects
      text = " ".join(_rows_for(name))
      gone = sorted(flag for flag in flags if flag not in text)
      if gone:
        missing[name] = gone
    self.assertEqual(missing, {}, "name each flag in its README command-table row")


if __name__ == "__main__":
  unittest.main()
