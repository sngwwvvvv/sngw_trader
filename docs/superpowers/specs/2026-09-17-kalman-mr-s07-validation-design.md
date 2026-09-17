# S07 Walk-Forward Validation Design

- 상태: `DESIGN_REVIEW`
- 범위: Phase 1 — Kalman 스프레드 전략 클래스 + 백테스트 러너 구현. Phase 2 — walk-forward 데이터, 스트레스 실행, 결과 집계, 판정
- 제외: 라이브 배포 승인, OKX 라이브 주문, 스크리닝 기준 변경

## Phases

S07은 두 개의 구현 단계로 나눈다. 각 단계는 별도 플랜으로 실행하며, 워크플로 규칙 "한 세션 한 단계"를 따른다.

- **Phase 1 — Strategy + Runner**: S01-S06 순수 모듈을 바인딩하는 `KalmanSpreadStrategy`와 `BacktestNode` 러너를 만들고 단위 테스트로 계약을 검증한다. 실제 유니버스 실행은 하지 않는다.
- **Phase 2 — Validation**: 12개월 데이터를 catalog에 적재하고, Phase 1 산출물로 베이스라인 + 스트레스 walk-forward를 실행·집계해 최종 판정을 낸다.

Phase 2에서 Phase 1의 계약을 바꿔야 하는 경우 Phase 1 플랜을 재검토한다.

## Context

S01-S06은 순수 모듈(kalman_spread, cost_model, contract_sizing, portfolio_risk, execution_recovery, event_gate)로 완료되어 있다. 전략 클래스와 러너는 아직 없으며, S07 스테이지 스펙의 수용 기준은 실제 데이터로 재현 가능한 walk-forward 실행을 요구한다. 따라서 S07은 바인딩 구현과 검증 실행을 모두 포함하되, 매매 조건은 전략에, 노드 조립은 러너에 두는 저장소 규칙을 유지한다.

## Phase 1 — Strategy + Runner

### 데이터 파이프라인 확장

`src/sngw_trader/data/catalog_writer.py`를 확장한다. 기존 LAST 1분 캔들 다운로더 패턴을 재사용하며 주문 경로는 만들지 않는다.

- OKX mark-price 캔들 엔드포인트(`history-mark-price-candles`) 지원, 바 크기 1H → `1-HOUR-MARK-EXTERNAL` 바로 catalog 적재
- 펀딩 이력 엔드포인트(`funding-rate-history`) 지원 → 페어별 펀딩 관측을 catalog custom data로 적재
- S02 비용 계약에 따라 각 페어·기대 펀딩 타임스탬프의 관측이 누락·중복되면 비용 계산이 실패한다. 적재 단계에서 중복을 제거하고 결측을 보고한다.

### 전략 클래스

`src/sngw_trader/strategies/kalman_spread.py`에 `KalmanSpreadStrategy` 하나를 추가한다. 단일 인스턴스가 15페어 유니버스 전체를 관리해 포트폴리오 조정(S04)을 한 곳에서 처리한다.

페어별 페이즈 머신:

```text
FORMATION(30일) -> TRADING(10일) -> FORMATION(재스크리닝) -> ...
```

- FORMATION: 완성된 1시간 mark 봉만 S01에 공급. 창 종료 시 `x_center`, `x_scale`을 계산·고정하고 형성 통계(잔차 σ, 상관, 반감기, 비용 대비 `2σ/c` 등)를 S02 가정으로 계산해 Hard/Soft 스크리닝을 판정한다. 스크리닝은 창 내 데이터만 사용한다.
- TRADING: 스펙 §7.1 밴드 표로 진입(\|z\| 상향 돌파), 청산(\|z\| 복귀), 손절(\|z\| 지속 + 시간손절 + 반대 +1.0), 리헤지(\|Δβ\|/β 임계)를 판정한다. 반대 플립은 금지한다. 창 종료 시 전량 청산 후 FORMATION으로 복귀한다.
- 진입·청산 의도는 S03 계약 수량 변환 → S04 포트폴리오 승인(gross 150%, 페어 마진 한도, 동시 4페어, 중복 그룹) → S05 실행 상태 전이로 전달한다.
- 모든 의사 시점에서 S06 이벤트 게이트를 평가하고, 게이트 실패 시 즉시 청산·쿨다운·재형성 전 Off를 적용한다.
- 체결 콜백은 S05 `fill`에 전달하고, 반환된 action intent(SUBMIT / CANCEL_UNFILLED / FLATTEN_FILLED)를 Nautilus 공식 주문 API로 번역한다.
- 펀딩 비용은 S02 계약대로 기대 펀딩 타임스탬프의 기록된 레이트로 전략 회계에 반영한다.
- 전략 파일은 노드를 조립하지 않는다.

### 러너

`research/`에 walk-forward 러너를 추가한다. 러너는 매매 조건을 갖지 않는다.

- `BacktestNode` + `ParquetDataCatalog` + OKX venue 에뮬레이션(taker 5bp 수수료 설정, slippage/fill model 파라미터)
- 실행 설정(데이터 범위, 비용 배수, fill model, 이벤트 파일 경로, 시드)을 JSON으로 읽고, 결과와 함께 동일 설정을 기록한다
- 큐레이션 이벤트 스트림(JSON)을 전략 설정으로 주입한다
- 스트레스 런은 동일 설정에서 비용 배수(1.5x, 2x), 슬리피지, 부분 체결 확률만 바꿔 실행한다

### Phase 1 테스트

- 페이즈 경계: 형성 종료 프리즈, 창 종료 청산, 재스크리닝 복귀
- 인과성: 형성/스크리닝이 창 밖 미래 데이터를 사용하지 않음, 신호 봉 마감 다음 봉 체결 가정
- 밴드·손절·리헤지·반대 플립 금지·쿨다운 전이
- S04 승인 거절 시 진입 보류, S05 intent → 주문 번역
- 이벤트 게이트 실패 시 즉시 청산 경로
- 러너: 최소 데이터 스모크 런이 설정을 기록하고 결과를 집계하는지 (실제 다운로드 데이터 없이 합성 데이터로)

## Phase 2 — Validation

### 데이터 적재

- 17 심볼(15페어 유니버스) × 12개월 1시간 mark 캔들 + 펀딩 이력을 catalog에 적재한다
- 12개월 창의 주요 이벤트(언락, 상장폐지, 체인 스톱, 거래소 장애 등)를 수동 큐레이션해 JSON 이벤트 스트림으로 만든다

### 실행 및 집계

- 베이스라인, 1.5x 비용, 2x 비용, 슬리피지, 부분 체결 케이스를 실행한다
- 집계 모듈이 재현 가능한 결과를 산출한다: 수익률, drawdown, turnover, 비용 비중, 손절 빈도, 익스포저 집중도, 실패 케이스
- 15페어 스크리닝(선택) 결과를 개별 페어 성과와 분리해 보고한다
- 비용 스트레스에서 break-even 비용과 drawdown 행태를 명시한다

### 판정 및 문서

- 수용 기준 점검: catalog 입력 + 기록된 설정으로 재현 가능, 의사 시점에 미래 봉·미래 펀딩·스무딩 상태·창 밖 스크린 값 미사용
- S07 산출물이 최종 상태(research-only / paper-ready / live-ineligible)를 명시한다
- 통과 시에만 `INDEX.md`(S05/S06 `DONE` 반영 포함)와 메인 스펙 상태를 갱신한다

## Error and Safety Rules

- 모든 의사 시점은 완성 봉 기준이다. 미래 데이터 사용 경로는 존재하지 않아야 하며, 위반 시 계약 실패로 처리한다.
- 펀딩 관측 누락·중복은 비용 계약에 따라 실패(fail-closed)이며 0으로 대체하지 않는다.
- 결측 2시간 연속 페어는 해당 창 무효(스펙 §6)를 적용한다.
- 다운로드 실패·부분 적재는 재실행 가능하게 멱등하게 처리하고, 어떤 심볼이 몇 개월 커버인지 보고한다.
- 검증 통과 전 라이브 관련 문서 상태는 변경하지 않는다.

## Test Contract

Phase 1: 위 Phase 1 테스트 목록. Phase 2: 집계 정확성 단위 테스트, 스트레스 케이스 실행 보고, 수용 기준 체크리스트 리뷰.

## Implementation Handoff

- Stage specification: [S07](2026-09-15-kalman-mr-s07-validation.md)
- Previous stage: [S06](2026-09-15-kalman-mr-s06-event-gate.md)
- Phase 1 plan / Phase 2 plan: 디자인 승인 후 별도 플랜으로 작성한다
