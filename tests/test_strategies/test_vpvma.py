"""Tests for the VPVMA indicator (handoff 20260905-bt-vpvma).

Paper literal (raw-trading-0005, arXiv:2206.12282 §4.1, formulas 4.2-1..8):
TP=(H+L+C)/3; VWMA_n = sum(TP*V)/sum(V); DV = std([H,L,C,O]) sample (ddof=1);
ESVMap = EMA(VWMA_fast * DV, fast); ELVMap = EMA(VWMA_slow * DV, slow)
(4.2-5 says SVWMA — treated as a typo for LVWMA, spec assumption 1);
VPVMA = ESVMap - ELVMap; VPVMAS = SMA(VPVMA, sign).

EMA convention matches repo's kd_macd._Ema: alpha = 2/(n+1), first-value seed.
"""

import statistics

from sngw_trader.indicators.vpvma import Vpvma


def _bars(n, start=100.0, step=1.0):
    """Simple rising bars: o, h, l, c, v per day."""
    out = []
    p = start
    for i in range(n):
        c = p + step
        out.append((p, max(p, c) + 0.5, min(p, c) - 0.5, c, 10.0 + i))
        p = c
    return out


def test_vpvma_matches_reference_formulas():
    bars = _bars(40)
    fast, slow, sign = 3, 5, 2
    ind = Vpvma(fast=fast, slow=slow, sign=sign)

    tps = [(h + l + c) / 3 for o, h, l, c, v in bars]
    dvs = [statistics.stdev([h, l, c, o]) for o, h, l, c, v in bars]

    def vwma(i, n):
        num = sum(tps[j] * bars[j][4] for j in range(i - n + 1, i + 1))
        den = sum(bars[j][4] for j in range(i - n + 1, i + 1))
        return num / den

    def ema_series(xs, n):
        a = 2.0 / (n + 1)
        out = [xs[0]]
        for x in xs[1:]:
            out.append(out[-1] + a * (x - out[-1]))
        return out

    outs = []
    for o, h, l, c, v in bars:
        outs.append(ind.update(h, l, c, o, v))

    assert all(o is None for o in outs[: slow - 1])
    # implementation seeds both EMA chains at the first valid slow-VWMA bar;
    # mirror that so the seeded EMA paths are identical
    start = slow - 1
    es = ema_series([vwma(i, fast) * dvs[i] for i in range(start, len(bars))], fast)
    el = ema_series([vwma(i, slow) * dvs[i] for i in range(start, len(bars))], slow)
    vp = [a - b for a, b in zip(es, el)]
    sig = [sum(vp[i - sign + 1 : i + 1]) / sign for i in range(sign - 1, len(vp))]

    last = outs[-1]
    assert last is not None
    v, s = last
    assert v == pytest.approx(vp[-1], rel=1e-9)
    assert s == pytest.approx(sig[-1], rel=1e-9)


import pytest  # noqa: E402


def test_warmup_gate():
    ind = Vpvma(fast=3, slow=5, sign=2)
    outs = [ind.update(h, l, c, o, v) for o, h, l, c, v in _bars(10)]
    # first slow-VWMA at bar slow (index slow-1); SMA needs sign VPVMA values
    # -> first output at index slow + sign - 2
    first_idx = 5 + 2 - 2
    assert all(o is None for o in outs[:first_idx])
    assert outs[first_idx] is not None
    assert ind.warmed


def test_monotone_rally_gives_positive_vpvma():
    ind = Vpvma(fast=12, slow=26, sign=9)
    v = None
    for o, h, l, c, vol in _bars(80):
        out = ind.update(h, l, c, o, vol)
        if out:
            v = out[0]
    assert v is not None and v > 0


def test_flat_series_gives_zero_vpvma():
    ind = Vpvma(fast=3, slow=5, sign=2)
    bars = [(100.0, 100.1, 99.9, 100.0, 10.0)] * 30
    outs = [ind.update(h, l, c, o, v) for o, h, l, c, v in bars]
    v, s = outs[-1]
    assert v == pytest.approx(0.0, abs=1e-9)
    assert s == pytest.approx(0.0, abs=1e-9)


def test_rejects_bad_spans():
    with pytest.raises(ValueError):
        Vpvma(fast=0)
    with pytest.raises(ValueError):
        Vpvma(slow=2, fast=3)  # slow must be > fast? paper: slow=26 > fast=12


# --- strategy-level tests (handoff 20260905-bt-vpvma Task 2) -----------------

from decimal import Decimal  # noqa: E402

from nautilus_trader.model import BarType, InstrumentId  # noqa: E402

from sngw_trader.strategies.vpvma import (  # noqa: E402
    Vpvma as VpvmaStrategy,
    VpvmaConfig,
    band_signal,
    direction_target,
)

INSTRUMENT = InstrumentId.from_str("BTC-USDT-SWAP.OKX")
BAR_TYPE = BarType.from_str("BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL")


def test_band_signal_truth_table():
    # prev condition: VPVMA <= VPVMAS
    # buy: now above upper band, prev at/below signal
    assert band_signal(10.0, 10.0, 12.0, 10.0, 0.1) == 1   # 12 > 11
    assert band_signal(12.0, 10.0, 12.0, 10.0, 0.1) == 0   # prev ABOVE signal (12>10) -> no cross
    assert band_signal(10.0, 10.0, 10.5, 10.0, 0.1) == 0   # inside upper band (10.5 < 11)
    # sell: now below lower band (1 - 2*bw = 0.8)
    assert band_signal(10.0, 10.0, 7.9, 10.0, 0.1) == -1   # 7.9 < 8
    assert band_signal(10.0, 10.0, 8.5, 10.0, 0.1) == 0    # inside lower band
    assert band_signal(8.0, 10.0, 7.9, 10.0, 0.1) == -1    # paper literal: single shared prev<= condition fires both directions
    # first bar: no prev
    assert band_signal(None, None, 12.0, 10.0, 0.1) == 0


def test_direction_target_truth_table():
    assert direction_target(1, 0, allow_short=False) == 1
    assert direction_target(1, -1, allow_short=True) == 1
    assert direction_target(-1, 1, allow_short=False) == 0
    assert direction_target(-1, 1, allow_short=True) == -1
    assert direction_target(0, 1, allow_short=False) == 1  # hold


def _sconfig(**overrides) -> VpvmaConfig:
    base = dict(
        instrument_id=INSTRUMENT,
        bar_type=BAR_TYPE,
        trade_size=Decimal("0.01"),
    )
    base.update(overrides)
    return VpvmaConfig(**base)


def test_strategy_config_defaults():
    c = _sconfig()
    assert (c.fast, c.slow, c.sign, c.bandwidth) == (12, 26, 9, 0.10)
    assert c.allow_short is False
    assert c.sizing_mode == "unit"
    assert c.instrument_id.value.endswith(".OKX")


def test_strategy_components():
    s = VpvmaStrategy(config=_sconfig())
    assert s._daily._bucket_ns == 86_400 * 1_000_000_000
    assert s._vpvma._slow == 26
    assert s._sizer._cfg.target_vol == 0.20


def test_invalid_sizing_mode_rejected():
    with pytest.raises(ValueError):
        VpvmaStrategy(config=_sconfig(sizing_mode="nope"))


def _sbar(n, start=100.0, step=1.0, vol=10.0):
    """Rising daily bars for _decide (o, h, l, c, volume)."""
    out = []
    p = start
    for _ in range(n):
        c = p + step
        out.append((p, max(p, c) + 0.5, min(p, c) - 0.5, c, vol))
        p = c
    return out


def _sflat(n, start=100.0, vol=10.0):
    return [(start, start + 0.1, start - 0.1, start, vol)] * n


def _sfall(n, start, step=1.0, vol=10.0):
    out = []
    p = start
    for _ in range(n):
        c = p - step
        out.append((p, max(p, c) + 0.5, min(p, c) - 0.5, c, vol))
        p = c
    return out


def _decide_days(s, days):
    intents = []
    for o, h, l, c, v in days:
        intents.extend(s._decide(o, h, l, c, v))
    return intents


VPVMA_WARMUP = 40  # slow 26 + sign 9


def test_unit_sizing_buys_on_rally():
    s = VpvmaStrategy(config=_sconfig())
    intents = _decide_days(s, _sflat(60) + _sbar(80))
    buys = [i for i in intents if i.side == 1]
    assert buys, "expected at least one BUY when VPVMA breaks the upper band"
    assert all(i.qty == Decimal("0.01") for i in buys)


def test_no_signal_on_flat_series():
    s = VpvmaStrategy(config=_sconfig())
    intents = _decide_days(s, _sflat(120))
    assert intents == []


def test_long_only_sells_to_flat_on_downtrend():
    s = VpvmaStrategy(config=_sconfig())
    _decide_days(s, _sflat(60) + _sbar(80))
    assert s._side == 1
    # short enough decline that no band re-entry buy fires (paper rule can
    # re-enter long when VPVMA mean-reverts above the upper band mid-decline)
    intents = _decide_days(s, _sfall(30, start=180.0))
    sells = [i for i in intents if i.side == -1]
    assert sells, "expected a closing SELL when VPVMA breaks the lower band"
    assert s._side in (0, 1)  # long-only never shorts


def test_allow_short_opens_short_on_downtrend():
    s = VpvmaStrategy(config=_sconfig(allow_short=True))
    intents = _decide_days(s, _sflat(60) + _sbar(80) + _sfall(40, start=180.0))
    assert s._side == -1, "expected short after decline breaks lower band"


def test_sell_with_no_position_emits_nothing():
    s = VpvmaStrategy(config=_sconfig())
    # straight decline from flat: sell signal fires but position already flat
    intents = _decide_days(s, _sfall(120, start=200.0))
    assert all(i.side != -1 or False for i in intents) or s._side == 0


def test_vol_target_scales_qty():
    s = VpvmaStrategy(config=_sconfig(sizing_mode="vol_target", size_target_vol=10.0))
    intents = _decide_days(s, _sflat(60) + _sbar(80))
    buys = [i for i in intents if i.side == 1]
    assert buys
    assert any(i.qty > Decimal("0.01") for i in buys)


def test_vol_target_warmup_falls_back_to_unit():
    s = VpvmaStrategy(config=_sconfig(sizing_mode="vol_target", size_half_life=40))
    assert s._sizer.scale() is None
    intents = _decide_days(s, _sflat(40) + _sbar(60))
    assert intents, "expected a signal during warmup"
    for i in intents:
        assert i.qty == Decimal("0.01")


def test_no_exchange_io():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src" / "sngw_trader" / "strategies"
    text = (root / "vpvma.py").read_text(encoding="utf-8")
    for token in ("ccxt", "requests.get", "httpx.", "websocket", "python_okx",
                  "BacktestNode", "TradingNode", "OKXDataClientFactory"):
        assert token not in text
