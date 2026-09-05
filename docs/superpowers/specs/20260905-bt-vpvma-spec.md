# VPVMA (swing) 백테스트 설계

- 날짜: 2026-09-05
- Handoff: `20260905-bt-vpvma` (40_AGENT_WORKSPACE/inbox, librarian → trading-agent)
- 전략 문서: `20_WIKI/trading/strategies/trading-strategy-vpvma.md` (status: research)
- 근거 논문: raw-trading-0005 (Chio 2022, arXiv:2206.12282) — **full text 확보 완료** (수식·파라미터·시그널 규칙 확정). handoff의 "확보 불가 시 blocked" 조건 불발동.
- 상태: 설계 (구현 전)
- 스코프 격리: 본 spec/result에는 VPVMA 단일 전략 규칙만 기재한다. 유사 지표(MACD)를 쓰는 다른 전략 문서의 규칙·파라미터·비교 서술은 일절 포함하지 않는다. (수식 구현에 필요한 EMA 계산은 공통 인프라이지 전략 규칙 공유가 아니다.)

## 1. 규칙 (raw-trading-0005 full text 그대로 — 추가 규칙 없음)

### 1.1 지표 (논문 §4.1, 식 4.2-1~8)

```
TP    = (High + Low + Close) / 3
SVWMA = Σ(TP·V)/ΣV  over window_fast (12)
LVWMA = Σ(TP·V)/ΣV  over window_slow (26)
DV    = Std(High, Low, Close, Open)  # 당일 4가격 표준편차
ESVMap = EMA(SVWMA · DV, short=12)
ELVMap = EMA(LVWMA · DV, long=26)
VPVMA  = ESVMap − ELVMap
VPVMAS = SMA(VPVMA, 9)
```

- 파라미터: window_fast=12, window_slow=26, window_sign=9, bandwidth=0.1 (논문 Table 8 명시 — 튜닝 금지, 고정)

### 1.2 시그널 (논문 Table 8 그대로)

- Buy: `VPVMA_t > (1 + bw)·VPVMAS_t` and `VPVMA_{t-1} <= VPVMAS_{t-1}`
- Sell: `VPVMA_t < (1 − 2·bw)·VPVMAS_t` and `VPVMA_{t-1} <= VPVMAS_{t-1}` (논문 원문 표기 그대로; 좌우 비대칭 밴드가 의도로 판단)

### 1.3 원문 백테스트 조건 (참고)

- 미국 3대 지수(DJ/Nasdaq/S&P500) 개별주, 2015-01-01~2021-08-28 일봉, 롱온리, 반대 시그널 시 전량 청산, 익절/손절 없음, 거래비용 미반영(논문 명시), 초기자금 $80k/종목.

## 2. 근거 시장 재현 가능성 — limitation (확정)

근거 시장은 미국 개별주 일봉. 카탈로그 보유 데이터는 `BTC-USDT-SWAP.OKX` 1분봉(2019-12-16~2025-12-30)뿐이라 미국 주식 재현 불가. KD-MACD/MACD-crossover 선례와 동일하게 **BTC 일봉 근사 백테스트**로 대체 (자산 클래스 다름 — 재현이 아니라 근사). KOSPI/KOSDAQ 추가 재현도 데이터 부재로 불가 → result에 limitation 기재, 전략 문서 Open Question으로 남음.

## 3. 결정 사항

| 항목 | 결정 |
|---|---|
| 전략 파일 | `src/sngw_trader/strategies/vpvma.py` **단일 클래스** `Vpvma` + `VpvmaConfig`. 롱온리/롱+숏은 config 분기 (`allow_short: bool`) |
| Config | `VpvmaConfig`: `fast=12, slow=26, sign=9, bandwidth=0.10, allow_short=False, trade_size`, vol-target 필드(선례 동일: `sizing_mode="unit"`, `size_target_vol=0.20, size_half_life=20, size_min_scale=0, size_max_scale=3.0, size_rebalance_band=0.10`) |
| 지표 | VPVMA 계산은 신규 `indicators/vpvma.py` (VWMA는 기존 지표에 없음 → pandas/nautilus 표준 수단으로 구현). EMA는 기존 `Macd`의 EMA 컴포넌트 패턴 참고 |
| 봉 | 일봉(1d). 1분 소스봉을 `BarAggregator(86_400)`로 일간 집계 |
| 유니버스 | `BTC-USDT-SWAP.OKX` (카탈로그 가용 유일 종목) |
| 체결 | 봉 마감 신호 → 다음 봉 시가 체결(기존 관례). 레버리지 없음, 기본 unit qty = `trade_size` |
| 방향 변형 | 논문은 롱온리 → 기본 보고는 롱온리. 롱+숏(Sell 시그널에 숏 진입, Buy에 반전)은 config 분기 sensitivity로 병행 (repo 확정 관례) |
| 사이징 | 기본 `sizing_mode="unit"` (논문도 전량 진입 unit 방식). sensitivity 변형: `sizing_mode="vol_target"` (VolTargetSizer, periods_per_year=365) |
| IS/OOS | IS: 2021-01-01~2022-12-31, OOS: 2023-01-01~2025-12-30 (카탈로그 가용 범위 기준). **파라미터 튜닝 금지** — 12/26/9/bw=0.1 고정 |
| 민감도 | 파라미터 최적화 금지. `allow_short`, sizing(unit/vol_target) 변형만. bandwidth 값 조정은 "기대 거래 수 조절용 control variable"(논문 명시)이므로 그리드 스캔 대상에서 제외 (튜닝 위반 방지) |
| 비용 | 기본: OKX taker 0.05% × 2 + 슬리피지 0.01% (repo 관례; 논문은 비용 미반영임을 result에 명시). 스트레스: 0.1% + 0.05% |
| 성과 지표 | CAGR, Sharpe, MDD, win rate, PF, trade count — `research/metrics.py` 재사용 |
| 러너 | `BacktestNode` + ParquetDataCatalog. `research/executor.run_window` 패턴 재사용, 실행 스크립트만 추가 |
| funding | 일봉 보유 기간 존재 → funding 반영 여부 result에 명시 (data 부재 시 무시 + limitation 기재) |

## 4. 파일 구성

```
src/sngw_trader/
  indicators/vpvma.py          # 신규: TP, SVWMA/LVWMA, DV, EMA 래핑, VPVMA/VPVMAS
  strategies/vpvma.py          # 신규: Vpvma + VpvmaConfig (kd_macd_crypto.py 구조 패턴)
research/
  vpvma_compare.py             # 신규 실행 스크립트 (macd_crossover_compare.py 패턴)
tests/test_strategies/
  test_vpvma.py                # 신규 단위 테스트
docs/superpowers/plans/
  20260905-bt-vpvma-plan.md    # 후속 작성
```

### 4.1 `indicators/vpvma.py`

- rolling VWMA: `(TP*V).rolling(n).sum() / V.rolling(n).sum()`
- DV: 당일 High/Low/Close/Open 4값의 표준편차 (표본 std, ddof 결정은 테스트에서 고정 후 result 명시)
- ESVMap/ELVMap: EMA(alpha=2/(n+1), 논문 식 2.1-1 정의) of (VWMA·DV)
- VPVMA = ESVMap − ELVMap; VPVMAS = rolling mean(VPVMA, 9)
- 워밍업 길이: slow + sign + EMA 수렴 버퍼 (result에 명시)

### 4.2 `strategies/vpvma.py`

- `kd_macd_crypto.py` 구조 재사용: `BarAggregator` → 일봉 완성 시 `_decide` → `OrderIntent` → `_execute`
- 시그널: §1.2 그대로. Buy 미발생 구간에서 청산 상태 유지. `allow_short=True`면 Sell 시그널에 숏 진입, Buy 시그널에 반전
- 사이징: unit / vol_target 분기는 `macd_crossover.py`의 `_intents_for` 패턴 그대로
- on_bar 내 네트워크/파일 I/O 없음. 러너/노드 조립 코드 미포함

### 4.3 `research/vpvma_compare.py`

- 변형: `{long_only, long_short} × {unit, vol_target}` — 기본 보고 long_only/unit, 나머지 sensitivity
- `executor.run_window` 재사용, 세그먼트: IS(2021-22) / OOS(2023-25)
- 각 run: `compute_equity_metrics` + `compute_trade_metrics` 출력, JSON 저장 (`logs/vpvma/`)

## 5. 검증 관문 (순서 고정)

1. **단위 테스트**: 합성 시퀀스로 VWMA/DV/VPVMA 값, 비대칭 밴드 시그널 검출, allow_short 분기, unit/vol_target 사이징 검증 (`tests/test_strategies/test_vpvma.py`)
2. **IS** (2021-01-01~2022-12-31, BTC): 기본 비용 + 스트레스 비용, long_only vs long_short (unit 기준)
3. **OOS** (2023-01-01~2025-12-30): 동일 구성, 파라미터 무튜닝 그대로
4. **민감도**: sizing(vol_target) 변형만 — 파라미터 최적화 없음
5. **리포트**: result에 handoff_id, 규칙/파라미터/유니버스/기간/비용, assumption vs verified 구분 기재 → receipt 작성 (accepted)

## 6. 가정(assumption) 목록 — result에 명시

1. 논문 식 4.2-5의 `ELVMap = EMA(SVWMA·DV, s)` 표기는 SVWMA 오타로 판단하고 `EMA(LVWMA·DV, long)`로 구현 (§4.1 본문 서술 "short-term and long-term volume weighted price moving average respectively" 근거)
2. Sell 시그널의 prev 조건이 Buy와 동일한 `<=`로 표기됨 — 논문 원문 그대로 구현 (의도 확인 불가)
3. 근거 시장(미국 3대 지수 개별주) 재현 불가 — 카탈로그 주식 데이터 부재. BTC-USDT-SWAP 일봉으로 **근사** 백테스트 (자산 클래스 다름)
4. KOSPI/KOSDAQ 재현 불가 (데이터 부재) — 전략 문서 Open Question으로 남음
5. DV의 std ddof(표본/모집단) 논문 미명시 → 구현 시 고정 후 result 명시
6. 거래비용: 논문은 미반영 → 본 repo 관례(OKX taker 0.05%×2 + 슬리피지 0.01%) 채택 (더 보수적)
7. 익절/손절 없음 (논문 그대로 — 반대 시그널만 청산)
8. DV·VWMA가 BTC 24시간 시장 특성과 결합 시 미국 주식과 변동성 규모가 다름 — 지표 값 스케일 차이는 비대칭 밴드(비율 기반)라 상쇄되지만 절대 값 비교는 근거 논문과 비교 불가

## 7. Acceptance criteria 대응

- [ ] spec/plan/result/receipt 파일 생성
- [ ] result에 handoff_id `20260905-bt-vpvma` 명시, 다른 전략 문서 내용 배제
- [ ] IS/OOS + 비용 반영 결과 산출, 파라미터 12/26/9/bw=0.1 고정 명시
- [ ] assumption(§6) vs verified(full text 수식·파라미터) 구분 기재
