"""Public contract for the pure Kalman spread filter."""

from __future__ import annotations

import math
from dataclasses import dataclass


def _require_finite(name: str, value: float) -> None:
    try:
        valid = math.isfinite(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not valid:
        raise ValueError(f"{name} must be finite")


@dataclass(frozen=True)
class KalmanSpreadConfig:
    r: float
    delta: float
    x_center: float
    x_scale: float

    def __post_init__(self) -> None:
        _require_finite("r", self.r)
        if self.r <= 0:
            raise ValueError("r must be positive")
        _require_finite("delta", self.delta)
        if not 0 < self.delta < 1:
            raise ValueError("delta must be between 0 and 1")
        if not math.isfinite(self.r * self.delta / (1.0 - self.delta)):
            raise ValueError("derived q must be finite")
        _require_finite("x_center", self.x_center)
        _require_finite("x_scale", self.x_scale)
        if self.x_scale <= 0:
            raise ValueError("x_scale must be positive")


@dataclass(frozen=True)
class KalmanSpreadResult:
    r: float
    innovation: float
    innovation_variance: float
    z_score: float
    beta: float
    ready: bool
    update_count: int

    def __post_init__(self) -> None:
        _require_finite("r", self.r)
        if self.r <= 0:
            raise ValueError("r must be positive")
        for name in ("innovation", "innovation_variance", "z_score", "beta"):
            _require_finite(name, getattr(self, name))
        if self.innovation_variance <= 0:
            raise ValueError("innovation_variance must be positive")
        if not isinstance(self.ready, bool):
            raise ValueError("ready must be a bool")
        if isinstance(self.update_count, bool) or not isinstance(self.update_count, int) or self.update_count < 1:
            raise ValueError("update_count must be a positive integer")
        if self.ready is not (self.update_count >= 72):
            raise ValueError("ready does not match update_count")


class KalmanSpreadFilter:
    def __init__(self, config: KalmanSpreadConfig) -> None:
        self._config = config
        self._q = config.r * config.delta / (1.0 - config.delta)
        self._update_count = 0
        self._theta: tuple[float, float] | None = None
        self._p00 = 0.0
        self._p01 = 0.0
        self._p10 = 0.0
        self._p11 = 0.0

    def update(self, y_price: float, x_price: float) -> KalmanSpreadResult:
        for name, price in (("y_price", y_price), ("x_price", x_price)):
            _require_finite(name, price)
            if price <= 0:
                raise ValueError(f"{name} must be positive")

        y = math.log(y_price)
        x = math.log(x_price)
        u = (x - self._config.x_center) / self._config.x_scale

        if self._theta is None:
            beta_scaled = self._config.x_scale
            self._theta = (y - beta_scaled * u, beta_scaled)
            self._p00 = self._config.r
            self._p01 = 0.0
            self._p10 = 0.0
            self._p11 = self._config.r
            self._update_count += 1
            return KalmanSpreadResult(
                r=self._config.r,
                innovation=0.0,
                innovation_variance=self._config.r,
                z_score=0.0,
                beta=beta_scaled / self._config.x_scale,
                ready=self._update_count >= 72,
                update_count=self._update_count,
            )

        alpha, beta_scaled = self._theta
        p00 = self._p00 + self._q
        p01 = self._p01
        p11 = self._p11 + self._q
        predicted_y = alpha + beta_scaled * u
        innovation = y - predicted_y
        innovation_variance = p00 + 2.0 * u * p01 + u * u * p11 + self._config.r
        if not math.isfinite(innovation_variance) or innovation_variance <= 0.0:
            raise ValueError("innovation variance must be finite and positive")

        gain_alpha = (p00 + p01 * u) / innovation_variance
        gain_beta = (p01 + p11 * u) / innovation_variance
        alpha += gain_alpha * innovation
        beta_scaled += gain_beta * innovation

        a00 = 1.0 - gain_alpha
        a01 = -gain_alpha * u
        a10 = -gain_beta
        a11 = 1.0 - gain_beta * u
        p00_post = a00 * (a00 * p00 + a01 * p01) + a01 * (a00 * p01 + a01 * p11)
        p01_post = a00 * (a10 * p00 + a11 * p01) + a01 * (a10 * p01 + a11 * p11)
        p10_post = a10 * (a00 * p00 + a01 * p01) + a11 * (a00 * p01 + a01 * p11)
        p11_post = a10 * (a10 * p00 + a11 * p01) + a11 * (a10 * p01 + a11 * p11)
        p00_post += self._config.r * gain_alpha * gain_alpha
        p01_post += self._config.r * gain_alpha * gain_beta
        p10_post += self._config.r * gain_beta * gain_alpha
        p11_post += self._config.r * gain_beta * gain_beta

        self._theta = (alpha, beta_scaled)
        self._p00 = p00_post
        self._p01 = 0.5 * (p01_post + p10_post)
        self._p10 = self._p01
        self._p11 = p11_post
        self._update_count += 1
        return KalmanSpreadResult(
            r=self._config.r,
            innovation=innovation,
            innovation_variance=innovation_variance,
            z_score=innovation / math.sqrt(innovation_variance),
            beta=beta_scaled / self._config.x_scale,
            ready=self._update_count >= 72,
            update_count=self._update_count,
        )
