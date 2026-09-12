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