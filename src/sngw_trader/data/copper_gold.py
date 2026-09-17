"""Copper/gold (HG=F/GC=F) same-date pairing and roll spike reporting.

Pure pandas. No fetching here; the writer calls yahoo_etf.fetch_daily_bars.
Report-only: no contract joining or spike corrections.
"""

from __future__ import annotations

from datetime import date

import pandas as pd


def paired_ratio(copper: pd.Series, gold: pd.Series) -> pd.Series:
    """Ratio of closes on dates present in both series (inner join, order preserved)."""
    joined = pd.concat([copper.rename("copper"), gold.rename("gold")], axis=1, join="inner")
    return (joined["copper"] / joined["gold"]).rename("ratio")


def spike_dates(series: pd.Series, abs_ret: float = 0.15) -> list[date]:
    """Dates whose |pct_change| exceeds abs_ret (first bar has no prior close)."""
    rets = series.pct_change()
    mask = rets.abs() > abs_ret
    return [ts.date() for ts in series.index[mask]]


def futures_quality_report(copper: pd.Series, gold: pd.Series, ratio: pd.Series) -> dict:
    return {
        "n": int(len(ratio)),
        "start": ratio.index[0].date().isoformat(),
        "end": ratio.index[-1].date().isoformat(),
        "copper_spikes": [d.isoformat() for d in spike_dates(copper)],
        "gold_spikes": [d.isoformat() for d in spike_dates(gold)],
        "ratio_spikes": [d.isoformat() for d in spike_dates(ratio)],
    }