"""Write market data into ParquetDataCatalog.

Do not place orders here. Do not start LiveNode here.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from nautilus_trader.model import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from sngw_trader.config import Settings, load_settings

_HISTORY_URL = "https://www.okx.com/api/v5/market/history-candles"


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


def _okx_http_client(settings: Settings):
    from nautilus_trader.adapters.okx.factories import get_cached_okx_http_client
    from nautilus_trader.core.nautilus_pyo3.okx import OKXEnvironment

    env = OKXEnvironment.DEMO if settings.is_demo else OKXEnvironment.LIVE
    return get_cached_okx_http_client(
        api_key="", api_secret="", api_passphrase="", environment=env
    )


def load_instrument(settings: Settings) -> Instrument:
    from nautilus_trader.adapters.okx import OKXInstrumentProvider
    from nautilus_trader.core.nautilus_pyo3.okx import OKXInstrumentType

    client = _okx_http_client(settings)
    provider = OKXInstrumentProvider(
        client,
        instrument_types=(OKXInstrumentType.SWAP,),
    )
    provider.load_all()
    instruments = provider.get_all()
    return instruments[InstrumentId.from_str(settings.instrument_id_str)]


def _fetch_candles(symbol: str, after_ms: int | None, limit: int = 300) -> list[list[str]]:
    params = {"instId": symbol, "bar": "1m", "limit": str(limit)}
    if after_ms is not None:
        params["after"] = str(after_ms)
    url = _HISTORY_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("code") != "0":
        raise RuntimeError(f"OKX history-candles error: {payload}")
    return payload["data"]


def _validate_range(settings: Settings) -> None:
    start, end = settings.catalog_start, settings.catalog_end
    if start is None or end is None:
        raise SystemExit("CATALOG_START and CATALOG_END are required for catalog download")
    if end <= start:
        raise SystemExit(f"CATALOG_END ({end}) must be after CATALOG_START ({start})")


def download_bars(
    settings: Settings,
    instrument: Instrument | None = None,
) -> list[Bar]:
    _validate_range(settings)
    if instrument is None:
        instrument = load_instrument(settings)
    symbol = settings.symbol.upper().replace(".OKX", "")
    start_ms = int(settings.catalog_start.astimezone(timezone.utc).timestamp() * 1000)
    end_ms = int(settings.catalog_end.astimezone(timezone.utc).timestamp() * 1000)

    bars: list[Bar] = []
    oldest = None
    while True:
        rows = _fetch_candles(symbol, oldest)
        if not rows:
            break
        for raw in rows:
            ts_ms = int(raw[0])
            if end_ms < ts_ms or ts_ms < start_ms:
                continue
            bars.append(
                raw_candle_to_bar(
                    raw,
                    settings.instrument_id_str,
                    instrument.price_precision,
                    instrument.size_precision,
                )
            )
        oldest = int(rows[-1][0])
        if oldest < start_ms or len(rows) < 300:
            break
        time.sleep(0.1)  # OKX rate limit guard; public candles, no auth
    bars.sort(key=lambda b: b.ts_init)  # catalog requires ascending ts_init
    return bars


def main() -> None:
    settings = load_settings()
    settings.catalog_path.mkdir(parents=True, exist_ok=True)
    catalog = ParquetDataCatalog(settings.catalog_path)

    instrument = load_instrument(settings)
    catalog.write_data([instrument], data_cls=Instrument)

    bars = download_bars(settings, instrument)
    if not bars:
        raise SystemExit("No candles downloaded for the requested range")
    catalog.write_data(bars, data_cls=Bar)

    first = min(b.ts_event for b in bars)
    last = max(b.ts_event for b in bars)
    print(
        f"Wrote {len(bars)} bars for {settings.instrument_id_str} "
        f"({_fmt(first)} -> {_fmt(last)}) to {settings.catalog_path}"
    )


def _fmt(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).isoformat()
