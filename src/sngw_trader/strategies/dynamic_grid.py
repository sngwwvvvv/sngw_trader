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


def sell_increments(remaining_qty: Decimal, n: int) -> list[Decimal]:
    if n <= 0:
        return []
    out: list[Decimal] = []
    rem = remaining_qty
    for i in range(1, n + 1):
        g = n - i + 1
        q = rem / g
        out.append(q)
        rem -= q
    return out


def buy_increments(remaining_cash: Decimal, prices: list[Decimal]) -> list[Decimal]:
    n = len(prices)
    if n == 0:
        return []
    out: list[Decimal] = []
    rem = remaining_cash
    for j, px in enumerate(prices, 1):
        g = n - j + 1
        cash_j = rem / g
        out.append(cash_j / px)
        rem -= cash_j
    return out


def sell_ladder(remaining_qty: Decimal, levels_up: list[Decimal]) -> list[tuple[Decimal, Decimal]]:
    return list(zip(levels_up, sell_increments(remaining_qty, len(levels_up))))


def buy_ladder(remaining_cash: Decimal, levels_down: list[Decimal]) -> list[tuple[Decimal, Decimal]]:
    return list(zip(levels_down, buy_increments(remaining_cash, levels_down)))