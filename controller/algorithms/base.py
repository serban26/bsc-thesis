from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

try:
    from controller.metrics import ServerMetrics
except ImportError:  # pragma: no cover
    from metrics import ServerMetrics  # type: ignore[no-redef]


class LoadBalancingAlgorithm(ABC):
    """All algorithms extend this."""

    name: str = "abstract"

    @abstractmethod
    def select(self, servers: Sequence[ServerMetrics], traffic_class: str = "http") -> ServerMetrics:
        """Choose a server for the next request."""

    def on_selected(self, server: ServerMetrics) -> None:
        """Hook called after a server is chosen. Override if the algorithm needs it."""

    def on_flow_removed(self, server: ServerMetrics) -> None:
        """Hook called when a flow expires. Override to update internal state."""

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r}>"
