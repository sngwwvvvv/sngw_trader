from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.config import BacktestRunConfig
from nautilus_trader.model import (
    Bar,
    BarType,
    ClientId,
    Currency,
    InstrumentId,
    Price,
    Quantity,
    Symbol,
)
from nautilus_trader.model.instruments import CryptoPerpetual, Instrument
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from sngw_trader.config import load_settings
from sngw_trader.config.settings import Settings
from sngw_trader.data.open_interest import (
    OpenInterestPoint,
    register_open_interest,
    wrap_open_interest,
)
from sngw_trader.runners.backtest_oi import (
    attach_oi_data,
    build_oi_run_config,
    build_oi_strategy,
    oi_bar_type,
)
from sngw_trader.strategies.oi_crowding_mean_reversion import OiCrowdingMeanReversion
from sngw_trader.strategies.oi_liquidation_mean_reversion import OiLiquidationMeanReversion


def make_settings(*, oi_strategy: str = "oi_a") -> Settings:
    return Settings(
        okx_env="demo",
        confirm_live="NO",
        trader_id="TRADER-001",
        account_id="OKX-001",
        node_name="N",
        instrument_type="SWAP",
        symbol="BTC-USDT-SWAP",
        margin_mode="CROSS",
        region="GLOBAL",
        catalog_path=Path("catalog"),
        log_dir=Path("logs"),
        redis_enabled=False,
        redis_host="127.0.0.1",
        redis_port=6379,
        bt_maker_fee=0.0002,
        bt_taker_fee=0.0005,
        bt_latency_ms=200,
        bt_prob_fill_on_limit=0.7,
        bt_prob_slippage=0.1,
        strategy="err_mom_a",
        w_f=10,
        w_e=10,
        momentum_window=200,
        theta=0.0,
        ema_fast=20,
        ema_slow=50,
        n_pull=24,
        trade_size="0.01",
        risk_stop_enabled=True,
        atr_period=14,
        atr_mult=3.0,
        sizing_mode="vol_target",
        size_target_vol=0.20,
        size_half_life=20,
        size_min_scale=0.0,
        size_max_scale=3.0,
        size_rebalance_band=0.10,
        catalog_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        catalog_end=datetime(2026, 3, 1, tzinfo=timezone.utc),
        oi_strategy=oi_strategy,
    )


def test_oi_bar_type_is_composite_5m():
    assert str(oi_bar_type("BTC-USDT-SWAP.OKX")) == (
        "BTC-USDT-SWAP.OKX-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL"
    )


def test_build_oi_strategy_selects_a():
    strategy = build_oi_strategy(make_settings(oi_strategy="oi_a"))
    assert isinstance(strategy, OiCrowdingMeanReversion)
    assert strategy.config.oi_client_id == "BACKTEST"
    assert strategy.config.use_hyphens_in_client_order_ids is False


def test_build_oi_strategy_selects_b():
    strategy = build_oi_strategy(make_settings(oi_strategy="oi_b"))
    assert isinstance(strategy, OiLiquidationMeanReversion)
    assert strategy.config.oi_client_id == "BACKTEST"
    assert strategy.config.use_hyphens_in_client_order_ids is False


@pytest.mark.parametrize("instrument_id", ["ETH-USDT-SWAP.OKX", "SPY.ARCA"])
def test_oi_strategy_rejects_non_btc_instrument(instrument_id):
    with pytest.raises(SystemExit, match="BTC-USDT-SWAP.OKX"):
        build_oi_strategy(
            replace(make_settings(), instrument_id=instrument_id, symbol=instrument_id)
        )


def test_oi_run_config_rejects_non_btc_instrument():
    with pytest.raises(SystemExit, match="BTC-USDT-SWAP.OKX"):
        build_oi_run_config(
            replace(
                make_settings(),
                instrument_id="ETH-USDT-SWAP.OKX",
                symbol="ETH-USDT-SWAP",
            )
        )


def test_build_oi_strategy_rejects_unknown_name():
    with pytest.raises(SystemExit, match="OI_STRATEGY"):
        build_oi_strategy(make_settings(oi_strategy="bad"))


def test_load_settings_parses_oi_strategy(monkeypatch):
    monkeypatch.setattr("sngw_trader.config.settings.load_dotenv", lambda path=".env": None)
    monkeypatch.setenv("OI_STRATEGY", "oi_b")

    assert load_settings().oi_strategy == "oi_b"


def test_build_oi_run_config_does_not_dispose_on_completion():
    config = build_oi_run_config(make_settings())

    assert config.dispose_on_completion is False


def test_build_oi_run_config_uses_price_1m_external_bar():
    settings = make_settings()
    config = build_oi_run_config(settings)

    assert config.data[0].bar_types == [
        "BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL"
    ]
    assert config.venues[0].starting_balances == ["10_000 USDT"]
    assert config.venues[0].fee_model.config["taker_fee_rate"] == 0.0005


class _FakeEngine:
    def __init__(self):
        self.added = []

    def add_data(self, data, *, client_id=None, sort):
        self.added.append((data, client_id, sort))


class _FakeCatalog:
    path = "catalog"

    def __init__(self, data):
        self.data = data
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        return self.data


def test_attach_oi_data_queries_catalog_and_adds_sorted_custom_data():
    register_open_interest()
    point = OpenInterestPoint(
        ts_event=200,
        ts_init=200,
        instrument_id=InstrumentId.from_str("BTC-USDT-SWAP.OKX"),
        open_interest=Decimal("101.5"),
    )
    catalog = _FakeCatalog([wrap_open_interest(point)])
    engine = _FakeEngine()

    attach_oi_data(engine, catalog, "BTC-USDT-SWAP.OKX", 100, 300)

    assert catalog.calls == [
        {
            "data_cls": OpenInterestPoint,
            "identifiers": ["BTC-USDT-SWAP.OKX"],
            "start": 100,
            "end": 300,
        }
    ]
    assert engine.added == [([catalog.data[0]], ClientId("BACKTEST"), True)]


def test_attach_oi_data_rejects_empty_catalog_range():
    with pytest.raises(RuntimeError, match="catalog.*BTC-USDT-SWAP.OKX.*100.*300"):
        attach_oi_data(
            _FakeEngine(),
            _FakeCatalog([]),
            "BTC-USDT-SWAP.OKX",
            100,
            300,
        )


def test_attach_oi_data_rejects_non_btc_instrument_before_query():
    catalog = _FakeCatalog([])
    with pytest.raises(ValueError, match="BTC-USDT-SWAP.OKX"):
        attach_oi_data(_FakeEngine(), catalog, "ETH-USDT-SWAP.OKX", 100, 300)
    assert catalog.calls == []


@pytest.mark.parametrize(
    ("oi_strategy", "oi_values"),
    [
        ("oi_a", [100.0, 99.0, 98.0, 104.0]),
        ("oi_b", [100.0, 101.0, 102.0, 98.0]),
    ],
)
def test_synthetic_oi_backtest_replays_lagged_oi_without_lookahead(
    tmp_path,
    oi_strategy,
    oi_values,
):
    result = run_synthetic_oi_backtest(
        tmp_path,
        oi_strategy=oi_strategy,
        oi_values=oi_values,
    )

    assert result.n_closed_positions == 1
    assert result.n_oi_points == 4
    assert result.n_price_bars == 35
    assert result.breach_oi_ts == result.breach_bar_ts
    assert result.entry_bar_ts > result.first_reentry_bar_ts
    assert result.entry_bar_ts > result.second_breach_bar_ts
    assert result.entry_bar_ts >= result.second_reentry_bar_ts


@dataclass(frozen=True)
class _SyntheticOiResult:
    n_closed_positions: int
    breach_bar_ts: int
    breach_oi_ts: int
    first_reentry_bar_ts: int
    second_breach_bar_ts: int
    second_reentry_bar_ts: int
    entry_bar_ts: int
    n_oi_points: int
    n_price_bars: int


def run_synthetic_oi_backtest(
    catalog_path: Path,
    *,
    oi_strategy: str,
    oi_values: list[float],
) -> _SyntheticOiResult:
    instrument_id = "BTC-USDT-SWAP.OKX"
    instrument = CryptoPerpetual(
        instrument_id=InstrumentId.from_str(instrument_id),
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
    start = datetime(2026, 9, 17, 14, 30, tzinfo=timezone.utc)
    bars = _synthetic_bars(instrument_id, start)
    oi_points = _synthetic_oi_points(instrument_id, start, oi_values)

    catalog = ParquetDataCatalog(str(catalog_path))
    catalog.write_data([instrument], data_cls=Instrument)
    catalog.write_data(bars, data_cls=Bar)
    register_open_interest()
    catalog.write_data(
        [wrap_open_interest(point) for point in oi_points],
        data_cls=OpenInterestPoint,
    )

    settings = replace(
        make_settings(oi_strategy=oi_strategy),
        catalog_path=catalog_path,
        catalog_start=start,
        catalog_end=datetime.fromtimestamp(
            bars[-1].ts_event / 1_000_000_000,
            tz=timezone.utc,
        ),
        bt_latency_ms=0,
        bt_prob_fill_on_limit=1.0,
        bt_prob_slippage=0.0,
    )
    run_config = build_oi_run_config(
        settings,
        start=start,
        end=settings.catalog_end,
    )
    run_config = BacktestRunConfig(
        venues=run_config.venues,
        data=run_config.data,
        engine=run_config.engine,
        dispose_on_completion=False,
        raise_exception=run_config.raise_exception,
    )
    node = BacktestNode(configs=[run_config])
    runtime_bar_type = BarType.from_str(
        f"{instrument_id}-5-MINUTE-LAST-INTERNAL"
    )

    try:
        node.build()
        engine = node.get_engine(run_config.id)
        if engine is None:
            raise RuntimeError("synthetic OI BacktestNode did not build an engine")

        strategy = build_oi_strategy(settings)
        engine.add_strategy(strategy)

        attach_oi_data(
            engine,
            catalog,
            instrument_id,
            int(start.timestamp() * 1_000_000_000),
            int(settings.catalog_end.timestamp() * 1_000_000_000),
        )
        node.run()
        closed = [position for position in engine.cache.positions() if position.is_closed]
        runtime_bars = sorted(
            engine.cache.bars(runtime_bar_type),
            key=lambda bar: int(bar.ts_event),
        )
        breach_index = next(
            index
            for index, bar in enumerate(runtime_bars)
            if bar.close.as_double() == 95.0
        )
        second_breach_index = next(
            index
            for index, bar in enumerate(
                runtime_bars[breach_index + 1 :], breach_index + 1
            )
            if bar.close.as_double() == 94.0
        )
        entry_bar_ts = min(position.ts_opened for position in closed)
        return _SyntheticOiResult(
            n_closed_positions=len(closed),
            breach_bar_ts=int(runtime_bars[breach_index].ts_event),
            breach_oi_ts=oi_points[-1].ts_event,
            first_reentry_bar_ts=int(runtime_bars[breach_index + 1].ts_event),
            second_breach_bar_ts=int(runtime_bars[second_breach_index].ts_event),
            second_reentry_bar_ts=int(runtime_bars[second_breach_index + 1].ts_event),
            entry_bar_ts=int(entry_bar_ts),
            n_oi_points=len(strategy._oi),
            n_price_bars=len(runtime_bars),
        )
    finally:
        node.dispose()


def _synthetic_bars(instrument_id: str, start: datetime) -> list[Bar]:
    bar_type = BarType.from_str(f"{instrument_id}-1-MINUTE-LAST-EXTERNAL")
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
                    volume=10**9,
                    size_prec=3,
                    ts_event=ts,
                    ts_init=ts,
                )
            )
    return bars


def _synthetic_oi_points(
    instrument_id: str,
    start: datetime,
    oi_values: list[float],
) -> list[OpenInterestPoint]:
    points = []
    for block, value in enumerate(oi_values, start=23):
        ts = int(
            (start + timedelta(minutes=block * 5)).timestamp()
            * 1_000_000_000
        )
        points.append(
            OpenInterestPoint(
                ts_event=ts,
                ts_init=ts,
                instrument_id=InstrumentId.from_str(instrument_id),
                open_interest=value,
            )
        )
    return points
