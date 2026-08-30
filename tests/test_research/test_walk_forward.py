import json

from sngw_trader.research.executor import RunResult
from sngw_trader.research.walk_forward import load_grid, stitch_oos


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