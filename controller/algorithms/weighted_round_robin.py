from __future__ import annotations

from typing import Sequence

from .base import LoadBalancingAlgorithm

try:
    from controller.metrics import ServerMetrics
except ImportError:  # pragma: no cover
    from metrics import ServerMetrics  # type: ignore[no-redef]


class WeightedRoundRobinAlgorithm(LoadBalancingAlgorithm):
    """Weighted Round Robin.

    Each backend is served in proportion to its weight.  A server with
    weight=3 receives three consecutive selections before the cursor moves on,
    giving it 3x the traffic of a weight=1 server.

    This is a *static* algorithm: it only reads the weight attribute, never
    any live metric, so its decisions are predetermined by configuration.
    """

    name = "weighted_round_robin"

    def __init__(self) -> None:
        self._cursor: int = 0
        self._picks_in_round: int = 0

    def select(self, servers: Sequence[ServerMetrics]) -> ServerMetrics:
        n = len(servers)
        if self._cursor >= n:
            self._cursor = 0
            self._picks_in_round = 0

        chosen = servers[self._cursor]

        # Normalize weights so the slot counts are *relative*, not absolute.
        # Without normalization, weights like 20,19,...,1 mean h2 gets 20
        # consecutive picks before the cursor moves — a full cycle takes
        # sum(1..20)=210 requests, so with 50 requests you'd only ever see h2/h3.
        # Instead, divide every weight by the minimum weight so the lightest
        # server always gets 1 slot and heavy servers get proportionally more,
        # capped at MAX_SLOTS to keep cycles short.
        MAX_SLOTS = 8
        min_w = min(max(s.weight, 0.01) for s in servers)
        budget = max(1, min(MAX_SLOTS, int(round(chosen.weight / min_w))))

        self._picks_in_round += 1
        if self._picks_in_round >= budget:
            self._cursor = (self._cursor + 1) % n
            self._picks_in_round = 0

        return chosen
