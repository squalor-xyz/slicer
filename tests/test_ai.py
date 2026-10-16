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
from slicer import ai, cli, roles


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
      (("--help",), "agent onboarding: instructions, skill, relay"),
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

  def test_CommittedSkill_MatchesSkillText(self) -> None:
    root = Path(__file__).resolve().parents[1]
    text = ai.skill_text()
    for rel in (
      "skills/slicer/SKILL.md",
      ".claude/skills/slicer/SKILL.md",
      ".agents/skills/slicer/SKILL.md",
    ):
      with self.subTest(path=rel):
        committed = (root / rel).read_text(encoding="utf-8")
        self.assertEqual(committed, text)
        self.assertIn(
          "When the owner asks for a slice relay, run `slicer ai relay` and confirm its "
          "Setup with the owner before creating the worktree.",
          committed,
        )

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
      + ai.HANDOFF_SECTION + ai.BETWEEN_SLICES + ai.STATE + ai.FEEDBACK + ai.EXITS
      + "\n" + ai.DONE_CLOSER
    )
    self.assertEqual(ai.INSTRUCTIONS, expected)
    self.assertIn(ai.loop_text("handoff"), ai.instructions_text("handoff"))

  def test_Instructions_Feedback_InTheGuideAndRestButNotTheSkill(self) -> None:
    self.assertIn("slicer feedback-report --dry-run", ai.FEEDBACK)
    self.assertIn("only when the owner\nasks", ai.FEEDBACK)
    self.assertIn(ai.FEEDBACK, ai.INSTRUCTIONS)
    self.assertIn(ai.FEEDBACK.rstrip("\n"), ai.rest_text())
    self.assertNotIn("feedback-report", ai.skill_text())
    self.assertIn("slicer issues-pull", ai.FEEDBACK)
    self.assertIn("pull only when the owner\nasks", ai.FEEDBACK)
    self.assertNotIn("issues-pull", ai.skill_text())

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

  def test_AgentsGuide_Onboarding_DoesNotRequireTheLongDocs(self) -> None:
    text = (Path(__file__).resolve().parents[1] / "AGENTS.md").read_text(encoding="utf-8")
    opening = " ".join(text.split("## Install", 1)[0].split())
    roadmap = text.split("## Working on the roadmap", 1)[1].split("## House style", 1)[0]
    self.assertNotIn("before changing anything non-trivial", opening)
    self.assertNotIn("Read README.md", roadmap)
    self.assertIn("one command", roadmap)
    self.assertIn("docs/agents.md", roadmap)

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
    # s235: the landing `done` moved to docs/landing.md.
    landing = (Path(__file__).resolve().parents[1] / "docs" / "landing.md").read_text(encoding="utf-8")
    done = 'slicer done <ID> --note "Describe the verified outcome" --render --check'
    self.assertIn(done, landing)
    self.assertNotIn(done + " --strict", landing)

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

  def test_AgentsGuide_LandingSection_PointsAtLandingDoc(self) -> None:
    # s235: the landing steps live in docs/landing.md; AGENTS.md keeps a stub.
    root = Path(__file__).resolve().parents[1]
    text = (root / "AGENTS.md").read_text(encoding="utf-8")
    roadmap = text.split("## Working on the roadmap", 1)[1].split("## House style", 1)[0]
    self.assertLessEqual(len(roadmap.encode("utf-8")), 3800 - 300)
    stub = " ".join(roadmap.split("### Landing a slice", 1)[1].split("\n**", 1)[0].split())
    self.assertIn("(docs/landing.md)", stub)
    self.assertIn("only when the owner asks", stub.lower())
    self.assertIn("`done` on `main`, never in a worktree", stub)
    landing = (root / "docs" / "landing.md").read_text(encoding="utf-8")
    for command in ("next --status review --start --ready --render", "slicer reject",
                    "git merge --no-ff", "slicer done"):
      self.assertIn(command, landing)

  def test_LandingDoc_NamesSchemaBumpMerge(self) -> None:
    # s290: Git runs `slicer merge-index` from PATH (the editable install of main), which
    # refuses a newer schema, so a bump is merged with a PATH shim running the branch's code.
    root = Path(__file__).resolve().parents[1]
    landing = (root / "docs" / "landing.md").read_text(encoding="utf-8")
    self.assertIn("\n## Schema bumps\n", landing)
    section = landing.split("\n## Schema bumps\n", 1)[1].split("\n## ", 1)[0]
    for name in ("SCHEMA_VERSION", "ROLES_VERSION", "CHANGELOG", "slicer-index",
                 "slicer-relay-roles", "merge-index", "merge-relay-roles"):
      self.assertIn(name, section)
    self.assertIn("PYTHONPATH=%s exec python3 -m slicer", section)
    self.assertIn('PATH="$shim:$PATH" git merge --no-ff', section)

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

  def test_Instructions_Compaction_AfterEachSlice(self) -> None:
    sentence = (
      "After the finish step and before claiming the next slice, compact or start a fresh "
      "session; state lives in slicer."
    )
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        self.assertIn(sentence, ai.instructions_text(finish))
        self.assertEqual(ai.instructions_text(finish).count(sentence), 1)
    self.assertIn(sentence, ai.INSTRUCTIONS)
    self.assertEqual(ai.INSTRUCTIONS.count(sentence), 1)
    section = ai.INSTRUCTIONS.split("## Between slices", 1)[1].split("## ", 1)[0]
    self.assertIn(sentence, section)
    self.assertIn(sentence, ai.rest_text())
    self.assertEqual(ai.rest_text().count(sentence), 1)
    rest_section = ai.rest_text().split("## Between slices", 1)[1].split("## ", 1)[0]
    self.assertIn(sentence, rest_section)
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      code, text, _err = repo.run("ai", "instructions")
      self.assertEqual(code, 0)
      self.assertIn(sentence, text)
      self.assertEqual(text.count(sentence), 1)
      code, text, _err = repo.run("ai", "instructions", "--rest")
      self.assertEqual(code, 0)
      self.assertIn(sentence, text)
      self.assertEqual(text.count(sentence), 1)


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


class AiRelayTests(unittest.TestCase):
  """`ai relay` prints the slice relay and writes nothing."""

  def test_AiRelay_Json_ContractFollowsContractLine(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      with patch.object(ai, "contract_line", return_value="relay v999-test"):
        for command in ("relay", "relay-orchestrator"):
          with self.subTest(command=command):
            code, out, _ = repo.run("ai", command, "--json")
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["contract"], "relay v999-test")

  def test_AiRelayOrchestrator_Text_NamesParentDisputesGateResume(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      code, text, _ = repo.run("ai", "relay-orchestrator")
    self.assertEqual(code, 0)
    flat = " ".join(text.split())
    for phrase in (
      "`slicer relay-run step RUN --role ROLE --agent AGENT --vendor VENDOR "
      "--model MODEL --reasoning REASONING --parent STEP --phase start --task TASK`",
      "where STEP is your own step id",
      '- Disputes: a reproduction the reviewer ran beats the implementer\'s "does not reproduce".',
      "A dispute inside the slice goes back to the implementer; one outside "
      "it goes in the report as a later note.",
      "- Gate: after FAIL, resume the agent that raised the blockers.",
      "Start a fresh reviewer only when that agent is gone or the fixes rewrote much of the diff.",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, flat)

  def test_Relay_OutsideProject_TextAndJsonHaveIdenticalContent(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      code, text, err = repo.run("ai", "relay")
      self.assertEqual(code, 0)
      self.assertIn("printing the generic guide", err)
      code, out, err = repo.run("ai", "relay", "--json")
      self.assertEqual(code, 0)
      self.assertIn("printing the generic guide", err)
      self.assertEqual(json.loads(out), {"contract": "relay v5", "relay": text})
      self.assertEqual(text, ai.relay_text())
      before = repo.run("ai", "--json", "relay")
      after = repo.run("ai", "relay", "--json")
      self.assertEqual(before, after)
      self.assertEqual(list(repo.root.iterdir()), [])

  def test_Relay_HandoffProject_ClosesWithHandoffAndNeverDone(self) -> None:
    with support.TempRepo() as repo:
      self.assertEqual(repo.run("init")[0], 0)
      path = repo.root / ".slicer/config.json"
      data = json.loads(path.read_text())
      data["implement_finish"] = "handoff"
      data["handoff_requires_note_kind"] = "implementation report"
      path.write_text(json.dumps(data) + "\n")
      code, text, err = repo.run("ai", "relay")
      self.assertEqual((code, err), (0, ""))
      # A project with no role matrix yet gets the --init-roles line on top of the text.
      self.assertEqual(
        text, ai.relay_text("handoff", "implementation report", in_project=True),
      )
      self.assertIn("slicer handoff ID --render --check", text)
      self.assertIn(ai.handoff_policy_text("implementation report").strip(), text)
      # `--phase done` is a relay-run phase, not the finish command.
      self.assertNotIn("slicer done", text)
      self.assertNotIn("run done", text)

  def test_Relay_DoneProject_ClosesWithDone(self) -> None:
    close = 'slicer done ID --note "Describe the verified outcome" --render --check'
    self.assertEqual(ai.relay_text(), ai.relay_text("done"))
    self.assertIn(close, ai.relay_text("done"))
    with support.TempRepo() as repo:
      self.assertEqual(repo.run("init")[0], 0)
      code, text, err = repo.run("ai", "relay")
      self.assertEqual((code, err), (0, ""))
      self.assertEqual(text, ai.relay_text("done", in_project=True))
      self.assertIn(close, text)

  def test_Relay_NamesRolesOrderAndTheGateVerdictLine(self) -> None:
    text = ai.relay_text()
    for phrase in (
      "orchestrator",
      "implementer",
      "reviewer",
      "architect",
      "writes no product code",
      "read-only",
      "A reviewer never wrote code in this slice.",
      "The owner chooses the models.",
      "worktree",
      "slicer start ID --render --strict",
      "red",
      "accepted findings",
      "mutation-checks",
      "VERDICT: PASS",
      "VERDICT: FAIL",
      "medium severity",
      "proving test",
      "docs matching the code",
      "diff stat",
      "slicer show ID --json --lean",
      "slicer goals --json --lean",
      "slicer list --json --lean",
      "three times",
      "ambiguous",
      "owner decision",
      ai.TRACKING_RULE,
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, text)
    self.assertLess(text.index("## Roles"), text.index("## Order"))
    self.assertLess(text.index("## Order"), text.index("## Gate"))
    self.assertLess(text.index("## Gate"), text.index("## Every brief"))
    self.assertLess(text.index("## Every brief"), text.index("## Stop"))
    self.assertLess(text.index("## Stop"), text.index("## Close"))

  def test_Relay_CommandExamples_AreAcceptedByTheParser(self) -> None:
    blob = "\n".join((
      ai.relay_text("done"),
      ai.relay_text("handoff"),
      ai.relay_text("handoff", "implementation report"),
    ))
    commands = re.findall(r"`(slicer [^`]+)`", blob)
    self.assertTrue(commands)
    parser = cli.build_parser()
    for command in commands:
      with self.subTest(command=command):
        parser.parse_args(shlex.split(command)[1:])

  def test_Relay_NamesNoVendorModelOrOutsidePath(self) -> None:
    blob = "\n".join((
      ai.relay_text("done"),
      ai.relay_text("handoff"),
      ai.relay_text("handoff", "implementation report"),
    )).casefold()
    self.assertIn("the owner chooses the models.", blob)
    for token in (
      "claude", "codex", "grok", "openai", "anthropic", "gemini", "gpt", "ollama",
      "http", "github.com", "/users", "\\",
    ):
      with self.subTest(token=token):
        self.assertNotIn(token, blob)

  def test_Relay_ReadsConfigAndIndex_WithoutLockingOrWriting(self) -> None:
    with support.TempRepo() as repo:
      self.assertEqual(repo.run("init")[0], 0)
      data = json.loads((repo.root / ".slicer/config.json").read_text())
      data["implement_finish"] = "handoff"
      (repo.root / ".slicer/config.json").write_text(json.dumps(data) + "\n")
      before = {p.relative_to(repo.root): p.read_bytes()
                for p in repo.root.rglob("*") if p.is_file()}
      with patch("slicer.cli.store.project_lock", side_effect=AssertionError("lock")), \
           patch("slicer.cli.store.load", side_effect=AssertionError("load")):
        code, text, err = repo.run("ai", "relay")
      self.assertEqual((code, err), (0, ""))
      self.assertIn("slicer handoff ID --render --check", text)
      # `--phase done` is a relay-run phase, not the finish command.
      self.assertNotIn("slicer done", text)
      self.assertNotIn("run done", text)
      after = {p.relative_to(repo.root): p.read_bytes()
               for p in repo.root.rglob("*") if p.is_file()}
      self.assertEqual(after, before)
      repo.write(".slicer/index.json", "{")
      before = {p.relative_to(repo.root): p.read_bytes()
                for p in repo.root.rglob("*") if p.is_file()}
      code, text, err = repo.run("ai", "relay")
      self.assertEqual(code, 0)
      self.assertEqual(text, ai.relay_text())
      self.assertIn("printing the generic guide", err)
      self.assertIn("done", text)
      after = {p.relative_to(repo.root): p.read_bytes()
               for p in repo.root.rglob("*") if p.is_file()}
      self.assertEqual(after, before)

  def test_TopLevelHelp_AiRow_NamesRelay(self) -> None:
    with redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
      with self.assertRaises(SystemExit):
        cli.main(["--help"])
    row = next(line for line in out.getvalue().splitlines() if line.strip().startswith("ai "))
    for name in ("instructions", "skill", "relay"):
      self.assertIn(name, row)

  def test_Instructions_PointToAiRelay(self) -> None:
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        self.assertIn("`slicer ai relay`", ai.instructions_text(finish))
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      code, text, _err = repo.run("ai", "instructions")
      self.assertEqual(code, 0)
      self.assertIn("`slicer ai relay`", text)
      code, out, _err = repo.run("ai", "instructions", "--json")
      self.assertEqual(code, 0)
      self.assertIn("`slicer ai relay`", json.loads(out)["instructions"])

  def test_InstructionsRest_PointToAiRelay(self) -> None:
    self.assertIn("`slicer ai relay`", ai.rest_text())
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      code, out, _err = repo.run("ai", "instructions", "--rest", "--json")
      self.assertEqual(code, 0)
      self.assertIn("`slicer ai relay`", json.loads(out)["instructions"])

  def test_SkillText_RelayRequest_PointsToAiRelay(self) -> None:
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        text = ai.skill_text(finish)
        self.assertIn(
          "When the owner asks for a slice relay, run `slicer ai relay` and confirm its "
          "Setup with the owner before creating the worktree.",
          text,
        )

  def test_SkillText_RelayMention_IsOnlyTheRequestLine(self) -> None:
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        lines = [line for line in ai.skill_text(finish).splitlines() if "relay" in line]
        self.assertEqual(len(lines), 1)

  def test_RelayText_Setup_BeforeOrder(self) -> None:
    text = ai.relay_text()
    self.assertIn("## Setup", text)
    self.assertLess(text.index("## Roles"), text.index("## Setup"))
    self.assertLess(text.index("## Setup"), text.index("## Order"))

  def test_RelayText_Setup_NamesRolesModelsAndEndPoint(self) -> None:
    text = ai.relay_text()
    self.assertIn("## Setup", text)
    setup = text.split("## Setup", 1)[1].split("## Order", 1)[0]
    for phrase in (
      "Before the worktree exists",
      "size",
      "which roles run",
      "The architect is optional",
      "the owner decides",
      "agent, vendor, model and reasoning effort",
      "after the finish step",
      "after a commit on the slice branch",
      "landed",
      "Land only when the owner asks.",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, setup)
    order = text.split("## Order", 1)[1].split("## Gate", 1)[0]
    self.assertIn("the architect, when the owner chose one", order)

  def test_RelayText_Setup_AsksWhetherToSplit(self) -> None:
    text = ai.relay_text()
    setup = " ".join(text.split("## Setup", 1)[1].split("## Order", 1)[0].split())
    for phrase in (
      "whether the implementation splits into parts",
      "The default is one implementer.",
      "parts with disjoint files and their own tests",
      "a slice too big for one implementer is two slices",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, setup)

  def test_AiRelay_HasSliversSectionAndSetupBullet(self) -> None:
    text = ai.relay_text()
    setup = " ".join(text.split("## Setup", 1)[1].split("## Order", 1)[0].split())
    for phrase in (
      "whether to sliver the slice. The default is no.",
      "confirms the sliver list and which slivers run in parallel with the owner",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, setup)
    self.assertLess(text.index("## Order"), text.index("## Slivers"))
    self.assertLess(text.index("## Slivers"), text.index("## Gate"))
    self.assertEqual(text.count("## Gate"), 1)
    self.assertEqual(text.count("Name the diff under review."), 1)
    slivers = " ".join(text.split("## Slivers", 1)[1].split("\n## ", 1)[0].split())
    for phrase in (
      "Eligibility",
      "effort 1 at any size",
      "effort 2 only when every sliver comes out at effort 1",
      "effort 3 never",
      "Filing",
      "on main before the worktree is cut",
      "`slicer add TITLE --parent ID`",
      "one `slicer import` file",
      "size S, effort 1",
      "the parent's tree and pass",
      "Order",
      "in the parent's worktree",
      "implement, review, gate, handoff",
      "one combined gate on the whole diff",
      "Parallel",
      "disjoint files and their own tests",
      "its own temp directory",
      "the full suite on the combined diff before the combined gate",
      "Escalation",
      "follow the matrix rule",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, slivers)
    # The section is generic text: the project's matrix is not needed to read it.
    self.assertIn("## Slivers", ai.relay_text("handoff", "implementation report", in_project=True))

  def test_RelayText_Roles_LeadImplementerOwnsTracking(self) -> None:
    text = ai.relay_text()
    roles_section = " ".join(text.split("## Roles", 1)[1].split("## Setup", 1)[0].split())
    for phrase in (
      "In a split, one lead implementer owns the slice",
      "runs `slicer start ID --render --strict`, the note and the finish step",
      "Part implementers change only their own files",
      "run no slicer command that writes",
      "A reviewer never wrote code in this slice. In a split, that holds for every part.",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, roles_section)

  def test_RelayText_Order_SplitPartsThenOneReview(self) -> None:
    text = ai.relay_text()
    order = " ".join(text.split("## Order", 1)[1].split("## Gate", 1)[0].split())
    for phrase in (
      "In a split, the parts run in the slice worktree",
      "in parallel only when their files are disjoint, otherwise one after another",
      "The orchestrator runs the full suite on the combined diff",
      "one review and one gate cover the whole slice",
      "Accepted findings go back to the part that owns the file.",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, order)

  def test_RelayText_EveryBrief_PartBriefNamesItsFiles(self) -> None:
    text = ai.relay_text()
    brief = " ".join(text.split("## Every brief", 1)[1].split(ai.TRACKING_RULE, 1)[0].split())
    for phrase in (
      "A part brief names the part's own files and the files it must not touch.",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, brief)

  def test_RelayText_Handoff_StillOmitsDone(self) -> None:
    text = ai.relay_text("handoff", "implementation report")
    self.assertIn("## Setup", text)
    self.assertIn("slicer handoff ID --render --check", text)
    # `--phase done` is a relay-run phase, not the finish command.
    self.assertNotIn("slicer done", text.casefold())
    self.assertNotIn("run done", text.casefold())

  def test_RelayText_EveryBrief_NamesTempDirPerRole(self) -> None:
    text = ai.relay_text()
    brief = " ".join(text.split("## Every brief", 1)[1].split(ai.TRACKING_RULE, 1)[0].split())
    sentence = "Every brief names the role's own temp directory, and no two agents share one."
    self.assertIn(sentence, brief)
    self.assertEqual(" ".join(text.split()).count(sentence), 1)

  def test_RelayText_Reviewers_MakeNoGitWrites(self) -> None:
    text = ai.relay_text()
    brief = " ".join(text.split("## Every brief", 1)[1].split(ai.TRACKING_RULE, 1)[0].split())
    sentence = (
      "Reviewer, architect and gate briefs forbid git writes; a needed write stops and "
      "goes to the owner."
    )
    self.assertIn(sentence, brief)
    self.assertEqual(" ".join(text.split()).count(sentence), 1)

  def test_RelayText_Orchestrator_ReadsDiffsAsItsContractSays(self) -> None:
    text = ai.relay_text()
    roles_section = " ".join(text.split("## Roles", 1)[1].split("## Setup", 1)[0].split())
    phrase = "reads diffs as `slicer ai relay-orchestrator` says"
    self.assertIn(phrase, roles_section)
    self.assertEqual(" ".join(text.split()).count(phrase), 1)

  def test_RelayText_ParallelSlices_LandOneAtATime(self) -> None:
    text = ai.relay_text()
    order = " ".join(text.split("## Order", 1)[1].split("## Gate", 1)[0].split())
    flat = " ".join(text.split())
    for phrase in (
      "Run slices in parallel only when their files are disjoint.",
      "Review and land them one at a time",
      "rerun the suite on the slice branch merged with the new main when main moved",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, order)
        self.assertEqual(flat.count(phrase), 1)

  def test_RelayText_FullSuite_RunsOncePerChange(self) -> None:
    text = ai.relay_text()
    order = " ".join(text.split("## Order", 1)[1].split("## Gate", 1)[0].split())
    flat = " ".join(text.split())
    for phrase in (
      "A whole-slice implementer runs the full suite once; in a split, parts run only "
      "their own tests and the orchestrator's run on the combined diff is the one full run.",
      "The orchestrator reruns the suite only if the diff changed after that run or main moved.",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, order)
        self.assertEqual(flat.count(phrase), 1)

  def test_RelayText_FreshOrchestrator_PerSlice(self) -> None:
    text = ai.relay_text()
    order = " ".join(text.split("## Order", 1)[1].split("## Gate", 1)[0].split())
    flat = " ".join(text.split())
    for phrase in (
      "Each slice gets a fresh orchestrator, so no orchestrator carries one slice's "
      "context into the next.",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, order)
        self.assertEqual(flat.count(phrase), 1)

  def test_RelayText_NamesRelayRunBeginStepEnd(self) -> None:
    repo = support.TempRepo()
    self.addCleanup(repo.close)
    self.assertEqual(repo.run("init")[0], 0)
    self.assertEqual(repo.run("relay-roles", "init", "--render")[0], 0)
    code, project, err = repo.run("ai", "relay")
    self.assertEqual((code, err), (0, ""))
    for label, text in (("generic", ai.relay_text()), ("project", project)):
      flat = " ".join(text.split())
      for phrase in (
        "`slicer relay-run begin ID --worktree PATH`, where PATH is the slice worktree.",
        "`slicer relay-run step RUN --role ROLE --agent AGENT --vendor VENDOR "
        "--model MODEL --reasoning REASONING --phase start --task TASK`",
        "`slicer relay-run step RUN --step STEP --phase done`",
        "`slicer relay-run end RUN --outcome pass`",
      ):
        with self.subTest(label=label, phrase=phrase):
          self.assertIn(phrase, flat)

  def test_RelayText_RelayRunCommands_PassUnknownFlagCheck(self) -> None:
    text = ai.relay_text()
    commands = [
      span for span in cli._BACKTICK_SPAN.findall(text) if span.startswith("slicer relay-run ")
    ]
    self.assertLessEqual({"begin", "step", "end"}, {span.split()[2] for span in commands})
    parser = cli._cached_parser()
    for command in commands:
      with self.subTest(command=command):
        self.assertEqual(cli._unknown_flags(parser, shlex.split(command)[1:]), [])

  def test_RelayText_RelayRunSentences_EachAppearsOnce(self) -> None:
    flat = " ".join(ai.relay_text().split())
    for sentence in (
      "`slicer relay-run step RUN --step STEP --phase start` re-records a long-running step "
      "as a heartbeat, so its run does not go stale after 30 minutes.",
      "After the finish step, or when the relay stops, end the run with "
      "`slicer relay-run end RUN --outcome pass` (or `fail` or `stopped`), after marking "
      "each step done or failed.",
      "Recording is advisory: a failed `relay-run` call never stops the relay.",
    ):
      with self.subTest(sentence=sentence):
        self.assertEqual(flat.count(sentence), 1)


class AiRelayRolesTests(unittest.TestCase):
  """`ai relay --init-roles` writes the role matrix; `ai relay-roles` and `ai relay` read it.

  The matrix is `.slicer/relay-roles.json` (s257); `tests/test_relay_roles.py` covers the
  `relay-roles` commands themselves and this class keeps the `ai` entry points honest.
  """

  ROLES = ".slicer/relay-roles.json"

  def project(self) -> support.TempRepo:
    repo = support.TempRepo()
    self.addCleanup(repo.close)
    self.assertEqual(repo.run("init")[0], 0)
    return repo

  def with_matrix(self) -> support.TempRepo:
    repo = self.project()
    self.assertEqual(repo.run("relay-roles", "init", "--render")[0], 0)
    return repo

  def snapshot(self, repo: support.TempRepo) -> dict[str, bytes]:
    return {
      str(p.relative_to(repo.root)): p.read_bytes()
      for p in repo.root.rglob("*") if p.is_file() and p.name != "lock"
    }

  def test_RelayInitRoles_NoFile_WritesSkeletonRowsAndRules(self) -> None:
    repo = self.project()
    code, out, err = repo.run("ai", "relay", "--init-roles", "--json")
    self.assertEqual((code, err), (0, ""))
    self.assertEqual(json.loads(out), {"written": [str(repo.root / self.ROLES)]})
    data = json.loads(repo.read(self.ROLES))
    self.assertEqual(
      [(row["role"], row["agent"]) for row in data["rows"]],
      [("implementer", "Claude Code")] * 5
      + [("reviewer", "Claude Code"), ("gate", "Claude Code"), ("architect", "Claude Code")],
    )
    self.assertEqual(len(data["rules"]), 8)
    self.assertEqual(repo.run("check")[0], 0)

  def test_RelayInitRoles_FileExists_RefusesWithoutForce(self) -> None:
    repo = self.with_matrix()
    before = self.snapshot(repo)
    code, out, err = repo.run("ai", "relay", "--init-roles", "--json")
    self.assertEqual(code, 2)
    self.assertEqual(json.loads(out)["error"]["code"], "state")
    self.assertIn("--force", err)
    self.assertEqual(self.snapshot(repo), before)

  def test_RelayInitRoles_Force_ReplacesFile(self) -> None:
    repo = self.with_matrix()
    self.assertEqual(repo.run("relay-roles", "remove", "r1")[0], 0)
    code, out, err = repo.run("ai", "relay", "--init-roles", "--force")
    self.assertEqual((code, err), (0, ""))
    self.assertEqual(len(json.loads(repo.read(self.ROLES))["rows"]), 8)

  def test_RelayInitRoles_ForceWithoutInitRoles_IsUsageError(self) -> None:
    repo = self.project()
    before = self.snapshot(repo)
    code, out, err = repo.run("ai", "relay", "--force", "--json")
    self.assertEqual((code, json.loads(out)["error"]["code"]), (2, "usage"))
    self.assertEqual(self.snapshot(repo), before)

  def test_RelayInitRoles_OutsideProject_RefusesAndWritesNothing(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      code, out, err = repo.run("ai", "relay", "--init-roles", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out)["error"]["code"], "state")
      self.assertEqual(list(repo.root.iterdir()), [])

  def test_Relay_MatrixPresent_EmbedsItsRenderUnderRolesMatrixHeading(self) -> None:
    repo = self.with_matrix()
    code, out, err = repo.run("ai", "relay")
    self.assertEqual((code, err), (0, ""))
    embedded = roles.render_markdown(roles.skeleton(), level=2)
    self.assertIn("## Roles matrix\n\n" + embedded.rstrip("\n") + "\n\n## Order", out)
    self.assertNotIn("# Relay roles", out)
    setup = out.split("## Setup", 1)[1].split("## Roles matrix", 1)[0]
    self.assertIn(
      "Propose the lineup (agent, vendor, model and reasoning effort for each role) from the matrix below.",
      setup,
    )
    self.assertNotIn("relay-roles init", out)
    code, out_json, err = repo.run("ai", "relay", "--json")
    self.assertEqual((code, err), (0, ""))
    self.assertEqual(json.loads(out_json)["relay"].rstrip("\n"), out.rstrip("\n"))

  def test_Relay_MatrixPresent_SetupNamesTheRecommendCommand(self) -> None:
    repo = self.with_matrix()
    code, out, err = repo.run("ai", "relay")
    self.assertEqual((code, err), (0, ""))
    setup = out.split("## Setup", 1)[1].split("## Roles matrix", 1)[0]
    self.assertIn(
      "`slicer relay-roles recommend ID --well-specified yes|no --split yes|no`", setup,
    )

  def test_Relay_MatrixPresent_SetupTellsPartsToPassSplitYes(self) -> None:
    repo = self.with_matrix()
    code, out, err = repo.run("ai", "relay")
    self.assertEqual((code, err), (0, ""))
    setup = out.split("## Setup", 1)[1].split("## Roles matrix", 1)[0]
    self.assertIn(
      "Pass `--split yes` for a part of a split slice and `--split no` for a whole one.",
      " ".join(setup.split()),
    )

  def test_Relay_NoMatrixOrOutsideProject_DoesNotNameRecommend(self) -> None:
    repo = self.project()
    self.assertNotIn("recommend", repo.run("ai", "relay")[1])
    self.assertNotIn("recommend", ai.relay_text())
    self.assertNotIn("recommend", ai.relay_text("done", in_project=True))

  def test_Relay_NoMatrix_SetupNamesRelayRolesInit(self) -> None:
    repo = self.project()
    code, out, err = repo.run("ai", "relay")
    self.assertEqual((code, err), (0, ""))
    self.assertNotIn("## Roles matrix", out)
    setup = out.split("## Setup", 1)[1].split("## Order", 1)[0]
    self.assertIn("`slicer relay-roles init`", setup)
    self.assertEqual(out, ai.relay_text("done", in_project=True))

  def test_Relay_OutsideProject_PrintsGenericTextWithoutRoles(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      code, text, err = repo.run("ai", "relay")
      self.assertEqual(code, 0)
      self.assertIn("printing the generic guide", err)
      self.assertEqual(text, ai.relay_text())
      self.assertNotIn("Roles matrix", text)
      self.assertNotIn("relay-roles init", text)
    with self.subTest(case="unreadable index beside a matrix"):
      repo = self.with_matrix()
      repo.write(".slicer/index.json", "{")
      code, text, err = repo.run("ai", "relay")
      self.assertEqual(code, 0)
      self.assertIn("printing the generic guide", err)
      self.assertEqual(text, ai.relay_text())

  def test_RelayRoles_TextAndJsonHaveIdenticalContent(self) -> None:
    repo = self.with_matrix()
    code, text, err = repo.run("ai", "relay-roles")
    self.assertEqual((code, err), (0, ""))
    self.assertTrue(text.startswith("# Relay roles\n"))
    code, out, err = repo.run("ai", "relay-roles", "--json")
    self.assertEqual((code, err), (0, ""))
    self.assertEqual(json.loads(out)["relay_roles"], text)
    self.assertEqual(text, roles.render_markdown(roles.skeleton()))

  def test_RelayRoles_NoFile_ExitsTwoWithCodeAndNamesInit(self) -> None:
    repo = self.project()
    code, out, err = repo.run("ai", "relay-roles", "--json")
    self.assertEqual(code, 2)
    error = json.loads(out)["error"]
    self.assertEqual(error["code"], "no_relay_roles")
    self.assertIn("slicer relay-roles init", error["message"])
    self.assertIn("slicer relay-roles init", err)
    self.assertFalse((repo.root / self.ROLES).exists())

  def test_RelayRoles_ReadsWithoutLockingOrWriting(self) -> None:
    repo = self.with_matrix()
    before = self.snapshot(repo)
    with patch("slicer.cli.store.project_lock", side_effect=AssertionError("lock")), \
         patch("slicer.cli.store.load", side_effect=AssertionError("load")):
      code, out, err = repo.run("ai", "relay-roles", "--json")
    self.assertEqual((code, err), (0, ""))
    self.assertIn("rows", json.loads(out))
    self.assertEqual(self.snapshot(repo), before)

  def test_RelayRoles_OutsideProject_RefusesWithStateCode(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      code, out, err = repo.run("ai", "relay-roles", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out)["error"]["code"], "state")
      self.assertEqual(list(repo.root.iterdir()), [])

  def test_Skill_RelayLine_PointsToRelayRolesCommands(self) -> None:
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        lines = [
          line for line in ai.skill_text(finish).splitlines()
          if "When the owner asks for a slice relay" in line
        ]
        self.assertEqual(len(lines), 1)
        self.assertIn("`slicer relay-roles show`", lines[0])
        self.assertIn("never from `.slicer/relay-roles.json`", lines[0])

  def test_Instructions_PointToRelayRolesShow(self) -> None:
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        self.assertIn("`slicer relay-roles show`", ai.instructions_text(finish))
    self.assertIn("`slicer relay-roles show`", ai.rest_text())

  def test_CommandsDoc_ListsRelayRolesAndInitRoles(self) -> None:
    root = Path(__file__).resolve().parents[1]
    rows = [
      line for line in (root / "docs" / "commands.md").read_text(encoding="utf-8").splitlines()
      if line.startswith("| `")
    ]
    init = [row for row in rows if row.startswith("| `ai relay --init-roles")]
    roles = [row for row in rows if row.startswith("| `ai relay-roles`")]
    self.assertEqual(len(init), 1)
    self.assertEqual(len(roles), 1)
    for flag in ("--init-roles", "--force", ".slicer/relay-roles.json"):
      self.assertIn(flag, init[0])
    self.assertIn("no_relay_roles", roles[0])

  def test_AgentsDoc_DocumentsRelayRolesPayloadAndCode(self) -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "docs" / "agents.md").read_text(encoding="utf-8")
    self.assertIn("`ai relay-roles`", text)
    self.assertIn("`relay_roles`", text)
    self.assertIn("| `no_relay_roles` |", text)

  def test_CommandsDoc_ListsRelayRolesRecommend(self) -> None:
    root = Path(__file__).resolve().parents[1]
    rows = [
      line for line in (root / "docs" / "commands.md").read_text(encoding="utf-8").splitlines()
      if line.startswith("| `relay-roles recommend ID ")
    ]
    self.assertEqual(len(rows), 1)
    for word in ("--well-specified", "--split", "no_relay_roles", "no_such_item", "first row"):
      self.assertIn(word, rows[0])

  def test_AgentsDoc_DocumentsRelayRolesRecommendPayload(self) -> None:
    root = Path(__file__).resolve().parents[1]
    rows = [
      line for line in (root / "docs" / "agents.md").read_text(encoding="utf-8").splitlines()
      if line.startswith("| `relay-roles recommend`")
    ]
    self.assertEqual(len(rows), 1)
    self.assertIn("{id, well_specified, split, sliver, roles, unmatched}", rows[0])
    self.assertIn("no_relay_roles", rows[0])

  def test_Relay_NonUtf8Matrix_IsCorruptExitThree(self) -> None:
    repo = self.project()
    path = repo.root / self.ROLES
    path.write_bytes(b"\xff\xfe not UTF-8\n")
    for argv in (("ai", "relay-roles", "--json"), ("ai", "relay", "--json")):
      with self.subTest(argv=argv):
        code, out, err = repo.run(*argv)
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(out)["error"]["code"], "corrupt")
