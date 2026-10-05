"""`slicer find` searches items by text across fields, including slice bodies."""

from __future__ import annotations

import json
import unittest

import support


def _ids(out: str) -> list[str]:
  return [item["id"] for item in json.loads(out)]


class FindTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    support.make_mini(repo)
    repo.run("init")
    repo.run("migrate", "--from", "docs/slices")
    return repo

  def test_Find_ByTitle_ReturnsTheMatchingItem(self) -> None:
    with self.repo() as repo:
      code, out, err = repo.run("find", "fourth thing", "--in", "title", "--json")
      self.assertEqual((code, err), (0, ""))
      self.assertEqual(_ids(out), ["S04"])

  def test_Find_ByFindings_ReturnsTheMatchingItem(self) -> None:
    with self.repo() as repo:
      _, out, _ = repo.run("find", "F9", "--in", "findings", "--json")
      self.assertEqual(_ids(out), ["S04"])

  def test_Find_ByBody_ReachesSliceSectionText(self) -> None:
    # "off-schema" appears only inside S03's off-schema section body.
    with self.repo() as repo:
      _, out, _ = repo.run("find", "off-schema", "--json")
      self.assertEqual(_ids(out), ["S03"])

  def test_Find_InTitle_ExcludesABodyOnlyMatch(self) -> None:
    with self.repo() as repo:
      code, out, _ = repo.run("find", "off-schema", "--in", "title", "--json")
      self.assertEqual((code, _ids(out)), (0, []))

  def test_Find_ScopeExcludesFieldsNotAsked(self) -> None:
    # "F9" is a findings value; searching only title/body must not find it.
    with self.repo() as repo:
      _, out, _ = repo.run("find", "F9", "--in", "title,body", "--json")
      self.assertEqual(_ids(out), [])

  def test_Find_IsCaseInsensitive(self) -> None:
    with self.repo() as repo:
      _, out, _ = repo.run("find", "OFF-SCHEMA", "--json")
      self.assertEqual(_ids(out), ["S03"])

  def test_Find_NoMatches_ExitsZeroWithMessage(self) -> None:
    with self.repo() as repo:
      code, out, _ = repo.run("find", "zzznevermatchzzz")
      self.assertEqual(code, 0)
      self.assertIn("no matching items", out)
      code, out, _ = repo.run("find", "zzznevermatchzzz", "--json")
      self.assertEqual((code, json.loads(out)), (0, []))

  def test_Find_UnknownInField_IsRefused(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("find", "x", "--in", "bogus")
      self.assertEqual(code, 2)
      self.assertIn("bogus", err)
      self.assertIn("id, title, short_title, findings, body", err)

  def test_Find_EmptyPattern_IsRefused(self) -> None:
    with self.repo() as repo:
      code, _, err = repo.run("find", "   ")
      self.assertEqual(code, 2)
      self.assertIn("nonempty", err)

  def test_Find_Rows_ReuseListFormatPlusAMatchLine(self) -> None:
    # find adds a "matched in ..." line under each item. The item line uses the
    # same formatter as list. The leading index counts that command's own rows,
    # and find still includes done items, so the numbers are not the same.
    with self.repo() as repo:
      found = repo.run("find", "spelled out")[1]
      for row in repo.run("list")[1].splitlines():
        if not row[:3].strip().isdigit():
          continue
        self.assertIn(row.split(None, 1)[1], found)
      self.assertIn("matched in title:", found)
      self.assertIn("S01", found)

  def test_Find_ReportsTheMatchedFieldAndSnippet(self) -> None:
    with self.repo() as repo:
      # title hit
      payload = json.loads(repo.run("find", "fourth thing", "--json")[1])
      match = next(p["match"] for p in payload if p["id"] == "S04")
      self.assertEqual(match["field"], "title")
      self.assertIn("fourth thing", match["snippet"])
      # body-only hit ("off-schema" lives only in S03's section body)
      payload = json.loads(repo.run("find", "off-schema", "--json")[1])
      self.assertEqual(payload[0]["id"], "S03")
      self.assertEqual(payload[0]["match"]["field"], "body")
      self.assertIn("off-schema", payload[0]["match"]["snippet"])
      # text form surfaces it too
      self.assertIn("matched in body:", repo.run("find", "off-schema")[1])

  def test_Find_MatchedField_RespectsInScope(self) -> None:
    with self.repo() as repo:
      payload = json.loads(repo.run("find", "F9", "--in", "findings", "--json")[1])
      self.assertEqual(payload[0]["match"]["field"], "findings")

  def test_FindLean_RowIsIdTitleStatusSliceAndMatch(self) -> None:
    with self.repo() as repo:
      repo.run("add", "Zebra crossing")
      _, out, _ = repo.run("find", "zebra", "--in", "title", "--json", "--lean")
      rows = json.loads(out)
      self.assertEqual(len(rows), 1)
      self.assertEqual(set(rows[0]), {"id", "title", "status", "has_slice", "match"})
      self.assertIs(rows[0]["has_slice"], False)
      self.assertEqual(rows[0]["match"]["field"], "title")
      _, out, _ = repo.run("find", "fourth thing", "--in", "title", "--json", "--lean")
      rows = json.loads(out)
      self.assertEqual(set(rows[0]), {"id", "title", "status", "has_slice", "match"})
      self.assertIs(rows[0]["has_slice"], True)

  def test_FindFull_KeepsTheWholeItem(self) -> None:
    with self.repo() as repo:
      _, out, _ = repo.run("find", "fourth thing", "--in", "title", "--json")
      row = json.loads(out)[0]
      for key in ("fields", "claim", "depends_on"):
        self.assertIn(key, row)

  def test_FindText_IsUnchanged(self) -> None:
    with self.repo() as repo:
      plain = repo.run("find", "off-schema")
      self.assertEqual(
        plain[1],
        "  1  S03   parked  -     2    L    -      22    -         Third thing\n"
        "      matched in body: …yet, but this heading is off-schema. Check None. Git none.\n",
      )
      self.assertEqual(repo.run("find", "off-schema", "--lean"), plain)


if __name__ == "__main__":
  unittest.main()
