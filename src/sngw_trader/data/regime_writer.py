"""Download macro inputs, align onto master sessions, write RegimeSnapshot
catalog + regime_manifest.json.

Network is injectable: fetch callables are parameters so tests stay offline.
Do not place orders here. Do not import runners or strategies.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from sngw_trader.config import load_settings
from sngw_trader.data.catalog_writer import _validate_range
from sngw_trader.data.copper_gold import futures_quality_report, paired_ratio
from sngw_trader.data.fred_macro import fetch_fred_series
from sngw_trader.data.regime_align import build_snapshots
from sngw_trader.data.regime_snapshot import RegimeSnapshot
from sngw_trader.data.regime_universe import (
    FEATURE_VERSION,
    FRED_OAS,
    FRED_VIX,
    FRED_VXV,
    SECTOR_ETFS,
    STALE_SESSIONS,
    YAHOO_COPPER,
    YAHOO_GOLD,
    instrument_id_for,
)
from sngw_trader.data.yahoo_etf import (
    download_and_write,
    etf_bar_type,
    fetch_daily_bars,
)
from sngw_trader.indicators.regime import IQR_LONG, MEDIAN_WINDOW, Z0

PARAMS: dict = {
    "median_window": MEDIAN_WINDOW,
    "iqr_long": IQR_LONG,
    "z0": Z0,
    "stale_sessions": STALE_SESSIONS,
}


def input_hash(parts: dict[str, bytes | str]) -> str:
    """sha256 over sorted keys, each `key\\0value\\0` — stable across runs."""
    digest = hashlib.sha256()
    for key in sorted(parts):
        value = parts[key]
        digest.update(key.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(value.encode("utf-8") if isinstance(value, str) else value)
        digest.update(b"\x00")
    return digest.hexdigest()


def write_manifest(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def bars_ts_event_by_date(bars: list) -> dict[date, int]:
    out: dict[date, int] = {}
    for bar in bars:
        d = datetime.fromtimestamp(bar.ts_event / 1e9, tz=timezone.utc).date()
        out[d] = bar.ts_event
    return out


def master_sessions(etf_dates: dict[str, set[date]]) -> list[date]:
    """Intersection of the 9 sector ETFs, ascending (CME-only days drop out)."""
    return sorted(set.intersection(*(etf_dates[s] for s in SECTOR_ETFS)))


def _series_csv(series: dict[date, float]) -> str:
    """Stable serialization: `YYYY-MM-DD,repr(value)\\n` ascending by date."""
    return "".join(f"{d.isoformat()},{v!r}\n" for d, v in sorted(series.items()))


def source_snapshot_id(inputs: dict) -> str:
    return input_hash(
        {
            "oas": _series_csv(inputs["oas"]),
            "vix": _series_csv(inputs["vix"]),
            "vxv": _series_csv(inputs["vxv"]),
            "copper": _series_csv(inputs["copper"]),
            "gold": _series_csv(inputs["gold"]),
            "params": json.dumps(PARAMS, sort_keys=True),
            "downloaded_at": inputs["downloaded_at"][:10],
        }
    )


def _pd_series(series: dict[date, float]) -> pd.Series:
    items = sorted(series.items())
    return pd.Series(
        [v for _, v in items],
        dtype=float,
        index=pd.DatetimeIndex([pd.Timestamp(d) for d, _ in items]),
    )


def _sources(inputs: dict) -> dict:
    pairs = (
        (FRED_OAS, "oas"),
        (FRED_VIX, "vix"),
        (FRED_VXV, "vxv"),
        (YAHOO_COPPER, "copper"),
        (YAHOO_GOLD, "gold"),
    )
    out: dict = {}
    for series_id, key in pairs:
        s = inputs[key]
        dates = sorted(s)
        out[series_id] = {
            "n": len(s),
            "start": dates[0].isoformat() if dates else "",
            "end": dates[-1].isoformat() if dates else "",
        }
    return out


def _futures_quality(inputs: dict) -> dict:
    copper = _pd_series(inputs["copper"])
    gold = _pd_series(inputs["gold"])
    report = futures_quality_report(copper, gold, paired_ratio(copper, gold))
    return {
        "n": report["n"],
        "copper_spikes": report["copper_spikes"],
        "gold_spikes": report["gold_spikes"],
        "ratio_spikes": report["ratio_spikes"],
    }


def run_write(catalog: ParquetDataCatalog, inputs: dict, manifest_path: Path) -> str:
    """Align inputs onto master sessions, write snapshots + manifest."""
    sessions = master_sessions(inputs["etf_dates"])
    sid = source_snapshot_id(inputs)
    copper_gold = {
        (d.date() if hasattr(d, "date") else d): float(v)
        for d, v in paired_ratio(
            _pd_series(inputs["copper"]), _pd_series(inputs["gold"])
        ).items()
    }
    snaps = build_snapshots(
        sessions,
        inputs["oas"],
        inputs["vix"],
        inputs["vxv"],
        copper_gold,
        inputs["copper"],
        inputs["gold"],
        inputs["feature_version"],
        sid,
        lambda d: inputs["bar_ts"][d],
    )
    snaps.sort(key=lambda s: s.ts_init)  # catalog requires ascending ts_init
    catalog.write_data(snaps, data_cls=RegimeSnapshot)
    write_manifest(
        manifest_path,
        {
            "source_snapshot_id": sid,
            "downloaded_at": inputs["downloaded_at"],
            "feature_version": inputs["feature_version"],
            "params": dict(PARAMS),
            "sources": _sources(inputs),
            "futures_quality": _futures_quality(inputs),
            "n_snapshots": len(snaps),
            "session_start": sessions[0].isoformat() if sessions else "",
            "session_end": sessions[-1].isoformat() if sessions else "",
        },
    )
    return sid


def download_inputs(
    settings,
    catalog: ParquetDataCatalog,
    fetch_fred=None,
    fetch_bars=None,
) -> dict:
    """Collect writer inputs. ETF bars are already in the catalog (written by
    download_and_write); FRED and HG=F/GC=F go through the injected fetchers.
    """
    fetch_fred = fetch_fred or fetch_fred_series
    fetch_bars = fetch_bars or fetch_daily_bars
    start, end = settings.catalog_start, settings.catalog_end

    bar_types = [etf_bar_type(instrument_id_for(s)) for s in SECTOR_ETFS]
    per_symbol: dict[str, list] = {s: [] for s in SECTOR_ETFS}
    for bar in catalog.bars(bar_types=bar_types):
        per_symbol[str(bar.bar_type).split("-")[0].split(".")[0]].append(bar)
    etf_dates: dict[str, set[date]] = {}
    bar_ts: dict[date, int] = {}
    for symbol in SECTOR_ETFS:
        by_date = bars_ts_event_by_date(per_symbol[symbol])
        etf_dates[symbol] = set(by_date)
        for d, ts in by_date.items():
            bar_ts.setdefault(d, ts)

    return {
        "etf_dates": etf_dates,
        "bar_ts": bar_ts,
        "oas": dict(fetch_fred(FRED_OAS, start, end)),
        "vix": dict(fetch_fred(FRED_VIX, start, end)),
        "vxv": dict(fetch_fred(FRED_VXV, start, end)),
        "copper": _closes(fetch_bars(YAHOO_COPPER, start, end)),
        "gold": _closes(fetch_bars(YAHOO_GOLD, start, end)),
        "feature_version": FEATURE_VERSION,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
    }


def _closes(df) -> dict[date, float]:
    s = df["Close"].dropna()
    s.index = [ts.date() for ts in s.index]
    return {d: float(v) for d, v in s.items()}


def main() -> None:
    settings = load_settings()
    _validate_range(settings)
    settings.catalog_path.mkdir(parents=True, exist_ok=True)
    catalog = ParquetDataCatalog(settings.catalog_path)
    download_and_write(dataclasses.replace(settings, etf_symbols=",".join(SECTOR_ETFS)), catalog)
    inputs = download_inputs(settings, catalog)
    sid = run_write(catalog, inputs, settings.catalog_path / "regime_manifest.json")
    print(f"Wrote {len(master_sessions(inputs['etf_dates']))} regime sessions ({sid})")