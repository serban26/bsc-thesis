"""Unit tests for the round-robin algorithm."""

from controller.algorithms import get_algorithm, available_algorithms
from controller.algorithms.round_robin import RoundRobinAlgorithm
from controller.metrics import ServerMetrics


def make_servers(weights=(1.0, 1.0, 1.0)):
    return [
        ServerMetrics(name=f"h{i+2}", ip=f"10.0.0.{i+2}",
                      mac=f"00:00:00:00:00:0{i+2}", port=i + 2, weight=w)
        for i, w in enumerate(weights)
    ]


def test_registry_lists_round_robin():
    assert "round_robin" in available_algorithms()


def test_get_algorithm_returns_instance():
    algo = get_algorithm("round_robin")
    assert isinstance(algo, RoundRobinAlgorithm)


def test_get_algorithm_unknown_raises():
    import pytest
    with pytest.raises(ValueError):
        get_algorithm("does_not_exist")


def test_round_robin_cycles_in_order():
    servers = make_servers()
    algo = RoundRobinAlgorithm()
    picks = [algo.select(servers).name for _ in range(6)]
    assert picks == ["h2", "h3", "h4", "h2", "h3", "h4"]


def test_round_robin_respects_weight():
    # h4 has weight 2 -> it should be picked twice in a row.
    servers = make_servers(weights=(1.0, 1.0, 2.0))
    algo = RoundRobinAlgorithm()
    picks = [algo.select(servers).name for _ in range(8)]
    # one full round = h2, h3, h4, h4 (= 4 picks)
    assert picks == ["h2", "h3", "h4", "h4", "h2", "h3", "h4", "h4"]


def test_round_robin_share_under_load():
    # Over a long run h4 (weight 2) should get ~2x the share of h2/h3.
    servers = make_servers(weights=(1.0, 1.0, 2.0))
    algo = RoundRobinAlgorithm()
    counts = {"h2": 0, "h3": 0, "h4": 0}
    for _ in range(400):
        counts[algo.select(servers).name] += 1
    assert counts["h4"] == 200
    assert counts["h2"] == counts["h3"] == 100


def test_round_robin_handles_weight_below_one():
    # weight < 1 should be treated as 1 (no division-by-zero, no skip).
    servers = make_servers(weights=(0.4, 1.0, 1.0))
    algo = RoundRobinAlgorithm()
    picks = [algo.select(servers).name for _ in range(6)]
    assert picks == ["h2", "h3", "h4", "h2", "h3", "h4"]
