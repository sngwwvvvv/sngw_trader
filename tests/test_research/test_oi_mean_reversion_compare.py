from datetime import datetime, timezone
from types import SimpleNamespace

from sngw_trader.research.config import GridSpec, MCConfig, WalkForwardConfig
from sngw_trader.research.executor import RunResult
from sngw_trader.research.oi_mean_reversion_compare import run_compare



def _spec(name: str, grid: dict) -> GridSpec:
    return GridSpec(
        strategy_path=f"pkg:{name}",
        config_path=f"pkg:{name}Config",
        fixed={},
        grid=grid,
    )


def test_run_compare_selects_best_and_reports(monkeypatch, tmp_path):
    import sngw_trader.research.oi_mean_reversion_compare as cmp

    monkeypatch.setattr(
        cmp, "detect_data_range",
        lambda path, bar_id: (
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 9, 1, tzinfo=timezone.utc),
        ),
    )

    def fake_window(settings, spec, params, start, end, warmup_days):
        # reentry_bars=3일 때만 2개의 좋은 트레이드, 아니면 min_trades 미달 1개
        if params.get("reentry_bars") == 3:
            return RunResult([1.0, 2.0], [0.01, 0.02], 2, 3.0)
        return RunResult([0.5], [0.005], 1, 0.5)

    specs = {"oi_a": _spec("oi_a", {"reentry_bars": [3, 5]})}
    settings = SimpleNamespace(catalog_path=tmp_path, instrument_id_str="BTC-USDT-SWAP.OKX")
    wf_cfg = WalkForwardConfig(is_months=1, oos_months=1, holdout_months=1, warmup_days=1, min_trades=2)
    mc_cfg = MCConfig(initial_capital=10_000.0)

    summary = run_compare(
        settings, wf_cfg, mc_cfg, out_dir=tmp_path,
        days=[(0, 100.0)], specs=specs, run_window=fake_window,
    )

    assert summary["proxy"] == cmp.PROXY_LABEL
    per_a = summary["strategies"]["oi_a"]
    assert per_a["windows"][0]["selected"] == {"reentry_bars": 3}
    assert per_a["windows"][0]["oos"]["n_trades"] == 2
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "wf_report.json").exists()


def test_run_compare_skips_window_without_eligible_params(monkeypatch, tmp_path):
    import sngw_trader.research.oi_mean_reversion_compare as cmp

    monkeypatch.setattr(
        cmp, "detect_data_range",
        lambda path, bar_id: (
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 9, 1, tzinfo=timezone.utc),
        ),
    )

    def fake_window(settings, spec, params, start, end, warmup_days):
        return RunResult([0.5], [0.005], 1, 0.5)  # 항상 min_trades 미달

    specs = {"oi_a": _spec("oi_a", {"reentry_bars": [3, 5]})}
    settings = SimpleNamespace(catalog_path=tmp_path, instrument_id_str="BTC-USDT-SWAP.OKX")
    wf_cfg = WalkForwardConfig(is_months=1, oos_months=1, holdout_months=1, warmup_days=1, min_trades=2)

    summary = run_compare(
        settings, wf_cfg, mc_cfg=MCConfig(initial_capital=10_000.0), out_dir=tmp_path,
        days=[(0, 100.0)], specs=specs, run_window=fake_window,
    )
    per_a = summary["strategies"]["oi_a"]
    assert per_a["windows"][0]["selected"] is None
    assert per_a["windows"][0]["oos"]["n_trades"] is None
