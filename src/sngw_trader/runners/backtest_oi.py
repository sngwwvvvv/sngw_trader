"""Assemble the OI mean-reversion BacktestNode. No signal logic."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.config import BacktestDataConfig, BacktestRunConfig
from nautilus_trader.model import Bar, BarType, ClientId, InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.trading import Strategy

from sngw_trader.config import load_settings
from sngw_trader.config.settings import Settings
from sngw_trader.data.open_interest import (
    OI_INSTRUMENT_ID,
    query_open_interest,
    register_open_interest,
)
from sngw_trader.runners.backtest_okx import (
    _export_fills,
    build_run_config,
)
from sngw_trader.strategies.oi_crowding_mean_reversion import (
    OiCrowdingMeanReversion,
    OiCrowdingMeanReversionConfig,
)
from sngw_trader.strategies.oi_liquidation_mean_reversion import (
    OiLiquidationMeanReversion,
    OiLiquidationMeanReversionConfig,
)


def oi_bar_type(instrument_id: str) -> BarType:
    if instrument_id != OI_INSTRUMENT_ID:
        raise ValueError(
            f"OI backtest supports only {OI_INSTRUMENT_ID}, got {instrument_id}"
        )
    return BarType.from_str(
        f"{instrument_id}-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL"
    )


def build_oi_strategy(settings: Settings) -> Strategy:
    instrument_id = settings.instrument_id_str
    if instrument_id != OI_INSTRUMENT_ID:
        raise SystemExit(
            f"OI backtest supports only {OI_INSTRUMENT_ID}, got {instrument_id}"
        )
    common = {
        "instrument_id": InstrumentId.from_str(instrument_id),
        "bar_type": oi_bar_type(instrument_id),
        "trade_size": Decimal(settings.trade_size),
        "oi_client_id": "BACKTEST",
    }
    if settings.oi_strategy == "oi_a":
        return OiCrowdingMeanReversion(OiCrowdingMeanReversionConfig(**common))
    if settings.oi_strategy == "oi_b":
        return OiLiquidationMeanReversion(OiLiquidationMeanReversionConfig(**common))
    raise SystemExit(
        f"Unknown OI_STRATEGY '{settings.oi_strategy}'. Use oi_a or oi_b."
    )


def build_oi_run_config(
    settings: Settings,
    start: datetime | None = None,
    end: datetime | None = None,
    dispose_on_completion: bool = False,
) -> BacktestRunConfig:
    if settings.instrument_id_str != OI_INSTRUMENT_ID:
        raise SystemExit(
            f"OI backtest supports only {OI_INSTRUMENT_ID}, got {settings.instrument_id_str}"
        )
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
        dispose_on_completion=dispose_on_completion,
        raise_exception=base.raise_exception,
    )


def attach_oi_data(
    engine: BacktestEngine,
    catalog: ParquetDataCatalog,
    instrument_id: str,
    start_ns: int | None,
    end_ns: int | None,
) -> None:
    if instrument_id != OI_INSTRUMENT_ID:
        raise ValueError(
            f"OI backtest supports only {OI_INSTRUMENT_ID}, got {instrument_id}"
        )
    register_open_interest()
    oi_data = query_open_interest(catalog, instrument_id, start=start_ns, end=end_ns)
    if not oi_data:
        raise RuntimeError(
            f"No open-interest rows in catalog {catalog.path!s} for {instrument_id} "
            f"in requested range {start_ns} to {end_ns}"
        )
    engine.add_data(oi_data, client_id=ClientId("BACKTEST"), sort=True)


def _to_ns(value: datetime | None) -> int | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1_000_000_000)


def main() -> None:
    settings = load_settings()
    settings.catalog_path.mkdir(parents=True, exist_ok=True)
    catalog = ParquetDataCatalog(str(settings.catalog_path))
    run_config = build_oi_run_config(
        settings,
        start=settings.catalog_start,
        end=settings.catalog_end,
    )
    node = BacktestNode(configs=[run_config])

    try:
        node.build()
        engine = node.get_engine(run_config.id)
        if engine is None:
            raise RuntimeError(f"Backtest engine was not built for run {run_config.id}")
        attach_oi_data(
            engine,
            catalog,
            settings.instrument_id_str,
            _to_ns(settings.catalog_start),
            _to_ns(settings.catalog_end),
        )
        engine.add_strategy(build_oi_strategy(settings))
        results = node.run()
        print(results)
        _export_fills(node, settings.log_dir / "oi_fills.csv")
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
