"""Grid scan for ErrMomentumRegime over a fixed period. No alpha here.

Spec gates (2026-08-31-4h-ermom-trailing-design.md):
  1st gate: --cost-mult 2 / 3 must not collapse net results.
  2nd gate: ablation vs fixed stop vs daily spec A.
"""

from __future__ import annotations

import argparse
import itertools
import math
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.model.identifiers import InstrumentId

from sngw_trader.config import load_settings
from sngw_trader.config.settings import Settings
from sngw_trader.research.config import GridSpec
from sngw_trader.research.executor import build_strategy
from sngw_trader.runners.backtest_okx import build_run_config

START = datetime(2023, 1, 1, tzinfo=timezone.utc)
END = datetime(2025, 12, 31, tzinfo=timezone.utc)
WARMUP_DAYS = 30  # 4h warm-up is ~10 days; pad for data edges (was 330 for daily)

SPEC = GridSpec(
    strategy_path="sngw_trader.strategies.err_momentum_regime:ErrMomentumRegime",
    config_path="sngw_trader.strategies.err_momentum_regime:ErrMomentumRegimeConfig",
    fixed={"trade_size": "0.01"},
    grid={},
)

GRID: dict[str, list] = {
    "momentum_window": [24, 48, 60],
    "atr_mult": [2.5, 3.0, 4.0],
    "theta": [0.0, 0.1, 0.2],
    "reentry_cooldown_bars": [1, 2],
}


def cost_settings(settings: Settings, mult: float) -> Settings:
    """Fee-rate multiplier = cost stress proxy (bps incl. slippage upper bound)."""
    return replace(
        settings,
        bt_maker_fee=settings.bt_maker_fee * mult,
        bt_taker_fee=settings.bt_taker_fee * mult,
    )


def compute_metrics(
    trade_pnls: list[float], trade_returns: list[float], hold_hours: list[float]
) -> dict:
    n = len(trade_pnls)
    if n == 0:
        return {"n_trades": 0, "total": 0.0, "sharpe": 0.0, "max_dd": 0.0, "avg_hold_h": 0.0}
    peak = 0.0
    max_dd = 0.0
    equity = 0.0
    for pnl in trade_pnls:
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    mean = sum(trade_returns) / n
    var = sum((r - mean) ** 2 for r in trade_returns) / max(n - 1, 1)
    std = math.sqrt(var)
    return {
        "n_trades": n,
        "total": sum(trade_pnls),
        "sharpe": mean / std if std > 0 else 0.0,
        "max_dd": max_dd,
        "avg_hold_h": sum(hold_hours) / n,
    }


def run_combo(params: dict, settings: Settings, instrument_id: str, bar_type: str) -> dict:
    run_config = build_run_config(
        str(settings.catalog_path), instrument_id, settings=settings,
        start=START - timedelta(days=WARMUP_DAYS), end=END,
        dispose_on_completion=False, raise_exception=True,
    )
    node = BacktestNode(configs=[run_config])
    node.build()
    engine = node.get_engine(run_config.id)
    engine.add_strategy(build_strategy(SPEC, params, instrument_id, bar_type))
    try:
        node.run()
        window_start_ns = dt_to_unix_nanos(START)
        closed = [
            p for p in engine.cache.positions()
            if p.is_closed and p.ts_closed >= window_start_ns
        ]
        closed.sort(key=lambda p: p.ts_closed)
        pnls = [p.realized_pnl.as_double() for p in closed]
        rets = [p.realized_return for p in closed]
        holds = [(p.ts_closed - p.ts_open) / 1e9 / 3_600 for p in closed]
        fills_df = engine.trader.generate_order_fills_report()
        fees = 0.0
        if "commission" in fills_df.columns:
            fees = fills_df["commission"].astype(str).str.extract(r"([\d.]+)").astype(float).sum()
        m = compute_metrics(pnls, rets, holds)
        m["fees"] = float(fees.iloc[0]) if hasattr(fees, "iloc") else float(fees)
        return m
    finally:
        node.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cost-mult", type=float, default=1.0)
    args = parser.parse_args()
    settings = cost_settings(load_settings(), args.cost_mult)
    instrument_id = settings.instrument_id_str
    bar_type = f"{instrument_id}-1-MINUTE-LAST-EXTERNAL"

    rows = []
    keys = sorted(GRID)
    for combo in itertools.product(*(GRID[k] for k in keys)):
        params = dict(zip(keys, combo))
        r = run_combo(params, settings, instrument_id, bar_type)
        rows.append((params, r))
        print(f"[scan] {params} total={r['total']:.2f} sharpe={r['sharpe']:.3f} "
              f"dd={r['max_dd']:.2f} trips={r['n_trades']} hold={r['avg_hold_h']:.1f}h "
              f"fees={r['fees']:.2f}")

    print(f"\n{'params':<64} {'trips':>5} {'total':>9} {'sharpe':>7} {'max_dd':>9} {'hold_h':>7}")
    for params, r in sorted(rows, key=lambda x: -x[1]["total"]):
        p = ", ".join(f"{k}={v}" for k, v in params.items())
        print(f"{p:<64} {r['n_trades']:>5} {r['total']:>9.2f} {r['sharpe']:>7.3f} "
              f"{r['max_dd']:>9.2f} {r['avg_hold_h']:>7.1f}")


if __name__ == "__main__":
    main()