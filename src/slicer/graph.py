"""Dependency edges between items: cycles, dangling refs, blocking.

Iterative depth-first search, not recursion: a project's roadmap is
user-supplied data and a pathological chain should report a cycle, not
raise RecursionError.
"""

from __future__ import annotations

from typing import Collection

from slicer.config import Config
from slicer.model import Index, Item


def dangling(index: Index) -> list[tuple[str, str]]:
  """(item, missing_dependency) pairs, in item order."""
  known = {it.id for it in index.items}
  return [
    (it.id, dep) for it in index.items for dep in it.depends_on if dep not in known
  ]


def cycles(index: Index) -> list[list[str]]:
  """Every dependency cycle, each as the path that closes it."""
  edges = {it.id: [d for d in it.depends_on if index.get(d) is not None] for it in index.items}
  found: list[list[str]] = []
  seen: set[str] = set()
  for start in edges:
    if start in seen:
      continue
    stack: list[tuple[str, int]] = [(start, 0)]
    path: list[str] = [start]
    on_path = {start}
    while stack:
      node, i = stack[-1]
      if i < len(edges.get(node, [])):
        stack[-1] = (node, i + 1)
        nxt = edges[node][i]
        if nxt in on_path:
          cycle = path[path.index(nxt):] + [nxt]
          if cycle not in found:
            found.append(cycle)
        elif nxt not in seen:
          stack.append((nxt, 0))
          path.append(nxt)
          on_path.add(nxt)
      else:
        stack.pop()
        seen.add(node)
        on_path.discard(path.pop())
  return found


def path(index: Index, start: str, goal: str) -> list[str] | None:
  """The dependency path from `start` to `goal`, both ends included, or None.

  Breadth-first over known edges, so the path is a shortest one. A new edge
  X -> Y closes a cycle exactly when this finds a path from Y back to X.
  """
  edges = {it.id: [d for d in it.depends_on if index.get(d) is not None] for it in index.items}
  parent: dict[str, str | None] = {start: None}
  queue = [start]
  while queue:
    node = queue.pop(0)
    if node == goal:
      out = [node]
      while parent[out[-1]] is not None:
        out.append(parent[out[-1]])
      return out[::-1]
    for nxt in edges.get(node, []):
      if nxt not in parent:
        parent[nxt] = node
        queue.append(nxt)
  return None


def blocked_by(index: Index, item: Item, satisfying: str | Collection[str]) -> list[str]:
  """Dependencies of `item` that are not satisfied yet, in declared order.

  `satisfying` is `Config.satisfying_statuses()`; one status key also works.
  A dependency that is not in the index is never satisfied.
  """
  if isinstance(satisfying, str):
    satisfying = {satisfying}
  out: list[str] = []
  for dep in item.depends_on:
    other = index.get(dep)
    if other is None or other.status not in satisfying:
      out.append(dep)
  return out


def dependents(index: Index) -> dict[str, list[str]]:
  """For each id, the ids that declare it as a dependency.

  The reverse of `depends_on`: if X depends on Y, then X is a dependent of Y.
  """
  rev: dict[str, list[str]] = {it.id: [] for it in index.items}
  for it in index.items:
    for dep in it.depends_on:
      if dep in rev:
        rev[dep].append(it.id)
  return rev


def mermaid(index: Index, focus: str | None = None) -> str:
  """A `graph TD` diagram of the dependency edges, dependent -> dependency.

  With `focus`, only edges touching that id (its neighbourhood) are drawn; a
  node's label is its id and display title, sanitised for mermaid.
  """
  edges = [(it.id, dep) for it in index.items for dep in it.depends_on if index.get(dep)]
  if focus is not None:
    edges = [(a, b) for a, b in edges if focus in (a, b)]
  if not edges:
    return "graph TD\n  %% no dependencies"
  nodes = {end for edge in edges for end in edge}
  lines = ["graph TD"]
  for it in index.items:
    if it.id in nodes:
      label = it.display_title().replace('"', "'").replace("\n", " ")
      lines.append(f'  {it.id}["{it.id} {label}"]')
  lines += [f"  {a} --> {b}" for a, b in edges]
  return "\n".join(lines)


def effective_scores(index: Index) -> dict[str, int]:
  """Each item's priority once it inherits from what depends on it.

  A blocker of a critical item is itself critical: you cannot start the
  critical work until the blocker is done. So a score propagates from a
  dependent to its dependency -- if X depends on Y, Y's effective score is at
  least X's -- transitively, up the whole chain.

  Computed by relaxing the edges to a fixed point rather than recursing, so a
  long chain cannot raise RecursionError and a cycle simply equalises to the
  cycle's maximum instead of looping forever.
  """
  eff = {it.id: it.score for it in index.items}
  # (dependent, dependency) edges; a dependency's score is pushed up to at
  # least its dependent's.
  edges = [(it.id, dep) for it in index.items for dep in it.depends_on if dep in eff]
  changed = True
  while changed:
    changed = False
    for dependent, dependency in edges:
      if eff[dependent] > eff[dependency]:
        eff[dependency] = eff[dependent]
        changed = True
  return eff


def ranked_order(
  index: Index, cfg: Config, items: list[Item], *, descending: bool = True,
  by_pass: bool = False,
) -> list[Item]:
  """Group by readiness without losing score inheritance or manual ties.

  Unblocked in-work items precede unblocked open items, then other visible
  rows, with parked items last. Each group uses descending effective score.
  With by_pass, declared pass order precedes score within each group;
  empty and undeclared keys share a final fallback rank.
  Ascending reverses groups, passes and scores. Ties keep stored queue order.
  """
  eff = effective_scores(index)
  in_work = cfg.in_work()
  satisfying = cfg.satisfying_statuses()
  parked = cfg.parked_status
  place = {it.id: n for n, it in enumerate(index.items)}
  passes = {p.key: n for n, p in enumerate(index.passes)} if by_pass else {}

  def tier(item: Item) -> int:
    if parked and item.status == parked:
      return 3
    if blocked_by(index, item, satisfying):
      return 2
    if item.status in in_work:
      return 0
    if item.status == cfg.open_status:
      return 1
    return 2

  def key(item: Item) -> tuple[int, int, int, int]:
    group = tier(item)
    pass_rank = passes.get(item.pass_key, len(passes)) if item.pass_key else len(passes)
    score = eff[item.id]
    if descending:
      return (group, pass_rank, -score, place[item.id])
    return (-group, -pass_rank, score, place[item.id])

  return sorted(items, key=key)
