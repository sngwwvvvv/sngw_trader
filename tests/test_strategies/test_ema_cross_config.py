from decimal import Decimal

from nautilus_trader.model import BarType, InstrumentId

from sngw_trader.strategies import EMACross, EMACrossConfig
from sngw_trader.strategies.example.ema_cross import make_order_qty, target_direction


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


def test_ema_cross_uses_4h_signal_and_daily_vol_targeting() -> None:
    config = EMACrossConfig(
        instrument_id=InstrumentId.from_str("BTC-USDT-SWAP.OKX"),
        bar_type=BarType.from_str("BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL"),
        trade_size=Decimal("0.01"),
        fast_ema_period=20,
        slow_ema_period=50,
        sizing_mode="vol_target",
    )
    strategy = EMACross(config=config)

    assert strategy._fourh._bucket_ns == 14_400 * 1_000_000_000
    assert strategy._utc_day._bucket_ns == 86_400 * 1_000_000_000
    assert strategy._sizer.scale() is None


def test_target_direction_is_long_short_always_in() -> None:
    assert target_direction(101.0, 100.0) == 1
    assert target_direction(99.0, 100.0) == -1
    assert target_direction(100.0, 100.0) == 0


def test_sub_increment_resize_is_skipped() -> None:
    class Instrument:
        def make_qty(self, qty):
            raise ValueError("rounded to zero")

    assert make_order_qty(Instrument(), Decimal("0.0034")) is None
