from datetime import datetime, timezone
from decimal import Decimal

import pytest

from nautilus_trader.model import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import Equity

from sngw_trader.data.yahoo_etf import (
    build_equity,
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
