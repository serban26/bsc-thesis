from __future__ import annotations

from typing import Sequence

from .base import LoadBalancingAlgorithm

try:
    from controller.metrics import ServerMetrics
except ImportError:  # pragma: no cover
    from metrics import ServerMetrics  # type: ignore[no-redef]

_MAX_SLOTS = 16


class WeightedRoundRobinAlgorithm(LoadBalancingAlgorithm):
    """Round robin where each server gets slots proportional to its weight."""

    name = "weighted_round_robin"

    def __init__(self) -> None:
        self._cursor: int = 0
        self._picks: int = 0

    def select(self, servers: Sequence[ServerMetrics], traffic_class: str = "http") -> ServerMetrics:
        pool = list(servers)
        n = len(pool)
        if self._cursor >= n:
            self._cursor, self._picks = 0, 0
        chosen = pool[self._cursor]
        # scale relative to smallest weight so the cycle stays short
        min_w = min(max(s.weight, 0.01) for s in pool)
        budget = max(1, min(_MAX_SLOTS, int(round(chosen.weight / min_w))))
        self._picks += 1
        if self._picks >= budget:
            self._cursor = (self._cursor + 1) % n
            self._picks = 0
        return chosen
