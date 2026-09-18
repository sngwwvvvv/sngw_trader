from datetime import datetime
import pytest
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from nautilus_trader.model import BarType, InstrumentId
from nautilus_trader.model.enums import OrderSide

from sngw_trader.strategies.volume_reentry_mean_reversion import (
    VolumeReentryMeanReversion,
    VolumeReentryMeanReversionConfig,
)


INSTRUMENT = InstrumentId.from_str("BTC-USDT-SWAP.OKX")
BAR_TYPE = BarType.from_str(
    "BTC-USDT-SWAP.OKX-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL"
)
REGIME_BAR_TYPE = "BTC-USDT-SWAP.OKX-4-HOUR-LAST-INTERNAL@1-MINUTE-EXTERNAL"
NY = ZoneInfo("America/New_York")


def make_strategy(**overrides) -> VolumeReentryMeanReversion:
    values = {
        "instrument_id": INSTRUMENT,
        "bar_type": BAR_TYPE,
        "trade_size": Decimal("0.01"),
    }
    values.update(overrides)
    return VolumeReentryMeanReversion(VolumeReentryMeanReversionConfig(**values))


def test_vr_defaults_match_spec():
    strategy = make_strategy()
    assert strategy.config.bb_period == 20
    assert strategy.config.bb_std == 2.0
    assert strategy.config.atr_mult == 2.5
    assert strategy.config.reentry_bars == 5
    assert strategy.config.reentry_spike_lookback == 20
    assert strategy.config.reentry_spike_threshold == 2.0
    assert strategy.config.max_trades_per_session == 3


def test_vr_breach_sets_up_without_volume_condition():
    strategy = make_strategy()
    assert strategy._test_start_setup(side=1, close=98.0, lower=99.0)
    assert strategy._test_start_setup(side=-1, close=102.0, upper=101.0)


def test_vr_reentry_with_spike_enters():
    strategy = make_strategy()
    strategy._test_feed_volumes([1.0] * 20 + [3.0])
    assert strategy._test_start_setup(side=1, close=98.0, lower=99.0)
    assert strategy._test_reentry(side=1, close=100.0, lower=99.0, upper=101.0)


def test_vr_reentry_without_spike_drops_setup():
    strategy = make_strategy()
    strategy._test_feed_volumes([1.0] * 20 + [1.0])
    assert strategy._test_start_setup(side=1, close=98.0, lower=99.0)
    assert not strategy._test_reentry(side=1, close=100.0, lower=99.0, upper=101.0)
    assert strategy._pending_setup is None


def test_vr_setup_expires_after_five_bars():
    strategy = make_strategy()
    strategy._test_feed_volumes([1.0] * 20 + [1.0])
    assert strategy._test_start_setup(side=1, close=98.0, lower=99.0)
    for _ in range(5):
        strategy._test_advance_pending(close=98.5, lower=99.0, upper=101.0)
    assert strategy._pending_setup is None


def test_vr_short_reentry_submits_sell_order(monkeypatch):
    strategy = make_strategy()
    strategy._test_feed_volumes([1.0] * 20 + [3.0])
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


def test_vr_session_uses_new_york_calendar_and_dst():
    strategy = make_strategy()
    assert not strategy._session_is_valid(datetime(2026, 9, 17, 9, 29, tzinfo=NY))
    assert strategy._session_is_valid(datetime(2026, 9, 17, 9, 30, tzinfo=NY))
    assert not strategy._session_is_valid(datetime(2026, 9, 17, 16, 0, tzinfo=NY))
    assert not strategy._session_is_valid(datetime(2026, 9, 19, 10, 0, tzinfo=NY))


def test_vr_rejects_fourth_filled_entry():
    strategy = make_strategy()
    strategy._session_date = datetime(2026, 9, 17, tzinfo=NY).date()
    strategy._session_trade_count = 3
    assert not strategy._can_submit_entry(datetime(2026, 9, 17, 10, 0, tzinfo=NY))


def test_vr_at_close_cancels_setup_and_closes_positions(monkeypatch):
    strategy = make_strategy()
    strategy._pending_setup = SimpleNamespace()
    calls = []
    monkeypatch.setattr(strategy, "cancel_all_orders", lambda instrument_id: calls.append("cancel"))
    monkeypatch.setattr(strategy, "close_all_positions", lambda instrument_id: calls.append("close"))

    strategy._test_force_flat(datetime(2026, 9, 17, 16, 0, tzinfo=NY))
    strategy._test_force_flat(datetime(2026, 9, 17, 16, 5, tzinfo=NY))

    assert strategy._pending_setup is None
    assert calls == ["cancel", "close"]


def test_vr_fill_submits_reduce_only_stop_and_target(monkeypatch):
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


def test_vr_partial_entry_fills_resize_one_exit_pair(monkeypatch):
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
    def __init__(
        self,
        ts_event: int,
        close: float,
        volume: float = 1.0,
        bar_type: BarType = BAR_TYPE,
    ):
        self.ts_event = ts_event
        self.ts_init = ts_event
        self.bar_type = bar_type
        self.high = _Price(close + 1.0)
        self.low = _Price(close - 1.0)
        self.close = _Price(close)
        self.volume = _Price(volume)


def test_vr_on_bar_breach_then_reentry_with_spike_submits_entry(monkeypatch):
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
        strategy.on_bar(_Bar(start + index * step, 100.0, volume=1.0))
    assert strategy._bb.initialized

    strategy.on_bar(_Bar(start + 21 * step, 90.0, volume=1.0))
    assert strategy._pending_setup is not None
    assert strategy._pending_setup.side == 1

    strategy.on_bar(_Bar(start + 22 * step, 100.0, volume=3.0))
    assert [order.kind for order in submitted] == ["entry"]
    assert submitted[0].order_side == OrderSide.BUY


def test_vr_on_bar_reentry_without_spike_drops_setup(monkeypatch):
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
        strategy.on_bar(_Bar(start + index * step, 100.0, volume=1.0))
    assert strategy._bb.initialized

    strategy.on_bar(_Bar(start + 21 * step, 90.0, volume=1.0))
    assert strategy._pending_setup is not None

    strategy.on_bar(_Bar(start + 22 * step, 100.0, volume=1.0))
    assert submitted == []
    assert strategy._pending_setup is None


def test_atr_tp_long_target_is_fill_plus_mult_times_atr(monkeypatch):
    strategy = make_strategy(tp_mode="atr", tp_atr_mult=1.5)
    submitted = []
    strategy._captured_atr = 2.0
    strategy._captured_target = 105.0
    strategy._instrument = lambda: SimpleNamespace(
        make_price=lambda value: Decimal(str(value)),
        make_qty=lambda value: Decimal(str(value)),
    )
    fake_factory = SimpleNamespace(
        stop_market=lambda **kwargs: SimpleNamespace(
            kind="stop", client_order_id="stop-1", **kwargs
        ),
        limit=lambda **kwargs: SimpleNamespace(
            kind="limit", client_order_id="target-1", **kwargs
        ),
    )
    monkeypatch.setattr(type(strategy), "order_factory", fake_factory, raising=False)
    monkeypatch.setattr(strategy, "submit_order", submitted.append)

    strategy._submit_bracket(fill_price=100.0, quantity=Decimal("0.01"), side=1)

    assert submitted[1].price == Decimal("103.0")
    assert submitted[0].trigger_price == Decimal("95.0")


def test_atr_tp_short_target_is_fill_minus_mult_times_atr(monkeypatch):
    strategy = make_strategy(tp_mode="atr", tp_atr_mult=1.5)
    submitted = []
    strategy._captured_atr = 2.0
    strategy._captured_target = 105.0
    strategy._instrument = lambda: SimpleNamespace(
        make_price=lambda value: Decimal(str(value)),
        make_qty=lambda value: Decimal(str(value)),
    )
    fake_factory = SimpleNamespace(
        stop_market=lambda **kwargs: SimpleNamespace(
            kind="stop", client_order_id="stop-1", **kwargs
        ),
        limit=lambda **kwargs: SimpleNamespace(
            kind="limit", client_order_id="target-1", **kwargs
        ),
    )
    monkeypatch.setattr(type(strategy), "order_factory", fake_factory, raising=False)
    monkeypatch.setattr(strategy, "submit_order", submitted.append)

    strategy._submit_bracket(fill_price=100.0, quantity=Decimal("0.01"), side=-1)

    assert submitted[1].price == Decimal("97.0")
    assert submitted[0].trigger_price == Decimal("105.0")


def test_rejects_invalid_tp_mode():
    with pytest.raises(ValueError, match="tp_mode"):
        make_strategy(tp_mode="invalid")


FOUR_H_NS = 4 * 60 * 60 * 1_000_000_000
MONDAY_0930 = int(datetime(2026, 9, 21, 9, 30, tzinfo=NY).timestamp() * 1_000_000_000)


def _regime_bar_type() -> BarType:
    return BarType.from_str(REGIME_BAR_TYPE)


def _feed_regime(strategy, count: int, close: float, start_ts: int) -> None:
    for index in range(count):
        strategy.on_bar(_Bar(start_ts + index * FOUR_H_NS, close, bar_type=_regime_bar_type()))


def _trade_bars(strategy, start_ts: int, trigger_close: float) -> None:
    step = 5 * 60 * 1_000_000_000
    for index in range(21):
        strategy.on_bar(_Bar(start_ts + index * step, 100.0))
    strategy.on_bar(_Bar(start_ts + 21 * step, trigger_close))


def test_regime_warmup_blocks_entries():
    strategy = make_strategy(regime_bar_type=REGIME_BAR_TYPE)
    _feed_regime(strategy, 2, 100.0, MONDAY_0930 - 25 * FOUR_H_NS)
    _trade_bars(strategy, MONDAY_0930, 90.0)
    assert strategy._pending_setup is None


def test_regime_uptrend_allows_long_blocks_short():
    strategy = make_strategy(regime_bar_type=REGIME_BAR_TYPE)
    _feed_regime(strategy, 21, 100.0, MONDAY_0930 - 22 * FOUR_H_NS)
    _trade_bars(strategy, MONDAY_0930, 90.0)
    assert strategy._pending_setup is not None
    assert strategy._pending_setup.side == 1


def test_regime_uptrend_blocks_short():
    strategy = make_strategy(regime_bar_type=REGIME_BAR_TYPE)
    _feed_regime(strategy, 21, 100.0, MONDAY_0930 - 22 * FOUR_H_NS)
    _trade_bars(strategy, MONDAY_0930, 110.0)
    assert strategy._pending_setup is None


def test_regime_downtrend_allows_short_blocks_long():
    regime_start = MONDAY_0930 - 22 * FOUR_H_NS

    strategy = make_strategy(regime_bar_type=REGIME_BAR_TYPE)
    _feed_regime(strategy, 21, 100.0, regime_start)
    _feed_regime(strategy, 1, 90.0, regime_start + 21 * FOUR_H_NS)
    _trade_bars(strategy, MONDAY_0930, 90.0)
    assert strategy._pending_setup is None

    strategy = make_strategy(regime_bar_type=REGIME_BAR_TYPE)
    _feed_regime(strategy, 21, 100.0, regime_start)
    _feed_regime(strategy, 1, 90.0, regime_start + 21 * FOUR_H_NS)
    _trade_bars(strategy, MONDAY_0930, 110.0)
    assert strategy._pending_setup is not None
    assert strategy._pending_setup.side == -1


def test_regime_off_by_default_ignores_bars():
    strategy = make_strategy()
    assert strategy._regime_bar_type is None
    _trade_bars(strategy, MONDAY_0930, 90.0)
    assert strategy._pending_setup is not None
    assert strategy._pending_setup.side == 1


def test_hold_across_sessions_skips_force_flat_at_close(monkeypatch):
    strategy = make_strategy(hold_across_sessions=True)
    calls = []
    monkeypatch.setattr(strategy, "cancel_all_orders", lambda instrument_id: calls.append("cancel"))
    monkeypatch.setattr(strategy, "close_all_positions", lambda instrument_id: calls.append("close"))
    strategy._pending_setup = SimpleNamespace()
    close_ts = int(datetime(2026, 9, 21, 16, 0, tzinfo=NY).timestamp() * 1_000_000_000)

    strategy.on_bar(_Bar(close_ts, 100.0))

    assert strategy._pending_setup is not None
    assert calls == []


def _hold_long(strategy, price: float = 100.0) -> None:
    strategy._signed_qty = Decimal("0.01")
    strategy._entry_side = 1
    strategy._entry_filled_qty = Decimal("0.01")
    strategy._entry_filled_notional = Decimal("0.01") * Decimal(str(price))
    strategy._stop_order = SimpleNamespace(client_order_id="stop-1")
    strategy._target_order = SimpleNamespace(client_order_id="target-1")
    strategy._stop_order_id = "stop-1"
    strategy._target_order_id = "target-1"
    strategy._captured_atr = 2.0
    strategy._captured_target = 105.0
    strategy._stop_trigger = price - 2.0 * 2.0


def _feed_in_session(strategy, count: int, close: float, start_ts: int) -> None:
    step = 5 * 60 * 1_000_000_000
    for index in range(count):
        strategy.on_bar(_Bar(start_ts + index * step, close))


def test_regime_flip_against_position_liquidates(monkeypatch):
    strategy = make_strategy(
        regime_bar_type=REGIME_BAR_TYPE, regime_exit_buffer=0.0, hold_across_sessions=True
    )
    calls = []
    monkeypatch.setattr(strategy, "cancel_all_orders", lambda instrument_id: calls.append("cancel"))
    monkeypatch.setattr(strategy, "close_all_positions", lambda instrument_id: calls.append("close"))
    _feed_regime(strategy, 21, 100.0, MONDAY_0930 - 22 * FOUR_H_NS)
    assert strategy._regime_side == 1
    _hold_long(strategy)

    _feed_regime(strategy, 1, 90.0, MONDAY_0930 - FOUR_H_NS)

    assert calls == ["cancel", "close"]
    assert strategy._exit_requested


def test_regime_flip_aligned_with_position_does_not_liquidate(monkeypatch):
    strategy = make_strategy(
        regime_bar_type=REGIME_BAR_TYPE, regime_exit_buffer=0.0, hold_across_sessions=True
    )
    calls = []
    monkeypatch.setattr(strategy, "cancel_all_orders", lambda instrument_id: calls.append("cancel"))
    monkeypatch.setattr(strategy, "close_all_positions", lambda instrument_id: calls.append("close"))
    _feed_regime(strategy, 21, 100.0, MONDAY_0930 - 22 * FOUR_H_NS)
    _hold_long(strategy)

    _feed_regime(strategy, 1, 100.0, MONDAY_0930 - FOUR_H_NS)

    assert calls == []
    assert not strategy._exit_requested


def test_regime_neutral_band_holds_position(monkeypatch):
    strategy = make_strategy(
        regime_bar_type=REGIME_BAR_TYPE, regime_exit_buffer=0.5, hold_across_sessions=True
    )
    calls = []
    monkeypatch.setattr(strategy, "cancel_all_orders", lambda instrument_id: calls.append("cancel"))
    monkeypatch.setattr(strategy, "close_all_positions", lambda instrument_id: calls.append("close"))
    _feed_regime(strategy, 21, 100.0, MONDAY_0930 - 22 * FOUR_H_NS)
    _hold_long(strategy)

    # 4H ATR ~= 2.0 (_Bar high=close+1, low=close-1), band = 0.5*2 = 1.0 > |99.5-100|
    _feed_regime(strategy, 1, 99.5, MONDAY_0930 - FOUR_H_NS)

    assert calls == []
    assert strategy._regime_side == 1
    assert not strategy._exit_requested


def test_regime_exit_ignores_no_position(monkeypatch):
    strategy = make_strategy(
        regime_bar_type=REGIME_BAR_TYPE, regime_exit_buffer=0.0, hold_across_sessions=True
    )
    calls = []
    monkeypatch.setattr(strategy, "cancel_all_orders", lambda instrument_id: calls.append("cancel"))
    monkeypatch.setattr(strategy, "close_all_positions", lambda instrument_id: calls.append("close"))
    _feed_regime(strategy, 21, 100.0, MONDAY_0930 - 22 * FOUR_H_NS)

    _feed_regime(strategy, 1, 90.0, MONDAY_0930 - FOUR_H_NS)

    assert calls == []
    assert strategy._regime_side == -1


def test_chandelier_raises_stop_after_new_highs(monkeypatch):
    strategy = make_strategy(chandelier_k=3.0, chandelier_lookback=3)
    modified = []
    _install_order_fakes(monkeypatch, strategy)
    strategy._instrument = lambda: SimpleNamespace(
        make_price=lambda value: Decimal(str(value)),
        make_qty=lambda value: Decimal(str(value)),
    )

    def _modify(order, **kwargs):
        modified.append((order, kwargs))

    monkeypatch.setattr(strategy, "modify_order", _modify)
    start_ts = int(datetime(2026, 9, 21, 9, 30, tzinfo=NY).timestamp() * 1_000_000_000)
    _feed_in_session(strategy, 21, 100.0, start_ts)
    assert strategy._atr.initialized
    _hold_long(strategy)  # stop_trigger = 96.0

    # close=103, high=104: chandelier = 104 - 3.0 * ATR(30m) > 96.0
    _feed_in_session(strategy, 1, 103.0, start_ts + 21 * 5 * 60 * 1_000_000_000)

    expected = 104.0 - 3.0 * float(strategy._atr.value)
    assert expected > 96.0
    assert strategy._stop_trigger == pytest.approx(expected)
    assert modified and modified[0][1]["trigger_price"] == Decimal(str(expected))


def test_chandelier_never_lowers_stop(monkeypatch):
    strategy = make_strategy(chandelier_k=3.0, chandelier_lookback=3)
    modified = []
    _install_order_fakes(monkeypatch, strategy)
    strategy._instrument = lambda: SimpleNamespace(
        make_price=lambda value: Decimal(str(value)),
        make_qty=lambda value: Decimal(str(value)),
    )

    def _modify(order, **kwargs):
        modified.append((order, kwargs))

    monkeypatch.setattr(strategy, "modify_order", _modify)
    start_ts = int(datetime(2026, 9, 21, 9, 30, tzinfo=NY).timestamp() * 1_000_000_000)
    _feed_in_session(strategy, 21, 100.0, start_ts)
    _hold_long(strategy)

    _feed_in_session(strategy, 1, 103.0, start_ts + 21 * 5 * 60 * 1_000_000_000)
    assert len(modified) == 1
    raised_to = strategy._stop_trigger

    _feed_in_session(strategy, 1, 97.0, start_ts + 22 * 5 * 60 * 1_000_000_000)

    assert strategy._stop_trigger == pytest.approx(raised_to)
    assert len(modified) == 1


def test_time_stop_liquidates_after_n_bars(monkeypatch):
    strategy = make_strategy(time_stop_bars=3, hold_across_sessions=True)
    calls = []
    monkeypatch.setattr(strategy, "cancel_all_orders", lambda instrument_id: calls.append("cancel"))
    monkeypatch.setattr(strategy, "close_all_positions", lambda instrument_id: calls.append("close"))
    start_ts = int(datetime(2026, 9, 21, 9, 30, tzinfo=NY).timestamp() * 1_000_000_000)
    _feed_in_session(strategy, 21, 100.0, start_ts)
    _hold_long(strategy)

    _feed_in_session(strategy, 2, 100.5, start_ts + 21 * 5 * 60 * 1_000_000_000)
    assert calls == []
    _feed_in_session(strategy, 1, 100.5, start_ts + 23 * 5 * 60 * 1_000_000_000)

    assert calls == ["cancel", "close"]
    assert strategy._exit_requested


def test_time_stop_off_by_default(monkeypatch):
    strategy = make_strategy()
    calls = []
    monkeypatch.setattr(strategy, "cancel_all_orders", lambda instrument_id: calls.append("cancel"))
    monkeypatch.setattr(strategy, "close_all_positions", lambda instrument_id: calls.append("close"))
    start_ts = int(datetime(2026, 9, 21, 9, 30, tzinfo=NY).timestamp() * 1_000_000_000)
    _feed_in_session(strategy, 21, 100.0, start_ts)
    _hold_long(strategy)

    _feed_in_session(strategy, 10, 100.5, start_ts + 21 * 5 * 60 * 1_000_000_000)

    assert calls == []