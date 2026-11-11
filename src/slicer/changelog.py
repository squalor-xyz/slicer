"""Changelog entries kept one file per slice, and the one layout they are assembled into.

Parallel slice branches that each add a line under `Unreleased` in a shared changelog
conflict at nearly every landing. Here a slice writes its own file (`landed-changed/ID.md`)
and nothing shared changes until `slicer changelog assemble` writes them all into the
Unreleased section at release. This module is the pure half: text in, dataclasses and text
out, no I/O. Reading and writing the files is `ops`.

An entry file is `category: NAME`, one blank line, then the entry text in Markdown with no
leading bullet. A project that configures no categories writes the text alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from slicer.errors import ChangelogError

ENTRY_SUFFIX = ".md"
CATEGORY_PREFIX = "category: "

# What `import` names an entry whose bullet ends with no known item id.
_IMPORTED_NAME = re.compile(r"entry-\d+")
# A bullet that ends with its item id in parentheses, as `CHANGELOG.md` writes them, with
# at most one full stop after the closing parenthesis (`... (s454).`).
_ID_TAIL = re.compile(r"\(([A-Za-z0-9][A-Za-z0-9._-]*)\)\.?\s*$")
_BULLET = re.compile(r"[-*+] ")
_BULLET_LINE = re.compile(r"[-*] (.*)")
_CATEGORY_HEADING = re.compile(r"###\s+(.+?)\s*")


@dataclass
class Entry:
  """One changelog entry: the file stem it lives under, its category and its text.

  `category` is empty for a project that configures none. `name` is an item id, or
  `entry-NNN` for an entry `import` could not tie to an item; from `split_unreleased` it is
  the id the bullet ends with in parentheses, or empty, and `ops` decides the real name.
  """

  name: str
  category: str
  text: str

  def to_dict(self) -> dict[str, str]:
    return {"name": self.name, "category": self.category, "text": self.text}

  @staticmethod
  def from_dict(d: dict[str, str]) -> "Entry":
    return Entry(name=d["name"], category=d["category"], text=d["text"])


def is_imported_name(name: str, item_ids: frozenset[str] | set[str] = frozenset()) -> bool:
  """True for the `entry-NNN` stem `import` gives a bullet that names no item.

  A stem that is an item id (`entry-001` is a legal id) is that item's entry, not an
  imported one, so `item_ids` takes precedence.
  """
  return name not in item_ids and bool(_IMPORTED_NAME.fullmatch(name))


def id_hint(text: str) -> str:
  """The id an entry's text ends with in parentheses (one full stop may follow), or empty."""
  found = _ID_TAIL.search(text)
  return found.group(1) if found else ""


def format_entry(category: str, text: str) -> str:
  """The bytes of an entry file."""
  head = f"{CATEGORY_PREFIX}{category}\n\n" if category else ""
  return f"{head}{text}\n"


def _fail(where: str, line: int, message: str) -> ChangelogError:
  return ChangelogError(f"{where}:{line}: {message}")


def parse_entry(name: str, text: str, categories: list[str], *, where: str = "") -> Entry:
  """Read an entry file, or raise `ChangelogError` naming the file and the line.

  `where` is how the error names the file; it defaults to the stem and `.md`.
  """
  where = where or f"{name}{ENTRY_SUFFIX}"
  lines = text.replace("\r\n", "\n").split("\n")
  category = ""
  first = 0
  if categories:
    names = ", ".join(categories)
    if not lines[0].startswith(CATEGORY_PREFIX) or lines[0][len(CATEGORY_PREFIX):] not in categories:
      raise _fail(where, 1, f"the first line must be `category: NAME` with NAME one of {names}")
    category = lines[0][len(CATEGORY_PREFIX):]
    if len(lines) < 2 or lines[1] != "":
      raise _fail(where, 2, "expected one blank line after the category line, then the entry text")
    first = 2
  elif lines[0].startswith(CATEGORY_PREFIX.rstrip()):
    raise _fail(where, 1, "this project configures no changelog_categories, so there is no "
                "category line; write the entry text alone")
  body = lines[first:]
  while body and not body[-1].strip():
    body.pop()
  if not body:
    raise _fail(where, first + 1, "no entry text")
  if not body[0].strip():
    raise _fail(where, first + 1, "expected exactly one blank line before the entry text")
  if _BULLET.match(body[0]):
    raise _fail(where, first + 1, "the entry text starts with a bullet marker; "
                "write the text without a leading `- `")
  return Entry(name=name, category=category, text="\n".join(body))


def _natural(name: str) -> list[tuple[int, int, str]]:
  """Sort key that puts `s9` before `s10`: digit runs compare as numbers."""
  return [
    (1, int(part), "") if part.isdigit() else (0, 0, part.casefold())
    for part in re.split(r"(\d+)", name) if part
  ]


def ordered(
  entries: list[Entry], categories: list[str], item_ids: frozenset[str] | set[str] = frozenset(),
) -> list[Entry]:
  """Entries in the order they are written: category order, then highest item id first.

  An imported `entry-NNN` follows the entries named for an item, in its own number order;
  a stem in `item_ids` is an item's entry whatever it looks like.
  An entry whose category is not configured is an error; `ops` refuses one before this.
  """
  unknown = sorted({e.category for e in entries if e.category not in (categories or [""])})
  if unknown:
    raise ChangelogError(f"entries use categories that are not configured: {', '.join(unknown)}")
  out: list[Entry] = []
  for category in categories or [""]:
    group = [e for e in entries if e.category == category]
    named = sorted((e for e in group if not is_imported_name(e.name, item_ids)),
                   key=lambda e: _natural(e.name), reverse=True)
    imported = sorted((e for e in group if is_imported_name(e.name, item_ids)),
                      key=lambda e: _natural(e.name))
    out.extend(named + imported)
  return out


def _bullet(text: str) -> list[str]:
  first, *rest = text.split("\n")
  return [f"- {first}"] + [f"  {line}" if line else "" for line in rest]


def format_unreleased(
  entries: list[Entry], categories: list[str], item_ids: frozenset[str] | set[str] = frozenset(),
) -> str:
  """The Unreleased body for `entries`: subsections in category order, bullets joined.

  Empty for no entries. Otherwise the text starts with the blank line that follows the
  heading and ends with the line break after the last bullet, so `split("\\n")` gives the
  lines between the heading and the next section, the blank line before it included.
  """
  if not entries:
    return ""
  lines: list[str] = []
  for entry in ordered(entries, categories, item_ids):
    if categories and (not lines or entry.category != last):
      lines += ["", f"### {entry.category}", ""]
    elif not categories and not lines:
      lines.append("")
    last = entry.category
    lines += _bullet(entry.text)
  return "\n".join(lines) + "\n"


def split_unreleased(
  body: str, categories: list[str], *, first_line: int = 1,
) -> tuple[list[Entry], list[str]]:
  """Split an Unreleased body into entries, for `import`; with every problem, as `LINE: message`.

  A `### NAME` line sets the category, which must be a configured one; a line starting
  `- ` or `* ` begins an entry; an indented line, or an unindented one straight after a
  bullet, continues it, and a blank line inside an entry stays when an indented line
  follows. Bullets may or may not be separated by blank lines. Anything else is a problem.
  `first_line` is the file line of the body's first line, so the numbers name the file.
  """
  entries: list[Entry] = []
  problems: list[str] = []
  names = ", ".join(categories)
  category: str | None = ""  # None: under a heading already reported
  current: list[str] | None = None
  current_category = ""
  current_line = 0
  blanks = 0

  def flush() -> None:
    nonlocal current
    if current is None:
      return
    text = "\n".join(current).rstrip()
    if not text:
      problems.append(f"{current_line}: an empty bullet")
    else:
      entries.append(Entry(id_hint(text), current_category, text))
    current = None

  for offset, line in enumerate(body.split("\n")):
    number = first_line + offset
    if not line.strip():
      blanks += 1
      continue
    if line.startswith("#"):
      flush()
      blanks = 0
      heading = _CATEGORY_HEADING.fullmatch(line)
      if not categories:
        problems.append(f"{number}: {line.strip()!r} is a subsection heading, but this project "
                        "configures no changelog_categories")
        category = None
      elif heading is not None and heading.group(1) in categories:
        category = heading.group(1)
      else:
        problems.append(f"{number}: {line.strip()!r} is not a `### CATEGORY` heading for one of "
                        f"the configured categories: {names}")
        category = None
      continue
    bullet = _BULLET_LINE.fullmatch(line)
    if bullet is not None:
      flush()
      if categories and category == "":
        problems.append(f"{number}: a bullet before any `### CATEGORY` heading")
      current, current_category, current_line = [bullet.group(1)], category or "", number
      blanks = 0
      continue
    if current is not None and (line[0] in " \t" or blanks == 0):
      current.extend([""] * blanks)
      current.append(line[2:] if line.startswith("  ") else line.lstrip())
      blanks = 0
      continue
    flush()
    blanks = 0
    problems.append(f"{number}: {line.strip()!r} is not a bullet or a heading")
  flush()
  return entries, problems


def _span(lines: list[str], heading: str) -> tuple[int, int] | None:
  """The index of the heading line and of the line the section ends at, or None.

  The heading matches a whole line. The section runs to the next heading of the same or a
  higher level, or to the end of the text.
  """
  level = len(heading) - len(heading.lstrip("#"))
  stop = re.compile(r"#{1,%d}(\s|$)" % level)
  for at, line in enumerate(lines):
    if line.rstrip("\r") == heading:
      for end in range(at + 1, len(lines)):
        if stop.match(lines[end]):
          return at, end
      return at, len(lines)
  return None


def unreleased_body(text: str, heading: str) -> tuple[str, int] | None:
  """The text between the Unreleased heading and the next section, and its first file line."""
  lines = text.split("\n")
  span = _span(lines, heading)
  if span is None:
    return None
  at, end = span
  return "\n".join(lines[at + 1:end]), at + 2


def replace_unreleased(text: str, heading: str, body: str) -> str:
  """`text` with the Unreleased body replaced by `body` (from `format_unreleased`)."""
  lines = text.split("\n")
  span = _span(lines, heading)
  if span is None:
    raise ChangelogError(f"no line is exactly {heading!r}")
  at, end = span
  return "\n".join(lines[:at + 1] + body.split("\n") + lines[end:])


__all__ = [
  "ChangelogError", "Entry", "format_entry", "format_unreleased", "id_hint", "is_imported_name",
  "ordered", "parse_entry", "replace_unreleased", "split_unreleased", "unreleased_body",
]
