# S06 Event Gate Specification

- 상태: `PENDING`
- 범위: 이벤트 입력 계약, fail-closed 게이트, 수동 Kill Switch
- 제외: 뉴스 공급자 자체 구현과 외부 API 호출

## Document Links

- [Work index](../../../ideas/kalman_MR_spread/INDEX.md)
- [Previous: S05](2026-09-15-kalman-mr-s05-execution-recovery.md)
- [Plan](../plans/2026-09-15-kalman-mr-s06-event-gate.md)
- [Next: S07](2026-09-15-kalman-mr-s07-validation.md)

## Required Decisions

- Events enter through a typed actor, catalog, or manual control input; the strategy performs no external I/O.
- Event records contain symbol scope, event type, effective time, expiry, and source status.
- Missing or stale event state fails closed for new entries.
- An event affecting an open position triggers the S05 exit/recovery path.
- Backtest event data uses the same timestamped contract as live data.

## Acceptance Criteria

- Tests cover active, expired, stale, symbol-specific, global, and missing event states.
- Event gate decisions are deterministic for the same timestamp and input set.
- No event gate path imports an HTTP or exchange client.

## Stage Handoff

- Previous: [S05](2026-09-15-kalman-mr-s05-execution-recovery.md)
- Next: [S07](2026-09-15-kalman-mr-s07-validation.md)
