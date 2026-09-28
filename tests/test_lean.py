"""`--json --lean` drops empty scaffolding and leaves the full shape opt-in."""

from __future__ import annotations

import json
import unittest

import support

from slicer.model import lean


def _dumps(payload: object) -> str:
  return json.dumps(payload, ensure_ascii=False, indent=2)


class LeanShapeTests(unittest.TestCase):
  def test_Lean_DropsEmptyScaffoldingAndARepeatedShortTitle(self) -> None:
    full = {
      "id": "S01",
      "title": "Parse the config file",
      "short_title": "Parse the config file",
      "status": "open",
      "has_slice": False,
      "depends_on": [],
      "notes": [],
      "fields": {
        "size": "M",
        "flags": [],
        "trees": ["core"],
        "trees_literal": False,
        "findings": "G1",
        "pass": "",
        "group": "",
        "reason": "",
        "importance": 2,
        "urgency": 2,
      },
      "path": "/tmp/project/.slicer/slices/S01.json",
      "effective_score": 22,
    }
    shaped = lean(full)
    self.assertLess(len(_dumps(shaped).encode()), len(_dumps(full).encode()) * 0.7)
    self.assertEqual(shaped["id"], "S01")
    self.assertEqual(shaped["title"], "Parse the config file")
    self.assertEqual(shaped["status"], "open")
    self.assertIs(shaped["has_slice"], False)
    self.assertEqual(shaped["fields"]["trees"], ["core"])
    self.assertEqual(shaped["fields"]["findings"], "G1")
    self.assertEqual(shaped["fields"]["size"], "M")
    self.assertEqual(shaped["fields"]["importance"], 2)
    self.assertEqual(shaped["fields"]["urgency"], 2)
    self.assertEqual(shaped["effective_score"], 22)
    for key in ("short_title", "depends_on", "notes", "path"):
      self.assertNotIn(key, shaped)
    for key in ("flags", "trees_literal", "pass", "group", "reason"):
      self.assertNotIn(key, shaped["fields"])

  def test_Lean_KeepsADistinctShortTitleNotesPassAndLiteralTrees(self) -> None:
    shaped = lean({
      "id": "S02",
      "title": "Full title",
      "short_title": "Brief",
      "notes": ["a note"],
      "fields": {"pass": "v1", "trees_literal": True, "importance": 3, "urgency": 1},
    })
    self.assertEqual(shaped["short_title"], "Brief")
    self.assertEqual(shaped["notes"], ["a note"])
    self.assertEqual(shaped["fields"]["pass"], "v1")
    self.assertIs(shaped["fields"]["trees_literal"], True)
    self.assertEqual(shaped["fields"]["importance"], 3)

  def test_Lean_KeepsTheEmptyNextSentinel(self) -> None:
    self.assertEqual(lean({"item": None, "blocked": []}), {"item": None})

  def test_Lean_KeepsAPathThatIsNotOnAnItem(self) -> None:
    shaped = lean([{"target": "README.md", "path": "README.md", "stale": False, "detail": ""}])
    self.assertEqual(shaped, [{"target": "README.md", "path": "README.md", "stale": False}])


class LeanCommandTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.run(
      "add", "Parse the config file", "--size", "M", "--tree", "core", "--findings", "G1",
    )
    repo.run("promote", "S01")
    return repo

  def test_ListNextAndShow_WithoutLean_KeepTheFullShape(self) -> None:
    with self.repo() as repo:
      listed = json.loads(repo.run("list", "--json")[1])[0]
      nxt = json.loads(repo.run("next", "--json")[1])
      shown = json.loads(repo.run("show", "S01", "--json")[1])
      for payload in (listed, nxt, shown):
        self.assertEqual(payload["short_title"], payload["title"])
        self.assertEqual(payload["depends_on"], [])
        self.assertEqual(payload["notes"], [])
        self.assertIs(payload["fields"]["trees_literal"], False)
        self.assertEqual(payload["fields"]["pass"], "")
      self.assertIn("path", nxt)
      self.assertNotIn("path", listed)
      self.assertIn("slice", shown)
      self.assertEqual(shown["slice"]["lead"], [])
      self.assertEqual(shown["slice"]["flags"], [])
      self.assertEqual(shown["slice"]["notes"], [])

  def test_Next_Lean_IsSmallerAndDropsThePath(self) -> None:
    with self.repo() as repo:
      full = repo.run("next", "--json")[1]
      slim = repo.run("next", "--json", "--lean")[1]
      self.assertLess(len(slim.encode()), len(full.encode()) * 0.7)
      payload = json.loads(slim)
      self.assertEqual(payload["id"], "S01")
      self.assertEqual(payload["fields"]["trees"], ["core"])
      self.assertEqual(payload["fields"]["findings"], "G1")
      self.assertIn("effective_score", payload)
      self.assertNotIn("path", payload)
      self.assertNotIn("short_title", payload)
      self.assertNotIn("notes", payload)

  def test_Show_Lean_DropsEmptySliceScaffoldingAndKeepsHeadings(self) -> None:
    with self.repo() as repo:
      payload = json.loads(repo.run("show", "S01", "--json", "--lean")[1])
      sl = payload["slice"]
      for key in ("lead", "flags", "notes", "depends_note"):
        self.assertNotIn(key, sl)
      self.assertEqual(sl["findings_note"], "G1")
      self.assertTrue(sl["sections"])
      for section in sl["sections"]:
        self.assertIn("heading", section)
        self.assertNotIn("body", section)

  def test_List_LeanWithoutJson_DoesNotChangeTheText(self) -> None:
    with self.repo() as repo:
      self.assertEqual(repo.run("list", "--lean")[1], repo.run("list")[1])

  def test_Show_LeanError_KeepsTheEnvelope(self) -> None:
    with self.repo() as repo:
      code, out, _ = repo.run("show", "NOPE", "--json", "--lean")
      self.assertEqual(code, 2)
      error = json.loads(out)["error"]
      self.assertEqual(error["code"], "no_such_item")
      self.assertIn("message", error)
