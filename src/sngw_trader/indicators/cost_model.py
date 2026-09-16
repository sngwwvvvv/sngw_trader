"""Pure USDT cost arithmetic for a two-leg spread trade."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence


def _require_finite(name: str, value: float) -> None:
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be finite")


@dataclass(frozen=True)
class Fill:
    leg: str
    signed_quantity: float
    price: float
    fee_rate: float
    half_spread_rate: float

    def __post_init__(self) -> None:
        if not isinstance(self.leg, str) or not self.leg:
            raise ValueError("leg must be a non-empty string")
        _require_finite("signed_quantity", self.signed_quantity)
        if self.signed_quantity == 0:
            raise ValueError("signed_quantity must be non-zero")
        _require_finite("price", self.price)
        if self.price <= 0:
            raise ValueError("price must be positive")
        _require_finite("fee_rate", self.fee_rate)
        if self.fee_rate < 0:
            raise ValueError("fee_rate must not be negative")
        _require_finite("half_spread_rate", self.half_spread_rate)
        if self.half_spread_rate < 0:
            raise ValueError("half_spread_rate must not be negative")


@dataclass(frozen=True)
class FundingObservation:
    leg: str
    timestamp: int
    signed_quantity: float
    mark_price: float
    funding_rate: float

    def __post_init__(self) -> None:
        if not isinstance(self.leg, str) or not self.leg:
            raise ValueError("leg must be a non-empty string")
        if isinstance(self.timestamp, bool) or not isinstance(self.timestamp, int):
            raise ValueError("timestamp must be an integer")
        _require_finite("signed_quantity", self.signed_quantity)
        _require_finite("mark_price", self.mark_price)
        if self.mark_price <= 0:
            raise ValueError("mark_price must be positive")
        _require_finite("funding_rate", self.funding_rate)


@dataclass(frozen=True)
class CostBreakdown:
    fee_usdt: float
    spread_usdt: float
    funding_usdt: float
    total_usdt: float


def calculate_cost(
    fills: Sequence[Fill],
    funding: Sequence[FundingObservation],
    *,
    expected_funding_timestamps: Sequence[int],
) -> CostBreakdown:
    """Calculate realized fee, executable spread, and signed funding costs.

    Funding must contain exactly one explicit observation for every traded leg
    at every expected timestamp. A zero funding rate is valid data; an absent
    or duplicate observation is not.
    """
    if not fills:
        raise ValueError("fills are required")
    if not funding:
        raise ValueError("funding observations are required")
    if not expected_funding_timestamps:
        raise ValueError("expected funding timestamps are required")
    if any(
        isinstance(timestamp, bool) or not isinstance(timestamp, int)
        for timestamp in expected_funding_timestamps
    ):
        raise ValueError("expected funding timestamps must be integers")
    expected_timestamps = tuple(expected_funding_timestamps)
    if len(set(expected_timestamps)) != len(expected_timestamps):
        raise ValueError("expected funding timestamps must be unique")

    legs = {fill.leg for fill in fills}
    funding_legs = {observation.leg for observation in funding}
    missing = sorted(legs - funding_legs)
    if missing:
        raise ValueError(f"missing funding observations for {missing[0]}")
    unknown = sorted(funding_legs - legs)
    if unknown:
        raise ValueError(f"funding observation has unknown leg {unknown[0]}")

    expected = set(expected_timestamps)
    observed: set[tuple[str, int]] = set()
    for observation in funding:
        if observation.timestamp not in expected:
            raise ValueError(
                f"unexpected funding timestamp {observation.timestamp}"
            )
        key = (observation.leg, observation.timestamp)
        if key in observed:
            raise ValueError(
                f"duplicate funding observation for {observation.leg} "
                f"at {observation.timestamp}"
            )
        observed.add(key)
    for leg in legs:
        for timestamp in expected_timestamps:
            if (leg, timestamp) not in observed:
                raise ValueError(
                    f"missing funding observation for {leg} at {timestamp}"
                )

    fee = 0.0
    spread = 0.0
    for fill in fills:
        notional = abs(fill.signed_quantity) * fill.price
        fee += notional * fill.fee_rate
        spread += notional * fill.half_spread_rate

    funding_cost = sum(
        observation.funding_rate
        * observation.signed_quantity
        * observation.mark_price
        for observation in funding
    )
    return CostBreakdown(
        fee_usdt=fee,
        spread_usdt=spread,
        funding_usdt=funding_cost,
        total_usdt=fee + spread + funding_cost,
    )
