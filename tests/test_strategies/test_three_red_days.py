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
