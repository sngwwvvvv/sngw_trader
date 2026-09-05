# Renko + MACD Intraday 백테스트 구현 계획

- 날짜: 2026-09-05
- Spec: `docs/superpowers/specs/20260905-bt-renko-macd-intraday-spec.md`
- Handoff: `20260905-bt-renko-macd-intraday`
- 원칙: 기존 패턴 100% 재사용. 신규 로직은 Renko 변환기 1개와 전략 클래스 1개뿐.

## 0. 재사용 목록 (작성 전 확인 완료)

| 재사용 대상 | 출처 | 용도 |
|---|---|---|
| `Macd`, `crossed_up/down` | `indicators/kd_macd.py` | brick 종가 시퀀스 MACD |
| `OrderIntent` + `_decide`/`_intents_for`/`on_event`/`_execute` 패턴 | `strategies/macd_crossover.py` | 전략 골격 (복사 후 Renko 피드로 교체) |
| `VolTargetSizer` | `indicators/vol_targeting.py` | sizing 민감도 |
| `GridSpec`, `build_strategy`, `run_window` 추출 로직 | `research/config.py`, `research/executor.py` | 러너 |
| `compute_equity_metrics`, `compute_trade_metrics` | `research/metrics.py` | 지표 |
| `run_cell`/`stress_settings`/`evaluate` 구조 | `research/macd_crossover_compare.py` | 비교 스크립트 템플릿 |
| `build_run_config`, `default_bar_type` | `runners/backtest_okx.py` | BacktestNode 조립 |

## 1. 단계 (순서 고정)

### Step 1 — `src/sngw_trader/indicators/renko.py` (신규)

```python
class RenkoBrickBuilder:
    def __init__(self, brick_size: float): ...
    def on_close(self, price: float) -> list[float]:
        """확정된 brick 종가들 반환. 상태: 마지막 brick 종가 + 방향."""
```

- close-only 규약: 상승 `price >= last + B` → brick 확정 (복수 brick 허용, 남은 이동 내에서 반복).
- 하락 전환: `price <= last - 2B` → brick 확정, 이후 방향 하락 (복수 brick).
- 같은 방향 하락: `price <= last - B`.
- 첫 가격: 첫 brick은 `price` 기준 방향 미정 → 첫 확정은 ±B 이동 시 그 방향으로.
- Nautilus 의존 없음 (kd_macd.py와 동일 순수 스타일).
- 마이크로 테스트 즉시 작성 (Step 1.5).

### Step 1.5 — `tests/test_indicators/test_renko.py` (신규)

케이스: 신규 brick 1개 / 복수 brick (3B 이동 → 3 brick) / 전환 2B (1B 반대 이동은 무시) / 전환 후 같은 방향 1B / 첫 가격 워밍업.

### Step 2 — `src/sngw_trader/strategies/renko_macd_intraday.py` (신규)

`macd_crossover.py` 복사 후 차이점만 수정:

1. `BarAggregator` → `RenkoBrickBuilder(config.brick_size)` (1분봉 close 그대로 투입, 일간 집계 없음).
2. `on_bar`: 봉마다 `builder.on_close(bar.close)` → 확정 brick마다 `macd.update(brick_close)` → crossover 검출. brick 없는 봉은 무시.
3. crossover 검출은 brick 단위: `_prev_line/_prev_signal` 갱신 및 `crossed_up/down` 재사용 (trigger_mode 없음 — spec에 state 변형 없음).
4. 당일 청산: `flatten_daily=True`일 때 봉 타임스탬프에서 UTC 일자가 바뀌기 직전 봉(다음 봉 ts가 다음 날이면 현재 봉)에서 target=0 intent. 구현은 `_decide`에 `utc_day` 인자 전달, "다음 봉의 날짜 != 현재"를 on_bar에서 판정해 `force_flat=True` 플래그로 전달. 재진입은 다음 세션 crossover에서 자동.
5. Config 필드: `brick_size: float`, `macd_fast=12, macd_slow=26, macd_signal=9`, `allow_short=False`, `flatten_daily=True`, `trade_size`, `sizing_mode="unit"`, vol-target 필드는 macd_crossover와 동일 세트 복사.
6. 방향 변환은 `direction_target` 함수 재사용 (import).

### Step 2.5 — `tests/test_strategies/test_renko_macd_intraday.py` (신규)

`test_macd_crossover.py` 케이스 구조 재사용. 케이스: brick 위 crossover 진입/청산 / allow_short 분기 / 당일 flatten / unit vs vol_target / 워밍업(35 brick 전 무신호).

### Step 3 — 검증 관문 1: 단위 테스트

`pytest tests/test_indicators/test_renko.py tests/test_strategies/test_renko_macd_intraday.py -q` → 통과.

### Step 4 — `src/sngw_trader/research/renko_macd_intraday_compare.py` (신규)

`macd_crossover_compare.py` 복사 후 수정:

- SPEC 전략 경로만 교체, `fixed={"trade_size": "0.02", "brick_size": 0.001}` (중앙값 brick = 가격의 0.1%).
- grid: `{"brick_size": [0.0005, 0.001, 0.002, 0.004, 0.008]}` (가격 대비 상대값 — config가 절대 float 받으므로 실행 시 grid 값을 그대로 brick_size에 주입).
- 베이스 셀: {long_only, long_short} × {is, oos} × {base, stress} × 중앙 brick × unit — 8셀.
- 민감도 셀: brick grid 전체 × 방향 2종 × {is, oos} (기본 비용) — 20셀 + sizing 민감도(vol_target) 중앙 brick × 방향 2종 × oos — 2셀.
- 복사본 `run_cell`의 params에 brick_size/allow_short/sizing_mode 병합되도록 유지 (기존 구조 그대로).
- 출력: `research/renko_macd_intraday/results.json` + 콘솔 표 (기본 표 + brick 민감도 표).
- 워밍업: brick 기반 → 120일 pad 유지 (1분봉이라 brick 충분).

### Step 5 — 검증 관문 2~4: 실행

1. `python -m sngw_trader.research.renko_macd_intraday_compare` (IS/OOS, 기본 비용, brick 그리드 전체).
2. 스트레스 비용 셀 확인.
3. vol_target 민감도 확인.

### Step 6 — 리포트 + receipt

- result 파일 (wiki 30_PROJECTS/trading/) 생성: spec §6 assumption 목록 전체, brick 그리드 전체 표, IS/OOS, 비용 모델, handoff_id 명시.
- receipt (40_AGENT_WORKSPACE/agents/trading-agent/) 작성 → accepted.

## 2. 의도적 생략 (ponytail)

- `trigger_mode` state 변형 — spec에 없음. 필요하면 macd_crossover에서 복사 1줄.
- 별도 runner/launcher — 비교 스크립트 1개면 충분.
- walk-forward 롤링 — spec은 고정 IS/OOS만 요구. `research/walk_forward.py` 확장 안 함.
- 상대 brick을 strategy 내부에서 정규화 — grid 주입이 절대값이면 충분. 실행 시퀀스에서 가격 정규화 없음.

## 3. 리스크

- brick_size가 클수록 trade 수 급감 → grid 최소값 0.05%로 인트레이 빈도 확보.
- 당일 flatten으로 o/h/l 무시 (close-only Renko) — assumption 5로 result에 기재됨.
- 6년 1분봉 × 30셀 실행 시간 — 실행은 기본 셀 먼저, 민감도 셀은 `--sensitivity` 플래그로 분리.
