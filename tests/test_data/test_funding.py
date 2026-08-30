"""Unit tests for funding post-processing. No network."""

from sngw_trader.data.funding import daily_returns, funding_cost, summarize


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
