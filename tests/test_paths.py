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
    self.assertEqual(paths.extract(body, top_level={"CHANGELOG.md"}), [
      "src/slicer/config.py",
      "src/slicer/ops.py",
      "tests/test_next.py",
      "docs/agents.md",
      "CHANGELOG.md",
      "Makefile/",
    ])

  def test_Extract_IdentifierTokens_NotPaths(self) -> None:
    for token in ("os.path", "os.fsync", "Config.load", "and/or"):
      with self.subTest(token=token):
        self.assertEqual(paths.extract(f"`{token}`"), [])

  def test_Extract_RepoPaths_Preserved(self) -> None:
    for token, expected in (
      ("src/slicer/paths.py", "src/slicer/paths.py"),
      ("docs/configuration.md", "docs/configuration.md"),
      ("CHANGELOG.md", "CHANGELOG.md"),
      ("tests/fixtures/legacy/", "tests/fixtures/legacy/"),
      ("src/slicer/paths.py:12", "src/slicer/paths.py"),
    ):
      with self.subTest(token=token):
        self.assertEqual(paths.extract(f"`{token}`", top_level={"CHANGELOG.md"}), [expected])

  def test_Extract_KnownTopLevel_ExtensionlessPaths(self) -> None:
    self.assertEqual(paths.extract("`assets/template`", top_level={"assets"}), ["assets/template"])

  def test_Extract_KnownTopLevel_BareNamesNeedExtension(self) -> None:
    top_level = {"tests", "src", "LICENSE", "CHANGELOG.md"}
    for token, expected in (
      ("tests", []),
      ("src", []),
      ("LICENSE", []),
      ("CHANGELOG.md", ["CHANGELOG.md"]),
      ("tests/", ["tests/"]),
    ):
      with self.subTest(token=token):
        self.assertEqual(paths.extract(f"`{token}`", top_level=top_level), expected)

  def test_Paths_NoBackticks_ReturnsEmpty(self) -> None:
    self.assertEqual(paths.extract(""), [])
    self.assertEqual(paths.extract("only prose, src/a.py"), [])


if __name__ == "__main__":
  unittest.main()
