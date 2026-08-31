from decimal import Decimal

from nautilus_trader.model import BarType, InstrumentId

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