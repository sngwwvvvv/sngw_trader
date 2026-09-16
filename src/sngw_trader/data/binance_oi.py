"""Binance Vision OI metrics importer. Research proxy only — never live data.

Binance BTCUSDT futures metrics CSV provides `sum_open_interest` at 5m
intervals for the older era (25m in recent years). This proxies the OKX OI
hypothesis for backtests. NAUTILUS_VIBE_RULES: live is OKX only.
"""

from __future__ import annotations

import io
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone

from nautilus_trader.model import InstrumentId

from sngw_trader.data.open_interest import OI_INSTRUMENT_ID, OpenInterestPoint

METRICS_URL = "https://data.binance.vision/data/futures/um/daily/metrics/BTCUSDT"
_FIVE_MINUTE_S = 300
BINANCE_OI_SOURCE = "binance-vision-metrics (research proxy, not OKX)"


def parse_metrics_csv(csv_text: str) -> tuple[list[OpenInterestPoint], str]:
    """-> (5m points, detected interval). Non-5m days yield no points."""
    lines = [line for line in csv_text.strip().splitlines() if line.strip()]
    if len(lines) < 3:
        return [], "unknown"
    header = lines[0].split(",")
    ts_col = header.index("create_time")
    oi_col = header.index("sum_open_interest")
    rows = []
    for line in lines[1:]:
        cols = line.split(",")
        ts = datetime.strptime(cols[ts_col], "%Y-%m-%d %H:%M:%S")
        rows.append((int(ts.replace(tzinfo=timezone.utc).timestamp()), float(cols[oi_col])))
    diffs = sorted(b - a for (a, _), (b, _) in zip(rows, rows[1:]))
    median = diffs[len(diffs) // 2]
    interval = "5m" if median == _FIVE_MINUTE_S else f"{median // 60}m"
    if interval != "5m":
        return [], interval
    points = [
        OpenInterestPoint(
            ts_event=ts * 1_000_000_000,
            ts_init=ts * 1_000_000_000,
            instrument_id=InstrumentId.from_str(OI_INSTRUMENT_ID),
            open_interest=oi,
        )
        for ts, oi in rows
    ]
    return points, interval
