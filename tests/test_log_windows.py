"""History windows compare instants while keeping the existing display contract."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import support
from slicer import store


class LogWindowTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    return repo

  def history(self, repo: support.TempRepo, entries: list[dict]) -> None:
    repo.write(".slicer/log.jsonl", "".join(json.dumps(e) + "\n" for e in entries))

  def entry(self, when, note: str, **fields) -> dict:
    return {"when": when, "item": "S01", "action": "edit", "note": note,
            "by": "owner", **fields}

  def logs(self, repo: support.TempRepo, *args: str) -> list[dict]:
    code, out, err = repo.run("log", *args, "--json")
    self.assertEqual(code, 0, err)
    return json.loads(out)

  def test_Log_Bounds_IncludeSinceAndExcludeUntil(self) -> None:
    with self.repo() as repo:
      entries = [self.entry(f"2026-10-05T12:00:0{i}Z", str(i)) for i in range(4)]
      self.history(repo, entries)
      self.assertEqual([e["note"] for e in self.logs(
        repo, "--since", entries[1]["when"], "--until", entries[3]["when"],
      )], ["2", "1"])
      self.assertEqual([e["note"] for e in self.logs(
        repo, "--since", entries[2]["when"],
      )], ["3", "2"])
      self.assertEqual([e["note"] for e in self.logs(
        repo, "--until", entries[2]["when"],
      )], ["1", "0"])

  def test_Log_OffsetsAndFractions_CompareInstantsAndPreserveStrings(self) -> None:
    with self.repo() as repo:
      entries = [
        self.entry("2026-10-05T08:00:00.125-04:00", "lower"),
        self.entry("2026-10-05T14:00:00.250+02:00", "inside"),
        self.entry("2026-10-05T12:00:00.500Z", "upper"),
        self.entry("2026-10-05T13:00:00+02:00", "before"),
      ]
      self.history(repo, entries)
      logs = self.logs(repo, "--since", "2026-10-05T12:00:00.125Z",
                       "--until", "2026-10-05T08:00:00.500-04:00")
      self.assertEqual({e["note"] for e in logs}, {"lower", "inside"})
      self.assertEqual([e["when"] for e in logs],
                       sorted([e["when"] for e in entries[:2]], reverse=True))

  def test_Log_UnionOrderAndTies_FiltersBeforeLimit(self) -> None:
    with self.repo() as repo:
      entries = [
        self.entry("2026-10-05T12:00:03Z", "first tie"),
        self.entry("2026-10-05T12:00:04Z", "other item", item="S02"),
        self.entry("2026-10-05T12:00:05Z", "other action", action="set"),
        self.entry("2026-10-05T12:00:06Z", "other owner", by="someone"),
        self.entry("2026-10-05T12:00:03Z", "last tie"),
        self.entry("2026-10-05T12:00:01Z", "old appended last"),
      ]
      self.history(repo, entries)
      logs = self.logs(repo, "--item", "S01", "--action", "edit", "--by", "owner",
                       "--since", "2026-10-05T12:00:02Z", "--limit", "2")
      self.assertEqual([e["note"] for e in logs], ["last tie", "first tie"])

  def test_Log_EqualAndEmptyWindows_Succeed(self) -> None:
    with self.repo() as repo:
      self.history(repo, [self.entry("2026-10-05T12:00:00Z", "one")])
      self.assertEqual(self.logs(repo, "--since", "2026-10-05T12:00:00Z",
                                 "--until", "2026-10-05T08:00:00-04:00"), [])
      self.assertEqual(self.logs(repo, "--since", "2027-01-01T00:00:00Z"), [])
      code, out, err = repo.run("log", "--until", "2020-01-01T00:00:00Z")
      self.assertEqual(code, 0, err)
      self.assertIn("no history for until", out)
      self.history(repo, [])
      self.assertEqual(self.logs(repo, "--since", "2026-10-05T12:00:00Z"), [])

  def test_Log_InvalidBounds_UsageBeforeHistoryReadAndNoWrites(self) -> None:
    cases = [
      ("--since", "bad"), ("--until", ""),
      ("--since", "2026-10-05T12:00:00"), ("--until", "2026-10-05"),
      ("--since", "2026-10-05T12:00:01Z", "--until", "2026-10-05T12:00:00Z"),
      ("--since", "0001-01-01T00:00:00+01:00"),
    ]
    with self.repo() as repo:
      self.history(repo, [self.entry("bad", "invalid history")])
      before = {p: p.read_bytes() for p in (repo.root / ".slicer").rglob("*") if p.is_file()}
      with patch.object(store.State, "history", side_effect=AssertionError("read history")):
        for args in cases:
          with self.subTest(args=args):
            code, out, err = repo.run("log", *args, "--json")
            self.assertEqual(code, 2, err)
            self.assertEqual(json.loads(out)["error"]["code"], "usage")
            self.assertNotIn("Traceback", err)
      self.assertEqual(before, {p: p.read_bytes() for p in before})

  def test_Log_InvalidStoredTimestamp_CorruptOnlyWhenEncounteredInWindowFilter(self) -> None:
    for bad in ("bad", "2026-10-05T12:00:00", "", None, 123,
                "9999-12-31T23:59:59-01:00"):
      with self.subTest(bad=bad), self.repo() as repo:
        self.history(repo, [self.entry(bad, "broken")])
        before = repo.read(".slicer/log.jsonl")
        code, out, err = repo.run("log", "--since", "2026-10-05T12:00:00Z", "--json")
        self.assertEqual(code, 3, err)
        error = json.loads(out)["error"]
        self.assertEqual(error["code"], "corrupt")
        self.assertIn("log.jsonl", error["message"])
        self.assertIn("S01", error["message"])
        self.assertNotIn("Traceback", err)
        self.assertEqual(repo.read(".slicer/log.jsonl"), before)
        self.assertEqual(self.logs(repo, "--item", "S02", "--since",
                                   "2026-10-05T12:00:00Z"), [])

  def test_Log_NoBounds_PreservesLegacyTimestampBehavior(self) -> None:
    with self.repo() as repo:
      entries = [self.entry("old timestamp", "first"), self.entry("old timestamp", "last")]
      self.history(repo, entries)
      self.assertEqual([e["note"] for e in self.logs(repo)], ["last", "first"])


if __name__ == "__main__":
  unittest.main()
