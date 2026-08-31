"""Walk-forward orchestration over BacktestNode. No alpha here."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from nautilus_trader.model.data import Bar
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

from sngw_trader.config import load_settings
from sngw_trader.research import report
from sngw_trader.research.config import GridSpec, MCConfig, WalkForwardConfig
from sngw_trader.research.executor import RunResult, run_window
from sngw_trader.research.metrics import compute_equity_metrics, compute_trade_metrics
from sngw_trader.research.monte_carlo import bootstrap_trades
from sngw_trader.research.select import param_keys, select_best, sharpe_from_trades
from sngw_trader.research.windows import compute_windows, holdout_start
from sngw_trader.runners.backtest_okx import default_bar_type

DEFAULT_GRID = GridSpec(
    strategy_path="sngw_trader.strategies.example.ema_cross:EMACross",
    config_path="sngw_trader.strategies.example.ema_cross:EMACrossConfig",
    fixed={"trade_size": "0.01"},
    grid={"fast_ema_period": [10, 20, 30], "slow_ema_period": [20, 50, 100]},
)


def load_grid() -> GridSpec:
    path = os.environ.get("WF_GRID_PATH")
    if not path:
        return DEFAULT_GRID
    data = json.loads(Path(path).read_text())
    return GridSpec(
        strategy_path=data["strategy_path"],
        config_path=data["config_path"],
        fixed=data.get("fixed", {}),
        grid=data["grid"],
    )


def _parse_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def detect_data_range(catalog_path: str, bar_type: str) -> tuple[datetime, datetime]:
    env_start = os.environ.get("WF_DATA_START")
    env_end = os.environ.get("WF_DATA_END")
    if (env_start is None) != (env_end is None):
        raise ValueError("set both WF_DATA_START and WF_DATA_END, or neither")
    if env_start and env_end:
        return _parse_utc(env_start), _parse_utc(env_end)
    catalog = ParquetDataCatalog(path=catalog_path)
    first = catalog.query_first_timestamp(Bar, identifier=bar_type)
    last = catalog.query_last_timestamp(Bar, identifier=bar_type)
    if first is None or last is None:
        raise ValueError(f"No bar data in catalog for {bar_type}; write data first or set WF_DATA_START/WF_DATA_END")
    return first.to_pydatetime(), last.to_pydatetime()


def stitch_oos(window_results: list[RunResult | None]) -> list[float]:
    out: list[float] = []
    for r in window_results:
        if r is not None:
            out.extend(r.trade_pnls)
    return out


def run_metrics(result: RunResult, initial_capital: float) -> dict:
    return {
        "trade": compute_trade_metrics(result.trade_pnls, result.trade_returns),
        "equity": compute_equity_metrics(result.equity_marks, initial_capital),
    }


def oos_is_sharpe_ratio(is_sharpe: float, oos_result: RunResult) -> float | None:
    """IS Sharpe <= 0이면 None 반환. '의미있는 전략 없음'으로 저장한다."""
    if is_sharpe <= 0:
        return None
    return sharpe_from_trades(oos_result.trade_returns) / is_sharpe


def robustness_summary(wf_windows: list[dict]) -> dict:
    ratios = [w["oos_is_sharpe_ratio"] for w in wf_windows
              if w.get("oos_is_sharpe_ratio") is not None]
    eqs = [w["oos_metrics"]["equity"] for w in wf_windows
           if w.get("oos_metrics") and w["oos_metrics"]["equity"]]

    def agg(key: str, with_max: bool) -> dict:
        vals = [m[key] for m in eqs if m.get(key) is not None]
        out: dict = {"mean": sum(vals) / len(vals) if vals else None,
                     "n_excluded": len(eqs) - len(vals)}
        if with_max:
            out["max"] = max(vals) if vals else None
        return out

    return {
        "oos_is_sharpe_ratios": ratios,
        "n_excluded_ratio_windows":
            sum(1 for w in wf_windows if w.get("oos_is_sharpe_ratio") is None),
        "oos_sortino": agg("sortino", with_max=False),
        "oos_calmar": agg("calmar", with_max=False),
        "oos_mdd_ratio": agg("mdd_ratio", with_max=True),
    }


def _params_for(spec: GridSpec, key: tuple) -> dict[str, object]:
    return dict(zip(sorted(spec.grid), key))


def _run_is_grid(settings, spec: GridSpec, window, wf_cfg: WalkForwardConfig) -> tuple[dict, dict]:
    catalog_path = str(settings.catalog_path)
    instrument_id = settings.instrument_id_str
    axes = sorted(spec.grid)
    results: dict[tuple, RunResult] = {}
    sharpes: dict[tuple, float] = {}
    for i, key in enumerate(param_keys(spec.grid), 1):
        res = run_window(
            catalog_path, instrument_id, settings=settings, spec=spec,
            params=dict(zip(axes, key)),
            start=window.is_start, end=window.is_end,
            warmup_days=wf_cfg.warmup_days,
        )
        results[key] = res
        sharpes[key] = sharpe_from_trades(res.trade_returns)
        print(f"[wf] window {window.index} IS {i}/{len(param_keys(spec.grid))} "
              f"{dict(zip(axes, key))} sharpe={sharpes[key]:.2f} trades={res.n_trades}")
    return results, sharpes


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw is not None else default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw is not None else default


def build_wf_configs() -> tuple[WalkForwardConfig, MCConfig]:
    wf_cfg = WalkForwardConfig(
        is_months=_env_int("WF_IS_MONTHS", 6),
        oos_months=_env_int("WF_OOS_MONTHS", 3),
        holdout_months=_env_int("WF_HOLDOUT_MONTHS", 6),
        warmup_days=_env_int("WF_WARMUP_DAYS", 1),
        min_trades=_env_int("WF_MIN_TRADES", 30),
    )
    mc_cfg = MCConfig(
        n_sims=_env_int("MC_ITERS", 1000),
        seed=_env_int("MC_SEED", 42),
        initial_capital=_env_float("MC_INITIAL_CAPITAL", 10000.0),
        ruin_threshold=_env_float("MC_RUIN_THRESHOLD", -0.5),
    )
    return wf_cfg, mc_cfg


def main() -> None:
    settings = load_settings()
    wf_cfg, mc_cfg = build_wf_configs()
    spec = load_grid()
    instrument_id = settings.instrument_id_str
    catalog_path = str(settings.catalog_path)
    bar_type = default_bar_type(instrument_id)

    data_start, data_end = detect_data_range(catalog_path, bar_type)
    windows = compute_windows(
        data_start, data_end, wf_cfg.is_months, wf_cfg.oos_months, wf_cfg.holdout_months,
    )
    h_start = holdout_start(data_end, wf_cfg.holdout_months)
    print(f"[wf] data {data_start:%Y-%m-%d}..{data_end:%Y-%m-%d} "
          f"holdout from {h_start:%Y-%m-%d}, {len(windows)} windows")

    strategy_name = spec.strategy_path.rsplit(":", 1)[-1]
    out_dir = report.run_dir(strategy_name)

    wf_windows: list[dict] = []
    oos_results: list[RunResult | None] = []
    last_params: tuple | None = None

    for window in windows:
        grid_results, sharpes = _run_is_grid(settings, spec, window, wf_cfg)
        best = select_best(
            spec.grid, sharpes,
            {k: r.n_trades for k, r in grid_results.items()},
            wf_cfg.min_trades,
        )
        oos_result: RunResult | None = None
        if best is not None:
            last_params = best
            oos_result = run_window(
                catalog_path, instrument_id, settings=settings, spec=spec,
                params=_params_for(spec, best),
                start=window.oos_start, end=window.oos_end,
                warmup_days=wf_cfg.warmup_days,
            )
        oos_results.append(oos_result)
        oos_metrics = run_metrics(oos_result, mc_cfg.initial_capital) if oos_result else None
        is_metrics = run_metrics(grid_results[best], mc_cfg.initial_capital) if best is not None else None
        ratio = (oos_is_sharpe_ratio(sharpes[best], oos_result)
                 if best is not None and oos_result is not None else None)
        wf_windows.append({
            "index": window.index,
            "is": {"start": window.is_start.isoformat(), "end": window.is_end.isoformat()},
            "oos": {
                "start": window.oos_start.isoformat(),
                "end": window.oos_end.isoformat(),
                "n_trades": oos_result.n_trades if oos_result else None,
                "total_pnl": oos_result.total_pnl if oos_result else None,
            },
            "selected": _params_for(spec, best) if best is not None else None,
            "grid": [
                {"params": _params_for(spec, k), "sharpe": sharpes[k], "n_trades": grid_results[k].n_trades}
                for k in grid_results
            ],
            "is_metrics": is_metrics,
            "oos_metrics": oos_metrics,
            "oos_is_sharpe_ratio": ratio,
        })
        if oos_result is not None:
            print(f"[wf] window {window.index} OOS pnl={oos_result.total_pnl:.2f} trades={oos_result.n_trades}")

    stitched = stitch_oos(oos_results)
    mc_oos = bootstrap_trades(stitched, mc_cfg) if len(stitched) >= 2 else None

    holdout: RunResult | None = None
    mc_holdout = None
    if last_params is not None:
        holdout = run_window(
            catalog_path, instrument_id, settings=settings, spec=spec,
            params=_params_for(spec, last_params),
            start=h_start, end=data_end,
            warmup_days=wf_cfg.warmup_days,
        )
        if holdout.n_trades >= 2:
            mc_holdout = bootstrap_trades(holdout.trade_pnls, mc_cfg)
    holdout_metrics = run_metrics(holdout, mc_cfg.initial_capital) if holdout else None

    wf_report = {
        "instrument_id": instrument_id,
        "data_start": data_start.isoformat(),
        "data_end": data_end.isoformat(),
        "holdout_start": h_start.isoformat(),
        "windows": wf_windows,
    }
    mc_report = {"oos": mc_oos, "holdout": mc_holdout}
    summary = {
        "instrument_id": instrument_id,
        "strategy": strategy_name,
        "n_windows": len(windows),
        "stitched_oos_trades": len(stitched),
        "stitched_oos_pnl": sum(stitched),
        "mc_oos": mc_oos,
        "holdout": None if holdout is None else {"n_trades": holdout.n_trades, "total_pnl": holdout.total_pnl, "metrics": holdout_metrics},
        "mc_holdout": mc_holdout,
        "robustness": robustness_summary(wf_windows),
    }

    report.write_reports(out_dir, wf_report, mc_report, summary)
    report.print_summary(summary)


if __name__ == "__main__":
    main()
