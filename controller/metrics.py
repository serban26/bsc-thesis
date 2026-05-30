# Keeps track of per-server stats: active connections, bytes transferred,
# average response time etc. Used by the load balancing algorithms.

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

_EWMA_ALPHA = 0.3  # EWMA smoothing factor


@dataclass
class RequestEntry:
    """One in-flight request."""
    cookie: int
    ts: float             # when the request was dispatched
    proto: int            # 6=TCP, 17=UDP
    client_src_port: int = 0
    server_dst_port: int = 0
    dispatched: bool = True
    client_ip: str = ""
    traffic_class: str = "http"


@dataclass
class ServerMetrics:
    # identity
    name: str
    ip: str
    mac: str
    port: int
    weight: float = 1.0

    # live metrics
    active_conns: int = 0
    total_bytes: int = 0
    total_packets: int = 0
    throughput_bps: float = 0.0
    conn_rate_eps: float = 0.0
    port_errors: int = 0
    avg_flow_dur_s: float = 0.0

    # bookkeeping
    _last_port_bytes: int = 0
    _last_port_ts: float = field(default_factory=time.monotonic)
    _last_conn_ts: float = field(default_factory=time.monotonic)
    selections_total: int = 0

    # queue of in-flight requests for this server
    _pending_queue: deque = field(default_factory=deque, repr=False)

    def enqueue_request(self, entry: RequestEntry) -> None:
        self._pending_queue.append(entry)

    def dequeue_request(self, cookie: int) -> Optional[RequestEntry]:
        for i, entry in enumerate(self._pending_queue):
            if entry.cookie == cookie:
                del self._pending_queue[i]
                return entry
        return None

    @property
    def queue_depth(self) -> int:
        return len(self._pending_queue)

    def on_selected(self) -> None:
        """Increment counters when this server gets a new connection."""
        self.active_conns += 1
        self.selections_total += 1

        now = time.monotonic()
        dt = max(now - self._last_conn_ts, 1e-3)
        self.conn_rate_eps = (
            _EWMA_ALPHA * (1.0 / dt)
            + (1.0 - _EWMA_ALPHA) * self.conn_rate_eps
        )
        self._last_conn_ts = now

    def on_flow_removed(self, byte_count: int, packet_count: int,
                        duration_s: float) -> None:
        """Update stats when a flow expires."""
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
        """Update throughput and error counts from the periodic port stats poll."""
        now = time.monotonic()
        dt = max(now - self._last_port_ts, 1e-3)
        total = tx_bytes + rx_bytes
        delta = max(0, total - self._last_port_bytes)
        self.throughput_bps = (delta * 8.0) / dt
        self._last_port_bytes = total
        self._last_port_ts = now
        self.port_errors = tx_errors + rx_errors + tx_dropped + rx_dropped

    def score(self) -> float:
        """Rough composite score for logging. Lower is better."""
        w = max(self.weight, 1e-3)
        c1 = 1.0  * self.active_conns / w
        c2 = 0.5  * self.queue_depth / w
        c3 = 0.3  * (self.throughput_bps / 10_000_000.0) / w
        c4 = 0.2  * self.conn_rate_eps / w
        c5 = 2.0  * self.port_errors          # health penalty
        c6 = 0.2  * self.avg_flow_dur_s
        c7 = 0.01 * self.selections_total / w  # slight fairness tie-break
        return c1 + c2 + c3 + c4 + c5 + c6 + c7

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
