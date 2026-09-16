# S07 Walk-Forward Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Produce reproducible evidence that the complete Kalman spread strategy is causal, cost-aware, and robust enough for its declared operating status.

**Architecture:** Reuse `research/` orchestration and `BacktestNode` with catalog data. The validation layer runs windows and aggregates results; it does not contain trading conditions.

**Tech Stack:** NautilusTrader `BacktestNode`, `ParquetDataCatalog`, existing research modules, pytest.

**Spec:** [S07 Walk-Forward Validation Specification](../specs/2026-09-15-kalman-mr-s07-validation.md)

## Global Constraints

- Use `BacktestNode` and `ParquetDataCatalog` for backtests.
- Do not use a smoother or future data in a decision.
- Include actual S02 cost and S03/S04 exposure outputs.
- Keep orchestration in `research/`; keep trading conditions in the strategy.

---

### Tasks

- [ ] Add tests for formation/trading window boundaries and timestamp causality.
- [ ] Implement the walk-forward window configuration for the 30-day formation and 10-day trading windows.
- [ ] Add tests for cost and fill stress scenarios.
- [ ] Run baseline, 1.5x-cost, 2x-cost, slippage, and partial-fill validation cases.
- [ ] Add reproducible result aggregation for returns, drawdown, turnover, cost share, and exposure concentration.
- [ ] Review the result against S07 acceptance criteria.
- [ ] Update `INDEX.md` and the main strategy spec with the final research status.

## Handoff

- [Index](../../../ideas/kalman_MR_spread/INDEX.md)
- [Main strategy spec](../../../ideas/kalman_MR_spread/OKX_Kalman_MR_Spec.md)
