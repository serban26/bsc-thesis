from __future__ import annotations

import random
import time
from typing import Sequence

from .base import LoadBalancingAlgorithm

try:
    from controller.metrics import ServerMetrics
except ImportError:  # pragma: no cover
    from metrics import ServerMetrics  # type: ignore[no-redef]

_STALE_SECS = 8.0   # re-probe a server we haven't used in this long
_EPSILON = 1e-6


class LeastResponseTimeAlgorithm(LoadBalancingAlgorithm):

    name = "least_response_time"

    def __init__(self) -> None:
        super().__init__()
        self._last_used: dict[str, float] = {}

    def select(self, servers: Sequence[ServerMetrics], traffic_class: str = "http") -> ServerMetrics:
        pool = list(servers)
        now = time.monotonic()

        def _score(s: ServerMetrics) -> float:
            last = self._last_used.get(s.name, 0.0)
            if now - last > _STALE_SECS:
                return 0.0  # probe it
            return s.avg_flow_dur_s / max(s.weight, 0.01)

        scores = [_score(s) for s in pool]
        min_score = min(scores)
        candidates = [s for s, sc in zip(pool, scores)
                      if abs(sc - min_score) < _EPSILON]
        chosen = random.choice(candidates)
        self._last_used[chosen.name] = now
        return chosen
