from decimal import Decimal, ROUND_DOWN

import pytest

from sngw_trader.strategies.dynamic_grid import (
    geometric_levels,
    quantize_price,
    quantize_qty,
)


def test_geometric_levels_center_bounds_and_count():
    p = Decimal("100")
    k = Decimal("0.01")
    h = 3
    levels = geometric_levels(p, k, h)
    assert len(levels) == 7
    assert levels[h] == p
    assert levels[0] == p * (1 + k) ** (-h)
    assert levels[-1] == p * (1 + k) ** h
    for i, lv in enumerate(levels):
        assert lv == p * (1 + k) ** (i - h)


def test_h0_is_single_center():
    assert geometric_levels(Decimal("50"), Decimal("0.02"), 0) == [Decimal("50")]


class _Inst:
    def make_price(self, value):
        d = Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_DOWN)
        if d == 0:
            raise ValueError("zero")
        return d

    def make_qty(self, value):
        d = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        if d == 0:
            raise ValueError("zero")
        return d


def test_quantize_skips_zero_and_sub_increment():
    inst = _Inst()
    assert quantize_price(inst, Decimal("123.49")) == Decimal("123.4")
    assert quantize_qty(inst, Decimal("0.019")) == Decimal("0.01")
    assert quantize_qty(inst, Decimal("0.003")) is None


from sngw_trader.strategies.dynamic_grid import (
    buy_increments,
    buy_ladder,
    sell_increments,
    sell_ladder,
)


def test_sell_increments_consume_remaining_exactly():
    remaining = Decimal("6")
    qtys = sell_increments(remaining, 3)
    assert qtys == [Decimal("2"), Decimal("2"), Decimal("2")]
    assert sum(qtys) == remaining


def test_sell_increments_n1_is_all():
    assert sell_increments(Decimal("1.5"), 1) == [Decimal("1.5")]


def test_buy_increments_consume_cash_exactly():
    cash = Decimal("99")
    prices = [Decimal("99"), Decimal("98"), Decimal("97")]
    qtys = buy_increments(cash, prices)
    spent = sum(q * p for q, p in zip(qtys, prices))
    assert spent == cash
    assert qtys[0] == (cash / 3) / prices[0]
    assert qtys[1] == (cash / 3) / prices[1]
    assert qtys[2] == (cash / 3) / prices[2]


def test_ladders_pair_price_and_qty():
    sells = sell_ladder(Decimal("6"), [Decimal("101"), Decimal("102"), Decimal("103")])
    assert sells == [
        (Decimal("101"), Decimal("2")),
        (Decimal("102"), Decimal("2")),
        (Decimal("103"), Decimal("2")),
    ]
    buys = buy_ladder(Decimal("9"), [Decimal("99"), Decimal("98"), Decimal("97")])
    assert len(buys) == 3
    assert buys[0][0] == Decimal("99")
    assert sum(q * p for p, q in buys) == Decimal("9")