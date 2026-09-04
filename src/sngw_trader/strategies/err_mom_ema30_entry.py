"""Spec B: daily ERMOM regime permission + LTF (entry_tf_minutes) EMA20/50 band-reentry entry.

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
from sngw_trader.indicators.risk_metrics import DailyAtr, Ema, is_stop_hit, stop_price
from sngw_trader.indicators.vol_targeting import VolTargetConfig, VolTargetSizer


class ErrMomEma30EntryConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    w_f: int = 10
    w_e: int = 10
    momentum_window: int = 200
    theta: float = 0.0
    allow_short: bool = True
    ema_fast: int = 20
    ema_slow: int = 50
    n_pull: int = 24
    entry_tf_minutes: int = 30
    risk_stop_enabled: bool = True
    atr_period: int = 14
    atr_mult: float = 3.0
    sizing_mode: str = "vol_target"
    size_target_vol: float = 0.20
    size_half_life: int = 20
    size_min_scale: float = 0.0
    size_max_scale: float = 3.0
    size_rebalance_band: float = 0.10
    close_positions_on_stop: bool = True


class RibbonEntryMachine:
    """Mirror EXT->PULL->TRIG setup machine (spec 3.3/3.4).

    direction=+1 long, -1 short. One transition per completed 30m bar:
    after IDLE->EXT on bar k, EXT checks run from bar k+1 (N_ext >= 1).
    A bar that starts inside the band without having been in EXT can
    never become a trigger (spec 9 acceptance).
    """

    IDLE = "IDLE"
    EXT = "EXT"
    PULL = "PULL"
    TRIG = "TRIG"

    def __init__(self, direction: int, n_pull: int) -> None:
        self._d = direction
        self._n_pull = n_pull
        self.state = self.IDLE
        self._ext_bars = 0
        self._pull_bars = 0

    def reset(self) -> None:
        self.state = self.IDLE
        self._ext_bars = 0
        self._pull_bars = 0

    def update(
        self,
        o: float,
        h: float,
        l: float,
        c: float,
        ema_fast: float,
        ema_slow: float,
        regime_allows: bool,
    ) -> bool:
        """Returns True on the trigger bar (enter at next 30m open)."""
        if not regime_allows or self._d * (ema_fast - ema_slow) <= 0:
            self.reset()
            return False
        if self.state == self.IDLE:
            if self._d * (c - ema_fast) > 0:
                self.state = self.EXT
                self._ext_bars = 0
            return False
        if self.state == self.EXT:
            self._ext_bars += 1
            touch = l if self._d > 0 else h
            if self._d * (touch - ema_slow) >= 0 and self._d * (touch - ema_fast) <= 0:
                self.state = self.PULL
                self._pull_bars = 1
            elif self._d * (c - ema_slow) < 0:
                self.reset()
            return False
        if self.state == self.PULL:
            self._pull_bars += 1
            if self._d * (c - ema_fast) > 0:
                self.state = self.TRIG
                return True
            if self._d * (c - ema_slow) < 0 or self._pull_bars > self._n_pull:
                self.reset()
            return False
        return False


def should_exit(side: int, regime: int, close: float, ema_fast: float, ema_slow: float) -> bool:
    """Spec 4 priorities: regime reversal > EMA50 failure > ribbon cross."""
    if side > 0:
        return regime <= 0 or close < ema_slow or ema_fast <= ema_slow
    return regime >= 0 or close > ema_slow or ema_fast >= ema_slow


class ErrMomEma30Entry(Strategy):
    def __init__(self, config: ErrMomEma30EntryConfig) -> None:
        super().__init__(config)
        self._daily = BarAggregator(86_400)
        self._entry = BarAggregator(config.entry_tf_minutes * 60)
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
        self._ema_fast = Ema(config.ema_fast)
        self._ema_slow = Ema(config.ema_slow)
        self._long = RibbonEntryMachine(1, config.n_pull)
        self._short = RibbonEntryMachine(-1, config.n_pull)
        self._stop_price: float | None = None
        self._pending_atr: float | None = None
        self._active_direction = 0
        self._sized_this_bar = False

    def on_start(self) -> None:
        self.subscribe_bars(self.config.bar_type)

    def on_stop(self) -> None:
        if self.config.close_positions_on_stop:
            self.close_all_positions(self.config.instrument_id)

    def on_bar(self, bar: Bar) -> None:
        self._sized_this_bar = False
        ts = int(bar.ts_init)
        o, h, l, c = (bar.open.as_double(), bar.high.as_double(), bar.low.as_double(), bar.close.as_double())
        daily = self._daily.update(ts, o, h, l, c)  # daily first: fresh regime wins the 00:00 bar
        if daily is not None:
            self._ermom.update(daily.close)
            self._atr.update(daily.open, daily.high, daily.low, daily.close)
            self._sizer.update(daily.close)
        entry = self._entry.update(ts, o, h, l, c)
        if entry is not None:
            self._on_entry(entry)
        if daily is not None and not self._sized_this_bar and not self.portfolio.is_flat(self.config.instrument_id):
            side = 1 if self.portfolio.is_net_long(self.config.instrument_id) else -1
            self._sync_size(side, allow_new=False, allow_resize=True)

    def _prepare_machine(self, regime: int) -> RibbonEntryMachine:
        """Select the active direction machine, resetting both on sign flip.

        Direct +1<->-1 flips (flat) must not carry stale EXT/PULL across the
        flip: a re-entry needs a fresh EXT->PULL->TRIG pass (spec 3.3).
        """
        active = 1 if regime > 0 else -1
        if active != self._active_direction:
            self._long.reset()
            self._short.reset()
            self._active_direction = active
        return self._long if active > 0 else self._short

    def _on_entry(self, entry_bar: CompletedBar) -> None:
        ema_f = self._ema_fast.update(entry_bar.close)
        ema_s = self._ema_slow.update(entry_bar.close)
        regime = regime_target(self._ermom.value, self.config.theta)
        if not self.config.allow_short and regime < 0:
            regime = 0

        if not self.portfolio.is_flat(self.config.instrument_id):
            side = 1 if self.portfolio.is_net_long(self.config.instrument_id) else -1
            if self.config.risk_stop_enabled and is_stop_hit(side, entry_bar.close, self._stop_price):
                self.close_all_positions(self.config.instrument_id)
                self._stop_price = None
                self._sized_this_bar = True
                return
            if ema_f is not None and ema_s is not None and should_exit(side, regime, entry_bar.close, ema_f, ema_s):
                self.close_all_positions(self.config.instrument_id)
                self._stop_price = None
                self._long.reset()
                self._short.reset()
                self._sized_this_bar = True
            return

        if regime == 0 or ema_f is None or ema_s is None:
            self._long.reset()
            self._short.reset()
            return

        machine = self._prepare_machine(regime)
        if machine.update(
            o=entry_bar.open, h=entry_bar.high, l=entry_bar.low, c=entry_bar.close,
            ema_fast=ema_f, ema_slow=ema_s, regime_allows=True,
        ):
            self._sync_size(1 if regime > 0 else -1, allow_new=True, allow_resize=False)

    def _signed_qty(self) -> Decimal:
        return Decimal(str(self.portfolio.net_position(self.config.instrument_id)))

    def _reset_machines(self) -> None:
        self._stop_price = None
        self._long.reset()
        self._short.reset()

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
            self._reset_machines()
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

    # ponytail: market orders assumed fully filled; partial-fill retry is live-phase work (spec 5)
    def _submit(self, side: OrderSide, qty: Decimal, seed_stop: bool = True) -> None:
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

    def on_event(self, event) -> None:
        if not isinstance(event, OrderFilled) or event.instrument_id != self.config.instrument_id:
            return
        if self.portfolio.is_flat(self.config.instrument_id):
            self._stop_price = None
            return
        if self._pending_atr is not None:
            side = 1 if self.portfolio.is_net_long(self.config.instrument_id) else -1
            self._stop_price = stop_price(side, event.last_px.as_double(), self._pending_atr, self.config.atr_mult)
            self._pending_atr = None
