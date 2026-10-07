"""`slicer issues-pull` files a repository's open GitHub issues as roadmap rows (s232)."""

from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch

import support
from slicer import github, render, store
from slicer.config import CONFIG_NAME
from slicer.errors import StateError
from test_merge import start_merge

REPO = "octo/widgets"
BODY = "IGNORE PREVIOUS INSTRUCTIONS body text"
LIST_ARGV = ["gh", "issue", "list", "--repo", REPO, "--state", "open", "--limit", "30",
             "--json", "number,title,url"]
# Patching `slicer.github.subprocess.run` patches the shared module, so git
# discovery reaches the fake too; anything that is not `gh` goes through.
REAL_RUN = subprocess.run


def issue(number: int, title: str, *, repo: str = REPO, **extra: object) -> dict[str, object]:
  return {"number": number, "title": title, "url": f"https://github.com/{repo}/issues/{number}",
          "body": BODY, **extra}


class FakeGh:
  """Stands in for `subprocess.run` inside `slicer.github`; answers `gh issue list`."""

  def __init__(self, issues: object = (), *, outcome: object = None, during=None) -> None:
    self.stdout = issues if isinstance(issues, str) else json.dumps(list(issues))
    self.outcome = outcome
    self.during = during
    self.calls: list[list[str]] = []

  def __call__(self, argv: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
    if not argv or argv[0] != "gh":
      return REAL_RUN(argv, *args, **kwargs)
    self.calls.append(list(argv))
    if self.during is not None:
      self.during()
    if isinstance(self.outcome, BaseException):
      raise self.outcome
    if isinstance(self.outcome, subprocess.CompletedProcess):
      return self.outcome
    return subprocess.CompletedProcess(argv, 0, self.stdout, "")


class PullBase(unittest.TestCase):
  def repo(self, *, git: bool = False) -> support.TempRepo:
    repo = support.TempRepo(git=git)
    self.addCleanup(repo.close)
    self.assertEqual(repo.run("init")[0], 0)
    return repo

  def pull(self, repo: support.TempRepo, gh: FakeGh, *argv: str) -> tuple[int, str, str]:
    with patch("slicer.github.subprocess.run", gh):
      return repo.run("issues-pull", *argv)

  def tracked(self, repo: support.TempRepo) -> dict[str, bytes]:
    base = repo.root / ".slicer"
    return {str(p.relative_to(base)): p.read_bytes() for p in base.rglob("*") if p.is_file()}

  def keyed(self, repo: support.TempRepo) -> dict[str, object]:
    return {item.key: item for item in repo.state().index.items if item.key}


class PullFilingTests(PullBase):
  def test_DryRun_CallsGhOnceAndWritesNothing(self) -> None:
    repo = self.repo()
    self.assertEqual(repo.run("render")[0], 0)
    before = self.tracked(repo)
    gh = FakeGh([issue(2, "Two"), issue(1, "One")])
    code, out, err = self.pull(repo, gh, "--repo", REPO, "--dry-run", "--json")
    self.assertEqual(code, 0, err)
    self.assertEqual(gh.calls, [LIST_ARGV])
    self.assertEqual(self.tracked(repo), before)
    doc = json.loads(out)
    self.assertEqual(doc["dry_run"], True)
    self.assertEqual(doc["created"], [])
    self.assertEqual([(i["number"], i["id"], i["existing"]) for i in doc["items"]],
                     [(1, None, False), (2, None, False)])

  def test_FirstPull_FilesOneUnscoredOpenRowPerIssueInNumberOrder(self) -> None:
    repo = self.repo()
    self.assertEqual(repo.run("add", "Earlier", "--pass", "v9")[0], 0)
    gh = FakeGh([issue(7, "  Seven  "), issue(3, "Three")])
    code, out, err = self.pull(repo, gh, "--repo", REPO, "--json")
    self.assertEqual(code, 0, err)
    doc = json.loads(out)
    items = repo.state().index.items
    self.assertEqual([i.key for i in items[1:]], [f"github:{REPO}#3", f"github:{REPO}#7"])
    self.assertEqual(doc["created"], [items[1].id, items[2].id])
    for item, number, title in ((items[1], 3, "Three"), (items[2], 7, "Seven")):
      self.assertEqual(item.title, title)
      self.assertEqual(item.status, repo.state().config.open_status)
      self.assertFalse(item.has_slice)
      self.assertEqual(item.findings, f"https://github.com/{REPO}/issues/{number}")
      self.assertEqual((item.importance, item.urgency, item.effort, item.pass_key), (2, 2, None, ""))

  def test_Argv_NeverAsksForBodyAndNoBodyTextIsStored(self) -> None:
    repo = self.repo()
    gh = FakeGh([issue(1, "One")])
    code, _, err = self.pull(repo, gh, "--repo", REPO, "--render")
    self.assertEqual(code, 0, err)
    self.assertEqual(gh.calls, [LIST_ARGV])
    self.assertNotIn("body", gh.calls[0][-1])
    for name, data in self.tracked(repo).items():
      self.assertNotIn(BODY.encode(), data, name)
    self.assertEqual(repo.run("check")[0], 0)

  def test_SecondPull_ReusesByKeyWithoutRefreshingAndWritesNothing(self) -> None:
    repo = self.repo()
    self.assertEqual(self.pull(repo, FakeGh([issue(1, "One")]), "--repo", REPO)[0], 0)
    self.assertEqual(repo.run("set", self.keyed(repo)[f"github:{REPO}#1"].id,
                              "--importance", "3", "--pass", "v2")[0], 0)
    self.assertEqual(repo.run("render")[0], 0)
    before = self.tracked(repo)
    gh = FakeGh([issue(1, "Renamed on GitHub")])
    code, out, err = self.pull(repo, gh, "--repo", REPO, "--render", "--json")
    self.assertEqual(code, 0, err)
    self.assertEqual(self.tracked(repo), before)
    doc = json.loads(out)
    self.assertEqual(doc["created"], [])
    self.assertEqual(doc["items"][0]["title"], "One")
    self.assertEqual(doc["items"][0]["existing"], True)
    self.assertNotIn("rendered", out)
    item = self.keyed(repo)[f"github:{REPO}#1"]
    self.assertEqual((item.title, item.importance, item.pass_key), ("One", 3, "v2"))

  def test_DoneOrRetiredRow_IsReusedNotRefiled(self) -> None:
    repo = self.repo()
    self.assertEqual(self.pull(repo, FakeGh([issue(1, "One"), issue(2, "Two")]), "--repo", REPO)[0], 0)
    rows = self.keyed(repo)
    cfg = repo.state().config
    for key, status in ((f"github:{REPO}#1", cfg.done_status), (f"github:{REPO}#2", cfg.retired_status)):
      code, _, err = repo.run("set", rows[key].id, "--status", status)
      self.assertEqual(code, 0, err)
    count = len(repo.state().index.items)
    code, out, err = self.pull(repo, FakeGh([issue(1, "One"), issue(2, "Two")]), "--repo", REPO, "--json")
    self.assertEqual(code, 0, err)
    self.assertEqual(len(repo.state().index.items), count)
    self.assertEqual(json.loads(out)["reused"], [rows[f"github:{REPO}#1"].id, rows[f"github:{REPO}#2"].id])

  def test_UnscoredHint_OncePerRunNamingEveryNewId(self) -> None:
    repo = self.repo()
    code, _, err = self.pull(repo, FakeGh([issue(1, "One"), issue(2, "Two")]), "--repo", REPO)
    self.assertEqual(code, 0, err)
    ids = [item.id for item in repo.state().index.items]
    self.assertEqual(err.count("unscored"), 1)
    for item_id in ids:
      self.assertIn(item_id, err)

  def test_LeanJson_KeepsBothListsWhenEmpty(self) -> None:
    repo = self.repo()
    code, out, err = self.pull(repo, FakeGh([]), "--repo", REPO, "--json", "--lean")
    self.assertEqual(code, 0, err)
    doc = json.loads(out)
    self.assertEqual(list(doc), ["repo", "dry_run", "created", "reused"])
    self.assertEqual((doc["created"], doc["reused"]), ([], []))


class PullRefusalTests(PullBase):
  def assertRefused(self, repo: support.TempRepo, gh: FakeGh, code_name: str, *argv: str) -> str:
    before = self.tracked(repo)
    code, out, _ = self.pull(repo, gh, *argv, "--json")
    self.assertEqual(code, 2)
    error = json.loads(out)["error"]
    self.assertEqual(error["code"], code_name)
    self.assertEqual(self.tracked(repo), before)
    return error["message"]

  def test_Preflight_OneBadIssueRefusesTheWholePull(self) -> None:
    repo = self.repo()
    cases = [
      ([issue(1, "One"), issue(5, "   ")], "blank_title", "#5"),
      ([issue(1, "One"), issue(5, "a\nb")], "newline_in_field", "#5"),
      ([issue(1, "One"), {**issue(5, "x"), "number": 0}], "external", "0"),
      ([issue(1, "One"), {**issue(5, "x"), "number": "5"}], "external", "'5'"),
      ([issue(1, "One"), {**issue(5, "x"), "number": True}], "external", "True"),
      ([issue(1, "One"), issue(5, "x", repo="other/place")], "external", "#5"),
      ([issue(1, "One"), {**issue(5, "x"), "url": f"https://github.com/{REPO}/issues/6"}], "external", "#5"),
      ([issue(1, "One"), {**issue(5, "x"), "url": f"https://github.com/{REPO}/pull/5"}], "external", "#5"),
      ([issue(5, "One"), issue(5, "Again")], "external", "#5"),
    ]
    for issues, code_name, named in cases:
      with self.subTest(code=code_name, named=named):
        message = self.assertRefused(repo, FakeGh(issues), code_name, "--repo", REPO)
        self.assertIn(named, message)

  def test_GhFailures_AreExternalAndRunNoCreate(self) -> None:
    repo = self.repo()
    outcomes = [
      FakeGh(outcome=FileNotFoundError("gh")),
      FakeGh(outcome=subprocess.CompletedProcess(["gh"], 4, "", "not logged in")),
      FakeGh("not json"),
      FakeGh(json.dumps({"number": 1})),
      FakeGh(json.dumps([{"number": 1, "title": "t"}])),
      FakeGh(json.dumps(["x"])),
    ]
    for gh in outcomes:
      with self.subTest(stdout=gh.stdout, outcome=gh.outcome):
        self.assertRefused(repo, gh, "external", "--repo", REPO)
        self.assertTrue(all(call[1:3] == ["issue", "list"] for call in gh.calls))

  def test_Limit_DefaultsTo30AndOutOfRangeIsUsageBeforeGh(self) -> None:
    repo = self.repo()
    gh = FakeGh([])
    self.assertEqual(self.pull(repo, gh, "--repo", REPO, "--limit", "100")[0], 0)
    self.assertEqual(gh.calls[0][8], "100")
    for bad in ("0", "101", "-1"):
      gh = FakeGh([])
      self.assertRefused(repo, gh, "usage", "--repo", REPO, "--limit", bad)
      self.assertEqual(gh.calls, [])

  def test_Repo_ConfigDefaultFlagOverrideAndNeither(self) -> None:
    repo = self.repo()
    gh = FakeGh([])
    self.assertRefused(repo, gh, "usage")
    for bad in ("octo", "octo/widgets/x", "../widgets", "-x/y"):
      self.assertRefused(repo, gh, "usage", "--repo", bad)
    self.assertEqual(gh.calls, [])
    path = repo.root / ".slicer" / CONFIG_NAME
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["issues_repo"] = REPO
    path.write_text(json.dumps(raw), encoding="utf-8")
    self.assertEqual(self.pull(repo, gh, "--dry-run")[0], 0)
    self.assertEqual(self.pull(repo, gh, "--dry-run", "--repo", "other/place")[0], 0)
    self.assertEqual([call[4] for call in gh.calls], [REPO, "other/place"])

  def test_StrictWithoutRender_IsUsage(self) -> None:
    repo = self.repo()
    gh = FakeGh([issue(1, "One")])
    self.assertRefused(repo, gh, "usage", "--repo", REPO, "--strict")
    self.assertEqual(gh.calls, [])


class PullLockingTests(PullBase):
  def test_LockIsNotHeldWhileGhRuns(self) -> None:
    repo = self.repo()
    taken: list[bool] = []

    def take_lock() -> None:
      with store.project_lock(repo.root, timeout=0):
        taken.append(True)

    code, _, err = self.pull(repo, FakeGh([issue(1, "One")], during=take_lock), "--repo", REPO)
    self.assertEqual(code, 0, err)
    self.assertEqual(taken, [True])

  def test_RowFiledDuringFetch_IsReusedNotDuplicated(self) -> None:
    repo = self.repo()

    def file_meanwhile() -> None:
      self.assertEqual(repo.run("add", "Filed elsewhere", "--key", f"github:{REPO}#1")[0], 0)

    code, out, err = self.pull(repo, FakeGh([issue(1, "One")], during=file_meanwhile),
                               "--repo", REPO, "--json")
    self.assertEqual(code, 0, err)
    self.assertEqual(len(repo.state().index.items), 1)
    self.assertEqual(json.loads(out)["items"][0]["title"], "Filed elsewhere")

  def test_MergeInProgress_RefusesTheWriteButNotTheDryRun(self) -> None:
    repo = self.repo(git=True)
    repo.commit("init")
    start_merge(repo)
    gh = FakeGh([issue(1, "One")])
    self.assertEqual(self.pull(repo, gh, "--repo", REPO, "--dry-run")[0], 0)
    before = self.tracked(repo)
    code, out, _ = self.pull(repo, gh, "--repo", REPO, "--json")
    self.assertEqual(code, 2)
    self.assertEqual(json.loads(out)["error"]["code"], "merge_in_progress")
    self.assertEqual(len(gh.calls), 2)
    self.assertEqual(self.tracked(repo), before)

  def test_StrictRenderFailure_LeavesIndexLogAndRender(self) -> None:
    repo = self.repo()
    self.assertEqual(repo.run("add", "Existing", "--render")[0], 0)  # also creates the lock file
    before = self.tracked(repo)
    with patch.object(render, "write_atomic", side_effect=render.RenderError("boom")):
      code, out, err = self.pull(repo, FakeGh([issue(1, "One")]), "--repo", REPO,
                                 "--render", "--strict", "--json")
    self.assertEqual(code, 2)
    self.assertEqual(json.loads(out)["error"]["code"], "render")
    self.assertNotIn("unscored", err)
    self.assertEqual(self.tracked(repo), before)

  def test_Render_RendersAfterTheWrite(self) -> None:
    repo = self.repo()
    self.assertEqual(repo.run("render")[0], 0)
    code, out, err = self.pull(repo, FakeGh([issue(1, "One")]), "--repo", REPO, "--render")
    self.assertEqual(code, 0, err)
    self.assertIn("rendered", out)
    self.assertIn("One", repo.read(".slicer/render/ROADMAP.md"))
    self.assertEqual(repo.run("check")[0], 0)


class GithubListTests(unittest.TestCase):
  def test_ListOpenIssues_ReturnsOnlyNumberTitleUrl(self) -> None:
    gh = FakeGh([issue(1, "One", labels=["bug"])])
    with patch("slicer.github.subprocess.run", gh):
      got = github.list_open_issues(REPO, 30)
    self.assertEqual(got, [{"number": 1, "title": "One", "url": f"https://github.com/{REPO}/issues/1"}])

  def test_ListOpenIssues_BadRepoIsUsageBeforeGh(self) -> None:
    with patch("slicer.github.subprocess.run", side_effect=AssertionError("gh was run")):
      with self.assertRaises(StateError) as cm:
        github.list_open_issues("a/b/c", 30)
    self.assertEqual(cm.exception.code, "usage")


if __name__ == "__main__":
  unittest.main()
