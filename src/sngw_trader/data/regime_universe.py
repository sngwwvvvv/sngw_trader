"""Sector ETF universe and session-age helpers for the macro-proxy regime filter.

Do not place orders here. Do not import runners or strategies.
`data/` must not import `research/`.
"""

from __future__ import annotations

from bisect import bisect_right
from datetime import date, datetime, timedelta, timezone

from sngw_trader.data.yahoo_etf import instrument_id_for

FEATURE_VERSION = "macro-proxy-2axis-v1"
STALE_SESSIONS = 3  # age > STALE_SESSIONS -> invalid
QUALITY_INVALID, QUALITY_LAGGED, QUALITY_FRESH = 0, 1, 2

SECTOR_ETFS: tuple[str, ...] = (
    "XLY", "XLI", "XLB", "XLF", "XLK", "XLE", "XLP", "XLU", "XLV",
)
CYC_ETFS: tuple[str, ...] = ("XLY", "XLI", "XLB", "XLF")
DEF_ETFS: tuple[str, ...] = ("XLP", "XLU", "XLV")
BENCHMARK_ETFS: tuple[str, ...] = ("SPY",)
REFERENCE_ETFS: tuple[str, ...] = ("QQQ", "IWM")
ALL_EVAL_ETFS: tuple[str, ...] = SECTOR_ETFS + BENCHMARK_ETFS + REFERENCE_ETFS

FRED_OAS = "BAMLH0A0HYM2"
FRED_VIX = "VIXCLS"
FRED_VXV = "VXVCLS"
YAHOO_COPPER = "HG=F"
YAHOO_GOLD = "GC=F"


def sector_instrument_ids() -> list[str]:
    return [instrument_id_for(symbol) for symbol in SECTOR_ETFS]


def session_index(sessions: list[date]) -> dict[date, int]:
    return {d: i for i, d in enumerate(sessions)}


def session_age(obs: date, session: date, sessions: list[date]) -> int:
    """Age of `obs` relative to `session` in session count.

    `obs` not in the calendar resolves to the last session on or before it.
    Observation newer than the session raises. Observation before the calendar
    start returns the session index (large -> invalid downstream).
    """
    i = bisect_right(sessions, session) - 1
    if i < 0:
        raise ValueError(f"session {session} before calendar start")
    j = bisect_right(sessions, obs) - 1
    if j > i:
        raise ValueError(f"observation {obs} is newer than session {session}")
    if j < 0:
        return i
    return i - j


def previous_calendar_date(d: date) -> date:
    return d - timedelta(days=1)


def is_last_session_of_week(session: date, sessions: list[date]) -> bool:
    i = sessions.index(session)
    if i + 1 == len(sessions):
        return True
    return sessions[i + 1].isocalendar()[:2] != session.isocalendar()[:2]


def session_date_from_ts(ts_ns: int) -> str:
    return datetime.fromtimestamp(ts_ns / 1_000_000_000, tz=timezone.utc).strftime(
        "%Y-%m-%d"
    )
