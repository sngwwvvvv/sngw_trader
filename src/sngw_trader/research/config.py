"""Walk-forward / Monte Carlo configuration dataclasses."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GridSpec:
    """External grid injection. The orchestrator knows no strategy classes."""

    strategy_path: str  # "pkg.module:ClassName"
    config_path: str  # "pkg.module:ConfigClassName"
    fixed: dict[str, object]
    grid: dict[str, list]


@dataclass(frozen=True)
class WalkForwardConfig:
    is_months: int = 6
    oos_months: int = 3
    holdout_months: int = 6
    warmup_days: int = 1
    min_trades: int = 30


@dataclass(frozen=True)
class MCConfig:
    n_sims: int = 1000
    seed: int = 42
    initial_capital: float = 10_000.0
    ruin_threshold: float = -0.5