"""Convert an existing markdown slice tree into canonical JSON state.

This is the one-time migration behind `slicer migrate`, for a project that
was already running this workflow by hand. `slicer import` is the other
direction of entry: a fresh outline, not an existing tree.

Everything is verified before anything is written: every file must
round-trip through `legacy`, ids must be unique, and each slice's location
must agree with its recorded status. A failure aborts the whole migration
rather than leaving a half-converted tracking directory.

Prose that belongs to no slice — a baseline block, a dependency rationale,
a "not slices" list — is relocated verbatim, never interpreted.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from slicer import ids, jsonio, legacy, templates
from slicer.config import CONFIG_NAME, Config
from slicer.model import Index, Item, PassInfo, Section, Slice, extract_boundary
from slicer.store import DIR_NAME, INDEX_NAME, SLICES_DIR, TEMPLATES_DIR

PASS_KEY_RE = re.compile(r"pass\s+([^\s)(]+)", re.I)
H1_LINE_RE = re.compile(r"^# ", re.M)
H2_LINE_RE = re.compile(r"^## ", re.M)
COLLECTIVE_RE = re.compile(r"^all\b", re.I)
DEPENDS_RE = re.compile(r"^\*\*Depends on:\*\*\s*(?P<ids>[^(]*?)\s*(?P<note>\(.*\))?$")
LEAD_STATUS_RE = re.compile(r"^\*\*(?:(?P<bare>Parked)|Status:\s*(?P<named>\w+))\*\*")
# Relative markdown links only: leave URLs, bare anchors and absolute paths alone.
LINK_RE = re.compile(r"\]\((?!https?://|#|/)([^)\s]+)(\s+\"[^\"]*\")?\)")
# Fenced blocks and inline code spans, which must never be rewritten: slice
# prose quotes shell and regex that happen to contain `](...)`.
# An inline span may wrap across a line, so it is closed by a matching run of
# backticks rather than by the end of the line.
CODE_RE = re.compile(r"(?ms)^(?:```|~~~).*?^(?:```|~~~)[^\n]*$|(`+)[^`]*\1")


def rewrite_links(text: str, from_dir: Path, to_dir: Path) -> str:
  """Re-base relative links when a document changes directory depth.

  Imported prose was written next to the files it points at. Rendering it
  somewhere else silently breaks every `../` unless the depth difference is
  applied, and a broken link in 49 files is a poor trade for a tidy layout.
  """
  if from_dir == to_dir:
    return text

  def repl(m: re.Match[str]) -> str:
    target, title = m.group(1), m.group(2) or ""
    path, sep, anchor = target.partition("#")
    if not path:
      return m.group(0)
    moved = os.path.relpath((from_dir / path).resolve(), to_dir)
    return f"]({moved}{sep}{anchor}{title})"

  out: list[str] = []
  at = 0
  for code in CODE_RE.finditer(text):
    out.append(LINK_RE.sub(repl, text[at : code.start()]))
    out.append(code.group(0))
    at = code.end()
  out.append(LINK_RE.sub(repl, text[at:]))
  return "".join(out)
MARKUP_RE = re.compile(r"[`*]")


def _bare(text: str) -> str:
  """Strip inline markup so a comparison reports meaning, not formatting.

  The index writes finding ids plain and the slice files write them in code
  spans; counting that as a difference would drown the real ones.
  """
  return " ".join(MARKUP_RE.sub("", text).split())


@dataclass
class MigrateReport:
  items: int = 0
  slices: int = 0
  passes: int = 0
  groups: int = 0
  next_id: str = ""
  by_status: dict[str, int] = field(default_factory=dict)
  by_size: dict[str, int] = field(default_factory=dict)
  by_tree: dict[str, int] = field(default_factory=dict)
  off_schema_sections: dict[str, int] = field(default_factory=dict)
  title_differences: int = 0
  findings_differences: int = 0
  trees_differences: int = 0
  depends_edges: int = 0
  roundtrip_ok: int = 0
  warnings: list[str] = field(default_factory=list)
  problems: list[str] = field(default_factory=list)

  def to_dict(self) -> dict[str, object]:
    return {
      "items": self.items,
      "slices": self.slices,
      "passes": self.passes,
      "groups": self.groups,
      "next_id": self.next_id,
      "by_status": self.by_status,
      "by_size": self.by_size,
      "by_tree": self.by_tree,
      "off_schema_sections": self.off_schema_sections,
      "title_differences": self.title_differences,
      "findings_differences": self.findings_differences,
      "trees_differences": self.trees_differences,
      "depends_edges": self.depends_edges,
      "roundtrip_ok": self.roundtrip_ok,
      "warnings": self.warnings,
      "problems": self.problems,
    }


def _md_text(chunk: list[str]) -> str:
  return "\n".join(chunk).strip("\n")


def split_prose(index: legacy.LegacyIndex) -> tuple[str, list[dict[str, str]], str]:
  """Carve index prose into a preamble, per-table heading/intro/outro, epilogue.

  The layout is a regular alternation of prose and table, so each prose run
  is split at the heading that opens the next table's section.
  """
  chunks = index.chunks
  md_runs = [_md_text(c) for k, c in chunks if k == "md"]
  tables = [c for k, c in chunks if k == "table"]
  if not tables:
    return (md_runs[0] if md_runs else ""), [], ""
  while len(md_runs) < len(tables) + 1:
    md_runs.append("")

  preamble = ""
  sections: list[dict[str, str]] = []
  for n in range(len(tables)):
    run = md_runs[n]
    m = H1_LINE_RE.search(run)
    before, at_heading = (run, "") if m is None else (run[: m.start()].strip("\n"), run[m.start() :].strip("\n"))
    if n == 0:
      preamble = before
    else:
      sections[-1]["outro"] = before
    heading, _, intro = at_heading.partition("\n")
    key_match = PASS_KEY_RE.search(heading)
    sections.append(
      {
        "key": key_match.group(1) if key_match else str(n + 1),
        "heading": heading,
        "intro": intro.strip("\n"),
        "outro": "",
      }
    )

  tail = md_runs[len(tables)]
  m2 = H2_LINE_RE.search(tail)
  if m2 is None:
    sections[-1]["outro"], epilogue = tail, ""
  else:
    sections[-1]["outro"] = tail[: m2.start()].strip("\n")
    epilogue = tail[m2.start() :].strip("\n")
  return preamble, sections, epilogue


def _parse_depends(line: str) -> tuple[list[str], str]:
  m = DEPENDS_RE.match(line)
  if m is None:
    return [], line
  raw = [p.strip() for p in m.group("ids").split(",") if p.strip()]
  return raw, line


def _trees_cell(cell: str) -> tuple[list[str], bool]:
  """Split a trees cell, unless it is a collective phrase like `all four`."""
  if COLLECTIVE_RE.match(cell):
    return [cell], True
  return [p.strip() for p in cell.split(",") if p.strip()], False


def build(
  source: Path,
  cfg: Config,
  *,
  render_dir: Path | None = None,
  index_render_dir: Path | None = None,
) -> tuple[Index, dict[str, Slice], MigrateReport]:
  """Parse and reconcile. Writes nothing; every check runs here.

  `render_dir` and `index_render_dir` are where the slice markdown and the
  roadmap will end up. They usually differ in depth, and each document's
  relative links are re-based against its own destination so they still
  resolve after the move.
  """
  legacy_index, legacy_slices, located = legacy.read_tree(source, done_dir=cfg.done_dir)
  report = MigrateReport()
  report.roundtrip_ok = len(legacy_slices) + 1

  preamble, pass_sections, epilogue = split_prose(legacy_index)
  fix_index = (
    (lambda s: rewrite_links(s, source, index_render_dir))
    if index_render_dir is not None
    else (lambda s: s)
  )
  index = Index(
    id_prefix=cfg.id_prefix,
    id_width=cfg.id_width,
    preamble=fix_index(preamble),
    epilogue=fix_index(epilogue),
    passes=[
      PassInfo(
        key=s["key"],
        heading=fix_index(s["heading"]),
        intro=fix_index(s["intro"]),
        outro=fix_index(s["outro"]),
      )
      for s in pass_sections
    ],
  )
  report.passes = len(index.passes)

  tables = legacy_index.tables()
  seen: set[str] = set()
  for n, table in enumerate(tables):
    pass_key = index.passes[n].key if n < len(index.passes) else str(n + 1)
    group = ""
    for row in table.rows:
      if row.kind == "group":
        group = row.cells[0]
        report.groups += 1
        continue
      item_id = row.item_id
      if not ids.is_valid(item_id):
        # The legacy row regex allows any character but ']', so a tree written
        # elsewhere can carry an id that would be written as a path.
        report.problems.append(
          f"{item_id!r} is not a usable id: it would be written as a filename"
        )
        continue
      if item_id in seen:
        report.problems.append(f"duplicate id {item_id} in the index")
        continue
      seen.add(item_id)
      size_cell = row.cells[2]
      flags = legacy.FLAG_RE.findall(size_cell)
      size = legacy.FLAG_RE.sub("", size_cell).strip()
      trees, literal = _trees_cell(row.cells[3])
      try:
        status = cfg.status_for_label(row.cells[5])
      except Exception as exc:
        report.problems.append(str(exc))
        continue
      sl = legacy_slices.get(item_id)
      index.items.append(
        Item(
          id=item_id,
          title=sl.title if sl else row.cells[1],
          short_title=row.cells[1],
          status=status,
          has_slice=sl is not None,
          size=size,
          flags=flags,
          trees=trees,
          trees_literal=literal,
          findings=row.cells[4],
          pass_key=pass_key,
          group=group,
        )
      )

  slices: dict[str, Slice] = {}
  for item in index.items:
    ls = legacy_slices.get(item.id)
    if ls is None:
      report.problems.append(f"{item.id}: index row has no slice file")
      continue
    meta = legacy.parse_meta(ls.meta, path=item.id)
    fix = (
      (lambda s: rewrite_links(s, source, render_dir)) if render_dir is not None else (lambda s: s)
    )
    depends_ids, depends_line = _parse_depends(ls.depends) if ls.depends else ([], "")
    item.depends_on = depends_ids
    report.depends_edges += len(depends_ids)
    if not ids.is_valid(ls.id):
      report.problems.append(
        f"{ls.id!r} in the slice file's heading is not a usable id"
      )
      continue
    sections = [Section(heading=h, body=fix(b)) for h, b in ls.sections]
    boundary = extract_boundary(sections, cfg.boundary)
    slices[item.id] = Slice(
      id=ls.id,
      title=ls.title,
      lead=[fix(b) for b in ls.lead],
      depends_note=depends_line or None,
      sections=sections,
      boundary=boundary,
      notes=[fix(b) for b in ls.notes],
      findings_note=str(meta["findings"]),
      size=str(meta["size"]),
      flags=list(meta["flags"]),
      trees_note=str(meta["trees"]),
      trees_plural=bool(meta["trees_plural"]),
    )

    if _bare(item.short_title) != _bare(ls.title):
      report.title_differences += 1
    if _bare(item.findings) != _bare(str(meta["findings"])):
      report.findings_differences += 1
    if _bare(", ".join(item.trees)) != _bare(str(meta["trees"])):
      report.trees_differences += 1

    in_done = located.get(item.id, False)
    if in_done != (item.status == cfg.done_status):
      report.problems.append(
        f"{item.id}: status {item.status!r} disagrees with its location "
        f"({'done/' if in_done else 'open'})"
      )
    for block in ls.lead:
      lm = LEAD_STATUS_RE.match(block)
      if lm is None:
        continue
      claimed = (lm.group("bare") or lm.group("named") or "").lower()
      if claimed in cfg.statuses and claimed != item.status:
        report.warnings.append(
          f"{item.id}: prose says {claimed!r} but the index says {item.status!r}; index wins"
        )

  for item in index.items:
    for sid in item.depends_on:
      if index.get(sid) is None:
        report.problems.append(f"{item.id}: depends on unknown id {sid}")

  known = set(index.get(i).id for i in [it.id for it in index.items])
  for sid in sorted(set(legacy_slices) - known):
    report.problems.append(f"{sid}: slice file has no index row")

  index.next_id = ids.high_water([it.id for it in index.items], cfg.id_prefix)
  report.next_id = ids.format_id(cfg.id_prefix, index.next_id, cfg.id_width)
  report.items = len(index.items)
  report.slices = len(slices)
  report.by_status = _tally(cfg.status_label(it.status) for it in index.items)
  report.by_size = _tally(
    it.size + ("".join(f" [{f}]" for f in it.flags)) for it in index.items if it.size
  )
  report.by_tree = _tally(t for it in index.items for t in it.trees)
  report.off_schema_sections = _tally(
    s.heading for sl in slices.values() for s in sl.sections if s.heading not in cfg.sections
  )
  return index, slices, report


def _tally(values) -> dict[str, int]:
  out: dict[str, int] = {}
  for v in values:
    out[v] = out.get(v, 0) + 1
  return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def write(root: Path, cfg: Config, index: Index, slices: dict[str, Slice]) -> list[Path]:
  """Materialise the tracking directory. Only called once `build` is clean."""
  base = root / DIR_NAME
  written: list[Path] = []
  jsonio.write(base / CONFIG_NAME, cfg.to_dict())
  written.append(base / CONFIG_NAME)
  jsonio.write(base / INDEX_NAME, index.to_dict())
  written.append(base / INDEX_NAME)
  for name, text in templates.defaults().items():
    path = base / TEMPLATES_DIR / name
    jsonio.write_text(path, text)
    written.append(path)
  for item in index.items:
    sl = slices.get(item.id)
    if sl is None:
      continue
    folder = base / SLICES_DIR / (cfg.done_dir if item.status == cfg.done_status else "")
    # This join does not go through State.slice_path, so it needs the rule too.
    ids.require_valid(sl.id)
    path = folder / f"{sl.id}.json"
    jsonio.write(path, sl.to_dict())
    written.append(path)
  return written
