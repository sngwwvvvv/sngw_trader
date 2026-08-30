"""Trade bootstrap Monte Carlo. Pure numpy, no nautilus."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from sngw_trader.research.config import MCConfig


@dataclass(frozen=True)
class MCResult:
    n_sims: int
    n_trades: int
    final_return_p5: float
    final_return_p50: float
    final_return_p95: float
    mdd_p50: float
    mdd_p95: float
    mdd_p99: float
    ruin_prob: float
    worst_final_return: float


def bootstrap_trades(pnls: list[float], cfg: MCConfig) -> MCResult:
    arr = np.asarray(pnls, dtype=float)
    if arr.size < 2:
        raise ValueError("need at least 2 closed trades for bootstrap")
    rng = np.random.default_rng(cfg.seed)
    finals = np.empty(cfg.n_sims)
    mdds = np.empty(cfg.n_sims)
    ruin = 0
    for i in range(cfg.n_sims):
        sample = rng.choice(arr, size=arr.size, replace=True)
        equity = cfg.initial_capital + np.cumsum(sample)
        peak = np.maximum.accumulate(equity)
        mdds[i] = float(np.max((peak - equity) / peak))
        finals[i] = equity[-1] / cfg.initial_capital - 1.0
        if float(equity.min()) <= cfg.initial_capital * (1.0 + cfg.ruin_threshold):
            ruin += 1
    return MCResult(
        n_sims=cfg.n_sims,
        n_trades=int(arr.size),
        final_return_p5=float(np.percentile(finals, 5)),
        final_return_p50=float(np.percentile(finals, 50)),
        final_return_p95=float(np.percentile(finals, 95)),
        mdd_p50=float(np.percentile(mdds, 50)),
        mdd_p95=float(np.percentile(mdds, 95)),
        mdd_p99=float(np.percentile(mdds, 99)),
        ruin_prob=ruin / cfg.n_sims,
        worst_final_return=float(finals.min()),
    )


def _selfcheck() -> None:
    r = bootstrap_trades([10.0] * 20, MCConfig(n_sims=500, seed=1))
    assert r.ruin_prob == 0.0 and r.mdd_p50 == 0.0, r
    print("ok")


if __name__ == "__main__":
    _selfcheck()