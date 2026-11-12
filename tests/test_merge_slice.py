"""The `slicer-slice` merge driver for slice JSON under `.slicer/slices/` (s451).

Two branches that each edit a slice with `slicer edit` or `slicer note` overlap in one
file, so Git's line merge calls it a conflict even when they touched different sections.
The driver matches fields by name, sections by heading and occurrence, and notes by exact
text; only a value both sides changed differently is refused, with `ours` left as it was.
The unit tests hand it three slice files; the end-to-end tests let Git run it.
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import support
from slicer import jsonio, mergeslice, store
from slicer.errors import StateError
from slicer.model import Section, Slice

SRC = Path(__file__).resolve().parents[1] / "src"


def _slice(sections: list[tuple[str, str]] | None = None, **fields: object) -> str:
  """A slice file as the bytes slicer writes."""
  sl = Slice(id="S01", title="A slice", lead=["lead text"], size="M")
  sl.sections = [Section(h, b) for h, b in (sections or [])]
  for key, value in fields.items():
    setattr(sl, key, value)
  return jsonio.dumps(sl.to_dict())


SECTIONS = [("Why", "why base"), ("Implement", "implement base"), ("Check", "check base")]
BASE = _slice(SECTIONS, notes=["n1", "n2"])


class MergeSliceCase(unittest.TestCase):
  def fixture(self) -> support.TempRepo:
    """A scratch directory with no project: the driver needs none."""
    repo = support.TempRepo()
    self.addCleanup(repo.close)
    self.enterContext(support.isolated_discovery(repo.root))
    return repo

  def merge(self, base: str, ours: str, theirs: str):
    repo = self.fixture()
    for name, text in (("base", base), ("ours", ours), ("theirs", theirs)):
      repo.write(name, text)
    code, out, err = repo.run(
      "merge-slice", *(str(repo.root / n) for n in ("base", "ours", "theirs")),
    )
    return code, out, err, repo.read("ours")

  def clean(self, base: str, ours: str, theirs: str) -> dict:
    code, out, err, merged = self.merge(base, ours, theirs)
    self.assertEqual(code, 0, err)
    self.assertEqual(out, "")
    # The result is exactly what the writer produces, so `check` and a later edit agree.
    doc = json.loads(merged)
    self.assertEqual(merged, jsonio.dumps(Slice.from_dict(doc).to_dict()))
    return doc

  def refused(self, base: str, ours: str, theirs: str, code: str) -> str:
    """The merge is refused with this stable code and OURS keeps its exact bytes."""
    repo = self.fixture()
    paths = {}
    for name, text in (("base", base), ("ours", ours), ("theirs", theirs)):
      paths[name] = repo.write(name, text)
    with self.assertRaises(StateError) as caught:
      mergeslice.merge(paths["base"], paths["ours"], paths["theirs"])
    self.assertEqual(caught.exception.code, code)
    self.assertEqual(repo.read("ours"), ours)
    return str(caught.exception)


class SectionTests(MergeSliceCase):
  def test_MergeSlice_EachSideEditsADifferentSection_KeepsBothEdits(self) -> None:
    ours = _slice([("Why", "why ours"), SECTIONS[1], SECTIONS[2]], notes=["n1", "n2"])
    theirs = _slice([SECTIONS[0], SECTIONS[1], ("Check", "check theirs")], notes=["n1", "n2"])
    doc = self.clean(BASE, ours, theirs)
    self.assertEqual(
      [(s["heading"], s["body"]) for s in doc["sections"]],
      [("Why", "why ours"), ("Implement", "implement base"), ("Check", "check theirs")],
    )

  def test_MergeSlice_OneSideAddsASection_KeepsItInPlace(self) -> None:
    ours = _slice([*SECTIONS[:2], ("Extra", "extra ours"), SECTIONS[2]], notes=["n1", "n2"])
    theirs = _slice([("Why", "why theirs"), *SECTIONS[1:]], notes=["n1", "n2"])
    doc = self.clean(BASE, ours, theirs)
    self.assertEqual(
      [(s["heading"], s["body"]) for s in doc["sections"]],
      [("Why", "why theirs"), ("Implement", "implement base"),
       ("Extra", "extra ours"), ("Check", "check base")],
    )
    # The same from the other side: theirs adds, ours edits.
    doc = self.clean(BASE, theirs, ours)
    self.assertEqual([s["heading"] for s in doc["sections"]],
                     ["Why", "Implement", "Extra", "Check"])

  def test_MergeSlice_RepeatedHeading_MatchedByHeadingAndOccurrence(self) -> None:
    pair = [("Pick (landed)", "first base"), ("Pick (landed)", "second base")]
    base = _slice([SECTIONS[0], *pair])
    ours = _slice([SECTIONS[0], ("Pick (landed)", "first ours"), pair[1]])
    theirs = _slice([SECTIONS[0], pair[0], ("Pick (landed)", "second theirs")])
    doc = self.clean(base, ours, theirs)
    self.assertEqual(
      [(s["heading"], s["body"]) for s in doc["sections"]],
      [("Why", "why base"), ("Pick (landed)", "first ours"), ("Pick (landed)", "second theirs")],
    )

  def test_MergeSlice_SameSectionEditedDifferently_IsAConflict(self) -> None:
    ours = _slice([SECTIONS[0], ("Implement", "implement ours"), SECTIONS[2]], notes=["n1", "n2"])
    theirs = _slice([SECTIONS[0], ("Implement", "implement theirs"), SECTIONS[2]], notes=["n1", "n2"])
    message = self.refused(BASE, ours, theirs, "merge_conflict")
    self.assertIn("sections/Implement/body", message)
    # The same edit on both sides is not a disagreement.
    self.assertEqual(
      self.clean(BASE, ours, ours)["sections"][1]["body"], "implement ours",
    )

  def test_MergeSlice_SectionRemovedOneSideEditedOnTheOther_IsAConflict(self) -> None:
    ours = _slice([SECTIONS[0], SECTIONS[2]], notes=["n1", "n2"])
    theirs = _slice([SECTIONS[0], ("Implement", "implement theirs"), SECTIONS[2]], notes=["n1", "n2"])
    message = self.refused(BASE, ours, theirs, "merge_conflict")
    self.assertIn("sections/Implement", message)
    # Removed on one side and untouched on the other: it stays removed.
    untouched = _slice(SECTIONS, notes=["n1", "n2"])
    doc = self.clean(BASE, ours, untouched)
    self.assertEqual([s["heading"] for s in doc["sections"]], ["Why", "Check"])

  def test_MergeSlice_BothSidesReorderSectionsDifferently_IsAConflict(self) -> None:
    ours = _slice([SECTIONS[1], SECTIONS[0], SECTIONS[2]], notes=["n1", "n2"])
    theirs = _slice([SECTIONS[2], SECTIONS[1], SECTIONS[0]], notes=["n1", "n2"])
    self.assertIn("sections/order", self.refused(BASE, ours, theirs, "merge_conflict"))
    # One side reordering alone is taken.
    doc = self.clean(BASE, ours, BASE)
    self.assertEqual([s["heading"] for s in doc["sections"]], ["Implement", "Why", "Check"])


class NoteTests(MergeSliceCase):
  def test_MergeSlice_BothSidesAppendANote_KeepsBoth(self) -> None:
    ours = _slice(SECTIONS, notes=["n1", "n2", "ours note"])
    theirs = _slice(SECTIONS, notes=["n1", "n2", "theirs note"])
    doc = self.clean(BASE, ours, theirs)
    self.assertEqual(doc["notes"], ["n1", "n2", "ours note", "theirs note"])

  def test_MergeSlice_BothSidesAppendTheSameNote_KeepsOne(self) -> None:
    ours = _slice(SECTIONS, notes=["n1", "n2", "same", "ours"])
    theirs = _slice(SECTIONS, notes=["n1", "n2", "same", "theirs"])
    self.assertEqual(self.clean(BASE, ours, theirs)["notes"], ["n1", "n2", "same", "ours", "theirs"])

  def test_MergeSlice_NoteRemovedOnOneSideAndKeptOnTheOther_StaysRemoved(self) -> None:
    ours = _slice(SECTIONS, notes=["n2", "ours note"])
    theirs = _slice(SECTIONS, notes=["n1", "n2", "theirs note"])
    self.assertEqual(self.clean(BASE, ours, theirs)["notes"], ["n2", "ours note", "theirs note"])

  def test_MergeSlice_NoteEditedOnOneSideAndRemovedOnTheOther_KeepsTheEdit(self) -> None:
    # An edited line is a removal plus an addition; a union never drops the addition.
    ours = _slice(SECTIONS, notes=["n1 edited", "n2"])
    theirs = _slice(SECTIONS, notes=["n2"])
    self.assertEqual(self.clean(BASE, ours, theirs)["notes"], ["n2", "n1 edited"])

  def test_MergeSlice_NotesChangedOnOneSideOnly_TakesThatSide(self) -> None:
    ours = _slice(SECTIONS, notes=["n2", "n1"])
    doc = self.clean(BASE, ours, BASE)
    self.assertEqual(doc["notes"], ["n2", "n1"])

  def test_MergeSlice_DuplicateNoteText_IsCountedByOccurrence(self) -> None:
    base = _slice(SECTIONS, notes=["a", "a"])
    ours = _slice(SECTIONS, notes=["a", "a", "a"])
    theirs = _slice(SECTIONS, notes=["a", "a", "b"])
    self.assertEqual(self.clean(base, ours, theirs)["notes"], ["a", "a", "a", "b"])


class FieldTests(MergeSliceCase):
  def test_MergeSlice_FieldChangedOnOneSide_TakesThatChange(self) -> None:
    ours = _slice(SECTIONS, notes=["n1", "n2"], size="L")
    theirs = _slice(SECTIONS, notes=["n1", "n2"], lead=["new lead"])
    doc = self.clean(BASE, ours, theirs)
    self.assertEqual((doc["size"], doc["lead"]), ("L", ["new lead"]))

  def test_MergeSlice_FieldChangedDifferentlyOnBothSides_IsAConflictNamingIt(self) -> None:
    ours = _slice(SECTIONS, notes=["n1", "n2"], size="L")
    theirs = _slice(SECTIONS, notes=["n1", "n2"], size="S")
    message = self.refused(BASE, ours, theirs, "merge_conflict")
    self.assertIn("conflict at size", message)
    self.assertIn("git checkout --ours|--theirs", message)
    self.assertIn("slicer edit", message)

  def test_MergeSlice_EveryConflictIsNamed(self) -> None:
    ours = _slice([SECTIONS[0], ("Implement", "ours"), SECTIONS[2]], notes=["n1", "n2"], size="L")
    theirs = _slice([SECTIONS[0], ("Implement", "theirs"), SECTIONS[2]], notes=["n1", "n2"], size="S")
    message = self.refused(BASE, ours, theirs, "merge_conflict")
    self.assertIn("conflict at size", message)
    self.assertIn("conflict at sections/Implement/body", message)

  def test_MergeSlice_EmptyBase_MergesTwoNewFiles(self) -> None:
    # Both branches created the file: Git hands over an empty base.
    ours = _slice([("Why", "w")], notes=["a"])
    theirs = _slice([("Why", "w")], notes=["b"])
    doc = self.clean("", ours, theirs)
    self.assertEqual(doc["notes"], ["a", "b"])
    self.refused("", _slice([("Why", "ours")]), _slice([("Why", "theirs")]), "merge_conflict")


class InputTests(MergeSliceCase):
  def test_MergeSlice_InputNotASlice_IsRefusedAsCorrupt(self) -> None:
    for bad in ("not json", "[1]", '{"title": "no id"}', '{"id": "S01", "title": "t", "sections": [1]}',
                '{"id": "S01", "title": "t", "notes": [1]}'):
      for side in range(3):
        texts = [BASE, BASE, BASE]
        texts[side] = bad
        with self.subTest(bad=bad, side=side):
          self.refused(*texts, "corrupt")

  def test_MergeSlice_ConflictMarkersInOurs_AreRefusedAsCorrupt(self) -> None:
    marked = "<<<<<<< ours\n" + BASE + "=======\n" + BASE + ">>>>>>> theirs\n"
    self.refused(BASE, marked, BASE, "corrupt")

  def test_MergeSlice_NeedsNoProject(self) -> None:
    with support.TempRepo() as repo, support.isolated_discovery(repo.root):
      with patch("slicer.store.discover", side_effect=AssertionError("discover")), \
           patch("slicer.store.load", side_effect=AssertionError("load")), \
           patch("slicer.store.project_lock", side_effect=AssertionError("lock")):
        for name in ("base", "ours", "theirs"):
          repo.write(name, BASE)
        code, out, err = repo.run(
          "merge-slice", *(str(repo.root / n) for n in ("base", "ours", "theirs")),
          allow_parent_project=True,
        )
      self.assertEqual((code, out, err), (0, "", ""))
      self.assertEqual(repo.read("ours"), BASE)


class EndToEndTests(unittest.TestCase):
  """Two branches that each edit a different section of one slice merge through Git."""

  def _configure(self, repo: support.TempRepo) -> None:
    repo._git("config", "merge.slicer-generated.driver", "true")
    repo._git("config", "merge.slicer-index.driver",
              f"{sys.executable} -m slicer merge-index %O %A %B")
    repo._git("config", "merge.slicer-slice.driver",
              f"{sys.executable} -m slicer merge-slice %O %A %B")

  OUTLINE = (
    "# Roadmap\n\n## A slice\nsize: S\n\nLead text.\n\n"
    "### Why\n\nwhy base\n\n### Check\n\ncheck base\n"
  )

  def _project(self, repo: support.TempRepo) -> None:
    """An initialised project with one slice, S01, holding a Why and a Check section."""
    repo.run("init")
    repo.write("outline.md", self.OUTLINE)
    code, _, err = repo.run("import", "outline.md", "--render")
    self.assertEqual(code, 0, err)

  def _edit(self, repo: support.TempRepo, section: str, text: str) -> None:
    code, _, err = repo.run("edit", "S01", "--section", section, "--text", text, "--render")
    self.assertEqual(code, 0, err)

  def _diverge_and_merge(self, repo: support.TempRepo, folder: str) -> dict[str, str]:
    """Each branch edits a different section of S01 and adds its own after the last one,
    which Git's line merge cannot combine. Returns the merged bodies by heading."""
    repo.commit("base")
    trunk = repo._git("branch", "--show-current").stdout.strip()
    repo._git("checkout", "-q", "-b", "feature")
    self._edit(repo, "Why", "feature why")
    self._edit(repo, "Feature extra", "feature extra")
    repo.commit("feature")
    repo._git("checkout", "-q", trunk)
    self._edit(repo, "Check", "trunk check")
    self._edit(repo, "Trunk extra", "trunk extra")
    repo.commit("trunk")
    merged = repo._git("merge", "--no-edit", "feature")
    self.assertEqual(merged.returncode, 0, merged.stdout + merged.stderr)
    path = repo.root / ".slicer" / "slices" / folder / "S01.json"
    return {s["heading"]: s["body"] for s in json.loads(path.read_text(encoding="utf-8"))["sections"]}

  def test_Merge_SliceEditedOnBothBranches_MergesWithoutHandResolution(self) -> None:
    with support.TempRepo(git=True) as repo, \
         patch.dict(os.environ, {"PYTHONPATH": str(SRC), "SLICER_NO_CODE_WARNING": "1"}):
      self._configure(repo)
      self._project(repo)
      bodies = self._diverge_and_merge(repo, "")
      # `import` also made the configured sections empty; only the four edited ones matter.
      self.assertEqual(
        {h: bodies[h] for h in ("Why", "Check", "Feature extra", "Trunk extra")},
        {"Why": "feature why", "Check": "trunk check",
         "Feature extra": "feature extra", "Trunk extra": "trunk extra"},
      )
      self.assertEqual(repo.run("render")[0], 0)
      code, out, err = repo.run("check")
      self.assertEqual(code, 0, out + err)

  def test_Merge_DoneSliceInSubfolder_UsesTheDriver(self) -> None:
    with support.TempRepo(git=True) as repo, \
         patch.dict(os.environ, {"PYTHONPATH": str(SRC), "SLICER_NO_CODE_WARNING": "1"}):
      self._configure(repo)
      self._project(repo)
      self.assertEqual(repo.run("done", "S01", "--note", "finished", "--render")[0], 0)
      self.assertTrue((repo.root / ".slicer" / "slices" / "done" / "S01.json").is_file())
      bodies = self._diverge_and_merge(repo, "done")
      # `import` also made the configured sections empty; only the four edited ones matter.
      self.assertEqual(
        {h: bodies[h] for h in ("Why", "Check", "Feature extra", "Trunk extra")},
        {"Why": "feature why", "Check": "trunk check",
         "Feature extra": "feature extra", "Trunk extra": "trunk extra"},
      )


if __name__ == "__main__":
  unittest.main()
