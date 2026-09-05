"""Renko + MACD intraday strategy (handoff 20260905-bt-renko-macd-intraday).

Rules (spec §1 — strategy doc + stated assumptions):
- Renko bricks are built from 1m bar closes (close-only convention: up needs
  >= +B, direction flip needs 2B — see indicators/renko.py).
- MACD(12/26/9) on confirmed brick closes. Golden cross -> long entry,
  death cross -> exit (or short entry when allow_short). Signals fire on
  brick confirmation (1m bar close), time-independent.
- Daily flatten: when flatten_daily=True, any open position is closed on the
  first bar of a new UTC day (overnight holds forbidden; NSE session-close
  proxy). Re-entry happens on the next signal.
- No take-profit / stop-loss — the doc adds none.
- Sizing: unit qty default; vol-target variant as sensitivity only.

Design follows macd_crossover.py: pure `_decide(brick_closes, force_flat)`
returns OrderIntents; `_side` is optimistic state reconciled against fills
in on_event. Strategy logic only — no runner assembly, no exchange I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model import Bar, BarType, InstrumentId
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.trading import Strategy

from sngw_trader.indicators.kd_macd import Macd, crossed_down, crossed_up
from sngw_trader.indicators.renko import RenkoBrickBuilder
from sngw_trader.indicators.vol_targeting import VolTargetConfig, VolTargetSizer
from sngw_trader.strategies.macd_crossover import OrderIntent, direction_target

_NS_DAY = 86_400 * 1_000_000_000
_VALID_SIZING = frozenset({"unit", "vol_target"})


class RenkoMacdIntradayConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    brick_pct: float  # brick size as a fraction of price (relative, spec §3)
    trade_size: Decimal
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    allow_short: bool = False
    flatten_daily: bool = True
    sizing_mode: str = "unit"  # "unit" (doc default) | "vol_target" (sensitivity)
    size_target_vol: float = 0.20
    size_half_life: int = 20
    size_min_scale: float = 0.0
    size_max_scale: float = 3.0
    size_rebalance_band: float = 0.10
    size_increment: str = "0.01"
    close_positions_on_stop: bool = True


class RenkoMacdIntraday(Strategy):
    """MACD(12,26,9) crossover on Renko brick closes, daily-flattened."""

    def __init__(self, config: RenkoMacdIntradayConfig) -> None:
        super().__init__(config)
        if config.sizing_mode not in _VALID_SIZING:
            raise ValueError(f"sizing_mode must be unit|vol_target, got {config.sizing_mode!r}")
        if config.brick_pct <= 0:
            raise ValueError("brick_pct must be > 0")
        self._renko = RenkoBrickBuilder(1.0)  # size passed per-bar (relative)
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
        self._side: int = 0
        self._signed_qty: Decimal = Decimal("0")
        self._day: int | None = None  # UTC day bucket of the last seen bar

    def on_start(self) -> None:
        self.subscribe_bars(self.config.bar_type)

    def on_stop(self) -> None:
        if self.config.close_positions_on_stop:
            self.close_all_positions(self.config.instrument_id)

    def on_bar(self, bar: Bar) -> None:
        ts = int(bar.ts_init)
        force_flat = False
        day = ts // _NS_DAY
        if self._day is not None and day != self._day and self._side != 0:
            force_flat = True  # new UTC day: close overnight hold (spec proxy)
        self._day = day
        close = bar.close.as_double()
        brick = close * self.config.brick_pct
        for b in self._renko.on_close(close, brick):
            for intent in self._decide(b, force_flat):
                self._execute(intent)
            force_flat = False  # only the first brick of the day flattens

    # --- pure decision core (unit-testable) ---------------------------------

    def _decide(self, brick_close: float, force_flat: bool = False) -> list[OrderIntent]:
        """Consume one confirmed brick close; return order intents."""
        self._sizer.update(brick_close)
        macd_out = self._macd.update(brick_close)
        target = 0
        if not force_flat:
            if macd_out is None:
                return []
            line, sig, _ = macd_out
            golden = death = False
            if self._prev_line is not None and self._prev_signal is not None:
                golden = crossed_up(self._prev_line, self._prev_signal, line, sig)
                death = crossed_down(self._prev_line, self._prev_signal, line, sig)
            self._prev_line, self._prev_signal = line, sig
            target = direction_target(golden, death, self._side, self.config.allow_short)
        # force_flat bypasses MACD warmup: flatten is a risk action.
        return self._intents_for(target)

    def _intents_for(self, target: int) -> list[OrderIntent]:
        unit = self.config.trade_size
        if self.config.sizing_mode == "unit":
            desired = Decimal("0") if target == 0 else Decimal(target) * unit
        else:
            desired = self._sizer.desired_qty(target, unit)
            if desired is None:
                desired = (
                    Decimal("0") if target == 0 else Decimal(target) * unit
                )
            else:
                step = Decimal(self.config.size_increment)
                desired = (desired / step).to_integral_value(rounding="ROUND_HALF_UP") * step
        current = self._signed_qty
        if current == desired:
            return []
        intents: list[OrderIntent] = []
        if current != 0 and (desired == 0 or (current > 0) != (desired > 0)):
            intents.append(OrderIntent(-1 if current > 0 else 1, abs(current)))
            current = Decimal("0")
            self._side = 0
            self._signed_qty = Decimal("0")
            if desired == 0:
                return intents
        delta = desired - current
        if delta == 0:
            return []
        if current != 0 and not self._sizer.should_rebalance(current, desired):
            return []
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
