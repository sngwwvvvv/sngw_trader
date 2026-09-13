"""4H EMA crossover with optional UTC-daily volatility targeting."""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model import Bar, BarType, InstrumentId
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.trading import Strategy

from sngw_trader.indicators.bar_aggregator import BarAggregator
from sngw_trader.indicators.risk_metrics import Ema
from sngw_trader.indicators.vol_targeting import VolTargetConfig, VolTargetSizer

DAY_NS = 86_400 * 1_000_000_000


def target_direction(fast: float, slow: float) -> int:
    if fast > slow:
        return 1
    if fast < slow:
        return -1
    return 0


def make_order_qty(instrument, qty: Decimal):
    try:
        rounded = instrument.make_qty(qty)
    except ValueError:
        return None
    return None if rounded == 0 else rounded


class EMACrossConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    fast_ema_period: int = 20
    slow_ema_period: int = 50
    sizing_mode: str = "vol_target"
    size_target_vol: float = 0.20
    size_half_life: int = 20
    size_min_scale: float = 0.0
    size_max_scale: float = 3.0
    size_rebalance_band: float = 0.10
    close_positions_on_stop: bool = True


class EMACross(Strategy):
    def __init__(self, config: EMACrossConfig) -> None:
        super().__init__(config)
        self._fourh = BarAggregator(14_400)
        self._utc_day = BarAggregator(86_400)
        self._ema_fast = Ema(config.fast_ema_period)
        self._ema_slow = Ema(config.slow_ema_period)
        self._sizer = VolTargetSizer(
            VolTargetConfig(
                target_vol=config.size_target_vol,
                half_life=config.size_half_life,
                min_scale=config.size_min_scale,
                max_scale=config.size_max_scale,
                rebalance_band=config.size_rebalance_band,
                periods_per_year=365,
                mode=config.sizing_mode,
            )
        )
        self._direction = 0
        self._daily_updates = 0
        self._sized_this_bar = False

    def on_start(self) -> None:
        self.subscribe_bars(self.config.bar_type)

    def on_stop(self) -> None:
        if self.config.close_positions_on_stop:
            self.close_all_positions(self.config.instrument_id)

    def on_bar(self, bar: Bar) -> None:
        self._sized_this_bar = False
        ts = int(bar.ts_init)
        o, h, l, c = (x.as_double() for x in (bar.open, bar.high, bar.low, bar.close))

        utc_day = self._utc_day.update(ts, o, h, l, c)
        if utc_day is not None and utc_day.ts_open_ns % DAY_NS == 0:
            self._sizer.update(utc_day.close)
            self._daily_updates += 1

        fourh = self._fourh.update(ts, o, h, l, c)
        if fourh is not None:
            fast = self._ema_fast.update(fourh.close)
            slow = self._ema_slow.update(fourh.close)
            if fast is not None and slow is not None:
                signal = target_direction(fast, slow)
                if signal:
                    self._direction = signal
                if self._daily_updates >= 61 and not self._sized_this_bar:
                    self._sync_size(self._direction, allow_resize=False)

        if utc_day is not None and utc_day.ts_open_ns % DAY_NS == 0:
            if self._daily_updates >= 61 and not self._sized_this_bar:
                self._sync_size(self._direction, allow_resize=True)

    def _signed_qty(self) -> Decimal:
        return Decimal(str(self.portfolio.net_position(self.config.instrument_id)))

    def _sync_size(self, direction: int, *, allow_resize: bool) -> None:
        desired = self._sizer.desired_qty(direction, self.config.trade_size)
        if desired is None:
            return
        current = self._signed_qty()
        same_sign = current != 0 and desired != 0 and (current > 0) == (desired > 0)
        if same_sign and not allow_resize:
            return
        if not self._sizer.should_rebalance(current, desired):
            return
        if current != 0 and (desired == 0 or (current > 0) != (desired > 0)):
            self.close_all_positions(self.config.instrument_id)
            current = Decimal("0")
            if desired == 0:
                self._sized_this_bar = True
                return
        if current == 0 and desired != 0:
            self._submit(OrderSide.BUY if desired > 0 else OrderSide.SELL, abs(desired))
            return
        delta = desired - current
        if delta:
            self._submit(OrderSide.BUY if delta > 0 else OrderSide.SELL, abs(delta))

    def _submit(self, side: OrderSide, qty: Decimal) -> None:
        instrument = self.cache.instrument(self.config.instrument_id)
        if instrument is None:
            self.log.error(f"Instrument not in cache: {self.config.instrument_id}")
            return
        q = make_order_qty(instrument, qty)
        if q is None:
            return
        self._sized_this_bar = True
        self.submit_order(self.order_factory.market(self.config.instrument_id, side, q))
