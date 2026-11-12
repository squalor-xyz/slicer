"""`Index.get` and `Catalog.get` answer from a keyed dict, never a scan per call (s450).

The dict is a cache over a public list that callers mutate in place, so these tests
pin every mutation pattern the code uses today (append, pop and insert in `ops.move`,
sort, reassignment) against what a linear scan answers, and show a repeated miss
walks the list no more than the one build.
"""

from __future__ import annotations

import copy
import json
import unittest

import support

from slicer import ops, store
from slicer.model import Catalog, CatalogRecord, Index, Item


class CountingList(list):
  """A list that counts how often it is iterated, for the test only."""

  iterations = 0

  def __iter__(self):
    self.iterations += 1
    return super().__iter__()


def scan(items, item_id):
  for it in items:
    if it.id == item_id:
      return it
  return None


def make_item(n: int) -> Item:
  return Item(f"S{n:02d}", f"Item {n}", "open")


def make_record(n: int) -> CatalogRecord:
  return CatalogRecord(f"C{n:02d}", "goal", f"Record {n}")


class IndexGetTests(unittest.TestCase):
  def test_IndexGet_HitMissAndAddedAfterFirstGet_SameAsScan(self) -> None:
    index = Index(items=[make_item(1), make_item(2), make_item(3)])
    self.assertIs(index.get("S02"), scan(index.items, "S02"))
    self.assertIsNone(index.get("S99"))
    index.items.append(make_item(4))
    self.assertIs(index.get("S04"), scan(index.items, "S04"))
    self.assertIsNotNone(index.get("S04"))
    self.assertIsNone(index.get("S99"))

  def test_IndexGet_DuplicateIds_ReturnsTheFirstLikeTheScan(self) -> None:
    first, second = make_item(1), make_item(1)
    index = Index(items=[first, second])
    self.assertIs(index.get("S01"), first)

  def test_IndexGet_AfterMoveSortAndReassign_SameAsScan(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      for title in ("One", "Two", "Three"):
        repo.run("add", title)
      state = store.load(repo.root)
      index = state.index
      ids = [it.id for it in index.items] + ["S99"]

      def agree() -> None:
        for item_id in ids:
          self.assertIs(index.get(item_id), scan(index.items, item_id), item_id)

      agree()  # builds the cache
      ops.move(state, "S03", to=1)  # pop and insert, same length
      agree()
      index.items.sort(key=lambda it: it.id, reverse=True)  # sort, same length
      agree()
      index.items = [it for it in index.items if it.id != "S02"]  # reassignment
      agree()
      self.assertIsNone(index.get("S02"))
      index.items = [make_item(2)] + index.items[:1] + index.items[2:]  # same length, new list
      agree()

  def test_IndexGet_RepeatedMiss_DoesNotScanTheItems(self) -> None:
    index = Index()
    index.items = CountingList(make_item(n) for n in range(1000))
    self.assertIsNone(index.get("missing"))
    after_first = index.items.iterations
    self.assertIsNone(index.get("missing"))
    self.assertEqual(index.items.iterations, after_first)

  def test_IndexGet_IdRewrittenInPlace_NeverReturnsTheWrongItem(self) -> None:
    item = make_item(1)
    index = Index(items=[item, make_item(2)])
    self.assertIs(index.get("S01"), item)
    item.id = "S77"
    self.assertIsNone(index.get("S01"))

  def test_Index_ToDictAndEquality_IgnoreTheCache(self) -> None:
    warm, cold = Index(items=[make_item(1), make_item(2)]), Index(items=[make_item(1), make_item(2)])
    before = json.dumps(warm.to_dict())
    repr_before = repr(warm)
    warm.get("S01")
    warm.get("S99")
    self.assertEqual(json.dumps(warm.to_dict()), before)
    self.assertEqual(repr(warm), repr_before)
    self.assertEqual(warm, cold)
    self.assertNotIn("lookup", " ".join(warm.to_dict()))

  def test_Index_DeepCopyAfterGet_DoesNotShareTheCache(self) -> None:
    index = Index(items=[make_item(1), make_item(2)])
    index.get("S01")
    draft = copy.deepcopy(index)
    self.assertIs(draft.get("S01"), draft.items[0])
    self.assertIsNot(draft.get("S01"), index.items[0])
    draft.items.append(make_item(3))
    self.assertIsNone(index.get("S03"))
    self.assertIsNotNone(draft.get("S03"))


class CatalogGetTests(unittest.TestCase):
  def test_CatalogGet_HitMissAddedAndRemoved_SameAsScan(self) -> None:
    catalog = Catalog(records=[make_record(1), make_record(2), make_record(3)])
    self.assertIs(catalog.get("C02"), scan(catalog.records, "C02"))
    self.assertIsNone(catalog.get("C99"))
    catalog.records.append(make_record(4))
    self.assertIs(catalog.get("C04"), scan(catalog.records, "C04"))
    self.assertIsNotNone(catalog.get("C04"))
    catalog.records = [r for r in catalog.records if r.id != "C02"]
    self.assertIsNone(catalog.get("C02"))
    self.assertIs(catalog.get("C03"), scan(catalog.records, "C03"))
    catalog.records = [make_record(2)] + catalog.records[:1] + catalog.records[2:]
    self.assertIs(catalog.get("C02"), scan(catalog.records, "C02"))

  def test_CatalogGet_RepeatedMiss_DoesNotScanTheRecords(self) -> None:
    catalog = Catalog()
    catalog.records = CountingList(make_record(n) for n in range(1000))
    self.assertIsNone(catalog.get("missing"))
    after_first = catalog.records.iterations
    self.assertIsNone(catalog.get("missing"))
    self.assertEqual(catalog.records.iterations, after_first)

  def test_Catalog_ToDictAndEquality_IgnoreTheCache(self) -> None:
    warm, cold = Catalog(records=[make_record(1)]), Catalog(records=[make_record(1)])
    before = warm.to_dict()
    repr_before = repr(warm)
    warm.get("C01")
    self.assertEqual(warm.to_dict(), before)
    self.assertEqual(repr(warm), repr_before)
    self.assertEqual(warm, cold)


if __name__ == "__main__":
  unittest.main()
