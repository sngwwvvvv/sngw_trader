"""Compare two strategies over a fixed period with default params.

NETTING OMS reuses one position object, so per-trade extraction is unreliable;
we report portfolio realized/total PnL and round-trip count from fills. No alpha.
"""

from __future__ import annotations

from datetime import datetime, timezone

from nautilus_trader.backtest.node import BacktestNode

from sngw_trader.config import load_settings
from sngw_trader.research.config import GridSpec
from sngw_trader.research.executor import build_strategy
from sngw_trader.runners.backtest_okx import build_run_config, default_bar_type

START = datetime(2021, 1, 1, tzinfo=timezone.utc)
END = datetime(2022, 12, 31, tzinfo=timezone.utc)
WARMUP_DAYS = 330  # >= ~310 cal days = 220 trading days (momentum 200 + w_f + w_e) before START

SPECS = {
    "err_momentum_regime": GridSpec(
        strategy_path="sngw_trader.strategies.err_momentum_regime:ErrMomentumRegime",
        config_path="sngw_trader.strategies.err_momentum_regime:ErrMomentumRegimeConfig",
        fixed={"trade_size": "0.01"},
        grid={},
    ),
    "err_mom_ema30_entry": GridSpec(
        strategy_path="sngw_trader.strategies.err_mom_ema30_entry:ErrMomEma30Entry",
        config_path="sngw_trader.strategies.err_mom_ema30_entry:ErrMomEma30EntryConfig",
        fixed={"trade_size": "0.01"},
        grid={},
    ),
}


def run(spec: GridSpec, instrument_id: str, bar_type: str) -> dict:
    settings = load_settings()
    catalog = str(settings.catalog_path)
    run_config = build_run_config(
        catalog, instrument_id, settings=settings,
        start=START - __import__("datetime").timedelta(days=WARMUP_DAYS),
        end=END, dispose_on_completion=False, raise_exception=True,
    )
    node = BacktestNode(configs=[run_config])
    node.build()
    engine = node.get_engine(run_config.id)
    engine.add_strategy(build_strategy(spec, {}, instrument_id, bar_type))
    try:
        node.run()
        portfolio = engine.portfolio
        iid = __import__("nautilus_trader.model.identifiers", fromlist=["InstrumentId"]).InstrumentId.from_str(instrument_id)
        realized = portfolio.realized_pnl(iid).as_double()
        total = portfolio.total_pnl(iid).as_double()
        fills = len(engine.trader.generate_order_fills_report())
        return {"realized": realized, "total": total, "round_trips": fills // 2}
    finally:
        node.dispose()


def main() -> None:
    settings = load_settings()
    instrument_id = settings.instrument_id_str
    bar_type = default_bar_type(instrument_id)
    rows = {name: run(spec, instrument_id, bar_type) for name, spec in SPECS.items()}

    print(f"{'strategy':<22} {'round_trips':>11} {'realized_pnl':>12} {'total_pnl':>10}")
    for name, r in rows.items():
        print(f"{name:<22} {r['round_trips']:>11} {r['realized']:>12.2f} {r['total']:>10.2f}")


if __name__ == "__main__":
    main()