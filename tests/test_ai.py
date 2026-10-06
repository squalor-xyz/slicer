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
    self.assertIn("slicer sections", ai.LOOP)
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

  def test_ImplementPrompt_FollowsThePrintedFinish(self) -> None:
    docs = (Path(__file__).resolve().parents[1] / "docs" / "agents.md").read_text(encoding="utf-8")
    prompt = docs.split("### Implement one slice", 1)[1].split("```", 2)[1]
    for text in ("slicer ai instructions", "implement_finish", "slicer done ID", "slicer handoff ID"):
      self.assertIn(text, prompt)
    self.assertIn("run done only after review and merge", prompt)
    self.assertEqual(prompt.count("run done only after review and merge"), 1)
    self.assertGreater(
      prompt.index("run done only after review and merge"), prompt.index("slicer handoff ID")
    )
    self.assertLess(prompt.index("slicer done ID"), prompt.index("slicer handoff ID"))
    self.assertNotIn("--check", prompt)
    self.assertLess(prompt.index("Read the scope"), prompt.index("slicer start ID"))
    self.assertIn("resolve the specification\nwith me first", prompt)
    self.assertIn("Do not commit or\npublish unless separately authorized", prompt)

  def test_PickupParagraph_NamesTheDefaultFinishAndTheHandoffChoice(self) -> None:
    docs = (Path(__file__).resolve().parents[1] / "docs" / "agents.md").read_text(encoding="utf-8")
    pickup = docs.split("**`slicer next` is the queue.**", 1)[1].split(
      "**Read only the implementation sections.**", 1
    )[0]
    self.assertIn('`slicer done ID --note "..." --render` followed by `slicer check`', pickup)
    self.assertIn("the\ndefault finish", pickup)
    self.assertIn("`implement_finish` is `handoff`", pickup)
    self.assertIn("handoff command from\n`slicer ai instructions`", pickup)

  def test_GettingStarted_LabelsBothFinishesAndRendersTheLoopExamples(self) -> None:
    guide = (Path(__file__).resolve().parents[1] / "docs" / "getting-started.md").read_text(
      encoding="utf-8"
    )
    handoff = guide.split("$ slicer handoff S01 --render", 1)[0]
    self.assertIn("When `implement_finish` is `handoff`", handoff[-500:])
    loop = guide.split("## 6. The loop", 1)[1].split("\n## ", 1)[0]
    self.assertIn("`slicer ai instructions`", loop)
    self.assertIn("default finish, `done`", loop)
    self.assertIn("$ slicer start S01 --render", loop)
    self.assertIn('$ slicer done S01 --note "loader now refuses a missing key" --render', loop)

  def test_Tracking_RequiresTheCliForReadsAndEdits_InInstructionsAndSkill(self) -> None:
    self.assertIn(ai.TRACKING, ai.INSTRUCTIONS)
    self.assertIn(ai.TRACKING, ai.skill_text())
    text = ai.TRACKING
    self.assertIn("read and to change", text)
    for noun in ("goals", "items", "slices", "notes", "history", "roadmap prose"):
      self.assertIn(noun, text)
    self.assertIn("reviewing a roadmap", text)
    self.assertIn("planning work", text)
    for verb in ("open", "search", "parse", "edit"):
      self.assertIn(verb, text)
    self.assertIn("tracking JSON", text)
    self.assertIn("history file", text)
    self.assertIn("generated roadmap and slice output", text)
    for command in (
      "slicer goals --json",
      "slicer list --json",
      "slicer status --json",
      "slicer stats --json",
      "slicer show ID --json",
      "slicer next --ready --json",
      "slicer find topic --json",
      "slicer prose list --json",
      "slicer prose show goals --json",
      "slicer log --json",
      "slicer check --json",
    ):
      self.assertIn(command, text)
    for command in (
      "goals", "list", "status", "stats", "show ID", "next --ready", "find topic",
      "prose list", "prose show goals", "log",
    ):
      self.assertIn(f"slicer {command} --json --lean", text)
    self.assertNotIn("slicer check --json --lean", text)
    self.assertIn("Use --lean on reads", text)
    self.assertIn("propose a roadmap item", text)
    self.assertIn("Do not switch to the tracking files.", text)
    self.assertIn("Source code", text)
    self.assertIn("ordinary project documentation", text)
    self.assertIn("templates", text)
    self.assertIn("this skill", text)
    self.assertIn("explicitly asks you to inspect or", text)
    self.assertIn("repair tracking internals", text)
    self.assertIn("PYTHONPATH=src python3 -m slicer", text)
    self.assertNotIn("slicer handoff ID --render --json", text)
    self.assertNotIn("only after review and merge are complete", text)
    self.assertNotIn(".slicer/render/", text)
    handoff = ai.skill_text("handoff")
    self.assertIn("slicer handoff ID --render --json", handoff)
    self.assertIn("ready for review", handoff)
    self.assertIn("only after review and merge are complete", handoff)
    skill = ai.skill_text()
    self.assertIn("review a roadmap", skill)
    self.assertIn("plan work", skill)
    agents = (Path(__file__).resolve().parents[1] / "AGENTS.md").read_text(encoding="utf-8")
    self.assertIn("Read and change tracking state through the slicer CLI.", agents)
    reference = (Path(__file__).resolve().parents[1] / "docs" / "agents.md").read_text(encoding="utf-8")
    review = reference.split("### Review an existing project", 1)[1].split("```", 2)[1]
    self.assertIn("slicer goals", review)
    self.assertNotIn("existing roadmap", review)

  def test_Instructions_SayNextOffersOnlyOpenAndStartedWork(self) -> None:
    text = ai.INSTRUCTIONS
    start = text.index("Do not combine `--ready` and `--show`.")
    window = text[start:start + 600]
    self.assertIn("config.statuses", window)
    self.assertIn("custom status", window)
    self.assertIn("open work", window)
    self.assertIn("started work", window)
    self.assertIn("`draft`", window)
    self.assertIn("`blocked`", window)
    self.assertIn("not a flag", window)
    self.assertIn("reviewing", window)

  def test_Instructions_NamePerCallOwnerForSharedCheckouts(self) -> None:
    text = ai.INSTRUCTIONS
    self.assertIn("--owner NAME", text)
    self.assertIn("SLICER_CLAIM_OWNER", text)
    self.assertIn("slicer log --by NAME", text)

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
      self.assertEqual(code, 0)
      self.assertIn("printing the generic guide", err)
      code, out, err = repo.run("ai", "instructions", "--json")
      self.assertEqual(code, 0)
      self.assertIn("printing the generic guide", err)
      self.assertEqual(json.loads(out), {"instructions": text})
      self.assertEqual(text, ai.INSTRUCTIONS)
      self.assertTrue(text.startswith(ai.TRACKING_RULE + "\n"))
      self.assertLess(text.index(ai.TRACKING_RULE), text.index(ai.LOOP))
      self.assertEqual(list(repo.root.iterdir()), [])

  def test_Instructions_ReadsConfigAndIndex_WithoutLockingOrWriting(self) -> None:
    with support.TempRepo() as repo:
      self.assertEqual(repo.run("init")[0], 0)
      data = json.loads((repo.root / ".slicer/config.json").read_text())
      data["implement_finish"] = "handoff"
      (repo.root / ".slicer/config.json").write_text(json.dumps(data) + "\n")
      before = {p.relative_to(repo.root): p.read_bytes()
                for p in repo.root.rglob("*") if p.is_file()}
      with patch("slicer.cli.store.project_lock", side_effect=AssertionError("lock")), \
           patch("slicer.cli.store.load", side_effect=AssertionError("load")):
        code, text, err = repo.run("ai", "instructions")
      self.assertEqual((code, err), (0, ""))
      self.assertIn("slicer handoff ID --render --json", text)
      self.assertNotIn("slicer done", text.split("## Hand off for review", 1)[0].split("4. Run", 1)[1])
      after = {p.relative_to(repo.root): p.read_bytes()
               for p in repo.root.rglob("*") if p.is_file()}
      self.assertEqual(after, before)

  def test_Instructions_UnreadableIndex_WarnsAndStaysGeneric(self) -> None:
    with support.TempRepo() as repo:
      self.assertEqual(repo.run("init")[0], 0)
      repo.write(".slicer/index.json", "{")
      before = {p.relative_to(repo.root): p.read_bytes()
                for p in repo.root.rglob("*") if p.is_file()}
      code, text, err = repo.run("ai", "instructions")
      self.assertEqual(code, 0)
      self.assertEqual(text, ai.INSTRUCTIONS)
      self.assertIn("printing the generic guide", err)
      after = {p.relative_to(repo.root): p.read_bytes()
               for p in repo.root.rglob("*") if p.is_file()}
      self.assertEqual(after, before)

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
            code, text, err = repo.run("ai", "instructions", *flags)
            self.assertEqual(code, 0)
            body = json.loads(text)["instructions"] if flags else text
            self.assertEqual(body, ai.INSTRUCTIONS)
            if corrupt:
              self.assertIn("printing the generic guide", err)
            else:
              self.assertEqual(err, "")
        after = {p.relative_to(repo.root): p.read_bytes()
                 for p in repo.root.rglob("*") if p.is_file()}
        self.assertEqual(after, before)

  def test_Instructions_HelpAtEveryLevel_IsDiscoverableAndHumanReadable(self) -> None:
    for argv, expected in (
      (("--help",), "onboarding instructions for coding agents"),
      (("ai", "--help"), "instructions"),
      (("ai", "--help"), "skill"),
      (("ai", "instructions", "--json", "--help"), "warns on stderr"),
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
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      for argv in (
        ("ai", "unknown", "--json"),
        ("ai", "instructions", "--unknown", "--json"),
        ("ai", "instructions", "--render", "--json"),
      ):
        with self.subTest(argv=argv):
          code, out, err = repo.run(*argv)
          self.assertEqual(code, 2)
          error = json.loads(out)["error"]
          self.assertEqual(error["code"], "usage")
          self.assertEqual(error["command"], "ai")
          self.assertIn("usage: slicer", err)

  def test_Ai_BareCommand_MatchesInstructions_IncludingJsonBeforeTheSubcommand(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      for flags in ((), ("--json",), ("--json", "--lean")):
        with self.subTest(flags=flags):
          bare = repo.run("ai", *flags)
          named = repo.run("ai", "instructions", *flags)
          self.assertEqual(bare[0], 0)
          self.assertEqual(bare, named)
      before = repo.run("ai", "--json", "instructions")
      after = repo.run("ai", "instructions", "--json")
      self.assertEqual(before[0], 0)
      self.assertEqual(before, after)
      skill = repo.run("ai", "skill", "--json")
      skill_first = repo.run("ai", "--json", "skill")
      self.assertEqual(skill[0], 0)
      self.assertEqual(skill_first, skill)
      self.assertNotEqual(skill[1], repo.run("ai", "--json")[1])

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
      "slicer done ID --note \"Describe the verified outcome\" --render --check --json",
    ):
      self.assertIn(command, text)
    self.assertNotIn("--require-render", text)
    self.assertNotIn("docs/agents.md", text)
    self.assertNotIn(".slicer/render/", text)
    for code in ("Exit 0", "exit 1", "exit 2", "exit 3"):
      self.assertIn(code, text)
    self.assertNotIn("import --skeleton", text)
    path = Path(__file__).resolve().parents[1] / "skills" / "slicer" / "SKILL.md"
    self.assertEqual(path.read_text(encoding="utf-8"), text)

  def test_Skill_OutsideProject_TextAndJsonMatch(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      code, text, err = repo.run("ai", "skill")
      self.assertEqual(code, 0)
      self.assertIn("printing the generic guide", err)
      self.assertEqual(text, ai.skill_text())
      self.assertTrue(text.split("---\n", 2)[2].lstrip().startswith(ai.TRACKING_RULE + "\n"))
      code, out, err = repo.run("ai", "skill", "--json")
      self.assertEqual(code, 0)
      self.assertIn("printing the generic guide", err)
      self.assertEqual(json.loads(out), {"skill": text})
      self.assertEqual(list(repo.root.iterdir()), [])

  def test_FinishText_FollowsImplementFinish_InSkillAndInstructions(self) -> None:
    done_skill = ai.skill_text("done")
    done_guide = ai.instructions_text("done")
    done_command = (
      'slicer done ID --note "Describe the verified outcome" --render --check --json'
    )
    for text in (done_skill, done_guide):
      self.assertIn("Step 4 is the finish.", text)
      self.assertNotIn("only after review and merge are complete", text)
      self.assertIn(done_command, text)
      self.assertIn("slicer handoff ID --render --check --json", text)

    handoff_skill = ai.skill_text("handoff")
    self.assertIn("slicer handoff ID --render --json", handoff_skill)
    self.assertIn("only after review and merge are complete", handoff_skill)
    self.assertNotIn("slicer done", handoff_skill)
    step = handoff_skill.split("4. Run", 1)[1].split("If `next` JSON", 1)[0]
    self.assertIn("slicer handoff ID --render --check --json", step)

    done_section = done_guide.split("## Hand off for review", 1)[1].split("\n## ", 1)[0]
    self.assertIn(ai.HANDOFF_APPLIES, done_section)
    self.assertIn("When step 4 is done, that command is the finish.", done_section)

    handoff_guide = ai.instructions_text("handoff")
    handoff_section = handoff_guide.split("## Hand off for review", 1)[1].split("\n## ", 1)[0]
    self.assertIn(ai.HANDOFF_REVIEW_SENTENCE, handoff_section)

    rest = ai.rest_text()
    self.assertIn("## Hand off for review", rest)
    self.assertIn(ai.HANDOFF_APPLIES, rest)
    self.assertNotIn(ai.DONE_CLOSER, rest)
    self.assertNotIn(ai.HANDOFF_REVIEW_SENTENCE, rest)
    with support.TempRepo() as repo:
      self.assertEqual(repo.run("init")[0], 0)
      data = json.loads((repo.root / ".slicer/config.json").read_text())
      data["implement_finish"] = "handoff"
      (repo.root / ".slicer/config.json").write_text(json.dumps(data) + "\n")
      code, text, err = repo.run("ai", "instructions", "--rest")
      self.assertEqual((code, err), (0, ""))
      self.assertEqual(text, rest)

  def test_Skill_HandoffProject_DropsTheDoneCommand_AndKeepsTheReviewStep(self) -> None:
    with support.TempRepo() as repo:
      self.assertEqual(repo.run("init")[0], 0)
      data = json.loads((repo.root / ".slicer/config.json").read_text())
      del data["implement_finish"]
      (repo.root / ".slicer/config.json").write_text(json.dumps(data) + "\n")
      code, text, err = repo.run("ai", "skill")
      self.assertEqual((code, err), (0, ""))
      self.assertEqual(text, ai.skill_text())
      self.assertIn("slicer done ID", text)
      data["implement_finish"] = "handoff"
      (repo.root / ".slicer/config.json").write_text(json.dumps(data) + "\n")
      code, text, err = repo.run("ai", "skill")
      self.assertEqual((code, err), (0, ""))
      self.assertEqual(text, ai.skill_text("handoff"))
      step = text.split("4. Run", 1)[1].split("If `next` JSON", 1)[0]
      self.assertIn("slicer handoff ID --render --check --json", step)
      self.assertNotIn("done", step)
      self.assertNotIn("slicer done", text)
      self.assertIn("only after review and merge are complete", text)

  def test_Ai_RequiredReport_ExposesKindAndCurrentAttemptInstructions(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      path = repo.root / ".slicer/config.json"
      data = json.loads(path.read_text())
      data["handoff_requires_note_kind"] = "implementation report"
      path.write_text(json.dumps(data))
      before = {p: p.read_bytes() for p in (repo.root / ".slicer").rglob("*") if p.is_file()}
      for finish in ("done", "handoff"):
        data["implement_finish"] = finish
        path.write_text(json.dumps(data))
        before[path] = path.read_bytes()
        for command in ("instructions", "skill"):
          code, out, err = repo.run("ai", command, "--json")
          self.assertEqual((code, err), (0, ""))
          text = json.loads(out)[command]
          self.assertIn("handoff_requires_note_kind to 'implementation report'", text)
          self.assertIn("slicer note ID --kind 'implementation report' --text", text)
          self.assertIn("current implementation attempt", text)
          self.assertIn("Changing attempts manually", text)
          if finish == "handoff" and command == "skill":
            self.assertNotIn("slicer done", text)
        self.assertEqual({p: p.read_bytes() for p in (repo.root / ".slicer").rglob("*") if p.is_file()}, before)
      self.assertEqual(repo.run("ai", "instructions", "--rest")[1], ai.rest_text())

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
    commands = re.findall(r"`(slicer [^`]+)`", ai.INSTRUCTIONS + ai.skill_text())
    self.assertTrue(commands)
    parser = cli.build_parser()
    for command in commands:
      with self.subTest(command=command):
        parser.parse_args(shlex.split(command)[1:])

  def test_Instructions_Rest_LeavesOutEverySkillBlock(self) -> None:
    text = ai.rest_text()
    for block in (ai.TRACKING_RULE, ai.TRACKING, ai.LOOP, ai.SPEC_GAP, ai.EXITS, ai.HANDOFF_STEP):
      self.assertNotIn(block, text)
    for heading in (
      "## Read the project first",
      "## Plan and record agreed work",
      "## Hand off for review",
      "## State and command results",
    ):
      self.assertIn(heading, text)
    self.assertIn("Do not combine `--ready` and `--show`.", text)

  def test_Instructions_Rest_IsSmallerThanHalfOfTheFullText(self) -> None:
    self.assertLess(len(ai.rest_text()), len(ai.INSTRUCTIONS) * 0.6)

  def test_Instructions_Full_IsUnchangedByTheSplit(self) -> None:
    expected = (
      f"{ai.TRACKING_RULE}\n\n"
      + ai.INTRO + ai.PLAN + ai.TRACKING + "\n"
      + "## Implement one slice\n\n" + ai.LOOP + ai.SPEC_GAP + "\n" + ai.IMPLEMENT_MORE
      + ai.HANDOFF_SECTION + ai.STATE + ai.EXITS
      + "\n" + ai.DONE_CLOSER
    )
    self.assertEqual(ai.INSTRUCTIONS, expected)
    self.assertIn(ai.loop_text("handoff"), ai.instructions_text("handoff"))

  def test_Instructions_RestFlag_TextAndJson(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      code, text, err = repo.run("ai", "instructions", "--rest")
      self.assertEqual(code, 0)
      self.assertEqual(err, "")
      self.assertEqual(text, ai.rest_text())
      code, out, _err = repo.run("ai", "instructions", "--rest", "--json")
      self.assertEqual(code, 0)
      self.assertEqual(json.loads(out), {"instructions": text})
      for argv in (("ai", "--rest"), ("ai", "--rest", "instructions")):
        with self.subTest(argv=argv):
          code, again, _err = repo.run(*argv)
          self.assertEqual(code, 0)
          self.assertEqual(again, text)

  def test_Skill_LastLine_PointsAtRest(self) -> None:
    last = [line for line in ai.skill_text().splitlines() if line.strip()][-1]
    self.assertEqual(
      last,
      "For planning, filing, claims, and review, run `slicer ai instructions --rest`; "
      "it leaves out what this skill already says.",
    )

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
    done = 'slicer done <ID> --note "Describe the verified outcome" --render --check'
    self.assertIn(done, section)
    self.assertNotIn(done + " --strict", section)

  def test_AgentsGuide_NamesSuiteDurationAndBackgroundRun(self) -> None:
    text = (Path(__file__).resolve().parents[1] / "AGENTS.md").read_text(encoding="utf-8")
    section = text.split("## Commands", 1)[1].split("\n## ", 1)[0]
    self.assertIn("python3 tests/affected.py --run", section)
    self.assertIn("background", section)
    self.assertIn("about one minute", section)
    self.assertIn("longer under load", section)
    self.assertIn("timeout over 180 s", section)
    self.assertIn("full discover once before done", section)

  def test_AgentsGuide_RoadmapSection_IsShorter(self) -> None:
    text = (Path(__file__).resolve().parents[1] / "AGENTS.md").read_text(encoding="utf-8")
    section = text.split("## Working on the roadmap", 1)[1].split("## House style", 1)[0]
    self.assertLess(len(section.encode("utf-8")), 3800)

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


INSTALL_PATHS = (
  ".claude/skills/slicer/SKILL.md",
  ".agents/skills/slicer/SKILL.md",
  ".grok/skills/slicer/SKILL.md",
)


class AiSkillWriteTests(unittest.TestCase):
  """`ai skill --output` and `--install` write the bytes the command prints."""

  def project(self) -> support.TempRepo:
    repo = support.TempRepo()
    self.addCleanup(repo.close)
    self.assertEqual(repo.run("init")[0], 0)
    return repo

  def files(self, repo: support.TempRepo) -> dict[str, bytes]:
    return {
      str(p.relative_to(repo.root)): p.read_bytes()
      for p in repo.root.rglob("*") if p.is_file() and ".slicer" not in p.parts
    }

  def test_SkillOutput_WritesThePrintedBytesAndCreatesParents(self) -> None:
    repo = self.project()
    printed = repo.run("ai", "skill")[1]
    code, out, err = repo.run("ai", "skill", "--output", "deep/er/SKILL.md")
    self.assertEqual((code, err), (0, ""))
    self.assertEqual((repo.root / "deep/er/SKILL.md").read_bytes(), printed.encode("utf-8"))
    self.assertNotIn("name: slicer", out)
    self.assertIn(str(repo.root / "deep/er/SKILL.md"), out)
    self.assertEqual(list(self.files(repo)), ["deep/er/SKILL.md"])

  def test_SkillInstall_WritesTheThreeProjectPathsOnly(self) -> None:
    repo = self.project()
    printed = repo.run("ai", "skill")[1].encode("utf-8")
    code, out, err = repo.run("ai", "skill", "--install")
    self.assertEqual((code, err), (0, ""))
    self.assertEqual(self.files(repo), {path: printed for path in INSTALL_PATHS})
    for path in INSTALL_PATHS:
      self.assertIn(f"wrote {repo.root / path}", out)
      self.assertFalse((repo.root / path).is_symlink())
    self.assertFalse((repo.root / ".codex").exists())

  def test_SkillInstall_HandoffProject_InstallsTheHandoffText(self) -> None:
    repo = self.project()
    path = repo.root / ".slicer/config.json"
    data = json.loads(path.read_text())
    data["implement_finish"] = "handoff"
    path.write_text(json.dumps(data) + "\n")
    self.assertEqual(repo.run("ai", "skill", "--install")[0], 0)
    for dest in INSTALL_PATHS:
      self.assertEqual(repo.read(dest), ai.skill_text("handoff"))
      self.assertNotIn("slicer done", repo.read(dest))

  def test_SkillInstall_EqualDestination_IsRewrittenCleanly(self) -> None:
    repo = self.project()
    self.assertEqual(repo.run("ai", "skill", "--install")[0], 0)
    before = self.files(repo)
    code, out, err = repo.run("ai", "skill", "--install")
    self.assertEqual((code, err), (0, ""))
    self.assertEqual(self.files(repo), before)
    self.assertEqual(len(out.splitlines()), 3)

  def test_SkillInstall_DifferentContents_RefuseAndWriteNothing(self) -> None:
    repo = self.project()
    repo.write(INSTALL_PATHS[1], "my own skill\n")
    before = self.files(repo)
    for argv in (("--install",), ("--install", "--output", "also.md")):
      with self.subTest(argv=argv):
        code, out, err = repo.run("ai", "skill", *argv, "--json")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"]["code"], "state")
        self.assertIn(INSTALL_PATHS[1], err)
        self.assertEqual(self.files(repo), before)

  def test_SkillInstall_Force_OverwritesDifferentContents(self) -> None:
    repo = self.project()
    repo.write(INSTALL_PATHS[1], "my own skill\n")
    code, out, err = repo.run("ai", "skill", "--install", "--force")
    self.assertEqual((code, err), (0, ""))
    for dest in INSTALL_PATHS:
      self.assertEqual(repo.read(dest), ai.skill_text())

  def test_SkillInstall_DirectoryDestination_IsRefused(self) -> None:
    repo = self.project()
    (repo.root / INSTALL_PATHS[0]).mkdir(parents=True)
    code, out, err = repo.run("ai", "skill", "--install", "--force", "--json")
    self.assertEqual((code, json.loads(out)["error"]["code"]), (2, "state"))
    self.assertFalse((repo.root / INSTALL_PATHS[2]).exists())

  def test_SkillWrite_Json_ReportsSkillAndPathsWritten(self) -> None:
    repo = self.project()
    code, out, err = repo.run("ai", "skill", "--install", "--output", "x/SKILL.md", "--json")
    self.assertEqual((code, err), (0, ""))
    payload = json.loads(out)
    self.assertEqual(payload["skill"], ai.skill_text())
    self.assertEqual(
      payload["written"],
      [str(repo.root / "x/SKILL.md"), *(str(repo.root / p) for p in INSTALL_PATHS)],
    )

  def test_SkillWrite_OutputNamingAnInstallPath_IsWrittenOnce(self) -> None:
    repo = self.project()
    code, out, _ = repo.run("ai", "skill", "--install", "--output", INSTALL_PATHS[0], "--json")
    self.assertEqual(code, 0)
    written = json.loads(out)["written"]
    self.assertEqual(len(written), len(set(written)))
    self.assertEqual(len(written), 3)

  def test_SkillWrite_NeverReadsTheCommittedSkillFile(self) -> None:
    committed = (Path(__file__).resolve().parents[1] / "skills" / "slicer" / "SKILL.md").resolve()
    repo = self.project()
    real_bytes, real_text = Path.read_bytes, Path.read_text

    def guard(real):
      def wrapper(self_path, *args, **kwargs):
        if self_path.resolve() == committed:
          raise AssertionError("the committed skills/slicer/SKILL.md must not be read")
        return real(self_path, *args, **kwargs)
      return wrapper

    with patch.object(Path, "read_bytes", guard(real_bytes)), \
         patch.object(Path, "read_text", guard(real_text)):
      self.assertEqual(repo.run("ai", "skill", "--install")[0], 0)

  def test_SkillWrite_NoFlags_StillPrintsAndWritesNothing(self) -> None:
    repo = self.project()
    before = self.files(repo)
    code, out, err = repo.run("ai", "skill")
    self.assertEqual((code, err, out), (0, "", ai.skill_text()))
    self.assertEqual(self.files(repo), before)

  def test_SkillForce_WithoutAWriteFlag_IsUsage(self) -> None:
    repo = self.project()
    code, out, err = repo.run("ai", "skill", "--force", "--json")
    self.assertEqual((code, json.loads(out)["error"]["code"]), (2, "usage"))
    self.assertEqual(self.files(repo), {})

  def test_SkillInstall_OutsideAProject_RefusesAndWritesNothing(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      code, out, err = repo.run("ai", "skill", "--install", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out)["error"]["code"], "state")
      self.assertEqual(list(repo.root.iterdir()), [])

  def test_SkillOutput_OutsideAProject_FallsBackToTheGenericText(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      code, out, err = repo.run("ai", "skill", "--output", "SKILL.md")
      self.assertEqual(code, 0)
      self.assertIn("printing the generic guide", err)
      self.assertEqual(repo.read("SKILL.md"), ai.skill_text())

  def test_SkillWrite_UnreadableProject_RefusesInsteadOfWritingTheGenericText(self) -> None:
    repo = self.project()
    (repo.root / ".slicer/index.json").write_text("{not json", encoding="utf-8")
    for argv in (("--install",), ("--output", "SKILL.md")):
      with self.subTest(argv=argv):
        code, _, _ = repo.run("ai", "skill", *argv)
        self.assertNotEqual(code, 0)
        self.assertEqual(self.files(repo), {})

  def test_SkillHelp_ListsTheWriteFlags(self) -> None:
    with redirect_stdout(io.StringIO()) as out:
      with self.assertRaises(SystemExit):
        cli.main(["ai", "skill", "--help"])
    text = " ".join(out.getvalue().split())
    for flag in ("--output", "--install", "--force"):
      self.assertIn(flag, text)
