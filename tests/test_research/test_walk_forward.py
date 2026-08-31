import json

import pytest

from sngw_trader.research.config import MCConfig, WalkForwardConfig
from sngw_trader.research.executor import RunResult
from sngw_trader.research.metrics import NS_PER_YEAR
from sngw_trader.research.select import sharpe_from_trades
from sngw_trader.research.walk_forward import (
    build_wf_configs,
    load_grid,
    oos_is_sharpe_ratio,
    robustness_summary,
    run_metrics,
    stitch_oos,
)

def test_stitch_concatenates_skipping_no_trade_windows():
    a = RunResult([1.0, 2.0], [0.01, 0.02], 2, 3.0)
    b = RunResult([-0.5], [-0.001], 1, -0.5)
    assert stitch_oos([a, None, b]) == [1.0, 2.0, -0.5]


def test_stitch_empty():
    assert stitch_oos([None, None]) == []


def test_load_grid_default(monkeypatch):
    monkeypatch.delenv("WF_GRID_PATH", raising=False)
    spec = load_grid()
    assert spec.strategy_path.endswith("ema_cross:EMACross")
    assert spec.grid == {"fast_ema_period": [10, 20, 30], "slow_ema_period": [20, 50, 100]}


def test_load_grid_from_json(tmp_path, monkeypatch):
    import json

    p = tmp_path / "grid.json"
    p.write_text(json.dumps({
        "strategy_path": "sngw_trader.strategies.example.ema_cross:EMACross",
        "config_path": "sngw_trader.strategies.example.ema_cross:EMACrossConfig",
        "fixed": {"trade_size": "0.02"},
        "grid": {"fast_ema_period": [5, 10]},
    }))
    monkeypatch.setenv("WF_GRID_PATH", str(p))
    spec = load_grid()
    assert spec.fixed == {"trade_size": "0.02"}
    assert spec.grid == {"fast_ema_period": [5, 10]}


def test_env_override_configs(monkeypatch):
    monkeypatch.delenv("WF_MIN_TRADES", raising=False)
    monkeypatch.delenv("MC_ITERS", raising=False)
    wf, mc = build_wf_configs()
    assert isinstance(wf, WalkForwardConfig)
    assert isinstance(mc, MCConfig)
    assert wf.min_trades == 30
    assert mc.n_sims == 1000

    monkeypatch.setenv("WF_MIN_TRADES", "99")
    monkeypatch.setenv("MC_ITERS", "777")
    monkeypatch.setenv("MC_SEED", "13")
    monkeypatch.setenv("MC_RUIN_THRESHOLD", "-0.2")
    monkeypatch.setenv("MC_INITIAL_CAPITAL", "12345.5")
    wf, mc = build_wf_configs()
    assert wf.min_trades == 99
    assert mc.n_sims == 777 and mc.seed == 13
    assert mc.ruin_threshold == -0.2 and mc.initial_capital == 12345.5


def test_oos_is_sharpe_ratio_nonpositive_is_sharpe_is_none():
    oos = RunResult([1.0, -1.0], [0.01, -0.01], 2, 0.0)
    assert oos_is_sharpe_ratio(0.0, oos) is None
    assert oos_is_sharpe_ratio(-1.0, oos) is None


def test_oos_is_sharpe_ratio_value():
    oos = RunResult([1.0, 1.0, -1.0], [0.01, 0.01, -0.01], 3, 1.0)
    r = oos_is_sharpe_ratio(1.0, oos)
    assert r == pytest.approx(sharpe_from_trades(oos.trade_returns))


def test_run_metrics_none_when_no_trades_and_marks():
    r = run_metrics(RunResult([], [], 0, 0.0), 10000.0)
    assert r == {"trade": None, "equity": None}


def test_run_metrics_populates_both():
    result = RunResult(
        [10.0, -5.0], [0.01, -0.005], 2, 5.0,
        equity_marks=[(0, 10000.0), (NS_PER_YEAR, 10010.0), (2 * NS_PER_YEAR, 10005.0)],
    )
    r = run_metrics(result, 10000.0)
    assert r["trade"]["win_rate"] == pytest.approx(0.5)
    assert r["equity"]["mdd_ratio"] == pytest.approx(0.0004995, rel=1e-2)


def test_robustness_summary_aggregates_and_excludes():
    windows = [
        {"oos_is_sharpe_ratio": 1.0,
         "oos_metrics": {"trade": None,
                         "equity": {"sortino": 0.2, "calmar": None, "mdd_ratio": 0.1}}},
        {"oos_is_sharpe_ratio": None,
         "oos_metrics": {"trade": None,
                         "equity": {"sortino": None, "calmar": 1.5, "mdd_ratio": 0.3}}},
        {"oos_is_sharpe_ratio": None, "oos_metrics": None},
    ]
    r = robustness_summary(windows)
    assert r["oos_is_sharpe_ratios"] == [1.0]
    assert r["n_excluded_ratio_windows"] == 2
    assert r["oos_sortino"] == {"mean": pytest.approx(0.2), "n_excluded": 1}
    assert r["oos_calmar"] == {"mean": pytest.approx(1.5), "n_excluded": 1}
    assert r["oos_mdd_ratio"] == {"mean": pytest.approx(0.2), "max": pytest.approx(0.3), "n_excluded": 0}
