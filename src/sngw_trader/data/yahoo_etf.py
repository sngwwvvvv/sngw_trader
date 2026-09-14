"""US ETF daily bars from Yahoo into ParquetDataCatalog.

Do not place orders here. Do not import runners or strategies.
"""

from __future__ import annotations

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
