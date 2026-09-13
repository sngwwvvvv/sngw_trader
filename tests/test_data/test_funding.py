"""Unit tests for funding post-processing. No network."""

import pytest
import pandas as pd

from sngw_trader.data.funding import apply_funding_to_marks, daily_returns, funding_cost, parse_fills, summarize


def test_funding_cost_long_pays_positive_rate():
    fills = [(0, 1.0, 100.0)]  # long 1 unit at 100 at t=0
    rates = {0: 0.0001, 8 * 3_600_000: 0.0001, 16 * 3_600_000: 0.0001}
    cost = funding_cost(fills, rates)
    assert abs(cost - 3 * 0.0001 * 100.0) < 1e-12


def test_funding_cost_short_receives():
    fills = [(0, -1.0, 100.0)]
    rates = {0: 0.0001}
    cost = funding_cost(fills, rates)
    assert abs(cost + 0.0001 * 100.0) < 1e-12  # short receives -> cost negative


def test_daily_returns_and_summary():
    closes = [100.0, 101.0, 99.0]
    pos = [0.0, 1.0, 1.0]  # position held during days 1, 2
    rets = daily_returns(pos, closes)
    assert rets[0] == 0.0
    assert abs(rets[1] - 0.01) < 1e-12
    assert abs(rets[2] - (99.0 / 101.0 - 1)) < 1e-12
    stats = summarize(rets)
    assert stats["sharpe"] < 0  # +1% then -2% day
    assert stats["max_dd"] <= 0.0


def test_apply_funding_to_marks_subtracts_cost_at_each_ts():
    fills = [(0, 1.0, 100.0)]
    rates = {0: 0.0001}
    marks = [(0, 10000.0), (8 * 3_600_000 * 1_000_000, 10000.0)]
    adjusted = apply_funding_to_marks(marks, fills, rates)
    assert adjusted[0][1] == pytest.approx(10000.0 - 0.01)
    # second mark still includes the t=0 payment
    assert adjusted[1][1] == pytest.approx(10000.0 - 0.01)


def test_parse_fills_accepts_report_dataframe_records():
    """generate_order_fills_report() records carry pd.Timestamp, not str/int."""
    rows = [
        {
            "ts_event": pd.Timestamp("2020-03-23 11:00:00+00:00"),
            "ts_last": pd.Timestamp("2020-03-23 11:01:00+00:00"),
            "order_side": "BUY",
            "last_qty": 0.5,
            "last_px": 100.0,
        },
        {
            "ts_event": pd.Timestamp("2020-03-23 11:02:00+00:00"),
            "ts_last": pd.Timestamp("2020-03-23 11:03:00+00:00"),
            "order_side": "SELL",
            "last_qty": 0.2,
            "last_px": 101.0,
        },
    ]
    fills = parse_fills(rows)
    assert fills[0] == (int(pd.Timestamp("2020-03-23 11:01:00+00:00").value), 0.5, 100.0)
    assert fills[1] == (int(pd.Timestamp("2020-03-23 11:03:00+00:00").value), -0.2, 101.0)
