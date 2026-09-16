# S02 Cost Model Specification

- 상태: `DONE`
- 범위: 두 레그의 수수료, bid/ask spread, funding을 일관된 USDT 비용으로 계산
- 제외: 계약 수량 변환, 포트폴리오 한도, 주문 실행

## Document Links

- [Work index](../../../ideas/kalman_MR_spread/INDEX.md)
- [Previous: S01](2026-09-15-kalman-mr-s01-model.md)
- [Plan](../plans/2026-09-15-kalman-mr-s02-cost.md)
- [Next: S03](2026-09-15-kalman-mr-s03-contract-sizing.md)

## Required Decisions

- Four order fills are charged for a complete two-leg round trip.
- Half-spread is defined per instrument and per fill.
- Funding is calculated per leg, per funding timestamp, using actual signed quantity and direction.
- Cost comparisons convert log-spread movement to the same notional-weighted USDT basis used by sizing.
- Default assumptions in `01_Assumptions` must not be confused with realized costs.

## Acceptance Criteria

- Factor and peer examples reproduce the corrected per-leg fee and spread arithmetic.
- Funding can be positive or negative for each leg.
- Missing cost data fails closed for a backtest sample instead of silently becoming zero.
- Unit tests cover zero, positive, negative, and multi-period funding.

## Implemented Contract

- `src/sngw_trader/indicators/cost_model.py` is a pure module with no
  exchange, network, or file I/O.
- `Fill` receives a leg name, signed quantity, price, fee rate, and per-fill
  half-spread rate. Fee and half-spread are charged from
  `abs(signed_quantity) * price` for every fill.
- `FundingObservation` receives the signed quantity and mark price at each
  funding timestamp. Funding cost is
  `funding_rate * signed_quantity * mark_price`, so positive and negative
  rates and long and short positions retain their signs.
- `calculate_cost` returns fee, spread, funding, and total USDT values in a
  frozen `CostBreakdown`.
- `calculate_cost` receives the expected funding timestamp set and requires
  exactly one observation for every traded leg at every timestamp.
- A zero funding period is represented by an explicit observation with
  `funding_rate=0.0`; missing or duplicate observations raise `ValueError`
  instead of becoming zero or being double-counted.

## Stage Handoff

- Previous: [S01](2026-09-15-kalman-mr-s01-model.md)
- Next: [S03](2026-09-15-kalman-mr-s03-contract-sizing.md)
