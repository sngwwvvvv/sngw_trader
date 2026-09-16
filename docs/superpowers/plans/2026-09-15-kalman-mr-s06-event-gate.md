# S06 Event Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a deterministic, fail-closed event gate without putting external I/O in the strategy.

**Architecture:** Define a small event record and gate evaluator. An actor, catalog, or manual control supplies records; the strategy consumes the evaluated result and delegates exits to S05.

**Tech Stack:** Python 3.12+, dataclasses, existing Nautilus actor/strategy boundaries, pytest.

**Spec:** [S06 Event Gate Specification](../specs/2026-09-15-kalman-mr-s06-event-gate.md)

## Global Constraints

- No `requests`, `httpx`, `websocket-client`, or direct exchange client in the strategy.
- Stale or missing event state blocks new entries.
- Open-position events use the tested S05 exit path.
- Event timestamps are UTC and deterministic in tests.

---

### Tasks

- [x] Add failing tests for event scope, expiry, stale state, and fail-closed behavior.
- [x] Implement the typed event record and pure gate evaluator.
- [x] Add tests for open-position exit decisions and manual Kill Switch behavior.
- [x] Integrate the gate at the strategy boundary without external I/O. (Satisfied by the pure module + spec contract — strategy adapter integration remains excluded, same ruling as S05; see [2026-09-17 design](../specs/2026-09-17-kalman-mr-s06-event-gate-design.md).)
- [x] Run focused event tests and the S05 suite.

Execution was carried out per the [2026-09-17 design plan](2026-09-17-kalman-mr-s06-event-gate-design.md): pure module `src/sngw_trader/indicators/event_gate.py` + 34 tests, 225 indicators tests passing.

## Handoff

- [Index](../../../ideas/kalman_MR_spread/INDEX.md)
- [Next spec: S07](../specs/2026-09-15-kalman-mr-s07-validation.md)
