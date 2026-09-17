from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from nautilus_trader.model import BarType, InstrumentId
from nautilus_trader.model.enums import OrderSide

from sngw_trader.data.open_interest import OpenInterestPoint, wrap_open_interest
from sngw_trader.strategies.oi_crowding_mean_reversion import (
    OiCrowdingMeanReversion,
    OiCrowdingMeanReversionConfig,
)


INSTRUMENT = InstrumentId.from_str("BTC-USDT-SWAP.OKX")
BAR_TYPE = BarType.from_str(
    "BTC-USDT-SWAP.OKX-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL"
)
NY = ZoneInfo("America/New_York")


def make_strategy(**overrides) -> OiCrowdingMeanReversion:
    values = {
        "instrument_id": INSTRUMENT,
        "bar_type": BAR_TYPE,
        "trade_size": Decimal("0.01"),
    }
    values.update(overrides)
    return OiCrowdingMeanReversion(OiCrowdingMeanReversionConfig(**values))


def test_a_config_defaults_match_spec():
    strategy = make_strategy()
    assert strategy.config.bb_period == 20
    assert strategy.config.bb_std == 2.0
    assert strategy.config.atr_period == 14
    assert strategy.config.atr_mult == 2.5
    assert strategy.config.reentry_bars == 5
    assert strategy.config.oi_lookback_bars == 3
    assert strategy.config.instrument_id == INSTRUMENT
    assert strategy.config.bar_type == BAR_TYPE


def test_a_subscribes_to_price_and_oi_data_on_start(monkeypatch):
    strategy = make_strategy()
    bars = []
    data = []
    monkeypatch.setattr(strategy, "subscribe_bars", bars.append)
    monkeypatch.setattr(strategy, "subscribe_data", data.append)

    strategy.on_start()

    assert bars == [BAR_TYPE]
    assert data[0].type.__name__ == "OpenInterestPoint"


def test_a_long_requires_lower_breach_oi_increase_rollover_and_reentry():
    strategy = make_strategy()
    strategy._test_feed_oi([100.0, 102.0, 104.0])
    strategy._test_start_setup(side=1, close=98.0, lower=99.0)
    assert strategy._pending_setup is not None
    assert strategy._test_reentry(
        side=1, close=100.0, lower=99.0, upper=101.0, oi=103.0
    )


def test_a_does_not_enter_while_oi_keeps_rising():
    strategy = make_strategy()
    strategy._test_start_setup(side=1, close=98.0, lower=99.0)
    assert not strategy._test_reentry(
        side=1, close=100.0, lower=99.0, upper=101.0, oi=105.0
    )


def test_first_failed_reentry_consumes_setup():
    strategy = make_strategy()
    strategy._test_feed_oi([100.0, 102.0, 104.0])
    strategy._test_start_setup(side=1, close=98.0, lower=99.0)

    assert not strategy._test_reentry(
        side=1, close=100.0, lower=99.0, upper=101.0, oi=105.0
    )
    assert strategy._pending_setup is None
    with pytest.raises(AssertionError, match="pending setup"):
        strategy._test_reentry(
            side=1, close=100.0, lower=99.0, upper=101.0, oi=103.0
        )


def test_test_helpers_require_a_real_breach_and_existing_setup():
    strategy = make_strategy()
    with pytest.raises(ValueError, match="long breach"):
        strategy._test_start_setup(side=1, close=100.0, lower=99.0)
    with pytest.raises(AssertionError, match="pending setup"):
        strategy._test_reentry(
            side=1, close=100.0, lower=99.0, upper=101.0, oi=103.0
        )


def test_a_expires_after_five_reentry_bars():
    strategy = make_strategy()
    strategy._test_start_setup(side=1, close=98.0, lower=99.0)
    for _ in range(5):
        strategy._test_advance_pending(
            close=98.5, lower=99.0, upper=101.0, oi=104.0
        )
    assert strategy._pending_setup is None


def test_session_uses_new_york_calendar_and_excludes_boundaries():
    strategy = make_strategy()
    assert strategy._session_is_valid(datetime(2026, 9, 17, 9, 29, tzinfo=NY)) is False
    assert strategy._session_is_valid(datetime(2026, 9, 17, 9, 30, tzinfo=NY)) is True
    assert strategy._session_is_valid(datetime(2026, 9, 17, 16, 0, tzinfo=NY)) is False
    assert strategy._session_is_valid(datetime(2026, 9, 19, 10, 0, tzinfo=NY)) is False


def test_session_handles_dst_without_fixed_offset():
    strategy = make_strategy()
    before = datetime(2026, 3, 6, 14, 30, tzinfo=timezone.utc)
    after = datetime(2026, 3, 9, 13, 30, tzinfo=timezone.utc)
    assert strategy._session_is_valid(before.astimezone(NY))
    assert strategy._session_is_valid(after.astimezone(NY))


def test_fourth_filled_entry_is_rejected():
    strategy = make_strategy()
    strategy._session_date = datetime(2026, 9, 17, tzinfo=NY).date()
    strategy._session_trade_count = 3
    assert not strategy._can_submit_entry(datetime(2026, 9, 17, 10, 0, tzinfo=NY))


def test_at_close_cancels_setup_and_closes_positions(monkeypatch):
    strategy = make_strategy()
    strategy._pending_setup = SimpleNamespace()
    calls = []
    monkeypatch.setattr(strategy, "cancel_all_orders", lambda instrument_id: calls.append("cancel"))
    monkeypatch.setattr(
        strategy,
        "close_all_positions",
        lambda instrument_id: calls.append("close"),
    )

    strategy._test_force_flat(datetime(2026, 9, 17, 16, 0, tzinfo=NY))
    strategy._test_force_flat(datetime(2026, 9, 17, 16, 5, tzinfo=NY))

    assert strategy._pending_setup is None
    assert calls == ["cancel", "close"]


def test_fill_submits_reduce_only_stop_and_target(monkeypatch):
    strategy = make_strategy()
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


def test_partial_entry_fills_keep_identity_and_resize_one_exit_pair(monkeypatch):
    strategy = make_strategy()
    submitted = []
    modified = _install_order_fakes(monkeypatch, strategy)
    strategy._captured_atr = 2.0
    strategy._captured_target = 105.0
    strategy._instrument = lambda: SimpleNamespace(
        make_price=lambda value: Decimal(str(value)),
        make_qty=lambda value: Decimal(str(value)),
    )
    monkeypatch.setattr(strategy, "submit_order", submitted.append)

    strategy._submit_entry(1)
    strategy.on_order_filled(_fill("entry-1", "0.004", 100.0))
    assert strategy._entry_order_id == "entry-1"
    assert strategy._entry_filled_qty == Decimal("0.004")
    assert [order.kind for order in submitted] == ["entry", "stop", "limit"]
    assert submitted[1].quantity == Decimal("0.004")

    strategy.on_order_filled(_fill("entry-1", "0.006", 102.0))
    assert strategy._entry_order_id is None
    assert strategy._entry_filled_qty == Decimal("0.010")
    assert modified
    assert modified[-2][1]["quantity"] == Decimal("0.010")
    assert modified[-1][1]["quantity"] == Decimal("0.010")


def test_force_flat_keeps_entry_identity_for_cancel_late_fill(monkeypatch):
    strategy = make_strategy()
    close_calls = []
    cancel_calls = []
    _install_order_fakes(monkeypatch, strategy)
    strategy._instrument = lambda: SimpleNamespace(
        make_price=lambda value: Decimal(str(value)),
        make_qty=lambda value: Decimal(str(value)),
    )
    monkeypatch.setattr(strategy, "submit_order", lambda order: None)
    monkeypatch.setattr(strategy, "cancel_all_orders", lambda instrument_id: cancel_calls.append(1))
    monkeypatch.setattr(strategy, "close_all_positions", lambda instrument_id: close_calls.append(1))
    strategy._captured_atr = 2.0
    strategy._captured_target = 105.0
    strategy._submit_entry(1)

    close_ts = int(datetime(2026, 9, 17, 16, 0, tzinfo=NY).timestamp() * 1_000_000_000)
    strategy.on_bar(_Bar(close_ts, 100.0))
    assert strategy._entry_order_id == "entry-1"
    assert cancel_calls == [1]
    assert close_calls == [1]

    strategy.on_bar(_Bar(close_ts + 5 * 60 * 1_000_000_000, 100.0))
    assert close_calls == [1]

    strategy.on_order_canceled(SimpleNamespace(client_order_id="entry-1"))
    strategy.on_order_filled(_fill("entry-1", "0.004", 100.0))
    assert close_calls == [1, 1]
    assert strategy._signed_qty == Decimal("0.004")


def test_late_entry_invalid_target_does_not_duplicate_flatten(monkeypatch):
    strategy = make_strategy()
    close_calls = []
    _install_order_fakes(monkeypatch, strategy)
    strategy._instrument = lambda: SimpleNamespace(
        make_price=lambda value: Decimal(str(value)),
        make_qty=lambda value: Decimal(str(value)),
    )
    monkeypatch.setattr(strategy, "submit_order", lambda order: None)
    monkeypatch.setattr(strategy, "cancel_all_orders", lambda instrument_id: None)
    monkeypatch.setattr(strategy, "close_all_positions", lambda instrument_id: close_calls.append(1))
    strategy._captured_atr = 2.0
    strategy._captured_target = 99.0
    strategy._submit_entry(1)

    close_ts = int(datetime(2026, 9, 17, 16, 0, tzinfo=NY).timestamp() * 1_000_000_000)
    strategy.on_bar(_Bar(close_ts, 100.0))
    strategy.on_order_canceled(SimpleNamespace(client_order_id="entry-1"))
    strategy.on_order_filled(_fill("entry-1", "0.004", 100.0, ts_event=close_ts))

    assert close_calls == [1, 1]


@pytest.mark.parametrize("callback", ["on_order_rejected", "on_order_denied", "on_order_expired"])
def test_entry_failure_clears_state_for_recovery(monkeypatch, callback):
    strategy = make_strategy()
    _install_order_fakes(monkeypatch, strategy)
    strategy._instrument = lambda: SimpleNamespace(
        make_price=lambda value: Decimal(str(value)),
        make_qty=lambda value: Decimal(str(value)),
    )
    monkeypatch.setattr(strategy, "submit_order", lambda order: None)
    strategy._captured_atr = 2.0
    strategy._captured_target = 105.0
    strategy._pending_setup = SimpleNamespace()
    strategy._submit_entry(1)

    getattr(strategy, callback)(SimpleNamespace(client_order_id="entry-1"))

    assert strategy._entry_order_id is None
    assert strategy._entry_order_active is False
    assert strategy._pending_setup is None
    assert strategy._captured_atr is None


@pytest.mark.parametrize("callback", ["on_order_rejected", "on_order_denied", "on_order_expired"])
def test_protective_failure_cancels_sibling_and_flattens(monkeypatch, callback):
    strategy = make_strategy()
    cancel_calls = []
    close_calls = []
    strategy._stop_order = SimpleNamespace(client_order_id="stop-1")
    strategy._stop_order_id = "stop-1"
    strategy._target_order = SimpleNamespace(client_order_id="target-1")
    strategy._target_order_id = "target-1"
    strategy._signed_qty = Decimal("0.01")
    monkeypatch.setattr(strategy, "cancel_order", lambda order: cancel_calls.append(order))
    monkeypatch.setattr(strategy, "close_all_positions", lambda instrument_id: close_calls.append(instrument_id))

    getattr(strategy, callback)(SimpleNamespace(client_order_id="stop-1"))

    assert cancel_calls == [strategy._target_order]
    assert close_calls == [INSTRUMENT]


def test_protective_failure_uses_existing_force_flat_request(monkeypatch):
    strategy = make_strategy()
    close_calls = []
    strategy._stop_order = SimpleNamespace(client_order_id="stop-1")
    strategy._stop_order_id = "stop-1"
    strategy._target_order = SimpleNamespace(client_order_id="target-1")
    strategy._target_order_id = "target-1"
    strategy._signed_qty = Decimal("0.01")
    strategy._force_flat_close_pending = True
    monkeypatch.setattr(strategy, "cancel_order", lambda order: None)
    monkeypatch.setattr(strategy, "close_all_positions", lambda instrument_id: close_calls.append(instrument_id))

    strategy.on_order_rejected(SimpleNamespace(client_order_id="stop-1"))

    assert close_calls == []


def test_repeated_protective_failure_does_not_duplicate_flatten(monkeypatch):
    strategy = make_strategy()
    close_calls = []
    strategy._stop_order = SimpleNamespace(client_order_id="stop-1")
    strategy._stop_order_id = "stop-1"
    strategy._target_order = SimpleNamespace(client_order_id="target-1")
    strategy._target_order_id = "target-1"
    strategy._signed_qty = Decimal("0.01")
    monkeypatch.setattr(strategy, "cancel_order", lambda order: None)
    monkeypatch.setattr(strategy, "close_all_positions", lambda instrument_id: close_calls.append(instrument_id))

    strategy.on_order_rejected(SimpleNamespace(client_order_id="stop-1"))
    strategy.on_order_rejected(SimpleNamespace(client_order_id="target-1"))

    assert close_calls == [INSTRUMENT]


def test_on_bar_warms_indicators_outside_session_and_force_flats_at_close(monkeypatch):
    strategy = make_strategy()
    calls = []
    monkeypatch.setattr(strategy, "cancel_all_orders", lambda instrument_id: calls.append("cancel"))
    monkeypatch.setattr(strategy, "close_all_positions", lambda instrument_id: calls.append("close"))
    start = int(datetime(2026, 9, 19, 10, 0, tzinfo=NY).timestamp() * 1_000_000_000)
    step = 5 * 60 * 1_000_000_000

    for index in range(21):
        strategy.on_bar(_Bar(start + index * step, 100.0))
    assert strategy._bb.initialized
    assert strategy._atr.initialized

    strategy._pending_setup = SimpleNamespace()
    close_ts = int(datetime(2026, 9, 21, 16, 0, tzinfo=NY).timestamp() * 1_000_000_000)
    strategy.on_bar(_Bar(close_ts, 100.0))
    assert strategy._pending_setup is None
    assert calls[-2:] == ["cancel", "close"]


class _Price:
    def __init__(self, value: float):
        self.value = value

    def as_double(self) -> float:
        return self.value


class _Bar:
    bar_type = BAR_TYPE

    def __init__(self, ts_event: int, close: float):
        self.ts_event = ts_event
        self.ts_init = ts_event
        self.high = _Price(close + 1.0)
        self.low = _Price(close - 1.0)
        self.close = _Price(close)


def test_on_bar_uses_only_oi_before_current_bar_for_reentry(monkeypatch):
    strategy = make_strategy()
    submitted = []
    _install_order_fakes(monkeypatch, strategy)
    strategy._instrument = lambda: SimpleNamespace(
        make_price=lambda value: Decimal(str(value)),
        make_qty=lambda value: Decimal(str(value)),
    )
    monkeypatch.setattr(strategy, "submit_order", submitted.append)
    start = int(datetime(2026, 9, 17, 9, 30, tzinfo=NY).timestamp() * 1_000_000_000)
    step = 5 * 60 * 1_000_000_000

    for index in range(21):
        strategy.on_bar(_Bar(start + index * step, 100.0))
    for index, value in enumerate((100.0, 101.0, 102.0, 103.0)):
        point = OpenInterestPoint(
            ts_event=start + 21 * step - 1_000_000 + index,
            ts_init=start + 21 * step - 1_000_000 + index,
            instrument_id=INSTRUMENT,
            open_interest=value,
        )
        strategy.on_data(wrap_open_interest(point))

    strategy.on_bar(_Bar(start + 21 * step, 90.0))
    assert strategy._pending_setup is not None

    point = OpenInterestPoint(
        ts_event=start + 22 * step - 1_000_000,
        ts_init=start + 22 * step - 1_000_000,
        instrument_id=INSTRUMENT,
        open_interest=102.0,
    )
    strategy.on_data(wrap_open_interest(point))
    same_bar = OpenInterestPoint(
        ts_event=start + 22 * step,
        ts_init=start + 22 * step,
        instrument_id=INSTRUMENT,
        open_interest=1.0,
    )
    strategy.on_data(wrap_open_interest(same_bar))
    strategy.on_bar(_Bar(start + 22 * step, 100.0))

    assert [order.kind for order in submitted] == ["entry"]


def test_a_atr_tp_long_target_is_fill_plus_mult_times_atr(monkeypatch):
    strategy = make_strategy(tp_mode="atr", tp_atr_mult=3.0)
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

    assert submitted[1].price == Decimal("106.0")
    assert submitted[0].trigger_price == Decimal("95.0")


def test_a_atr_tp_short_target_is_fill_minus_mult_times_atr(monkeypatch):
    strategy = make_strategy(tp_mode="atr", tp_atr_mult=3.0)
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

    assert submitted[1].price == Decimal("94.0")
    assert submitted[0].trigger_price == Decimal("105.0")


def test_a_rejects_invalid_tp_mode():
    with pytest.raises(ValueError, match="tp_mode"):
        make_strategy(tp_mode="invalid")


def test_a_hold_across_sessions_skips_force_flat_at_close(monkeypatch):
    strategy = make_strategy(hold_across_sessions=True)
    calls = []
    monkeypatch.setattr(strategy, "cancel_all_orders", lambda instrument_id: calls.append("cancel"))
    monkeypatch.setattr(strategy, "close_all_positions", lambda instrument_id: calls.append("close"))
    strategy._pending_setup = SimpleNamespace()
    close_ts = int(datetime(2026, 9, 21, 16, 0, tzinfo=NY).timestamp() * 1_000_000_000)

    strategy.on_bar(_Bar(close_ts, 100.0))

    assert strategy._pending_setup is not None
    assert calls == []
