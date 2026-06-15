from __future__ import annotations

from collections import Counter


class MetricsService:
    def __init__(self):
        self.counters: Counter[str] = Counter()

    def increment(self, name: str, amount: int = 1) -> None:
        self.counters[name] += amount

    def snapshot(self) -> dict[str, int]:
        return dict(self.counters)
