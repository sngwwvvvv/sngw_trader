# S04 Portfolio Risk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add deterministic portfolio approval checks for shared asset exposure, gross exposure, margin, losses, and liquidation buffer.

**Architecture:** Consume rounded sizing from S03 and costs from S02. Keep risk approval independent from order submission so it can run identically in research and live modes.

**Tech Stack:** Python 3.12+, dataclasses, Decimal where monetary precision matters, pytest.

**Spec:** [S04 Portfolio Risk Specification](../specs/2026-09-15-kalman-mr-s04-portfolio-risk.md)

## Global Constraints

- Aggregate positions by underlying asset before checking limits.
- Use actual rounded quantities, not target notionals.
- Reject on missing equity, price, margin, or position data.
- Risk approval must not send, cancel, or modify orders.

---

### Tasks

- [x] Add failing tests for pair, asset, gross, and portfolio capacity checks.
- [x] Implement the immutable exposure snapshot and deterministic approval result.
- [x] Add failing tests for daily-loss, drawdown, and liquidation-buffer lockouts.
- [x] Implement the lockout gates and reason codes.
- [x] Integrate S02 cost and S03 rounded-sizing outputs through typed inputs.
- [x] Run focused risk tests and all preceding focused tests.

## Handoff

- [Index](../../../ideas/kalman_MR_spread/INDEX.md)
- [Next spec: S05](../specs/2026-09-15-kalman-mr-s05-execution-recovery.md)
