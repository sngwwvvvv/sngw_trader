from collections.abc import Sequence


def volume_ratio(values: Sequence[float], lookback: int) -> float | None:
    if lookback < 1 or len(values) <= lookback:
        return None
    prior = values[-lookback - 1 : -1]
    mean = sum(prior) / len(prior)
    if mean <= 0:
        return None
    return values[-1] / mean


def is_volume_spike(values: Sequence[float], lookback: int, threshold: float) -> bool:
    ratio = volume_ratio(values, lookback)
    return ratio is not None and ratio >= threshold


def is_volume_fading(values: Sequence[float], lookback: int, threshold: float) -> bool:
    ratio = volume_ratio(values, lookback)
    return ratio is not None and ratio <= threshold


def rejection_ratio(side: int, high: float, low: float, close: float) -> float | None:
    rng = high - low
    if rng <= 0:
        return None
    return (close - low) / rng if side == 1 else (high - close) / rng


def is_rejection(
    side: int, high: float, low: float, close: float, threshold: float
) -> bool:
    ratio = rejection_ratio(side, high, low, close)
    return ratio is not None and ratio >= threshold