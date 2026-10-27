"""The relay role matrix is versioned JSON that only `slicer relay-roles` changes (s257).

The tests drive the CLI in-process and read the files it writes. Tests that need a
hand-edited or corrupt matrix write the JSON fixture directly; production code never
does, because `slicer relay-roles` is the only way to change it.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import unittest
from pathlib import Path
from unittest.mock import patch

import support  # noqa: F401 -- puts src/ on sys.path
from slicer import ai, cli, jsonio, model, render, roles, store

JSON = ".slicer/relay-roles.json"
RENDER = ".slicer/render/RELAY-ROLES.md"
LEGACY = ".slicer/relay-roles.md"


class RelayRolesCase(unittest.TestCase):
  def project(self) -> support.TempRepo:
    repo = support.TempRepo()
    self.addCleanup(repo.close)
    self.assertEqual(repo.run("init")[0], 0)
    return repo

  def with_matrix(self) -> support.TempRepo:
    repo = self.project()
    self.ok(repo, "relay-roles", "init", "--render")
    return repo

  def ok(self, repo: support.TempRepo, *argv: str) -> str:
    code, out, err = repo.run(*argv)
    self.assertEqual((code, err), (0, ""), f"{argv}: {out}")
    return out

  def refuse(self, repo: support.TempRepo, *argv: str, code: int, error: str) -> dict:
    got, out, err = repo.run(*argv, "--json")
    self.assertEqual(got, code, f"{argv}: {out} {err}")
    payload = json.loads(out)["error"]
    self.assertEqual(payload["code"], error)
    return payload

  def matrix(self, repo: support.TempRepo) -> dict:
    return json.loads(repo.read(JSON))

  def snapshot(self, repo: support.TempRepo) -> dict[str, bytes]:
    return {
      str(p.relative_to(repo.root)): p.read_bytes()
      for p in repo.root.rglob("*")
      if p.is_file() and ".git" not in p.parts and p.name != "lock"  # the lock file is scratch
    }

  def history(self, repo: support.TempRepo) -> list:
    return [e for e in repo.state().history() if e.action == "relay-roles"]


class LedgerRoleNamesTests(unittest.TestCase):
  def test_RoleNames_MatrixRolesUnchanged_LedgerOnlyRolesAppendedToTheLedgerSet(self) -> None:
    self.assertEqual(roles.ROLE_NAMES, ("implementer", "reviewer", "gate", "architect", "orchestrator"))
    self.assertEqual(roles.LEDGER_ONLY_ROLES, ("ui", "spec-check"))
    self.assertEqual(roles.LEDGER_ROLE_NAMES, roles.ROLE_NAMES + roles.LEDGER_ONLY_ROLES)

  def test_RoleFromDict_LedgerOnlyRole_IsRefused(self) -> None:
    for role in ("ui", "spec-check"):
      with self.subTest(role=role), self.assertRaises(ValueError):
        roles.RoleRow.from_dict({
          "id": "r1", "role": role, "when": roles.RoleWhen().to_dict(), "agent": "", "vendor": "",
          "model": "", "reasoning": "", "note": "",
        })


class RoleModelTests(unittest.TestCase):
  def test_RoleMatrix_RoundTrip_IsIdentityWithFixedKeyOrder(self) -> None:
    matrix = roles.skeleton()
    data = matrix.to_dict()
    self.assertEqual(roles.RoleMatrix.from_dict(data).to_dict(), data)
    self.assertEqual(list(data), ["version", "rows", "rules", "next_row", "next_rule"])
    self.assertEqual(
      list(data["rows"][0]),
      ["id", "role", "when", "agent", "vendor", "model", "reasoning", "note"],
    )
    self.assertEqual(
      list(data["rows"][0]["when"]),
      ["size", "effort", "importance", "urgency", "well_specified", "split", "sliver"],
    )
    self.assertEqual(list(data["rules"][0]), ["id", "text"])
    self.assertEqual(json.dumps(roles.RoleMatrix.from_dict(data).to_dict()), json.dumps(data))

  def test_Skeleton_FirstRowAndFirstRule_NumberTheExampleFromThere(self) -> None:
    matrix = roles.skeleton(first_row=8, first_rule=6)
    self.assertEqual([r.id for r in matrix.rules], [f"u{n}" for n in range(6, 14)])
    self.assertEqual((matrix.next_row, matrix.next_rule), (19, 14))
    plain = roles.skeleton()
    self.assertEqual(
      [r.id for r in plain.rows],
      ["r8", "r1", "r2", "r3", "r4", "r9", "r5", "r6", "r7", "r10", "r11"],
    )
    self.assertEqual(
      [r.id for r in matrix.rows],
      ["r15", "r8", "r9", "r10", "r11", "r16", "r12", "r13", "r14", "r17", "r18"],
    )
    self.assertEqual(
      [(r.role, r.when.to_dict()) for r in matrix.rows],
      [(r.role, r.when.to_dict()) for r in plain.rows],
    )

  def test_Skeleton_HoldsElevenStructuredRowsAndEightRules(self) -> None:
    matrix = roles.skeleton()
    self.assertEqual(
      [(r.id, r.role) for r in matrix.rows],
      [("r8", "implementer"), ("r1", "implementer"), ("r2", "implementer"),
       ("r3", "implementer"), ("r4", "implementer"), ("r9", "implementer"),
       ("r5", "reviewer"), ("r6", "gate"), ("r7", "architect"),
       ("r10", "orchestrator"), ("r11", "orchestrator")],
    )
    claude = [r for r in matrix.rows if r.id not in ("r9", "r11")]
    self.assertEqual({r.agent for r in claude}, {"Claude Code"})
    self.assertEqual({r.vendor for r in claude}, {"Claude"})
    alternate, default, codex = matrix.rows[5], matrix.rows[9], matrix.rows[10]
    self.assertEqual(
      (alternate.agent, alternate.vendor, alternate.model, alternate.reasoning, alternate.note),
      ("Codex", "OpenAI", "gpt-6.1-sol", "medium",
       "alternate implementer for any slice, split part or sliver"),
    )
    self.assertEqual(alternate.when.to_dict(), roles.RoleWhen().to_dict())
    self.assertEqual(
      (default.agent, default.vendor, default.model, default.reasoning, default.note),
      ("Claude Code", "Claude", "opus", "high", "default orchestrator"),
    )
    self.assertEqual(default.when.to_dict(), roles.RoleWhen().to_dict())
    self.assertEqual(
      (codex.agent, codex.vendor, codex.model, codex.reasoning, codex.note),
      ("Codex", "OpenAI", "gpt-6.1-sol", "high", "alternate orchestrator"),
    )
    self.assertEqual(codex.when.to_dict(), roles.RoleWhen().to_dict())
    first = matrix.rows[1]
    self.assertEqual(first.when.size, ["S", "M"])
    self.assertEqual(first.when.effort, [1])
    self.assertIs(first.when.well_specified, True)
    self.assertEqual(
      matrix.rows[3].when.to_dict(), roles.RoleWhen(split=False).to_dict(),
    )
    self.assertEqual(matrix.rows[8].when.size, ["L"])  # r7 stays size L only
    self.assertEqual(
      [rule.text for rule in matrix.rules][2],
      "Roles on the orchestrator's own agent run as subagents; a role on another agent "
      "runs headless in the slice worktree with the same brief.",
    )
    self.assertEqual([rule.id for rule in matrix.rules], [f"u{n}" for n in range(1, 9)])
    self.assertEqual((matrix.next_row, matrix.next_rule), (12, 9))

  def test_Skeleton_ImplementerRows_GeneralOnesAreWholeSliceAndPartRowIsSplit(self) -> None:
    matrix = roles.skeleton()
    self.assertEqual(
      [(r.id, r.when.split) for r in matrix.rows],
      [("r8", None), ("r1", False), ("r2", False), ("r3", False), ("r4", True),
       ("r9", None), ("r5", None), ("r6", None), ("r7", None), ("r10", None), ("r11", None)],
    )
    part = matrix.rows[4]
    self.assertEqual(
      (part.role, part.agent, part.vendor, part.model, part.reasoning),
      ("implementer", "Claude Code", "Claude", "haiku", "xhigh"),
    )
    self.assertIn("per part", part.note)
    self.assertEqual(part.when.to_dict(), roles.RoleWhen(split=True).to_dict())

  def test_RoleWhen_Sliver_RoundTrips(self) -> None:
    for value in (True, False, None):
      with self.subTest(value=value):
        when = roles.RoleWhen(sliver=value, effort=[1])
        data = when.to_dict()
        self.assertEqual(data["sliver"], value)
        self.assertEqual(roles.RoleWhen.from_dict(data).sliver, value)
        self.assertEqual(roles.RoleWhen.from_dict(data).to_dict(), data)
    with self.assertRaises(TypeError):
      roles.RoleWhen.from_dict(roles.RoleWhen().to_dict() | {"sliver": "yes"})
    # A matrix written before slivers existed has no key; it loads as "either".
    old = {k: v for k, v in roles.RoleWhen().to_dict().items() if k != "sliver"}
    self.assertIsNone(roles.RoleWhen.from_dict(old).sliver)

  def test_RoleWhen_SliverDescribe_NamesSliverAndNotASliver(self) -> None:
    self.assertEqual(roles.RoleWhen(sliver=True).describe(), "sliver")
    self.assertEqual(roles.RoleWhen(sliver=False).describe(), "not a sliver")
    self.assertEqual(roles.RoleWhen(sliver=None).describe(), "every slice")
    self.assertEqual(
      roles.RoleWhen(well_specified=True, split=False, sliver=True, effort=[1]).describe(),
      "well specified, whole slice, sliver, effort 1",
    )

  def test_Skeleton_SliverRow_BeforeGeneralImplementers(self) -> None:
    matrix = roles.skeleton()
    implementers = [r for r in matrix.rows if r.role == "implementer"]
    first = implementers[0]
    self.assertEqual(first.id, "r8")
    self.assertEqual(first.when.to_dict(), roles.RoleWhen(sliver=True).to_dict())
    self.assertEqual(
      (first.agent, first.vendor, first.model, first.reasoning, first.note),
      ("Claude Code", "Claude", "haiku", "xhigh", "per sliver"),
    )
    # The earlier ids keep their meaning and none of them asks about slivers.
    self.assertEqual([r.id for r in implementers[1:]], ["r1", "r2", "r3", "r4", "r9"])
    self.assertEqual({r.when.sliver for r in implementers[1:]}, {None})
    self.assertEqual(matrix.next_row, 12)

  def test_Skeleton_SliverRules_AreAfterTheOriginalRules(self) -> None:
    rules = {r.id: r.text for r in roles.skeleton().rules}
    self.assertEqual(list(rules), [f"u{n}" for n in range(1, 9)])
    self.assertEqual(
      rules["u6"],
      "Sliver a slice only at effort 1, or at effort 2 when every sliver is effort 1; "
      "never at effort 3.",
    )
    self.assertEqual(
      rules["u7"],
      "Slivers with disjoint files and their own tests may run in parallel, at most 3 at "
      "once; the rest run in order.",
    )
    self.assertEqual(
      rules["u8"],
      "A sliver that fails its gate twice, or whose diff falls short of the orchestrator's "
      "check, moves to the next model up (haiku to sonnet high).",
    )

  def test_Skeleton_Rules_NameTheSplitRule(self) -> None:
    rules = {r.id: r.text for r in roles.skeleton().rules}
    self.assertEqual(
      rules["u5"],
      "Split a slice only into parts with disjoint files and their own tests; "
      "a slice too big for one implementer is two slices or a slivered slice.",
    )

  def test_RenderMarkdown_Skeleton_ShowsGeneratedWhenCells(self) -> None:
    text = roles.render_markdown(roles.skeleton())
    self.assertIn("| Role | When | Agent | Vendor | Model | Reasoning | Note |", text)
    self.assertIn("| ID | Role | When | Agent | Vendor | Model | Reasoning | Note |", text)
    self.assertIn(
      "| r1 | Implementer | well specified, whole slice, effort 1, size S or M | Claude Code | Claude | haiku "
      "| xhigh |",
      text,
    )
    self.assertIn("| r7 | Architect | size L | Claude Code |", text)
    self.assertIn("| r6 | Gate | every slice | Claude Code |", text)
    self.assertIn("\n- u1: A reviewer never wrote code in the slice it reviews.\n", text)
    self.assertEqual(roles.render_markdown(roles.skeleton()), text)
    self.assertTrue(text.endswith("\n") and not text.endswith("\n\n"))

  def test_RenderMarkdown_Level2_DropsTheTitleAndDemotesTheHeadings(self) -> None:
    top = roles.render_markdown(roles.skeleton())
    embedded = roles.render_markdown(roles.skeleton(), level=2)
    self.assertTrue(top.startswith("# Relay roles\n\n## Matrix\n"))
    self.assertIn("\n## Rules\n", top)
    self.assertNotIn("# Relay roles", embedded)
    self.assertTrue(embedded.startswith("### Matrix\n"))
    self.assertIn("\n### Rules\n", embedded)
    self.assertNotIn("\n## ", embedded)
    self.assertEqual(embedded.split("### Matrix\n", 1)[1], top.split("## Matrix\n", 1)[1].replace("## Rules", "### Rules"))

  def test_Describe_SplitCondition_ReadsInTheWhenCell(self) -> None:
    self.assertEqual(roles.RoleWhen(split=True).describe(), "split part")
    self.assertEqual(roles.RoleWhen(split=False).describe(), "whole slice")
    self.assertEqual(roles.RoleWhen(split=None).describe(), "every slice")
    self.assertEqual(
      roles.RoleWhen(well_specified=True, split=True, effort=[1], size=["S"]).describe(),
      "well specified, split part, effort 1, size S",
    )

  def test_RoleWhenFromDict_SplitMissingOrNotBool_IsRefused(self) -> None:
    good = roles.RoleWhen(split=True).to_dict()
    self.assertIs(roles.RoleWhen.from_dict(good).split, True)
    with self.assertRaises(TypeError):
      roles.RoleWhen.from_dict(good | {"split": "yes"})
    with self.assertRaises(KeyError):
      roles.RoleWhen.from_dict({k: v for k, v in good.items() if k != "split"})

  def test_RenderMarkdown_PipeInCell_IsEscaped(self) -> None:
    matrix = roles.skeleton()
    matrix.rows[0].note = "a|b"
    self.assertIn("a\\|b", roles.render_markdown(matrix))

  def test_FromDict_NewerVersion_IsSchemaTooNew(self) -> None:
    self.assertEqual(roles.ROLES_VERSION, 3)
    data = roles.skeleton().to_dict() | {"version": 4}
    with self.assertRaises(roles.StateError) as caught:
      roles.RoleMatrix.from_dict(data)
    self.assertEqual(caught.exception.code, "schema_too_new")

  def test_FromDict_VersionThree_IsCurrentAndLoads(self) -> None:
    data = roles.skeleton().to_dict()
    self.assertEqual(data["version"], 3)
    self.assertEqual(roles.RoleMatrix.from_dict(data).to_dict(), data)

  def test_FromDict_VersionTwoMatrix_LoadsUnchangedAndWritesVersionThree(self) -> None:
    data = roles.skeleton().to_dict() | {"version": 2}
    matrix = roles.RoleMatrix.from_dict(data)
    out = matrix.to_dict()
    self.assertEqual(out["version"], 3)
    self.assertEqual({k: v for k, v in out.items() if k != "version"},
                     {k: v for k, v in data.items() if k != "version"})

  def test_RoleNames_Orchestrator_IsLastAndTheFirstFourKeepTheirPlaces(self) -> None:
    self.assertEqual(
      roles.ROLE_NAMES, ("implementer", "reviewer", "gate", "architect", "orchestrator"),
    )
    row = roles.RoleRow.from_dict(roles.RoleRow(id="r1", role="orchestrator").to_dict())
    self.assertEqual(row.role, "orchestrator")

  def test_FromDict_VersionOneMatrix_LoadsWithSliverNoneAndWritesVersionTwo(self) -> None:
    data = roles.skeleton().to_dict() | {"version": 1}
    for row in data["rows"]:
      del row["when"]["sliver"]
    matrix = roles.RoleMatrix.from_dict(data)
    self.assertEqual({row.when.sliver for row in matrix.rows}, {None})
    out = matrix.to_dict()
    self.assertEqual(out["version"], roles.ROLES_VERSION)
    self.assertEqual({row["when"]["sliver"] for row in out["rows"]}, {None})


def _row(row_id: str, role: str, **when: object) -> roles.RoleRow:
  return roles.RoleRow(id=row_id, role=role, when=roles.RoleWhen(**when), agent="A")


def _ids(picks: list) -> list:
  return [row.id if row else None for _, row in picks]


def _item(**fields: object) -> model.Item:
  return model.Item(id="s1", title="T", status="open", **fields)


def _pick(rows: list[roles.RoleRow], item: model.Item, ws: bool = True, split: bool = False) -> dict:
  matrix = roles.RoleMatrix(rows=rows)
  return {role: (row.id if row else None) for role, row in roles.recommend(matrix, item, ws, split)}


class RoleRecommendTests(unittest.TestCase):
  def test_Recommend_RowsOfEveryRole_ReturnsRolesInRoleOrderWithTheFirstMatch(self) -> None:
    rows = [
      _row("r1", "architect"), _row("r2", "gate"), _row("r3", "reviewer"),
      _row("r4", "implementer", size=["L"]), _row("r5", "implementer"), _row("r6", "implementer"),
    ]
    rows.append(_row("r7", "orchestrator"))
    got = roles.recommend(roles.RoleMatrix(rows=rows), _item(size="S"), True, False)
    self.assertEqual([role for role, _ in got], list(roles.ROLE_NAMES))
    self.assertEqual([row.id for _, row in got], ["r5", "r3", "r2", "r1", "r7"])

  def test_Recommend_RowOrder_DecidesBetweenTwoMatchingRows(self) -> None:
    first = [_row("r1", "gate", size=["S"]), _row("r2", "gate")]
    self.assertEqual(_pick(first, _item(size="S"))["gate"], "r1")
    self.assertEqual(_pick(first[::-1], _item(size="S"))["gate"], "r2")

  def test_Recommend_NoRowForARole_IsNoneForThatRoleOnly(self) -> None:
    got = _pick([_row("r1", "gate"), _row("r2", "reviewer", size=["L"])], _item(size="S"))
    self.assertEqual(got, {
      "implementer": None, "reviewer": None, "gate": "r1", "architect": None, "orchestrator": None,
    })

  def test_Recommend_SizeCondition_MustContainTheSlicesSize(self) -> None:
    rows = [_row("r1", "gate", size=["S", "M"]), _row("r2", "gate")]
    self.assertEqual(_pick(rows, _item(size="M"))["gate"], "r1")
    self.assertEqual(_pick(rows, _item(size="L"))["gate"], "r2")

  def test_Recommend_EffortCondition_MustContainTheSlicesEffort(self) -> None:
    rows = [_row("r1", "gate", effort=[1, 2]), _row("r2", "gate")]
    self.assertEqual(_pick(rows, _item(effort=2))["gate"], "r1")
    self.assertEqual(_pick(rows, _item(effort=3))["gate"], "r2")

  def test_Recommend_ImportanceCondition_MustContainTheSlicesImportance(self) -> None:
    rows = [_row("r1", "gate", importance=[3]), _row("r2", "gate")]
    self.assertEqual(_pick(rows, _item(importance=3))["gate"], "r1")
    self.assertEqual(_pick(rows, _item(importance=1))["gate"], "r2")

  def test_Recommend_UrgencyCondition_MustContainTheSlicesUrgency(self) -> None:
    rows = [_row("r1", "gate", urgency=[3]), _row("r2", "gate")]
    self.assertEqual(_pick(rows, _item(urgency=3))["gate"], "r1")
    self.assertEqual(_pick(rows, _item(urgency=1))["gate"], "r2")

  def test_Recommend_UnsetSizeAndEffort_MatchOnlyEmptyConditions(self) -> None:
    rows = [
      _row("r1", "gate", size=["S"]), _row("r2", "gate", effort=[1, 2, 3]), _row("r3", "gate"),
    ]
    self.assertEqual(_pick(rows, _item())["gate"], "r3")
    self.assertEqual(_pick(rows[:2], _item())["gate"], None)

  def test_Recommend_WellSpecifiedCondition_TrueFalseAndNull(self) -> None:
    rows = [_row("r1", "gate", well_specified=True), _row("r2", "gate", well_specified=False)]
    self.assertEqual(_pick(rows, _item(), ws=True)["gate"], "r1")
    self.assertEqual(_pick(rows, _item(), ws=False)["gate"], "r2")
    either = [_row("r1", "gate", well_specified=None)]
    self.assertEqual(_pick(either, _item(), ws=True)["gate"], "r1")
    self.assertEqual(_pick(either, _item(), ws=False)["gate"], "r1")

  def test_Recommend_SplitCondition_TrueFalseAndNull(self) -> None:
    rows = [_row("r1", "gate", split=False), _row("r2", "gate", split=True)]
    self.assertEqual(_pick(rows, _item(), split=True)["gate"], "r2")
    self.assertEqual(_pick(rows, _item(), split=False)["gate"], "r1")
    either = [_row("r1", "gate", split=None)]
    self.assertEqual(_pick(either, _item(), split=True)["gate"], "r1")
    self.assertEqual(_pick(either, _item(), split=False)["gate"], "r1")

  def test_Recommend_SliverItem_PicksSliverRow(self) -> None:
    matrix = roles.skeleton()
    sliver = _item(size="S", effort=1, parent="s9")
    self.assertEqual(_ids(roles.recommend(matrix, sliver, True, False)), ["r8", "r5", "r6", None, "r10"])
    whole = _item(size="S", effort=1)
    self.assertEqual(_ids(roles.recommend(matrix, whole, True, False)), ["r1", "r5", "r6", None, "r10"])
    # The sliver row asks only about slivers, so an ill-specified sliver still gets it.
    self.assertEqual(
      _ids(roles.recommend(matrix, _item(size="M", effort=3, parent="s9"), False, False))[0], "r8",
    )

  def test_Recommend_SliverCondition_TrueFalseAndNull(self) -> None:
    rows = [_row("r1", "gate", sliver=True), _row("r2", "gate", sliver=False)]
    self.assertEqual(_pick(rows, _item(parent="s2"))["gate"], "r1")
    self.assertEqual(_pick(rows, _item())["gate"], "r2")
    either = [_row("r1", "gate", sliver=None)]
    self.assertEqual(_pick(either, _item(parent="s2"))["gate"], "r1")
    self.assertEqual(_pick(either, _item())["gate"], "r1")

  def test_Recommend_Skeleton_ProposesTheExampleRowsForSmallAndLargeSlices(self) -> None:
    matrix = roles.skeleton()
    small = roles.recommend(matrix, _item(size="S", effort=1), True, False)
    self.assertEqual(_ids(small), ["r1", "r5", "r6", None, "r10"])
    large = roles.recommend(matrix, _item(size="L", effort=3), False, False)
    self.assertEqual(_ids(large), ["r3", "r5", "r6", "r7", "r10"])

  def test_Recommend_Skeleton_SplitYesReturnsThePartRowAndSplitNoTheWholeSliceRows(self) -> None:
    matrix = roles.skeleton()
    item = _item(size="S", effort=1)
    part = roles.recommend(matrix, item, True, True)
    self.assertEqual(_ids(part), ["r4", "r5", "r6", None, "r10"])
    self.assertEqual(
      (part[0][1].model, part[0][1].reasoning), ("haiku", "xhigh"),
    )
    whole = roles.recommend(matrix, item, True, False)
    self.assertEqual(_ids(whole), ["r1", "r5", "r6", None, "r10"])
    # A whole slice that no specific row matches still falls to the general row, never the part row.
    other = roles.recommend(matrix, _item(size="M", effort=3), False, False)
    self.assertEqual(_ids(other), ["r3", "r5", "r6", None, "r10"])


def _agent_row(row_id: str, role: str, agent: str, **when: object) -> roles.RoleRow:
  return roles.RoleRow(id=row_id, role=role, when=roles.RoleWhen(**when), agent=agent)


def _resolved(rows: list[roles.RoleRow], item: model.Item | None = None, ws: bool = True,
              split: bool = False, **options: object) -> dict:
  matrix = roles.RoleMatrix(rows=rows)
  got = roles.resolve(matrix, item or _item(), ws, split, **options)
  return {pick.role: pick for pick in got}


def _alts(pick: roles.RolePick) -> list[str]:
  return [row.id for row in pick.alternatives]


class RoleResolveTests(unittest.TestCase):
  ROWS = [
    _agent_row("r1", "implementer", "Claude Code"),
    _agent_row("r2", "implementer", "Codex"),
    _agent_row("r3", "implementer", "Claude Code"),
    _agent_row("r4", "reviewer", "Claude Code"),
    _agent_row("r5", "reviewer", "Codex"),
    _agent_row("r6", "orchestrator", "Claude Code"),
    _agent_row("r7", "orchestrator", "Codex"),
  ]

  def test_Resolve_NoPreferenceOrSelection_TakesMatrixOrderAndListsTheRestAsAlternatives(self) -> None:
    got = _resolved(self.ROWS)
    self.assertEqual(list(got), list(roles.ROLE_NAMES))
    self.assertEqual(
      [(r, got[r].row.id if got[r].row else None, got[r].selected_by) for r in got],
      [("implementer", "r1", "matrix_order"), ("reviewer", "r4", "matrix_order"),
       ("gate", None, "unmatched"), ("architect", None, "unmatched"),
       ("orchestrator", "r6", "matrix_order")],
    )
    self.assertEqual(_alts(got["implementer"]), ["r2", "r3"])
    self.assertEqual(_alts(got["reviewer"]), ["r5"])
    self.assertEqual(_alts(got["orchestrator"]), ["r7"])

  def test_Resolve_NoMatchingRow_IsNullRowEmptyAlternativesAndUnmatched(self) -> None:
    pick = _resolved(self.ROWS)["gate"]
    self.assertEqual((pick.row, pick.alternatives, pick.selected_by), (None, [], "unmatched"))
    self.assertEqual(
      pick.to_dict(), {"role": "gate", "row": None, "alternatives": [], "selected_by": "unmatched"},
    )

  def test_Resolve_PreferredAgent_TakesItsFirstMatchingRowPerRole(self) -> None:
    got = _resolved(self.ROWS, prefer_agent="Codex")
    self.assertEqual(
      {r: (p.row.id, p.selected_by) for r, p in got.items() if p.row},
      {"implementer": ("r2", "preferred_agent"), "reviewer": ("r5", "preferred_agent"),
       "orchestrator": ("r7", "preferred_agent")},
    )
    # The rest stay in matrix order, with the selected row left out.
    self.assertEqual(_alts(got["implementer"]), ["r1", "r3"])
    self.assertEqual(_alts(got["orchestrator"]), ["r6"])

  def test_Resolve_PreferredAgent_IsTrimmedAndCasefolded(self) -> None:
    for spelled in ("codex", "  CODEX ", "Codex"):
      with self.subTest(spelled=spelled):
        self.assertEqual(_resolved(self.ROWS, prefer_agent=spelled)["implementer"].row.id, "r2")
    row = _resolved(self.ROWS, prefer_agent="codex")["implementer"].row
    self.assertEqual(row.agent, "Codex")  # the row keeps its own display text
    padded = [_agent_row("r1", "implementer", " codeX "), *self.ROWS[:1]]
    self.assertEqual(_resolved(padded, prefer_agent="CODEX")["implementer"].row.id, "r1")

  def test_Resolve_PreferredAgentWithNoMatchingRow_FallsBackToMatrixOrderForThatRole(self) -> None:
    rows = [r for r in self.ROWS if not (r.role == "reviewer" and r.agent == "Codex")]
    got = _resolved(rows, prefer_agent="Codex")
    self.assertEqual((got["reviewer"].row.id, got["reviewer"].selected_by), ("r4", "matrix_order"))
    self.assertEqual((got["implementer"].row.id, got["implementer"].selected_by), ("r2", "preferred_agent"))
    unknown = _resolved(self.ROWS, prefer_agent="Gemini")
    self.assertEqual({p.selected_by for p in unknown.values() if p.row}, {"matrix_order"})
    self.assertEqual(unknown["implementer"].row.id, "r1")

  def test_Resolve_PreferredAgent_NeverPicksARowThatDoesNotMatchTheSlice(self) -> None:
    rows = [
      _agent_row("r1", "implementer", "Claude Code"),
      _agent_row("r2", "implementer", "Codex", size=["L"]),
    ]
    got = _resolved(rows, _item(size="S"), prefer_agent="Codex")["implementer"]
    self.assertEqual((got.row.id, got.selected_by, got.alternatives), ("r1", "matrix_order", []))

  def test_Resolve_Selection_WinsOverPreferenceAndMatrixOrder(self) -> None:
    got = _resolved(self.ROWS, prefer_agent="Codex", select={"implementer": "r3", "reviewer": "r4"})
    self.assertEqual((got["implementer"].row.id, got["implementer"].selected_by), ("r3", "override"))
    self.assertEqual(_alts(got["implementer"]), ["r1", "r2"])
    self.assertEqual((got["reviewer"].row.id, got["reviewer"].selected_by), ("r4", "override"))
    # A role with no override still follows the preference.
    self.assertEqual(got["orchestrator"].row.id, "r7")
    plain = _resolved(self.ROWS, select={"implementer": "r3"})
    self.assertEqual(_alts(plain["implementer"]), ["r1", "r2"])
    self.assertEqual(plain["reviewer"].row.id, "r4")

  def test_Resolve_Alternatives_SatisfyTheSameConditionsAsTheSelectedRow(self) -> None:
    rows = [
      _agent_row("r1", "implementer", "A", size=["S"], effort=[1], well_specified=True, split=False),
      _agent_row("r2", "implementer", "B", size=["L"]),
      _agent_row("r3", "implementer", "C", effort=[3]),
      _agent_row("r4", "implementer", "D", well_specified=False),
      _agent_row("r5", "implementer", "E", split=True),
      _agent_row("r6", "implementer", "F", sliver=True),
      _agent_row("r7", "implementer", "G", importance=[3]),
      _agent_row("r8", "implementer", "H", urgency=[3]),
      _agent_row("r9", "implementer", "I"),
    ]
    item = _item(size="S", effort=1, importance=2, urgency=2)
    pick = _resolved(rows, item)["implementer"]
    self.assertEqual((pick.row.id, _alts(pick)), ("r1", ["r9"]))
    sliver = _resolved(rows, _item(size="S", effort=1, importance=3, urgency=2, parent="s9"))["implementer"]
    self.assertEqual((sliver.row.id, _alts(sliver)), ("r1", ["r6", "r7", "r9"]))

  def test_Resolve_SelectedRowThatDoesNotMatch_IsBadRoleFieldNamingTheEligibleRows(self) -> None:
    rows = [
      _agent_row("r1", "implementer", "A", size=["S"]),
      _agent_row("r2", "implementer", "B", size=["L"]),
      _agent_row("r3", "implementer", "C"),
    ]
    with self.assertRaises(roles.StateError) as caught:
      _resolved(rows, _item(size="S"), select={"implementer": "r2"})
    self.assertEqual(caught.exception.code, "bad_role_field")
    self.assertIn("r2", str(caught.exception))
    self.assertIn("r1, r3", str(caught.exception))
    for name, row in (
      ("effort", _agent_row("r2", "implementer", "B", effort=[3])),
      ("importance", _agent_row("r2", "implementer", "B", importance=[3])),
      ("urgency", _agent_row("r2", "implementer", "B", urgency=[3])),
      ("well_specified", _agent_row("r2", "implementer", "B", well_specified=False)),
      ("split", _agent_row("r2", "implementer", "B", split=True)),
      ("sliver", _agent_row("r2", "implementer", "B", sliver=True)),
    ):
      with self.subTest(condition=name):
        with self.assertRaises(roles.StateError) as caught:
          _resolved([rows[0], row], _item(size="S", effort=1, importance=1, urgency=1),
                    select={"implementer": "r2"})
        self.assertEqual(caught.exception.code, "bad_role_field")

  def test_Resolve_SelectedRowOfAnotherRole_IsBadRoleField(self) -> None:
    with self.assertRaises(roles.StateError) as caught:
      _resolved(self.ROWS, select={"implementer": "r4"})
    self.assertEqual(caught.exception.code, "bad_role_field")
    self.assertIn("reviewer", str(caught.exception))
    self.assertIn("implementer", str(caught.exception))

  def test_Resolve_SelectedRowThatDoesNotExist_IsUnknownRoleRow(self) -> None:
    with self.assertRaises(roles.StateError) as caught:
      _resolved(self.ROWS, select={"implementer": "r99"})
    self.assertEqual(caught.exception.code, "unknown_role_row")

  def test_Resolve_SelectionForARoleWithNoRows_IsBadRoleField(self) -> None:
    with self.assertRaises(roles.StateError) as caught:
      _resolved(self.ROWS, select={"gate": "r1"})
    self.assertEqual(caught.exception.code, "bad_role_field")

  def test_Resolve_BlankPreferredAgent_IsBadRoleField(self) -> None:
    for blank in ("", "   "):
      with self.subTest(blank=blank):
        with self.assertRaises(roles.StateError) as caught:
          _resolved(self.ROWS, prefer_agent=blank)
        self.assertEqual(caught.exception.code, "bad_role_field")

  def test_Resolve_Result_DoesNotChangeTheMatrix(self) -> None:
    matrix = roles.RoleMatrix(rows=list(self.ROWS))
    before = matrix.to_dict()
    roles.resolve(matrix, _item(), True, False, prefer_agent="Codex", select={"reviewer": "r4"})
    self.assertEqual(matrix.to_dict(), before)

  def test_ParseSelectors_RoleAndRowPairs_BecomeAMapping(self) -> None:
    self.assertEqual(
      roles.parse_selectors(["implementer=r3", " gate = r6 "]), {"implementer": "r3", "gate": "r6"},
    )
    self.assertEqual(roles.parse_selectors([]), {})

  def test_ParseSelectors_Malformed_IsBadRoleFieldWithTheExpectedShape(self) -> None:
    for bad in ("implementer", "=r1", "implementer=", " = ", "r1", "implementer r1"):
      with self.subTest(bad=bad):
        with self.assertRaises(roles.StateError) as caught:
          roles.parse_selectors([bad])
        self.assertEqual(caught.exception.code, "bad_role_field")
        self.assertIn("ROLE=ROW_ID", str(caught.exception))

  def test_ParseSelectors_UnknownRole_IsBadRoleFieldNamingTheRoles(self) -> None:
    with self.assertRaises(roles.StateError) as caught:
      roles.parse_selectors(["planner=r1"])
    self.assertEqual(caught.exception.code, "bad_role_field")
    for name in roles.ROLE_NAMES:
      self.assertIn(name, str(caught.exception))

  def test_ParseSelectors_TwoOverridesForOneRole_IsBadRoleField(self) -> None:
    for pair in (["gate=r1", "gate=r2"], ["gate=r1", "gate=r1"]):
      with self.subTest(pair=pair):
        with self.assertRaises(roles.StateError) as caught:
          roles.parse_selectors(pair)
        self.assertEqual(caught.exception.code, "bad_role_field")
        self.assertIn("gate", str(caught.exception))


class RelayRolesInitTests(RelayRolesCase):
  def test_Init_WritesSkeletonJsonAndNothingElse(self) -> None:
    repo = self.project()
    before = self.snapshot(repo)
    out = json.loads(self.ok(repo, "relay-roles", "init", "--json"))
    self.assertEqual(out, {"written": [str(repo.root / JSON)]})
    after = self.snapshot(repo)
    changed = {k for k in after if before.get(k) != after[k]}
    self.assertEqual(changed - {".slicer/log.jsonl"}, {JSON})
    self.assertEqual(repo.read(JSON), jsonio.dumps(roles.skeleton().to_dict()))

  def test_Init_Existing_IsStateWithoutForceAndForceReplaces(self) -> None:
    repo = self.with_matrix()
    self.ok(repo, "relay-roles", "remove", "r1")
    before = self.snapshot(repo)
    error = self.refuse(repo, "relay-roles", "init", code=2, error="state")
    self.assertIn("--force", error["message"])
    self.assertEqual(self.snapshot(repo), before)
    self.ok(repo, "relay-roles", "init", "--force")
    data = self.matrix(repo)
    expected = roles.skeleton(first_row=12, first_rule=9).to_dict()
    self.assertEqual(
      [r["id"] for r in data["rows"]],
      ["r19", "r12", "r13", "r14", "r15", "r20", "r16", "r17", "r18", "r21", "r22"],
    )
    self.assertEqual(data, expected)

  def test_Init_ReplaceLegacy_WritesJsonAndRemovesMarkdown(self) -> None:
    repo = self.project()
    repo.write(LEGACY, "# hand edited\n")
    out = json.loads(self.ok(repo, "relay-roles", "init", "--replace-legacy", "--json"))
    self.assertEqual(out["written"], [str(repo.root / JSON)])
    self.assertEqual(out["removed"], [str(repo.root / LEGACY)])
    self.assertFalse((repo.root / LEGACY).exists())
    self.assertTrue((repo.root / JSON).is_file())

  def test_Init_Force_NumbersTheExampleAfterEveryIdEverIssued(self) -> None:
    repo = self.with_matrix()
    self.ok(repo, "relay-roles", "remove", "r1")
    self.ok(repo, "relay-roles", "add", "--role", "gate")
    self.ok(repo, "relay-roles", "rule-add", "Extra.")
    self.ok(repo, "relay-roles", "init", "--force")
    data = self.matrix(repo)
    self.assertEqual(
      [r["id"] for r in data["rows"]],
      ["r20", "r13", "r14", "r15", "r16", "r21", "r17", "r18", "r19", "r22", "r23"],
    )
    self.assertEqual([r["id"] for r in data["rules"]], [f"u{n}" for n in range(10, 18)])
    self.assertEqual((data["next_row"], data["next_rule"]), (24, 18))
    self.assertEqual([r["role"] for r in data["rows"]], [r.role for r in roles.skeleton().rows])
    row = json.loads(self.ok(repo, "relay-roles", "add", "--role", "gate", "--json"))
    rule = json.loads(self.ok(repo, "relay-roles", "rule-add", "Again.", "--json"))
    self.assertEqual((row["id"], rule["id"]), ("r24", "u18"))

  def test_Init_Force_CorruptFile_StartsFromTheSkeleton(self) -> None:
    repo = self.with_matrix()
    repo.write(JSON, "{")
    self.ok(repo, "relay-roles", "init", "--force")
    self.assertEqual(self.matrix(repo), roles.skeleton().to_dict())

  def test_Init_Force_NewerSchemaFile_IsNotOverwritten(self) -> None:
    repo = self.with_matrix()
    newer = jsonio.dumps(self.matrix(repo) | {"version": 4})
    repo.write(JSON, newer)
    self.refuse(repo, "relay-roles", "init", "--force", code=3, error="schema_too_new")
    self.assertEqual(repo.read(JSON), newer)

  def test_Init_ReplaceLegacy_MatrixExists_RemovesOnlyTheMarkdown(self) -> None:
    repo = self.with_matrix()
    self.ok(repo, "relay-roles", "add", "--role", "gate", "--model", "mine")
    repo.write(LEGACY, "# old\n")
    kept = repo.read(JSON)
    out = json.loads(self.ok(repo, "relay-roles", "init", "--replace-legacy", "--json"))
    self.assertEqual(out["removed"], [str(repo.root / LEGACY)])
    self.assertEqual(out.get("written", []), [])
    self.assertFalse((repo.root / LEGACY).exists())
    self.assertEqual(repo.read(JSON), kept)

  def test_Init_ReplaceLegacy_MatrixExistsAndNoMarkdown_IsStillTheStateError(self) -> None:
    repo = self.with_matrix()
    before = self.snapshot(repo)
    error = self.refuse(repo, "relay-roles", "init", "--replace-legacy", code=2, error="state")
    self.assertIn("--force", error["message"])
    self.assertEqual(self.snapshot(repo), before)

  def test_Init_ReplaceLegacy_StrictRenderFailure_KeepsTheMarkdown(self) -> None:
    repo = self.with_matrix()
    repo.write(LEGACY, "# old\n")
    before = self.snapshot(repo)
    with patch("slicer.render.write_atomic", side_effect=OSError("boom")):
      code, out, err = repo.run("relay-roles", "init", "--replace-legacy", "--render", "--strict")
    self.assertNotEqual(code, 0)
    self.assertEqual(self.snapshot(repo), before)

  def test_Path_IsADirectory_IsIoOnShowAddAndInit(self) -> None:
    repo = self.project()
    (repo.root / JSON).mkdir()
    for argv in (
      ("relay-roles", "show"),
      ("relay-roles", "add", "--role", "gate"),
      ("relay-roles", "init"),
      ("relay-roles", "init", "--force"),
      ("ai", "relay"),
    ):
      with self.subTest(argv=argv):
        error = self.refuse(repo, *argv, code=3, error="io")
        self.assertIn("relay-roles.json", error["message"])

  def test_Init_ReplaceLegacy_WriteFails_KeepsMarkdown(self) -> None:
    repo = self.project()
    repo.write(LEGACY, "# hand edited\n")
    with patch("slicer.store.jsonio.write", side_effect=OSError("disk full")):
      code, out, err = repo.run("relay-roles", "init", "--replace-legacy", "--json")
    self.assertEqual(code, 3)
    self.assertEqual(repo.read(LEGACY), "# hand edited\n")
    self.assertFalse((repo.root / JSON).exists())

  def test_Init_WithoutReplaceLegacy_LeavesMarkdownAlone(self) -> None:
    repo = self.project()
    repo.write(LEGACY, "# hand edited\n")
    self.ok(repo, "relay-roles", "init")
    self.assertEqual(repo.read(LEGACY), "# hand edited\n")

  def test_Show_NoFile_IsNoRelayRoles(self) -> None:
    repo = self.project()
    error = self.refuse(repo, "relay-roles", "show", code=2, error="no_relay_roles")
    self.assertIn("relay-roles init", error["message"])

  def test_Load_NewerVersion_IsSchemaTooNewExitThree(self) -> None:
    repo = self.with_matrix()
    repo.write(JSON, jsonio.dumps(self.matrix(repo) | {"version": 4}))
    self.refuse(repo, "relay-roles", "show", code=3, error="schema_too_new")

  def test_Load_VersionOneFile_ReadsAsIsAndTheNextWriteMakesItCurrent(self) -> None:
    repo = self.with_matrix()
    data = self.matrix(repo) | {"version": 1}
    for row in data["rows"]:
      del row["when"]["sliver"]
    repo.write(JSON, jsonio.dumps(data))
    shown = json.loads(self.ok(repo, "relay-roles", "show", "--json"))
    self.assertEqual({row["when"]["sliver"] for row in shown["rows"]}, {None})
    self.assertEqual(self.matrix(repo)["version"], 1)  # reading writes nothing
    self.ok(repo, "relay-roles", "set", "r1", "--model", "opus")
    after = self.matrix(repo)
    self.assertEqual(after["version"], roles.ROLES_VERSION)
    self.assertEqual(after["rows"][1]["model"], "opus")
    self.assertEqual({row["when"]["sliver"] for row in after["rows"]}, {None})

  def test_Load_MalformedJson_IsCorrupt(self) -> None:
    repo = self.with_matrix()
    repo.write(JSON, "{")
    self.refuse(repo, "relay-roles", "show", code=3, error="corrupt")

  def test_Load_UnknownRoleInFile_IsCorrupt(self) -> None:
    repo = self.with_matrix()
    data = self.matrix(repo)
    data["rows"][0]["role"] = "janitor"
    repo.write(JSON, jsonio.dumps(data))
    self.refuse(repo, "relay-roles", "show", code=3, error="corrupt")

  def test_Load_DuplicateRowIds_IsCorrupt(self) -> None:
    repo = self.with_matrix()
    data = self.matrix(repo)
    data["rows"][2]["id"] = "r1"
    repo.write(JSON, jsonio.dumps(data))
    self.refuse(repo, "relay-roles", "show", code=3, error="corrupt")

  def test_Load_ScoreOutOfRange_IsCorrupt(self) -> None:
    repo = self.with_matrix()
    data = self.matrix(repo)
    data["rows"][0]["when"]["effort"] = [4]
    repo.write(JSON, jsonio.dumps(data))
    self.refuse(repo, "relay-roles", "show", code=3, error="corrupt")

  def test_Add_CounterBehindExistingIds_IsCorruptNotAReusedId(self) -> None:
    repo = self.with_matrix()
    data = self.matrix(repo)
    data["next_row"] = 1
    repo.write(JSON, jsonio.dumps(data))
    before = self.snapshot(repo)
    self.refuse(repo, "relay-roles", "add", "--role", "gate", code=3, error="corrupt")
    self.assertEqual(self.snapshot(repo), before)

  def test_Load_WrongShape_IsCorrupt(self) -> None:
    repo = self.with_matrix()
    data = self.matrix(repo)
    del data["rows"][0]["role"]
    repo.write(JSON, jsonio.dumps(data))
    self.refuse(repo, "relay-roles", "show", code=3, error="corrupt")


class RelayRolesAddTests(RelayRolesCase):
  def test_Add_AssignsNextIdAndAppends(self) -> None:
    repo = self.with_matrix()
    row = json.loads(self.ok(
      repo, "relay-roles", "add", "--role", "reviewer", "--size", "S,M", "--effort", "1",
      "--importance", "2,3", "--urgency", "1", "--well-specified", "yes", "--split", "no",
      "--agent", "Codex CLI", "--vendor", "OpenAI", "--model", "m1", "--reasoning", "high",
      "--note", "second opinion", "--json",
    ))
    self.assertEqual(row["id"], "r12")
    self.assertEqual(row["when"], {
      "size": ["S", "M"], "effort": [1], "importance": [2, 3], "urgency": [1],
      "well_specified": True, "split": False, "sliver": None,
    })
    self.assertEqual(
      (row["role"], row["agent"], row["vendor"], row["model"], row["reasoning"], row["note"]),
      ("reviewer", "Codex CLI", "OpenAI", "m1", "high", "second opinion"),
    )
    self.assertEqual(self.matrix(repo)["rows"][-1], row)
    self.assertEqual(self.matrix(repo)["next_row"], 13)

  def test_Add_IdIsNeverReusedAfterRemove(self) -> None:
    repo = self.with_matrix()
    self.ok(repo, "relay-roles", "remove", "r7")
    row = json.loads(self.ok(repo, "relay-roles", "add", "--role", "architect", "--json"))
    self.assertEqual(row["id"], "r12")

  def test_Add_WellSpecifiedValues(self) -> None:
    repo = self.with_matrix()
    for flag, want in (("yes", True), ("no", False), ("any", None)):
      with self.subTest(flag=flag):
        row = json.loads(self.ok(
          repo, "relay-roles", "add", "--role", "gate", "--well-specified", flag, "--json",
        ))
        self.assertIs(row["when"]["well_specified"], want)

  def test_Add_SplitValues(self) -> None:
    repo = self.with_matrix()
    for flag, want in (("yes", True), ("no", False), ("any", None)):
      with self.subTest(flag=flag):
        row = json.loads(self.ok(
          repo, "relay-roles", "add", "--role", "gate", "--split", flag, "--json",
        ))
        self.assertIs(row["when"]["split"], want)

  def test_Add_SliverValues(self) -> None:
    repo = self.with_matrix()
    for flag, want in (("yes", True), ("no", False), ("any", None)):
      with self.subTest(flag=flag):
        row = json.loads(self.ok(
          repo, "relay-roles", "add", "--role", "gate", "--sliver", flag, "--json",
        ))
        self.assertIs(row["when"]["sliver"], want)
    self.refuse(
      repo, "relay-roles", "add", "--role", "gate", "--sliver", "maybe",
      code=2, error="bad_role_field",
    )

  def test_Add_BadFields_AreBadRoleFieldAndWriteNothing(self) -> None:
    repo = self.with_matrix()
    before = self.snapshot(repo)
    cases = (
      ("--role", "janitor"),
      ("--role", "gate", "--split", "maybe"),
      ("--role", "gate", "--effort", "4"),
      ("--role", "gate", "--effort", "0"),
      ("--role", "gate", "--effort", "x"),
      ("--role", "gate", "--importance", "9"),
      ("--role", "gate", "--urgency", "-1"),
      ("--role", "gate", "--well-specified", "maybe"),
    )
    for flags in cases:
      with self.subTest(flags=flags):
        self.refuse(repo, "relay-roles", "add", *flags, code=2, error="bad_role_field")
        self.assertEqual(self.snapshot(repo), before)

  def test_Add_AnySingleLineSize_IsAcceptedAndNormalised(self) -> None:
    repo = self.with_matrix()
    row = json.loads(self.ok(
      repo, "relay-roles", "add", "--role", "architect", "--size", "XL,L,xs,S,L", "--json",
    ))
    self.assertEqual(row["when"]["size"], ["S", "L", "XL", "xs"])

  def test_Add_SizeWithNewline_IsRefused(self) -> None:
    repo = self.with_matrix()
    before = self.snapshot(repo)
    self.refuse(
      repo, "relay-roles", "add", "--role", "gate", "--size", "S,a\nb",
      code=2, error="newline_in_field",
    )
    self.assertEqual(self.snapshot(repo), before)

  def test_Add_NewlineInText_IsRefused(self) -> None:
    repo = self.with_matrix()
    self.refuse(
      repo, "relay-roles", "add", "--role", "gate", "--note", "a\nb",
      code=2, error="newline_in_field",
    )

  def test_Add_NoMatrix_IsNoRelayRoles(self) -> None:
    repo = self.project()
    self.refuse(repo, "relay-roles", "add", "--role", "gate", code=2, error="no_relay_roles")

  def test_Add_LedgerOnlyRole_RefusedAndMatrixUnchanged(self) -> None:
    repo = self.with_matrix()
    before = self.snapshot(repo)
    for role in ("ui", "spec-check"):
      with self.subTest(role=role):
        payload = self.refuse(repo, "relay-roles", "add", "--role", role, code=2, error="bad_role_field")
        self.assertIn(", ".join(roles.ROLE_NAMES), payload["message"])
    self.assertEqual(self.snapshot(repo), before)

  def test_Set_LedgerOnlyRole_RefusedAndRowKept(self) -> None:
    repo = self.with_matrix()
    for role in ("ui", "spec-check"):
      with self.subTest(role=role):
        self.refuse(repo, "relay-roles", "set", "r1", "--role", role, code=2, error="bad_role_field")
    self.assertEqual(self.matrix(repo)["rows"][0]["role"], "implementer")


class RelayRolesEditTests(RelayRolesCase):
  def test_Set_ChangesOnlyTheGivenFields(self) -> None:
    repo = self.with_matrix()
    before = self.matrix(repo)
    row = json.loads(self.ok(
      repo, "relay-roles", "set", "r1", "--model", "opus", "--size", "L", "--json",
    ))
    expected = before["rows"][1] | {"model": "opus"}
    expected["when"] = expected["when"] | {"size": ["L"]}
    self.assertEqual(row, expected)
    after = self.matrix(repo)
    self.assertEqual(after["rows"][:1] + after["rows"][2:], before["rows"][:1] + before["rows"][2:])
    self.assertEqual(after["rules"], before["rules"])

  def test_Set_EmptyListFlagAndAnyClearACondition(self) -> None:
    repo = self.with_matrix()
    row = json.loads(self.ok(
      repo, "relay-roles", "set", "r1", "--size", "", "--effort", "any",
      "--well-specified", "any", "--json",
    ))
    self.assertEqual(row["when"]["size"], [])
    self.assertEqual(row["when"]["effort"], [])
    self.assertIsNone(row["when"]["well_specified"])

  def test_Set_Split_ChangesAndClearsTheCondition(self) -> None:
    repo = self.with_matrix()
    row = json.loads(self.ok(repo, "relay-roles", "set", "r3", "--split", "yes", "--json"))
    self.assertIs(row["when"]["split"], True)
    row = json.loads(self.ok(repo, "relay-roles", "set", "r3", "--split", "any", "--json"))
    self.assertIsNone(row["when"]["split"])
    self.refuse(repo, "relay-roles", "set", "r3", "--split", "x", code=2, error="bad_role_field")

  def test_RelayRolesSet_Sliver_UpdatesRow(self) -> None:
    repo = self.with_matrix()
    before = self.matrix(repo)
    row = json.loads(self.ok(repo, "relay-roles", "set", "r3", "--sliver", "no", "--json"))
    self.assertIs(row["when"]["sliver"], False)
    self.assertEqual(self.matrix(repo)["rows"][3]["when"]["sliver"], False)
    self.assertIn("not a sliver", self.ok(repo, "relay-roles", "show"))
    row = json.loads(self.ok(repo, "relay-roles", "set", "r3", "--sliver", "yes", "--json"))
    self.assertIs(row["when"]["sliver"], True)
    row = json.loads(self.ok(repo, "relay-roles", "set", "r3", "--sliver", "any", "--json"))
    self.assertIsNone(row["when"]["sliver"])
    self.refuse(repo, "relay-roles", "set", "r3", "--sliver", "x", code=2, error="bad_role_field")
    self.assertEqual(self.matrix(repo), before)

  def test_Set_SameSizesInAnotherOrder_IsANoOp(self) -> None:
    repo = self.with_matrix()
    seen = len(self.history(repo))
    before = self.snapshot(repo)
    self.ok(repo, "relay-roles", "set", "r1", "--size", "M,S,M")
    self.assertEqual(self.snapshot(repo), before)
    self.assertEqual(len(self.history(repo)), seen)

  def test_Set_BadValue_IsBadRoleFieldAndWritesNothing(self) -> None:
    repo = self.with_matrix()
    before = self.snapshot(repo)
    self.refuse(repo, "relay-roles", "set", "r1", "--effort", "7", code=2, error="bad_role_field")
    self.refuse(repo, "relay-roles", "set", "r1", "--role", "nope", code=2, error="bad_role_field")
    self.assertEqual(self.snapshot(repo), before)

  def test_Remove_DropsTheRow(self) -> None:
    repo = self.with_matrix()
    row = json.loads(self.ok(repo, "relay-roles", "remove", "r2", "--json"))
    self.assertEqual(row["id"], "r2")
    self.assertEqual(
      [r["id"] for r in self.matrix(repo)["rows"]],
      ["r8", "r1", "r3", "r4", "r9", "r5", "r6", "r7", "r10", "r11"],
    )

  def test_Move_BeforeAndLast_ReorderRows(self) -> None:
    repo = self.with_matrix()
    self.ok(repo, "relay-roles", "move", "r7", "--before", "r2")
    self.assertEqual(
      [r["id"] for r in self.matrix(repo)["rows"]],
      ["r8", "r1", "r7", "r2", "r3", "r4", "r9", "r5", "r6", "r10", "r11"],
    )
    self.ok(repo, "relay-roles", "move", "r1", "--last")
    self.assertEqual(
      [r["id"] for r in self.matrix(repo)["rows"]],
      ["r8", "r7", "r2", "r3", "r4", "r9", "r5", "r6", "r10", "r11", "r1"],
    )

  def test_Move_NeedsExactlyOnePlacementAndNotItself(self) -> None:
    repo = self.with_matrix()
    before = self.snapshot(repo)
    self.refuse(repo, "relay-roles", "move", "r1", code=2, error="usage")
    self.refuse(repo, "relay-roles", "move", "r1", "--before", "r1", code=2, error="usage")
    self.refuse(
      repo, "relay-roles", "move", "r1", "--before", "r2", "--last", code=2, error="usage",
    )
    self.assertEqual(self.snapshot(repo), before)

  def test_UnknownRowId_IsUnknownRoleRowAndWritesNothing(self) -> None:
    repo = self.with_matrix()
    before = self.snapshot(repo)
    for argv in (
      ("set", "r99", "--model", "x"),
      ("remove", "r99"),
      ("move", "r99", "--last"),
      ("move", "r1", "--before", "r99"),
    ):
      with self.subTest(argv=argv):
        self.refuse(repo, "relay-roles", *argv, code=2, error="unknown_role_row")
        self.assertEqual(self.snapshot(repo), before)


class RelayRolesRuleTests(RelayRolesCase):
  def test_RuleAdd_AssignsNextIdAndNeverReusesOne(self) -> None:
    repo = self.with_matrix()
    rule = json.loads(self.ok(repo, "relay-roles", "rule-add", "Keep it small.", "--json"))
    self.assertEqual(rule, {"id": "u9", "text": "Keep it small."})
    self.ok(repo, "relay-roles", "rule-remove", "u9")
    rule = json.loads(self.ok(repo, "relay-roles", "rule-add", "Again.", "--json"))
    self.assertEqual(rule["id"], "u10")

  def test_RuleEditAndRemove_ChangeOnlyThatRule(self) -> None:
    repo = self.with_matrix()
    before = self.matrix(repo)
    rule = json.loads(self.ok(repo, "relay-roles", "rule-edit", "u2", "New text.", "--json"))
    self.assertEqual(rule, {"id": "u2", "text": "New text."})
    self.ok(repo, "relay-roles", "rule-remove", "u1")
    after = self.matrix(repo)
    self.assertEqual([r["id"] for r in after["rules"]], [f"u{n}" for n in range(2, 9)])
    self.assertEqual(after["rules"][1:], before["rules"][2:])
    self.assertEqual(after["rows"], before["rows"])

  def test_RuleUnknownId_IsUnknownRoleRule(self) -> None:
    repo = self.with_matrix()
    before = self.snapshot(repo)
    self.refuse(repo, "relay-roles", "rule-edit", "u99", "x", code=2, error="unknown_role_rule")
    self.refuse(repo, "relay-roles", "rule-remove", "u99", code=2, error="unknown_role_rule")
    self.assertEqual(self.snapshot(repo), before)

  def test_RuleText_BlankOrNewline_IsRefused(self) -> None:
    repo = self.with_matrix()
    before = self.snapshot(repo)
    self.refuse(repo, "relay-roles", "rule-add", "  ", code=2, error="blank_title")
    self.refuse(repo, "relay-roles", "rule-add", "a\nb", code=2, error="newline_in_field")
    self.refuse(repo, "relay-roles", "rule-edit", "u1", "", code=2, error="blank_title")
    self.refuse(repo, "relay-roles", "rule-edit", "u1", "a\nb", code=2, error="newline_in_field")
    self.assertEqual(self.snapshot(repo), before)


class RelayRolesMutationContractTests(RelayRolesCase):
  MUTATIONS = (
    ("add", "--role", "gate"),
    ("set", "r1", "--model", "opus"),
    ("remove", "r1"),
    ("move", "r2", "--last"),
    ("rule-add", "One more."),
    ("rule-edit", "u1", "Changed."),
    ("rule-remove", "u1"),
  )

  def test_Mutations_HoldTheProjectLock(self) -> None:
    repo = self.with_matrix()
    for argv in self.MUTATIONS:
      with self.subTest(argv=argv):
        before = self.snapshot(repo)
        with store.project_lock(repo.root), patch.dict(os.environ, {"SLICER_LOCK_TIMEOUT": "0"}):
          code, out, err = repo.run("relay-roles", *argv, "--json")
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(out)["error"]["code"], "locked")
        self.assertEqual(self.snapshot(repo), before)

  def test_Mutations_WriteOneNamespacedHistoryEntryEach(self) -> None:
    repo = self.with_matrix()
    seen = len(self.history(repo))
    expected = (
      ("relay-roles.r12", "add", "", "gate"),
      ("relay-roles.r1", "set", "", "model"),
      ("relay-roles.r1", "remove", "", "implementer"),
      ("relay-roles.r2", "move", "2", "11"),
      ("relay-roles.u9", "rule-add", "", ""),
      ("relay-roles.u1", "rule-edit", "", ""),
      ("relay-roles.u1", "rule-remove", "", ""),
    )
    for argv, (item, note, frm, to) in zip(self.MUTATIONS, expected):
      with self.subTest(argv=argv):
        self.ok(repo, "relay-roles", *argv)
        entries = self.history(repo)
        self.assertEqual(len(entries), seen + 1)
        last = entries[-1]
        self.assertEqual((last.item, last.note, last.frm, last.to), (item, note, frm, to))
        seen += 1
    first = self.history(repo)[0]
    self.assertEqual((first.item, first.note), ("relay-roles", "init"))

  def test_NoOpSet_WritesNoHistory(self) -> None:
    repo = self.with_matrix()
    seen = len(self.history(repo))
    self.ok(repo, "relay-roles", "set", "r1", "--model", "haiku")
    self.assertEqual(len(self.history(repo)), seen)

  def test_NoOp_WithRender_WritesNothingNotEvenTheRender(self) -> None:
    repo = self.with_matrix()
    noops = (
      ("set", "r1", "--model", "haiku"),
      ("move", "r1", "--before", "r2"),
      ("rule-edit", "u1", "A reviewer never wrote code in the slice it reviews."),
    )
    for argv in noops:
      for extra in (("--render",), ("--render", "--strict")):
        with self.subTest(argv=argv, extra=extra):
          (repo.root / RENDER).unlink(missing_ok=True)
          before = self.snapshot(repo)
          self.ok(repo, "relay-roles", *argv, *extra)
          self.assertEqual(self.snapshot(repo), before)
          self.assertFalse((repo.root / RENDER).exists())

  def test_Mutations_WithRender_KeepTheRenderCurrent(self) -> None:
    repo = self.with_matrix()
    for argv in self.MUTATIONS:
      with self.subTest(argv=argv):
        self.ok(repo, "relay-roles", *argv, "--render")
        self.assertEqual((self.check(repo)), 0)

  def check(self, repo: support.TempRepo) -> int:
    return repo.run("check", "--json")[0]

  def test_Mutation_WithoutRender_LeavesCheckStale(self) -> None:
    repo = self.with_matrix()
    self.ok(repo, "relay-roles", "rule-add", "Unrendered.")
    code, out, err = repo.run("check", "--json")
    self.assertEqual(code, 1)
    self.assertIn("RELAY-ROLES.md", json.loads(out)["stale_render"])

  def test_Strict_RenderFailure_RollsTheMatrixBack(self) -> None:
    repo = self.with_matrix()
    before = self.snapshot(repo)
    with patch("slicer.render.write_atomic", side_effect=OSError("boom")):
      code, out, err = repo.run("relay-roles", "rule-add", "Never lands.", "--render", "--strict")
    self.assertNotEqual(code, 0)
    self.assertEqual(self.snapshot(repo), before)

  def test_Strict_Success_LandsMatrixAndRenderTogether(self) -> None:
    repo = self.with_matrix()
    self.ok(repo, "relay-roles", "rule-add", "Lands.", "--render", "--strict")
    self.assertIn("Lands.", repo.read(JSON))
    self.assertIn("Lands.", repo.read(RENDER))

  def test_Strict_ReplaceLegacy_RemovesMarkdownOnlyAfterTheRender(self) -> None:
    repo = self.project()
    repo.write(LEGACY, "# hand edited\n")
    before = self.snapshot(repo)
    with patch("slicer.render.write_atomic", side_effect=OSError("boom")):
      code, out, err = repo.run(
        "relay-roles", "init", "--replace-legacy", "--render", "--strict",
      )
    self.assertNotEqual(code, 0)
    self.assertEqual(self.snapshot(repo), before)
    self.ok(repo, "relay-roles", "init", "--replace-legacy", "--render", "--strict")
    self.assertFalse((repo.root / LEGACY).exists())
    self.assertTrue((repo.root / RENDER).is_file())


class RelayRolesShowAndRenderTests(RelayRolesCase):
  def test_Show_PrintsTheRenderWithoutItsBanner(self) -> None:
    repo = self.with_matrix()
    text = self.ok(repo, "relay-roles", "show")
    rendered = repo.read(RENDER)
    self.assertTrue(rendered.startswith(render.BANNER_PREFIX))
    self.assertEqual(rendered.split("\n", 2)[2], text)

  def test_Show_Json_ReturnsTextRowsAndRules(self) -> None:
    repo = self.with_matrix()
    payload = json.loads(self.ok(repo, "relay-roles", "show", "--json"))
    self.assertEqual(list(payload), ["relay_roles", "rows", "rules"])
    data = self.matrix(repo)
    self.assertEqual(payload["rows"], data["rows"])
    self.assertEqual(payload["rules"], data["rules"])
    self.assertEqual(payload["relay_roles"], roles.render_markdown(roles.skeleton()))

  def test_Show_LeanJson_KeepsRowsAndRulesWhenEmpty(self) -> None:
    repo = self.with_matrix()
    for row_id in ("r1", "r2", "r3", "r4", "r5", "r6", "r7", "r8", "r9", "r10", "r11"):
      self.ok(repo, "relay-roles", "remove", row_id)
    for rule_id in ("u1", "u2", "u3", "u4", "u5", "u6", "u7", "u8"):
      self.ok(repo, "relay-roles", "rule-remove", rule_id)
    payload = json.loads(self.ok(repo, "relay-roles", "show", "--json", "--lean"))
    self.assertEqual((payload["rows"], payload["rules"]), ([], []))
    self.assertIn("No rows.", payload["relay_roles"])

  def test_Show_ReadsWithoutLockingOrWriting(self) -> None:
    repo = self.with_matrix()
    before = self.snapshot(repo)
    with patch("slicer.cli.store.project_lock", side_effect=AssertionError("lock")):
      self.ok(repo, "relay-roles", "show", "--json")
    self.assertEqual(self.snapshot(repo), before)

  def test_Render_FileStartsWithBannerNamingTheJsonAndIsDeterministic(self) -> None:
    repo = self.with_matrix()
    first = repo.read(RENDER)
    self.assertTrue(first.startswith(render.banner(".slicer/relay-roles.json")))
    self.ok(repo, "render")
    self.assertEqual(repo.read(RENDER), first)
    expected = render.plan(repo.state())
    self.assertEqual(expected["RELAY-ROLES.md"], first.encode("utf-8"))

  def test_Check_HandEditedJsonWithoutRender_IsStaleRender(self) -> None:
    repo = self.with_matrix()
    self.assertEqual(repo.run("check")[0], 0)
    data = self.matrix(repo)
    data["rows"][0]["model"] = "hand-edited"
    repo.write(JSON, jsonio.dumps(data))
    code, out, err = repo.run("check", "--json")
    self.assertEqual(code, 1)
    report = json.loads(out)
    self.assertEqual(report["stale_render"], ["RELAY-ROLES.md"])
    self.assertEqual(report["stale_render_details"][0]["cause"], "content")

  def test_Check_RenderWithNoJson_IsAnOrphan(self) -> None:
    repo = self.with_matrix()
    (repo.root / JSON).unlink()
    code, out, err = repo.run("check", "--json")
    self.assertEqual(code, 1)
    self.assertEqual(json.loads(out)["orphan_render"], ["RELAY-ROLES.md"])

  def test_Plan_NoMatrix_HasNoRelayRolesFile(self) -> None:
    repo = self.project()
    self.assertNotIn("RELAY-ROLES.md", render.plan(repo.state()))


class RelayRolesLegacyTests(RelayRolesCase):
  def test_Check_LeftoverMarkdownBesideJson_WarnsToRemoveItAndKeepTheMatrix(self) -> None:
    repo = self.with_matrix()
    repo.write(LEGACY, "# old\n")
    code, out, err = repo.run("check", "--json")
    self.assertEqual(code, 0)
    warnings = [w for w in json.loads(out)["warnings"] if "legacy_relay_roles" in w]
    self.assertEqual(len(warnings), 1)
    self.assertIn("relay-roles init --replace-legacy", warnings[0])
    self.assertIn("keep the matrix", warnings[0])
    self.assertNotIn("--force", warnings[0])

  def test_Check_NoMarkdown_NoLegacyWarning(self) -> None:
    repo = self.with_matrix()
    warnings = json.loads(repo.run("check", "--json")[1])["warnings"]
    self.assertEqual([w for w in warnings if "legacy_relay_roles" in w], [])

  def test_Check_MarkdownWithoutJson_StillWarns(self) -> None:
    repo = self.project()
    repo.write(LEGACY, "# old\n")
    warnings = [
      w for w in json.loads(repo.run("check", "--json")[1])["warnings"]
      if "legacy_relay_roles" in w
    ]
    self.assertEqual(len(warnings), 1)
    self.assertIn("relay-roles init --replace-legacy", warnings[0])
    self.assertIn("write the example matrix", warnings[0])

  def test_AiRelay_LeftoverMarkdown_IsIgnoredWithStderrWarning(self) -> None:
    repo = self.project()
    repo.write(LEGACY, "# old matrix\n")
    code, out, err = repo.run("ai", "relay")
    self.assertEqual(code, 0)
    self.assertNotIn("old matrix", out)
    self.assertNotIn("## Roles matrix", out)
    self.assertIn("relay-roles init --replace-legacy", err)
    self.assertEqual(out, ai.relay_text("done", in_project=True))

  def test_Check_DirectoryAtLegacyPath_NoLegacyWarning(self) -> None:
    repo = self.with_matrix()
    (repo.root / LEGACY).mkdir()
    warnings = json.loads(repo.run("check", "--json")[1])["warnings"]
    self.assertEqual([w for w in warnings if "legacy_relay_roles" in w], [])

  def test_AiRelay_DirectoryAtLegacyPath_NoStderrWarning(self) -> None:
    repo = self.with_matrix()
    (repo.root / LEGACY).mkdir()
    code, out, err = repo.run("ai", "relay")
    self.assertEqual((code, err), (0, ""))
    self.assertNotIn("legacy_relay_roles", out)

  def test_AiRelay_DirectoryAtJsonPathAndLeftoverMarkdown_FailsIoWithoutAdvice(self) -> None:
    repo = self.project()
    (repo.root / JSON).mkdir()
    repo.write(LEGACY, "# old matrix\n")
    code, out, err = repo.run("ai", "relay", "--json")
    self.assertEqual((code, json.loads(out)["error"]["code"]), (3, "io"))
    self.assertNotIn("legacy_relay_roles", err)

  def test_AiRelay_ReadableMatrixAndLeftoverMarkdown_StillWarnsOnStderr(self) -> None:
    repo = self.with_matrix()
    repo.write(LEGACY, "# old matrix\n")
    code, out, err = repo.run("ai", "relay")
    self.assertEqual(code, 0)
    self.assertIn("legacy_relay_roles", err)
    self.assertIn("keep the matrix", err)
    self.assertIn("## Roles matrix", out)


class RelayRolesRecommendTests(RelayRolesCase):
  def item(self, repo: support.TempRepo, *flags: str) -> str:
    scores = ("--importance", "2", "--urgency", "2", "--effort", "2")  # unscored items warn
    return json.loads(self.ok(repo, "add", "A slice", "--json", *scores, *flags))["id"]

  def recommend(self, repo: support.TempRepo, item: str, ws: str = "yes", split: str = "no") -> dict:
    return json.loads(self.ok(
      repo, "relay-roles", "recommend", item, "--well-specified", ws, "--split", split, "--json",
    ))

  def test_Recommend_LedgerOnlyRoles_NeverProposed(self) -> None:
    repo = self.with_matrix()
    item = self.item(repo, "--size", "S", "--effort", "1")
    payload = self.recommend(repo, item)
    self.assertEqual([r["role"] for r in payload["roles"]], list(roles.ROLE_NAMES))
    for role in ("ui", "spec-check"):
      self.assertNotIn(role, payload["unmatched"])

  def test_Recommend_Json_ReturnsRoleOrderedRowsAndUnmatched(self) -> None:
    repo = self.with_matrix()
    item = self.item(repo, "--size", "S", "--effort", "1")
    self.ok(repo, "relay-roles", "remove", "r7")
    payload = self.recommend(repo, item)
    self.assertEqual(
      list(payload), ["id", "well_specified", "split", "sliver", "roles", "unmatched"],
    )
    self.assertIs(payload["sliver"], False)
    self.assertEqual((payload["id"], payload["well_specified"], payload["split"]), (item, True, False))
    self.assertEqual([r["role"] for r in payload["roles"]], list(roles.ROLE_NAMES))
    self.assertEqual(list(payload["roles"][0]), ["role", "row", "alternatives", "selected_by"])
    rows = {r["id"]: r for r in self.matrix(repo)["rows"]}
    self.assertEqual(payload["roles"][0]["row"], rows["r1"])
    self.assertEqual(payload["roles"][1]["row"], rows["r5"])
    self.assertEqual(payload["roles"][2]["row"], rows["r6"])
    self.assertIsNone(payload["roles"][3]["row"])
    self.assertEqual(payload["unmatched"], ["architect"])

  def test_Recommend_SliverAndParent_GetDifferentImplementerRows(self) -> None:
    repo = self.with_matrix()
    parent = self.item(repo, "--size", "M", "--effort", "1")
    sliver = self.item(repo, "--size", "S", "--effort", "1", "--parent", parent)
    got = self.recommend(repo, sliver)
    self.assertIs(got["sliver"], True)
    self.assertEqual(got["roles"][0]["row"]["id"], "r8")
    self.assertEqual(got["roles"][0]["row"]["note"], "per sliver")
    whole = self.recommend(repo, parent)
    self.assertIs(whole["sliver"], False)
    self.assertEqual(whole["roles"][0]["row"]["id"], "r1")

  def test_Recommend_Json_NothingUnmatched_ListsNone(self) -> None:
    repo = self.with_matrix()
    self.ok(repo, "relay-roles", "add", "--role", "orchestrator")
    item = self.item(repo, "--size", "L")
    payload = self.recommend(repo, item, ws="no")
    self.assertEqual(payload["unmatched"], [])
    self.assertEqual(payload["roles"][0]["row"]["id"], "r3")
    self.assertEqual(payload["roles"][3]["row"]["id"], "r7")

  def test_Recommend_Lean_KeepsTheShapeWhenRolesAreUnmatched(self) -> None:
    repo = self.with_matrix()
    for row_id in ("r1", "r2", "r3", "r4", "r5", "r6", "r7", "r9", "r10", "r11"):
      self.ok(repo, "relay-roles", "remove", row_id)
    item = self.item(repo)
    payload = json.loads(self.ok(
      repo, "relay-roles", "recommend", item, "--well-specified", "yes", "--split", "no",
      "--json", "--lean",
    ))
    self.assertEqual(payload["roles"], [
      {"role": r, "row": None, "alternatives": [], "selected_by": "unmatched"}
      for r in roles.ROLE_NAMES
    ])
    out = self.ok(
      repo, "relay-roles", "recommend", item, "--well-specified", "yes", "--split", "no",
      "--json", "--lean",
    )
    self.assertEqual(out.count("\n"), 1 if out.endswith("\n") else 0)
    self.assertNotIn('": ', out)
    self.assertNotIn("\n ", out)
    self.assertEqual(payload["unmatched"], list(roles.ROLE_NAMES))

  def test_Recommend_Text_IsOneLinePerRoleAndNamesUnmatchedRoles(self) -> None:
    repo = self.with_matrix()
    item = self.item(repo, "--size", "S", "--effort", "1")
    lines = self.ok(
      repo, "relay-roles", "recommend", item, "--well-specified", "yes", "--split", "no",
    ).splitlines()
    self.assertEqual(lines[:3], [
      "implementer  r1  Claude Code  Claude  haiku  xhigh  [matrix_order]",
      "  alternative  r3  Claude Code  Claude  sonnet  high",
      "  alternative  r9  Codex  OpenAI  gpt-6.1-sol  medium",
    ])
    self.assertTrue(lines[3].startswith("reviewer  r5  "))
    self.assertTrue(lines[4].startswith("gate  r6  "))
    self.assertEqual(lines[-3:], [
      "architect  (no matching row)",
      "orchestrator  r10  Claude Code  Claude  opus  high  [matrix_order]",
      "  alternative  r11  Codex  OpenAI  gpt-6.1-sol  high",
    ])
    self.assertEqual(len([line for line in lines if not line.startswith(" ")]), 5)

  def test_Recommend_RowWithEmptyFields_PrintsDashesNotBlankColumns(self) -> None:
    repo = self.with_matrix()
    for row_id in ("r1", "r2", "r3", "r4", "r9"):
      self.ok(repo, "relay-roles", "remove", row_id)
    self.ok(repo, "relay-roles", "add", "--role", "implementer", "--model", "whole")
    item = self.item(repo)
    line = self.ok(
      repo, "relay-roles", "recommend", item, "--well-specified", "yes", "--split", "no",
    ).splitlines()[0]
    self.assertEqual(line, "implementer  r12  -  -  whole  -  [matrix_order]")

  def test_Recommend_FlagsMissing_AreUsageErrors(self) -> None:
    repo = self.with_matrix()
    item = self.item(repo)
    for flags in ((), ("--well-specified", "yes"), ("--split", "no")):
      with self.subTest(flags=flags):
        self.refuse(repo, "relay-roles", "recommend", item, *flags, code=2, error="usage")

  def test_Recommend_FlagValueOtherThanYesOrNo_IsBadRoleField(self) -> None:
    repo = self.with_matrix()
    item = self.item(repo)
    for ws, split in (("any", "no"), ("maybe", "no"), ("yes", "any"), ("yes", "true")):
      with self.subTest(ws=ws, split=split):
        self.refuse(
          repo, "relay-roles", "recommend", item, "--well-specified", ws, "--split", split,
          code=2, error="bad_role_field",
        )

  def test_Recommend_WellSpecifiedMaybe_MessageSaysYesOrNoNotAny(self) -> None:
    repo = self.with_matrix()
    item = self.item(repo)
    payload = self.refuse(
      repo, "relay-roles", "recommend", item, "--well-specified", "maybe", "--split", "no",
      code=2, error="bad_role_field",
    )
    self.assertIn("yes or no", payload["message"])
    self.assertNotIn("any", payload["message"])

  def test_Recommend_SplitMaybe_MessageSaysYesOrNoNotAny(self) -> None:
    repo = self.with_matrix()
    item = self.item(repo)
    payload = self.refuse(
      repo, "relay-roles", "recommend", item, "--well-specified", "yes", "--split", "maybe",
      code=2, error="bad_role_field",
    )
    self.assertIn("yes or no", payload["message"])
    self.assertNotIn("any", payload["message"])

  def test_Recommend_SplitAny_MessageSaysYesOrNo(self) -> None:
    repo = self.with_matrix()
    item = self.item(repo)
    payload = self.refuse(
      repo, "relay-roles", "recommend", item, "--well-specified", "yes", "--split", "any",
      code=2, error="bad_role_field",
    )
    self.assertIn("yes or no", payload["message"])

  def test_Recommend_UnknownItem_IsNoSuchItem(self) -> None:
    repo = self.with_matrix()
    self.refuse(
      repo, "relay-roles", "recommend", "s99", "--well-specified", "yes", "--split", "no",
      code=2, error="no_such_item",
    )

  def test_Recommend_NoMatrix_IsNoRelayRoles(self) -> None:
    repo = self.project()
    item = self.item(repo)
    self.refuse(
      repo, "relay-roles", "recommend", item, "--well-specified", "yes", "--split", "no",
      code=2, error="no_relay_roles",
    )

  def test_Recommend_ReadsWithoutLockingOrWriting(self) -> None:
    repo = self.with_matrix()
    item = self.item(repo, "--size", "S")
    before = self.snapshot(repo)
    with patch("slicer.cli.store.project_lock", side_effect=AssertionError("lock")):
      self.recommend(repo, item)
    self.assertEqual(self.snapshot(repo), before)

  def test_Recommend_SplitPart_GetsTheSplitRowAndNeverTheWholeSliceRow(self) -> None:
    repo = self.with_matrix()
    for row_id in ("r1", "r2", "r3", "r4", "r9"):
      self.ok(repo, "relay-roles", "remove", row_id)
    self.ok(repo, "relay-roles", "add", "--role", "implementer", "--split", "no", "--model", "whole")
    self.ok(repo, "relay-roles", "add", "--role", "implementer", "--split", "yes", "--model", "part")
    item = self.item(repo)
    part = self.recommend(repo, item, split="yes")
    self.assertEqual(part["roles"][0]["row"]["model"], "part")
    self.assertIs(part["roles"][0]["row"]["when"]["split"], True)
    whole = self.recommend(repo, item, split="no")
    self.assertEqual(whole["roles"][0]["row"]["model"], "whole")
    self.ok(repo, "relay-roles", "remove", "r13")
    self.assertEqual(self.recommend(repo, item, split="yes")["roles"][0]["row"], None)

  def test_Recommend_ItemScores_SelectRowsByImportanceAndUrgency(self) -> None:
    repo = self.with_matrix()
    self.ok(repo, "relay-roles", "add", "--role", "gate", "--importance", "3", "--urgency", "3", "--model", "hot")
    self.ok(repo, "relay-roles", "move", "r12", "--before", "r6")
    hot = self.item(repo, "--importance", "3", "--urgency", "3")
    calm = self.item(repo, "--importance", "3", "--urgency", "1")
    self.assertEqual(self.recommend(repo, hot)["roles"][2]["row"]["model"], "hot")
    self.assertEqual(self.recommend(repo, calm)["roles"][2]["row"]["id"], "r6")

  def lineup(self) -> tuple[support.TempRepo, str]:
    """The fresh example, which ships the Codex implementer (r9) and the orchestrators (r10, r11)."""
    repo = self.with_matrix()
    return repo, self.item(repo, "--size", "S", "--effort", "1")

  def lineup_cli(self, repo: support.TempRepo, item: str, *flags: str) -> dict:
    return json.loads(self.ok(
      repo, "relay-roles", "recommend", item, "--well-specified", "yes", "--split", "no",
      "--json", *flags,
    ))

  def picks(self, payload: dict) -> dict:
    return {r["role"]: (r["row"]["id"] if r["row"] else None, r["selected_by"]) for r in payload["roles"]}

  def test_Recommend_Default_KeepsExistingPicksAndAppendsTheFirstOrchestrator(self) -> None:
    repo, item = self.lineup()
    payload = self.lineup_cli(repo, item)
    self.assertEqual(self.picks(payload), {
      "implementer": ("r1", "matrix_order"), "reviewer": ("r5", "matrix_order"),
      "gate": ("r6", "matrix_order"), "architect": (None, "unmatched"),
      "orchestrator": ("r10", "matrix_order"),
    })
    self.assertEqual([r["role"] for r in payload["roles"]], list(roles.ROLE_NAMES))
    self.assertEqual(payload["unmatched"], ["architect"])
    rows = {r["id"]: r for r in self.matrix(repo)["rows"]}
    implementer = payload["roles"][0]
    self.assertEqual([r["id"] for r in implementer["alternatives"]], ["r3", "r9"])
    self.assertEqual(implementer["alternatives"][1], rows["r9"])  # full row objects
    self.assertEqual([r["id"] for r in payload["roles"][4]["alternatives"]], ["r11"])
    self.assertEqual(payload["roles"][3]["alternatives"], [])
    self.assertEqual(payload["roles"][1]["alternatives"], [])

  def test_Recommend_PreferAgent_SelectsThatAgentsRowsAndKeepsOtherRolesInOrder(self) -> None:
    repo, item = self.lineup()
    payload = self.lineup_cli(repo, item, "--prefer-agent", " codex ")
    self.assertEqual(self.picks(payload), {
      "implementer": ("r9", "preferred_agent"), "reviewer": ("r5", "matrix_order"),
      "gate": ("r6", "matrix_order"), "architect": (None, "unmatched"),
      "orchestrator": ("r11", "preferred_agent"),
    })
    self.assertEqual(payload["roles"][0]["row"]["model"], "gpt-6.1-sol")
    self.assertEqual(payload["roles"][0]["row"]["reasoning"], "medium")
    self.assertEqual([r["id"] for r in payload["roles"][0]["alternatives"]], ["r1", "r3"])
    self.assertEqual(payload["roles"][0]["row"]["agent"], "Codex")  # the row's own text

  def test_Recommend_Select_WinsOverPreferenceAndRepeatsPerRole(self) -> None:
    repo, item = self.lineup()
    payload = self.lineup_cli(
      repo, item, "--prefer-agent", "Codex", "--select", "implementer=r3", "--select", "orchestrator=r10",
    )
    got = self.picks(payload)
    self.assertEqual(got["implementer"], ("r3", "override"))
    self.assertEqual(got["orchestrator"], ("r10", "override"))
    self.assertEqual(got["reviewer"], ("r5", "matrix_order"))
    self.assertEqual([r["id"] for r in payload["roles"][0]["alternatives"]], ["r1", "r9"])
    self.assertEqual(self.picks(self.lineup_cli(repo, item, "--select", "implementer=r9"))["implementer"],
                     ("r9", "override"))

  def test_Recommend_SelectionErrors_AreNamedByCodeAndDiagnosed(self) -> None:
    repo, item = self.lineup()
    base = ("relay-roles", "recommend", item, "--well-specified", "yes", "--split", "no")
    cases = (
      (("--select", "implementer"), "bad_role_field", "ROLE=ROW_ID"),
      (("--select", "=r1"), "bad_role_field", "ROLE=ROW_ID"),
      (("--select", "implementer="), "bad_role_field", "ROLE=ROW_ID"),
      (("--select", "planner=r1"), "bad_role_field", "orchestrator"),
      (("--select", "implementer=r1", "--select", "implementer=r3"), "bad_role_field", "implementer"),
      (("--select", "implementer=r99"), "unknown_role_row", "r99"),
      (("--select", "implementer=r5"), "bad_role_field", "reviewer"),
      (("--select", "implementer=r2"), "bad_role_field", "r1, r3, r9"),  # S, effort 2 row
      (("--select", "gate=r1"), "bad_role_field", "gate"),
      (("--prefer-agent", "  "), "bad_role_field", "prefer-agent"),
    )
    before = self.snapshot(repo)
    for flags, code, text in cases:
      with self.subTest(flags=flags):
        payload = self.refuse(repo, *base, *flags, code=2, error=code)
        self.assertIn(text, payload["message"])
    self.assertEqual(self.snapshot(repo), before)

  def test_Recommend_Lean_KeepsNullRowsAndEmptyAlternativesInTheShape(self) -> None:
    repo, item = self.lineup()
    out = self.ok(
      repo, "relay-roles", "recommend", item, "--well-specified", "yes", "--split", "no",
      "--json", "--lean",
    )
    payload = json.loads(out)
    architect = payload["roles"][3]
    self.assertEqual(architect, {"role": "architect", "row": None, "alternatives": [], "selected_by": "unmatched"})
    self.assertEqual(payload["roles"][1]["alternatives"], [])
    self.assertEqual(payload["unmatched"], ["architect"])

  def test_Recommend_SameInputs_GiveByteIdenticalOutput(self) -> None:
    repo, item = self.lineup()
    for flags in ((), ("--prefer-agent", "Codex", "--select", "gate=r6")):
      for extra in ((), ("--json",), ("--json", "--lean")):
        argv = ("relay-roles", "recommend", item, "--well-specified", "yes", "--split", "no", *extra, *flags)
        with self.subTest(argv=argv):
          self.assertEqual(self.ok(repo, *argv), self.ok(repo, *argv))

  def test_Recommend_LaunchingHarness_HasNoEffectOnTheLineup(self) -> None:
    repo, item = self.lineup()
    plain = self.lineup_cli(repo, item)
    for name, value in (("CLAUDECODE", "1"), ("CODEX_HOME", "/nonexistent"), ("CODEX_SANDBOX", "seatbelt")):
      with self.subTest(env=name), patch.dict(os.environ, {name: value}):
        self.assertEqual(self.lineup_cli(repo, item), plain)

  def test_Recommend_Text_ShowsSelectionReasonsAndAlternatives(self) -> None:
    repo, item = self.lineup()
    lines = self.ok(
      repo, "relay-roles", "recommend", item, "--well-specified", "yes", "--split", "no",
      "--prefer-agent", "Codex", "--select", "reviewer=r5",
    ).splitlines()
    self.assertEqual(lines, [
      "implementer  r9  Codex  OpenAI  gpt-6.1-sol  medium  [preferred_agent]",
      "  alternative  r1  Claude Code  Claude  haiku  xhigh",
      "  alternative  r3  Claude Code  Claude  sonnet  high",
      "reviewer  r5  Claude Code  Claude  opus  medium  [override]",
      "gate  r6  Claude Code  Claude  opus  medium  [matrix_order]",
      "architect  (no matching row)",
      "orchestrator  r11  Codex  OpenAI  gpt-6.1-sol  high  [preferred_agent]",
      "  alternative  r10  Claude Code  Claude  opus  high",
    ])

  def test_Recommend_WithFlags_WritesNoStateAndNoHistory(self) -> None:
    repo, item = self.lineup()
    before = self.snapshot(repo)
    history = len(repo.state().history())
    with patch("slicer.cli.store.project_lock", side_effect=AssertionError("lock")):
      self.lineup_cli(repo, item, "--prefer-agent", "Codex", "--select", "implementer=r3")
      self.ok(repo, "relay-roles", "recommend", item, "--well-specified", "yes", "--split", "no",
              "--prefer-agent", "Codex")
    self.assertEqual(self.snapshot(repo), before)
    self.assertEqual(len(repo.state().history()), history)

  def test_Recommend_VersionTwoMatrix_LoadsWithoutWritingAndTheNextMutationUpgradesIt(self) -> None:
    repo = self.with_matrix()
    item = self.item(repo, "--size", "S", "--effort", "1")
    current = self.matrix(repo)
    # A schema 2 file predates the orchestrator rows, so it holds none; the counter stays at 12.
    v2_rows = [r for r in current["rows"] if r["role"] != "orchestrator"]
    repo.write(JSON, jsonio.dumps(current | {"version": 2, "rows": v2_rows}))
    before = self.snapshot(repo)
    payload = self.recommend(repo, item)
    self.assertEqual(self.picks(payload)["implementer"], ("r1", "matrix_order"))
    self.assertEqual(self.picks(payload)["orchestrator"], (None, "unmatched"))
    self.ok(repo, "relay-roles", "show")
    self.assertEqual(self.snapshot(repo), before)
    self.assertEqual(self.matrix(repo)["version"], 2)
    self.ok(repo, "relay-roles", "add", "--role", "orchestrator", "--model", "opus")
    after = self.matrix(repo)
    self.assertEqual(after["version"], 3)
    self.assertEqual(
      [r["id"] for r in after["rows"]],
      ["r8", "r1", "r2", "r3", "r4", "r9", "r5", "r6", "r7", "r12"],
    )

  def test_Recommend_FutureVersionMatrix_IsRefused(self) -> None:
    repo = self.with_matrix()
    item = self.item(repo)
    repo.write(JSON, jsonio.dumps(self.matrix(repo) | {"version": 4}))
    self.refuse(
      repo, "relay-roles", "recommend", item, "--well-specified", "yes", "--split", "no",
      code=3, error="schema_too_new",
    )

  def test_Orchestrator_Row_RoundTripsThroughAddSetShowRecommendAndRender(self) -> None:
    repo = self.with_matrix()
    added = json.loads(self.ok(
      repo, "relay-roles", "add", "--role", "orchestrator", "--agent", "Claude Code",
      "--vendor", "Claude", "--model", "opus", "--reasoning", "high", "--size", "M", "--json", "--render",
    ))
    self.assertEqual((added["id"], added["role"]), ("r12", "orchestrator"))
    self.ok(repo, "relay-roles", "set", added["id"], "--model", "sonnet", "--reasoning", "xhigh", "--render")
    shown = json.loads(self.ok(repo, "relay-roles", "show", "--json"))
    row = next(r for r in shown["rows"] if r["id"] == "r12")
    self.assertEqual((row["role"], row["model"], row["reasoning"], row["when"]["size"]),
                     ("orchestrator", "sonnet", "xhigh", ["M"]))
    self.assertIn("| r12 | Orchestrator | size M | Claude Code | Claude | sonnet | xhigh | - |",
                  self.ok(repo, "relay-roles", "show"))
    # Matrix order: moved above the default orchestrator, the M-only row is the first match for an M slice.
    self.ok(repo, "relay-roles", "move", added["id"], "--before", "r10")
    item = self.item(repo, "--size", "M")
    self.assertEqual(self.picks(self.recommend(repo, item))["orchestrator"], ("r12", "matrix_order"))
    small = self.item(repo, "--size", "S", "--effort", "1")
    self.assertEqual(self.picks(self.recommend(repo, small))["orchestrator"], ("r10", "matrix_order"))
    self.ok(repo, "relay-roles", "set", "r1", "--role", "orchestrator", "--render")
    self.assertEqual(self.matrix(repo)["rows"][1]["role"], "orchestrator")
    self.ok(repo, "relay-roles", "set", "r1", "--role", "implementer", "--render")
    self.assertEqual(repo.run("check")[0], 0)
    self.assertEqual(
      [e.to for e in self.history(repo) if e.note == "add"], ["orchestrator"],
    )
    self.refuse(repo, "relay-roles", "add", "--role", "planner", code=2, error="bad_role_field")

  def test_RelayRolesRecommend_FreshExampleSizeS_LeavesOnlyArchitectUnmatched(self) -> None:
    repo = self.with_matrix()
    item = self.item(repo, "--size", "S", "--effort", "1")
    payload = self.recommend(repo, item)
    self.assertEqual(payload["unmatched"], ["architect"])
    self.assertEqual(self.picks(payload), {
      "implementer": ("r1", "matrix_order"), "reviewer": ("r5", "matrix_order"),
      "gate": ("r6", "matrix_order"), "architect": (None, "unmatched"),
      "orchestrator": ("r10", "matrix_order"),
    })
    picked = [r["row"] for r in payload["roles"] if r["row"]]
    self.assertEqual({row["agent"] for row in picked}, {"Claude Code"})
    self.assertEqual([r["id"] for r in payload["roles"][0]["alternatives"]], ["r3", "r9"])
    self.assertEqual([r["id"] for r in payload["roles"][4]["alternatives"]], ["r11"])

  def test_RelayRolesRecommend_FreshExampleSizeL_ResolvesEveryRole(self) -> None:
    repo = self.with_matrix()
    item = self.item(repo, "--size", "L", "--effort", "1")
    payload = self.recommend(repo, item)
    self.assertEqual(payload["unmatched"], [])
    self.assertEqual(self.picks(payload), {
      "implementer": ("r3", "matrix_order"), "reviewer": ("r5", "matrix_order"),
      "gate": ("r6", "matrix_order"), "architect": ("r7", "matrix_order"),
      "orchestrator": ("r10", "matrix_order"),
    })
    picked = [r["row"] for r in payload["roles"]]
    self.assertEqual({row["agent"] for row in picked}, {"Claude Code"})

  def test_RelayRolesRecommend_FreshExamplePreferCodex_PicksCodexRows(self) -> None:
    repo = self.with_matrix()
    item = self.item(repo, "--size", "L", "--effort", "1")
    payload = self.lineup_cli(repo, item, "--prefer-agent", "Codex")
    self.assertEqual(self.picks(payload), {
      "implementer": ("r9", "preferred_agent"), "reviewer": ("r5", "matrix_order"),
      "gate": ("r6", "matrix_order"), "architect": ("r7", "matrix_order"),
      "orchestrator": ("r11", "preferred_agent"),
    })
    self.assertEqual([r["id"] for r in payload["roles"][0]["alternatives"]], ["r3"])
    self.assertEqual([r["id"] for r in payload["roles"][4]["alternatives"]], ["r10"])


class AiRelayJsonTests(RelayRolesCase):
  def test_AiRelay_WithMatrix_EmbedsTheRenderUnderRolesMatrix(self) -> None:
    repo = self.with_matrix()
    code, out, err = repo.run("ai", "relay")
    self.assertEqual((code, err), (0, ""))
    body = roles.render_markdown(roles.skeleton(), level=2)
    self.assertIn("## Roles matrix\n\n" + body.rstrip("\n") + "\n\n## Order", out)
    self.assertNotIn("# Relay roles", out)
    self.assertIn("\n### Matrix\n", out)
    setup = out.split("## Setup", 1)[1].split("## Roles matrix", 1)[0]
    self.assertIn("agent, vendor, model and reasoning effort", setup)
    self.assertNotIn("relay-roles init", out)
    code, as_json, err = repo.run("ai", "relay", "--json")
    self.assertEqual(json.loads(as_json)["relay"].rstrip("\n"), out.rstrip("\n"))

  def test_AiRelay_ChangedMatrix_ShowsTheChange(self) -> None:
    repo = self.with_matrix()
    self.ok(repo, "relay-roles", "set", "r3", "--model", "special-model")
    self.assertIn("special-model", self.ok(repo, "ai", "relay"))

  def test_AiRelay_NoMatrix_SetupNamesRelayRolesInit(self) -> None:
    repo = self.project()
    out = self.ok(repo, "ai", "relay")
    self.assertNotIn("## Roles matrix", out)
    setup = out.split("## Setup", 1)[1].split("## Order", 1)[0]
    self.assertIn("`slicer relay-roles init`", setup)
    self.assertNotIn("--init-roles", out)

  def test_GenericRelayText_StillNamesNoProduct(self) -> None:
    blob = (ai.relay_text("done") + ai.relay_text("handoff") + ai.ROLES_PROPOSE
            + ai.ROLES_MISSING).casefold()
    for token in ("claude", "codex", "grok", "openai", "anthropic", "gemini"):
      with self.subTest(token=token):
        self.assertNotIn(token, blob)

  def test_AiRelay_CorruptMatrix_IsCorruptNotAFallback(self) -> None:
    repo = self.with_matrix()
    repo.write(JSON, "{")
    self.refuse(repo, "ai", "relay", code=3, error="corrupt")

  def test_AiRelay_ReadsWithoutLockingOrLoadingState(self) -> None:
    repo = self.with_matrix()
    before = self.snapshot(repo)
    with patch("slicer.cli.store.project_lock", side_effect=AssertionError("lock")), \
         patch("slicer.cli.store.load", side_effect=AssertionError("load")):
      self.ok(repo, "ai", "relay")
      self.ok(repo, "ai", "relay-roles")
    self.assertEqual(self.snapshot(repo), before)

  def every_brief(self, out: str) -> str:
    self.assertIn("## Every brief\n", out)
    section = out.split("## Every brief", 1)[1].split("\n## ", 1)[0]
    return " ".join(section.split())

  def test_AiRelay_EveryBrief_NamesLabelAndExplicitModelEffort(self) -> None:
    repo = self.with_matrix()
    section = self.every_brief(self.ok(repo, "ai", "relay"))
    self.assertIn("`ID role`", section)
    self.assertIn("model and reasoning effort its role row names", section)
    self.assertIn("set explicitly", section)

  def test_AiRelay_EveryBrief_NamesNoHarness(self) -> None:
    repo = self.with_matrix()
    section = self.every_brief(self.ok(repo, "ai", "relay"))
    # The label paragraph must be present, so the absence checks below are not vacuous.
    self.assertIn("`ID role`", section)
    for name in ("Claude Code", "/tasks", "subagentStatusLine"):
      with self.subTest(name=name):
        self.assertNotIn(name, section)

  def test_AiRelay_Order_HeartbeatPassesNoTask(self) -> None:
    repo = self.with_matrix()
    out = self.ok(repo, "ai", "relay")
    order = " ".join(out.split("## Order", 1)[1].split("\n## ", 1)[0].split())
    for phrase in ("A heartbeat passes no `--task`", "each round records its own step"):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, order)


class AiRelayContractTests(RelayRolesCase):
  """The nested relay: `ai relay` is the outer session's contract and
  `ai relay-orchestrator` the orchestrator's, both carrying one version marker."""

  MARKER = "Contract: relay v9"

  def flat(self, text: str) -> str:
    return " ".join(text.split())

  def section(self, text: str, start: str, end: str) -> str:
    return self.flat(text.split(start, 1)[1].split(end, 1)[0])

  def orchestrator(self, repo: support.TempRepo) -> str:
    return self.ok(repo, "ai", "relay-orchestrator")

  def test_AiRelay_Roles_OuterSessionSpawnsOrchestratorPerSlice(self) -> None:
    roles_section = self.section(ai.relay_text(), "## Roles", "## Setup")
    self.assertIn("The outer session is the session the owner talks to", roles_section)
    self.assertIn("spawns one orchestrator per slice", roles_section)
    self.assertIn("`slicer ai relay-orchestrator`", roles_section)
    self.assertIn("writes no product code", roles_section)

  def test_AiRelay_Order_FreshOrchestratorPerSlice(self) -> None:
    order = self.section(ai.relay_text(), "## Order", "## Slivers")
    self.assertNotIn("compact", order)
    self.assertIn("Each slice gets a fresh orchestrator", order)
    self.assertIn('a brief that starts "First run `slicer ai relay-orchestrator` and follow it."', order)

  def test_AiRelay_Order_OuterSessionChecksTheReport(self) -> None:
    order = self.section(ai.relay_text(), "## Order", "## Slivers")
    for phrase in (
      "Check the report's first line, the contract marker, and its last line, "
      "`nothing left running: yes`.",
      "file the report's later notes as one more commit on the slice branch",
      "waits for the reports without polling",
      "with the role `orchestrator`",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, order)

  def test_RelayText_HandBackWithoutLastLine_SaysToRelayTheWaitedRole(self) -> None:
    order = self.section(ai.relay_text(), "## Order", "## Slivers")
    for phrase in (
      "A hand-back whose last line is not `nothing left running: yes`, and which names "
      "the role it waits on (a last line `waiting on: ROLE AGENT-ID`), is a wait, not a report.",
      "Wait for that role's report, then resume the orchestrator with the report pasted in.",
      "Do not stop the orchestrator for it.",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, order)

  def test_RelayText_StopOrchestrator_OnlyWhenNothingIsWaited(self) -> None:
    order = self.section(ai.relay_text(), "## Order", "## Slivers")
    self.assertIn(
      "Stop the orchestrator first when a hand-back lacks that last line and names no running "
      "role and no unanswered owner question, or names a role that has already finished and "
      "whose report the orchestrator already has, or a question whose answer the orchestrator "
      "was already resumed with.",
      order)
    self.assertNotIn("Without that last line, stop the orchestrator first", order)

  def test_OrchestratorText_Waiting_NamesTheRoleWaitedOn(self) -> None:
    repo = self.project()
    waiting = self.flat(next(b for b in self.orchestrator(repo).split("\n- ")
                             if b.startswith("Waiting")))
    for phrase in (
      "When the turn ends before the final report, the last line of the hand-back is "
      "`waiting on: ROLE AGENT-ID`, or the closest thing the harness gives, so the outer "
      "session can route the report.",
      "A role report pasted in by the outer session on resume is that role's report.",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, waiting)

  def test_AiRelay_Roles_OrchestratorReadsAsItsContractSays(self) -> None:
    roles_section = self.section(ai.relay_text(), "## Roles", "## Setup")
    self.assertIn("reads the reports and reads diffs as `slicer ai relay-orchestrator` says",
                  roles_section)
    self.assertNotIn("reads every report", roles_section)

  def test_AiRelay_Order_OrchestratorCommitsOnlyWhenBriefAllows(self) -> None:
    order = self.section(ai.relay_text(), "## Order", "## Slivers")
    self.assertIn("commits on the slice branch only when its brief allows it", order)
    self.assertIn("merge and push are the outer session's, when the owner authorizes them", order)
    self.assertNotIn("Commit, merge and push only when", order)

  def test_AiRelayOrchestrator_Intro_NamesItsWholeContract(self) -> None:
    intro = self.section(ai.orchestrator_text(), "# Slice relay orchestrator", "## Rules")
    self.assertIn("This text is your whole contract for one slice", intro)
    self.assertNotIn("one-slice parts", intro)
    self.assertNotIn("(Order, Gate, Every brief, Stop)", intro)

  def test_AiRelayOrchestrator_TrackingBullet_IsWrapped(self) -> None:
    for finish in ("done", "handoff"):
      bullet = next(b for b in re.split(r"\n(?=- )", ai.orchestrator_text(finish))
                    if b.startswith("- Tracking order"))
      with self.subTest(finish=finish):
        self.assertGreater(len(bullet.splitlines()), 2)
        for line in bullet.splitlines():
          self.assertLessEqual(len(line), 88, line)

  def test_AiRelay_FirstLine_NamesContractVersion(self) -> None:
    self.assertEqual(ai.relay_text().splitlines()[0], self.MARKER)
    repo = self.project()
    self.assertEqual(self.ok(repo, "ai", "relay").splitlines()[0], self.MARKER)

  def test_AiRelayOrchestrator_FirstLine_SameContractVersion(self) -> None:
    repo = self.project()
    first = self.orchestrator(repo).splitlines()[0]
    self.assertEqual(first, self.MARKER)
    self.assertEqual(first, self.ok(repo, "ai", "relay").splitlines()[0])
    # Both texts build the line from the one constant: a bumped number shows in both.
    with patch.object(ai, "RELAY_CONTRACT", 10):
      self.assertEqual(ai.relay_text().splitlines()[0], "Contract: relay v10")
      self.assertEqual(ai.orchestrator_text().splitlines()[0], "Contract: relay v10")

  def test_AiRelay_Json_CarriesContract(self) -> None:
    repo = self.project()
    relay = json.loads(self.ok(repo, "ai", "relay", "--json"))
    self.assertEqual(relay["contract"], "relay v9")
    self.assertEqual(relay["relay"].rstrip("\n"), self.ok(repo, "ai", "relay").rstrip("\n"))
    orchestrator = json.loads(self.ok(repo, "ai", "relay-orchestrator", "--json"))
    self.assertEqual(orchestrator["contract"], "relay v9")

  def golden(self, command, finish="done", matrix=False):
    return json.loads((Path(__file__).parent / "fixtures/relay-contract-v9.json").read_text())[
      f"{command}-{finish}-{matrix}"
    ]

  def numbered_steps(self, text):
    order = text.split("## Order\n\n", 1)[1].split("\n## ", 1)[0]
    return [
      {"step": int(number), "text": self.flat(body)}
      for number, body in re.findall(r"(?m)^(\d+)\. (.*(?:\n {2,}[^\n]+)*)", order)
    ]

  def test_AiRelay_JsonChanges_ListsEachVersionWithText(self):
    repo = self.project()
    changes = json.loads(self.ok(repo, "ai", "relay", "--json"))["changes"]
    self.assertIsInstance(changes, list)
    self.assertEqual([entry["version"] for entry in changes], list(range(5, ai.RELAY_CONTRACT + 1)))
    for entry in changes:
      with self.subTest(version=entry["version"]):
        self.assertEqual(list(entry), ["version", "text"])
        self.assertIsInstance(entry["text"], str)
        self.assertTrue(entry["text"].strip())
        self.assertNotIn("\n", entry["text"])
        self.assertEqual(entry["text"], entry["text"].strip())
        self.assertEqual(len(entry["text"].splitlines()), 1)

  def test_AiRelayOrchestrator_JsonChanges_SameAsRelay(self):
    repo = self.project()
    relay = json.loads(self.ok(repo, "ai", "relay", "--json"))
    orchestrator = json.loads(self.ok(repo, "ai", "relay-orchestrator", "--json"))
    self.assertEqual(orchestrator["changes"], relay["changes"])

  def test_RelayContract_Bump_NeedsAChangeEntry(self):
    self.assertEqual(ai.RELAY_CHANGES[-1][0], ai.RELAY_CONTRACT)

  def test_AiRelay_Json_CarriesStructuredContract(self):
    repo = self.project()
    payload = json.loads(self.ok(repo, "ai", "relay", "--json"))
    self.assertEqual(list(payload), ["contract", "pinned", "relay", "setup", "order", "land", "report", "outcomes", "stop", "changes",
      "roles", "gate", "finding_kinds", "conformance"])
    setup = self.golden("relay").split("## Setup\n\n", 1)[1].split("Wait for", 1)[0]
    bullets = re.findall(r"(?m)^- (.*(?:\n  [^\n]+)*)", setup)
    expected = [dict(key=key, text=self.flat(text)) for key, text in zip(
      ("spec_check", "roles", "split", "sliver", "role_models", "orchestrator", "environment", "end_point"),
      bullets
    )]
    expected[-1]["options"] = ["finish", "commit", "landed"]
    self.assertEqual(payload["setup"], expected)
    self.assertEqual(payload["order"], self.numbered_steps(self.golden("relay")))
    self.assertEqual(payload["outcomes"], ["pass", "fail", "stopped"])
    self.assertEqual(payload["stop"], [
      "the same slice fails its gate three times", "the spec turns out ambiguous or wrong",
      "an owner decision marked `blocker` is needed (asked and waited on with `relay-run ask`)",
    ])

  def test_AiRelayOrchestrator_Json_CarriesStructuredContract(self):
    repo = self.project()
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        self.ok(repo, "config", "implement_finish", finish)
        payload = json.loads(self.ok(repo, "ai", "relay-orchestrator", "--json"))
        self.assertEqual(list(payload), ["contract", "pinned", "orchestrator", "order", "report", "outcomes", "stop", "changes",
      "gate", "finding_kinds", "conformance"])
        self.assertEqual(payload["order"], self.numbered_steps(self.golden("relay-orchestrator", finish)))
        relay = json.loads(self.ok(repo, "ai", "relay", "--json"))
        for key in ("report", "outcomes", "stop"):
          self.assertEqual(payload[key], relay[key])

  def test_AiRelay_JsonReport_NamesEachReportLine(self):
    repo = self.project()
    templates = self.golden("relay-orchestrator").split("## Run report", 1)[1].split("```", 2)[1].strip().splitlines()
    keys = ["contract", "slice", "worktree", "rounds", "tests", "diff_stat", "deviations",
            "later_notes", "owner_needed", "role_steps", "nothing_left_running"]
    for command in ("relay", "relay-orchestrator"):
      with self.subTest(command=command):
        payload = json.loads(self.ok(repo, "ai", command, "--json"))
        self.assertEqual(payload["report"], {"max_lines": 30, "lines": [
          {"key": key, "template": line} for key, line in zip(keys, templates)
        ]})

  def test_AiRelay_JsonText_IsOneLine(self):
    repo = self.project()
    for command in ("relay", "relay-orchestrator"):
      payload = json.loads(self.ok(repo, "ai", command, "--json"))
      for key in ("setup", "order", "stop"):
        if key == "setup" and command == "relay-orchestrator":
          continue
        for entry in payload[key]:
          text = entry if isinstance(entry, str) else entry["text"]
          with self.subTest(command=command, key=key, text=text):
            self.assertEqual(text, self.flat(text))
            self.assertIn(text, self.flat(self.golden(command)))

  def test_AiRelay_JsonOutcomes_MatchRelayRunEnd(self):
    repo = self.project()
    self.ok(repo, "add", "Example", "--importance", "2", "--urgency", "2", "--effort", "1")
    payload = json.loads(self.ok(repo, "ai", "relay", "--json"))
    self.assertEqual(payload["outcomes"], ["pass", "fail", "stopped"])
    for outcome in payload["outcomes"]:
      run = json.loads(self.ok(repo, "relay-run", "begin", "S01", "--json"))["run"]["id"]
      ended = json.loads(self.ok(repo, "relay-run", "end", run, "--outcome", outcome, "--json"))
      self.assertEqual(ended["run"]["outcome"], outcome)
    self.refuse(repo, "relay-run", "end", run, "--outcome", "invalid", code=2, error="bad_run_field")

  def test_AiRelay_Lean_KeepsContractLists(self):
    repo = self.project()
    for command in ("relay", "relay-orchestrator"):
      payload = json.loads(self.ok(repo, "ai", command, "--json"))
      self.assertIn("order", payload)
      self.assertEqual(json.loads(self.ok(repo, "ai", command, "--json", "--lean")), payload)
      # Simulate an empty contract list at the presentation boundary.
      empty_contract = {"order": [], "report": {"max_lines": 30, "lines": []}, "outcomes": [], "stop": [], "changes": []}
      if command == "relay":
        empty_contract = {"setup": [], **empty_contract}
      with patch("slicer.ai.relay_contract", return_value=empty_contract):
        empty = json.loads(self.ok(repo, "ai", command, "--json", "--lean"))
      for key in ("order", "outcomes", "stop", "changes"):
        self.assertEqual(empty[key], [])
      self.assertEqual(empty["report"]["lines"], [])
      if command == "relay":
        self.assertEqual(empty["setup"], [])
        with patch("slicer.ai.RELAY_END_OPTIONS", ()):
          no_options = json.loads(self.ok(repo, "ai", command, "--json", "--lean"))
        self.assertEqual(no_options["setup"][-1]["options"], [])

  def test_AiRelay_Setup_NamesOrchestratorAgentType(self) -> None:
    setup = self.section(ai.relay_text(), "## Setup", "## Order")
    self.assertIn("agent type `relay-orchestrator`", setup)
    self.assertIn("general-purpose agent briefed with `slicer ai relay-orchestrator`", setup)
    self.assertIn("set explicitly on every spawn", setup)
    self.assertIn("environment lines", setup)
    self.assertIn("environment lines verbatim", self.section(ai.relay_text(), "## Every brief", "\n## "))

  def test_AiRelay_Setup_ExplainsCollectiveAcceptanceOverridesAndRouting(self) -> None:
    repo = self.with_matrix()
    out = self.ok(repo, "ai", "relay")
    setup = self.section(out, "## Setup", "## Roles matrix")
    for phrase in (
      "resolves each role, the orchestrator included, to the first matching row",
      "lists each role's other matching rows as alternatives",
      "matrix order decides which row a role gets",
      "`--prefer-agent AGENT`",
      "`--select ROLE=ROW_ID`",
      "the harness the owner launched the relay from changes nothing by itself",
      "Show the resolved lineup once",
      "accept it as a whole or override individual rows",
      "does not by itself authorize starting paid sessions, committing or landing",
      "the other Setup decisions",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, setup)
    for name, text in (("project", out), ("generic", ai.relay_text())):
      with self.subTest(text=name):
        self.assertIn(
          "A role on the orchestrator's own harness runs as a native subagent; a role on "
          "another harness runs as a headless session in the slice worktree with the same brief.",
          self.section(text, "## Setup", "## Order"),
        )
        self.assertIn("headless session", self.section(text, "## Every brief", "\n## "))

  def test_AiRelay_Setup_SaysAnUnmatchedRoleIsReported(self) -> None:
    repo = self.with_matrix()
    setup = self.section(self.ok(repo, "ai", "relay"), "## Setup", "## Roles matrix")
    self.assertIn("(no matching row)", setup)
    self.assertIn("reports a role no row matches as unmatched", setup)
    self.assertIn("`unmatched` in its JSON", setup)
    self.assertIn("only for a row that matches the slice", setup)
    self.assertIn("goes into the lineup as named", setup)
    self.assertIn("not through `--select`", setup)
    self.assertNotIn("resolves every role", setup)

  def test_AiRelay_NoMatrix_SetupNamesNoSelectionFlags(self) -> None:
    setup = self.section(ai.relay_text(in_project=True), "## Setup", "## Order")
    self.assertNotIn("--prefer-agent", setup)
    self.assertNotIn("--select", setup)

  def test_AiRelay_ContractMarker_AgreesAcrossBothCommandsAndTheConstant(self) -> None:
    repo = self.project()
    self.assertEqual(ai.RELAY_CONTRACT, 9)
    texts = [self.ok(repo, "ai", "relay"), self.orchestrator(repo)]
    self.assertEqual({t.splitlines()[0] for t in texts}, {"Contract: relay v9"})
    payloads = [json.loads(self.ok(repo, "ai", c, "--json")) for c in ("relay", "relay-orchestrator")]
    self.assertEqual({p["contract"] for p in payloads}, {"relay v9"})

  def test_AiRelayOrchestrator_Waiting_NoForegroundAssumed(self) -> None:
    repo = self.project()
    text = self.orchestrator(repo)
    waiting = next(b for b in text.split("\n- ") if b.startswith("Waiting"))
    waiting = self.flat(waiting)
    for phrase in (
      "end the turn", "let its report wake you",
      "A nudge to report while a role you started still runs is not a reason to report",
      "Send the final report once, never an interim one",
      "resume it and tell it to finish",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, waiting)
    self.assertNotIn("run a role in the foreground", self.flat(text))
    self.assertNotIn("a role in the foreground", waiting)
    # The foreground is for a role's own long commands, which the brief asks for.
    self.assertIn(
      "Every role brief says to run long commands such as the full suite in the foreground "
      "with a long timeout", waiting)

  def test_AiRelayOrchestrator_FinishOnlyAfterPass(self) -> None:
    done = self.flat(ai.orchestrator_text("done"))
    self.assertIn("only after `VERDICT: PASS`", done)
    self.assertIn('slicer done ID --note "Describe the verified outcome" --render --check', done)
    self.assertNotIn("slicer handoff", done)
    handoff = self.flat(ai.orchestrator_text("handoff", "implementation report"))
    self.assertIn("only after `VERDICT: PASS`", handoff)
    self.assertIn("slicer handoff ID --render --check", handoff)
    self.assertNotIn("slicer done", handoff.casefold())
    self.assertNotIn("run done", handoff.casefold())
    self.assertIn(ai.handoff_policy_text("implementation report").strip().split("\n")[0][:60], handoff)
    self.assertIn("Every implementer brief says so", handoff)

  def test_AiRelayOrchestrator_ReadsTheProjectsFinish(self) -> None:
    repo = self.project()
    self.ok(repo, "config", "implement_finish", "handoff")
    self.ok(repo, "config", "handoff_requires_note_kind", "implementation report")
    text = self.orchestrator(repo)
    self.assertEqual(text, ai.orchestrator_text("handoff", "implementation report"))
    self.assertIn("slicer handoff ID --render --check", text)
    self.assertIn("implementation report", text)

  def test_AiRelayOrchestrator_Report_StartsWithMarkerEndsWithNothingRunning(self) -> None:
    text = ai.orchestrator_text()
    report = text.split("## Run report", 1)[1]
    block = report.split("```\n")[1].splitlines()
    self.assertEqual(block[0], self.MARKER)
    self.assertTrue(block[1].startswith("SLICE ID OUTCOME pass|fail|stopped BRANCH "))
    self.assertEqual(block[-1], "nothing left running: yes")
    self.assertLessEqual(len(block), 30)
    self.assertIn("At most 30 lines", self.flat(report))

  def test_AiRelayOrchestrator_LeavesOutOuterSteps(self) -> None:
    # The Conformance section forbids merge, land and push too, so it is not a rule that names them.
    text = ai.orchestrator_text().split("\n## Conformance\n", 1)[0]
    bullets = re.split(r"\n(?=- )", text)
    hits = [b for b in bullets
            if any(token in b for token in ("relay-run begin", "relay-run end", "push"))]
    self.assertEqual(len(hits), 1)
    self.assertTrue(hits[0].startswith("- Never run "), hits[0])
    self.assertNotIn("slicer relay-run begin", text)
    self.assertNotIn("slicer relay-run end", text)

  def test_AiRelayOrchestrator_NamesNoHarnessOrOutsideFile(self) -> None:
    blob = ai.orchestrator_text("done") + ai.orchestrator_text("handoff", "implementation report")
    for token in ("Claude Code", "SendMessage", "ToolSearch", "playbook", "relay.conf",
                  "http", "/Users", "\\"):
      with self.subTest(token=token):
        self.assertNotIn(token, blob)
    for token in ("claude", "codex", "grok", "openai", "anthropic", "gemini"):
      with self.subTest(token=token):
        self.assertNotIn(token, blob.casefold())

  def test_AiRelayOrchestrator_CommandExamples_AreAcceptedByTheParser(self) -> None:
    blob = ai.orchestrator_text("done") + ai.orchestrator_text("handoff", "implementation report")
    commands = re.findall(r"`(slicer [^`]+)`", blob)
    self.assertTrue(commands)
    parser = cli.build_parser()
    for command in commands:
      with self.subTest(command=command):
        parser.parse_args(shlex.split(command)[1:])

  def test_AiRelayOrchestrator_Json_HasOrchestratorKey(self) -> None:
    repo = self.project()
    text = self.orchestrator(repo)
    payload = json.loads(self.ok(repo, "ai", "relay-orchestrator", "--json"))
    self.assertEqual(list(payload), ["contract", "pinned", "orchestrator", "order", "report", "outcomes", "stop", "changes",
      "gate", "finding_kinds", "conformance"])
    self.assertEqual(payload["contract"], "relay v9")
    self.assertEqual(payload["orchestrator"].rstrip("\n"), text.rstrip("\n"))
    lean = json.loads(self.ok(repo, "ai", "relay-orchestrator", "--json", "--lean"))
    self.assertEqual(lean, payload)

  def test_AiRelayOrchestrator_OutsideProject_PrintsGenericWithWarning(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      code, out, err = repo.run("ai", "relay-orchestrator")
      self.assertEqual(code, 0)
      self.assertIn("printing the generic guide", err)
      self.assertEqual(out.rstrip("\n"), ai.orchestrator_text().rstrip("\n"))
      self.assertEqual(list(repo.root.iterdir()), [])

  def test_AiRelayOrchestrator_ReadsWithoutLockingOrLoadingState(self) -> None:
    repo = self.project()
    before = self.snapshot(repo)
    with patch("slicer.cli.store.project_lock", side_effect=AssertionError("lock")), \
         patch("slicer.cli.store.load", side_effect=AssertionError("load")):
      self.ok(repo, "ai", "relay-orchestrator")
    self.assertEqual(self.snapshot(repo), before)


class AiCompatibilityTests(RelayRolesCase):
  def test_InitRoles_WritesJsonAndRender(self) -> None:
    repo = self.project()
    out = json.loads(self.ok(repo, "ai", "relay", "--init-roles", "--json"))
    self.assertEqual(out, {"written": [str(repo.root / JSON)]})
    self.assertTrue((repo.root / JSON).is_file())
    self.assertEqual(repo.run("check")[0], 0)
    self.assertEqual(len(self.history(repo)), 1)

  def test_InitRoles_ExistingNeedsForce(self) -> None:
    repo = self.with_matrix()
    before = self.snapshot(repo)
    self.refuse(repo, "ai", "relay", "--init-roles", code=2, error="state")
    self.assertEqual(self.snapshot(repo), before)
    self.ok(repo, "ai", "relay", "--init-roles", "--force")

  def test_InitRoles_MatrixExistsAndMarkdownLeftover_StillNeedsForce(self) -> None:
    repo = self.with_matrix()
    repo.write(LEGACY, "# old\n")
    self.refuse(repo, "ai", "relay", "--init-roles", code=2, error="state")
    self.assertTrue((repo.root / LEGACY).exists())

  def test_InitRoles_ForceWithoutInitRoles_IsUsage(self) -> None:
    repo = self.project()
    self.refuse(repo, "ai", "relay", "--force", code=2, error="usage")

  def test_InitRoles_OutsideProject_IsStateAndWritesNothing(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      code, out, err = repo.run("ai", "relay", "--init-roles", "--json")
      self.assertEqual((code, json.loads(out)["error"]["code"]), (2, "state"))
      self.assertEqual(list(repo.root.iterdir()), [])

  def test_InitRoles_HoldsTheProjectLock(self) -> None:
    repo = self.project()
    before = self.snapshot(repo)
    with store.project_lock(repo.root), patch.dict(os.environ, {"SLICER_LOCK_TIMEOUT": "0"}):
      code, out, err = repo.run("ai", "relay", "--init-roles", "--json")
    self.assertEqual((code, json.loads(out)["error"]["code"]), (3, "locked"))
    self.assertEqual(self.snapshot(repo), before)

  def test_AiRelayRoles_PrintsTheSameAsShow(self) -> None:
    repo = self.with_matrix()
    self.assertEqual(self.ok(repo, "ai", "relay-roles"), self.ok(repo, "relay-roles", "show"))
    self.assertEqual(
      json.loads(self.ok(repo, "ai", "relay-roles", "--json")),
      json.loads(self.ok(repo, "relay-roles", "show", "--json")),
    )

  def test_AiRelayRoles_NoMatrix_IsNoRelayRoles(self) -> None:
    repo = self.project()
    error = self.refuse(repo, "ai", "relay-roles", code=2, error="no_relay_roles")
    self.assertIn("relay-roles init", error["message"])

  def test_AiRelayRoles_OutsideProject_IsState(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      code, out, err = repo.run("ai", "relay-roles", "--json")
      self.assertEqual((code, json.loads(out)["error"]["code"]), (2, "state"))


class RelayRolesDocsTests(unittest.TestCase):
  def read(self, rel: str) -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / rel).read_text(encoding="utf-8")

  def test_Docs_NameTheCodesAndTheWarning(self) -> None:
    agents = self.read("docs/agents.md")
    for code in ("unknown_role_row", "unknown_role_rule", "bad_role_field", "legacy_relay_roles"):
      with self.subTest(code=code):
        self.assertIn(f"`{code}`", agents)

  def test_Docs_CodeTable_NamesRuleAndRowTextForTheSharedCodes(self) -> None:
    rows = self.read("docs/agents.md").splitlines()
    blank = next(r for r in rows if r.startswith("| `blank_title` |"))
    newline = next(r for r in rows if r.startswith("| `newline_in_field` |"))
    self.assertIn("rule", blank)
    for word in ("rule", "agent", "vendor", "model", "reasoning", "note", "size"):
      self.assertIn(word, newline)

  def test_Docs_DocumentTheSplitConditionAndFreeSizes(self) -> None:
    commands = self.read("docs/commands.md")
    row = next(r for r in commands.splitlines() if r.startswith("| `relay-roles add"))
    self.assertIn("--split", row)
    self.assertIn("split part", commands)
    self.assertIn("whole slice", commands)
    self.assertNotIn("a size the project does not use", self.read("docs/agents.md"))

  def test_Docs_NameEveryRelayRolesCommandAndTheMigration(self) -> None:
    commands = self.read("docs/commands.md")
    for name in ("show", "init", "add", "set", "remove", "move", "rule-add", "rule-edit",
                 "rule-remove"):
      with self.subTest(name=name):
        self.assertIn(f"| `relay-roles {name}", commands)
    self.assertIn("--replace-legacy", commands)
    self.assertIn("relay-roles init --replace-legacy", self.read("CHANGELOG.md"))

  def test_Docs_PointAgentsAtTheJsonCommandsNotTheMarkdownFile(self) -> None:
    for rel in ("AGENTS.md", "docs/agents.md", "docs/getting-started.md"):
      with self.subTest(rel=rel):
        text = self.read(rel)
        self.assertIn("relay-roles.json", text)
        # The old file may appear only as the leftover the migration removes.
        for paragraph in text.split("\n\n"):
          if "relay-roles.md" in paragraph:
            self.assertRegex(paragraph, r"legacy|leftover|1\.9")
    skill = ai.skill_text()
    self.assertIn("never from `.slicer/relay-roles.json`", skill)
    self.assertNotIn("relay-roles.md", skill)


if __name__ == "__main__":
  unittest.main()
