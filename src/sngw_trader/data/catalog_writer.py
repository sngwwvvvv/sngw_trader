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
from sngw_trader.data.funding import fetch_funding_rates

_HISTORY_URL = "https://www.okx.com/api/v5/market/history-candles"
_MARK_HISTORY_URL = "https://www.okx.com/api/v5/market/history-mark-price-candles"

_BAR_SPEC = {"1H": "1-HOUR", "1m": "1-MINUTE"}


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


def load_all_instruments(settings: Settings) -> dict:
    """All OKX USDT-SWAP instruments keyed by InstrumentId."""
    from nautilus_trader.adapters.okx import OKXInstrumentProvider
    from nautilus_trader.core.nautilus_pyo3.okx import OKXInstrumentType

    provider = OKXInstrumentProvider(
        _okx_http_client(settings),
        instrument_types=(OKXInstrumentType.SWAP,),
    )
    provider.load_all()
    return provider.get_all()


def _fetch_okx(url: str, params: dict) -> list[list[str]]:
    req = urllib.request.Request(
        url + "?" + urllib.parse.urlencode(params), headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("code") != "0":
        raise RuntimeError(f"OKX API error: {payload}")
    return payload["data"]


def _fetch_candles(symbol: str, after_ms: int | None, limit: int = 300) -> list[list[str]]:
    params = {"instId": symbol, "bar": "1m", "limit": str(limit)}
    if after_ms is not None:
        params["after"] = str(after_ms)
    return _fetch_okx(_HISTORY_URL, params)


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


def raw_mark_candle_to_bar(
    raw: list[str], instrument_id: str, price_prec: int, size_prec: int, bar: str = "1H"
) -> Bar:
    """row: [ts, o, h, l, c, confirm]; mark candles carry no volume."""
    ts_ns = int(raw[0]) * 1_000_000
    bar_type = BarType.from_str(f"{instrument_id}-{_BAR_SPEC[bar]}-MARK-EXTERNAL")
    scale = Decimal(10) ** 9
    return Bar.from_raw(
        bar_type=bar_type,
        open=Decimal(raw[1]) * scale,
        high=Decimal(raw[2]) * scale,
        low=Decimal(raw[3]) * scale,
        close=Decimal(raw[4]) * scale,
        price_prec=price_prec,
        volume=Decimal("0"),
        size_prec=size_prec,
        ts_event=ts_ns,
        ts_init=ts_ns,
    )


def download_mark_bars(settings: Settings, instrument: Instrument, symbol: str, bar: str = "1H") -> list[Bar]:
    _validate_range(settings)
    start_ms = int(settings.catalog_start.astimezone(timezone.utc).timestamp() * 1000)
    end_ms = int(settings.catalog_end.astimezone(timezone.utc).timestamp() * 1000)
    bars: list[Bar] = []
    oldest = None
    while True:
        rows = _fetch_okx(_MARK_HISTORY_URL, {"instId": symbol, "bar": bar, "limit": "100", **({"after": str(oldest)} if oldest else {})})
        if not rows:
            break
        for raw in rows:
            ts_ms = int(raw[0])
            if start_ms <= ts_ms <= end_ms:
                bars.append(raw_mark_candle_to_bar(raw, f"{symbol}.OKX",
                                                   instrument.price_precision, instrument.size_precision, bar))
        oldest = int(rows[-1][0])
        if oldest < start_ms or len(rows) < 100:
            break
        time.sleep(0.15)  # OKX public rate limit guard
    bars.sort(key=lambda b: b.ts_init)  # catalog requires ascending ts_init
    return bars


def download_universe_mark_bars(settings: Settings, catalog: ParquetDataCatalog, symbols, bar: str = "1H") -> dict:
    instruments = load_all_instruments(settings)
    report: dict = {}
    for symbol in symbols:
        inst_id = InstrumentId.from_str(f"{symbol}.OKX")
        instrument = instruments.get(inst_id)
        if instrument is None:
            raise ValueError(f"no OKX SWAP instrument for {inst_id}")
        catalog.write_data([instrument], data_cls=Instrument)
        bars = download_mark_bars(settings, instrument, symbol, bar)
        if not bars:
            report[symbol] = {"n": 0, "first": None, "last": None}
            continue
        catalog.write_data(bars, data_cls=Bar)
        report[symbol] = {
            "n": len(bars),
            "first": _fmt(bars[0].ts_event),
            "last": _fmt(bars[-1].ts_event),
        }
        print(f"{symbol}: {len(bars)} bars {_fmt(bars[0].ts_event)} -> {_fmt(bars[-1].ts_event)}")
    return report


def write_funding_history(inst_ids, start_ms: int, end_ms: int, out_dir: Path) -> dict[str, dict[int, float]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    history: dict[str, dict[int, float]] = {}
    for inst_id in inst_ids:
        rates = fetch_funding_rates(inst_id, start_ms, end_ms)
        history[inst_id] = rates
        with (out_dir / f"{inst_id}.json").open("w", encoding="utf-8") as f:
            json.dump({str(ts): rate for ts, rate in sorted(rates.items())}, f)
    return history


def run_download(settings: Settings, catalog: ParquetDataCatalog) -> None:
    if settings.catalog_source == "yahoo-etf":
        from sngw_trader.data.yahoo_etf import download_and_write

        download_and_write(settings, catalog)
        return
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


def main() -> None:
    settings = load_settings()
    settings.catalog_path.mkdir(parents=True, exist_ok=True)
    catalog = ParquetDataCatalog(settings.catalog_path)
    run_download(settings, catalog)


def _fmt(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).isoformat()
