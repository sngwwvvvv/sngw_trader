"""Streaming ERROR-adjusted momentum (Varadi 2014).

Inputs are DAILY closes only. One indicator instance serves both Spec A
and Spec B so the two strategies share an identical ERMOM series.
Pure logic, no Nautilus imports.
"""

from __future__ import annotations

from collections import deque


def regime_target(ermom: float | None, theta: float = 0.0) -> int:
    if ermom is None:
        return 0
    if ermom > theta:
        return 1
    if ermom < -theta:
        return -1
    return 0


class ErrorAdjustedMomentum:
    def __init__(self, w_f: int = 10, w_e: int = 10, momentum_window: int = 200) -> None:
        self._value: float | None = None
        self._wf = w_f
        self._we = w_e
        self._l = momentum_window
        self._prev_close: float | None = None
        self._returns: deque[float] = deque(maxlen=w_f)
        self._abs_err: deque[float] = deque(maxlen=w_e)
        self._adj_r: deque[float] = deque(maxlen=momentum_window)
        self._n_closes = 0

    @property
    def warmup_bars(self) -> int:
        return self._l + self._wf + self._we

    @property
    def value(self) -> float | None:
        """Last computed ERMOM (or None). Read by strategies for regime."""
        return self._value

    def update(self, daily_close: float) -> float | None:
        if self._prev_close is None:
            self._prev_close = daily_close
            self._n_closes = 1
            self._value = None
            return None
        r = daily_close / self._prev_close - 1
        self._prev_close = daily_close
        self._n_closes += 1

        e: float | None = None
        if len(self._returns) == self._wf:
            e = r - sum(self._returns) / self._wf  # e_t = r_t - f_{t-1}, 1-bar lag
        self._returns.append(r)

        if e is not None:
            self._abs_err.append(abs(e))
            if len(self._abs_err) == self._we:
                mae = sum(self._abs_err) / self._we
                if mae > 0:
                    self._adj_r.append(r / mae)

        if len(self._adj_r) == self._l and self._n_closes >= self._l + self._wf + self._we:
            self._value = sum(self._adj_r) / self._l
        else:
            self._value = None
        return self._value
