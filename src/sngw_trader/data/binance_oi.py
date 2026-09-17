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


def fetch_metrics_day(day: date, timeout: int = 60) -> str | None:
    url = f"{METRICS_URL}/BTCUSDT-metrics-{day:%Y-%m-%d}.zip"
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".csv"))
        return zf.read(name).decode("utf-8")


def scan_binance_oi(
    start: date, end: date, fetch=fetch_metrics_day
) -> tuple[list[OpenInterestPoint], date | None]:
    """Days oldest->newest; non-5m (corrupt) days are skipped, 5m may resume later.

    -> (points, era_end) where era_end is the day after the last 5m day
    if any non-5m day was seen, else None.
    """
    points: dict[int, OpenInterestPoint] = {}
    last_5m_day: date | None = None
    saw_non_5m = False
    day = start
    while day <= end:
        text = fetch(day)
        if text is not None:
            try:
                day_points, interval = parse_metrics_csv(text)
            except Exception:
                # ponytail: malformed row/header kills multi-hour scans; skip day, continue
                day_points, interval = [], "corrupt"
            if interval != "5m":
                saw_non_5m = True
            else:
                for point in day_points:
                    points[point.ts_event] = point
                last_5m_day = day
        day += timedelta(days=1)
    era_end = last_5m_day + timedelta(days=1) if saw_non_5m and last_5m_day else None
    return [points[ts] for ts in sorted(points)], era_end


def write_binance_oi(settings, points: list[OpenInterestPoint]) -> None:
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    from sngw_trader.data.open_interest import register_open_interest, wrap_open_interest

    register_open_interest()
    catalog = ParquetDataCatalog(str(settings.catalog_path))
    catalog.write_data(
        [wrap_open_interest(point, source=BINANCE_OI_SOURCE) for point in points],
        data_cls=OpenInterestPoint,
    )


def main() -> None:
    import argparse

    from sngw_trader.config import load_settings

    parser = argparse.ArgumentParser(description="Download Binance OI proxy into the catalog")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD inclusive")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD inclusive")
    args = parser.parse_args()
    settings = load_settings()
    points, era_end = scan_binance_oi(date.fromisoformat(args.start), date.fromisoformat(args.end))
    if not points:
        raise SystemExit("No 5m Binance OI rows in the requested range")
    write_binance_oi(settings, points)
    print(f"binance oi points={len(points)} era_end={era_end}")


if __name__ == "__main__":
    main()
