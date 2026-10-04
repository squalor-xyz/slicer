"""`slicer next` shows why an item is next and can start it in one shot."""

from __future__ import annotations

import json
import unittest

import support


class NextVerboseTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    return repo

  def test_Next_ShowsScoreAndStatus(self) -> None:
    with self.repo() as repo:
      repo.run("add", "a thing", "--importance", "3", "--urgency", "1")
      _, text, _ = repo.run("next")
      self.assertIn("score 31", text)
      payload = json.loads(repo.run("next", "--json")[1])
      self.assertEqual(payload["effective_score"], 31)

  def test_Next_InheritedScore_IsMarked(self) -> None:
    with self.repo() as repo:
      repo.run("add", "high", "--importance", "3", "--urgency", "3")  # S01
      repo.run("add", "low", "--importance", "1", "--urgency", "1")   # S02
      repo.run("set", "S01", "--depends-on", "S02")                    # S02 inherits 33, S01 blocked
      _, text, _ = repo.run("next")
      self.assertIn("S02", text)
      self.assertIn("score 33^", text)  # inherited from its dependent

  def test_Next_Start_MarksItStartedInOneShot(self) -> None:
    with self.repo() as repo:
      repo.run("add", "work")
      code, text, err = repo.run("next", "--start")
      self.assertEqual(code, 0, err)
      started = repo.state().config.started_status
      self.assertEqual(repo.state().index.require("S01").status, started)
      # idempotent: still returns and stays started
      self.assertEqual(repo.run("next", "--start")[0], 0)
      self.assertEqual(repo.state().index.require("S01").status, started)

  def test_Next_Start_NothingEligible_ChangesNothing(self) -> None:
    with self.repo() as repo:
      repo.run("add", "only")
      repo.run("done", "S01")
      before = repo.state().index.require("S01").status
      code, _, _ = repo.run("next", "--start")
      self.assertEqual(code, 2)
      self.assertEqual(repo.state().index.require("S01").status, before)

  def test_Next_Show_IncludesSliceEqualToShow(self) -> None:
    with self.repo() as repo:
      repo.run("add", "work")
      repo.run("promote", "S01")
      repo.run("edit", "S01", "--section", "Implement", "--text", "Do the thing.")
      repo.run("edit", "S01", "--section", "Check", "--text", "The thing works.")
      payload = json.loads(repo.run("next", "--show", "--json")[1])
      shown = json.loads(repo.run("show", "S01", "--json")[1])
      # The fused read equals the two-call composition: same slice object.
      self.assertEqual(payload["slice"], shown["slice"])

  def test_Next_StartShow_ReturnsStartedItemWithSlice(self) -> None:
    with self.repo() as repo:
      repo.run("add", "work")
      repo.run("promote", "S01")
      repo.run("edit", "S01", "--section", "Implement", "--text", "Do the thing.")
      repo.run("edit", "S01", "--section", "Check", "--text", "The thing works.")
      code, _, err = repo.run("next", "--start", "--show", "--json")
      self.assertEqual(code, 0, err)
      payload = json.loads(repo.run("next", "--show", "--json")[1])
      self.assertEqual(payload["status"], repo.state().config.started_status)
      self.assertIn("slice", payload)

  def test_Next_Show_NoSlice_OmitsSliceKey(self) -> None:
    with self.repo() as repo:
      repo.run("add", "work")  # a bare row, never promoted
      code, _, err = repo.run("next", "--show", "--json")
      self.assertEqual(code, 0, err)
      self.assertNotIn("slice", json.loads(repo.run("next", "--show", "--json")[1]))

  def test_Next_EmptyImplementOrCheck_IsSkippedAndNamed(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Needs a spec")
      repo.run("promote", "S01")
      repo.run("add", "Ready")
      repo.run("promote", "S02")
      repo.run("edit", "S02", "--section", "Implement", "--text", "Do the thing.")
      repo.run("edit", "S02", "--section", "Check", "--text", "The thing works.")
      code, out, err = repo.run("next", "--json")
      self.assertEqual(code, 0, err)
      payload = json.loads(out)
      self.assertEqual(payload["id"], "S02")
      self.assertEqual(payload["unspecified"], [{"id": "S01", "missing": ["Implement", "Check"]}])
      code, text, err = repo.run("next")
      self.assertEqual(code, 0, err)
      self.assertIn("skipped S01: Implement and Check are empty.", text)
      self.assertIn("`slicer edit S01 --section Implement`", text)
      self.assertIn("`slicer edit S01 --section Check`", text)
      self.assertEqual(repo.state().index.require("S01").status, "open")

  def test_Next_OnlyUnspecified_IsTheNoItemPath(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Needs a spec")
      repo.run("promote", "S01")
      repo.run("edit", "S01", "--section", "Implement", "--text", "Do the thing.")
      code, out, _ = repo.run("next", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out), {
        "item": None,
        "blocked": [],
        "unspecified": [{"id": "S01", "missing": ["Check"]}],
      })

  def test_Next_RequiredSections_ReplaceTheDefaultPair(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Needs a spec")
      repo.run("promote", "S01")
      data = json.loads((repo.root / ".slicer/config.json").read_text())
      data["required_sections"] = ["Why"]
      (repo.root / ".slicer/config.json").write_text(json.dumps(data) + "\n")
      code, out, err = repo.run("next", "--json")
      self.assertEqual(code, 2, err)
      self.assertEqual(json.loads(out)["unspecified"], [{"id": "S01", "missing": ["Why"]}])
      repo.run("edit", "S01", "--section", "Why", "--text", "Because.")
      code, out, err = repo.run("next", "--json")
      self.assertEqual(code, 0, err)
      payload = json.loads(out)
      self.assertEqual(payload["id"], "S01")
      self.assertNotIn("unspecified", payload)

  def test_Next_SpacedRequiredHeading_IsQuotedInTheEditHint(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Needs a spec")
      repo.run("promote", "S01")
      data = json.loads((repo.root / ".slicer/config.json").read_text())
      data["required_sections"] = ["Failing tests"]
      (repo.root / ".slicer/config.json").write_text(json.dumps(data) + "\n")
      code, text, err = repo.run("next")
      self.assertEqual(code, 2, err)
      self.assertIn("skipped S01: Failing tests is empty.", text)
      self.assertIn("`slicer edit S01 --section 'Failing tests'`", text)

  def test_Next_EmptyRequiredSections_DoesNotSkipForAnEmptySection(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Needs a spec")
      repo.run("promote", "S01")
      data = json.loads((repo.root / ".slicer/config.json").read_text())
      data["required_sections"] = []
      (repo.root / ".slicer/config.json").write_text(json.dumps(data) + "\n")
      code, out, err = repo.run("next", "--json")
      self.assertEqual(code, 0, err)
      payload = json.loads(out)
      self.assertEqual(payload["id"], "S01")
      self.assertNotIn("unspecified", payload)

  def test_Next_MissingRequiredSectionsKey_StaysImplementAndCheck(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Needs a spec")
      repo.run("promote", "S01")
      path = repo.root / ".slicer/config.json"
      data = json.loads(path.read_text())
      data.pop("required_sections", None)
      path.write_text(json.dumps(data) + "\n")
      code, out, err = repo.run("next", "--json")
      self.assertEqual(code, 2, err)
      self.assertEqual(
        json.loads(out)["unspecified"],
        [{"id": "S01", "missing": ["Implement", "Check"]}],
      )

  def test_Next_NoSlice_StaysEligible(self) -> None:
    with self.repo() as repo:
      repo.run("add", "bare row")
      code, out, err = repo.run("next", "--json")
      self.assertEqual(code, 0, err)
      payload = json.loads(out)
      self.assertEqual(payload["id"], "S01")
      self.assertNotIn("unspecified", payload)

  def test_Next_Show_NothingEligible_StillEmptyEnvelope(self) -> None:
    with self.repo() as repo:
      repo.run("add", "only")
      repo.run("done", "S01")
      code, out, _ = repo.run("next", "--show", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out), {"item": None, "blocked": []})

  def _ready_slice(self, repo: support.TempRepo) -> None:
    repo.run("add", "work")
    repo.run("promote", "S01")
    repo.run("edit", "S01", "--section", "Implement", "--text", "Do the thing.")
    repo.run("edit", "S01", "--section", "Check", "--text", "The thing works.")

  def test_Next_Text_HintsAtShow_AndOmitsTheSlicePath(self) -> None:
    with self.repo() as repo:
      self._ready_slice(repo)
      forms = (
        ("next",),
        ("next", "--ready"),
        ("next", "--show"),
        ("next", "--batch", "1"),
      )
      for args in forms:
        with self.subTest(args=args):
          code, text, err = repo.run(*args)
          self.assertEqual(code, 0, err)
          # `--show` still appends the rendered slice, whose banner names the source file.
          head = text.split("<!--", 1)[0]
          self.assertIn("`slicer show S01`", head)
          self.assertNotIn("S01.json", head)
      shown = repo.run("next", "--show")[1]
      self.assertIn("Do the thing.", shown)

  def test_Next_NoSlice_KeepsThePromoteHint_WithoutAShowHint(self) -> None:
    with self.repo() as repo:
      repo.run("add", "bare row")
      plain = repo.run("next")[1]
      ready = repo.run("next", "--ready")[1]
      shown = repo.run("next", "--show")[1]
      batch = repo.run("next", "--batch", "1")[1]
      for text in (plain, ready, shown, batch):
        self.assertNotIn("slicer show", text)
        self.assertNotIn(".json", text)
      for text in (ready, shown):
        self.assertIn("`slicer promote S01`", text)
      self.assertNotIn("slicer promote", plain)

  def test_Next_PathFlag_AddsThePathLine_AndLeavesJsonAlone(self) -> None:
    with self.repo() as repo:
      self._ready_slice(repo)
      code, text, err = repo.run("next", "--path")
      self.assertEqual(code, 0, err)
      self.assertIn("`slicer show S01`", text)
      self.assertIn("S01.json", text)
      ready = repo.run("next", "--ready", "--path")[1]
      self.assertIn("`slicer show S01`", ready)
      self.assertIn("S01.json", ready)
      full = json.loads(repo.run("next", "--json")[1])
      flagged = json.loads(repo.run("next", "--path", "--json")[1])
      self.assertTrue(full["path"].endswith("S01.json"))
      self.assertEqual(flagged["path"], full["path"])
      lean = json.loads(repo.run("next", "--json", "--lean")[1])
      lean_path = json.loads(repo.run("next", "--path", "--json", "--lean")[1])
      self.assertNotIn("path", lean)
      self.assertNotIn("path", lean_path)
      ready_json = json.loads(repo.run("next", "--ready", "--json")[1])
      self.assertTrue(ready_json["item"]["path"].endswith("S01.json"))
      ready_lean = json.loads(repo.run("next", "--ready", "--path", "--json", "--lean")[1])
      self.assertNotIn("path", ready_lean["item"])

  def test_ListShowAndStatus_Text_StayFreeOfTheShowHint(self) -> None:
    with self.repo() as repo:
      self._ready_slice(repo)
      listed = repo.run("list")[1]
      shown = repo.run("show", "S01")[1]
      status = repo.run("status")[1]
      self.assertNotIn("slicer show", listed)
      self.assertNotIn("slicer show", shown)
      self.assertNotIn("slicer show", status)
      self.assertNotIn("S01.json", status)


if __name__ == "__main__":
  unittest.main()
