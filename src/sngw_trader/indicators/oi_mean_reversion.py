from collections.abc import Sequence

from nautilus_trader.model import BarType


def bar_type_matches(actual: BarType, expected: BarType) -> bool:
    if actual.instrument_id != expected.instrument_id:
        return False
    if expected.is_composite():
        if actual.is_composite():
            return actual == expected and actual.composite() == expected.composite()
        return actual == expected.standard()
    return actual == expected


def oi_return(values: Sequence[float], lookback: int) -> float | None:
    if lookback < 1 or len(values) <= lookback:
        return None
    oldest = values[-lookback - 1]
    if oldest <= 0:
        return None
    return values[-1] / oldest - 1


def is_oi_increasing(values: Sequence[float], lookback: int, threshold: float) -> bool:
    change = oi_return(values, lookback)
    return change is not None and change >= threshold


def is_oi_decreasing(values: Sequence[float], lookback: int, threshold: float) -> bool:
    change = oi_return(values, lookback)
    return change is not None and change <= -threshold


def has_oi_rollover(latest_oi: float, peak_oi: float, threshold: float) -> bool:
    return latest_oi <= peak_oi * (1 - threshold)


def reentry_side(side: int, close: float, lower: float, upper: float) -> bool:
    if side == 1:
        return close >= lower
    if side == -1:
        return close <= upper
    return False
