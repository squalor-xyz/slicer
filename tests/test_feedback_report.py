"""`slicer feedback-report` files local feedback entries as GitHub issues through `gh` (s231)."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import support
from slicer import config, feedback, github
from slicer.config import CONFIG_NAME, Config
from slicer.errors import ConfigError, StateError

STAMP = "2026-10-07T10:00:00Z"
LOG = ".slicer/feedback.md"
SIDECAR = ".slicer/feedback-reported.json"
REPO = "octo/widgets"
# Patching `slicer.github.subprocess.run` patches the shared module, so git
# discovery reaches the fake too; anything that is not `gh` goes through.
REAL_RUN = subprocess.run


class FakeGh:
  """Stands in for `subprocess.run` inside `slicer.github`; keeps argv and body text."""

  def __init__(self, outcomes: list[object] | None = None) -> None:
    self.outcomes = list(outcomes or [])
    self.calls: list[list[str]] = []
    self.bodies: list[str] = []
    self.number = 0

  def __call__(self, argv: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
    if not argv or argv[0] != "gh":
      return REAL_RUN(argv, *args, **kwargs)
    self.calls.append(list(argv))
    self.bodies.append(Path(argv[-1]).read_text(encoding="utf-8"))
    outcome = self.outcomes.pop(0) if self.outcomes else None
    if isinstance(outcome, BaseException):
      raise outcome
    if isinstance(outcome, subprocess.CompletedProcess):
      return outcome
    self.number += 1
    return subprocess.CompletedProcess(
      argv, 0, f"https://github.com/{argv[4]}/issues/{self.number}\n", ""
    )


def done(code: int, out: str = "", err: str = "") -> subprocess.CompletedProcess[str]:
  return subprocess.CompletedProcess(["gh"], code, out, err)


class ReportBase(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    self.addCleanup(repo.close)
    self.assertEqual(repo.run("init")[0], 0)
    return repo

  def add(self, repo: support.TempRepo, kind: str, text: str, *extra: str) -> None:
    with patch("slicer.feedback._now", return_value=STAMP):
      code, _, err = repo.run("feedback", "--kind", kind, "--text", text, *extra)
    self.assertEqual(code, 0, err)

  def report(self, repo: support.TempRepo, gh: FakeGh, *argv: str) -> tuple[int, str, str]:
    with patch("slicer.github.subprocess.run", gh):
      return repo.run("feedback-report", *argv)

  def sidecar(self, repo: support.TempRepo) -> dict:
    return json.loads(repo.read(SIDECAR))

  def tracked(self, repo: support.TempRepo) -> dict[str, bytes]:
    base = repo.root / ".slicer"
    local = {feedback.LOG_NAME, feedback.SIDECAR_NAME, feedback.GITIGNORE_NAME, "lock"}
    return {str(p.relative_to(base)): p.read_bytes() for p in base.rglob("*")
            if p.is_file() and p.name not in local}


class ReportSelectionTests(ReportBase):
  def test_Yes_FilesOneIssuePerBugAndFeatureInTheExactForm(self) -> None:
    repo = self.repo()
    self.add(repo, "bug", "first bug\nmore detail", "--item", "s7")
    self.add(repo, "friction", "slow thing")
    self.add(repo, "feature", "a feature")
    gh = FakeGh()
    code, out, err = self.report(repo, gh, "--repo", REPO, "--yes")
    self.assertEqual(code, 0, err)
    self.assertEqual(len(gh.calls), 2)
    for call, title in zip(gh.calls, ("[bug] first bug", "[feature] a feature")):
      self.assertEqual(call[:7], ["gh", "issue", "create", "--repo", REPO, "--title", title])
      self.assertEqual(call[7], "--body-file")
      self.assertEqual(len(call), 9)
      self.assertFalse(Path(call[8]).exists(), "the body file is removed after the call")
      self.assertNotEqual(Path(call[8]).resolve(), (repo.root / LOG).resolve())
    self.assertEqual(
      gh.bodies[0],
      f"Kind: bug\nAt: {STAMP}\nItem: s7\n\nfirst bug\nmore detail\n\n"
      "Filed by `slicer feedback-report` from a local `slicer feedback` entry.\n",
    )
    self.assertNotIn("Item:", gh.bodies[1])
    self.assertIn(f"https://github.com/{REPO}/issues/1", out)

  def test_Kind_FrictionOnlyWhenAskedAndRepeatableNarrows(self) -> None:
    repo = self.repo()
    self.add(repo, "bug", "b")
    self.add(repo, "friction", "f")
    self.add(repo, "feature", "x")
    gh = FakeGh()
    code, _, err = self.report(repo, gh, "--repo", REPO, "--yes", "--kind", "friction", "--kind", "bug")
    self.assertEqual(code, 0, err)
    self.assertEqual([c[6] for c in gh.calls], ["[bug] b", "[friction] f"])

  def test_Item_KeepsOnlyEntriesWithThatExactString(self) -> None:
    repo = self.repo()
    self.add(repo, "bug", "tagged", "--item", "s7")
    self.add(repo, "bug", "other", "--item", "S7")
    self.add(repo, "bug", "untagged")
    gh = FakeGh()
    code, _, err = self.report(repo, gh, "--repo", REPO, "--yes", "--item", "s7")
    self.assertEqual(code, 0, err)
    self.assertEqual([c[6] for c in gh.calls], ["[bug] tagged"])

  def test_Title_IsTruncatedToTheLimit(self) -> None:
    entry = {"kind": "feature", "at": STAMP, "text": "w" * 300 + "\nsecond line"}
    title = feedback.issue_title(entry)
    self.assertEqual(len(title), feedback.TITLE_LIMIT)
    self.assertTrue(title.startswith("[feature] www"))
    self.assertEqual(feedback.issue_title({"kind": "bug", "at": STAMP, "text": "  short  \nx"}),
      "[bug] short")

  def test_UnknownKind_IsUsageAndCallsNothing(self) -> None:
    repo = self.repo()
    self.add(repo, "bug", "b")
    gh = FakeGh()
    code, _, _ = self.report(repo, gh, "--repo", REPO, "--yes", "--kind", "idea")
    self.assertEqual(code, 2)
    self.assertEqual(gh.calls, [])


class ReportModeTests(ReportBase):
  def test_DryRun_PrintsIssuesCallsNothingAndWritesNoSidecar(self) -> None:
    repo = self.repo()
    self.add(repo, "bug", "dry bug")
    gitignore = repo.read(".slicer/.gitignore")
    gh = FakeGh()
    code, out, err = self.report(repo, gh, "--repo", REPO, "--dry-run", "--json")
    self.assertEqual(code, 0, err)
    self.assertEqual(gh.calls, [])
    self.assertFalse((repo.root / SIDECAR).exists())
    self.assertEqual(repo.read(".slicer/.gitignore"), gitignore)
    payload = json.loads(out)
    self.assertEqual(payload["repo"], REPO)
    self.assertTrue(payload["dry_run"])
    self.assertEqual([i["title"] for i in payload["issues"]], ["[bug] dry bug"])
    self.assertIn("dry bug", payload["issues"][0]["body"])

  def test_NeitherOrBothModes_AreUsage(self) -> None:
    repo = self.repo()
    self.add(repo, "bug", "b")
    for argv in ((), ("--dry-run", "--yes")):
      gh = FakeGh()
      code, out, _ = self.report(repo, gh, "--repo", REPO, *argv, "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out)["error"]["code"], "usage")
      self.assertEqual(gh.calls, [])

  def test_AbsentLogOrEmptySelection_ExitsZeroWithNothingToFile(self) -> None:
    repo = self.repo()
    gh = FakeGh()
    code, out, err = self.report(repo, gh, "--repo", REPO, "--yes")
    self.assertEqual(code, 0, err)
    self.assertIn("nothing to file", out)
    self.add(repo, "friction", "only friction")
    code, out, err = self.report(repo, gh, "--repo", REPO, "--yes", "--json", "--lean")
    self.assertEqual(code, 0, err)
    self.assertEqual(json.loads(out), {"repo": REPO, "dry_run": False, "issues": [], "unmarked": []})
    self.assertEqual(gh.calls, [])
    self.assertFalse((repo.root / SIDECAR).exists())


class ReportRepoTests(ReportBase):
  def test_BadRepo_IsUsageAndCallsNothing(self) -> None:
    repo = self.repo()
    self.add(repo, "bug", "b")
    for bad in ("octo", "octo/widgets/x", "../widgets", "octo/..", "oc to/w", "-x/y", "octo/"):
      gh = FakeGh()
      code, out, _ = self.report(repo, gh, "--repo", bad, "--yes", "--json")
      self.assertEqual(code, 2, bad)
      self.assertEqual(json.loads(out)["error"]["code"], "usage", bad)
      self.assertEqual(gh.calls, [], bad)

  def test_NoRepoAndNoConfig_IsUsageWithNoDefault(self) -> None:
    repo = self.repo()
    self.add(repo, "bug", "b")
    gh = FakeGh()
    code, out, _ = self.report(repo, gh, "--yes", "--json")
    self.assertEqual(code, 2)
    self.assertEqual(json.loads(out)["error"]["code"], "usage")
    self.assertEqual(gh.calls, [])

  def test_ConfigIssuesRepo_IsTheDefaultAndRepoOverridesIt(self) -> None:
    repo = self.repo()
    path = repo.root / ".slicer" / CONFIG_NAME
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["issues_repo"] = REPO
    path.write_text(json.dumps(raw), encoding="utf-8")
    self.add(repo, "bug", "b")
    gh = FakeGh()
    self.assertEqual(self.report(repo, gh, "--dry-run")[0], 0)
    code, out, err = self.report(repo, gh, "--yes", "--repo", "other/place")
    self.assertEqual(code, 0, err)
    self.assertEqual(gh.calls[0][4], "other/place")


class ReportSidecarTests(ReportBase):
  def test_Yes_RecordsHashRepoIssueUrlAndSkipsOnRerun(self) -> None:
    repo = self.repo()
    self.add(repo, "bug", "b1")
    self.add(repo, "feature", "f1")
    log = (repo.root / LOG).read_bytes()
    gh = FakeGh()
    self.assertEqual(self.report(repo, gh, "--repo", REPO, "--yes")[0], 0)
    data = self.sidecar(repo)
    self.assertEqual(list(data), ["version", "reported"])
    self.assertEqual([list(r) for r in data["reported"]], [["entry", "repo", "issue", "url"]] * 2)
    hashes = [h for h, _ in feedback.hashed(repo.root)]
    self.assertEqual([r["entry"] for r in data["reported"]], hashes)
    self.assertEqual([r["issue"] for r in data["reported"]], [1, 2])
    self.assertEqual(data["reported"][0]["url"], f"https://github.com/{REPO}/issues/1")
    self.assertEqual((repo.root / LOG).read_bytes(), log)
    self.assertIn(feedback.SIDECAR_NAME, repo.read(".slicer/.gitignore").splitlines())
    again = FakeGh()
    code, out, _ = self.report(repo, again, "--repo", REPO, "--yes")
    self.assertEqual(code, 0)
    self.assertEqual(again.calls, [])
    self.assertIn("nothing to file", out)
    elsewhere = FakeGh()
    self.assertEqual(self.report(repo, elsewhere, "--repo", "other/place", "--yes")[0], 0)
    self.assertEqual(len(elsewhere.calls), 2, "the skip is per repo")

  def test_HandEditedEntry_HashesDifferentlyAndIsFiledAgain(self) -> None:
    repo = self.repo()
    self.add(repo, "bug", "original")
    self.assertEqual(self.report(repo, FakeGh(), "--repo", REPO, "--yes")[0], 0)
    repo.write(LOG, repo.read(LOG).replace("original", "edited"))
    gh = FakeGh()
    self.assertEqual(self.report(repo, gh, "--repo", REPO, "--yes")[0], 0)
    self.assertEqual([c[6] for c in gh.calls], ["[bug] edited"])

  def test_EntryChangedWhileSent_IsNotMarked(self) -> None:
    repo = self.repo()
    self.add(repo, "bug", "racing")
    gh = FakeGh()
    original = gh.__call__

    def edit_then_file(argv: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
      if argv and argv[0] == "gh":
        repo.write(LOG, repo.read(LOG).replace("racing", "raced"))
      return original(argv, *args, **kwargs)

    with patch("slicer.github.subprocess.run", edit_then_file):
      code, out, err = repo.run("feedback-report", "--repo", REPO, "--yes", "--json")
    self.assertEqual(code, 0, err)
    self.assertIn("not marked", err)
    payload = json.loads(out)
    self.assertEqual(payload["issues"], [])
    self.assertEqual([i["issue"] for i in payload["unmarked"]], [1])
    self.assertFalse((repo.root / SIDECAR).exists())

  def test_MidBatchFailure_RecordsEarlierCreatesAndNamesTheEntry(self) -> None:
    repo = self.repo()
    self.add(repo, "bug", "one")
    self.add(repo, "bug", "two")
    self.add(repo, "bug", "three")
    gh = FakeGh([None, done(1, err="HTTP 401: Bad credentials")])
    code, out, err = self.report(repo, gh, "--repo", REPO, "--yes", "--json")
    self.assertEqual(code, 2)
    error = json.loads(out)["error"]
    self.assertEqual(error["code"], "external")
    self.assertIn("entry 2 of 3", error["message"])
    self.assertIn("Bad credentials", error["message"])
    self.assertIn("gh auth status", error["message"])
    self.assertEqual(len(gh.calls), 2)
    self.assertEqual([r["issue"] for r in self.sidecar(repo)["reported"]], [1])

  def test_SuccessWithoutAnIssueUrl_IsExternalAndUnmarked(self) -> None:
    repo = self.repo()
    self.add(repo, "bug", "b")
    for stdout in ("created\n", "https://github.com/someone/else/issues/3\n"):
      gh = FakeGh([done(0, out=stdout)])
      code, out, _ = self.report(repo, gh, "--repo", REPO, "--yes", "--json")
      self.assertEqual(code, 2)
      error = json.loads(out)["error"]
      self.assertEqual(error["code"], "external")
      self.assertIn("duplicate", error["message"])
      self.assertFalse((repo.root / SIDECAR).exists())

  def test_ReportLeavesRoadmapStateAndRenderAlone(self) -> None:
    repo = self.repo()
    self.assertEqual(repo.run("add", "A title", "--render")[0], 0)
    self.add(repo, "bug", "b", "--item", "s1")
    before = self.tracked(repo)
    self.assertEqual(self.report(repo, FakeGh(), "--repo", REPO, "--dry-run")[0], 0)
    self.assertEqual(self.report(repo, FakeGh(), "--repo", REPO, "--yes")[0], 0)
    self.assertEqual(self.tracked(repo), before)
    code, _, err = repo.run("check")
    self.assertEqual(code, 0, err)


class MissingGhTests(ReportBase):
  def test_MissingGh_IsExternalWritesNothingAndLeavesOtherCommandsAlone(self) -> None:
    repo = self.repo()
    self.assertEqual(repo.run("render")[0], 0)
    self.add(repo, "bug", "b")
    slicer_dir = repo.root / ".slicer"
    before = {p: p.read_bytes() for p in slicer_dir.rglob("*") if p.is_file() and p.name != "lock"}
    gh = FakeGh([FileNotFoundError("gh")])
    code, out, _ = self.report(repo, gh, "--repo", REPO, "--yes", "--json")
    self.assertEqual(code, 2)
    error = json.loads(out)["error"]
    self.assertEqual(error["code"], "external")
    self.assertIn("cli.github.com", error["message"])
    after = {p: p.read_bytes() for p in slicer_dir.rglob("*") if p.is_file() and p.name != "lock"}
    self.assertEqual(after, before)
    with patch("slicer.github.subprocess.run", FakeGh([FileNotFoundError("gh")] * 9)):
      self.assertEqual(repo.run("feedback", "--kind", "bug", "--text", "still works")[0], 0)
      self.assertEqual(repo.run("feedback")[0], 0)
      self.assertEqual(repo.run("check")[0], 0)
      self.assertEqual(repo.run("init", "--force")[0], 0)


class GithubAllowlistTests(unittest.TestCase):
  GOOD = ("issue", "create", "--repo", REPO, "--title", "[bug] t", "--body-file", "/tmp/b.md")

  def test_Run_RefusesEveryOtherArgv(self) -> None:
    refused = [
      (),
      ("issue", "list", "--repo", REPO),
      ("issue", "create", "--repo", REPO, "--title", "t"),
      ("issue", "create", "--repo", REPO, "--title", "t", "--body", "x"),
      ("issue", "create", "--repo", REPO, "--title", "t", "--body-file", "p", "--label", "x"),
      ("issue", "create", "--title", "t", "--repo", REPO, "--body-file", "p"),
      ("issue", "create", "--repo", "a/b/c", "--title", "t", "--body-file", "p"),
      ("issue", "create", "--repo", REPO, "--title", "--web", "--body-file", "p"),
      ("pr", "create", "--repo", REPO, "--title", "t", "--body-file", "p"),
      ("api", "repos/x", "--method", "DELETE", "--title", "t", "--body-file", "p"),
    ]
    with patch("slicer.github.subprocess.run", side_effect=AssertionError("gh was run")):
      for argv in refused:
        with self.assertRaises(StateError, msg=argv):
          github._run(argv)

  def test_Run_ExecsArgvListWithoutAShell(self) -> None:
    with patch("slicer.github.subprocess.run", return_value=done(0)) as run:
      github._run(self.GOOD)
    args, kwargs = run.call_args
    self.assertEqual(args[0], ["gh", *self.GOOD])
    self.assertNotIn("shell", kwargs)

  def test_NonzeroExit_IsExternalAndNamesAuth(self) -> None:
    with patch("slicer.github.subprocess.run", return_value=done(4, err="not logged in")):
      with self.assertRaises(StateError) as cm:
        github.create_issue(REPO, "[bug] t", "body")
    self.assertEqual(cm.exception.code, "external")
    self.assertIn("gh auth", str(cm.exception))


class IssuesRepoConfigTests(unittest.TestCase):
  def test_OlderConfig_LoadsEmptyAndIsNotRestampedOnRead(self) -> None:
    repo = support.TempRepo()
    self.addCleanup(repo.close)
    self.assertEqual(repo.run("init")[0], 0)
    path = repo.root / ".slicer" / CONFIG_NAME
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["version"] = 4
    raw.pop("issues_repo")
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    before = path.read_bytes()
    self.assertEqual(Config.load(path).issues_repo, "")
    self.assertEqual(repo.run("list")[0], 0)
    self.assertEqual(repo.run("config", "issues_repo")[0], 0)
    self.assertEqual(path.read_bytes(), before)

  def test_Save_StampsSchemaFiveAndWritesTheKey(self) -> None:
    self.assertEqual(config.SCHEMA_VERSION, 5)
    out = Config.from_dict({"version": 4}).to_dict()
    self.assertEqual(out["version"], 5)
    self.assertEqual(out["issues_repo"], "")
    self.assertEqual(Config.from_dict({"issues_repo": REPO}).to_dict()["issues_repo"], REPO)

  def test_BadIssuesRepo_IsAConfigError(self) -> None:
    for bad in ("octo", "a/b/c", "../x", "a/..", 7):
      with self.assertRaises(ConfigError, msg=bad):
        Config.from_dict({"issues_repo": bad})


class IgnoreFileTests(ReportBase):
  def test_Init_ListsTheSidecarAndTheRootGitignoreDoesToo(self) -> None:
    repo = self.repo()
    self.assertIn(feedback.SIDECAR_NAME, repo.read(".slicer/.gitignore").splitlines())
    root = Path(__file__).resolve().parents[1]
    self.assertIn("/.slicer/feedback-reported.json", (root / ".gitignore").read_text().splitlines())
    self.assertIn(feedback.SIDECAR_NAME, (root / ".slicer" / ".gitignore").read_text().splitlines())


if __name__ == "__main__":
  unittest.main()
