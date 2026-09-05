"""MACD + Momentum combo strategy (Chio 2022, arXiv:2206.12282, Table 4 literal).

Rules (paper-verified):
- MACD(12,26,9); momentum indicator RSI(14, lower 35 / upper 70) or
  MFI(14, lower 25 / upper 70), selected via ``momentum`` config.
- Buy condition (paper literal): MACD_t > Signal_t (state, not crossover)
  AND ALL of the last 6 bars' momentum values (t..t-5) <= lower threshold.
- Sell condition (paper literal): MACD_t < Signal_t AND ALL of the last 6
  bars' momentum values >= upper threshold.
- Long-only is the paper variant. allow_short=True is a user-approved
  direction-symmetric extension: the paper Sell condition opens a short and
  the Buy condition flips back to long. NOT a paper rule — reported as such.
- Default exits are opposite signals only (no TP/SL in the paper).
  use_bracket=True adds an ATR(14)×3 stop + 1:1 TP seeded at fill
  (sensitivity only, repo KD-MACD precedent).

Sizing: all-in by default (paper: "uses all the money to buy as many as
possible shares" / "sells all the shares"); unit qty via all_in=False as a
comparison cell. All-in qty is computed from portfolio free balance at
execution time, floored to the instrument qty step.

Design: signal -> target decision is a pure method (``_decide``) fed by
completed UTC daily bars (BarAggregator preserves volume for MFI);
position tracking accumulates actual signed fill qty (``_signed_qty``).
Strategy logic only. No runner assembly, no exchange I/O.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model import Bar, BarType, InstrumentId
from nautilus_trader.model.enums import OrderSide, PriceType
from nautilus_trader.trading import Strategy

from sngw_trader.indicators.bar_aggregator import BarAggregator
from sngw_trader.indicators.kd_macd import Macd
from sngw_trader.indicators.mfi import Mfi
from sngw_trader.indicators.risk_metrics import DailyAtr, bracket_hit, stop_price
from sngw_trader.indicators.rsi import Rsi

_VALID_MOMENTUM = frozenset({"rsi", "mfi"})

# Paper Table 4 thresholds
MOMENTUM_WINDOW = 14
RSI_LOWER, RSI_UPPER = 35.0, 70.0
MFI_LOWER, MFI_UPPER = 25.0, 70.0
COND_BARS = 6  # all-of-6 lookback: t .. t-5


def momentum_all_below(values: deque[float], threshold: float) -> bool:
    """Paper Buy-side momentum clause: every value <= threshold."""
    return len(values) == COND_BARS and all(v <= threshold for v in values)


def momentum_all_above(values: deque[float], threshold: float) -> bool:
    """Paper Sell-side momentum clause: every value >= threshold."""
    return len(values) == COND_BARS and all(v >= threshold for v in values)


def direction_target(buy_cond: bool, sell_cond: bool, current: int, allow_short: bool) -> int:
    """Map combo conditions to a target direction.

    buy_cond -> +1; sell_cond -> -1 (allow_short) or 0 (long-only); no
    condition -> hold current. Buy takes precedence when both fire.
    """
    if buy_cond:
        return 1
    if sell_cond:
        return -1 if allow_short else 0
    return current


@dataclass(frozen=True)
class OrderIntent:
    """Pure order decision emitted by _decide; executed by _execute."""

    side: int  # +1 buy, -1 sell
    qty: Decimal


class MacdMomentumComboConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal  # unit qty (all_in=False cells); all-in ignores this
    momentum: str = "rsi"  # "rsi" (paper MACD&RSI) | "mfi" (paper MACD&MFI)
    allow_short: bool = False  # long-only is the paper variant
    all_in: bool = True  # paper sizing; False = fixed unit qty comparison cell
    use_bracket: bool = False  # ATR stop/TP sensitivity; paper has no TP/SL
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    momentum_window: int = MOMENTUM_WINDOW
    atr_period: int = 14
    atr_mult: float = 3.0
    size_increment: str = "0.01"  # instrument qty step; all-in floors to it
    close_positions_on_stop: bool = True


class MacdMomentumCombo(Strategy):
    """MACD + RSI/MFI combo. One UTC daily bar = one decision."""

    def __init__(self, config: MacdMomentumComboConfig) -> None:
        super().__init__(config)
        if config.momentum not in _VALID_MOMENTUM:
            raise ValueError(f"momentum must be rsi|mfi, got {config.momentum!r}")
        self._daily = BarAggregator(86_400)
        self._macd = Macd(
            fast=config.macd_fast, slow=config.macd_slow, signal=config.macd_signal
        )
        if config.momentum == "rsi":
            self._momentum = Rsi(config.momentum_window)
            self._lower, self._upper = RSI_LOWER, RSI_UPPER
        else:
            self._momentum = Mfi(config.momentum_window)
            self._lower, self._upper = MFI_LOWER, MFI_UPPER
        self._atr = DailyAtr(config.atr_period)
        self._mom_values: deque[float] = deque(maxlen=COND_BARS)
        self._side: int = 0  # optimistic internal state; reconciled by on_event
        self._signed_qty: Decimal = Decimal("0")
        self._stop_price: float | None = None
        self._tp_price: float | None = None

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
        if self.config.use_bracket:
            bracket = self._bracket_intent(h, l)
            if bracket is not None:
                self._execute(bracket)
                self._signed_qty = Decimal("0")
                self._side = 0
                self._stop_price = None
                self._tp_price = None
        day = self._daily.update(ts, o, h, l, c, bar.volume.as_double())
        if day is None:
            return
        for intent in self._decide(day.open, day.high, day.low, day.close, day.volume):
            self._execute(intent)

    # --- pure decision core (unit-testable) ---------------------------------

    def _decide(self, o: float, h: float, l: float, c: float, v: float) -> list[OrderIntent]:
        """Consume one completed UTC daily bar; return order intents."""
        self._atr.update(o, h, l, c)
        macd_out = self._macd.update(c)
        if macd_out is None:
            return []
        line, sig, _bar = macd_out
        if self.config.momentum == "rsi":
            mom = self._momentum.update(c)
        else:
            mom = self._momentum.update(h, l, c, v)
        if mom is not None:
            self._mom_values.append(mom)

        buy_cond = line > sig and momentum_all_below(self._mom_values, self._lower)
        sell_cond = line < sig and momentum_all_above(self._mom_values, self._upper)
        target = direction_target(buy_cond, sell_cond, self._side, self.config.allow_short)
        return self._intents_for(target)

    def _intents_for(self, target: int) -> list[OrderIntent]:
        if self.config.all_in:
            # qty depends on account balance, resolved at execution time
            if target == self._side:
                return []
            intents: list[OrderIntent] = []
            current = self._signed_qty
            if current != 0:
                intents.append(OrderIntent(-1 if current > 0 else 1, abs(current)))
                self._signed_qty = Decimal("0")
                self._side = 0
            if target != 0:
                intents.append(OrderIntent(1 if target > 0 else -1, Decimal("0")))
            self._side = target
            return intents
        # unit sizing: fixed trade_size, exact qty known here
        desired = Decimal("0") if target == 0 else Decimal(target) * self.config.trade_size
        current = self._signed_qty
        if current == desired:
            return []
        intents = []
        if current != 0:
            intents.append(OrderIntent(-1 if current > 0 else 1, abs(current)))
            current = Decimal("0")
            self._signed_qty = Decimal("0")
            self._side = 0
            if desired == 0:
                return intents
        intents.append(
            OrderIntent(1 if desired > 0 else -1, abs(desired))
        )
        self._side = target
        self._signed_qty = desired
        return intents

    def _desired_all_in_qty(self, target: int) -> Decimal:
        """Paper all-in: use all available balance at the last price."""
        if target == 0:
            return Decimal("0")
        instrument = self.cache.instrument(self.config.instrument_id)
        if instrument is None:
            return Decimal("0")
        account = self.portfolio.account(self.config.instrument_id.venue)
        if account is None:
            return Decimal("0")
        price = self.cache.price(self.config.instrument_id, PriceType.LAST)
        if price is None:
            return Decimal("0")
        free = account.balance_free(instrument.quote_currency)
        if free is None or free.as_decimal() <= 0:
            return Decimal("0")
        raw = free.as_decimal() / Decimal(str(price))
        step = Decimal(self.config.size_increment)
        return (raw / step).to_integral_value(rounding="ROUND_DOWN") * step

    def _seed_bracket(self, side: int, fill_px: float) -> None:
        atr = self._atr.value
        if side == 0 or atr is None:
            self._stop_price = None
            self._tp_price = None
            return
        self._stop_price = stop_price(side, fill_px, atr, self.config.atr_mult)
        self._tp_price = stop_price(-side, fill_px, atr, self.config.atr_mult)

    def _bracket_intent(self, high: float, low: float) -> OrderIntent | None:
        if not self.config.use_bracket or self._side == 0:
            return None
        if bracket_hit(self._side, high, low, self._stop_price, self._tp_price) is None:
            return None
        qty = abs(self._signed_qty)
        if qty == 0:
            return None
        return OrderIntent(-1 if self._side > 0 else 1, qty)

    # --- execution (thin) ----------------------------------------------------

    def on_event(self, event) -> None:
        from nautilus_trader.model.events import OrderFilled

        if not isinstance(event, OrderFilled) or event.instrument_id != self.config.instrument_id:
            return
        prev_qty = self._signed_qty
        signed = event.last_qty * (
            Decimal(1) if event.order_side == OrderSide.BUY else Decimal(-1)
        )
        self._signed_qty += signed
        self._side = 1 if self._signed_qty > 0 else (-1 if self._signed_qty < 0 else 0)
        if not self.config.use_bracket:
            return
        if prev_qty == 0 and self._signed_qty != 0:
            self._seed_bracket(self._side, event.last_px.as_double())
        elif self._signed_qty == 0:
            self._stop_price = None
            self._tp_price = None

    def _execute(self, intent: OrderIntent) -> None:
        qty = intent.qty
        if self.config.all_in and intent.side != 0 and self._side != 0 and intent.qty == 0:
            # entry leg of an all-in flip: resolve qty from balance now
            qty = self._desired_all_in_qty(self._side)
        if qty == 0:
            return
        instrument = self.cache.instrument(self.config.instrument_id)
        if instrument is None:
            self.log.error(f"Instrument not in cache: {self.config.instrument_id}")
            return
        q = instrument.make_qty(qty)
        if q == 0:
            return
        self.submit_order(
            self.order_factory.market(
                self.config.instrument_id,
                OrderSide.BUY if intent.side > 0 else OrderSide.SELL,
                q,
            )
        )
