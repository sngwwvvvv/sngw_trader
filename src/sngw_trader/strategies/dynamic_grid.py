"""Dynamic Grid Trading. Strategy + Config only. No node / OKX adapter."""

from __future__ import annotations

from decimal import Decimal


def geometric_levels(center: Decimal, k: Decimal, h: int) -> list[Decimal]:
    if h < 0:
        raise ValueError("h must be >= 0")
    return [center * (1 + k) ** i for i in range(-h, h + 1)]


def quantize_price(instrument, value: Decimal):
    try:
        px = instrument.make_price(value)
    except ValueError:
        return None
    return None if px == 0 else px


def quantize_qty(instrument, value: Decimal):
    try:
        qty = instrument.make_qty(value)
    except ValueError:
        return None
    return None if qty == 0 else qty