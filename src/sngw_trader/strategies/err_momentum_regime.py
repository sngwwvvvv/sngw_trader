"""Spec A (4h): ERMOM regime IS the position, chandelier trailing stop on 1m stream.

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
from sngw_trader.indicators.risk_metrics import DailyAtr, is_stop_hit, stop_price
from sngw_trader.indicators.vol_targeting import VolTargetConfig, VolTargetSizer


class ErrMomentumRegimeConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    w_f: int = 5
    w_e: int = 5
    momentum_window: int = 48
    theta: float = 0.0
    allow_short: bool = True
    risk_stop_enabled: bool = True
    atr_period: int = 14
    atr_mult: float = 3.0
    sizing_mode: str = "vol_target"
    size_target_vol: float = 0.20
    size_half_life: int = 20
    size_min_scale: float = 0.0
    size_max_scale: float = 3.0
    size_rebalance_band: float = 0.10
    reentry_cooldown_bars: int = 1
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
        self._daily = BarAggregator(14_400)
        self._utc_day = BarAggregator(86_400)
        self._ermom = ErrorAdjustedMomentum(config.w_f, config.w_e, config.momentum_window)
        self._atr = DailyAtr(config.atr_period)
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
        self._stop_price: float | None = None
        self._pending_atr: float | None = None
        self._high_water: float | None = None
        self._low_water: float | None = None
        self._cooldown_bars = 0
        self._sized_this_bar = False

    def on_start(self) -> None:
        self.subscribe_bars(self.config.bar_type)

    def on_stop(self) -> None:
        if self.config.close_positions_on_stop:
            self.close_all_positions(self.config.instrument_id)

    def on_bar(self, bar: Bar) -> None:
        self._sized_this_bar = False
        exited = False
        side = self._current_side()
        if self._trailing(side, bar.high.as_double(), bar.low.as_double()):
            self.close_all_positions(self.config.instrument_id)
            self._reset_stop()
            self._cooldown_bars = self.config.reentry_cooldown_bars
            exited = True
            self._sized_this_bar = True
        ts = int(bar.ts_init)
        o, h, l, c = bar.open.as_double(), bar.high.as_double(), bar.low.as_double(), bar.close.as_double()
        utc_day = self._utc_day.update(ts, o, h, l, c)
        if utc_day is not None:
            self._sizer.update(utc_day.close)
        fourh = self._daily.update(ts, o, h, l, c)
        if fourh is not None:
            self._ermom.update(fourh.close)
            self._atr.update(fourh.open, fourh.high, fourh.low, fourh.close)
            if not exited:
                self._on_bar_4h(fourh)
        if utc_day is not None and not self._sized_this_bar and not exited:
            hold = self._current_side()
            if hold != 0:
                self._sync_size(hold, allow_new=False, allow_resize=True)

    def on_event(self, event) -> None:
        if not isinstance(event, OrderFilled) or event.instrument_id != self.config.instrument_id:
            return
        self._on_fill(self._current_side(), event.last_px.as_double())

    def _current_side(self) -> int:
        if self.portfolio.is_net_long(self.config.instrument_id):
            return 1
        if self.portfolio.is_net_short(self.config.instrument_id):
            return -1
        return 0

    def _signed_qty(self) -> Decimal:
        return Decimal(str(self.portfolio.net_position(self.config.instrument_id)))

    def _reset_stop(self) -> None:
        self._stop_price = None
        self._high_water = None
        self._low_water = None

    def _on_fill(self, side: int, fill_px: float) -> None:
        """Seed stop/watermark only on the 0->+/-1 transition (partial fills skip)."""
        if side == 0:
            self._reset_stop()
        elif self._pending_atr is not None:
            self._stop_price = stop_price(side, fill_px, self._pending_atr, self.config.atr_mult)
            if side > 0:
                self._high_water = fill_px
            else:
                self._low_water = fill_px
            self._pending_atr = None

    def _trailing(self, side: int, high: float, low: float) -> bool:
        """1m track: hit-check FIRST (vs prior stop), then watermark, then stop recalc."""
        stop = self._stop_price
        if not self.config.risk_stop_enabled or side == 0 or stop is None:
            return False
        if is_stop_hit(side, low if side > 0 else high, stop):
            return True
        atr = self._atr.value
        if atr is None:
            return False
        if side > 0:
            self._high_water = max(self._high_water, high)
            self._stop_price = max(stop, self._high_water - self.config.atr_mult * atr)
        else:
            self._low_water = min(self._low_water, low)
            self._stop_price = min(stop, self._low_water + self.config.atr_mult * atr)
        return False

    def _tick_4h(self) -> None:
        if self._cooldown_bars > 0:
            self._cooldown_bars -= 1

    def _target_4h(self, current: int) -> int:
        entry_blocked = self._cooldown_bars > 0
        target = regime_target(self._ermom.value, self.config.theta)
        if not self.config.allow_short and target < 0:
            target = 0
        return apply_entry_block(current, target, entry_blocked)

    def _on_bar_4h(self, daily: CompletedBar) -> None:
        self._tick_4h()
        current = self._current_side()
        target = self._target_4h(current)
        self._sync_size(target, allow_new=True, allow_resize=False)

    def _sync_size(self, direction: int, *, allow_new: bool, allow_resize: bool) -> None:
        desired = self._sizer.desired_qty(direction, self.config.trade_size)
        current = self._signed_qty()
        if desired is None:
            return
        if current == 0 and desired != 0 and not allow_new:
            return
        same_sign = current != 0 and desired != 0 and (current > 0) == (desired > 0)
        if same_sign and not allow_resize:
            return
        if not self._sizer.should_rebalance(current, desired):
            return
        if current != 0 and (desired == 0 or (current > 0) != (desired > 0)):
            self.close_all_positions(self.config.instrument_id)
            self._reset_stop()
            self._sized_this_bar = True
            current = Decimal("0")
            if desired == 0:
                return
        if current == 0 and desired != 0:
            self._submit(OrderSide.BUY if desired > 0 else OrderSide.SELL, abs(desired), seed_stop=True)
            return
        delta = desired - current
        if delta == 0:
            return
        self._submit(OrderSide.BUY if delta > 0 else OrderSide.SELL, abs(delta), seed_stop=False)

    def _submit(self, side: OrderSide, qty: Decimal, seed_stop: bool) -> None:
        instrument = self.cache.instrument(self.config.instrument_id)
        if instrument is None:
            self.log.error(f"Instrument not in cache: {self.config.instrument_id}")
            return
        q = instrument.make_qty(qty)
        if q == 0:
            return
        if seed_stop:
            self._pending_atr = self._atr.value
        self._sized_this_bar = True
        self.submit_order(
            self.order_factory.market(
                self.config.instrument_id,
                side,
                q,
            )
        )
