"""Assemble the ARCA multi-ETF BacktestNode with RegimeSnapshot custom data.

No trading logic here. The regime strategy attaches later (Task 14); the
runners are the only runners.
"""

from __future__ import annotations

import os
from datetime import datetime

from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.config import (
    BacktestDataConfig,
    BacktestEngineConfig,
    BacktestRunConfig,
    BacktestVenueConfig,
    ImportableFeeModelConfig,
    LoggingConfig,
)
from nautilus_trader.core.inspect import is_nautilus_class
from nautilus_trader.model import Bar
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from sngw_trader.config.settings import Settings
from sngw_trader.data.regime_snapshot import RegimeSnapshot
from sngw_trader.data.regime_universe import sector_instrument_ids
from sngw_trader.data.yahoo_etf import etf_bar_type

_DEFAULT_FEE = 0.0005


def _fee_model_config() -> ImportableFeeModelConfig:
    taker = float(os.environ.get("BT_TAKER_FEE", _DEFAULT_FEE))
    return ImportableFeeModelConfig(
        fee_model_path="sngw_trader.runners.backtest_models:OkxRateFeeModel",
        config_path="sngw_trader.runners.backtest_models:OkxRateFeeModelConfig",
        config={"maker_fee_rate": _DEFAULT_FEE, "taker_fee_rate": taker},
    )


def build_etf_regime_run_config(
    catalog_path: str,
    settings: Settings,
    start: datetime | None = None,
    end: datetime | None = None,
    raise_exception: bool = False,
    dispose_on_completion: bool = True,
    quiet: bool = False,
) -> BacktestRunConfig:
    venue = BacktestVenueConfig(
        name="ARCA",
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        book_type="L1_MBP",
        starting_balances=["1_000_000 USD"],
        fee_model=_fee_model_config(),
    )
    data = [
        BacktestDataConfig(
            data_cls=Bar,
            catalog_path=catalog_path,
            bar_types=[etf_bar_type(iid) for iid in sector_instrument_ids()],
            start_time=start.isoformat() if start else None,
            end_time=end.isoformat() if end else None,
        ),
        BacktestDataConfig(
            data_cls=RegimeSnapshot,
            catalog_path=catalog_path,
            start_time=start.isoformat() if start else None,
            end_time=end.isoformat() if end else None,
        ),
    ]
    return BacktestRunConfig(
        venues=[venue],
        data=data,
        engine=BacktestEngineConfig(
            logging=LoggingConfig(bypass_logging=True)
        ) if quiet else BacktestEngineConfig(),
        raise_exception=raise_exception,
        dispose_on_completion=dispose_on_completion,
    )


def _bars_only_config(catalog_path: str, source: BacktestRunConfig) -> BacktestRunConfig:
    return BacktestRunConfig(
        venues=source.venues,
        data=[c for c in source.data if c.data_cls is Bar],
        engine=source.engine,
        raise_exception=source.raise_exception,
        dispose_on_completion=source.dispose_on_completion,
    )


def run_etf_regime_backtest(
    catalog_path: str,
    settings: Settings,
    strategy=None,
    start: datetime | None = None,
    end: datetime | None = None,
    raise_exception: bool = True,
    dispose_on_completion: bool = False,
    quiet: bool = True,
) -> BacktestNode:
    """Build + run the node. If the RegimeSnapshot BacktestDataConfig can be
    loaded into the engine (nautilus's own loadability check), the config path
    is used; otherwise snapshots go in via engine.add_data from the catalog.
    """
    run_config = build_etf_regime_run_config(
        catalog_path,
        settings,
        start=start,
        end=end,
        raise_exception=raise_exception,
        dispose_on_completion=dispose_on_completion,
        quiet=quiet,
    )
    # BacktestNode._load_engine_data raises ValueError for custom data classes
    # it cannot load (is_nautilus_class False, no client_id). Probe with the
    # same predicate and take the fallback path before building the engine.
    direct = is_nautilus_class(RegimeSnapshot)
    if not direct:
        run_config = _bars_only_config(catalog_path, run_config)

    node = BacktestNode(configs=[run_config])
    node.build()
    engine = node.get_engines()[0]
    if not direct:
        engine.add_data(
            ParquetDataCatalog(catalog_path).custom_data(cls=RegimeSnapshot),
            validate=False,
        )
    if strategy is not None:
        engine.trader.add_strategy(strategy)
    print(
        "RegimeSnapshot loaded via BacktestDataConfig"
        if direct
        else "RegimeSnapshot loaded via engine.add_data fallback"
    )
    node.run()
    return node
