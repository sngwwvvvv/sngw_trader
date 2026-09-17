"""OI A/B IS/WF compare on the Binance OI proxy. BacktestNode is the runner.

OI proxy: Binance BTCUSDT metrics (NOT OKX). Every report labels the proxy.
Grids live in SPECS below; adjust after the oneshot sanity run (Task 6).
"""

from __future__ import annotations

import os
from dataclasses import is_dataclass, replace
from datetime import datetime, timezone

from nautilus_trader.core.datetime import dt_to_unix_nanos

from sngw_trader.config import load_settings
from sngw_trader.data.funding import (
    apply_funding_to_marks,
    fetch_funding_rates,
    funding_cost,
)
from sngw_trader.data.open_interest import OI_INSTRUMENT_ID
from sngw_trader.research import report
from sngw_trader.research.config import GridSpec
from sngw_trader.research.executor import RunResult, run_oi_window
from sngw_trader.research.metrics import compute_equity_metrics, compute_trade_metrics
from sngw_trader.research.monte_carlo import bootstrap_trades
from sngw_trader.research.select import param_keys, select_best, sharpe_from_trades
from sngw_trader.research.walk_forward import (
    bh_metrics,
    build_wf_configs,
    detect_data_range,
    load_daily_closes,
    robustness_summary,
    stitch_oos,
)
from sngw_trader.research.windows import compute_windows, holdout_start

PROXY_LABEL = "Binance OI proxy (BTCUSDT metrics) — NOT OKX data"

ASSUMPTIONS = [
    "OI는 Binance BTCUSDT metrics 합계(sum_open_interest)이며 OKX OI가 아니다. venue 불일치를 전제로 한 proxy 결과다",
    "Binance sum_open_interest는 BTC 계약 수, OKX는 컨트랙트 밸류 단위다. 전략은 상대 변화율만 쓰므로 단위 차이는 신호에 영향 없다",
    "Binance 5m era 종료일 이전 구간만 사용한다 (25m era 제외)",
    "tp_mode=atr, hold_across_sessions=true 재설계 구조다. TP=진입가±tp_atr_mult×ATR, SL=진입가∓atr_mult×ATR, 세션 종료 청산 없음(주말 보유 가능)",
    "주말/격일 보유의 갭 슬리피지는 백테스트 체결 모델이 과소평가할 수 있다",
    "펀딩비는 OKX 실측 funding-rate-history를 트레이드별 (진입, 청산] 구간에 귀속해 반영한다",
    "이 결과만으로 라이브 투입 근거로 삼지 않는다. 실측 OKX OI 축적 후 재검증한다",
]

SPECS: dict[str, GridSpec] = {
    "oi_a": GridSpec(
        strategy_path="sngw_trader.strategies.oi_crowding_mean_reversion:OiCrowdingMeanReversion",
        config_path="sngw_trader.strategies.oi_crowding_mean_reversion:OiCrowdingMeanReversionConfig",
        fixed={
            "trade_size": "0.01",
            "oi_client_id": "BACKTEST",
            "tp_mode": "atr",
            "hold_across_sessions": True,
        },
        grid={"atr_mult": [2.0, 3.0, 4.0], "tp_atr_mult": [1.5, 2.5, 4.0]},
    ),
    "oi_b": GridSpec(
        strategy_path="sngw_trader.strategies.oi_liquidation_mean_reversion:OiLiquidationMeanReversion",
        config_path="sngw_trader.strategies.oi_liquidation_mean_reversion:OiLiquidationMeanReversionConfig",
        fixed={
            "trade_size": "0.01",
            "oi_client_id": "BACKTEST",
            "tp_mode": "atr",
            "hold_across_sessions": True,
        },
        grid={"atr_mult": [2.0, 3.0, 4.0], "tp_atr_mult": [1.5, 2.5, 4.0]},
    ),
}

_RATES_CACHE: dict[tuple[str, int, int], dict[int, float]] = {}


def _funding_rates_cached(inst_id: str, start_ns: int, end_ns: int) -> dict[int, float]:
    key = (inst_id, start_ns, end_ns)
    if key not in _RATES_CACHE:
        _RATES_CACHE[key] = fetch_funding_rates(inst_id, start_ns // 1_000_000, end_ns // 1_000_000)
    return _RATES_CACHE[key]


def _trade_spans(fills: list[tuple[int, float, float]]) -> list[tuple[int, int, float, float]]:
    """fills (ts_ns, signed_qty, px) -> [(entry_ts, exit_ts, signed_qty, entry_notional)]
    per closed trade. Strategy holds at most one net position, so trades are
    flat->nonzero->flat fill spans (partial fills included)."""
    spans: list[tuple[int, int, float, float]] = []
    cum = 0.0
    entry_ts = 0
    entry_qty = 0.0
    entry_notional = 0.0
    for ts, signed_qty, px in sorted(fills):
        was_flat = cum == 0.0
        cum += signed_qty
        if was_flat:
            entry_ts = ts
            entry_qty = cum
            entry_notional = abs(signed_qty) * px
        elif cum != 0.0:
            entry_notional += abs(signed_qty) * px
        else:
            spans.append((entry_ts, ts, entry_qty, entry_notional))
    return spans


def funded_trade_metrics(result: RunResult, rates: dict[int, float]) -> dict:
    """Per-trade funding allocation: each trade pays the funding accrued
    between its entry and exit (OKX 실측 rate, long pays positive)."""
    spans = _trade_spans(result.fills)
    exact = len(spans) == result.n_trades and result.n_trades == len(result.trade_pnls)
    if exact:
        funded_pnls: list[float] = []
        funded_rets: list[float] = []
        for (entry_ts, exit_ts, signed_qty, notional), pnl, ret in zip(
            spans, result.trade_pnls, result.trade_returns
        ):
            window = {
                t: r for t, r in rates.items() if entry_ts < t * 1_000_000 <= exit_ts
            }
            ref_px = notional / abs(signed_qty) if signed_qty else 0.0
            cost = funding_cost([(entry_ts, signed_qty, ref_px)], window)
            funded_pnls.append(pnl - cost)
            funded_rets.append(ret - cost / notional if notional else ret)
    else:
        # fill/trade 개수 불일치(부분체결 엣지): 집계 펀딩을 pnl 비례 배분
        total_cost = funding_cost(result.fills, rates)
        total = sum(result.trade_pnls)
        shares = [
            p / total if total else 1.0 / max(1, result.n_trades)
            for p in result.trade_pnls
        ]
        funded_pnls = [p - total_cost * s for p, s in zip(result.trade_pnls, shares)]
        funded_rets = list(result.trade_returns)
    return {"funded_pnls": funded_pnls, "funded_returns": funded_rets, "exact": exact}


def _funded_run_metrics(result: RunResult, rates: dict[int, float], initial_capital: float) -> dict:
    funded = funded_trade_metrics(result, rates)
    funded_marks = (
        apply_funding_to_marks(result.equity_marks, result.fills, rates)
        if result.equity_marks and result.fills
        else result.equity_marks
    )
    return {
        "trade": compute_trade_metrics(funded["funded_pnls"], funded["funded_returns"]),
        "equity": compute_equity_metrics(funded_marks, initial_capital),
        "funding_exact": funded["exact"],
    }


def stress_settings(settings):
    """2x taker fee + 5x slippage stress variant (kd_macd_compare 선례)."""
    if not is_dataclass(settings):
        return settings
    return replace(
        settings,
        bt_taker_fee=settings.bt_taker_fee * 2,
        bt_prob_slippage=min(0.99, settings.bt_prob_slippage * 5),
    )


def _params_for(spec: GridSpec, key: tuple) -> dict[str, object]:
    return dict(zip(sorted(spec.grid), key))


def _run_window(settings, spec, params, start, end, warmup_days):
    return run_oi_window(
        str(settings.catalog_path), settings.instrument_id_str, settings=settings,
        spec=spec, params=params, start=start, end=end, warmup_days=warmup_days,
    )


def _data_range(settings):
    start, end = detect_data_range(
        str(settings.catalog_path), f"{settings.instrument_id_str}-1-MINUTE-LAST-EXTERNAL"
    )
    era_end = os.environ.get("OI_ERA_END")
    if era_end:
        end = min(end, datetime.fromisoformat(era_end).replace(tzinfo=timezone.utc))
    return start, end


def _wf_windows(settings, spec, windows, wf_cfg, mc_cfg, run_window):
    out = []
    oos_results: list[RunResult | None] = []
    last_params: tuple | None = None
    n_cells = len(param_keys(spec.grid))
    inst = settings.instrument_id_str.split(".", 1)[0]
    for window in windows:
        is_rates = _funding_rates_cached(
            inst, dt_to_unix_nanos(window.is_start), dt_to_unix_nanos(window.is_end)
        )
        oos_rates = _funding_rates_cached(
            inst, dt_to_unix_nanos(window.oos_start), dt_to_unix_nanos(window.oos_end)
        )
        grid_results = {}
        for i, key in enumerate(param_keys(spec.grid), 1):
            params = _params_for(spec, key)
            grid_results[key] = run_window(
                settings, spec, params, window.is_start, window.is_end, wf_cfg.warmup_days
            )
            print(f"[oi-compare] {spec.strategy_path.rsplit(':', 1)[-1]} "
                  f"w{window.index} IS {i}/{n_cells} {params} trades={grid_results[key].n_trades}")
        # IS 셀 선택 점수도 funded 수치로 (gates는 n_trades 그대로)
        sharpes = {
            k: sharpe_from_trades(funded_trade_metrics(r, is_rates)["funded_returns"])
            for k, r in grid_results.items()
        }
        gates = {k: r.n_trades for k, r in grid_results.items()}
        best = select_best(spec.grid, sharpes, gates, wf_cfg.min_trades)
        oos_result = None
        if best is not None:
            last_params = best
            oos_result = run_window(
                settings, spec, _params_for(spec, best),
                window.oos_start, window.oos_end, wf_cfg.warmup_days,
            )
        oos_results.append(oos_result)
        is_metrics = (
            _funded_run_metrics(grid_results[best], is_rates, mc_cfg.initial_capital)
            if best is not None else None
        )
        oos_metrics = (
            _funded_run_metrics(oos_result, oos_rates, mc_cfg.initial_capital)
            if oos_result else None
        )
        # compute_trade_metrics에 sharpe 키가 없으므로 funded_returns로 직접 계산
        is_funded_sharpe = (
            sharpe_from_trades(funded_trade_metrics(grid_results[best], is_rates)["funded_returns"])
            if best is not None else None
        )
        oos_funded_sharpe = (
            sharpe_from_trades(funded_trade_metrics(oos_result, oos_rates)["funded_returns"])
            if oos_result is not None else None
        )
        ratio = (
            None if oos_funded_sharpe is None or not is_funded_sharpe > 0
            else oos_funded_sharpe / is_funded_sharpe
        )
        out.append({
            "index": window.index,
            "is": {"start": window.is_start.isoformat(), "end": window.is_end.isoformat()},
            "oos": {
                "start": window.oos_start.isoformat(),
                "end": window.oos_end.isoformat(),
                "n_trades": oos_result.n_trades if oos_result else None,
                "total_pnl": oos_result.total_pnl if oos_result else None,
            },
            "selected": _params_for(spec, best) if best is not None else None,
            "is_metrics": is_metrics,
            "oos_metrics": oos_metrics,
            "oos_is_sharpe_ratio": ratio,
        })
    return out, oos_results, last_params


def run_compare(settings, wf_cfg, mc_cfg, out_dir, days, specs=SPECS, run_window=_run_window):
    data_start, data_end = _data_range(settings)
    windows = compute_windows(
        data_start, data_end, wf_cfg.is_months, wf_cfg.oos_months, wf_cfg.holdout_months,
    )
    h_start = holdout_start(data_end, wf_cfg.holdout_months)
    print(f"[oi-compare] data {data_start:%Y-%m-%d}..{data_end:%Y-%m-%d} "
          f"holdout from {h_start:%Y-%m-%d}, {len(windows)} windows")

    strategies: dict[str, dict] = {}
    inst = settings.instrument_id_str.split(".", 1)[0]
    for name, spec in specs.items():
        wf_windows, oos_results, last_params = _wf_windows(
            settings, spec, windows, wf_cfg, mc_cfg, run_window,
        )
        stitched = stitch_oos(oos_results)
        # stress: 각 OOS window를 선택된 파라미터로 스트레스 설정 재실행
        stress_runs: list[tuple[RunResult, object]] = []
        for entry, window in zip(wf_windows, windows):
            if entry["selected"] is None:
                continue
            stress_runs.append((
                run_window(
                    stress_settings(settings), spec, entry["selected"],
                    window.oos_start, window.oos_end, wf_cfg.warmup_days,
                ),
                window,
            ))
        stress_oos_funded_pnl = sum(
            sum(funded_trade_metrics(
                r,
                _funding_rates_cached(
                    inst, dt_to_unix_nanos(w.oos_start), dt_to_unix_nanos(w.oos_end)
                ),
            )["funded_pnls"])
            for r, w in stress_runs
        )
        # stitched는 raw pnls 유지 + funded pnls 이어붙이기
        stitched_funded: list[float] = []
        for r, window in zip(oos_results, windows):
            if r is None:
                continue
            rates = _funding_rates_cached(
                inst, dt_to_unix_nanos(window.oos_start), dt_to_unix_nanos(window.oos_end)
            )
            stitched_funded.extend(funded_trade_metrics(r, rates)["funded_pnls"])
        holdout = None
        if last_params is not None:
            holdout = run_window(
                settings, spec, _params_for(spec, last_params),
                h_start, data_end, wf_cfg.warmup_days,
            )
        holdout_rates = _funding_rates_cached(
            inst, dt_to_unix_nanos(h_start), dt_to_unix_nanos(data_end)
        )
        strategies[name] = {
            "windows": wf_windows,
            "grid": spec.grid,
            "stitched_oos_trades": len(stitched),
            "stitched_oos_pnl": sum(stitched),
            "stitched_oos_funded_pnl": sum(stitched_funded),
            "stress_oos_pnl": sum(r.total_pnl for r, _ in stress_runs),
            "stress_oos_funded_pnl": stress_oos_funded_pnl,
            "mc_oos": bootstrap_trades(stitched_funded, mc_cfg) if len(stitched_funded) >= 2 else None,
            "robustness": robustness_summary(wf_windows),
            "holdout": None if holdout is None else {
                "n_trades": holdout.n_trades,
                "total_pnl": holdout.total_pnl,
                "metrics": _funded_run_metrics(holdout, holdout_rates, mc_cfg.initial_capital),
            },
        }
        print(f"[oi-compare] {name}: oos_pnl={sum(stitched):.2f} "
              f"stress={sum(r.total_pnl for r, _ in stress_runs):.2f} "
              f"holdout={holdout.total_pnl if holdout else None}")

    summary = {
        "label": "IS/WF compare",
        "proxy": PROXY_LABEL,
        "instrument_id": settings.instrument_id_str,
        "data_start": data_start.isoformat(),
        "data_end": data_end.isoformat(),
        "holdout_start": h_start.isoformat(),
        "strategies": strategies,
        "oos_benchmark": bh_metrics(days, data_start, h_start),
        "assumptions": ASSUMPTIONS,
    }
    report.write_reports(
        out_dir,
        {"proxy": PROXY_LABEL, "data_start": data_start.isoformat(),
         "data_end": data_end.isoformat(), "strategies": strategies},
        {"oos": {n: s["mc_oos"] for n, s in strategies.items()}},
        summary,
    )
    return summary


def run_oneshot(settings, wf_cfg, mc_cfg, out_dir, days, specs=SPECS, run_window=_run_window):
    """Single full-range sanity run with default params. Grid 결정 전 트레이드 수 확인용."""
    data_start, data_end = _data_range(settings)
    rates = _funding_rates_cached(
        settings.instrument_id_str.split(".", 1)[0],
        dt_to_unix_nanos(data_start), dt_to_unix_nanos(data_end),
    )
    cells = {}
    for name, spec in specs.items():
        res = run_window(settings, spec, {}, data_start, data_end, wf_cfg.warmup_days)
        cells[name] = {
            "n_trades": res.n_trades,
            "total_pnl": res.total_pnl,
            "metrics": _funded_run_metrics(res, rates, mc_cfg.initial_capital),
            "benchmark": bh_metrics(days, data_start, data_end),
        }
        print(f"[oneshot] {name} trades={res.n_trades} pnl={res.total_pnl:.2f}")
    summary = {
        "label": "oneshot",
        "proxy": PROXY_LABEL,
        "instrument_id": settings.instrument_id_str,
        "data_start": data_start.isoformat(),
        "data_end": data_end.isoformat(),
        "cells": cells,
        "assumptions": ASSUMPTIONS,
    }
    report.write_reports(out_dir, {"label": "oneshot", "proxy": PROXY_LABEL}, {}, summary)
    return summary


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def main() -> None:
    settings = load_settings()
    if settings.instrument_id_str != OI_INSTRUMENT_ID:
        raise SystemExit(f"OI compare supports only {OI_INSTRUMENT_ID}, got {settings.instrument_id_str}")
    wf_cfg, mc_cfg = build_wf_configs()
    out_dir = report.run_dir("oi_mean_reversion_compare")
    days = load_daily_closes()
    if _env_flag("OI_ONESHOT"):
        run_oneshot(settings, wf_cfg, mc_cfg, out_dir, days)
    else:
        run_compare(settings, wf_cfg, mc_cfg, out_dir, days)
    report.print_summary({"proxy": PROXY_LABEL, "out_dir": str(out_dir)})


if __name__ == "__main__":
    main()
