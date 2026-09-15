"""Frozen experiment constants for the macro-proxy regime study.

Fix before running. Do not tune after seeing results. z/window/label constants
belong to indicators, not here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RegimeExperimentConfig:
    MIN_REGIME_SESSIONS: int = 40
    MIN_REGIME_SHARE: float = 0.05
    FWD_HORIZONS: tuple[int, ...] = (5, 20)
    DEV_END: str = "2015-12-31"
    VALIDATION_END: str = "2019-12-31"
    TEST_START: str = "2020-01-01"
    BOOTSTRAP_ITERS: int = 1000
    BOOTSTRAP_SEED: int = 42
    MIN_AVG_INVESTED: float = 0.20
    MIN_STAGE1_TRADES: int = 30
    MOMENTUM_LOOKBACK: int = 20
    TOP_K: int = 3
    ONE_WAY_FEE: float = 0.0005
    STARTING_BALANCE: str = "1_000_000 USD"


DEFAULT_REGIME_CONFIG = RegimeExperimentConfig()
