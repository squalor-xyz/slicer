"""Dependency edges between items: cycles, dangling refs, blocking.

Iterative depth-first search, not recursion: a project's roadmap is
user-supplied data and a pathological chain should report a cycle, not
raise RecursionError.
"""

from __future__ import annotations

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


def blocked_by(index: Index, item: Item, done_status: str) -> list[str]:
  """Dependencies of `item` that are not finished yet, in declared order."""
  out: list[str] = []
  for dep in item.depends_on:
    other = index.get(dep)
    if other is None or other.status != done_status:
      out.append(dep)
  return out
