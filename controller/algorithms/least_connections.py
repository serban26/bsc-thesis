from __future__ import annotations

import random
from typing import Sequence

from .base import LoadBalancingAlgorithm

try:
    from controller.metrics import ServerMetrics
    from controller.traffic import server_pool
except ImportError:
    from metrics import ServerMetrics  # type: ignore[no-redef]
    from traffic import server_pool  # type: ignore[no-redef]


class LeastConnectionsAlgorithm(LoadBalancingAlgorithm):
    """Picks the server with the fewest active connections."""

    name = "least_connections"

    def select(self, servers: Sequence[ServerMetrics], traffic_class: str = "http") -> ServerMetrics:
        pool = server_pool(servers, traffic_class)
        # normalize by weight so higher-capacity servers get proportionally more
        min_load = min(s.active_conns / max(s.weight, 0.01) for s in pool)
        candidates = [s for s in pool
                      if s.active_conns / max(s.weight, 0.01) == min_load]
        return random.choice(candidates)
