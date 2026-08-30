"""Write market data into ParquetDataCatalog.

Do not place orders here. Do not start LiveNode here.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from nautilus_trader.model import Bar, BarType


def raw_candle_to_bar(
    raw: list[str],
    instrument_id: str,
    price_prec: int,
    size_prec: int,
) -> Bar:
    """Map one OKX history-candles row to a Nautilus Bar.

    row: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
    """
    ts_ms = int(raw[0])
    ts_ns = ts_ms * 1_000_000
    bar_type = BarType.from_str(f"{instrument_id}-1-MINUTE-LAST-EXTERNAL")
    scale = Decimal(10) ** 9
    return Bar.from_raw(
        bar_type=bar_type,
        open=Decimal(raw[1]) * scale,
        high=Decimal(raw[2]) * scale,
        low=Decimal(raw[3]) * scale,
        close=Decimal(raw[4]) * scale,
        price_prec=price_prec,
        volume=Decimal(raw[5]) * scale,
        size_prec=size_prec,
        ts_event=ts_ns,
        ts_init=ts_ns,
    )


def write_placeholder_note(catalog_path: Path) -> Path:
    catalog_path.mkdir(parents=True, exist_ok=True)
    note = catalog_path / "README.txt"
    note.write_text(
        "Put Nautilus parquet catalog files here.\n"
        "Use ParquetDataCatalog.write_instruments / write_bars / write_quote_ticks.\n"
        "Do not call OKX order APIs from this module.\n",
        encoding="utf-8",
    )
    return note
