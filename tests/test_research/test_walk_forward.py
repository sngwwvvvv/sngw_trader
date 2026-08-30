import json

from sngw_trader.research.config import MCConfig, WalkForwardConfig
from sngw_trader.research.executor import RunResult
from sngw_trader.research.walk_forward import build_wf_configs, load_grid, stitch_oos


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
