import math

from sngw_trader.indicators.rsi import Rsi


def test_rsi_all_up_is_100():
    r = Rsi(14)
    out = None
    for p in [float(100 + i) for i in range(15)]:
        out = r.update(p)
    assert out == 100.0


def test_rsi_all_down_is_0():
    r = Rsi(14)
    out = None
    for p in [float(100 - i) for i in range(15)]:
        out = r.update(p)
    assert out == 0.0


def test_rsi_none_during_warmup():
    r = Rsi(14)
    for i in range(14):
        assert r.update(float(100 + i)) is None
    assert r.update(114.0) is not None


def test_rsi_known_value_synthetic():
    # 2-period RSI, hand-computable: gains 1, losses 1 alternating
    r = Rsi(2)
    prices = [100.0, 101.0, 100.0, 101.0, 100.0]
    out = None
    for p in prices:
        out = r.update(p)
    # Wilder: seed avg after 2 changes = (1,1)/2, then +1 → (0.75,0.25), then -1 → (0.375,0.625)
    # RS = 0.6 -> RSI = 37.5
    assert out is not None and math.isclose(out, 37.5)
