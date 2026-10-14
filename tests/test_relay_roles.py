"""The relay role matrix is versioned JSON that only `slicer relay-roles` changes (s257).

The tests drive the CLI in-process and read the files it writes. Tests that need a
hand-edited or corrupt matrix write the JSON fixture directly; production code never
does, because `slicer relay-roles` is the only way to change it.
"""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

import support  # noqa: F401 -- puts src/ on sys.path
from slicer import ai, jsonio, model, render, roles, store

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
      ["size", "effort", "importance", "urgency", "well_specified", "split"],
    )
    self.assertEqual(list(data["rules"][0]), ["id", "text"])
    self.assertEqual(json.dumps(roles.RoleMatrix.from_dict(data).to_dict()), json.dumps(data))

  def test_Skeleton_FirstRowAndFirstRule_NumberTheExampleFromThere(self) -> None:
    matrix = roles.skeleton(first_row=8, first_rule=6)
    self.assertEqual([r.id for r in matrix.rows], [f"r{n}" for n in range(8, 15)])
    self.assertEqual([r.id for r in matrix.rules], [f"u{n}" for n in range(6, 11)])
    self.assertEqual((matrix.next_row, matrix.next_rule), (15, 11))
    plain = roles.skeleton()
    self.assertEqual([r.id for r in plain.rows][0], "r1")
    self.assertEqual(
      [(r.role, r.when.to_dict()) for r in matrix.rows],
      [(r.role, r.when.to_dict()) for r in plain.rows],
    )

  def test_Skeleton_HoldsSevenStructuredRowsAndFiveRules(self) -> None:
    matrix = roles.skeleton()
    self.assertEqual(
      [(r.id, r.role) for r in matrix.rows],
      [("r1", "implementer"), ("r2", "implementer"), ("r3", "implementer"),
       ("r4", "implementer"), ("r5", "reviewer"), ("r6", "gate"), ("r7", "architect")],
    )
    self.assertEqual({r.agent for r in matrix.rows}, {"Claude Code"})
    first = matrix.rows[0]
    self.assertEqual(first.when.size, ["S", "M"])
    self.assertEqual(first.when.effort, [1])
    self.assertIs(first.when.well_specified, True)
    self.assertEqual(
      matrix.rows[2].when.to_dict(), roles.RoleWhen(split=False).to_dict(),
    )
    self.assertEqual(matrix.rows[6].when.size, ["L"])
    self.assertEqual(
      [rule.text for rule in matrix.rules][2],
      "Roles on the orchestrator's own agent run as subagents; a role on another agent "
      "runs headless in the slice worktree with the same brief.",
    )
    self.assertEqual([rule.id for rule in matrix.rules], ["u1", "u2", "u3", "u4", "u5"])
    self.assertEqual((matrix.next_row, matrix.next_rule), (8, 6))

  def test_Skeleton_ImplementerRows_GeneralOnesAreWholeSliceAndPartRowIsSplit(self) -> None:
    matrix = roles.skeleton()
    self.assertEqual(
      [(r.id, r.when.split) for r in matrix.rows],
      [("r1", False), ("r2", False), ("r3", False), ("r4", True),
       ("r5", None), ("r6", None), ("r7", None)],
    )
    part = matrix.rows[3]
    self.assertEqual(
      (part.role, part.agent, part.vendor, part.model, part.reasoning),
      ("implementer", "Claude Code", "Claude", "haiku", "xhigh"),
    )
    self.assertIn("per part", part.note)
    self.assertEqual(part.when.to_dict(), roles.RoleWhen(split=True).to_dict())

  def test_Skeleton_Rules_NameTheSplitRule(self) -> None:
    rules = {r.id: r.text for r in roles.skeleton().rules}
    self.assertEqual(
      rules["u5"],
      "Split a slice only into parts with disjoint files and their own tests; "
      "a slice too big for one implementer is two slices.",
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
    data = roles.skeleton().to_dict() | {"version": 2}
    with self.assertRaises(roles.StateError) as caught:
      roles.RoleMatrix.from_dict(data)
    self.assertEqual(caught.exception.code, "schema_too_new")


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
    got = roles.recommend(roles.RoleMatrix(rows=rows), _item(size="S"), True, False)
    self.assertEqual([role for role, _ in got], list(roles.ROLE_NAMES))
    self.assertEqual([row.id for _, row in got], ["r5", "r3", "r2", "r1"])

  def test_Recommend_RowOrder_DecidesBetweenTwoMatchingRows(self) -> None:
    first = [_row("r1", "gate", size=["S"]), _row("r2", "gate")]
    self.assertEqual(_pick(first, _item(size="S"))["gate"], "r1")
    self.assertEqual(_pick(first[::-1], _item(size="S"))["gate"], "r2")

  def test_Recommend_NoRowForARole_IsNoneForThatRoleOnly(self) -> None:
    got = _pick([_row("r1", "gate"), _row("r2", "reviewer", size=["L"])], _item(size="S"))
    self.assertEqual(got, {"implementer": None, "reviewer": None, "gate": "r1", "architect": None})

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

  def test_Recommend_Skeleton_ProposesTheExampleRowsForSmallAndLargeSlices(self) -> None:
    matrix = roles.skeleton()
    small = roles.recommend(matrix, _item(size="S", effort=1), True, False)
    self.assertEqual(_ids(small), ["r1", "r5", "r6", None])
    large = roles.recommend(matrix, _item(size="L", effort=3), False, False)
    self.assertEqual(_ids(large), ["r3", "r5", "r6", "r7"])

  def test_Recommend_Skeleton_SplitYesReturnsThePartRowAndSplitNoTheWholeSliceRows(self) -> None:
    matrix = roles.skeleton()
    item = _item(size="S", effort=1)
    part = roles.recommend(matrix, item, True, True)
    self.assertEqual(_ids(part), ["r4", "r5", "r6", None])
    self.assertEqual(
      (part[0][1].model, part[0][1].reasoning), ("haiku", "xhigh"),
    )
    whole = roles.recommend(matrix, item, True, False)
    self.assertEqual(_ids(whole), ["r1", "r5", "r6", None])
    # A whole slice that no specific row matches still falls to the general row, never the part row.
    other = roles.recommend(matrix, _item(size="M", effort=3), False, False)
    self.assertEqual(_ids(other), ["r3", "r5", "r6", None])


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
    self.assertEqual([r["id"] for r in data["rows"]], [f"r{n}" for n in range(8, 15)])
    expected = roles.skeleton(first_row=8, first_rule=6).to_dict()
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
    self.assertEqual([r["id"] for r in data["rows"]], [f"r{n}" for n in range(9, 16)])
    self.assertEqual([r["id"] for r in data["rules"]], [f"u{n}" for n in range(7, 12)])
    self.assertEqual((data["next_row"], data["next_rule"]), (16, 12))
    self.assertEqual([r["role"] for r in data["rows"]], [r.role for r in roles.skeleton().rows])
    row = json.loads(self.ok(repo, "relay-roles", "add", "--role", "gate", "--json"))
    rule = json.loads(self.ok(repo, "relay-roles", "rule-add", "Again.", "--json"))
    self.assertEqual((row["id"], rule["id"]), ("r16", "u12"))

  def test_Init_Force_CorruptFile_StartsFromTheSkeleton(self) -> None:
    repo = self.with_matrix()
    repo.write(JSON, "{")
    self.ok(repo, "relay-roles", "init", "--force")
    self.assertEqual(self.matrix(repo), roles.skeleton().to_dict())

  def test_Init_Force_NewerSchemaFile_IsNotOverwritten(self) -> None:
    repo = self.with_matrix()
    newer = jsonio.dumps(self.matrix(repo) | {"version": 2})
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
    repo.write(JSON, jsonio.dumps(self.matrix(repo) | {"version": 2}))
    self.refuse(repo, "relay-roles", "show", code=3, error="schema_too_new")

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
    data["rows"][1]["id"] = "r1"
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
    self.assertEqual(row["id"], "r8")
    self.assertEqual(row["when"], {
      "size": ["S", "M"], "effort": [1], "importance": [2, 3], "urgency": [1],
      "well_specified": True, "split": False,
    })
    self.assertEqual(
      (row["role"], row["agent"], row["vendor"], row["model"], row["reasoning"], row["note"]),
      ("reviewer", "Codex CLI", "OpenAI", "m1", "high", "second opinion"),
    )
    self.assertEqual(self.matrix(repo)["rows"][-1], row)
    self.assertEqual(self.matrix(repo)["next_row"], 9)

  def test_Add_IdIsNeverReusedAfterRemove(self) -> None:
    repo = self.with_matrix()
    self.ok(repo, "relay-roles", "remove", "r7")
    row = json.loads(self.ok(repo, "relay-roles", "add", "--role", "architect", "--json"))
    self.assertEqual(row["id"], "r8")

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


class RelayRolesEditTests(RelayRolesCase):
  def test_Set_ChangesOnlyTheGivenFields(self) -> None:
    repo = self.with_matrix()
    before = self.matrix(repo)
    row = json.loads(self.ok(
      repo, "relay-roles", "set", "r1", "--model", "opus", "--size", "L", "--json",
    ))
    expected = before["rows"][0] | {"model": "opus"}
    expected["when"] = expected["when"] | {"size": ["L"]}
    self.assertEqual(row, expected)
    after = self.matrix(repo)
    self.assertEqual(after["rows"][1:], before["rows"][1:])
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
    self.assertEqual([r["id"] for r in self.matrix(repo)["rows"]], ["r1", "r3", "r4", "r5", "r6", "r7"])

  def test_Move_BeforeAndLast_ReorderRows(self) -> None:
    repo = self.with_matrix()
    self.ok(repo, "relay-roles", "move", "r7", "--before", "r2")
    self.assertEqual(
      [r["id"] for r in self.matrix(repo)["rows"]], ["r1", "r7", "r2", "r3", "r4", "r5", "r6"],
    )
    self.ok(repo, "relay-roles", "move", "r1", "--last")
    self.assertEqual(
      [r["id"] for r in self.matrix(repo)["rows"]], ["r7", "r2", "r3", "r4", "r5", "r6", "r1"],
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
    self.assertEqual(rule, {"id": "u6", "text": "Keep it small."})
    self.ok(repo, "relay-roles", "rule-remove", "u6")
    rule = json.loads(self.ok(repo, "relay-roles", "rule-add", "Again.", "--json"))
    self.assertEqual(rule["id"], "u7")

  def test_RuleEditAndRemove_ChangeOnlyThatRule(self) -> None:
    repo = self.with_matrix()
    before = self.matrix(repo)
    rule = json.loads(self.ok(repo, "relay-roles", "rule-edit", "u2", "New text.", "--json"))
    self.assertEqual(rule, {"id": "u2", "text": "New text."})
    self.ok(repo, "relay-roles", "rule-remove", "u1")
    after = self.matrix(repo)
    self.assertEqual([r["id"] for r in after["rules"]], ["u2", "u3", "u4", "u5"])
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
      ("relay-roles.r8", "add", "", "gate"),
      ("relay-roles.r1", "set", "", "model"),
      ("relay-roles.r1", "remove", "", "implementer"),
      ("relay-roles.r2", "move", "1", "7"),
      ("relay-roles.u6", "rule-add", "", ""),
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
    for row_id in ("r1", "r2", "r3", "r4", "r5", "r6", "r7"):
      self.ok(repo, "relay-roles", "remove", row_id)
    for rule_id in ("u1", "u2", "u3", "u4", "u5"):
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

  def test_Recommend_Json_ReturnsRoleOrderedRowsAndUnmatched(self) -> None:
    repo = self.with_matrix()
    item = self.item(repo, "--size", "S", "--effort", "1")
    self.ok(repo, "relay-roles", "remove", "r7")
    payload = self.recommend(repo, item)
    self.assertEqual(list(payload), ["id", "well_specified", "split", "roles", "unmatched"])
    self.assertEqual((payload["id"], payload["well_specified"], payload["split"]), (item, True, False))
    self.assertEqual([r["role"] for r in payload["roles"]], list(roles.ROLE_NAMES))
    self.assertEqual(list(payload["roles"][0]), ["role", "row"])
    rows = {r["id"]: r for r in self.matrix(repo)["rows"]}
    self.assertEqual(payload["roles"][0]["row"], rows["r1"])
    self.assertEqual(payload["roles"][1]["row"], rows["r5"])
    self.assertEqual(payload["roles"][2]["row"], rows["r6"])
    self.assertIsNone(payload["roles"][3]["row"])
    self.assertEqual(payload["unmatched"], ["architect"])

  def test_Recommend_Json_NothingUnmatched_ListsNone(self) -> None:
    repo = self.with_matrix()
    item = self.item(repo, "--size", "L")
    payload = self.recommend(repo, item, ws="no")
    self.assertEqual(payload["unmatched"], [])
    self.assertEqual(payload["roles"][0]["row"]["id"], "r3")
    self.assertEqual(payload["roles"][3]["row"]["id"], "r7")

  def test_Recommend_Lean_KeepsTheShapeWhenRolesAreUnmatched(self) -> None:
    repo = self.with_matrix()
    for row_id in ("r1", "r2", "r3", "r4", "r5", "r6", "r7"):
      self.ok(repo, "relay-roles", "remove", row_id)
    item = self.item(repo)
    payload = json.loads(self.ok(
      repo, "relay-roles", "recommend", item, "--well-specified", "yes", "--split", "no",
      "--json", "--lean",
    ))
    self.assertEqual(payload["roles"], [{"role": r, "row": None} for r in roles.ROLE_NAMES])
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
    self.assertEqual(len(lines), 4)
    self.assertTrue(lines[0].startswith("implementer  r1  Claude Code  Claude  haiku  xhigh"))
    self.assertTrue(lines[1].startswith("reviewer  r5  "))
    self.assertTrue(lines[2].startswith("gate  r6  "))
    self.assertEqual(lines[3], "architect  (no matching row)")

  def test_Recommend_RowWithEmptyFields_PrintsDashesNotBlankColumns(self) -> None:
    repo = self.with_matrix()
    for row_id in ("r1", "r2", "r3", "r4"):
      self.ok(repo, "relay-roles", "remove", row_id)
    self.ok(repo, "relay-roles", "add", "--role", "implementer", "--model", "whole")
    item = self.item(repo)
    line = self.ok(
      repo, "relay-roles", "recommend", item, "--well-specified", "yes", "--split", "no",
    ).splitlines()[0]
    self.assertEqual(line, "implementer  r8  -  -  whole  -")

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
    for row_id in ("r1", "r2", "r3", "r4"):
      self.ok(repo, "relay-roles", "remove", row_id)
    self.ok(repo, "relay-roles", "add", "--role", "implementer", "--split", "no", "--model", "whole")
    self.ok(repo, "relay-roles", "add", "--role", "implementer", "--split", "yes", "--model", "part")
    item = self.item(repo)
    part = self.recommend(repo, item, split="yes")
    self.assertEqual(part["roles"][0]["row"]["model"], "part")
    self.assertIs(part["roles"][0]["row"]["when"]["split"], True)
    whole = self.recommend(repo, item, split="no")
    self.assertEqual(whole["roles"][0]["row"]["model"], "whole")
    self.ok(repo, "relay-roles", "remove", "r9")
    self.assertEqual(self.recommend(repo, item, split="yes")["roles"][0]["row"], None)

  def test_Recommend_ItemScores_SelectRowsByImportanceAndUrgency(self) -> None:
    repo = self.with_matrix()
    self.ok(repo, "relay-roles", "add", "--role", "gate", "--importance", "3", "--urgency", "3", "--model", "hot")
    self.ok(repo, "relay-roles", "move", "r8", "--before", "r6")
    hot = self.item(repo, "--importance", "3", "--urgency", "3")
    calm = self.item(repo, "--importance", "3", "--urgency", "1")
    self.assertEqual(self.recommend(repo, hot)["roles"][2]["row"]["model"], "hot")
    self.assertEqual(self.recommend(repo, calm)["roles"][2]["row"]["id"], "r6")


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
