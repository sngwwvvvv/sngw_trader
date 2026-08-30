"""EMA crossover strategy.

This module contains strategy logic only.
Do not import TradingNode or OKX factories here.
"""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.model import Bar, BarType, InstrumentId
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.trading import Strategy


class EMACrossConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    fast_ema_period: int = 10
    slow_ema_period: int = 20
    close_positions_on_stop: bool = True


class EMACross(Strategy):
    def __init__(self, config: EMACrossConfig) -> None:
        super().__init__(config)
        self.fast_ema = ExponentialMovingAverage(config.fast_ema_period)
        self.slow_ema = ExponentialMovingAverage(config.slow_ema_period)

    def on_start(self) -> None:
        self.register_indicator_for_bars(self.config.bar_type, self.fast_ema)
        self.register_indicator_for_bars(self.config.bar_type, self.slow_ema)
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        if not self.indicators_initialized():
            return

        instrument_id = self.config.instrument_id
        bullish = self.fast_ema.value >= self.slow_ema.value

        if bullish:
            if self.portfolio.is_net_flat(instrument_id):
                self._enter(OrderSide.BUY)
            elif self.portfolio.is_net_short(instrument_id):
                self.close_all_positions(instrument_id)
                self._enter(OrderSide.BUY)
            return

        if self.portfolio.is_net_flat(instrument_id):
            self._enter(OrderSide.SELL)
        elif self.portfolio.is_net_long(instrument_id):
            self.close_all_positions(instrument_id)
            self._enter(OrderSide.SELL)

    def on_stop(self) -> None:
        if self.config.close_positions_on_stop:
            self.close_all_positions(self.config.instrument_id)

    def _enter(self, side: OrderSide) -> None:
        instrument = self.cache.instrument(self.config.instrument_id)
        if instrument is None:
            self.log.error(f"Instrument not in cache: {self.config.instrument_id}")
            return

        order = self.order_factory.market(
            self.config.instrument_id,
            side,
            instrument.make_qty(self.config.trade_size),
        )
        self.submit_order(order)
