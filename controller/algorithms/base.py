from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

try:
    from controller.metrics import ServerMetrics
except ImportError:  # pragma: no cover
    from metrics import ServerMetrics  # type: ignore[no-redef]


class LoadBalancingAlgorithm(ABC):
    """Base class for load-balancing algorithms."""

    name: str = "abstract"

    @abstractmethod
    def select(self, servers: Sequence[ServerMetrics]) -> ServerMetrics:
        """Pick a backend for the next incoming flow."""

    def on_selected(self, server: ServerMetrics) -> None:
        """Called after a server is picked.  Override if you need it."""

    def on_flow_removed(self, server: ServerMetrics) -> None:
        """Called when a forward flow for this server expires."""

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r}>"
