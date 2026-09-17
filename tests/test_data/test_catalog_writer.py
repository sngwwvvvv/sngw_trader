from nautilus_trader.model import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument

from sngw_trader.data.catalog_writer import raw_candle_to_bar, run_download
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


# --- mark candles + funding history (S07) ---

from datetime import datetime, timezone
from decimal import Decimal

from nautilus_trader.model import BarType

from sngw_trader.data import catalog_writer as cw


def _settings_stub():
    return type(
        "S",
        (),
        {
            "catalog_start": datetime(2023, 11, 1, tzinfo=timezone.utc),
            "catalog_end": datetime(2023, 12, 1, tzinfo=timezone.utc),
            "instrument_id_str": "BTC-USDT-SWAP.OKX",
        },
    )()


def _instrument_stub():
    return type(
        "I",
        (),
        {"price_precision": 2, "size_precision": 3},
    )()


def test_raw_mark_candle_to_bar_maps_fields():
    bar = cw.raw_mark_candle_to_bar(
        ["1700000000000", "30000.5", "30100.0", "29950.0", "30080.2", "1"],
        "BTC-USDT-SWAP.OKX",
        price_prec=2,
        size_prec=3,
    )
    assert str(bar.bar_type) == "BTC-USDT-SWAP.OKX-1-HOUR-MARK-EXTERNAL"
    assert bar.close.as_decimal() == Decimal("30080.2")
    assert bar.volume.as_decimal() == Decimal("0")   # mark candles carry no volume


def test_download_mark_bars_paginates_and_sorts(monkeypatch):
    pages = {
        0: [["1700000000000", "1", "1", "1", "1", "1"], ["1700003600000", "2", "2", "2", "2", "1"]],
    }

    def fake_fetch(url, params):
        assert "history-mark-price-candles" in url
        after = int(params.get("after", 0))
        if after == 0:
            return pages[0]
        return []          # second page empty -> stop

    monkeypatch.setattr(cw, "_fetch_okx", fake_fetch)
    bars = cw.download_mark_bars(_settings_stub(), _instrument_stub(), "BTC-USDT-SWAP", "1H")
    assert [b.ts_init for b in bars] == sorted(b.ts_init for b in bars)
    assert len(bars) == 2


def test_download_universe_mark_bars_labels_per_symbol(monkeypatch):
    written: list[tuple] = []

    class FakeCatalog:
        def write_data(self, data, data_cls):
            written.append((data_cls, list(data)))

    def fake_instruments(settings):
        return {
            InstrumentId.from_str("ETH-USDT-SWAP.OKX"): _instrument_stub(),
            InstrumentId.from_str("BTC-USDT-SWAP.OKX"): _instrument_stub(),
        }

    def fake_fetch(url, params):
        assert "history-mark-price-candles" in url
        return [["1700000000000", "1", "1", "1", "1", "1"]]

    monkeypatch.setattr(cw, "load_all_instruments", fake_instruments)
    monkeypatch.setattr(cw, "_fetch_okx", fake_fetch)
    report = cw.download_universe_mark_bars(
        _settings_stub(), FakeCatalog(), ["ETH-USDT-SWAP", "BTC-USDT-SWAP"]
    )
    bar_ids = {
        str(b.bar_type.instrument_id)
        for data_cls, data in written if data_cls is Bar
        for b in data
    }
    assert bar_ids == {"ETH-USDT-SWAP.OKX", "BTC-USDT-SWAP.OKX"}
    assert report["ETH-USDT-SWAP"]["n"] == 1 and report["BTC-USDT-SWAP"]["n"] == 1
    instruments = [d for data_cls, d in written if data_cls is Instrument]
    assert len(instruments) == 2   # one per symbol, written before its bars


def test_write_funding_history_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cw, "fetch_funding_rates", lambda inst, s, e: {1_700_000_000_000: 0.0001, 1_700_028_800_000: -0.0002}
    )
    out = cw.write_funding_history(["BTC-USDT-SWAP"], 0, 2_000_000_000_000, tmp_path)
    assert out["BTC-USDT-SWAP"][1_700_000_000_000] == 0.0001
    loaded = (tmp_path / "BTC-USDT-SWAP.json").read_text()
    assert "1700000000000" in loaded