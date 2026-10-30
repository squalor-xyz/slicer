"""A scripted orchestrator conforms to the relay contract (s406).

The Conformance section states what any orchestrator owes, agent or program, and
`ai relay --json` carries the machine-readable parts. Each new JSON list is built from
the data that renders the prose, so these tests read the prose back and compare.
"""

from __future__ import annotations

import json
import re
import unittest

import support
from slicer import ai, cli, roles

NEW_RELAY_KEYS = ["roles", "gate", "finding_kinds", "conformance"]
NEW_ORCHESTRATOR_KEYS = ["gate", "finding_kinds", "conformance"]
RELAY_KEYS = ["contract", "pinned", "check_command", "relay", "setup", "order", "land", "report", "outcomes", "stop", "changes"]
ORCHESTRATOR_KEYS = ["contract", "pinned", "orchestrator", "order", "report", "outcomes", "stop", "changes"]
OBLIGATIONS = [
  "role_order", "gate_rule", "finding_kinds", "stop_rules", "blocker_wait", "role_fallback", "ledger",
  "finish_after_pass", "run_report", "worktree_scope", "commit_scope",
]
ROLE_KEYS = [
  "outer_session", "orchestrator", "implementer", "reviewer", "architect", "ui_pass", "gate", "spec_check",
]


def flat(text: str) -> str:
  return " ".join(text.split())


def section(text: str, heading: str) -> str:
  """The body of `## heading`, up to the next `## ` heading or the end."""
  return text.split(f"\n## {heading}\n\n", 1)[1].split("\n## ", 1)[0]


def bullets(body: str) -> list[str]:
  return [flat(item) for item in re.findall(r"(?m)^- (.*(?:\n {2}[^\n]+)*)", body)]


class RelayConformanceCase(unittest.TestCase):
  def texts(self):
    for finish in ("done", "handoff"):
      yield f"relay-{finish}", ai.relay_text(finish)
      yield f"orchestrator-{finish}", ai.orchestrator_text(finish)
      yield f"session-{finish}", ai.orchestrator_text(finish, session=True)

  def payload(self, command: str, *extra: str) -> dict:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      self.assertEqual(repo.run("init")[0], 0)
      code, out, err = repo.run("ai", command, "--json", *extra)
      self.assertEqual((code, err), (0, ""), out)
      return json.loads(out)


class ConformanceSectionTests(RelayConformanceCase):
  def test_RelayConformance_Section_BindsAnAgentOrAProgramInBothContracts(self) -> None:
    for label, text in self.texts():
      with self.subTest(label=label):
        body = flat(section(text, "Conformance"))
        for phrase in (
          "These obligations bind any orchestrator, whether an LLM agent briefed with this text "
          "or a program that spawns the roles, records the ledger and applies the rules in code.",
          "a program meets each obligation in its own code",
          "`slicer ai relay --json` and `slicer ai relay-orchestrator --json` carry",
        ):
          self.assertIn(phrase, body)

  def test_RelayConformance_Section_SitsAfterStopAndBeforeCloseOrReport(self) -> None:
    for label, text in self.texts():
      with self.subTest(label=label):
        self.assertLess(text.index("\n## Stop\n"), text.index("\n## Conformance\n"))
        after = "\n## Close\n" if label.startswith("relay") else "\n## Run report\n"
        self.assertLess(text.index("\n## Conformance\n"), text.index(after))

  def test_RelayConformance_Obligations_NameEachRuleAnOrchestratorOwes(self) -> None:
    for label, text in self.texts():
      with self.subTest(label=label):
        items = bullets(section(text, "Conformance"))
        self.assertEqual(len(items), len(OBLIGATIONS))
        for needle, item in zip(
          ("Run the roles in Order's sequence", "Decide the gate on the criteria in Gate",
           "Tag every reviewer, UI pass and gate finding", "Stop when a stop condition in Stop holds",
           "Wait for the answer to a `blocker` owner question",
           "When a role's round ends in a vendor limit",
           "Record each role's round in the run ledger", "Run the closing note and the finish step only after",
           "Return one run report", "Work only in the worktree the brief names",
           "Commit on the slice branch only when the brief allows it"),
          items,
        ):
          self.assertTrue(item.startswith(needle), item)
    body = flat(section(ai.relay_text(), "Conformance"))
    for phrase in ("`VERDICT: PASS`", "`nothing left running: yes`", "OUTCOME stopped"):
      self.assertIn(phrase, body)
    self.assertNotIn("contract_marker", [item["key"] for item in ai.relay_contract()["conformance"]])

  def test_RelayConformance_StopRules_NameThePermissionDenialStop(self) -> None:
    for label, text in self.texts():
      with self.subTest(label=label):
        items = {key: item for key, item in zip(OBLIGATIONS, bullets(section(text, "Conformance")))}
        for phrase in (
          "A permission denial stops before the commit, with the denial's exact text as a "
          "`blocker` owner-needed question.",
          "with OUTCOME stopped",
        ):
          self.assertIn(phrase, items["stop_rules"])
    for command in ("relay", "relay-orchestrator"):
      with self.subTest(command=command):
        stop_rules = next(
          item["text"] for item in ai.relay_contract(orchestrator=command != "relay")["conformance"]
          if item["key"] == "stop_rules"
        )
        self.assertIn("A permission denial stops before the commit", stop_rules)
        # The denial is one of the keyed stop conditions Conformance points to, not restated.
        keys = [item["key"] for item in ai.relay_contract()["stop"]]
        self.assertIn("permission_denied", keys)
        self.assertEqual(len(ai.RELAY_STOP), 4)

  def test_RelayConformance_ScopeRules_NameWorktreeCommitAndFinalReportOnce(self) -> None:
    for label, text in self.texts():
      with self.subTest(label=label):
        items = dict(zip(OBLIGATIONS, bullets(section(text, "Conformance"))))
        self.assertEqual(
          items["worktree_scope"],
          "Work only in the worktree the brief names, with absolute paths and no change of "
          "directory, and write no product code.",
        )
        self.assertEqual(
          items["commit_scope"],
          "Commit on the slice branch only when the brief allows it. Never merge, land, push or "
          "rebase onto main: the outer session owns those.",
        )
        self.assertIn("Send it once, never an interim run report.", items["run_report"])

  def test_RelayConformance_Literals_ComeFromTheGateAndReportData(self) -> None:
    default = {item["key"]: item["text"] for item in ai.relay_contract()["conformance"]}
    self.assertIn(ai.RELAY_GATE_VERDICTS[0], default["finish_after_pass"])
    self.assertNotIn("VERDICT:", default["gate_rule"])
    last = next(t for k, t in ai.RELAY_REPORT_LINES if k == "nothing_left_running")
    self.assertIn(last, default["run_report"])
    swapped = dict(ai._relay_conformance("VERDICT: OK", "all done: yes"))
    self.assertIn("`VERDICT: OK`", swapped["finish_after_pass"])
    self.assertIn("`all done: yes`", swapped["run_report"])
    for key, text in swapped.items():
      self.assertNotIn("VERDICT: PASS", text, key)
      self.assertNotIn("nothing left running", text, key)
      self.assertNotIn("{", text, key)

  def test_RelayConformance_Section_NamesNoProjectAndNoCommandTheParserRefuses(self) -> None:
    parser = cli.build_parser()
    for label, text in self.texts():
      with self.subTest(label=label):
        body = section(text, "Conformance")
        for token in ("ork", "adj", "check_command", "pinned"):
          self.assertNotRegex(body.casefold(), r"(?<![a-z0-9_])" + re.escape(token) + r"(?![a-z0-9_])")
        for command in re.findall(r"`(slicer [^`]+)`", body):
          parser.parse_args(command.split()[1:])

  def test_RelayConformance_Section_NamesNoRelayRunBeginOrEnd(self) -> None:
    # The outer session owns `relay-run begin` and `end`; an orchestrator is never told to run them.
    for label, text in self.texts():
      with self.subTest(label=label):
        body = section(text, "Conformance")
        self.assertNotIn("relay-run begin", body)
        self.assertNotIn("relay-run end", body)
    for command in ("relay", "relay-orchestrator"):
      with self.subTest(command=command):
        conformance = json.dumps(self.payload(command)["conformance"])
        self.assertNotIn("relay-run begin", conformance)
        self.assertNotIn("relay-run end", conformance)


class ConformanceJsonTests(RelayConformanceCase):
  def test_AiRelayJson_NewKeys_AppendAfterChangesInFixedOrder(self) -> None:
    payload = self.payload("relay")
    self.assertEqual(list(payload), RELAY_KEYS + NEW_RELAY_KEYS)
    lean = self.payload("relay", "--lean")
    self.assertEqual(list(lean), RELAY_KEYS + NEW_RELAY_KEYS)
    self.assertEqual(lean, payload)

  def test_AiRelayOrchestratorJson_NewKeys_AppendAfterChangesWithoutRoles(self) -> None:
    payload = self.payload("relay-orchestrator")
    self.assertEqual(list(payload), ORCHESTRATOR_KEYS + NEW_ORCHESTRATOR_KEYS)
    self.assertNotIn("roles", payload)
    self.assertEqual(self.payload("relay-orchestrator", "--lean"), payload)

  def test_AiRelayJson_PinnedAndCheckCommand_PinnedFollowsContractAndContractIsCurrent(self) -> None:
    # The pin (s387) and check_command (s379) are built: `ai relay` carries both right
    # after contract, and `ai relay-orchestrator` carries the pin only.
    payload = self.payload("relay")
    self.assertEqual(payload["contract"], "relay v11")
    self.assertEqual(list(payload)[:3], ["contract", "pinned", "check_command"])
    self.assertIsNone(payload["pinned"])
    self.assertEqual(payload["check_command"], "")
    orchestrator = self.payload("relay-orchestrator")
    self.assertEqual(orchestrator["contract"], "relay v11")
    self.assertEqual(list(orchestrator)[:2], ["contract", "pinned"])
    self.assertIsNone(orchestrator["pinned"])
    self.assertNotIn("check_command", orchestrator)
    self.assertEqual(ai.RELAY_CONTRACT, 11)
    self.assertEqual([version for version, _ in ai.RELAY_CHANGES][-1], 11)

  def test_AiRelayJson_Conformance_ListsKeyAndTextForEachObligation(self) -> None:
    for command in ("relay", "relay-orchestrator"):
      with self.subTest(command=command):
        items = self.payload(command)["conformance"]
        self.assertEqual([item["key"] for item in items], OBLIGATIONS)
        for item in items:
          self.assertEqual(list(item), ["key", "text"])
          self.assertNotIn("\n", item["text"])
          self.assertEqual(item["text"], flat(item["text"]))

  def test_AiRelayJson_Conformance_AgreesWithTheProse(self) -> None:
    for finish in ("done", "handoff"):
      for command, text in (("relay", ai.relay_text(finish)), ("relay-orchestrator", ai.orchestrator_text(finish))):
        with self.subTest(command=command, finish=finish):
          expected = bullets(section(text, "Conformance"))
          self.assertEqual([item["text"] for item in ai.relay_contract(
            finish, orchestrator=command == "relay-orchestrator")["conformance"]], expected)
    self.assertEqual(
      [item["text"] for item in self.payload("relay")["conformance"]],
      bullets(section(ai.relay_text(), "Conformance")),
    )

  def test_AiRelayJson_Roles_AgreesWithTheRolesSection(self) -> None:
    roles = self.payload("relay")["roles"]
    self.assertEqual([item["role"] for item in roles], ROLE_KEYS)
    prose = flat(section(ai.relay_text(), "Roles"))
    last = -1
    for item in roles:
      with self.subTest(role=item["role"]):
        self.assertEqual(list(item), ["role", "text", "ledger_role"])
        self.assertEqual(item["text"], flat(item["text"]))
        position = prose.find(item["text"])
        self.assertGreater(position, last)
        last = position
    # The paragraphs that are not roles stay prose: the nesting fallback, the split and the models.
    paragraphs = [flat(part) for part in section(ai.relay_text(), "Roles").split("\n\n")]
    role_texts = {item["text"] for item in roles}
    self.assertEqual(len([part for part in paragraphs if part in role_texts]), len(roles))
    self.assertEqual(len([part for part in paragraphs if part not in role_texts]), 3)

  def test_AiRelayJson_Roles_LedgerRoleNamesTheStepEachRoleRecords(self) -> None:
    roles_json = self.payload("relay")["roles"]
    self.assertEqual([(item["role"], item["ledger_role"]) for item in roles_json], [
      ("outer_session", None), ("orchestrator", "orchestrator"),
      ("implementer", "implementer"), ("reviewer", "reviewer"), ("architect", "architect"),
      ("ui_pass", "ui"), ("gate", "gate"), ("spec_check", "spec-check"),
    ])
    for item in roles_json:
      with self.subTest(role=item["role"]):
        if item["ledger_role"] is not None:
          self.assertIn(item["ledger_role"], roles.LEDGER_ROLE_NAMES)
    # `--lean` drops nulls elsewhere, so the outer session's null must survive it.
    self.assertEqual(self.payload("relay", "--lean")["roles"], roles_json)

  def test_AiRelayJson_Gate_CriteriaAndVerdictsAgreeWithTheProse(self) -> None:
    for command, text in (("relay", ai.relay_text()), ("relay-orchestrator", ai.orchestrator_text())):
      with self.subTest(command=command):
        gate = self.payload(command)["gate"]
        self.assertEqual(list(gate), ["criteria", "verdicts"])
        self.assertEqual(gate["verdicts"], ["VERDICT: PASS", "VERDICT: FAIL"])
        prose = flat(section(text, "Gate"))
        criteria = prose.split("The criteria are: ", 1)[1].split(". The first line", 1)[0]
        head, last = criteria.rsplit(", and ", 1)
        self.assertEqual(gate["criteria"], head.split(", ") + [last])
        self.assertEqual(len(gate["criteria"]), 5)
        self.assertIn("The first line is exactly `VERDICT: PASS` or `VERDICT: FAIL`.", prose)
        self.assertIn("no `fix` finding", gate["criteria"])
        self.assertIn("no `blocker` owner finding", gate["criteria"])

  def test_RelayGate_CriteriaSentence_IsWrappedLikeTheRestOfTheProse(self) -> None:
    for command, text in (("relay", ai.relay_text()), ("relay-orchestrator", ai.orchestrator_text())):
      with self.subTest(command=command):
        lines = section(text, "Gate").splitlines()
        self.assertLessEqual(max(len(line) for line in lines), 90)
        self.assertEqual(lines[0], "Name the diff under review. The criteria are: earlier blockers resolved with a")

  def test_AiRelayJson_FindingKinds_AgreeWithTheGateBullets(self) -> None:
    for command, text in (("relay", ai.relay_text()), ("relay-orchestrator", ai.orchestrator_text())):
      with self.subTest(command=command):
        kinds = self.payload(command)["finding_kinds"]
        self.assertEqual([item["kind"] for item in kinds], ["fix", "owner", "later"])
        expected = bullets(section(text, "Gate").split("Findings:", 1)[1])
        self.assertEqual([f"`{item['kind']}`: {item['text']}" for item in kinds], expected)
        for item in kinds:
          self.assertEqual(list(item), ["kind", "text"])

  def test_AiRelayJson_SharedParts_IdenticalInBothContracts(self) -> None:
    relay = self.payload("relay")
    orchestrator = self.payload("relay-orchestrator")
    for key in ("gate", "finding_kinds", "conformance"):
      with self.subTest(key=key):
        self.assertEqual(relay[key], orchestrator[key])

  def test_AiRelayJson_ExistingParts_AreReusedNotRestated(self) -> None:
    # The run report fields, outcomes and stop rules stay the existing keys; conformance
    # refers to them rather than carrying a second copy.
    payload = self.payload("relay")
    self.assertEqual(payload["outcomes"], ["pass", "fail", "stopped"])
    self.assertEqual(len(payload["stop"]), 4)
    self.assertEqual(payload["report"]["lines"][-1]["key"], "nothing_left_running")
    self.assertEqual(payload["contract"], ai.contract_line())
    text = json.dumps(payload["conformance"])
    for stop in payload["stop"]:
      self.assertNotIn(stop["text"], text)


class VendorLimitTests(RelayConformanceCase):
  """A vendor limit or an error exit falls back to the role's next matching row (s405, s424)."""

  def stop(self, text: str) -> str:
    return flat(section(text, "Stop"))

  def test_Stop_VendorLimit_FallsBackToTheNextRowAndNamesTheSwitch(self) -> None:
    for label, text in self.texts():
      with self.subTest(label=label):
        stop = self.stop(text)
        for phrase in (
          "A vendor limit or an error exit is not a stop condition.",
          "start the same round as a new `relay-run step` on a fresh agent on the role's next "
          "matching matrix row, with the partial diff left in the worktree",
          "`slicer relay-run step RUN --step STEP --phase fail --reason TEXT`",
          "Name the switch on the report's deviations line as `role switched: ROLE FROM to TO "
          "(REASON)`",
          "With no eligible row left (a project with no role matrix has none), or on any other "
          "failure, stop.",
        ):
          self.assertIn(phrase, stop)

  def test_Stop_VendorLimit_ClassifiesFromTheVendorErrorAndQuotesIt(self) -> None:
    for label, text in self.texts():
      with self.subTest(label=label):
        stop = self.stop(text)
        self.assertIn(
          "A role's round that ends in a rate limit, out of credits or an error exit of the "
          "agent process is a failed round", stop,
        )
        self.assertIn("classify it from the vendor's or harness's error or the process's exit, "
                      "not from the role's own account", stop)
        self.assertIn("Record the failed step with that error as its reason", stop)

  def test_Stop_VendorLimit_NextRowSkipsSameVendorOnlyOnOutOfCredits(self) -> None:
    for label, text in self.texts():
      with self.subTest(label=label):
        self.assertIn(
          "Rows only move forward: on out of credits skip later rows on the same vendor, on a "
          "rate limit or an error exit take the plain next matching row. The new row keeps the "
          "role for the rest of the run.", self.stop(text),
        )

  def test_Stop_VendorLimit_KeepsTheIndependenceStatedOnceHere(self) -> None:
    for label, text in self.texts():
      with self.subTest(label=label):
        self.assertIn(
          "The fresh agent is never a resumed one. Independence holds on the new row as on every "
          "row: a reviewer or gate never wrote code in the slice (in a split, in any part of it), "
          "and an implementer never reuses the reviewer's or gate's agent.", self.stop(text),
        )

  def test_Stop_VendorLimit_OuterSessionOrOwnerStartsTheNextOrchestrator(self) -> None:
    for label, text in self.texts():
      with self.subTest(label=label):
        self.assertIn(
          "When the orchestrator's own round ends in a vendor limit or an error exit, the next matching row takes over "
          "from the ledger and the worktree: the outer session starts a new orchestrator on it, "
          "or the owner restarts the session when the session orchestrates itself.",
          self.stop(text),
        )

  def test_Stop_VendorLimit_FromAndToAreRowIds(self) -> None:
    for label, text in self.texts():
      with self.subTest(label=label):
        self.assertIn("FROM and TO being row ids", self.stop(text))

  def test_Stop_KeyedConditions_AreFourAndTheFallbackIsNotOne(self) -> None:
    self.assertEqual([key for key, _ in ai.RELAY_STOP], [
      "gate_fail_3", "spec_wrong", "permission_denied", "no_eligible_row",
    ])
    for command in ("relay", "relay-orchestrator"):
      with self.subTest(command=command):
        self.assertEqual(len(self.payload(command)["stop"]), 4)

  def test_Conformance_RoleFallback_IsInBothTextAndJson(self) -> None:
    for label, text in self.texts():
      with self.subTest(label=label):
        item = dict(zip(OBLIGATIONS, bullets(section(text, "Conformance"))))["role_fallback"]
        for phrase in (
          "ends in a vendor limit (a rate limit or out of credits) or an error exit of the "
          "agent process",
          "record the failed step with its reason",
          "as a new step on a fresh agent on the role's next matching row",
          "later rows on the same vendor skipped on out of credits",
          "independence kept as Stop says",
          "name the switch on the run report's deviations line",
          "A gate FAIL, a wrong spec or a permission denial is not an error exit.",
          "The same fallback applies to an orchestrator, as Stop says.",
          "With no eligible row left, or on any other failure, stop as Stop says.",
        ):
          self.assertIn(phrase, item)
    for command in ("relay", "relay-orchestrator"):
      with self.subTest(command=command):
        data = {i["key"]: i["text"] for i in self.payload(command)["conformance"]}
        self.assertIn("fresh agent on the role's next matching row", data["role_fallback"])
        self.assertNotIn("vendor_limit", data)
        self.assertIn("or on any other failure, stop as Stop says.", data["role_fallback"])

  def test_OrchestratorRules_VendorLimit_NamesTheSwitchOnTheDeviationsLine(self) -> None:
    text = flat(ai.orchestrator_text("done"))
    self.assertIn(
      "A role whose round ends in a vendor limit or an error exit: record its step failed with "
      "`--reason`, start the same round as a new step on a fresh agent on the next matching row "
      "and put `role switched: ROLE FROM to TO (REASON)` on the deviations line.",
      text,
    )

  def test_Changes_Version10_MentionsTheFallbackAndKeepsTheVersion(self) -> None:
    text = dict(ai.RELAY_CHANGES)[10]
    self.assertIn("vendor limit", text)
    self.assertIn("next matching row", text)
    self.assertIn("fresh agent", text)
    self.assertIn("outer session does the same for an orchestrator", text)


if __name__ == "__main__":
  unittest.main()
