"""Assemble BacktestNode. No signal logic."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.config import (
    BacktestDataConfig,
    BacktestEngineConfig,
    BacktestRunConfig,
    BacktestVenueConfig,
    FillModelConfig,
    ImportableFeeModelConfig,
    ImportableFillModelConfig,
    ImportableLatencyModelConfig,
    LatencyModelConfig,
    LoggingConfig,
)
from nautilus_trader.model import Bar
from nautilus_trader.model.enums import AccountType, OmsType

from sngw_trader.config import load_settings
from sngw_trader.config.settings import Settings
from sngw_trader.runners.strategy_factory import build_strategy


def venue_name(instrument_id: str) -> str:
    if "." not in instrument_id:
        raise SystemExit(
            f"instrument id must include venue after '.', got {instrument_id!r}"
        )
    return instrument_id.rsplit(".", 1)[-1]


def default_bar_type(instrument_id: str) -> str:
    if venue_name(instrument_id) == "OKX":
        return f"{instrument_id}-1-MINUTE-LAST-EXTERNAL"
    return f"{instrument_id}-1-DAY-LAST-EXTERNAL"


def build_fill_model_config(settings: Settings) -> ImportableFillModelConfig:
    return ImportableFillModelConfig(
        fill_model_path="nautilus_trader.backtest.models:ProbabilisticFillModel",
        config_path="nautilus_trader.backtest.config:FillModelConfig",
        config=FillModelConfig(
            prob_fill_on_limit=settings.bt_prob_fill_on_limit,
            prob_slippage=settings.bt_prob_slippage,
            random_seed=42,
        ),
    )


def _fee_rates(instrument_id: str, settings: Settings) -> tuple[float, float]:
    if venue_name(instrument_id) == "OKX":
        return settings.bt_maker_fee, settings.bt_taker_fee
    maker = (
        float(os.environ["BT_MAKER_FEE"])
        if "BT_MAKER_FEE" in os.environ
        else 0.0001
    )
    taker = (
        float(os.environ["BT_TAKER_FEE"])
        if "BT_TAKER_FEE" in os.environ
        else 0.0001
    )
    return maker, taker


def build_fee_model_config(
    settings: Settings, instrument_id: str
) -> ImportableFeeModelConfig:
    maker, taker = _fee_rates(instrument_id, settings)
    return ImportableFeeModelConfig(
        fee_model_path="sngw_trader.runners.backtest_models:OkxRateFeeModel",
        config_path="sngw_trader.runners.backtest_models:OkxRateFeeModelConfig",
        config={
            "maker_fee_rate": maker,
            "taker_fee_rate": taker,
        },
    )


def build_latency_model_config(settings: Settings) -> ImportableLatencyModelConfig:
    nanos = settings.bt_latency_ms * 1_000_000
    return ImportableLatencyModelConfig(
        latency_model_path="nautilus_trader.backtest.models:LatencyModel",
        config_path="nautilus_trader.backtest.config:LatencyModelConfig",
        config=LatencyModelConfig(
            base_latency_nanos=nanos,
            insert_latency_nanos=0,
            update_latency_nanos=0,
            cancel_latency_nanos=0,
        ),
    )


def build_run_config(
    catalog_path: str,
    instrument_id: str,
    settings: Settings,
    start: datetime | None = None,
    end: datetime | None = None,
    dispose_on_completion: bool = True,
    raise_exception: bool = False,
    quiet: bool = False,
) -> BacktestRunConfig:
    venue_id = venue_name(instrument_id)
    quote = "USDT" if venue_id == "OKX" else "USD"
    venue = BacktestVenueConfig(
        name=venue_id,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        book_type="L1_MBP",
        starting_balances=[f"10_000 {quote}"],
        fill_model=build_fill_model_config(settings),
        fee_model=build_fee_model_config(settings, instrument_id),
        latency_model=build_latency_model_config(settings),
    )
    data = BacktestDataConfig(
        data_cls=Bar,
        catalog_path=catalog_path,
        instrument_id=instrument_id,
        start_time=start.isoformat() if start else None,
        end_time=end.isoformat() if end else None,
    )
    return BacktestRunConfig(
        venues=[venue],
        data=[data],
        # quiet: research grid runs log millions of events to stdout at INFO,
        # which dominates wall time. Live-runner default stays INFO.
        engine=BacktestEngineConfig(
            logging=LoggingConfig(bypass_logging=True)
        ) if quiet else BacktestEngineConfig(),
        dispose_on_completion=dispose_on_completion,
        raise_exception=raise_exception,
    )


def build_multi_instrument_run_config(
    catalog_path: str,
    instrument_ids,
    *,
    start: datetime,
    end: datetime,
    taker_fee: float,
    maker_fee: float,
    prob_slippage: float,
    prob_fill_on_limit: float,
    latency_ms: int = 10,
    starting_balance: str = "10_000 USDT",
    quiet: bool = True,
) -> BacktestRunConfig:
    """OKX venue + one bar-data config per instrument. Assembly only."""
    venue = BacktestVenueConfig(
        name="OKX",
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        book_type="L1_MBP",
        starting_balances=[starting_balance],
        fill_model=ImportableFillModelConfig(
            fill_model_path="nautilus_trader.backtest.models:ProbabilisticFillModel",
            config_path="nautilus_trader.backtest.config:FillModelConfig",
            config=FillModelConfig(
                prob_fill_on_limit=prob_fill_on_limit,
                prob_slippage=prob_slippage,
                random_seed=42,
            ),
        ),
        fee_model=ImportableFeeModelConfig(
            fee_model_path="sngw_trader.runners.backtest_models:OkxRateFeeModel",
            config_path="sngw_trader.runners.backtest_models:OkxRateFeeModelConfig",
            config={"maker_fee_rate": maker_fee, "taker_fee_rate": taker_fee},
        ),
        latency_model=ImportableLatencyModelConfig(
            latency_model_path="nautilus_trader.backtest.models:LatencyModel",
            config_path="nautilus_trader.backtest.config:LatencyModelConfig",
            config=LatencyModelConfig(base_latency_nanos=latency_ms * 1_000_000),
        ),
    )
    data = [
        BacktestDataConfig(
            data_cls=Bar,
            catalog_path=catalog_path,
            instrument_id=iid,
            start_time=start.isoformat(),
            end_time=end.isoformat(),
        )
        for iid in instrument_ids
    ]
    return BacktestRunConfig(
        venues=[venue],
        data=data,
        engine=BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True)) if quiet else BacktestEngineConfig(),
        dispose_on_completion=True,
        raise_exception=True,
    )


def attach_strategy(node: BacktestNode, run_id: object, settings: Settings) -> None:
    strategy = build_strategy(settings)
    if hasattr(node, "add_strategy"):
        try:
            node.add_strategy(strategy)
            return
        except TypeError:
            pass
    if hasattr(node, "add_strategy"):
        node.add_strategy(run_id, strategy)
        return
    raise RuntimeError("BacktestNode has no compatible add_strategy method")


def _export_fills(node: BacktestNode, path: Path) -> None:
    trader = None
    for engine in node.get_engines():
        if engine is not None and hasattr(engine, "trader"):
            trader = engine.trader
            break
    if trader is None and hasattr(node, "trader"):
        trader = node.trader
    if trader is None:
        raise RuntimeError(
            "Cannot locate trader for the fills report. Inspect: "
            'uv run python -c "from nautilus_trader.backtest.node import BacktestNode; print(dir(BacktestNode))"'
        )
    df = trader.generate_order_fills_report()
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def main() -> None:
    settings = load_settings()
    settings.catalog_path.mkdir(parents=True, exist_ok=True)

    run_config = build_run_config(
        str(settings.catalog_path),
        settings.instrument_id_str,
        settings=settings,
    )
    node = BacktestNode(configs=[run_config])
    node.build()
    attach_strategy(node, run_config.id, settings)

    try:
        results = node.run()
        print(results)
        _export_fills(node, Path(settings.log_dir) / "fills.csv")
    finally:
        if hasattr(node, "dispose"):
            node.dispose()


if __name__ == "__main__":
    main()