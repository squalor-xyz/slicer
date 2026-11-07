"""The slice-relay role matrix as versioned data.

The matrix used to be a Markdown file people edited by hand, which left slicer
unable to say anything about it. Each row here names a role, the slice conditions
it applies to, and the agent, vendor, model and reasoning effort that run it, so a
later change can recommend rows for a slice. Rules are short texts. The file is
state: only the `relay-roles` commands change it, and the Markdown in
`.slicer/render/` is generated from it. This module does no I/O.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping

from slicer import jsonio
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
# Roles the relay ledger records that no matrix row runs: a read-only UI pass on a UI slice
# and the read-only spec check before a wave. `relay-run step --role` accepts them, so the
# steps are told apart in `relay-run list`; the matrix and `relay-roles recommend` keep
# ROLE_NAMES alone.
LEDGER_ONLY_ROLES = ("ui", "spec-check")
LEDGER_ROLE_NAMES = ROLE_NAMES + LEDGER_ONLY_ROLES
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


def revision(matrix: RoleMatrix) -> str:
  """Twelve hex digits that name this exact matrix: rows, rules, their order and both counters.

  Same digest as `snapshot.revision` (which this module cannot import), so a tool that reads
  `relay-roles show --json` can name what it read as `expect` in a change set.
  """
  return hashlib.sha256(jsonio.dumps(matrix.to_dict()).encode("utf-8")).hexdigest()[:12]


# ---- change sets --------------------------------------------------------------
# `relay-roles apply` takes one JSON object: {"expect": REVISION?, "ops": [OP, ...]}. Each OP
# is one of the single edit verbs as an object. Or, in place of `ops`, {"replace": {"rows":
# [...], "rules": [...]}}: a whole matrix, which `replace_steps` turns into the same steps.
# This half only reads the shape; `ops.py` checks the values against a copy of the matrix and
# applies them.

SCORE_FIELDS = ("effort", "importance", "urgency")
LIST_FIELDS = ("size",) + SCORE_FIELDS
YES_NO_ANY_FIELDS = ("well_specified", "split", "sliver")
TEXT_FIELDS = ("agent", "vendor", "model", "reasoning", "note")
ROW_FIELDS = LIST_FIELDS + YES_NO_ANY_FIELDS + TEXT_FIELDS
# Per op: the required keys, then the optional ones. Every other key is refused.
_OP_KEYS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
  "add": (("role",), ("ref",) + ROW_FIELDS),
  "set": (("id",), ("role",) + ROW_FIELDS),
  "remove": (("id",), ()),
  "move": (("id",), ("before", "last")),
  "rule-add": (("text",), ("ref",)),
  "rule-edit": (("id", "text"), ()),
  "rule-remove": (("id",), ()),
}
_RULE_OPS = ("rule-add", "rule-edit", "rule-remove")
CHANGE_SET_KEYS = ("expect", "ops", "replace")
# `rule-move` is a step a replace derives so the rules end in the order given. It is not in
# `_OP_KEYS`, so a user's `ops` cannot name it: slicer has no verb that moves a rule.
INTERNAL_OPS = ("rule-move",)


@dataclass
class RoleOp:
  """One step of a change set. `problem` is set when its shape is wrong; it is then not run.

  `where` names the step in a problem: `replace.rows[3]` for a step a replace derived, empty
  for a step of `ops`, which is named by its position (`ops[3]`).
  """

  op: str
  args: dict[str, Any] = field(default_factory=dict)
  ref: str | None = None
  problem: str | None = None
  where: str = ""


@dataclass
class ReplaceRow:
  """One row of a replace as given. `args` is complete: every field has a value."""

  index: int
  id: str | None = None
  args: dict[str, Any] = field(default_factory=dict)
  problem: str | None = None


@dataclass
class ReplaceRule:
  index: int
  id: str | None = None
  text: str = ""
  problem: str | None = None


@dataclass
class Replace:
  rows: list[ReplaceRow]
  rules: list[ReplaceRule]


@dataclass
class ChangeSet:
  expect: str | None
  ops: list[RoleOp]
  replace: Replace | None = None


def _usage(message: str) -> StateError:
  return StateError(message, code="usage")


def parse_change_set(text: str) -> ChangeSet:
  """Read the JSON text of a change set. A shape problem of the whole set raises `usage`.

  A shape problem of one step does not raise: it is kept on the step so that every step's
  problem can be reported together, in order, with the problems the values give.
  """
  try:
    data = json.loads(text)
  except ValueError as exc:
    raise _usage(f"the change set is not JSON: {exc}") from None
  if not isinstance(data, dict):
    raise _usage('the change set must be one JSON object, {"expect": ..., "ops": [...]}')
  unknown = [key for key in data if key not in CHANGE_SET_KEYS]
  if unknown:
    raise _usage(
      f"the change set has unknown key(s): {', '.join(unknown)}; "
      f"its keys are {', '.join(CHANGE_SET_KEYS)}"
    )
  expect = data.get("expect")
  if expect is not None and not isinstance(expect, str):
    raise _usage("expect must be a revision such as the one `relay-roles show --json` prints")
  if "ops" in data and "replace" in data:
    raise _usage('the change set has both "ops" and "replace"; give one: steps to run, or the whole matrix')
  if "replace" in data:
    return ChangeSet(expect=expect, ops=[], replace=_parse_replace(data["replace"]))
  steps = data.get("ops")
  if not isinstance(steps, list) or not steps:
    raise _usage(
      'the change set needs "ops" (a non-empty list of steps) or "replace" (a whole matrix)'
    )
  refs: dict[str, str] = {}  # ref name -> "row" or "rule", for the refs defined so far
  return ChangeSet(expect=expect, ops=[_parse_op(step, refs) for step in steps])


def _parse_op(step: Any, refs: dict[str, str]) -> RoleOp:
  if not isinstance(step, dict):
    return RoleOp(op="", problem="a step must be a JSON object with an \"op\" key")
  name = step.get("op")
  if not isinstance(name, str) or name not in _OP_KEYS:
    return RoleOp(op=str(name) if isinstance(name, str) else "", problem=(
      f"unknown op {name!r}; the ops are {', '.join(_OP_KEYS)}" if name is not None
      else f"a step needs an \"op\" key; the ops are {', '.join(_OP_KEYS)}"
    ))
  required, optional = _OP_KEYS[name]
  op = RoleOp(op=name)
  # A valid ref is taken even when the step has another problem, so a later `@NAME` does not
  # report a second, derived problem.
  ref = step.get("ref")
  kind = "rule" if name in _RULE_OPS else "row"
  if "ref" in step:
    if not isinstance(ref, str) or not ref:
      return _bad(op, '"ref" must be a non-empty name')
    if ref in refs:
      return _bad(op, f'"ref" {ref!r} is already used by an earlier step')
    refs[ref] = kind
    op.ref = ref
  unknown = [key for key in step if key != "op" and key not in required + optional]
  if unknown:
    return _bad(op, f"{name} has unknown key(s): {', '.join(unknown)}")
  missing = [key for key in required if key not in step]
  if missing:
    return _bad(op, f"{name} needs {', '.join(missing)}")
  for key in required + optional:
    if key not in step or key == "ref":
      continue
    value = step[key]
    problem = _check_value(name, key, value, refs)
    if problem:
      return _bad(op, problem)
    op.args[key] = _normalize(key, value)
  if name == "move":
    last = op.args.get("last", False)
    if ("before" in op.args) == bool(last):
      return _bad(op, 'move needs exactly one of "before" or "last": true')
  return op


def _bad(op: RoleOp, problem: str) -> RoleOp:
  op.problem = problem
  return op


# ---- replace --------------------------------------------------------------------
# {"replace": {"rows": [ROW, ...], "rules": [RULE, ...]}} says what the matrix should be.
# A ROW is what `relay-roles show --json` prints for a row (`id` optional) with the conditions
# either in `when` or as the flat keys of an `add` step. A row or rule with an `id` is that
# row or rule, kept; one without is new. `replace_steps` turns the set into steps.

_REPLACE_KEYS = ("rows", "rules")
_ROW_KEYS = ("id", "role", "when") + ROW_FIELDS
_CONDITION_KEYS = LIST_FIELDS + YES_NO_ANY_FIELDS
_FLAG_WORDS = {True: "yes", False: "no", None: "any"}


def _parse_replace(data: Any) -> Replace:
  """Read the shape of a replace. A problem of the whole raises `usage`; a problem of one
  row or rule stays on it, so every one is reported together with the problems of values."""
  if not isinstance(data, dict):
    raise _usage('"replace" must be an object, {"rows": [...], "rules": [...]}')
  unknown = [key for key in data if key not in _REPLACE_KEYS]
  if unknown:
    raise _usage(
      f"replace has unknown key(s): {', '.join(unknown)}; its keys are {', '.join(_REPLACE_KEYS)}"
    )
  for key in _REPLACE_KEYS:
    if key not in data:
      raise _usage(f'replace needs "{key}": the whole list, `[]` to have none (replace.{key})')
    if not isinstance(data[key], list):
      raise _usage(f'"replace.{key}" must be a list')
  rows = [_parse_replace_row(i, raw) for i, raw in enumerate(data["rows"])]
  rules = [_parse_replace_rule(i, raw) for i, raw in enumerate(data["rules"])]
  for items, name in ((rows, "rows"), (rules, "rules")):
    first: dict[str, int] = {}
    for item in items:
      if item.id is None or item.problem:
        continue
      if item.id in first:
        item.problem = f'"id" {item.id!r} is also at replace.{name}[{first[item.id]}]; give it once'
      else:
        first[item.id] = item.index
  return Replace(rows=rows, rules=rules)


def _replace_id(raw: dict[str, Any]) -> tuple[str | None, str | None]:
  """The row's or rule's `id` and a problem with it. `@` names a ref, which a replace has none of."""
  if "id" not in raw:
    return None, None
  value = raw["id"]
  if not isinstance(value, str) or not value.strip():
    return None, '"id" must be an id'
  if value.startswith("@"):
    return None, f'"id" {value!r} is not an id; a replace has no refs'
  return value, None


def _parse_replace_row(index: int, raw: Any) -> ReplaceRow:
  row = ReplaceRow(index=index)
  if not isinstance(raw, dict):
    row.problem = "a row must be a JSON object"
    return row
  unknown = [key for key in raw if key not in _ROW_KEYS]
  if unknown:
    row.problem = f"a row has unknown key(s): {', '.join(unknown)}"
    return row
  row.id, row.problem = _replace_id(raw)
  if row.problem:
    return row
  if "role" not in raw:
    row.problem = "a row needs role"
    return row
  conditions: Any = {key: raw[key] for key in _CONDITION_KEYS if key in raw}
  if "when" in raw:
    if conditions:
      row.problem = (
        f"a row gives its conditions in \"when\" or as flat keys, not both "
        f"(flat: {', '.join(conditions)})"
      )
      return row
    conditions = raw["when"]
    if not isinstance(conditions, dict):
      row.problem = '"when" must be an object'
      return row
    unknown = [key for key in conditions if key not in _CONDITION_KEYS]
    if unknown:
      row.problem = f"when has unknown key(s): {', '.join(unknown)}"
      return row
  args: dict[str, Any] = {"role": raw["role"]}
  for key in ("role",) + TEXT_FIELDS:
    if key in raw:
      problem = _check_value("add", key, raw[key], {})
      if problem:
        row.problem = problem
        return row
  for key in _CONDITION_KEYS:
    value = conditions.get(key)
    if key in YES_NO_ANY_FIELDS:
      value = _FLAG_WORDS[value] if isinstance(value, bool) or value is None else value
    elif key not in conditions:
      value = []
    problem = _check_value("add", key, value, {})
    if problem:
      row.problem = problem
      return row
    args[key] = _normalize(key, value)
  for key in TEXT_FIELDS:
    args[key] = raw.get(key, "")
  row.args = args
  return row


def _parse_replace_rule(index: int, raw: Any) -> ReplaceRule:
  rule = ReplaceRule(index=index)
  if not isinstance(raw, dict):
    rule.problem = "a rule must be a JSON object"
    return rule
  unknown = [key for key in raw if key not in ("id", "text")]
  if unknown:
    rule.problem = f"a rule has unknown key(s): {', '.join(unknown)}"
    return rule
  rule.id, rule.problem = _replace_id(raw)
  if rule.problem:
    return rule
  if not isinstance(raw.get("text"), str):
    rule.problem = 'a rule needs "text"'
    return rule
  rule.text = raw["text"]
  return rule


def _row_is(row: RoleRow, args: dict[str, Any]) -> bool:
  """Whether the row already has exactly the fields a replace gives, so no step is derived."""
  when = row.when.to_dict()
  return (
    args["role"] == row.role
    and all(args[key] == when[key] for key in LIST_FIELDS)
    and all(args[key] == _FLAG_WORDS[when[key]] for key in YES_NO_ANY_FIELDS)
    and all(args[key] == getattr(row, key) for key in TEXT_FIELDS)
  )


def _order_steps(
  current: list[str], wanted: list[str], op: str, where: dict[str, str],
) -> list[RoleOp]:
  """Moves that leave `current` in the order `wanted`, one per item that is out of place.

  Each puts the next item due before the one standing in its place, so an order that is
  already right derives nothing and a single item moved ahead derives one step. Nothing is
  derived unless `wanted` holds exactly the items of `current`: a replace that names an id
  the matrix lacks, or has a problem, is refused whatever its order.
  """
  if sorted(current) != sorted(wanted):
    return []
  steps: list[RoleOp] = []
  order = list(current)
  for at, item in enumerate(wanted):
    if order[at] == item:
      continue
    steps.append(RoleOp(op=op, args={"id": item, "before": order[at]}, where=where[item]))
    order.remove(item)
    order.insert(at, item)
  return steps


def replace_steps(matrix: RoleMatrix, replace: Replace) -> list[RoleOp]:
  """The steps that make `matrix` what `replace` says. Pure: it reads, and changes nothing.

  Order: removes, sets, adds, moves so the rows end in the order given, then rule removes,
  rule edits, rule adds and rule moves. A row or rule with an id is kept and its fields become
  exactly the ones given; one without an id is added with the next id; one the replace leaves
  out is removed. A row or rule with an id the matrix does not hold gets a step that
  refuses it (`unknown_role_row`, `unknown_role_rule`), so no id is reused or invented. A
  shape problem stays on its step. An item that already is what the replace says derives no
  step, so a replace equal to the matrix derives none.
  """
  steps: list[RoleOp] = []
  have_rows = {row.id: row for row in matrix.rows}
  given_rows = {row.id for row in replace.rows if row.id is not None}
  for row in matrix.rows:
    if row.id not in given_rows:
      steps.append(RoleOp(op="remove", args={"id": row.id}, where="replace.rows"))
  added: list[ReplaceRow] = []
  for item in replace.rows:
    where = f"replace.rows[{item.index}]"
    op = "set" if item.id is not None else "add"
    if item.problem:
      steps.append(RoleOp(op=op, problem=item.problem, where=where))
    elif item.id is None:
      added.append(item)
    elif item.id not in have_rows or not _row_is(have_rows[item.id], item.args):
      steps.append(RoleOp(op="set", args={"id": item.id, **item.args}, where=where))
  for item in added:
    steps.append(RoleOp(
      op="add", args=dict(item.args), ref=f"rows[{item.index}]", where=f"replace.rows[{item.index}]",
    ))
  wanted_rows = [item.id or f"@rows[{item.index}]" for item in replace.rows]
  steps += _order_steps(
    [row.id for row in matrix.rows if row.id in given_rows]
    + [f"@rows[{item.index}]" for item in added],
    wanted_rows, "move",
    {name: f"replace.rows[{item.index}]" for name, item in zip(wanted_rows, replace.rows)},
  )
  have_rules = {rule.id: rule for rule in matrix.rules}
  given_rules = {rule.id for rule in replace.rules if rule.id is not None}
  for rule in matrix.rules:
    if rule.id not in given_rules:
      steps.append(RoleOp(op="rule-remove", args={"id": rule.id}, where="replace.rules"))
  new_rules: list[ReplaceRule] = []
  for item in replace.rules:
    where = f"replace.rules[{item.index}]"
    if item.problem:
      steps.append(RoleOp(
        op="rule-edit" if item.id is not None else "rule-add", problem=item.problem, where=where,
      ))
    elif item.id is None:
      new_rules.append(item)
    elif item.id not in have_rules or have_rules[item.id].text != item.text:
      steps.append(RoleOp(op="rule-edit", args={"id": item.id, "text": item.text}, where=where))
  for item in new_rules:
    steps.append(RoleOp(
      op="rule-add", args={"text": item.text}, ref=f"rules[{item.index}]",
      where=f"replace.rules[{item.index}]",
    ))
  wanted_rules = [item.id or f"@rules[{item.index}]" for item in replace.rules]
  steps += _order_steps(
    [rule.id for rule in matrix.rules if rule.id in given_rules]
    + [f"@rules[{item.index}]" for item in new_rules],
    wanted_rules, "rule-move",
    {name: f"replace.rules[{item.index}]" for name, item in zip(wanted_rules, replace.rules)},
  )
  return steps


def _normalize(key: str, value: Any) -> Any:
  if key in LIST_FIELDS and isinstance(value, str):
    return []  # "any" clears the condition, as the flag does
  return value


def _check_value(op: str, key: str, value: Any, refs: dict[str, str]) -> str | None:
  """Why `value` has the wrong type for `key`, or None. Whether it is a good value is `ops.py`'s."""
  if key in ("id", "before"):
    if not isinstance(value, str) or not value.strip():
      return f'"{key}" must be an id'
    if value.startswith("@"):
      wanted = "rule" if op in _RULE_OPS else "row"
      if refs.get(value[1:]) != wanted:
        return f'"{key}" {value!r} names no earlier {wanted} step with that "ref"'
    return None
  if key == "last":
    return None if isinstance(value, bool) else '"last" must be true or false'
  if key in LIST_FIELDS:
    if isinstance(value, str) and value.strip().casefold() == "any":
      return None
    if not isinstance(value, list):
      return f'"{key}" must be a list, or "any" to clear it'
    allowed = (str,) if key == "size" else (str, int)
    if any(isinstance(n, bool) or not isinstance(n, allowed) for n in value):
      return f'"{key}" must hold only {"text" if key == "size" else "numbers or text"}'
    return None
  if not isinstance(value, str):
    return f'"{key}" must be text'
  return None


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
  """The example matrix `relay-roles init` writes: the owner's lineup, vendor by vendor.

  Implementers: Codex sol (r9) first for every slice, then Codex terra rows for small,
  low-effort work (whole slice r12-r13, sliver r14-r15, split part r16-r17), then the
  Claude rows (r8, r1-r4) as the fallback. Reviewer, gate and architect: Grok first
  (r18-r20), Claude opus (r5-r7) after it. The architect rows match size L only, so a
  smaller slice has no architect. The orchestrator is Claude (r10), with Codex (r11) as
  the alternate. Nothing here assumes a vendor is installed: every row is written, and
  rule u9 sends a role to its next matching row when a row's agent is missing.

  The first eleven ids (r1-r11) keep the meaning they had before the lineup grew; the rows
  added since take r12 on and are listed where the lineup wants them, not in id order.
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

  def terra(n: int, reasoning: str, when: RoleWhen, note: str) -> RoleRow:
    return row(n, "implementer", "gpt-6.1-terra", reasoning, when=when, agent="Codex",
               vendor="OpenAI", note=note)

  def grok(n: int, role: str, reasoning: str, note: str, when: RoleWhen | None = None) -> RoleRow:
    return row(n, role, "grok-4.7", reasoning, when=when, agent="Grok", vendor="xAI", note=note)

  rows = [
    # Codex implementers first: the first matching row wins, so sol takes every slice
    # and the terra rows are the next choices for small, low-effort work.
    row(9, "implementer", "gpt-6.1-sol", "medium", agent="Codex", vendor="OpenAI",
        note="sol medium, any slice, split part or sliver: first choice; terra rows follow "
             "and the Claude rows are the fallback"),
    terra(12, "xhigh", RoleWhen(size=["M"], effort=[2], split=False, sliver=False),
          "terra xhigh, size M at effort 2, ahead of terra high; Claude rows are the fallback"),
    terra(13, "high", RoleWhen(size=["S", "M"], effort=[1, 2], split=False, sliver=False),
          "terra high, size S or M at effort 1 or 2, after sol; Claude rows are the fallback"),
    terra(14, "medium", RoleWhen(sliver=True),
          "terra medium per sliver: Haiku-class Codex model; Claude rows are the fallback"),
    terra(15, "high", RoleWhen(sliver=True),
          "terra high per sliver: Haiku-class Codex model; Claude rows are the fallback"),
    terra(16, "medium", RoleWhen(split=True),
          "terra medium per part of a split slice; Claude rows are the fallback"),
    terra(17, "high", RoleWhen(split=True),
          "terra high per part of a split slice; Claude rows are the fallback"),
    # Claude implementers, the fallback. Numbered last so r1-r3 keep their meaning, but
    # the sliver row leads them: a sliver reaches it before the general rows.
    row(8, "implementer", "haiku", "xhigh", when=RoleWhen(sliver=True),
        note="per sliver; fallback when Codex is out"),
    row(1, "implementer", "haiku", "xhigh",
        when=RoleWhen(size=["S", "M"], effort=[1], well_specified=True, split=False),
        note="fallback when Codex is out"),
    row(2, "implementer", "haiku", "xhigh",
        when=RoleWhen(size=["S"], effort=[2], well_specified=True, split=False),
        note="fallback when Codex is out"),
    row(3, "implementer", "sonnet", "high", when=RoleWhen(split=False),
        note="anything the rows above do not match; fallback when Codex is out"),
    row(4, "implementer", "haiku", "xhigh", when=RoleWhen(split=True),
        note="per part of a split slice; fallback when Codex is out"),
    grok(18, "reviewer", "medium", "Grok reviewer, first choice; Claude opus (r5) is the fallback"),
    row(5, "reviewer", "opus", "medium", note="fallback when Grok is out"),
    grok(19, "gate", "medium", "Grok gate, first choice; Claude opus (r6) is the fallback"),
    row(6, "gate", "opus", "medium", note="fallback when Grok is out"),
    grok(20, "architect", "high", "Grok architect, size L, first choice; Claude opus (r7) "
         "is the fallback", when=RoleWhen(size=["L"])),
    row(7, "architect", "opus", "high", when=RoleWhen(size=["L"]), note="fallback when Grok is out"),
    row(10, "orchestrator", "opus", "high", note="default orchestrator"),
    row(11, "orchestrator", "gpt-6.1-sol", "high", agent="Codex", vendor="OpenAI",
        note="alternate orchestrator"),
  ]
  texts = [
    "A reviewer never wrote code in the slice it reviews.",
    "Rows for any role may be selected across vendors, automatically or by the owner; "
    "u1 still holds, so nobody who wrote code in a slice reviews or gates it.",
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
    "A row whose agent is not installed is skipped; the role takes its next matching row.",
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
