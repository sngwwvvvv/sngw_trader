"""Three consecutive red daily bars, long next open, exit after hold_bars closes.

Spec: docs/superpowers/specs/2026-09-13-three-red-days-design.md
Strategy logic only. No runner assembly, no exchange I/O.
"""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model import BarType, InstrumentId
from nautilus_trader.trading import Strategy

from sngw_trader.indicators.bar_aggregator import NS

DAY_NS = 86_400 * NS
SOURCE_NS = 60 * NS


def is_red(open_px: float, close_px: float) -> bool:
    return close_px < open_px


def next_streak(streak: int, red: bool) -> int:
    return streak + 1 if red else 0


def utc_bucket(ts_close_ns: int) -> int:
    return (ts_close_ns - SOURCE_NS) // DAY_NS


def is_last_minute(ts_close_ns: int) -> bool:
    return ts_close_ns % DAY_NS == 0


def exit_bucket(entry_bucket: int, hold_bars: int) -> int:
    return entry_bucket + hold_bars - 1


class ThreeRedDaysConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    streak_len: int = 3
    hold_bars: int = 3
    close_positions_on_stop: bool = True


class ThreeRedDays(Strategy):
    """Stub filled in Task 2."""

    def __init__(self, config: ThreeRedDaysConfig) -> None:
        super().__init__(config)
