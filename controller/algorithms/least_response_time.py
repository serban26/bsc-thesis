from __future__ import annotations

import random
import time
from typing import Sequence

from .base import LoadBalancingAlgorithm

try:
    from controller.metrics import ServerMetrics
    from controller.traffic import server_pool
except ImportError:  # pragma: no cover
    from metrics import ServerMetrics  # type: ignore[no-redef]
    from traffic import server_pool  # type: ignore[no-redef]

# if we haven't used a server in this long, give it a probe request
_STALE_SECS = 8.0

_EPSILON = 1e-6


class LeastResponseTimeAlgorithm(LoadBalancingAlgorithm):
    """Sends requests to the server with the lowest average response time."""

    name = "least_response_time"

    def __init__(self) -> None:
        super().__init__()
        self._last_used: dict[str, float] = {}

    def select(self, servers: Sequence[ServerMetrics], traffic_class: str = "http") -> ServerMetrics:
        pool = server_pool(servers, traffic_class)
        now = time.monotonic()

        def _score(s: ServerMetrics) -> float:
            last = self._last_used.get(s.name, 0.0)
            if now - last > _STALE_SECS:
                return 0.0  # hasn't been used in a while, probe it
            return s.avg_flow_dur_s / max(s.weight, 0.01)

        min_score = min(_score(s) for s in pool)
        candidates = [s for s in pool
                      if abs(_score(s) - min_score) < _EPSILON]
        chosen = random.choice(candidates)
        self._last_used[chosen.name] = now
        return chosen
