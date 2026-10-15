"""The command tables cover the real parser, so a new command or flag cannot
ship undocumented (S127). The reference in docs/commands.md names every flag;
the README's summary table names every command (S142). The README links the
why-slicer page, and that page's relative links resolve (S269). Each doc's sentence
that lists the commands without --json (agents.md, the README, commands.md and
import.md) names exactly those the parser builds without it (S272)."""

from __future__ import annotations

import argparse
import re
import unittest
from pathlib import Path

import support  # noqa: F401 -- puts src/ on sys.path
from slicer import cli

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
REFERENCE = ROOT / "docs" / "commands.md"
# Flags every command shares, documented once rather than per row.
COMMON = {"--help", "--root", "--json", "--lean", "--render", "--strict"}
WHY = ROOT / "docs" / "why-slicer.md"
WHY_LINK = "](docs/why-slicer.md)"
MARKDOWN_LINK = re.compile(r"\]\(([^)\s]+)\)")
AGENTS_DOC = ROOT / "docs" / "agents.md"
IMPORT_DOC = ROOT / "docs" / "import.md"
# Each doc's sentence that names the commands without --json, in its own wording.
JSON_DOCS = (AGENTS_DOC, README, REFERENCE, IMPORT_DOC)
JSON_SENTENCE = re.compile(r"Every (?:sub)?command except (.+?) (?:accepts|takes) `--json`")


def _subcommands() -> dict[str, argparse.ArgumentParser]:
  parser = cli.build_parser()
  action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
  return dict(action.choices)


def _rows(doc: Path) -> list[str]:
  """Each command-table row, first cell and description joined."""
  return [line for line in doc.read_text(encoding="utf-8").splitlines() if line.startswith("| `")]


def _rows_for(name: str, doc: Path = REFERENCE) -> list[str]:
  # A row names a command at the start of a backtick span, alone or after a `/`
  # in a combined row such as `done ID` / `park ID`.
  pattern = re.compile(rf"`{re.escape(name)}(?:[ `]|$)")
  return [row for row in _rows(doc) if pattern.search(row.split(" | ")[0])]


class CommandTableTests(unittest.TestCase):
  def test_EverySubcommand_HasACommandTableRow(self) -> None:
    for doc in (REFERENCE, README):
      with self.subTest(doc=doc.name):
        missing = sorted(name for name in _subcommands() if not _rows_for(name, doc))
        self.assertEqual(missing, [], f"add a {doc.name} command-table row for each")

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
    self.assertEqual(missing, {}, "name each flag in its docs/commands.md command-table row")


def _section(lines: list[str], heading: str) -> list[str]:
  """The lines under one `## ` heading, up to the next one."""
  start = lines.index(heading) + 1
  end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
  return lines[start:end]


class WhySlicerTests(unittest.TestCase):
  def test_Readme_LinksWhySlicer_InIntroAndDocumentationList(self) -> None:
    lines = README.read_text(encoding="utf-8").splitlines()
    intro = lines[: lines.index("## Install")]
    documented = _section(lines, "## Documentation")
    self.assertEqual(sum(WHY_LINK in line for line in intro), 1,
                     "link docs/why-slicer.md once in the README intro, before ## Install")
    self.assertEqual(sum(WHY_LINK in line for line in documented), 1,
                     "list docs/why-slicer.md once under ## Documentation")

  def test_WhySlicer_RelativeLinks_Resolve(self) -> None:
    self.assertTrue(WHY.is_file(), "write docs/why-slicer.md")
    targets = [m.group(1) for m in MARKDOWN_LINK.finditer(WHY.read_text(encoding="utf-8"))]
    relative = [t.split("#", 1)[0] for t in targets if not t.startswith(("http://", "https://"))]
    relative = [path for path in relative if path]
    self.assertTrue(relative, "docs/why-slicer.md links to the docs it describes")
    missing = [path for path in relative if not (WHY.parent / path).exists()]
    self.assertEqual(missing, [], "each relative link in docs/why-slicer.md must exist")


def _json_less_commands() -> list[str]:
  """Each leaf command the real parser builds with no --json option, by its words.
  A group such as `prose` has no run of its own, so its leaves are the ones checked."""
  found: list[str] = []

  def walk(parser: argparse.ArgumentParser, words: tuple[str, ...]) -> None:
    groups = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    if not groups:
      if not any("--json" in a.option_strings for a in parser._actions):
        found.append(" ".join(words))
      return
    for name, sub in groups[0].choices.items():
      walk(sub, (*words, name))

  walk(cli.build_parser(), ())
  return sorted(found)


class AgentsDocTests(unittest.TestCase):
  def test_AgentsDoc_JsonExceptions_MatchParser(self) -> None:
    parser_set = _json_less_commands()
    for doc in JSON_DOCS:
      with self.subTest(doc=doc.relative_to(ROOT).as_posix()):
        text = " ".join(doc.read_text(encoding="utf-8").split())
        matches = JSON_SENTENCE.findall(text)
        self.assertEqual(len(matches), 1, "keep one 'Every command except ... takes `--json`' sentence")
        named = sorted(re.findall(r"`([^`]+)`", matches[0]))
        self.assertEqual(named, parser_set,
                         "name exactly the commands the parser builds without --json")


if __name__ == "__main__":
  unittest.main()
