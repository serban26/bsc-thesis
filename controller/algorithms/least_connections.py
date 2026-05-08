from __future__ import annotations

import random
from typing import Sequence

from .base import LoadBalancingAlgorithm

try:
    from controller.metrics import ServerMetrics
except ImportError:
    from metrics import ServerMetrics  # type: ignore[no-redef]


class LeastConnectionsAlgorithm(LoadBalancingAlgorithm):
    """Pick the backend with the fewest active connections.

    Ties are broken randomly so no single server gets permanently
    favoured when the load is perfectly balanced.  Weight is respected
    by dividing active_conns by the server weight before comparing,
    which gives higher-capacity backends a proportionally larger share.
    """

    name = "least_connections"

    def select(self, servers: Sequence[ServerMetrics]) -> ServerMetrics:
        # weighted load: active_conns / weight  (lower = less loaded)
        min_load = min(s.active_conns / max(s.weight, 0.01) for s in servers)
        candidates = [s for s in servers
                      if s.active_conns / max(s.weight, 0.01) == min_load]
        return random.choice(candidates)
