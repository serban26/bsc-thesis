from __future__ import annotations

from typing import Sequence

from .base import LoadBalancingAlgorithm

try:
    from controller.metrics import ServerMetrics
except ImportError:  # pragma: no cover
    from metrics import ServerMetrics  # type: ignore[no-redef]


class RoundRobinAlgorithm(LoadBalancingAlgorithm):
    """Simple round robin - cycles through all servers equally regardless of traffic class."""

    name = "round_robin"

    def __init__(self) -> None:
        self._cursor: int = 0

    def select(self, servers: Sequence[ServerMetrics], traffic_class: str = "http") -> ServerMetrics:
        pool = list(servers)
        chosen = pool[self._cursor % len(pool)]
        self._cursor = (self._cursor + 1) % len(pool)
        return chosen
