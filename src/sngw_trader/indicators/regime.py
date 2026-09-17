"""Robust-z based regime scoring: stress/growth scores and R0-R4 labels.

Pure numpy. Constants are replicated from research.regime_config
(indicators must not import research).
"""

from __future__ import annotations

import numpy as np

R0, R1, R2, R3, R4 = 0, 1, 2, 3, 4

Z0 = 0.5
MEDIAN_WINDOW = 60
IQR_LONG = 252
IQR_TO_SIGMA = 0.7413


def robust_z(
    x: np.ndarray,
    median_window: int = MEDIAN_WINDOW,
    iqr_long: int = IQR_LONG,
    iqr_to_sigma: float = IQR_TO_SIGMA,
) -> np.ndarray:
    """Rolling robust z: (x - median_w) / (max(iqr_w, iqr_L/3) * iqr_to_sigma).

    NaN before the long IQR window fills (t < iqr_long - 1) or when the
    denominator is zero.
    """
    x = np.asarray(x, dtype=float)
    n = x.size
    z = np.full(n, np.nan)
    for t in range(n):
        if t < iqr_long - 1:
            continue
        med_w = x[max(0, t - median_window + 1) : t + 1]
        long_w = x[t - iqr_long + 1 : t + 1]
        med = np.median(med_w)
        iqr_w = np.percentile(med_w, 75) - np.percentile(med_w, 25)
        iqr_l = np.percentile(long_w, 75) - np.percentile(long_w, 25)
        denom = max(iqr_w, iqr_l / 3.0) * iqr_to_sigma
        if denom == 0 or np.isnan(denom):
            continue
        z[t] = (x[t] - med) / denom
    return z


def stress_score(oas_z: np.ndarray, vix_vxv_z: np.ndarray) -> np.ndarray:
    return 0.5 * np.asarray(oas_z, dtype=float) + 0.5 * np.asarray(vix_vxv_z, dtype=float)


def growth_score(copper_gold_z: np.ndarray) -> np.ndarray:
    return np.asarray(copper_gold_z, dtype=float)


def regime_code(stress: float, growth: float, z0: float = Z0) -> int | None:
    if np.isnan(stress) or np.isnan(growth):
        return None
    if abs(stress) < z0 and abs(growth) < z0:
        return R0
    s_plus = stress > 0
    g_plus = growth > 0
    if s_plus and g_plus:
        return R2
    if not s_plus and g_plus:
        return R1
    if not s_plus and not g_plus:
        return R3
    return R4


def label_series(stress: np.ndarray, growth: np.ndarray, z0: float = Z0) -> np.ndarray:
    stress = np.asarray(stress, dtype=float)
    growth = np.asarray(growth, dtype=float)
    out = np.full(stress.shape, -1, dtype=int)
    for i in range(stress.size):
        code = regime_code(stress[i], growth[i], z0)
        if code is not None:
            out[i] = code
    return out
