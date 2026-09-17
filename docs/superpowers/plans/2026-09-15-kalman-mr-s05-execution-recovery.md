# S05 Execution and Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make two-leg execution and restart recovery fail-safe without creating a custom exchange runner.

**Architecture:** Keep order orchestration in the Nautilus strategy/official execution APIs. Isolate pure leg-state transitions and reconciliation decisions so they can be tested without a live exchange.

**Tech Stack:** NautilusTrader official order APIs, existing cache/portfolio interfaces, pytest.

**Spec:** [S05 Execution and Recovery Specification](../specs/2026-09-15-kalman-mr-s05-execution-recovery.md)

## Global Constraints

- Do not use direct OKX REST/WebSocket calls.
- Do not create a custom event loop or runner.
- Do not allow an unmanaged one-leg position after timeout.
- Reconcile before enabling new entries after restart.

---

### Tasks

- [x] Add pure state-transition tests for full fill, partial fill, timeout, and emergency flattening.
- [x] Implement the minimal leg-state transition model used by the strategy.
- [x] Add reconciliation tests for matching, missing, and unexpected positions.
- [x] Connect the transitions to Nautilus order callbacks and cache/portfolio state. (Deferred by the design review — see [2026-09-17 design](../specs/2026-09-17-kalman-mr-s05-execution-recovery-design.md); Nautilus connection is excluded from S05.)
- [x] Add persisted state fields and restart gating.
- [x] Run focused execution/recovery tests and the S04 suite.

Execution was carried out per the [2026-09-17 design plan](2026-09-17-kalman-mr-s05-execution-recovery-design.md): pure module `src/sngw_trader/indicators/execution_recovery.py` + 54 tests, 187 indicators tests passing.

## Handoff

- [Index](../../../ideas/kalman_MR_spread/INDEX.md)
- [Next spec: S06](../specs/2026-09-15-kalman-mr-s06-event-gate.md)
