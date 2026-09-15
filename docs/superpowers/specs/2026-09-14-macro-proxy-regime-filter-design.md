# 거시 금융 프록시 2축 레짐 필터 — 섹터 ETF (Stage 0 / Stage 1)

- 날짜: 2026-09-14
- 상태: 개선안 반영 초안 (수정 스펙 리뷰 후 구현 계획)
- 적용 규칙: `NAUTILUS_VIBE_RULES.md`
- 범위: yfinance/FRED 기반 일봉 연구·백테스트. 라이브 데이터 소스·US 섹터 ETF 라이브 체결 없음

---

## 1. 목적

실물 거시지표의 시차를 피해, **가격이 형성되는 금융 프록시**로 시장 국면을 판별한다. 레짐은 매수/매도 알파가 아니라 **하위 섹터 전략의 익스포저·유니버스 게이트**다.

매매 대상은 개별 주식이 아니라 **GICS 섹터 ETF**다. 프록시는 신용·유동성·경기 공통 팩터만 설명하고, 개별 종목의 실적·이벤트 잔차는 설명하지 않는다.

실험 순서:

1. **Stage 0** — 트레이드 없이 분면이 이후 섹터 수익률을 가르는지 (상태인가)
2. **Stage 1** — 고정된 더미 섹터 모멘텀 위에 2축 게이트를 올려 Base 대비 DD/Sharpe를 보는지 (게이트인가)
3. **Stage 2** — 인플레 틸트 (`T5YIE` / `DFII5`). **지금은 하지 않는다.** 2축이 2022에서 약한 것이 보인 뒤에, 같은 Base에 틸트만 켜서 A/B 한다.

---

## 2. 확정된 요구사항


| 항목     | 결정                                                           |
| ------ | ------------------------------------------------------------ |
| 자산     | US 섹터 ETF 일봉. 개별 주식 없음                                       |
| 레짐 축   | Stress, Growth 2축만. 인플레는 Stage 2                             |
| Stress | FRED OAS + VIX/VXV, 동일가중                                     |
| Growth | Yahoo 구리/금 **레벨** 비율의 robust z. 커브·유가 제외                     |
| 소스     | 연구·백테스트는 yfinance + FRED 무료 EOD. 라이브 소스는 별도 스펙으로 분리 |
| 시계     | NYSE 거래일. 장중 웹소켓 없음                                          |
| 숏      | 없음                                                           |
| 현금 이자  | 0 (R4에 무위험금리를 주지 않음)                                         |
| 라이브 체결 | 이 스펙에 없음. OKX만                                               |
| FRED 인증 | `FRED_API_KEY` env. writer/data 모듈만 읽음                         |


### 2.1 v1에서 버린 것 (되살리지 말 것)


| v1                                            | 처리                         |
| --------------------------------------------- | -------------------------- |
| 4분면 그림이 표와 반대 (고스트레스를 오른쪽에 두고 R1을 우측)         | 표의 번호 유지, 그림만 교정           |
| R3 이름 “Mean-Reversion Golden”                 | **Slowdown**               |
| SOFR−IORB, RRP/TGA, 유가 기간구조, HYG/LQD를 레짐 입력으로 | 축에서 제외                     |
| 백테스트 OAS / 라이브 HYG 이중 트랙                      | 금지. 같은 시리즈                 |
| Growth에 `Δ` 구리/금, `Δ` 커브                      | 레벨 구리/금만. `T10Y2Y`는 축 아님   |
| R1 추세 / R3 볼린저 MR 스위칭                         | Stage 1에서 금지 (실험 confound) |
| 페어 트레이딩, HMM, 인덱스 선물, 개별 숏                    | 제외                         |
| 장중 −3σ 서킷                                     | 제외                         |


---

## 3. 4분면 + R0

축: 오른쪽 Stress+, 위 Growth+.

```
                 Growth (+)
                      │
     R1 Risk-On       │      R2 Fragile Rally
     Stress −         │      Stress +
  ────────────────────┼────────────────────
     R3 Slowdown      │      R4 Liquidity Shock
     Stress −         │      Stress +
                      │
                 Growth (−)
```


| 코드  | 상태                            | 이름                |
| --- | ----------------------------- | ----------------- |
| R1  | Stress− / Growth+             | Risk-On Expansion |
| R2  | Stress+ / Growth+             | Fragile Rally     |
| R3  | Stress− / Growth−             | Slowdown          |
| R4  | Stress+ / Growth−             | Liquidity Shock   |
| R0  | |S| &lt; 0.5 and |G| &lt; 0.5 | Neutral           |


R0가 아니면 약한 축도 부호로 분면에 넣는다. 예: `|G|=1.5`, `|S|=0.2` → R0가 아니라 R1 또는 R2.

Stage 0 라벨에는 히스테리시스 없음.

---

## 4. 입력 데이터

### 4.1 레짐 입력 (거래하지 않음)


| 역할       | 시리즈                | 소스                       | 산출                      |
| -------- | ------------------ | ------------------------ | ----------------------- |
| 신용 스트레스  | ICE BofA US HY OAS | FRED `BAMLH0A0HYM2`      | 레벨                      |
| 변동성 기간구조 | VIX / 3M vol       | FRED `VIXCLS` / `VXVCLS` | 비율 `VIXCLS / VXVCLS`    |
| 성장       | 구리/금               | Yahoo `HG=F` / `GC=F`    | 비율 레벨. **같은 CME 날짜 쌍만** |


`CPER`는 2011 상장이라 2008이 안 들어간다. 구리/금은 데이터 전용이며 매매 유니버스에 넣지 않는다.
`HG=F`/`GC=F`는 yfinance가 제공하는 연속선물 시계열을 사용한다. 계약 롤로 인한
레벨 단절과 비정상 스파이크를 Stage 0A에서 검사하고, 검사 결과를 manifest에 남긴다.
별도 계약 연결 알고리즘은 이 스펙 범위 밖이다.

FRED API 키는 `FRED_API_KEY` env에서 읽는다. Yahoo는 기존 writer extra를 사용한다.
FRED/yfinance HTTP 호출은 writer/data 모듈에서만 한다. 전략·runner는 FRED/yfinance를
import하지 않는다.

연구 데이터는 다운로드 시점의 snapshot으로 고정한다. FRED 최신 vintage를 사용하며
ALFRED 기반 point-in-time 복원은 이 스펙 범위 밖이다. 따라서 `D−1` 규칙은 보수적인
공개 지연 정책이지 과거 시점의 실제 vintage를 보장하지 않는다. snapshot ID, 다운로드
시각, 소스 파라미터, feature version을 결과와 함께 보존한다.

### 4.2 평가·매매 유니버스

원래 Select Sector SPDR **9개** (1998–). Stage 0과 Stage 1이 같은 집합이다.


| 티커  | 섹터   | 묶음            |
| --- | ---- | ------------- |
| XLY | 경기소비 | Cyc           |
| XLI | 산업   | Cyc           |
| XLB | 소재   | Cyc           |
| XLF | 금융   | Cyc           |
| XLK | 기술   | 개별 라인 (묶음 제외) |
| XLE | 에너지  | 개별 라인 (묶음 제외) |
| XLP | 필수소비 | Def           |
| XLU | 유틸   | Def           |
| XLV | 헬스케어 | Def           |


```
Cyc = equal(XLY, XLI, XLB, XLF)
Def = equal(XLP, XLU, XLV)
MKT = SPY
```

제외: XLC (2018, 이전 XLK), XLRE (2015, 이전 XLF), SMH/XBI/XOP/ARKK/3x, GLD/HYG (레짐 입력과 동어반복).

참고 라인 (합격 조건 아님): `QQQ`, `IWM`.

InstrumentId: `{SYM}.ARCA` (기존 `yahoo_etf.venue_for`, 기본 ARCA). BarType: `{id}-1-DAY-LAST-EXTERNAL`. `auto_adjust=True`.

---

## 5. 스코어

동일가중, 그리드 없음.


Z_t = \frac{X_t - \mathrm{median}*{60}(X)}{\max(\mathrm{IQR}*{60}, \tfrac{1}{3}\mathrm{IQR}_{252}) \times 0.7413}



S_t = 0.5 Z(\mathrm{OAS})_t + 0.5 Z(\mathrm{VIX}/\mathrm{VXV})_t



G_t = Z(\mathrm{HG}/\mathrm{GC})_t



| 항목        | 값                          |
| --------- | -------------------------- |
| median 창  | 60 NYSE 세션                 |
| IQR floor | `max(IQR_60, IQR_252 / 3)` |
| 워밍업       | 252 세션. 그전 라벨 없음           |
| 채택용 W     | 60                         |
| 강건성       | W=120 **1회만**. 채택 그리드 아님   |
| z0        | 0.5 (box: 둘 다 작을 때만 R0)    |


부호: OAS↑·VIX/VXV↑ → S+. 구리/금↑ → G+.

S, G는 성분 z를 먼저 구한 뒤 합성한다. 원시 OAS와 VIX 비율을 한 시계열로 합치지 않는다.

---

## 6. 캘린더와 시차

마스터 캘린더 = **NYSE 거래일**. 타임존 `America/New_York`.

연구 스케줄(as-of): 08:30 ET에 직전 완료 세션 `D`만 사용.


| 입력                    | 세션 D 종가 이후 허용                          |
| --------------------- | -------------------------------------- |
| 섹터 ETF, SPY, QQQ, IWM | 날짜 ≤ D. 모멘텀 skip-1이라 D 종가는 20일 수익률에 없음 |
| `VIXCLS`, `VXVCLS`    | 날짜 ≤ D                                 |
| `HG=F`, `GC=F`        | 날짜 ≤ D, 둘 다 있는 마지막 CME 날짜의 비율          |
| `BAMLH0A0HYM2`        | 날짜 ≤ **D−1** (당일 OAS 금지)               |


OAS만 한 칸 더 늦다. `shift(1)`과 `date ≤ D−1`을 동시에 쓰지 않는다.

### 6.1 결측

무조건부 ffill 없음. 선형보간 없음. ALFRED vintage 복원 없음.


| 상황                     | 처리                                 |
| ---------------------- | ---------------------------------- |
| OAS 정상 T+1             | 위 표                                |
| NYSE 개장 · 채권/FRED 휴장   | OAS 직전 값, `is_lagged=1`            |
| NYSE 개장 · CME 휴장       | 같은 날짜 쌍의 직전 구리/금 비율, `is_lagged=1` |
| VIX/VXV 결측             | 직전 값, `is_lagged=1`                |
| 같은 입력이 3 NYSE 세션 초과 정체 | 그날 레짐 = R0                         |
| VIX 또는 VXV 없어 비율 불가    | R0                                 |


롤링 60/252는 정렬된 NYSE 행 수다.

CME만 열린 날: 라벨 없음, 거래 없음.

### 6.2 RegimeSnapshot 데이터 계약

Stage 0과 Stage 1은 동일한 연구 snapshot에서 생성된 `RegimeSnapshot`을 사용한다.
Snapshot은 NYSE 세션당 하나이며, 원시 입력값과 파생값을 함께 보관한다. 원본 API
응답 전체를 저장하는 것이 아니라 세션 기준으로 정규화된 값만 저장한다.

```text
RegimeSnapshot
  session_date, ts_event, ts_init

  # 정규화된 원시 입력
  oas, vix, vxv, copper, gold, copper_gold, vix_vxv
  oas_observation_date, vix_observation_date, vxv_observation_date
  copper_observation_date, gold_observation_date
  oas_age, vix_age, vxv_age, copper_gold_age

  # 파생값
  oas_z, vix_vxv_z, growth_z, stress, growth
  regime_code, quality_code

  # 재현성
  feature_version, source_snapshot_id
```

`regime_code`는 `R0=0`, `R1=1`, `R2=2`, `R3=3`, `R4=4`로 저장한다.
`quality_code`는 `invalid=0`, `lagged=1`, `fresh=2`로 저장한다. 사람이 읽는
문자열은 리포터에서 변환한다. 전략은 `stress`, `growth`, `regime_code`,
`quality_code`만 사용하고 원시값을 다시 계산하지 않는다.

`session_date`와 각 입력의 `observation_date`는 별도 필드다. 예를 들어 NYSE
세션 D에 D−1 OAS를 사용하면 `oas_age`와 `oas_observation_date`에 그 사실이
남아야 한다. Stage 0 보고서와 Stage 1 백테스트는 동일한
`source_snapshot_id` 및 `feature_version`을 요구한다.

---

### 6.3 Stage 0A — 데이터 계약·품질 검증

Stage 0 전에 다음을 수행한다.

1. yfinance/FRED 데이터를 다운로드하고 원천 snapshot ID를 만든다.
2. NYSE master calendar에 입력을 정렬한다.
3. as-of, 관측일, age, 결측·정체 상태를 계산한다.
4. robust z, S/G, R0–R4와 `RegimeSnapshot`을 생성한다.
5. 9개 ETF와 레짐 snapshot의 공통 시작일·종료일·결측을 검증한다.
6. 원시 입력과 파생 snapshot의 기간, 행 수, 품질 비율을 보고한다.

Stage 0A가 실패하면 Stage 0과 Stage 1을 실행하지 않는다.

---

## 7. Stage 0 — 분면 프로브

트레이드 없음. 라벨 `T` 이후 섹터 fwd만 본다.

실행: `src/sngw_trader/research/` 프로브. HTTP는 writer/data 모듈만.
BacktestNode 필수 아님. Stage 0과 Stage 1은 같은 `source_snapshot_id`를 사용한다.

fwd: `open[T+1] → open[T+H]`의 H=5/20 세션 수익률. 당일 수익 없음.
Open이 없는 관측치는 종가로 대체하지 않고 해당 관측을 제외하며, 제외 수를 보고한다.

보고: 각 R0–R4 × {9섹터, Cyc, Def, SPY, XLK, XLE}의 fwd 평균·중앙·20일 5% 분위, 레짐 지속기간.
각 레짐의 표본 수·노출 세션 수·비중, R0/lagged 비중, 차이의 block-bootstrap
구간 추정도 보고한다. 5일·20일 forward return의 겹치는 관측은 독립 표본으로
취급하지 않는다.

### 7.1 합격 (아래 전부)

1. R1: Cyc 20d fwd &gt; Def, Cyc ≥ SPY
2. R4: Cyc 좌꼬리(20d 5% 분위)가 Def·R1보다 나쁨, Cyc &lt; SPY
3. R1–R4 지속기간 중앙값 ≥ 10 NYSE 세션 (R0는 따로 보고, 이 기준에 넣지 않음)

추가로 각 레짐은 실험 설정의 `min_regime_sessions`와 `min_regime_share`를
충족해야 한다. 두 값은 Stage 0 실행 전에 고정하고 결과를 본 뒤 바꾸지 않는다.
표본 수가 부족한 레짐의 방향성은 합격 근거로 사용하지 않는다. 개발 구간에서
축·창·z0를 고정한 뒤 검증 구간에서 재평가하며, 최종 테스트 구간은 Stage 1
채택 판정에 사용하지 않는다.

R2·R3는 보고만. 방향 가설로 합격/기각하지 않음.

**2022 XLK 약세 + XLE 강세는 Stage 0 실패가 아니다.** 2축이 인플레를 안 가른다는 관측이며 Stage 2의 이유다.

R0 ≈ SPY는 진단 보고 항목이다. R0와 R1 fwd가 같은지, R4가 지나치게
희소한지를 보고하지만, 결과를 본 뒤 z0를 조정하지 않는다. 이 실험의 z0는
0.5로 고정한다.

W=60이 지속기간만 실패하고 W=120에서만 경제 의미가 보이면, 창을 늘릴지를 따로 논의한다. 120을 잘 나왔다고 채택하지 않는다.

---

## 8. Stage 1 — 더미 모멘텀 + 게이트

Stage 0이 위 합격을 못 하면 Stage 1을 만들지 않는다.

레짐은 시그널을 바꾸지 않는다. **익스포저·유니버스만** 바꾼다. R3에 평균회귀 엔진을 넣지 않는다.

### 8.1 Base (필터 없음)


| 항목    | 값                                             |
| ----- | --------------------------------------------- |
| 유니버스  | 9 SPDR                                        |
| 시그널   | 20일 수익률 skip-1 (`D−21 → D−1`)                 |
| 보유    | 상위 K=3 equal weight                           |
| 리밸런스  | 그 주 **마지막 NYSE 세션** 종가 신호 → **다음 NYSE 세션 시가** |
| 방향    | 롱온리. 숏·스탑·볼타겟·MR 없음                           |
| 현금 수익 | 0                                             |
| 비용    | 편도 5bp, 턴오버 난 레그만                             |


비교 대상은 SPY B&amp;H가 아니라 이 Base다.

### 8.2 Exposure-only overlay (+2축, 1차 채택 대상)


| 레짐  | 익스포저 | 유니버스 | 포트폴리오                         |
| --- | ---- | ---- | ----------------------------- |
| R1  | 100% | 9개   | skip-1 상위 3 EW                |
| R2  | 50%  | 9개   | 상위 3 EW × 50%                 |
| R3  | 50%  | 9개   | 상위 3 EW × 50%                 |
| R4  | 0%   | —    | 현금. **매일** 종가 후 판정, 다음 시가 청산  |
| R0  | 50%  | 9개   | 상위 3 EW × 50%                 |


R2와 R3는 의도적으로 같다. 9개 ETF만으로 퀄리티 사이클주를 나눌 수 없고,
Stage 0에서 R3 MR을 요구하지 않았다. 이 overlay는 유니버스를 바꾸지 않아
레짐의 익스포저 게이트 효과만 검증한다.

### 8.2.1 Defensive mapping (별도 실험)

R2/R3에서 `Def EW × 50%`를 사용하는 변형은 별도 실험으로 둔다. 이는 레짐
게이트와 방어적 유니버스 선택을 동시에 바꾸므로 1차 채택 대상이 아니다.

R4에서 빠져도 주간 신호까지 재진입하지 않는다. 로테이션은 주간, R4 청산만 일간.

R1에서 Cyc를 강제하지 않는다. 진짜 R1이면 20일 모멘텀이 민감·기술을 올린다.

시그모이드·히스테리시스·ATR·페어·숏·인플레 틸트·SGOV 없음.

### 8.3 비교

같은 기간, 같은 비용, 같은 시가 체결:

1. **Base** — 9개 top 3, 100%
2. **Exposure-only** — 9개 top 3, 표의 레짐별 익스포저. 1차 채택 대상
3. **Defensive mapping** — R2/R3에서 Def EW × 50%. 별도 실험
4. **Fixed exposure** — 동일 top 3, 고정 50% 또는 100%. 현금 비중 효과 확인
5. **Placebo gate** — 실제 레짐의 노출 분포·지속기간을 보존한 무작위 게이트

모든 비교는 같은 snapshot, 기간, 비용, 다음 시가 체결 규칙을 사용한다.

### 8.4 Stage 1 합격

+Exposure-only가 Base 대비 **max DD가 줄고 Sharpe는 같거나 낫다.** 단, 최종 테스트
구간에서 판정하며, 평균 투자 비중·CAGR·turnover·거래 수를 함께 보고한다.
고정 50%와 placebo보다 개선되지 않으면 레짐 정보의 기여가 없는 것으로 본다.
거래가 사라져 통계가 비거나 최소 투자 비중·최소 표본 수를 충족하지 못하면
탈락한다. Sharpe만 오르고 DD가 커져도 탈락한다.

실행: `BacktestNode` + `ParquetDataCatalog`. 전략은 `strategies/`에 Strategy+Config만.
러너는 매매 조건을 갖지 않는다. 레짐 z는 `indicators/` 순수 함수이며,
전략은 Snapshot을 다시 계산하지 않는다. 피처 HTTP는 없다.

---

## 9. 아키텍처 (구현 시)

```
data/          FRED + Yahoo writer, RegimeSnapshot catalog writer
indicators/    robust z, S/G, 레짐 라벨 (순수)
research/      Stage 0A/Stage 0 프로브, Stage 1 비교 집계
strategies/    Stage 1 다중 ETF 전략 (시그널 + submit_order만)
runners/       OKX runner와 ETF/레짐 BacktestNode 조립만
```

- writer/data만 FRED/Yahoo를 연다. 전략은 ETF `Bar`와 이미 정렬된
  `RegimeSnapshot`만 본다.
- Snapshot에는 정규화된 원시 입력값과 파생값을 함께 저장한다. Stage 0과 Stage 1은
  같은 `source_snapshot_id`와 `feature_version`을 사용한다.
- ETF/레짐 runner는 9개 ETF Bar와 세션당 하나의 Snapshot을
  `BacktestDataConfig`로 연결한다. 같은 세션의 전체 입력이 도착하기 전에는
  리밸런싱하지 않는다.
- `RegimeSnapshot`은 Nautilus의 catalog/backtest가 직렬화·재생할 수 있는
  custom `Data` 타입으로 구현한다. 커스텀 타입이 현재 설치 버전에서 지원되지
  않으면 구현을 진행하지 않고 실제 API를 확인해 대체 경로를 별도 합의한다.
- `feature_version`과 `source_snapshot_id`가 custom `Data`의 primitive 필드 제약을
  넘으면 이벤트에는 numeric snapshot key만 저장하고 상세 메타데이터는 같은
  catalog 옆 manifest에 둔다.
- `HG=F`/`GC=F`는 catalog에 데이터로만 넣거나 research 입력으로만 쓴다. 주문 대상 아님.
- 한 프로세스에 BacktestNode와 TradingNode를 같이 올리지 않는다.
- US 섹터 ETF를 OKX/`ccxt`/IB로 주문하는 코드를 이 스펙으로 만들지 않는다.

구현 순서: (1) yfinance/FRED writer와 snapshot manifest (2) RegimeSnapshot 및
catalog 직렬화 검증 (3) 데이터 as-of 정렬·Stage 0A (4) Stage 0 프로브
(5) synthetic multi-asset BacktestNode 체결·동기화 테스트 (6) 합격 시에만
Stage 1 전략.

---

## 10. Stage 2 이후 (이 문서 범위 밖)

- 인플레 틸트: FRED `T5YIE`, 선택 `DFII5`. 분면을 8칸으로 쪼개지 않고 슬리브만 수정
- `T10Y2Y`를 XLF vs XLK 틸트로 쓸지
- MOVE, 순유동성 헤어컷, 내부 시장(RSP/SPY) 강등
- 소프트 웨이팅, 히스테리시스
- XLC/XLRE 편입 (표본이 2008을 못 넣음)
- US 브로커 라이브

Stage 2는 Stage 1과 **같은 Base**에 틸트 플래그만 켠 A/B다. 2축 구성을 다시 열지 않는다.
