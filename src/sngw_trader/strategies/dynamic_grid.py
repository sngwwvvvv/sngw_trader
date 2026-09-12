"""Dynamic Grid Trading. Strategy + Config only. No node / OKX adapter."""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass
class GridBook:
    h: int
    k: Decimal
    reset_enabled: bool
    bag_qty: Decimal = Decimal("0")
    grid_qty: Decimal = Decimal("0")
    working_cash: Decimal = Decimal("0")
    center: Decimal | None = None
    levels: tuple[Decimal, ...] = ()
    active: bool = False
    stopped: bool = False

    @property
    def lower(self) -> Decimal | None:
        return self.levels[0] if self.levels else None

    @property
    def upper(self) -> Decimal | None:
        return self.levels[-1] if self.levels else None

    def can_open(self) -> bool:
        return not self.stopped

    def open_grid(self, m: Decimal, price: Decimal, min_qty: Decimal) -> bool:
        if self.stopped:
            return False
        qty = (m / 2) / price
        if qty < min_qty:
            return False
        self.center = price
        self.levels = tuple(geometric_levels(price, self.k, self.h))
        self.grid_qty = qty
        self.working_cash = m / 2
        self.active = True
        return True

    def apply_buy(self, qty: Decimal, price: Decimal) -> None:
        self.grid_qty += qty
        self.working_cash -= qty * price
        if self.working_cash < 0:
            self.working_cash = Decimal("0")

    def apply_sell(self, qty: Decimal, price: Decimal) -> None:
        filled = qty if qty <= self.grid_qty else self.grid_qty
        self.grid_qty -= filled
        self.working_cash += filled * price

    def working_orders(
        self, last_price: Decimal
    ) -> tuple[list[tuple[Decimal, Decimal]], list[tuple[Decimal, Decimal]]]:
        if not self.active or not self.levels:
            return [], []
        up = [lv for lv in self.levels[self.h + 1 :] if lv > last_price]
        down = [
            self.levels[self.h - i]
            for i in range(1, self.h + 1)
            if self.levels[self.h - i] < last_price
        ]
        return sell_ladder(self.grid_qty, up), buy_ladder(self.working_cash, down)

    def should_reset_upper(self, price: Decimal) -> bool:
        return self.active and self.upper is not None and (price > self.upper or self.grid_qty == 0)

    def should_reset_lower(self, price: Decimal) -> bool:
        return self.active and self.lower is not None and (
            price < self.lower or self.working_cash == 0
        )

    def reset_upper(self) -> None:
        self.grid_qty = Decimal("0")
        self.active = False
        if not self.reset_enabled:
            self.stopped = True

    def reset_lower(self) -> None:
        self.bag_qty += self.grid_qty
        self.grid_qty = Decimal("0")
        self.working_cash = Decimal("0")
        self.active = False
        if not self.reset_enabled:
            self.stopped = True