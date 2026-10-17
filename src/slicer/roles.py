"""The slice-relay role matrix as versioned data.

The matrix used to be a Markdown file people edited by hand, which left slicer
unable to say anything about it. Each row here names a role, the slice conditions
it applies to, and the agent, vendor, model and reasoning effort that run it, so a
later change can recommend rows for a slice. Rules are short texts. The file is
state: only the `relay-roles` commands change it, and the Markdown in
`.slicer/render/` is generated from it. This module does no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping

from slicer.errors import StateError, reject_future_schema

if TYPE_CHECKING:
  from slicer.model import Item

# 2 adds `when.sliver`. A version 1 file has no such key; it loads as "either" and the
# next write makes it version 2. An older slicer refuses a version 2 file instead of
# ignoring the condition (which would make the sliver row match every item).
# 3 adds the `orchestrator` role. A version 1 or 2 file has no such row, so it loads
# unchanged and the next write makes it version 3; an older slicer refuses a version 3
# file instead of dropping or rejecting the row it cannot name.
ROLES_VERSION = 3
# The roles the relay knows. A row for any other role has nothing to run. The order is
# the order of `relay-roles recommend`: new roles go last so earlier positions keep their
# meaning.
ROLE_NAMES = ("implementer", "reviewer", "gate", "architect", "orchestrator")
# Why a row was selected for a role, in the order the choice is tried.
SELECTED_BY = ("override", "preferred_agent", "matrix_order", "unmatched")
# Sizes are free text, but the conventional ones read in size order, not alphabetically.
_SIZE_ORDER = ("S", "M", "L")
SCORES = (1, 2, 3)
EMPTY_CELL = "-"


def normalize_sizes(values: list[str]) -> list[str]:
  """De-duplicated sizes in a stable order: S, M, L first, then the rest by text."""
  return sorted(set(values), key=lambda v: (
    _SIZE_ORDER.index(v) if v in _SIZE_ORDER else len(_SIZE_ORDER), v,
  ))


def _ints(name: str, value: Any) -> list[int]:
  if not isinstance(value, list):
    raise TypeError(f"{name} must be a list")
  out: list[int] = []
  for n in value:
    if isinstance(n, bool) or not isinstance(n, int) or n not in SCORES:
      raise ValueError(f"{name} holds {n!r}; each must be 1, 2 or 3")
    out.append(n)
  return out


def _strings(name: str, value: Any) -> list[str]:
  if not isinstance(value, list) or not all(isinstance(s, str) for s in value):
    raise TypeError(f"{name} must be a list of text")
  return list(value)


def _text(name: str, value: Any) -> str:
  if not isinstance(value, str):
    raise TypeError(f"{name} must be text")
  return value


@dataclass
class RoleWhen:
  """The slice conditions a row applies to. An empty list or None matches anything."""

  size: list[str] = field(default_factory=list)
  effort: list[int] = field(default_factory=list)
  importance: list[int] = field(default_factory=list)
  urgency: list[int] = field(default_factory=list)
  well_specified: bool | None = None
  # True for a part of a split slice, False for a whole one, None for either.
  split: bool | None = None
  # True for a sliver (an item with a parent), False for any other item, None for either.
  sliver: bool | None = None

  def to_dict(self) -> dict[str, Any]:
    return {
      "size": list(self.size),
      "effort": list(self.effort),
      "importance": list(self.importance),
      "urgency": list(self.urgency),
      "well_specified": self.well_specified,
      "split": self.split,
      "sliver": self.sliver,
    }

  @staticmethod
  def from_dict(d: Mapping[str, Any]) -> "RoleWhen":
    flags = {}
    for name in ("well_specified", "split", "sliver"):
      # A matrix written before slivers existed has no `sliver`; it means either.
      value = d.get(name) if name == "sliver" else d[name]
      if value is not None and not isinstance(value, bool):
        raise TypeError(f"{name} must be true, false or null")
      flags[name] = value
    return RoleWhen(
      size=_strings("size", d["size"]),
      effort=_ints("effort", d["effort"]),
      importance=_ints("importance", d["importance"]),
      urgency=_ints("urgency", d["urgency"]),
      well_specified=flags["well_specified"],
      split=flags["split"],
      sliver=flags["sliver"],
    )

  def matches(self, item: Item, well_specified: bool, split: bool) -> bool:
    """Whether every condition holds for the slice and the two answers given.

    An empty list matches anything; a nonempty one must contain the slice's value, so
    a slice with no size or effort set matches only rows that do not name one.
    `well_specified` and `split` are not slice fields: the caller judges them. A sliver
    is an item with a parent, so `sliver` is read from the slice itself.
    """
    if self.well_specified is not None and self.well_specified != well_specified:
      return False
    if self.split is not None and self.split != split:
      return False
    if self.sliver is not None and self.sliver != is_sliver(item):
      return False
    for allowed, value in (
      (self.size, item.size), (self.effort, item.effort),
      (self.importance, item.importance), (self.urgency, item.urgency),
    ):
      if allowed and value not in allowed:
        return False
    return True

  def describe(self) -> str:
    """The When cell, generated from the conditions so it cannot drift from them."""
    parts: list[str] = []
    if self.well_specified is not None:
      parts.append("well specified" if self.well_specified else "not well specified")
    if self.split is not None:
      parts.append("split part" if self.split else "whole slice")
    if self.sliver is not None:
      parts.append("sliver" if self.sliver else "not a sliver")
    for label, values in (
      ("effort", self.effort), ("size", self.size),
      ("importance", self.importance), ("urgency", self.urgency),
    ):
      if values:
        parts.append(f"{label} {_either([str(v) for v in values])}")
    return ", ".join(parts) or "every slice"


def is_sliver(item: Item) -> bool:
  """Whether the item was carved from a larger slice, which is what its `parent` records."""
  return bool(item.parent)


def _either(values: list[str]) -> str:
  if len(values) == 1:
    return values[0]
  return ", ".join(values[:-1]) + " or " + values[-1]


@dataclass
class RoleRow:
  """One role, when it applies, and who runs it."""

  id: str
  role: str
  when: RoleWhen = field(default_factory=RoleWhen)
  agent: str = ""
  vendor: str = ""
  model: str = ""
  # Free text such as low, medium, high or xhigh. Named so it is not confused
  # with the slice's own `effort` score in `when`.
  reasoning: str = ""
  note: str = ""

  def to_dict(self) -> dict[str, Any]:
    return {
      "id": self.id,
      "role": self.role,
      "when": self.when.to_dict(),
      "agent": self.agent,
      "vendor": self.vendor,
      "model": self.model,
      "reasoning": self.reasoning,
      "note": self.note,
    }

  @staticmethod
  def from_dict(d: Mapping[str, Any]) -> "RoleRow":
    role = _text("role", d["role"])
    if role not in ROLE_NAMES:
      raise ValueError(f"role {role!r} is not one of {', '.join(ROLE_NAMES)}")
    return RoleRow(
      id=_text("id", d["id"]),
      role=role,
      when=RoleWhen.from_dict(d["when"]),
      agent=_text("agent", d["agent"]),
      vendor=_text("vendor", d["vendor"]),
      model=_text("model", d["model"]),
      reasoning=_text("reasoning", d["reasoning"]),
      note=_text("note", d["note"]),
    )


@dataclass
class RoleRule:
  id: str
  text: str

  def to_dict(self) -> dict[str, Any]:
    return {"id": self.id, "text": self.text}

  @staticmethod
  def from_dict(d: Mapping[str, Any]) -> "RoleRule":
    return RoleRule(id=_text("id", d["id"]), text=_text("text", d["text"]))


@dataclass
class RoleMatrix:
  """Rows, rules and the counters that keep their ids from being reused."""

  rows: list[RoleRow] = field(default_factory=list)
  rules: list[RoleRule] = field(default_factory=list)
  next_row: int = 1
  next_rule: int = 1
  version: int = ROLES_VERSION

  def to_dict(self) -> dict[str, Any]:
    return {
      "version": self.version,
      "rows": [row.to_dict() for row in self.rows],
      "rules": [rule.to_dict() for rule in self.rules],
      "next_row": self.next_row,
      "next_rule": self.next_rule,
    }

  @staticmethod
  def from_dict(d: Mapping[str, Any]) -> "RoleMatrix":
    version = d["version"]
    if isinstance(version, bool) or not isinstance(version, int):
      raise TypeError("version must be a number")
    reject_future_schema("relay-roles.json", version, ROLES_VERSION)
    rows = [RoleRow.from_dict(r) for r in d["rows"]]
    rules = [RoleRule.from_dict(r) for r in d["rules"]]
    for label, items in (("row", rows), ("rule", rules)):
      ids = [item.id for item in items]
      if len(set(ids)) != len(ids):
        raise ValueError(f"two {label}s share an id")
    next_row, next_rule = d["next_row"], d["next_rule"]
    if any(isinstance(n, bool) or not isinstance(n, int) or n < 1 for n in (next_row, next_rule)):
      raise ValueError("next_row and next_rule must be positive numbers")
    # Older files are lifted to the current version here, so every write is current.
    return RoleMatrix(
      rows=rows, rules=rules, next_row=next_row, next_rule=next_rule, version=ROLES_VERSION,
    )

  def row(self, row_id: str) -> RoleRow:
    for row in self.rows:
      if row.id == row_id:
        return row
    raise StateError(
      f"no such relay role row: {row_id}; `slicer relay-roles show` lists them",
      code="unknown_role_row",
    )

  def rule(self, rule_id: str) -> RoleRule:
    for rule in self.rules:
      if rule.id == rule_id:
        return rule
    raise StateError(
      f"no such relay role rule: {rule_id}; `slicer relay-roles show` lists them",
      code="unknown_role_rule",
    )


@dataclass
class RolePick:
  """One role's resolved row, why it was chosen, and the other rows that could have been."""

  role: str
  row: RoleRow | None
  # Every other row that matches the slice, in matrix order.
  alternatives: list[RoleRow] = field(default_factory=list)
  selected_by: str = "unmatched"

  def to_dict(self) -> dict[str, Any]:
    return {
      "role": self.role,
      "row": self.row.to_dict() if self.row else None,
      "alternatives": [row.to_dict() for row in self.alternatives],
      "selected_by": self.selected_by,
    }


def _bad_field(message: str) -> StateError:
  return StateError(message, code="bad_role_field")


def _ids(rows: list[RoleRow]) -> str:
  return ", ".join(row.id for row in rows)


def parse_selectors(values: list[str]) -> dict[str, str]:
  """`ROLE=ROW_ID` selectors as a role-to-row-id mapping, at most one per role."""
  out: dict[str, str] = {}
  for value in values:
    role, sep, row_id = value.partition("=")
    role, row_id = role.strip(), row_id.strip()
    if not sep or not role or not row_id:
      raise _bad_field(
        f"selector {value!r} is not ROLE=ROW_ID, such as implementer=r9; "
        "`slicer relay-roles show` lists the row ids"
      )
    if role not in ROLE_NAMES:
      raise _bad_field(
        f"selector {value!r} names role {role!r}, which is not one of {', '.join(ROLE_NAMES)}"
      )
    if role in out:
      raise _bad_field(
        f"role {role} is selected twice ({out[role]} and {row_id}); give one --select per role"
      )
    out[role] = row_id
  return out


def _check_selection(
  matrix: RoleMatrix, role: str, row_id: str, eligible: list[RoleRow],
) -> RoleRow:
  """The row an override names, once it is known to be a row of this role that matches."""
  row = matrix.row(row_id)
  if row.role != role:
    raise _bad_field(
      f"row {row.id} has role {row.role}, not {role}; "
      + (f"the {role} rows that match this slice are {_ids(eligible)}" if eligible
         else f"no {role} row matches this slice")
    )
  if row not in eligible:
    raise _bad_field(
      f"{role} row {row.id} does not match this slice (it applies to {row.when.describe()}); "
      + (f"the {role} rows that match are {_ids(eligible)}" if eligible
         else f"no {role} row matches this slice")
    )
  return row


def resolve(
  matrix: RoleMatrix, item: Item, well_specified: bool, split: bool, *,
  prefer_agent: str | None = None, select: Mapping[str, str] | None = None,
) -> list[RolePick]:
  """For each role, in `ROLE_NAMES` order, the row to use and the rows that could have been.

  A row is eligible when its conditions match the slice. An explicit selection for a
  role wins and must be an eligible row of that role; otherwise the first eligible row on
  `prefer_agent` (compared trimmed and casefolded) wins; otherwise the first eligible
  row in matrix order. Every other eligible row is an alternative, in matrix order. A
  role with no eligible row has no row and no alternatives. Nothing here reads the
  environment: the harness the relay was launched from has no effect. Only reads.
  """
  preferred = None
  if prefer_agent is not None:
    preferred = prefer_agent.strip().casefold()
    if not preferred:
      raise _bad_field("--prefer-agent is blank; name an agent such as one in `relay-roles show`")
  select = dict(select or {})
  for role in select:
    if role not in ROLE_NAMES:
      raise _bad_field(f"role {role!r} is not one of {', '.join(ROLE_NAMES)}")
  picks: list[RolePick] = []
  for role in ROLE_NAMES:
    eligible = [
      row for row in matrix.rows if row.role == role and row.when.matches(item, well_specified, split)
    ]
    chosen: RoleRow | None = None
    by = "unmatched"
    if role in select:
      chosen, by = _check_selection(matrix, role, select[role], eligible), "override"
    else:
      if preferred is not None:
        chosen = next((row for row in eligible if row.agent.strip().casefold() == preferred), None)
        by = "preferred_agent"
      if chosen is None and eligible:
        chosen, by = eligible[0], "matrix_order"
    if chosen is None:
      by = "unmatched"
    picks.append(RolePick(
      role=role, row=chosen,
      alternatives=[row for row in eligible if row is not chosen], selected_by=by,
    ))
  return picks


def recommend(
  matrix: RoleMatrix, item: Item, well_specified: bool, split: bool,
) -> list[tuple[str, RoleRow | None]]:
  """For each role, in `ROLE_NAMES` order, the first row that matches the slice.

  Row order in the matrix decides between two matching rows. A role with no
  matching row maps to None. This only reads; it proposes and stores nothing.
  """
  return [(pick.role, pick.row) for pick in resolve(matrix, item, well_specified, split)]


def skeleton(first_row: int = 1, first_rule: int = 1) -> RoleMatrix:
  """The example matrix `relay-roles init` writes.

  Claude Code rows pick every role by matrix order. The Codex implementer (r9) and
  orchestrator (r11) are alternatives; the Claude orchestrator (r10) is the default.
  The architect row (r7) matches size L only, so a smaller slice has no architect.

  Rows and rules are numbered from `first_row` and `first_rule`, so replacing a
  matrix that has issued ids never hands one out again.
  """
  def row(
    n: int, role: str, model: str, reasoning: str, *, when: RoleWhen | None = None,
    note: str = "", agent: str = "Claude Code", vendor: str = "Claude",
  ) -> RoleRow:
    return RoleRow(
      id=f"r{first_row + n - 1}", role=role, when=when or RoleWhen(), agent=agent, vendor=vendor,
      model=model, reasoning=reasoning, note=note,
    )

  rows = [
    # Numbered last so r1-r3 keep their meaning, but listed first: the first matching row
    # wins, so a sliver reaches this row before the general implementer rows.
    row(8, "implementer", "haiku", "xhigh", when=RoleWhen(sliver=True), note="per sliver"),
    row(1, "implementer", "haiku", "xhigh",
        when=RoleWhen(size=["S", "M"], effort=[1], well_specified=True, split=False)),
    row(2, "implementer", "haiku", "xhigh",
        when=RoleWhen(size=["S"], effort=[2], well_specified=True, split=False)),
    row(3, "implementer", "sonnet", "high", when=RoleWhen(split=False),
        note="anything the rows above do not match"),
    row(4, "implementer", "haiku", "xhigh", when=RoleWhen(split=True),
        note="per part of a split slice"),
    # Listed after the Claude implementer rows, so a Claude row still matches first.
    row(9, "implementer", "gpt-6.1-sol", "medium", agent="Codex", vendor="OpenAI",
        note="alternate implementer for any slice, split part or sliver"),
    row(5, "reviewer", "opus", "medium"),
    row(6, "gate", "opus", "medium"),
    row(7, "architect", "opus", "high", when=RoleWhen(size=["L"])),
    row(10, "orchestrator", "opus", "high", note="default orchestrator"),
    row(11, "orchestrator", "gpt-6.1-sol", "high", agent="Codex", vendor="OpenAI",
        note="alternate orchestrator"),
  ]
  texts = [
    "A reviewer never wrote code in the slice it reviews.",
    "No cross-vendor reviewer unless the owner asks.",
    "Roles on the orchestrator's own agent run as subagents; a role on another agent "
    "runs headless in the slice worktree with the same brief.",
    "\"well specified\" means the slice names its files, failing tests, implementation "
    "steps and checks, and needs no open owner decision.",
    "Split a slice only into parts with disjoint files and their own tests; a slice too "
    "big for one implementer is two slices or a slivered slice.",
    "Sliver a slice only at effort 1, or at effort 2 when every sliver is effort 1; "
    "never at effort 3.",
    "Slivers with disjoint files and their own tests may run in parallel, at most 3 at "
    "once; the rest run in order.",
    "A sliver that fails its gate twice, or whose diff falls short of the orchestrator's "
    "check, moves to the next model up (haiku to sonnet high).",
  ]
  rules = [RoleRule(id=f"u{n}", text=text) for n, text in enumerate(texts, first_rule)]
  return RoleMatrix(
    rows=rows, rules=rules, next_row=first_row + len(rows), next_rule=first_rule + len(rules),
  )


def _cell(text: str) -> str:
  return text.replace("|", "\\|") or EMPTY_CELL


def render_markdown(matrix: RoleMatrix, *, level: int = 1) -> str:
  """The matrix as Markdown, without the generated banner. Deterministic.

  `level` is the depth of the title heading. At 1 the text is a document of its
  own, with a title; deeper, it is a section of a larger one, so it has no title
  and its headings sit one level below `level`.
  """
  mark = "#" * (level + 1)
  lines = ["# Relay roles", ""] if level == 1 else []
  lines += [f"{mark} Matrix", ""]
  if matrix.rows:
    lines += [
      "| ID | Role | When | Agent | Vendor | Model | Reasoning | Note |",
      "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in matrix.rows:
      cells = [
        row.id, row.role.capitalize(), row.when.describe(), row.agent, row.vendor, row.model,
        row.reasoning, row.note,
      ]
      lines.append("| " + " | ".join(_cell(c) for c in cells) + " |")
  else:
    lines.append("No rows.")
  lines += ["", f"{mark} Rules", ""]
  if matrix.rules:
    lines += [f"- {rule.id}: {rule.text}" for rule in matrix.rules]
  else:
    lines.append("No rules.")
  return "\n".join(lines) + "\n"


def require_matrix(matrix: RoleMatrix | None, path: object) -> RoleMatrix:
  """Share the missing-matrix diagnostic between read-only front ends."""
  if matrix is None:
    raise StateError(
      f"{path} does not exist; run "
      "`slicer relay-roles init` to write the example matrix",
      code="no_relay_roles",
    )
  return matrix


def _row_cells(row: RoleRow) -> str:
  cells = [row.id, row.agent, row.vendor, row.model, row.reasoning]
  return "  ".join(cell or EMPTY_CELL for cell in cells)


def recommend_lines(pick: RolePick) -> list[str]:
  """A role's selected row and why, then its alternatives indented below it."""
  if pick.row is None:
    return [f"{pick.role}  (no matching row)"]
  return [
    f"{pick.role}  {_row_cells(pick.row)}  [{pick.selected_by}]",
    *(f"  alternative  {_row_cells(row)}" for row in pick.alternatives),
  ]
