"""Backtest execution models: OKX fee rates as a FeeModel.

MakerTakerFeeModel reads rates from the catalog instrument, but our catalog
instruments are not guaranteed to carry OKX rates. This model takes rates
directly, so the runner controls them via settings.
"""

from __future__ import annotations

from nautilus_trader.backtest.models import FeeModel
from nautilus_trader.config import NautilusConfig
from nautilus_trader.core.message import Event
from nautilus_trader.model.enums import OrderType
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Money, Price, Quantity


class OkxRateFeeModelConfig(NautilusConfig):
    maker_fee_rate: float
    taker_fee_rate: float


class OkxRateFeeModel(FeeModel):
    """Linear fee: notional * rate. Limit orders pay maker, rest pay taker."""

    def __init__(self, config: OkxRateFeeModelConfig) -> None:
        self.maker_fee_rate = config.maker_fee_rate
        self.taker_fee_rate = config.taker_fee_rate

    def get_commission(
        self,
        order: Event,
        fill_qty: Quantity,
        fill_px: Price,
        instrument: Instrument,
    ) -> Money:
        # ponytail: liquidity side unknown at fill time; LIMIT≈maker is the same
        # approximation Nautilus's own MakerTakerFeeModel uses.
        if order.order_type == OrderType.LIMIT:
            rate = self.maker_fee_rate
        else:
            rate = self.taker_fee_rate
        notional = fill_px.as_double() * fill_qty.as_double()
        return Money(notional * rate, instrument.quote_currency)


def _selfcheck() -> None:
    from types import SimpleNamespace

    from nautilus_trader.model.currencies import USDT

    instrument = SimpleNamespace(quote_currency=USDT)
    market = SimpleNamespace(order_type=OrderType.MARKET)
    limit = SimpleNamespace(order_type=OrderType.LIMIT)
    model = OkxRateFeeModel(OkxRateFeeModelConfig(maker_fee_rate=0.0002, taker_fee_rate=0.0005))

    fee = model.get_commission(market, Quantity.from_int(1), Price.from_str("100.0"), instrument)
    assert fee == Money(0.05, USDT), fee
    fee = model.get_commission(limit, Quantity.from_int(1), Price.from_str("100.0"), instrument)
    assert fee == Money(0.02, USDT), fee
    print("ok")


if __name__ == "__main__":
    _selfcheck()
