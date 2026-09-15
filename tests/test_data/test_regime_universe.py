from datetime import date, datetime, timezone

from sngw_trader.data.regime_universe import (
    SECTOR_ETFS,
    session_age,
    previous_calendar_date,
    is_last_session_of_week,
    sector_instrument_ids,
)


def test_nine_sector_etfs_and_arca_ids() -> None:
    assert len(SECTOR_ETFS) == 9
    assert "XLC" not in SECTOR_ETFS
    assert "XLRE" not in SECTOR_ETFS
    ids = sector_instrument_ids()
    assert ids[0] == "XLY.ARCA"
    assert "XLK.ARCA" in ids


def test_session_age_same_day_is_zero() -> None:
    sessions = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    assert session_age(date(2024, 1, 3), date(2024, 1, 3), sessions) == 0


def test_session_age_skips_weekend() -> None:
    sessions = [date(2024, 1, 5), date(2024, 1, 8)]  # Fri, Mon
    assert session_age(date(2024, 1, 5), date(2024, 1, 8), sessions) == 1


def test_session_age_fred_weekend_obs_uses_last_session() -> None:
    sessions = [date(2024, 1, 5), date(2024, 1, 8)]
    assert session_age(date(2024, 1, 7), date(2024, 1, 8), sessions) == 1  # Sun → Fri


def test_oas_cap_is_calendar_d_minus_1() -> None:
    assert previous_calendar_date(date(2024, 1, 8)) == date(2024, 1, 7)


def test_last_session_of_week() -> None:
    sessions = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]
    assert is_last_session_of_week(date(2024, 1, 5), sessions) is True
    assert is_last_session_of_week(date(2024, 1, 4), sessions) is False


def test_session_date_from_ts_utc() -> None:
    from sngw_trader.data.regime_universe import session_date_from_ts

    ts = int(datetime(2020, 1, 2, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    assert session_date_from_ts(ts) == "2020-01-02"
