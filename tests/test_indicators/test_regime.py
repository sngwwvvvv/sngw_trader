import numpy as np

from sngw_trader.indicators.regime import (
    growth_score,
    label_series,
    regime_code,
    robust_z,
    stress_score,
)


def test_warmup_is_nan_before_252() -> None:
    x = np.arange(300, dtype=float)
    z = robust_z(x)
    assert np.all(np.isnan(z[:251]))
    assert not np.isnan(z[251])


def test_constant_series_z_is_nan() -> None:
    x = np.ones(300)
    z = robust_z(x)
    assert np.all(np.isnan(z[251:]))


def test_known_median_iqr_window() -> None:
    # zeros + one end outlier makes every percentile 0 -> denom 0 ->
    # ZeroDivisionError in this test body itself; use a spread series.
    x = np.arange(300, dtype=float)
    x[-1] = 10.0
    z = robust_z(x, median_window=60, iqr_long=252)
    sl60 = x[-60:]
    sl252 = x[-252:]
    med = float(np.median(sl60))
    iqr60 = float(np.percentile(sl60, 75) - np.percentile(sl60, 25))
    iqr252 = float(np.percentile(sl252, 75) - np.percentile(sl252, 25))
    denom = max(iqr60, iqr252 / 3) * 0.7413
    assert np.isclose(z[-1], (10.0 - med) / denom)


def test_stress_is_equal_weight_of_component_z() -> None:
    oas = np.array([2.0, 0.0])
    vx = np.array([0.0, 4.0])
    np.testing.assert_allclose(stress_score(oas, vx), [1.0, 2.0])


def test_growth_is_copper_gold_z() -> None:
    g = np.array([-1.5, 0.2])
    np.testing.assert_allclose(growth_score(g), g)


def test_r0_box_and_weak_axis_still_quadrant() -> None:
    assert regime_code(0.2, 0.2) == 0
    assert regime_code(-0.2, 1.5) == 1
    assert regime_code(0.2, 1.5) == 2
    assert regime_code(-1.0, 1.0) == 1
    assert regime_code(1.0, 1.0) == 2
    assert regime_code(-1.0, -1.0) == 3
    assert regime_code(1.0, -1.0) == 4
    assert regime_code(0.0, 1.5) == 1  # S==0 → S−
    assert regime_code(float("nan"), 1.0) is None


def test_label_series_uses_minus_one_for_nan() -> None:
    s = np.array([np.nan, -1.0])
    g = np.array([1.0, 1.0])
    out = label_series(s, g)
    assert out[0] == -1
    assert out[1] == 1
