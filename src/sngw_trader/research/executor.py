"""Run one backtest window on a fresh BacktestNode. No alpha here."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.trading.config import ImportableStrategyConfig, StrategyFactory

from sngw_trader.config.settings import Settings
from sngw_trader.research.config import GridSpec
from sngw_trader.runners.backtest_okx import build_run_config, default_bar_type


@dataclass(frozen=True)
class RunResult:
    trade_pnls: list[float]
    trade_returns: list[float]
    n_trades: int
    total_pnl: float
    equity_marks: list[tuple[int, float]] = field(default_factory=list)


def build_strategy(spec: GridSpec, params: dict[str, object], instrument_id: str, bar_type: str):
    config = {
        **spec.fixed,
        "instrument_id": instrument_id,
        "bar_type": bar_type,
        **params,
    }
    return StrategyFactory.create(
        ImportableStrategyConfig(
            strategy_path=spec.strategy_path,
            config_path=spec.config_path,
            config=config,
        )
    )


def extract_run_result(positions: list, window_start_ns: int) -> RunResult:
    """Closed positions at/after window start (warmup pad excluded), close-time order."""
    closed = [p for p in positions if p.is_closed and p.ts_closed >= window_start_ns]
    closed.sort(key=lambda p: p.ts_closed)
    pnls = [p.realized_pnl.as_double() for p in closed]
    returns = [p.realized_return for p in closed]
    return RunResult(
        trade_pnls=pnls,
        trade_returns=returns,
        n_trades=len(closed),
        total_pnl=sum(pnls),
    )


def extract_equity_marks(cache, venue: str, window_start_ns: int) -> list[tuple[int, float]]:
    """(ts_ns, balance total) marks from account state events at/after window start."""
    account = cache.account_for_venue(Venue(venue))
    if account is None:
        return []
    marks: list[tuple[int, float]] = []
    events = account.events() if callable(account.events) else account.events
    for ev in events:
        if ev.ts_init < window_start_ns or not ev.balances:
            continue
        # ponytail: 단일 자산 계정 가정. 복수 자산이면 최대 잔고 1개만 사용.
        total = max(b.total.as_double() for b in ev.balances)
        marks.append((ev.ts_init, total))
    marks.sort(key=lambda m: m[0])
    return marks


def run_window(
    catalog_path: str,
    instrument_id: str,
    settings: Settings,
    spec: GridSpec,
    params: dict[str, object],
    start: datetime,
    end: datetime,
    warmup_days: int = 1,
):
    bar_type = default_bar_type(instrument_id)
    run_config = build_run_config(
        catalog_path,
        instrument_id,
        settings=settings,
        start=start - timedelta(days=warmup_days),
        end=end,
        dispose_on_completion=False,
        raise_exception=True,
    )
    node = BacktestNode(configs=[run_config])
    node.build()
    engine = node.get_engine(run_config.id)
    if engine is None:
        raise RuntimeError(f"Engine not built for run config {run_config.id}")
    engine.add_strategy(build_strategy(spec, params, instrument_id, bar_type))
    try:
        node.run()
        window_start_ns = dt_to_unix_nanos(start)
        result = extract_run_result(
            engine.cache.positions() + engine.cache.position_snapshots(),
            window_start_ns,
        )
        marks = extract_equity_marks(
            engine.cache, instrument_id.rsplit(".", 1)[-1], window_start_ns
        )
        return replace(result, equity_marks=marks)
    finally:
        node.dispose()
