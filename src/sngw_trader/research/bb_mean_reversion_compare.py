"""IS-only benchmark comparison for BB mean-reversion and its volume variants.

Report is a fixed-period, default-param comparison table — the control
(BB only) against volume/OI variants. Realized/total PnL and round-trip
count only; no alpha decisions here.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.config import BacktestDataConfig, BacktestRunConfig
from nautilus_trader.model import Bar, BarType

from sngw_trader.config import load_settings
from sngw_trader.config.settings import Settings
from sngw_trader.research.config import GridSpec
from sngw_trader.research.executor import build_strategy
from sngw_trader.runners.backtest_okx import build_run_config

INSTRUMENT_ID = "BTC-USDT-SWAP.OKX"
START = datetime(2025, 1, 1, tzinfo=timezone.utc)
END = datetime(2025, 12, 31, tzinfo=timezone.utc)

SPECS = {
    "bb_benchmark": GridSpec(
        strategy_path="sngw_trader.strategies.bb_mean_reversion:BbMeanReversion",
        config_path="sngw_trader.strategies.bb_mean_reversion:BbMeanReversionConfig",
        fixed={"trade_size": "0.01"},
        grid={},
    ),
    "volume_climax": GridSpec(
        strategy_path=(
            "sngw_trader.strategies.volume_climax_mean_reversion:"
            "VolumeClimaxMeanReversion"
        ),
        config_path=(
            "sngw_trader.strategies.volume_climax_mean_reversion:"
            "VolumeClimaxMeanReversionConfig"
        ),
        fixed={"trade_size": "0.01"},
        grid={},
    ),
    "volume_fade": GridSpec(
        strategy_path=(
            "sngw_trader.strategies.volume_fade_mean_reversion:"
            "VolumeFadeMeanReversion"
        ),
        config_path=(
            "sngw_trader.strategies.volume_fade_mean_reversion:"
            "VolumeFadeMeanReversionConfig"
        ),
        fixed={"trade_size": "0.01"},
        grid={},
    ),
    "rejection": GridSpec(
        strategy_path="sngw_trader.strategies.rejection_mean_reversion:RejectionMeanReversion",
        config_path="sngw_trader.strategies.rejection_mean_reversion:RejectionMeanReversionConfig",
        fixed={"trade_size": "0.01"},
        grid={},
    ),
    "volume_reentry": GridSpec(
        strategy_path=(
            "sngw_trader.strategies.volume_reentry_mean_reversion:"
            "VolumeReentryMeanReversion"
        ),
        config_path=(
            "sngw_trader.strategies.volume_reentry_mean_reversion:"
            "VolumeReentryMeanReversionConfig"
        ),
        fixed={"trade_size": "0.01"},
        grid={},
    ),
}


def bb_bar_type(instrument_id: str) -> BarType:
    return BarType.from_str(
        f"{instrument_id}-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL"
    )


def build_bb_run_config(
    settings: Settings,
    start: datetime | None = None,
    end: datetime | None = None,
) -> BacktestRunConfig:
    base = build_run_config(
        str(settings.catalog_path),
        settings.instrument_id_str,
        settings,
        start=start,
        end=end,
    )
    data = BacktestDataConfig(
        data_cls=Bar,
        catalog_path=str(settings.catalog_path),
        bar_types=[f"{settings.instrument_id_str}-1-MINUTE-LAST-EXTERNAL"],
        start_time=start.isoformat() if start else None,
        end_time=end.isoformat() if end else None,
    )
    return BacktestRunConfig(
        venues=base.venues,
        data=[data],
        engine=base.engine,
        dispose_on_completion=False,
        raise_exception=base.raise_exception,
    )


def run(spec: GridSpec, settings: Settings, start: datetime, end: datetime) -> dict:
    instrument_id = settings.instrument_id_str
    run_config = build_bb_run_config(settings, start=start, end=end)
    node = BacktestNode(configs=[run_config])
    node.build()
    engine = node.get_engine(run_config.id)
    engine.add_strategy(
        build_strategy(spec, {}, instrument_id, str(bb_bar_type(instrument_id)))
    )
    try:
        node.run()
        portfolio = engine.portfolio
        iid = __import__(
            "nautilus_trader.model.identifiers", fromlist=["InstrumentId"]
        ).InstrumentId.from_str(instrument_id)
        realized = portfolio.realized_pnl(iid).as_double()
        total = portfolio.total_pnl(iid).as_double()
        fills = len(engine.trader.generate_order_fills_report())
        return {"realized": realized, "total": total, "round_trips": fills // 2}
    finally:
        node.dispose()


def main() -> None:
    settings = load_settings()
    if not settings.instrument_id_str.endswith(".OKX"):
        raise SystemExit("BB benchmark supports OKX instruments only")
    rows = {name: run(spec, settings, START, END) for name, spec in SPECS.items()}

    print(f"{'strategy':<16} {'round_trips':>11} {'realized_pnl':>12} {'total_pnl':>10}")
    for name, r in rows.items():
        print(
            f"{name:<16} {r['round_trips']:>11} {r['realized']:>12.2f} {r['total']:>10.2f}"
        )
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()