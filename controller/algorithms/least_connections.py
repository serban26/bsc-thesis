from __future__ import annotations

import random
from typing import Sequence

from .base import LoadBalancingAlgorithm

try:
    from controller.metrics import ServerMetrics
except ImportError:
    from metrics import ServerMetrics  # type: ignore[no-redef]

_EPSILON = 1e-9


class LeastConnectionsAlgorithm(LoadBalancingAlgorithm):

    name = "least_connections"

    def select(self, servers: Sequence[ServerMetrics], traffic_class: str = "http") -> ServerMetrics:
        pool = list(servers)
        # normalise by weight so higher-capacity servers absorb more load
        min_load = min(s.active_conns / max(s.weight, 0.01) for s in pool)
        candidates = [s for s in pool
                      if abs(s.active_conns / max(s.weight, 0.01) - min_load) < _EPSILON]
        return random.choice(candidates)
