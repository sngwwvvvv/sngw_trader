"""VPVMA comparison matrix: long-only vs long+short over IS/OOS segments.

Handoff 20260905-bt-vpvma. VPVMA(12,26,9, bw=0.1) fixed — no parameter
optimization (handoff constraint; bandwidth is the paper's own trade-count
control variable, so it is not scanned either). Base variants use unit sizing
(paper trades all-in per stock); vol-target sizing is a sensitivity run
reported separately, never selected.

Structure cloned from macd_crossover_compare.py. Emits research/vpvma/
results.json + a console table. No alpha here.
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
    strategy_path="sngw_trader.strategies.vpvma:Vpvma",
    config_path="sngw_trader.strategies.vpvma:VpvmaConfig",
    fixed={"trade_size": "0.02"},
    grid={},
)

# US-equity evidence markets are not reproducible: the catalog holds only
# BTC-USDT-SWAP.OKX 1m bars (2019-12-16 ~ 2025-12-30) — BTC approximate run.
SEGMENTS = {
    "is": (datetime(2021, 1, 1, tzinfo=timezone.utc), datetime(2022, 12, 31, tzinfo=timezone.utc)),
    "oos": (datetime(2023, 1, 1, tzinfo=timezone.utc), datetime(2025, 12, 30, tzinfo=timezone.utc)),
}

WARMUP_DAYS = 120  # slow 26 + sign 9 + EMA convergence -> 120 safe

BASE_VARIANTS = {
    "long_only": {"allow_short": False},
    "long_short": {"allow_short": True},
}

SENS_VARIANTS = {
    "long_only/vt": {"allow_short": False, "sizing_mode": "vol_target"},
    "long_short/vt": {"allow_short": True, "sizing_mode": "vol_target"},
}

OUT_DIR = Path("research/vpvma")

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


def run_cell(variant: str, segment: str, cost: str, settings: Settings, params: dict | None = None) -> dict:
    start, end = SEGMENTS[segment]
    bar_type = default_bar_type(INSTRUMENT_ID)
    run_config = build_run_config(
        str(settings.catalog_path),
        INSTRUMENT_ID,
        settings=settings,
        start=start - timedelta(days=WARMUP_DAYS),
        end=end,
        dispose_on_completion=False,
        raise_exception=True,
    )
    node = BacktestNode(configs=[run_config])
    node.build()
    engine = node.get_engine(run_config.id)
    engine.add_strategy(build_strategy(SPEC, {**BASE_VARIANTS[variant.split("/")[0]], **(params or {})},
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
        help="also run vol-target sizing (reported, never selected)",
    )
    args = parser.parse_args()

    settings = load_settings()
    stressed = stress_settings(settings)

    cells: dict[str, dict] = {}
    combos = [
        (v, s, c) for v in BASE_VARIANTS for s in SEGMENTS for c in ("base", "stress")
    ]
    sens_combos = []
    if args.sensitivity:
        for v in SENS_VARIANTS:
            sens_combos.append((v, "oos", "base", SENS_VARIANTS[v]))
    total = len(combos) + len(sens_combos)
    done = 0
    for variant, segment, cost in combos:
        key = f"{variant}/{segment}/{cost}"
        print(f"[{done + 1}/{total}] {key}", flush=True)
        st = stressed if cost == "stress" else settings
        cells[key] = run_cell(variant, segment, cost, st)
        done += 1
    for variant, segment, cost, params in sens_combos:
        key = f"{variant}/{segment}/{cost}/sens"
        print(f"[{done + 1}/{total}] {key}", flush=True)
        cells[key] = run_cell(variant.split("/")[0], segment, cost, settings, params)
        done += 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "results.json"
    out.write_text(json.dumps(cells, indent=2, default=str), encoding="utf-8")

    print("\n=== VPVMA long_only vs long_short (base cost) ===")
    header = (f"{'variant/segment':28s} {'cl':>3s} {'op':>3s} {'pnl':>9s} "
              f"{'cagr':>8s} {'mdd':>7s} {'win':>6s} {'pf':>6s}")
    print(header)
    for key, m in cells.items():
        if key.endswith("/stress") or key.endswith("/sens"):
            continue
        print(
            f"{key:28s} {m['n_trades_closed']:>3d} {m['n_open_at_end']:>3d} "
            f"{m['total_pnl']:>9.2f} {_fmt(m['cagr']):>8s} {_fmt(m['mdd_ratio']):>7s} "
            f"{_fmt(m['win_rate']):>6s} {_fmt(m['profit_factor']):>6s}"
        )
    print(f"\nsaved: {out}")
    return 0


def _fmt(x) -> str:
    return f"{x:.3f}" if isinstance(x, (int, float)) else "-"


if __name__ == "__main__":
    raise SystemExit(main())
