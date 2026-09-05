"""MACD crossover core strategy (swing; handoff 20260905-bt-macd-crossover-core).

Rules (strategy doc literal):
- Long entry: MACD line (EMA12 - EMA26) crosses above the signal line
  (EMA9 of the MACD line).
- Exit / short: MACD line crosses below the signal line.
- allow_short=True variant: the death cross opens a short instead of only
  closing; the golden cross flips back to long.
- No take-profit / stop-loss / regime filter — the doc adds none.

Sizing: the strategy doc leaves position sizing unconfirmed, so the default
is unit qty (``sizing_mode="unit"``). A Harvey-style volatility-targeting
variant (``sizing_mode="vol_target"``) is provided as a sensitivity run only,
via VolTargetSizer on UTC daily bars (periods_per_year=365), with unit-qty
fallback during the vol warmup and band-based rebalancing while holding.

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
from sngw_trader.indicators.kd_macd import Macd, crossed_down, crossed_up
from sngw_trader.indicators.vol_targeting import VolTargetConfig, VolTargetSizer

_VALID_TRIGGERS = frozenset({"cross", "state"})
_VALID_SIZING = frozenset({"unit", "vol_target"})


def direction_target(golden: bool, death: bool, current: int, allow_short: bool) -> int:
    """Map MACD crossover signals to a target direction.

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


class MacdCrossoverConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    allow_short: bool = False
    trigger_mode: str = "cross"  # "cross" (doc literal) | "state" (sensitivity)
    sizing_mode: str = "unit"  # "unit" (doc default) | "vol_target" (sensitivity)
    size_target_vol: float = 0.20
    size_half_life: int = 20
    size_min_scale: float = 0.0
    size_max_scale: float = 3.0
    size_rebalance_band: float = 0.10
    size_increment: str = "0.01"  # instrument qty step; smaller sizes round to 0
    close_positions_on_stop: bool = True


class MacdCrossover(Strategy):
    """MACD(12,26,9) crossover with unit or vol-target sizing.

    One UTC daily bar = one decision.
    """

    def __init__(self, config: MacdCrossoverConfig) -> None:
        super().__init__(config)
        if config.trigger_mode not in _VALID_TRIGGERS:
            raise ValueError(f"trigger_mode must be cross|state, got {config.trigger_mode!r}")
        if config.sizing_mode not in _VALID_SIZING:
            raise ValueError(f"sizing_mode must be unit|vol_target, got {config.sizing_mode!r}")
        self._daily = BarAggregator(86_400)
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
                mode="vol_target",
            )
        )
        self._prev_line: float | None = None
        self._prev_signal: float | None = None
        self._prev_long_cond: bool | None = None
        self._prev_short_cond: bool | None = None
        self._side: int = 0  # optimistic internal state; reconciled by on_event
        self._signed_qty: Decimal = Decimal("0")  # actual position qty from fills
        self._fills_signed_total: float = 0.0

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
        macd_out = self._macd.update(c)
        if macd_out is None:
            return []
        line, sig, _bar = macd_out

        golden = death = False
        if self.config.trigger_mode == "cross":
            if self._prev_line is not None and self._prev_signal is not None:
                golden = (
                    crossed_up(self._prev_line, self._prev_signal, line, sig)
                )
                death = (
                    crossed_down(self._prev_line, self._prev_signal, line, sig)
                )
        else:  # state: condition transition
            long_cond = line > sig
            short_cond = line < sig
            golden = long_cond and not (self._prev_long_cond or False)
            death = short_cond and not (self._prev_short_cond or False)
            self._prev_long_cond = long_cond
            self._prev_short_cond = short_cond
        self._prev_line, self._prev_signal = line, sig

        target = direction_target(golden, death, self._side, self.config.allow_short)
        return self._intents_for(target)

    def _intents_for(self, target: int) -> list[OrderIntent]:
        unit = self.config.trade_size
        if self.config.sizing_mode == "unit":
            desired = Decimal("0") if target == 0 else Decimal(target) * unit
        else:
            desired = self._sizer.desired_qty(target, unit)
            if desired is None:
                # vol-target warmup: fall back to unit qty so signals still trade
                desired = (
                    Decimal("0") if target == 0 else Decimal(target) * unit
                )
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
            # close existing position first (NETTING-safe); qty = ACTUAL size,
            # not side*unit — scaled adds make the position larger than unit.
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
        # Reconcile internal position from fills: accumulate signed qty exactly
        # (Decimal), so partial closes of scaled positions are tracked.
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