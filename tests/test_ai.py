"""Keep agent onboarding usable before any project state can be trusted."""

from __future__ import annotations

import io
import json
import re
import shlex
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import support
from slicer import ai, cli


class AiInstructionsTests(unittest.TestCase):
  def test_Instructions_UseOneCallSliceReadPath_AndPreserveReadBeforeStart(self) -> None:
    expected = "slicer next --ready --section \"Implement\" --section \"Check\" --json --lean"
    self.assertIn(expected, ai.LOOP)
    self.assertIn("--section", ai.LOOP)
    self.assertIn("The headings are examples", ai.LOOP)
    self.assertIn("pass the section names the project configures", ai.LOOP)
    self.assertIn("Read its scope, dependencies, and acceptance checks", ai.LOOP)
    self.assertIn("before changing its status", ai.LOOP)
    self.assertIn("specification is\n   trusted and needs no clarification", ai.LOOP)
    self.assertLess(ai.LOOP.index("Read its scope"), ai.LOOP.index("slicer start ID"))
    self.assertNotIn("slicer next --json`, then `slicer show", ai.LOOP)

    reference = (Path(__file__).resolve().parents[1] / "docs" / "agents.md")
    docs = reference.read_text(encoding="utf-8")
    self.assertIn(expected, docs)
    prompt = docs.split("### Implement one slice", 1)[1].split("```", 2)[1]
    self.assertIn(expected, prompt)
    self.assertIn("Read the scope, dependencies", prompt)
    self.assertIn("resolve the specification\nwith me first", prompt)
    self.assertLess(prompt.index("Read the scope"), prompt.index("slicer start ID"))
    self.assertIn("Once the specification is clear and trusted", prompt)

    reference_flow = docs.split("**`slicer next` is the queue.**", 1)[1].split(
      "**Read only the implementation sections.**", 1
    )[0]
    self.assertLess(reference_flow.index("resolve any\nambiguity"),
                    reference_flow.index("slicer start ID"))
    self.assertIn("If the\nspecification is already trusted", reference_flow)

  def test_Instructions_LeaveRenderUnread_AndDoNotLinkTheLongReference(self) -> None:
    text = ai.INSTRUCTIONS
    self.assertNotIn("docs/agents.md", text)
    self.assertIn("Do not open or hand-merge `.slicer/render/`", text)
    self.assertIn("run `slicer render` then `slicer check`", text)
    self.assertIn("Do not invent the section body.", text)
    self.assertIn("unspecified", text)
    self.assertIn("--render --strict", text)
    self.assertNotIn("--require-render", text)
    agents = (Path(__file__).resolve().parents[1] / "docs" / "agents.md").read_text(encoding="utf-8")
    self.assertLess(len(ai.skill_text()), len(agents) // 2)

  def test_Instructions_OutsideProject_TextAndJsonHaveIdenticalContent(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      code, text, err = repo.run("ai", "instructions")
      self.assertEqual((code, err), (0, ""))
      code, out, err = repo.run("ai", "instructions", "--json")
      self.assertEqual((code, err), (0, ""))
      self.assertEqual(json.loads(out), {"instructions": text})
      self.assertEqual(text, ai.INSTRUCTIONS)
      self.assertTrue(text.startswith("# Getting started with slicer\n"))
      self.assertEqual(list(repo.root.iterdir()), [])

  def test_Instructions_RootAtEveryLevel_NeverDiscoversLoadsOrLocks(self) -> None:
    with support.TempRepo() as repo:
      root = str(repo.root / "does-not-exist")
      cases = (
        ("--root", root, "ai", "instructions"),
        ("ai", "--root", root, "instructions"),
        ("ai", "instructions", "--root", root),
        ("ai", "instructions"),
      )
      with patch("slicer.cli.store.discover", side_effect=AssertionError("discovery")), \
           patch("slicer.cli.store.load", side_effect=AssertionError("load")), \
           patch("slicer.cli.store.project_lock", side_effect=AssertionError("lock")):
        for argv in cases:
          with self.subTest(argv=argv), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main([*argv, "--json"]), 0)
            self.assertEqual(json.loads(out.getvalue())["instructions"], ai.INSTRUCTIONS)
      self.assertEqual(list(repo.root.iterdir()), [])

  def test_Instructions_ValidAndCorruptState_LeaveAllFilesUnchanged(self) -> None:
    with support.TempRepo() as repo:
      self.assertEqual(repo.run("init")[0], 0)
      for corrupt in (False, True):
        if corrupt:
          repo.write(".slicer/config.json", "not JSON")
          repo.write(".slicer/index.json", "also not JSON")
        before = {p.relative_to(repo.root): p.read_bytes()
                  for p in repo.root.rglob("*") if p.is_file()}
        for flags in ((), ("--json",)):
          with self.subTest(corrupt=corrupt, flags=flags):
            code, _, err = repo.run("ai", "instructions", *flags)
            self.assertEqual((code, err), (0, ""))
        after = {p.relative_to(repo.root): p.read_bytes()
                 for p in repo.root.rglob("*") if p.is_file()}
        self.assertEqual(after, before)

  def test_Instructions_HelpAtEveryLevel_IsDiscoverableAndHumanReadable(self) -> None:
    for argv, expected in (
      (("--help",), "onboarding instructions for coding agents"),
      (("ai", "--help"), "instructions"),
      (("ai", "instructions", "--json", "--help"), "--root is accepted but unused"),
    ):
      with self.subTest(argv=argv), redirect_stdout(io.StringIO()) as out, \
           redirect_stderr(io.StringIO()) as err:
        with self.assertRaises(SystemExit) as caught:
          cli.main(list(argv))
        self.assertEqual(caught.exception.code, 0)
        self.assertIn(expected, " ".join(out.getvalue().split()))
        self.assertTrue(out.getvalue().startswith("usage: slicer"))
        self.assertEqual(err.getvalue(), "")

  def test_Instructions_InvalidSyntax_ReturnsAiUsageEnvelope(self) -> None:
    with support.TempRepo() as repo:
      for argv in (
        ("ai", "--json"),
        ("ai", "unknown", "--json"),
        ("ai", "instructions", "--unknown", "--json"),
        ("ai", "instructions", "--render", "--json"),
        ("ai", "--json", "instructions"),
      ):
        with self.subTest(argv=argv):
          code, out, err = repo.run(*argv)
          self.assertEqual(code, 2)
          error = json.loads(out)["error"]
          self.assertEqual(error["code"], "usage")
          self.assertEqual(error["command"], "ai")
          self.assertIn("usage: slicer", err)
      code, out, err = repo.run("ai")
      self.assertEqual((code, out), (2, ""))
      self.assertIn("usage: slicer", err)

  def test_Skill_MatchesTheInstructionsLoop_AndTheCommittedFile(self) -> None:
    text = ai.skill_text()
    self.assertIn(ai.LOOP, text)
    self.assertIn(ai.EXITS, text)
    self.assertIn(ai.LOOP, ai.INSTRUCTIONS)
    self.assertIn(ai.EXITS, ai.INSTRUCTIONS)
    for command in (
      "slicer next --ready --section \"Implement\" --section \"Check\" --json --lean",
      "slicer start ID --render --strict --json",
      "slicer edit ID --section NAME --text \"Body\" --render --strict --json",
      "slicer check --json",
      "slicer done ID --note \"Describe the verified outcome\" --render --json",
    ):
      self.assertIn(command, text)
    self.assertNotIn("--require-render", text)
    self.assertNotIn("docs/agents.md", text)
    self.assertNotIn(".slicer/render/", text)
    for code in ("Exit 0", "exit 1", "exit 2", "exit 3"):
      self.assertIn(code, text)
    self.assertNotIn("import --skeleton", text)
    self.assertNotIn("slicer goals", text)
    path = Path(__file__).resolve().parents[1] / "skills" / "slicer" / "SKILL.md"
    self.assertEqual(path.read_text(encoding="utf-8"), text)

  def test_Skill_OutsideProject_TextAndJsonMatch(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      code, text, err = repo.run("ai", "skill")
      self.assertEqual((code, err), (0, ""))
      self.assertEqual(text, ai.skill_text())
      code, out, err = repo.run("ai", "skill", "--json")
      self.assertEqual((code, err), (0, ""))
      self.assertEqual(json.loads(out), {"skill": text})
      self.assertEqual(list(repo.root.iterdir()), [])

  def test_Skill_RootFlag_DoesNotDiscover(self) -> None:
    with support.TempRepo() as repo:
      root = str(repo.root / "does-not-exist")
      with patch("slicer.cli.store.discover", side_effect=AssertionError("discovery")), \
           patch("slicer.cli.store.load", side_effect=AssertionError("load")), \
           patch("slicer.cli.store.project_lock", side_effect=AssertionError("lock")):
        with redirect_stdout(io.StringIO()) as out:
          self.assertEqual(cli.main(["ai", "skill", "--root", root, "--json"]), 0)
        self.assertEqual(json.loads(out.getvalue())["skill"], ai.skill_text())

  def test_Instructions_TargetedRead_DocumentsRepeatedSectionAndContext(self) -> None:
    text = ai.INSTRUCTIONS
    example = 'slicer show ID --section "Implement" --section "Check" --context --json'
    self.assertIn(example, text)
    self.assertGreaterEqual(example.count("--section"), 2)
    self.assertIn("--context", example)
    start = text.index(example)
    window = text[max(0, start - 240): start + len(example) + 240]
    self.assertIn("examples", window)
    self.assertIn("the project configures", window)
    self.assertNotIn("every project", window.lower())
    self.assertNotIn("all projects", window.lower())

  def test_Instructions_CommandExamples_AreAcceptedByTheParser(self) -> None:
    commands = re.findall(r"`(slicer [^`]+)`", ai.INSTRUCTIONS)
    self.assertTrue(commands)
    parser = cli.build_parser()
    for command in commands:
      with self.subTest(command=command):
        parser.parse_args(shlex.split(command)[1:])

  def test_AgentsGuide_UsesTheBoundedPickup_NotNextThenShow(self) -> None:
    text = (Path(__file__).resolve().parents[1] / "AGENTS.md").read_text(encoding="utf-8")
    section = text.split("## Working on the roadmap", 1)[1].split("## House style", 1)[0]
    pickup = section.split("To pick up existing work", 1)[1].split("```", 2)[1]
    self.assertIn(
      'slicer next --ready --section "Implement" --section "Check" --json --lean',
      pickup,
    )
    self.assertGreaterEqual(pickup.count("--section"), 2)
    self.assertNotIn("slicer show", pickup)
    self.assertIn("slicer start <ID> --render --strict", section)
    self.assertIn("slicer edit S07 --section Why --file note.md --render --strict", section)
    done = 'slicer done <ID> --note "Describe the verified outcome" --render'
    self.assertIn(done, section)
    self.assertNotIn(done + " --strict", section)

  def test_Instructions_ImplementSection_LeavesTheCommandCatalogueToHelp(self) -> None:
    # S138: the loop keeps the reads it needs; the rest is `--help`'s job.
    section = ai.INSTRUCTIONS.split("## Implement one slice", 1)[1].split("\n## ", 1)[0]
    self.assertLess(len(section.split()), 350)
    for catalogue in ("slicer list --sort score", "slicer stats --json", "slicer log --json"):
      self.assertNotIn(catalogue, section)
    self.assertIn("slicer list --json", section)  # still needed to see claims
    self.assertIn("--help", section)

  def test_Instructions_Planning_AsksForScoresWithReasons(self) -> None:
    # S139: filed items arrive scored, so the queue sorts immediately.
    section = ai.INSTRUCTIONS.split("## Plan and record agreed work", 1)[1].split("## ", 1)[0]
    for flag in ("--importance", "--urgency", "--effort"):
      self.assertIn(flag, section)
    self.assertIn("reason", section)
    agents = (Path(__file__).resolve().parents[1] / "docs" / "agents.md").read_text(encoding="utf-8")
    convert = agents.split("### Convert an agreed roadmap", 1)[1].split("```", 2)[1]
    for field in ("importance", "urgency", "effort"):
      self.assertIn(field, convert)
