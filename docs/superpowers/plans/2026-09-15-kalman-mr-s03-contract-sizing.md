# S03 Contract Sizing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Convert pair notionals and beta into valid OKX contract quantities without losing the hedge constraint during rounding.

**Architecture:** Separate pure quantity arithmetic from Nautilus instrument adapters. The adapter supplies metadata; the calculator returns quantities, actual notionals, and hedge error.

**Tech Stack:** Python 3.12+, `Decimal` for contract arithmetic, pytest.

**Spec:** [S03 Contract Sizing Specification](../specs/2026-09-15-kalman-mr-s03-contract-sizing.md)

## Global Constraints

- Use exchange instrument metadata, not hard-coded contract values.
- Reject beta `<= 0`.
- Recalculate notional after lot-size rounding.
- Do not submit orders from the sizing module.

---

### Tasks

- [x] Add failing tests for contract-value conversion, lot-size rounding, minimum size, and beta rejection.
- [x] Implement the pure `Decimal` sizing calculation.
- [x] Add tests for actual gross notional and hedge error after rounding.
- [x] Add the instrument metadata adapter at the boundary used by the later strategy.
- [x] Run focused sizing tests and the previous S02 tests.
- [x] Link the returned sizing contract from S04 risk design.

## Handoff

- [Index](../../../ideas/kalman_MR_spread/INDEX.md)
- [Next spec: S04](../specs/2026-09-15-kalman-mr-s04-portfolio-risk.md)
