# S05 Execution and Recovery Specification

- 상태: `PENDING`
- 범위: 양다리 주문, 부분 체결, 슬리피지, 재시작 복구
- 제외: 외부 뉴스 이벤트 판정

## Document Links

- [Work index](../../../ideas/kalman_MR_spread/INDEX.md)
- [Previous: S04](2026-09-15-kalman-mr-s04-portfolio-risk.md)
- [Plan](../plans/2026-09-15-kalman-mr-s05-execution-recovery.md)
- [Next: S06](2026-09-15-kalman-mr-s06-event-gate.md)

## Required Decisions

- Signal prices are mark prices; execution uses executable bid/ask or a conservative fill model.
- Entry, exit, rehedge, and emergency flattening have explicit leg timeout and maximum slippage rules.
- A one-leg fill cannot remain unmanaged beyond the configured timeout.
- Position mode, client-order identity, reduce-only behavior, and reconciliation are explicit.
- Persisted state includes Kalman state, pair state, entry beta, cooldown, counters, and pending leg status.
- Restart recovery reconciles cache, portfolio, and exchange positions before allowing new orders.

## Acceptance Criteria

- Tests cover full fill, partial fill, timeout, rollback, emergency flatten, and restart reconciliation.
- A failed recovery path blocks new entries and exposes a reason code.
- Backtest execution assumptions are documented separately from live behavior.

## Stage Handoff

- Previous: [S04](2026-09-15-kalman-mr-s04-portfolio-risk.md)
- Next: [S06](2026-09-15-kalman-mr-s06-event-gate.md)
