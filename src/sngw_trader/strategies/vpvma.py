"""VPVMA strategy (swing; handoff 20260905-bt-vpvma).

Paper literal (raw-trading-0005, arXiv:2206.12282, Table 8), long-only:
- Buy:  VPVMA_t > (1 + bandwidth) * VPVMAS_t  and  VPVMA_{t-1} <= VPVMAS_{t-1}
- Sell: VPVMA_t < (1 - 2*bandwidth) * VPVMAS_t  and  VPVMA_{t-1} <= VPVMAS_{t-1}
  (asymmetric bands, paper literal; prev condition written <= for both).
- Params 12/26/9, bandwidth 0.1 — fixed, no optimization allowed.
- allow_short=True variant: the Sell signal opens a short, the Buy signal
  flips back to long (repo convention; the paper itself is long-only).
- No take-profit / stop-loss / regime filter — the paper adds none.

Sizing: default unit qty (``sizing_mode="unit"``; the paper trades all-in
per $80k stock). Vol-target variant (``sizing_mode="vol_target"``) is a
sensitivity run only, via VolTargetSizer on UTC daily bars
(periods_per_year=365), unit-qty fallback during warmup, band rebalancing.

Structure cloned from macd_crossover.py: pure ``_decide`` fed by completed
UTC daily bars, ``_side`` optimistic internal state reconciled in on_event.

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
from sngw_trader.indicators.vpvma import Vpvma as VpvmaIndicator
from sngw_trader.indicators.vol_targeting import VolTargetConfig, VolTargetSizer

_VALID_SIZING = frozenset({"unit", "vol_target"})


def band_signal(
    prev_v: float | None, prev_s: float | None, v: float, s: float, bandwidth: float
) -> int:
    """Paper Table 8 signal: +1 buy, -1 sell, 0 none."""
    if prev_v is None or prev_s is None:
        return 0
    crossed_prev = prev_v <= prev_s
    if crossed_prev and v > (1.0 + bandwidth) * s:
        return 1
    if crossed_prev and v < (1.0 - 2.0 * bandwidth) * s:
        return -1
    return 0


def direction_target(signal: int, current: int, allow_short: bool) -> int:
    """Buy -> +1, Sell -> 0 (long-only) / -1 (allow_short), none -> hold."""
    if signal == 1:
        return 1
    if signal == -1:
        return -1 if allow_short else 0
    return current


@dataclass(frozen=True)
class OrderIntent:
    """Pure order decision emitted by _decide; executed by _submit."""

    side: int  # +1 buy, -1 sell
    qty: Decimal


class VpvmaConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    fast: int = 12
    slow: int = 26
    sign: int = 9
    bandwidth: float = 0.10
    allow_short: bool = False
    sizing_mode: str = "unit"  # "unit" (default) | "vol_target" (sensitivity)
    size_target_vol: float = 0.20
    size_half_life: int = 20
    size_min_scale: float = 0.0
    size_max_scale: float = 3.0
    size_rebalance_band: float = 0.10
    size_increment: str = "0.01"  # instrument qty step; smaller sizes round to 0
    close_positions_on_stop: bool = True


class Vpvma(Strategy):
    """VPVMA(12,26,9, bw=0.1) band strategy with unit or vol-target sizing.

    One UTC daily bar = one decision.
    """

    def __init__(self, config: VpvmaConfig) -> None:
        super().__init__(config)
        if config.sizing_mode not in _VALID_SIZING:
            raise ValueError(f"sizing_mode must be unit|vol_target, got {config.sizing_mode!r}")
        self._daily = BarAggregator(86_400)
        self._vpvma = VpvmaIndicator(fast=config.fast, slow=config.slow, sign=config.sign)
        self._sizer = VolTargetSizer(
            VolTargetConfig(
                target_vol=config.size_target_vol,
                half_life=config.size_half_life,
                min_scale=config.size_min_scale,
                max_scale=config.size_max_scale,
                rebalance_band=config.size_rebalance_band,
                periods_per_year=365,
                mode="vol_target",
            )
        )
        self._prev_v: float | None = None
        self._prev_s: float | None = None
        self._side: int = 0  # optimistic internal state; reconciled by on_event
        self._signed_qty: Decimal = Decimal("0")  # actual position qty from fills

    def on_start(self) -> None:
        self.subscribe_bars(self.config.bar_type)

    def on_stop(self) -> None:
        if self.config.close_positions_on_stop:
            self.close_all_positions(self.config.instrument_id)

    def on_bar(self, bar: Bar) -> None:
        ts = int(bar.ts_init)
        o = bar.open.as_double()
        h = bar.high.as_double()
        l = bar.low.as_double()
        c = bar.close.as_double()
        day = self._daily.update(ts, o, h, l, c, bar.volume.as_double())
        if day is None:
            return
        for intent in self._decide(
            day.open, day.high, day.low, day.close, day.volume
        ):
            self._execute(intent)

    # --- pure decision core (unit-testable) ---------------------------------

    def _decide(
        self, o: float, h: float, l: float, c: float, volume: float = 0.0
    ) -> list[OrderIntent]:
        """Consume one completed UTC daily bar; return order intents."""
        self._sizer.update(c)
        out = self._vpvma.update(h, l, c, o, volume)
        if out is None:
            return []
        v, s = out
        sig = band_signal(self._prev_v, self._prev_s, v, s, self.config.bandwidth)
        self._prev_v, self._prev_s = v, s
        target = direction_target(sig, self._side, self.config.allow_short)
        return self._intents_for(target)

    def _intents_for(self, target: int) -> list[OrderIntent]:
        unit = self.config.trade_size
        if self.config.sizing_mode == "unit":
            desired = Decimal("0") if target == 0 else Decimal(target) * unit
        else:
            desired = self._sizer.desired_qty(target, unit)
            if desired is None:
                # vol-target warmup: fall back to unit qty so signals still trade
                desired = Decimal("0") if target == 0 else Decimal(target) * unit
            else:
                # round to the instrument qty step (scaled sizes may round to zero:
                # vol targeting then holds no position rather than an untradable one)
                step = Decimal(self.config.size_increment)
                desired = (desired / step).to_integral_value(rounding="ROUND_HALF_UP") * step
        current = self._signed_qty  # actual position qty from fills (see on_event)
        if current == desired:
            return []
        intents: list[OrderIntent] = []
        if current != 0 and (desired == 0 or (current > 0) != (desired > 0)):
            # close existing position first (NETTING-safe); qty = ACTUAL size
            intents.append(OrderIntent(-1 if current > 0 else 1, abs(current)))
            current = Decimal("0")
            self._side = 0
            self._signed_qty = Decimal("0")
            if desired == 0:
                return intents
        delta = desired - current
        if delta == 0:
            return intents
        if current != 0 and not self._sizer.should_rebalance(current, desired):
            return intents
        intents.append(OrderIntent(1 if delta > 0 else -1, abs(delta)))
        # Optimistic update; on_event reconciles with real fills.
        self._side = target
        self._signed_qty = desired
        return intents

    # --- execution (thin) ----------------------------------------------------

    def on_event(self, event) -> None:
        from nautilus_trader.model.events import OrderFilled

        if not isinstance(event, OrderFilled) or event.instrument_id != self.config.instrument_id:
            return
        signed = event.last_qty * (
            Decimal(1) if event.order_side == OrderSide.BUY else Decimal(-1)
        )
        self._signed_qty += signed
        self._side = 1 if self._signed_qty > 0 else (-1 if self._signed_qty < 0 else 0)

    def _execute(self, intent: OrderIntent) -> None:
        instrument = self.cache.instrument(self.config.instrument_id)
        if instrument is None:
            self.log.error(f"Instrument not in cache: {self.config.instrument_id}")
            return
        q = instrument.make_qty(intent.qty)
        if q == 0:
            return
        self.submit_order(
            self.order_factory.market(
                self.config.instrument_id,
                OrderSide.BUY if intent.side > 0 else OrderSide.SELL,
                q,
            )
        )
