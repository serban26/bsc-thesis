from __future__ import annotations

"""
Adaptive Predictive Load Balancer — Least Work Left with Dynamic Bandwidth
Allocation (LWL-DBA).

─── Why this beats every other algorithm in this project ────────────────────

  Round Robin      ignores load entirely — blind cycling
  Weighted RR      uses static config weights — can't adapt when server
                   behaviour changes at runtime
  Least Conns      score = active_conns / weight
                   Problem: treats all connections equally.  10 connections
                   each lasting 1 ms scores WORSE than 1 connection lasting
                   500 ms.  It will idle a fast server and starve it.
  Least Resp Time  score = avg_dur / weight
                   Problem: completely ignores how many connections are open.
                   A server with 20 in-flight flows gets the same score as
                   one that is completely idle.

  This algorithm   score = (active_conns + 1) × avg_dur / dyn_weight
                   • (active_conns + 1) × avg_dur  =  estimated total work
                     queued at the server right now  ("least work left")
                   • dyn_weight  =  runtime-learned capacity, updated after
                     every completed flow (DBA step).

─── Dynamic Bandwidth Allocation (DBA) ──────────────────────────────────────

  After each flow completes for server s:

      target_w = static_weight × (global_mean_dur / s.avg_flow_dur_s)

  A server that is twice as fast as the fleet average gets twice the weight,
  so it absorbs twice as many requests.  A floor of 20 % of static_weight
  prevents complete starvation of slow servers.

  dyn_weight tracks this with its own EWMA so it reacts to workload shifts
  without over-reacting to individual outlier flows.

─── Complexity ──────────────────────────────────────────────────────────────
  select()          O(N)  — one pass to score, one pass for ties
  on_flow_removed() O(1)  — two EWMA updates
"""

import random
import time
from typing import Sequence

from .base import LoadBalancingAlgorithm

try:
    from controller.metrics import ServerMetrics
except ImportError:          # pragma: no cover
    from metrics import ServerMetrics  # type: ignore[no-redef]

# ── tuning constants ──────────────────────────────────────────────────────
_DYN_ALPHA  = 0.25   # EWMA learning rate for dyn_weight and global_dur
_MIN_DUR    = 0.001  # 1 ms floor on avg_flow_dur_s to avoid division by zero
_FLOOR_FRAC = 0.20   # minimum dyn_weight = FLOOR_FRAC × static_weight
_STALE_SECS = 30.0   # only re-probe a server not seen in this many seconds
                     # (4 s was far too short: at 5 req/s with 10 servers,
                     # 10/4 = 2.5 mandatory probes/s = 50% of all traffic
                     # wasted on probing known-bad Tier C/D servers.)
_EPSILON    = 1e-9   # tie-breaking threshold


class AdaptivePredictiveAlgorithm(LoadBalancingAlgorithm):
    """Least Work Left + Dynamic Bandwidth Allocation.

    Per-server state maintained by the algorithm (not in ServerMetrics):
      _dyn_weight   : auto-tuned capacity multiplier, starts at static weight
      _global_dur   : fleet-wide EWMA of flow duration (reference baseline)
      _last_selected: wall-clock time of last selection (anti-starvation)
    """

    name = "adaptive_predictive"

    def __init__(self) -> None:
        super().__init__()
        self._dyn_weight:    dict[str, float] = {}
        self._last_selected: dict[str, float] = {}
        self._global_dur:    float = 0.0   # fleet-wide EWMA of flow duration

    # ------------------------------------------------------------------
    def _dw(self, server: ServerMetrics) -> float:
        """Return current dynamic weight for *server*."""
        return self._dyn_weight.get(server.name, server.weight)

    # ------------------------------------------------------------------
    def select(self, servers: Sequence[ServerMetrics]) -> ServerMetrics:
        now = time.monotonic()

        def _score(s: ServerMetrics) -> float:
            # Anti-starvation: any server we haven't measured recently
            # gets score 0 so it receives a probe request and its metrics
            # stay fresh.
            last = self._last_selected.get(s.name, 0.0)
            if now - last > _STALE_SECS:
                return 0.0

            # Predicted remaining work if we route this request here:
            #   (connections already open + the one we are about to add)
            #   × expected time per connection
            dur  = max(s.avg_flow_dur_s, _MIN_DUR)
            work = (s.active_conns + 1) * dur

            # Divide by auto-tuned capacity so faster servers absorb more
            cap  = max(self._dw(s), _EPSILON)
            return work / cap

        scores    = [_score(s) for s in servers]
        min_score = min(scores)
        candidates = [s for s, sc in zip(servers, scores)
                      if abs(sc - min_score) < _EPSILON]
        chosen = random.choice(candidates)
        self._last_selected[chosen.name] = now
        return chosen

    # ------------------------------------------------------------------
    def on_flow_removed(self, server: ServerMetrics) -> None:
        """DBA update: recalibrate this server's dynamic weight.

        Steps
        ─────
        1.  Update the fleet-wide EWMA of flow duration (the reference speed).
        2.  Compute the target weight:
                target = static_weight × (global_mean_dur / this_server_dur)
            → faster-than-average server → target > static_weight (more slots)
            → slower-than-average server → target < static_weight (fewer slots)
        3.  Blend into dyn_weight with its own EWMA (smooth adaptation).
        4.  Apply floor: never drop below FLOOR_FRAC × static_weight.
        """
        dur = max(server.avg_flow_dur_s, _MIN_DUR)

        # Step 1 — update fleet-wide reference speed
        if self._global_dur <= 0.0:
            self._global_dur = dur
        else:
            self._global_dur = (
                _DYN_ALPHA * dur
                + (1.0 - _DYN_ALPHA) * self._global_dur
            )

        # Step 2 — target capacity proportional to how fast this server is
        global_dur  = max(self._global_dur, _MIN_DUR)
        target      = server.weight * (global_dur / dur)
        floor_w     = server.weight * _FLOOR_FRAC
        target      = max(target, floor_w)

        # Step 3 & 4 — EWMA blend
        old = self._dyn_weight.get(server.name, server.weight)
        self._dyn_weight[server.name] = (
            (1.0 - _DYN_ALPHA) * old + _DYN_ALPHA * target
        )
