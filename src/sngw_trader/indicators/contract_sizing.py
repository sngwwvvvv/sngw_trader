"""Pure Decimal sizing for linear OKX contract instruments."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN


def _decimal(name: str, value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a finite Decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    return result


@dataclass(frozen=True)
class ContractSpec:
    ct_val: Decimal
    lot_sz: Decimal
    min_sz: Decimal
    price_precision: int

    def __post_init__(self) -> None:
        for name in ("ct_val", "lot_sz", "min_sz"):
            value = _decimal(name, getattr(self, name))
            if value <= 0:
                raise ValueError(f"{name} must be positive")
            object.__setattr__(self, name, value)
        if (
            isinstance(self.price_precision, bool)
            or not isinstance(self.price_precision, int)
            or self.price_precision < 0
        ):
            raise ValueError("price_precision must be a non-negative integer")


@dataclass(frozen=True)
class PairSizing:
    y_quantity: Decimal
    x_quantity: Decimal
    y_notional_usdt: Decimal
    x_notional_usdt: Decimal
    gross_notional_usdt: Decimal
    hedge_error_usdt: Decimal


def _round_price(price: object, precision: int, name: str) -> Decimal:
    value = _decimal(name, price)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    quantum = Decimal(1).scaleb(-precision)
    rounded = value.quantize(quantum, rounding=ROUND_DOWN)
    if rounded <= 0:
        raise ValueError(f"{name} must remain positive after precision rounding")
    return rounded


def _size_leg(target_notional: Decimal, price: object, spec: ContractSpec, name: str):
    rounded_price = _round_price(price, spec.price_precision, f"{name}_price")
    raw_quantity = target_notional / (rounded_price * spec.ct_val)
    quantity = (raw_quantity / spec.lot_sz).to_integral_value(rounding=ROUND_DOWN) * spec.lot_sz
    if quantity < spec.min_sz or quantity <= 0:
        raise ValueError(f"{name} quantity below minimum")
    return quantity, quantity * spec.ct_val * rounded_price


def size_pair(
    *,
    target_notional_usdt: object,
    beta: object,
    y_price: object,
    x_price: object,
    y_contract: ContractSpec,
    x_contract: ContractSpec,
) -> PairSizing:
    """Convert target Y notional and positive beta into valid leg quantities."""
    target = _decimal("target_notional_usdt", target_notional_usdt)
    if target <= 0:
        raise ValueError("target_notional_usdt must be positive")
    beta_value = _decimal("beta", beta)
    if beta_value <= 0:
        raise ValueError("beta must be positive")

    y_quantity, y_notional = _size_leg(target, y_price, y_contract, "y")
    x_quantity, x_notional = _size_leg(target * beta_value, x_price, x_contract, "x")
    return PairSizing(
        y_quantity=y_quantity,
        x_quantity=x_quantity,
        y_notional_usdt=y_notional,
        x_notional_usdt=x_notional,
        gross_notional_usdt=y_notional + x_notional,
        hedge_error_usdt=abs(x_notional - beta_value * y_notional),
    )


def contract_spec_from_instrument(instrument: object) -> ContractSpec:
    """Map a Nautilus-like instrument's metadata without importing Nautilus."""
    quote_currency = getattr(instrument, "quote_currency", None)
    settlement_currency = getattr(instrument, "settlement_currency", None)
    if (
        getattr(instrument, "is_inverse", False)
        or any(
            currency is not None and str(currency).upper() != "USDT"
            for currency in (quote_currency, settlement_currency)
        )
    ):
        raise ValueError("instrument must be a linear USDT contract")
    try:
        return ContractSpec(
            ct_val=_decimal("multiplier", getattr(instrument, "multiplier")),
            lot_sz=_decimal("lot_size", getattr(instrument, "lot_size")),
            min_sz=_decimal("min_quantity", getattr(instrument, "min_quantity")),
            price_precision=getattr(instrument, "price_precision"),
        )
    except AttributeError as exc:
        raise ValueError("instrument metadata is missing contract sizing fields") from exc
