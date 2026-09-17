"""Pure Bollinger-band mean-reversion benchmark.

NY RTH sessions only, entry on re-entry cross back inside the band.
No volume or open-interest condition: the control for volume/OI variants.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from nautilus_trader.config import StrategyConfig
from nautilus_trader.indicators import AverageTrueRange, BollingerBands, MovingAverageType
from nautilus_trader.model import Bar, BarType, InstrumentId
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.trading import Strategy

from sngw_trader.indicators.oi_mean_reversion import (
    bar_type_matches,
    reentry_side,
)
from sngw_trader.strategies.oi_order_lifecycle import handle_order_failure, request_flatten


@dataclass
class _PendingSetup:
    side: int
    age: int


class BbMeanReversionConfig(StrategyConfig, frozen=True, kw_only=True):
    use_hyphens_in_client_order_ids: bool = False
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    bb_period: int = 20
    bb_std: float = 2.0
    atr_period: int = 14
    atr_mult: float = 2.5
    reentry_bars: int = 5
    session_timezone: str = "America/New_York"
    session_start: str = "09:30"
    session_end: str = "16:00"
    max_trades_per_session: int = 3
    close_positions_on_stop: bool = True
    tp_mode: str = "band"  # "band": opposite BB band (spec 2026-09-16) | "atr": fill ± tp_atr_mult * ATR
    tp_atr_mult: float = 2.0
    hold_across_sessions: bool = False


class BbMeanReversion(Strategy):
    """Price-band excursion plus re-entry cross, nothing else."""

    def __init__(self, config: BbMeanReversionConfig) -> None:
        super().__init__(config)
        if config.tp_mode not in {"band", "atr"}:
            raise ValueError(f"tp_mode must be 'band' or 'atr', got {config.tp_mode!r}")
        self._bb = BollingerBands(config.bb_period, config.bb_std)
        self._atr = AverageTrueRange(
            config.atr_period,
            MovingAverageType.WILDER,
            use_previous=True,
        )
        self._tz = ZoneInfo(config.session_timezone)
        self._session_start = _parse_clock(config.session_start)
        self._session_end = _parse_clock(config.session_end)
        self._pending_setup: _PendingSetup | None = None
        self._captured_atr: float | None = None
        self._captured_target: float | None = None
        self._entry_order = None
        self._entry_order_id = None
        self._entry_order_active = False
        self._entry_order_qty = Decimal("0")
        self._entry_filled_qty = Decimal("0")
        self._entry_filled_notional = Decimal("0")
        self._entry_fill_counted = False
        self._entry_side: int | None = None
        self._stop_order = None
        self._target_order = None
        self._stop_order_id = None
        self._target_order_id = None
        self._signed_qty = Decimal("0")
        self._session_date: date | None = None
        self._session_trade_count = 0
        self._force_flat_date: date | None = None
        self._force_flat_close_pending = False
        self._late_fill_close_requested = False
        self._protective_failure_handled = False

    def on_start(self) -> None:
        self.subscribe_bars(self.config.bar_type)

    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        if self.config.close_positions_on_stop:
            self.close_all_positions(self.config.instrument_id)

    def on_bar(self, bar: Bar) -> None:
        if not bar_type_matches(bar.bar_type, self.config.bar_type):
            return

        ts = _bar_timestamp(bar)
        local_dt = datetime.fromtimestamp(
            ts / 1_000_000_000,
            tz=timezone.utc,
        ).astimezone(self._tz)
        high = bar.high.as_double()
        low = bar.low.as_double()
        close = bar.close.as_double()
        self._bb.update_raw(high, low, close)
        self._atr.update_raw(high, low, close)

        self._reset_session_if_needed(local_dt.date())
        if local_dt.weekday() >= 5 or local_dt.time() >= self._session_end:
            if not self.config.hold_across_sessions:
                self._force_flat(local_dt)
            return
        if not self._session_is_valid(local_dt):
            return

        if not self._bb.initialized or not self._atr.initialized:
            return

        lower = float(self._bb.lower)
        upper = float(self._bb.upper)
        atr = float(self._atr.value)

        if self._pending_setup is not None:
            self._process_pending(
                close=close,
                lower=lower,
                upper=upper,
                atr=atr,
                target=upper if self._pending_setup.side == 1 else lower,
                submit=True,
            )
            return

        if not self._can_submit_entry(local_dt):
            return
        if close < lower:
            self._pending_setup = _PendingSetup(1, 0)
        elif close > upper:
            self._pending_setup = _PendingSetup(-1, 0)

    def on_order_filled(self, event: OrderFilled) -> None:
        if event.instrument_id != self.config.instrument_id:
            return
        quantity = _decimal_value(event.last_qty)
        fill_price = _decimal_value(event.last_px)

        if self._same_order_id(event.client_order_id, self._entry_order_id):
            signed = quantity if self._entry_side == 1 else -quantity
            self._signed_qty += signed
            self._entry_filled_qty += quantity
            self._entry_filled_notional += quantity * fill_price
            fill_dt = datetime.fromtimestamp(
                int(event.ts_event) / 1_000_000_000,
                tz=timezone.utc,
            ).astimezone(self._tz)
            self._reset_session_if_needed(fill_dt.date())
            if not self._entry_fill_counted:
                self._session_trade_count += 1
                self._entry_fill_counted = True
            average_fill = self._entry_filled_notional / self._entry_filled_qty
            self._submit_bracket(
                fill_price=float(average_fill),
                quantity=abs(self._signed_qty),
                side=self._entry_side,
            )
            if self._force_flat_date is not None and not self._late_fill_close_requested:
                self._late_fill_close_requested = True
                request_flatten(self, force=True)
            if self._entry_filled_qty >= self._entry_order_qty:
                self._entry_order_active = False
                self._entry_order = None
                self._entry_order_id = None
            return

        signed = quantity if event.order_side == OrderSide.BUY else -quantity
        self._signed_qty += signed
        if self._signed_qty == 0:
            if self._stop_order_id is not None or self._target_order_id is not None:
                self.cancel_all_orders(self.config.instrument_id)
            self._clear_trade_state()
        elif self._stop_order is not None and self._target_order is not None:
            self._modify_bracket_quantity(abs(self._signed_qty))

    def on_order_canceled(self, event) -> None:
        if self._same_order_id(
            getattr(event, "client_order_id", None),
            self._entry_order_id,
        ):
            self._entry_order_active = False
            self._entry_order = None
            if self._signed_qty == 0 and self._force_flat_date is None:
                self._clear_entry_state()
        if self._same_order_id(
            getattr(event, "client_order_id", None),
            self._stop_order_id,
        ):
            self._stop_order = None
            self._stop_order_id = None
        if self._same_order_id(
            getattr(event, "client_order_id", None),
            self._target_order_id,
        ):
            self._target_order = None
            self._target_order_id = None

    def on_order_rejected(self, event) -> None:
        handle_order_failure(self, event)

    def on_order_denied(self, event) -> None:
        handle_order_failure(self, event)

    def on_order_expired(self, event) -> None:
        handle_order_failure(self, event)

    def _process_pending(
        self,
        *,
        close: float,
        lower: float,
        upper: float,
        atr: float,
        target: float,
        submit: bool,
    ) -> bool:
        setup = self._pending_setup
        if setup is None:
            return False
        setup.age += 1
        if reentry_side(setup.side, close, lower, upper):
            if submit:
                self._captured_atr = atr
                self._captured_target = target
                self._submit_entry(setup.side)
            self._pending_setup = None
            return True
        if setup.age >= self.config.reentry_bars:
            self._pending_setup = None
        return False

    def _submit_entry(self, side: int) -> None:
        instrument = self._instrument()
        if instrument is None:
            return
        quantity = instrument.make_qty(self.config.trade_size)
        if quantity == 0:
            return
        order = self.order_factory.market(
            self.config.instrument_id,
            OrderSide.BUY if side == 1 else OrderSide.SELL,
            quantity,
        )
        self._entry_order = order
        self._entry_order_id = order.client_order_id
        self._entry_order_active = True
        self._entry_order_qty = quantity
        self._entry_filled_qty = Decimal("0")
        self._entry_filled_notional = Decimal("0")
        self._entry_fill_counted = False
        self._entry_side = side
        self.submit_order(order)

    def _submit_bracket(self, *, fill_price: float, quantity: Decimal, side: int) -> None:
        if self._captured_atr is None or self._captured_target is None:
            return
        if self.config.tp_mode == "atr":
            target = (
                fill_price + self.config.tp_atr_mult * self._captured_atr
                if side == 1
                else fill_price - self.config.tp_atr_mult * self._captured_atr
            )
        else:
            target = self._captured_target
        if (side == 1 and target <= fill_price) or (side == -1 and target >= fill_price):
            request_flatten(self)
            return
        instrument = self._instrument()
        if instrument is None:
            return
        stop_value = fill_price - self.config.atr_mult * self._captured_atr
        if side == -1:
            stop_value = fill_price + self.config.atr_mult * self._captured_atr
        stop = instrument.make_price(Decimal(str(stop_value)))
        limit = instrument.make_price(Decimal(str(target)))
        quantity = instrument.make_qty(quantity)
        if stop == 0 or limit == 0 or quantity == 0:
            return
        if self._stop_order is not None and self._target_order is not None:
            self.modify_order(self._stop_order, quantity=quantity, trigger_price=stop)
            self.modify_order(self._target_order, quantity=quantity, price=limit)
            return
        exit_side = OrderSide.SELL if side == 1 else OrderSide.BUY
        stop_order = self.order_factory.stop_market(
            instrument_id=self.config.instrument_id,
            order_side=exit_side,
            quantity=quantity,
            trigger_price=stop,
            reduce_only=True,
        )
        target_order = self.order_factory.limit(
            instrument_id=self.config.instrument_id,
            order_side=exit_side,
            quantity=quantity,
            price=limit,
            reduce_only=True,
        )
        self._stop_order = stop_order
        self._target_order = target_order
        self._stop_order_id = stop_order.client_order_id
        self._target_order_id = target_order.client_order_id
        self.submit_order(stop_order)
        self.submit_order(target_order)

    def _modify_bracket_quantity(self, quantity: Decimal) -> None:
        self.modify_order(self._stop_order, quantity=quantity)
        self.modify_order(self._target_order, quantity=quantity)

    def _instrument(self):
        cache = self.cache
        return None if cache is None else cache.instrument(self.config.instrument_id)

    def _force_flat(self, local_dt: datetime) -> None:
        self._pending_setup = None
        if self._force_flat_date == local_dt.date() and self._force_flat_close_pending:
            return
        self._force_flat_date = local_dt.date()
        self.cancel_all_orders(self.config.instrument_id)
        request_flatten(self, force=True)
        self._force_flat_close_pending = True

    def _clear_entry_state(self) -> None:
        self._entry_order = None
        self._entry_order_id = None
        self._entry_order_active = False
        self._entry_order_qty = Decimal("0")
        self._entry_filled_qty = Decimal("0")
        self._entry_filled_notional = Decimal("0")
        self._entry_fill_counted = False
        self._entry_side = None

    def _clear_trade_state(self) -> None:
        self._captured_atr = None
        self._captured_target = None
        self._clear_entry_state()
        self._force_flat_date = None
        self._stop_order = None
        self._target_order = None
        self._stop_order_id = None
        self._target_order_id = None
        self._pending_setup = None
        self._force_flat_close_pending = False
        self._late_fill_close_requested = False
        self._protective_failure_handled = False

    def _reset_session_if_needed(self, session_date: date) -> None:
        if self._session_date != session_date:
            self._session_date = session_date
            self._session_trade_count = 0
            self._force_flat_close_pending = False
            self._late_fill_close_requested = False
            if (
                self._signed_qty == 0
                and not self._entry_order_active
                and self._force_flat_date != session_date
            ):
                self._clear_entry_state()
                self._force_flat_date = None

    def _session_is_valid(self, local_dt: datetime) -> bool:
        local_dt = local_dt.astimezone(self._tz)
        return (
            local_dt.weekday() < 5
            and self._session_start <= local_dt.time() < self._session_end
        )

    def _can_submit_entry(self, local_dt: datetime) -> bool:
        self._reset_session_if_needed(local_dt.date())
        return (
            self._session_is_valid(local_dt)
            and self._session_trade_count < self.config.max_trades_per_session
            and self._signed_qty == 0
            and self._entry_order is None
            and self._entry_order_id is None
            and not self._entry_order_active
            and self._pending_setup is None
        )

    @staticmethod
    def _same_order_id(left, right) -> bool:
        return left is not None and right is not None and str(left) == str(right)

    # These helpers inject state for unit tests only. They never submit orders.
    def _test_start_setup(
        self,
        *,
        side: int,
        close: float,
        lower: float | None = None,
        upper: float | None = None,
    ) -> bool:
        if side == 1 and lower is not None and close >= lower:
            raise ValueError("long breach requires close < lower")
        if side == -1 and upper is not None and close <= upper:
            raise ValueError("short breach requires close > upper")
        if side not in (1, -1):
            raise ValueError("side must be 1 or -1")
        self._pending_setup = _PendingSetup(side, 0)
        return True

    def _test_reentry(
        self,
        *,
        side: int,
        close: float,
        lower: float,
        upper: float,
    ) -> bool:
        if self._pending_setup is None:
            raise AssertionError("pending setup is required")
        if side != self._pending_setup.side:
            raise ValueError("re-entry side must match pending setup")
        return self._process_pending(
            close=close,
            lower=lower,
            upper=upper,
            atr=1.0,
            target=upper if side == 1 else lower,
            submit=False,
        )

    def _test_advance_pending(
        self,
        *,
        close: float,
        lower: float,
        upper: float,
    ) -> None:
        if self._pending_setup is None:
            raise AssertionError("pending setup is required")
        self._test_reentry(
            side=self._pending_setup.side,
            close=close,
            lower=lower,
            upper=upper,
        )

    def _test_force_flat(self, local_dt: datetime) -> None:
        self._force_flat(local_dt.astimezone(self._tz))


def _parse_clock(value: str) -> time:
    hour, minute = (int(part) for part in value.split(":", 1))
    return time(hour, minute)


def _bar_timestamp(bar: Bar) -> int:
    value = getattr(bar, "ts_event", None)
    if value is None:
        value = bar.ts_init
    return int(value)


def _decimal_value(value) -> Decimal:
    return value.as_decimal() if hasattr(value, "as_decimal") else Decimal(str(value))