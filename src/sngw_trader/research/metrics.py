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