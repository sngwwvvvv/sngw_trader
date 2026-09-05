"""KD + MACD combo strategy (Wang & Huang 2026 entry, repo exit overlay).

Entry (both modes, cross default):
- Long: KD golden cross (K crosses above D) AND MACD bar > 0.
- Short (allow_short only, and only when flat): KD death cross AND bar < 0.

Exits:
- Long-only: KD death cross OR MACD bar < 0 (daily close).
- Long+short: ATR(14)×3 stop and 1:1 take-profit, seeded at fill.
  Opposite KD-MACD signals do not flip; the bracket owns the exit.
  1-minute high/low hit-check; if both sides tag the same bar, SL wins.

Sizing: Harvey-style volatility targeting via VolTargetSizer on UTC daily
bars (periods_per_year=365). Desired qty is compared to actual signed qty
(not ±trade_size). Before the vol warmup the sizer has no scale, so the
unit trade_size qty is used (documented fallback).

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
from sngw_trader.indicators.risk_metrics import DailyAtr, bracket_hit, stop_price
from sngw_trader.indicators.vol_targeting import VolTargetConfig, VolTargetSizer

_VALID_TRIGGERS = frozenset({"cross", "state"})


def direction_target(
    golden: bool,
    death: bool,
    current: int,
    allow_short: bool,
    macd_bar_neg: bool = False,
) -> int:
    """Map KD-MACD signals to a target direction.

    Long-only: golden -> +1; death or MACD bar < 0 -> 0.
    Long+short: entries only when flat; opposite signals do not flip (bracket exits).
    """
    if allow_short:
        if current != 0:
            return current
        if golden:
            return 1
        if death:
            return -1
        return 0
    if current == 1 and (death or macd_bar_neg):
        return 0
    if golden:
        return 1
    if death:
        return 0
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
    atr_period: int = 14
    atr_mult: float = 3.0
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
        self._atr = DailyAtr(config.atr_period)
        self._prev_k: float | None = None
        self._prev_d: float | None = None
        self._prev_long_cond: bool | None = None
        self._prev_short_cond: bool | None = None
        self._side: int = 0  # optimistic internal state; reconciled by on_event
        self._signed_qty_total: float = 0.0
        self._stop_price: float | None = None
        self._tp_price: float | None = None
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
        exited = False
        bracket = self._bracket_intent(h, l)
        if bracket is not None:
            self._execute(bracket)
            self._side = 0
            self._stop_price = None
            self._tp_price = None
            exited = True
        day = self._daily.update(ts, o, h, l, c)
        if day is None:
            return
        if exited:
            self._update_indicators(day.open, day.high, day.low, day.close)
            return
        for intent in self._decide(day.open, day.high, day.low, day.close):
            self._execute(intent)

    # --- pure decision core (unit-testable) ---------------------------------

    def _update_indicators(
        self, o: float, h: float, l: float, c: float
    ) -> tuple[tuple[float, float] | None, tuple[float, float, float] | None]:
        self._sizer.update(c)
        self._atr.update(o, h, l, c)
        return self._kd.update(h, l, c), self._macd.update(c)

    def _decide(
        self, o: float, h: float, l: float, c: float
    ) -> list[OrderIntent]:
        """Consume one completed UTC daily bar; return order intents."""
        kd_out, macd_out = self._update_indicators(o, h, l, c)
        if kd_out is None or macd_out is None:
            return []
        k, d = kd_out
        _dif, _dea, bar_val = macd_out

        kd_up = kd_down = False
        if self.config.trigger_mode == "cross":
            if self._prev_k is not None:
                kd_up = crossed_up(self._prev_k, self._prev_d, k, d)
                kd_down = crossed_down(self._prev_k, self._prev_d, k, d)
        else:
            long_cond = k > d
            short_cond = k < d
            kd_up = long_cond and not (self._prev_long_cond or False)
            kd_down = short_cond and not (self._prev_short_cond or False)
            self._prev_long_cond = long_cond
            self._prev_short_cond = short_cond
        self._prev_k, self._prev_d = k, d

        if self.config.allow_short:
            target = direction_target(
                golden=kd_up and bar_val > 0,
                death=kd_down and bar_val < 0,
                current=self._side,
                allow_short=True,
            )
        else:
            target = direction_target(
                golden=kd_up and bar_val > 0,
                death=kd_down,
                current=self._side,
                allow_short=False,
                macd_bar_neg=bar_val < 0,
            )
        return self._intents_for(target)

    def _current_qty(self) -> Decimal:
        step = Decimal(self.config.size_increment)
        raw = Decimal(str(self._signed_qty_total))
        return (raw / step).to_integral_value(rounding="ROUND_HALF_UP") * step

    def _intents_for(self, target: int) -> list[OrderIntent]:
        desired = self._sizer.desired_qty(target, self.config.trade_size)
        step = Decimal(self.config.size_increment)
        if desired is None:
            # vol-target warmup: fall back to unit qty so signals still trade
            desired = (
                Decimal("0") if target == 0 else Decimal(target) * self.config.trade_size
            )
        else:
            desired = (desired / step).to_integral_value(rounding="ROUND_HALF_UP") * step
        current = self._current_qty()
        if current == desired:
            return []
        intents: list[OrderIntent] = []
        if current != 0 and (desired == 0 or (current > 0) != (desired > 0)):
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
        self._side = target
        return intents

    def _seed_bracket(self, side: int, fill_px: float) -> None:
        atr = self._atr.value
        if side == 0 or atr is None:
            self._stop_price = None
            self._tp_price = None
            return
        self._stop_price = stop_price(side, fill_px, atr, self.config.atr_mult)
        self._tp_price = stop_price(-side, fill_px, atr, self.config.atr_mult)

    def _bracket_intent(self, high: float, low: float) -> OrderIntent | None:
        if not self.config.allow_short or self._side == 0:
            return None
        if bracket_hit(self._side, high, low, self._stop_price, self._tp_price) is None:
            return None
        qty = abs(self._current_qty())
        if qty == 0:
            return None
        return OrderIntent(-1 if self._side > 0 else 1, qty)

    # --- execution (thin) ----------------------------------------------------

    def _apply_fill(self, signed_qty: float) -> None:
        self._signed_qty_total += signed_qty
        # flatten only inside half a qty step, not half a unit (vol-target
        # can hold a single increment that is smaller than trade_size)
        tol = float(Decimal(self.config.size_increment)) / 2.0
        if abs(self._signed_qty_total) <= tol:
            self._signed_qty_total = 0.0
        self._side = (
            1 if self._signed_qty_total > 0 else (-1 if self._signed_qty_total < 0 else 0)
        )

    def on_event(self, event) -> None:
        from nautilus_trader.model.events import OrderFilled

        if not isinstance(event, OrderFilled) or event.instrument_id != self.config.instrument_id:
            return
        prev_qty = self._signed_qty_total
        signed = float(event.last_qty) * (1.0 if event.order_side == OrderSide.BUY else -1.0)
        self._apply_fill(signed)
        if not self.config.allow_short:
            return
        if prev_qty == 0.0 and self._signed_qty_total != 0.0:
            self._seed_bracket(self._side, event.last_px.as_double())
        elif self._signed_qty_total == 0.0:
            self._stop_price = None
            self._tp_price = None

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
