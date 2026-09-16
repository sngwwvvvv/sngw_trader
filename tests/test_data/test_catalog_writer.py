from types import SimpleNamespace

import pytest
from nautilus_trader.model import Bar, BarType, InstrumentId

from sngw_trader.data.catalog_writer import raw_candle_to_bar, run_download
from sngw_trader.data.open_interest import OpenInterestPoint
from sngw_trader.runners.backtest_okx import default_bar_type


def test_raw_candle_to_bar_maps_fields() -> None:
    raw = ["1704067200000", "43000", "43100", "42900", "43050", "12.5", "537500", "537500", "1"]
    bar = raw_candle_to_bar(
        raw,
        instrument_id="BTC-USDT-SWAP.OKX",
        price_prec=1,
        size_prec=0,
    )
    assert isinstance(bar, Bar)
    assert bar.bar_type == BarType.from_str("BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL")
    assert str(bar.open) == "43000.0"
    assert str(bar.high) == "43100.0"
    assert str(bar.low) == "42900.0"
    assert str(bar.close) == "43050.0"
    assert str(bar.volume) == "12"


def test_bar_type_matches_backtest_runner() -> None:
    assert raw_candle_to_bar(["1", "2", "3", "2", "2", "1", "", "", ""],
                             "BTC-USDT-SWAP.OKX", 1, 0).bar_type \
        == BarType.from_str(default_bar_type("BTC-USDT-SWAP.OKX"))


def test_run_download_dispatches_yahoo_etf(monkeypatch) -> None:
    called = {}

    def fake_download(settings, catalog):
        called["yes"] = True

    monkeypatch.setattr(
        "sngw_trader.data.yahoo_etf.download_and_write", fake_download
    )
    settings = type("S", (), {"catalog_source": "yahoo-etf"})()
    run_download(settings, catalog=object())
    assert called["yes"] is True


def test_run_download_writes_optional_open_interest(monkeypatch) -> None:
    settings = SimpleNamespace(
        catalog_source="okx",
        oi_enabled=True,
        oi_period="5m",
        instrument_id_str="BTC-USDT-SWAP.OKX",
        catalog_path="catalog",
        catalog_start=None,
        catalog_end=None,
    )
    bars = [SimpleNamespace(ts_event=100, ts_init=100)]
    points = [
        OpenInterestPoint(
            100,
            100,
            InstrumentId.from_str("BTC-USDT-SWAP.OKX"),
            12.5,
        )
    ]
    writes = []

    class Catalog:
        def write_data(self, data, *, data_cls):
            writes.append((data, data_cls))

    monkeypatch.setattr(
        "sngw_trader.data.catalog_writer.load_instrument",
        lambda settings: object(),
    )
    monkeypatch.setattr(
        "sngw_trader.data.catalog_writer.download_bars",
        lambda settings, instrument: bars,
    )
    monkeypatch.setattr(
        "sngw_trader.data.catalog_writer.download_open_interest",
        lambda settings, instrument_id: points,
    )

    run_download(settings, Catalog())

    assert writes[1][1] is Bar
    assert writes[2][1] is OpenInterestPoint
    assert writes[2][0][0].data == points[0]


def test_run_download_rejects_non_btc_open_interest_before_request(monkeypatch) -> None:
    settings = SimpleNamespace(
        catalog_source="okx",
        oi_enabled=True,
        instrument_id_str="ETH-USDT-SWAP.OKX",
    )
    monkeypatch.setattr(
        "sngw_trader.data.catalog_writer.download_open_interest",
        lambda *_args: pytest.fail("OI request must not be made"),
    )

    with pytest.raises(SystemExit, match="BTC-USDT-SWAP.OKX"):
        run_download(settings, object())
