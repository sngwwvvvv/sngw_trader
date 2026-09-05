"""Pure KD (stochastic) and MACD indicator math. No Nautilus imports.

Indicator definitions follow Wang & Huang 2026 (raw-trading-0007):
- RSV = (C - L_n) / (H_n - L_n) * 100 over an n-bar rolling window
  (RSV := 0 when H == L, to avoid division by zero).
- K_t = a * RSV_t + (1 - a) * K_{t-1};  D_t = a * K_t + (1 - a) * D_{t-1}
  Seeded at K = D = 50. Paper says alpha "generally 1/3"; the authors'
  published data fits alpha = 0.5 (spec §1.2) — callers choose.
- MACD: DIF = EMA_fast - EMA_slow; DEA = EMA_signal(DIF); bar = DIF - DEA.
  EMA uses the span form alpha = 2/(n+1), seeded with the first value.
"""

from __future__ import annotations

from collections import deque


def crossed_up(prev_k: float, prev_d: float, k: float, d: float) -> bool:
    """K crossed above D: previous K <= D and now K > D."""
    return prev_k <= prev_d and k > d


def crossed_down(prev_k: float, prev_d: float, k: float, d: float) -> bool:
    """K crossed below D: previous K >= D and now K < D."""
    return prev_k >= prev_d and k < d


class _Ema:
    """Span-based EMA seeded with the first value."""

    def __init__(self, span: int) -> None:
        self._a = 2.0 / (span + 1)
        self._value: float | None = None

    @property
    def value(self) -> float | None:
        return self._value

    def update(self, x: float) -> float | None:
        if self._value is None:
            self._value = x
        else:
            self._value += self._a * (x - self._value)
        return self._value


class KdStochastic:
    """Rolling-window stochastic with EWMA-smoothed K and D lines."""

    def __init__(self, n: int, alpha: float) -> None:
        if n < 1:
            raise ValueError("n must be >= 1")
        if not 0.0 < alpha <= 1.0:
            raise ValueError("alpha must be in (0, 1]")
        self._n = n
        self._alpha = alpha
        self._highs: deque[float] = deque(maxlen=n)
        self._lows: deque[float] = deque(maxlen=n)
        self._k: float = 50.0
        self._d: float = 50.0
        self._seen = 0

    def update(self, high: float, low: float, close: float) -> tuple[float, float] | None:
        self._highs.append(high)
        self._lows.append(low)
        self._seen += 1
        if self._seen < self._n:
            return None
        hh = max(self._highs)
        ll = min(self._lows)
        rsv = 0.0 if hh == ll else (close - ll) / (hh - ll) * 100.0
        self._k = self._alpha * rsv + (1.0 - self._alpha) * self._k
        self._d = self._alpha * self._k + (1.0 - self._alpha) * self._d
        return self._k, self._d


class Macd:
    """MACD = (EMA_fast - EMA_slow, EMA_signal(DIF), DIF - DEA)."""

    def __init__(self, fast: int, slow: int, signal: int) -> None:
        if fast < 1 or slow < 1 or signal < 1:
            raise ValueError("spans must be >= 1")
        self._slow_span = slow
        self._signal_span = signal
        self._fast = _Ema(fast)
        self._slow = _Ema(slow)
        self._sig = _Ema(signal)
        self._seen = 0

    def update(self, close: float) -> tuple[float, float, float] | None:
        self._seen += 1
        f = self._fast.update(close)
        s = self._slow.update(close)
        if f is None or s is None:
            return None
        dif = f - s
        if self._seen < self._slow_span + self._signal_span - 1:
            self._sig.update(dif)  # keep signal EMA fed during warmup
            return None
        dea = self._sig.update(dif)
        assert dea is not None
        return dif, dea, dif - dea
