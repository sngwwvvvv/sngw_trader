"""Deterministic portfolio approval checks for the Kalman spread strategy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import MappingProxyType

from sngw_trader.indicators.contract_sizing import PairSizing
from sngw_trader.indicators.cost_model import CostBreakdown


def _decimal(name: str, value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a finite Decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    return result


def _safe_decimal(value: object) -> Decimal | None:
    try:
        return _decimal("value", value)
    except ValueError:
        return None


@dataclass(frozen=True)
class RiskLimits:
    max_active_pairs: int
    max_gross_exposure_ratio: Decimal
    max_pair_margin_ratio: Decimal
    max_asset_exposure_usdt: Decimal
    max_daily_loss_usdt: Decimal
    max_drawdown_ratio: Decimal
    min_liquidation_buffer_usdt: Decimal

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_active_pairs, bool)
            or not isinstance(self.max_active_pairs, int)
            or self.max_active_pairs < 1
        ):
            raise ValueError("max_active_pairs must be a positive integer")
        for name in (
            "max_gross_exposure_ratio",
            "max_pair_margin_ratio",
            "max_asset_exposure_usdt",
            "max_daily_loss_usdt",
            "max_drawdown_ratio",
            "min_liquidation_buffer_usdt",
        ):
            value = _decimal(name, getattr(self, name))
            if value is None or value < 0:
                raise ValueError(f"{name} must not be negative")
            object.__setattr__(self, name, value)


@dataclass(frozen=True)
class PortfolioSnapshot:
    equity_usdt: Decimal | None
    peak_equity_usdt: Decimal | None
    daily_loss_usdt: Decimal | None
    liquidation_buffer_usdt: Decimal | None
    active_pairs: frozenset[str] | None
    gross_notional_usdt: Decimal | None
    asset_exposure_usdt: Mapping[str, Decimal] | None

    def __post_init__(self) -> None:
        if self.asset_exposure_usdt is not None:
            object.__setattr__(
                self,
                "asset_exposure_usdt",
                MappingProxyType(dict(self.asset_exposure_usdt)),
            )


@dataclass(frozen=True)
class PairCandidate:
    pair_id: str
    y_asset: str
    x_asset: str
    sizing: PairSizing
    margin_usdt: Decimal
    cost: CostBreakdown | None
    y_price_usdt: Decimal | None
    x_price_usdt: Decimal | None
    y_side: int
    x_side: int

    def __post_init__(self) -> None:
        if not self.pair_id or not self.y_asset or not self.x_asset:
            raise ValueError("pair and asset names are required")
        if (
            isinstance(self.y_side, bool)
            or isinstance(self.x_side, bool)
            or self.y_side not in (-1, 1)
            or self.x_side not in (-1, 1)
        ):
            raise ValueError("side must be 1 or -1")


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reasons: tuple[str, ...]
    projected_gross_notional_usdt: Decimal
    projected_asset_exposure_usdt: dict[str, Decimal]


def _projected_assets(snapshot: PortfolioSnapshot, candidate: PairCandidate) -> dict[str, Decimal]:
    current = {
        asset: _safe_decimal(value) or Decimal("0")
        for asset, value in (snapshot.asset_exposure_usdt or {}).items()
    }
    y_notional = _safe_decimal(getattr(candidate.sizing, "y_notional_usdt", None)) or Decimal("0")
    x_notional = _safe_decimal(getattr(candidate.sizing, "x_notional_usdt", None)) or Decimal("0")
    current[candidate.y_asset] = current.get(candidate.y_asset, Decimal("0")) + (
        Decimal(candidate.y_side) * y_notional
    )
    current[candidate.x_asset] = current.get(candidate.x_asset, Decimal("0")) + (
        Decimal(candidate.x_side) * x_notional
    )
    return current


def approve_pair(
    snapshot: PortfolioSnapshot,
    candidate: PairCandidate,
    limits: RiskLimits,
) -> RiskDecision:
    """Approve a candidate without submitting or modifying any orders."""
    projected_assets = _projected_assets(snapshot, candidate)
    current_gross = _safe_decimal(snapshot.gross_notional_usdt)
    daily_loss_value = _safe_decimal(snapshot.daily_loss_usdt)
    candidate_gross = _safe_decimal(getattr(candidate.sizing, "gross_notional_usdt", None))
    projected_gross = (current_gross or Decimal("0")) + (candidate_gross or Decimal("0"))

    sizing_values = tuple(
        _safe_decimal(getattr(candidate.sizing, name, None))
        for name in (
            "y_quantity",
            "x_quantity",
            "y_notional_usdt",
            "x_notional_usdt",
            "gross_notional_usdt",
            "hedge_error_usdt",
        )
    )
    cost_total = _safe_decimal(
        getattr(candidate.cost, "total_usdt", None) if candidate.cost is not None else None
    )
    cost_values = tuple(
        _safe_decimal(getattr(candidate.cost, name, None))
        for name in ("fee_usdt", "spread_usdt", "funding_usdt", "total_usdt")
    )
    asset_values = (
        tuple(_safe_decimal(value) for value in snapshot.asset_exposure_usdt.values())
        if snapshot.asset_exposure_usdt is not None
        else ()
    )
    invalid_sizing = (
        len(sizing_values) != 6
        or any(value is None for value in sizing_values)
        or any(value <= 0 for value in sizing_values[:5] if value is not None)
        or sizing_values[5] is None
        or sizing_values[5] < 0
    )
    invalid_nested_data = (
        any(value is None for value in cost_values)
        or any(value < 0 for value in cost_values[:2] if value is not None)
        or any(value is None for value in asset_values)
        or invalid_sizing
        or _safe_decimal(candidate.y_price_usdt) is None
        or _safe_decimal(candidate.x_price_usdt) is None
        or (_safe_decimal(candidate.y_price_usdt) or Decimal("0")) <= 0
        or (_safe_decimal(candidate.x_price_usdt) or Decimal("0")) <= 0
    )

    missing = (
        _safe_decimal(snapshot.equity_usdt) is None
        or _safe_decimal(snapshot.peak_equity_usdt) is None
        or _safe_decimal(snapshot.daily_loss_usdt) is None
        or _safe_decimal(snapshot.liquidation_buffer_usdt) is None
        or snapshot.active_pairs is None
        or current_gross is None
        or snapshot.asset_exposure_usdt is None
        or candidate.cost is None
        or invalid_nested_data
        or (current_gross is not None and current_gross < 0)
        or (daily_loss_value is not None and daily_loss_value < 0)
    )
    if missing:
        return RiskDecision(False, ("MISSING_DATA",), projected_gross, projected_assets)

    equity = _safe_decimal(snapshot.equity_usdt)
    peak_equity = _safe_decimal(snapshot.peak_equity_usdt)
    daily_loss = daily_loss_value
    liquidation_buffer = _safe_decimal(snapshot.liquidation_buffer_usdt)
    margin = _safe_decimal(candidate.margin_usdt)
    if any(value is None for value in (equity, peak_equity, daily_loss, liquidation_buffer, margin)):
        return RiskDecision(False, ("MISSING_DATA",), projected_gross, projected_assets)
    if equity <= 0 or peak_equity <= 0 or margin < 0:
        return RiskDecision(False, ("MISSING_DATA",), projected_gross, projected_assets)

    reasons: list[str] = []
    if candidate.pair_id in snapshot.active_pairs:
        reasons.append("DUPLICATE_PAIR")
    elif len(snapshot.active_pairs) >= limits.max_active_pairs:
        reasons.append("MAX_ACTIVE_PAIRS")
    if margin > equity * limits.max_pair_margin_ratio:
        reasons.append("PAIR_MARGIN")
    if any(abs(value) > limits.max_asset_exposure_usdt for value in projected_assets.values()):
        reasons.append("ASSET_EXPOSURE")
    if projected_gross > equity * limits.max_gross_exposure_ratio:
        reasons.append("GROSS_EXPOSURE")

    cost_reserve = max(cost_total or Decimal("0"), Decimal("0"))
    if daily_loss + cost_reserve > limits.max_daily_loss_usdt:
        reasons.append("DAILY_LOSS")
    drawdown = max((peak_equity - equity) / peak_equity, Decimal("0"))
    if drawdown > limits.max_drawdown_ratio:
        reasons.append("DRAWDOWN")
    if liquidation_buffer - margin < limits.min_liquidation_buffer_usdt:
        reasons.append("LIQUIDATION_BUFFER")
    return RiskDecision(not reasons, tuple(reasons), projected_gross, projected_assets)
