"""`add` and `import` hint when an item is filed unscored (S140): importance
and urgency at the default 2 and no effort, so the queue has nothing to sort on.
The hint never changes stdout, the JSON payload, or the exit code."""

from __future__ import annotations

import json
import unittest

import support


class AddHintTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    return repo

  def test_Add_Unscored_HintsOnStderrOnly(self) -> None:
    with self.repo() as repo:
      code, out, err = repo.run("add", "plain")
      self.assertEqual(code, 0, err)
      self.assertEqual(out.strip(), "added S01  plain")
      for flag in ("--importance", "--urgency", "--effort"):
        self.assertIn(flag, err)
      self.assertIn("S01", err)

  def test_Add_UnscoredJson_PayloadIsUnchanged(self) -> None:
    with self.repo() as repo:
      code, out, err = repo.run("add", "plain", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(json.loads(out)["id"], "S01")
      self.assertIn("unscored", err)

  def test_Add_AnyScoreSet_IsQuiet(self) -> None:
    for flags in (("--effort", "2"), ("--importance", "3"), ("--urgency", "1")):
      with self.subTest(flags=flags), self.repo() as repo:
        code, _, err = repo.run("add", "scored", *flags)
        self.assertEqual(code, 0, err)
        self.assertEqual(err, "")

  def test_Add_DoneItem_IsQuiet(self) -> None:
    with self.repo() as repo:
      _, _, err = repo.run("add", "history", "--status", "done")
      self.assertEqual(err, "")


class ImportHintTests(unittest.TestCase):
  OUTLINE = (
    "# Roadmap\n\n## Bare\n\n## Scored\neffort: 1\n\n## Shipped\nstatus: done\n"
  )

  def test_Import_Unscored_ListedInWarningsOnDryRunAndApply(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.write("outline.md", self.OUTLINE)
      for extra in (("--dry-run",), ()):
        with self.subTest(extra=extra):
          code, out, err = repo.run("import", "outline.md", *extra, "--json")
          self.assertEqual(code, 0, out + err)
          warnings = [w for w in json.loads(out)["warnings"] if "unscored" in w]
          self.assertEqual(len(warnings), 1)
          self.assertIn("'Bare'", warnings[0])
          self.assertNotIn("'Scored'", warnings[0])
          self.assertNotIn("'Shipped'", warnings[0])  # done items do not rank

  def test_Import_AllScored_HasNoHint(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.write("outline.md", "# Roadmap\n\n## Scored\nimportance: 3\n")
      payload = json.loads(repo.run("import", "outline.md", "--dry-run", "--json")[1])
      self.assertFalse(any("unscored" in w for w in payload["warnings"]))


if __name__ == "__main__":
  unittest.main()
