"""Read an existing markdown slice tree and prove we understood it.

`parse_*` and `emit_*` are inverses. Import runs `emit(parse(text)) == text`
on every file it reads and refuses to write anything if one disagrees, so a
document slicer cannot reproduce is never silently half-imported.

The emitters exist only for that proof, for `--dry-run`, and for the tests.
They are not a command: `slicer render` produces slicer's own markdown, which
is deliberately not byte-identical to the legacy shape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from slicer.errors import LegacyImportError

MIDDOT = "·"
EMDASH = "—"

TABLE_HEADER = "| # | Slice | Size | Trees | Findings | Status |"
TABLE_SEP = "|---|---|---|---|---|---|"

H1_RE = re.compile(r"^# (?P<id>\S+) " + EMDASH + r" (?P<title>.+)$")
PASS_RE = re.compile(r"^# .*?pass (?P<key>\S+?)[ )]", re.I)
ITEM_ROW_RE = re.compile(r"^\[(?P<id>[^\]]+)\]\((?P<link>[^)]+)\)$")
SIZE_CELL_RE = re.compile(r"^(?P<size>[A-Z]+)(?:\s+`\[(?P<flag>[^\]]+)\]`)?$")

# The middot also occurs *inside* a field value ("`D20` · ROADMAP 6"), so the
# meta line is anchored on its bold keys and never split on the separator.
META_RE = re.compile(
  r"^\*\*Findings:\*\*(?P<findings>.*?)"
  r"\s*" + MIDDOT + r"\s*\*\*Size:\s*(?P<size>[A-Z]+)\*\*(?P<flags>.*?)"
  r"\s*" + MIDDOT + r"\s*\*\*(?P<treekey>Trees?):\*\*(?P<trees>.*)$"
)
FLAG_RE = re.compile(r"`\[([^\]]+)\]`")


@dataclass
class LegacyRow:
  """One table line: a slice row, or a phase-group label row."""

  kind: str
  cells: list[str]

  @property
  def item_id(self) -> str:
    m = ITEM_ROW_RE.match(self.cells[0])
    return m.group("id") if m else ""

  @property
  def link(self) -> str:
    m = ITEM_ROW_RE.match(self.cells[0])
    return m.group("link") if m else ""

  def emit(self) -> str:
    # Empty cells render as "| |", never "|  |" — the source spells them that way.
    return "|" + "".join(f" {c} |" if c else " |" for c in self.cells)


@dataclass
class LegacyTable:
  rows: list[LegacyRow] = field(default_factory=list)

  def emit(self) -> list[str]:
    return [TABLE_HEADER, TABLE_SEP] + [r.emit() for r in self.rows]


@dataclass
class LegacyIndex:
  """The index as an ordered run of prose chunks and tables.

  Keeping prose as opaque chunks is what makes the round trip exact and what
  guarantees the hand-written sections (Baseline, Dependencies, Size, the
  "If you only get through five" notes) survive import untouched.
  """

  chunks: list[tuple[str, object]] = field(default_factory=list)

  def tables(self) -> list[LegacyTable]:
    return [c for kind, c in self.chunks if kind == "table"]

  def rows(self) -> list[LegacyRow]:
    return [r for t in self.tables() for r in t.rows]

  def emit(self) -> str:
    out: list[str] = []
    for kind, chunk in self.chunks:
      if kind == "table":
        out.extend(chunk.emit())
      else:
        out.extend(chunk)
    return "\n".join(out)


@dataclass
class LegacySlice:
  """One slice file. Sections keep their order and their bodies verbatim."""

  id: str
  title: str
  lead: list[str] = field(default_factory=list)
  meta: str = ""
  depends: str = ""
  notes: list[str] = field(default_factory=list)
  sections: list[tuple[str, str]] = field(default_factory=list)

  def emit(self) -> str:
    meta_block = self.meta + ("\n" + self.depends if self.depends else "")
    parts = [f"# {self.id} {EMDASH} {self.title}"]
    parts.extend(self.lead)
    if meta_block:
      parts.append(meta_block)
    parts.extend(self.notes)
    parts.extend(f"## {h}\n\n{b}" if b else f"## {h}" for h, b in self.sections)
    return "\n\n".join(parts) + "\n"


def _blocks(lines: list[str]) -> list[str]:
  """Group lines into blank-line-separated blocks, dropping the blanks."""
  out: list[str] = []
  current: list[str] = []
  for line in lines:
    if line.strip() == "":
      if current:
        out.append("\n".join(current))
        current = []
    else:
      current.append(line)
  if current:
    out.append("\n".join(current))
  return out


def parse_slice(text: str, *, path: str) -> LegacySlice:
  if "\r" in text:
    raise LegacyImportError(f"{path}: CRLF line endings; convert to LF first")
  if not text.endswith("\n"):
    raise LegacyImportError(f"{path}: no final newline")
  lines = text[:-1].split("\n")
  m = H1_RE.match(lines[0]) if lines else None
  if m is None:
    raise LegacyImportError(f"{path}: first line is not '# <id> {EMDASH} <title>'")

  first_section = next((i for i, l in enumerate(lines) if l.startswith("## ")), len(lines))
  head_blocks = _blocks(lines[1:first_section])
  meta_index = [i for i, b in enumerate(head_blocks) if b.startswith("**Findings:**")]
  if len(meta_index) != 1:
    raise LegacyImportError(
      f"{path}: expected exactly one '**Findings:**' block, found {len(meta_index)}"
    )
  at = meta_index[0]
  meta_lines = head_blocks[at].split("\n")
  depends = ""
  if len(meta_lines) > 1 and meta_lines[1].startswith("**Depends on:**"):
    depends = meta_lines[1]
  if len(meta_lines) > (2 if depends else 1):
    raise LegacyImportError(f"{path}: unexpected extra lines in the '**Findings:**' block")

  sections: list[tuple[str, str]] = []
  i = first_section
  while i < len(lines):
    heading = lines[i][3:]
    j = i + 1
    while j < len(lines) and not lines[j].startswith("## "):
      j += 1
    sections.append((heading, "\n".join(lines[i + 1 : j]).strip("\n")))
    i = j

  return LegacySlice(
    id=m.group("id"),
    title=m.group("title"),
    lead=head_blocks[:at],
    meta=meta_lines[0],
    depends=depends,
    notes=head_blocks[at + 1 :],
    sections=sections,
  )


def parse_index(text: str, *, path: str) -> LegacyIndex:
  if "\r" in text:
    raise LegacyImportError(f"{path}: CRLF line endings; convert to LF first")
  lines = text.split("\n")
  chunks: list[tuple[str, object]] = []
  prose: list[str] = []
  i = 0
  while i < len(lines):
    if lines[i] == TABLE_HEADER and i + 1 < len(lines) and lines[i + 1] == TABLE_SEP:
      if prose:
        chunks.append(("md", prose))
        prose = []
      table = LegacyTable()
      i += 2
      while i < len(lines) and lines[i].startswith("|"):
        table.rows.append(_parse_row(lines[i], path=path, line_no=i + 1))
        i += 1
      chunks.append(("table", table))
      continue
    prose.append(lines[i])
    i += 1
  if prose:
    chunks.append(("md", prose))
  return LegacyIndex(chunks=chunks)


def _parse_row(line: str, *, path: str, line_no: int) -> LegacyRow:
  inner = line.strip()
  if not (inner.startswith("|") and inner.endswith("|")):
    raise LegacyImportError(f"{path}:{line_no}: table row is not pipe-delimited")
  cells = [c.strip() for c in inner[1:-1].split("|")]
  if len(cells) != 6:
    raise LegacyImportError(
      f"{path}:{line_no}: expected 6 cells, found {len(cells)} (a cell may contain a '|')"
    )
  kind = "item" if ITEM_ROW_RE.match(cells[0]) else "group"
  return LegacyRow(kind=kind, cells=cells)


def parse_meta(meta: str, *, path: str) -> dict[str, object]:
  """Split the bold metadata line into findings / size / flags / trees."""
  m = META_RE.match(meta)
  if m is None:
    raise LegacyImportError(f"{path}: cannot parse metadata line: {meta!r}")
  trees_raw = m.group("trees").strip()
  return {
    "findings": m.group("findings").strip(),
    "size": m.group("size"),
    "flags": FLAG_RE.findall(m.group("flags")),
    "trees_plural": m.group("treekey") == "Trees",
    "trees": trees_raw,
  }


def roundtrip_slice(text: str, *, path: str) -> LegacySlice:
  """Parse, then refuse to return unless re-emitting reproduces the input."""
  parsed = parse_slice(text, path=path)
  again = parsed.emit()
  if again != text:
    raise LegacyImportError(f"{path}: does not round-trip; slicer would lose content\n{_diff(text, again)}")
  return parsed


def roundtrip_index(text: str, *, path: str) -> LegacyIndex:
  parsed = parse_index(text, path=path)
  again = parsed.emit()
  if again != text:
    raise LegacyImportError(f"{path}: does not round-trip; slicer would lose content\n{_diff(text, again)}")
  return parsed


def _diff(want: str, got: str, limit: int = 20) -> str:
  import difflib

  lines = list(difflib.unified_diff(want.splitlines(), got.splitlines(), "original", "re-emitted", lineterm=""))
  return "\n".join(lines[:limit])


def read_tree(source: Path, done_dir: str = "done") -> tuple[LegacyIndex, dict[str, LegacySlice], dict[str, bool]]:
  """Parse an index plus every slice file beside and below it.

  Returns the index, slices by id, and whether each slice sat in `done/`.
  """
  index_path = source / "README.md"
  if not index_path.exists():
    raise LegacyImportError(f"{index_path}: no index found")
  index = roundtrip_index(index_path.read_text(encoding="utf-8"), path=str(index_path))

  slices: dict[str, LegacySlice] = {}
  located: dict[str, bool] = {}
  for is_done, folder in ((False, source), (True, source / done_dir)):
    if not folder.is_dir():
      continue
    for md in sorted(folder.glob("*.md")):
      if md.name == "README.md":
        continue
      parsed = roundtrip_slice(md.read_text(encoding="utf-8"), path=str(md))
      if parsed.id in slices:
        raise LegacyImportError(f"{md}: duplicate slice id {parsed.id}")
      slices[parsed.id] = parsed
      located[parsed.id] = is_done
  return index, slices, located
