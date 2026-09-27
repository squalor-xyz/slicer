"""Parallel branches must not conflict on slicer's append-only history."""

from __future__ import annotations

import json
import unittest

import support

from slicer import store


class GitattributesTests(unittest.TestCase):
  def test_Init_WritesGitattributes_MarkingLogAsUnionMerge(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      text = repo.read(f".slicer/{store.GITATTRIBUTES_NAME}")
      self.assertIn("log.jsonl merge=union", text)


class LogOrderTests(unittest.TestCase):
  def test_Log_UnionInterleavedTimestamps_DisplayNewestFirst(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "one")
      repo.run("add", "two")
      # A union merge can drop an older entry after newer ones in file order.
      old = json.dumps(
        {"when": "2020-01-01T00:00:00Z", "item": "S99",
         "action": "edit", "from": "", "to": "", "note": "OLD"}
      )
      with open(repo.root / ".slicer" / store.LOG_NAME, "a", encoding="utf-8") as fh:
        fh.write(old + "\n")
      _, out, _ = repo.run("log", "--json")
      whens = [e["when"] for e in json.loads(out)]
      self.assertEqual(whens, sorted(whens, reverse=True))
      self.assertEqual(whens[-1], "2020-01-01T00:00:00Z")


class UnionMergeTests(unittest.TestCase):
  def test_ParallelBranches_AppendingHistory_MergeTheLogWithoutConflict(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      repo.run("add", "base")
      repo.commit("base")  # commits .gitattributes so union applies on merge
      base = repo._git("branch", "--show-current").stdout.strip()
      repo._git("checkout", "-q", "-b", "feature")
      repo.run("add", "from feature")
      repo.commit("feature")
      repo._git("checkout", "-q", base)
      repo.run("add", "from mainline")
      repo.commit("mainline")
      # index.json legitimately conflicts (both added an item); the log must not.
      repo._git("merge", "--no-edit", "feature")
      log = repo.read(f".slicer/{store.LOG_NAME}")
      self.assertNotIn("<<<<<<<", log)
      self.assertIn("from feature", log)
      self.assertIn("from mainline", log)


if __name__ == "__main__":
  unittest.main()
