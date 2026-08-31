import pytest

from sngw_trader.research.metrics import compute_trade_metrics


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