# Kalman Mean-Reversion Spread Work Index

이 파일은 `ideas/kalman_MR_spread` 작업의 전체 진행 원본이다. 단계별 설계와 구현계획은 `docs/superpowers/specs/`와 `docs/superpowers/plans/`에 두며, 각 문서의 상태와 관계는 이 파일에서 관리한다.

## Source Documents

- [Strategy concept and procedure](OKX_Kalman_MR_Spec.md)
- [Operational rules and inputs](OKX_Kalman_MR_Rules.xlsx)
- [Repository rules](../../NAUTILUS_VIBE_RULES.md)

## Overall Status

| Field | Value |
|---|---|
| Overall | `IN_PROGRESS` |
| Current stage | `S02` |
| Production ready | `NO` |
| Strategy status | `DRAFT until S07 validation` |

## Stage Map

| ID | Stage | Status | Depends on | Specification | Implementation plan |
|---|---|---|---|---|---|
| S01 | Kalman model | `DONE` | - | [S01 spec](../../docs/superpowers/specs/2026-09-15-kalman-mr-s01-model.md) | [S01 plan](../../docs/superpowers/plans/2026-09-15-kalman-mr-s01-model.md) |
| S02 | Cost model | `IN_PROGRESS` | S01 | [S02 spec](../../docs/superpowers/specs/2026-09-15-kalman-mr-s02-cost.md) | [S02 plan](../../docs/superpowers/plans/2026-09-15-kalman-mr-s02-cost.md) |
| S03 | Contract sizing | `PENDING` | S02 | [S03 spec](../../docs/superpowers/specs/2026-09-15-kalman-mr-s03-contract-sizing.md) | [S03 plan](../../docs/superpowers/plans/2026-09-15-kalman-mr-s03-contract-sizing.md) |
| S04 | Portfolio risk | `PENDING` | S02, S03 | [S04 spec](../../docs/superpowers/specs/2026-09-15-kalman-mr-s04-portfolio-risk.md) | [S04 plan](../../docs/superpowers/plans/2026-09-15-kalman-mr-s04-portfolio-risk.md) |
| S05 | Execution and recovery | `PENDING` | S03, S04 | [S05 spec](../../docs/superpowers/specs/2026-09-15-kalman-mr-s05-execution-recovery.md) | [S05 plan](../../docs/superpowers/plans/2026-09-15-kalman-mr-s05-execution-recovery.md) |
| S06 | Event gate | `PENDING` | S05 | [S06 spec](../../docs/superpowers/specs/2026-09-15-kalman-mr-s06-event-gate.md) | [S06 plan](../../docs/superpowers/plans/2026-09-15-kalman-mr-s06-event-gate.md) |
| S07 | Walk-forward validation | `PENDING` | S01-S06 | [S07 spec](../../docs/superpowers/specs/2026-09-15-kalman-mr-s07-validation.md) | [S07 plan](../../docs/superpowers/plans/2026-09-15-kalman-mr-s07-validation.md) |

## Dependency Graph

```text
S01 Kalman model
  -> S02 Cost model
    -> S03 Contract sizing
      -> S04 Portfolio risk
        -> S05 Execution and recovery
          -> S06 Event gate
            -> S07 Walk-forward validation
```

S04 also depends directly on S02 because portfolio limits use cost-adjusted risk. S07 depends on every previous stage and is the only stage allowed to change the overall strategy status from `DRAFT` to a validated state.

## Workflow Rules

1. 한 세션에서는 한 단계만 구현한다.
2. 각 단계는 해당 스펙 승인, 계획 검토, 테스트 통과 후에만 `DONE`으로 바꾼다.
3. 후속 단계가 선행 단계의 계약을 바꾸면 선행 단계와 연결된 계획을 다시 검토한다.
4. `OKX_Kalman_MR_Spec.md`의 `LOCKED v1.0` 표기는 S07 완료 전까지 사용하지 않는다.
5. 스프레드시트의 운영 원본 변경은 해당 단계의 완료 조건에 포함된 경우에만 한다.

## Session Handoff

S01의 순수 Kalman 모델 코드와 집중 테스트가 완료되었고, 정규화 상태·공정잡음·lazy initialization·Joseph 공분산 갱신·72회 유효 업데이트 warm-up·잘못된 가격 거부 계약을 아이디어 문서에 반영했다. 현재 작업은 S02 비용 모델 단계이며, 전략·러너·주문·리스크·검증 구현과 production readiness는 아직 확정하지 않는다. 세션 종료 시 변경된 문서, 실행한 테스트, 남은 결정사항을 해당 단계 계획 문서에 기록한다.
