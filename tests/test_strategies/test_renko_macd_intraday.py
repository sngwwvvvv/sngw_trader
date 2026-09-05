"""Tests for RenkoMacdIntraday (handoff 20260905-bt-renko-macd-intraday).

Drives the pure `_decide` core with brick closes (no Nautilus engine), plus
day-boundary flatten via the ts->force_flat path in on_bar.
"""

from decimal import Decimal

import pytest
from nautilus_trader.model import BarType, InstrumentId

from sngw_trader.strategies.renko_macd_intraday import (
    RenkoMacdIntraday,
    RenkoMacdIntradayConfig,
)

INSTRUMENT = InstrumentId.from_str("BTC-USDT-SWAP.OKX")
BAR_TYPE = BarType.from_str("BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL")


def _config(**overrides) -> RenkoMacdIntradayConfig:
    base = dict(
        instrument_id=INSTRUMENT,
        bar_type=BAR_TYPE,
        brick_pct=0.1,
        trade_size=Decimal("0.01"),
    )
    base.update(overrides)
    return RenkoMacdIntradayConfig(**base)


# --- scenarios (brick closes) ------------------------------------------------

WARMUP = 40  # slow 26 + signal 9 -> first MACD value at brick 34; headroom


def _flat(n, start=100.0):
    return [start] * n


def _ramp(n, start=100.0, step=10.0):
    out = []
    p = start
    for _ in range(n):
        p += step
        out.append(p)
    return out


def _fall(n, start=None, step=10.0):
    out = []
    p = start if start is not None else 100.0 + 10.0 * 40
    for _ in range(n):
        p -= step
        out.append(p)
    return out


def _decide_all(s, bricks):
    intents = []
    for c in bricks:
        intents.extend(s._decide(c))
    return intents


def _scenario_long():
    """Flat base then sustained rally (each brick is one MACD input)."""
    return _flat(60) + _ramp(40)


def _scenario_down():
    """Rally top then sustained decline."""
    return _ramp(40) + _fall(40)


# --- config / wiring ---------------------------------------------------------


def test_config_defaults_match_spec():
    c = _config()
    assert (c.macd_fast, c.macd_slow, c.macd_signal) == (12, 26, 9)
    assert c.allow_short is False
    assert c.flatten_daily is True
    assert c.sizing_mode == "unit"
    assert c.instrument_id.value.endswith(".OKX")


def test_invalid_sizing_mode_rejected():
    with pytest.raises(ValueError):
        RenkoMacdIntraday(config=_config(sizing_mode="nope"))


def test_invalid_brick_pct_rejected():
    with pytest.raises(ValueError):
        RenkoMacdIntraday(config=_config(brick_pct=0.0))


def test_warmup_no_signal_before_macd_ready():
    s = RenkoMacdIntraday(config=_config())
    assert _decide_all(s, _flat(WARMUP - 10)) == []


# --- crossover on bricks ------------------------------------------------------


def test_golden_cross_buys_on_rally():
    s = RenkoMacdIntraday(config=_config())
    intents = _decide_all(s, _scenario_long())
    buys = [i for i in intents if i.side == 1]
    assert buys
    assert all(i.qty == Decimal("0.01") for i in buys)
    assert s._side == 1


def test_death_cross_long_only_exits():
    s = RenkoMacdIntraday(config=_config())
    _decide_all(s, _scenario_long())
    assert s._side == 1
    intents = _decide_all(s, _fall(40))
    sells = [i for i in intents if i.side == -1]
    assert sells
    assert s._side == 0


def test_allow_short_opens_short():
    s = RenkoMacdIntraday(config=_config(allow_short=True))
    intents = _decide_all(s, _scenario_down())
    assert s._side == -1


def test_no_signal_on_constant_bricks():
    s = RenkoMacdIntraday(config=_config())
    assert _decide_all(s, _flat(WARMUP + 40)) == []


# --- daily flatten ------------------------------------------------------------


def test_force_flat_closes_position():
    s = RenkoMacdIntraday(config=_config())
    _decide_all(s, _scenario_long())
    assert s._side == 1
    intents = s._decide(500.0, force_flat=True)
    assert intents and intents[0].side == -1
    assert s._side == 0


def test_force_flat_with_no_position_is_noop():
    s = RenkoMacdIntraday(config=_config())
    assert s._decide(100.0, force_flat=True) == []


# --- sizing -------------------------------------------------------------------


def test_vol_target_scales_qty():
    s = RenkoMacdIntraday(
        config=_config(sizing_mode="vol_target", size_target_vol=10.0, size_half_life=20)
    )
    intents = _decide_all(s, _scenario_long())
    buys = [i for i in intents if i.side == 1]
    assert buys
    assert any(i.qty > Decimal("0.01") for i in buys)


def test_vol_target_warmup_falls_back_to_unit():
    s = RenkoMacdIntraday(config=_config(sizing_mode="vol_target", size_half_life=40))
    intents = _decide_all(s, _scenario_long())
    assert intents
    for i in intents:
        assert i.qty == Decimal("0.01")


# --- on_bar day-boundary + renko feed ----------------------------------------


class _Price:
    def __init__(self, v: float):
        self._v = v

    def as_double(self) -> float:
        return self._v


class _Bar:
    def __init__(self, ts_close_ns: int, close: float):
        self.ts_init = ts_close_ns
        self.close = _Price(close)


NS = 1_000_000_000


def _bar(day: int, minute: int, close: float):
    # ts_init is bar close: day d, minute m -> (d * 86400 + (m + 1) * 60) * NS
    return _Bar((day * 86_400 + (minute + 1) * 60) * NS, close)


def test_on_bar_flattens_on_first_bar_of_new_day(monkeypatch):
    s = RenkoMacdIntraday(config=_config())
    s._side = 1
    s._signed_qty = Decimal("0.01")
    captured = []
    monkeypatch.setattr(s, "_execute", lambda intent: captured.append(intent))
    # day 100: seed renko, confirm one up brick (100 -> 110)
    s.on_bar(_bar(100, 0, 100.0))
    s.on_bar(_bar(100, 1, 110.0))
    assert captured == []  # same day: no flatten
    # next day first bar: position is open -> flatten intent on first brick
    s.on_bar(_bar(101, 0, 120.0))
    assert len(captured) == 1
    assert captured[0].side == -1
    assert captured[0].qty == Decimal("0.01")


def test_on_bar_feeds_renko_not_raw_closes():
    s = RenkoMacdIntraday(config=_config(brick_pct=0.1))
    # seed 100; move to 108: sub-brick, MACD sees nothing
    s.on_bar(_bar(100, 0, 100.0))
    s.on_bar(_bar(100, 1, 108.0))
    assert s._macd._seen == 0
    s.on_bar(_bar(100, 2, 112.0))  # crosses +B -> one brick confirmed
    assert s._macd._seen == 1


def test_no_exchange_io():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src" / "sngw_trader" / "strategies"
    text = (root / "renko_macd_intraday.py").read_text(encoding="utf-8")
    for token in ("ccxt", "requests.get", "httpx.", "websocket", "python_okx",
                  "BacktestNode", "TradingNode", "OKXDataClientFactory"):
        assert token not in text
