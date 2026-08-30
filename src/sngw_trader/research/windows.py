"""Walk-forward window boundaries. Pure datetime math, no nautilus."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Window:
    index: int
    is_start: datetime
    is_end: datetime
    oos_start: datetime
    oos_end: datetime


def add_months(dt: datetime, months: int) -> datetime:
    total = dt.year * 12 + (dt.month - 1) + months
    year, month_index = divmod(total, 12)
    month = month_index + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def holdout_start(data_end: datetime, holdout_months: int) -> datetime:
    return add_months(data_end, -holdout_months)


def compute_windows(
    data_start: datetime,
    data_end: datetime,
    is_months: int = 6,
    oos_months: int = 3,
    holdout_months: int = 6,
) -> list[Window]:
    """Rolling windows in [data_start, data_end - holdout]. OOS ends before holdout."""
    if not data_start < data_end:
        raise ValueError("data_start must be before data_end")
    wf_end = holdout_start(data_end, holdout_months)
    windows: list[Window] = []
    t = data_start
    index = 0
    while True:
        is_end = add_months(t, is_months)
        oos_end = add_months(is_end, oos_months)
        if oos_end > wf_end:
            break
        windows.append(Window(index, t, is_end, is_end, oos_end))
        index += 1
        t = add_months(t, oos_months)
    if not windows:
        raise ValueError("Not enough data for one IS/OOS window before holdout")
    return windows