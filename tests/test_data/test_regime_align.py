from datetime import date

from sngw_trader.data.regime_align import build_snapshots, quality_for
from sngw_trader.data.regime_universe import QUALITY_FRESH, QUALITY_INVALID, QUALITY_LAGGED
from sngw_trader.indicators.regime import R0


def test_oas_age_1_is_fresh_others_zero() -> None:
    assert quality_for(1, 0, 0, 0, True) == QUALITY_FRESH


def test_bond_holiday_lags_oas() -> None:
    assert quality_for(2, 0, 0, 0, True) == QUALITY_LAGGED


def test_stale_over_3_is_invalid() -> None:
    assert quality_for(4, 0, 0, 0, True) == QUALITY_INVALID


def test_missing_vxv_ratio_invalid() -> None:
    assert quality_for(1, 0, 0, 0, False) == QUALITY_INVALID


def test_build_uses_oas_d_minus_1_not_double_shift() -> None:
    sessions = [date(2020, 1, d) for d in range(2, 32)] + [date(2020, 2, d) for d in range(3, 28)]
    # 252+ 세션이 필요하므로 길게 만든다
    sessions = []
    d = date(2019, 1, 2)
    while len(sessions) < 280:
        if d.weekday() < 5:
            sessions.append(d)
        d = date.fromordinal(d.toordinal() + 1)

    oas = {s: 4.0 for s in sessions}
    vix = {s: 15.0 for s in sessions}
    vxv = {s: 16.0 for s in sessions}
    cg = {s: 0.002 for s in sessions}
    copper = {s: 3.0 for s in sessions}
    gold = {s: 1500.0 for s in sessions}

    def ts(session: date) -> int:
        return int(session.strftime("%Y%m%d"))

    snaps = build_snapshots(
        sessions, oas, vix, vxv, cg, copper, gold,
        "macro-proxy-2axis-v1", "id1", ts,
    )
    last = snaps[-1]
    assert last.oas_observation_date == sessions[-2].isoformat()  # D-1 session, cap is calendar D-1 which includes previous session
    assert last.oas_age == 1
    assert last.vix_observation_date == sessions[-1].isoformat()
    assert last.vix_age == 0


def test_stale_session_forced_r0() -> None:
    sessions = []
    d = date(2019, 1, 2)
    while len(sessions) < 260:
        if d.weekday() < 5:
            sessions.append(d)
        d = date.fromordinal(d.toordinal() + 1)
    oas = {s: 4.0 for s in sessions[:-5]}  # last 5 sessions missing OAS
    vix = {s: 15.0 for s in sessions}
    vxv = {s: 16.0 for s in sessions}
    cg = {s: 0.002 for s in sessions}
    copper = {s: 3.0 for s in sessions}
    gold = {s: 1500.0 for s in sessions}
    snaps = build_snapshots(
        sessions, oas, vix, vxv, cg, copper, gold, "v1", "id", lambda s: 1,
    )
    assert snaps[-1].regime_code == R0
    assert snaps[-1].quality_code == QUALITY_INVALID
