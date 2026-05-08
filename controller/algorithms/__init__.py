"""
Load-balancing algorithms.

Each algorithm is a small class that decides, for every new flow, which
backend should handle it. The controller picks the algorithm at startup
based on the ``LB_ALGORITHM`` environment variable (or the
``DEFAULT_ALGORITHM`` constant in ``load_balancer.py``) and then calls
``select`` for every PacketIn that needs load balancing.

To add a new algorithm:

1. Create a new file in this package, e.g. ``least_connections.py``.
2. Implement a class that inherits from ``LoadBalancingAlgorithm``.
3. Register it in ``registry.py``.

The interface is intentionally tiny so that algorithms with very
different strategies (stateless round robin, connection-count tracking,
multi-metric scoring, machine-learning based, ...) all fit.
"""

from .base import LoadBalancingAlgorithm
from .registry import get_algorithm, available_algorithms

__all__ = [
    "LoadBalancingAlgorithm",
    "get_algorithm",
    "available_algorithms",
]
