"""`slicer next --ready` is a bounded pickup of the next item and its slice."""

from __future__ import annotations

import json
import unittest

import support

ITEM_KEYS = ["attempts", "depends_on", "effective_score", "id", "note_records", "notes", "path", "status", "title"]


class ReadyTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    return repo

  def test_Ready_PromotedItem_ReturnsThatItemAndItsSlice(self) -> None:
    with self.repo() as repo:
      repo.run("add", "work", "--importance", "3", "--urgency", "1")
      repo.run("promote", "S01")
      repo.run("edit", "S01", "--section", "Implement", "--text", "Do the thing.")
      repo.run("edit", "S01", "--section", "Check", "--text", "The thing works.")
      code, out, err = repo.run("next", "--ready", "--json")
      self.assertEqual((code, err), (0, ""))
      payload = json.loads(out)
      self.assertEqual(sorted(payload), ["blocked", "item", "slice"])
      self.assertEqual(sorted(payload["item"]), ITEM_KEYS)
      self.assertEqual(payload["item"]["id"], "S01")
      self.assertEqual(payload["item"]["title"], "work")
      self.assertEqual(payload["item"]["status"], "open")
      self.assertEqual(payload["item"]["depends_on"], [])
      self.assertEqual(payload["item"]["effective_score"], 31)
      self.assertTrue(str(payload["item"]["path"]).endswith(".slicer/slices/S01.json"))
      self.assertEqual(payload["blocked"], [])
      shown = json.loads(repo.run("show", "S01", "--json")[1])
      self.assertEqual(payload["slice"], shown["slice"])
      self.assertNotIn("fields", payload["item"])
      self.assertNotIn("census", payload)
      self.assertNotIn("goals", payload)

  def test_Ready_Text_NamesSectionsWithoutTheirBodies(self) -> None:
    with self.repo() as repo:
      repo.run("add", "work")
      repo.run("promote", "S01", "--boundary", "Stay inside the loader.")
      repo.run("edit", "S01", "--section", "Implement", "--text", "Do the thing.")
      repo.run("edit", "S01", "--section", "Check", "--text", "The thing works.")
      code, text, err = repo.run("next", "--ready")
      self.assertEqual((code, err), (0, ""))
      self.assertIn("S01", text)
      self.assertIn("score 22", text)
      self.assertIn("boundary  Stay inside the loader.", text)
      self.assertIn("sections  ", text)
      self.assertIn("Implement", text)
      self.assertIn("Blocked   none", text)
      self.assertNotIn("Do the thing.", text)

  def test_Ready_OtherBlockedItem_IsListedBesideThePick(self) -> None:
    with self.repo() as repo:
      repo.run("add", "blocker")
      repo.run("add", "later", "--importance", "3", "--urgency", "3")
      repo.run("set", "S02", "--depends-on", "S01")
      payload = json.loads(repo.run("next", "--ready", "--json")[1])
      self.assertEqual(payload["item"]["id"], "S01")
      self.assertEqual(payload["blocked"], [{"id": "S02", "waiting_on": ["S01"]}])
      plain = json.loads(repo.run("next", "--json")[1])
      self.assertNotIn("blocked", plain)
      text = repo.run("next", "--ready")[1]
      self.assertIn("S02 waits on S01", text)

  def test_Ready_NoSlice_OmitsTheSliceKey(self) -> None:
    with self.repo() as repo:
      repo.run("add", "bare row")
      code, out, err = repo.run("next", "--ready", "--json")
      self.assertEqual((code, err), (0, ""))
      payload = json.loads(out)
      self.assertEqual(sorted(payload), ["blocked", "item"])
      self.assertIsNone(payload["item"]["path"])
      self.assertNotIn("slice", payload)
      self.assertIn("slicer promote S01", repo.run("next", "--ready")[1])

  def test_Ready_Offset_ReturnsThatEligibleItem(self) -> None:
    with self.repo() as repo:
      repo.run("add", "first")
      repo.run("add", "second")
      payload = json.loads(repo.run("next", "-n", "1", "--ready", "--json")[1])
      self.assertEqual(payload["item"]["id"], "S02")
      self.assertEqual(payload["item"]["title"], "second")

  def test_Ready_Start_MarksTheItemAndReportsThatStatus(self) -> None:
    with self.repo() as repo:
      repo.run("add", "work")
      code, out, err = repo.run("next", "--start", "--ready", "--json")
      self.assertEqual((code, err), (0, ""))
      started = repo.state().config.started_status
      payload = json.loads(out)
      self.assertEqual(payload["item"]["status"], started)
      self.assertEqual(repo.state().index.require("S01").status, started)

  def test_Ready_NothingEligible_MatchesNext(self) -> None:
    with self.repo() as repo:
      repo.run("add", "only")
      repo.run("done", "S01")
      code, out, _ = repo.run("next", "--ready", "--json")
      plain_code, plain, _ = repo.run("next", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(plain_code, 2)
      self.assertEqual(json.loads(out), json.loads(plain))
      self.assertEqual(json.loads(out), {"item": None, "blocked": []})

  def test_Ready_FullyBlockedQueue_MatchesNext(self) -> None:
    with self.repo() as repo:
      repo.run("add", "held")
      repo.run("add", "waiting")
      repo.run("set", "S02", "--depends-on", "S01")
      repo.run("park", "S01")
      code, out, _ = repo.run("next", "--ready", "--json")
      plain = json.loads(repo.run("next", "--json")[1])
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out), plain)
      self.assertEqual(plain["item"], None)
      self.assertEqual(plain["blocked"], [{"id": "S02", "waiting_on": ["S01"]}])

  def test_Ready_OnlyUnspecified_MatchesNext(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Needs a spec")
      repo.run("promote", "S01")
      repo.run("edit", "S01", "--section", "Implement", "--text", "Do the thing.")
      code, out, _ = repo.run("next", "--ready", "--json")
      plain = json.loads(repo.run("next", "--json")[1])
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out), plain)
      self.assertEqual(plain, {
        "item": None,
        "blocked": [],
        "unspecified": [{"id": "S01", "missing": ["Check"]}],
      })

  def test_Ready_SkippedSlice_IsNamedBesideThePick(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Needs a spec")
      repo.run("promote", "S01")
      repo.run("add", "Ready")
      repo.run("promote", "S02")
      repo.run("edit", "S02", "--section", "Implement", "--text", "Do the thing.")
      repo.run("edit", "S02", "--section", "Check", "--text", "The thing works.")
      payload = json.loads(repo.run("next", "--ready", "--json")[1])
      self.assertEqual(payload["item"]["id"], "S02")
      self.assertEqual(payload["unspecified"], [{"id": "S01", "missing": ["Implement", "Check"]}])
      self.assertIn("slice", payload)
      text = repo.run("next", "--ready")[1]
      self.assertIn("skipped S01: Implement and Check are empty.", text)

  def test_Ready_WithShow_IsUsage(self) -> None:
    with self.repo() as repo:
      repo.run("add", "work")
      code, out, err = repo.run("next", "--ready", "--show", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out)["error"]["code"], "usage")
      self.assertIn("--ready", err)
      self.assertEqual(repo.state().index.require("S01").status, "open")

  def test_Ready_Lean_DropsEmptyFieldsAndKeepsTheItem(self) -> None:
    with self.repo() as repo:
      repo.run("add", "work")
      payload = json.loads(repo.run("next", "--ready", "--json", "--lean")[1])
      self.assertEqual(payload, {
        "item": {
          "id": "S01",
          "title": "work",
          "status": "open",
          "effective_score": 22,
        },
      })
      repo.run("done", "S01")
      empty = json.loads(repo.run("next", "--ready", "--json", "--lean")[1])
      self.assertEqual(empty, {"item": None})

  def test_Ready_Section_ReturnsBoundaryAndThoseBodiesOnly(self) -> None:
    with self.repo() as repo:
      repo.run("add", "work", "--importance", "3", "--urgency", "1")
      repo.run("promote", "S01", "--boundary", "Stay inside the loader.")
      repo.run("edit", "S01", "--section", "Why", "--text", "Because.")
      repo.run("edit", "S01", "--section", "Implement", "--text", "Do the thing.")
      repo.run("edit", "S01", "--section", "Check", "--text", "The thing works.")
      repo.run("note", "S01", "--text", "a durable note")
      code, out, err = repo.run(
        "next", "--ready", "--section", "Check", "--section", "Implement", "--json",
      )
      self.assertEqual((code, err), (0, ""))
      payload = json.loads(out)
      self.assertEqual(sorted(payload["item"]), ITEM_KEYS)
      self.assertEqual(payload["item"]["id"], "S01")
      self.assertEqual(payload["item"]["depends_on"], [])
      self.assertEqual(payload["item"]["effective_score"], 31)
      self.assertTrue(str(payload["item"]["path"]).endswith(".slicer/slices/S01.json"))
      self.assertEqual(payload["slice"], {
        "boundary": "Stay inside the loader.",
        "notes": [],
        "sections": [
          {"heading": "Implement", "body": "Do the thing."},
          {"heading": "Check", "body": "The thing works."},
        ],
      })
      self.assertNotIn("Because.", out)
      self.assertTrue(payload["item"]["notes"][0].endswith("a durable note"))
      self.assertNotIn("lead", payload["slice"])
      self.assertEqual(payload["slice"]["notes"], [])
      shown = repo.run("next", "--show", "--json")[1]
      self.assertLess(len(out), len(shown))
      self.assertIn("Because.", shown)
      text = repo.run("next", "--ready", "--section", "Implement")[1]
      self.assertIn("boundary  Stay inside the loader.", text)
      self.assertIn("Implement", text)
      self.assertNotIn("Do the thing.", text)

  def test_Ready_WithoutSection_StillReturnsTheFullSlice(self) -> None:
    with self.repo() as repo:
      repo.run("add", "work")
      repo.run("promote", "S01")
      repo.run("edit", "S01", "--section", "Implement", "--text", "Do the thing.")
      repo.run("edit", "S01", "--section", "Check", "--text", "The thing works.")
      payload = json.loads(repo.run("next", "--ready", "--json")[1])
      shown = json.loads(repo.run("show", "S01", "--json")[1])
      self.assertEqual(payload["slice"], shown["slice"])

  def test_Ready_Section_UnknownHeadingMatchesShow(self) -> None:
    with self.repo() as repo:
      repo.run("add", "work")
      repo.run("promote", "S01")
      repo.run("edit", "S01", "--section", "Implement", "--text", "Do the thing.")
      repo.run("edit", "S01", "--section", "Check", "--text", "The thing works.")
      ready = repo.run("next", "--ready", "--section", "Nope", "--json")
      show = repo.run("show", "S01", "--section", "Nope", "--json")
      self.assertEqual(ready[0], show[0])
      self.assertEqual(ready[0], 2)
      ready_error = json.loads(ready[1])["error"]
      show_error = json.loads(show[1])["error"]
      self.assertEqual(ready_error["code"], show_error["code"])
      self.assertEqual(ready_error["message"], show_error["message"])
      self.assertEqual(ready_error["code"], "no_such_section")
      bare = repo.run("next", "--section", "Implement", "--json")
      self.assertEqual(bare[0], 2)
      self.assertEqual(json.loads(bare[1])["error"]["code"], "usage")
      both = repo.run("next", "--ready", "--show", "--json")
      self.assertEqual(both[0], 2)
      self.assertEqual(json.loads(both[1])["error"]["code"], "usage")
      repo.run("done", "S01")
      code, out, _ = repo.run("next", "--ready", "--section", "Implement", "--json")
      plain = repo.run("next", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(json.loads(out), json.loads(plain[1]))
      self.assertEqual(repo.state().index.require("S01").status, repo.state().config.done_status)

  def test_Ready_Section_UnknownHeadingDoesNotStart(self) -> None:
    with self.repo() as repo:
      repo.run("add", "work")
      repo.run("promote", "S01")
      repo.run("edit", "S01", "--section", "Implement", "--text", "Do the thing.")
      repo.run("edit", "S01", "--section", "Check", "--text", "The thing works.")
      code, _, _ = repo.run("next", "--start", "--ready", "--section", "Nope", "--json")
      self.assertEqual(code, 2)
      self.assertEqual(repo.state().index.require("S01").status, "open")

  def test_Ready_Section_NoSlice_DoesNotInventSections(self) -> None:
    with self.repo() as repo:
      repo.run("add", "bare row")
      code, out, err = repo.run("next", "--ready", "--section", "Implement", "--json")
      self.assertEqual((code, err), (0, ""))
      payload = json.loads(out)
      self.assertNotIn("slice", payload)
      self.assertIsNone(payload["item"]["path"])
      self.assertIn("slicer promote S01", repo.run("next", "--ready", "--section", "Implement")[1])

  def test_Ready_Section_Lean_DropsEmptyScaffoldingAndKeepsBodies(self) -> None:
    with self.repo() as repo:
      repo.run("add", "work")
      repo.run("promote", "S01", "--boundary", "Stay inside the loader.")
      repo.run("edit", "S01", "--section", "Implement", "--text", "Do the thing.")
      repo.run("edit", "S01", "--section", "Check", "--text", "The thing works.")
      payload = json.loads(repo.run(
        "next", "--ready", "--section", "Implement", "--section", "Check", "--json", "--lean",
      )[1])
      self.assertEqual(payload, {
        "item": {
          "id": "S01",
          "title": "work",
          "status": "open",
          "effective_score": 22,
        },
        "slice": {
          "boundary": "Stay inside the loader.",
          "sections": [
            {"heading": "Implement", "body": "Do the thing."},
            {"heading": "Check", "body": "The thing works."},
          ],
        },
      })
      repo.run("add", "bare")
      repo.run("done", "S01")
      bare = json.loads(repo.run(
        "next", "--ready", "--section", "Implement", "--json", "--lean",
      )[1])
      self.assertEqual(bare, {
        "item": {
          "id": "S02",
          "title": "bare",
          "status": "open",
          "effective_score": 22,
        },
      })


if __name__ == "__main__":
  unittest.main()
