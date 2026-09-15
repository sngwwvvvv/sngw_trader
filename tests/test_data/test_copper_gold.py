from datetime import date

import pandas as pd

from sngw_trader.data.copper_gold import futures_quality_report, paired_ratio, spike_dates


def test_ratio_keeps_intersection_only() -> None:
    copper = pd.Series([3.0, 3.3, 3.6], index=pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06"]))
    gold = pd.Series([1500.0, 1600.0], index=pd.to_datetime(["2020-01-02", "2020-01-06"]))
    ratio = paired_ratio(copper, gold)
    assert list(ratio.index.date) == [date(2020, 1, 2), date(2020, 1, 6)]
    assert ratio.iloc[0] == 3.0 / 1500.0


def test_spike_dates_flag_large_returns() -> None:
    s = pd.Series([100.0, 101.0, 130.0], index=pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06"]))
    assert spike_dates(s, abs_ret=0.15) == [date(2020, 1, 6)]


def test_quality_report_lists_spikes() -> None:
    idx = pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06"])
    copper = pd.Series([3.0, 3.0, 4.0], index=idx)
    gold = pd.Series([1500.0, 1500.0, 1500.0], index=idx)
    ratio = paired_ratio(copper, gold)
    rep = futures_quality_report(copper, gold, ratio)
    assert date(2020, 1, 6).isoformat() in rep["copper_spikes"]
    assert rep["n"] == 3