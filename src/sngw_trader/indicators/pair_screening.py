"""Formation-window statistics and Hard/Soft screening gates (S07 Phase 1).

Pure math over completed hourly mark closes. No nautilus imports, no I/O.
Gates follow OKX_Kalman_MR_Spec §6. Liquidity/OI/depth gates are fixed
assumptions in v1 validation (NOT_ASSESSED); ADF is omitted and Hurst < 0.48
stays the primary mean-reversion gate. Both ceilings are reported with the
S07 results.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from sngw_trader.indicators.kalman_spread import KalmanSpreadConfig, KalmanSpreadFilter

BOOK_ROUND_TRIP_COST = {"FACTOR": 0.0030, "PEER": 0.0036}
COST_MULTIPLE_MIN = 2.5
RHO_MIN, RHO_MAX = 0.55, 0.92
HL_MIN_HOURS, HL_MAX_HOURS = 6.0, 240.0
HURST_MAX = 0.48
BETA_IQR_MAX = 0.40
JUMP45_MAX = 2
ONESIDE_JUMP_MAX = 2
FUNDING_CORR_MAX = 0.35
FUNDING_ONESIDED_MAX_DAYS = 7

_RS_CHUNK_SIZES = (8, 16, 32, 64, 128, 256)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _pstdev(values: Sequence[float]) -> float:
    m = _mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / len(values))


def _pearson(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or len(a) < 2:
        return 0.0
    ma, mb = _mean(a), _mean(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return 0.0
    return cov / math.sqrt(va * vb)


def _iqr(values: Sequence[float]) -> float:
    ordered = sorted(values)
    def _pct(p):
        idx = p * (len(ordered) - 1)
        lo, hi = int(idx), min(int(idx) + 1, len(ordered) - 1)
        frac = idx - lo
        return ordered[lo] * (1 - frac) + ordered[hi] * frac
    return _pct(0.75) - _pct(0.25)


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if n % 2:
        return ordered[n // 2]
    return (ordered[n // 2 - 1] + ordered[n // 2]) / 2


def _half_life_hours(residuals: Sequence[float]) -> float:
    """AR(1) fit e_t = phi e_{t-1} + u_t; HL = ln 0.5 / ln|phi| (spec §6.1)."""
    if len(residuals) < 3:
        return math.inf
    num = sum(a * b for a, b in zip(residuals, residuals[1:]))
    den = sum(a * a for a in residuals[:-1])
    if den <= 0:
        return math.inf
    phi = num / den
    if abs(phi) >= 1.0 or phi <= 0.0:
        return math.inf
    return math.log(0.5) / math.log(phi)


def _hurst_rs(series: Sequence[float]) -> float:
    """Rescaled-range Hurst estimate; 0.5 fallback when data is too short."""
    n = len(series)
    if n < 16:
        return 0.5
    xs, ys = [], []
    for size in _RS_CHUNK_SIZES:
        if size > n // 2:
            break
        ratios = []
        for start in range(0, n - size + 1, size):
            chunk = series[start : start + size]
            m = _mean(chunk)
            cum = mx = mn = 0.0
            ss = 0.0
            for v in chunk:
                d = v - m
                cum += d
                ss += d * d
                mx = max(mx, cum)
                mn = min(mn, cum)
            rng = mx - mn
            std = math.sqrt(ss / size)
            if std > 0 and rng > 0:
                ratios.append(math.log10(rng / std))
        if ratios:
            xs.append(math.log10(size))
            ys.append(_mean(ratios))
    if len(xs) < 2:
        return 0.5
    mx, my = _mean(xs), _mean(ys)
    denom = sum((x - mx) ** 2 for x in xs)
    if denom <= 0:
        return 0.5
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom


@dataclass(frozen=True)
class ScreenDecision:
    passed: bool
    soft_score: int
    reasons: tuple[str, ...]


@dataclass
class FormationStats:
    x_center: float
    x_scale: float
    sigma_e: float
    sigma_u: float
    rho: float
    hl_hours: float
    hurst: float
    beta_iqr_ratio: float
    jump45_count: int
    oneside_jump_count: int
    filter: KalmanSpreadFilter


def formation_stats(
    y_marks: Sequence[float], x_marks: Sequence[float], r: float, delta: float
) -> FormationStats:
    """Replay the formation window through S01 and compute screening stats.

    Causality: only the supplied window is used; the returned filter has
    consumed exactly these bars and continues online during trading.
    """
    if len(y_marks) != len(x_marks) or len(y_marks) < 3:
        raise ValueError("formation window requires >=3 aligned (y, x) marks")
    y_log = [math.log(v) for v in y_marks]
    x_log = [math.log(v) for v in x_marks]
    x_center = _mean(x_log)
    x_scale = max(_pstdev(x_log), 1e-6)
    kf = KalmanSpreadFilter(KalmanSpreadConfig(r=r, delta=delta, x_center=x_center, x_scale=x_scale))
    e_path: list[float] = []
    z_path: list[float] = []
    b_path: list[float] = []
    for y, x in zip(y_marks, x_marks):
        res = kf.update(y, x)
        e_path.append(res.innovation)
        z_path.append(res.z_score)
        b_path.append(res.beta)
    core = e_path[1:]  # skip the lazy-initialization observation
    sigma_e = max(_pstdev(core), 1e-9)
    # Unit-notional hourly spread return r_t = dln y - beta_t * dln x.
    spread_returns = []
    for i in range(1, len(y_log)):
        spread_returns.append(
            (y_log[i] - y_log[i - 1]) - b_path[i] * (x_log[i] - x_log[i - 1])
        )
    sigma_u = max(_pstdev(spread_returns), 1e-9)
    dy = [y_log[i] - y_log[i - 1] for i in range(1, len(y_log))]
    dx = [x_log[i] - x_log[i - 1] for i in range(1, len(x_log))]
    beta_med = _median(b_path[1:])
    beta_iqr = _iqr(b_path[1:]) / abs(beta_med) if abs(beta_med) > 1e-12 else math.inf
    # One-sided 12h jump: |dln y| over 12h >= 20% while |dln x| <= 5%.
    oneside = 0
    for i in range(12, len(y_log)):
        if abs(y_log[i] - y_log[i - 12]) >= 0.20 and abs(x_log[i] - x_log[i - 12]) <= 0.05:
            oneside += 1
    return FormationStats(
        x_center=x_center,
        x_scale=x_scale,
        sigma_e=sigma_e,
        sigma_u=sigma_u,
        rho=_pearson(dy, dx),
        hl_hours=_half_life_hours(core),
        hurst=_hurst_rs(core),
        beta_iqr_ratio=beta_iqr,
        jump45_count=sum(1 for z in z_path[1:] if abs(z) > 4.5),
        oneside_jump_count=oneside,
        filter=kf,
    )


def screen_pair(
    stats: FormationStats,
    book: str,
    *,
    funding_corr: float,
    funding_one_sided_days: int,
    cost_multiplier: float = 1.0,
) -> ScreenDecision:
    """Apply spec §6.1 Hard gates and §6.2 Soft score. Liquidity gates are
    fixed assumptions in v1 (NOT_ASSESSED); ADF is omitted."""
    if book not in BOOK_ROUND_TRIP_COST:
        raise ValueError(f"unknown book {book!r}")
    reasons: list[str] = []
    if not RHO_MIN <= stats.rho <= RHO_MAX:
        reasons.append("RHO_OUT_OF_BAND")
    if not HL_MIN_HOURS <= stats.hl_hours <= HL_MAX_HOURS:
        reasons.append("HALF_LIFE_OUT_OF_BAND")
    if stats.hurst >= HURST_MAX:
        reasons.append("HURST_NOT_MR")
    if stats.beta_iqr_ratio >= BETA_IQR_MAX:
        reasons.append("BETA_UNSTABLE")
    if stats.jump45_count > JUMP45_MAX:
        reasons.append("TOO_MANY_JUMPS")
    if stats.oneside_jump_count > ONESIDE_JUMP_MAX:
        reasons.append("TOO_MANY_ONESIDE_JUMPS")
    if abs(funding_corr) > FUNDING_CORR_MAX:
        reasons.append("FUNDING_RESID_COUPLED")
    if funding_one_sided_days > FUNDING_ONESIDED_MAX_DAYS:
        reasons.append("FUNDING_ONE_SIDED")
    c = BOOK_ROUND_TRIP_COST[book] * cost_multiplier
    if 2.0 * stats.sigma_e <= COST_MULTIPLE_MIN * c:
        reasons.append("COST_MULTIPLE")
    score = 0
    score += 25 if 24.0 <= stats.hl_hours <= 96.0 else 0
    score += 20 if 2.0 * stats.sigma_u / c >= 5.0 else 0
    score += 15 if stats.hurst <= 0.35 else 0
    score += 15 if stats.beta_iqr_ratio <= 0.15 else 0
    score += 10 if stats.jump45_count == 0 else 0
    score += 10 if abs(funding_corr) <= 0.10 else 0
    score += 5  # depth: assumed pass in v1 validation
    return ScreenDecision(passed=not reasons and score >= 60, soft_score=score, reasons=tuple(reasons))
