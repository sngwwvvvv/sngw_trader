"""US ETF daily bars from Yahoo into ParquetDataCatalog.

Do not place orders here. Do not import runners or strategies.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from nautilus_trader.model import Bar, BarType
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Price, Quantity

_PRICE_PREC = 2
_SIZE_PREC = 0
_RAW_SCALE = Decimal(10) ** 9

_VENUE = {
    "SPY": "ARCA",
    "IWM": "ARCA",
    "QQQ": "NASDAQ",
}


def venue_for(symbol: str) -> str:
    return _VENUE.get(symbol.strip().upper(), "ARCA")


def instrument_id_for(symbol: str) -> str:
    sym = symbol.strip().upper()
    return f"{sym}.{venue_for(sym)}"


def etf_bar_type(instrument_id: str) -> str:
    return f"{instrument_id}-1-DAY-LAST-EXTERNAL"


def parse_etf_symbols(raw: str) -> list[str]:
    return [part.strip().upper() for part in raw.split(",") if part.strip()]


def _ts_ns(when: datetime) -> int:
    if hasattr(when, "to_pydatetime"):
        when = when.to_pydatetime()
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    else:
        when = when.astimezone(timezone.utc)
    return int(when.timestamp() * 1_000_000_000)


def _raw_px(value: object) -> Decimal:
    return Decimal(str(value)) * _RAW_SCALE


def _raw_qty(value: object | None) -> Decimal:
    if value is None or value == "":
        return Decimal(0)
    text = str(value)
    if text.lower() == "nan":
        return Decimal(0)
    return Decimal(text) * _RAW_SCALE


def row_to_bar(
    instrument_id: str,
    when: datetime,
    open_: object,
    high: object,
    low: object,
    close: object,
    volume: object | None = None,
) -> Bar:
    ts_ns = _ts_ns(when)
    return Bar.from_raw(
        bar_type=BarType.from_str(etf_bar_type(instrument_id)),
        open=_raw_px(open_),
        high=_raw_px(high),
        low=_raw_px(low),
        close=_raw_px(close),
        price_prec=_PRICE_PREC,
        volume=_raw_qty(volume),
        size_prec=_SIZE_PREC,
        ts_event=ts_ns,
        ts_init=ts_ns,
    )


def build_equity(symbol: str) -> Equity:
    instrument_id = instrument_id_for(symbol)
    raw = symbol.strip().upper()
    return Equity(
        instrument_id=InstrumentId.from_str(instrument_id),
        raw_symbol=Symbol(raw),
        currency=USD,
        price_precision=_PRICE_PREC,
        price_increment=Price.from_str("0.01"),
        lot_size=Quantity.from_int(1),
        ts_event=0,
        ts_init=0,
    )
