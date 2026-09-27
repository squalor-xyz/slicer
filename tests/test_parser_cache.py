"""The argparse parser is built once per process and reused across main calls."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import support

from slicer import cli


class ParserCacheTests(unittest.TestCase):
  def setUp(self) -> None:
    cli._parser = None
    self.addCleanup(lambda: setattr(cli, "_parser", None))

  def test_Main_RepeatedInvocations_BuildTheParserOnce(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      cli._parser = None  # reset so the count starts after any warm-up above
      with patch.object(cli, "build_parser", wraps=cli.build_parser) as spy:
        self.assertEqual(repo.run("list")[0], 0)
        self.assertEqual(repo.run("stats")[0], 0)
      self.assertEqual(spy.call_count, 1)

  def test_CachedParser_ResolvesRootOnEitherSideAndKeepsDispatch(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      root = str(repo.root)
      with redirect_stdout(io.StringIO()):
        # Same cached parser, --root before and after the subcommand, back to back.
        before = cli.main(["--root", root, "stats", "--json"])
        after = cli.main(["stats", "--root", root, "--json"])
        listed = cli.main(["list", "--root", root])
      self.assertEqual((before, after, listed), (0, 0, 0))

  def test_BuildParser_StillReturnsAFreshUsableParser(self) -> None:
    first, second = cli.build_parser(), cli.build_parser()
    self.assertIsNot(first, second)
    args = first.parse_args(["stats"])
    self.assertEqual(args.command, "stats")
    self.assertTrue(hasattr(args, "func"))


if __name__ == "__main__":
  unittest.main()
