import math
import random

from sngw_trader.indicators.kalman_spread import KalmanSpreadConfig, KalmanSpreadFilter
from sngw_trader.indicators.pair_screening import (
    FormationStats,
    formation_stats,
    screen_pair,
    _half_life_hours,
)


def _seeded_pair(n=720, seed=7):
    rng = random.Random(seed)
    x, e = 30_000.0, 0.0
    y_px, x_px = [], []
    for _ in range(n):
        x *= math.exp(rng.gauss(0.0, 0.001))
        e = 0.9714 * e + rng.gauss(0.0, 0.001)
        y_px.append(x * math.exp(e))
        x_px.append(x)
    return y_px, x_px


def _stats(**overrides) -> FormationStats:
    kf = KalmanSpreadFilter(KalmanSpreadConfig(0.001, 0.0001, 10.0, 0.1))
    base = dict(
        x_center=10.0, x_scale=0.1, sigma_e=0.004, sigma_u=0.01, rho=0.70,
        hl_hours=24.0, hurst=0.40, beta_iqr_ratio=0.20, jump45_count=0,
        oneside_jump_count=0, filter=kf,
    )
    base.update(overrides)
    return FormationStats(**base)


def test_formation_stats_recovers_relationship():
    y_px, x_px = _seeded_pair()
    stats = formation_stats(y_px, x_px, r=0.001, delta=0.0001)
    assert stats.x_center > 0 and stats.x_scale > 0
    assert stats.rho < 0.92          # noise keeps correlation inside the hard band
    assert 10.0 < stats.hl_hours < 60.0   # AR(1) phi=0.9714 -> HL ~ 24h
    assert stats.sigma_e > 0 and stats.sigma_u > 0
    assert stats.jump45_count == 0   # seeded shocks stay below 4.5 sigma


def test_half_life_matches_ar1_coefficient():
    rng = random.Random(3)
    e, series = 0.0, []
    for _ in range(4000):
        e = 0.5 * e + rng.gauss(0.0, 0.001)
        series.append(e)
    hl = _half_life_hours(series[1:])
    assert 0.5 < hl < 2.5            # phi=0.5 -> HL ~ 1h


def test_screen_pair_passes_clean_pair():
    d = screen_pair(_stats(), "FACTOR", funding_corr=0.05, funding_one_sided_days=2)
    assert d.passed and d.soft_score >= 60
    d = screen_pair(_stats(sigma_e=0.005), "PEER", funding_corr=0.05, funding_one_sided_days=2)
    assert d.passed


def test_screen_pair_hard_gate_reasons():
    cases = [
        (dict(rho=0.95), "RHO_OUT_OF_BAND"),
        (dict(rho=0.40), "RHO_OUT_OF_BAND"),
        (dict(hl_hours=2.0), "HALF_LIFE_OUT_OF_BAND"),
        (dict(hl_hours=300.0), "HALF_LIFE_OUT_OF_BAND"),
        (dict(hurst=0.55), "HURST_NOT_MR"),
        (dict(beta_iqr_ratio=0.5), "BETA_UNSTABLE"),
        (dict(jump45_count=3), "TOO_MANY_JUMPS"),
        (dict(oneside_jump_count=3), "TOO_MANY_ONESIDE_JUMPS"),
        (dict(sigma_e=0.001), "COST_MULTIPLE"),
    ]
    for overrides, reason in cases:
        d = screen_pair(_stats(**overrides), "FACTOR", funding_corr=0.05, funding_one_sided_days=2)
        assert not d.passed and reason in d.reasons, overrides


def test_screen_pair_funding_gates():
    d = screen_pair(_stats(), "FACTOR", funding_corr=0.5, funding_one_sided_days=2)
    assert not d.passed and "FUNDING_RESID_COUPLED" in d.reasons
    d = screen_pair(_stats(), "FACTOR", funding_corr=0.05, funding_one_sided_days=9)
    assert not d.passed and "FUNDING_ONE_SIDED" in d.reasons


def test_screen_pair_cost_multiplier_stress_fails():
    d = screen_pair(_stats(sigma_e=0.004), "FACTOR", funding_corr=0.05, funding_one_sided_days=2, cost_multiplier=2.0)
    assert not d.passed and "COST_MULTIPLE" in d.reasons


def test_screen_pair_soft_score_components():
    weak = _stats(hl_hours=150.0, hurst=0.46, beta_iqr_ratio=0.3, sigma_u=0.01)
    d = screen_pair(weak, "FACTOR", funding_corr=0.2, funding_one_sided_days=4)
    assert d.reasons == () and d.passed is (d.soft_score >= 60)
