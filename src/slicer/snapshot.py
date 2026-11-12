"""Give dashboard readers compact, independently revisioned views of one state."""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from slicer import check, graph, jsonio, queries, relayrun, vcs, vocab
from slicer.store import State

PARTS = vocab.PARTS


def revision(value: object) -> str:
  return hashlib.sha256(jsonio.dumps(value).encode("utf-8")).hexdigest()[:12]


def held_revisions(reply: dict) -> dict[str, dict[str, str]]:
  """Project root to part name to revision, from a snapshot reply the caller holds.

  A project with an error, and any entry that is not shaped like a reply's, holds nothing.
  """
  held: dict[str, dict[str, str]] = {}
  for project in reply["projects"]:
    if not isinstance(project, dict) or project.get("error") is not None:
      continue
    root, parts = project.get("root"), project.get("parts")
    if not isinstance(root, str) or not isinstance(parts, dict):
      continue
    held[root] = {name: part["revision"] for name, part in parts.items()
                  if isinstance(part, dict) and isinstance(part.get("revision"), str)}
  return held


def build(state: State, parts: list[str], read: Callable[[str], object],
          since: dict[str, str] | None = None) -> dict:
  """Reuse command readers bound to this loaded state, without printing their views.

  `since` maps part name to the revision the caller holds for this project; a part whose
  revision still matches is answered as `unchanged`, with no value.
  """
  def items():
    rows = []
    for row in read("list"):
      item = state.index.require(row["id"])
      rows.append({
        "id": item.id, "short_title": item.short_title, "status": item.status,
        "claim": item.claim_owner or None, "pass": item.pass_key,
        "release_target": item.release_target, "wave": item.wave,
        "size": item.size, "effort": item.effort, "score": item.score,
        "quadrant": item.quadrant, "held": bool(queries.held_flags(state, item)),
        "blocked": bool(graph.pending(state.index, item, state.config)),
      })
    return rows

  def goals():
    value = read("goals")
    return {
      "goals": value["goals"], "non_goals": value["non_goals"],
      "records": [{k: record[k] for k in ("id", "kind", "title")}
                  for records in value["records"].values() for record in records],
    }

  def runs():
    # The step's own `to_dict` values, so `question` is the object `relay-run list --json` gives.
    keys = ("id", "role", "phase", "task", "model", "waiting_on", "question")

    def step_view(step: relayrun.Step) -> dict:
      dumped = step.to_dict()  # once per step, not once per key
      return {k: dumped[k] for k in keys}

    return [{
      "id": run.id, "items": run.items, "worktree": run.worktree,
      "state": run.state, "updated_at": run.updated_at,
      "steps": [step_view(step) for step in run.steps],
    } for run in relayrun.list_runs(state.root, active=True)]

  def status():
    value = read("status")
    return {
      "next": value["next"]["id"] if value["next"] else None,
      "blocked": [entry["id"] for entry in value["blocked"]],
      "held": [entry["id"] for entry in value.get("held", [])],
    }

  def health():
    report, _, _ = check.run(state)
    return {"render_stale": bool(report.stale_render or report.orphan_render),
            "merge_in_progress": vcs.merge_in_progress(state.root)}

  def deps():
    return read("deps")  # what `slicer deps --json` prints for the project

  readers = {"items": items, "goals": goals, "runs": runs, "status": status, "health": health,
             "deps": deps}
  held = since or {}
  result = {}
  for name in parts:
    value = readers[name]()
    rev = revision(value)
    # The value is built either way, since a revision hashes it; only the reply drops it.
    result[name] = ({"revision": rev, "unchanged": True} if held.get(name) == rev
                    else {"revision": rev, "value": value})
  return {
    "root": str(state.root.resolve()),
    "revision": revision({name: part["revision"] for name, part in result.items()}),
    "parts": result, "error": None,
  }
