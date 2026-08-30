"""Grid scan for ErrMomentumRegime over a fixed period. No alpha here."""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.model.identifiers import InstrumentId

from sngw_trader.config import load_settings
from sngw_trader.research.config import GridSpec
from sngw_trader.research.executor import build_strategy
from sngw_trader.runners.backtest_okx import build_run_config

START = datetime(2023, 1, 1, tzinfo=timezone.utc)
END = datetime(2025, 12, 31, tzinfo=timezone.utc)
WARMUP_DAYS = 330  # strategy fully warm at START (prior 3y data available)

SPEC = GridSpec(
    strategy_path="sngw_trader.strategies.err_momentum_regime:ErrMomentumRegime",
    config_path="sngw_trader.strategies.err_momentum_regime:ErrMomentumRegimeConfig",
    fixed={"trade_size": "0.01"},
    grid={},
)

GRID: dict[str, list] = {}  # defaults: L=200, theta=0.0, vol_threshold=0.8


def run_combo(params: dict, instrument_id: str, bar_type: str) -> dict:
    settings = load_settings()
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
        iid = InstrumentId.from_str(instrument_id)
        portfolio = engine.portfolio
        fills_df = engine.trader.generate_order_fills_report()
        fees = 0.0
        if "commission" in fills_df.columns:
            fees = fills_df["commission"].astype(str).str.extract(r"([\d.]+)").astype(float).sum()
        return {
            "realized": portfolio.realized_pnl(iid).as_double(),
            "total": portfolio.total_pnl(iid).as_double(),
            "round_trips": len(fills_df) // 2,
            "fees": float(fees.iloc[0]) if hasattr(fees, "iloc") else float(fees),
        }
    finally:
        node.dispose()


def main() -> None:
    settings = load_settings()
    instrument_id = settings.instrument_id_str
    bar_type = f"{instrument_id}-1-MINUTE-LAST-EXTERNAL"

    rows = []
    keys = sorted(GRID)
    for combo in itertools.product(*(GRID[k] for k in keys)):
        params = dict(zip(keys, combo))
        r = run_combo(params, instrument_id, bar_type)
        rows.append((params, r))
        print(f"[scan] {params} realized={r['realized']:.2f} "
              f"total={r['total']:.2f} trips={r['round_trips']} fees={r['fees']:.2f}")

    print(f"\n{'params':<48} {'trips':>5} {'realized':>9} {'total':>8}")
    for params, r in sorted(rows, key=lambda x: -x[1]["realized"]):
        p = ", ".join(f"{k}={v}" for k, v in params.items())
        print(f"{p:<48} {r['round_trips']:>5} {r['realized']:>9.2f} {r['total']:>8.2f}")


if __name__ == "__main__":
    main()