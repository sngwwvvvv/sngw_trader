"""IS benchmark comparison for BB mean-reversion and its volume variants.

Each strategy (control + volume/OI variants) runs the same 9-cell TP/SL
grid: sl atr_mult 2/3/4 x tp_atr_mult 1.5/2.5/4, ATR take-profit, and
positions held across sessions. Funding cost is applied outside the
engine from OKX funding-rate history (data/funding.py pattern).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from itertools import product

from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.config import BacktestDataConfig, BacktestRunConfig
from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.model import Bar, BarType

from sngw_trader.config import load_settings
from sngw_trader.config.settings import Settings
from sngw_trader.data.funding import fetch_funding_rates_proxy, funding_cost
from sngw_trader.research.config import GridSpec
from sngw_trader.research.executor import build_strategy, extract_fills
from sngw_trader.runners.backtest_okx import build_run_config

INSTRUMENT_ID = "BTC-USDT-SWAP.OKX"
START = datetime(2025, 1, 1, tzinfo=timezone.utc)
END = datetime(2025, 12, 31, tzinfo=timezone.utc)

SL_ATR_MULTS = (2.0, 3.0, 4.0)
TP_ATR_MULTS = (1.5, 2.5, 4.0)

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


def tp_sl_cells() -> list[dict[str, float]]:
    """Hold-across-session ATR TP/SL grid: sl 2/3/4 x tp 1.5/2.5/4."""
    return [
        {
            "tp_mode": "atr",
            "hold_across_sessions": True,
            "atr_mult": sl,
            "tp_atr_mult": tp,
        }
        for sl, tp in product(SL_ATR_MULTS, TP_ATR_MULTS)
    ]


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
        quiet=True,
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


def run(
    spec: GridSpec,
    params: dict,
    settings: Settings,
    start: datetime,
    end: datetime,
    rates: dict[int, float],
) -> dict:
    instrument_id = settings.instrument_id_str
    run_config = build_bb_run_config(settings, start=start, end=end)
    node = BacktestNode(configs=[run_config])
    node.build()
    engine = node.get_engine(run_config.id)
    engine.add_strategy(
        build_strategy(spec, params, instrument_id, str(bb_bar_type(instrument_id)))
    )
    try:
        node.run()
        portfolio = engine.portfolio
        iid = __import__(
            "nautilus_trader.model.identifiers", fromlist=["InstrumentId"]
        ).InstrumentId.from_str(instrument_id)
        realized = portfolio.realized_pnl(iid).as_double()
        total = portfolio.total_pnl(iid).as_double()
        fills_report = engine.trader.generate_order_fills_report()
        fills = extract_fills(fills_report, dt_to_unix_nanos(start))
        # funding_cost returns a signed cost (positive = loss).
        funding = funding_cost(fills, rates)
        return {
            "realized": realized,
            "total": total,
            "round_trips": len(fills_report) // 2,
            "funding": funding,
            "net": total - funding,
        }
    finally:
        node.dispose()


def main() -> None:
    settings = load_settings()
    if not settings.instrument_id_str.endswith(".OKX"):
        raise SystemExit("BB benchmark supports OKX instruments only")
    inst_id = settings.instrument_id_str.split(".", 1)[0]
    # OKX only serves ~3 months of funding history; use the Binance proxy.
    rates = fetch_funding_rates_proxy(
        inst_id, int(START.timestamp() * 1000), int(END.timestamp() * 1000)
    )
    print(f"funding points (Binance proxy): {len(rates)}")

    cells = tp_sl_cells()
    rows = []
    for name, spec in SPECS.items():
        for cell in cells:
            result = run(spec, cell, settings, START, END, rates)
            rows.append(
                {
                    "strategy": name,
                    "sl_atr_mult": cell["atr_mult"],
                    "tp_atr_mult": cell["tp_atr_mult"],
                    **result,
                }
            )
            print(
                f"{name:<16} sl={cell['atr_mult']:<4} tp={cell['tp_atr_mult']:<4} "
                f"rt={result['round_trips']:>4} total={result['total']:>12.2f} "
                f"funding={result['funding']:>10.2f} net={result['net']:>12.2f}"
            )

    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
