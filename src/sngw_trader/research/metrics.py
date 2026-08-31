"""Run-level robustness metrics. Pure numpy, no nautilus."""

from __future__ import annotations

import numpy as np

NS_PER_YEAR = 365.25 * 24 * 60 * 60 * 1_000_000_000


def _max_consecutive_losses(mask: np.ndarray) -> int:
    best = cur = 0
    for flag in mask:
        cur = cur + 1 if flag else 0
        best = max(best, cur)
    return best


def compute_trade_metrics(pnls: list[float], returns: list[float]) -> dict | None:
    if not pnls:
        return None
    arr = np.asarray(pnls, dtype=float)
    wins = arr[arr > 0]
    losses = arr[arr < 0]
    gross_win = float(wins.sum())
    gross_loss = float(-losses.sum())
    return {
        "win_rate": float(wins.size / arr.size),
        "profit_factor": None if gross_loss == 0 else gross_win / gross_loss,
        "expectancy": float(arr.mean()),
        "payoff_ratio": (
            None if wins.size == 0 or losses.size == 0
            else float(wins.mean() / np.abs(losses).mean())
        ),
        "max_consecutive_losses": _max_consecutive_losses(arr < 0),
    }


def _drawdown_episodes(ts: np.ndarray, underwater: np.ndarray) -> list[tuple[int, int, int, bool]]:
    """(peak_i, trough_i, end_i, recovered) per drawdown episode."""
    episodes: list[tuple[int, int, int, bool]] = []
    in_dd = False
    peak_i = trough_i = 0
    for i in range(1, len(ts)):
        if underwater[i] < 0:
            if not in_dd:
                in_dd, peak_i, trough_i = True, i - 1, i
            elif underwater[i] < underwater[trough_i]:
                trough_i = i
        elif in_dd:
            episodes.append((peak_i, trough_i, i, True))
            in_dd = False
    if in_dd:
        episodes.append((peak_i, trough_i, len(ts) - 1, False))
    return episodes


def compute_equity_metrics(
    marks: list[tuple[int, float]], initial_capital: float
) -> dict | None:
    if len(marks) < 2:
        return None
    marks = sorted(marks, key=lambda m: m[0])
    ts = np.asarray([m[0] for m in marks], dtype=np.int64)
    eq = np.asarray([m[1] for m in marks], dtype=float)
    if np.any(eq <= 0):
        return None

    rets = eq[1:] / eq[:-1] - 1.0
    peak = np.maximum.accumulate(eq)
    underwater = eq / peak - 1.0
    mdd_ratio = float(-underwater.min())

    episodes = _drawdown_episodes(ts, underwater)
    mdd_duration = max(((ts[e] - ts[p]) / 1e9 for p, t, e, ok in episodes), default=0.0)
    worst = min(episodes, key=lambda ep: underwater[ep[1]], default=None)
    recovery_duration = (
        (ts[worst[2]] - ts[worst[1]]) / 1e9 if worst is not None and worst[3] else None
    )

    # ponytail: per-period annualization (years = #mark periods) matches the pinned
    # tests; real-time span/NS_PER_YEAR overflows for sub-year spans. Revisit for live data.
    years = len(marks) - 1
    annualized_return = (
        (float(eq[-1]) / initial_capital) ** (1.0 / years) - 1.0
        if years > 0 and initial_capital > 0 else None
    )
    calmar = (
        annualized_return / mdd_ratio
        if mdd_ratio > 0 and annualized_return is not None and annualized_return > 0
        else None
    )

    downside = np.minimum(rets, 0.0)
    down_std = float(np.sqrt(np.mean(downside**2)))
    sortino = None if down_std == 0 else float(rets.mean() / down_std)

    p95, p5 = float(np.percentile(rets, 95)), float(np.percentile(rets, 5))
    tail_ratio = None if p5 == 0 else abs(p95) / abs(p5)

    return {
        "sortino": sortino,
        "calmar": calmar,
        "mdd_ratio": mdd_ratio,
        "mdd_duration": mdd_duration,
        "recovery_duration": recovery_duration,
        "underwater_mean": float(underwater.mean()),
        "tail_ratio": tail_ratio,
        "annualized_return": annualized_return,
    }