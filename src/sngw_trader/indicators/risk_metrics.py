"""Pure daily-bar risk math shared by both error-momentum strategies.

Streaming, one update per completed bar, valid values only after warm-up.
"""

from __future__ import annotations


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


def stop_price(side: int, ref_price: float, atr: float, mult: float) -> float:
    return ref_price - mult * atr if side > 0 else ref_price + mult * atr


def is_stop_hit(side: int, price: float, stop: float | None) -> bool:
    if stop is None or side == 0:
        return False
    return price < stop if side > 0 else price > stop


def is_take_profit_hit(side: int, price: float, tp: float | None) -> bool:
    if tp is None or side == 0:
        return False
    return price > tp if side > 0 else price < tp


def bracket_hit(
    side: int,
    high: float,
    low: float,
    sl: float | None,
    tp: float | None,
) -> str | None:
    """Return 'sl' or 'tp' if the 1m bar range tags the bracket.

    If both are touched in the same bar, stop-loss wins (pessimistic).
    """
    if side == 0:
        return None
    stop_px = low if side > 0 else high
    tp_px = high if side > 0 else low
    if is_stop_hit(side, stop_px, sl):
        return "sl"
    if is_take_profit_hit(side, tp_px, tp):
        return "tp"
    return None
