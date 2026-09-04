"""Harvey-style volatility targeting. Pure math; no Nautilus imports."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

_MODES = frozenset({"vol_target", "fixed"})


@dataclass(frozen=True)
class VolTargetConfig:
    target_vol: float = 0.20
    half_life: int = 20
    min_scale: float = 0.0
    max_scale: float = 3.0
    rebalance_band: float = 0.10
    periods_per_year: int = 365
    mode: str = "vol_target"

    def __post_init__(self) -> None:
        if self.mode not in _MODES:
            raise ValueError(f"mode must be vol_target|fixed, got {self.mode!r}")
        if self.target_vol <= 0 or self.half_life < 1 or self.min_scale < 0:
            raise ValueError("invalid vol-target bounds")
        if self.max_scale <= self.min_scale:
            raise ValueError("max_scale must be > min_scale")
        if self.rebalance_band < 0 or self.periods_per_year < 1:
            raise ValueError("invalid band or periods_per_year")


class VolTargetSizer:
    def __init__(self, config: VolTargetConfig) -> None:
        self._cfg = config
        self._lam = 2 ** (-1 / config.half_life)
        self._warmup = 3 * config.half_life
        self._prev: float | None = None
        self._var: float | None = None
        self._n: int = 0

    def update(self, close: float) -> float | None:
        if self._prev is None:
            self._prev = close
            return None
        r = close / self._prev - 1.0
        self._prev = close
        self._var = r * r if self._var is None else self._lam * self._var + (1 - self._lam) * r * r
        self._n += 1
        return self._sigma()

    def _sigma(self) -> float | None:
        if self._cfg.mode == "fixed" or self._var is None or self._n < self._warmup:
            return None
        return math.sqrt(self._var * self._cfg.periods_per_year)

    def scale(self) -> float | None:
        if self._cfg.mode == "fixed":
            return 1.0
        sig = self._sigma()
        if sig is None:
            return None
        if sig == 0.0:
            return self._cfg.max_scale
        s = self._cfg.target_vol / sig
        return min(self._cfg.max_scale, max(self._cfg.min_scale, s))

    def desired_qty(self, direction: int, unit_qty: Decimal) -> Decimal | None:
        if direction not in (-1, 0, 1):
            raise ValueError(f"direction must be -1, 0, +1, got {direction}")
        if direction == 0:
            return Decimal("0")
        sc = self.scale()
        if sc is None:
            return None
        return Decimal(direction) * Decimal(str(sc)) * unit_qty

    def should_rebalance(self, current_qty: Decimal, desired_qty: Decimal) -> bool:
        if current_qty == 0 and desired_qty == 0:
            return False
        if current_qty == 0 or desired_qty == 0:
            return True
        if (current_qty > 0) != (desired_qty > 0):
            return True
        drift = abs(desired_qty / current_qty - 1)
        return drift > Decimal(str(self._cfg.rebalance_band))
