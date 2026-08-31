# Robustness Run Report — Design

- 날짜: 2026-08-31
- 상태: 승인 대기
- 범위: walk_forward 우선 적용

## 1. 문제

현재 백테스트 성과 평가는 pnl / MDD(Monte Carlo) / Sharpe에 의존한다. 이것만으로는
다음 질문에 답할 수 없다:

- 이 전략은 손실 구간을 어떻게 견디는가? (Sortino, Calmar, underwater)
- 파라미터가 과적합인가? (OOS/IS 성과 괴리)
- 손익 구조가 안정적인가? (win rate, profit factor, 연속 손실)

## 2. 목표

- 각 백테스트 run(IS 최적 조합 / OOS / holdout)에 대해 강건성 지표를 계산한다.
- 기존 리포트 3종(`wf_report.json`, `mc_report.json`, `summary.json`) 형식은 유지하고
  필드를 확장한다.
- 의존성을 추가하지 않는다. 기존 `monte_carlo.py`의 "pure numpy, no nautilus" 패턴을
  따른다.

## 3. 데이터 흐름

```
BacktestNode 실행 (executor.run_window)
  → 체결 포지션 추출 (기존)
  → 계좌 잔고 이벤트에서 (ts_ns, balance) equity 마크 추출 (신규, 엔진이 이미 기록한 것만 사용)
  → RunResult에 equity_marks 추가
  → metrics.py로 run 단위 지표 계산
  → wf_report.json / summary.json에 metrics 반영
```

전략·노드 코드는 수정하지 않는다. 러너(`BacktestNode`)와 executor 계층만 건드린다.

### 3.1 equity 마크의 의미와 한계 (중요)

- 계좌 잔고 이벤트(AccountState)의 잔고는 **realized balance**다. 미실현 손익이
  반영되지 않으므로 포지션 보유 중의 드로다운은 보이지 않고, MDD / underwater /
  mdd_duration은 **과소평가**될 수 있다. 이 한계는 리포트 해석 시 전제로 명시한다.
- 마크 발생 시점은 체결·정산 이벤트에 종속되므로 해상도가 균일하지 않다. 마크 수가
  지나치게 적으면(예: 2개) 인접 수익률 기반 지표는 구현은 되지만 해석력이 낮다.
  본 스펙은 엔진이 기록한 이벤트만 소비하며, 별도 마크 주입(전략 수정)은 하지 않는다.
- 캐시에서 잔고 이벤트 조회 API는 구현 첫 단계에서 반드시 확인한다:
  `python -c "from nautilus_trader.backtest.engine import BacktestEngine; e=BacktestEngine; print([m for m in dir(e.cache) if 'account' in m])"`
  확인 전까지는 대상 API를 `# UNVERIFIED IMPORT` 주석으로 표기한다.

## 4. 구성 요소

### 4.1 `src/sngw_trader/research/metrics.py` (신규)

순수 numpy 모듈. nautilus import 금지. 입력은 2종:

- `compute_trade_metrics(pnls, returns) -> dict | None`
  - win_rate, profit_factor, expectancy, payoff_ratio(avg win / avg loss),
    max_consecutive_losses
  - 입력 사용: `pnls` → win_rate / profit_factor / expectancy / payoff_ratio /
    max_consecutive_losses 전부. `returns`는 본 모듈에서 사용하지 않으며
    호출부 시그니처 호환을 위해 유지한다(사용처가 생기기 전까지는 제거 후보).
  - 거래 0건이면 `None`
- `compute_equity_metrics(marks: list[tuple[int, float]], initial_capital) -> dict | None`
  - sortino, calmar, mdd_ratio, mdd_duration, recovery_duration, underwater_mean,
    tail_ratio(|p95 returns| / |p5 returns|)
  - 기준: equity 마크의 인접 수익률 기준으로 계산, 연율화는 마크 간 평균 간격으로 추정
    (마크가 이벤트 종속이라 불규칙하므로 연율화 값은 근사다 — 해석 시 한계).
    Sortino의 하위 위험은 하방 표준편차(LPM 2차 아님), Calmar = 연율 수익률 / mdd_ratio.
  - equity 마크가 2개 미만이면 `None`

특이값 규칙 — "NaN 금지, 계산 불가 항목은 `None`"의 항목별 적용:

| 항목 | None이 되는 조건 |
|---|---|
| profit_factor | 총 손실 = 0 (발산 방지) |
| payoff_ratio | 손실 거래 0건 |
| tail_ratio | p5 수익률의 절댓값이 0 (발산 방지) |
| calmar | mdd_ratio가 0이거나, 연율 수익률이 음수 (음수 Calmar는 부호 해석이 역전되므로 None) |
| sortino | 하방 표준편차가 0 |
| mdd_ratio | 인접 수익률이 전부 0 이상 (드로다운 없음 → 0.0 반환, None 아님) |

모든 함수는 NaN을 반환하지 않는다. 계산 불가 항목은 `None`.

### 4.2 `src/sngw_trader/research/executor.py` (수정)

- `RunResult`에 `equity_marks: list[tuple[int, float]]` 필드 추가.
  frozen dataclass이므로 기본값을 둔다: `field(default_factory=list)`.
  기존 생성부(`extract_run_result`)는 채워 넣고, 기존 테스트 등 위치 인자 생성부는
  호환된다.
- `extract_run_result`가 `engine.cache`의 계좌 잔고 이벤트에서 마크를 수집한다
  (§3.1의 API 검증 절차 우선).
  - window_start 이전 warmup 구간 마크는 제외한다.
  - 이벤트가 비어 있으면 빈 리스트. 다운스트림은 `None` 지표로 처리한다.

### 4.3 `src/sngw_trader/research/walk_forward.py` (수정)

- IS 그리드: 조합별 trade-level 지표 중 sharpe는 기존대로, 전체 metrics는
  **선택된 최적 조합에만** 계산한다. (그리드 크기만큼 전체 지표를 계산하는 것은 YAGNI)
- OOS run: trade + equity 전체 지표를 계산해 윈도우 항목에 `metrics` 필드로 추가.
- holdout run: OOS와 동일하게 trade + equity 전체 지표를 계산한다. 단 holdout에는
  대응하는 IS run이 없으므로 **`oos_is_sharpe_ratio`는 생성하지 않는다**.
- 신규 핵심 지표: **oos_is_sharpe_ratio** = OOS Sharpe / IS Sharpe.
  - Sharpe 정의는 **기존 `select.sharpe_from_trades`(trade-return 기반 per-trade
    Sharpe, 미연율화)와 동일 값**을 쓴다. 새 equity 기반 지표와 섞지 않는다.
  - 1에 가까울수록 과적합 없음. summary에 윈도우별 비율 리스트를 포함.
  - 특이값 규칙: **IS Sharpe ≤ 0이면 비율은 `None`**. `sharpe_from_trades`는
    거래 <2건 또는 std=0일 때 0.0을 반환하므로 이 케이스가 실제로 발생하며,
    분모 ≤ 0인 비율은 발산하거나 (음/음 = 양수) "과적합 없음"으로 위장한다.
    해당 윈도우는 비율 미산출로 표기하고 집계 리스트에서 제외한다.

### 4.4 리포트 확장

- `wf_report.json`: 각 윈도우에 `is_metrics`(최적 조합), `oos_metrics`,
  `oos_is_sharpe_ratio` 추가.
- `summary.json`: `robustness` 블록 추가:
  - `oos_is_sharpe_ratios`: 비율 리스트 (IS Sharpe ≤ 0 윈도우는 제외).
    `n_excluded_ratio_windows`(제외된 윈도우 수)를 함께 기록한다.
  - OOS 윈도우별 지표 집계 — 산술평균 sortino / calmar, mdd_ratio는
    **평균과 max(최악)를 모두** 기록 (mdd는 최악값이 의미 있음).
    평균 대상이 `None`인 항목은 집계에서 제외하고 제외 개수를 함께 기록한다.
- `mc_report.json`: 변경 없음. 콘솔 출력(`print_summary`)은 summary 확장을 그대로
  따라간다.

## 5. 에러 처리

- 잔고 이벤트 부재 → equity 기반 지표 `null`, 나머지 흐름(MC, summary)은 정상 진행.
- 거래 부재 → trade 기반 지표 `null`.
- 기존 파이프라인의 실패 조건(min_trades 게이트, 부트스트랩 최소 거래 수 등)은 유지.

## 6. 테스트

- `tests/test_research/test_metrics.py` (신규)
  - synthetic equity 시계열 / trades로 지표 값 검증 (mdd, sortino, profit_factor,
    연속 손실, 꼬리 비율)
  - 빈 입력 → `None` 검증
  - §4.1 특이값 규칙 표의 각 조건별 검증 (profit_factor 총손실 0, payoff_ratio
    손실거래 0건, tail_ratio p5=0, calmar 음수 수익률 / mdd 0, sortino 하방편차 0)
- `tests/test_research/test_executor.py` (수정)
  - equity 마크 추출, warmup 제외 검증
  - nautilus 캐시 의존은 실제 캐시를 쓰지 않고 잔고 이벤트만 갖는 최소 fake
    (또는 실제 `BacktestEngine` 캐시에 이벤트 삽입)으로 대체한다. 네트워크/노드
    실행 없이 순수 단위 테스트로 유지한다.
- `tests/test_research/test_walk_forward.py` (수정)
  - 리포트 필드 확장, oos_is_sharpe_ratio 계산 검증
  - **IS Sharpe ≤ 0 엣지**: 비율 `None` + 집계 제외 + `n_excluded_ratio_windows`
    카운트 검증
  - holdout 윈도우에 비율 필드가 없음을 검증

## 7. 비목표 (Out of Scope)

- HTML/마크다운 시각 리포트
- param_scan, compare_strategies, live 러너 통합 (공용 metrics 모듈만 재사용 가능 상태로
  설계, 통합은 별도 작업)
- IS 그리드 전 조합의 전체 metrics
- 새 의존성 추가
