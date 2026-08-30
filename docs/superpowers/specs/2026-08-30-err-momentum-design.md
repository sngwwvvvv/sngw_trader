# ERROR-Adjusted Momentum 전략 A/B 설계 (설계 확정)

- 날짜: 2026-08-30
- 원본 스펙: `ideas/err_momentum/spec_error_mom_original.md` (스펙 A), `ideas/err_momentum/spec_error_mom_ema30_entry.md` (스펙 B)
- 상태: 설계 확정 (사용자 승인)

## 1. 목적

일봉 ERMOM(Error-Adjusted Momentum)을 (A) 포지션 그 자체로 쓰는 경우와 (B) 방향 허가로만 쓰고 30분 EMA 리본 상태머신으로 진입하는 경우를 **동일 조건**에서 백테스트 비교한다. 차이는 진입/홀딩 규칙 하나로 제한한다.

## 2. 공통 결정 사항

| 항목 | 결정 |
|---|---|
| 상품 | `BTC-USDT-SWAP.OKX` (USDT 무기한 선물) |
| 원본 데이터 | catalog의 기존 1분봉 (`1-MINUTE-LAST-EXTERNAL`) |
| 30분봉/일봉 | **1분봉 내부 집계**로 합성. 일봉 경계는 UTC 00:00. 별도 다운로드 없음 |
| ERMOM 계산 | 두 전략이 **하나의 공용 인디케이터**를 공유 (수락 조건: A/B ERMOM 시계열 일치) |
| 집행 | 봉 t 마감 `on_bar` 안에서 시장가 주문 제출 → 다음 봉 시가 체결. 룩어헤드 없음 |
| 비용 | OKX taker fee (기존 `OkxRateFeeModel` 사용) + 고정 슬리피지 bps |
| 펀딩비 | **엔진 밖 후처리**: 백테스트 포지션 시계열 × OKX funding rate(공개 API) 차감. 펀딩 제외/포함 두 숫자 병기 |
| 사이징 | 명목 1 단위 (풀 인/풀 아웃), A/B 동일 settings 주입 |
| L 그리드 | {60, 90, 120, 200} — 코드 추가 없이 settings 변경으로 반복 실행 |
| 공통 리스크 레이어 | 하드 스톱 + 변동성 킬스위치 — **A/B 동일 적용**, config로 on/off (3.6절 참조) |
| backtest/live | 동일 전략 클래스. 라이브에서는 BarType만 OKX 실제 30m/1D 캔들로 교체 |

## 3. 파일 구성

```
src/sngw_trader/
  indicators/
    err_momentum.py            # 신규: 스트리밍 ERMOM 계산기 (순수 Python)
    risk_metrics.py            # 신규: 일봉 ATR(14) + 실현변동성 계산기 (리스크 레이어 공용)
    bar_aggregator.py          # 신규: 1m -> 30m / 1D(UTC) 합성기, 완성 봉만 콜백
  strategies/
    err_momentum_regime.py     # 신규: 스펙 A 전략
    err_mom_ema30_entry.py     # 신규: 스펙 B 전략
  runners/
    strategy_factory.py        # 수정: build_err_mom_a / build_err_mom_b 추가, build_ema_cross 제거
    backtest_okx.py            # 수정: settings의 STRATEGY 선택자로 전략 부착, ema_cross 와이어링 제거
  config/
    settings.py                # 수정: STRATEGY, W_f/W_e/L/theta, N_pull, EMA 20/50, trade_size 추가
  data/
    funding.py                 # 신규: OKX funding rate 다운로드 + 펀딩 후처리 차감
tests/test_strategies/
  test_err_momentum.py         # ERMOM 골든 테스트
  test_err_mom_strategies.py   # FSM 전이 + 수락 조건 테스트
```

### 3.1 `indicators/err_momentum.py`

- 인터페이스: 봉 단위 `update(daily_close) -> ERMOM | None`
- 공식 (스펙 공통 2.2): `r_t` → `f_t = SMA(r, W_f)` → `e_t = r_t - f_{t-1}` (1봉 래그) → `MAE_t = SMA(|e|, W_e)` → `adj_r = r_t / MAE_t` → `ERMOM = SMA(adj_r, L)`
- `MAE_t == 0`이면 해당 봉 `adj_r`는 결측 처리, 이전 유효값만으로 SMA 구성. 0으로 나누지 않음
- warm-up: `L + W_f + W_e` 일봉 미충족 시 `None` (레짐 0)
- 라이브러리 의존 없는 스트리밍 구현 (`on_bar`에서 수 ms 이내)

### 3.2 `indicators/bar_aggregator.py`

- 1분봉을 받아 30분봉(00/30 경계)과 일봉(UTC 00:00 경계)으로 합성
- 완성된 봉만 콜백으로 전달 (미완성 봉으로 시그널 계산 금지)
- 합성 일봉 종가 = 해당 UTC일 마지막 1분봉 종가

### 3.3 스펙 A — `strategies/err_momentum_regime.py`

- 구독: 소스 1m BarType → 내부 집계로 일봉만 사용
- 일봉 완성 시: ERMOM 갱신 → 레짐 = 포지션
  - `ERMOM > 0` → 롱 +1 (이미 롱이면 유지)
  - `ERMOM < 0` → 숏 −1 (이미 숏이면 유지)
  - `ERMOM = 0` / warm-up 미충족 → 플랫 0
  - 레짐 반전 시 다음 일봉 시가에 플립 (청산 + 반대 진입)
- 30분봉, EMA, 밴드, 임계값 θ(≠0), 손절 없음

### 3.4 스펙 B — `strategies/err_mom_ema30_entry.py`

- 구독: 소스 1m BarType → 내부 집계로 일봉 + 30분봉 사용
- 일봉 완성 시: ERMOM 갱신 → regime 갱신 (허가 용도만)
- 30분봉 완성 시:
  - 롱/숏 거울 FSM: `IDLE → EXT → PULL → TRIG → IN` (스펙 B 3.3/3.4 전이표 그대로)
  - EMA20/EMA50 (span 기준, 30분 종가), 최소 50봉 warm-up
  - 밴드 터치는 윅(low/high) 인정, `N_pull = 24` 봉 체류 한도
  - 트리거 봉 다음 30분봉 시가에 시장가 진입. 시가 전 regime이 반대면 주문 취소
- 청산 우선순위 (스펙 B 4절): 1) 레짐 반대 전환 → 2) 롱 `close < EMA50` / 숏 `close > EMA50` → 3) 리본 교차 (`EMA20 ≤ EMA50` 등). 모두 다음 30분 시가
- 레짐 반전 시 플립하지 않고 플랫. 반대 진입은 그 방향 FSM을 처음부터 (EXT→PULL→TRIG 재통과)
- 피라미딩 없음, 부분 체결 시 잔량 1회 재시도 후 셋업 취소

### 3.5 러너 / 설정

- `strategy_factory.py`: 두 전략 빌더 추가. `build_ema_cross`는 제거 (예제 파일 자체는 유지)
- `backtest_okx.py`: settings의 `STRATEGY` 선택자 (`err_mom_a` | `err_mom_b`)로 전략 부착. 러너는 매매 조건을 갖지 않음
- A/B 비교 동등성: 동일 settings 객체에서 파라미터 주입
- `config/settings.py` 추가 필드: `strategy`, `w_f=10`, `w_e=10`, `l=200`, `theta=0`, `n_pull=24`, `ema_fast=20`, `ema_slow=50`, `trade_size`

- `config/settings.py` 추가 필드: `strategy`, `w_f=10`, `w_e=10`, `l=200`, `theta=0`, `n_pull=24`, `ema_fast=20`, `ema_slow=50`, `trade_size`
- 리스크 레이어 필드: `risk_stop_enabled=True`, `atr_period=14`, `atr_mult=3`, `vol_filter_enabled=True`, `vol_lookback=20`, `vol_threshold=0.80`

### 3.6 공통 리스크 레이어 (A/B 동일)

스펙 8절의 오염 우려 때문에 원본 스펙에서는 제외되었으나, 사용자 결정으로 1차에 포함한다. 비교 유효성을 위해 **두 전략에 완전히 동일한 정의와 파라미터**를 적용하고, config로 on/off 가능하게 한다 (off 상태 = 원본 스펙 순수 비교).

#### 하드 스톱

- 기준: **일봉 ATR(14)** — A/B 동일 (B의 진입이 30분이어도 스톱 기준은 일봉으로 통일)
- 스톱가: 진입가 ∓ `3 × ATR(14)`. 진입 시점 ATR로 고정, **트레일링 없음**
- 검사: 스펙 A는 일봉 종가, 스펙 B는 30분봉 종가에서 이탈 확인 → 다음 봉 시가 시장가 청산
- 스톱 주문을 시장에 예약하지 않는다 (bar 백테스트 체결가 재현 문제 + 룩어헤드 검증 단순화)
- 청산 후 재진입은 각 전략의 정상 진입 규칙을 따른다 (스펙 A: 레짐 유지 시 다음 일봉 시가 재진입. 스펙 B: EXT→PULL→TRIG 재통과)

#### 변동성 킬스위치

- 지표: 일간 수익률 20일 실현변동성 연율화 (일봉 완성 시에만 갱신)
- 조건: 연율화 변동성 > 80% → **신규 진입 금지, 기존 포지션은 유지** (청산까지 하면 A의 always-in 특성이 사라져 A/B 비교가 무너짐)
- 킬스위치 발동 중 청산은 기존 청산 규칙(스톱, 레짐, EMA)으로만 발생
- 변동성이 임계 이하로 회복되면 진입 허가 재개

### 3.7 펀딩 후처리 — `data/funding.py`

- 백테스트 결과의 포지션 시계열 × OKX funding rate 히스토리(공개 API, 인증 불필요)로 펀딩비 산출·차감
- 리포트는 펀딩 제외/포함 병기 + 스펙 7.3 지표: 순수익/CAGR, Sharpe/Sortino(일간), MaxDD/회복일, 거래 횟수/평균 보유, 시장 체류 비율, 승률/payoff, 롱 vs 숏, 비용 기여

## 4. 테스트 (수락 조건 반영)

1. **ERMOM 골든 테스트**: 합성 일봉 수익률에 대해 수기 계산/참조 구현과 일치. `f_t`–`e_t` 1봉 래그 확인, MAE=0 결측 처리 확인
2. **스펙 A**: 레짐→주문 전이 테스트 (FLAT/LONG/SHORT × +1/−1/0). 룩어헤드 없음 (봉 t 종가 신호 → 봉 t+1 시가 체결) 확인
3. **스펙 B FSM**: 스펙 9절 수락 조건 테스트
   - `ERMOM > 0`이 아닌 구간 롱 진입 0건, `ERMOM < 0`이 아닌 구간 숏 진입 0건
   - `L_EXT`를 거치지 않은 밴드 안 시작 봉은 트리거가 아님
   - 트리거 봉 종가가 아닌 다음 30분 시가 체결
   - 레짐 반전 시 플립 없이 플랫
4. **공유 계산 일치**: A/B 전략이 동일 일봉 입력에서 동일 ERMOM 시계열
5. **A/B 파라미터 동등성**: 러너가 동일 settings를 주입
6. **룩어헤드 검증**: 백테스트 리포트에서 체결가가 체결 봉 시가와 일치하는지 확인
7. **리스크 레이어**: ATR 스톱 도달 시 다음 봉 시가 청산 확인, 스톱 청산 후 재진입 규칙 확인, 킬스위치 발동 중 신규 진입 0건 확인, 레이어 off 시 원본 스펙 동작 확인

## 5. 검증 포인트 (구현 중 확인)

- Nautilus bar 백테스트에서 `on_bar(t)` 내 제출 시장가가 다음 봉 시가에 체결되는지 실제 리포트로 확인. 다를 경우 봉 경계 처리 조정
- 라이브 OKX 실제 30m/1D 캔들과 내부 집계 결과의 인터페이스 일치 (전략이 받는 봉 객체 동일성)

## 6. 1차에서 제외 (후속 레이어)

- 목표가(테이크프로핏), 피라미딩, 분할매수
- 펀딩 캡, VIX/DVOL 분모
- 스펙 B의 레짐-오프 ablation (30분 EMA만) 시리즈 — 스펙 7.4 후속
- θ ≠ 0 임계값 실험
- 펀딩비 엔진 내장 (후처리로 대체. 복리 효과 무시 근사 — 1차 비교 목적상 허용)

## 7. 알려진 근사 / 갭

- 펀딩비는 후처리 근사: 복리·증거금 동역학 미반영. 펀딩 제외 시 연 수 %-포인트 과대계상 가능 (스펙 A가 항상 체류라 더 과대계상됨) — 두 숫자 병기로 판단
- 합성 일봉/30분봉은 OKX 공식 캔들과 체결 소스는 같으나 mark 가격 기준이 다를 수 있음. 1차 비교는 A/B 동일 소스라 상대 비교에 영향 없음
- float에서 `ERMOM == 0`은 사실상 발생하지 않음. 레짐 0은 실질적으로 warm-up 기간만 해당
