"""The derived pointers, and the line they must reproduce exactly."""

from __future__ import annotations

import json
import unittest

import support

from slicer import sync
from slicer.config import Config
from slicer.importer import build

NEXT = "**S13** atlas tables must split behind the data view"
LATER = "S14; parked S09/S10; later S11; atlas ROADMAP 3–4; beacon parsers; lasso"
SUFFIX = "atlas ROADMAP 3–4; beacon parsers; lasso"


def fixture_config() -> Config:
  cfg = Config()
  cfg.exclude_flags = ["OWNER"]
  cfg.later["suffix"] = SUFFIX
  return cfg


class SyncTests(unittest.TestCase):
  def setUp(self) -> None:
    self.cfg = fixture_config()
    self.index, _, _ = build(support.LEGACY, self.cfg)

  def test_Next_FixtureIndex_NamesFirstOpenRowInTableOrder(self) -> None:
    self.assertEqual(sync.next_pointer(self.index, self.cfg), NEXT)

  def test_Later_FixtureIndex_MatchesTheLegacyOrdering(self) -> None:
    self.assertEqual(sync.later_pointer(self.index, self.cfg), LATER)

  def test_StatusLine_MigratedFixtureState_MatchesLegacyByteForByte(self) -> None:
    want = (support.LEGACY / "status-line.txt").read_text(encoding="utf-8").rstrip("\n")
    template = want.replace(NEXT, "{{next}}").replace(LATER, "{{later}}")
    target = sync.SyncTarget(
      name="plan", path="docs/implementation-plan.md", match="^Status:.*$", template=template
    )
    self.assertEqual(sync.render_target(self.index, self.cfg, target), want)

  def test_Next_NoOpenItems_UsesTheEmptyPhrase(self) -> None:
    for item in self.index.items:
      item.status = "done"
    self.assertEqual(sync.next_pointer(self.index, self.cfg), self.cfg.next_empty)

  def test_Later_SingleOpenItem_OmitsTheOpenGroup(self) -> None:
    for item in self.index.items:
      if item.status == "open" and item.id != "S13":
        item.status = "done"
    self.assertFalse(sync.later_pointer(self.index, self.cfg).startswith("S14"))

  def test_Next_OwnerFlaggedRow_IsExcludedFromThePointer(self) -> None:
    flagged = next(i for i in self.index.items if "OWNER" in i.flags)
    flagged.status = "open"
    self.index.items.remove(flagged)
    self.index.items.insert(0, flagged)
    self.assertEqual(sync.next_pointer(self.index, self.cfg), NEXT)

  def test_Next_BlockedFirstOpenItem_IsStillNamedBySync(self) -> None:
    # `slicer next` skips a blocked item; the written pointer deliberately
    # does not, because it reproduces a queue a human reads top-down.
    self.index.require("S13").depends_on = ["S14"]
    self.assertEqual(sync.next_pointer(self.index, self.cfg), NEXT)

  def test_SyncCheck_StaleLine_ReportsDriftAndWritesNothing(self) -> None:
    with support.TempRepo() as repo:
      self._prepare(repo)
      code, out, _ = repo.run("sync", "--check")
      self.assertEqual(code, 1)
      self.assertIn("stale", out)
      self.assertIn("STALE", repo.read("docs/implementation-plan.md"))

  def test_Sync_StaleLine_RewritesItThenChecksClean(self) -> None:
    with support.TempRepo() as repo:
      self._prepare(repo)
      self.assertEqual(repo.run("sync")[0], 0)
      want = (support.LEGACY / "status-line.txt").read_text(encoding="utf-8").rstrip("\n")
      lines = repo.read("docs/implementation-plan.md").splitlines()
      self.assertIn(want, lines)
      self.assertEqual(repo.run("sync", "--check")[0], 0)

  def _prepare(self, repo: support.TempRepo) -> None:
    repo.copy_legacy()
    repo.run("init")
    want = (support.LEGACY / "status-line.txt").read_text(encoding="utf-8").rstrip("\n")
    template = want.replace(NEXT, "{{next}}").replace(LATER, "{{later}}")
    path = repo.root / ".slicer/config.json"
    cfg = json.loads(path.read_text())
    cfg["exclude_flags"] = ["OWNER"]
    cfg["pointers"]["later"]["suffix"] = SUFFIX
    cfg["sync"] = {
      "targets": [
        {
          "name": "plan",
          "path": "docs/implementation-plan.md",
          "match": "^Status:.*$",
          "count": 1,
          "template": template,
        }
      ]
    }
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    repo.write("docs/implementation-plan.md", "# Plan\n\nStatus: STALE\n\nbody\n")
    repo.run("import", "--from", "docs/slices")


if __name__ == "__main__":
  unittest.main()
