"""A project pins the relay contract version and slicer refuses to serve another (s387).

The pin is `relay_contract` in `.slicer/config.json`. `ai relay`, `ai relay-orchestrator`
and `ai relay-brief` refuse a mismatch with `contract_mismatch`, `check` warns, and both
relay JSONs report the pin as `pinned` right after `contract`.
"""

from __future__ import annotations

import json
import unittest

import support
from slicer import ai

RELAY_KEYS = [
  "contract", "pinned", "relay", "setup", "order", "land", "report", "outcomes", "stop",
  "changes", "roles", "gate", "finding_kinds", "conformance",
]
ORCHESTRATOR_KEYS = [
  "contract", "pinned", "orchestrator", "order", "report", "outcomes", "stop", "changes",
  "gate", "finding_kinds", "conformance",
]


def pin(repo: support.TempRepo, value: object) -> None:
  """Write the key straight into config.json, so these tests do not lean on `config`."""
  path = repo.root / ".slicer/config.json"
  raw = json.loads(path.read_text(encoding="utf-8"))
  raw["relay_contract"] = value
  path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class RelayPinCase(unittest.TestCase):
  def project(self) -> support.TempRepo:
    repo = support.TempRepo()
    self.addCleanup(repo.close)
    for args in (
      ("init",), ("relay-roles", "init"),
      ("add", "Build the thing", "--id", "S01", "--size", "M", "--effort", "2"),
      ("promote", "S01"),
      ("edit", "S01", "--section", "Implement", "--text", "Build it"),
      ("edit", "S01", "--section", "Check", "--text", "Test it"),
    ):
      code, out, err = repo.run(*args)
      self.assertEqual(code, 0, out + err)
    return repo

  def serve(self, repo: support.TempRepo, command: str, *extra: str) -> tuple[int, str, str]:
    if command == "relay-brief":
      return repo.run(
        "ai", "relay-brief", "S01", "--worktree", str(repo.root), "--branch", "feature/pin",
        "--run", "run-pin", "--step", "1", "--well-specified", "yes", "--split", "no", *extra,
      )
    return repo.run("ai", command, *extra)


class PinServedTests(RelayPinCase):
  def test_AiRelay_NoPin_PinnedNullAndTextUnchanged(self) -> None:
    repo = self.project()
    for command, keys in (("relay", RELAY_KEYS), ("relay-orchestrator", ORCHESTRATOR_KEYS)):
      with self.subTest(command=command):
        code, out, err = self.serve(repo, command, "--json")
        self.assertEqual((code, err), (0, ""), out)
        payload = json.loads(out)
        self.assertEqual(list(payload), keys)
        self.assertIsNone(payload["pinned"])
        lean = json.loads(self.serve(repo, command, "--json", "--lean")[1])
        self.assertEqual(lean, payload)
        code, text, err = self.serve(repo, command)
        self.assertEqual((code, err), (0, ""))
        body = payload["relay" if command == "relay" else "orchestrator"]
        self.assertEqual(text, body.rstrip("\n") + "\n")
        self.assertTrue(text.startswith(f"Contract: {ai.contract_line()}\n"))
    self.assertNotIn("relay_contract", json.loads((repo.root / ".slicer/config.json").read_text()))

  def test_AiRelay_PinEqual_ServesAndReportsPinned(self) -> None:
    repo = self.project()
    plain = {c: self.serve(repo, c)[1] for c in ("relay", "relay-orchestrator", "relay-brief")}
    pin(repo, ai.RELAY_CONTRACT)
    for command in ("relay", "relay-orchestrator", "relay-brief"):
      with self.subTest(command=command):
        code, out, err = self.serve(repo, command)
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(out, plain[command])
    for command, keys in (("relay", RELAY_KEYS), ("relay-orchestrator", ORCHESTRATOR_KEYS)):
      for extra in ((), ("--lean",)):
        with self.subTest(command=command, extra=extra):
          code, out, err = self.serve(repo, command, "--json", *extra)
          self.assertEqual((code, err), (0, ""), out)
          payload = json.loads(out)
          self.assertEqual(list(payload), keys)
          self.assertEqual(payload["pinned"], ai.RELAY_CONTRACT)
          self.assertIs(type(payload["pinned"]), int)

  def test_AiRelayInitRoles_PinDiffers_StillWrites(self) -> None:
    with support.TempRepo() as repo:
      self.assertEqual(repo.run("init")[0], 0)
      pin(repo, ai.RELAY_CONTRACT - 1)
      code, out, err = repo.run("ai", "relay", "--init-roles", "--json")
      self.assertEqual(code, 0, out + err)
      self.assertTrue((repo.root / ".slicer/relay-roles.json").is_file())

  def test_AiRelay_OutsideProject_NothingIsPinned(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      for command in ("relay", "relay-orchestrator"):
        with self.subTest(command=command):
          code, out, err = repo.run("ai", command, "--json")
          self.assertEqual(code, 0, err)
          self.assertIsNone(json.loads(out)["pinned"])


class PinMismatchTests(RelayPinCase):
  def assert_refused(self, repo: support.TempRepo, command: str, pinned: int) -> None:
    code, out, err = self.serve(repo, command)
    self.assertEqual(code, 2, (out, err))
    self.assertEqual(out, "")
    self.assertNotIn("Contract: relay", err)
    message = self.message(err)
    self.assertIn(f"relay v{pinned}", message)
    self.assertIn(f"relay v{ai.RELAY_CONTRACT}", message)
    self.assertIn(f"slicer config relay_contract {ai.RELAY_CONTRACT}", message)
    self.assertRegex(message, r"(?i)install a slicer")
    code, out, err = self.serve(repo, command, "--json")
    self.assertEqual(code, 2, (out, err))
    error = json.loads(out)
    self.assertEqual(list(error), ["error"])
    self.assertEqual(error["error"]["code"], "contract_mismatch")
    self.assertEqual(error["error"]["message"], message)
    self.assertNotIn("Contract: relay", out.replace(message, ""))

  @staticmethod
  def message(err: str) -> str:
    return err.strip().removeprefix("slicer: ")

  def test_AiRelay_PinDiffers_RefusesWithContractMismatch(self) -> None:
    repo = self.project()
    pin(repo, ai.RELAY_CONTRACT - 1)
    for command in ("relay", "relay-orchestrator", "relay-brief"):
      with self.subTest(command=command):
        self.assert_refused(repo, command, ai.RELAY_CONTRACT - 1)

  def test_AiRelay_PinNewerThanRunning_RefusesToo(self) -> None:
    repo = self.project()
    pin(repo, ai.RELAY_CONTRACT + 1)
    for command in ("relay", "relay-orchestrator", "relay-brief"):
      with self.subTest(command=command):
        self.assert_refused(repo, command, ai.RELAY_CONTRACT + 1)

  def test_AiRelay_PinDiffers_WritesNothing(self) -> None:
    repo = self.project()
    pin(repo, ai.RELAY_CONTRACT - 1)
    before = {p: p.read_bytes() for p in (repo.root / ".slicer").rglob("*") if p.is_file()}
    for command in ("relay", "relay-orchestrator", "relay-brief"):
      self.serve(repo, command)
    after = {p: p.read_bytes() for p in (repo.root / ".slicer").rglob("*") if p.is_file()}
    self.assertEqual(before, after)

  def test_AiRelayBrief_PinDiffersAndUnknownItem_RefusesWithContractMismatchFirst(self) -> None:
    repo = self.project()
    pin(repo, ai.RELAY_CONTRACT - 1)
    for extra in ((), ("--json",)):
      with self.subTest(extra=extra):
        code, out, err = repo.run(
          "ai", "relay-brief", "S99", "--worktree", str(repo.root), "--branch", "feature/pin",
          "--run", "run-pin", "--step", "1", "--well-specified", "yes", "--split", "no", *extra,
        )
        self.assertEqual(code, 2, (out, err))
        self.assertIn("pins relay", out + err)
        self.assertNotIn("S99", err)
        if extra:
          self.assertEqual(json.loads(out)["error"]["code"], "contract_mismatch")
    pin(repo, ai.RELAY_CONTRACT)
    code, out, err = repo.run(
      "ai", "relay-brief", "S99", "--worktree", str(repo.root), "--branch", "b",
      "--run", "r", "--step", "1", "--well-specified", "yes", "--split", "no", "--json",
    )
    self.assertEqual(json.loads(out)["error"]["code"], "no_such_item")

  def test_PinMismatch_Helper_ReturnsNoneForUnsetOrEqual(self) -> None:
    self.assertIsNone(ai.pin_mismatch(None))
    self.assertIsNone(ai.pin_mismatch(ai.RELAY_CONTRACT))
    message = ai.pin_mismatch(ai.RELAY_CONTRACT - 1)
    self.assertIsInstance(message, str)
    self.assertIn(f"v{ai.RELAY_CONTRACT - 1}", message)
    self.assertIn(f"v{ai.RELAY_CONTRACT}", message)


class PinCheckTests(RelayPinCase):
  def pin_warnings(self, repo: support.TempRepo) -> tuple[int, list[str]]:
    code, out, err = repo.run("check", "--json")
    self.assertEqual(code, 0, out + err)
    return code, [w for w in json.loads(out)["warnings"] if "pins relay" in w or "relay_contract" in w]

  def test_Check_PinDiffers_WarnsAndPasses(self) -> None:
    repo = self.project()
    repo.run("render")
    pin(repo, ai.RELAY_CONTRACT - 1)
    code, warnings = self.pin_warnings(repo)
    self.assertEqual(code, 0)
    self.assertEqual(len(warnings), 1, warnings)
    self.assertIn(f"v{ai.RELAY_CONTRACT - 1}", warnings[0])
    self.assertIn(f"v{ai.RELAY_CONTRACT}", warnings[0])
    code, out, err = repo.run("check")
    self.assertEqual(code, 0, out + err)
    self.assertIn("relay v", out + err)

  def test_Check_NoPinOrEqualPin_HasNoSuchWarning(self) -> None:
    repo = self.project()
    repo.run("render")
    self.assertEqual(self.pin_warnings(repo)[1], [])
    pin(repo, ai.RELAY_CONTRACT)
    self.assertEqual(self.pin_warnings(repo)[1], [])


def poke(repo: support.TempRepo, **changes: object) -> None:
  """Edit config.json directly, the way a person would by hand."""
  path = repo.root / ".slicer/config.json"
  raw = json.loads(path.read_text(encoding="utf-8"))
  raw.update(changes)
  path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class PinUnreadableTests(RelayPinCase):
  """A config that does not load must not unpin a project that wrote a pin."""

  BAD_PINS = ("7", 0, -1, 8.5, True, [7], {"v": 7})

  def error(self, out: str) -> dict:
    return json.loads(out)["error"]

  def test_AiRelay_PinFailsValidation_RefusesAsUnreadable(self) -> None:
    for bad in self.BAD_PINS:
      repo = self.project()
      pin(repo, bad)
      for command in ("relay", "relay-orchestrator", "relay-brief"):
        with self.subTest(pin=bad, command=command):
          code, out, err = self.serve(repo, command)
          self.assertEqual(code, 2, (out, err))
          self.assertEqual(out, "")
          self.assertNotIn("generic guide", err)
          message = err.strip().removeprefix("slicer: ")
          for phrase in ("relay_contract", "could not be read", "by hand", "config relay_contract --unset"):
            self.assertIn(phrase, message)
          code, out, err = self.serve(repo, command, "--json")
          self.assertEqual(code, 2, (out, err))
          self.assertEqual(self.error(out)["code"], "contract_mismatch")
          self.assertEqual(self.error(out)["message"], message)
          self.assertNotIn("Contract: relay", out)

  def test_AiRelay_BadConfigWithoutPin_StillServesGenericText(self) -> None:
    for extra in ({}, {"relay_contract": None}):
      repo = self.project()
      poke(repo, implement_finish="bogus", **extra)
      for command in ("relay", "relay-orchestrator"):
        with self.subTest(command=command, extra=extra):
          code, out, err = self.serve(repo, command, "--json")
          self.assertEqual(code, 0, (out, err))
          self.assertIn("printing the generic guide", err)
          payload = json.loads(out)
          self.assertIsNone(payload["pinned"])
          self.assertEqual(payload["contract"], ai.contract_line())
      code, out, err = self.serve(repo, "relay-brief", "--json")
      self.assertEqual(code, 3, (out, err))
      self.assertEqual(self.error(out)["code"], "config")

  def test_AiRelay_InvalidJsonConfig_KeepsTodaysBehaviour(self) -> None:
    repo = self.project()
    (repo.root / ".slicer/config.json").write_text('{"relay_contract": 7,', encoding="utf-8")
    code, out, err = self.serve(repo, "relay")
    self.assertEqual(code, 0, (out, err))
    self.assertIn("printing the generic guide", err)
    code, out, err = self.serve(repo, "relay-brief", "--json")
    self.assertEqual((code, self.error(out)["code"]), (3, "config"))

  def test_AiRelay_ValidPinBesideUnrelatedConfigError_StillComparesTheVersions(self) -> None:
    repo = self.project()
    poke(repo, implement_finish="bogus", relay_contract=ai.RELAY_CONTRACT - 1)
    for command in ("relay", "relay-orchestrator", "relay-brief"):
      with self.subTest(command=command):
        for extra in ((), ("--json",)):
          code, out, err = self.serve(repo, command, *extra)
          self.assertEqual(code, 2, (out, err))
          # The refusal stands alone: no "printing the generic guide" before it.
          self.assertNotIn("generic guide", err)
          self.assertEqual(err.count("slicer: "), 1, err)
          if extra:
            error = self.error(out)
            self.assertEqual(error["code"], "contract_mismatch")
            self.assertIn(f"pins relay v{ai.RELAY_CONTRACT - 1}", error["message"])
    poke(repo, relay_contract=ai.RELAY_CONTRACT)
    for command in ("relay", "relay-orchestrator"):
      with self.subTest(command=command, pin="equal"):
        code, out, err = self.serve(repo, command, "--json")
        self.assertEqual(code, 0, (out, err))
        self.assertIn("printing the generic guide", err)
        self.assertEqual(json.loads(out)["pinned"], ai.RELAY_CONTRACT)

  def test_AiRelayInitRoles_UnreadablePin_IsNotARefusalOfTheContract(self) -> None:
    repo = self.project()
    poke(repo, relay_contract="7")
    code, out, err = repo.run("ai", "relay", "--init-roles", "--force", "--json")
    self.assertNotEqual(self.error(out)["code"] if code else "", "contract_mismatch", (out, err))


if __name__ == "__main__":
  unittest.main()
