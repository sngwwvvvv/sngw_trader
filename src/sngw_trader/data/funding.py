"""Funding-cost post-processing and perf metrics (spec 7.3).

The backtest engine runs WITHOUT funding; this module applies it outside
the engine from the fills report and prints funding-excluded vs included
metrics. No orders, no nodes here.
"""

from __future__ import annotations

import json
import math
import urllib.request
from pathlib import Path

FUNDING_URL = "https://www.okx.com/api/v5/public/funding-rate-history"


def fetch_funding_rates(inst_id: str, start_ms: int, end_ms: int) -> dict[int, float]:
    """Public OKX funding-rate-history (100/page, newest first) -> {ts_ms: rate}."""
    rates: dict[int, float] = {}
    after = end_ms + 1
    while True:
        url = f"{FUNDING_URL}?instId={inst_id}&after={after}&limit=100"
        with urllib.request.urlopen(url, timeout=30) as resp:
            rows = json.loads(resp.read())["data"]
        if not rows:
            break
        for row in rows:
            ts = int(row["fundingTime"])
            if start_ms <= ts <= end_ms:
                rates[ts] = float(row["fundingRate"])
        oldest = min(int(row["fundingTime"]) for row in rows)
        if oldest <= start_ms or len(rows) < 100:
            break
        after = oldest
    return dict(sorted(rates.items()))


def funding_cost(fills: list[tuple[int, float, float]], rates: dict[int, float]) -> float:
    """fills: (ts_ns, signed_qty, fill_price) -> cumulative funding PnL.
    Long pays positive funding; short receives it.
    ponytail: notional approximated by the most recent fill price;
    switch to mark closes from the catalog if too coarse."""
    events = sorted(fills)
    total = 0.0
    qty, ref = 0.0, None
    idx = 0
    for ts_ms, rate in sorted(rates.items()):
        t_ns = ts_ms * 1_000_000
        while idx < len(events) and events[idx][0] <= t_ns:
            qty += events[idx][1]
            ref = events[idx][2]
            idx += 1
        if ref is not None and qty != 0:
            total += rate * abs(qty) * ref * (1.0 if qty > 0 else -1.0)
    return total


def daily_returns(pos: list[float], closes: list[float]) -> list[float]:
    """Day t return = position held during day t * close-to-close return."""
    rets = [0.0]
    for t in range(1, len(closes)):
        rets.append(pos[t] * (closes[t] / closes[t - 1] - 1))
    return rets if len(closes) > 1 else [0.0]


def summarize(rets: list[float]) -> dict[str, float]:
    mean = sum(rets) / len(rets) if rets else 0.0
    var = sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1)
    sharpe = mean / (var ** 0.5) * math.sqrt(365) if var > 0 else 0.0
    downside = [r for r in rets if r < 0]
    dvar = sum(r**2 for r in downside) / max(1, len(rets)) if downside else 0.0
    sortino = mean / (dvar ** 0.5) * math.sqrt(365) if downside and dvar > 0 else 0.0
    equity, peak, max_dd = 1.0, 1.0, 0.0
    for r in rets:
        equity *= 1 + r
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1)
    years = len(rets) / 365 if len(rets) else 0
    cagr = equity ** (1 / years) - 1 if years > 0 and equity > 0 else 0.0
    return {"total_return": equity - 1, "cagr": cagr, "sharpe": sharpe, "sortino": sortino, "max_dd": max_dd}


def _dt_to_ns(dt_str: str) -> int:
    from datetime import datetime

    return int(datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").timestamp()) * 1_000_000_000


def _ts_to_ns(ts) -> int:
    """ns epoch from int/float, digit str, or datetime-like (e.g. pd.Timestamp)."""
    if isinstance(ts, (int, float)):
        return int(ts)
    if isinstance(ts, str):
        return int(ts) if ts.isdigit() else _dt_to_ns(ts)
    return int(ts.timestamp()) * 1_000_000_000


def parse_fills(rows: list[dict]) -> list[tuple[int, float, float]]:
    """Map fills CSV rows to (ts_ns, signed_qty, px). Defensive on column names."""
    if not rows:
        return []
    cols = set(rows[0].keys())
    if "filled_ts" in cols:
        ts_col = "filled_ts"
    elif "ts_last" in cols:
        ts_col = "ts_last"
    else:
        ts_col = "ts_event" if "ts_event" in cols else None
    side_col = "order_side" if "order_side" in cols else ("side" if "side" in cols else None)
    qty_col = "last_qty" if "last_qty" in cols else ("filled_qty" if "filled_qty" in cols else None)
    px_col = "last_px" if "last_px" in cols else ("avg_px" if "avg_px" in cols else None)

    fills: list[tuple[int, float, float]] = []
    for r in rows:
        if ts_col is None or side_col is None or qty_col is None or px_col is None:
            continue
        ts = r[ts_col]
        if not ts:
            continue
        ts_ns = _ts_to_ns(ts)
        sign = -1.0 if r[side_col].strip().upper() == "SELL" else 1.0
        fills.append((ts_ns, sign * float(r[qty_col]), float(r[px_col])))
    return fills


# backward-compatible alias
_parse_fills = parse_fills


def apply_funding_to_marks(
    marks: list[tuple[int, float]],
    fills: list[tuple[int, float, float]],
    rates: dict[int, float],
) -> list[tuple[int, float]]:
    out: list[tuple[int, float]] = []
    for ts, eq in marks:
        cut = {t: r for t, r in rates.items() if t * 1_000_000 <= ts}
        out.append((ts, eq - funding_cost(fills, cut)))
    return out


def main() -> None:
    import csv

    fills_path = Path("logs/fills.csv")
    with fills_path.open() as f:
        rows = list(csv.DictReader(f))
    fills = parse_fills(rows)
    if not fills:
        print("no fills parsed")
        return
    start_ms = min(f[0] for f in fills) // 1_000_000
    end_ms = max(f[0] for f in fills) // 1_000_000
    rates = fetch_funding_rates("BTC-USDT-SWAP", start_ms, end_ms)
    cost = funding_cost(fills, rates)
    print(f"funding cost (signed PnL): {cost:.4f}")


if __name__ == "__main__":
    main()
