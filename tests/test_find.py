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

  def test_Find_Rows_MatchListFormatForTheSameItems(self) -> None:
    # Every item's title contains "spelled out", so find returns them all in
    # queue order — the same rows `list` prints, guarding the shared formatter.
    with self.repo() as repo:
      _, found, _ = repo.run("find", "spelled out")
      _, listed, _ = repo.run("list")
      self.assertEqual(found, listed)


if __name__ == "__main__":
  unittest.main()
