import math

from sngw_trader.indicators.risk_metrics import (
    DailyAtr,
    Ema,
    is_stop_hit,
    stop_price,
)


def test_ema_valid_after_span_and_smooths():
    ema = Ema(span=3)
    assert ema.update(1.0) is None
    assert ema.update(2.0) is None
    assert ema.update(3.0) == 2.0  # SMA seed
    v = ema.update(4.0)
    assert math.isclose(v, 2.0 + (2 / 4) * (4.0 - 2.0))  # alpha = 2/(3+1)


def test_atr_seed_then_wilder():
    atr = DailyAtr(period=3)
    # TRs: (h-l)=2 ; (h-l=4, |h-pc|=1, |l-pc|=2 -> 3) ; (h-l=2, |h-pc|=1, |l-pc|=1 -> 2)
    assert atr.update(10, 11, 9, 9.5) is None
    assert atr.update(9, 12, 9, 9.0) is None
    v3 = atr.update(9, 10, 9, 9.0)
    assert math.isclose(v3, (2.0 + 3.0 + 1.0) / 3)  # seed at 3rd TR
    tr4 = max(11 - 9, abs(11 - 9), abs(9 - 9))  # bar4: h=11,l=9,pc=9 -> 2.0
    v4 = atr.update(9, 11, 9, 10.5)
    assert math.isclose(v4, (v3 * 2 + tr4) / 3)  # Wilder with period=3


def test_atr_constant_range():
    atr = DailyAtr(period=5)
    v = None
    for _ in range(10):
        v = atr.update(100, 101, 99, 100)
    assert v is not None
    assert math.isclose(v, 2.0)


def test_stop_helpers():
    assert stop_price(+1, ref_price=100.0, atr=2.0, mult=3.0) == 94.0
    assert stop_price(-1, ref_price=100.0, atr=2.0, mult=3.0) == 106.0
    assert is_stop_hit(+1, price=93.0, stop=100.0) is True
    assert is_stop_hit(+1, price=101.0, stop=99.0) is False
    assert is_stop_hit(-1, price=115.0, stop=110.0) is True
    assert is_stop_hit(-1, price=100.0, stop=110.0) is False
    assert is_stop_hit(0, price=50.0, stop=60.0) is False
    assert is_stop_hit(1, price=50.0, stop=None) is False
