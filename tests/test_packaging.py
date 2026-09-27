"""A no-clone install must stay self-contained: shipped templates and entry point.

These are the packaging facts that let `pip install git+...` (no checkout) produce a
working `slicer` with its templates. If a future edit drops them, `init`/`render` break
only for installed users, whom the suite otherwise never exercises. Guard them here.
"""

from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

from slicer import templates

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


class PackagingTests(unittest.TestCase):
  def setUp(self) -> None:
    self.pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))

  def test_PackageData_Templates_AreDeclaredForTheWheel(self) -> None:
    package_data = self.pyproject["tool"]["setuptools"]["package-data"]
    self.assertIn("templates/*.md", package_data["slicer"])

  def test_Scripts_SlicerConsoleEntryPoint_IsDeclared(self) -> None:
    self.assertEqual(self.pyproject["project"]["scripts"]["slicer"], "slicer.cli:main")

  def test_Defaults_EveryBundledTemplate_LoadsThroughThePackage(self) -> None:
    loaded = templates.defaults()
    self.assertEqual(sorted(loaded), sorted(templates.TEMPLATE_NAMES))
    for name, text in loaded.items():
      with self.subTest(template=name):
        self.assertTrue(text.strip(), f"{name} is empty")


if __name__ == "__main__":
  unittest.main()
