"""Tests for MacdMomentumCombo (handoff 20260905-bt-macd-momentum-combo).

The signal -> target -> qty decision core is pure (`_decide` returns
OrderIntents), so tests drive completed daily bars directly without a
Nautilus engine. Execution (_execute/on_event) stays thin.
"""

from decimal import Decimal

import pytest
from nautilus_trader.model import BarType, InstrumentId

from sngw_trader.strategies.macd_momentum_combo import (
    COND_BARS,
    RSI_LOWER,
    MacdMomentumCombo,
    MacdMomentumComboConfig,
    direction_target,
    momentum_all_above,
    momentum_all_below,
)

INSTRUMENT = InstrumentId.from_str("BTC-USDT-SWAP.OKX")
BAR_TYPE = BarType.from_str("BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL")


# --- pure helpers -----------------------------------------------------------


def test_momentum_all_below():
    assert momentum_all_below([1.0] * COND_BARS, 35.0)
    assert not momentum_all_below([35.0] * 5 + [36.0], 35.0)
    assert not momentum_all_below([35.0] * 5, 35.0)  # window not full


def test_momentum_all_above():
    assert momentum_all_above([70.0] * COND_BARS, 70.0)
    assert not momentum_all_above([69.0] * COND_BARS, 70.0)


def test_direction_target_truth_table():
    assert direction_target(True, False, 0, allow_short=False) == 1
    assert direction_target(False, True, 1, allow_short=False) == 0
    assert direction_target(False, True, 1, allow_short=True) == -1
    assert direction_target(False, True, -1, allow_short=True) == -1
    assert direction_target(True, False, -1, allow_short=True) == 1  # flip back
    assert direction_target(False, False, 1, allow_short=False) == 1  # hold
    assert direction_target(False, False, 0, allow_short=True) == 0


# --- config -----------------------------------------------------------------


def _config(**overrides) -> MacdMomentumComboConfig:
    base = dict(
        instrument_id=INSTRUMENT,
        bar_type=BAR_TYPE,
        trade_size=Decimal("0.01"),
    )
    base.update(overrides)
    return MacdMomentumComboConfig(**base)


def test_config_defaults_match_paper():
    c = _config()
    assert (c.macd_fast, c.macd_slow, c.macd_signal) == (12, 26, 9)
    assert c.momentum == "rsi"
    assert c.momentum_window == 14
    assert c.allow_short is False  # paper is long-only
    assert c.all_in is True  # paper sizing
    assert c.use_bracket is False  # paper has no TP/SL
    assert c.instrument_id.value.endswith(".OKX")


def test_invalid_momentum_rejected():
    with pytest.raises(ValueError):
        MacdMomentumCombo(config=_config(momentum="nope"))


def test_mfi_variant_uses_mfi_thresholds():
    s = MacdMomentumCombo(config=_config(momentum="mfi", all_in=False))
    assert s._lower == 25.0
    assert s._upper == 70.0
    s2 = MacdMomentumCombo(config=_config(all_in=False))
    assert s2._lower == 35.0  # RSI lower threshold (paper Table 4)


# --- decision core ----------------------------------------------------------

WARMUP = 40  # macd 26+9 + rsi window + headroom


def _fall(n, start=300.0, step=3.0):
    out = []
    p = start
    for _ in range(n):
        c = p - step
        out.append((p, max(p, c) + 0.5, min(p, c) - 1.0, c))
        p = c
    return out


def _rise(n, start=100.0, step=3.0):
    out = []
    p = start
    for _ in range(n):
        c = p + step
        out.append((p, max(p, c) + 0.5, min(p, c) - 1.0, c))
        p = c
    return out


def _flat(n, start=200.0):
    return [(start, start + 0.1, start - 0.1, start) for _ in range(n)]


def _run(s, days):
    intents = []
    for o, h, l, c in days:
        intents.extend(s._decide(o, h, l, c, 10.0))
    return intents


def test_unit_long_entry_in_oversold_uptick():
    s = MacdMomentumCombo(config=_config(all_in=False))
    # V-bottom: long fall drives RSI <= 35 and MACD below signal; the early
    # rebound bars lift MACD above its signal while RSI is still <= 35
    # (paper rule 특성: only the first bars of the reversal qualify).
    days = _fall(60) + _rise(12, start=300.0 - 3.0 * 60, step=4.0)
    buys = [i for i in _run(s, days) if i.side == 1]
    assert buys, "expected a buy intent in early reversal"
    assert s._signed_qty > 0


def test_unit_sell_exit_after_overbought_turn():
    # Full cycle: buy in early reversal, then the decline fires the paper
    # Sell while RSI is still >= 70 (needs a big rally: RSI must sit at 100
    # through the top so the early-decline bars keep all-of-6 >= 70).
    s = MacdMomentumCombo(config=_config(all_in=False))
    pre = _fall(60, start=300.0, step=3.0)          # -> 120
    rebound = _rise(6, start=120.0, step=4.0)        # buy fires early here
    rally = _rise(40, start=120.0 + 4.0 * 6, step=5.0)  # -> RSI ~100
    decline = _fall(12, start=120.0 + 4.0 * 6 + 5.0 * 40, step=6.0)
    intents = _run(s, pre + rebound + rally + decline)
    sides = [i.side for i in intents]
    assert 1 in sides, "expected buy during rebound"
    assert -1 in sides, "expected sell exit during decline"
    assert s._side == 0  # long-only ends flat


def test_allow_short_flips_to_short():
    s = MacdMomentumCombo(config=_config(all_in=False, allow_short=True))
    s._side = 1
    s._signed_qty = Decimal("0.01")
    intents = s._intents_for(-1)
    # close long (sell) then open short (also a sell order)
    assert [i.side for i in intents] == [-1, -1]
    assert intents[0].qty == Decimal("0.01")
    assert intents[1].qty == Decimal("0.01")
    assert s._side == -1


def test_long_only_closes_not_short():
    s = MacdMomentumCombo(config=_config(all_in=False))
    s._side = 1
    s._signed_qty = Decimal("0.01")
    intents = s._intents_for(0)
    assert [i.side for i in intents] == [-1]
    assert s._side == 0


def test_all_in_entry_leg_has_zero_qty():
    s = MacdMomentumCombo(config=_config(all_in=True))
    intents = s._intents_for(1)
    assert [i.side for i in intents] == [1]
    assert intents[0].qty == Decimal("0")  # resolved in _execute from balance


def test_rsi_reaches_paper_thresholds():
    from sngw_trader.indicators.rsi import Rsi

    r = Rsi(14)
    vals = [v for p in _fall(60) for v in [r.update(p[3])] if v is not None]
    assert min(vals) <= RSI_LOWER
    r2 = Rsi(14)
    vals2 = [v for p in _rise(60) for v in [r2.update(p[3])] if v is not None]
    assert max(vals2) >= 70.0
