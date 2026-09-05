"""Renko+MACD intraday comparison: brick-size grid, long-only vs long+short.

Handoff 20260905-bt-renko-macd-intraday. MACD(12,26,9) fixed — no parameter
optimization (handoff constraint). Brick size grid is a handoff-required
sensitivity analysis (paper value unconfirmed), reported as a table, never
selected. Sizing: unit default; vol-target as sensitivity only.

Universe: BTC-USDT-SWAP.OKX 1m bars (catalog's only instrument) — an
APPROXIMATE reproduction of NSE intraday equities, not a replication.

Drives BacktestNode (the only runner) via the same local pattern as
macd_crossover_compare so end-of-window open positions can be valued at the
engine's last price. Emits research/renko_macd_intraday/results.json.
No alpha here.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.model.enums import PriceType
from nautilus_trader.model.identifiers import Venue

from sngw_trader.config import load_settings
from sngw_trader.config.settings import Settings
from sngw_trader.research.config import GridSpec
from sngw_trader.research.executor import build_strategy
from sngw_trader.research.metrics import (
    compute_equity_metrics,
    compute_trade_metrics,
)
from sngw_trader.runners.backtest_okx import build_run_config, default_bar_type

INSTRUMENT_ID = "BTC-USDT-SWAP.OKX"

SPEC = GridSpec(
    strategy_path="sngw_trader.strategies.renko_macd_intraday:RenkoMacdIntraday",
    config_path="sngw_trader.strategies.renko_macd_intraday:RenkoMacdIntradayConfig",
    fixed={"trade_size": "0.02"},
    grid={},
)

SEGMENTS = {
    "is": (datetime(2021, 1, 1, tzinfo=timezone.utc), datetime(2022, 12, 31, tzinfo=timezone.utc)),
    "oos": (datetime(2023, 1, 1, tzinfo=timezone.utc), datetime(2025, 12, 30, tzinfo=timezone.utc)),
}

WARMUP_DAYS = 5  # brick-based: 5 days of 1m bars is thousands of bricks

# Brick sizes as a fraction of price (relative, not absolute — BTC price level
# independence, spec §3). Median (0.001) is the default report; rest is the
# sensitivity table.
BRICK_GRID = [0.0005, 0.001, 0.002, 0.004, 0.008]
MEDIAN_BRICK = 0.001

BASE_VARIANTS = {
    "long_only": {"allow_short": False},
    "long_short": {"allow_short": True},
}

OUT_DIR = Path("research/renko_macd_intraday")

INITIAL_CAPITAL = 10_000.0


def stress_settings(settings: Settings) -> Settings:
    """2x taker fee + 5x slippage stress variant."""
    return replace(
        settings,
        bt_taker_fee=settings.bt_taker_fee * 2,
        bt_prob_slippage=min(0.99, settings.bt_prob_slippage * 5),
    )


def evaluate(result, open_positions, engine_cache) -> dict:
    equity = compute_equity_metrics(result.equity_marks, INITIAL_CAPITAL)
    trades = compute_trade_metrics(result.trade_pnls, result.trade_returns)
    extra = 0.0
    n_open = 0
    for p in open_positions:
        try:
            px = engine_cache.price(p.instrument_id, PriceType.LAST)
            last = float(px) if px is not None else None
        except Exception:
            last = None
        if last is None:
            continue
        qty = float(p.quantity)
        avg = float(p.avg_px_open)
        side = 1.0 if p.side.name == "LONG" else -1.0
        extra += side * (last - avg) * qty
        n_open += 1
    # Intraday: trade count is large, so total cost share matters (spec §4.3).
    return {
        "n_trades_closed": result.n_trades,
        "n_open_at_end": n_open,
        "open_pnl": round(extra, 2),
        "total_pnl": round(result.total_pnl + extra, 2),
        "cagr": equity["annualized_return"] if equity else None,
        "mdd_ratio": equity["mdd_ratio"] if equity else None,
        "sharpe_like_sortino": equity["sortino"] if equity else None,
        "win_rate": trades["win_rate"] if trades else None,
        "profit_factor": trades["profit_factor"] if trades else None,
    }


def run_cell(settings: Settings, params: dict) -> dict:
    """One BacktestNode run; params carry variant + brick_size."""
    start, end = SEGMENTS[params.pop("segment")]
    brick_pct = params.pop("brick_pct")
    bar_type = default_bar_type(INSTRUMENT_ID)
    run_config = build_run_config(
        str(settings.catalog_path),
        INSTRUMENT_ID,
        settings=settings,
        start=start - timedelta(days=WARMUP_DAYS),
        end=end,
        dispose_on_completion=False,
        raise_exception=True,
        quiet=True,
    )
    node = BacktestNode(configs=[run_config])
    node.build()
    engine = node.get_engine(run_config.id)
    engine.add_strategy(build_strategy(SPEC, {"brick_pct": brick_pct, **params},
                                       INSTRUMENT_ID, bar_type))
    try:
        node.run()
        window_ns = dt_to_unix_nanos(start)
        all_positions = engine.cache.positions() + engine.cache.position_snapshots()
        closed = [p for p in all_positions if p.is_closed and p.ts_closed >= window_ns]
        closed.sort(key=lambda p: p.ts_closed)
        pnls = [p.realized_pnl.as_double() for p in closed]
        returns = [p.realized_return for p in closed]
        open_positions = [p for p in all_positions if not p.is_closed]
        marks = []
        account = engine.cache.account_for_venue(Venue("OKX"))
        if account is not None:
            events = account.events() if callable(account.events) else account.events
            for ev in events:
                if ev.ts_init >= window_ns and ev.balances:
                    marks.append((ev.ts_init, max(b.total.as_double() for b in ev.balances)))
            marks.sort()
        from sngw_trader.research.executor import RunResult

        result = RunResult(
            trade_pnls=pnls,
            trade_returns=returns,
            n_trades=len(closed),
            total_pnl=sum(pnls),
            equity_marks=marks,
        )
        return evaluate(result, open_positions, engine.cache)
    finally:
        node.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sensitivity", action="store_true",
        help="brick grid (non-median) + vol-target sizing (reported, never selected)",
    )
    args = parser.parse_args()

    settings = load_settings()
    stressed = stress_settings(settings)

    cells: dict[str, dict] = {}

    # Base matrix: {long_only, long_short} x {is, oos} x {base, stress},
    # median brick, unit sizing. Spec §5 gates 2-4.
    combos: list[tuple[str, str, str, dict]] = [
        (v, s, c, {**BASE_VARIANTS[v], "brick_pct": MEDIAN_BRICK, "segment": s})
        for v in BASE_VARIANTS for s in SEGMENTS for c in ("base", "stress")
    ]

    # Sensitivity: full brick grid x {is, oos} x both directions (base cost).
    if args.sensitivity:
        for b in BRICK_GRID:
            if b == MEDIAN_BRICK:
                continue  # already in the base matrix
            for v in BASE_VARIANTS:
                for s in SEGMENTS:
                    combos.append(
                        (f"brick{b}/{v}", s, "base",
                         {**BASE_VARIANTS[v], "brick_pct": b, "segment": s})
                    )
        # vol-target sizing sensitivity at the median brick.
        for v in BASE_VARIANTS:
            combos.append(
                (f"{v}/vt", "oos", "base",
                 {**BASE_VARIANTS[v], "brick_pct": MEDIAN_BRICK,
                  "sizing_mode": "vol_target", "segment": "oos"})
            )

    total = len(combos)
    for i, (name, _seg, cost, params) in enumerate(combos):
        key = f"{name}/{_seg}/{cost}"
        print(f"[{i + 1}/{total}] {key}", flush=True)
        st = stressed if cost == "stress" else settings
        cells[key] = run_cell(st, dict(params))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "results.json"
    out.write_text(json.dumps(cells, indent=2, default=str), encoding="utf-8")

    print("\n=== Renko+MACD intraday: median brick (0.1% of price), base cost ===")
    header = (f"{'variant/segment':32s} {'cl':>4s} {'op':>3s} {'pnl':>10s} "
              f"{'cagr':>8s} {'mdd':>7s} {'win':>6s} {'pf':>6s}")
    print(header)
    for key, m in cells.items():
        if "/stress" in key or "/vt" in key or key.startswith("brick"):
            continue
        print(
            f"{key:32s} {m['n_trades_closed']:>4d} {m['n_open_at_end']:>3d} "
            f"{m['total_pnl']:>10.2f} {_fmt(m['cagr']):>8s} {_fmt(m['mdd_ratio']):>7s} "
            f"{_fmt(m['win_rate']):>6s} {_fmt(m['profit_factor']):>6s}"
        )
    print("\n=== brick-size sensitivity (base cost) ===")
    print(f"{'brick':>14s} {'variant':>12s} {'segment':>5s} {'cl':>4s} {'pnl':>10s} {'win':>6s} {'pf':>6s}")
    for b in BRICK_GRID:
        for v in BASE_VARIANTS:
            for s in SEGMENTS:
                key = f"brick{b}/{v}/{s}/base" if b != MEDIAN_BRICK else f"{v}/{s}/base"
                m = cells.get(key)
                if m is None:
                    continue
                tag = f"{b:.4f}" + (" (median)" if b == MEDIAN_BRICK else "")
                print(
                    f"{tag:>14s} {v:>12s} {s:>5s} {m['n_trades_closed']:>4d} "
                    f"{m['total_pnl']:>10.2f} {_fmt(m['win_rate']):>6s} {_fmt(m['profit_factor']):>6s}"
                )
    print(f"\nsaved: {out}")
    return 0


def _fmt(x) -> str:
    return f"{x:.3f}" if isinstance(x, (int, float)) else "-"


if __name__ == "__main__":
    raise SystemExit(main())
