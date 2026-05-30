from __future__ import annotations

# My adaptive predictive algorithm - the main contribution of this thesis.
# Basic idea: combine response time and active connections into one score,
# but also learn weights over time so fast servers get picked more often.

import random
import time
from typing import Sequence

from .base import LoadBalancingAlgorithm

try:
    from controller.metrics import ServerMetrics
    from controller.traffic import server_pool, TrafficClass
except ImportError:
    from metrics import ServerMetrics  # type: ignore[no-redef]
    from traffic import server_pool, TrafficClass  # type: ignore[no-redef]

_DYN_ALPHA_FAST = 0.60   # learn quickly at the start
_DYN_ALPHA_SLOW = 0.20   # slow down once we have enough data
_MIN_DUR        = 0.001  # avoid dividing by zero
_FLOOR_FRAC     = 0.05   # don't let the learned weight drop too low
_STALE_SECS     = 4.0    # re-probe a server if we haven't used it in 4s
_BOOTSTRAP_N    = 5      # need at least 5 flows before trusting a server's weight
_DYN_POOL_FRAC  = 0.08   # exclude servers whose learned weight is < 8% of the best
_EPSILON        = 1e-9

# VOIP and audio are more sensitive to latency so we penalize busy servers harder
_AC_MULT: dict[str, float] = {
    TrafficClass.VOIP:  3.0,
    TrafficClass.AUDIO: 2.0,
    TrafficClass.VIDEO: 1.5,
    TrafficClass.MAPS:  1.2,
    TrafficClass.HTTP:  1.0,
}


class AdaptivePredictiveAlgorithm(LoadBalancingAlgorithm):
    """
    Picks servers based on response time and active connections,
    but also learns which servers are fast over time.
    """

    name = "adaptive_predictive"

    def __init__(self) -> None:
        super().__init__()
        self._dyn_weight:    dict[str, float] = {}
        self._flow_count:    dict[str, int]   = {}
        self._last_selected: dict[str, float] = {}

    def _dw(self, server: ServerMetrics) -> float:
        # use static weight until we've seen enough flows
        return self._dyn_weight.get(server.name, server.weight)

    def select(self, servers: Sequence[ServerMetrics], traffic_class: str = "http") -> ServerMetrics:
        pool = server_pool(servers, traffic_class)
        now = time.monotonic()
        ac_mult = _AC_MULT.get(traffic_class, 1.0)

        # once we have data on enough servers, stop sending requests to the
        # ones we already know are slow
        learned = [s for s in pool
                   if self._flow_count.get(s.name, 0) >= _BOOTSTRAP_N]
        if len(learned) >= len(pool) // 2:
            max_dyn = max(self._dw(s) for s in learned)
            threshold = max_dyn * _DYN_POOL_FRAC
            filtered = [s for s in pool if self._dw(s) >= threshold]
            if filtered:
                pool = filtered

        def _score(s: ServerMetrics) -> float:
            dyn = max(self._dw(s), _EPSILON)
            ac  = max(s.active_conns, 0)
            actual = s.avg_flow_dur_s * (dyn + ac * ac_mult) / (dyn * dyn)

            count = self._flow_count.get(s.name, 0)
            if count < _BOOTSTRAP_N:
                # not measured yet - give it a probe occasionally so we get data
                last = self._last_selected.get(s.name, 0.0)
                if now - last > _STALE_SECS:
                    return 0.0
            return actual

        scores    = [_score(s) for s in pool]
        min_score = min(scores)
        candidates = [s for s, sc in zip(pool, scores)
                      if abs(sc - min_score) < _EPSILON]
        chosen = random.choice(candidates)
        self._last_selected[chosen.name] = now
        return chosen

    def on_flow_removed(self, server: ServerMetrics) -> None:
        dur  = max(server.avg_flow_dur_s, _MIN_DUR)
        name = server.name

        # faster servers get a higher target weight
        target = server.weight / dur
        target = max(target, server.weight * _FLOOR_FRAC)

        count = self._flow_count.get(name, 0) + 1
        self._flow_count[name] = count
        alpha = _DYN_ALPHA_FAST if count <= _BOOTSTRAP_N else _DYN_ALPHA_SLOW

        old = self._dyn_weight.get(name, server.weight)
        self._dyn_weight[name] = (1.0 - alpha) * old + alpha * target
