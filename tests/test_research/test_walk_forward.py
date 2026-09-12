import json
from datetime import datetime, timedelta, timezone

import pytest

import sngw_trader.research.report as report
from sngw_trader.research.config import MCConfig, WalkForwardConfig
from sngw_trader.research.executor import RunResult
from sngw_trader.research.metrics import NS_PER_YEAR
from sngw_trader.research.select import sharpe_from_trades
from sngw_trader.research.walk_forward import (
    DAY_NS,
    bh_metrics,
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
    monkeypatch.delenv("WF_SIZING_MODE", raising=False)
    spec = load_grid()
    assert spec.strategy_path.endswith("ema_cross:EMACross")
    assert spec.grid == {}
    assert spec.fixed["trade_size"] == "0.10"
    assert spec.fixed["fast_ema_period"] == 20
    assert spec.fixed["slow_ema_period"] == 50
    assert spec.fixed["sizing_mode"] == "vol_target"


def test_load_grid_selects_sizing_mode(monkeypatch):
    monkeypatch.delenv("WF_GRID_PATH", raising=False)
    monkeypatch.setenv("WF_SIZING_MODE", "fixed")
    assert load_grid().fixed["sizing_mode"] == "fixed"


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
    assert wf.min_trades == 1
    assert wf.warmup_days == 61
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
                         "equity": {"sharpe": 0.4, "sortino": 0.2, "calmar": None, "mdd_ratio": 0.1}}},
        {"oos_is_sharpe_ratio": None,
         "oos_metrics": {"trade": None,
                         "equity": {"sharpe": None, "sortino": None, "calmar": 1.5, "mdd_ratio": 0.3}}},
        {"oos_is_sharpe_ratio": None, "oos_metrics": None},
    ]
    r = robustness_summary(windows)
    assert r["oos_is_sharpe_ratios"] == [1.0]
    assert r["n_excluded_ratio_windows"] == 2
    assert r["oos_sharpe"] == {"mean": pytest.approx(0.4), "n_excluded": 1}
    assert r["oos_sortino"] == {"mean": pytest.approx(0.2), "n_excluded": 1}
    assert r["oos_calmar"] == {"mean": pytest.approx(1.5), "n_excluded": 1}
    assert r["oos_mdd_ratio"] == {"mean": pytest.approx(0.2), "max": pytest.approx(0.3), "n_excluded": 0}


_FAKE_RESULT = RunResult(
    trade_pnls=[10.0, -4.0, 8.0],
    trade_returns=[0.01, -0.004, 0.008],
    n_trades=3,
    total_pnl=14.0,
    equity_marks=[
        (0, 10000.0),
        (NS_PER_YEAR // 4, 10100.0),
        (NS_PER_YEAR // 2, 9900.0),
    ],
)


def _wf_env(monkeypatch):
    monkeypatch.delenv("WF_GRID_PATH", raising=False)
    monkeypatch.setenv("WF_DATA_START", "2024-01-01T00:00:00+00:00")
    monkeypatch.setenv("WF_DATA_END", "2024-07-01T00:00:00+00:00")
    monkeypatch.setenv("WF_IS_MONTHS", "2")
    monkeypatch.setenv("WF_OOS_MONTHS", "1")
    monkeypatch.setenv("WF_HOLDOUT_MONTHS", "2")
    monkeypatch.setenv("WF_WARMUP_DAYS", "1")
    monkeypatch.setenv("WF_MIN_TRADES", "1")


def _run_main(monkeypatch, tmp_path):
    import sngw_trader.research.walk_forward as wf

    monkeypatch.setattr(wf, "run_window", lambda *a, **k: _FAKE_RESULT)
    monkeypatch.setattr(
        wf, "bootstrap_trades", lambda trades, mc_cfg: {"sims": mc_cfg.n_sims}
    )
    monkeypatch.setattr(report, "run_dir", lambda name: tmp_path)
    monkeypatch.setattr(report, "print_summary", lambda summary: None)
    wf.main()
    wf_report = json.loads((tmp_path / "wf_report.json").read_text(encoding="utf-8"))
    mc_report = json.loads((tmp_path / "mc_report.json").read_text(encoding="utf-8"))
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    return wf_report, mc_report, summary


def test_main_wires_metrics_into_reports(monkeypatch, tmp_path):
    _wf_env(monkeypatch)
    wf_report, mc_report, summary = _run_main(monkeypatch, tmp_path)

    assert wf_report["windows"], "expected at least one walk-forward window"
    for w in wf_report["windows"]:
        assert w["oos"]["start"] and w["oos"]["end"]
        assert w["oos"]["n_trades"] == 3
        assert w["oos"]["total_pnl"] == pytest.approx(14.0)
        assert set(w["is_metrics"]) == {"trade", "equity"}
        assert w["is_metrics"]["trade"]["win_rate"] == pytest.approx(2 / 3)
        assert w["is_metrics"]["equity"]["mdd_ratio"] == pytest.approx(1 - 9900 / 10100, rel=1e-6)
        assert w["oos_metrics"]["equity"]["mdd_ratio"] == pytest.approx(1 - 9900 / 10100, rel=1e-6)
        assert isinstance(w["oos_is_sharpe_ratio"], float) and w["oos_is_sharpe_ratio"] > 0

    rb = summary["robustness"]
    assert rb["oos_is_sharpe_ratios"] == [w["oos_is_sharpe_ratio"] for w in wf_report["windows"]]
    assert rb["n_excluded_ratio_windows"] == 0
    assert rb["oos_mdd_ratio"]["max"] is not None

    assert summary["holdout"] is not None
    assert summary["holdout"]["metrics"]["trade"]["win_rate"] is not None
    assert "oos_is_sharpe_ratio" not in summary["holdout"]["metrics"]

    assert mc_report == {"oos": {"sims": 1000}, "holdout": {"sims": 1000}}
    assert summary["mc_holdout"] == {"sims": 1000}


def test_bh_metrics_total_return_and_sharpe():
    days = [(i * DAY_NS, 100.0 + i) for i in range(11)]
    m = bh_metrics(days, datetime(1970, 1, 1, tzinfo=timezone.utc),
                   datetime(1970, 1, 11, tzinfo=timezone.utc))
    assert m["total_return"] == pytest.approx(0.09)  # [start, end): closes 100..109
    assert m["n_days"] == 9
    assert m["sharpe_ann"] > 0


def test_bh_metrics_zero_variance_sharpe_is_zero():
    days = [(i * DAY_NS, 100.0) for i in range(5)]
    m = bh_metrics(days, datetime(1970, 1, 1, tzinfo=timezone.utc),
                   datetime(1970, 1, 5, tzinfo=timezone.utc))
    assert m["total_return"] == pytest.approx(0.0)
    assert m["sharpe_ann"] == 0.0


def test_bh_metrics_insufficient_days_is_none():
    days = [(i * DAY_NS, 100.0) for i in range(2)]
    assert bh_metrics(days, datetime(1970, 1, 1, tzinfo=timezone.utc),
                      datetime(1970, 1, 2, tzinfo=timezone.utc)) is None


def test_main_includes_bh_benchmarks(monkeypatch, tmp_path):
    import sngw_trader.research.walk_forward as wf

    _wf_env(monkeypatch)
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    fake_days = [((start + timedelta(days=i)).timestamp() * 1e9, 100.0 + i)
                 for i in range(183)]
    monkeypatch.setattr(wf, "load_daily_closes", lambda: fake_days)
    wf_report, mc_report, summary = _run_main(monkeypatch, tmp_path)

    for w in wf_report["windows"]:
        assert w["oos_benchmark"] is not None
        assert w["oos_benchmark"]["n_days"] > 0
    assert summary["oos_benchmark"] is not None
    assert summary["holdout_benchmark"] is not None
    # +1/day over the stitched OOS span (data_start..holdout_start)
    assert summary["oos_benchmark"]["total_return"] > 0


def test_main_no_eligible_combo_yields_null_metrics(monkeypatch, tmp_path):
    _wf_env(monkeypatch)
    monkeypatch.setenv("WF_MIN_TRADES", "99")
    wf_report, mc_report, summary = _run_main(monkeypatch, tmp_path)

    assert wf_report["windows"]
    for w in wf_report["windows"]:
        assert w["oos"]["n_trades"] is None
        assert w["oos"]["total_pnl"] is None
        assert w["selected"] is None
        assert w["is_metrics"] is None
        assert w["oos_metrics"] is None
        assert w["oos_is_sharpe_ratio"] is None
    assert summary["holdout"] is None
    assert summary["robustness"]["oos_is_sharpe_ratios"] == []
    assert summary["robustness"]["n_excluded_ratio_windows"] == len(wf_report["windows"])
