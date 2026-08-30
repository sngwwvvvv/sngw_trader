"""Assemble BacktestNode. No signal logic."""

from __future__ import annotations

from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.config import (
    BacktestDataConfig,
    BacktestEngineConfig,
    BacktestRunConfig,
    BacktestVenueConfig,
)
from nautilus_trader.model.enums import AccountType, BookType, OmsType

from sngw_trader.config import load_settings
from sngw_trader.runners.strategy_factory import build_ema_cross


def default_bar_type(instrument_id: str) -> str:
    return f"{instrument_id}-1-MINUTE-LAST-EXTERNAL"


def build_run_config(catalog_path: str, instrument_id: str) -> BacktestRunConfig:
    venue = BacktestVenueConfig(
        name="OKX",
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        book_type=BookType.L1_MBP,
        starting_balances=["10_000 USDT"],
    )
    data = BacktestDataConfig(
        data_type="Bar",
        catalog_path=catalog_path,
        instrument_id=instrument_id,
    )
    return BacktestRunConfig(
        venues=[venue],
        data=[data],
        engine=BacktestEngineConfig(),
    )


def attach_strategy(node: BacktestNode, run_id: object, instrument_id: str) -> None:
    strategy = build_ema_cross(
        instrument_id=instrument_id,
        bar_type=default_bar_type(instrument_id),
        trade_size="0.01",
    )
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

    run_config = build_run_config(str(settings.catalog_path), settings.instrument_id_str)
    node = BacktestNode(configs=[run_config])
    node.build()
    attach_strategy(node, run_config.id, settings.instrument_id_str)

    try:
        results = node.run()
        print(results)
    finally:
        if hasattr(node, "dispose"):
            node.dispose()


if __name__ == "__main__":
    main()
