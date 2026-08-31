import math

from sngw_trader.research.param_scan import compute_metrics


def test_compute_metrics_empty():
    m = compute_metrics([], [], [])
    assert m["n_trades"] == 0
    assert m["total"] == 0.0
    assert m["sharpe"] == 0.0
    assert m["max_dd"] == 0.0
    assert m["avg_hold_h"] == 0.0


def test_compute_metrics_basic():
    pnls = [100.0, -50.0, 30.0]
    rets = [0.10, -0.05, 0.03]
    holds = [10.0, 20.0, 30.0]
    m = compute_metrics(pnls, rets, holds)
    assert m["n_trades"] == 3
    assert math.isclose(m["total"], 80.0)
    assert math.isclose(m["max_dd"], 50.0)  # peak 100 -> trough 50
    assert math.isclose(m["avg_hold_h"], 20.0)
    mean = sum(rets) / 3
    var = sum((r - mean) ** 2 for r in rets) / 2
    assert math.isclose(m["sharpe"], mean / math.sqrt(var))


def test_compute_metrics_zero_std_sharpe():
    m = compute_metrics([10.0, 10.0], [0.01, 0.01], [1.0, 1.0])
    assert m["sharpe"] == 0.0