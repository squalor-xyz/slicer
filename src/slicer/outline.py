"""Read the markdown outline that `slicer import` accepts.

The outline is the way into an empty roadmap: a person or an agent writes a
plain markdown document, and one command turns it into items and slices. It
is deliberately not the legacy format — that one is rigid because it has to
round-trip byte for byte, and it is read by `slicer migrate` instead.

This module only parses. It has no I/O, knows nothing about ids, and never
touches state: it turns text into `ItemSpec`s and complains precisely about
text it cannot read. Applying them is `ops.apply_outline`.

The shape:

    ## Parse the config file        an item; the heading is its title
    size: M                         key lines, directly under the heading
    tree: core, cli
    depends: Some other title

    A paragraph before the first `###` becomes the slice's lead.

    ### Why                         a section of the slice
    Because the loader accepts anything.

An entry with no `###` sections is a roadmap row and nothing more, exactly
as `slicer add` produces. An entry with sections also gets a slice file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from slicer.errors import OutlineError

# Keys an entry may carry. Anything else is an error naming this set, because
# a silently ignored key is a roadmap item that quietly lost its size.
KEYS = ("size", "tree", "trees", "findings", "status", "pass", "group", "depends")
LIST_KEYS = ("tree", "trees", "depends")

KEY_RE = re.compile(r"^(?P<key>[a-z][a-z-]*)\s*:\s*(?P<value>.*)$")
COMMENT_RE = re.compile(r"(?s)<!--.*?-->")


@dataclass
class SectionSpec:
  heading: str
  body: str


@dataclass
class ItemSpec:
  """One `##` entry, before any of it has been reconciled against state."""

  title: str
  line: int = 0
  size: str = ""
  trees: list[str] = field(default_factory=list)
  findings: str = ""
  status: str = ""
  pass_key: str = ""
  group: str = ""
  depends: list[str] = field(default_factory=list)
  lead: list[str] = field(default_factory=list)
  sections: list[SectionSpec] = field(default_factory=list)

  @property
  def has_slice(self) -> bool:
    return bool(self.sections or self.lead)

  def to_dict(self) -> dict[str, object]:
    return {
      "title": self.title,
      "size": self.size,
      "trees": list(self.trees),
      "findings": self.findings,
      "status": self.status,
      "pass": self.pass_key,
      "group": self.group,
      "depends": list(self.depends),
      "sections": [s.heading for s in self.sections],
    }


def _split_list(value: str) -> list[str]:
  return [part.strip() for part in value.split(",") if part.strip()]


def _blocks(lines: list[str]) -> list[str]:
  """Blank-line-separated paragraphs, blanks dropped."""
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


def parse(text: str, *, path: str = "<outline>") -> list[ItemSpec]:
  """Turn outline markdown into specs, or say exactly what is wrong with it."""
  if "\r" in text:
    raise OutlineError(f"{path}: CRLF line endings; convert to LF first")
  text = COMMENT_RE.sub("", text)
  lines = text.split("\n")

  specs: list[ItemSpec] = []
  current: ItemSpec | None = None
  # Which part of an entry we are in: keys come first, then lead prose, then
  # sections. Once prose or a section has started, a key line is just text.
  phase = "keys"
  section: SectionSpec | None = None
  buffer: list[str] = []

  def close_section() -> None:
    nonlocal section, buffer
    if section is not None and current is not None:
      section.body = "\n".join(buffer).strip("\n")
      current.sections.append(section)
    section, buffer = None, []

  def close_lead() -> None:
    nonlocal buffer
    if current is not None and buffer:
      current.lead.extend(_blocks(buffer))
    buffer = []

  for n, line in enumerate(lines, 1):
    if line.startswith("## ") and not line.startswith("### "):
      if section is not None:
        close_section()
      else:
        close_lead()
      title = line[3:].strip()
      if not title:
        raise OutlineError(f"{path}:{n}: '##' with no title")
      current = ItemSpec(title=title, line=n)
      specs.append(current)
      phase = "keys"
      continue

    if line.startswith("### "):
      if current is None:
        raise OutlineError(f"{path}:{n}: '###' section before any '##' item")
      if section is not None:
        close_section()
      else:
        close_lead()
      heading = line[4:].strip()
      if not heading:
        raise OutlineError(f"{path}:{n}: '###' with no heading")
      section = SectionSpec(heading=heading, body="")
      phase = "sections"
      continue

    if line.startswith("# "):
      # A document title. Ignored, so a generated outline may carry one.
      if current is not None and phase != "sections":
        raise OutlineError(
          f"{path}:{n}: '# ' heading inside item {current.title!r}; use '##' for an item"
        )
      continue

    if current is None:
      if line.strip():
        raise OutlineError(
          f"{path}:{n}: text before the first '##' item; a document title needs a single '#'"
        )
      continue

    if phase == "keys":
      if not line.strip():
        continue
      m = KEY_RE.match(line)
      if m is None:
        # The key block is over; this is lead prose.
        phase = "lead"
        buffer.append(line)
        continue
      key, value = m.group("key"), m.group("value").strip()
      if key not in KEYS:
        raise OutlineError(
          f"{path}:{n}: unknown key {key!r}; known: {', '.join(KEYS)}"
        )
      _assign(current, key, value, path=path, line=n)
      continue

    buffer.append(line)

  if section is not None:
    close_section()
  else:
    close_lead()

  if not specs:
    raise OutlineError(f"{path}: no items; an item is a '## ' heading")
  return specs


def _assign(spec: ItemSpec, key: str, value: str, *, path: str, line: int) -> None:
  if key in LIST_KEYS:
    values = _split_list(value)
    if key == "depends":
      spec.depends.extend(values)
    else:
      spec.trees.extend(values)
    return
  if not value:
    raise OutlineError(f"{path}:{line}: {key!r} has no value")
  if key == "pass":
    spec.pass_key = value
  else:
    setattr(spec, key, value)
