"""IS-only backtest for ThreeRedDays. No alpha.

USDJPY video rule applied to BTC-USDT-SWAP.OKX UTC daily bars — approximation,
not a reproduction.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.model.enums import PriceType
from nautilus_trader.model.identifiers import Venue

from sngw_trader.config import load_settings
from sngw_trader.config.settings import Settings
from sngw_trader.research.config import GridSpec
from sngw_trader.research.executor import (
    RunResult,
    build_strategy,
    extract_analyzer_equity_marks,
)
from sngw_trader.research.metrics import compute_equity_metrics, compute_trade_metrics
from sngw_trader.runners.backtest_okx import build_run_config, default_bar_type

INSTRUMENT_ID = "BTC-USDT-SWAP.OKX"
ENGINE_START = datetime(2019, 12, 16, tzinfo=timezone.utc)
IS_START = datetime(2020, 1, 1, tzinfo=timezone.utc)
IS_END = datetime(2022, 1, 1, tzinfo=timezone.utc)
INITIAL_CAPITAL = 10_000.0
OUT_DIR = Path("research/three_red_days")

SPEC = GridSpec(
    strategy_path="sngw_trader.strategies.three_red_days:ThreeRedDays",
    config_path="sngw_trader.strategies.three_red_days:ThreeRedDaysConfig",
    fixed={"trade_size": "0.01", "streak_len": 3, "hold_bars": 3},
    grid={},
)

ASSUMPTIONS = [
    "원본 시장은 USDJPY 일봉. BTC-USDT-SWAP UTC 일봉 적용은 근사이지 재현이 아니다.",
    "음봉 = close < open.",
    "스트릭 트리거는 == 3 에지. >= 3 상태 재진입이 아니다.",
    "일봉 경계는 UTC 00:00.",
    "진입은 다음날 첫 1분 시가(±latency/슬리피지). 영상 5분 지연 없음.",
    "청산은 3일째 마지막 1분 종가. 결측 시 다음날 시가 폴백.",
    "거래비용은 repo 백테스트 관례. 펀딩 미반영. unit qty만.",
]


def survived(
    n_trades: int,
    sharpe: float | None,
    profit_factor: float | None,
    pnls: list[float],
) -> dict:
    trades_ok = n_trades >= 20
    sharpe_ok = sharpe is not None and sharpe > 0
    if profit_factor is not None:
        pf_ok = profit_factor > 1
    else:
        pf_ok = n_trades > 0 and all(p > 0 for p in pnls)
    return {
        "trades_ok": trades_ok,
        "sharpe_ok": sharpe_ok,
        "pf_ok": pf_ok,
        "survived": bool(trades_ok and sharpe_ok and pf_ok),
    }


def stress_settings(settings: Settings) -> Settings:
    return replace(
        settings,
        bt_taker_fee=settings.bt_taker_fee * 2,
        bt_prob_slippage=min(0.99, settings.bt_prob_slippage * 5),
    )


def _account_marks(cache, window_ns: int) -> list[tuple[int, float]]:
    account = cache.account_for_venue(Venue("OKX"))
    if account is None:
        return []
    events = account.events() if callable(account.events) else account.events
    marks = []
    for ev in events:
        if ev.ts_init >= window_ns and ev.balances:
            marks.append((ev.ts_init, max(b.total.as_double() for b in ev.balances)))
    marks.sort()
    return marks


def evaluate(result: RunResult, open_positions, engine_cache) -> dict:
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
        "cagr": None if equity is None else equity["annualized_return"],
        "sharpe": None if equity is None else equity["sharpe"],
        "mdd_ratio": None if equity is None else equity["mdd_ratio"],
        "win_rate": None if trades is None else trades["win_rate"],
        "profit_factor": None if trades is None else trades["profit_factor"],
        "trade_pnls": result.trade_pnls,
    }


def run_cell(settings: Settings) -> dict:
    bar_type = default_bar_type(INSTRUMENT_ID)
    run_config = build_run_config(
        str(settings.catalog_path),
        INSTRUMENT_ID,
        settings=settings,
        start=ENGINE_START,
        end=IS_END,
        dispose_on_completion=False,
        raise_exception=True,
    )
    node = BacktestNode(configs=[run_config])
    node.build()
    engine = node.get_engine(run_config.id)
    engine.add_strategy(build_strategy(SPEC, {}, INSTRUMENT_ID, bar_type))
    try:
        node.run()
        window_ns = dt_to_unix_nanos(IS_START)
        all_positions = engine.cache.positions() + engine.cache.position_snapshots()
        closed = [p for p in all_positions if p.is_closed and p.ts_closed >= window_ns]
        closed.sort(key=lambda p: p.ts_closed)
        pnls = [p.realized_pnl.as_double() for p in closed]
        returns = [p.realized_return for p in closed]
        open_positions = [p for p in all_positions if not p.is_closed]
        marks = extract_analyzer_equity_marks(
            engine.portfolio.analyzer, INITIAL_CAPITAL, window_ns
        )
        if not marks:
            marks = _account_marks(engine.cache, window_ns)
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


def _fmt(x) -> str:
    return f"{x:.3f}" if isinstance(x, (int, float)) else "-"


def main() -> int:
    settings = load_settings()
    cells: dict[str, dict] = {}
    for cost, st in (("base", settings), ("stress", stress_settings(settings))):
        print(f"running is/{cost}", flush=True)
        cells[f"is/{cost}"] = run_cell(st)
    base = cells["is/base"]
    gate = survived(
        base["n_trades_closed"],
        base["sharpe"],
        base["profit_factor"],
        base["trade_pnls"],
    )
    payload = {
        "approximation": (
            "USDJPY video rule on BTC-USDT-SWAP.OKX UTC daily; not a reproduction"
        ),
        "instrument_id": INSTRUMENT_ID,
        "is": {
            "start": IS_START.isoformat(),
            "end": IS_END.isoformat(),
            "end_exclusive": True,
        },
        "params": {"streak_len": 3, "hold_bars": 3, "trade_size": "0.01"},
        "assumptions": ASSUMPTIONS,
        "cells": cells,
        "gate": gate,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "results.json"
    out.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    print(
        f"{'cell':12s} {'cl':>3s} {'op':>3s} {'pnl':>9s} "
        f"{'cagr':>8s} {'sharpe':>7s} {'mdd':>7s} {'win':>6s} {'pf':>6s}"
    )
    for key, m in cells.items():
        print(
            f"{key:12s} {m['n_trades_closed']:>3d} {m['n_open_at_end']:>3d} "
            f"{m['total_pnl']:>9.2f} {_fmt(m['cagr']):>8s} {_fmt(m['sharpe']):>7s} "
            f"{_fmt(m['mdd_ratio']):>7s} {_fmt(m['win_rate']):>6s} "
            f"{_fmt(m['profit_factor']):>6s}"
        )
    print(f"gate: {gate}")
    print(f"saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
