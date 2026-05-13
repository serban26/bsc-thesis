from __future__ import annotations

import random
import time
from typing import Sequence

from .base import LoadBalancingAlgorithm

try:
    from controller.metrics import ServerMetrics
except ImportError:  # pragma: no cover
    from metrics import ServerMetrics  # type: ignore[no-redef]

# If a server hasn't been selected in this many seconds, its score is
# reset to 0 so it gets re-probed. This prevents starvation: once the
# fastest server dominates, slower servers would never get new flows
# and their measured latency would never update.
_STALE_SECS = 8.0

# Tie-breaking threshold
_EPSILON = 1e-6


class LeastResponseTimeAlgorithm(LoadBalancingAlgorithm):
    """Least Response Time (LRT).

    Selects the backend with the lowest weighted response-time score:

        score_i = avg_flow_dur_s_i / max(weight_i, 0.01)

    ``avg_flow_dur_s`` is an EWMA of completed flow durations updated on
    every OFPFlowRemoved event, making it a real-time proxy for server
    service time.  A slower server accumulates a higher score and receives
    progressively fewer new connections.

    Starvation prevention: any server that has not been selected in the
    last ``_STALE_SECS`` seconds has its score treated as 0 (unknown),
    forcing a periodic probe so that all servers' metrics stay fresh.
    """

    name = "least_response_time"

    def __init__(self) -> None:
        super().__init__()
        # wall-clock time of the last selection for each server name
        self._last_used: dict[str, float] = {}

    def select(self, servers: Sequence[ServerMetrics]) -> ServerMetrics:
        now = time.monotonic()

        def _score(s: ServerMetrics) -> float:
            # If never selected or not selected recently → treat as unknown (0)
            last = self._last_used.get(s.name, 0.0)
            if now - last > _STALE_SECS:
                return 0.0
            return s.avg_flow_dur_s / max(s.weight, 0.01)

        min_score = min(_score(s) for s in servers)
        candidates = [s for s in servers
                      if abs(_score(s) - min_score) < _EPSILON]
        chosen = random.choice(candidates)
        self._last_used[chosen.name] = now
        return chosen
