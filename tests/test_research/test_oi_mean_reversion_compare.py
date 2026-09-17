from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from sngw_trader.research.config import GridSpec, MCConfig, WalkForwardConfig
from sngw_trader.research.executor import RunResult
from sngw_trader.research.oi_mean_reversion_compare import (
    funded_trade_metrics,
    run_compare,
)
from sngw_trader.research.oi_mean_reversion_compare import _trade_spans


E_NS = 1_700_000_000_000_000_000  # 트레이드 진입 기준 (ns)
RATES = {1_700_000_005_000: 0.0001}  # 1번 트레이드 (entry, exit] 구간 내 1회 펀딩


def _spec(name: str, grid: dict) -> GridSpec:
    return GridSpec(
        strategy_path=f"pkg:{name}",
        config_path=f"pkg:{name}Config",
        fixed={},
        grid=grid,
    )


def _good_result() -> RunResult:
    # 2트레이드: 1번 롱 (진입 100, 청산 101), 2번 롱 (진입 100, 청산 102)
    fills = [
        (E_NS, 0.01, 100.0),
        (E_NS + 10**13, -0.01, 101.0),
        (E_NS + 2 * 10**13, 0.01, 100.0),
        (E_NS + 3 * 10**13, -0.01, 102.0),
    ]
    return RunResult([1.0, 2.0], [0.01, 0.02], 2, 3.0, fills=fills)


def test_run_compare_selects_best_and_reports(monkeypatch, tmp_path):
    import sngw_trader.research.oi_mean_reversion_compare as cmp

    monkeypatch.setattr(
        cmp, "detect_data_range",
        lambda path, bar_id: (
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 9, 1, tzinfo=timezone.utc),
        ),
    )
    monkeypatch.setattr(cmp, "_funding_rates_cached", lambda inst, s, e: RATES)

    def fake_window(settings, spec, params, start, end, warmup_days):
        # reentry_bars=3일 때만 2개의 좋은 트레이드, 아니면 min_trades 미달 1개
        if params.get("reentry_bars") == 3:
            return _good_result()
        return RunResult([0.5], [0.005], 1, 0.5, fills=[(E_NS, 0.01, 100.0), (E_NS + 10**13, -0.01, 100.5)])

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
    assert per_a["grid"] == {"reentry_bars": [3, 5]}
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "wf_report.json").exists()

    # 펀딩 반영: 1번 트레이드가 0.0001 * 0.01 * 100 = 0.0001 비용 부담
    is_metrics = per_a["windows"][0]["is_metrics"]
    assert is_metrics["funding_exact"] is True
    assert is_metrics["trade"]["expectancy"] == pytest.approx((1.0 - 0.0001 + 2.0) / 2)
    # raw와 달라졌는지
    assert is_metrics["trade"]["expectancy"] != pytest.approx(1.5)

    # stitched funded: OOS마다 같은 결과 → raw 3.0, funded 3.0 - 0.0001
    assert per_a["stitched_oos_pnl"] == pytest.approx(6 * 3.0)
    assert per_a["stitched_oos_funded_pnl"] == pytest.approx(6 * (3.0 - 0.0001))
    assert per_a["stress_oos_funded_pnl"] == pytest.approx(6 * (3.0 - 0.0001))
    assert per_a["mc_oos"] is not None

    # holdout도 funded
    assert per_a["holdout"]["metrics"]["funding_exact"] is True
    assert per_a["holdout"]["metrics"]["trade"]["expectancy"] == pytest.approx((1.0 - 0.0001 + 2.0) / 2)


def test_run_compare_skips_window_without_eligible_params(monkeypatch, tmp_path):
    import sngw_trader.research.oi_mean_reversion_compare as cmp

    monkeypatch.setattr(
        cmp, "detect_data_range",
        lambda path, bar_id: (
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 9, 1, tzinfo=timezone.utc),
        ),
    )
    monkeypatch.setattr(cmp, "_funding_rates_cached", lambda inst, s, e: RATES)

    def fake_window(settings, spec, params, start, end, warmup_days):
        return RunResult([0.5], [0.005], 1, 0.5, fills=[(E_NS, 0.01, 100.0), (E_NS + 10**13, -0.01, 100.5)])

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


def test_trade_spans_partial_fills_and_multiple_trades():
    fills = [
        (1, 0.006, 100.0),   # 부분 진입
        (2, 0.004, 102.0),   # 부분 진입 (총 0.01)
        (3, -0.01, 103.0),   # 청산
        (4, 0.01, 100.0),    # 2번 트레이드 진입
        (5, -0.01, 101.0),   # 2번 트레이드 청산
    ]
    spans = _trade_spans(fills)
    assert len(spans) == 2
    entry_ts, exit_ts, qty, notional = spans[0]
    # entry_qty는 첫 부분체결 수치 (ref_px = notional/qty → rate*qty*ref_px = rate*notional 총액 기준)
    assert (entry_ts, exit_ts, qty) == (1, 3, 0.006)
    assert notional == pytest.approx(0.006 * 100.0 + 0.004 * 102.0)
    assert spans[1] == (4, 5, 0.01, 0.01 * 100.0)


def test_funded_trade_metrics_long_pays_short_receives():
    from sngw_trader.research.executor import RunResult as RR

    rate_ts_ms = 1_700_000_005_000
    rates = {rate_ts_ms: 0.0001}
    cost = 0.0001 * 0.01 * 100.0  # long, 양수 rate → 비용

    long_res = RR([1.0], [0.01], 1, 1.0, fills=[(E_NS, 0.01, 100.0), (E_NS + 10**13, -0.01, 100.5)])
    long_funded = funded_trade_metrics(long_res, rates)
    assert long_funded["exact"] is True
    assert long_funded["funded_pnls"][0] == pytest.approx(1.0 - cost)
    assert long_funded["funded_returns"][0] == pytest.approx(0.01 - cost / 1.0)

    short_res = RR([1.0], [0.01], 1, 1.0, fills=[(E_NS, -0.01, 100.0), (E_NS + 10**13, 0.01, 99.5)])
    short_funded = funded_trade_metrics(short_res, rates)
    assert short_funded["exact"] is True
    assert short_funded["funded_pnls"][0] == pytest.approx(1.0 + cost)

    # rate가 스팬 밖이면 비용 0
    outside = {rate_ts_ms + 10**9: 0.0001}
    assert funded_trade_metrics(long_res, outside)["funded_pnls"][0] == pytest.approx(1.0)


def test_funded_trade_metrics_mismatch_is_pro_rata():
    from sngw_trader.research.executor import RunResult as RR

    rates = {1_700_000_005_000: 0.0001}
    # n_trades=2지만 fill 스팬은 1개 → exact=False, pnl 비례 배분
    res = RR([1.0, 3.0], [0.01, 0.03], 2, 4.0, fills=[(E_NS, 0.01, 100.0), (E_NS + 10**13, -0.01, 100.5)])
    funded = funded_trade_metrics(res, rates)
    assert funded["exact"] is False
    total_cost = 0.0001 * 0.01 * 100.0
    assert funded["funded_pnls"][0] == pytest.approx(1.0 - total_cost * 0.25)
    assert funded["funded_pnls"][1] == pytest.approx(3.0 - total_cost * 0.75)
