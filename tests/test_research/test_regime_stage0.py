from datetime import date

import numpy as np

from sngw_trader.research.regime_stage0 import (
    block_bootstrap_mean_ci,
    fwd_return,
    regime_durations,
    stage0_pass,
)


def test_fwd_open_to_open_skips_missing() -> None:
    sessions = [date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6), date(2020, 1, 7)]
    opens = {date(2020, 1, 2): 10.0, date(2020, 1, 3): 11.0, date(2020, 1, 7): 12.0}
    assert fwd_return(opens, date(2020, 1, 2), 2, sessions) is None  # T+H=1/6 missing open
    opens[date(2020, 1, 6)] = 12.1
    assert abs(fwd_return(opens, date(2020, 1, 2), 2, sessions) - (12.1 / 11.0 - 1)) < 1e-12


def test_duration_median_ignores_r0_for_pass_input() -> None:
    d = regime_durations([1, 1, 1, 0, 0, 4, 4, 4, 4])
    assert d[1] == [3]
    assert d[4] == [4]
    assert d[0] == [2]


def test_pass_requires_all_three_plus_sample() -> None:
    table = {
        "r1_cyc_20": 0.02, "r1_def_20": 0.01, "r1_spy_20": 0.015,
        "r4_cyc_20": -0.03, "r4_spy_20": -0.01,
        "r4_cyc_p5": -0.10, "r4_def_p5": -0.04, "r1_cyc_p5": -0.03,
        "duration_median_r1_r4": 12,
        "n_r1": 40, "n_r4": 40, "share_r1": 0.1, "share_r4": 0.1, "n_total": 400,
    }
    ok, reasons = stage0_pass(table, None)
    assert ok is True
    table["r1_cyc_20"] = 0.005
    ok, _ = stage0_pass(table, None)
    assert ok is False


def test_bootstrap_ci_contains_mean() -> None:
    rng = np.random.default_rng(0)
    x = rng.normal(0.01, 0.02, size=200)
    mean, lo, hi = block_bootstrap_mean_ci(x, block=5, iters=200, seed=1)
    assert lo <= mean <= hi


def test_rescored_codes_reproduce_stored_at_same_window() -> None:
    from datetime import timedelta

    from sngw_trader.data.regime_align import build_snapshots
    from sngw_trader.data.regime_universe import QUALITY_INVALID
    from sngw_trader.indicators.regime import IQR_LONG, MEDIAN_WINDOW
    from sngw_trader.research.regime_stage0 import rescored_codes

    sessions = []
    d = date(2010, 1, 4)
    while len(sessions) < 700:
        if d.weekday() < 5:
            sessions.append(d)
        d += timedelta(days=1)
    rng = np.random.default_rng(7)
    oas = {s: float(2.0 + np.cumsum(rng.normal(0, 0.01, 700))[i]) for i, s in enumerate(sessions)}
    vix = {s: 15.0 + (i % 11) * 0.5 for i, s in enumerate(sessions)}
    vxv = {s: 1.0 + (i % 7) * 0.01 for i, s in enumerate(sessions)}
    cg = {s: float(1.0 + np.sin(i / 13.0) * 0.05) for i, s in enumerate(sessions)}
    snaps = build_snapshots(sessions, oas, vix, vxv, cg, cg, cg, "v", "sid", lambda s: 0)
    assert snaps
    stored = {s.session_date: s.regime_code for s in snaps}
    assert len(set(stored.values())) > 1
    # First session has no prior OAS obs -> stored already contains one
    # quality-INVALID warmup row; alignment must be via session_date.
    assert any(s.quality_code == QUALITY_INVALID for s in snaps)
    resc = rescored_codes(snaps, MEDIAN_WINDOW)
    assert set(resc) == set(stored)
    valid = [s for s in snaps if s.quality_code != QUALITY_INVALID]
    for j, s in enumerate(valid):
        if j < IQR_LONG - 1:
            assert resc[s.session_date] == 0  # pre-long-window head: not rescorable -> R0
        else:
            assert resc[s.session_date] == stored[s.session_date]  # same window == stored codes exactly
    w120 = rescored_codes(snaps, 120)
    assert set(w120) == set(stored)
    assert set(w120.values()) <= {0, 1, 2, 3, 4}


def test_rescored_codes_robust_to_invalid_warmup_rows() -> None:
    from datetime import timedelta

    from sngw_trader.data.regime_align import build_snapshots
    from sngw_trader.data.regime_universe import QUALITY_INVALID
    from sngw_trader.indicators.regime import IQR_LONG, MEDIAN_WINDOW, R0
    from sngw_trader.research.regime_stage0 import rescored_codes

    sessions = []
    d = date(2010, 1, 4)
    while len(sessions) < 700:
        if d.weekday() < 5:
            sessions.append(d)
        d += timedelta(days=1)
    rng = np.random.default_rng(7)
    oas = {s: float(2.0 + np.cumsum(rng.normal(0, 0.01, 700))[i]) for i, s in enumerate(sessions)}
    vix = {s: 15.0 + (i % 11) * 0.5 for i, s in enumerate(sessions)}
    vxv = {s: 1.0 + (i % 7) * 0.01 for i, s in enumerate(sessions)}
    cg = {s: float(1.0 + np.sin(i / 13.0) * 0.05) for i, s in enumerate(sessions)}
    # Missing OAS at the start -> sessions 0..5 are quality-INVALID, all inside
    # the warmup range (t < IQR_LONG-1); build_snapshots emits them as R0 rows.
    oas_warm = {s: v for i, (s, v) in enumerate(oas.items()) if i >= 5}
    snaps = build_snapshots(sessions, oas_warm, vix, vxv, cg, cg, cg, "v", "sid", lambda s: 0)
    invalid = [s for s in snaps if s.quality_code == QUALITY_INVALID]
    assert invalid and all(sessions.index(date.fromisoformat(s.session_date)) < IQR_LONG - 1 for s in invalid)
    stored = {s.session_date: s.regime_code for s in snaps}
    resc = rescored_codes(snaps, MEDIAN_WINDOW)
    assert set(resc) == set(stored)  # invalid sessions still emit a code
    valid = [s for s in snaps if s.quality_code != QUALITY_INVALID]
    for j, s in enumerate(valid):
        if j < IQR_LONG - 1:
            assert resc[s.session_date] == R0  # pre-long-window head: not rescorable
        else:
            # past the long window, same window == stored codes exactly
            assert resc[s.session_date] == stored[s.session_date]
    for s in invalid:
        assert resc[s.session_date] == R0
