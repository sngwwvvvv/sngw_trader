# S04 Portfolio Risk Specification

- 상태: `DONE`
- 범위: 페어별·자산별·전체 포트폴리오 노출과 손실 한도
- 제외: 주문 전송과 이벤트 데이터 수집

## Document Links

- [Work index](../../../ideas/kalman_MR_spread/INDEX.md)
- [Previous: S02](2026-09-15-kalman-mr-s02-cost.md)
- [Previous: S03](2026-09-15-kalman-mr-s03-contract-sizing.md)
- [Plan](../plans/2026-09-15-kalman-mr-s04-portfolio-risk.md)
- [Next: S05](2026-09-15-kalman-mr-s05-execution-recovery.md)

## Required Decisions

- Per-pair margin cap and total gross exposure are separate limits.
- Common BTC and other asset legs are aggregated before approval.
- Portfolio risk uses rounded actual quantities and S02 cost assumptions.
- Portfolio risk consumes S03 `PairSizing.gross_notional_usdt` and
  `PairSizing.hedge_error_usdt`, never pre-rounding target notionals.
- A daily loss limit, drawdown lockout, and liquidation buffer are explicit gates.
- Four-pair maximum is not a substitute for factor exposure control.

## Acceptance Criteria

- Shared BTC exposure is detected across multiple factor pairs.
- A trade is rejected when any pair, asset, gross, daily-loss, or drawdown limit fails.
- Risk checks are deterministic and do not depend on order submission.
- Tests cover simultaneous accepted, rejected, and partially available capacity cases.

## Implemented Contract

- `src/sngw_trader/indicators/portfolio_risk.py` is a pure module with no
  Nautilus, exchange, or order I/O.
- `RiskLimits` requires active-pair, gross, pair-margin, asset-exposure,
  daily-loss, drawdown, and liquidation-buffer limits. No loss or buffer
  threshold is invented by the module.
- `PortfolioSnapshot` provides current equity, peak equity, daily loss,
  liquidation buffer, active pairs, gross notional, and signed asset exposure.
- `PairCandidate` consumes S03 `PairSizing` and S02 `CostBreakdown`, plus asset
  names, pair margin, and leg directions. Shared assets are aggregated by
  signed actual notional before checking the absolute asset cap.
- `approve_pair` returns projected gross and asset exposure plus deterministic
  reason codes. Missing inputs fail closed with `MISSING_DATA`.
- Positive S02 total cost is reserved against the daily-loss limit; a funding
  credit never creates additional risk capacity.

## Stage Handoff

- Previous: [S03](2026-09-15-kalman-mr-s03-contract-sizing.md)
- Next: [S05](2026-09-15-kalman-mr-s05-execution-recovery.md)
