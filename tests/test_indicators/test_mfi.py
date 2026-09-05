import math

from sngw_trader.indicators.mfi import Mfi


def _feed(m, rows):
    out = None
    for h, l, c, v in rows:
        out = m.update(h, l, c, v)
    return out


def test_mfi_all_positive_flow_is_100():
    m = Mfi(14)
    # strictly rising typical price -> all positive money flow
    rows = [(float(100 + i), float(99 + i), float(100 + i), 10.0) for i in range(15)]
    out = _feed(m, rows)
    assert out == 100.0


def test_mfi_all_negative_flow_is_0():
    m = Mfi(14)
    rows = [(float(100 - i), float(99 - i), float(100 - i), 10.0) for i in range(15)]
    out = _feed(m, rows)
    assert out == 0.0


def test_mfi_none_during_warmup():
    m = Mfi(14)
    for i in range(14):
        assert m.update(float(100 + i), float(99 + i), float(100 + i), 10.0) is None
    assert m.update(114.0, 113.0, 114.0, 10.0) is not None


def test_mfi_zero_volume_is_no_flow_not_crash():
    m = Mfi(14)
    rows = [(float(100 + i), float(99 + i), float(100 + i), 0.0) for i in range(15)]
    # zero volume on both sides -> 0/0; expect None (no flow) not ZeroDivisionError
    out = _feed(m, rows)
    assert out is None or math.isnan(out) or out == 50.0 or out == 0.0
