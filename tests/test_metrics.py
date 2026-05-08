"""Pure-Python tests for ServerMetrics. Run with `pytest tests/`."""

import time

from controller.metrics import ServerMetrics


def make(name="h2", ip="10.0.0.2", port=2, weight=1.0):
    return ServerMetrics(name=name, ip=ip, mac="00:00:00:00:00:02",
                         port=port, weight=weight)


def test_initial_score_is_zero_ish():
    s = make()
    assert s.score() == 0.0
    snap = s.snapshot()
    assert snap["active_conns"] == 0
    assert snap["score"] == 0.0


def test_on_selected_increments_load():
    s = make()
    s.on_selected()
    s.on_selected()
    assert s.active_conns == 2
    assert s.selections_total == 2
    assert s.score() > 0


def test_weight_lowers_score():
    light = make(weight=1.0)
    heavy = make(weight=4.0)
    for _ in range(5):
        light.on_selected()
        heavy.on_selected()
    # both took the same load, but heavy has more capacity -> lower score
    assert heavy.score() < light.score()


def test_on_flow_removed_decrements_and_accumulates():
    s = make()
    s.on_selected()
    s.on_selected()
    s.on_flow_removed(byte_count=1500, packet_count=10, duration_s=0.5)
    assert s.active_conns == 1
    assert s.total_bytes == 1500
    assert s.total_packets == 10
    assert s.avg_flow_dur_s > 0


def test_port_errors_dominate_score():
    healthy = make(name="h2")
    sick = make(name="h3")
    sick.on_port_stats(0, 0, tx_errors=5, rx_errors=5,
                       tx_dropped=0, rx_dropped=0)
    assert sick.score() > healthy.score()


def test_throughput_metric_updates():
    s = make()
    # prime baseline
    s.on_port_stats(0, 0, 0, 0, 0, 0)
    time.sleep(0.05)
    s.on_port_stats(tx_bytes=125_000, rx_bytes=0,
                    tx_errors=0, rx_errors=0,
                    tx_dropped=0, rx_dropped=0)
    assert s.throughput_bps > 0


def test_least_loaded_is_picked_when_scoring():
    a = make(name="h2", ip="10.0.0.2", port=2)
    b = make(name="h3", ip="10.0.0.3", port=3)
    c = make(name="h4", ip="10.0.0.4", port=4, weight=2.0)
    for _ in range(3):
        a.on_selected()
    # b has no load, should win
    chosen = min([a, b, c], key=lambda s: (s.score(), s.selections_total))
    assert chosen.name == "h3"
