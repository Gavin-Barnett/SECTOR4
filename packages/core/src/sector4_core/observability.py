from __future__ import annotations

from collections import Counter
from threading import Lock


class MetricsRegistry:
    def __init__(self) -> None:
        self._counters: Counter[str] = Counter()
        self._lock = Lock()

    def increment(self, name: str, amount: int = 1) -> None:
        if amount == 0:
            return
        with self._lock:
            self._counters[name] += amount

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(sorted(self._counters.items()))

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()


_registry = MetricsRegistry()


def get_metrics_registry() -> MetricsRegistry:
    return _registry
