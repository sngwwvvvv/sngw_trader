from decimal import Decimal

from nautilus_trader.model import BarType, InstrumentId

from sngw_trader.indicators.bar_aggregator import BarAggregator
from sngw_trader.strategies.three_red_days import (
    DAY_NS,
    SOURCE_NS,
    ThreeRedDaysConfig,
    exit_bucket,
    is_last_minute,
    is_red,
    next_streak,
    utc_bucket,
)

INSTRUMENT = InstrumentId.from_str("BTC-USDT-SWAP.OKX")
BAR_TYPE = BarType.from_str("BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL")


def test_is_red_strict_close_lt_open():
    assert is_red(101.0, 100.0) is True
    assert is_red(100.0, 100.0) is False
    assert is_red(100.0, 101.0) is False


def test_next_streak_increments_and_resets():
    assert next_streak(0, True) == 1
    assert next_streak(2, True) == 3
    assert next_streak(3, False) == 0
    assert next_streak(1, False) == 0


def test_utc_bucket_matches_bar_aggregator():
    agg = BarAggregator(86_400)
    last_d0 = DAY_NS  # close at UTC midnight ending day 0
    first_d1 = DAY_NS + SOURCE_NS
    assert utc_bucket(last_d0) == 0
    assert utc_bucket(first_d1) == 1
    assert utc_bucket(last_d0) == (last_d0 - SOURCE_NS) // DAY_NS
    # aggregator: last minute of day 0 stays in bucket 0; first minute of day 1 completes it
    assert agg.update(last_d0, 1, 1, 1, 1) is None
    done = agg.update(first_d1, 2, 2, 2, 2)
    assert done is not None
    assert utc_bucket(done.ts_close_ns) == 0


def test_is_last_minute_only_at_utc_midnight():
    assert is_last_minute(DAY_NS) is True
    assert is_last_minute(0) is True  # unix epoch midnight
    assert is_last_minute(DAY_NS + SOURCE_NS) is False
    assert is_last_minute(DAY_NS - SOURCE_NS) is False


def test_exit_bucket_is_entry_plus_hold_minus_one():
    assert exit_bucket(4, 3) == 6
    assert exit_bucket(0, 3) == 2


def test_config_defaults():
    c = ThreeRedDaysConfig(
        instrument_id=INSTRUMENT,
        bar_type=BAR_TYPE,
        trade_size=Decimal("0.01"),
    )
    assert c.streak_len == 3
    assert c.hold_bars == 3
    assert c.close_positions_on_stop is True
    assert c.instrument_id.value.endswith(".OKX")


from sngw_trader.strategies.three_red_days import ThreeRedDays, ThreeRedDaysConfig


def _config(**overrides) -> ThreeRedDaysConfig:
    base = dict(
        instrument_id=INSTRUMENT,
        bar_type=BAR_TYPE,
        trade_size=Decimal("0.01"),
    )
    base.update(overrides)
    return ThreeRedDaysConfig(**base)


def last_minute_ts(day: int) -> int:
    return (day + 1) * DAY_NS


def first_minute_ts(day: int) -> int:
    return day * DAY_NS + SOURCE_NS


def _red():
    return 101.0, 100.0  # open, close


def _green():
    return 100.0, 101.0


def _doji():
    return 100.0, 100.0


def _complete(s: ThreeRedDays, day: int, oc: tuple[float, float]):
    """Day `day` completes on the first minute of day+1."""
    o, c = oc
    now = first_minute_ts(day + 1)
    completed_close = last_minute_ts(day)
    return s._on_completed_day(o, c, now, completed_close)


def test_three_reds_buy_next_open():
    s = ThreeRedDays(config=_config())
    assert _complete(s, 0, _red()) == []
    assert _complete(s, 1, _red()) == []
    intents = _complete(s, 2, _red())
    assert len(intents) == 1
    assert intents[0].side == 1
    assert intents[0].qty == Decimal("0.01")
    assert s._in_position is True
    assert s._entry_bucket == utc_bucket(first_minute_ts(3)) == 3


def test_exit_on_third_holding_day_last_minute():
    s = ThreeRedDays(config=_config())
    _complete(s, 0, _red())
    _complete(s, 1, _red())
    _complete(s, 2, _red())
    assert s._on_last_minute(last_minute_ts(3)) == []
    assert s._on_last_minute(last_minute_ts(4)) == []
    intents = s._on_last_minute(last_minute_ts(5))
    assert len(intents) == 1
    assert intents[0].side == -1
    assert intents[0].qty == Decimal("0.01")
    assert s._in_position is False
    assert s._entry_bucket is None


def test_fallback_exit_on_completed_exit_day_no_reentry():
    s = ThreeRedDays(config=_config())
    _complete(s, 0, _red())
    _complete(s, 1, _red())
    _complete(s, 2, _red())
    # skip last minutes; complete holding days 3,4,5 (exit_bucket=5)
    assert _complete(s, 3, _red()) == []
    assert _complete(s, 4, _red()) == []
    intents = _complete(s, 5, _red())
    assert len(intents) == 1
    assert intents[0].side == -1
    assert s._in_position is False
    # streak is 6, not 3; and same-call reentry is forbidden anyway
    assert all(i.side != 1 for i in intents)


def test_fourth_red_does_not_buy_again():
    s = ThreeRedDays(config=_config())
    _complete(s, 0, _red())
    _complete(s, 1, _red())
    _complete(s, 2, _red())
    assert _complete(s, 3, _red()) == []
    assert s._streak == 4
    assert s._in_position is True


def test_doji_and_green_reset_streak():
    s = ThreeRedDays(config=_config())
    _complete(s, 0, _red())
    _complete(s, 1, _red())
    assert _complete(s, 2, _doji()) == []
    assert s._streak == 0
    assert _complete(s, 3, _red()) == []
    assert _complete(s, 4, _red()) == []
    assert s._in_position is False
    s2 = ThreeRedDays(config=_config())
    _complete(s2, 0, _red())
    _complete(s2, 1, _red())
    assert _complete(s2, 2, _green()) == []
    assert s2._streak == 0


def test_no_reentry_until_fresh_three_after_break():
    s = ThreeRedDays(config=_config())
    _complete(s, 0, _red())
    _complete(s, 1, _red())
    _complete(s, 2, _red())
    s._on_last_minute(last_minute_ts(3))
    s._on_last_minute(last_minute_ts(4))
    s._on_last_minute(last_minute_ts(5))
    # complete exit day (red) after close: streak continues, not a fresh 3
    assert _complete(s, 5, _red()) == []
    assert s._in_position is False
    assert _complete(s, 6, _green()) == []
    assert s._streak == 0
    assert _complete(s, 7, _red()) == []
    assert _complete(s, 8, _red()) == []
    intents = _complete(s, 9, _red())
    assert len(intents) == 1 and intents[0].side == 1


def test_ignore_signal_while_in_position():
    s = ThreeRedDays(config=_config())
    _complete(s, 0, _red())
    _complete(s, 1, _red())
    _complete(s, 2, _red())
    for d in range(3, 6):
        intents = _complete(s, d, _red())
        assert all(i.side != 1 for i in intents)
    assert s._in_position is False


def test_on_1m_routes_last_minute_then_completed_day():
    s = ThreeRedDays(config=_config())
    # seed aggregator + three red days via 1m: last min day d + first min day d+1
    for d in range(3):
        o, c = _red()
        assert s._on_1m(last_minute_ts(d), o, o, c, c) == []
        intents = s._on_1m(first_minute_ts(d + 1), o, o, c, c)
        if d < 2:
            assert intents == []
        else:
            assert len(intents) == 1 and intents[0].side == 1
    # last minutes of hold days 3,4,5
    assert s._on_1m(last_minute_ts(3), 1, 1, 1, 1) == []
    assert s._on_1m(last_minute_ts(4), 1, 1, 1, 1) == []
    exit_intents = s._on_1m(last_minute_ts(5), 1, 1, 1, 1)
    assert len(exit_intents) == 1 and exit_intents[0].side == -1


def test_entry_does_not_inflate_signed_qty_before_fill():
    s = ThreeRedDays(config=_config())
    _complete(s, 0, _red())
    _complete(s, 1, _red())
    assert _complete(s, 2, _red())[0].side == 1
    # _signed_qty reconciles from fills (on_event) only, never optimistically
    assert s._signed_qty == Decimal("0")
    # simulate the fill reconciliation on_event performs
    s._signed_qty = Decimal("0.01")
    exit_intents = s._on_last_minute(last_minute_ts(5))
    assert exit_intents[0].qty == Decimal("0.01")


def test_exit_intent_does_not_zero_signed_qty_before_fill():
    s = ThreeRedDays(config=_config())
    _complete(s, 0, _red())
    _complete(s, 1, _red())
    assert _complete(s, 2, _red())[0].side == 1
    s._signed_qty = Decimal("0.01")  # simulate on_event buy-fill reconciliation
    exit_intents = s._on_last_minute(last_minute_ts(5))
    assert len(exit_intents) == 1
    assert exit_intents[0].qty == Decimal("0.01")
    assert s._in_position is False
    # intent must NOT zero _signed_qty; the sell fill reconciles it
    assert s._signed_qty == Decimal("0.01")
    s._signed_qty += Decimal("-0.01")  # simulate on_event sell-fill reconciliation
    assert s._signed_qty == Decimal("0")
