"""Backticked repo paths in a section body: what counts, and what is dropped."""

from __future__ import annotations

import unittest

import support  # noqa: F401 -- puts src/ on sys.path
from slicer import paths


class PathsTests(unittest.TestCase):
  def test_Paths_ExtractsBacktickedRepoPaths_DropsLineSuffix(self) -> None:
    body = (
      "- `src/slicer/config.py`: the keys, and `src/slicer/ops.py:1848` too\n"
      "- `tests/test_next.py:10-20` and `./docs/agents.md`\n"
      "- `CHANGELOG.md`, `Makefile/` and `src/slicer/config.py` again\n"
      "- not paths: `next --batch`, `files_section`, `[\"CHANGELOG.md\"]`, `0.5`, "
      "plain src/unticked.py\n"
      "- slashes only: `/`, `//`, `./`\n"
    )
    self.assertEqual(paths.extract(body), [
      "src/slicer/config.py",
      "src/slicer/ops.py",
      "tests/test_next.py",
      "docs/agents.md",
      "CHANGELOG.md",
      "Makefile/",
    ])

  def test_Paths_NoBackticks_ReturnsEmpty(self) -> None:
    self.assertEqual(paths.extract(""), [])
    self.assertEqual(paths.extract("only prose, src/a.py"), [])


if __name__ == "__main__":
  unittest.main()
