"""Assemble BacktestNode. No signal logic."""

from __future__ import annotations

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
)
from nautilus_trader.model import Bar
from nautilus_trader.model.enums import AccountType, BookType, OmsType

from sngw_trader.config import load_settings
from sngw_trader.config.settings import Settings
from sngw_trader.runners.strategy_factory import build_strategy


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


def build_fee_model_config(settings: Settings) -> ImportableFeeModelConfig:
    return ImportableFeeModelConfig(
        fee_model_path="sngw_trader.runners.backtest_models:OkxRateFeeModel",
        config_path="sngw_trader.runners.backtest_models:OkxRateFeeModelConfig",
        config={
            "maker_fee_rate": settings.bt_maker_fee,
            "taker_fee_rate": settings.bt_taker_fee,
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


def build_run_config(catalog_path: str, instrument_id: str, settings: Settings) -> BacktestRunConfig:
    venue = BacktestVenueConfig(
        name="OKX",
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        book_type=BookType.L1_MBP,
        starting_balances=["10_000 USDT"],
        fill_model=build_fill_model_config(settings),
        fee_model=build_fee_model_config(settings),
        latency_model=build_latency_model_config(settings),
    )
    data = BacktestDataConfig(
        data_cls=Bar,
        catalog_path=catalog_path,
        instrument_id=instrument_id,
    )
    return BacktestRunConfig(
        venues=[venue],
        data=[data],
        engine=BacktestEngineConfig(),
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
    finally:
        if hasattr(node, "dispose"):
            node.dispose()


if __name__ == "__main__":
    main()
