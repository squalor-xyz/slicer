"""Project-owned relay briefs must use the normal template lifecycle."""

from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

import support
import test_ai


class RelayBriefTemplateTests(unittest.TestCase):
  def test_AiRelayBrief_ProjectTemplate_UsedAndReset(self):
    case = test_ai.AiRelayBriefTests()
    self.addCleanup(case.doCleanups)
    repo = case.project()
    original = case.brief(repo)[1]
    custom = "ENVIRONMENT\nUse project-interpreter for {{id}}\n"
    with patch("slicer.cli.sys.stdin", io.StringIO(custom)):
      code, out, err = repo.run("template", "set", "relay-brief.md", "--stdin")
    self.assertEqual(code, 0, out + err)
    self.assertEqual(case.payload(repo)["brief"], "ENVIRONMENT\nUse project-interpreter for S01\n")
    repo.run("template", "reset", "relay-brief.md")
    self.assertEqual(case.payload(repo)["brief"], original)

  def test_AiRelayBrief_UnknownPlaceholder_Refused(self):
    with support.TempRepo() as repo:
      repo.run("init")
      with patch("slicer.cli.sys.stdin", io.StringIO("{{unknown}}")):
        code, out, err = repo.run("template", "set", "relay-brief.md", "--stdin", "--json")
      self.assertEqual(code, 2, out + err)
      self.assertEqual(json.loads(out)["error"]["code"], "bad_template", err)
