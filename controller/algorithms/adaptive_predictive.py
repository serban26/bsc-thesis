from __future__ import annotations

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
_DYN_ALPHA_SLOW = 0.20   # stable rate once we have enough data
_MIN_DUR        = 0.001
_FLOOR_FRAC     = 0.05   # dyn_weight floor: 5% of static weight
_STALE_SECS     = 4.0    # re-probe a server we haven't touched in 4s
_BOOTSTRAP_N    = 5      # flows needed before we trust a server's weight
_DYN_POOL_FRAC  = 0.08   # drop servers below 8% of best known weight
_EPSILON        = 1e-9

# latency-sensitive classes get a heavier penalty for busy servers
_AC_MULT: dict[str, float] = {
    TrafficClass.VOIP:  3.0,
    TrafficClass.AUDIO: 2.0,
    TrafficClass.VIDEO: 1.5,
    TrafficClass.MAPS:  1.2,
    TrafficClass.HTTP:  1.0,
}


class AdaptivePredictiveAlgorithm(LoadBalancingAlgorithm):

    name = "adaptive_predictive"

    def __init__(self) -> None:
        super().__init__()
        self._dyn_weight:    dict[str, float] = {}
        self._flow_count:    dict[str, int]   = {}
        self._last_selected: dict[str, float] = {}

    def _dw(self, server: ServerMetrics) -> float:
        # fall back to static weight until we have real data
        return self._dyn_weight.get(server.name, server.weight)

    def select(self, servers: Sequence[ServerMetrics], traffic_class: str = "http") -> ServerMetrics:
        pool = server_pool(servers, traffic_class)
        now = time.monotonic()
        ac_mult = _AC_MULT.get(traffic_class, 1.0)

        # once enough servers are profiled, cut out the known slow ones
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
                last = self._last_selected.get(s.name, 0.0)
                if now - last > _STALE_SECS:
                    return 0.0  # force a probe
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

        target = server.weight / dur
        target = max(target, server.weight * _FLOOR_FRAC)

        count = self._flow_count.get(name, 0) + 1
        self._flow_count[name] = count
        alpha = _DYN_ALPHA_FAST if count <= _BOOTSTRAP_N else _DYN_ALPHA_SLOW

        old = self._dyn_weight.get(name, server.weight)
        self._dyn_weight[name] = (1.0 - alpha) * old + alpha * target
