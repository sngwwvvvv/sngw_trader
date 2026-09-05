"""VPVMA indicator — raw-trading-0005 (Chio 2022, arXiv:2206.12282) §4.1 literal.

Formulas (4.2-1..8):
- TP = (H + L + C) / 3
- SVWMA = sum(TP*V)/sum(V) over window_fast; LVWMA same over window_slow
- DV = std(H, L, C, O) — sample std (ddof=1); ddof not stated in the paper
- ESVMap = EMA(SVWMA * DV, fast); ELVMap = EMA(LVWMA * DV, slow)
  (formula 4.2-5 prints SVWMA for ELVMap — typo for LVWMA, spec assumption 1)
- VPVMA = ESVMap - ELVMap
- VPVMAS = SMA(VPVMA, sign)

EMA: span form alpha = 2/(n+1), first-value seed (paper 2.1-1; same
convention as kd_macd._Ema). Pure math, no Nautilus imports, no I/O.
"""

from __future__ import annotations

from collections import deque


class _Ema:
    """Span-based EMA seeded with the first value."""

    def __init__(self, span: int) -> None:
        self._a = 2.0 / (span + 1)
        self._value: float | None = None

    def update(self, x: float) -> float:
        if self._value is None:
            self._value = x
        else:
            self._value += self._a * (x - self._value)
        return self._value


def _vwma(tpv: deque, v: deque) -> float:
    return sum(tpv) / sum(v)


class Vpvma:
    """Incremental VPVMA: (vpvma, vpvmas) or None until warmed."""

    def __init__(self, fast: int = 12, slow: int = 26, sign: int = 9) -> None:
        if fast < 1 or slow < 1 or sign < 1:
            raise ValueError("spans must be >= 1")
        if slow <= fast:
            raise ValueError("slow must be > fast")
        self._fast = fast
        self._slow = slow
        self._sign = sign
        self._tpv_fast: deque[float] = deque(maxlen=fast)
        self._v_fast: deque[float] = deque(maxlen=fast)
        self._tpv_slow: deque[float] = deque(maxlen=slow)
        self._v_slow: deque[float] = deque(maxlen=slow)
        self._ema_fast = _Ema(fast)
        self._ema_slow = _Ema(slow)
        self._vpvmas: deque[float] = deque(maxlen=sign)
        self._seen = 0

    @property
    def warmed(self) -> bool:
        return self._seen >= self._slow + self._sign - 1

    def update(
        self, high: float, low: float, close: float, open_: float, volume: float
    ) -> tuple[float, float] | None:
        self._seen += 1
        tp = (high + low + close) / 3.0
        self._tpv_fast.append(tp * volume)
        self._v_fast.append(volume)
        self._tpv_slow.append(tp * volume)
        self._v_slow.append(volume)
        if len(self._v_slow) < self._slow:
            return None
        dv = statistics_stdev([high, low, close, open_])
        es = self._ema_fast.update(_vwma(self._tpv_fast, self._v_fast) * dv)
        el = self._ema_slow.update(_vwma(self._tpv_slow, self._v_slow) * dv)
        vpvma = es - el
        self._vpvmas.append(vpvma)
        if len(self._vpvmas) < self._sign:
            return None
        return vpvma, sum(self._vpvmas) / len(self._vpvmas)


def statistics_stdev(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5
