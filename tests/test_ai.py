"""Keep agent onboarding usable before any project state can be trusted."""

from __future__ import annotations

import ast
import dataclasses
import io
import json
import re
import shlex
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import support
from slicer import ai, cli, config, ops, relayrun, roles, store, templates


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
    sections = (
      ("## Start the work", "$ slicer start S01 --render", "S01 -> started"),
      ("## Finish and verify", "default finish, `done`",
       'slicer done S01 --note "Greeting checked" --render', "S01 -> done", "slicer check"),
      ("### Handoff alternative", "When `implement_finish` is `handoff`",
       "$ slicer handoff S01 --render", "S01 -> review, unclaimed", "slicer config implement_finish"),
    )
    for heading, *examples in sections:
      section = guide.partition(heading + "\n")[2].split("\n## ", 1)[0]
      for example in examples:
        with self.subTest(heading=heading, example=example):
          self.assertIn(example, section)

  def test_GettingStarted_MovedSections_KeepOldAnchorSignposts(self) -> None:
    guide = (Path(__file__).resolve().parents[1] / "docs" / "getting-started.md").read_text(
      encoding="utf-8"
    )
    anchors = (
      "1-install",
      "install-it-no-clone",
      "develop-on-it-clone--editable",
      "2-set-it-up",
      "3-configure-before-you-add-anything",
      "worked-workflows",
      "a-new-project-goals-to-a-roadmap",
      "an-existing-project-ai-review-to-a-roadmap",
      "import-prioritize-and-work-through-either-example",
      "4-get-your-roadmap-in",
      "4a-from-nothing",
      "4b-in-bulk-from-a-markdown-outline",
      "4c-from-an-existing-legacy-markdown-tree",
      "5-turn-an-item-into-a-slice",
      "6-the-loop",
      "priority",
      "relay-roles",
      "batch-changes",
      "7-passes-if-you-want-them",
      "goals-and-non-goals",
      "8-wire-it-into-ci",
      "9-when-something-goes-wrong",
    )
    for anchor in anchors:
      with self.subTest(anchor=anchor):
        pattern = rf'<a id="{anchor}"></a> \[[^\]]+\]\(([^\)]+)\)'
        self.assertRegex(guide, pattern)
        target, _, fragment = re.search(pattern, guide).group(1).partition("#")
        path = Path(__file__).resolve().parents[1] / "docs" / target
        self.assertTrue(path.is_file(), f"Missing signpost target: {target}")
        if fragment:
          text = path.read_text(encoding="utf-8")
          ids = set(re.findall(r'<a id="([^"]+)"', text))
          counts = {}
          for heading in re.findall(r"^#{1,6} +(.+?) *#* *$", text, re.MULTILINE):
            heading = re.sub(r"<[^>]+>", "", heading).lower()
            slug = re.sub(r"[^\w\- ]", "", heading).replace(" ", "-")
            count = counts.get(slug, 0)
            counts[slug] = count + 1
            ids.add(f"{slug}-{count}" if count else slug)
          self.assertIn(fragment, ids, f"Missing signpost fragment: {target}#{fragment}")

  def test_Instructions_ExplainBlockedMarker_InListRows(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      text = repo.run("ai", "instructions")[1]
    expected = (
      "The text form of `list` and `find` marks an item that waits on a dependency "
      "not yet met with `!` after the row number; `slicer show ID --json --lean` "
      "names its `depends_on`."
    )
    for output, body in (("instructions", text), ("skill", ai.skill_text())):
      with self.subTest(output=output):
        self.assertIn(expected, body)

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
    self.assertIn("hold_flags", window)
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
    # s235: the landing `done` moved to docs/landing.md; s327: `land` took its place.
    landing = (Path(__file__).resolve().parents[1] / "docs" / "landing.md").read_text(encoding="utf-8")
    land = 'slicer land <ID> --branch feature/<id>-<slug> --note "Describe the verified outcome"'
    self.assertIn(land, landing)
    self.assertNotIn(land + " --strict", landing)

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

  def test_AgentsGuide_RoadmapNote_Renders(self) -> None:
    # s351: the contributor's closing note renders the slice, as the relay's does.
    text = (Path(__file__).resolve().parents[1] / "AGENTS.md").read_text(encoding="utf-8")
    section = text.split("## Working on the roadmap", 1)[1].split("## House style", 1)[0]
    notes = [line for line in section.splitlines() if "slicer note " in line]
    self.assertTrue(notes)
    for line in notes:
      self.assertIn("--render", line)

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

  def test_OrchestratorText_DoesNotSendToAiRelay(self) -> None:
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        text = " ".join(ai.orchestrator_text(finish).split())
        self.assertNotRegex(text, r"slicer ai relay`[^.]*follow")
        self.assertNotIn("one-slice parts", text)
        self.assertIn("This text is your whole contract for one slice", text)

  def test_OrchestratorText_GateEveryBriefStop_SameAsRelayText(self) -> None:
    for finish in ("done", "handoff"):
      for kind in ("", "implementation report"):
        relay = ai.relay_text(finish, kind)
        orchestrator = ai.orchestrator_text(finish, kind)
        for heading in ("Gate", "Every brief", "Stop"):
          with self.subTest(finish=finish, kind=kind, heading=heading):
            marker = f"## {heading}\n\n"
            self.assertEqual(orchestrator.count(marker), 1)
            body = relay.split(marker, 1)[1].split("\n## ", 1)[0]
            self.assertEqual(orchestrator.split(marker, 1)[1].split("\n## ", 1)[0], body)

  def test_OrchestratorText_Order_HasNoOuterSteps(self) -> None:
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        text = ai.orchestrator_text(finish)
        self.assertEqual(text.count("## Order\n\n"), 1)
        order = text.split("## Order\n\n", 1)[1].split("\n## ", 1)[0]
        self.assertNotRegex(order.lower(), r"relay-run (?:begin|end)|merge|land")
        for step in ("implementer", "review", "gate", "VERDICT: PASS", "closing note", "commit"):
          self.assertIn(step, order)
        self.assertIn(f"slicer {finish} ID", order)
        self.assertIn("Recording is advisory: a failed `relay-run` call never stops the relay.", text)

  def rule(self, finish: str, name: str) -> str:
    text = ai.orchestrator_text(finish)
    rules = text.split("## Rules\n\n", 1)[1].split("\n## ", 1)[0]
    bullet = next((b for b in rules.split("\n- ") if b.lstrip("- ").startswith(name)), "")
    self.assertTrue(bullet, f"no rule starting {name!r}")
    return " ".join(bullet.split())

  def test_OrchestratorText_SmallFixRound_ReviewAndGateInOnePass(self) -> None:
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        rule = self.rule(finish, "A small fix round")
        for phrase in (
          "(accepted findings only, no new files)",
          "reviewed and gated in one pass",
          "resume the reviewer, who checks the new diff and gives the VERDICT",
          "Anything larger gets a review round, then a gate round.",
        ):
          self.assertIn(phrase, rule)

  def test_OrchestratorText_PermissionDenied_StopsBeforeCommit(self) -> None:
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        rule = self.rule(finish, "A role's slicer step")
        for phrase in (
          "denies",
          "do not run it yourself and do not work around it",
          "Stop before the commit with OUTCOME stopped",
          "put the denial's exact text under owner needed",
          "as a `blocker` question whose reasoning is that exact text",
          "with options such as `grant:` and `skip:`",
        ):
          self.assertIn(phrase, rule)

  def test_AiRelay_Json_CarriesCheckCommand(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      self.assertEqual(repo.run("init")[0], 0)
      self.assertEqual(json.loads(repo.run("ai", "relay", "--json")[1])["check_command"], "")
      lean = json.loads(repo.run("ai", "relay", "--json", "--lean")[1])
      self.assertEqual(lean["check_command"], "")
      self.assertEqual(repo.run("config", "check_command", "make test")[0], 0)
      self.assertEqual(json.loads(repo.run("ai", "relay", "--json")[1])["check_command"], "make test")
      lean = json.loads(repo.run("ai", "relay", "--json", "--lean")[1])
      self.assertEqual(lean["check_command"], "make test")
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      code, out, _ = repo.run("ai", "relay", "--json")
      self.assertEqual((code, json.loads(out)["check_command"]), (0, ""))

  def test_AiRelay_Json_ContractFollowsContractLine(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      with patch.object(ai, "contract_line", return_value="relay v1099-test"):
        for command in ("relay", "relay-orchestrator"):
          with self.subTest(command=command):
            code, out, _ = repo.run("ai", command, "--json")
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["contract"], "relay v1099-test")

  def test_AiRelayOrchestrator_FailedReview_NoReject(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      for command in ("relay-orchestrator", "relay"):
        text = repo.run("ai", command)[1]
        for phrase, present in (
          ("slicer reject", False),
          ('slicer note ID --text "Review FAIL: ..." --render', True),
          ("Record a failed review on the started item", True),
          ("Add `--kind` only when `note_kinds` configures a kind for reviews.", True),
        ):
          with self.subTest(command=command, phrase=phrase):
            if present:
              self.assertIn(phrase, text)
            else:
              self.assertNotIn(phrase, text)

  def contract_texts(self):
    """Both relay contracts in each finish mode, as (label, flattened text)."""
    for finish in ("done", "handoff"):
      for label, text in (("relay", ai.relay_text(finish)), ("orchestrator", ai.orchestrator_text(finish))):
        yield f"{label}-{finish}", " ".join(text.split())

  def test_RelayGate_Findings_EachCarriesFixOwnerOrLaterKind(self) -> None:
    for label, flat in self.contract_texts():
      with self.subTest(label=label):
        for phrase in (
          "every reviewer, UI pass and gate finding is tagged with one kind",
          "`fix`: goes to the fix round.",
          "`owner`: a design call for the owner, with 2 to 4 options and a preferred one.",
          "does not block the gate unless it is marked `blocker`.",
          "`later`: a follow-up item with a one-line title.",
        ):
          self.assertIn(phrase, flat)

  def test_RelayGate_ReviewFail_WrittenOnlyAfterVerdictFail(self) -> None:
    for label, flat in self.contract_texts():
      with self.subTest(label=label):
        for phrase in (
          "Write a `Review FAIL` note only after `VERDICT: FAIL`.",
          "A gate that passed writes none, even when a fix round follows it (UI edits, say).",
        ):
          self.assertIn(phrase, flat)

  def test_RelayEveryBrief_ReviewerUiPassGate_TagFindingsByKind(self) -> None:
    for label, flat in self.contract_texts():
      with self.subTest(label=label):
        self.assertIn(
          "Reviewer, UI pass and gate briefs tell the agent to tag each finding `fix`, `owner` or "
          "`later` as Gate defines, and to give an `owner` finding its 2 to 4 options and a preferred one.",
          flat,
        )

  def test_RelayRoles_UiPass_ReadOnlyBesideReviewerProjectMarksUiSlices(self) -> None:
    flat = " ".join(ai.relay_text().split("## Roles", 1)[1].split("## Setup", 1)[0].split())
    for phrase in (
      "The UI pass is read-only and edits nothing.",
      "It runs beside the reviewer on a slice the project marks as UI;",
      "the project says what counts as a UI slice.",
      "It returns triaged suggestions, each tagged with a finding kind, and, where the project "
      "can render, the changed views.",
      "The gate still decides.",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, flat)

  def test_RelayOrder_UiPass_ParallelWithReviewerGateStillDecides(self) -> None:
    for finish in ("done", "handoff"):
      for label, text in (
        ("relay", ai.relay_text(finish)),
        ("orchestrator", ai.orchestrator_text(finish)),
      ):
        with self.subTest(label=label, finish=finish):
          order = " ".join(text.split("## Order\n\n", 1)[1].split("\n## ", 1)[0].split())
          for phrase in (
            "on a slice the project marks as UI, the UI pass runs in parallel with the reviewer",
            "the gate, which still decides",
          ):
            self.assertIn(phrase, order)

  def test_RelayContract_OrchestratorOrderJson_NamesUiPassInParallel(self) -> None:
    steps = ai.relay_contract(orchestrator=True)["order"]
    review = next(step["text"] for step in steps if step["step"] == 2)
    self.assertIn("the UI pass runs in parallel with the reviewer", review)
    self.assertIn("the gate, which still decides", review)

  def test_RelayEveryBrief_UiPass_SkillsThenVendorNeutralPromptAndRenders(self) -> None:
    for label, flat in self.contract_texts():
      with self.subTest(label=label):
        for phrase in (
          "The UI pass brief says the agent is read-only, edits nothing and makes no git writes, and "
          "tells it to load the skills the project's role matrix names for the UI pass;",
          "a vendor that cannot load skills gets the project's vendor-neutral UI prompt instead.",
          "It asks for each finding's kind and, where the project can render, the changed views.",
        ):
          self.assertIn(phrase, flat)

  UI_BRIEF_ITEM = (
    "whether the slice is a UI slice and whether the reviewer carries the UI pass, for a "
    "separate UI pass the skills the project's role matrix names for the UI pass or its "
    "vendor-neutral UI prompt"
  )

  def test_RelayHead_UiPass_NamedBesideTheReviewer(self) -> None:
    flat = " ".join(ai.relay_text().split("## Roles", 1)[0].split())
    self.assertIn("a reviewer (and, on a UI slice, a UI pass), an architect and a gate.", flat)

  def test_RelaySetup_RolesBullet_NamesUiPassBesideOrFoldedIntoReviewer(self) -> None:
    phrase = (
      "On a slice the project marks as UI, a UI pass runs beside the reviewer, or the reviewer "
      "carries it where the project folds it in by slice size."
    )
    bullet = next(item["text"] for item in ai.relay_contract()["setup"] if item["key"] == "roles")
    self.assertIn(phrase, bullet)
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        setup = " ".join(ai.relay_text(finish).split("## Setup", 1)[1].split("Wait for", 1)[0].split())
        self.assertIn(phrase, setup)

  def test_RelayOrder_OrchestratorBrief_NamesUiSliceFoldAndSkills(self) -> None:
    steps = ai.relay_contract()["order"]
    spawn = next(step["text"] for step in steps if step["step"] == 2)
    self.assertIn(self.UI_BRIEF_ITEM, spawn)
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        order = " ".join(ai.relay_text(finish).split("## Order", 1)[1].split("\n## ", 1)[0].split())
        self.assertIn(self.UI_BRIEF_ITEM, order)

  def test_OrchestratorText_Opening_BriefNamesUiSliceFoldAndSkills(self) -> None:
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        opening = " ".join(ai.orchestrator_text(finish).split("## Rules", 1)[0].split())
        self.assertIn("Your brief names the slice, the worktree and branch,", opening)
        self.assertIn(self.UI_BRIEF_ITEM, opening)

  def test_RelayEveryBrief_UiPass_ProjectMayFoldItIntoReviewerBySize(self) -> None:
    for label, flat in self.contract_texts():
      with self.subTest(label=label):
        for phrase in (
          "The project may fold the UI pass into the reviewer by slice size,",
          "such as the reviewer on a small slice and a separate role on a larger one;",
          "where it does, the reviewer's brief also carries the UI pass brief, and no separate UI pass runs.",
        ):
          self.assertIn(phrase, flat)

  def test_RelayText_UiPass_NamesNoProjectSkillPathOrRule(self) -> None:
    for label, flat in self.contract_texts():
      with self.subTest(label=label):
        for token in ("tui-design", "frontend-design", "u10", "ork", "adj 165", "roles.ui-pass"):
          self.assertNotRegex(flat.casefold(), r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])")

  def test_OrchestratorText_OwnerFinding_BlocksOnlyWhenBlocker(self) -> None:
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        rule = self.rule(finish, "Stop and return without asking the owner")
        for phrase in (
          "An `owner` finding that is not a `blocker` does not stop the relay",
          "goes in the report as a numbered owner-needed question",
          "A `blocker` owner finding stops the relay: ask it as Owner questions says",
          "return the report with OUTCOME stopped only when you cannot wait for the answer",
        ):
          self.assertIn(phrase, rule)
        self.assertNotIn('goes under "Owner needed"', rule)

  def test_RunReport_OwnerNeeded_NumberedQuestionWithS396Fields(self) -> None:
    template = next(
      line["template"] for line in ai.relay_contract()["report"]["lines"] if line["key"] == "owner_needed"
    )
    for field in (
      "owner needed: N. TITLE",
      "options: LABEL:SUMMARY; LABEL:SUMMARY",
      "recommend: LABEL",
      "blocker (optional)",
      "key: AREA.TOPIC (optional)",
      "reasoning: TEXT",
    ):
      with self.subTest(field=field):
        self.assertIn(field, template)
    self.assertNotIn("LIST", template)
    # Free text can hold `;` and `|`, so the free-text field is last and the template is format only.
    self.assertTrue(template.endswith(" | reasoning: TEXT"), template)
    self.assertNotIn("OR none", template)
    self.assertNotIn("one line per question", template)
    self.assertNotIn("(2 to 4)", template)
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        self.assertIn(template, ai.orchestrator_text(finish).split("## Run report", 1)[1])

  def test_RunReport_OwnerNeeded_QuestionsCountTowardLineLimit(self) -> None:
    self.assertEqual(ai.relay_contract()["report"]["max_lines"], 30)
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        flat = " ".join(ai.orchestrator_text(finish).split("## Run report", 1)[1].split())
        for phrase in (
          "At most 30 lines",
          "Each owner question is one line and counts toward the line limit.",
          "Number the questions from 1 and give each 2 to 4 options.",
          "Use no `|` in any value but `reasoning`, no `;` in a label or summary, and the first "
          "`:` ends the label.",
          "With no question, the line is `owner needed: none`.",
          "`reasoning` is the last field and takes the rest of the line.",
        ):
          self.assertIn(phrase, flat)

  def test_RelayStop_OwnerDecision_NamesBlockerInJsonAndText(self) -> None:
    phrase = "an owner decision marked `blocker` is needed (asked and waited on with `relay-run ask`)"
    for command in ("relay", "relay-orchestrator"):
      with self.subTest(command=command, part="json"):
        self.assertIn(phrase, ai.relay_contract(orchestrator=command == "relay-orchestrator")["stop"])
    for label, text in (("relay", ai.relay_text()), ("orchestrator", ai.orchestrator_text())):
      with self.subTest(command=label, part="text"):
        stop = " ".join(text.split("## Stop\n\n", 1)[1].split("\n## ", 1)[0].split())
        self.assertIn(phrase, stop)
        self.assertNotIn("or an owner decision is needed", stop)
    self.assertEqual(len(ai.RELAY_STOP), 3)
    rule = self.rule("done", "Stop and return without asking the owner")
    self.assertNotIn("or an owner decision marked `blocker` is needed", rule)

  def test_RelayGate_Criteria_FixFindingsAndBlockerOwnerOnly(self) -> None:
    for label, flat in self.contract_texts():
      with self.subTest(label=label):
        self.assertIn(
          "no new `fix` finding of medium severity or worse, no `blocker` owner finding, tests green",
          flat,
        )
        self.assertNotIn("no new issue of medium severity", flat)

  def setup_text(self, finish: str) -> str:
    return " ".join(ai.relay_text(finish).split("## Setup", 1)[1].split("Wait for", 1)[0].split())

  def test_RelaySetup_SpecCheckBullet_OptionalReadOnlyBatchBeforeWorktree(self) -> None:
    items = ai.relay_contract()["setup"]
    self.assertEqual(items[0]["key"], "spec_check")
    for finish in ("done", "handoff"):
      for label, flat in (
        ("json", items[0]["text"]),
        ("text", self.setup_text(finish)),
      ):
        with self.subTest(finish=finish, label=label):
          for phrase in (
            "whether to run a spec check on a batch of slices before any worktree exists",
            "It is optional.",
            "The owner answers the whole batch in one sitting.",
          ):
            self.assertIn(phrase, flat)

  def test_RelaySetup_SpecCheckBullet_AnswersFiledAsOwnerDecisionsAndCommittedOnMain(self) -> None:
    items = ai.relay_contract()["setup"]
    for finish in ("done", "handoff"):
      for label, flat in (
        ("json", items[0]["text"]),
        ("text", self.setup_text(finish)),
      ):
        with self.subTest(finish=finish, label=label):
          for phrase in (
            "records each answer in its slice as an `Owner decisions` paragraph with "
            '`slicer edit ID --section SECTION --append --text "Owner decisions: ..." --render`',
            "commits those filings on main before the worktree is cut",
            "a worktree starts at HEAD and an uncommitted filing never reaches it",
            "(SECTION being the section the question concerns)",
            "the owner authorizes that commit",
            "The brief carries the same paragraph.",
          ):
            self.assertIn(phrase, flat)

  def test_RelayRoles_SpecCheck_ReadOnlyReturnsReadyOrOwnerQuestions(self) -> None:
    flat = " ".join(ai.relay_text().split("## Roles", 1)[1].split("## Setup", 1)[0].split())
    for phrase in (
      "The spec check is optional, read-only and edits nothing.",
      "It runs before any worktree exists, on a batch of slices the owner picks.",
      "For each slice it returns `ready` or questions in the shape of the run report's "
      "`owner needed` line, each with 2 to 4 options, a preferred one, and `blocker` where "
      "the slice cannot go on without the answer.",
      "The outer session spawns it and reads its report. It has no matrix row, so the owner names "
      "its agent, model and reasoning effort at Setup like any role.",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, flat)

  def test_RelayRoles_Implementer_ReAnchorsLineReferencesLateAtStart(self) -> None:
    flat = " ".join(ai.relay_text().split("## Roles", 1)[1].split("## Setup", 1)[0].split())
    self.assertIn(
      "Line numbers a slice cites may have moved since its spec was written; at start the "
      "implementer re-anchors them against the code in the worktree.",
      flat,
    )
    self.assertIn(
      "It does not re-anchor the line numbers a slice cites: the implementer does that late.", flat
    )

  def test_RelayOrder_OwnerAnswerMidRun_NoteInWorktreeAndResumedRoleBrief(self) -> None:
    step3 = next(step["text"] for step in ai.relay_contract()["order"] if step["step"] == 3)
    self.assertIn(
      "An owner answer given while the run waits goes in a note on the item in the worktree "
      "and in the resumed role's brief, not in the slice on main.",
      step3,
    )
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        order = " ".join(ai.relay_text(finish).split("## Order", 1)[1].split("\n## ", 1)[0].split())
        for phrase in (
          "Owner answers: an answer the owner gives after the worktree exists never reaches it "
          "through the slice on main.",
          "The outer session records it against the slice worktree with "
          '`slicer note ID --text "Owner answer: ..." --render`, then resumes the orchestrator '
          "with the answer pasted in, and the orchestrator carries it into the resumed role's brief.",
        ):
          self.assertIn(phrase, order)

  def test_RelayOrder_OrchestratorBrief_NamesOwnerDecisionsAndAnswersSince(self) -> None:
    phrase = "the slice's `Owner decisions` paragraph and any owner answer since"
    spawn = next(step["text"] for step in ai.relay_contract()["order"] if step["step"] == 2)
    self.assertIn(phrase, spawn)
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        order = " ".join(ai.relay_text(finish).split("## Order", 1)[1].split("\n## ", 1)[0].split())
        self.assertIn(phrase, order)

  def test_RelayOrder_DecisionLog_ProjectRecordsExplicitlyPickedAnswersGenerically(self) -> None:
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        order = " ".join(ai.relay_text(finish).split("## Order", 1)[1].split("\n## ", 1)[0].split())
        self.assertIn(
          "A project that keeps a decision log also records there a design answer the owner "
          "explicitly picked, not a default an agent assumed.",
          order,
        )

  def test_OrchestratorRules_OwnerAnswer_CarriedVerbatimIntoResumedRoleBrief(self) -> None:
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        rule = self.rule(finish, "Owner answers")
        for phrase in (
          "an answer the outer session pastes in on resume is the owner's, not a role's report.",
          "Carry it verbatim, with the slice's `Owner decisions` paragraph, into the brief of "
          "the role you resume or start.",
          "The outer session has already recorded it as a note on the item in the worktree.",
        ):
          self.assertIn(phrase, rule)

  def test_RelayEveryBrief_OwnerDecisionsSpecCheckAndReAnchor_Carried(self) -> None:
    for label, flat in self.contract_texts():
      with self.subTest(label=label):
        for phrase in (
          "Every brief carries the slice's `Owner decisions` paragraph, when it has one, and "
          "each owner answer recorded in a note since, verbatim.",
          "The implementer brief says cited line numbers may have moved and to re-anchor them at start.",
          "The spec-check brief says the agent is read-only, edits nothing and makes no git "
          "writes, and asks for `ready` or `owner needed` lines for each slice.",
        ):
          self.assertIn(phrase, flat)

  def test_RelayEveryBrief_Implementer_SelfChecksDiffBeforeEachReview(self) -> None:
    for label, flat in self.contract_texts():
      with self.subTest(label=label):
        self.assertIn(
          "Before each review, fix rounds included, the implementer checks its own diff for "
          "concurrency and handle lifetime (locks, files and processes it opens are released on "
          "every path), for docs that describe the changed behaviour updated in the same diff, "
          "and for a test that fails when the new wiring is removed (a mutation that disconnects "
          "the call site), and fixes what it finds.",
          flat,
        )

  def test_RelayEveryBrief_ImplementerReport_CarriesSelfCheck(self) -> None:
    for label, flat in self.contract_texts():
      with self.subTest(label=label):
        self.assertIn(
          "The report is a diff stat, red lines, test counts, a mutation table, the implementer's "
          "self-check (each of the three: what it checked, or `n/a`) and deviations.",
          flat,
        )
        self.assertNotIn("a mutation table and deviations.", flat)

  def test_RelayEveryBrief_ReAnchor_NotRestated(self) -> None:
    for label, flat in self.contract_texts():
      with self.subTest(label=label):
        expected = 3 if label.startswith("relay-") else 1
        self.assertEqual(flat.lower().count("re-anchor"), expected)

  def test_RelaySpecCheck_Text_NamesNoProject(self) -> None:
    self.assertEqual(ai.RELAY_CONTRACT, 10)
    self.assertEqual([version for version, _ in ai.RELAY_CHANGES][-1], 10)
    for label, flat in self.contract_texts():
      with self.subTest(label=label):
        for token in ("ork", "adj", "s116", "s115", "roles.spec-check"):
          self.assertNotRegex(flat.casefold(), r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])")

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

  def test_AiRelay_EveryBrief_ForbidsCdForEachRole(self) -> None:
    for finish in ("done", "handoff"):
      for label, text in (
        ("relay", ai.relay_text(finish)), ("orchestrator", ai.orchestrator_text(finish)),
      ):
        with self.subTest(label=label, finish=finish):
          brief = " ".join(text.split("## Every brief", 1)[1].split("\n## ", 1)[0].split())
          self.assertIn(
            "Every brief says that every role, the read-only ones included, uses absolute paths "
            "and `git -C` and never `cd`.", brief)

  def test_AiRelayOrchestrator_Resume_PinsHeadlessWorkdir(self) -> None:
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        text = ai.orchestrator_text(finish)
        flat = " ".join(text.split())
        for phrase in (
          "A headless role you resume is given the slice worktree as its working directory, "
          "and you ask it to name the directory it ran in.",
          "Check that the directory it names is the slice worktree before you go on.",
        ):
          self.assertIn(phrase, flat)
        waiting = next(b for b in text.split("\n- ") if b.startswith("Waiting"))
        self.assertIn("in the slice worktree when it is headless, as the Gate rule below says", " ".join(waiting.split()))

  def test_Relay_OutsideProject_TextAndJsonHaveIdenticalContent(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      code, text, err = repo.run("ai", "relay")
      self.assertEqual(code, 0)
      self.assertIn("printing the generic guide", err)
      code, out, err = repo.run("ai", "relay", "--json")
      self.assertEqual(code, 0)
      self.assertIn("printing the generic guide", err)
      self.assertEqual({key: json.loads(out)[key] for key in ("contract", "relay")},
                       {"contract": "relay v10", "relay": text})
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

  def test_Relay_UiPassAndSpecCheckNameTheirLedgerRoles(self) -> None:
    orchestrator = " ".join(ai.orchestrator_text().split())
    self.assertIn("the UI pass as role `ui`", orchestrator)
    relay = " ".join(ai.relay_text().split())
    self.assertIn("The spec check runs before any orchestrator", relay)
    self.assertIn("with `--role spec-check` and no `--parent`", relay)
    self.assertIn(
      "ends that run with `slicer relay-run end RUN --outcome pass --close-steps done`", relay,
    )
    self.assertIn("(`--outcome stopped` if the owner stops it)", relay)

  def test_Relay_Heartbeat_SaysItEndsAWait(self) -> None:
    sentence = "A heartbeat on a waiting step ends its wait: do not heartbeat a step that waits."
    for label, text in (("relay", ai.relay_text()), ("orchestrator", ai.orchestrator_text())):
      with self.subTest(text=label):
        self.assertIn(sentence, " ".join(text.split()))

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
        "`slicer relay-run end RUN --outcome pass --close-steps done`",
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
      "End the run when its end point is reached, or when the relay or the owner stops it, with "
      "`slicer relay-run end RUN --outcome pass --close-steps done` (or `--outcome fail` or "
      "`stopped`, and `--close-steps fail` when the steps failed). It first marks each step "
      "still running, the orchestrator's own included, and without `--close-steps` it names "
      "those steps on stderr.",
      "Recording is advisory: a failed `relay-run` call never stops the relay.",
    ):
      with self.subTest(sentence=sentence):
        self.assertEqual(flat.count(sentence), 1)

  def test_AiRelay_ClosingNote_Renders(self) -> None:
    # s351: the closing note renders the slice, so the render is current once it lands.
    # Every `slicer note` line in the relay, orchestrator, instructions and skill outputs
    # carries --render, in each finish mode and with a required report kind. The relay
    # always names the closing note, and the orchestrator names it only when it hands off
    # (its done mode closes with `done`). instructions and skill name the ready-for-review
    # and kind notes, so each must print at least one note line.
    closing = 'slicer note ID --text "Describe the verified outcome" --render'
    for finish in ("done", "handoff"):
      for kind in ("", "implementation report"):
        with self.subTest(finish=finish, kind=kind):
          with support.TempRepo() as repo:
            self.assertEqual(repo.run("init")[0], 0)
            path = repo.root / ".slicer/config.json"
            data = json.loads(path.read_text())
            data["implement_finish"] = finish
            if kind:
              data["handoff_requires_note_kind"] = kind
            path.write_text(json.dumps(data) + "\n")
            for command in ("relay", "relay-orchestrator", "instructions", "skill"):
              with self.subTest(command=command):
                code, text, err = repo.run("ai", command)
                self.assertEqual((code, err), (0, ""))
                notes = [line for line in text.splitlines() if "slicer note " in line]
                # Review FAIL notes are separate from closing notes. The done-mode
                # orchestrator without a required kind closes with `slicer done --note`.
                closing_notes = [line for line in notes if '"Review FAIL:' not in line]
                names_note = not (command == "relay-orchestrator" and finish == "done" and not kind)
                self.assertEqual(bool(closing_notes), names_note)
                for line in notes:
                  self.assertIn("--render", line)
                if command == "relay" or (command == "relay-orchestrator" and finish == "handoff"):
                  self.assertIn(closing, text)

  def test_RelayTexts_ProseLines_FitIn88Columns(self):
    """Both texts, both finish modes, with and without a role matrix. Exempt: a line in a
    fenced block, a line of `## Roles matrix` (the project's table and the rule bullets
    `relay-roles` renders from its data, not contract prose), and a line that is one
    backticked `slicer` command alone (a wrap would split it)."""
    for finish in ("done", "handoff"):
      for matrix in (False, True):
        for command in ("relay", "relay-orchestrator"):
          with self.subTest(command=command, finish=finish, matrix=matrix), support.TempRepo() as repo:
            for argv in (("init",), ("config", "implement_finish", finish)):
              code, out, err = repo.run(*argv)
              self.assertEqual((code, err), (0, ""), out)
            if matrix:
              code, out, err = repo.run("relay-roles", "init", "--render")
              self.assertEqual((code, err), (0, ""), out)
            code, out, err = repo.run("ai", command)
            self.assertEqual((code, err), (0, ""), out)
            long, fenced, in_matrix = [], False, False
            for number, line in enumerate(out.splitlines(), 1):
              if line.startswith("## "):
                in_matrix = line == "## Roles matrix"
              if line.startswith("```"):
                fenced = not fenced
                continue
              if fenced or in_matrix or re.fullmatch(r"\s*`slicer [^`]+`[,.;:]?", line):
                continue
              if len(line) > 88:
                long.append(f"{number} ({len(line)}): {line}")
            self.assertEqual(long, [])

  def test_RunReport_QuestionValueRule_IsTheSentenceAskEnforces(self):
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        report = " ".join(ai.orchestrator_text(finish).split("## Run report", 1)[1].split())
        self.assertNotIn("no `;` in a SUMMARY", report)
        self.assertIn(" ".join(relayrun.QUESTION_VALUE_RULE.split()), report)
    rule = " ".join(relayrun.QUESTION_VALUE_RULE.split())
    for phrase in (
      "Use no `|` in any value but `reasoning`",
      "no `;` in a label or summary",
      "the first `:` ends the label",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, rule)


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
      + [("implementer", "Codex"), ("reviewer", "Claude Code"), ("gate", "Claude Code"),
         ("architect", "Claude Code"), ("orchestrator", "Claude Code"),
         ("orchestrator", "Codex")],
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
    self.assertEqual(len(json.loads(repo.read(self.ROLES))["rows"]), 11)

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
    setup = " ".join(out.split("## Setup", 1)[1].split("## Roles matrix", 1)[0].split())
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


class AiModuleTests(unittest.TestCase):
  def test_AiModule_Imports_ReachNoStateModule(self) -> None:
    source = (Path(support.__file__).resolve().parents[1] / "src" / "slicer" / "ai.py").read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
      if isinstance(node, ast.ImportFrom) and node.module == "slicer":
        imported.update(alias.name for alias in node.names)
      elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("slicer."):
        imported.add(node.module.split(".")[1])
      elif isinstance(node, ast.Import):
        imported.update(alias.name.split(".")[1] for alias in node.names if alias.name.startswith("slicer."))
    self.assertFalse(imported & {"ops", "render", "roles", "store", "templates"}, sorted(imported))


class AiRelayBriefTests(unittest.TestCase):
  def project(self):
    repo = support.TempRepo()
    self.addCleanup(repo.close)
    for args in (("init",), ("relay-roles", "init"), ("add", "Build the thing", "--id", "S01", "--size", "M", "--effort", "2"),
                 ("promote", "S01"),
                 ("edit", "S01", "--section", "Implement", "--text", "Build it"),
                 ("edit", "S01", "--section", "Check", "--text", "Test it")):
      code, out, err = repo.run(*args)
      self.assertEqual(code, 0, out + err)
    return repo

  def brief(self, repo, *extra, item="S01"):
    return repo.run("ai", "relay-brief", item, "--worktree", str(repo.root),
                    "--branch", "feature/brief", "--run", "run-example", "--step", "7",
                    "--well-specified", "yes", "--split", "no", *extra)

  def payload(self, repo, *extra):
    code, out, err = self.brief(repo, *extra, "--json", "--lean")
    self.assertEqual(code, 0, out + err)
    return json.loads(out)

  def test_AiRelayBrief_Json_CarriesCheckCommand(self):
    repo = self.project()
    self.assertEqual(self.payload(repo)["check_command"], "")
    self.assertEqual(repo.run("config", "check_command", "make test")[0], 0)
    self.assertEqual(self.payload(repo)["check_command"], "make test")

  def test_AiRelayBrief_Filled_ContainsIdentityAndRun(self):
    repo = self.project()
    text = self.payload(repo)["brief"]
    for expected in ("`S01: Build the thing`", "Slice: S01, size M, effort 2.", str(repo.root), "feature/brief", "RUN=run-example", "step: 7", "size M", "effort 2", "slicer ai relay-orchestrator"):
      self.assertIn(expected, text)
    self.assertEqual(self.brief(repo)[1], text)

  def test_AiRelayBrief_MatrixChange_UsesRecommendRows(self):
    repo = self.project()
    repo.run("relay-roles", "set", "r3", "--model", "changed-model")
    payload = self.payload(repo)
    recommended = json.loads(repo.run("relay-roles", "recommend", "S01", "--well-specified", "yes", "--split", "no", "--json")[1])
    self.assertEqual(payload["roles"], recommended["roles"])
    for expected in ("changed-model", "no architect", "orchestrator: r10", "reviewer: r5", "gate: r6", "Claude Code", "Claude", "high"):
      self.assertIn(expected, payload["brief"])

  def test_AiRelayBrief_PreferredAgent_UsesAcceptedRows(self):
    repo = self.project()
    text = self.payload(repo, "--prefer-agent", "Codex")["brief"]
    self.assertIn("implementer: r9 | Codex | OpenAI | gpt-6.1-sol | medium", text)
    self.assertIn("orchestrator: r11 | Codex | OpenAI | gpt-6.1-sol | high", text)

  def test_AiRelayBrief_SelectedRole_OverridesPreference(self):
    repo = self.project()
    text = self.payload(repo, "--prefer-agent", "Codex", "--select", "implementer=r3", "--select", "orchestrator=r10")["brief"]
    self.assertIn("implementer: r3 | Claude Code | Claude | sonnet | high", text)
    self.assertIn("orchestrator: r10 | Claude Code | Claude | opus | high", text)

  def test_AiRelayBrief_HandoffProject_HasFinishOverride(self):
    repo = self.project()
    repo.run("config", "implement_finish", "handoff")
    text = self.payload(repo)["brief"]
    self.assertIn("slicer start S01 --render --strict", text)
    self.assertIn('After VERDICT: PASS, run `slicer note S01 --text "Ready for review: ..." --render`', text)
    self.assertIn("then `slicer handoff S01 --render --check`", text)
    self.assertIn("Never run done in the worktree", text)

  def test_AiRelayBrief_NoteKind_RequiresKindAfterPass(self):
    repo = self.project()
    repo.run("config", "implement_finish", "handoff")
    repo.run("config", "note_kinds", '["implementation report"]')
    repo.run("config", "handoff_requires_note_kind", "implementation report")
    self.assertIn("--kind 'implementation report' --text \"Ready for review: ...\" --render", self.payload(repo)["brief"])

  def test_AiRelayBrief_DoneProject_NoOverride(self):
    repo = self.project()
    self.assertNotIn("Finish override", self.payload(repo)["brief"])

  def test_AiRelayBrief_Parallel_NamesOnlyOtherSlices(self):
    repo = self.project()
    repo.run("add", "Other", "--id", "S02")
    repo.run("add", "Third", "--id", "S03")
    text = self.payload(repo, "--parallel", "S02", "--parallel", "S01", "--parallel", "S03", "--parallel", "S02")["brief"]
    self.assertEqual([line for line in text.splitlines() if line.startswith("Parallel slices:")],
                     ["Parallel slices: S02, S03"])

  def test_AiRelayBrief_NoSlice_IsNoSlice(self):
    repo = self.project()
    repo.run("add", "Row only", "--id", "S02")
    code, out, err = self.brief(repo, "--json", item="S02")
    self.assertEqual(code, 2, out + err)
    self.assertEqual(json.loads(out)["error"]["code"], "no_slice", err)

  def test_AiRelayBrief_EmptyRequiredSection_Unspecified(self):
    repo = self.project()
    repo.run("edit", "S01", "--section", "Check", "--text", " ")
    code, out, err = self.brief(repo, "--json")
    self.assertEqual(code, 2, out + err)
    self.assertEqual(json.loads(out)["error"]["code"], "unspecified", err)

  def test_AiRelayBrief_UnknownId_NoSuchItem(self):
    repo = self.project()
    code, out, err = self.brief(repo, "--json", item="S99")
    self.assertEqual(code, 2, out + err)
    self.assertEqual(json.loads(out)["error"]["code"], "no_such_item", err)

  def test_AiRelayBrief_BuiltInTemplate_StatesNoGateFailureCount(self):
    repo = self.project()
    text = self.payload(repo)["brief"]
    self.assertNotIn("twice", text)
    self.assertIn("Stop section of `slicer ai relay-orchestrator`", text)

  def test_AiRelayBrief_BuiltInTemplate_StopNamesNoOwnerDecision(self):
    template = " ".join(templates.default("relay-brief.md").split())
    stop = template.split("- Stop and report", 1)[1].split("- Hand back", 1)[0]
    self.assertIn("(repeated gate failures or a wrong specification) instead of guessing.", stop)
    self.assertNotIn("owner decision", stop)
    self.assertIn(
      "A `blocker` owner question is not a stop: ask it and wait as the Owner questions rule says.",
      stop,
    )

  def test_AiRelayBrief_Rules_ForbidBackgroundShell(self):
    repo = self.project()
    rules = " ".join(self.payload(repo)["brief"].split()).split("## Rules", 1)[1]
    self.assertIn(
      "You and every role run their own shell commands in the foreground and background none.",
      rules,
    )
    self.assertIn(
      "Run long commands such as the full suite in the foreground with a long timeout.",
      rules,
    )

  def test_AiRelayBrief_MissingProjectTemplate_UsesBuiltIn(self):
    repo = self.project()
    before = self.brief(repo)[1]
    (repo.root / ".slicer" / "templates" / "relay-brief.md").unlink()
    code, out, err = self.brief(repo)
    self.assertEqual(code, 0, out + err)
    self.assertEqual(out, before)

  def test_AiRelayBrief_PlaceholderKeys_MatchTemplateKeys(self):
    repo = self.project()
    state = store.load(repo.root)
    context = {}
    with patch.object(ops.render, "expand", side_effect=lambda text, ctx: context.update(ctx) or text):
      ops.relay_brief(state, "S01", worktree="w", branch="b", run="r", step="1",
                     parallel=[], well_specified=True, split=False)
    self.assertEqual(set(context), set(templates.RELAY_BRIEF_KEYS))

  def test_AiRelayBrief_Contract_UsesLiveMarker(self):
    repo = self.project()
    with patch.object(ai, "contract_line", return_value="relay v1099-test"):
      self.assertEqual(self.payload(repo)["contract"], "relay v1099-test")


class RelayGoldenTests(unittest.TestCase):
  """Pin the CLI bytes of the current contract version independently of the live contract data."""

  def check_golden(self, command):
    golden = json.loads((Path(__file__).parent / "fixtures/relay-contract-v10.json").read_text())
    for finish in ("done", "handoff"):
      for matrix in (False, True):
        with self.subTest(finish=finish, matrix=matrix), support.TempRepo() as repo:
          for argv in (("init",), ("config", "implement_finish", finish)):
            code, out, err = repo.run(*argv)
            self.assertEqual((code, err), (0, ""), out)
          if matrix:
            code, out, err = repo.run("relay-roles", "init", "--render")
            self.assertEqual((code, err), (0, ""), out)
          code, out, err = repo.run("ai", command)
          self.assertEqual((code, err), (0, ""), out)
          self.assertEqual(out, golden[f"{command}-{finish}-{matrix}"])

  def test_AiRelay_Text_UnchangedByteForByte(self):
    self.check_golden("relay")

  def test_AiRelayOrchestrator_Text_UnchangedByteForByte(self):
    self.check_golden("relay-orchestrator")


class RelayApproveLandTests(unittest.TestCase):
  """The outer session's Approve and Land steps (s403) and the human checks recorded at approval."""

  WAIT_APPROVAL = "`slicer relay-run step RUN --step STEP --phase wait --waiting-on approval`"
  NOTE_KIND = "Add `--kind human-check` only when `note_kinds` configures that kind."
  END_RUN = "`slicer relay-run end RUN --outcome pass --close-steps done`"
  LAND = '`slicer land ID --branch BRANCH --note "Describe the verified outcome"`'

  def outer_step(self, number: int) -> str:
    return next(step["text"] for step in ai.relay_contract()["order"] if step["step"] == number)

  def land_steps(self, finish: str = "done") -> list[dict]:
    land = ai.relay_text(finish).split("## Land\n\n", 1)[1].split("\n## ", 1)[0]
    return [
      {"step": int(number), "text": " ".join(body.split())}
      for number, body in re.findall(r"(?m)^(\d+)\. (.*(?:\n {2,}[^\n]+)*)", land)
    ]

  def land_text(self, number: int, finish: str = "done") -> str:
    return next(step["text"] for step in self.land_steps(finish) if step["step"] == number)

  def land_section(self, finish: str = "done") -> str:
    return " ".join(ai.relay_text(finish).split("## Land\n\n", 1)[1].split("\n## ", 1)[0].split())

  def test_RelayOrder_ApproveStep_RecordsApprovalWaitAndOwnerWait(self) -> None:
    step = self.outer_step(5)
    self.assertTrue(step.startswith("Approve"), step)
    for phrase in (
      "Nothing is merged or landed before the owner approves; the only commit before it is the "
      "slice commit the orchestrator's brief allows.",
      "While the owner decides, record the wait on the orchestrator's step with " + self.WAIT_APPROVAL,
      "or with `--waiting-on owner` while the owner answers an owner-needed question from the "
      "report. A `blocker` question the orchestrator asked with `relay-run ask` already holds "
      "its step in that wait.",
      "Do not end the run while it waits: a waiting step never makes its run stale.",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, step)

  def test_RelayOrder_ApproveStep_ShowsVerdictTestsDiffStatOwnerFindingsAndHumanChecks(self) -> None:
    step = self.outer_step(5)
    for phrase in (
      "Show the owner the gate verdict, the tests, the diff stat, each owner-needed question, "
      "and each `[HUMAN CHECK]` line of the slice's Check section.",
      "The owner approves, or sends a fix note.",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, step)

  def test_RelayOrder_ApproveStep_FixNoteGoesToFixRoundThenApprovesAgain(self) -> None:
    step = self.outer_step(5)
    for phrase in (
      "Record a fix note as a note on the item in the worktree",
      "record `--phase start` on the step to end the wait",
      "resume the orchestrator with the note for a fix round",
      "which is reviewed and gated again before Approve repeats",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, step)

  def test_RelayOrder_ApproveStep_OnApprovalRecordsStartToEndTheWait(self) -> None:
    step = self.outer_step(5)
    self.assertIn(
      "On approval, record `slicer relay-run step RUN --step STEP --phase start` to end the wait.",
      step,
    )
    self.assertLess(step.index("On approval, record"), step.index("Record a fix note"))

  def test_RelayOrder_ApproveStep_FinishAlreadyRan_SetsItemBackToStartedStatus(self) -> None:
    step = self.outer_step(5)
    for phrase in (
      "When the finish step already ran, send the item back to the project's started status with "
      "`slicer set ID --status STATUS --render` (`slicer config started_status` names it) "
      "before the fix round;",
      "after its PASS, the closing note and the finish step run again.",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, step)
    # `started_status` is a real config key and `set --status` a real flag.
    self.assertIn("started_status", {f.name for f in dataclasses.fields(config.Config)})

  def test_RelayOrder_EndPointFinishEndsTheRunHere(self) -> None:
    step = self.outer_step(5)
    self.assertIn(
      "Approve, unless the end point is `finish`: end the run there with "
      "`slicer relay-run end RUN --outcome pass --close-steps done`.",
      step,
    )
    self.assertNotIn("Nothing is committed", step)

  def test_RelayOrder_HumanCheck_RecordedAsNoteWarnsAndDoesNotBlock(self) -> None:
    step = self.outer_step(5)
    for phrase in (
      "Record each human check the owner confirms as a note on the item in the worktree with "
      '`slicer note ID --text "Human check: ..." --render`.',
      self.NOTE_KIND,
      "A human check the owner has not confirmed is shown with a warning and does not block landing.",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, step)
    note_lines = [
      line for line in ai.relay_text().splitlines() if "Human check:" in line and "slicer note" in line
    ]
    self.assertTrue(note_lines)
    for line in note_lines:
      self.assertIn("--render", line)

  def test_RelayOrder_LandStep_FollowsTheLandSection(self) -> None:
    step = self.outer_step(6)
    self.assertTrue(step.startswith("Land"), step)
    for phrase in (
      "when the end point is `commit` or `landed` and the owner approved",
      "follow the Land section",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, step)
    self.assertNotIn("close-steps", step)
    self.assertEqual([item["step"] for item in ai.relay_contract()["order"]], [1, 2, 3, 4, 5, 6])

  def test_RelayLand_Section_SitsBetweenOrderAndSlivers(self) -> None:
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        text = ai.relay_text(finish)
        self.assertEqual(text.count("## Land\n\n"), 1)
        self.assertLess(text.index("## Order"), text.index("## Land"))
        self.assertLess(text.index("## Land"), text.index("## Slivers"))

  def test_RelayLand_SlicerRunsNoGit_OuterSessionRunsEachGitStep(self) -> None:
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        self.assertIn(
          "slicer runs no git command: the outer session runs the merge, commit, push and "
          "worktree removal itself.",
          self.land_section(finish),
        )

  def test_RelayLand_Steps_AreInTheRelayLandingOrder(self) -> None:
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        steps = self.land_steps(finish)
        self.assertEqual([step["step"] for step in steps], list(range(1, 11)))
        for number, phrase in (
          (1, "Rerun the slice's Check commands in the worktree."),
          (2, "Commit on the slice branch"),
          (3, "When main moved, merge main into the slice branch in the worktree"),
          (4, "Run `slicer render` in the worktree and commit any rewritten render files."),
          (5, "Run `slicer check`"),
          (6, "Rerun the full suite on the merged branch."),
          (7, "`git merge --ff-only BRANCH`"),
          (8, self.END_RUN),
          (9, self.LAND),
          (10, "push only when the owner authorizes it"),
        ):
          self.assertIn(phrase, self.land_text(number, finish))

  def test_RelayLand_Commit_CarriesHumanCheckNotesAndLaterNotes(self) -> None:
    text = self.land_text(2)
    for phrase in (
      "Commit on the slice branch what the worktree still holds: the human-check notes, and the "
      "finish step's state when the orchestrator did not commit it.",
      "Then file the report's later notes as one more commit on the slice branch.",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, text)

  def test_RelayLand_MergeMain_UsesMergeDriversAndRebaselinesGoldens(self) -> None:
    text = self.land_text(3)
    for phrase in (
      "slicer's merge drivers (`slicer setup-git` configures them)",
      "a conflict a driver leaves goes to the owner",
      "A merge can make a golden stale: rebaseline it and commit the result.",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, text)

  def test_RelayLand_CheckAndSuite_RepeatAfterDrift(self) -> None:
    self.assertIn("exit 1 means drift: fix it, then render and check again", self.land_text(5))
    self.assertIn("A failure goes back to the orchestrator as a fix round.", self.land_text(6))

  def test_RelayLand_FfOnly_IsTheContractDefault(self) -> None:
    text = self.land_text(7)
    for phrase in (
      "On main, merge the branch with `git merge --ff-only BRANCH`",
      "ff-only is the contract's default",
      "When main moved after step 3, go back to Land step 3.",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, text)
    self.assertNotIn("--no-ff", self.land_section())

  def test_RelayLand_EndRun_ComesBeforeSlicerLand(self) -> None:
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        section = self.land_section(finish)
        self.assertLess(section.index(self.END_RUN), section.index(self.LAND))
        end_text = self.land_text(8, finish)
        self.assertIn("before the `land` command", end_text)
        self.assertIn("refuses (`run_active`) while a run on the slice is not ended", end_text)

  def test_RelayLand_SlicerLand_NamesItsRefusalsAndPrintedCommands(self) -> None:
    text = self.land_text(9)
    for phrase in (
      "On main, run " + self.LAND,
      "It refuses (`not_merged`) unless the branch tip is reachable from HEAD.",
      "It marks the slice done, renders and checks, and prints the git commands left.",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, text)

  def test_RelayLand_Finish_CommitPushOnlyWhenAuthorizedThenRemoveWorktreeAndBranch(self) -> None:
    text = self.land_text(10)
    for phrase in (
      "Commit `.slicer` on main with the command `land` printed",
      "push only when the owner authorizes it",
      "then remove the worktree and the branch with the commands `land` printed.",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, text)
    self.assertLess(text.index("push only"), text.index("remove the worktree"))

  def test_RelayLand_Intro_EndPointCommitStopsAfterCommit(self) -> None:
    section = self.land_section()
    self.assertIn(
      "The end point `commit` stops after step 2 and then ends the run as in step 8; "
      "`landed` runs every step.",
      section,
    )

  def test_RelayRecord_EndRun_AtTheEndPointNeverWhileAStepWaits(self) -> None:
    flat = " ".join(ai.relay_text().split())
    for phrase in (
      "End the run when its end point is reached, or when the relay or the owner stops it, with "
      "`slicer relay-run end RUN --outcome pass --close-steps done` (or `--outcome fail` or "
      "`stopped`, and `--close-steps fail` when the steps failed).",
      "For `landed`, that is after the ff-only merge and before the `land` command; see Land. "
      "For `commit`, after the commit on the slice branch.",
      "Never end a run while a step waits for the owner or for approval.",
    ):
      with self.subTest(phrase=phrase):
        self.assertEqual(flat.count(phrase), 1)
    self.assertNotIn("After the finish step, or when the relay stops, end the run", flat)

  def test_RelayRoles_OuterSession_LandsOnlyAfterApproval(self) -> None:
    roles_section = " ".join(ai.relay_text().split("## Roles", 1)[1].split("## Setup", 1)[0].split())
    self.assertIn("lands branches one at a time when the owner authorizes it", roles_section)

  def test_OrchestratorText_HasNoApproveOrLandSteps(self) -> None:
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        text = " ".join(ai.orchestrator_text(finish).split())
        self.assertNotIn("## Land", text)
        for token in ("ff-only", "--waiting-on", "[HUMAN CHECK]", "slicer land"):
          self.assertNotIn(token, text)
        steps = ai.relay_contract(orchestrator=True)["order"]
        self.assertEqual([step["step"] for step in steps], [1, 2, 3, 4])

  def test_AiRelay_Json_LandFollowsOrderAndMatchesTheText(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      self.assertEqual(repo.run("init")[0], 0)
      code, out, err = repo.run("ai", "relay", "--json")
      self.assertEqual((code, err), (0, ""))
      payload = json.loads(out)
      self.assertEqual(
        list(payload),
        ["contract", "pinned", "check_command", "relay", "setup", "order", "land", "report", "outcomes", "stop", "changes",
      "roles", "gate", "finding_kinds", "conformance"],
      )
      self.assertEqual(payload["land"], self.land_steps())
      self.assertEqual(list(payload["land"][0]), ["step", "text"])
      code, out, err = repo.run("ai", "relay-orchestrator", "--json")
      self.assertEqual((code, err), (0, ""))
      self.assertNotIn("land", json.loads(out))
      code, out, err = repo.run("ai", "relay", "--json", "--lean")
      self.assertEqual(json.loads(out)["land"], payload["land"])

  def test_RelayLand_Commands_NamedInFullAndAcceptedByTheParser(self) -> None:
    flat = " ".join(ai.relay_text().split())
    parser = cli.build_parser()
    for command in (
      self.WAIT_APPROVAL, self.END_RUN, self.LAND, "`slicer render`", "`slicer check`",
      "`slicer setup-git`",
    ):
      with self.subTest(command=command):
        self.assertIn(command, flat)
        parser.parse_args(shlex.split(command.strip("`"))[1:])

  def test_RelayContract_ApproveAndLand_NameNoProject(self) -> None:
    self.assertEqual(ai.RELAY_CONTRACT, 10)
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        flat = " ".join(ai.relay_text(finish).split())
        self.assertTrue(flat.startswith("Contract: relay v10"))
        for token in ("ork", "adj", "s118", "s119", "landing.in-ork"):
          self.assertNotRegex(flat.casefold(), r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])")
        if finish == "handoff":
          self.assertNotIn("slicer done", flat)
          self.assertNotIn("run done", flat.casefold())


class RelayLandingDocsTests(unittest.TestCase):
  """docs/landing.md puts the relay flow beside the manual one (s403)."""

  def landing(self) -> str:
    return (Path(__file__).resolve().parents[1] / "docs" / "landing.md").read_text(encoding="utf-8")

  def test_LandingDocs_RelayFlow_FfOnlyDefaultBesideManualNoFf(self) -> None:
    text = " ".join(self.landing().split())
    self.assertIn("## Landing a relay slice", self.landing())
    for phrase in (
      "`git merge --ff-only feature/<id>-<slug>`",
      "ff-only is the relay default",
      "`--no-ff` stays valid for the manual flow above",
      "slicer runs no git command",
      "`slicer relay-run end RUN --outcome pass --close-steps done`, before `slicer land`",
      "`relay-run step RUN --step STEP --phase wait --waiting-on approval`",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, text)

  def test_LandingDocs_RelayFlow_EndPointsEndTheRunAndOnlyBriefedCommitBeforeApproval(self) -> None:
    text = " ".join(self.landing().split())
    for phrase in (
      "`commit` stops after step 2 and then ends the run as in step 8",
      "With the end point `finish`, the run ends at the finish step with `relay-run end`",
      "Before anything is merged or landed, the owner approves; the only commit before that is the slice "
      "commit the orchestrator's brief allows.",
      "Commit on the slice branch what the worktree still holds: the human-check notes, and the finish "
      "step's state when the orchestrator did not commit it.",
      "On approval, the outer session records `relay-run step RUN --step STEP --phase start` to end the wait.",
      "`slicer config started_status` names it",
      "go back to Land step 3",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, text)
    self.assertNotIn("before anything is committed", text)

  def test_AgentsDocs_And_WorkingWithAgents_ApprovalMatchesTheContract(self) -> None:
    docs = Path(__file__).resolve().parents[1] / "docs"
    agents = " ".join((docs / "agents.md").read_text(encoding="utf-8").split())
    working = " ".join((docs / "working-with-agents.md").read_text(encoding="utf-8").split())
    self.assertIn("before anything is merged or landed (the only commit before it is the slice commit", agents)
    self.assertIn("goes back to `started_status`", agents)
    self.assertIn("before anything is merged or landed (the orchestrator's own slice commit comes before it)", working)
    self.assertIn("lands, as far as your end point allows, in the order of", working)
    self.assertNotIn("before anything is committed or landed", agents + working)

  def test_LandingDocs_ManualFlow_StillMergesNoFf(self) -> None:
    text = self.landing()
    self.assertIn("merge into `main` with `git merge --no-ff`", " ".join(text.split()))
    self.assertIn("A failed review is `slicer reject <ID>", text)

  def test_LandingDocs_RelayFlow_HumanChecksWarnAndDoNotBlock(self) -> None:
    text = " ".join(self.landing().split())
    for phrase in (
      "`[HUMAN CHECK]`",
      "`--kind human-check`",
      "`note_kinds`",
      "does not block landing",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, text)


class RelayOwnerQuestionContractTests(unittest.TestCase):
  """The contract text for an owner question that waits in the ledger (s396). It is part of
  the v9 bundle that s397 numbers."""

  ASK = (
    "`slicer relay-run ask RUN --step STEP --title TITLE --option LABEL:SUMMARY "
    "--option LABEL:SUMMARY --recommend LABEL --blocker --key AREA.TOPIC --reasoning TEXT`"
  )
  ANSWER = "`slicer relay-run answer RUN QUESTION_ID --option LABEL --why TEXT --by NAME`"
  ANSWER_TEXT = "`slicer relay-run answer RUN QUESTION_ID --text TEXT`"
  GET = "`slicer relay-run answer RUN QUESTION_ID --get`"
  WAITING = "`waiting on: owner QUESTION_ID`"

  def rule(self, finish: str, name: str) -> str:
    rules = ai.orchestrator_text(finish).split("## Rules\n\n", 1)[1].split("\n## ", 1)[0]
    bullet = next((b for b in rules.split("\n- ") if b.lstrip("- ").startswith(name)), "")
    self.assertTrue(bullet, f"no rule starting {name!r}")
    return " ".join(bullet.split())

  def outer_step(self, number: int) -> str:
    return next(step["text"] for step in ai.relay_contract()["order"] if step["step"] == number)

  def test_OrchestratorRules_OwnerQuestions_AskWaitAndResumeOnTheAnswer(self) -> None:
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        rule = self.rule(finish, "Owner questions")
        for phrase in (
          "an owner decision marked `blocker` that you cannot go on without is asked in the ledger",
          self.ASK,
          "STEP is your own step id",
          "the fields are those of the run report's `owner needed` line",
          "End your turn with the last line " + self.WAITING,
          "The outer session records the answer with "
          "`slicer relay-run answer RUN QUESTION_ID --option LABEL` (or `--text TEXT`) and resumes you with it",
          "to read it again, run " + self.GET,
          "A non-blocker owner question stays in the run report",
          "Do not poll the ledger for the answer",
        ):
          self.assertIn(phrase, rule)

  def test_OrchestratorRules_StopForBlocker_AsksUnlessItCannotWait(self) -> None:
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        rule = self.rule(finish, "Stop and return without asking the owner")
        for phrase in (
          "A `blocker` owner finding stops the relay: ask it as Owner questions says",
          "return the report with OUTCOME stopped only when you cannot wait for the answer",
        ):
          self.assertIn(phrase, rule)
        self.assertTrue(
          rule.startswith(
            "Stop and return without asking the owner when the same slice fails its gate three "
            "times or the spec is ambiguous or wrong. An `owner` finding"
          ),
          rule,
        )
        self.assertNotIn("an owner decision marked `blocker` is needed", rule)

  def test_OrchestratorRules_Waiting_NamesTheOwnerHandBack(self) -> None:
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        rule = self.rule(finish, "Waiting")
        self.assertIn("`waiting on: ROLE AGENT-ID`", rule)
        self.assertIn(
          "When you wait for the owner's answer to a question you asked, it is "
          "`waiting on: owner QUESTION_ID`.", rule,
        )

  def test_OrchestratorRules_OwnerAnswers_CoverAnAnswerToItsOwnQuestion(self) -> None:
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        rule = self.rule(finish, "Owner answers")
        self.assertIn("an answer the outer session pastes in on resume is the owner's", rule)
        self.assertIn("That includes the answer to a question you asked with `relay-run ask`.", rule)

  def test_RelayOrder_Step3_OwnerHandBackIsAWaitTheOuterSessionAnswers(self) -> None:
    step = self.outer_step(3)
    for phrase in (
      "A hand-back whose last line is " + self.WAITING + " is a wait for the owner",
      "a question the orchestrator asked with `relay-run ask`",
      "`slicer relay-run list RUN` shows the question",
      "record the answer with " + self.ANSWER,
      "(" + self.ANSWER_TEXT + " for a free-text answer)",
      "If the question is already answered (a dashboard recorded it), read it with " + self.GET
      + " and resume the orchestrator with it.",
      "which ends the step's owner wait",
      "then record the note in the worktree and resume the orchestrator with the answer pasted in",
    ):
      with self.subTest(phrase=phrase):
        self.assertIn(phrase, step)
    self.assertIn("An owner answer given while the run waits goes in a note on the item in the worktree", step)

  def test_RelayOrder_Step4_DoesNotStopAnOrchestratorThatWaitsOnTheOwner(self) -> None:
    step = self.outer_step(4)
    self.assertIn("names no running role and no unanswered owner question", step)
    self.assertIn("a question whose answer the orchestrator was already resumed with", step)
    self.assertNotIn("has already answered", step)

  def test_RelayOrder_OwnerAnswersParagraph_RecordsTheAnswerInTheLedgerToo(self) -> None:
    for finish in ("done", "handoff"):
      with self.subTest(finish=finish):
        order = " ".join(ai.relay_text(finish).split("## Order", 1)[1].split("\n## ", 1)[0].split())
        self.assertIn(
          "When the orchestrator waits on a question of its own (`waiting on: owner QUESTION_ID`), "
          "the outer session also records the answer in the ledger with " + self.ANSWER,
          order,
        )

  def test_RelayConformance_StopRules_NameTheAskWaitResumeException(self) -> None:
    for command in ("relay", "relay-orchestrator"):
      with self.subTest(command=command):
        item = next(
          item["text"] for item in ai.relay_contract(orchestrator=command != "relay")["conformance"]
          if item["key"] == "stop_rules"
        )
        for phrase in (
          "do not ask the owner",
          "A `blocker` owner question that can wait for its answer is the exception",
          "record it with `relay-run ask`",
          "end the turn with `waiting on: owner QUESTION_ID`",
          "go on only from the answer the outer session records and resumes you with",
        ):
          self.assertIn(phrase, item)

  def test_RelayContract_Commands_AreNamedInFullAndAcceptedByTheParser(self) -> None:
    parser = cli._cached_parser()
    texts = {
      "orchestrator": " ".join(ai.orchestrator_text().split()),
      "relay": " ".join(ai.relay_text().split()),
    }
    wanted = {
      "orchestrator": (self.ASK, self.GET, "`slicer relay-run answer RUN QUESTION_ID --option LABEL`"),
      "relay": (self.ANSWER, self.ANSWER_TEXT, "`slicer relay-run list RUN`"),
    }
    for label, commands in wanted.items():
      for command in commands:
        with self.subTest(label=label, command=command):
          self.assertIn(command, texts[label])
          argv = shlex.split(command.strip("`"))[1:]
          parser.parse_args(argv)
          self.assertEqual(cli._unknown_flags(parser, argv), [])

  def test_RelayContract_Bundle_IsV10WithItsChangeEntry(self) -> None:
    self.assertEqual(ai.RELAY_CONTRACT, 10)
    self.assertEqual([version for version, _ in ai.RELAY_CHANGES], [5, 6, 7, 8, 9, 10])
    self.assertEqual(ai.contract_line(), "relay v10")

  def test_RelayContract_OwnerQuestion_NamesNoProject(self) -> None:
    for text in (ai.relay_text(), ai.orchestrator_text()):
      flat = " ".join(text.split()).casefold()
      for token in ("ork", "adj 165"):
        self.assertNotRegex(flat, r"(?<![a-z0-9])" + re.escape(token) + r"(?![a-z0-9])")
