import pytest
from nautilus_trader.model import BarType

from sngw_trader.indicators.oi_mean_reversion import (
    bar_type_matches,
    has_oi_rollover,
    is_oi_decreasing,
    is_oi_increasing,
    oi_return,
    reentry_side,
)


INSTRUMENT = "BTC-USDT-SWAP.OKX"
COMPOSITE = BarType.from_str(
    f"{INSTRUMENT}-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL"
)
RUNTIME = BarType.from_str(f"{INSTRUMENT}-5-MINUTE-LAST-INTERNAL")


def test_oi_return_requires_lookback_history():
    assert oi_return([100.0, 101.0], 3) is None


def test_oi_return_uses_percentage_change_and_rejects_non_positive_baseline():
    assert oi_return([100.0, 102.0, 104.0], 2) == pytest.approx(0.04)
    assert oi_return([0.0, 1.0], 1) is None


def test_oi_increase_and_decrease_use_percentage_change():
    values = [100.0, 102.0, 104.0]
    assert is_oi_increasing(values, 2, 0.01)
    assert not is_oi_decreasing(values, 2, 0.01)


def test_oi_direction_checks_include_threshold_boundaries():
    values = [100.0, 99.0]
    assert is_oi_decreasing(values, 1, 0.01)
    assert not is_oi_increasing(values, 1, 0.01)


def test_rollover_requires_current_oi_not_above_peak():
    assert has_oi_rollover(105.0, 105.0, 0.0)
    assert has_oi_rollover(104.0, 105.0, 0.0)
    assert not has_oi_rollover(106.0, 105.0, 0.0)
    assert has_oi_rollover(95.0, 100.0, 0.05)
    assert not has_oi_rollover(96.0, 100.0, 0.05)


def test_reentry_is_directional():
    assert reentry_side(1, 100.0, 99.0, 101.0)
    assert reentry_side(-1, 100.0, 99.0, 101.0)
    assert not reentry_side(1, 98.0, 99.0, 101.0)
    assert not reentry_side(-1, 102.0, 99.0, 101.0)
    assert not reentry_side(0, 100.0, 99.0, 101.0)


def test_runtime_internal_bar_matches_composite_instrument_and_spec():
    assert bar_type_matches(RUNTIME, COMPOSITE)
    assert not bar_type_matches(
        BarType.from_str("ETH-USDT-SWAP.OKX-5-MINUTE-LAST-INTERNAL"),
        COMPOSITE,
    )
    assert not bar_type_matches(
        BarType.from_str(f"{INSTRUMENT}-1-MINUTE-LAST-INTERNAL"),
        COMPOSITE,
    )
    assert not bar_type_matches(
        BarType.from_str(
            f"{INSTRUMENT}-5-MINUTE-LAST-INTERNAL@1-MINUTE-INTERNAL"
        ),
        COMPOSITE,
    )
