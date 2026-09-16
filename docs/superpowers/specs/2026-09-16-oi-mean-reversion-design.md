# OI A/B 평균회귀 전략 백테스트 설계

- 날짜: 2026-09-16
- 상태: 승인됨
- 적용 규칙: `NAUTILUS_VIBE_RULES.md`

## 1. 목적

BTC-USDT-SWAP.OKX 5분봉 평균회귀 전략에서 OI를 두 가지 독립 가설로 검증한다.

- A: 가격 극단에서 OI가 증가한 뒤 증가세가 꺾이는 포지션 과밀 반전
- B: 가격 극단에서 OI가 감소하는 포지션 청산 소진

두 가설은 하나의 전략 조건으로 합치지 않는다. 각각 별도의 Nautilus `Strategy` 클래스로 백테스트하여 OI가 가격 기반 평균회귀에 추가 정보를 제공하는지 비교한다.

이번 범위는 백테스트 데이터·전략·러너·단위 테스트다. 라이브 OKX OI 스트리밍 어댑터는 이번 범위에 넣지 않는다.

## 2. 확정된 요구사항

| 항목 | 결정 |
|---|---|
| 상품 | `BTC-USDT-SWAP.OKX` |
| 가격 원천 | 기존 `ParquetDataCatalog`의 OKX 1분 외부 Bar |
| 전략 Bar | `5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL` composite Bar |
| OI 원천 | OKX 공개 historical open-interest API. 정확한 endpoint 응답은 구현 시 확인 |
| OI 주기 | 5분. 5분 원자료가 없으면 보간하지 않고 실패 |
| OI 저장 | Nautilus registered Python custom data + `ParquetDataCatalog` |
| A 전략 | 가격 이탈 중 OI 증가, 이후 OI rollover, 밴드 재진입 |
| B 전략 | 가격 이탈 중 OI 감소, 밴드 재진입 |
| BB | 종가 기준 SMA 20, 표준편차 2 |
| ATR | 완료된 5분봉의 Wilder ATR(14) |
| SL | 진입 체결가 기준 `2.5 * ATR`, 진입 시점에 고정 |
| TP | 재진입 신호가 확정된 5분봉의 반대쪽 BB, 진입 시점에 고정 |
| 세션 | `America/New_York` 평일 09:30 이상, 16:00 미만 |
| 세션 종료 | 16:00에 포지션 강제 청산. 주말 포지션 보유 금지 |
| 거래 제한 | 뉴욕 시간 세션당 체결된 신규 진입 최대 3회 |
| 동시 포지션 | 순포지션 1개 |
| 진입 | 재진입 캔들 종가 확인 후 다음 5분봉 시장가 |
| lookahead | 현재 가격봉에 대해 현재봉 이후 또는 동일 시점의 OI를 사용하지 않음 |
| 백테스트 러너 | `BacktestNode`만 사용. 자체 event loop 금지 |
| 라이브 | `TradingNode` 및 기존 live OKX 경로 변경 없음 |

OI는 롱·숏 방향을 직접 판별하지 않는다. OI 변화는 포지션 밀도와 청산 상태를 나타내는 조건으로만 사용한다.

## 3. OI 가설

### 3.1 A: OI Crowding Mean Reversion

가설은 다음과 같다.

> 가격이 BB 바깥으로 이탈하는 동안 신규 포지션이 쌓이고, 이후 OI 증가세가 꺾이면 과밀 포지션의 반대 방향 평균회귀가 발생한다.

롱과 숏 모두 가격 방향과 OI 증가를 함께 요구한다.

- 롱 후보: 가격 하락 + OI 증가
- 숏 후보: 가격 상승 + OI 증가
- 진입 확인: OI 고점 형성 또는 감소 전환 + 가격 BB 재진입

`가격 하락 + OI 상승`을 즉시 롱으로 해석하지 않는다. OI 증가가 계속되는 동안에는 추세 지속 가능성이 있으므로 진입하지 않는다.

### 3.2 B: OI Liquidation Mean Reversion

가설은 다음과 같다.

> 가격이 BB 바깥으로 이탈하는 동안 기존 포지션이 청산되어 OI가 감소하고, 청산 흐름이 소진되면 가격이 평균으로 복귀한다.

- 롱 후보: 가격 하락 + OI 감소
- 숏 후보: 가격 상승 + OI 감소
- 진입 확인: 가격 BB 재진입

B는 청산 데이터가 없는 경우 OI 감소를 청산 소진의 대리 지표로 사용한다. 따라서 A와 B의 결과를 합산하거나 같은 거래로 중복 집계하지 않는다.

## 4. 신호 정의

### 4.1 공통 가격 이벤트

각 완료 5분봉을 `t`라고 한다.

```text
lower[t] = BB 하단
upper[t] = BB 상단

long breach:
    close[t] < lower[t]

short breach:
    close[t] > upper[t]

long re-entry:
    breach 이후 1~5번째 완료 5분봉에서
    close[t] >= lower[t]

short re-entry:
    breach 이후 1~5번째 완료 5분봉에서
    close[t] <= upper[t]
```

첫 번째 이탈 이벤트에 대해 첫 번째 재진입만 사용한다. 5개 봉 안에 재진입하지 못하면 후보를 폐기한다. 후보가 재진입 전에 반대쪽 밴드를 이탈해도 기존 후보를 재사용하지 않는다.

재진입 신호는 해당 캔들 종가에서 확인하고, 실제 주문은 다음 5분봉 시장가로 제출한다. 캔들 종가 체결을 가정하지 않는다.

### 4.2 OI 관찰 구간

OI 비교의 기본 관찰 구간은 `oi_lookback_bars=3`개 완료 OI 포인트다.

```text
oi_return[t] = oi[t-1] / oi[t-1-lookback] - 1
```

현재 가격봉 `t`의 조건은 `oi[t-1]`까지만 사용한다. `oi[t]`가 가격봉과 같은 종료 시각에 존재하더라도 현재 가격봉의 신호 계산에는 사용하지 않는다. 이 보수적 1개 봉 지연으로 custom data와 composite Bar의 동일 timestamp 순서에 따른 lookahead를 차단한다.

초기 기본 임계값은 부호 비교를 위한 0이다.

- A OI 증가: `oi_return >= oi_increase_threshold`, 기본 `0.0`
- B OI 감소: `oi_return <= -oi_decrease_threshold`, 기본 `0.0`

임계값은 config 필드로 노출한다. 초기 백테스트에서는 부호 기반 기준선을 먼저 만들고, 이후 z-score 또는 변동성 정규화 임계값을 별도 그리드로 검증한다.

### 4.3 A rollover

A 후보는 가격 breach 시점의 OI가 관찰 구간에서 증가해야 한다. 후보가 유지되는 동안 OI 최고값을 기록한다.

재진입 시 다음을 요구한다.

```text
latest_oi <= oi_peak_since_breach * (1 - oi_rollover_threshold)
```

초기 `oi_rollover_threshold`는 `0.0`이다. 즉, breach 이후 OI 고점을 만들고 현재 OI가 그 고점보다 낮거나 같아지면 rollover로 인정한다. 이 값은 이후 민감도 검증 대상이다.

B에는 rollover 조건을 추가하지 않는다. B의 가설 자체가 breach 구간의 OI 감소다.

### 4.4 신호 순서

롱과 숏은 동일한 순서를 반대 방향으로 적용한다.

```text
1. 세션 내에서 가격 breach 확인
2. 해당 시점의 지연된 OI 조건 확인
3. pending setup 생성
4. 최대 5개 완료 가격봉 동안 OI 상태 갱신
5. A는 OI rollover를 확인하고, B는 OI 감소 조건을 유지
6. 첫 가격 re-entry에서 다음 봉 진입 주문 제출
```

포지션이 이미 있거나 세션 진입 한도에 도달했으면 setup을 거래하지 않는다. 포지션이 닫히면 pending setup, captured ATR/target, peak OI, entry-order reference를 모두 초기화한다.

## 5. 데이터 모델

### 5.1 `OpenInterestPoint`

모듈: `src/sngw_trader/data/open_interest.py`

순수 Python custom data 클래스를 등록한다.

필드:

| 필드 | 타입 | 의미 |
|---|---|---|
| `instrument_id` | `InstrumentId` | `BTC-USDT-SWAP.OKX` |
| `open_interest` | `float` | OKX 원자료의 일관된 OI 단위 |
| `ts_event` | `int` | OI 관측 시각, UNIX ns |
| `ts_init` | `int` | catalog/replay 초기화 시각, UNIX ns |

OI 단위는 endpoint가 제공하는 단위를 그대로 사용하되, 하나의 catalog 안에서 계약 수와 코인 수를 섞지 않는다. 단위와 endpoint는 catalog metadata 또는 다운로드 로그에 남긴다.

클래스는 Nautilus custom data 등록에 필요한 다음을 제공한다.

- `type_name_static`
- JSON encode/decode
- PyArrow schema
- record batch encode/decode
- `ts_init` 필드

`DataType`은 `OpenInterestPoint`와 instrument metadata를 사용한다. 전략은 payload의 OI 값과 timestamp만 사용하며, OKX HTTP API를 import하지 않는다. `InstrumentId`를 사용해야 catalog가 instrument identifier를 안전하게 추출할 수 있다.

### 5.2 Catalog 적재

모듈: `src/sngw_trader/data/catalog_writer.py` 및 `open_interest.py`

기존 `catalog-download`의 OKX 가격 적재는 유지한다. OI 적재는 `OI_ENABLED=true`일 때 추가한다.

흐름:

1. OKX instrument를 catalog에 적재
2. 기존 1분 가격 Bar를 적재
3. OKX 공개 historical OI endpoint에서 5분 데이터를 페이지 단위로 수집
4. 응답을 `OpenInterestPoint`로 변환
5. 시간순 정렬 및 중복 제거
6. `ParquetDataCatalog.write_custom_data(...)`로 적재

다음 조건은 실패로 처리한다.

- OI 응답 code가 성공이 아님
- 요청 구간에 OI 데이터가 없음
- 5분보다 거친 데이터만 제공됨
- timestamp가 오름차순이 아님
- 동일 구간에서 OI 단위가 변경됨

데이터가 없는 구간을 가격 기준으로 보간하지 않는다. API의 실제 endpoint path, pagination 필드, timestamp 단위, OI 단위는 구현 전에 공개 응답으로 확인한다.

## 6. 백테스트 데이터 흐름

```text
catalog/
  bars/.../BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL
  custom/OpenInterestPoint/...

backtest_oi.py
  BacktestNode.build()
  engine = node.get_engine(...)
  oi = catalog.query_custom_data(...)
  engine.add_data(oi)
  engine.run()

strategy
  subscribe_bars(composite_5m_bar_type)
  subscribe_data(oi_data_type)
  on_data() -> latest completed OI state
  on_bar() -> BB/ATR/session/setup/order logic
```

`BacktestDataConfig`는 가격 `Bar` 로드에 사용한다. Nautilus 버전상 arbitrary custom data를 `BacktestDataConfig`에 직접 지정하지 않고, engine build 후 `engine.add_data(...)`로 추가한다.

custom data와 현재 가격봉이 같은 timestamp에 도착하더라도 전략은 현재 가격봉에 대한 OI를 사용하지 않는다. 따라서 event ordering에 의존하지 않는다.

## 7. 전략 구성

각 모듈은 하나의 `Strategy` 클래스와 해당 `StrategyConfig`를 가진다.

### 7.1 A 전략

파일: `src/sngw_trader/strategies/oi_crowding_mean_reversion.py`

- `OiCrowdingMeanReversionConfig`
- `OiCrowdingMeanReversion`

필수 config:

```text
instrument_id
bar_type
trade_size
bb_period=20
bb_std=2.0
atr_period=14
atr_mult=2.5
reentry_bars=5
oi_lookback_bars=3
oi_increase_threshold=0.0
oi_rollover_threshold=0.0
session_timezone="America/New_York"
session_start="09:30"
session_end="16:00"
max_trades_per_session=3
close_positions_on_stop=True
```

### 7.2 B 전략

파일: `src/sngw_trader/strategies/oi_liquidation_mean_reversion.py`

- `OiLiquidationMeanReversionConfig`
- `OiLiquidationMeanReversion`

A와 공통인 가격·운용 config를 사용하고 다음을 가진다.

```text
oi_lookback_bars=3
oi_decrease_threshold=0.0
```

공통 계산을 순수 helper로 추출할 수는 있지만, Strategy 클래스의 수명주기와 주문 상태를 억지로 상속 계층으로 합치지 않는다. 실제 중복이 확인될 때만 `indicators/` 또는 작은 순수 함수로 추출한다.

### 7.3 주문과 위험

- 주문은 `self.order_factory`와 `self.submit_order`만 사용한다.
- 가격·수량은 instrument의 `make_price`와 `make_qty`로 양자화한다.
- 진입 체결 후 SL과 TP를 reduce-only 주문으로 설치한다.
- 가능하면 Nautilus native bracket/OCO 주문을 사용하고, API 지원 여부는 구현 시 확인한다.
- 전략 안에서 OKX raw order parameter, REST, websocket을 사용하지 않는다.
- SL과 TP가 같은 5분봉에서 모두 닿는 처리는 엔진의 fill model과 테스트 명세에 따른다. 캔들 OHLC만으로 순서를 임의로 낙관 처리하지 않는다.
- 포지션 사이징은 이번 전략의 핵심 비교 대상이 아니므로 초기에는 `trade_size` 고정값을 사용한다. 계좌 위험률 기반 sizing은 별도 설계다.

## 8. 세션과 거래 제한

전략은 모든 timestamp를 `America/New_York`로 변환하여 세션을 판단한다. 고정 UTC offset을 사용하지 않으므로 DST를 처리한다.

- 월요일~금요일만 setup과 신규 진입 허용
- 09:30 이상 16:00 미만에서만 신규 setup과 진입 허용
- 16:00에 보유 포지션을 닫고 pending setup 폐기
- 세션별 체결된 신규 진입 수를 0으로 초기화
- 신규 진입 체결 시 카운터 증가
- 같은 이탈 이벤트에서 재진입 금지
- 동시에 한 instrument의 순포지션 하나만 허용

주말 보유 포지션은 허용하지 않는다. `on_stop`에서도 설정이 켜져 있으면 포지션을 닫는다.

## 9. Runner와 설정

파일: `src/sngw_trader/runners/backtest_oi.py`

러너는 다음만 담당한다.

1. settings 로드
2. OKX venue와 기존 fee/fill/latency 모델로 `BacktestRunConfig` 조립
3. 1분 Bar 데이터 설정
4. `BacktestNode` build
5. custom OI data query 및 engine 추가
6. A 또는 B Strategy 부착
7. `node.run()` 및 report/export
8. dispose

러너에 BB, OI, session 등 매매 조건을 넣지 않는다.

전략 선택은 `OI_STRATEGY` 환경변수로 한다.

```text
OI_STRATEGY=oi_a  -> OiCrowdingMeanReversion
OI_STRATEGY=oi_b  -> OiLiquidationMeanReversion
```

잘못된 값은 실행 전에 `SystemExit`로 거부한다. 기존 `backtest_okx.py`와 기존 전략의 기본 동작은 변경하지 않는다.

`research/`의 walk-forward 통합은 이번 구현의 필수 경로가 아니다. 우선 단일 `BacktestNode`에서 custom data replay와 A/B 결과가 재현되는 것을 확인한다. 이후 grid/walk-forward가 필요하면 custom data injection을 `research/executor.py`에 별도 설계한다.

## 10. 테스트

네트워크에 의존하지 않는 합성 데이터 테스트를 기본 스위트에 넣는다.

### 10.1 Custom data

- `OpenInterestPoint` 생성 및 timestamp 보존
- JSON round-trip
- Arrow record batch round-trip
- `DataType` 등록 및 식별자 보존
- catalog write/query round-trip
- 시간순이 아닌 입력 거부 또는 정렬 규칙 검증

### 10.2 A 신호

- 하단 breach 뒤 OI가 증가하면 롱 후보 생성
- 상단 breach 뒤 OI가 증가하면 숏 후보 생성
- OI 증가가 계속되면 re-entry에서도 진입하지 않음
- OI peak 이후 rollover와 re-entry가 함께 있으면 진입
- 5개 봉 안에 re-entry가 없으면 후보 폐기
- 첫 re-entry만 사용

### 10.3 B 신호

- 하단 breach 뒤 OI가 감소하면 롱 후보 생성
- 상단 breach 뒤 OI가 감소하면 숏 후보 생성
- re-entry에서 롱·숏 방향이 올바르게 선택됨
- OI 증가 상태에서는 B 후보가 생성되지 않음

### 10.4 공통 운용

- 현재 가격봉과 같은 timestamp의 OI를 사용하지 않음
- BB/ATR warm-up 전에는 거래하지 않음
- 세션 밖 setup과 진입을 무시
- DST 전환일의 세션 판정
- 주말 거래 금지
- 16:00 포지션 강제 청산
- 세션당 세 번째 체결 후 네 번째 진입 거부
- TP가 재진입 시점 반대 밴드에 고정됨
- ATR 손절이 진입 시점에 고정됨

### 10.5 Runner

- `OI_STRATEGY=oi_a`가 A config를 생성
- `OI_STRATEGY=oi_b`가 B config를 생성
- 잘못된 전략 이름 거부
- runner가 custom OI data를 engine에 추가
- 기존 `backtest_okx.py`의 가격-only runner 테스트 유지

실제 OKX API 호출은 단위 테스트에 포함하지 않는다. endpoint smoke는 명시적으로 실행하는 수동 검증으로 둔다.

## 11. 비용과 결과 해석

기존 `BacktestNode` 비용 모델을 그대로 사용한다.

- OKX maker/taker fee
- 기존 probabilistic fill model
- 기존 slippage 설정
- 기존 latency 설정
- 시작 잔고 10,000 USDT

펀딩은 기존 `data/funding.py` 후처리 경로를 따른다. 이번 스펙은 펀딩 모델 자체를 엔진에 추가하지 않는다.

A와 B는 다음을 별도 보고한다.

- 거래 수
- 승률
- 평균 거래 손익
- 수수료 전후 손익
- 최대 낙폭
- 롱/숏별 결과
- OI 조건 없는 BB 기준선 대비 차이

A와 B의 거래를 합쳐 하나의 성과로 보고하지 않는다.

## 12. 파일 변경 예정

```text
src/sngw_trader/data/open_interest.py
src/sngw_trader/data/catalog_writer.py                 # OI 적재 분기
src/sngw_trader/strategies/oi_crowding_mean_reversion.py
src/sngw_trader/strategies/oi_liquidation_mean_reversion.py
src/sngw_trader/runners/backtest_oi.py
src/sngw_trader/config/settings.py                     # OI/전략 설정
.env.example                                           # OI 설정 예시
tests/test_data/test_open_interest.py
tests/test_strategies/test_oi_crowding_mean_reversion.py
tests/test_runners/test_backtest_oi.py
```

기존 `backtest_okx.py`, 기존 전략, live OKX runner는 기본 동작을 유지한다. 구현 중 custom data API 제약으로 파일 구성이 달라지면 이 스펙의 파일 목록을 먼저 갱신한다.

## 13. 구현 전 확인 사항

다음 Nautilus/OKX API 세부사항은 구현 시 실제 설치 버전과 공개 응답으로 확인한다.

- `register_custom_data_class`의 import 경로와 등록 방식
- Python custom data의 Arrow schema 요구사항
- `ParquetDataCatalog.write_custom_data`와 `query_custom_data` 시그니처
- `BacktestEngine.add_data`가 `CustomData` wrapper를 받는 방식
- composite Bar의 `on_bar` timestamp와 1분 Bar replay 순서
- native bracket/OCO 주문 생성 API
- OKX historical OI endpoint path, pagination, period, unit, timestamp

확인되지 않은 API를 추측하여 호환성 래퍼로 감싸지 않는다. 설치된 버전에 맞춰 최소 구현한다.

## 14. 범위 밖

- OKX live OI custom data adapter
- funding을 BacktestEngine 내부 이벤트로 재생
- liquidation endpoint를 이용한 별도 전략
- funding, liquidation, taker imbalance를 A/B 신호에 추가
- OI z-score 자동 최적화
- 다중 instrument 또는 ETH
- 자동 walk-forward/grid integration
- 별도 custom event loop 또는 pandas 백테스트 엔진
