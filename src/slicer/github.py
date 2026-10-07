"""The only place slicer runs `gh`: to open one issue, or to list open ones.

`slicer feedback-report` copies local feedback entries to GitHub, and
`slicer issues-pull` reads open issues back as roadmap rows. `gh` is an
optional external binary, not a dependency: a missing or failing `gh` is code
`external`, never an internal error. Like `vcs.py`, the argv is checked against
two exact forms before anything runs, and nothing goes through a shell, so text
from the feedback log cannot become a flag or a command.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from slicer.errors import StateError

_PART = re.compile(r"^[A-Za-z0-9_.-]+$")

# `gh issue create --repo OWNER/REPO --title TITLE --body-file PATH`; None marks a value.
_CREATE = ("issue", "create", "--repo", None, "--title", None, "--body-file", None)
# `gh issue list --repo OWNER/REPO --state open --limit N --json number,title,url`.
# The body is never asked for: an issue body is untrusted text.
_LIST = ("issue", "list", "--repo", None, "--state", "open", "--limit", None,
         "--json", "number,title,url")


def valid_repo(value: str) -> bool:
  """Whether `value` is OWNER/REPO with no path tricks in either part."""
  parts = value.split("/")
  return len(parts) == 2 and ".." not in value and all(_PART.match(part) for part in parts)


def require_repo(value: str) -> str:
  if not valid_repo(value):
    raise StateError(
      f"{value!r} is not OWNER/REPO; use letters, digits, '.', '_' or '-' in each part",
      code="usage",
    )
  return value


def _matches(argv: tuple[str, ...], form: tuple[str | None, ...]) -> bool:
  return len(argv) == len(form) and all(want is None or arg == want for arg, want in zip(argv, form))


def _check(argv: tuple[str, ...]) -> None:
  # Each value must stay a value: a leading '-' could read as another flag.
  if _matches(argv, _CREATE):
    allowed = valid_repo(argv[3]) and not any(argv[i].startswith("-") for i in (5, 7))
  elif _matches(argv, _LIST):
    allowed = valid_repo(argv[3]) and argv[7].isascii() and argv[7].isdigit()
  else:
    allowed = False
  if not allowed:
    raise StateError(
      f"gh {' '.join(argv)!r} is not permitted; slicer only runs `gh issue create` and `gh issue list`"
    )


def _run(argv: tuple[str, ...]) -> subprocess.CompletedProcess[str]:
  _check(argv)
  try:
    return subprocess.run(
      ["gh", *argv], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
  except FileNotFoundError:
    raise StateError(
      "gh (the GitHub CLI) is not installed or not on PATH; install it from "
      "https://cli.github.com, run `gh auth login`, then run this again",
      code="external",
    ) from None


def create_issue(repo: str, title: str, body: str) -> tuple[int, str]:
  """Open one issue and return its number and URL. Every failure is `external`."""
  require_repo(repo)
  fd, path = tempfile.mkstemp(prefix="slicer-issue-", suffix=".md")
  try:
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
      fh.write(body)
    done = _run(("issue", "create", "--repo", repo, "--title", title, "--body-file", path))
  finally:
    Path(path).unlink(missing_ok=True)
  if done.returncode != 0:
    detail = (done.stderr or done.stdout).strip() or "no output"
    raise StateError(
      f"gh issue create exited {done.returncode}: {detail}; run `gh auth status` and check "
      f"that you can open issues on {repo}",
      code="external",
    )
  found = re.search(rf"https://github\.com/{re.escape(repo)}/issues/(\d+)", done.stdout, re.IGNORECASE)
  if found is None:
    raise StateError(
      f"gh issue create succeeded but printed no issue URL for {repo} "
      f"({done.stdout.strip()!r}); the issue may exist, so a later run may file a duplicate",
      code="external",
    )
  return int(found.group(1)), found.group(0)


def list_open_issues(repo: str, limit: int) -> list[dict[str, object]]:
  """Return up to `limit` open issues as `{number, title, url}`. Every failure is `external`.

  Only the shape is checked here; `ops.pull_issues` checks each value.
  """
  require_repo(repo)
  done = _run(("issue", "list", "--repo", repo, "--state", "open", "--limit", str(limit),
               "--json", "number,title,url"))
  if done.returncode != 0:
    detail = (done.stderr or done.stdout).strip() or "no output"
    raise StateError(
      f"gh issue list exited {done.returncode}: {detail}; run `gh auth status` and check "
      f"that you can read issues on {repo}",
      code="external",
    )
  try:
    data = json.loads(done.stdout)
  except json.JSONDecodeError as exc:
    raise StateError(f"gh issue list printed output that is not JSON ({exc}); nothing was filed",
      code="external") from None
  fields = ("number", "title", "url")
  if not isinstance(data, list) or not all(
    isinstance(issue, dict) and all(key in issue for key in fields) for issue in data
  ):
    raise StateError("gh issue list did not print a list of {number, title, url} objects; "
      "nothing was filed", code="external")
  return [{key: issue[key] for key in fields} for issue in data]
