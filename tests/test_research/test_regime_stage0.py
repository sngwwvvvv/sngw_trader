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
