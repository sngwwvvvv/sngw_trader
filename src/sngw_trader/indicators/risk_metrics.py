"""Pure daily-bar risk math shared by both error-momentum strategies.

Streaming, one update per completed bar, valid values only after warm-up.
"""

from __future__ import annotations

import math


class Ema:
    """Span-based EMA seeded with the SMA of the first `span` values."""

    def __init__(self, span: int) -> None:
        self._span = span
        self._seed: list[float] = []
        self._value: float | None = None

    @property
    def value(self) -> float | None:
        return self._value

    def update(self, value: float) -> float | None:
        if self._value is None:
            self._seed.append(value)
            if len(self._seed) == self._span:
                self._value = sum(self._seed) / self._span
            return self._value
        self._value += (2.0 / (self._span + 1)) * (value - self._value)
        return self._value


class DailyAtr:
    """Wilder ATR. Valid after `period` updates (seed = mean of first TRs)."""

    def __init__(self, period: int = 14) -> None:
        self._period = period
        self._prev_close: float | None = None
        self._seed: list[float] = []
        self._atr: float | None = None

    @property
    def value(self) -> float | None:
        return self._atr

    def update(self, o: float, h: float, l: float, c: float) -> float | None:
        if self._prev_close is None:
            tr = h - l
        else:
            pc = self._prev_close
            tr = max(h - l, abs(h - pc), abs(l - pc))
        self._prev_close = c
        if self._atr is None:
            self._seed.append(tr)
            if len(self._seed) == self._period:
                self._atr = sum(self._seed) / self._period
            return self._atr
        self._atr = (self._atr * (self._period - 1) + tr) / self._period
        return self._atr


class RealizedVol:
    """Annualized sample std of the last `lookback` daily simple returns."""

    def __init__(self, lookback: int = 20, periods_per_year: int = 365) -> None:
        self._lookback = lookback
        self._ppy = periods_per_year
        self._prev: float | None = None
        self._returns: list[float] = []

    @property
    def value(self) -> float | None:
        if len(self._returns) < self._lookback:
            return None
        mean = sum(self._returns) / self._lookback
        var = sum((r - mean) ** 2 for r in self._returns) / (self._lookback - 1)
        return math.sqrt(var) * math.sqrt(self._ppy)

    def update(self, close: float) -> float | None:
        if self._prev is not None:
            self._returns.append(close / self._prev - 1)
            if len(self._returns) > self._lookback:
                self._returns.pop(0)
        self._prev = close
        return self.value


def stop_price(side: int, ref_price: float, atr: float, mult: float) -> float:
    return ref_price - mult * atr if side > 0 else ref_price + mult * atr


def is_stop_hit(side: int, price: float, stop: float | None) -> bool:
    if stop is None or side == 0:
        return False
    return price < stop if side > 0 else price > stop