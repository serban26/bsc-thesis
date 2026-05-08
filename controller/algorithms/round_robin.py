from __future__ import annotations

from typing import Sequence

from .base import LoadBalancingAlgorithm

try:
    from controller.metrics import ServerMetrics
except ImportError:  # pragma: no cover
    from metrics import ServerMetrics  # type: ignore[no-redef]


class RoundRobinAlgorithm(LoadBalancingAlgorithm):
    """Weighted round robin.  Each backend gets weight N picks before moving on."""

    name = "round_robin"

    def __init__(self) -> None:
        self._cursor: int = 0
        self._picks_in_round: int = 0

    def select(self, servers: Sequence[ServerMetrics]) -> ServerMetrics:
        n = len(servers)
        if self._cursor >= n:
            self._cursor = 0
            self._picks_in_round = 0

        chosen = servers[self._cursor]
        budget = max(1, int(round(chosen.weight)))

        self._picks_in_round += 1
        if self._picks_in_round >= budget:
            self._cursor = (self._cursor + 1) % n
            self._picks_in_round = 0

        return chosen
