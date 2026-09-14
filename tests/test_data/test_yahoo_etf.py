import pytest

from sngw_trader.data.yahoo_etf import (
    etf_bar_type,
    instrument_id_for,
    parse_etf_symbols,
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
