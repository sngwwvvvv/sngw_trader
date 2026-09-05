"""RSI (Wilder smoothing) — Chio 2022 (arXiv:2206.12282) §2.3.

Pure logic, no Nautilus imports, no I/O.
"""

from __future__ import annotations


class Rsi:
    def __init__(self, window: int = 14) -> None:
        if window < 2:
            raise ValueError("window must be >= 2")
        self._n = window
        self._prev: float | None = None
        self._count = 0
        self._avg_gain = 0.0
        self._avg_loss = 0.0

    def update(self, close: float) -> float | None:
        if self._prev is None:
            self._prev = close
            return None
        change = close - self._prev
        self._prev = close
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        self._count += 1
        if self._count < self._n:
            self._avg_gain += gain
            self._avg_loss += loss
            return None
        if self._count == self._n:
            # first value: simple average of the first n changes
            self._avg_gain = (self._avg_gain + gain) / self._n
            self._avg_loss = (self._avg_loss + loss) / self._n
        else:
            self._avg_gain = (self._avg_gain * (self._n - 1) + gain) / self._n
            self._avg_loss = (self._avg_loss * (self._n - 1) + loss) / self._n
        if self._avg_loss == 0.0:
            return 100.0 if self._avg_gain > 0.0 else 50.0
        rs = self._avg_gain / self._avg_loss
        return 100.0 - 100.0 / (1.0 + rs)
