"""KD-MACD comparison matrix: long-only vs long+short over paper/IS/OOS segments.

Drives BacktestNode (the only runner) via a thin local wrapper so end-of-window
open positions can be valued at the engine's last price. Emits
research/kd_macd/results.json + a console table. No alpha here.
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
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

from sngw_trader.config import load_settings
from sngw_trader.config.settings import Settings
from sngw_trader.indicators.bar_aggregator import BarAggregator
from sngw_trader.research.config import GridSpec
from sngw_trader.research.executor import build_strategy
from sngw_trader.research.metrics import (
    compute_equity_metrics,
    compute_trade_metrics,
)
from sngw_trader.runners.backtest_okx import build_run_config, default_bar_type

INSTRUMENT_ID = "BTC-USDT-SWAP.OKX"

SPEC = GridSpec(
    strategy_path="sngw_trader.strategies.kd_macd_crypto:KdMacdCrypto",
    config_path="sngw_trader.strategies.kd_macd_crypto:KdMacdCryptoConfig",
    # 0.02 BTC unit with size_increment 0.01: vol-target scale granularity 50%,
    # max scale 2.0 -> 0.04 BTC stays affordable on 10k USDT across OOS prices.
    fixed={"trade_size": "0.02", "size_max_scale": 2.0},
    grid={},
)

SEGMENTS = {
    "paper": (datetime(2020, 7, 1, tzinfo=timezone.utc), datetime(2020, 12, 31, tzinfo=timezone.utc)),
    "is": (datetime(2021, 1, 1, tzinfo=timezone.utc), datetime(2022, 12, 31, tzinfo=timezone.utc)),
    "oos": (datetime(2023, 1, 1, tzinfo=timezone.utc), datetime(2025, 12, 30, tzinfo=timezone.utc)),
}

WARMUP_DAYS = 120  # kd 9 + macd 35 + vol half-life*3 (60) -> 120 safe

VARIANTS = {
    "long_only": {"allow_short": False},
    "long_short": {"allow_short": True},
}

OUT_DIR = Path("research/kd_macd")

INITIAL_CAPITAL = 10_000.0


def stress_settings(settings: Settings) -> Settings:
    """2x taker fee + 5x slippage stress variant."""
    return replace(
        settings,
        bt_taker_fee=settings.bt_taker_fee * 2,
        bt_prob_slippage=min(0.99, settings.bt_prob_slippage * 5),
    )


def mtm_equity_marks(
    daily_closes: list[tuple[int, float]],
    cash_marks: list[tuple[int, float]],
    lots: list[tuple[int, int | None, float, float]],
    window_start_ns: int,
    initial_cash: float,
) -> list[tuple[int, float]]:
    """Daily mark-to-market equity: cash + signed qty * (close - avg)."""
    cash_marks = sorted(cash_marks, key=lambda m: m[0])

    def cash_at(ts: int) -> float:
        last = initial_cash
        for t, v in cash_marks:
            if t > ts:
                break
            last = v
        return last

    def lot_at(ts: int) -> tuple[float, float]:
        qty = avg = 0.0
        for opened, closed, q, a in lots:
            if opened <= ts and (closed is None or ts < closed):
                qty += q
                avg = a
        return qty, avg

    out: list[tuple[int, float]] = []
    for ts, px in daily_closes:
        if ts < window_start_ns:
            continue
        qty, avg = lot_at(ts)
        upl = qty * (px - avg) if qty != 0 else 0.0
        out.append((ts, cash_at(ts) + upl))
    return out


def _lots_from_positions(positions) -> list[tuple[int, int | None, float, float]]:
    lots = []
    for p in positions:
        side = 1.0 if p.side.name == "LONG" else -1.0
        closed = int(p.ts_closed) if p.is_closed else None
        lots.append((int(p.ts_opened), closed, side * float(p.quantity), float(p.avg_px_open)))
    return lots


def load_daily_closes(catalog_path: str, bar_type: str) -> list[tuple[int, float]]:
    """UTC-daily closes from the 1-minute catalog. Loaded once per process."""
    catalog = ParquetDataCatalog(path=str(catalog_path))
    bars = catalog.bars(bar_types=[bar_type])
    agg = BarAggregator(86_400)
    out: list[tuple[int, float]] = []
    for bar in bars:
        day = agg.update(
            int(bar.ts_init),
            bar.open.as_double(),
            bar.high.as_double(),
            bar.low.as_double(),
            bar.close.as_double(),
        )
        if day is not None:
            out.append((day.ts_close_ns, day.close))
    return out


def evaluate(result, open_positions, engine_cache) -> dict:
    basis = result.equity_marks[0][1] if result.equity_marks else INITIAL_CAPITAL
    equity = compute_equity_metrics(result.equity_marks, basis)
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


def run_cell(
    variant: str,
    segment: str,
    cost: str,
    settings: Settings,
    daily_closes: list[tuple[int, float]],
    params: dict | None = None,
) -> dict:
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
    engine.add_strategy(build_strategy(SPEC, {**VARIANTS[variant], **(params or {})},
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
        cash_marks = []
        account = engine.cache.account_for_venue(Venue("OKX"))
        if account is not None:
            events = account.events() if callable(account.events) else account.events
            for ev in events:
                if ev.ts_init >= window_ns and ev.balances:
                    cash_marks.append(
                        (ev.ts_init, max(b.total.as_double() for b in ev.balances))
                    )
            cash_marks.sort()
        lots = _lots_from_positions(all_positions)
        end_ns = dt_to_unix_nanos(end)
        window_closes = [(t, px) for t, px in daily_closes if window_ns <= t <= end_ns]
        marks = mtm_equity_marks(
            window_closes, cash_marks, lots, window_ns, INITIAL_CAPITAL
        )
        if not marks:
            marks = cash_marks
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
        help="also run state trigger + kd_n in {5,14} (reported, never selected)",
    )
    args = parser.parse_args()

    settings = load_settings()
    stressed = stress_settings(settings)
    bar_type = default_bar_type(INSTRUMENT_ID)
    print("loading daily closes from catalog...", flush=True)
    daily_closes = load_daily_closes(str(settings.catalog_path), bar_type)

    cells: dict[str, dict] = {}
    combos = [
        (v, s, c) for v in VARIANTS for s in SEGMENTS for c in ("base", "stress")
    ]
    sens_combos = []
    if args.sensitivity:
        for kd_n in (5, 14):
            for v in VARIANTS:
                sens_combos.append((v, "oos", "base", {"kd_n": kd_n, "trigger_mode": "state"}))
    total = len(combos) + len(sens_combos)
    done = 0
    for variant, segment, cost in combos:
        key = f"{variant}/{segment}/{cost}"
        print(f"[{done + 1}/{total}] {key}", flush=True)
        st = stressed if cost == "stress" else settings
        cells[key] = run_cell(variant, segment, cost, st, daily_closes)
        done += 1
    for variant, segment, cost, params in sens_combos:
        key = f"{variant}/{segment}/{cost}/sens(kd_n={params['kd_n']},state)"
        print(f"[{done + 1}/{total}] {key}", flush=True)
        cells[key] = run_cell(
            variant, segment, cost, settings, daily_closes, params
        )
        done += 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "results.json"
    out.write_text(json.dumps(cells, indent=2, default=str), encoding="utf-8")

    print("\n=== KD-MACD long_only vs long_short (base cost) ===")
    header = (f"{'variant/segment':28s} {'cl':>3s} {'op':>3s} {'pnl':>9s} "
              f"{'cagr':>8s} {'mdd':>7s} {'win':>6s} {'pf':>6s}")
    print(header)
    for key, m in cells.items():
        if key.endswith("/stress") or "/sens(" in key:
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
