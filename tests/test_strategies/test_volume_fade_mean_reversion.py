from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from nautilus_trader.model import BarType, InstrumentId
from nautilus_trader.model.enums import OrderSide

from sngw_trader.strategies.volume_fade_mean_reversion import (
    VolumeFadeMeanReversion,
    VolumeFadeMeanReversionConfig,
)


INSTRUMENT = InstrumentId.from_str("BTC-USDT-SWAP.OKX")
BAR_TYPE = BarType.from_str(
    "BTC-USDT-SWAP.OKX-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL"
)
NY = ZoneInfo("America/New_York")


def make_strategy(**overrides) -> VolumeFadeMeanReversion:
    values = {
        "instrument_id": INSTRUMENT,
        "bar_type": BAR_TYPE,
        "trade_size": Decimal("0.01"),
    }
    values.update(overrides)
    return VolumeFadeMeanReversion(VolumeFadeMeanReversionConfig(**values))


def test_vf_defaults_match_spec():
    strategy = make_strategy()
    assert strategy.config.bb_period == 20
    assert strategy.config.bb_std == 2.0
    assert strategy.config.atr_mult == 2.5
    assert strategy.config.reentry_bars == 5
    assert strategy.config.vol_fade_lookback == 20
    assert strategy.config.vol_fade_threshold == 0.5
    assert strategy.config.max_trades_per_session == 3


def test_vf_long_requires_breach_and_volume_fade():
    strategy = make_strategy()
    strategy._test_feed_volumes([2.0] * 20 + [1.0])
    assert strategy._test_start_setup(side=1, close=98.0, lower=99.0)


def test_vf_short_requires_breach_and_volume_fade():
    strategy = make_strategy()
    strategy._test_feed_volumes([2.0] * 20 + [1.0])
    assert strategy._test_start_setup(side=-1, close=102.0, upper=101.0)


def test_vf_rejects_breach_without_fade():
    strategy = make_strategy()
    strategy._test_feed_volumes([2.0] * 20 + [3.0])
    assert not strategy._test_start_setup(side=1, close=98.0, lower=99.0)


def test_vf_rejects_insufficient_history():
    strategy = make_strategy()
    strategy._test_feed_volumes([2.0] * 3)
    assert not strategy._test_start_setup(side=1, close=98.0, lower=99.0)
    assert strategy._pending_setup is None


def test_vf_first_reentry_submits_once_and_expires_after_five_bars():
    strategy = make_strategy()
    strategy._test_feed_volumes([2.0] * 20 + [1.0])
    assert strategy._test_start_setup(side=1, close=98.0, lower=99.0)
    assert strategy._test_reentry(side=1, close=100.0, lower=99.0, upper=101.0)

    expired = make_strategy()
    expired._test_feed_volumes([2.0] * 20 + [1.0])
    assert expired._test_start_setup(side=1, close=98.0, lower=99.0)
    for _ in range(5):
        expired._test_advance_pending(close=98.5, lower=99.0, upper=101.0)
    assert expired._pending_setup is None


def test_vf_short_reentry_submits_sell_order(monkeypatch):
    strategy = make_strategy()
    strategy._test_feed_volumes([2.0] * 20 + [1.0])
    assert strategy._test_start_setup(side=-1, close=102.0, upper=101.0)
    submitted = []
    _install_order_fakes(monkeypatch, strategy)
    strategy._instrument = lambda: SimpleNamespace(
        make_qty=lambda value: Decimal(str(value)),
    )
    monkeypatch.setattr(strategy, "submit_order", submitted.append)

    assert strategy._process_pending(
        close=100.0, lower=99.0, upper=101.0, atr=1.0, target=99.0, submit=True
    )

    assert submitted[0].order_side == OrderSide.SELL


def test_vf_session_uses_new_york_calendar_and_dst():
    strategy = make_strategy()
    assert not strategy._session_is_valid(datetime(2026, 9, 17, 9, 29, tzinfo=NY))
    assert strategy._session_is_valid(datetime(2026, 9, 17, 9, 30, tzinfo=NY))
    assert not strategy._session_is_valid(datetime(2026, 9, 17, 16, 0, tzinfo=NY))
    assert not strategy._session_is_valid(datetime(2026, 9, 19, 10, 0, tzinfo=NY))


def test_vf_rejects_fourth_filled_entry():
    strategy = make_strategy()
    strategy._session_date = datetime(2026, 9, 17, tzinfo=NY).date()
    strategy._session_trade_count = 3
    assert not strategy._can_submit_entry(datetime(2026, 9, 17, 10, 0, tzinfo=NY))


def test_vf_at_close_cancels_setup_and_closes_positions(monkeypatch):
    strategy = make_strategy()
    strategy._pending_setup = SimpleNamespace()
    calls = []
    monkeypatch.setattr(strategy, "cancel_all_orders", lambda instrument_id: calls.append("cancel"))
    monkeypatch.setattr(strategy, "close_all_positions", lambda instrument_id: calls.append("close"))

    strategy._test_force_flat(datetime(2026, 9, 17, 16, 0, tzinfo=NY))
    strategy._test_force_flat(datetime(2026, 9, 17, 16, 5, tzinfo=NY))

    assert strategy._pending_setup is None
    assert calls == ["cancel", "close"]


def test_vf_fill_submits_reduce_only_stop_and_target(monkeypatch):
    strategy = make_strategy()
    submitted = []
    strategy._captured_atr = 2.0
    strategy._captured_target = 105.0
    strategy._instrument = lambda: SimpleNamespace(
        make_price=lambda value: Decimal(str(value)),
        make_qty=lambda value: Decimal(str(value)),
    )
    fake_factory = SimpleNamespace(
        stop_market=lambda **kwargs: SimpleNamespace(kind="stop", client_order_id="stop-1", **kwargs),
        limit=lambda **kwargs: SimpleNamespace(kind="limit", client_order_id="target-1", **kwargs),
    )
    monkeypatch.setattr(type(strategy), "order_factory", fake_factory, raising=False)
    monkeypatch.setattr(strategy, "submit_order", submitted.append)

    strategy._submit_bracket(fill_price=100.0, quantity=Decimal("0.01"), side=1)

    assert [order.kind for order in submitted] == ["stop", "limit"]
    assert all(order.reduce_only for order in submitted)
    assert submitted[0].trigger_price == Decimal("95.0")
    assert submitted[1].price == Decimal("105.0")


def _install_order_fakes(monkeypatch, strategy):
    sequence = iter(("entry-1", "stop-1", "target-1"))
    modified = []

    def _order(kind, **kwargs):
        return SimpleNamespace(kind=kind, client_order_id=next(sequence), **kwargs)

    fake_factory = SimpleNamespace(
        market=lambda *args: _order(
            "entry", instrument_id=args[0], order_side=args[1], quantity=args[2]
        ),
        stop_market=lambda **kwargs: _order("stop", **kwargs),
        limit=lambda **kwargs: _order("limit", **kwargs),
    )
    monkeypatch.setattr(type(strategy), "order_factory", fake_factory, raising=False)
    monkeypatch.setattr(strategy, "modify_order", lambda order, **kwargs: modified.append((order, kwargs)))
    return modified


def _fill(order_id, quantity, price, side=OrderSide.BUY, ts_event=0):
    return SimpleNamespace(
        instrument_id=INSTRUMENT,
        client_order_id=order_id,
        last_qty=Decimal(str(quantity)),
        last_px=Decimal(str(price)),
        order_side=side,
        ts_event=ts_event,
    )


def test_vf_partial_entry_fills_resize_one_exit_pair(monkeypatch):
    strategy = make_strategy()
    submitted = []
    _install_order_fakes(monkeypatch, strategy)
    strategy._captured_atr = 2.0
    strategy._captured_target = 105.0
    strategy._instrument = lambda: SimpleNamespace(
        make_price=lambda value: Decimal(str(value)),
        make_qty=lambda value: Decimal(str(value)),
    )
    monkeypatch.setattr(strategy, "submit_order", submitted.append)

    strategy._submit_entry(1)
    strategy.on_order_filled(_fill("entry-1", "0.004", 100.0))
    strategy.on_order_filled(_fill("entry-1", "0.006", 102.0))

    assert strategy._entry_order_id is None
    assert strategy._entry_filled_qty == Decimal("0.010")
    assert [order.kind for order in submitted] == ["entry", "stop", "limit"]


class _Price:
    def __init__(self, value: float):
        self.value = value

    def as_double(self) -> float:
        return self.value


class _Bar:
    bar_type = BAR_TYPE

    def __init__(self, ts_event: int, close: float, volume: float = 1.0):
        self.ts_event = ts_event
        self.ts_init = ts_event
        self.high = _Price(close + 1.0)
        self.low = _Price(close - 1.0)
        self.close = _Price(close)
        self.volume = _Price(volume)


def test_vf_on_bar_breach_with_fade_then_reentry_submits_entry(monkeypatch):
    strategy = make_strategy()
    submitted = []
    _install_order_fakes(monkeypatch, strategy)
    strategy._instrument = lambda: SimpleNamespace(
        make_qty=lambda value: Decimal(str(value)),
    )
    monkeypatch.setattr(strategy, "submit_order", submitted.append)

    start = int(datetime(2026, 9, 14, 9, 30, tzinfo=NY).timestamp() * 1_000_000_000)
    step = 5 * 60 * 1_000_000_000
    for index in range(21):
        strategy.on_bar(_Bar(start + index * step, 100.0, volume=2.0))
    assert strategy._bb.initialized

    strategy.on_bar(_Bar(start + 21 * step, 90.0, volume=1.0))
    assert strategy._pending_setup is not None
    assert strategy._pending_setup.side == 1

    strategy.on_bar(_Bar(start + 22 * step, 100.0, volume=1.0))
    assert [order.kind for order in submitted] == ["entry"]
    assert submitted[0].order_side == OrderSide.BUY


def test_vf_on_bar_breach_without_fade_never_sets_up(monkeypatch):
    strategy = make_strategy()
    submitted = []
    _install_order_fakes(monkeypatch, strategy)
    strategy._instrument = lambda: SimpleNamespace(
        make_qty=lambda value: Decimal(str(value)),
    )
    monkeypatch.setattr(strategy, "submit_order", submitted.append)

    start = int(datetime(2026, 9, 14, 9, 30, tzinfo=NY).timestamp() * 1_000_000_000)
    step = 5 * 60 * 1_000_000_000
    for index in range(21):
        strategy.on_bar(_Bar(start + index * step, 100.0, volume=2.0))
    assert strategy._bb.initialized

    strategy.on_bar(_Bar(start + 21 * step, 90.0, volume=3.0))
    assert strategy._pending_setup is None