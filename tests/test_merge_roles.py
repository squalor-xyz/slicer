"""The `slicer-relay-roles` merge driver for `.slicer/relay-roles.json` (s262).

Two branches that each run `relay-roles add` issue the same `r` id, so Git's line merge
can only call that a conflict. The driver matches rows and rules by id instead, gives the
side merged in a fresh id when both sides issued one id for different content, and
refuses a disagreement without writing anything. The unit tests hand it three matrix
files; the end-to-end tests let Git run it.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import support
from slicer import jsonio, mergeroles, roles, store
from slicer.errors import StateError
from slicer.roles import RoleMatrix, RoleRow, RoleRule, RoleWhen

SRC = Path(__file__).resolve().parents[1] / "src"


def _row(row_id: str, role: str = "reviewer", **fields: object) -> RoleRow:
  return RoleRow(id=row_id, role=role, **fields)


def _matrix(
  rows: list[RoleRow], rules: list[RoleRule] | None = None, next_row: int | None = None,
  next_rule: int | None = None,
) -> str:
  """A matrix as the bytes `relay-roles` writes. Counters default to just past the ids."""
  rules = rules or []

  def past(items: list, prefix: str) -> int:
    return max([int(i.id[len(prefix):]) for i in items], default=0) + 1

  return jsonio.dumps(RoleMatrix(
    rows=rows, rules=rules,
    next_row=next_row if next_row is not None else past(rows, "r"),
    next_rule=next_rule if next_rule is not None else past(rules, "u"),
  ).to_dict())


def _base_rows() -> list[RoleRow]:
  return [_row("r1", "implementer", model="haiku"), _row("r2", "reviewer", model="opus")]


def _base_rules() -> list[RoleRule]:
  return [RoleRule(id="u1", text="first rule"), RoleRule(id="u2", text="second rule")]


BASE = _matrix(_base_rows(), _base_rules())


class MergeRelayRolesCase(unittest.TestCase):
  def fixture(self) -> support.TempRepo:
    """A scratch directory with no project: the driver needs none."""
    repo = support.TempRepo()
    self.addCleanup(repo.close)
    self.enterContext(support.isolated_discovery(repo.root))
    return repo

  def merge(self, base: str, ours: str, theirs: str):
    repo = self.fixture()
    for name, text in (("base", base), ("ours", ours), ("theirs", theirs)):
      repo.write(name, text)
    code, out, err = repo.run(
      "merge-relay-roles", *(str(repo.root / n) for n in ("base", "ours", "theirs")),
    )
    return code, out, err, repo.read("ours")

  def clean(self, base: str, ours: str, theirs: str) -> dict:
    code, out, err, merged = self.merge(base, ours, theirs)
    self.assertEqual(code, 0, err)
    self.assertEqual(out, "")
    # The result is exactly what the writer produces, so `check` and a later add agree.
    doc = json.loads(merged)
    self.assertEqual(merged, jsonio.dumps(RoleMatrix.from_dict(doc).to_dict()))
    return doc

  def refused(self, base: str, ours: str, theirs: str, code: str) -> str:
    """The merge is refused with this stable code and OURS keeps its exact bytes."""
    repo = self.fixture()
    paths = {}
    for name, text in (("base", base), ("ours", ours), ("theirs", theirs)):
      paths[name] = repo.write(name, text)
    with self.assertRaises(StateError) as caught:
      mergeroles.merge(paths["base"], paths["ours"], paths["theirs"])
    self.assertEqual(caught.exception.code, code)
    self.assertEqual(repo.read("ours"), ours)
    return str(caught.exception)


class RowAddTests(MergeRelayRolesCase):
  def test_MergeRelayRoles_BothSidesAddARow_KeepsBothAndRenumbersTheirs(self) -> None:
    ours = _matrix([*_base_rows(), _row("r3", "gate", model="ours-gate")], _base_rules())
    theirs = _matrix(
      [*_base_rows(), _row("r3", "architect", model="theirs-arch", note="plain note")],
      _base_rules(),
    )
    code, _, err, merged = self.merge(BASE, ours, theirs)
    self.assertEqual(code, 0, err)
    doc = json.loads(merged)
    self.assertEqual([r["id"] for r in doc["rows"]], ["r1", "r2", "r3", "r4"])
    self.assertEqual((doc["rows"][2]["role"], doc["rows"][2]["model"]), ("gate", "ours-gate"))
    self.assertEqual((doc["rows"][3]["role"], doc["rows"][3]["model"]), ("architect", "theirs-arch"))
    # Owner decision: the old id is not copied into the row; the stderr line is the record.
    self.assertEqual(doc["rows"][3]["note"], "plain note")
    self.assertIn("r3", err)
    self.assertIn("r4", err)
    self.assertEqual((doc["next_row"], doc["next_rule"]), (5, 3))
    self.assertEqual(merged, jsonio.dumps(RoleMatrix.from_dict(doc).to_dict()))

  def test_MergeRelayRoles_TheirsIssuedMoreIds_FreshIdIsPastEveryIssuedId(self) -> None:
    ours = _matrix([*_base_rows(), _row("r3", "gate")], _base_rules())
    theirs = _matrix(
      [*_base_rows(), _row("r3", "architect"), _row("r4", "reviewer", model="t4")],
      _base_rules(), next_row=7,
    )
    doc = self.clean(BASE, ours, theirs)
    # r4 is only theirs', so it keeps its id; the renumbered row must not take 4,
    # and the counter 7 theirs reached has issued ids up to 6.
    self.assertEqual([r["id"] for r in doc["rows"]], ["r1", "r2", "r3", "r7", "r4"])
    self.assertEqual(doc["rows"][3]["role"], "architect")
    self.assertEqual(doc["next_row"], 8)

  def test_MergeRelayRoles_OursCounterIsHigher_FreshIdIsPastIt(self) -> None:
    ours = _matrix([*_base_rows(), _row("r3", "gate")], _base_rules(), next_row=9)
    theirs = _matrix([*_base_rows(), _row("r3", "architect")], _base_rules())
    doc = self.clean(BASE, ours, theirs)
    self.assertEqual([r["id"] for r in doc["rows"]], ["r1", "r2", "r3", "r9"])
    self.assertEqual(doc["next_row"], 10)

  def test_MergeRelayRoles_TwoNewRowsOnTheirsBothCollide_GetConsecutiveFreshIds(self) -> None:
    ours = _matrix([*_base_rows(), _row("r3", "gate"), _row("r4", "gate")], _base_rules())
    theirs = _matrix(
      [*_base_rows(), _row("r3", "architect"), _row("r4", "architect", model="m")],
      _base_rules(),
    )
    doc = self.clean(BASE, ours, theirs)
    self.assertEqual([r["id"] for r in doc["rows"]], ["r1", "r2", "r3", "r4", "r5", "r6"])
    self.assertEqual([r["role"] for r in doc["rows"][4:]], ["architect", "architect"])
    self.assertEqual(doc["next_row"], 7)

  def test_MergeRelayRoles_OursIssuedThenRemovedAnId_TheirsDoesNotTakeItBack(self) -> None:
    # Ours added r3 and removed it again, so only its counter shows that r3 was spent.
    ours = _matrix(_base_rows(), _base_rules(), next_row=4)
    theirs = _matrix([*_base_rows(), _row("r3", "architect")], _base_rules())
    code, _, err, merged = self.merge(BASE, ours, theirs)
    self.assertEqual(code, 0, err)
    doc = json.loads(merged)
    self.assertEqual([r["id"] for r in doc["rows"]], ["r1", "r2", "r4"])
    self.assertEqual(doc["rows"][2]["role"], "architect")
    self.assertEqual(doc["next_row"], 5)
    self.assertIn("now r4", err)

  def test_MergeRelayRoles_OursIssuedThenRemovedARuleId_TheirsDoesNotTakeItBack(self) -> None:
    ours = _matrix(_base_rows(), _base_rules(), next_rule=4)
    theirs = _matrix(_base_rows(), [*_base_rules(), RoleRule(id="u3", text="theirs rule")])
    doc = self.clean(BASE, ours, theirs)
    self.assertEqual([(r["id"], r["text"]) for r in doc["rules"]][2:], [("u4", "theirs rule")])
    self.assertEqual(doc["next_rule"], 5)

  def test_MergeRelayRoles_OnlyTheirsAddsARow_KeepsItsIdAndCounter(self) -> None:
    theirs = _matrix([*_base_rows(), _row("r3", "gate")], _base_rules())
    doc = self.clean(BASE, BASE, theirs)
    self.assertEqual([r["id"] for r in doc["rows"]], ["r1", "r2", "r3"])
    self.assertEqual(doc["next_row"], 4)

  def test_MergeRelayRoles_BothAddTheSameRow_KeepsItOnce(self) -> None:
    both = _matrix([*_base_rows(), _row("r3", "gate", model="same")], _base_rules())
    doc = self.clean(BASE, both, both)
    self.assertEqual([r["id"] for r in doc["rows"]], ["r1", "r2", "r3"])
    self.assertEqual(doc["next_row"], 4)

  def test_MergeRelayRoles_CounterOnlyOneSideMoved_TakesTheLarger(self) -> None:
    # Theirs added and removed a row, so only its counter moved.
    theirs = _matrix(_base_rows(), _base_rules(), next_row=4)
    ours = _matrix([*_base_rows(), _row("r3", "gate")], _base_rules())
    doc = self.clean(BASE, ours, theirs)
    self.assertEqual(doc["next_row"], 4)
    self.assertEqual([r["id"] for r in doc["rows"]], ["r1", "r2", "r3"])

  def test_MergeRelayRoles_EmptyBase_BothSidesCreatedTheFile(self) -> None:
    # Git hands an empty %O when both branches added the file.
    ours = _matrix([_row("r1", "gate", model="a")])
    theirs = _matrix([_row("r1", "architect", model="b")])
    doc = self.clean("", ours, theirs)
    self.assertEqual([(r["id"], r["model"]) for r in doc["rows"]], [("r1", "a"), ("r2", "b")])
    self.assertEqual(doc["next_row"], 3)


class RowEditTests(MergeRelayRolesCase):
  def test_MergeRelayRoles_EachSideEditsADifferentRow_KeepsBothEdits(self) -> None:
    ours = _matrix([_row("r1", "implementer", model="sonnet"), _base_rows()[1]], _base_rules())
    theirs = _matrix([_base_rows()[0], _row("r2", "reviewer", model="opus", reasoning="high")], _base_rules())
    doc = self.clean(BASE, ours, theirs)
    self.assertEqual(doc["rows"][0]["model"], "sonnet")
    self.assertEqual(doc["rows"][1]["reasoning"], "high")

  def test_MergeRelayRoles_EachSideEditsADifferentFieldOfOneRow_KeepsBoth(self) -> None:
    ours = _matrix(
      [_row("r1", "implementer", model="sonnet"), _base_rows()[1]], _base_rules(),
    )
    theirs = _matrix(
      [_row("r1", "implementer", model="haiku", when=RoleWhen(size=["S"])), _base_rows()[1]],
      _base_rules(),
    )
    doc = self.clean(BASE, ours, theirs)
    self.assertEqual(doc["rows"][0]["model"], "sonnet")
    self.assertEqual(doc["rows"][0]["when"]["size"], ["S"])

  def test_MergeRoles_SliverField_Kept(self) -> None:
    ours = _matrix(
      [_row("r1", "implementer", model="sonnet"), _base_rows()[1]], _base_rules(),
    )
    theirs = _matrix(
      [_row("r1", "implementer", model="haiku", when=RoleWhen(sliver=True)), _base_rows()[1]],
      _base_rules(),
    )
    doc = self.clean(BASE, ours, theirs)
    self.assertEqual(doc["rows"][0]["model"], "sonnet")
    self.assertIs(doc["rows"][0]["when"]["sliver"], True)
    # Both sides changing it differently is a disagreement about that field alone.
    other = _matrix(
      [_row("r1", "implementer", model="haiku", when=RoleWhen(sliver=False)), _base_rows()[1]],
      _base_rules(),
    )
    message = self.refused(BASE, other, theirs, "merge_conflict")
    self.assertIn("rows/r1/when/sliver", message)

  def test_MergeRoles_MatrixWrittenBeforeSlivers_MergesAndGainsTheField(self) -> None:
    def old(text: str) -> str:
      doc = json.loads(text)
      for row in doc["rows"]:
        del row["when"]["sliver"]
      return jsonio.dumps(doc)

    theirs = _matrix(
      [_row("r1", "implementer", model="haiku", when=RoleWhen(sliver=True)), _base_rows()[1]],
      _base_rules(),
    )
    doc = self.clean(old(BASE), old(BASE), theirs)
    self.assertIs(doc["rows"][0]["when"]["sliver"], True)
    self.assertIsNone(doc["rows"][1]["when"]["sliver"])

  def test_MergeRelayRoles_BothSetTheSameFieldToTheSameValue_KeepsIt(self) -> None:
    edited = _matrix([_row("r1", "implementer", model="sonnet"), _base_rows()[1]], _base_rules())
    self.assertEqual(self.clean(BASE, edited, edited)["rows"][0]["model"], "sonnet")

  def test_MergeRelayRoles_SameFieldOfSameRowDiffers_IsRefusedAndWritesNothing(self) -> None:
    ours = _matrix([_row("r1", "implementer", model="sonnet"), _base_rows()[1]], _base_rules())
    # Theirs also edits another row cleanly, so a half-merge would differ from ours.
    theirs = _matrix(
      [_row("r1", "implementer", model="opus"), _row("r2", "reviewer", model="opus", reasoning="high")],
      _base_rules(),
    )
    message = self.refused(BASE, ours, theirs, "merge_conflict")
    self.assertIn("rows/r1/model", message)

  def test_MergeRelayRoles_SameFieldDiffersOnTheCommandLine_ExitsTwoAndLeavesOurs(self) -> None:
    # Both sides also added a row under one id, so a renumbering was on the way.
    ours = _matrix(
      [_row("r1", "implementer", model="sonnet"), _base_rows()[1], _row("r3", "gate")], _base_rules(),
    )
    theirs = _matrix(
      [_row("r1", "implementer", model="opus"), _base_rows()[1], _row("r3", "architect")],
      _base_rules(),
    )
    code, out, err, merged = self.merge(BASE, ours, theirs)
    self.assertEqual((code, out), (2, ""))
    self.assertIn("rows/r1/model", err)
    # A merge that wrote nothing must not announce a renumbering.
    self.assertNotIn("now r", err)
    self.assertEqual(merged, ours)

  def test_MergeRelayRoles_EditOnOneSideRemovalOnTheOther_IsRefused(self) -> None:
    ours = _matrix([_row("r1", "implementer", model="sonnet"), _base_rows()[1]], _base_rules())
    theirs = _matrix([_base_rows()[1]], _base_rules(), next_row=3)
    message = self.refused(BASE, ours, theirs, "merge_conflict")
    self.assertIn("rows/r1", message)

  def test_MergeRelayRoles_OneSideRemovesAnUntouchedRow_StaysRemoved(self) -> None:
    ours = _matrix([_base_rows()[0]], _base_rules(), next_row=3)
    theirs = _matrix([_row("r1", "implementer", model="sonnet"), _base_rows()[1]], _base_rules())
    doc = self.clean(BASE, ours, theirs)
    self.assertEqual([(r["id"], r["model"]) for r in doc["rows"]], [("r1", "sonnet")])
    self.assertEqual(doc["next_row"], 3)

  def test_MergeRelayRoles_BothMoveRowsDifferently_IsRefused(self) -> None:
    ours = _matrix([_base_rows()[1], _base_rows()[0], _row("r3")], _base_rules())
    base = _matrix([*_base_rows(), _row("r3")], _base_rules())
    theirs = _matrix([_base_rows()[0], _row("r3"), _base_rows()[1]], _base_rules())
    message = self.refused(base, ours, theirs, "merge_conflict")
    self.assertIn("rows/order", message)

  def test_MergeRelayRoles_OneSideMovesARow_TheMoveIsKept(self) -> None:
    base = _matrix([*_base_rows(), _row("r3")], _base_rules())
    ours = _matrix([_base_rows()[1], _base_rows()[0], _row("r3")], _base_rules())
    doc = self.clean(base, ours, base)
    self.assertEqual([r["id"] for r in doc["rows"]], ["r2", "r1", "r3"])


class RuleTests(MergeRelayRolesCase):
  def test_MergeRelayRoles_BothSidesAddARule_KeepsBothAndRenumbersTheirs(self) -> None:
    ours = _matrix(_base_rows(), [*_base_rules(), RoleRule(id="u3", text="ours rule")])
    theirs = _matrix(_base_rows(), [*_base_rules(), RoleRule(id="u3", text="theirs rule")])
    code, _, err, merged = self.merge(BASE, ours, theirs)
    self.assertEqual(code, 0, err)
    doc = json.loads(merged)
    self.assertEqual(
      [(r["id"], r["text"]) for r in doc["rules"]],
      [("u1", "first rule"), ("u2", "second rule"), ("u3", "ours rule"), ("u4", "theirs rule")],
    )
    self.assertEqual((doc["next_row"], doc["next_rule"]), (3, 5))
    self.assertIn("u3", err)
    self.assertIn("u4", err)

  def test_MergeRelayRoles_RowAndRuleAddedOnDifferentSides_BothKeepTheirIds(self) -> None:
    ours = _matrix([*_base_rows(), _row("r3", "gate")], _base_rules())
    theirs = _matrix(_base_rows(), [*_base_rules(), RoleRule(id="u3", text="theirs rule")])
    doc = self.clean(BASE, ours, theirs)
    self.assertEqual([r["id"] for r in doc["rows"]], ["r1", "r2", "r3"])
    self.assertEqual([r["id"] for r in doc["rules"]], ["u1", "u2", "u3"])
    self.assertEqual((doc["next_row"], doc["next_rule"]), (4, 4))

  def test_MergeRelayRoles_EachSideEditsADifferentRule_KeepsBothEdits(self) -> None:
    ours = _matrix(_base_rows(), [RoleRule(id="u1", text="ours edit"), _base_rules()[1]])
    theirs = _matrix(_base_rows(), [_base_rules()[0], RoleRule(id="u2", text="theirs edit")])
    doc = self.clean(BASE, ours, theirs)
    self.assertEqual([r["text"] for r in doc["rules"]], ["ours edit", "theirs edit"])

  def test_MergeRelayRoles_SameRuleEditedDifferently_IsRefusedAndWritesNothing(self) -> None:
    ours = _matrix(_base_rows(), [RoleRule(id="u1", text="ours edit"), _base_rules()[1]])
    theirs = _matrix(_base_rows(), [RoleRule(id="u1", text="theirs edit"), RoleRule(id="u2", text="theirs u2")])
    message = self.refused(BASE, ours, theirs, "merge_conflict")
    self.assertIn("rules/u1/text", message)


class UnreadableInputTests(MergeRelayRolesCase):
  def test_MergeRelayRoles_TheirsIsNotJson_IsRefusedAsCorrupt(self) -> None:
    self.refused(BASE, BASE, "not json {", "corrupt")

  def test_MergeRelayRoles_OursIsNotAMatrix_IsRefusedAsCorrupt(self) -> None:
    self.refused(BASE, '{"version": 1}\n', BASE, "corrupt")

  def test_MergeRelayRoles_CounterAtOrBelowAnIdInUse_IsRefusedAsCorrupt(self) -> None:
    low_row = _matrix(_base_rows(), _base_rules(), next_row=2)
    low_rule = _matrix(_base_rows(), _base_rules(), next_rule=2)
    for side, text in (("ours", low_row), ("theirs", low_rule)):
      with self.subTest(side=side):
        ours, theirs = (text, BASE) if side == "ours" else (BASE, text)
        message = self.refused(BASE, ours, theirs, "corrupt")
        self.assertIn(f"the {side} version", message)
        self.assertIn("at or below an id in use", message)

  def test_MergeRelayRoles_VersionOneSideWithCurrentSide_MergesIntoCurrentVersion(self) -> None:
    def v1(text: str) -> str:
      doc = json.loads(text)
      doc["version"] = 1
      for row in doc["rows"]:
        del row["when"]["sliver"]
      return jsonio.dumps(doc)

    theirs = _matrix(
      [_row("r1", "implementer", model="haiku", when=RoleWhen(sliver=True)), _base_rows()[1]],
      _base_rules(),
    )
    ours = _matrix([_row("r1", "implementer", model="sonnet"), _base_rows()[1]], _base_rules())
    doc = self.clean(v1(BASE), v1(ours), theirs)
    self.assertEqual(doc["version"], roles.ROLES_VERSION)
    self.assertEqual(doc["rows"][0]["model"], "sonnet")
    self.assertIs(doc["rows"][0]["when"]["sliver"], True)
    # And the other way round: ours is current, theirs predates the sliver condition.
    doc = self.clean(v1(BASE), theirs, v1(ours))
    self.assertEqual(doc["version"], roles.ROLES_VERSION)
    self.assertEqual(doc["rows"][0]["model"], "sonnet")
    self.assertIs(doc["rows"][0]["when"]["sliver"], True)
    # Two version 1 sides merge into a current-version file: the driver always writes current.
    only_theirs = _matrix(
      [_base_rows()[0], _row("r2", "reviewer", model="opus", reasoning="high")], _base_rules(),
    )
    doc = self.clean(v1(BASE), v1(ours), v1(only_theirs))
    self.assertEqual(doc["version"], roles.ROLES_VERSION)
    self.assertEqual((doc["rows"][0]["model"], doc["rows"][1]["reasoning"]), ("sonnet", "high"))

  def test_MergeRelayRoles_OrchestratorRows_SurviveTheMerge(self) -> None:
    ours = _matrix([*_base_rows(), _row("r3", "orchestrator", model="opus", reasoning="high")], _base_rules())
    theirs = _matrix([*_base_rows(), _row("r3", "orchestrator", model="sol", reasoning="high")], _base_rules())
    code, out, err, merged = self.merge(BASE, ours, theirs)
    self.assertEqual((code, out), (0, ""))
    doc = json.loads(merged)
    self.assertEqual(
      [(r["id"], r["role"], r["model"]) for r in doc["rows"][2:]],
      [("r3", "orchestrator", "opus"), ("r4", "orchestrator", "sol")],
    )
    self.assertIn("row r3 was issued on both sides", err)

  def test_MergeRelayRoles_PreviousVersionOnOneSide_MergesWithAnOrchestratorRowFromTheOther(self) -> None:
    def previous(text: str) -> str:
      return jsonio.dumps({**json.loads(text), "version": roles.ROLES_VERSION - 1})

    orchestrator = _row("r3", "orchestrator", model="opus", reasoning="high")
    new = _matrix([*_base_rows(), orchestrator], _base_rules())
    edited = _matrix([_row("r1", "implementer", model="sonnet"), _base_rows()[1]], _base_rules())
    # ours predates the role, theirs adds a row of it; and the reverse; and only the base is old.
    for base, ours, theirs in (
      (previous(BASE), previous(edited), new),
      (previous(BASE), new, previous(edited)),
      (previous(BASE), edited, new),
    ):
      with self.subTest(base=json.loads(base)["version"], ours=json.loads(ours)["version"]):
        doc = self.clean(base, ours, theirs)
        self.assertEqual(doc["version"], roles.ROLES_VERSION)
        self.assertEqual(doc["rows"][0]["model"], "sonnet")
        self.assertEqual([r["role"] for r in doc["rows"]][2:], ["orchestrator"])
        self.assertEqual(doc["next_row"], 4)

  def test_MergeRelayRoles_AllSidesOnThePreviousVersion_WritesTheCurrentVersion(self) -> None:
    def previous(text: str) -> str:
      return jsonio.dumps({**json.loads(text), "version": roles.ROLES_VERSION - 1})

    ours = _matrix([_row("r1", "implementer", model="sonnet"), _base_rows()[1]], _base_rules())
    theirs = _matrix([_base_rows()[0], _row("r2", "reviewer", model="opus", reasoning="high")], _base_rules())
    doc = self.clean(previous(BASE), previous(ours), previous(theirs))
    self.assertEqual(doc["version"], roles.ROLES_VERSION)
    self.assertEqual((doc["rows"][0]["model"], doc["rows"][1]["reasoning"]), ("sonnet", "high"))

  def test_MergeRelayRoles_TheirsIsFromANewerSlicer_IsRefused(self) -> None:
    newer = json.dumps({**json.loads(BASE), "version": 99}) + "\n"
    self.refused(BASE, BASE, newer, "schema_too_new")

  def test_MergeRelayRoles_NeedsNoProject(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      with patch("slicer.cli.store.discover", side_effect=AssertionError("discover")), \
           patch("slicer.cli.store.load", side_effect=AssertionError("load")), \
           patch("slicer.cli.store.project_lock", side_effect=AssertionError("lock")):
        for name in ("base", "ours", "theirs"):
          repo.write(name, BASE)
        code, out, err = repo.run(
          "merge-relay-roles", *(str(repo.root / n) for n in ("base", "ours", "theirs")),
          allow_parent_project=True,
        )
      self.assertEqual((code, out, err), (0, "", ""))
      self.assertEqual(repo.read("ours"), BASE)


class GitattributesTests(unittest.TestCase):
  def test_Init_WritesGitattributes_PointingRelayRolesAtTheDriver(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      text = repo.read(f".slicer/{store.GITATTRIBUTES_NAME}")
      self.assertIn("\nrelay-roles.json merge=slicer-relay-roles\n", text)
      self.assertIn(
        '#   git config merge.slicer-relay-roles.driver "slicer merge-relay-roles %O %A %B"', text,
      )

  def test_SetupGit_PrintsTheRelayRolesDriverLines(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      out = repo.run("setup-git")[1]
      self.assertIn(
        'git config merge.slicer-relay-roles.name "keep both sides\' relay roles rows and '
        'rules with fresh ids"', out,
      )
      self.assertIn(
        'git config merge.slicer-relay-roles.driver "slicer merge-relay-roles %O %A %B"', out,
      )

  def test_SetupGit_Attributes_MapRelayRolesJsonToTheDriver(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      found = repo._git("check-attr", "merge", "--", ".slicer/relay-roles.json")
      self.assertEqual(
        found.stdout.strip(), ".slicer/relay-roles.json: merge: slicer-relay-roles",
      )


class RelayRolesDriverEndToEndTests(unittest.TestCase):
  """Two branches that each `relay-roles add` a row merge through Git with no conflict."""

  def _configure(self, repo: support.TempRepo) -> None:
    repo._git("config", "merge.slicer-generated.driver", "true")
    repo._git("config", "merge.slicer-index.driver",
              f"{sys.executable} -m slicer merge-index %O %A %B")
    repo._git("config", "merge.slicer-relay-roles.driver",
              f"{sys.executable} -m slicer merge-relay-roles %O %A %B")

  def _diverge(self, repo: support.TempRepo) -> str:
    repo.run("init")
    self.assertEqual(repo.run("relay-roles", "init", "--render")[0], 0)
    repo.commit("base")
    trunk = repo._git("branch", "--show-current").stdout.strip()
    repo._git("checkout", "-q", "-b", "feature")
    self.assertEqual(repo.run(
      "relay-roles", "add", "--role", "gate", "--model", "feature-gate", "--render")[0], 0)
    self.assertEqual(repo.run("relay-roles", "rule-add", "feature rule", "--render")[0], 0)
    repo.commit("feature")
    repo._git("checkout", "-q", trunk)
    self.assertEqual(repo.run(
      "relay-roles", "add", "--role", "architect", "--model", "trunk-arch", "--render")[0], 0)
    self.assertEqual(repo.run("relay-roles", "rule-add", "trunk rule", "--render")[0], 0)
    repo.commit("trunk")
    return trunk

  def test_Merge_BothBranchesAddARow_KeepsBothWithDistinctIdsAndCheckPasses(self) -> None:
    with support.TempRepo(git=True) as repo, \
         patch.dict(os.environ, {"PYTHONPATH": str(SRC), "SLICER_NO_CODE_WARNING": "1"}):
      self._configure(repo)
      self._diverge(repo)
      merged = repo._git("merge", "--no-edit", "feature")
      self.assertEqual(merged.returncode, 0, merged.stdout + merged.stderr)
      self.assertNotIn("<<<<<<<", repo.read(".slicer/relay-roles.json"))
      matrix = store.load(repo.root).roles
      self.assertEqual(
        [(r.id, r.model) for r in matrix.rows[-2:]],
        [("r12", "trunk-arch"), ("r13", "feature-gate")],
      )
      self.assertEqual([r.id for r in matrix.rules[-2:]], ["u9", "u10"])
      self.assertEqual((matrix.next_row, matrix.next_rule), (14, 11))
      # History is not rewritten: both branches' original add entries are still there.
      log = [
        json.loads(line) for line in repo.read(".slicer/log.jsonl").splitlines()
        if line.strip()
      ]
      adds = [e for e in log if e["item"] == "relay-roles.r12" and e["note"] == "add"]
      self.assertEqual(sorted(e["to"] for e in adds), ["architect", "gate"])
      self.assertEqual(repo.run("render")[0], 0)
      code, out, err = repo.run("check")
      self.assertEqual(code, 0, out + err)

  def test_Merge_SameFieldEditedOnBothBranches_LeavesOursAndConflicts(self) -> None:
    with support.TempRepo(git=True) as repo, \
         patch.dict(os.environ, {"PYTHONPATH": str(SRC), "SLICER_NO_CODE_WARNING": "1"}):
      self._configure(repo)
      repo.run("init")
      repo.run("relay-roles", "init", "--render")
      repo.commit("base")
      trunk = repo._git("branch", "--show-current").stdout.strip()
      repo._git("checkout", "-q", "-b", "feature")
      repo.run("relay-roles", "set", "r1", "--model", "feature-model", "--render")
      repo.commit("feature")
      repo._git("checkout", "-q", trunk)
      repo.run("relay-roles", "set", "r1", "--model", "trunk-model", "--render")
      repo.commit("trunk")
      ours = repo.read(".slicer/relay-roles.json")
      merge = repo._git("merge", "--no-edit", "feature")
      self.assertNotEqual(merge.returncode, 0)
      self.assertIn("conflict at rows/r1/model", merge.stdout + merge.stderr)
      self.assertIn("UU .slicer/relay-roles.json", repo._git("status", "--porcelain").stdout)
      self.assertEqual(repo.read(".slicer/relay-roles.json"), ours)
      self.assertEqual(store.load(repo.root).roles.row("r1").model, "trunk-model")


if __name__ == "__main__":
  unittest.main()
