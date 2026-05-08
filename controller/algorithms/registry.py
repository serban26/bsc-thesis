from __future__ import annotations

from typing import Dict, List, Type

from .base import LoadBalancingAlgorithm
from .round_robin import RoundRobinAlgorithm
from .least_connections import LeastConnectionsAlgorithm

_REGISTRY: Dict[str, Type[LoadBalancingAlgorithm]] = {
    RoundRobinAlgorithm.name: RoundRobinAlgorithm,
    LeastConnectionsAlgorithm.name: LeastConnectionsAlgorithm,
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
