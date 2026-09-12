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


from sngw_trader.strategies.dynamic_grid import GridBook


def _book(**kw) -> GridBook:
    return GridBook(h=3, k=Decimal("0.01"), reset_enabled=True, **kw)


def test_open_grid_splits_cash_and_sets_levels():
    b = _book()
    assert b.open_grid(Decimal("10000"), Decimal("100"), Decimal("0.01")) is True
    assert b.grid_qty == Decimal("50")          # (10000/2)/100
    assert b.working_cash == Decimal("5000")
    assert b.bag_qty == 0
    assert b.center == Decimal("100")
    assert len(b.levels) == 7
    assert b.lower == b.levels[0]
    assert b.upper == b.levels[-1]
    assert b.active is True


def test_open_grid_below_min_qty_is_noop():
    b = _book()
    assert b.open_grid(Decimal("1"), Decimal("100"), Decimal("0.01")) is False
    assert b.active is False
    assert b.grid_qty == 0


def test_working_sell_does_not_reduce_bag():
    b = _book()
    b.open_grid(Decimal("10000"), Decimal("100"), Decimal("0.01"))
    b.bag_qty = Decimal("10")
    b.apply_sell(Decimal("5"), Decimal("101"))
    assert b.bag_qty == Decimal("10")
    assert b.grid_qty == Decimal("45")
    assert b.working_cash == Decimal("5000") + Decimal("5") * Decimal("101")


def test_sell_clips_to_grid_qty_never_short():
    b = _book()
    b.open_grid(Decimal("10000"), Decimal("100"), Decimal("0.01"))
    b.apply_sell(Decimal("999"), Decimal("101"))
    assert b.grid_qty == 0
    assert b.bag_qty == 0


def test_upper_reset_clears_grid_keeps_bag():
    b = _book()
    b.open_grid(Decimal("10000"), Decimal("100"), Decimal("0.01"))
    b.bag_qty = Decimal("7")
    assert b.should_reset_upper(b.upper + Decimal("0.01"))
    b.reset_upper()
    assert b.grid_qty == 0
    assert b.bag_qty == Decimal("7")
    assert b.active is False
    assert b.stopped is False
    assert b.can_open() is True


def test_lower_reset_moves_grid_to_bag():
    b = _book()
    b.open_grid(Decimal("10000"), Decimal("100"), Decimal("0.01"))
    grid = b.grid_qty
    assert b.should_reset_lower(b.lower - Decimal("0.01"))
    b.reset_lower()
    assert b.bag_qty == grid
    assert b.grid_qty == 0
    assert b.working_cash == 0
    assert b.active is False
    assert b.can_open() is True


def test_reset_disabled_stops_after_first_bound():
    b = GridBook(h=3, k=Decimal("0.01"), reset_enabled=False)
    b.open_grid(Decimal("10000"), Decimal("100"), Decimal("0.01"))
    b.reset_upper()
    assert b.stopped is True
    assert b.can_open() is False
    assert b.open_grid(Decimal("5000"), Decimal("110"), Decimal("0.01")) is False
    assert b.active is False


def test_working_orders_only_remaining_side_of_last_price():
    b = _book()
    b.open_grid(Decimal("10000"), Decimal("100"), Decimal("0.01"))
    sells, buys = b.working_orders(Decimal("100"))
    assert len(sells) == 3 and len(buys) == 3
    assert sum(q for _, q in sells) == b.grid_qty
    assert sum(p * q for p, q in buys) == b.working_cash
    sells_up, buys_up = b.working_orders(b.levels[b.h + 1])  # last_price = level(+1)
    assert all(px > b.levels[b.h + 1] for px, _ in sells_up)
    assert len(sells_up) == 2
    assert all(px < b.levels[b.h + 1] for px, _ in buys_up)


from nautilus_trader.model import BarType, InstrumentId

from sngw_trader.strategies.dynamic_grid import DynamicGrid, DynamicGridConfig


def test_config_defaults_and_okx_ids():
    cfg = DynamicGridConfig(
        instrument_id=InstrumentId.from_str("BTC-USDT-SWAP.OKX"),
        bar_type=BarType.from_str("BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL"),
        grid_size=0.01,
        grid_numbers_half=3,
    )
    assert str(cfg.instrument_id).endswith(".OKX")
    assert "1-MINUTE-LAST-EXTERNAL" in str(cfg.bar_type)
    assert cfg.reset_enabled is True
    assert cfg.close_positions_on_stop is False
    assert cfg.use_hyphens_in_client_order_ids is False
    assert cfg.order_id_tag == "DGT"
    s = DynamicGrid(config=cfg)
    assert s._book.h == 3
    assert s._book.k == Decimal("0.01")
    assert s._book.reset_enabled is True
    assert s._in_reset is False
    assert s._opening_id is None


def test_strategy_module_has_no_runner_imports():
    from pathlib import Path

    text = Path("src/sngw_trader/strategies/dynamic_grid.py").read_text(encoding="utf-8")
    for token in ("BacktestNode", "TradingNode", "OKXDataClientFactory", "ccxt", "python-okx"):
        assert token not in text


from nautilus_trader.model.enums import OrderSide


class _Num:
    def __init__(self, d):
        self._d = d

    def as_decimal(self):
        return self._d


class _Fill:
    def __init__(self, cid, side):
        self.client_order_id = cid
        self.instrument_id = InstrumentId.from_str("BTC-USDT-SWAP.OKX")
        self.order_side = side
        self.last_qty = _Num(Decimal("1"))
        self.last_px = _Num(Decimal("100"))


def _strategy() -> DynamicGrid:
    cfg = DynamicGridConfig(
        instrument_id=InstrumentId.from_str("BTC-USDT-SWAP.OKX"),
        bar_type=BarType.from_str("BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL"),
        grid_size=0.01,
        grid_numbers_half=3,
    )
    return DynamicGrid(config=cfg)


def test_opening_latch_keys_on_client_order_id():
    s = _strategy()
    s._book.open_grid(Decimal("10000"), Decimal("100"), Decimal("0.01"))
    s._opening_id = "O-OPEN"
    s.cancel_all_orders = lambda *a, **k: None
    s._place_ladder = lambda: None
    s.on_order_filled(_Fill("O-OTHER", OrderSide.BUY))
    assert s._book.grid_qty == Decimal("51")  # non-matching fill -> apply_buy runs
    assert s._opening_id == "O-OPEN"
    s.on_order_filled(_Fill("O-OPEN", OrderSide.BUY))
    assert s._opening_id is None
    assert s._book.grid_qty == Decimal("51")  # opening fill skips apply_buy


def test_place_ladder_cancels_before_replacing():
    s = _strategy()
    s._book.open_grid(Decimal("10000"), Decimal("100"), Decimal("0.01"))
    s._last_price = Decimal("100")
    calls = []
    s.cancel_all_orders = lambda *a, **k: calls.append("cancel")
    s._instrument = lambda: _Inst()
    s._submit_limit = lambda instrument, side, px, qty, reduce_only: calls.append("submit")
    s._place_ladder()
    assert calls[0] == "cancel"
    assert calls.count("cancel") == 1
    assert calls.count("submit") == 6  # 3 sells + 3 buys, all after the cancel