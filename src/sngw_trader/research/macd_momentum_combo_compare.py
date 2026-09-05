"""MACD + Momentum combo comparison matrix (handoff 20260905-bt-macd-momentum-combo).

Chio 2022 (arXiv:2206.12282) Table 4 rules, fixed parameters — no
optimization (handoff constraint). Matrix: {rsi, mfi} x {long_only,
long_short} x {all_in, unit} x {base, stress} over IS/OOS segments, plus
ATR bracket TP/SL sensitivity cells (long_short only). Long-only all-in
base cells are the paper-approximation headline; long_short is a
user-approved direction-symmetric extension, not a paper rule.

Drives BacktestNode (the only runner) via a thin local wrapper so
end-of-window open positions are valued at the engine's last price.
Emits research/macd_momentum_combo/results.json + a console table.
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
    strategy_path="sngw_trader.strategies.macd_momentum_combo:MacdMomentumCombo",
    config_path="sngw_trader.strategies.macd_momentum_combo:MacdMomentumComboConfig",
    fixed={"trade_size": "0.02"},
    grid={},
)

# Paper markets (US index constituents) are not reproducible: the catalog
# holds only BTC-USDT-SWAP.OKX 1m bars (2019-12-16 ~ 2025-12-30) — BTC
# daily approximation, not a replication.
SEGMENTS = {
    "is": (datetime(2021, 1, 1, tzinfo=timezone.utc), datetime(2022, 12, 31, tzinfo=timezone.utc)),
    "oos": (datetime(2023, 1, 1, tzinfo=timezone.utc), datetime(2025, 12, 30, tzinfo=timezone.utc)),
}

WARMUP_DAYS = 120  # macd 26+9 + momentum 14 + 6-bar window -> 120 safe

OUT_DIR = Path("research/macd_momentum_combo")

INITIAL_CAPITAL = 10_000.0

MOMENTA = ("rsi", "mfi")
DIRECTIONS = {"long_only": {"allow_short": False}, "long_short": {"allow_short": True}}
SIZINGS = {"all_in": {"all_in": True}, "unit": {"all_in": False}}
# bracket TP/SL sensitivity: long_short only, all_in, base cost
BRACKET_PARAMS = {"use_bracket": True, "all_in": True}


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


def run_cell(params: dict, segment: str, settings: Settings) -> dict:
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
    engine.add_strategy(build_strategy(SPEC, params, INSTRUMENT_ID, bar_type))
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
        help="also run ATR bracket TP/SL cells (long_short, all_in, base cost)",
    )
    args = parser.parse_args()

    settings = load_settings()
    stressed = stress_settings(settings)

    cells: dict[str, dict] = {}
    combos = [
        (m, d, z, s, c)
        for m in MOMENTA
        for d in DIRECTIONS
        for z in SIZINGS
        for s in SEGMENTS
        for c in ("base", "stress")
    ]
    sens_combos = [
        (m, s) for m in MOMENTA for s in SEGMENTS
    ] if args.sensitivity else []
    total = len(combos) + len(sens_combos)
    done = 0
    for m, d, z, segment, cost in combos:
        key = f"{m}/{d}/{z}/{segment}/{cost}"
        print(f"[{done + 1}/{total}] {key}", flush=True)
        st = stressed if cost == "stress" else settings
        params = {"momentum": m, **DIRECTIONS[d], **SIZINGS[z]}
        cells[key] = run_cell(params, segment, st)
        done += 1
    for m, segment in sens_combos:
        key = f"{m}/bracket_sens/{segment}"
        print(f"[{done + 1}/{total}] {key}", flush=True)
        cells[key] = run_cell({"momentum": m, **DIRECTIONS["long_short"], **BRACKET_PARAMS}, segment, settings)
        done += 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "results.json"
    out.write_text(json.dumps(cells, indent=2, default=str), encoding="utf-8")

    print("\n=== MACD+Momentum combo (base cost) ===")
    header = (f"{'cell':36s} {'cl':>4s} {'op':>3s} {'pnl':>10s} "
              f"{'cagr':>8s} {'mdd':>7s} {'win':>6s} {'pf':>6s}")
    print(header)
    for key, m in cells.items():
        if key.endswith("/stress") or "sens" in key:
            continue
        print(
            f"{key:36s} {m['n_trades_closed']:>4d} {m['n_open_at_end']:>3d} "
            f"{m['total_pnl']:>10.2f} {_fmt(m['cagr']):>8s} {_fmt(m['mdd_ratio']):>7s} "
            f"{_fmt(m['win_rate']):>6s} {_fmt(m['profit_factor']):>6s}"
        )
    if args.sensitivity:
        print("\n-- bracket TP/SL sensitivity (long_short/all_in/base) --")
        for key, m in cells.items():
            if "bracket_sens" in key:
                print(
                    f"{key:36s} {m['n_trades_closed']:>4d} {m['n_open_at_end']:>3d} "
                    f"{m['total_pnl']:>10.2f} {_fmt(m['cagr']):>8s} {_fmt(m['mdd_ratio']):>7s} "
                    f"{_fmt(m['win_rate']):>6s} {_fmt(m['profit_factor']):>6s}"
                )
    print(f"\nsaved: {out}")
    return 0


def _fmt(x) -> str:
    return f"{x:.3f}" if isinstance(x, (int, float)) else "-"


if __name__ == "__main__":
    raise SystemExit(main())
