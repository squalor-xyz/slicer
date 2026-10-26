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
          self.assertIn("`slicer show S01`", text)
          self.assertNotIn("S01.json", text)
      shown = repo.run("next", "--show")[1]
      self.assertIn("Do the thing.", shown)

  def test_NextShow_Text_PrintsTheSliceBodyWithoutTheGeneratedBanner(self) -> None:
    with self.repo() as repo:
      self._ready_slice(repo)
      code, text, err = repo.run("next", "--show")
      self.assertEqual(code, 0, err)
      self.assertIn("`slicer show S01`", text)
      self.assertIn("Do the thing.", text)
      self.assertIn("The thing works.", text)
      self.assertNotIn(".slicer/slices", text)
      self.assertNotIn("<!--", text)
      started = repo.run("next", "--start", "--show")[1]
      self.assertNotIn(".slicer/slices", started)

  def test_Show_Text_KeepsTheBannerWithTheSourcePath(self) -> None:
    with self.repo() as repo:
      self._ready_slice(repo)
      text = repo.run("show", "S01")[1]
      self.assertTrue(text.startswith("<!-- GENERATED by slicer."))
      self.assertIn("Source: .slicer/slices/S01.json.", text.split("\n", 1)[0])

  def test_Render_SliceFile_StillStartsWithTheBanner(self) -> None:
    with self.repo() as repo:
      self._ready_slice(repo)
      repo.run("render")
      text = repo.read(".slicer/render/slices/S01.md")
      self.assertTrue(text.startswith("<!-- GENERATED by slicer. Source: .slicer/slices/S01.json."))

  def test_Next_Json_KeepsThePath_AndLeanDropsItOnTheItem(self) -> None:
    with self.repo() as repo:
      self._ready_slice(repo)
      for args in (("next", "--json"), ("next", "--show", "--json")):
        with self.subTest(args=args):
          payload = json.loads(repo.run(*args)[1])
          self.assertTrue(payload["path"].endswith("S01.json"))
          lean = json.loads(repo.run(*args, "--lean")[1])
          self.assertNotIn("path", lean)

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


class HeldTests(unittest.TestCase):
  def repo(self):
    repo = support.TempRepo()
    repo.run("init")
    repo.run("add", "waiting")
    repo.run("set", "S01", "--add-flag", "needs-owner", "--add-flag", "keep")
    return repo

  def test_Next_StartOnlyHeldItem_ClaimsNothing(self):
    with self.repo() as repo:
      code, out, _ = repo.run("next", "--start", "--json")
      shown = json.loads(repo.run("show", "S01", "--json")[1])
      self.assertEqual((code, json.loads(out).get("item", "missing"),
        shown["status"], shown["claim"]), (2, None, "open", None))

  def test_HoldFlags_InvalidConfig_RejectsWithConfigCode(self):
    for value in ('"x"', '[""]'):
      with self.subTest(value=value), self.repo() as repo:
        code, out, _ = repo.run("config", "hold_flags", value, "--json")
        self.assertEqual((code, json.loads(out).get("error", {}).get("code")),
          (2, "bad_config_value"))

  def test_Next_HeldItem_SkippedAndListedUnderHeld(self):
    for form in ((), ("--ready",), ("--show",)):
      with self.subTest(form=form), self.repo() as repo:
        repo.run("add", "ready")
        payload = json.loads(repo.run("next", *form, "--json")[1])
        self.assertEqual((payload.get("item", payload)["id"], payload.get("held")),
          ("S02", [{"id": "S01", "title": "waiting", "flags": ["needs-owner"]}]))

  def test_NextBatch_HeldItem_Skipped(self):
    with self.repo() as repo:
      repo.run("add", "dependent", "--depends-on", "S01")
      code, out, _ = repo.run("next", "--batch", "2", "--json")
      payload = json.loads(out)
      self.assertEqual((code, payload["items"], payload.get("held"), payload["blocked"]),
        (2, [], [{"id": "S01", "title": "waiting", "flags": ["needs-owner"]}],
          [{"id": "S02", "waiting_on": ["S01"]}]))

  def test_Status_ListsHeldItems(self):
    with self.repo() as repo:
      payload = json.loads(repo.run("status", "--json")[1])
      self.assertEqual((payload["next"], payload.get("held"),
        "Held\n  S01 waiting [needs-owner]" in repo.run("status")[1]),
        (None, [{"id": "S01", "title": "waiting", "flags": ["needs-owner"]}], True))

  def test_HoldFlags_EmptyConfig_NothingHeld(self):
    with self.repo() as repo:
      code, _, _ = repo.run("config", "hold_flags", "[]")
      self.assertEqual(code, 0)
      payload = json.loads(repo.run("next", "--json")[1])
      self.assertEqual((payload.get("id"), payload.get("held", [])), ("S01", []))

  def test_List_Held_ReturnsOnlyFlaggedItems(self):
    with self.repo() as repo:
      repo.run("add", "ready")
      code, out, err = repo.run("list", "--held", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual([i["id"] for i in json.loads(out)], ["S01"])

  def test_HoldFlags_CustomConfig_HoldsCustomFlagOnly(self):
    with self.repo() as repo:
      code, _, _ = repo.run("config", "hold_flags", '["design"]')
      self.assertEqual(code, 0)
      repo.run("add", "design waiting")
      repo.run("set", "S02", "--add-flag", "design")
      payload = json.loads(repo.run("next", "--json")[1])
      self.assertEqual((payload["id"], payload.get("held")),
        ("S01", [{"id": "S02", "title": "design waiting", "flags": ["design"]}]))


class DisjointTests(unittest.TestCase):
  """`next --batch K --disjoint` and `deps --overlap` read a configured section's paths."""

  def repo(self, ignore: str = "[]") -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.run("config", "files_section", "Files")
    repo.run("config", "overlap_ignore", ignore)
    (repo.root / "CHANGELOG.md").write_text("Changelog\n")
    return repo

  def file_item(self, repo: support.TempRepo, title: str, files: str | None) -> str:
    """Add an item; with `files`, give it a specified slice whose Files section is that text."""
    item_id = json.loads(repo.run("add", title, "--json")[1])["id"]
    if files is not None:
      repo.run("promote", item_id)
      repo.run("edit", item_id, "--section", "Implement", "--text", "Do it.")
      repo.run("edit", item_id, "--section", "Check", "--text", "It works.")
      repo.run("edit", item_id, "--section", "Files", "--text", files)
    return item_id

  def test_NextBatchDisjoint_FilesSectionMissing_Warns(self) -> None:
    with self.repo() as repo:
      self.file_item(repo, "first", "- `src/a.py`")
      self.file_item(repo, "second", "- `src/b.py`")
      repo.run("config", "files_section", "MissingFiles")
      for command in (("next", "--batch", "2", "--disjoint"),
                      ("deps", "--overlap", "S01", "S02")):
        with self.subTest(command=command):
          code, out, err = repo.run(*command, "--json")
          self.assertIn("no item has", err)
          self.assertIn("MissingFiles", err)
          self.assertIn("files_section", err)
          self.assertEqual(code, 2 if command[0] == "next" else 0)
          payload = json.loads(out)
          if command[0] == "next":
            self.assertEqual(payload["unknown_files"], ["S01", "S02"])
          else:
            self.assertEqual(payload, {"overlap": [{"a": "S01", "b": "S02", "shared": []}]})
      repo.run("config", "files_section", "Files")
      for command in (("next", "--batch", "2", "--disjoint"),
                      ("deps", "--overlap", "S01", "S02")):
        with self.subTest(present=command):
          self.assertNotIn("no item has", repo.run(*command, "--json")[2])

  def test_NextBatchDisjoint_DependentAfterDependency(self) -> None:
    with self.repo() as repo:
      first = self.file_item(repo, "dependency", "- `src/a.py`")
      second = self.file_item(repo, "dependent", "- `src/b.py`")
      repo.run("set", second, "--depends-on", first)
      plain = json.loads(repo.run("next", "--batch", "2", "--json")[1])
      self.assertEqual([i["id"] for i in plain["items"]], [first, second])
      payload = json.loads(repo.run("next", "--batch", "2", "--disjoint", "--json")[1])
      self.assertEqual([i["id"] for i in payload["items"]], [first])
      self.assertEqual(payload["blocked"], [{"id": second, "waiting_on": [first]}])

  def test_NextBatchDisjoint_SkipsOverlappingItem(self) -> None:
    with self.repo() as repo:
      self.file_item(repo, "first", "- `src/a.py`\n- `docs/x.md`")
      self.file_item(repo, "overlaps first", "- `src/b.py:12`\n- `docs/x.md`")
      self.file_item(repo, "disjoint", "- `src/c.py`")
      self.file_item(repo, "no files", None)
      code, out, err = repo.run("next", "--batch", "3", "--disjoint", "--json")
      self.assertEqual(code, 0, err)
      payload = json.loads(out)
      self.assertEqual([i["id"] for i in payload["items"]], ["S01", "S03"])
      self.assertEqual(payload["unknown_files"], ["S04"])
      plain = json.loads(repo.run("next", "--batch", "3", "--json")[1])
      self.assertEqual([i["id"] for i in plain["items"]], ["S01", "S02", "S03"])
      self.assertNotIn("unknown_files", plain)
      text = repo.run("next", "--batch", "3", "--disjoint")[1]
      self.assertIn("S04", text)

  def test_NextBatchDisjoint_IgnoresOverlapIgnorePaths(self) -> None:
    with self.repo(ignore='["CHANGELOG.md"]') as repo:
      self.file_item(repo, "first", "- `src/a.py`\n- `CHANGELOG.md`")
      self.file_item(repo, "second", "- `src/b.py`\n- `CHANGELOG.md`")
      self.file_item(repo, "only changelog", "- `CHANGELOG.md`")
      payload = json.loads(repo.run("next", "--batch", "3", "--disjoint", "--json")[1])
      self.assertEqual([i["id"] for i in payload["items"]], ["S01", "S02", "S03"])
      self.assertNotIn("unknown_files", payload)
      repo.run("config", "overlap_ignore", "[]")
      payload = json.loads(repo.run("next", "--batch", "3", "--disjoint", "--json")[1])
      self.assertEqual([i["id"] for i in payload["items"]], ["S01"])

  def test_NextBatchDisjoint_NoFilesSection_Usage(self) -> None:
    with self.repo() as repo:
      repo.run("config", "files_section", "")
      self.file_item(repo, "first", "- `src/a.py`")
      code, out, _ = repo.run("next", "--batch", "2", "--disjoint", "--json")
      error = json.loads(out)["error"]
      self.assertEqual((code, error["code"]), (2, "usage"))
      self.assertIn("files_section", error["message"])
      repo.run("config", "files_section", "Files")
      code, out, _ = repo.run("next", "--disjoint", "--json")
      error = json.loads(out)["error"]
      self.assertEqual((code, error["code"]), (2, "usage"))
      self.assertIn("--batch", error["message"])

  def test_NextBatchDisjoint_HeldItem_NeverAppears(self) -> None:
    with self.repo() as repo:
      held = self.file_item(repo, "held", "- `src/a.py`")
      self.file_item(repo, "other", "- `src/b.py`")
      repo.run("set", held, "--add-flag", "needs-owner")
      payload = json.loads(repo.run("next", "--batch", "2", "--disjoint", "--json")[1])
      self.assertEqual([i["id"] for i in payload["items"]], ["S02"])
      self.assertEqual([h["id"] for h in payload["held"]], ["S01"])

  def test_OverlapConfig_InvalidIgnoreList_RejectedWithConfigCode(self) -> None:
    with self.repo() as repo:
      for value in ('[""]', '"CHANGELOG.md"'):
        code, out, _ = repo.run("config", "overlap_ignore", value, "--json")
        self.assertEqual((code, json.loads(out)["error"]["code"]), (2, "bad_config_value"))

  def test_DepsOverlap_PrintsSharedPaths(self) -> None:
    with self.repo(ignore='["CHANGELOG.md"]') as repo:
      self.file_item(repo, "one", "- `src/a.py`\n- `docs/x.md`\n- `CHANGELOG.md`")
      self.file_item(repo, "two", "- `docs/x.md:3`\n- `src/a.py`\n- `CHANGELOG.md`")
      self.file_item(repo, "three", "- `src/c.py`")
      code, out, err = repo.run("deps", "--overlap", "S01", "S02", "S03", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out), {"overlap": [
        {"a": "S01", "b": "S02", "shared": ["src/a.py", "docs/x.md"]},
        {"a": "S01", "b": "S03", "shared": []},
        {"a": "S02", "b": "S03", "shared": []},
      ]})
      text = repo.run("deps", "--overlap", "S01", "S02", "S03")[1]
      self.assertIn("S01 S02  src/a.py, docs/x.md", text)
      self.assertIn("S01 S03  -", text)

  def test_DepsOverlap_BadArguments_Usage(self) -> None:
    with self.repo() as repo:
      self.file_item(repo, "one", "- `src/a.py`")
      self.file_item(repo, "two", "- `src/a.py`")
      for argv in (("--overlap", "S01"), ("S01", "--overlap", "S01", "S02"),
                   ("--overlap", "S01", "S02", "--format", "mermaid")):
        with self.subTest(argv=argv):
          code, out, _ = repo.run("deps", *argv, "--json")
          self.assertEqual((code, json.loads(out)["error"]["code"]), (2, "usage"))
      code, out, _ = repo.run("deps", "--overlap", "S01", "S99", "--json")
      self.assertEqual((code, json.loads(out)["error"]["code"]), (2, "no_such_item"))
      repo.run("config", "files_section", "")
      code, out, _ = repo.run("deps", "--overlap", "S01", "S02", "--json")
      self.assertEqual((code, json.loads(out)["error"]["code"]), (2, "usage"))

  def test_Show_FilesSection_ListsPathSpansInJson(self) -> None:
    with self.repo(ignore='["CHANGELOG.md"]') as repo:
      item = self.file_item(
        repo, "one", "- `src/a.py`\n- `docs/x.md:3`\n- `tests/`\n- `CHANGELOG.md`",
      )
      code, out, err = repo.run("show", item, "--json")
      self.assertEqual(code, 0, err)
      payload = json.loads(out)
      self.assertEqual(payload["paths"], ["src/a.py", "docs/x.md", "tests/", "CHANGELOG.md"])
      lean = json.loads(repo.run("show", item, "--json", "--lean")[1])
      self.assertEqual(lean["paths"], payload["paths"])

  def test_Show_OverlapIgnore_KeepsPathsThatDepsOverlapDrops(self) -> None:
    with self.repo(ignore='["CHANGELOG.md"]') as repo:
      one = self.file_item(repo, "one", "- `src/a.py`\n- `CHANGELOG.md`")
      two = self.file_item(repo, "two", "- `CHANGELOG.md`")
      payload = json.loads(repo.run("show", one, "--json")[1])
      self.assertEqual(payload["paths"], ["src/a.py", "CHANGELOG.md"])
      overlap = json.loads(repo.run("deps", "--overlap", one, two, "--json")[1])
      self.assertEqual(overlap["overlap"], [{"a": one, "b": two, "shared": []}])

  def test_Show_NoSliceOrNoFilesSection_EmptyPathsAndLeanDropsIt(self) -> None:
    with self.repo() as repo:
      bare = self.file_item(repo, "no slice", None)
      no_files = json.loads(repo.run("add", "no files", "--json")[1])["id"]
      repo.run("promote", no_files)
      repo.run("edit", no_files, "--section", "Implement", "--text", "Do it.")
      repo.run("edit", no_files, "--section", "Check", "--text", "It works.")
      for item in (bare, no_files):
        with self.subTest(item=item):
          self.assertEqual(json.loads(repo.run("show", item, "--json")[1])["paths"], [])
          lean = json.loads(repo.run("show", item, "--json", "--lean")[1])
          self.assertNotIn("paths", lean)
      with_files = self.file_item(repo, "with files", "- `src/a.py`")
      self.assertEqual(json.loads(repo.run("show", with_files, "--json")[1])["paths"],
                       ["src/a.py"])
      repo.run("config", "files_section", "")
      self.assertEqual(json.loads(repo.run("show", with_files, "--json")[1])["paths"], [])

  def test_Show_PathsKey_SitsAfterSliceBeforeCitations(self) -> None:
    with self.repo() as repo:
      item = self.file_item(repo, "one", "- `src/a.py`")
      keys = list(json.loads(repo.run("show", item, "--json")[1]))
      self.assertEqual(keys[-4:], ["slice", "paths", "citations", "slivers"])
      bare = self.file_item(repo, "bare", None)
      keys = list(json.loads(repo.run("show", bare, "--json")[1]))
      self.assertEqual(keys[-3:], ["paths", "citations", "slivers"])


if __name__ == "__main__":
  unittest.main()
