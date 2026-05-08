"""
Per-server metrics for the adaptive load balancer.

We track at least 6 metrics per backend server and combine them into a single
score. Lower score == better candidate.

Metrics
-------
1. active_conns   : currently installed (forward) flows for this server
2. total_bytes    : cumulative bytes served (from OFPFlowStats)
3. total_packets  : cumulative packets served (from OFPFlowStats)
4. throughput_bps : recent throughput in bits/s (delta on port stats)
5. conn_rate_eps  : EWMA of new connections per second
6. port_errors    : tx_errors + rx_errors + tx_dropped + rx_dropped on the
                    server's switch port (health indicator)
7. avg_flow_dur_s : EWMA of completed flow durations (proxy for service time)
8. weight         : static capacity hint (>=1, configurable)

The score is a weighted, normalized sum. Servers with weight=W behave as if
they had W times more capacity (load metrics are divided by weight).
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional


# Smoothing factor for EWMA (0 < alpha <= 1). Higher = more reactive.
_EWMA_ALPHA = 0.3


@dataclass
class RequestEntry:
    """Tracks a single in-flight (or queued) request assigned to a server."""
    cookie: int
    ts: float          # time.monotonic() when enqueued
    proto: int         # IP protocol number (6=TCP, 17=UDP, 1=ICMP)
    client_src_port: int = 0   # L4 source port (0 for ICMP)
    server_dst_port: int = 0   # L4 destination port (0 for ICMP)
    dispatched: bool = True    # False = held in queue, True = sent to server


@dataclass
class ServerMetrics:
    # --- identity -------------------------------------------------------
    name: str
    ip: str
    mac: str
    port: int               # switch port the server is reachable on
    weight: float = 1.0     # static capacity hint

    # --- live metrics ---------------------------------------------------
    active_conns: int = 0
    total_bytes: int = 0
    total_packets: int = 0
    throughput_bps: float = 0.0
    conn_rate_eps: float = 0.0
    port_errors: int = 0
    avg_flow_dur_s: float = 0.0

    # --- bookkeeping ----------------------------------------------------
    _last_port_bytes: int = 0
    _last_port_ts: float = field(default_factory=time.monotonic)
    _last_conn_ts: float = field(default_factory=time.monotonic)
    selections_total: int = 0  # how many times this server has been picked

    # --- per-server request queue ---------------------------------------
    # Tracks in-flight (dispatched) requests assigned to this server.
    # For round-robin every request is dispatched immediately so
    # queue_depth == active_conns at all times.  Future algorithms
    # (e.g. strict single-connection mode) may hold requests here as
    # pending (dispatched=False) until the server is free.
    _pending_queue: deque = field(default_factory=deque, repr=False)

    # ------------------------------------------------------------------
    # Queue helpers
    # ------------------------------------------------------------------
    def enqueue_request(self, entry: RequestEntry) -> None:
        """Add a request to this server's pending queue."""
        self._pending_queue.append(entry)

    def dequeue_request(self, cookie: int) -> Optional[RequestEntry]:
        """Remove and return the request matching *cookie*, or None."""
        for i, entry in enumerate(self._pending_queue):
            if entry.cookie == cookie:
                del self._pending_queue[i]
                return entry
        return None

    @property
    def queue_depth(self) -> int:
        """Number of requests currently in the queue (in-flight or pending)."""
        return len(self._pending_queue)

    # ------------------------------------------------------------------
    # Updates
    # ------------------------------------------------------------------
    def on_selected(self) -> None:
        """Called when controller picks this server for a new connection."""
        self.active_conns += 1
        self.selections_total += 1

        now = time.monotonic()
        dt = max(now - self._last_conn_ts, 1e-3)
        instant_rate = 1.0 / dt
        self.conn_rate_eps = (
            _EWMA_ALPHA * instant_rate
            + (1.0 - _EWMA_ALPHA) * self.conn_rate_eps
        )
        self._last_conn_ts = now

    def on_flow_removed(self, byte_count: int, packet_count: int,
                        duration_s: float) -> None:
        """Called when a forward flow for this server is removed by switch."""
        self.active_conns = max(0, self.active_conns - 1)
        self.total_bytes += byte_count
        self.total_packets += packet_count
        if duration_s > 0:
            self.avg_flow_dur_s = (
                _EWMA_ALPHA * duration_s
                + (1.0 - _EWMA_ALPHA) * self.avg_flow_dur_s
            )

    def on_port_stats(self, tx_bytes: int, rx_bytes: int,
                      tx_errors: int, rx_errors: int,
                      tx_dropped: int, rx_dropped: int) -> None:
        """Called periodically from OFPPortStatsReply for this server's port."""
        now = time.monotonic()
        dt = max(now - self._last_port_ts, 1e-3)
        total = tx_bytes + rx_bytes
        delta = max(0, total - self._last_port_bytes)
        self.throughput_bps = (delta * 8.0) / dt
        self._last_port_bytes = total
        self._last_port_ts = now
        self.port_errors = tx_errors + rx_errors + tx_dropped + rx_dropped

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------
    def score(self) -> float:
        """
        Adaptive composite score. Lower is better.

        Each component is normalized roughly to O(1) and divided by weight
        where it represents a "load" the server is currently handling.

        Metrics used
        ~~~~~~~~~~~~
        1. active_conns   – dominant load signal
        2. queue_depth    – pending + in-flight requests (future: may differ
                            from active_conns when strict serialisation is on)
        3. throughput_bps – recent traffic load
        4. conn_rate_eps  – EWMA of connection arrival rate (burst proxy)
        5. port_errors    – tx/rx errors + drops (health penalty)
        6. avg_flow_dur_s – EWMA of completed flow durations (service-time proxy)
        7. selections_total – lifetime fairness tie-breaker
        """
        w = max(self.weight, 1e-3)

        # 1. active connection load (dominant factor)
        c1 = 1.0 * self.active_conns / w
        # 2. queue depth (same as active_conns for round-robin; may differ
        #    for future serialising algorithms)
        c2 = 0.5 * self.queue_depth / w
        # 3. throughput load (per 10 Mb/s normalised)
        c3 = 0.3 * (self.throughput_bps / 10_000_000.0) / w
        # 4. recent connection arrival rate
        c4 = 0.2 * self.conn_rate_eps / w
        # 5. port error penalty (health)
        c5 = 2.0 * self.port_errors
        # 6. service-time proxy (slow servers get penalised)
        c6 = 0.2 * self.avg_flow_dur_s
        # 7. lifetime fairness — slight tie-break by total selections
        c7 = 0.01 * self.selections_total / w

        return c1 + c2 + c3 + c4 + c5 + c6 + c7

    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        return {
            "name": self.name,
            "ip": self.ip,
            "weight": self.weight,
            "active_conns": self.active_conns,
            "queue_depth": self.queue_depth,
            "total_bytes": self.total_bytes,
            "total_packets": self.total_packets,
            "throughput_bps": round(self.throughput_bps, 1),
            "conn_rate_eps": round(self.conn_rate_eps, 3),
            "port_errors": self.port_errors,
            "avg_flow_dur_s": round(self.avg_flow_dur_s, 3),
            "selections_total": self.selections_total,
            "score": round(self.score(), 4),
        }
