# Renko + MACD Intraday 백테스트 설계

- 날짜: 2026-09-05
- Handoff: `20260905-bt-renko-macd-intraday` (40_AGENT_WORKSPACE/inbox, librarian → trading-agent)
- 전략 문서: `20_WIKI/trading/strategies/trading-strategy-renko-macd-intraday.md` (status: research)
- 근거 논문: raw-trading-0006 (Asrani et al. 2026, Springer SoCTA 2025, LNNS vol 1981, DOI 10.1007/978-3-032-26373-5_12)
- 상태: 설계 (구현 전)
- 스코프 격리: 본 spec/result에는 Renko + MACD Intraday 단일 전략 규칙만 기재한다. 다른 전략 문서(macd-crossover-core, macd-momentum-combo, kd-macd-crypto, vpvma)의 규칙·파라미터·비교 서술은 일절 포함하지 않는다. (MACD 지표 정의와 `indicators/kd_macd.py`의 Macd 컴포넌트 재사용은 공통 인프라 재사용이지 전략 규칙 공유가 아니다.)

## 1. 규칙 (전략 문서 + 확인 가능한 근거 — 추가 규칙 없음)

- 구조: 가격 이동 기반 **Renko brick** 위에서 MACD 신호. 진입/청산은 brick 확정 시점에 발생하며 시간과 무관 (전략 문서 명시된 구조적 특징).
- 신호: MACD(12/26/9)를 Renko brick 종가 시퀀스에 적용. MACD line이 signal line 상향 crossover → 롱 진입. 하향 crossover → 청산 (allow_short 시 숏 진입). 12/26/9는 문서/논문 미확인 → 관례 기본값 (handoff도 "12/26/9 등 문서 기본값 우선" 지시).
- Renko brick: brick 크기 미확인 (논문 full text 폐쇄 접근 — abstract 이외 확보 불가). → handoff가 명시적으로 **brick size grid 민감도 분석을 요구**하므로 그리드를 실행한다. 이는 handoff 지시에 따른 것이며 파라미터 최적화 아님 — 대표 파라미터 1개(중앙값)를 기본 보고로, 나머지는 민감도로 보고.
- Renko 변환 규칙 (표준 정의 — 논문 미확인 항목이므로 assumption): close-only Renko. brick 크기 B. 가격이 B 이상 이동 시 새 brick 확정 (반대 방향은 2B 필요 — 표준 ATR/고정 brick Renko 규약). brick 종가 시퀀스만 MACD에 입력.
- 당일 청산 (daytrading): 전략 문서 명시 — overnight 보유 없음. 근사 환경(24/7 crypto)에서는 **UTC 일별 세션 종료 시 청산**을 당일 청산 프록시로 채택 (assumption). 다음 세션에서 신호가 유지되면 재진입.
- 익절/손절: 문서에 없음 → 추가하지 않음. 반대 crossover + 당일 청산만.
- position sizing: 문서 미확인 → 기본 unit qty, vol-target은 sensitivity 변형.

## 2. 근거 시장 재현 가능성 — limitation (확정)

- 근거 시장: NSE 개별주 15종, 인트레이 (종목당 93,750+ 데이터 포인트 ≈ 1분봉 12개월). 카탈로그에는 NSE 주식 데이터가 전무 — **재현 불가**.
- 사용자 결정 (2026-09-05): `BTC-USDT-SWAP.OKX` 1분봉 (2019-12-16~2025-12-30)으로 근사 백테스트 + assumption 명시. 자산 클래스·거래 세션이 다른 **근사(approximate) 비교**이며 재현이 아니다.
- KOSPI/KOSDAQ 재현도 데이터 부재로 불가 — result에 limitation 기재 (handoff가 "가능하면" 요구 수준).

## 3. 결정 사항

| 항목 | 결정 |
|---|---|
| 전략 파일 | `src/sngw_trader/strategies/renko_macd_intraday.py` **단일 클래스** `RenkoMacdIntraday` + `RenkoMacdIntradayConfig`. 롱온리/롱+숏은 config 분기 (`allow_short: bool`) |
| Renko 변환 | 신규 `indicators/renko.py`: 1분봉 close 시퀀스 → brick 확정 이벤트 스트림 (`on_close(price) -> list[brick_close]`). 순수 함수형, 단위 테스트 가능 |
| Config | `RenkoMacdIntradayConfig`: `brick_size` (그리드 대상), `macd_fast=12, macd_slow=26, macd_signal=9`, `allow_short=False`, `flatten_daily=True` (당일 청산 프록시), `trade_size`, `sizing_mode="unit"` + vol-target 필드 |
| 봉 | 1분 소스봉 그대로 사용 (일간 집계 안 함 — 인트레이 전략). Renko는 1분 close에서 변환 |
| 체결 | brick 확정(1분봉 마감) 신호 → 다음 1분봉 시가 체결. 레버리지 없음 |
| 유니버스 | `BTC-USDT-SWAP.OKX` (카탈로그 가용 유일 종목) — 근사 |
| 방향 변형 | runner 1종, 실행 config 2종: (A) 롱온리, (B) 롱+숏. 비교 보고 |
| 사이징 | 기본 unit qty. sensitivity: vol_target (VolTargetSizer, periods_per_year=365×1440 아님 — brick 기반이므로 annualization은 Sharpe 산출 시에만 문제; metrics에서 기간 기반 산출 유지) |
| brick 그리드 | handoff 요구 민감도: BTC 변동성 대비 brick 크기 grid (예: 가격의 0.05% / 0.1% / 0.2% / 0.4% / 0.8% — 절대값 아닌 상대 크기로 BTC 가격 구간 무관화, assumption). 기본 보고 = 중앙값 brick, 나머지는 민감도 표 |
| MACD 파라미터 | 12/26/9 고정, 튜닝 금지 (handoff 지시) |
| IS/OOS | IS: 2021-01-01~2022-12-31, OOS: 2023-01-01~2025-12-30. 그리드 전체를 IS에서 관찰하고 OOS에서 동일 파라미터 재평가 (튜닝 아님 — handoff가 grid 민감도 자체를 요구. 단, "가장 좋은 셀"을 성과 주장으로 쓰지 않고 민감도 표 전체를 보고) |
| 비용 | 기본: OKX taker 0.05% × 2 + 슬리피지 0.01%. 인트레이는 거래 횟수가 많아 비용 민감 → 스트레스(0.1% + 0.05%)도 함께 보고. 논문 비용 모델 미확인 (assumption) |
| 성과 지표 | CAGR, Sharpe, MDD, win rate, PF, trade count — `research/metrics.py` 재사용 |
| 러너 | `BacktestNode` + ParquetDataCatalog, `research/executor.run_window` 패턴. 실행 스크립트만 추가 |

## 4. 파일 구성

```
src/sngw_trader/
  indicators/
    renko.py                     # 신규: close-only Renko 변환기
  strategies/
    renko_macd_intraday.py       # 신규: RenkoMacdIntraday + Config
research/
  renko_macd_intraday_compare.py # 신규 실행 스크립트 (executor.run_window 패턴)
tests/
  test_indicators/test_renko.py  # 신규
  test_strategies/test_renko_macd_intraday.py  # 신규
docs/superpowers/plans/
  20260905-bt-renko-macd-intraday-plan.md      # 후속 작성
```

### 4.1 `indicators/renko.py`

- `RenkoBrickBuilder(brick_size)`: `on_close(price) -> list[Decimal]` — 확정된 brick 종가들 반환. 상태: 마지막 brick 종가. 상승: price ≥ last + B → brick(last+B까지, 규약에 따라 복수 brick 허용). 하락: price ≤ last − 2B (방향 전환 시 2B) — 표준 close-only 규약, assumption으로 명시.
- 방향 전환 2B 규약은 그리드 결과에 영향 → 민감도 표에 규약 명시.

### 4.2 `strategies/renko_macd_intraday.py`

- `macd_crossover.py`의 정정된 패턴 준용: `_signed_qty: Decimal`은 OrderFilled 이벤트 누적 (on_event), `_decide(brick_closes) -> [OrderIntent]` 순수 함수, thin `_execute`.
- BarAggregator 대신 RenkoBrickBuilder: 1분봉마다 `on_close`, 확정 brick마다 MACD 갱신 → crossover 검출.
- 당일 청산: UTC 자정 직전 봉에서 flatten intent (`flatten_daily=True`).
- 워밍업: macd_slow + macd_signal brick 수 확보 전까지 신호 없음.

### 4.3 `research/renko_macd_intraday_compare.py`

- 변형: {long_only, long_short} × brick grid × {unit, vol_target} — 기본 보고는 long_only/중앙brick/unit vs long_short/중앙brick/unit.
- IS/OOS 각각, 기본+스트레스 비용. JSON 저장 (`logs/renko_macd_intraday/`).
- trade count가 인트레이에서 크므로 비용 총액도 함께 보고.

## 5. 검증 관문 (순서 고정)

1. **단위 테스트**: Renko 변환(신규 brick, 전환 2B, 복수 brick), MACD crossover on bricks, allow_short 분기, 당일 청산, unit/vol_target 사이징
2. **IS** (2021-01-01~2022-12-31, BTC 1분봉): brick grid 전체 × 방향 2종, 기본 비용
3. **OOS** (2023-01-01~2025-12-30): 동일 그리드 재평가, 파라미터 무튜닝
4. **비용 스트레스 + sizing 민감도**: 중앙값 brick 기준
5. **리포트**: result에 handoff_id, 규칙/파라미터/유니버스/기간/비용/brick 그리드 전체, assumption vs verified 구분 → receipt 작성 (accepted)

## 6. 가정(assumption) 목록 — result에 명시

1. NSE 15종 인트레이 재현 불가 — BTC-USDT-SWAP 1분봉 **근사** (자산 클래스·세션 다름)
2. KOSPI/KOSDAQ 재현 불가 (데이터 부재)
3. 논문 full text 폐쇄 접근 — brick 크기, MACD 파라미터, 비용 모델, walk-forward 세부 모두 미확인. abstract에서 확인된 것: walk-forward 분석 언급, KiteConnect 실행, 12개월 백테스트 + 60일 paper trading, 58.3%/1.65/<5.2%
4. brick 크기: 절대값 아닌 가격 대비 상대 크기 grid로 민감도 대체 (논문 값 미확인)
5. Renko 표준 close-only 규약 (전환 2B) 채택 — 논문 규약 미확인
6. 당일 청산: UTC 일별 flatten을 NSE 세션 청산의 프록시로 채택
7. 12/26/9: 관례 기본값 (handoff 지시대로)
8. 거래비용: 본 repo 관례 채택

## 7. Acceptance criteria 대응

- [ ] result 파일에 handoff_id `20260905-bt-renko-macd-intraday` 명시
- [ ] 규칙·파라미터·유니버스·기간·비용 모델 전부 명시
- [ ] 가정 vs 검증된 사항 구분 기재
- [ ] receipt 작성 (accepted 예상 — 규칙 확정 불가 항목은 그리드 민감도로 handoff가 허용했으므로 blocked 사유 아님)
- [ ] 다른 전략 문서 내용 result 배제
