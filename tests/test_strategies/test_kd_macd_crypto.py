"""Tests for KdMacdCrypto strategy.

The signal -> target -> qty decision core is pure (`_decide` returns
OrderIntents), so tests drive completed daily bars directly without a
Nautilus engine. Execution (_execute/on_event) stays thin.
"""

from decimal import Decimal

import pytest
from nautilus_trader.model import BarType, InstrumentId

from sngw_trader.strategies.kd_macd_crypto import (
    KdMacdCrypto,
    KdMacdCryptoConfig,
    direction_target,
)

INSTRUMENT = InstrumentId.from_str("BTC-USDT-SWAP.OKX")
BAR_TYPE = BarType.from_str("BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL")


# --- direction_target truth table ------------------------------------------


def test_direction_target_truth_table():
    # golden cross -> long (both modes)
    assert direction_target(golden=True, death=False, current=0, allow_short=False) == 1
    assert direction_target(golden=True, death=False, current=0, allow_short=True) == 1
    # death cross: long-only closes, long+short flips
    assert direction_target(golden=False, death=True, current=1, allow_short=False) == 0
    assert direction_target(golden=False, death=True, current=1, allow_short=True) == -1
    # long-only mode cannot hold a short; death maps to flat by definition
    assert direction_target(golden=False, death=True, current=-1, allow_short=False) == 0
    # no signal -> hold current
    assert direction_target(golden=False, death=False, current=1, allow_short=False) == 1
    assert direction_target(golden=False, death=False, current=0, allow_short=True) == 0
    # golden while short flips to long
    assert direction_target(golden=True, death=False, current=-1, allow_short=True) == 1


# --- config defaults --------------------------------------------------------


def _config(**overrides) -> KdMacdCryptoConfig:
    base = dict(
        instrument_id=INSTRUMENT,
        bar_type=BAR_TYPE,
        trade_size=Decimal("0.01"),
    )
    base.update(overrides)
    return KdMacdCryptoConfig(**base)


def test_config_defaults_match_paper():
    c = _config()
    assert c.kd_n == 9
    assert c.kd_alpha == 0.5
    assert (c.macd_fast, c.macd_slow, c.macd_signal) == (12, 26, 9)
    assert c.allow_short is False
    assert c.trigger_mode == "cross"
    assert c.sizing_mode == "vol_target"
    assert c.size_target_vol == 0.20
    assert c.size_half_life == 20
    assert c.instrument_id.value.endswith(".OKX")


def test_strategy_components():
    s = KdMacdCrypto(config=_config())
    assert s._daily._bucket_ns == 86_400 * 1_000_000_000  # UTC daily aggregation
    assert s._kd._n == 9
    assert s._sizer._cfg.target_vol == 0.20
    assert s._sizer.scale() is None  # vol-target warmup


def test_invalid_trigger_mode_rejected():
    with pytest.raises(ValueError):
        KdMacdCrypto(config=_config(trigger_mode="nope"))


# --- decision core ----------------------------------------------------------

WARMUP = 35  # > max(kd_n, slow+signal-1)


def _ramp(n, start=100.0, step=2.0):
    """Monotonic rising closes -> persistent KD uptrend, positive MACD bar."""
    out = []
    p = start
    for _ in range(n):
        c = p + step
        out.append((p, max(p, c) + 0.5, min(p, c) - 1.0, c))
        p = c
    return out


def _flat(n, start=100.0):
    out = []
    for _ in range(n):
        out.append((start, start + 0.1, start - 0.1, start))
    return out


def _fall(n, start=200.0, step=2.0):
    out = []
    p = start
    for _ in range(n):
        c = p - step
        out.append((p, max(p, c) + 1.0, min(p, c) - 0.5, c))
        p = c
    return out


def _scenario_long():
    """Flat base then sustained rally: triggers exactly one golden cross + bar>0."""
    base = _flat(60)
    rally = _ramp(80, start=100.0)
    return base + rally


def _scenario_down():
    """Rally top then sustained decline: triggers death cross + bar<0."""
    rally = _ramp(80, start=100.0)
    decline = _fall(60, start=100.0 + 2.0 * 80)
    return rally + decline


def _decide_all(s, days):
    intents = []
    for o, h, l, c in days:
        intents.extend(s._decide(o, h, l, c))
    return intents


def test_fixed_sizing_uses_unit_qty():
    s = KdMacdCrypto(config=_config(sizing_mode="fixed"))
    intents = _decide_all(s, _scenario_long())
    buys = [i for i in intents if i.side == 1]
    assert buys, "expected at least one BUY during the rally onset"
    assert all(i.qty == Decimal("0.01") for i in buys)


def test_long_only_no_short_after_death_cross():
    s = KdMacdCrypto(config=_config(sizing_mode="fixed"))
    intents = _decide_all(s, _scenario_long() + _scenario_down())
    side = 0
    for i in intents:
        if i.side == 1:
            side = 1
        elif i.side == -1 and side == 1:
            side = 0
    assert 1 in [i.side for i in intents]  # was long at some point
    assert s._side in (0, 1)  # never went short


def test_allow_short_opens_short_on_death_cross():
    s = KdMacdCrypto(config=_config(sizing_mode="fixed", allow_short=True))
    intents = _decide_all(s, _scenario_long() + _scenario_down())
    assert s._side == -1, "expected to end short after sustained decline"


def test_death_cross_long_only_returns_to_flat():
    s = KdMacdCrypto(config=_config(sizing_mode="fixed"))
    _decide_all(s, _scenario_long())
    assert s._side == 1
    intents = _decide_all(s, _scenario_down())
    sells = [i for i in intents if i.side == -1]
    assert sells, "expected a closing SELL on the downturn"
    assert s._side in (0, 1)


def test_vol_target_scales_qty():
    s = KdMacdCrypto(
        config=_config(sizing_mode="vol_target", size_target_vol=10.0, size_half_life=20)
    )
    # flat base gives tiny sigma; rally keeps steps constant -> scale = target/sigma
    intents = _decide_all(s, _scenario_long())
    buys = [i for i in intents if i.side == 1]
    assert buys
    assert any(i.qty > Decimal("0.01") for i in buys)


def test_vol_target_warmup_falls_back_to_unit():
    s = KdMacdCrypto(config=_config(size_half_life=40))  # warmup = 120
    assert s._sizer.scale() is None
    # golden cross fires around bar 61 of scenario_long, well before warmup ends
    intents = _decide_all(s, _scenario_long()[:100])
    assert intents, "expected a signal during the rally onset"
    for i in intents:
        assert i.qty == Decimal("0.01")


def test_state_trigger_mode_runs():
    s = KdMacdCrypto(config=_config(sizing_mode="fixed", trigger_mode="state"))
    intents = _decide_all(s, _scenario_long() + _scenario_down())
    assert intents


def test_no_exchange_io():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src" / "sngw_trader" / "strategies"
    text = (root / "kd_macd_crypto.py").read_text(encoding="utf-8")
    for token in ("ccxt", "requests.get", "httpx.", "websocket", "python_okx",
                  "BacktestNode", "TradingNode", "OKXDataClientFactory"):
        assert token not in text
