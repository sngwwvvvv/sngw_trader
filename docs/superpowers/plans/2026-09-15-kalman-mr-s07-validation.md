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

## Session handoff — 2026-09-17 (S07 Phase 1 executed)

- Design: docs/superpowers/specs/2026-09-17-kalman-mr-s07-validation-design.md (Phase 1/2 split approved).
- Phase 1 plan: docs/superpowers/plans/2026-09-17-kalman-mr-s07-phase1-strategy-runner.md — all 6 tasks implemented, per-task reviews + final whole-branch review clean after one fix wave (ct_val accounting, per-symbol bar labels, stress multiplier, mutex at execution, flatten rejection retry, funding accrual during exit-in-flight).
- Deliverables: src/sngw_trader/indicators/pair_screening.py, src/sngw_trader/strategies/kalman_spread.py, catalog_writer 1H mark + funding history, src/sngw_trader/research/kalman_walkforward.py, tests (test_pair_screening, test_kalman_spread_strategy, test_kalman_runner, test_kalman_smoke E2E green on synthetic catalog). Full suite 543 passed.
- Key rulings: soft 2σ/c score uses sigma_u (hard gate stays sigma_e, 2.5c unchanged); seeded-pair generator x-vol 0.001; exit-side derived from kept position fields across window reset; rehedge fills bypass S05 with guards; nautilus 1.231 rejects MARK bars for execution -> smoke uses bar_execution=False + QuoteTick feed.
- Phase 2 open items: (1) data download needs QuoteTick feed or LAST-bar venue redesign for execution (nautilus MARK-bar limitation); (2) curated event stream JSON for the 12-month window; (3) screening funding inputs (funding_corr, one-sided days) currently neutral 0 — wire from funding history; (4) data-gap rule (2h consecutive missing -> pair window invalid) not implemented — defer to Phase 2 data validation; (5) stress runs: baseline / 1.5x / 2x / slippage / partial fills via runner config; (6) aggregation + final verdict (research-only / paper-ready / live-ineligible), INDEX.md + main spec update.
