"""`slicer refs` finds file:line references in slice text, reports the ones that
drifted, and with `--fix` refreshes only those it can place unambiguously."""

from __future__ import annotations

import json
import unittest

import support
from slicer import model, refs

REPORT_KEYS = ["fix", "items", "references", "counts", "fixed", "drift"]
REFERENCE_KEYS = [
  "id", "where", "line", "ref", "path", "file", "start", "end", "approx", "anchor",
  "status", "found", "to", "paths", "lines", "fixed",
]
COUNT_KEYS = [
  "ok", "unanchored", "missing_file", "ambiguous_path", "out_of_range", "moved",
  "ambiguous_symbol", "symbol_gone",
]


def source(*lines: str) -> str:
  return "\n".join(lines) + "\n"


# `open_thing` is on line 3 of APP; `close_thing` on line 7; the file has 10 lines.
APP = source(
  "# app",
  "",
  "def open_thing():",
  "  return 1",
  "",
  "",
  "def close_thing():",
  "  return 2",
  "",
  "# end",
)


class RefsFixture(unittest.TestCase):
  """A project with a source tree and slices written through the CLI."""

  def repo(self) -> support.TempRepo:
    repo = support.TempRepo()
    repo.run("init")
    repo.write("src/app.py", APP)
    return repo

  def add(self, repo: support.TempRepo, title: str, *, lead: str = "", why: str = "",
          boundary: str = "", files: str = "") -> str:
    """File one item with a slice and return its id."""
    code, out, err = repo.run("add", title, "--json")
    self.assertEqual(code, 0, err)
    item_id = json.loads(out)["id"]
    draft = f"## {title}\n\n{lead}\n\n### Why\n{why}\n\n### Files\n{files}\n"
    repo.write(f"draft-{item_id}.md", draft)
    args = ["promote", item_id, "--file", str(repo.root / f"draft-{item_id}.md")]
    if boundary:
      args += ["--boundary", boundary]
    code, _, err = repo.run(*args)
    self.assertEqual(code, 0, err)
    return item_id

  def refs(self, repo: support.TempRepo, *argv: str) -> tuple[int, dict, str]:
    code, out, err = repo.run("refs", *argv, "--json")
    return code, (json.loads(out) if out.strip() else {}), err

  def only(self, report: dict) -> dict:
    self.assertEqual(len(report["references"]), 1, report)
    return report["references"][0]

  def section(self, repo: support.TempRepo, item_id: str, heading: str) -> str:
    return repo.state().slices[item_id].section(heading).body

  def snapshot(self, repo: support.TempRepo) -> dict[str, bytes]:
    return {
      str(p.relative_to(repo.root)): p.read_bytes()
      for p in (repo.root / ".slicer").rglob("*")
      if p.is_file() and p.name != "lock"
    }


class RefsStatusTests(RefsFixture):
  def test_Refs_AnchorOnCitedLine_IsOk(self) -> None:
    with self.repo() as repo:
      self.add(repo, "A", why="`open_thing` (`src/app.py:3`).")
      code, report, err = self.refs(repo)
      self.assertEqual(code, 0, err)
      ref = self.only(report)
      self.assertEqual(ref["status"], "ok")
      self.assertEqual(ref["anchor"], "open_thing")
      self.assertEqual(ref["file"], "src/app.py")
      self.assertEqual(ref["start"], 3)

  def test_Refs_AnchorMoved_ReportsNewLine(self) -> None:
    with self.repo() as repo:
      self.add(repo, "A", why="`close_thing` (`src/app.py:3`).")
      code, report, err = self.refs(repo)
      self.assertEqual(code, 1, err)
      ref = self.only(report)
      self.assertEqual(ref["status"], "moved")
      self.assertEqual(ref["found"], 7)
      self.assertEqual(ref["to"], "src/app.py:7")
      self.assertFalse(ref["fixed"])

  def test_Refs_FileMissing_IsMissingFile(self) -> None:
    with self.repo() as repo:
      self.add(repo, "A", why="`open_thing` (`src/gone.py:3`).")
      code, report, err = self.refs(repo)
      self.assertEqual(code, 1, err)
      self.assertEqual(self.only(report)["status"], "missing_file")

  def test_Refs_LineBeyondFile_IsOutOfRange(self) -> None:
    with self.repo() as repo:
      self.add(repo, "A", why="`open_thing` (`src/app.py:99`) and `close_thing` (`src/app.py:5-99`).")
      code, report, err = self.refs(repo)
      self.assertEqual(code, 1, err)
      self.assertEqual([r["status"] for r in report["references"]], ["out_of_range"] * 2)

  def test_Refs_NoAnchor_IsUnanchoredAndExitsZero(self) -> None:
    with self.repo() as repo:
      self.add(repo, "A", why="See src/app.py:4 for the body.")
      code, report, err = self.refs(repo)
      self.assertEqual(code, 0, err)
      ref = self.only(report)
      self.assertEqual(ref["status"], "unanchored")
      self.assertIsNone(ref["anchor"])

  def test_Refs_AnchorSeveralTimesNoUniqueNearest_IsAmbiguousSymbolAndNotFixed(self) -> None:
    with self.repo() as repo:
      repo.write("src/twice.py", source("twice", "", "", "", "", "pad", "", "", "twice", "end"))
      item_id = self.add(repo, "A", why="`twice` (`src/twice.py:5`).")
      before = self.section(repo, item_id, "Why")
      code, report, err = self.refs(repo, "--fix")
      self.assertEqual(code, 1, err)
      ref = self.only(report)
      self.assertEqual(ref["status"], "ambiguous_symbol")
      self.assertEqual(ref["lines"], [1, 9])
      self.assertFalse(ref["fixed"])
      self.assertEqual(self.section(repo, item_id, "Why"), before)

  def test_Refs_AnchorGone_IsSymbolGone(self) -> None:
    with self.repo() as repo:
      self.add(repo, "A", why="`vanished` (`src/app.py:3`).")
      code, report, err = self.refs(repo)
      self.assertEqual(code, 1, err)
      self.assertEqual(self.only(report)["status"], "symbol_gone")

  def test_Refs_DotSlashAndParentPaths_ResolveInsideRootOnly(self) -> None:
    with self.repo() as repo:
      outside = repo.root.parent / f"{repo.root.name}-escape.py"
      outside.write_text("x\n", encoding="utf-8")
      self.addCleanup(outside.unlink)
      self.add(repo, "A", why=(
        "`open_thing` (`./src/app.py:3`).\n`open_thing` (`src/../src/app.py:3`).\n"
        f"`x` (`../{outside.name}:1`)."
      ))
      code, report, err = self.refs(repo)
      self.assertEqual(code, 1, err)
      self.assertEqual([(r["status"], r["file"]) for r in report["references"]],
                       [("ok", "src/app.py"), ("ok", "src/app.py"), ("missing_file", None)])

  def test_Refs_BareFilename_ResolvesUniqueBasename(self) -> None:
    with self.repo() as repo:
      repo.write("src/deep/Open.cs", source("a", "b", "OpenAsync()", "c"))
      self.add(repo, "A", why="`OpenAsync` (`Open.cs:3`).")
      code, report, err = self.refs(repo)
      self.assertEqual(code, 0, err)
      ref = self.only(report)
      self.assertEqual(ref["status"], "ok")
      self.assertEqual(ref["path"], "Open.cs")
      self.assertEqual(ref["file"], "src/deep/Open.cs")

  def test_Refs_BareFilenameSeveralMatches_IsAmbiguousPath(self) -> None:
    with self.repo() as repo:
      repo.write("src/a/util.py", "x\n")
      repo.write("src/b/util.py", "x\n")
      repo.write(".hidden/util.py", "x\n")
      repo.write("node_modules/util.py", "x\n")
      self.add(repo, "A", why="`x` (`util.py:1`).")
      code, report, err = self.refs(repo)
      self.assertEqual(code, 1, err)
      ref = self.only(report)
      self.assertEqual(ref["status"], "ambiguous_path")
      self.assertEqual(ref["paths"], ["src/a/util.py", "src/b/util.py"])


class RefsSelectionTests(RefsFixture):
  def three(self, repo: support.TempRepo) -> tuple[str, str, str]:
    stale = "`close_thing` (`src/app.py:3`)."
    open_id = self.add(repo, "Open", why=stale)
    done_id = self.add(repo, "Done", why=stale)
    gone_id = self.add(repo, "Gone", why=stale)
    self.assertEqual(repo.run("done", done_id)[0], 0)
    self.assertEqual(repo.run("remove", gone_id, "--reason", "obsolete")[0], 0)
    return open_id, done_id, gone_id

  def test_Refs_NoIds_SkipsDoneAndRetired(self) -> None:
    with self.repo() as repo:
      open_id, _, _ = self.three(repo)
      code, report, err = self.refs(repo)
      self.assertEqual(code, 1, err)
      self.assertEqual(report["items"], [open_id])
      self.assertEqual({r["id"] for r in report["references"]}, {open_id})

  def test_Refs_NamedDoneId_IsScanned(self) -> None:
    with self.repo() as repo:
      _, done_id, gone_id = self.three(repo)
      code, report, err = self.refs(repo, done_id, gone_id)
      self.assertEqual(code, 1, err)
      self.assertEqual(report["items"], [done_id, gone_id])
      self.assertEqual([r["id"] for r in report["references"]], [done_id, gone_id])


class RefsFixTests(RefsFixture):
  def test_RefsFix_ApproximateAndRange_KeepTildeAndWidth(self) -> None:
    with self.repo() as repo:
      why = "\n".join((
        "`close_thing` (`src/app.py:~3`).",
        "`close_thing` (`src/app.py:2-3`).",
        "`close_thing` (`src/app.py:~2-4`).",
        "`close_thing` (`src/app.py:3`).",
      ))
      item_id = self.add(repo, "A", why=why)
      code, report, err = self.refs(repo, item_id, "--fix")
      self.assertEqual(code, 0, err)
      self.assertEqual(report["fixed"], 4)
      self.assertEqual(self.section(repo, item_id, "Why"), "\n".join((
        "`close_thing` (`src/app.py:~7`).",
        "`close_thing` (`src/app.py:7-8`).",
        "`close_thing` (`src/app.py:~7-9`).",
        "`close_thing` (`src/app.py:7`).",
      )))
      self.assertEqual(self.refs(repo, item_id)[0], 0)

  def test_RefsFix_Notes_ReportedNeverRewritten(self) -> None:
    with self.repo() as repo:
      item_id = self.add(repo, "A", why="`close_thing` (`src/app.py:3`).")
      note = "`close_thing` (`src/app.py:3`) was stale when noted"
      self.assertEqual(repo.run("note", item_id, "--text", note)[0], 0)
      before = repo.state().index.require(item_id).note_records[0].text
      code, report, err = self.refs(repo, "--fix")
      self.assertEqual(code, 1, err)
      by_where = {r["where"]: r for r in report["references"]}
      self.assertEqual(set(by_where), {"section Why", "note 1"})
      self.assertEqual(by_where["note 1"]["status"], "moved")
      self.assertFalse(by_where["note 1"]["fixed"])
      self.assertTrue(by_where["section Why"]["fixed"])
      self.assertEqual(repo.state().index.require(item_id).note_records[0].text, before)
      self.assertEqual(self.section(repo, item_id, "Why"), "`close_thing` (`src/app.py:7`).")

  def test_RefsFix_AnchorInsideRange_IsOkNotShifted(self) -> None:
    with self.repo() as repo:
      repo.write("src/box.py", source("# box", "", "class Box:", "  x = 1", "", "  def helper(self):", "    return 1", ""))
      why = "`helper` (`src/box.py:3-7`)."
      item_id = self.add(repo, "A", why=why)
      code, report, err = self.refs(repo, item_id, "--fix")
      self.assertEqual(code, 0, err)
      self.assertEqual(self.only(report)["status"], "ok")
      self.assertEqual(report["fixed"], 0)
      self.assertEqual(self.section(repo, item_id, "Why"), why)

  def test_RefsFix_ShiftPastEof_IsNotFixed(self) -> None:
    with self.repo() as repo:
      why = "`close_thing` (`src/app.py:2-6`)."  # the file has 10 lines: 2-6 would shift to 7-11
      item_id = self.add(repo, "A", why=why)
      code, report, err = self.refs(repo, item_id, "--fix")
      self.assertEqual(code, 1, err)
      ref = self.only(report)
      self.assertEqual(ref["status"], "out_of_range")
      self.assertFalse(ref["fixed"])
      self.assertEqual(self.section(repo, item_id, "Why"), why)

  def test_RefsFix_RepeatedHeading_LaterSectionIsReportedNotRewritten(self) -> None:
    with self.repo() as repo:
      stale = "`close_thing` (`src/app.py:3`)."
      item_id = self.add(repo, "A", why=stale)
      state = repo.state()
      sl = state.slices[item_id]
      sl.sections.append(model.Section("Why", stale))
      state.save_slice(sl)
      code, report, err = self.refs(repo, item_id, "--fix")
      self.assertEqual(code, 1, err)
      self.assertEqual([r["fixed"] for r in report["references"]], [True, False])
      sl = repo.state().slices[item_id]
      self.assertEqual([s.body for s in sl.sections if s.heading == "Why"],
                       ["`close_thing` (`src/app.py:7`).", stale])

  def test_RefsFix_MovedOnly_RewritesLeadBoundaryAndSections(self) -> None:
    with self.repo() as repo:
      repo.write("src/twice.py", source("twice", "", "", "", "", "pad", "", "", "twice", "end"))
      stale = "`close_thing` (`src/app.py:3`)"
      keep = "`open_thing` (`src/app.py:3`), `twice` (`src/twice.py:5`)\na bare src/app.py:4"
      item_id = self.add(
        repo, "A", lead=f"Lead {stale}.", why=f"Why {stale}.\n{keep}",
        files=f"- {stale}", boundary=f"**Not in this slice:** {stale}.",
      )
      self.assertEqual(repo.run("render")[0], 0)
      self.assertEqual(repo.run("check")[0], 0)
      code, report, err = self.refs(repo, item_id, "--fix", "--render", "--strict")
      self.assertEqual(code, 1, err)  # the ambiguous reference is still wrong
      self.assertEqual(report["fixed"], 4)
      self.assertEqual(report["drift"], 1)
      fixed = "`close_thing` (`src/app.py:7`)"
      sl = repo.state().slices[item_id]
      self.assertEqual(sl.lead, [f"Lead {fixed}."])
      self.assertEqual(sl.boundary, f"**Not in this slice:** {fixed}.")
      self.assertEqual(sl.section("Why").body, f"Why {fixed}.\n{keep}")
      self.assertEqual(sl.section("Files").body, f"- {fixed}")
      edits = [e for e in json.loads(repo.run("log", "--json")[1]) if e["action"] == "edit"]
      self.assertEqual(
        sorted((e["item"], e["note"]) for e in edits),
        [(item_id, "Files"), (item_id, "Why"), (item_id, "boundary"), (item_id, "lead")],
      )
      self.assertEqual(repo.run("check")[0], 0)

  def test_RefsFix_SharedLine_SecondRefNotRewritten(self) -> None:
    with self.repo() as repo:
      item_id = self.add(repo, "A", why="`close_thing` (`src/app.py:3`) and `src/app.py:3` later.")
      code, report, err = self.refs(repo, item_id, "--fix")
      self.assertEqual(code, 0, err)
      self.assertEqual(report["fixed"], 1)
      self.assertEqual([r["status"] for r in report["references"]], ["moved", "unanchored"])
      self.assertEqual(self.section(repo, item_id, "Why"),
                       "`close_thing` (`src/app.py:7`) and `src/app.py:3` later.")


class RefsContractTests(RefsFixture):
  def test_Refs_ExitCodes_ZeroOneTwo(self) -> None:
    with self.repo() as repo:
      item_id = self.add(repo, "A", why="`open_thing` (`src/app.py:3`).")
      self.assertEqual(repo.run("refs")[0], 0)
      self.assertEqual(repo.run("refs", item_id)[0], 0)
      self.assertEqual(repo.run("edit", item_id, "--section", "Why",
                                "--text", "`close_thing` (`src/app.py:3`).")[0], 0)
      self.assertEqual(repo.run("refs")[0], 1)
      code, out, err = repo.run("refs", "S99", "--json")
      self.assertEqual(code, 2, err)
      self.assertEqual(json.loads(out)["error"]["code"], "no_such_item")
      code, out, err = repo.run("refs", "--render", "--json")
      self.assertEqual(code, 2, err)
      self.assertEqual(json.loads(out)["error"]["code"], "usage")

  def test_Refs_WithoutFix_IsReadOnly(self) -> None:
    with self.repo() as repo:
      item_id = self.add(repo, "A", lead="`close_thing` (`src/app.py:3`).",
                         why="`close_thing` (`src/app.py:3`).")
      repo.run("note", item_id, "--text", "`close_thing` (`src/app.py:3`)")
      self.assertEqual(repo.run("render")[0], 0)
      before = self.snapshot(repo)
      for argv in ((), ("--json",), (item_id, "--json", "--lean")):
        with self.subTest(argv=argv):
          code, out, err = repo.run("refs", *argv)
          self.assertEqual(code, 1, err)
          self.assertIn("moved", out)
          self.assertEqual(self.snapshot(repo), before)

  def test_Refs_Json_FixedKeyOrder(self) -> None:
    with self.repo() as repo:
      self.add(repo, "A", why="`close_thing` (`src/app.py:3`).")
      code, report, err = self.refs(repo)
      self.assertEqual(code, 1, err)
      self.assertEqual(list(report), REPORT_KEYS)
      self.assertEqual(list(report["counts"]), COUNT_KEYS)
      self.assertEqual(list(self.only(report)), REFERENCE_KEYS)
      code, out, err = repo.run("refs", "--json", "--lean")
      self.assertEqual(code, 1, err)
      lean = json.loads(out)
      self.assertEqual(list(lean["references"][0]), [
        "id", "where", "line", "ref", "path", "file", "start", "approx", "anchor",
        "status", "found", "to", "fixed",
      ])

  def test_Refs_JsonLeanWithNothingToScan_KeepsItemsAndReferences(self) -> None:
    with self.repo() as repo:
      code, out, err = repo.run("refs", "--json", "--lean")
      self.assertEqual(code, 0, err)
      report = json.loads(out)
      self.assertEqual((report["items"], report["references"], report["drift"]), ([], [], 0))


class RefsParserTests(unittest.TestCase):
  def test_Find_Forms_ReadsPathLineAndRangeWithTilde(self) -> None:
    found = refs.find(
      "a src/x.py:12, b.cs:~7, c/d.go:3-9 and e.py:~4-6; src/slicer/ai.py:142 "
      "docs/agents.md:271 DataBall.Open.cs:60"
    )
    self.assertEqual(
      [(r.path, r.start, r.end, r.approx) for r in found],
      [("src/x.py", 12, None, False), ("b.cs", 7, None, True),
       ("c/d.go", 3, 9, False), ("e.py", 4, 6, True), ("src/slicer/ai.py", 142, None, False),
       ("docs/agents.md", 271, None, False), ("DataBall.Open.cs", 60, None, False)],
    )

  def test_Find_ProseAndUrls_AreNotReferences(self) -> None:
    text = (
      "line 2722 at filing; see http://host.example:8080 at 12:30; v1.2:3; /abs/x.py:4; x.py:; "
      "api.example.com:443 db.internal.net:5432 config.yaml:12:30"
    )
    self.assertEqual(refs.find(text), [])

  def test_Find_Anchor_IsNearestIdentifierSincePreviousRef(self) -> None:
    text = (
      "`Outer` and `Inner` (`a.py:1`)\n`Other` then `--flag` `b.py:2` and `c.py:3`\n"
      "plain d.py:4\n`util.py` (`e.py:5`)\n`Before`\n`f.py:6`"
    )
    anchors = [(r.path, r.anchor) for r in refs.find(text)]
    self.assertEqual(anchors, [
      ("a.py", "Inner"), ("b.py", "Other"), ("c.py", None), ("d.py", None),
      ("e.py", None), ("f.py", None),
    ])

  def test_Find_SharedLine_SecondRefIsUnanchored(self) -> None:
    found = refs.find("`Box` (`src/app.py:1`) and `src/app.py:9` later")
    self.assertEqual([(r.start, r.anchor) for r in found], [(1, "Box"), (9, None)])

  def test_Find_EachRefOwnAnchor_BothAnchored(self) -> None:
    found = refs.find("`A` (`a.py:1`), `B` (`a.py:5`)")
    self.assertEqual([(r.start, r.anchor) for r in found], [(1, "A"), (5, "B")])

  def test_Find_IdentifierBetweenRefs_AnchorsSecondOnlyOnce(self) -> None:
    cases = {
      "`Box` (`a.py:1`), `Pen` (`b.py:2`), `c.py:3`": [("a.py", "Box"), ("b.py", "Pen"), ("c.py", None)],
      "`Box` (`a.py:1`) and `b.py:2`": [("a.py", "Box"), ("b.py", None)],
      "a.py:1`Pen` and `b.py:2`": [("a.py", None), ("b.py", "Pen")],
      "`Pen`b.py:2": [("b.py", "Pen")],
      "`Box` at host:443 (`a.py:1`)": [("a.py", "Box")],
    }
    for text, expected in cases.items():
      with self.subTest(text=text):
        self.assertEqual([(r.path, r.anchor) for r in refs.find(text)], expected)

  def test_Find_QualifiedAnchor_SearchesLastComponent(self) -> None:
    [ref] = refs.find("`store.discover_all` (`store.py:78`) and `Open()`")
    self.assertEqual(ref.anchor, "store.discover_all")
    self.assertEqual(ref.symbol, "discover_all")

  def test_Find_FencedBlock_IsSkipped(self) -> None:
    text = source(
      "a.py:1",
      "```python",
      "b.py:2",
      "~~~",
      "f.py:6",
      "```",
      "c.py:3",
      "   ~~~",
      "d.py:4",
      "   ~~~",
      "e.py:5",
      "````",
      "g.py:7",
      "```",
      "h.py:8",
      "````",
      "i.py:9",
    )
    self.assertEqual(
      [(r.path, r.line) for r in refs.find(text)],
      [("a.py", 1), ("c.py", 7), ("e.py", 11), ("i.py", 17)],
    )

  def test_Find_ExampleMarker_SkipsWholeLine(self) -> None:
    text = source("see a.py:1 and b.py:2 <!-- example -->", "c.py:3")
    self.assertEqual([(r.path, r.line) for r in refs.find(text)], [("c.py", 2)])

  def test_Find_UnmarkedLine_StillParsed(self) -> None:
    text = source("a.py:1 <!-- example -->", "b.py:2", "c.py:3 <!-- example -->")
    self.assertEqual([(r.path, r.line) for r in refs.find(text)], [("b.py", 2)])
    [ref] = refs.find(text)
    self.assertEqual(text[ref.begin:ref.stop], "2")

  def test_Find_RefAfterFenceCloses_KeepsLineAndOffsets(self) -> None:
    text = source("```", "q.py:9", "```", "alpha `Thing` (`src/a.py:3-5`)")
    self.assertEqual([(r.path, r.line) for r in refs.find(text)], [("src/a.py", 4)])
    [ref] = refs.find(text)
    self.assertEqual(ref.line, 4)
    self.assertEqual(text[ref.begin:ref.stop], "3-5")
    self.assertEqual(ref.anchor, "Thing")

  def test_Find_CloserWithInfoString_DoesNotClose(self) -> None:
    text = source("```", "a.py:2", "```python", "b.py:4", "```", "c.py:6")
    self.assertEqual([(r.path, r.line) for r in refs.find(text)], [("c.py", 6)])

  def test_Find_BacktickInInfoString_IsNotAFence(self) -> None:
    text = source("```x``` is inline", "a.py:2", "~~~ tilde ` info", "b.py:4", "~~~", "c.py:6")
    self.assertEqual(
      [(r.path, r.line) for r in refs.find(text)],
      [("a.py", 2), ("c.py", 6)],
    )

  def test_Find_FourSpaceFence_IsNotAFence(self) -> None:
    text = source("    ```", "a.py:2", "    ```", "b.py:4")
    self.assertEqual([(r.path, r.line) for r in refs.find(text)], [("a.py", 2), ("b.py", 4)])

  def test_Find_UnclosedFence_SkipsToEnd(self) -> None:
    text = source("a.py:1", "```", "b.py:3", "", "c.py:5")
    self.assertEqual([(r.path, r.line) for r in refs.find(text)], [("a.py", 1)])


if __name__ == "__main__":
  unittest.main()
