"""MFI (Money Flow Index) — Chio 2022 (arXiv:2206.12282) §2.4.

Pure logic, no Nautilus imports, no I/O.
"""

from __future__ import annotations


class Mfi:
    def __init__(self, window: int = 14) -> None:
        if window < 2:
            raise ValueError("window must be >= 2")
        self._n = window
        self._prev_tp: float | None = None
        self._rows: list[tuple[float, float]] = []  # (signed flow, volume)

    def update(self, high: float, low: float, close: float, volume: float) -> float | None:
        tp = (high + low + close) / 3.0
        flow = tp * volume
        if self._prev_tp is None:
            self._prev_tp = tp
            return None
        signed = flow if tp > self._prev_tp else (-flow if tp < self._prev_tp else 0.0)
        self._prev_tp = tp
        self._rows.append((signed, volume))
        if len(self._rows) < self._n:
            return None
        if len(self._rows) > self._n:
            self._rows.pop(0)
        pos = sum(f for f, _ in self._rows if f > 0)
        neg = sum(-f for f, _ in self._rows if f < 0)
        if neg == 0.0:
            return 100.0 if pos > 0.0 else 50.0
        return 100.0 - 100.0 / (1.0 + pos / neg)
