from __future__ import annotations

from typing import Dict, List, Type

from .base import LoadBalancingAlgorithm
from .round_robin import RoundRobinAlgorithm
from .weighted_round_robin import WeightedRoundRobinAlgorithm
from .least_connections import LeastConnectionsAlgorithm
from .least_response_time import LeastResponseTimeAlgorithm

_REGISTRY: Dict[str, Type[LoadBalancingAlgorithm]] = {
    RoundRobinAlgorithm.name: RoundRobinAlgorithm,
    WeightedRoundRobinAlgorithm.name: WeightedRoundRobinAlgorithm,
    LeastConnectionsAlgorithm.name: LeastConnectionsAlgorithm,
    LeastResponseTimeAlgorithm.name: LeastResponseTimeAlgorithm,
}


def available_algorithms() -> List[str]:
    return sorted(_REGISTRY.keys())


def get_algorithm(name: str) -> LoadBalancingAlgorithm:
    try:
        cls = _REGISTRY[name]
    except KeyError as exc:
        raise ValueError(
            f"unknown algorithm {name!r}; available: {available_algorithms()}"
        ) from exc
    return cls()

    return cls()
