"""`check` says when the only stale render is the roadmap a merge leaves behind.

A landing merges the index and leaves the roadmap render on the current branch's
bytes (the `slicer-generated` driver keeps one side), so the roadmap alone goes stale
until `render` runs. Only that exact shape gets the merge line and `roadmap_only`.
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import support

from slicer import check, render, sync, verify

MERGE_HINT = (
  "only the roadmap render is stale, as a merge leaves it; "
  "any slicer write regenerates it, or run slicer render"
)


class CheckTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.run("add", "a thing")
    repo.run("promote", "S01")
    repo.run("render")
    return repo

  def _tamper(self, repo: support.TempRepo, *rels: str) -> None:
    for rel in rels:
      path = repo.root / ".slicer/render" / rel
      text = path.read_text(encoding="utf-8")
      path.write_text(text + "tampered\n", encoding="utf-8", newline="\n")

  def _age_format(self, repo: support.TempRepo, *rels: str) -> None:
    old = f"Render format: {render.RENDER_FORMAT}."
    for rel in rels:
      path = repo.root / ".slicer/render" / rel
      text = path.read_text(encoding="utf-8")
      self.assertIn(old, text.split("\n", 1)[0])
      path.write_text(text.replace(old, "Render format: 0.", 1), encoding="utf-8", newline="\n")

  def _json(self, repo: support.TempRepo) -> tuple[int, dict]:
    code, out, _ = repo.run("check", "--json")
    return code, json.loads(out)

  def test_Check_OnlyRoadmapStale_SaysMergeLeftIt(self) -> None:
    with self.repo() as repo:
      self._tamper(repo, "ROADMAP.md", "ROADMAP.html")
      code, out, _ = repo.run("check")
      self.assertEqual(code, 1)
      self.assertIn("stale render: ROADMAP.md", out)
      self.assertIn("stale render: ROADMAP.html", out)
      self.assertEqual(out.count(MERGE_HINT), 1, out)

  def test_Check_SliceRenderAlsoStale_NoMergeHint(self) -> None:
    with self.repo() as repo:
      self._tamper(repo, "ROADMAP.md", "slices/S01.md")
      code, out, _ = repo.run("check")
      self.assertEqual(code, 1)
      self.assertIn("stale render: slices/S01.md", out)
      self.assertNotIn("only the roadmap render is stale", out)
      _, report = self._json(repo)
      self.assertEqual(report["stale_render_kind"], "mixed")

  def test_CheckJson_OnlyRoadmapStale_KindRoadmapOnly(self) -> None:
    with self.repo() as repo:
      self._tamper(repo, "ROADMAP.md", "ROADMAP.html")
      code, report = self._json(repo)
      self.assertEqual(code, 1)
      self.assertFalse(report["ok"])
      self.assertEqual(report["stale_render"], ["ROADMAP.html", "ROADMAP.md"])
      self.assertEqual(report["stale_render_kind"], "roadmap_only")

  def test_Check_RenderedAgain_NoMergeHint(self) -> None:
    with self.repo() as repo:
      self._tamper(repo, "ROADMAP.md", "ROADMAP.html")
      self.assertIn(MERGE_HINT, repo.run("check")[1])
      self.assertEqual(repo.run("render")[0], 0)
      code, out, _ = repo.run("check")
      self.assertEqual(code, 0, out)
      self.assertNotIn("only the roadmap render is stale", out)

  def test_CheckJson_RenderedAgain_NoStaleRenderKind(self) -> None:
    with self.repo() as repo:
      self._tamper(repo, "ROADMAP.md", "ROADMAP.html")
      self.assertEqual(self._json(repo)[1]["stale_render_kind"], "roadmap_only")
      self.assertEqual(repo.run("render")[0], 0)
      code, report = self._json(repo)
      self.assertEqual(code, 0)
      self.assertNotIn("stale_render_kind", report)

  def test_Check_RoadmapStaleWithOrphan_NoMergeHint(self) -> None:
    with self.repo() as repo:
      self._tamper(repo, "ROADMAP.md", "ROADMAP.html")
      repo.write(".slicer/render/slices/S99.md", render.banner("x") + "\n\nstale\n")
      code, out, _ = repo.run("check")
      self.assertEqual(code, 1)
      self.assertIn("orphan render: slices/S99.md", out)
      self.assertNotIn("only the roadmap render is stale", out)
      self.assertEqual(self._json(repo)[1]["stale_render_kind"], "mixed")

  def test_Check_RoadmapStaleWithProblem_NoMergeHint(self) -> None:
    with self.repo() as repo:
      self._tamper(repo, "ROADMAP.md", "ROADMAP.html")
      forced = verify.VerifyReport(findings=[verify.Finding("error", "S01", "forced problem")])
      with patch("slicer.verify.offline", return_value=forced):
        code, out, _ = repo.run("check")
        self.assertEqual(code, 1)
        self.assertIn("problem: S01: forced problem", out)
        self.assertNotIn("only the roadmap render is stale", out)
        self.assertEqual(self._json(repo)[1]["stale_render_kind"], "mixed")

  def test_Check_RoadmapStaleWithStaleSync_NoMergeHint(self) -> None:
    with self.repo() as repo:
      self._tamper(repo, "ROADMAP.md", "ROADMAP.html")
      drift = [sync.SyncFinding("docs", "README.md", True, "line is stale")]
      with patch("slicer.sync.apply", return_value=drift):
        code, out, _ = repo.run("check")
        self.assertEqual(code, 1)
        self.assertIn("stale sync: README.md: line is stale", out)
        self.assertNotIn("only the roadmap render is stale", out)
        self.assertEqual(self._json(repo)[1]["stale_render_kind"], "mixed")

  def test_Check_RelayRolesOnlyStale_NoMergeHint(self) -> None:
    with self.repo() as repo:
      repo.run("relay-roles", "init")
      self.assertEqual(repo.run("render")[0], 0)
      self._tamper(repo, "RELAY-ROLES.md")
      code, out, _ = repo.run("check")
      self.assertEqual(code, 1)
      self.assertIn("stale render: RELAY-ROLES.md", out)
      self.assertNotIn("only the roadmap render is stale", out)
      self.assertEqual(self._json(repo)[1]["stale_render_kind"], "mixed")

  def test_Check_RoadmapMissing_NoMergeHint(self) -> None:
    with self.repo() as repo:
      (repo.root / ".slicer/render/ROADMAP.md").unlink()
      code, out, _ = repo.run("check")
      self.assertEqual(code, 1)
      self.assertIn("stale render: ROADMAP.md: file is missing", out)
      self.assertNotIn("only the roadmap render is stale", out)
      self.assertEqual(self._json(repo)[1]["stale_render_kind"], "mixed")

  def test_Check_RoadmapRendererFormat_NoMergeHint(self) -> None:
    with self.repo() as repo:
      self._age_format(repo, "ROADMAP.md", "ROADMAP.html")
      code, out, _ = repo.run("check")
      self.assertEqual(code, 1)
      self.assertIn("stale render: ROADMAP.md: rendered with render format 0", out)
      self.assertNotIn("only the roadmap render is stale", out)
      _, report = self._json(repo)
      self.assertEqual(report["stale_render"], ["ROADMAP.html", "ROADMAP.md"])
      self.assertEqual(report["stale_render_kind"], "mixed")

  def test_Check_RoadmapUnstamped_NoMergeHint(self) -> None:
    with self.repo() as repo:
      path = repo.root / ".slicer/render/ROADMAP.md"
      stamp = f" Render format: {render.RENDER_FORMAT}."
      text = path.read_text(encoding="utf-8")
      self.assertIn(stamp, text.split("\n", 1)[0])
      path.write_text(text.replace(stamp, "", 1), encoding="utf-8", newline="\n")
      code, out, _ = repo.run("check")
      self.assertEqual(code, 1)
      self.assertIn("stale render: ROADMAP.md: the header records no readable render format", out)
      self.assertNotIn("only the roadmap render is stale", out)
      self.assertEqual(self._json(repo)[1]["stale_render_kind"], "mixed")

  def test_Check_RoadmapMixedCauses_NoMergeHint(self) -> None:
    with self.repo() as repo:
      self._tamper(repo, "ROADMAP.md")
      (repo.root / ".slicer/render/ROADMAP.html").unlink()
      code, out, _ = repo.run("check")
      self.assertEqual(code, 1)
      self.assertIn("stale render: ROADMAP.html: file is missing", out)
      self.assertIn("stale render: ROADMAP.md: content differs", out)
      self.assertNotIn("only the roadmap render is stale", out)
      _, report = self._json(repo)
      self.assertEqual(report["stale_render"], ["ROADMAP.html", "ROADMAP.md"])
      self.assertEqual(report["stale_render_kind"], "mixed")


  def test_CheckJson_OnlyOrphan_NoStaleRenderKind(self) -> None:
    with self.repo() as repo:
      repo.write(".slicer/render/slices/S99.md", render.banner("x") + "\n\nstale\n")
      code, out, _ = repo.run("check", "--json")
      report = json.loads(out)
      self.assertEqual(code, 1)
      self.assertEqual(report["stale_render"], [])
      self.assertEqual(report["orphan_render"], ["slices/S99.md"])
      self.assertNotIn("stale_render_kind", report)
      code, out, _ = repo.run("check")
      self.assertEqual(code, 1)
      self.assertIn("orphan render: slices/S99.md", out)
      self.assertNotIn("only the roadmap render is stale", out)


class VagueAcceptanceTests(unittest.TestCase):
  """A Check with no command or `[HUMAN CHECK]`, or Failing tests with no backticked test, warns."""

  CHECK_LINE = (
    "S02 / Check: no command or [HUMAN CHECK] line; say how to tell the slice is done"
  )
  TESTS_LINE = "S02 / Failing tests: no backticked test name"

  def _fresh(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.run("add", "a thing")
    repo.run("add", "another thing")
    return repo

  def _promote(self, repo: support.TempRepo, item_id: str, **sections: str) -> None:
    body = "".join(f"### {name.replace('_', ' ')}\n{text}\n\n" for name, text in sections.items())
    repo.write("draft.md", f"## a thing\n\n{body}")
    code, _, err = repo.run("promote", item_id, "--file", str(repo.root / "draft.md"), "--render")
    self.assertEqual(code, 0, err)

  def _warnings(self, repo: support.TempRepo) -> list[str]:
    code, out, err = repo.run("check", "--json")
    self.assertEqual(code, 0, out + err)
    return json.loads(out)["warnings"]

  def test_check_warns_on_a_check_section_with_no_command_or_human_check(self) -> None:
    with self._fresh() as repo:
      self._promote(repo, "S02", Check="- Tests pass.")
      code, out, _ = repo.run("check")
      self.assertEqual(code, 0, out)
      self.assertIn(f"warn: {self.CHECK_LINE}", out)
      self.assertEqual(self._warnings(repo), [self.CHECK_LINE])

  def test_check_accepts_a_backticked_command_or_a_human_check(self) -> None:
    with self._fresh() as repo:
      self._promote(
        repo, "S01",
        Check="- Tests pass: `python3 -m unittest discover -s tests -t tests` exits 0.",
      )
      self._promote(repo, "S02", Check="- [HUMAN CHECK] Open the page and see the table.")
      self.assertEqual(self._warnings(repo), [])

  def test_check_warns_on_failing_tests_with_no_backticked_test(self) -> None:
    with self._fresh() as repo:
      self._promote(repo, "S02", Failing_tests="- add tests for the new flag")
      self.assertEqual(self._warnings(repo), [self.TESTS_LINE])
      self._promote_again(repo, "S02", "- `test_new_flag`: fails first, no flag.")
      self.assertEqual(self._warnings(repo), [])

  def _promote_again(self, repo: support.TempRepo, item_id: str, text: str) -> None:
    repo.write("note.md", text)
    code, _, err = repo.run(
      "edit", item_id, "--section", "Failing tests", "--file", str(repo.root / "note.md"),
      "--render",
    )
    self.assertEqual(code, 0, err)

  def test_check_accepts_a_check_that_is_a_fenced_command_block(self) -> None:
    with self._fresh() as repo:
      self._promote(
        repo, "S02",
        Check="```sh\npython3 -m unittest discover -s tests -t tests\n```",
      )
      self.assertEqual(self._warnings(repo), [])

  def test_check_warns_on_prose_plus_an_empty_fence(self) -> None:
    with self._fresh() as repo:
      self._promote(repo, "S02", Check="- Tests pass.\n\n```sh\n```")
      self.assertEqual(self._warnings(repo), [self.CHECK_LINE])

  def test_check_skips_done_retired_and_slices_without_the_section(self) -> None:
    with self._fresh() as repo:
      repo.run("add", "third thing")
      repo.run("add", "fourth thing")
      self._promote(repo, "S01", Check="- Works.", Failing_tests="- prose only")
      self._promote(repo, "S02", Check="- Works.", Failing_tests="- prose only")
      self._promote(repo, "S03", Why="Because.")
      for item_id, status in (("S01", "done"), ("S02", "retired")):
        code, _, err = repo.run("set", item_id, "--status", status, "--render")
        self.assertEqual(code, 0, err)
      self.assertEqual(self._warnings(repo), [])
      # A slice whose sections lack the headings altogether: promote always writes every
      # configured section, so drop them in memory.
      self._promote(repo, "S04", Check="- Works.", Failing_tests="- prose only")
      state = repo.state()
      self.assertEqual(len(check.vague_acceptance(state, "S04")), 2)
      sl = state.slices["S04"]
      sl.sections = [s for s in sl.sections if s.heading not in ("Check", "Failing tests")]
      self.assertEqual(check.vague_acceptance(state, "S04"), [])


if __name__ == "__main__":
  unittest.main()
