"""Give dashboard readers compact, independently revisioned views of one state."""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from slicer import check, graph, jsonio, ops, relayrun, vcs
from slicer.store import State

PARTS = ("items", "goals", "runs", "status", "health")


def revision(value: object) -> str:
  return hashlib.sha256(jsonio.dumps(value).encode("utf-8")).hexdigest()[:12]


def build(state: State, parts: list[str], read: Callable[[str], object]) -> dict:
  """Reuse command readers bound to this loaded state, without printing their views."""
  def items():
    rows = []
    for row in read("list"):
      item = state.index.require(row["id"])
      rows.append({
        "id": item.id, "short_title": item.short_title, "status": item.status,
        "claim": item.claim_owner or None, "pass": item.pass_key,
        "size": item.size, "effort": item.effort, "score": item.score,
        "quadrant": item.quadrant, "held": bool(ops.held_flags(state, item)),
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
    return [{
      "id": run.id, "items": run.items, "worktree": run.worktree,
      "state": run.state, "updated_at": run.updated_at,
      "steps": [{k: getattr(step, k) for k in ("id", "role", "phase", "task", "model")}
                for step in run.steps],
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

  readers = {"items": items, "goals": goals, "runs": runs, "status": status, "health": health}
  result = {}
  for name in parts:
    value = readers[name]()
    result[name] = {"revision": revision(value), "value": value}
  return {
    "root": str(state.root.resolve()),
    "revision": revision({name: part["revision"] for name, part in result.items()}),
    "parts": result, "error": None,
  }
