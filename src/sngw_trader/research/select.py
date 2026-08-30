"""Grid selection: min-trades gate + neighbor-median smoothing (plateau)."""

from __future__ import annotations

import statistics
from itertools import product

import numpy as np


def param_keys(grid: dict[str, list]) -> list[tuple]:
    axes = sorted(grid)
    return [tuple(c) for c in product(*(grid[a] for a in axes))]


def neighbors(key: tuple, grid: dict[str, list]) -> list[tuple]:
    axes = sorted(grid)
    out: list[tuple] = []
    for i, axis in enumerate(axes):
        vals = grid[axis]
        pos = vals.index(key[i])
        for j in (pos - 1, pos + 1):
            if 0 <= j < len(vals):
                out.append(key[:i] + (vals[j],) + key[i + 1 :])
    return out


def sharpe_from_trades(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    arr = np.asarray(returns, dtype=float)
    std = arr.std(ddof=1)
    if std == 0:
        return 0.0
    return float(arr.mean() / std)


def select_best(
    grid: dict[str, list],
    sharpe_by_key: dict[tuple, float],
    trades_by_key: dict[tuple, int],
    min_trades: int = 30,
) -> tuple | None:
    """Pick the combo whose (self + neighbor) Sharpe median is highest.

    외톨이 파라미터(이웃은 다 망하고 자기만 급조합)는 이웃 중앙값에 깎여 탈락한다.
    """
    eligible = [k for k in param_keys(grid) if trades_by_key.get(k, 0) >= min_trades]
    if not eligible:
        return None

    def smoothed(key: tuple) -> float:
        vals = [sharpe_by_key[key]]
        vals += [sharpe_by_key[n] for n in neighbors(key, grid) if n in eligible]
        return statistics.median(vals)

    return max(eligible, key=smoothed)