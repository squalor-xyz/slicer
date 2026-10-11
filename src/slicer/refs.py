"""Find `path:line` references in slice text and say whether they still hold.

Slice specs cite code as `path:N` or `path:A-B`. Each landing moves the lines, so
the numbers rot and every reader re-greps. A reference is checkable when a
backticked identifier stands next to it on the same line, for example
"`OpenAsync` (`Open.cs:60`)": if the cited line still names that identifier the
reference is fine, and if not, a search of the file can usually say where it went.
A reference inside a fenced code block, or on a line marked `<!-- example -->`, is
an example and is not checked.

`find` is the pure half, text in and references out, like `outline.py`. `Checker`
is the half that reads the project's files. Neither mutates anything: rewriting a
slice goes through `ops.fix_refs`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from slicer.model import Item, Slice

OK = "ok"
UNANCHORED = "unanchored"
MISSING_FILE = "missing_file"
AMBIGUOUS_PATH = "ambiguous_path"
OUT_OF_RANGE = "out_of_range"
MOVED = "moved"
AMBIGUOUS_SYMBOL = "ambiguous_symbol"
SYMBOL_GONE = "symbol_gone"

# Fixed order: it is the key order of the report's `counts`.
STATUSES = (
  OK, UNANCHORED, MISSING_FILE, AMBIGUOUS_PATH, OUT_OF_RANGE, MOVED,
  AMBIGUOUS_SYMBOL, SYMBOL_GONE,
)
# A reference in one of these needs no attention. Everything else is drift.
CLEAN = frozenset({OK, UNANCHORED})

_PATH = r"[\w.+@-]+(?:/[\w.+@-]+)*"
# The path must not continue to the left of the match (`/abs/x.py:4`, a URL's
# host, `v1.x.py:3`), and the numbers must not run into a word or into a second
# `:NN` (`config.yaml:12:30` is a line and column, not a line reference).
_REF = re.compile(
  r"(?<![\w.+@/:~-])(?P<path>" + _PATH + r"):(?P<tilde>~)?(?P<start>\d+)(?:-(?P<end>\d+))?(?!\w|:\d)"
)
# A path needs a real extension: it keeps `12:30` and `v1.2:3` out.
_EXTENSION = re.compile(r"\.[A-Za-z0-9]*[A-Za-z][A-Za-z0-9]*$")
_SPAN = re.compile(r"`([^`\n]+)`")
# A fence opens a code block: three or more backticks or tildes, up to three
# spaces in. A backtick run's info string holds no backtick, so "```x``` is
# inline" is a code span and not a fence. A fence closes on a line of the same
# character, at least as long as the opener, with no text after the run.
_FENCE = re.compile(r"^ {0,3}(`{3,}(?=[^`]*$)|~{3,})")
_CLOSE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")
# A line carrying this HTML comment is an example, not a reference.
_EXAMPLE = "<!-- example -->"
_IDENTIFIER = re.compile(r"^[A-Za-z_]\w*(?:(?:\.|::|#)[A-Za-z_]\w*)*(?:\(\))?$")
_COMPONENT = re.compile(r"\.|::|#")
# A dotted span ending in one of these is a file name, not a qualified symbol,
# and a bare multi-dot name must end in one of these to be a path.
_FILE_EXTENSIONS = frozenset(
  "c cc cpp cs css csv go h hpp html java js json jsx kt md php py rb rs sh sql swift toml "
  "ts tsx txt xml yaml yml".split()
)


@dataclass(frozen=True)
class Ref:
  """One `path:N` or `path:A-B` found in a text.

  `begin` and `stop` bound the numbers (`~60-75`) in that text, so a rewrite
  changes only them and leaves the path as the author spelled it.
  """

  path: str
  start: int
  end: int | None
  approx: bool
  anchor: str | None
  line: int
  begin: int
  stop: int

  @property
  def symbol(self) -> str | None:
    """What to search for: the last component of a qualified anchor."""
    if self.anchor is None:
      return None
    return _COMPONENT.split(self.anchor.removesuffix("()"))[-1]

  def numbers(self, start: int | None = None) -> str:
    """The numbers as written, or moved so the range starts at `start`."""
    first = self.start if start is None else start
    text = ("~" if self.approx else "") + str(first)
    if self.end is not None:
      text += f"-{self.end + first - self.start}"
    return text

  def written(self, start: int | None = None) -> str:
    return f"{self.path}:{self.numbers(start)}"


def _identifier_span(span: str) -> bool:
  if not _IDENTIFIER.match(span):
    return False
  last = _COMPONENT.split(span)[-1]
  return not ("." in span and last in _FILE_EXTENSIONS)


def _is_path(path: str) -> bool:
  """Whether `path` can name a file. It needs a real extension, and a bare name
  with several dots (`api.example.com`, `DataBall.Open.cs`) needs a known file
  extension, which keeps `host.name:port` out while `Foo.Bar.cs:60` stays in."""
  name = path.rsplit("/", 1)[-1]
  if not _EXTENSION.search(name):
    return False
  return "/" in path or name.count(".") < 2 or name.rsplit(".", 1)[-1].lower() in _FILE_EXTENSIONS


def find(text: str) -> list[Ref]:
  """Every reference in `text`, in order, with the anchor each one has.

  The anchor is the nearest backticked identifier before the reference on the
  same line that the reference is the first to follow: an identifier sits
  between the previous reference on the line (or the line start) and this one.
  So "`Box` (`a.py:1`) and `b.py:9`" anchors only `a.py:1`, and `b.py:9` has no
  anchor. A reference written inside backticks never anchors itself: its span
  holds a `:` and so is not an identifier.

  An example is not a reference, so it is not returned. A fenced code block is
  skipped whole. Its opening line is a run of three or more backticks or tildes
  (up to three spaces of indent; a backtick run's info string holds no backtick).
  Its closing line is the same character, at least as long, with no text after
  the run. A fence that never closes runs to the end of `text`. Each line
  containing `<!-- example -->` is skipped too. Skipped lines still advance
  `line`, `begin` and `stop`.
  """
  found: list[Ref] = []
  offset = 0
  fence = ""
  for number, line in enumerate(text.split("\n"), start=1):
    line_start = offset
    offset += len(line) + 1
    opening = _FENCE.match(line)
    if fence:
      closing = _CLOSE.match(line)
      if closing and closing.group(1)[0] == fence[0] and len(closing.group(1)) >= len(fence):
        fence = ""
      continue
    if opening:
      fence = opening.group(1)
      continue
    if _EXAMPLE in line:
      continue
    spans = [m for m in _SPAN.finditer(line) if _identifier_span(m.group(1))]
    previous = 0
    for match in _REF.finditer(line):
      path = match.group("path")
      if not _is_path(path):
        continue
      anchor = None
      for span in spans:
        if previous <= span.start() and span.end() <= match.start():
          anchor = span.group(1)
      previous = match.end()
      found.append(Ref(
        path=path,
        start=int(match.group("start")),
        end=int(match.group("end")) if match.group("end") else None,
        approx=bool(match.group("tilde")),
        anchor=anchor,
        line=number,
        begin=line_start + (match.start("tilde") if match.group("tilde") else match.start("start")),
        stop=line_start + match.end(),
      ))
  return found


@dataclass
class Finding:
  """What a check concluded about one reference."""

  status: str
  file: str | None = None
  found: int | None = None
  paths: list[str] = field(default_factory=list)
  lines: list[int] = field(default_factory=list)


class Checker:
  """Resolves references against the files under a project root.

  Files and the basename index are read once per instance, so a scan of many
  slices pays for each file and for the walk only once.
  """

  def __init__(self, root: Path) -> None:
    self.root = root.resolve()
    self._basenames: dict[str, list[str]] | None = None
    self._files: dict[str, list[str] | None] = {}

  def _walk(self) -> dict[str, list[str]]:
    """Basename -> root-relative paths. Skips what `store.discover_all` skips:
    dot-directories, `node_modules` and symlinks."""
    index: dict[str, list[str]] = {}
    for here, dirnames, filenames in os.walk(self.root, followlinks=False):
      dirnames[:] = sorted(n for n in dirnames if not n.startswith(".") and n != "node_modules")
      for name in sorted(filenames):
        full = Path(here) / name
        if full.is_symlink():
          continue
        index.setdefault(name, []).append(full.relative_to(self.root).as_posix())
    return index

  def resolve(self, path: str) -> tuple[str | None, list[str]]:
    """The file a path names: relative to the root, else by unique basename.

    Returns `(file, candidates)`. `file` is None when nothing matched or
    several did; `candidates` then lists the several.
    """
    full = (self.root / path).resolve()
    try:
      relative = full.relative_to(self.root).as_posix()
    except ValueError:
      return None, []
    if full.is_file():
      return relative, []
    if "/" in path:
      return None, []
    if self._basenames is None:
      self._basenames = self._walk()
    matches = self._basenames.get(path, [])
    if len(matches) == 1:
      return matches[0], []
    return None, list(matches) if len(matches) > 1 else []

  def _lines(self, file: str) -> list[str] | None:
    if file not in self._files:
      try:
        text = (self.root / file).read_bytes().decode("utf-8", errors="replace")
      except OSError:
        self._files[file] = None
      else:
        lines = text.split("\n")
        if lines and lines[-1] == "":
          lines.pop()
        self._files[file] = [line.removesuffix("\r") for line in lines]
    return self._files[file]

  def check(self, ref: Ref) -> Finding:
    file, candidates = self.resolve(ref.path)
    if candidates:
      return Finding(AMBIGUOUS_PATH, paths=candidates)
    lines = self._lines(file) if file is not None else None
    if file is None or lines is None:
      return Finding(MISSING_FILE)
    last = ref.start if ref.end is None else ref.end
    if ref.start < 1 or last < ref.start or last > len(lines):
      return Finding(OUT_OF_RANGE, file=file)
    symbol = ref.symbol
    if symbol is None:
      return Finding(UNANCHORED, file=file)
    word = re.compile(r"(?<!\w)" + re.escape(symbol) + r"(?!\w)")
    hits = [number for number, line in enumerate(lines, start=1) if word.search(line)]
    if any(ref.start <= hit <= last for hit in hits):
      return Finding(OK, file=file)
    if not hits:
      return Finding(SYMBOL_GONE, file=file)
    nearest = min(abs(hit - ref.start) for hit in hits)
    closest = [hit for hit in hits if abs(hit - ref.start) == nearest]
    if len(closest) == 1:
      if ref.end is not None and ref.end + closest[0] - ref.start > len(lines):
        return Finding(OUT_OF_RANGE, file=file)  # the range cannot follow its anchor
      return Finding(MOVED, file=file, found=closest[0])
    return Finding(AMBIGUOUS_SYMBOL, file=file, lines=hits)


@dataclass(frozen=True)
class Place:
  """One piece of an item's text that is scanned: a lead paragraph, the
  boundary, a section body or a note. `index` is the paragraph, section or
  note position; `base` is the line before its first line within `where`."""

  kind: str
  where: str
  index: int
  text: str
  base: int = 0

  @property
  def rewritable(self) -> bool:
    return self.kind != "note"


def places(item: Item, sl: Slice | None) -> list[Place]:
  """Where an item keeps text that may cite code, in reading order."""
  found: list[Place] = []
  if sl is not None:
    base = 0
    for at, paragraph in enumerate(sl.lead):
      found.append(Place("lead", "lead", at, paragraph, base))
      base += paragraph.count("\n") + 2
    if sl.boundary:
      found.append(Place("boundary", "boundary", 0, sl.boundary))
    for at, section in enumerate(sl.sections):
      found.append(Place("section", f"section {section.heading}", at, section.body))
  for at, record in enumerate(item.note_records):
    found.append(Place("note", f"note {at + 1}", at, record.text))
  return found


@dataclass
class Located:
  """A reference, where it was found and what the check said."""

  item: str
  place: Place
  ref: Ref
  finding: Finding
  fixed: bool = False

  @property
  def drift(self) -> bool:
    return self.finding.status not in CLEAN and not self.fixed

  def to_dict(self) -> dict[str, Any]:
    finding = self.finding
    return {
      "id": self.item,
      "where": self.place.where,
      "line": self.place.base + self.ref.line,
      "ref": self.ref.written(),
      "path": self.ref.path,
      "file": finding.file,
      "start": self.ref.start,
      "end": self.ref.end,
      "approx": self.ref.approx,
      "anchor": self.ref.anchor,
      "status": finding.status,
      "found": finding.found,
      "to": self.ref.written(finding.found) if finding.found is not None else None,
      "paths": list(finding.paths),
      "lines": list(finding.lines),
      "fixed": self.fixed,
    }


def scan(item: Item, sl: Slice | None, checker: Checker) -> list[Located]:
  """Every reference in one item's text, checked."""
  return [
    Located(item.id, place, ref, checker.check(ref))
    for place in places(item, sl)
    for ref in find(place.text)
  ]


def rewrite(text: str, moved: list[Located]) -> str:
  """`text` with each moved reference's numbers replaced, nothing else touched."""
  for loc in sorted(moved, key=lambda l: l.ref.begin, reverse=True):
    text = text[:loc.ref.begin] + loc.ref.numbers(loc.finding.found) + text[loc.ref.stop:]
  return text
