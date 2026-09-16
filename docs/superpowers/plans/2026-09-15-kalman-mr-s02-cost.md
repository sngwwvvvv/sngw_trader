# S02 Cost Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the current pair-level cost shortcut with per-leg fees, spread, and signed funding.

**Architecture:** Keep deterministic cost arithmetic in a pure module. Data loading remains outside the calculator; callers pass normalized fills and funding observations.

**Tech Stack:** Python 3.12+, standard library, pytest.

**Spec:** [S02 Cost Model Specification](../specs/2026-09-15-kalman-mr-s02-cost.md)

## Global Constraints

- Four fills represent entry and exit of both legs.
- Half-spread is charged per instrument fill.
- Funding is signed and timestamped.
- No exchange or network calls belong in the cost calculator.

---

### Tasks

- [x] Add failing unit tests for four-fill fee arithmetic and per-fill half-spread arithmetic.
- [x] Implement the pure fee and spread calculator.
- [x] Add failing tests for signed multi-period funding and missing-data rejection.
- [x] Implement funding aggregation and total-cost output.
- [x] Update `01_Assumptions`, S02 spec, and the main strategy spec with the same definitions.
- [x] Run the focused cost tests and the repository test suite.

## Handoff

- [Index](../../../ideas/kalman_MR_spread/INDEX.md)
- [Next spec: S03](../specs/2026-09-15-kalman-mr-s03-contract-sizing.md)
