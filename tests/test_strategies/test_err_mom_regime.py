from decimal import Decimal

from nautilus_trader.model import BarType, InstrumentId

from sngw_trader.indicators.bar_aggregator import CompletedBar
from sngw_trader.strategies.err_momentum_regime import (
    ErrMomentumRegime,
    ErrMomentumRegimeConfig,
    apply_entry_block,
)


def test_entry_block_rules():
    assert apply_entry_block(0, 1, True) == 0  # flat + blocked -> stay flat
    assert apply_entry_block(1, 1, True) == 1  # long held, target long -> hold
    assert apply_entry_block(1, -1, True) == 0  # flip blocked -> close only
    assert apply_entry_block(-1, 1, True) == 0
    assert apply_entry_block(1, -1, False) == -1  # not blocked -> normal
    assert apply_entry_block(0, -1, False) == -1
    assert apply_entry_block(1, 0, False) == 0


def test_config_builds():
    config = ErrMomentumRegimeConfig(
        instrument_id=InstrumentId.from_str("BTC-USDT-SWAP.OKX"),
        bar_type=BarType.from_str("BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL"),
        trade_size=Decimal("0.01"),
    )
    strategy = ErrMomentumRegime(config=config)
    assert strategy.config.w_f == 5
    assert strategy.config.w_e == 5
    assert strategy.config.momentum_window == 48
    assert strategy.config.vol_lookback == 120
    assert strategy.config.reentry_cooldown_bars == 1
    assert strategy._vol._ppy == 2190  # 4h returns annualization
    assert strategy._daily._bucket_ns == 14_400 * 1_000_000_000  # 4h aggregation
    assert strategy._ermom.warmup_bars == 58  # spec: warm-up 58 x 4h bars
    assert strategy.config.instrument_id.value.endswith(".OKX")


def _make_strategy():
    config = ErrMomentumRegimeConfig(
        instrument_id=InstrumentId.from_str("BTC-USDT-SWAP.OKX"),
        bar_type=BarType.from_str("BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL"),
        trade_size=Decimal("0.01"),
    )
    return ErrMomentumRegime(config=config)


def _warm_atr(s, rng=2.0):
    for _ in range(s.config.atr_period + 1):
        s._atr.update(100.0, 100.0 + rng / 2, 100.0 - rng / 2, 100.0)
    assert s._atr.value == rng  # constant-TR (rng) ATR converges to rng


def test_trailing_long_ratchet_and_hit():
    s = _make_strategy()
    _warm_atr(s)  # ATR=2.0, mult 3.0 -> stop distance 6.0
    s._pending_atr = s._atr.value
    s._on_fill(1, 100.0)
    assert s._stop_price == 94.0
    assert s._high_water == 100.0
    # new high ratchets stop up
    assert s._trailing(1, high=103.0, low=99.0) is False
    assert s._high_water == 103.0
    assert s._stop_price == 97.0
    # retracement never lowers the stop (monotonic)
    assert s._trailing(1, high=101.0, low=99.5) is False
    assert s._stop_price == 97.0
    assert s._high_water == 103.0
    # 1m bar pierces the stop (a 4h close-based check would see close 99.8 > 97 and miss it)
    assert s._trailing(1, high=99.8, low=96.9) is True
    # hit bar must not touch watermark/stop
    assert s._high_water == 103.0
    assert s._stop_price == 97.0


def test_trailing_short_mirror():
    s = _make_strategy()
    _warm_atr(s)
    s._pending_atr = s._atr.value
    s._on_fill(-1, 100.0)
    assert s._stop_price == 106.0
    assert s._low_water == 100.0
    assert s._trailing(-1, high=99.5, low=97.0) is False
    assert s._low_water == 97.0
    assert s._stop_price == 103.0  # 97 + 6
    assert s._trailing(-1, high=99.0, low=98.5) is False
    assert s._stop_price == 103.0  # monotonic
    assert s._trailing(-1, high=103.1, low=99.0) is True
    assert s._stop_price == 103.0


def test_partial_fill_does_not_reseed_watermark():
    s = _make_strategy()
    _warm_atr(s)
    s._pending_atr = s._atr.value
    s._on_fill(1, 100.0)
    s._trailing(1, high=103.0, low=99.0)  # watermark 103, stop 97
    s._on_fill(1, 100.5)  # second fill event: _pending_atr is None -> no reseed
    assert s._high_water == 103.0
    assert s._stop_price == 97.0


def test_close_fill_resets_stop_state():
    s = _make_strategy()
    _warm_atr(s)
    s._pending_atr = s._atr.value
    s._on_fill(1, 100.0)
    s._on_fill(0, 99.0)
    assert s._stop_price is None
    assert s._high_water is None
    assert s._low_water is None


_4H_BAR = CompletedBar(ts_open_ns=0, ts_close_ns=1, open=1.0, high=1.0, low=1.0, close=1.0)


def _feed_vol_high(s):
    px = 100.0
    for i in range(s.config.vol_lookback + 1):
        px *= 1.02 if i % 2 else 0.98
        s._vol.update(px)
    assert s._vol.value > s.config.vol_threshold


def test_cooldown_blocks_then_releases():
    s = _make_strategy()
    s._ermom._value = 0.5  # regime long
    s._cooldown_bars = 1
    assert s._target_4h(0) == 0  # blocked
    s._cooldown_bars = 0
    assert s._target_4h(0) == 1  # released


def test_vol_block_still_applies_without_cooldown():
    s = _make_strategy()
    s._ermom._value = 0.5
    _feed_vol_high(s)
    assert s._target_4h(0) == 0
    assert s._target_4h(1) == 1  # blocked only blocks NEW entries


def test_cooldown_decrements_per_4h_close():
    s = _make_strategy()
    s._ermom._value = 0.5
    s._cooldown_bars = 2
    s._tick_4h()
    assert s._cooldown_bars == 1
    assert s._target_4h(0) == 0
    s._tick_4h()
    assert s._cooldown_bars == 0
    assert s._target_4h(0) == 1