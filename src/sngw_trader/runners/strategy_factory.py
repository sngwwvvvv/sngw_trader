"""Attach the same strategy class to either node type."""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.model import BarType, InstrumentId

from sngw_trader.strategies import EMACross, EMACrossConfig


def build_ema_cross(
    instrument_id: str,
    bar_type: str,
    trade_size: str,
    fast_ema_period: int = 10,
    slow_ema_period: int = 20,
) -> EMACross:
    config = EMACrossConfig(
        instrument_id=InstrumentId.from_str(instrument_id),
        bar_type=BarType.from_str(bar_type),
        trade_size=Decimal(trade_size),
        fast_ema_period=fast_ema_period,
        slow_ema_period=slow_ema_period,
    )
    return EMACross(config=config)


def importable_ema_cross_config(
    instrument_id: str,
    bar_type: str,
    trade_size: str,
    fast_ema_period: int = 10,
    slow_ema_period: int = 20,
) -> dict[str, object]:
    return {
        "strategy_path": "sngw_trader.strategies.ema_cross:EMACross",
        "config_path": "sngw_trader.strategies.ema_cross:EMACrossConfig",
        "config": {
            "instrument_id": instrument_id,
            "bar_type": bar_type,
            "trade_size": trade_size,
            "fast_ema_period": fast_ema_period,
            "slow_ema_period": slow_ema_period,
        },
    }
