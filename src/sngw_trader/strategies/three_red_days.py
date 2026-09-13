"""Three consecutive red daily bars, long next open, exit after hold_bars closes.

Spec: docs/superpowers/specs/2026-09-13-three-red-days-design.md
Strategy logic only. No runner assembly, no exchange I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model import Bar, BarType, InstrumentId
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.trading import Strategy

from sngw_trader.indicators.bar_aggregator import NS, BarAggregator

DAY_NS = 86_400 * NS
SOURCE_NS = 60 * NS


def is_red(open_px: float, close_px: float) -> bool:
    return close_px < open_px


def next_streak(streak: int, red: bool) -> int:
    return streak + 1 if red else 0


def utc_bucket(ts_close_ns: int) -> int:
    return (ts_close_ns - SOURCE_NS) // DAY_NS


def is_last_minute(ts_close_ns: int) -> bool:
    return ts_close_ns % DAY_NS == 0


def exit_bucket(entry_bucket: int, hold_bars: int) -> int:
    return entry_bucket + hold_bars - 1


class ThreeRedDaysConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    streak_len: int = 3
    hold_bars: int = 3
    close_positions_on_stop: bool = True


@dataclass(frozen=True)
class OrderIntent:
    side: int
    qty: Decimal


class ThreeRedDays(Strategy):
    def __init__(self, config: ThreeRedDaysConfig) -> None:
        super().__init__(config)
        self._daily = BarAggregator(86_400)
        self._streak = 0
        self._in_position = False
        self._entry_bucket: int | None = None
        self._signed_qty = Decimal("0")

    def on_start(self) -> None:
        self.subscribe_bars(self.config.bar_type)

    def on_stop(self) -> None:
        if self.config.close_positions_on_stop:
            self.close_all_positions(self.config.instrument_id)

    def on_bar(self, bar: Bar) -> None:
        ts = int(bar.ts_init)
        for intent in self._on_1m(
            ts,
            bar.open.as_double(),
            bar.high.as_double(),
            bar.low.as_double(),
            bar.close.as_double(),
        ):
            self._execute(intent)

    def _on_1m(
        self,
        ts_close_ns: int,
        o: float,
        h: float,
        l: float,
        c: float,
        v: float = 0.0,
    ) -> list[OrderIntent]:
        intents: list[OrderIntent] = []
        if is_last_minute(ts_close_ns):
            intents.extend(self._on_last_minute(ts_close_ns))
        day = self._daily.update(ts_close_ns, o, h, l, c, v)
        if day is not None:
            intents.extend(
                self._on_completed_day(day.open, day.close, ts_close_ns, day.ts_close_ns)
            )
        return intents

    def _sell_qty(self) -> Decimal:
        return abs(self._signed_qty) if self._signed_qty != 0 else self.config.trade_size

    def _clear_position(self) -> None:
        self._in_position = False
        self._entry_bucket = None
        self._signed_qty = Decimal("0")

    def _on_last_minute(self, ts_close_ns: int) -> list[OrderIntent]:
        if not self._in_position or self._entry_bucket is None:
            return []
        if utc_bucket(ts_close_ns) != exit_bucket(self._entry_bucket, self.config.hold_bars):
            return []
        qty = self._sell_qty()
        self._clear_position()
        return [OrderIntent(-1, qty)]

    def _on_completed_day(
        self,
        open_px: float,
        close_px: float,
        now_ts_close_ns: int,
        completed_ts_close_ns: int,
    ) -> list[OrderIntent]:
        intents: list[OrderIntent] = []
        sold = False
        if self._in_position and self._entry_bucket is not None:
            if utc_bucket(completed_ts_close_ns) == exit_bucket(
                self._entry_bucket, self.config.hold_bars
            ):
                intents.append(OrderIntent(-1, self._sell_qty()))
                self._clear_position()
                sold = True
        self._streak = next_streak(self._streak, is_red(open_px, close_px))
        if (
            not sold
            and not self._in_position
            and self._streak == self.config.streak_len
        ):
            qty = self.config.trade_size
            self._in_position = True
            self._entry_bucket = utc_bucket(now_ts_close_ns)
            self._signed_qty = qty
            intents.append(OrderIntent(1, qty))
        return intents

    def on_event(self, event) -> None:
        from nautilus_trader.model.events import OrderFilled

        if not isinstance(event, OrderFilled) or event.instrument_id != self.config.instrument_id:
            return
        signed = event.last_qty * (
            Decimal(1) if event.order_side == OrderSide.BUY else Decimal(-1)
        )
        self._signed_qty += signed
        self._in_position = self._signed_qty > 0
        if not self._in_position:
            self._entry_bucket = None

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
