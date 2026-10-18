"""`slicer template`: render templates shown, replaced and reset without opening the files."""

from __future__ import annotations

import io
import json
import os
import unittest
from unittest.mock import patch

import support

from slicer import store, templates
from slicer.errors import RenderError

NAMES = ("slice.md", "roadmap.md", "row.md")
# The default row with the status cell put first of two in one cell: same arity,
# only known placeholders, different bytes.
ALT_ROW = (
  "| {{position}} | {{idcell}} | {{title}} | {{size}} | {{effort}} | {{trees}} "
  "| {{findings}} | {{status}} {{id}} |\n"
)


def _tracking_bytes(repo: support.TempRepo) -> dict[str, bytes]:
  return {
    str(p.relative_to(repo.root)): p.read_bytes()
    for p in sorted((repo.root / ".slicer").rglob("*")) if p.is_file() and p.name != "lock"
  }


def _error(out: str) -> dict:
  return json.loads(out)["error"]


def _template(repo: support.TempRepo, name: str) -> bytes:
  return (repo.root / ".slicer/templates" / name).read_bytes()


def _log_pairs(repo: support.TempRepo) -> list[tuple[str, str]]:
  """(from, to) of every template history entry, oldest first (`log` lists newest first)."""
  _, out, _ = repo.run("log", "--action", "template", "--json")
  entries = json.loads(out)
  return [(e["from"], e["to"]) for e in reversed(entries)]


def _set(repo: support.TempRepo, name: str, text: str, *flags: str) -> tuple[int, str, str]:
  with patch("slicer.cli.sys.stdin", io.StringIO(text)):
    return repo.run("template", "set", name, "--stdin", *flags)


class TemplateCommandTests(unittest.TestCase):
  def repo(self, *, items: bool = True) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    if items:
      repo.run("add", "one")
    return repo

  # ---- list -------------------------------------------------------------

  def test_TemplateList_FreshInit_NamesEveryTemplateAsNotCustomized(self) -> None:
    with self.repo() as repo:
      code, out, err = repo.run("template", "list", "--json")
      self.assertEqual(code, 0, err)
      rows = json.loads(out)
      self.assertEqual([r["name"] for r in rows], list(templates.TEMPLATE_NAMES))
      self.assertEqual([r["customized"] for r in rows], [False, False, False, False])

  def test_TemplateList_AfterSetThatChangesBytes_MarksOnlyThatTemplate(self) -> None:
    with self.repo() as repo:
      self.assertEqual(_set(repo, "row.md", ALT_ROW)[0], 0)
      _, out, _ = repo.run("template", "list", "--json")
      self.assertEqual(
        {r["name"]: r["customized"] for r in json.loads(out)},
        {"slice.md": False, "roadmap.md": False, "row.md": True, "relay-brief.md": False},
      )

  def test_TemplateList_Text_SaysDefaultOrCustomizedPerName(self) -> None:
    with self.repo() as repo:
      _set(repo, "row.md", ALT_ROW)
      code, out, _ = repo.run("template", "list")
      self.assertEqual(code, 0)
      lines = {line.split()[0]: line.split()[1:] for line in out.splitlines()}
      self.assertEqual(lines["row.md"], ["customized"])
      self.assertEqual(lines["slice.md"], ["default"])

  def test_TemplateList_MissingFile_ReportsMissingAndStillLists(self) -> None:
    with self.repo() as repo:
      (repo.root / ".slicer/templates/slice.md").unlink()
      code, out, err = repo.run("template", "list", "--json")
      self.assertEqual(code, 0, err)
      row = {r["name"]: r for r in json.loads(out)}["slice.md"]
      self.assertEqual((row["customized"], row["missing"]), (False, True))
      self.assertIn("missing", repo.run("template", "list")[1])

  def test_TemplateList_NewDefaultMissing_NotCustomized(self) -> None:
    with self.repo() as repo:
      (repo.root / ".slicer/templates/relay-brief.md").unlink()
      code, out, err = repo.run("template", "list", "--json")
      self.assertEqual(code, 0, err)
      rows = {r["name"]: r for r in json.loads(out)}
      self.assertEqual((rows["relay-brief.md"]["customized"], rows["relay-brief.md"]["missing"]), (False, True))
      self.assertEqual([r["customized"] for r in rows.values()], [False, False, False, False])

  # ---- show -------------------------------------------------------------

  def test_TemplateShow_ProjectFile_PrintsItByteForByte(self) -> None:
    with self.repo() as repo:
      body = "# {{id}} \r\n\r\n{{sections}}"  # no final newline, CRLF kept
      path = repo.root / ".slicer/templates/slice.md"
      path.write_bytes(body.encode("utf-8"))
      code, out, err = repo.run("template", "show", "slice.md")
      self.assertEqual((code, out, err), (0, body, ""))
      code, out, _ = repo.run("template", "show", "row.md")
      self.assertEqual(out, _template(repo, "row.md").decode("utf-8"))

  def test_TemplateShow_Default_PrintsTheBuiltInNotTheProjectFile(self) -> None:
    with self.repo() as repo:
      _set(repo, "row.md", ALT_ROW)
      code, out, _ = repo.run("template", "show", "row.md", "--default")
      self.assertEqual((code, out), (0, templates.default("row.md")))

  def test_TemplateShow_Json_IsNameAndText(self) -> None:
    with self.repo() as repo:
      code, out, _ = repo.run("template", "show", "row.md", "--json")
      self.assertEqual(code, 0)
      self.assertEqual(json.loads(out), {"name": "row.md", "text": templates.default("row.md")})

  def test_TemplateShow_UnknownName_IsUnknownTemplateAndListsTheNames(self) -> None:
    with self.repo() as repo:
      for flags in ((), ("--default",)):
        with self.subTest(flags=flags):
          code, out, _ = repo.run("template", "show", "nope.md", "--json", *flags)
          self.assertEqual(code, 2)
          err = _error(out)
          self.assertEqual(err["code"], "unknown_template")
          for name in NAMES:
            self.assertIn(name, err["message"])

  def test_TemplateShow_MissingFile_PointsAtTemplateReset(self) -> None:
    with self.repo() as repo:
      (repo.root / ".slicer/templates/row.md").unlink()
      code, out, _ = repo.run("template", "show", "row.md", "--json")
      self.assertEqual(code, 2)
      self.assertIn("slicer template reset row.md", _error(out)["message"])
      self.assertNotIn("init --force", _error(out)["message"])

  # ---- set --------------------------------------------------------------

  def test_TemplateSet_Stdin_ReplacesTheFileAndLogsOneEntry(self) -> None:
    with self.repo() as repo:
      code, out, err = _set(repo, "row.md", ALT_ROW)
      self.assertEqual(code, 0, err)
      self.assertEqual(_template(repo, "row.md"), ALT_ROW.encode("utf-8"))
      _, out, _ = repo.run("log", "--action", "template", "--json")
      entries = json.loads(out)
      self.assertEqual(len(entries), 1)
      self.assertEqual((entries[0]["item"], entries[0]["action"]), ("template.row.md", "template"))
      self.assertEqual((entries[0]["from"], entries[0]["to"]), ("default", "custom"))

  def test_TemplateSet_File_ReadsBytesExactly(self) -> None:
    with self.repo() as repo:
      src = repo.root / "mine.md"
      want = ALT_ROW.replace("\n", "\r\n").encode("utf-8")
      src.write_bytes(want)
      code, _, err = repo.run("template", "set", "row.md", "--file", str(src))
      self.assertEqual(code, 0, err)
      self.assertEqual(_template(repo, "row.md"), want)

  def test_TemplateSet_ThenRenderAndCheck_AreClean(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      _set(repo, "row.md", ALT_ROW)
      self.assertEqual(repo.run("render")[0], 0)
      code, out, err = repo.run("check")
      self.assertEqual(code, 0, out + err)
      self.assertIn("S01 |", repo.read(".slicer/render/ROADMAP.md"))

  def test_TemplateSet_Json_ReportsNameAndChanged(self) -> None:
    with self.repo() as repo:
      with patch("slicer.cli.sys.stdin", io.StringIO(ALT_ROW)):
        code, out, _ = repo.run("template", "set", "row.md", "--stdin", "--json")
      self.assertEqual(code, 0)
      self.assertEqual(
        json.loads(out), {"name": "row.md", "changed": True, "customized": True})

  def test_TemplateSet_UnknownPlaceholder_IsBadTemplateAndWritesNothing(self) -> None:
    # An empty project, so no row and no slice is ever expanded: the check cannot
    # lean on the project's own data to find the placeholder.
    with self.repo(items=False) as repo:
      before = _tracking_bytes(repo)
      for name in NAMES:
        for flags in ((), ("--render",), ("--render", "--strict")):
          with self.subTest(name=name, flags=flags):
            code, out, _ = _set(repo, name, "{{nope}}\n", "--json", *flags)
            self.assertEqual(code, 2)
            err = _error(out)
            self.assertEqual(err["code"], "bad_template")
            self.assertIn("nope", err["message"])
            self.assertIn(name, err["message"])
            self.assertEqual(_tracking_bytes(repo), before)

  def test_TemplateSet_UnknownPlaceholderWithItems_WritesNothing(self) -> None:
    with self.repo() as repo:
      before = _tracking_bytes(repo)
      code, out, _ = _set(repo, "row.md", "{{position}} {{typo}}\n", "--json")
      self.assertEqual((code, _error(out)["code"]), (2, "bad_template"))
      self.assertEqual(_tracking_bytes(repo), before)

  def test_TemplateSet_RowWithTheWrongNumberOfCells_IsBadTemplateAndWritesNothing(self) -> None:
    with self.repo() as repo:
      before = _tracking_bytes(repo)
      code, out, _ = _set(repo, "row.md", "| {{position}} | {{title}} |\n", "--json")
      self.assertEqual((code, _error(out)["code"]), (2, "bad_template"))
      self.assertIn("cells", _error(out)["message"])
      self.assertEqual(_tracking_bytes(repo), before)

  def test_TemplateSet_RowWithTheWrongNumberOfCellsAndNoItems_IsBadTemplate(self) -> None:
    with self.repo(items=False) as repo:
      before = _tracking_bytes(repo)
      code, out, _ = _set(repo, "row.md", "| {{position}} | {{idcell}} |\n", "--json")
      self.assertEqual((code, _error(out)["code"]), (2, "bad_template"))
      self.assertIn("cells", _error(out)["message"])
      self.assertEqual(_tracking_bytes(repo), before)
      repo.run("add", "one")
      self.assertEqual(repo.run("render")[0], 0)

  def test_TemplateSet_NonUtf8Stdin_IsIoAndWritesNothing(self) -> None:
    class BadStdin:
      def read(self) -> str:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    with self.repo() as repo:
      before = _tracking_bytes(repo)
      with patch("slicer.cli.sys.stdin", BadStdin()):
        code, out, _ = repo.run("template", "set", "row.md", "--stdin", "--json")
      self.assertEqual((code, _error(out)["code"]), (3, "io"))
      self.assertEqual(_tracking_bytes(repo), before)

  def test_TemplateSet_OtherTemplateMissing_StillSetsThisOne(self) -> None:
    with self.repo() as repo:
      (repo.root / ".slicer/templates/slice.md").unlink()
      (repo.root / ".slicer/templates/roadmap.md").unlink()
      code, _, err = _set(repo, "row.md", ALT_ROW)
      self.assertEqual(code, 0, err)
      self.assertEqual(_template(repo, "row.md"), ALT_ROW.encode("utf-8"))

  def test_TemplateSet_NeitherOrBothSources_IsUsage(self) -> None:
    with self.repo() as repo:
      before = _tracking_bytes(repo)
      code, out, _ = repo.run("template", "set", "row.md", "--json")
      self.assertEqual((code, _error(out)["code"]), (2, "usage"))
      src = repo.root / "t.md"
      src.write_text(ALT_ROW)
      with patch("slicer.cli.sys.stdin", io.StringIO(ALT_ROW)):
        code, out, _ = repo.run(
          "template", "set", "row.md", "--file", str(src), "--stdin", "--json")
      self.assertEqual((code, _error(out)["code"]), (2, "usage"))
      self.assertEqual(_tracking_bytes(repo), before)

  def test_TemplateSet_UnknownName_IsUnknownTemplate(self) -> None:
    with self.repo() as repo:
      before = _tracking_bytes(repo)
      code, out, _ = _set(repo, "other.md", ALT_ROW, "--json")
      self.assertEqual((code, _error(out)["code"]), (2, "unknown_template"))
      self.assertEqual(_tracking_bytes(repo), before)

  def test_TemplateSet_SameBytesAsTheFile_WritesAndLogsNothing(self) -> None:
    with self.repo() as repo:
      before = _tracking_bytes(repo)
      for flags in ((), ("--render",), ("--render", "--strict")):
        with self.subTest(flags=flags):
          code, out, err = _set(repo, "row.md", templates.default("row.md"), "--json", *flags)
          self.assertEqual(code, 0, err)
          self.assertEqual(
            json.loads(out), {"name": "row.md", "changed": False, "customized": False})
          self.assertEqual(_tracking_bytes(repo), before)

  def test_TemplateWrite_Text_SaysWhatHappenedAndHintsAtRender(self) -> None:
    with self.repo() as repo:
      self.assertEqual(
        _set(repo, "row.md", ALT_ROW)[1], "replaced row.md; run `slicer render`\n")
      self.assertEqual(
        repo.run("template", "reset", "row.md")[1], "reset row.md; run `slicer render`\n")
      self.assertEqual(
        repo.run("template", "reset", "row.md")[1], "row.md is already the default\n")
      self.assertEqual(
        _set(repo, "row.md", ALT_ROW, "--render")[1].splitlines()[0], "replaced row.md")
      self.assertEqual(
        _set(repo, "row.md", ALT_ROW)[1], "row.md is already that text\n")

  def test_TemplateOps_UnknownName_IsUnknownTemplate(self) -> None:
    from slicer import ops
    from slicer.errors import StateError
    with self.repo() as repo:
      state = repo.state()
      for call in (ops.template_set, ops.template_reset):
        with self.subTest(call=call.__name__):
          args = ("x.md", "text") if call is ops.template_set else ("x.md",)
          with self.assertRaises(StateError) as caught:
            call(state, *args)
          self.assertEqual(caught.exception.code, "unknown_template")

  def test_TemplateSet_ToTheDefaultText_IsNotCustomized(self) -> None:
    with self.repo() as repo:
      _set(repo, "row.md", ALT_ROW)
      code, out, _ = _set(repo, "row.md", templates.default("row.md"), "--json")
      self.assertEqual(code, 0)
      self.assertEqual(
        json.loads(out), {"name": "row.md", "changed": True, "customized": False})
      self.assertEqual(_log_pairs(repo), [("default", "custom"), ("custom", "default")])

  def test_TemplateSet_StrictRender_RendersTheNewTemplateAndCheckIsClean(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      code, out, err = _set(repo, "row.md", ALT_ROW, "--render", "--strict")
      self.assertEqual(code, 0, err)
      self.assertIn("rendered", out)
      self.assertEqual(repo.run("check")[0], 0)
      self.assertEqual(_template(repo, "row.md"), ALT_ROW.encode("utf-8"))
      self.assertIn("| — S01 |", repo.read(".slicer/render/ROADMAP.md"))

  def test_TemplateSet_RenderAfter_RendersTheNewTemplate(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      code, out, err = _set(repo, "row.md", ALT_ROW, "--render")
      self.assertEqual(code, 0, err)
      self.assertIn("rendered", out)
      self.assertEqual(repo.run("check")[0], 0)

  def test_TemplateSet_WithoutRender_CheckReportsDrift(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      _set(repo, "row.md", ALT_ROW)
      self.assertEqual(repo.run("check")[0], 1)

  def test_TemplateSet_StrictRenderFailure_LeavesTheFileAndLogAlone(self) -> None:
    with self.repo() as repo:
      before = _tracking_bytes(repo)
      with patch("slicer.cli.render.plan", side_effect=RenderError("boom")):
        code, out, _ = _set(repo, "row.md", ALT_ROW, "--render", "--strict")
      self.assertEqual(code, 2)
      self.assertEqual(out, "")
      self.assertEqual(_tracking_bytes(repo), before)

  def test_TemplateSet_NonUtf8File_IsIoAndWritesNothing(self) -> None:
    with self.repo() as repo:
      src = repo.root / "bad.md"
      src.write_bytes(b"\xff\xfe{{id}}")
      before = _tracking_bytes(repo)
      code, out, _ = repo.run("template", "set", "row.md", "--file", str(src), "--json")
      self.assertEqual((code, _error(out)["code"]), (3, "io"))
      self.assertEqual(_tracking_bytes(repo), before)

  # ---- reset ------------------------------------------------------------

  def test_TemplateReset_AfterSet_RestoresTheDefaultBytes(self) -> None:
    with self.repo() as repo:
      _set(repo, "slice.md", "# {{id}}\n\n{{sections}}\n")
      code, out, err = repo.run("template", "reset", "slice.md", "--json")
      self.assertEqual(code, 0, err)
      self.assertEqual(
        json.loads(out), {"name": "slice.md", "changed": True, "customized": False})
      self.assertEqual(_template(repo, "slice.md"), templates.default("slice.md").encode("utf-8"))
      self.assertEqual(_log_pairs(repo), [("default", "custom"), ("custom", "default")])

  def test_TemplateReset_DeletedTemplate_IsRecreatedAndRenderWorksAgain(self) -> None:
    with self.repo() as repo:
      (repo.root / ".slicer/templates/row.md").unlink()
      self.assertEqual(repo.run("render")[0], 2)  # "template missing"
      code, _, err = repo.run("template", "reset", "row.md")
      self.assertEqual(code, 0, err)
      self.assertEqual(_template(repo, "row.md"), templates.default("row.md").encode("utf-8"))
      self.assertEqual(repo.run("render")[0], 0)
      self.assertEqual(repo.run("check")[0], 0)
      self.assertEqual(_log_pairs(repo), [("missing", "default")])

  def test_TemplateReset_MissingTemplateMessage_NamesTemplateReset(self) -> None:
    with self.repo() as repo:
      (repo.root / ".slicer/templates/row.md").unlink()
      code, out, _ = repo.run("render", "--json")
      message = _error(out)["message"]
      self.assertIn("`slicer template reset row.md`", message)
      self.assertNotIn("init --force", message)

  def test_TemplateReset_AlreadyDefault_WritesAndLogsNothing(self) -> None:
    with self.repo() as repo:
      before = _tracking_bytes(repo)
      for flags in ((), ("--render",), ("--render", "--strict")):
        with self.subTest(flags=flags):
          code, out, err = repo.run("template", "reset", "row.md", "--json", *flags)
          self.assertEqual(code, 0, err)
          self.assertEqual(
            json.loads(out), {"name": "row.md", "changed": False, "customized": False})
          self.assertEqual(_tracking_bytes(repo), before)

  def test_TemplateReset_AlreadyDefaultWithRender_RendersNothing(self) -> None:
    with self.repo() as repo:
      code, out, _ = repo.run("template", "reset", "row.md", "--render")
      self.assertEqual(code, 0)
      self.assertNotIn("rendered", out)
      self.assertIn("already", out)

  def test_TemplateReset_UnknownName_IsUnknownTemplate(self) -> None:
    with self.repo() as repo:
      code, out, _ = repo.run("template", "reset", "nope.md", "--json")
      self.assertEqual((code, _error(out)["code"]), (2, "unknown_template"))

  def test_TemplateReset_StrictRender_RestoresRenderToo(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      _set(repo, "row.md", ALT_ROW, "--render")
      code, out, err = repo.run("template", "reset", "row.md", "--render", "--strict")
      self.assertEqual(code, 0, err)
      self.assertEqual(repo.run("check")[0], 0)
      self.assertEqual(_template(repo, "row.md"), templates.default("row.md").encode("utf-8"))

  # ---- lock and merge ---------------------------------------------------

  def test_Template_LockHeld_WritesAreLockedAndReadsStillWork(self) -> None:
    if store.fcntl is None:  # pragma: no cover - POSIX only
      self.skipTest("no flock on this platform")
    with self.repo() as repo:
      _set(repo, "row.md", ALT_ROW)
      before = _tracking_bytes(repo)
      with store.project_lock(repo.root), patch.dict(os.environ, {"SLICER_LOCK_TIMEOUT": "0"}):
        code, out, _ = _set(repo, "row.md", templates.default("row.md"), "--json")
        self.assertEqual((code, _error(out)["code"]), (3, "locked"))
        code, out, _ = repo.run("template", "reset", "row.md", "--json")
        self.assertEqual((code, _error(out)["code"]), (3, "locked"))
        self.assertEqual(repo.run("template", "list")[0], 0)
        self.assertEqual(repo.run("template", "show", "row.md")[1], ALT_ROW)
      self.assertEqual(_tracking_bytes(repo), before)

  def test_Template_MergeInProgress_WritesAreRefused(self) -> None:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      (repo.root / ".git" / "MERGE_HEAD").write_text("0" * 40 + "\n")
      before = _tracking_bytes(repo)
      code, out, _ = _set(repo, "row.md", ALT_ROW, "--json")
      self.assertEqual((code, _error(out)["code"]), (2, "merge_in_progress"))
      self.assertEqual(_tracking_bytes(repo), before)


if __name__ == "__main__":
  unittest.main()
