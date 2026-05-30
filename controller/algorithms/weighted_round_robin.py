from __future__ import annotations

from typing import Sequence

from .base import LoadBalancingAlgorithm

try:
    from controller.metrics import ServerMetrics
    from controller.traffic import server_pool
except ImportError:  # pragma: no cover
    from metrics import ServerMetrics  # type: ignore[no-redef]
    from traffic import server_pool  # type: ignore[no-redef]

_MAX_SLOTS = 8


class WeightedRoundRobinAlgorithm(LoadBalancingAlgorithm):
    """Like round robin but heavier servers get picked more often."""

    name = "weighted_round_robin"

    def __init__(self) -> None:
        # [cursor, picks_in_current_round] keyed by traffic class
        self._state: dict[str, list] = {}

    def select(self, servers: Sequence[ServerMetrics], traffic_class: str = "http") -> ServerMetrics:
        pool = server_pool(servers, traffic_class)
        n = len(pool)
        state = self._state.setdefault(traffic_class, [0, 0])
        cursor, picks = state
        if cursor >= n:
            cursor, picks = 0, 0
        chosen = pool[cursor]
        # scale relative to smallest weight so the cycle stays short
        min_w = min(max(s.weight, 0.01) for s in pool)
        budget = max(1, min(_MAX_SLOTS, int(round(chosen.weight / min_w))))
        picks += 1
        if picks >= budget:
            cursor = (cursor + 1) % n
            picks = 0
        state[0], state[1] = cursor, picks
        return chosen
