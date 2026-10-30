"""`slicer ready` records a spec check's verdict tied to a digest of the slice (s400)."""

from __future__ import annotations

import json
import re
import unittest
from unittest.mock import patch

import support
from slicer import jsonio, mergeindex, model
from slicer.errors import StateError

WHEN = r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$"


def _doc(**readiness: dict) -> dict:
  """An index-shaped document with s01 and s02; `readiness` maps an id to its record."""
  items = []
  for n in range(1, 3):
    item = {
      "id": f"s{n:02d}", "title": f"item {n}", "short_title": f"item {n}", "status": "open",
      "has_slice": True, "depends_on": [], "fields": {},
    }
    if item["id"] in readiness:
      item["readiness"] = readiness[item["id"]]
    items.append(item)
  return {"version": model.SCHEMA_VERSION, "id_prefix": "s", "next_id": 3, "items": items}


RECORD = {"by": "Ada", "at": "2026-10-26T09:00:00Z", "note": "", "digest": "sha256:" + "a" * 64}


class ReadinessTests(unittest.TestCase):
  def repo(self, count: int = 1) -> support.TempRepo:
    """A project whose items each carry a full spec."""
    repo = support.TempRepo()
    self.assertEqual(repo.run("init")[0], 0)
    for n in range(1, count + 1):
      repo.run("add", f"work {n}")
      repo.run("promote", f"S{n:02d}")
      repo.run("edit", f"S{n:02d}", "--section", "Files", "--text", "src/a.py")
      repo.run("edit", f"S{n:02d}", "--section", "Implement", "--text", "Do the thing.")
      repo.run("edit", f"S{n:02d}", "--section", "Check", "--text", "The thing works.")
    return repo

  def ready_of(self, repo: support.TempRepo, item_id: str = "S01") -> object:
    code, out, err = repo.run("show", item_id, "--json")
    self.assertEqual(code, 0, err)
    return json.loads(out)["ready"]

  def mark(self, repo: support.TempRepo, item_id: str = "S01", *extra: str) -> None:
    code, _, err = repo.run("ready", item_id, "--by", "Ada", *extra)
    self.assertEqual(code, 0, err)

  def error_code(self, out: str) -> str:
    return json.loads(out)["error"]["code"]

  def test_Show_NeverChecked_ReadyFalseWithoutARecord(self) -> None:
    with self.repo() as repo:
      payload = json.loads(repo.run("show", "S01", "--json")[1])
      self.assertIs(payload["ready"], False)
      self.assertNotIn("readiness", payload)

  def test_Ready_Records_TrueWithWhoWhenNoteAndDigest(self) -> None:
    with self.repo() as repo:
      code, out, err = repo.run("ready", "S01", "--by", "Ada", "--note", "spec check passed", "--json")
      self.assertEqual((code, err), (0, ""))
      said = json.loads(out)
      self.assertIs(said["ready"], True)
      record = json.loads(repo.run("show", "S01", "--json")[1])["readiness"]
      self.assertEqual(list(record), ["by", "at", "note", "digest"])
      self.assertEqual((record["by"], record["note"]), ("Ada", "spec check passed"))
      self.assertRegex(record["at"], WHEN)
      self.assertRegex(record["digest"], r"^sha256:[0-9a-f]{64}$")
      self.assertEqual(said["readiness"], record)
      self.assertIs(self.ready_of(repo), True)

  def test_Ready_Text_NamesTheItemAndWho(self) -> None:
    with self.repo() as repo:
      code, out, err = repo.run("ready", "S01", "--by", "Ada")
      self.assertEqual((code, err), (0, ""))
      self.assertIn("S01", out)
      self.assertIn("Ada", out)

  def test_Ready_ThenSectionEdit_IsStale(self) -> None:
    for heading in ("Files", "Implement", "Check"):
      with self.subTest(heading=heading), self.repo() as repo:
        self.mark(repo)
        repo.run("edit", "S01", "--section", heading, "--text", "changed")
        self.assertEqual(self.ready_of(repo), "stale")

  def test_Ready_ThenNewSection_IsStale(self) -> None:
    with self.repo() as repo:
      self.mark(repo)
      repo.run("edit", "S01", "--section", "Why", "--text", "late rationale")
      self.assertEqual(self.ready_of(repo), "stale")

  def test_Ready_ThenBoundaryOrLeadEdit_IsStale(self) -> None:
    for flag in ("--boundary", "--lead"):
      with self.subTest(flag=flag), self.repo() as repo:
        self.mark(repo)
        code, _, err = repo.run("edit", "S01", flag, "--text", "Stay inside the loader.")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.ready_of(repo), "stale")

  def test_Ready_ThenRetitle_IsStale(self) -> None:
    with self.repo() as repo:
      self.mark(repo)
      repo.run("set", "S01", "--title", "a different scope")
      self.assertEqual(self.ready_of(repo), "stale")

  def test_Ready_ThenItemNote_StaysTrue(self) -> None:
    with self.repo() as repo:
      self.mark(repo)
      repo.run("note", "S01", "--text", "a progress note")
      self.assertIs(self.ready_of(repo), True)

  def test_Ready_ThenSchedulingFieldEdits_StaysTrue(self) -> None:
    with self.repo(2) as repo:
      self.mark(repo)
      for argv in (("--size", "L"), ("--pass", "P2"), ("--effort", "3"), ("--importance", "3"),
                   ("--depends-on", "S02"), ("--tree", "alpha"), ("--flag", "held")):
        code, _, err = repo.run("set", "S01", *argv)
        self.assertEqual(code, 0, (argv, err))
        self.assertIs(self.ready_of(repo), True, argv)

  def test_Ready_ThenLifecycle_StaysTrue(self) -> None:
    with self.repo() as repo:
      self.mark(repo)
      repo.run("start", "S01")
      repo.run("handoff", "S01")
      self.assertIs(self.ready_of(repo), True)

  def test_Ready_EditRevertedByteForByte_IsTrueAgain(self) -> None:
    with self.repo() as repo:
      self.mark(repo)
      repo.run("edit", "S01", "--section", "Check", "--text", "changed")
      self.assertEqual(self.ready_of(repo), "stale")
      repo.run("edit", "S01", "--section", "Check", "--text", "The thing works.")
      self.assertIs(self.ready_of(repo), True)

  def test_Ready_Rerun_RefreshesTheRecordAndClearsStale(self) -> None:
    with self.repo() as repo:
      self.mark(repo, "S01", "--note", "first")
      first = json.loads(repo.run("show", "S01", "--json")[1])["readiness"]
      repo.run("edit", "S01", "--section", "Check", "--text", "changed")
      code, _, err = repo.run("ready", "S01", "--by", "Grace", "--note", "second")
      self.assertEqual(code, 0, err)
      second = json.loads(repo.run("show", "S01", "--json")[1])["readiness"]
      self.assertEqual((second["by"], second["note"]), ("Grace", "second"))
      self.assertNotEqual(second["digest"], first["digest"])
      self.assertIs(self.ready_of(repo), True)

  def test_Ready_NoSlice_IsNoSlice(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "bare row")
      code, out, _ = repo.run("ready", "S01", "--by", "Ada", "--json")
      self.assertNotEqual(code, 0)
      self.assertEqual(self.error_code(out), "no_slice")
      self.assertIs(self.ready_of(repo), False)
      self.assertNotIn("readiness", json.loads(repo.run("show", "S01", "--json")[1]))

  def test_Ready_UnknownItem_IsNoSuchItem(self) -> None:
    with self.repo() as repo:
      code, out, _ = repo.run("ready", "S99", "--by", "Ada", "--json")
      self.assertNotEqual(code, 0)
      self.assertEqual(self.error_code(out), "no_such_item")

  def test_Ready_MissingOrBlankBy_IsUsageAndWritesNothing(self) -> None:
    with self.repo() as repo:
      before = repo.read(".slicer/index.json"), repo.read(".slicer/log.jsonl")
      for argv in (("ready", "S01"), ("ready", "S01", "--by", "  ")):
        code, out, _ = repo.run(*argv, "--json")
        self.assertEqual(code, 2, argv)
        self.assertEqual(self.error_code(out), "usage", argv)
      self.assertEqual((repo.read(".slicer/index.json"), repo.read(".slicer/log.jsonl")), before)

  def test_Ready_MultilineBy_IsRefused(self) -> None:
    with self.repo() as repo:
      code, out, _ = repo.run("ready", "S01", "--by", "Ada\nGrace", "--json")
      self.assertNotEqual(code, 0)
      self.assertIn("error", json.loads(out))
      self.assertNotIn("readiness", json.loads(repo.run("show", "S01", "--json")[1]))

  def test_Ready_Logs_AReadyEntryWithByAndNote(self) -> None:
    with self.repo() as repo:
      self.mark(repo, "S01", "--note", "spec check passed")
      entry = json.loads(repo.run("log", "--item", "S01", "--limit", "1", "--json")[1])[0]
      self.assertEqual((entry["action"], entry["by"], entry["note"]),
                       ("ready", "Ada", "spec check passed"))

  def test_Ready_Render_OutputIsUnchanged(self) -> None:
    with self.repo() as repo:
      repo.run("render")
      before = {str(p): p.read_bytes() for p in (repo.root / ".slicer" / "render").rglob("*") if p.is_file()}
      self.mark(repo, "S01")
      after = {str(p): p.read_bytes() for p in (repo.root / ".slicer" / "render").rglob("*") if p.is_file()}
      self.assertEqual(after, before)
      self.assertEqual(repo.run("check")[0], 0)

  def test_Unused_IndexAndSliceBytes_CarryNoReadinessKey(self) -> None:
    with self.repo() as repo:
      self.assertNotIn("readiness", repo.read(".slicer/index.json"))
      self.assertNotIn("ready", repo.read(".slicer/slices/S01.json"))
      self.assertNotIn("readiness", repo.state().index.require("S01").to_dict())

  def test_List_Json_ReportsReadyPerItem(self) -> None:
    with self.repo(3) as repo:
      self.mark(repo, "S01")
      self.mark(repo, "S02")
      repo.run("edit", "S02", "--section", "Check", "--text", "changed")
      rows = {row["id"]: row for row in json.loads(repo.run("list", "--json")[1])}
      self.assertEqual({k: v["ready"] for k, v in rows.items()},
                       {"S01": True, "S02": "stale", "S03": False})
      self.assertIn("readiness", rows["S01"])
      self.assertNotIn("readiness", rows["S03"])

  def test_List_BareRow_ReadyFalse(self) -> None:
    with support.TempRepo() as repo:
      repo.run("init")
      repo.run("add", "bare row")
      rows = json.loads(repo.run("list", "--json")[1])
      self.assertIs(rows[0]["ready"], False)

  def test_Lean_KeepsReadyFalseAndDropsEmptyNote(self) -> None:
    with self.repo(2) as repo:
      self.mark(repo, "S01")
      rows = {r["id"]: r for r in json.loads(repo.run("list", "--json", "--lean")[1])}
      self.assertIs(rows["S02"]["ready"], False)
      self.assertIs(rows["S01"]["ready"], True)
      self.assertNotIn("note", rows["S01"]["readiness"])
      shown = json.loads(repo.run("show", "S01", "--json", "--lean")[1])
      self.assertIs(shown["ready"], True)

  def test_Show_Text_AddsAReadyLineOnlyOnceChecked(self) -> None:
    with self.repo() as repo:
      plain = repo.run("show", "S01")[1]
      self.assertNotIn("Ready:", plain)
      self.mark(repo)
      checked = repo.run("show", "S01")[1]
      self.assertRegex(checked, r"Ready: true \(Ada, \d{4}-\d\d-\d\dT")
      repo.run("edit", "S01", "--section", "Check", "--text", "changed")
      self.assertRegex(repo.run("show", "S01")[1], r"Ready: stale \(Ada, ")


class DigestTests(unittest.TestCase):
  def slice(self, **kw: object) -> model.Slice:
    base = dict(
      id="S01", title="t", lead=["lead"], boundary="Stay.",
      sections=[model.Section("Files", "a"), model.Section("Check", "b")],
    )
    return model.Slice(**{**base, **kw})  # type: ignore[arg-type]

  def test_Digest_EqualSlices_AreEqualAndPrefixed(self) -> None:
    a, b = model.spec_digest(self.slice()), model.spec_digest(self.slice())
    self.assertEqual(a, b)
    self.assertRegex(a, r"^sha256:[0-9a-f]{64}$")

  def test_Digest_SpecParts_EachChangeIt(self) -> None:
    base = model.spec_digest(self.slice())
    changed = {
      "title": self.slice(title="u"),
      "lead": self.slice(lead=["other"]),
      "boundary": self.slice(boundary="Go."),
      "body": self.slice(sections=[model.Section("Files", "a"), model.Section("Check", "c")]),
      "heading": self.slice(sections=[model.Section("Files", "a"), model.Section("Done", "b")]),
      "order": self.slice(sections=[model.Section("Check", "b"), model.Section("Files", "a")]),
      "extra": self.slice(sections=[
        model.Section("Files", "a"), model.Section("Check", "b"), model.Section("Check", "")]),
    }
    for name, sl in changed.items():
      self.assertNotEqual(model.spec_digest(sl), base, name)

  def test_Digest_NonSpecParts_DoNotChangeIt(self) -> None:
    base = model.spec_digest(self.slice())
    for name, sl in {
      "notes": self.slice(notes=["a note"]),
      "size": self.slice(size="L"),
      "flags": self.slice(flags=["held"]),
      "findings": self.slice(findings_note="F1"),
      "trees": self.slice(trees_note="alpha", trees_plural=False),
      "depends": self.slice(depends_note="S02"),
    }.items():
      self.assertEqual(model.spec_digest(sl), base, name)

  def test_Digest_FieldBoundaries_AreNotAmbiguous(self) -> None:
    one = self.slice(sections=[model.Section("A", "bc")])
    two = self.slice(sections=[model.Section("Ab", "c")])
    self.assertNotEqual(model.spec_digest(one), model.spec_digest(two))

  def test_Digest_PinnedValue_GuardsTheCanonicalForm(self) -> None:
    # A change to this value makes every stored record stale at once. That is
    # safe but must be deliberate, so the canonical form is pinned here.
    sl = model.Slice(id="S01", title="t", lead=["l"], boundary="b",
                     sections=[model.Section("H", "body")])
    self.assertEqual(
      model.spec_digest(sl),
      "sha256:08bb4f4fcb15b93e8ed60c8ac4fa291713132b9537325c1e18dac019e025fb91",
    )


class RecordShapeTests(unittest.TestCase):
  def item(self, **extra: object) -> dict:
    return {"id": "S01", "title": "t", "status": "open", **extra}

  def test_Item_RoundTripsAReadinessRecord(self) -> None:
    item = model.Item.from_dict(self.item(readiness=RECORD))
    self.assertEqual(item.to_dict()["readiness"], RECORD)
    self.assertEqual(jsonio.dumps(model.Item.from_dict(item.to_dict()).to_dict()),
                     jsonio.dumps(item.to_dict()))

  def test_Item_NoRecord_OmitsTheKey(self) -> None:
    self.assertNotIn("readiness", model.Item.from_dict(self.item()).to_dict())
    self.assertNotIn("readiness", model.Item.from_dict(self.item()).persisted_dict())

  def test_Item_BadRecords_AreCorrupt(self) -> None:
    for bad in ("yes", [], {"by": "", "at": "x", "note": "", "digest": "d"},
                {"by": "Ada", "at": "x", "note": "", "digest": ""},
                {"by": "Ada", "at": 3, "note": "", "digest": "d"}):
      with self.subTest(bad=bad):
        with self.assertRaises(StateError) as caught:
          model.Item.from_dict(self.item(readiness=bad))
        self.assertEqual(caught.exception.code, "corrupt")

  def test_Readiness_States(self) -> None:
    sl = model.Slice(id="S01", title="t", sections=[model.Section("Check", "b")])
    record = model.Readiness("Ada", "2026-10-26T09:00:00Z", "", model.spec_digest(sl))
    unchecked = model.Item(id="S01", title="t", status="open")
    checked = model.Item(id="S01", title="t", status="open", readiness=record)
    self.assertIs(model.readiness_of(unchecked, sl), False)
    self.assertIs(model.readiness_of(unchecked, None), False)
    self.assertIs(model.readiness_of(checked, sl), True)
    sl.sections[0].body = "changed"
    self.assertEqual(model.readiness_of(checked, sl), "stale")
    self.assertEqual(model.readiness_of(checked, None), "stale")


class SchemaTests(unittest.TestCase):
  def test_Schema_IsElevenAndSavesStampIt(self) -> None:
    self.assertEqual(model.SCHEMA_VERSION, 11)
    with ReadinessTests().repo() as repo:
      ReadinessTests().mark(repo)
      self.assertEqual(json.loads(repo.read(".slicer/index.json"))["version"], 11)

  def test_Schema_OlderBuild_RefusesAnIndexThatMayHoldARecord(self) -> None:
    with ReadinessTests().repo() as repo:
      ReadinessTests().mark(repo)
      data = json.loads(repo.read(".slicer/index.json"))
      with patch.object(model, "SCHEMA_VERSION", 9), self.assertRaises(StateError) as caught:
        model.Index.from_dict(data)
      self.assertEqual(caught.exception.code, "schema_too_new")

  def test_Schema_OlderIndex_LoadsWithoutARecordAndWithoutWrites(self) -> None:
    with ReadinessTests().repo() as repo:
      path = repo.root / ".slicer" / "index.json"
      data = json.loads(path.read_text())
      data["version"] = 9
      path.write_text(json.dumps(data, indent=2) + "\n")
      before = path.read_bytes()
      code, out, err = repo.run("show", "S01", "--json")
      self.assertEqual((code, err), (0, ""))
      self.assertIs(json.loads(out)["ready"], False)
      self.assertEqual(path.read_bytes(), before)


class MergeIndexTests(unittest.TestCase):
  def merge(self, base: dict, ours: dict, theirs: dict) -> tuple[int, dict]:
    with support.TempRepo(git=True) as repo:
      repo.run("init")
      for name, doc in (("base", base), ("ours", ours), ("theirs", theirs)):
        repo.write(name, jsonio.dumps(doc))
      code, out, err = repo.run(
        "merge-index", *(str(repo.root / name) for name in ("base", "ours", "theirs")))
      return code, json.loads(repo.read("ours")) if code == 0 else {"out": out + err}

  def test_MergeIndex_RecordOnOneSide_SurvivesAnotherSidesEdit(self) -> None:
    theirs = _doc()
    theirs["items"][0]["status"] = "done"
    code, merged = self.merge(_doc(), _doc(s01=RECORD), theirs)
    self.assertEqual(code, 0, merged)
    first = merged["items"][0]
    self.assertEqual((first["status"], first["readiness"]), ("done", RECORD))

  def test_MergeIndex_RecordsOnDifferentItems_BothKept(self) -> None:
    code, merged = self.merge(_doc(), _doc(s01=RECORD), _doc(s02=RECORD))
    self.assertEqual(code, 0, merged)
    self.assertEqual([i.get("readiness") for i in merged["items"]], [RECORD, RECORD])

  def test_MergeIndex_DifferentRecordsOnOneItem_Conflict(self) -> None:
    other = {**RECORD, "by": "Grace", "digest": "sha256:" + "b" * 64}
    code, merged = self.merge(_doc(), _doc(s01=RECORD), _doc(s01=other))
    self.assertNotEqual(code, 0)

  def test_MergeIndex_Problems_AcceptsARecord(self) -> None:
    self.assertEqual(mergeindex._problems(_doc(s01=RECORD)), [])


class NextOrderTests(unittest.TestCase):
  def picks(self, repo: support.TempRepo, *argv: str) -> list[str]:
    ids = []
    for n in range(4):
      code, out, _ = repo.run("next", *argv, "-n", str(n), "--json")
      if code != 0:
        break
      payload = json.loads(out)
      ids.append((payload["item"] if "--ready" in argv else payload)["id"])
    return ids

  def repo(self) -> support.TempRepo:
    return ReadinessTests().repo(3)

  def test_NextReady_SortsReadyFirstAndDoesNotFilter(self) -> None:
    with self.repo() as repo:
      ReadinessTests().mark(repo, "S02")
      self.assertEqual(self.picks(repo, "--ready"), ["S02", "S01", "S03"])

  def test_NextReady_NothingReady_IsTheExistingOrder(self) -> None:
    with self.repo() as repo:
      self.assertEqual(self.picks(repo, "--ready"), ["S01", "S02", "S03"])

  def test_NextReady_ReadyKeepTheirRelativeOrder(self) -> None:
    with self.repo() as repo:
      ReadinessTests().mark(repo, "S03")
      ReadinessTests().mark(repo, "S02")
      self.assertEqual(self.picks(repo, "--ready"), ["S02", "S03", "S01"])

  def test_NextReady_StaleItem_IsNotPreferred(self) -> None:
    with self.repo() as repo:
      ReadinessTests().mark(repo, "S03")
      repo.run("edit", "S03", "--section", "Check", "--text", "changed")
      self.assertEqual(self.picks(repo, "--ready"), ["S01", "S02", "S03"])

  def test_NextPlain_IgnoresReadiness(self) -> None:
    with self.repo() as repo:
      ReadinessTests().mark(repo, "S02")
      self.assertEqual(self.picks(repo), ["S01", "S02", "S03"])

  def test_NextReady_StartedItemStillComesFirst(self) -> None:
    with self.repo() as repo:
      repo.run("start", "S01")
      ReadinessTests().mark(repo, "S03")
      self.assertEqual(self.picks(repo, "--ready"), ["S01", "S03", "S02"])

  def test_NextReady_BlockedReadyItem_StaysOutOfThePool(self) -> None:
    # Ready only sorts the pickable pool: a blocked ready item stays out of it.
    with self.repo() as repo:
      repo.run("set", "S02", "--depends-on", "S01")
      ReadinessTests().mark(repo, "S02")
      self.assertEqual(self.picks(repo, "--ready"), ["S01", "S03"])

  def test_NextReadyBatch_ListsReadyFirst(self) -> None:
    with self.repo() as repo:
      ReadinessTests().mark(repo, "S03")
      code, out, err = repo.run("next", "--ready", "--batch", "3", "--json")
      self.assertEqual((code, err), (0, ""))
      self.assertEqual([e["item"]["id"] for e in json.loads(out)["items"]],
                       ["S03", "S01", "S02"])

  def test_NextReadyBatch_BlockedReadyItem_StaysAfterTheUnblockedOnes(self) -> None:
    with self.repo() as repo:
      repo.run("set", "S02", "--depends-on", "S01")
      ReadinessTests().mark(repo, "S02")
      code, out, err = repo.run("next", "--ready", "--batch", "3", "--json")
      self.assertEqual((code, err), (0, ""))
      self.assertEqual([e["item"]["id"] for e in json.loads(out)["items"]],
                       ["S01", "S03", "S02"])

  def passes(self, repo: support.TempRepo, *specs: tuple[str, str]) -> None:
    """Declare passes p1 then p2, then give each (id, pass) item its pass."""
    for key in ("p1", "p2"):
      self.assertEqual(repo.run("prose", "add-pass", key)[0], 0)
    for item_id, key in specs:
      self.assertEqual(repo.run("set", item_id, "--pass", key)[0], 0)

  def test_NextReady_ReadyInLaterPass_DoesNotPassEarlierPassItems(self) -> None:
    with self.repo() as repo:
      self.passes(repo, ("S01", "p1"), ("S02", "p1"), ("S03", "p2"))
      ReadinessTests().mark(repo, "S03")
      self.assertEqual(self.picks(repo, "--ready"), ["S01", "S02", "S03"])
      self.assertEqual(self.picks(repo), ["S01", "S02", "S03"])

  def test_NextReady_ReadyInSamePass_ComesBeforeUnreadyInThatPass(self) -> None:
    with self.repo() as repo:
      self.passes(repo, ("S01", "p1"), ("S02", "p1"), ("S03", "p2"))
      ReadinessTests().mark(repo, "S02")
      ReadinessTests().mark(repo, "S03")
      self.assertEqual(self.picks(repo, "--ready"), ["S02", "S01", "S03"])
      self.assertEqual(self.picks(repo), ["S01", "S02", "S03"])

  def test_NextReady_ReadyItemWithNoPass_StaysInItsSlot(self) -> None:
    with self.repo() as repo:
      self.passes(repo, ("S01", "p1"), ("S03", "p2"))
      ReadinessTests().mark(repo, "S02")
      self.assertEqual(self.picks(repo, "--ready"), self.picks(repo))

  def test_NextReady_ReadyUnpassedItem_LeadsOnlyAmongUnpassedItems(self) -> None:
    with self.repo() as repo:
      repo.run("add", "work 4")
      self.passes(repo, ("S01", "p1"), ("S04", "p2"))
      ReadinessTests().mark(repo, "S03")
      plain = self.picks(repo)
      ready = self.picks(repo, "--ready")
      self.assertEqual(plain, ["S01", "S02", "S03", "S04"])
      self.assertEqual(ready, ["S01", "S03", "S02", "S04"])

  def test_NextReady_NoDeclaredPasses_KeysDoNotSplitTheGroup(self) -> None:
    # With no declared pass the ranking ignores pass keys, so ready leads across them.
    with self.repo() as repo:
      for item_id, key in (("S01", "a"), ("S02", "b"), ("S03", "b")):
        repo.run("set", item_id, "--pass", key)
      ReadinessTests().mark(repo, "S02")
      self.assertEqual(self.picks(repo), ["S01", "S02", "S03"])
      self.assertEqual(self.picks(repo, "--ready"), ["S02", "S01", "S03"])

  def test_NextReady_UndeclaredPasses_ShareOneRankAfterTheDeclaredOnes(self) -> None:
    with self.repo() as repo:
      repo.run("prose", "add-pass", "p1")
      for item_id, key in (("S01", "zz"), ("S02", "yy"), ("S03", "p1")):
        repo.run("set", item_id, "--pass", key)
      ReadinessTests().mark(repo, "S02")
      self.assertEqual(self.picks(repo), ["S03", "S01", "S02"])
      self.assertEqual(self.picks(repo, "--ready"), ["S03", "S02", "S01"])


class NextQueueOrderTests(unittest.TestCase):
  def repo(self) -> support.TempRepo:
    repo = ReadinessTests().repo(3)
    path = repo.root / ".slicer" / "config.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg.update(claim_owner="Ada", statuses=cfg["statuses"] | {"draft": "draft"})
    path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    for item_id in ("S01", "S02", "S03"):
      repo.run("set", item_id, "--status", "draft")
    return repo

  def picks(self, repo: support.TempRepo, *argv: str) -> list[str]:
    ids = []
    for n in range(3):
      code, out, _ = repo.run("next", "--status", "draft", *argv, "-n", str(n), "--json")
      if code != 0:
        break
      payload = json.loads(out)
      ids.append((payload["item"] if "--ready" in argv else payload)["id"])
    return ids

  def test_NextReadyQueue_ClaimedFirst_ThenReadyBeforeUnreadyUnclaimed(self) -> None:
    with self.repo() as repo:
      repo.run("next", "--status", "draft", "--start", "--owner", "Ada")
      ReadinessTests().mark(repo, "S03")
      self.assertEqual(self.picks(repo), ["S01", "S02", "S03"])
      self.assertEqual(self.picks(repo, "--ready"), ["S01", "S03", "S02"])

  def test_NextReadyQueue_NothingReady_IsTheQueueOrder(self) -> None:
    with self.repo() as repo:
      self.assertEqual(self.picks(repo, "--ready"), self.picks(repo))


class NextReviewOrderTests(unittest.TestCase):
  def test_NextReadyReview_ReviewingFirst_ThenReadyBeforeUnreadyReview(self) -> None:
    with ReadinessTests().repo(3) as repo:
      for item_id in ("S01", "S02", "S03"):
        repo.run("set", item_id, "--status", "review")
      repo.run("next", "--status", "review", "--start", "--owner", "Ada")
      ReadinessTests().mark(repo, "S03")
      ids = []
      for n in range(3):
        out = repo.run("next", "--status", "review", "--ready", "-n", str(n), "--json")[1]
        ids.append(json.loads(out)["item"]["id"])
      self.assertEqual(ids, ["S01", "S03", "S02"])


if __name__ == "__main__":
  unittest.main()
