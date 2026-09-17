from sngw_trader.indicators.volume_mean_reversion import (
    is_rejection,
    is_volume_fading,
    is_volume_spike,
    rejection_ratio,
    volume_ratio,
)


def test_volume_ratio_returns_latest_over_prior_mean():
    values = [1.0, 1.0, 1.0, 1.0, 4.0]
    assert volume_ratio(values, lookback=4) == 4.0


def test_volume_ratio_requires_enough_history():
    assert volume_ratio([], lookback=3) is None
    assert volume_ratio([1.0, 2.0, 3.0], lookback=3) is None


def test_volume_ratio_rejects_zero_mean():
    assert volume_ratio([0.0, 0.0, 1.0], lookback=1) is None


def test_is_volume_spike_true_at_or_above_threshold():
    values = [1.0, 1.0, 1.0, 1.0, 2.0]
    assert is_volume_spike(values, lookback=4, threshold=2.0)
    assert is_volume_spike(values, lookback=4, threshold=1.9)


def test_is_volume_spike_false_below_threshold():
    values = [1.0, 1.0, 1.0, 1.0, 1.5]
    assert not is_volume_spike(values, lookback=4, threshold=2.0)


def test_is_volume_spike_false_without_history():
    assert not is_volume_spike([], lookback=4, threshold=2.0)


def test_is_volume_fading_true_at_or_below_threshold():
    values = [2.0, 2.0, 2.0, 2.0, 1.0]
    assert is_volume_fading(values, lookback=4, threshold=0.5)
    assert is_volume_fading(values, lookback=4, threshold=0.6)


def test_is_volume_fading_false_above_threshold():
    values = [2.0, 2.0, 2.0, 2.0, 1.5]
    assert not is_volume_fading(values, lookback=4, threshold=0.5)


def test_is_volume_fading_false_without_history():
    assert not is_volume_fading([], lookback=4, threshold=0.5)


def test_rejection_ratio_long_uses_lower_wick():
    assert rejection_ratio(1, high=105.0, low=90.0, close=100.0) == 2.0 / 3.0


def test_rejection_ratio_short_uses_upper_wick():
    assert rejection_ratio(-1, high=110.0, low=100.0, close=104.0) == 0.6


def test_rejection_ratio_rejects_degenerate_range():
    assert rejection_ratio(1, high=100.0, low=100.0, close=100.0) is None


def test_is_rejection_true_at_or_above_threshold():
    assert is_rejection(1, high=105.0, low=90.0, close=100.0, threshold=0.66)
    assert is_rejection(-1, high=110.0, low=100.0, close=103.0, threshold=0.7)


def test_is_rejection_false_below_threshold():
    assert not is_rejection(1, high=105.0, low=90.0, close=95.0, threshold=0.66)