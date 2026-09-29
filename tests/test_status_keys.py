"""JSON status tallies are keyed by status key, not display label (S133).

The default config renders `open` as "—", so a label-keyed tally would put
open items under "—" -- a key an agent cannot join to an item's `status`.
Text output keeps the labels people read.
"""

from __future__ import annotations

import json
import unittest

import support


class StatusKeyTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.run("add", "open one", "--tree", "core")
    repo.run("add", "done one", "--tree", "core")
    repo.run("set", "S02", "--status", "done")
    return repo

  def test_Stats_Json_KeysByStatusKey(self) -> None:
    with self.repo() as repo:
      payload = json.loads(repo.run("stats", "--json")[1])
      self.assertEqual(payload["by_status"], {"open": 1, "done": 1})
      self.assertEqual(payload["by_tree_status"], {"core": {"open": 1, "done": 1}})

  def test_Status_Json_CensusKeysByStatusKey(self) -> None:
    with self.repo() as repo:
      payload = json.loads(repo.run("status", "--json")[1])
      self.assertEqual(payload["census"]["by_status"], {"open": 1, "done": 1})

  def test_Import_Json_KeysByStatusKey(self) -> None:
    with self.repo() as repo:
      repo.write("outline.md", "# Roadmap\n\n## Fresh\n\n## Old\nstatus: parked\n")
      payload = json.loads(repo.run("import", "outline.md", "--dry-run", "--json")[1])
      self.assertEqual(payload["by_status"], {"open": 1, "parked": 1})

  def test_Text_KeepsTheDisplayLabel(self) -> None:
    with self.repo() as repo:
      stats = repo.run("stats")[1]
      self.assertIn("— 1", stats)
      self.assertNotIn("open 1", stats)
      self.assertIn("— 1", repo.run("status")[1])

  def test_Json_KeysSurviveARelabel(self) -> None:
    with self.repo() as repo:
      path = repo.root / ".slicer" / "config.json"
      config = json.loads(path.read_text(encoding="utf-8"))
      config["statuses"]["open"] = "todo"
      path.write_text(json.dumps(config, indent=2), encoding="utf-8")
      payload = json.loads(repo.run("stats", "--json")[1])
      self.assertEqual(payload["by_status"], {"open": 1, "done": 1})
      self.assertIn("todo 1", repo.run("stats")[1])


if __name__ == "__main__":
  unittest.main()
