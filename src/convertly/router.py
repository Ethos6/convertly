"""Find the cheapest chain of converters between two formats (Dijkstra)."""

from __future__ import annotations

import heapq
import itertools

from .formats import normalize
from .registry import Converter, Registry, default_registry

MAX_HOPS = 3
HOP_PENALTY = 0.25  # each extra step costs something (time, temp files, fidelity)


class NoRoute(Exception):
    def __init__(self, src: str, dst: str) -> None:
        super().__init__(f"no way to convert {src} to {dst}")
        self.src, self.dst = src, dst


def find_route(src: str, dst: str, registry: Registry | None = None, *, max_hops: int = MAX_HOPS) -> list[Converter]:
    """Return the list of converter steps from ``src`` to ``dst``.

    Raises :class:`NoRoute` when nothing connects them. Edges marked ``terminal``
    are only used as the final step.
    """
    reg = registry or default_registry()
    src, dst = normalize(src), normalize(dst)
    counter = itertools.count()
    # (cost, tiebreak, fmt, path)
    heap: list[tuple[float, int, str, list[Converter]]] = [(0.0, next(counter), src, [])]
    best: dict[tuple[str, int], float] = {}
    while heap:
        cost, _, fmt, path = heapq.heappop(heap)
        if path and fmt == dst:
            return path
        if len(path) >= max_hops:
            continue
        if path and path[-1].terminal:
            continue
        for edge in reg.edges_from(fmt):
            if edge.terminal and edge.dst != dst:
                continue
            if edge.direct and path:
                continue
            if any(step.src == edge.dst for step in path) and edge.dst != dst:
                continue  # no cycles
            new_cost = cost + edge.cost + (HOP_PENALTY if path else 0.0)
            key = (edge.dst, len(path) + 1)
            if best.get(key, float("inf")) <= new_cost:
                continue
            best[key] = new_cost
            heapq.heappush(heap, (new_cost, next(counter), edge.dst, [*path, edge]))
    raise NoRoute(src, dst)


def targets(src: str, registry: Registry | None = None, *, max_hops: int = MAX_HOPS) -> set[str]:
    """Every format reachable from ``src``."""
    reg = registry or default_registry()
    src = normalize(src)
    seen: set[str] = set()
    frontier = [(src, 0, False)]
    visited: set[tuple[str, int, bool]] = set()
    while frontier:
        fmt, depth, terminal = frontier.pop()
        if terminal or depth >= max_hops:
            continue
        for edge in reg.edges_from(fmt):
            if edge.direct and depth:
                continue
            seen.add(edge.dst)
            key = (edge.dst, depth + 1, edge.terminal)
            if key not in visited:
                visited.add(key)
                frontier.append((edge.dst, depth + 1, edge.terminal))
    if not any(e.dst == src for e in reg.edges_from(src)):
        seen.discard(src)
    return seen


def common_targets(formats: list[str], registry: Registry | None = None) -> list[str]:
    """Targets every given source format can reach (what the UI offers for a mixed drop)."""
    sets = [targets(f, registry) for f in formats]
    if not sets:
        return []
    out = set.intersection(*sets)
    return sorted(out)
