from sngw_trader.config import load_settings
from sngw_trader.runners.strategy_factory import build_strategy, source_bar_type
from sngw_trader.strategies.err_mom_ema30_entry import ErrMomEma30Entry
from sngw_trader.strategies.err_momentum_regime import ErrMomentumRegime


def test_build_dispatch_default_is_a():
    assert isinstance(build_strategy(load_settings()), ErrMomentumRegime)


def test_build_dispatch_b(monkeypatch):
    monkeypatch.setenv("STRATEGY", "err_mom_b")
    assert isinstance(build_strategy(load_settings()), ErrMomEma30Entry)


def test_source_bar_type_is_1_minute_external():
    assert source_bar_type("BTC-USDT-SWAP.OKX") == "BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL"