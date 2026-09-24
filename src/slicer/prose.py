"""Addressing for the roadmap's own prose blocks.

A roadmap carries text that belongs to no slice: an opening note, a heading
and surrounding prose per review pass, and a closing section. This module is
the only place that knows how those are named, so every caller passes a plain
string reference and nothing else has to parse it.

No I/O: these functions read and write an in-memory `Index`.
"""

from __future__ import annotations

from dataclasses import dataclass

from slicer.errors import StateError
from slicer.model import Index, PassInfo

PASS_FIELDS = ("heading", "intro", "outro")
FORMS = "preamble, epilogue, pass.<key>.heading, pass.<key>.intro, pass.<key>.outro"


@dataclass(frozen=True)
class BlockRef:
  kind: str
  pass_key: str = ""
  field: str = ""

  def __str__(self) -> str:
    return self.kind if self.kind != "pass" else f"pass.{self.pass_key}.{self.field}"


def parse_ref(ref: str) -> BlockRef:
  """Turn `pass.5.intro` into a reference, or say what the valid forms are."""
  if ref in ("preamble", "epilogue"):
    return BlockRef(kind=ref)
  parts = ref.split(".")
  if len(parts) == 3 and parts[0] == "pass":
    _, key, field = parts
    if field not in PASS_FIELDS:
      raise StateError(f"unknown prose field {field!r}; valid forms: {FORMS}")
    if not key:
      raise StateError(f"a pass reference needs a key; valid forms: {FORMS}")
    return BlockRef(kind="pass", pass_key=key, field=field)
  raise StateError(f"unknown prose block {ref!r}; valid forms: {FORMS}")


def refs(index: Index) -> list[str]:
  """Every addressable block, in the order it appears in the rendered roadmap."""
  out = ["preamble"]
  for info in index.passes:
    out.extend(f"pass.{info.key}.{field}" for field in PASS_FIELDS)
  out.append("epilogue")
  return out


def _require_pass(index: Index, key: str) -> PassInfo:
  info = index.pass_info(key)
  if info is None:
    known = ", ".join(p.key for p in index.passes) or "none"
    raise StateError(f"no pass {key!r}; declared passes: {known}")
  return info


def get(index: Index, ref: str) -> str:
  block = parse_ref(ref)
  if block.kind == "pass":
    return getattr(_require_pass(index, block.pass_key), block.field)
  return getattr(index, block.kind)


def put(index: Index, ref: str, text: str) -> None:
  block = parse_ref(ref)
  if block.kind == "pass":
    setattr(_require_pass(index, block.pass_key), block.field, text)
  else:
    setattr(index, block.kind, text)


def summary(index: Index, ref: str, width: int = 48) -> tuple[int, str]:
  """(line count, one-line preview) for listings."""
  text = get(index, ref)
  if not text:
    return 0, ""
  lines = text.split("\n")
  first = next((l.strip() for l in lines if l.strip()), "")
  return len(lines), (first if len(first) <= width else first[: width - 1] + "…")
