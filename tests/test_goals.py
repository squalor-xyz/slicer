"""Project direction must survive a JSON round-trip and read back in one call."""

from __future__ import annotations

import json
import unittest

import support
from slicer.model import Index


class IndexDirectionTests(unittest.TestCase):
  def test_ToFromDict_GoalsAndNonGoals_RoundTrip(self) -> None:
    index = Index(goals="- keep it small", non_goals="- not a server")
    restored = Index.from_dict(index.to_dict())
    self.assertEqual(restored.goals, "- keep it small")
    self.assertEqual(restored.non_goals, "- not a server")

  def test_FromDict_MissingKeys_DefaultToEmpty(self) -> None:
    # An index written before S56 has neither key; loading must not fail.
    index = Index.from_dict({"version": 1, "items": []})
    self.assertEqual((index.goals, index.non_goals), ("", ""))


class GoalsCommandTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    return repo

  def test_Goals_Unset_ReportsNoneSetInTextAndEmptyInJson(self) -> None:
    with self.repo() as repo:
      code, text, err = repo.run("goals")
      self.assertEqual((code, err), (0, ""))
      self.assertIn("Goals:", text)
      self.assertIn("(none set)", text)
      code, out, err = repo.run("goals", "--json")
      self.assertEqual((code, err), (0, ""))
      self.assertEqual(json.loads(out), {"goals": "", "non_goals": ""})

  def test_Goals_AfterRecording_ReturnsBothBlocksTogether(self) -> None:
    with self.repo() as repo:
      repo.write("g.md", "- a goal\n")
      repo.write("n.md", "- a non-goal\n")
      repo.run("prose", "edit", "goals", "--file", str(repo.root / "g.md"))
      repo.run("prose", "edit", "non_goals", "--file", str(repo.root / "n.md"))
      code, text, _ = repo.run("goals")
      self.assertIn("- a goal", text)
      self.assertIn("- a non-goal", text)
      code, out, _ = repo.run("goals", "--json")
      self.assertEqual(json.loads(out), {"goals": "- a goal", "non_goals": "- a non-goal"})


if __name__ == "__main__":
  unittest.main()
