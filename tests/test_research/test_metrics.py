import pytest

from sngw_trader.research.metrics import compute_trade_metrics, compute_equity_metrics


def test_trade_metrics_values():
    m = compute_trade_metrics(
        [10.0, -5.0, 20.0, -5.0, -5.0], [0.01, -0.005, 0.02, -0.005, -0.005]
    )
    assert m["win_rate"] == pytest.approx(2 / 5)
    assert m["profit_factor"] == pytest.approx(30 / 15)
    assert m["expectancy"] == pytest.approx(3.0)
    assert m["payoff_ratio"] == pytest.approx(15 / 5)
    assert m["max_consecutive_losses"] == 2


def test_trade_metrics_empty_returns_none():
    assert compute_trade_metrics([], []) is None


def test_trade_metrics_no_losses():
    m = compute_trade_metrics([1.0, 2.0], [0.01, 0.02])
    assert m["profit_factor"] is None
    assert m["payoff_ratio"] is None
    assert m["win_rate"] == 1.0


def test_trade_metrics_no_wins():
    m = compute_trade_metrics([-1.0, -2.0], [-0.01, -0.02])
    assert m["payoff_ratio"] is None
    assert m["profit_factor"] == pytest.approx(0.0)
    assert m["max_consecutive_losses"] == 2


NS = 1_000_000_000


def test_equity_metrics_values():
    marks = [
        (0 * NS, 100.0),
        (1 * NS, 120.0),
        (2 * NS, 90.0),
        (3 * NS, 100.0),
        (4 * NS, 130.0),
    ]
    m = compute_equity_metrics(marks, 100.0)
    assert m["mdd_ratio"] == pytest.approx(0.25)
    assert m["mdd_duration"] == pytest.approx(3.0)
    assert m["recovery_duration"] == pytest.approx(2.0)
    assert m["underwater_mean"] == pytest.approx((0.0 + 0.0 - 0.25 - 1 / 6 + 0.0) / 5)
    assert m["sortino"] == pytest.approx(0.0902777 / 0.125, rel=1e-3)
    assert m["tail_ratio"] > 1.0
    assert m["annualized_return"] == pytest.approx((1.3) ** 0.25 - 1, rel=1e-6)
    assert m["calmar"] == pytest.approx(m["annualized_return"] / 0.25, rel=1e-6)


def test_equity_metrics_no_drawdown():
    m = compute_equity_metrics([(0, 100.0), (NS, 101.0), (2 * NS, 102.0)], 100.0)
    assert m["mdd_ratio"] == 0.0
    assert m["mdd_duration"] == 0.0
    assert m["recovery_duration"] is None
    assert m["calmar"] is None
    assert m["sortino"] is None


def test_equity_metrics_negative_annual_return():
    m = compute_equity_metrics([(0, 100.0), (NS, 50.0)], 100.0)
    assert m["annualized_return"] < 0
    assert m["calmar"] is None


def test_equity_metrics_none_cases():
    assert compute_equity_metrics([(0, 100.0)], 100.0) is None
    assert compute_equity_metrics([(0, 100.0), (NS, 0.0)], 100.0) is None
    m = compute_equity_metrics([(0, 100.0), (NS, 100.0), (2 * NS, 100.0)], 100.0)
    assert m["tail_ratio"] is None