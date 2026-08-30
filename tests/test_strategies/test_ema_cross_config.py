from decimal import Decimal

from nautilus_trader.model import BarType, InstrumentId

from sngw_trader.strategies import EMACross, EMACrossConfig


def test_strategy_config_builds() -> None:
    config = EMACrossConfig(
        instrument_id=InstrumentId.from_str("BTC-USDT-SWAP.OKX"),
        bar_type=BarType.from_str("BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL"),
        trade_size=Decimal("0.01"),
        fast_ema_period=10,
        slow_ema_period=20,
    )
    strategy = EMACross(config=config)
    assert strategy.config.instrument_id.value.endswith(".OKX")
    assert strategy.config.fast_ema_period < strategy.config.slow_ema_period
