"""Per-slice changelog entries in `landed-changed/`, assembled into the changelog at release (s454).

Every test builds its own fixture project. The fixture's config is patched as raw JSON so
a test of one command does not depend on `slicer config` accepting the changelog keys; the
keys themselves are tested apart. Parallel slice branches each write their own file, so
`test_ChangelogEntries_TwoBranches_MergeWithoutConflict` runs a real `git merge`.
"""

from __future__ import annotations

import io
import json
import unittest
from unittest.mock import patch

import support  # noqa: F401 -- puts src/ on sys.path
from slicer import changelog
from slicer.config import Config
from slicer.errors import ConfigError

from test_land import BRANCH, LandCase

CATEGORIES = ["Added", "Changed", "Deprecated", "Removed", "Fixed", "Security"]

BASE = """\
# Changelog

Notes.

## [Unreleased]

## [1.0.0] - 2026-01-01

### Added

- first (s01)
"""

MIXED = """\
# Changelog

## [Unreleased]

### Added

- First added (s01)

- Second added,
  over two lines (s02)
- Joined added, no id

### Fixed

- Fixed it (s03)
- Fixed unnamed too

## [1.0.0] - 2026-01-01
"""


def patch_config(repo: support.TempRepo, **keys: object) -> None:
  """Set raw keys in the fixture's config.json, whatever this build knows about them."""
  path = repo.root / ".slicer" / "config.json"
  raw = json.loads(path.read_text(encoding="utf-8"))
  raw.update(keys)
  path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


class ChangelogCase(unittest.TestCase):
  def repo(
    self, *, categories: list[str] | None = None, git: bool = False, changelog_text: str = BASE,
    on: bool = True, **extra: object,
  ) -> support.TempRepo:
    """Items s01, s02, s03, a changelog, and the feature on with the usual categories."""
    repo = support.TempRepo(git=git)
    self.addCleanup(repo.close)
    self.enterContext(support.isolated_discovery(repo.root))
    self.assertEqual(repo.run("init", "--id-prefix", "s")[0], 0)
    for title in ("one", "two", "three"):
      self.assertEqual(repo.run("add", title)[0], 0)
    keys: dict[str, object] = dict(extra)
    if on:
      keys["changelog_path"] = "CHANGELOG.md"
    keys["changelog_categories"] = CATEGORIES if categories is None else categories
    patch_config(repo, **keys)
    repo.write("CHANGELOG.md", changelog_text)
    self.assertEqual(repo.run("render")[0], 0)
    return repo

  def add(self, repo: support.TempRepo, item: str, category: str | None, text: str) -> None:
    argv = ["changelog", "add", item, "--text", text]
    if category is not None:
      argv += ["--category", category]
    code, out, err = repo.run(*argv)
    self.assertEqual(code, 0, f"{out} {err}")

  def refused(self, repo: support.TempRepo, error: str, *argv: str) -> dict:
    code, out, err = repo.run(*argv, "--json")
    self.assertEqual(code, 2, f"{out} {err}")
    payload = json.loads(out)["error"]
    self.assertEqual(payload["code"], error, payload["message"])
    return payload

  def entry_files(self, repo: support.TempRepo) -> list[str]:
    folder = repo.root / "landed-changed"
    return sorted(p.name for p in folder.glob("*.md")) if folder.is_dir() else []


class ChangelogAddTests(ChangelogCase):
  def test_ChangelogAdd_WritesCategoryLineAndText(self) -> None:
    repo = self.repo()
    code, out, err = repo.run("changelog", "add", "s01", "--category", "Changed", "--text", "x")
    self.assertEqual((code, err), (0, ""), out)
    self.assertEqual(repo.read("landed-changed/s01.md"), "category: Changed\n\nx\n")

  def test_ChangelogAdd_Json_NamesFileAndCategory(self) -> None:
    repo = self.repo()
    code, out, _ = repo.run("changelog", "add", "s01", "--category", "Fixed", "--text", "x", "--json")
    self.assertEqual(code, 0, out)
    self.assertEqual(json.loads(out), {
      "id": "s01", "path": "landed-changed/s01.md", "category": "Fixed", "replaced": False,
    })

  def test_ChangelogAdd_Stdin_ReadsTheText(self) -> None:
    repo = self.repo()
    with patch("sys.stdin", io.StringIO("from stdin\nsecond line\n\n")):
      code, out, err = repo.run("changelog", "add", "s01", "--category", "Added", "--stdin")
    self.assertEqual(code, 0, f"{out} {err}")
    self.assertEqual(repo.read("landed-changed/s01.md"), "category: Added\n\nfrom stdin\nsecond line\n")

  def test_ChangelogAdd_NeitherTextNorStdin_IsUsage(self) -> None:
    repo = self.repo()
    self.refused(repo, "usage", "changelog", "add", "s01", "--category", "Added")
    self.assertEqual(self.entry_files(repo), [])

  def test_ChangelogAdd_BlankOrBulletText_IsRefused(self) -> None:
    repo = self.repo()
    for text in ("  \n", "- a bullet"):
      with self.subTest(text=text):
        self.refused(repo, "changelog_entry", "changelog", "add", "s01", "--category", "Added",
                     "--text", text)
        self.assertEqual(self.entry_files(repo), [])

  def test_ChangelogAdd_UnknownCategory_IsRefused(self) -> None:
    repo = self.repo()
    payload = self.refused(repo, "changelog_category", "changelog", "add", "s01",
                           "--category", "Bogus", "--text", "x")
    self.assertIn("Added", payload["message"])
    self.assertEqual(self.entry_files(repo), [])
    self.assertFalse((repo.root / "landed-changed").exists())

  def test_ChangelogAdd_CategoryMissingWhenConfigured_IsRefused(self) -> None:
    repo = self.repo()
    self.refused(repo, "changelog_category", "changelog", "add", "s01", "--text", "x")
    self.assertEqual(self.entry_files(repo), [])

  def test_ChangelogAdd_NoCategoriesConfigured_WritesNoCategoryLine(self) -> None:
    repo = self.repo(categories=[])
    self.refused(repo, "changelog_category", "changelog", "add", "s01",
                 "--category", "Added", "--text", "x")
    self.add(repo, "s01", None, "plain (s01)")
    self.assertEqual(repo.read("landed-changed/s01.md"), "plain (s01)\n")

  def test_ChangelogAdd_ExistingEntry_RefusedUnlessReplace(self) -> None:
    repo = self.repo()
    self.add(repo, "s01", "Added", "first")
    self.refused(repo, "changelog_entry_exists", "changelog", "add", "s01",
                 "--category", "Fixed", "--text", "second")
    self.assertEqual(repo.read("landed-changed/s01.md"), "category: Added\n\nfirst\n")
    code, out, err = repo.run("changelog", "add", "s01", "--category", "Fixed", "--text", "second",
                              "--replace")
    self.assertEqual((code, err), (0, ""), out)
    self.assertEqual(repo.read("landed-changed/s01.md"), "category: Fixed\n\nsecond\n")

  def test_ChangelogAdd_ReplaceJson_SaysReplaced(self) -> None:
    repo = self.repo()
    self.add(repo, "s01", "Added", "first")
    code, out, _ = repo.run("changelog", "add", "s01", "--category", "Added", "--text", "again",
                            "--replace", "--json")
    self.assertEqual(code, 0, out)
    self.assertTrue(json.loads(out)["replaced"])

  def test_ChangelogAdd_UnknownItem_IsRefused(self) -> None:
    repo = self.repo()
    self.refused(repo, "no_such_item", "changelog", "add", "s99", "--category", "Added",
                 "--text", "x")
    self.assertFalse((repo.root / "landed-changed").exists())

  def test_ChangelogAdd_Off_IsRefused(self) -> None:
    repo = self.repo(on=False)
    payload = self.refused(repo, "changelog_off", "changelog", "add", "s01", "--category", "Added",
                           "--text", "x")
    self.assertIn("changelog_path", payload["message"])
    self.assertFalse((repo.root / "landed-changed").exists())

  def test_ChangelogAssembleAndImport_Off_AreRefused(self) -> None:
    repo = self.repo(on=False)
    for sub in ("assemble", "import"):
      with self.subTest(sub=sub):
        self.refused(repo, "changelog_off", "changelog", sub)

  def test_ChangelogAdd_StaleRender_IsNotHealed(self) -> None:
    """The entry files are not tracking state, so a write to them leaves `render/` alone."""
    repo = self.repo()
    roadmap = repo.root / ".slicer" / "render" / "ROADMAP.md"
    roadmap.write_text(roadmap.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
    before = roadmap.read_bytes()
    code, out, err = repo.run("changelog", "add", "s01", "--category", "Added", "--text", "x")
    self.assertEqual((code, err), (0, ""), out)
    self.assertEqual(roadmap.read_bytes(), before)

  def test_ChangelogAdd_CustomEntriesDir_IsUsed(self) -> None:
    repo = self.repo(changelog_entries_dir="notes/next")
    self.add(repo, "s01", "Added", "x")
    self.assertEqual(repo.read("notes/next/s01.md"), "category: Added\n\nx\n")


class ChangelogMergeTests(ChangelogCase):
  def test_ChangelogEntries_TwoBranches_MergeWithoutConflict(self) -> None:
    repo = self.repo(git=True)
    repo.commit("base")
    base = repo._git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    for branch, item in (("one", "s01"), ("two", "s02")):
      self.assertEqual(repo._git("checkout", "-q", "-b", branch, base).returncode, 0)
      self.add(repo, item, "Added", f"entry for {item}")
      repo.commit(f"entry {item}")
    merged = repo._git("merge", "--no-edit", "one")
    self.assertEqual(merged.returncode, 0, merged.stdout + merged.stderr)
    self.assertEqual(self.entry_files(repo), ["s01.md", "s02.md"])


class ChangelogAssembleTests(ChangelogCase):
  def test_ChangelogAssemble_GroupsByCategoryInConfigOrder_HighestIdFirst(self) -> None:
    repo = self.repo()
    self.add(repo, "s01", "Fixed", "fix one (s01)")
    self.add(repo, "s03", "Changed", "change three (s03)")
    self.add(repo, "s02", "Changed", "change two (s02)")
    code, out, err = repo.run("changelog", "assemble")
    self.assertEqual((code, err), (0, ""), out)
    self.assertEqual(repo.read("CHANGELOG.md"), """\
# Changelog

Notes.

## [Unreleased]

### Changed

- change three (s03)
- change two (s02)

### Fixed

- fix one (s01)

## [1.0.0] - 2026-01-01

### Added

- first (s01)
""")
    self.assertEqual(self.entry_files(repo), [])

  def test_ChangelogAssemble_Json_NamesTheEntries(self) -> None:
    repo = self.repo()
    self.add(repo, "s01", "Fixed", "fix")
    self.add(repo, "s02", "Added", "add")
    code, out, _ = repo.run("changelog", "assemble", "--json")
    self.assertEqual(code, 0, out)
    self.assertEqual(json.loads(out), {
      "changelog": "CHANGELOG.md", "dry_run": False,
      "entries": [
        {"file": "landed-changed/s02.md", "category": "Added"},
        {"file": "landed-changed/s01.md", "category": "Fixed"},
      ],
    })

  def test_ChangelogAssemble_NoEntries_ChangesNothing(self) -> None:
    repo = self.repo()
    code, out, err = repo.run("changelog", "assemble")
    self.assertEqual((code, err), (0, ""), out)
    self.assertEqual(repo.read("CHANGELOG.md"), BASE)

  def test_ChangelogAssemble_NoCategories_FlatList(self) -> None:
    repo = self.repo(categories=[])
    self.add(repo, "s01", None, "a (s01)")
    self.add(repo, "s02", None, "b (s02)")
    self.assertEqual(repo.run("changelog", "assemble")[0], 0)
    self.assertIn("## [Unreleased]\n\n- b (s02)\n- a (s01)\n\n## [1.0.0]", repo.read("CHANGELOG.md"))
    self.assertNotIn("###", repo.read("CHANGELOG.md").split("## [1.0.0]")[0])

  def test_ChangelogAssemble_MultiLineEntry_IndentsContinuation(self) -> None:
    repo = self.repo()
    self.add(repo, "s01", "Added", "line one\nline two")
    self.assertEqual(repo.run("changelog", "assemble")[0], 0)
    self.assertIn("### Added\n\n- line one\n  line two\n\n## [1.0.0]", repo.read("CHANGELOG.md"))

  def test_ChangelogAssemble_UnreleasedLast_EndsWithOneNewline(self) -> None:
    repo = self.repo(changelog_text="# Changelog\n\n## [Unreleased]\n")
    self.add(repo, "s01", "Added", "a")
    self.assertEqual(repo.run("changelog", "assemble")[0], 0)
    self.assertEqual(repo.read("CHANGELOG.md"), "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- a\n")

  def test_ChangelogAssemble_DryRun_PrintsSameBytesAndWritesNothing(self) -> None:
    repo = self.repo()
    self.add(repo, "s01", "Fixed", "fix")
    self.add(repo, "s02", "Added", "add")
    before = repo.read("CHANGELOG.md")
    first = repo.run("changelog", "assemble", "--dry-run")
    second = repo.run("changelog", "assemble", "--dry-run")
    self.assertEqual(first[0], 0, first)
    self.assertEqual(first, second)
    self.assertEqual(repo.read("CHANGELOG.md"), before)
    self.assertEqual(self.entry_files(repo), ["s01.md", "s02.md"])
    self.assertNotEqual(first[1], before)
    self.assertEqual(repo.run("changelog", "assemble")[0], 0)
    self.assertEqual(first[1], repo.read("CHANGELOG.md"))

  def test_ChangelogAssemble_NonEmptyUnreleased_IsRefused(self) -> None:
    text = BASE.replace("## [Unreleased]\n", "## [Unreleased]\n\n- already here\n")
    repo = self.repo(changelog_text=text)
    self.add(repo, "s01", "Added", "a")
    payload = self.refused(repo, "changelog_unreleased_not_empty", "changelog", "assemble")
    self.assertIn("slicer changelog import", payload["message"])
    self.assertEqual(repo.read("CHANGELOG.md"), text)
    self.assertEqual(self.entry_files(repo), ["s01.md"])

  def test_ChangelogAssemble_NoHeading_IsRefused(self) -> None:
    repo = self.repo(changelog_text="# Changelog\n\n## [1.0.0]\n")
    self.add(repo, "s01", "Added", "a")
    payload = self.refused(repo, "changelog_no_heading", "changelog", "assemble")
    self.assertIn("CHANGELOG.md", payload["message"])
    self.assertEqual(self.entry_files(repo), ["s01.md"])

  def test_ChangelogAssemble_CustomHeading_IsMatchedAsAWholeLine(self) -> None:
    text = "# Changes\n\n## Unreleased\n\n## Unreleased work\n\nnot this\n\n## 1.0\n"
    repo = self.repo(changelog_text=text, changelog_unreleased_heading="## Unreleased")
    self.add(repo, "s01", "Added", "a")
    self.assertEqual(repo.run("changelog", "assemble")[0], 0)
    self.assertEqual(
      repo.read("CHANGELOG.md"),
      "# Changes\n\n## Unreleased\n\n### Added\n\n- a\n\n## Unreleased work\n\nnot this\n\n## 1.0\n",
    )

  def test_ChangelogAssemble_ProblemEntry_RefusesAllAndKeepsFiles(self) -> None:
    repo = self.repo()
    self.add(repo, "s01", "Added", "a")
    repo.write("landed-changed/s02.md", "no category line\n")
    payload = self.refused(repo, "changelog_entry", "changelog", "assemble")
    self.assertIn("landed-changed/s02.md:1", payload["message"])
    self.assertEqual(repo.read("CHANGELOG.md"), BASE)
    self.assertEqual(self.entry_files(repo), ["s01.md", "s02.md"])


class ChangelogImportTests(ChangelogCase):
  def test_ChangelogImport_MixedLayout_OneFilePerEntry(self) -> None:
    repo = self.repo(changelog_text=MIXED)
    code, out, err = repo.run("changelog", "import")
    self.assertEqual((code, err), (0, ""), out)
    self.assertEqual(self.entry_files(repo),
                     ["entry-001.md", "entry-002.md", "s01.md", "s02.md", "s03.md"])
    self.assertEqual(repo.read("landed-changed/s01.md"), "category: Added\n\nFirst added (s01)\n")
    self.assertEqual(repo.read("landed-changed/s02.md"),
                     "category: Added\n\nSecond added,\nover two lines (s02)\n")
    self.assertEqual(repo.read("landed-changed/entry-001.md"),
                     "category: Added\n\nJoined added, no id\n")
    self.assertEqual(repo.read("landed-changed/s03.md"), "category: Fixed\n\nFixed it (s03)\n")
    self.assertEqual(repo.read("landed-changed/entry-002.md"),
                     "category: Fixed\n\nFixed unnamed too\n")
    self.assertEqual(repo.read("CHANGELOG.md"),
                     "# Changelog\n\n## [Unreleased]\n\n## [1.0.0] - 2026-01-01\n")

  def test_ChangelogImport_DryRun_WritesNothing(self) -> None:
    repo = self.repo(changelog_text=MIXED)
    code, out, err = repo.run("changelog", "import", "--dry-run", "--json")
    self.assertEqual((code, err), (0, ""), out)
    payload = json.loads(out)
    self.assertTrue(payload["dry_run"])
    self.assertEqual([e["file"] for e in payload["entries"]], [
      "landed-changed/s01.md", "landed-changed/s02.md", "landed-changed/entry-001.md",
      "landed-changed/s03.md", "landed-changed/entry-002.md",
    ])
    self.assertEqual(repo.read("CHANGELOG.md"), MIXED)
    self.assertEqual(self.entry_files(repo), [])

  def test_ChangelogImport_EmptyUnreleased_ChangesNothing(self) -> None:
    repo = self.repo()
    code, out, err = repo.run("changelog", "import")
    self.assertEqual((code, err), (0, ""), out)
    self.assertEqual(repo.read("CHANGELOG.md"), BASE)
    self.assertEqual(self.entry_files(repo), [])

  def test_ChangelogImport_IdNamingNoItemOrRepeated_FallsBackToEntryNames(self) -> None:
    text = BASE.replace(
      "## [Unreleased]\n",
      "## [Unreleased]\n\n### Added\n\n- one (s01)\n- again (s01)\n- other (s77)\n",
    )
    repo = self.repo(changelog_text=text)
    self.assertEqual(repo.run("changelog", "import")[0], 0)
    self.assertEqual(self.entry_files(repo), ["entry-001.md", "entry-002.md", "s01.md"])
    self.assertEqual(repo.read("landed-changed/entry-001.md"), "category: Added\n\nagain (s01)\n")

  def test_ChangelogImport_TrailingFullStopAfterId_NamesTheItem(self) -> None:
    text = BASE.replace("## [Unreleased]\n", "## [Unreleased]\n\n### Added\n\n- Did the thing (s01).\n")
    repo = self.repo(changelog_text=text)
    self.assertEqual(repo.run("changelog", "import")[0], 0)
    self.assertEqual(self.entry_files(repo), ["s01.md"])
    self.assertEqual(repo.read("landed-changed/s01.md"), "category: Added\n\nDid the thing (s01).\n")

  def test_ChangelogImport_EntryNnnItem_DoesNotOverwrite(self) -> None:
    """An item may legally be called `entry-001`; a fallback name must not land on its file."""
    text = BASE.replace(
      "## [Unreleased]\n",
      "## [Unreleased]\n\n### Added\n\n- unnamed first\n- named (entry-001)\n",
    )
    repo = self.repo(changelog_text=text)
    self.assertEqual(repo.run("add", "odd", "--id", "entry-001")[0], 0)
    self.assertEqual(repo.run("render")[0], 0)
    self.assertEqual(repo.run("changelog", "import")[0], 0)
    self.assertEqual(self.entry_files(repo), ["entry-001.md", "entry-002.md"])
    self.assertEqual(repo.read("landed-changed/entry-001.md"), "category: Added\n\nnamed (entry-001)\n")
    self.assertEqual(repo.read("landed-changed/entry-002.md"), "category: Added\n\nunnamed first\n")
    self.assertEqual(repo.run("changelog", "assemble")[0], 0)
    self.assertIn("### Added\n\n- named (entry-001)\n- unnamed first\n", repo.read("CHANGELOG.md"))

  def test_ChangelogAssemble_EntryNnnItem_SortsAsAnItemNotAsImported(self) -> None:
    repo = self.repo()
    self.assertEqual(repo.run("add", "odd", "--id", "entry-005")[0], 0)
    self.assertEqual(repo.run("render")[0], 0)
    self.add(repo, "entry-005", "Added", "the item")
    repo.write("landed-changed/entry-001.md", "category: Added\n\nimported\n")
    self.assertEqual(repo.run("changelog", "assemble")[0], 0)
    self.assertIn("### Added\n\n- the item\n- imported\n", repo.read("CHANGELOG.md"))

  def test_ChangelogImport_ThenAssemble_GivesTheSameEntries(self) -> None:
    repo = self.repo(changelog_text=MIXED)

    def entries(text: str) -> set[tuple[str, str]]:
      body = text.split("## [Unreleased]\n", 1)[1].split("## [1.0.0]", 1)[0]
      found, problems = changelog.split_unreleased(body, CATEGORIES)
      self.assertEqual(problems, [])
      return {(entry.category, entry.text) for entry in found}

    original = entries(MIXED)
    self.assertEqual(len(original), 5)
    self.assertEqual(repo.run("changelog", "import")[0], 0)
    self.assertEqual(repo.run("changelog", "assemble")[0], 0)
    assembled = repo.read("CHANGELOG.md")
    self.assertEqual(entries(assembled), original)
    self.assertEqual(assembled, """\
# Changelog

## [Unreleased]

### Added

- Second added,
  over two lines (s02)
- First added (s01)
- Joined added, no id

### Fixed

- Fixed it (s03)
- Fixed unnamed too

## [1.0.0] - 2026-01-01
""")

  def test_ChangelogImport_IsAllOrNothing(self) -> None:
    unknown = MIXED.replace("### Fixed", "### Other")
    cases = {
      "heading not a category": (unknown, None),
      "existing entry file": (MIXED, ("s02", "category: Added\n\nalready\n")),
      "bullet before any heading": (
        MIXED.replace("### Added\n\n", "", 1), None),
      "prose": (MIXED.replace("- First added (s01)", "Some prose."), None),
    }
    for name, (text, existing) in cases.items():
      with self.subTest(name):
        repo = self.repo(changelog_text=text)
        if existing is not None:
          repo.write(f"landed-changed/{existing[0]}.md", existing[1])
        before = {n: repo.read(f"landed-changed/{n}") for n in self.entry_files(repo)}
        self.refused(repo, "changelog_import", "changelog", "import")
        self.assertEqual(repo.read("CHANGELOG.md"), text)
        self.assertEqual({n: repo.read(f"landed-changed/{n}") for n in self.entry_files(repo)}, before)


class ChangelogCheckTests(ChangelogCase):
  def problems(self, repo: support.TempRepo) -> tuple[int, list[str]]:
    code, out, _ = repo.run("check", "--json")
    return code, json.loads(out)["problems"]

  def test_Check_ValidEntries_AreClean(self) -> None:
    repo = self.repo()
    self.add(repo, "s01", "Added", "a")
    self.assertEqual(self.problems(repo), (0, []))

  def test_Check_MalformedEntry_NamesTheFile(self) -> None:
    for name, text in {
      "no category line": "just text\n",
      "category not configured": "category: Bogus\n\ntext\n",
      "no blank line": "category: Added\ntext\n",
      "no text": "category: Added\n\n",
    }.items():
      with self.subTest(name):
        repo = self.repo()
        repo.write("landed-changed/s01.md", text)
        code, problems = self.problems(repo)
        self.assertEqual(code, 1)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("landed-changed/s01.md:", problems[0])

  def test_Check_MalformedEntry_NamesLineOne(self) -> None:
    repo = self.repo()
    repo.write("landed-changed/s01.md", "category: Bogus\n\ntext\n")
    code, problems = self.problems(repo)
    self.assertEqual(code, 1)
    self.assertIn("landed-changed/s01.md:1:", problems[0])

  def test_Check_EntryForUnknownId_IsAProblem(self) -> None:
    repo = self.repo()
    repo.write("landed-changed/s99.md", "category: Added\n\ntext\n")
    code, problems = self.problems(repo)
    self.assertEqual(code, 1)
    self.assertEqual(len(problems), 1, problems)
    self.assertIn("landed-changed/s99.md", problems[0])
    self.assertIn("no item", problems[0])

  def test_Check_TwoFilesForOneItem_IsAProblem(self) -> None:
    repo = self.repo()
    repo.write("landed-changed/s01.md", "category: Added\n\none\n")
    repo.write("landed-changed/S01.md", "category: Added\n\ntwo\n")
    code, problems = self.problems(repo)
    self.assertEqual(code, 1)
    self.assertEqual(len(problems), 1, problems)
    self.assertIn("landed-changed/S01.md", problems[0])
    self.assertIn("landed-changed/s01.md", problems[0])

  def test_Check_EntryNamedEntryNnn_IsAccepted(self) -> None:
    repo = self.repo()
    repo.write("landed-changed/entry-001.md", "category: Added\n\nimported\n")
    self.assertEqual(self.problems(repo), (0, []))

  def test_Check_Off_ReadsNoEntryFiles(self) -> None:
    repo = self.repo(on=False)
    repo.write("landed-changed/s99.md", "garbage\n")
    self.assertEqual(self.problems(repo), (0, []))

  def test_Check_OtherFilesInTheFolder_AreIgnored(self) -> None:
    repo = self.repo()
    repo.write("landed-changed/.gitkeep", "")
    repo.write("landed-changed/notes.txt", "garbage\n")
    self.assertEqual(self.problems(repo), (0, []))


class ChangelogHandoffTests(ChangelogCase):
  def started(self, **keys: object) -> support.TempRepo:
    repo = self.repo(**keys)
    for argv in (("promote", "s01"), ("start", "s01"), ("render",)):
      self.assertEqual(repo.run(*argv)[0], 0)
    return repo

  def test_Handoff_NoEntry_WarnsOnlyWhenRequired(self) -> None:
    repo = self.started(changelog_require_entry=True)
    code, out, err = repo.run("handoff", "s01")
    self.assertEqual(code, 0, f"{out} {err}")
    self.assertIn("landed-changed/s01.md", err)
    self.assertIn("slicer changelog add s01", err)
    quiet = self.started(changelog_require_entry=False)
    code, out, err = quiet.run("handoff", "s01")
    self.assertEqual((code, err), (0, ""), out)

  def test_Handoff_EntryPresent_IsQuiet(self) -> None:
    repo = self.started(changelog_require_entry=True)
    self.add(repo, "s01", "Added", "a")
    code, out, err = repo.run("handoff", "s01")
    self.assertEqual((code, err), (0, ""), out)

  def test_Handoff_RequiredButFeatureOff_IsQuiet(self) -> None:
    repo = self.started(on=False, changelog_require_entry=True)
    code, out, err = repo.run("handoff", "s01")
    self.assertEqual((code, err), (0, ""), out)

  def test_Handoff_Json_GainsNothing(self) -> None:
    repo = self.started(changelog_require_entry=True)
    code, out, err = repo.run("handoff", "s01", "--json")
    self.assertEqual(code, 0, err)
    self.assertNotIn("changelog", out)


class ChangelogLandTests(LandCase):
  def landed(self, **keys: object) -> tuple[support.TempRepo, int, str, str]:
    repo = self.repo()
    patch_config(repo, changelog_path="CHANGELOG.md", **keys)
    repo.write("CHANGELOG.md", "# Changelog\n\n## [Unreleased]\n")
    repo.commit("changelog")
    self.worktree(repo)
    self.merge(repo)
    code, out, err = self.land(repo)
    return repo, code, out, err

  def test_Land_NoEntry_WarnsOnlyWhenRequired(self) -> None:
    repo, code, out, err = self.landed(changelog_require_entry=True)
    self.assertEqual(code, 0, f"{out} {err}")
    self.assertIn("landed-changed/S01.md", err)
    self.assertEqual(self.status(repo), "done")
    _, code, out, err = self.landed(changelog_require_entry=False)
    self.assertEqual((code, err), (0, ""), out)


class ChangelogConfigTests(unittest.TestCase):
  def test_Config_ChangelogKeys_DefaultOffAndRoundTrip(self) -> None:
    cfg = Config.from_dict({})
    self.assertEqual(
      (cfg.changelog_path, cfg.changelog_entries_dir, cfg.changelog_unreleased_heading,
       cfg.changelog_categories, cfg.changelog_require_entry),
      ("", "landed-changed", "## [Unreleased]", [], False),
    )
    given = {
      "changelog_path": "CHANGELOG.md", "changelog_entries_dir": "notes",
      "changelog_unreleased_heading": "## Unreleased", "changelog_categories": ["Added", "Fixed"],
      "changelog_require_entry": True,
    }
    out = Config.from_dict(given).to_dict()
    self.assertEqual({key: out[key] for key in given}, given)

  def test_Config_BadChangelogKeys_AreConfigErrors(self) -> None:
    bad = {
      "changelog_path": 5,
      "changelog_entries_dir": "",
      "changelog_unreleased_heading": "Unreleased",
      "changelog_categories": ["Added", "Added"],
      "changelog_require_entry": "yes",
    }
    for key, value in bad.items():
      with self.subTest(key=key):
        with self.assertRaises(ConfigError) as caught:
          Config.from_dict({key: value})
        self.assertIn(key, str(caught.exception))
    for categories in ("Added", ["Added", ""], ["Added", 3], ["A\nB"]):
      with self.subTest(categories=categories):
        with self.assertRaises(ConfigError):
          Config.from_dict({"changelog_categories": categories})
    for path in ("/abs/CHANGELOG.md", "../CHANGELOG.md"):
      with self.subTest(path=path):
        with self.assertRaises(ConfigError):
          Config.from_dict({"changelog_path": path})


class ChangelogModuleTests(unittest.TestCase):
  def test_ParseEntry_Valid_GivesCategoryAndText(self) -> None:
    entry = changelog.parse_entry("s01", "category: Added\n\nline one\nline two\n", CATEGORIES)
    self.assertEqual(entry, changelog.Entry("s01", "Added", "line one\nline two"))
    self.assertEqual(changelog.Entry.from_dict(entry.to_dict()), entry)

  def test_ParseEntry_Malformed_NamesFileAndLine(self) -> None:
    for text, line in (
      ("text\n", 1), ("category: Bogus\n\ntext\n", 1), ("category: Added\ntext\n", 2),
      ("category: Added\n\n", 3), ("", 1),
    ):
      with self.subTest(text=text):
        with self.assertRaises(changelog.ChangelogError) as caught:
          changelog.parse_entry("s01", text, CATEGORIES, where="landed-changed/s01.md")
        self.assertTrue(str(caught.exception).startswith(f"landed-changed/s01.md:{line}:"),
                        str(caught.exception))

  def test_ParseEntry_NoCategories_TakesWholeFileAsText(self) -> None:
    self.assertEqual(changelog.parse_entry("s01", "plain\n", []).text, "plain")
    with self.assertRaises(changelog.ChangelogError):
      changelog.parse_entry("s01", "category: Added\n\nplain\n", [])

  def test_IdHint_AllowsOneFullStopAfterTheParenthesis(self) -> None:
    for text, hint in (("did it (s01)", "s01"), ("did it (s01).", "s01"), ("did it (s01). ", "s01"),
                       ("did it (s01)..", ""), ("did it (s01). More", ""), ("did it (s01), too", "")):
      with self.subTest(text=text):
        self.assertEqual(changelog.id_hint(text), hint)

  def test_ImportedName_ItemIdWins(self) -> None:
    self.assertTrue(changelog.is_imported_name("entry-001"))
    self.assertFalse(changelog.is_imported_name("entry-001", {"entry-001"}))

  def test_FormatUnreleased_NoEntries_IsEmptyAndReplaceKeepsOneBlankLine(self) -> None:
    self.assertEqual(changelog.format_unreleased([], CATEGORIES), "")
    text = "## [Unreleased]\n\n- a\n\n## [1.0]\n"
    self.assertEqual(changelog.replace_unreleased(text, "## [Unreleased]", ""),
                     "## [Unreleased]\n\n## [1.0]\n")

  def test_FormatUnreleased_ImportedNamesComeAfterItemIds(self) -> None:
    entries = [
      changelog.Entry("entry-002", "Added", "b"), changelog.Entry("s02", "Added", "two"),
      changelog.Entry("entry-001", "Added", "a"), changelog.Entry("s10", "Added", "ten"),
    ]
    self.assertEqual(changelog.format_unreleased(entries, CATEGORIES),
                     "\n### Added\n\n- ten\n- two\n- a\n- b\n")

  def test_SplitUnreleased_Problems_CarryFileLines(self) -> None:
    body = "\n### Other\n\n- a\n\nprose\n"
    found, problems = changelog.split_unreleased(body, CATEGORIES, first_line=10)
    self.assertEqual(len(problems), 2, problems)
    self.assertTrue(problems[0].startswith("11:"), problems)
    self.assertTrue(problems[1].startswith("15:"), problems)


if __name__ == "__main__":
  unittest.main()
