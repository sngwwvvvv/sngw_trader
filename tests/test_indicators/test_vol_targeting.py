import math
from decimal import Decimal

import pytest

from sngw_trader.indicators.vol_targeting import VolTargetConfig, VolTargetSizer


def _cfg(**kw) -> VolTargetConfig:
    base = dict(
        target_vol=0.20,
        half_life=1,
        min_scale=0.0,
        max_scale=3.0,
        rebalance_band=0.10,
        periods_per_year=365,
        mode="vol_target",
    )
    base.update(kw)
    return VolTargetConfig(**base)


def test_config_rejects_bad_mode():
    with pytest.raises(ValueError):
        VolTargetConfig(
            target_vol=0.20,
            half_life=1,
            min_scale=0.0,
            max_scale=3.0,
            rebalance_band=0.10,
            periods_per_year=365,
            mode="nope",
        )


def test_ewma_variance_matches_hand_calc():
    s = VolTargetSizer(_cfg(half_life=1, target_vol=0.20))
    assert s.update(100.0) is None
    lam = 2 ** (-1 / 1)
    r1 = 110 / 100 - 1
    s.update(110.0)
    r2 = 99 / 110 - 1
    s.update(99.0)
    r3 = 108 / 99 - 1
    sigma = s.update(108.0)
    v = r1 * r1
    v = lam * v + (1 - lam) * r2 * r2
    v = lam * v + (1 - lam) * r3 * r3
    assert sigma is not None
    assert math.isclose(sigma, math.sqrt(v * 365), rel_tol=1e-12)
    assert math.isclose(s.scale(), min(3.0, max(0.0, 0.20 / sigma)))


def test_zero_sigma_uses_max_scale():
    s = VolTargetSizer(_cfg(half_life=1, max_scale=2.5))
    for px in (100.0, 100.0, 100.0, 100.0):
        s.update(px)
    assert s.scale() == 2.5


def test_desired_qty_sign_and_warmup():
    s = VolTargetSizer(_cfg(half_life=1))
    unit = Decimal("0.01")
    assert s.desired_qty(0, unit) == Decimal("0")
    assert s.desired_qty(1, unit) is None
    with pytest.raises(ValueError):
        s.desired_qty(2, unit)
    for px in (100.0, 101.0, 99.0, 102.0):
        s.update(px)
    q = s.desired_qty(1, unit)
    assert q is not None and q > 0
    assert s.desired_qty(-1, unit) == -q
    assert s.desired_qty(0, unit) == Decimal("0")


def test_fixed_mode_scale_one_no_warmup():
    s = VolTargetSizer(_cfg(mode="fixed"))
    assert s.scale() == 1.0
    assert s.desired_qty(1, Decimal("0.01")) == Decimal("0.01")
    s.update(100.0)
    assert s.desired_qty(-1, Decimal("0.01")) == Decimal("-0.01")


def test_should_rebalance_rules():
    s = VolTargetSizer(_cfg(rebalance_band=0.10))
    z, a, b = Decimal("0"), Decimal("0.010"), Decimal("0.011")
    assert s.should_rebalance(z, z) is False
    assert s.should_rebalance(z, a) is True
    assert s.should_rebalance(a, z) is True
    assert s.should_rebalance(a, Decimal("-0.010")) is True
    assert s.should_rebalance(a, b) is False
    assert s.should_rebalance(a, Decimal("0.012")) is True


def test_should_rebalance_band_zero():
    s = VolTargetSizer(_cfg(rebalance_band=0.0))
    assert s.should_rebalance(Decimal("0.01"), Decimal("0.0100001")) is True
    assert s.should_rebalance(Decimal("0.01"), Decimal("0.01")) is False


def test_vol_targeting_module_has_no_nautilus():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / "src" / "sngw_trader" / "indicators" / "vol_targeting.py").read_text(
        encoding="utf-8"
    )
    assert "nautilus_trader" not in text
