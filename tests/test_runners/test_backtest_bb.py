from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.model import (
    Bar,
    BarType,
    Currency,
    InstrumentId,
    Price,
    Quantity,
    Symbol,
)
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from sngw_trader.config import load_settings
from sngw_trader.research.bb_mean_reversion_compare import (
    bb_bar_type,
    build_bb_run_config,
)
from sngw_trader.strategies.bb_mean_reversion import (
    BbMeanReversion,
    BbMeanReversionConfig,
)
from sngw_trader.strategies.volume_climax_mean_reversion import (
    VolumeClimaxMeanReversion,
    VolumeClimaxMeanReversionConfig,
)


INSTRUMENT_ID = "BTC-USDT-SWAP.OKX"


def _settings(catalog_path: Path, bars: list[Bar]):
    start = datetime.fromtimestamp(bars[0].ts_event / 1_000_000_000, tz=timezone.utc)
    end = datetime.fromtimestamp(bars[-1].ts_event / 1_000_000_000, tz=timezone.utc)
    return replace(
        load_settings(),
        instrument_id=INSTRUMENT_ID,
        symbol="BTC-USDT-SWAP",
        catalog_path=catalog_path,
        catalog_start=start,
        catalog_end=end,
        bt_latency_ms=0,
        bt_prob_fill_on_limit=1.0,
        bt_prob_slippage=0.0,
    )


def _instrument() -> CryptoPerpetual:
    return CryptoPerpetual(
        instrument_id=InstrumentId.from_str(INSTRUMENT_ID),
        raw_symbol=Symbol("BTC-USDT-SWAP"),
        base_currency=Currency.from_str("BTC"),
        quote_currency=Currency.from_str("USDT"),
        settlement_currency=Currency.from_str("USDT"),
        is_inverse=False,
        price_precision=1,
        size_precision=3,
        price_increment=Price.from_str("0.1"),
        size_increment=Quantity.from_str("0.001"),
        ts_event=0,
        ts_init=0,
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0005"),
    )


def _synthetic_bars(start: datetime, *, spike: bool) -> list[Bar]:
    bar_type = BarType.from_str(f"{INSTRUMENT_ID}-1-MINUTE-LAST-EXTERNAL")
    bars = []
    for block in range(35):
        block_start = start + timedelta(minutes=block * 5)
        if block == 26:
            values = (100.0, 100.0, 94.0, 95.0)
        elif block == 27:
            values = (95.0, 100.5, 94.8, 100.0)
        elif block == 28:
            values = (100.0, 100.0, 93.5, 94.0)
        elif block == 29:
            values = (94.0, 100.5, 93.8, 100.0)
        elif block == 30:
            values = (100.0, 102.0, 99.8, 102.0)
        else:
            close = 100.1 if block % 2 else 99.9
            values = (close, close + 0.2, close - 0.2, close)
        # ponytail: 10x so the spike survives 5-min bucket-boundary dilution
        # (a partial bucket with one spike minute still clears the 2.0 ratio).
        volume = 10 * 10**9 if spike and block == 26 else 10**9
        for minute in range(5):
            ts = int((block_start + timedelta(minutes=minute)).timestamp() * 1_000_000_000)
            bars.append(
                Bar.from_raw(
                    bar_type=bar_type,
                    open=round(values[0] * 10**9),
                    high=round(values[1] * 10**9),
                    low=round(values[2] * 10**9),
                    close=round(values[3] * 10**9),
                    price_prec=1,
                    volume=volume,
                    size_prec=3,
                    ts_event=ts,
                    ts_init=ts,
                )
            )
    return bars


def _run(tmp_path: Path, strategy, spike: bool) -> tuple[object, list[Bar]]:
    start = datetime(2026, 9, 17, 14, 30, tzinfo=timezone.utc)
    bars = _synthetic_bars(start, spike=spike)
    settings = _settings(tmp_path, bars)

    catalog = ParquetDataCatalog(str(tmp_path))
    catalog.write_data([_instrument()], data_cls=CryptoPerpetual)
    catalog.write_data(bars, data_cls=Bar)

    run_config = build_bb_run_config(settings, start=start, end=settings.catalog_end)
    node = BacktestNode(configs=[run_config])
    runtime_bar_type = BarType.from_str(f"{INSTRUMENT_ID}-5-MINUTE-LAST-INTERNAL")
    try:
        node.build()
        engine = node.get_engine(run_config.id)
        if engine is None:
            raise RuntimeError("BB BacktestNode did not build an engine")
        engine.add_strategy(strategy)
        node.run()
        closed = [p for p in engine.cache.positions() if p.is_closed]
        runtime_bars = sorted(
            engine.cache.bars(runtime_bar_type),
            key=lambda bar: int(bar.ts_event),
        )
        return closed, runtime_bars
    finally:
        node.dispose()


def _bb_strategy() -> BbMeanReversion:
    return BbMeanReversion(
        BbMeanReversionConfig(
            instrument_id=InstrumentId.from_str(INSTRUMENT_ID),
            bar_type=bb_bar_type(INSTRUMENT_ID),
            trade_size=Decimal("0.01"),
        )
    )


def _climax_strategy() -> VolumeClimaxMeanReversion:
    return VolumeClimaxMeanReversion(
        VolumeClimaxMeanReversionConfig(
            instrument_id=InstrumentId.from_str(INSTRUMENT_ID),
            bar_type=bb_bar_type(INSTRUMENT_ID),
            trade_size=Decimal("0.01"),
        )
    )


def test_bb_synthetic_backtest_aggregates_5m_and_closes(tmp_path):
    closed, runtime_bars = _run(tmp_path, _bb_strategy(), spike=False)

    assert len(runtime_bars) == 35
    assert len(closed) >= 1


def test_volume_climax_synthetic_backtest_requires_spike(tmp_path):
    closed_spike, _ = _run(tmp_path / "spike", _climax_strategy(), spike=True)
    closed_flat, _ = _run(tmp_path / "flat", _climax_strategy(), spike=False)

    assert len(closed_spike) >= 1
    assert len(closed_flat) == 0