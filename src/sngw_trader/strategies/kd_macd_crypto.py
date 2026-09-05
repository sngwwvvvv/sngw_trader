"""KD + MACD combo strategy (Wang & Huang 2026, raw-trading-0007).

Rules (paper literal, cross mode):
- Long entry: KD golden cross (K crosses above D) AND MACD bar > 0.
- Long exit:  KD death cross (K crosses below D) AND MACD bar < 0.
- allow_short=True variant: death-cross condition opens a short instead of
  only closing; golden cross flips back to long.

Sizing: Harvey-style volatility targeting via VolTargetSizer on UTC daily
bars (periods_per_year=365). Position is resized on daily closes when the
desired qty drifts beyond the rebalance band. Before the vol warmup the
sizer has no scale, so the unit trade_size qty is used (documented fallback).

Design note: the signal -> target -> qty decision is a pure method
(``_decide``) fed by completed UTC daily bars, so it is unit-testable
without a Nautilus engine. ``_side`` is optimistic internal position state,
reconciled against real fills (position_qty) in ``on_event``.

Strategy logic only. No runner assembly, no exchange I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model import Bar, BarType, InstrumentId
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.trading import Strategy

from sngw_trader.indicators.bar_aggregator import BarAggregator
from sngw_trader.indicators.kd_macd import KdStochastic, Macd, crossed_down, crossed_up
from sngw_trader.indicators.vol_targeting import VolTargetConfig, VolTargetSizer

_VALID_TRIGGERS = frozenset({"cross", "state"})


def direction_target(golden: bool, death: bool, current: int, allow_short: bool) -> int:
    """Map KD-MACD signals to a target direction.

    golden -> +1, death -> 0 (long-only) or -1 (allow_short), no signal -> hold.
    """
    if golden:
        return 1
    if death:
        return -1 if allow_short else 0
    return current


@dataclass(frozen=True)
class OrderIntent:
    """Pure order decision emitted by _decide; executed by _submit."""

    side: int  # +1 buy, -1 sell
    qty: Decimal


class KdMacdCryptoConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    kd_n: int = 9
    kd_alpha: float = 0.5
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    allow_short: bool = False
    trigger_mode: str = "cross"  # "cross" (paper literal) | "state" (sensitivity)
    sizing_mode: str = "vol_target"
    size_target_vol: float = 0.20
    size_half_life: int = 20
    size_min_scale: float = 0.0
    size_max_scale: float = 3.0
    size_rebalance_band: float = 0.10
    size_increment: str = "0.01"  # instrument qty step; smaller sizes round to 0
    close_positions_on_stop: bool = True


class KdMacdCrypto(Strategy):
    """KD-MACD combo with vol-target sizing. One UTC daily bar = one decision."""

    def __init__(self, config: KdMacdCryptoConfig) -> None:
        super().__init__(config)
        if config.trigger_mode not in _VALID_TRIGGERS:
            raise ValueError(f"trigger_mode must be cross|state, got {config.trigger_mode!r}")
        self._daily = BarAggregator(86_400)
        self._kd = KdStochastic(n=config.kd_n, alpha=config.kd_alpha)
        self._macd = Macd(
            fast=config.macd_fast, slow=config.macd_slow, signal=config.macd_signal
        )
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
        self._prev_k: float | None = None
        self._prev_d: float | None = None
        self._prev_long_cond: bool | None = None
        self._prev_short_cond: bool | None = None
        self._side: int = 0  # optimistic internal state; reconciled by on_event
        self._signed_qty_total: float = 0.0
        self._sized_this_bar = False

    def on_start(self) -> None:
        self.subscribe_bars(self.config.bar_type)

    def on_stop(self) -> None:
        if self.config.close_positions_on_stop:
            self.close_all_positions(self.config.instrument_id)

    def on_bar(self, bar: Bar) -> None:
        self._sized_this_bar = False
        ts = int(bar.ts_init)
        o = bar.open.as_double()
        h = bar.high.as_double()
        l = bar.low.as_double()
        c = bar.close.as_double()
        day = self._daily.update(ts, o, h, l, c)
        if day is None:
            return
        for intent in self._decide(day.open, day.high, day.low, day.close):
            self._execute(intent)

    # --- pure decision core (unit-testable) ---------------------------------

    def _decide(
        self, o: float, h: float, l: float, c: float
    ) -> list[OrderIntent]:
        """Consume one completed UTC daily bar; return order intents."""
        self._sizer.update(c)
        kd_out = self._kd.update(h, l, c)
        macd_out = self._macd.update(c)
        if kd_out is None or macd_out is None:
            return []
        k, d = kd_out
        _dif, _dea, bar_val = macd_out

        golden = death = False
        if self.config.trigger_mode == "cross":
            if self._prev_k is not None:
                golden = crossed_up(self._prev_k, self._prev_d, k, d) and bar_val > 0
                death = crossed_down(self._prev_k, self._prev_d, k, d) and bar_val < 0
        else:  # state: condition transition
            long_cond = k > d and bar_val > 0
            short_cond = k < d and bar_val < 0
            golden = long_cond and not (self._prev_long_cond or False)
            death = short_cond and not (self._prev_short_cond or False)
            self._prev_long_cond = long_cond
            self._prev_short_cond = short_cond
        self._prev_k, self._prev_d = k, d

        target = direction_target(golden, death, self._side, self.config.allow_short)
        return self._intents_for(target)

    def _intents_for(self, target: int) -> list[OrderIntent]:
        desired = self._sizer.desired_qty(target, self.config.trade_size)
        if desired is None:
            # vol-target warmup: fall back to unit qty so signals still trade
            desired = (
                Decimal("0") if target == 0 else Decimal(target) * self.config.trade_size
            )
        else:
            # round to the instrument qty step (scaled sizes may round to zero:
            # vol targeting then holds no position rather than an untradable one)
            step = Decimal(self.config.size_increment)
            desired = (desired / step).to_integral_value(rounding="ROUND_HALF_UP") * step
        current = self._side * self.config.trade_size
        if current == desired:
            return []
        intents: list[OrderIntent] = []
        if current != 0 and (desired == 0 or (current > 0) != (desired > 0)):
            # close existing position first (NETTING-safe)
            intents.append(
                OrderIntent(-1 if current > 0 else 1, abs(current))
            )
            current = Decimal("0")
            self._side = 0
            if desired == 0:
                return intents
        delta = desired - current
        if delta == 0:
            return intents
        if current != 0 and not self._sizer.should_rebalance(current, desired):
            return intents
        intents.append(OrderIntent(1 if delta > 0 else -1, abs(delta)))
        # Optimistic side update; on_event reconciles with real fills.
        self._side = target
        return intents

    # --- execution (thin) ----------------------------------------------------

    def on_event(self, event) -> None:
        # Reconcile internal position from fills: accumulate signed qty.
        from nautilus_trader.model.events import OrderFilled

        if not isinstance(event, OrderFilled) or event.instrument_id != self.config.instrument_id:
            return
        signed = float(event.last_qty) * (1.0 if event.order_side == OrderSide.BUY else -1.0)
        self._signed_qty_total += signed
        tol = float(self.config.trade_size) / 2.0
        if abs(self._signed_qty_total) <= tol:
            self._signed_qty_total = 0.0
        self._side = (
            1 if self._signed_qty_total > 0 else (-1 if self._signed_qty_total < 0 else 0)
        )

    def _execute(self, intent: OrderIntent) -> None:
        instrument = self.cache.instrument(self.config.instrument_id)
        if instrument is None:
            self.log.error(f"Instrument not in cache: {self.config.instrument_id}")
            return
        q = instrument.make_qty(intent.qty)
        if q == 0:
            return
        self._sized_this_bar = True
        self.submit_order(
            self.order_factory.market(
                self.config.instrument_id,
                OrderSide.BUY if intent.side > 0 else OrderSide.SELL,
                q,
            )
        )
