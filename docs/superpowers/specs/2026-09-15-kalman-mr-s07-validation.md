# S07 Walk-Forward Validation Specification

- 상태: `PENDING`
- 범위: 전체 전략의 인과성, 비용·체결 스트레스, out-of-sample 검증
- 제외: 검증 통과 전 라이브 배포 승인

## Document Links

- [Work index](../../../ideas/kalman_MR_spread/INDEX.md)
- [Previous: S06](2026-09-15-kalman-mr-s06-event-gate.md)
- [Plan](../plans/2026-09-15-kalman-mr-s07-validation.md)
- [Main strategy spec](../../../ideas/kalman_MR_spread/OKX_Kalman_MR_Spec.md)

## Required Decisions

- Formation and trading windows are strictly separated.
- Screening, bands, costs, funding, and events use only information available at each decision timestamp.
- Validation includes per-leg fees, executable spread, funding, partial fills, and parameter stress.
- Selection across 15 pairs is reported separately from individual pair performance.
- Results include return, drawdown, turnover, cost share, stop frequency, exposure concentration, and failure cases.

## Acceptance Criteria

- Walk-forward runs are reproducible from catalog inputs and a recorded configuration.
- No future bars, future funding, smoothed state, or post-window screen values enter a decision.
- Stress cases show the strategy's break-even cost and drawdown behavior.
- S07 output explicitly states whether the strategy remains research-only, paper-ready, or live-ineligible.

## Stage Handoff

- Previous: [S06](2026-09-15-kalman-mr-s06-event-gate.md)
- Final stage: update [INDEX](../../../ideas/kalman_MR_spread/INDEX.md) and the main strategy spec only after all acceptance criteria pass.
