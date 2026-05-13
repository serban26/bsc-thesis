from __future__ import annotations

from typing import Sequence

from .base import LoadBalancingAlgorithm

try:
    from controller.metrics import ServerMetrics
except ImportError:  # pragma: no cover
    from metrics import ServerMetrics  # type: ignore[no-redef]


class RoundRobinAlgorithm(LoadBalancingAlgorithm):
    """Simple Round Robin.

    Cycles through backends one by one, ignoring weights.  Every backend
    receives exactly the same number of requests regardless of capacity.
    This is a *static* algorithm: no live metrics are read.
    """

    name = "round_robin"

    def __init__(self) -> None:
        self._cursor: int = 0

    def select(self, servers: Sequence[ServerMetrics]) -> ServerMetrics:
        chosen = servers[self._cursor % len(servers)]
        self._cursor += 1
        return chosen
