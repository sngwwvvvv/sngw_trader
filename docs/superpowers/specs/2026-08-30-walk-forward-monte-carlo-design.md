# Walk-Forward (IS/OOS) + Monte Carlo 시뮬레이터 설계

- 날짜: 2026-08-30
- 상태: 승인됨 (설계 협의 완료)
- 적용 규칙: `NAUTILUS_VIBE_RULES.md` (러너는 BacktestNode뿐, 전략-러너 역할 분리 유지)

## 1. 목적

기존 단일 BacktestNode 러너 위에 오케스트레이션 레이어를 추가한다.

1. **Walk-forward (IS/OOS)**: IS 6개월에서 그리드 서치로 파라미터 선택 → 선택된 파라미터로 OOS 3개월 검증을 rolling 방식으로 반복하고, 최신 6개월(holdout)은 최종 OOS 검증용으로 남긴다.
2. **Monte Carlo**: 백테스트 체결(포지션) 수열을 iid 부트스트랩으로 재추출해 MDD/수익 분포와 파산확률을 추정한다.

러너를 새로 만들지 않는다. 모든 백테스트 실행은 `BacktestNode`를 통해 이뤄지며,
오케스트레이터는 창을 나누고 실행을 반복하고 결과를 집계하는 역할만 한다.

## 2. 확정된 요구사항 (협의 결과)

| 항목 | 결정 |
|---|---|
| IS / OOS 길이 | 6개월 / 3개월 |
| Window 이동 | Rolling, 3개월 스텝 |
| Holdout | 최신 6개월 — WF window에서 제외, 최종 OOS 검증 1회 사용 |
| IS 최적화 | 그리드 서치 |
| 그리드 주입 | 외부 config (strategy_path + params grid) — 오케스트레이터는 전략 무지 |
| 선택 기준 | Sharpe + 최소 거래수(기본 30) + **이웃 중앙값 평탄화**(외톨이 파라미터 탈락) |
| MC 방식 | 트레이드 부트스트랩, iid 1,000회 |
| 실행 모델 | 실행 1회마다 fresh `BacktestNode` (build → 전략 부착 → run → dispose) |
| 리포트 | `logs/{전략명}/{실행시각}/` 아래 JSON + 콘솔 요약 |
| 코드 위치 | `src/sngw_trader/research/` (신규 서브패키지) |

## 3. 아키텍처

```
research/
  config.py        # WalkForwardConfig, GridSpec, MCConfig (dataclass)
  windows.py       # window 경계 계산 (순수 함수, nautilus 불필요)
  executor.py      # 1회 백테스트 실행 + 결과 추출 (fresh BacktestNode)
  select.py        # 그리드 결과 → 이웃 중앙값 평탄화 → 최적 선택
  walk_forward.py  # 파이프라인 오케스트레이터 + main()
  monte_carlo.py   # 트레이드 부트스트랩 (순수 python/numpy)
  report.py        # JSON 리포트 + 콘솔 요약
```

역할 분리 원칙:

- `executor.py`는 `runners/backtest_okx.py`의 `build_run_config()`와
  `runners/strategy_factory.py`의 빌더를 **재사용**한다.
  `build_run_config()`에 `start`/`end`(그리고 warmup pad) 파라미터를 추가하되
  기존 단일 백테스트 러너의 동작은 유지한다 (기본값 하위호환).
- 오케스트레이터는 전략 클래스를 모른다. `strategy_path + config_path + params grid`
  만 주입받는다 (Nautilus `ImportableStrategyConfig` 패턴, 코드베이스의
  `importable_ema_cross_config()`와 동일 형태).
- 오케스트레이터는 OKX 어댑터/거래소 I/O를 전혀 하지 않는다.
- 실행 순서는 규칙 §5.1을 따른다: catalog 확인 → run config → 노드 build →
  전략 부착 → run → 리포트 → dispose.
- 한 프로세스에서 BacktestNode는 **순차** 실행한다 (동시 실행 아님 — 규칙상 금지는
  BacktestNode와 TradingNode의 동시 실행).

## 4. Walk-forward 시맨틱

### 4.1 데이터 범위

- catalog에서 bar 최소/최대 타임스탬프를 자동 감지한다.
- CLI/env override를 허용한다 (`WF_DATA_START`, `WF_DATA_END`).
- 데이터가 최소 1 window (IS 6개월 + OOS 3개월)에 못 미치면 에러로 종료한다.

### 4.2 Window 구조

```
[data_start .................................. data_end]
[WF 대상 구간 = data_start .. data_end - 6mo][holdout 6mo]
window k:  IS = [t, t+6mo)   OOS = [t+6mo, t+9mo)   t는 3개월씩 전진
```

- 경계는 UTC 달력 기준.
- 마지막 window의 OOS 끝이 holdout 시작(data_end − 6mo)에 닿으면 WF 종료.

### 4.3 Window별 진행

1. **IS**: 그리드 전 조합을 IS 구간에서 실행 → Sharpe + 거래수 집계.
2. **선택**: `select.py` 규칙으로 최적 조합 1개 선택 (아래 §5).
3. **OOS**: 선택된 조합으로 OOS 구간 1회 실행 → 성과 기록.
4. 전 조합이 최소 거래수 미달이면 해당 window는 "no-trade"로 기록하고
   stitched equity에서 제외, 다음 window로 진행.
5. 모든 window의 OOS 거래 수열을 이어 붙여 **stitched OOS equity** 1개를 만든다.

### 4.4 Holdout 검증

- 마지막 WF window에서 선택된 파라미터로 holdout 6개월을 1회 실행한다.
- 결과는 stitched OOS와 별도 표기한다 (섞지 않는다).

### 4.5 Warm-up pad

- 각 IS/OOS 실행 시 구간 시작 전 pad(기본 1일, 1분봉 기준)만큼 데이터를
  추가로 포함해 지표(EMA 등)를 수렴시킨다.
- 성과 집계는 pad 이후 구간만 대상으로 한다.
- pad 길이는 config로 조정 가능 (`wf_warmup_days`, 기본 1).

## 5. IS 선택 로직 (plateau selection)

1. IS 내 거래수 < 최소 거래수(기본 30)인 조합은 탈락.
2. 남은 각 조합의 평탄화 점수 = 자기 Sharpe와 **그리드 인접 조합 Sharpe들의
   중앙값**을 결합해 계산한다. 인접 = 각 파라미터 차원에서 ±1 스텝(그리드 끝은
   존재하는 이웃만), 나머지 파라미터는 고정. 이웃이 전부 낮으면(외톨이)
   점수가 깎인다.
   - 예: 그리드 [10, 20, 30]에서 Sharpe가 [−1.0, 14.0, −2.5]면
     EMA(20)은 이웃 중앙값이 낮아 선택되지 않는다.
3. 평탄화 점수가 최고인 조합을 선택한다.
4. 탈락/선택 과정과 점수표는 전부 리포트에 기록된다.

## 6. Monte Carlo (트레이드 부트스트랩)

- 입력: 백테스트의 완결 포지션별 **realized PnL 수열**.
- iid 재추출 1,000회 (`mc_iters`, config). random seed 고정 가능.
  각 시뮬레이션은 동일 거래 수로 리샘플해 equity curve를 구성한다.
- 산출물:
  - 최종수익 분포 (p5 / p50 / p95)
  - MDD 분포 (p50 / p95 / p99)
  - 파산확률: equity가 초기자본 대비 −50% 도달 확률 (threshold config, `mc_ruin_threshold`)
  - 최악 시나리오 요약
- 적용 대상: **stitched OOS 수열**과 **holdout 수열** 각각 1회씩.
- nautilus 없이 동작하는 순수 함수다. 합성 거래 리스트로 단위 테스트한다.

## 7. 리포트

```
logs/{strategy_name}/{YYYYMMDD-HHMMSS}/
  wf_report.json      # window별: IS 그리드 점수표, 선택 파라미터, OOS 성과
  mc_report.json      # stitched OOS + holdout 각각의 MC 결과
  summary.json        # stitched OOS 성과, holdout 성과, MC 핵심 수치
```

- `{strategy_name}`은 grid config의 strategy class명 (예: `EMACross`).
- 콘솔에는 window별 선택 결과 1줄씩 + 최종 요약 표를 출력한다.
- 차트/CSV는 만들지 않는다. 필요해지면 그때 추가한다.
- `logs/`는 git 제외 관례를 유지한다.

## 8. 진입점

- `pyproject.toml`에 스크립트 추가:
  `wf-okx = sngw_trader.research.walk_forward:main`
- MC 단독 CLI는 만들지 않는다. WF 파이프라인의 한 단계다.
  단독 실행이 필요해지면 그때 추가한다.

## 9. 테스트

| 대상 | 수준 |
|---|---|
| `windows.py` | 순수 단위 테스트: 경계 계산, holdout 제외, 데이터 부족 에러 |
| `select.py` | 단위 테스트: 외톨이 탈락 케이스 (`[−1.0, 14.0, −2.5]`) 필수 |
| `monte_carlo.py` | 합성 거래 리스트 + 고정 seed 결정적 검증 |
| `executor.py` | 노드를 띄우지 않는 조립 smoke 테스트 (규칙 §9) |
| WF 전체 e2e | catalog 데이터 필요 — 단위 테스트 제외, 수동 실행으로 검증 |

네트워크/실계좌 테스트는 없다.

## 10. UNVERIFIED IMPORT (구현 시 확인 필수)

- Nautilus 결과에서 Sharpe 추출: 포트폴리오 analyzer 통계 심볼
  (`python -c "from nautilus_trader.portfolio...; print(dir(...))"`로 확인).
- 포지션별 realized PnL: fills/positions 리포트의 실제 필드명 확인.
- `BacktestDataConfig`의 `start_time`/`end_time` 파라미터 형식 확인.
- import 실패 시 임의 클래스를 만들지 않고 설치된 패키지에서 확인한다 (규칙 §5.3).

## 11. 레이아웃 규칙 업데이트

`NAUTILUS_VIBE_RULES.md` §3 저장소 레이아웃에 다음을 추가한다:

```
    research/                    # 백테스트 오케스트레이션 (WF/MC)
```

역할 정의: `research/`는 창 분할·반복 실행·집계만 한다. 노드 조립은
`runners/`의 함수를 재사용하고, 매매 조건은 갖지 않는다.

## 12. 범위 밖 (YAGNI)

- Anchored walk-forward (rolling만 지원)
- Block bootstrap / 입력 랜덤화 MC
- 차트 이미지, CSV 출력
- MC 단독 CLI
- 전략 랭킹/앙상블, 리스크 사이징 로직
- 병렬 실행 (필요해지면 그리드 배치화로 최적화)
