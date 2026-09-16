# S05 Execution and Recovery Design

- 상태: `DESIGN_REVIEW`
- 범위: 순수 양다리 실행 상태 전이, timeout/rollback, reconciliation, restart gate
- 제외: Nautilus strategy callback 연결, OKX 주문 전송, 외부 뉴스 이벤트 판정

## Context

S05 has no existing Kalman strategy execution flow to extend. The first
implementation therefore keeps the execution contract independent of Nautilus
and the exchange. A later strategy adapter can translate the returned action
intents to official Nautilus order APIs without moving execution rules into a
runner or creating a custom event loop.

## Module Boundary

Add:

- `src/sngw_trader/indicators/execution_recovery.py`
- `tests/test_indicators/test_execution_recovery.py`

The module uses frozen dataclasses and pure transition/reconciliation
functions. It does not import Nautilus, call an exchange, persist files, or
submit/cancel orders.

## State Model

`ExecutionState` contains:

- pair phase and both leg statuses
- expected and filled quantity for each leg
- pending order identity and deadline
- entry beta
- cooldown deadline
- transition counters
- persisted Kalman state
- recovery gate status

The normal entry path is:

```text
IDLE -> ENTRY_PENDING -> ENTRY_PARTIAL -> OPEN
```

The normal exit path is:

```text
OPEN -> EXIT_PENDING -> EXIT_PARTIAL -> CLOSED
```

`FLATTENING` is used whenever an exposure exists but the intended two-leg
execution cannot continue. `BLOCKED` prevents new entries after failed
reconciliation or recovery.

## Events and Transitions

- `begin_entry` records both target quantities and a leg timeout deadline.
- `fill` accumulates quantity by fill identity. Duplicate callbacks are
  ignored. Both legs at target quantity transition to `OPEN`; otherwise the
  state remains partial.
- A timeout before the deadline returns `TIMEOUT_NOT_DUE` and leaves the state
  unchanged.
- A timeout after the deadline with no exposure returns to `IDLE` and emits
  `CANCEL_UNFILLED`.
- A timeout after the deadline with any exposure transitions to `FLATTENING`
  and emits `CANCEL_UNFILLED` and `FLATTEN_FILLED` intents.
- Order failure uses the same rollback rule as a post-deadline timeout.
- `emergency_flatten` immediately transitions to `FLATTENING`. After all
  flatten quantities are confirmed, the state becomes `CLOSED` and records
  cooldown.
- Execution observations carry fill price and executable reference price.
  Slippage is `abs(fill_price - reference_price) / reference_price * 10_000`.
  A fill beyond `max_slippage_bps` stops new execution and routes existing
  exposure to flattening.

Transitions return action intents, not exchange operations:

- `SUBMIT`
- `CANCEL_UNFILLED`
- `FLATTEN_FILLED`
- `BLOCK_ENTRIES`
- `ENABLE_ENTRIES`

## Reconciliation and Restart Gate

`reconcile(expected, observed)` compares:

- expected leg positions against portfolio positions
- persisted pending orders against cache orders
- unexpected positions and orders

An exact match returns `RECONCILED` and `ENABLE_ENTRIES`. Missing positions,
quantity mismatches, unexpected positions, and pending-order mismatches return
`BLOCKED` with a reason code. Any exposure requiring cleanup produces a
flatten action intent. New entries remain disabled until a later reconciliation
matches.

## Persistence Contract

The persisted state includes Kalman state, pair phase, entry beta, cooldown
deadline, counters, leg status, pending order identity, and recovery gate.
Deserialized values are validated before becoming active state. Persistence
itself is outside this module; the caller owns serialization and storage.

## Error and Safety Rules

- No unmanaged one-leg exposure may remain after its configured deadline.
- Invalid quantities, missing leg identity, duplicate fill identity, and
  invalid slippage inputs fail closed with a reason code.
- A recovery failure blocks new entries rather than guessing whether the
  exchange and local state agree.
- Backtest callers may use the same transitions with a documented fill model;
  live callers must supply executable bid/ask references and official order
  callbacks.

## Test Contract

Focused tests will cover:

- full fill and partial fill
- timeout before and after deadline
- rollback with and without exposure
- emergency flatten
- slippage rejection
- duplicate fill callback
- matching, missing, and unexpected reconciliation
- failed recovery entry block
- persisted state round-trip

## Implementation Handoff

- Previous stage: [S04 Portfolio Risk](2026-09-15-kalman-mr-s04-portfolio-risk.md)
- Existing stage specification: [S05](2026-09-15-kalman-mr-s05-execution-recovery.md)
- Next stage: [S06 Event Gate](2026-09-15-kalman-mr-s06-event-gate.md)
