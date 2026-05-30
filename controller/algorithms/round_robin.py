from __future__ import annotations

from typing import Sequence

from .base import LoadBalancingAlgorithm

try:
    from controller.metrics import ServerMetrics
    from controller.traffic import server_pool
except ImportError:  # pragma: no cover
    from metrics import ServerMetrics  # type: ignore[no-redef]
    from traffic import server_pool  # type: ignore[no-redef]


class RoundRobinAlgorithm(LoadBalancingAlgorithm):
    """Simple round robin. Each traffic class has its own cursor."""

    name = "round_robin"

    def __init__(self) -> None:
        self._cursors: dict[str, int] = {}

    def select(self, servers: Sequence[ServerMetrics], traffic_class: str = "http") -> ServerMetrics:
        pool = server_pool(servers, traffic_class)
        cursor = self._cursors.get(traffic_class, 0)
        chosen = pool[cursor % len(pool)]
        self._cursors[traffic_class] = (cursor + 1) % len(pool)
        return chosen
