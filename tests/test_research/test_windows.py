from datetime import datetime, timezone

import pytest

from sngw_trader.research.windows import add_months, compute_windows, holdout_start


def _dt(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, tzinfo=timezone.utc)


def test_add_months_clamps_day():
    assert add_months(_dt(2023, 1, 31), 1) == _dt(2023, 2, 28)
    assert add_months(_dt(2024, 1, 31), 1) == _dt(2024, 2, 29)


def test_holdout_start():
    assert holdout_start(_dt(2025, 6, 1), 6) == _dt(2024, 12, 1)


def test_compute_windows_rolling():
    windows = compute_windows(_dt(2023, 6, 1), _dt(2025, 6, 1))
    assert len(windows) == 4
    assert windows[0].is_start == _dt(2023, 6, 1)
    assert windows[0].oos_end == _dt(2024, 3, 1)
    assert windows[-1].oos_end == _dt(2024, 12, 1)  # holdout 시작(2024-12-01)에 닿는 마지막
    for i, w in enumerate(windows):
        assert w.index == i
        assert w.is_end == w.oos_start


def test_insufficient_data_raises():
    with pytest.raises(ValueError):
        compute_windows(_dt(2025, 1, 1), _dt(2025, 6, 1))


def test_start_after_end_raises():
    with pytest.raises(ValueError):
        compute_windows(_dt(2025, 6, 1), _dt(2025, 1, 1))