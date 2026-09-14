from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from nautilus_trader.model import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import Equity

from sngw_trader.config.settings import Settings
from sngw_trader.data.yahoo_etf import (
    build_equity,
    download_and_write,
    etf_bar_type,
    instrument_id_for,
    parse_etf_symbols,
    row_to_bar,
    venue_for,
)


def test_venue_for_known_and_default() -> None:
    assert venue_for("SPY") == "ARCA"
    assert venue_for("spy") == "ARCA"
    assert venue_for("IWM") == "ARCA"
    assert venue_for("QQQ") == "NASDAQ"
    assert venue_for("XLK") == "ARCA"


def test_instrument_id_for() -> None:
    assert instrument_id_for("SPY") == "SPY.ARCA"
    assert instrument_id_for("QQQ") == "QQQ.NASDAQ"
    assert instrument_id_for("IWM") == "IWM.ARCA"
    assert instrument_id_for("XLK") == "XLK.ARCA"


def test_etf_bar_type() -> None:
    assert etf_bar_type("SPY.ARCA") == "SPY.ARCA-1-DAY-LAST-EXTERNAL"


def test_parse_etf_symbols() -> None:
    assert parse_etf_symbols("SPY,QQQ,IWM") == ["SPY", "QQQ", "IWM"]
    assert parse_etf_symbols(" spy, qqq ") == ["SPY", "QQQ"]
    assert parse_etf_symbols("") == []


def test_row_to_bar_naive_utc_midnight() -> None:
    bar = row_to_bar(
        "SPY.ARCA",
        datetime(2020, 1, 2),
        "100.00",
        "101.50",
        "99.25",
        "100.75",
        "123456",
    )
    assert isinstance(bar, Bar)
    assert bar.bar_type == BarType.from_str("SPY.ARCA-1-DAY-LAST-EXTERNAL")
    assert str(bar.open) == "100.00"
    assert str(bar.high) == "101.50"
    assert str(bar.low) == "99.25"
    assert str(bar.close) == "100.75"
    assert str(bar.volume) == "123456"
    assert bar.ts_event == int(
        datetime(2020, 1, 2, tzinfo=timezone.utc).timestamp() * 1_000_000_000
    )
    assert bar.ts_init == bar.ts_event


def test_row_to_bar_aware_converts_to_utc() -> None:
    when = datetime(2020, 1, 2, 0, 0, tzinfo=timezone.utc)
    bar = row_to_bar("QQQ.NASDAQ", when, 1, 1, 1, 1, None)
    assert bar.bar_type == BarType.from_str("QQQ.NASDAQ-1-DAY-LAST-EXTERNAL")
    assert str(bar.volume) == "0"


def test_build_equity_spy() -> None:
    inst = build_equity("SPY")
    assert isinstance(inst, Equity)
    assert inst.id == InstrumentId.from_str("SPY.ARCA")
    assert inst.raw_symbol == Symbol("SPY")
    assert inst.quote_currency.code == "USD"
    assert inst.price_precision == 2
    assert inst.size_precision == 0


def _settings(**kwargs) -> Settings:
    base = dict(
        okx_env="demo", confirm_live="NO", trader_id="T", account_id="A",
        node_name="N", instrument_type="SWAP", symbol="BTC-USDT-SWAP",
        margin_mode="CROSS", region="GLOBAL",
        catalog_path=Path("catalog"), log_dir=Path("logs"),
        redis_enabled=False, redis_host="", redis_port=6379,
        bt_maker_fee=0.0002, bt_taker_fee=0.0005, bt_latency_ms=200,
        bt_prob_fill_on_limit=0.7, bt_prob_slippage=0.1,
        strategy="err_mom_a", w_f=10, w_e=10, momentum_window=200, theta=0.0,
        ema_fast=20, ema_slow=50, n_pull=24, trade_size="0.01",
        risk_stop_enabled=True, atr_period=14, atr_mult=3.0,
        sizing_mode="vol_target", size_target_vol=0.20, size_half_life=20,
        size_min_scale=0.0, size_max_scale=3.0, size_rebalance_band=0.10,
        catalog_start=None, catalog_end=None,
        catalog_source="yahoo-etf", etf_symbols="SPY,QQQ", instrument_id="",
    )
    base.update(kwargs)
    return Settings(**base)


class _FakeCatalog:
    def __init__(self) -> None:
        self.writes: list[tuple[list, object]] = []

    def write_data(self, data, data_cls=None):
        self.writes.append((list(data), data_cls))


def test_download_and_write_empty_symbols_exits() -> None:
    with pytest.raises(SystemExit):
        download_and_write(_settings(etf_symbols=" , "), _FakeCatalog())


def test_download_and_write_writes_equity_and_bars(monkeypatch) -> None:
    df = pd.DataFrame(
        {
            "Open": [100.0],
            "High": [101.0],
            "Low": [99.0],
            "Close": [100.5],
            "Volume": [1000],
        },
        index=pd.DatetimeIndex(["2020-01-02"], tz="UTC"),
    )
    monkeypatch.setattr(
        "sngw_trader.data.yahoo_etf.fetch_daily_bars",
        lambda symbol, start, end: df,
    )
    monkeypatch.setattr("sngw_trader.data.yahoo_etf.time.sleep", lambda _s: None)
    catalog = _FakeCatalog()
    download_and_write(_settings(), catalog)
    assert len(catalog.writes[0][0]) == 1
    assert isinstance(catalog.writes[0][0][0], Equity)
    bar_batches = [rows for rows, cls in catalog.writes if cls is Bar]
    assert len(bar_batches) == 2
    assert all(
        str(row.bar_type).endswith("-1-DAY-LAST-EXTERNAL")
        for rows in bar_batches
        for row in rows
    )


def test_download_and_write_empty_frame_mentions_written(monkeypatch) -> None:
    spy = pd.DataFrame(
        {"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1]},
        index=pd.DatetimeIndex(["2020-01-02"], tz="UTC"),
    )
    empty = pd.DataFrame()

    def fake_fetch(symbol, start, end):
        return spy if symbol == "SPY" else empty

    monkeypatch.setattr("sngw_trader.data.yahoo_etf.fetch_daily_bars", fake_fetch)
    monkeypatch.setattr("sngw_trader.data.yahoo_etf.time.sleep", lambda _s: None)
    with pytest.raises(SystemExit, match="QQQ") as exc:
        download_and_write(_settings(etf_symbols="SPY,QQQ"), _FakeCatalog())
    assert "SPY" in str(exc.value)
