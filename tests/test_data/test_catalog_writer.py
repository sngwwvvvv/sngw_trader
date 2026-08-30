from nautilus_trader.model import Bar, BarType

from sngw_trader.data.catalog_writer import raw_candle_to_bar
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