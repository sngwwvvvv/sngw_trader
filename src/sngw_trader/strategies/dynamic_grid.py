"""Dynamic Grid Trading. Strategy + Config only. No node / OKX adapter."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model import Bar, BarType, InstrumentId
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.objects import Currency
from nautilus_trader.trading import Strategy


def geometric_levels(center: Decimal, k: Decimal, h: int) -> list[Decimal]:
    if h < 0:
        raise ValueError("h must be >= 0")
    return [center * (1 + k) ** i for i in range(-h, h + 1)]


def quantize_price(instrument, value: Decimal):
    try:
        px = instrument.make_price(value)
    except ValueError:
        return None
    return None if px == 0 else px


def quantize_qty(instrument, value: Decimal):
    try:
        qty = instrument.make_qty(value)
    except ValueError:
        return None
    return None if qty == 0 else qty


def sell_increments(remaining_qty: Decimal, n: int) -> list[Decimal]:
    if n <= 0:
        return []
    out: list[Decimal] = []
    rem = remaining_qty
    for i in range(1, n + 1):
        g = n - i + 1
        q = rem / g
        out.append(q)
        rem -= q
    return out


def buy_increments(remaining_cash: Decimal, prices: list[Decimal]) -> list[Decimal]:
    n = len(prices)
    if n == 0:
        return []
    out: list[Decimal] = []
    rem = remaining_cash
    for j, px in enumerate(prices, 1):
        g = n - j + 1
        cash_j = rem / g
        out.append(cash_j / px)
        rem -= cash_j
    return out


def sell_ladder(remaining_qty: Decimal, levels_up: list[Decimal]) -> list[tuple[Decimal, Decimal]]:
    return list(zip(levels_up, sell_increments(remaining_qty, len(levels_up))))


def buy_ladder(remaining_cash: Decimal, levels_down: list[Decimal]) -> list[tuple[Decimal, Decimal]]:
    return list(zip(levels_down, buy_increments(remaining_cash, levels_down)))


@dataclass
class GridBook:
    h: int
    k: Decimal
    reset_enabled: bool
    bag_qty: Decimal = Decimal("0")
    grid_qty: Decimal = Decimal("0")
    working_cash: Decimal = Decimal("0")
    center: Decimal | None = None
    levels: tuple[Decimal, ...] = ()
    active: bool = False
    stopped: bool = False

    @property
    def lower(self) -> Decimal | None:
        return self.levels[0] if self.levels else None

    @property
    def upper(self) -> Decimal | None:
        return self.levels[-1] if self.levels else None

    def can_open(self) -> bool:
        return not self.stopped

    def open_grid(self, m: Decimal, price: Decimal, min_qty: Decimal) -> bool:
        if self.stopped:
            return False
        qty = (m / 2) / price
        if qty < min_qty:
            return False
        self.center = price
        self.levels = tuple(geometric_levels(price, self.k, self.h))
        self.grid_qty = qty
        self.working_cash = m / 2
        self.active = True
        return True

    def apply_buy(self, qty: Decimal, price: Decimal) -> None:
        self.grid_qty += qty
        self.working_cash -= qty * price
        if self.working_cash < 0:
            self.working_cash = Decimal("0")

    def apply_sell(self, qty: Decimal, price: Decimal) -> None:
        filled = qty if qty <= self.grid_qty else self.grid_qty
        self.grid_qty -= filled
        self.working_cash += filled * price

    def working_orders(
        self, last_price: Decimal
    ) -> tuple[list[tuple[Decimal, Decimal]], list[tuple[Decimal, Decimal]]]:
        if not self.active or not self.levels:
            return [], []
        up = [lv for lv in self.levels[self.h + 1 :] if lv > last_price]
        down = [
            self.levels[self.h - i]
            for i in range(1, self.h + 1)
            if self.levels[self.h - i] < last_price
        ]
        return sell_ladder(self.grid_qty, up), buy_ladder(self.working_cash, down)

    def should_reset_upper(self, price: Decimal) -> bool:
        return self.active and self.upper is not None and (price > self.upper or self.grid_qty == 0)

    def should_reset_lower(self, price: Decimal) -> bool:
        return self.active and self.lower is not None and (
            price < self.lower or self.working_cash == 0
        )

    def reset_upper(self) -> None:
        self.grid_qty = Decimal("0")
        self.active = False
        if not self.reset_enabled:
            self.stopped = True

    def reset_lower(self) -> None:
        self.bag_qty += self.grid_qty
        self.grid_qty = Decimal("0")
        self.working_cash = Decimal("0")
        self.active = False
        if not self.reset_enabled:
            self.stopped = True


class DynamicGridConfig(StrategyConfig, frozen=True, kw_only=True):  # ponytail: kw_only - StrategyConfig's optional fields precede ours, msgspec forbids required-after-optional
    instrument_id: InstrumentId
    bar_type: BarType
    grid_size: float
    grid_numbers_half: int
    reset_enabled: bool = True
    close_positions_on_stop: bool = False
    use_hyphens_in_client_order_ids: bool = False
    order_id_tag: str = "DGT"


class DynamicGrid(Strategy):
    def __init__(self, config: DynamicGridConfig) -> None:
        super().__init__(config)
        self._book = GridBook(
            h=config.grid_numbers_half,
            k=Decimal(str(config.grid_size)),
            reset_enabled=config.reset_enabled,
        )
        self._in_reset = False
        self._opening_id: str | None = None
        self._last_price: Decimal | None = None

    def on_start(self) -> None:
        self.subscribe_bars(self.config.bar_type)

    def on_stop(self) -> None:
        self.cancel_all_orders(self.config.instrument_id)
        if self.config.close_positions_on_stop:
            self.close_all_positions(self.config.instrument_id)

    def on_bar(self, bar: Bar) -> None:
        if bar.bar_type != self.config.bar_type:
            return
        price = Decimal(str(bar.close))
        self._last_price = price
        if self._book.stopped:
            return
        if not self._book.active:
            self._try_start(price)
            return
        if self._book.should_reset_upper(price):
            self._reset_upper(price)
            return
        if self._book.should_reset_lower(price):
            self._reset_lower(price)
            return
        self._place_ladder()

    def on_order_filled(self, event: OrderFilled) -> None:
        if event.instrument_id != self.config.instrument_id:
            return
        qty = event.last_qty.as_decimal()
        px = event.last_px.as_decimal()
        if self._opening_id is not None and event.client_order_id == self._opening_id:
            self._opening_id = None
            if self._book.active and not self._book.stopped:
                self._place_ladder()
            return
        if event.order_side == OrderSide.BUY:
            self._book.apply_buy(qty, px)
        else:
            self._book.apply_sell(qty, px)
        if self._in_reset or self._book.stopped or not self._book.active:
            return
        self.cancel_all_orders(self.config.instrument_id)
        self._place_ladder()

    def _free_usdt(self) -> Decimal:
        account = self.cache.account_for_venue(self.config.instrument_id.venue)
        if account is None:
            return Decimal("0")
        bal = account.balance(Currency.from_str("USDT"))
        if bal is None:
            return Decimal("0")
        return bal.free.as_decimal()

    def _instrument(self):
        return self.cache.instrument(self.config.instrument_id)

    def _min_qty(self, instrument) -> Decimal:
        raw = getattr(instrument, "min_quantity", None)
        if raw is None:
            return Decimal("0")
        return raw.as_decimal() if hasattr(raw, "as_decimal") else Decimal(str(raw))

    def _try_start(self, price: Decimal) -> None:
        instrument = self._instrument()
        if instrument is None:
            self.log.error(f"Instrument not in cache: {self.config.instrument_id}")
            return
        m = self._free_usdt()
        min_qty = self._min_qty(instrument)
        probe = (m / 2) / price if price != 0 else Decimal("0")
        q = quantize_qty(instrument, probe)
        if q is None:
            return
        if not self._book.open_grid(m, price, min_qty):
            return
        order = self.order_factory.market(self.config.instrument_id, OrderSide.BUY, q)
        self._opening_id = order.client_order_id
        self.submit_order(order)

    def _place_ladder(self) -> None:
        instrument = self._instrument()
        if instrument is None or self._last_price is None or not self._book.active:
            return
        self.cancel_all_orders(self.config.instrument_id)
        sells, buys = self._book.working_orders(self._last_price)
        for px, qty in sells:
            self._submit_limit(instrument, OrderSide.SELL, px, qty, reduce_only=True)
        for px, qty in buys:
            self._submit_limit(instrument, OrderSide.BUY, px, qty, reduce_only=False)

    def _submit_limit(self, instrument, side: OrderSide, px: Decimal, qty: Decimal, *, reduce_only: bool) -> None:
        price = quantize_price(instrument, px)
        q = quantize_qty(instrument, qty)
        if price is None or q is None:
            return
        self.submit_order(
            self.order_factory.limit(
                instrument_id=self.config.instrument_id,
                order_side=side,
                quantity=q,
                price=price,
                time_in_force=TimeInForce.GTC,
                reduce_only=reduce_only,
            )
        )

    def _reset_upper(self, price: Decimal) -> None:
        self._in_reset = True
        try:
            self.cancel_all_orders(self.config.instrument_id)
            instrument = self._instrument()
            if instrument is not None and self._book.grid_qty > 0:
                q = quantize_qty(instrument, self._book.grid_qty)
                if q is not None:
                    self.submit_order(
                        self.order_factory.market(
                            self.config.instrument_id,
                            OrderSide.SELL,
                            q,
                            reduce_only=True,
                        )
                    )
            self._book.reset_upper()
            if self._book.can_open():
                self._try_start(price)
        finally:
            self._in_reset = False

    def _reset_lower(self, price: Decimal) -> None:
        self._in_reset = True
        try:
            self.cancel_all_orders(self.config.instrument_id)
            self._book.reset_lower()
            if self._book.can_open():
                self._try_start(price)
        finally:
            self._in_reset = False