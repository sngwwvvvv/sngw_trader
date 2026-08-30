"""Spec A: daily ERMOM regime IS the position.

Strategy logic only. Do not import TradingNode or OKX factories here.
"""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model import Bar, BarType, InstrumentId
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.trading import Strategy

from sngw_trader.indicators.bar_aggregator import BarAggregator, CompletedBar
from sngw_trader.indicators.err_momentum import ErrorAdjustedMomentum, regime_target
from sngw_trader.indicators.risk_metrics import (
    DailyAtr,
    RealizedVol,
    is_stop_hit,
    stop_price,
)


class ErrMomentumRegimeConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    w_f: int = 10
    w_e: int = 10
    momentum_window: int = 200
    theta: float = 0.0
    risk_stop_enabled: bool = True
    atr_period: int = 14
    atr_mult: float = 3.0
    vol_filter_enabled: bool = True
    vol_lookback: int = 20
    vol_threshold: float = 0.80
    close_positions_on_stop: bool = True


def apply_entry_block(current: int, target: int, entry_blocked: bool) -> int:
    """Kill switch blocks NEW positions only: flip -> close to flat, hold stays."""
    if not entry_blocked:
        return target
    if current == 0:
        return 0
    if target == 0 or (target > 0) != (current > 0):
        return 0
    return current


class ErrMomentumRegime(Strategy):
    def __init__(self, config: ErrMomentumRegimeConfig) -> None:
        super().__init__(config)
        self._daily = BarAggregator(86_400)
        self._ermom = ErrorAdjustedMomentum(config.w_f, config.w_e, config.momentum_window)
        self._atr = DailyAtr(config.atr_period)
        self._vol = RealizedVol(config.vol_lookback)
        self._stop: float | None = None
        self._pending_atr: float | None = None

    def on_start(self) -> None:
        self.subscribe_bars(self.config.bar_type)

    def on_stop(self) -> None:
        if self.config.close_positions_on_stop:
            self.close_all_positions(self.config.instrument_id)

    def on_bar(self, bar: Bar) -> None:
        daily = self._daily.update(
            int(bar.ts_init),
            bar.open.as_double(),
            bar.high.as_double(),
            bar.low.as_double(),
            bar.close.as_double(),
        )
        if daily is None:
            return
        self._ermom.update(daily.close)
        self._atr.update(daily.open, daily.high, daily.low, daily.close)
        self._vol.update(daily.close)
        self._on_daily(daily)

    def on_event(self, event) -> None:
        if not isinstance(event, OrderFilled) or event.instrument_id != self.config.instrument_id:
            return
        side = self._current_side()
        if side == 0:
            self._stop = None
        elif self._pending_atr is not None:
            self._stop = stop_price(side, event.last_px.as_double(), self._pending_atr, self.config.atr_mult)

    def _current_side(self) -> int:
        if self.portfolio.is_net_long(self.config.instrument_id):
            return 1
        if self.portfolio.is_net_short(self.config.instrument_id):
            return -1
        return 0

    def _check_stop(self, price: float) -> bool:
        if self.config.risk_stop_enabled and is_stop_hit(self._current_side(), price, self._stop):
            self.close_all_positions(self.config.instrument_id)
            self._stop = None
            return True
        return False

    def _on_daily(self, daily: CompletedBar) -> None:
        current = self._current_side()
        if current != 0 and self._check_stop(daily.close):
            return  # re-entry next daily bar via normal regime rule
        entry_blocked = (
            self.config.vol_filter_enabled
            and self._vol.value is not None
            and self._vol.value > self.config.vol_threshold
        )
        target = apply_entry_block(current, regime_target(self._ermom.value, self.config.theta), entry_blocked)
        if current == target:
            return
        if current != 0:
            self.close_all_positions(self.config.instrument_id)
            self._stop = None
        if target != 0:
            self._submit(OrderSide.BUY if target > 0 else OrderSide.SELL)

    def _submit(self, side: OrderSide) -> None:
        instrument = self.cache.instrument(self.config.instrument_id)
        if instrument is None:
            self.log.error(f"Instrument not in cache: {self.config.instrument_id}")
            return
        self._pending_atr = self._atr.value
        self.submit_order(
            self.order_factory.market(
                self.config.instrument_id,
                side,
                instrument.make_qty(self.config.trade_size),
            )
        )