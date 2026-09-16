# OI A/B 전략 IS/WF 백테스트 설계 (Binance OI proxy)

- 날짜: 2026-09-17
- 상태: 승인됨
- 선행 문서: `2026-09-16-oi-mean-reversion-design.md` (전략 A/B 자체의 설계)
- 적용 규칙: `NAUTILUS_VIBE_RULES.md`

## 1. 목적

OI A/B 평균회귀 전략의 IS/WF 백테스트를 수행한다. OKX OI 히스토리는 API 소급 한계(약 4~5일)로 백테스트에 쓸 수 없으므로, 과거 구간은 Binance 공개 metrics 데이터를 OI proxy로 사용하고, 향후 실측 OKX OI는 별도 repo의 OI 수집기(Railway)로 축적한다.

이번 범위는 (A) Binance OI 임포터, (B) executor 확장 + IS/WF 실행, (C) OI 수집기 스펙 문서(별도 repo 구현용)다. 라이브 코드 변경은 없다.

## 2. 데이터 제약 (2026-09-17 프로브 결과)

| 항목 | 결과 |
|---|---|
| OKX rubik `open-interest-history` 5m 소급 | **약 4~5일**. 4일 전까지 데이터, 5일 전부터 빈 응답 |
| Binance Vision metrics | `data/futures/um/daily/metrics/BTCUSDT/`, **2020-09-01부터** 무료 zip CSV |
| Binance 5m era | 2023-06-01 파일: 5m 간격 확인. 최근(2026-09): 25m 간격으로 열화. 5m→25m 전환 시점은 임포터 구현 시 파일 간격 검출로 확인 |
| Tardis.dev (OKX OI, 2020-01부터, tick-level) | 유료 구독제. 이번 단계에서는 제외. 신호가 살아있으면 이후 검증 옵션 |
| 카탈로그 상태 | 비어 있음. OI 축적량 0 |

## 3. 확정된 요구사항

| 항목 | 결정 |
|---|---|
| OI 원천 (과거) | Binance Vision metrics CSV. **연구용 proxy로 한정**, 리포트 전체에 "Binance OI proxy" 명시 |
| OI 원천 (향후) | OKX rubik API. 별도 repo OI 수집기가 Railway cron으로 수집. 이 repo는 스펙 문서만 |
| OI 주기 | 5m. 25m era 데이터는 사용하지 않고 스킵 + 5m era 종료일 리포트 |
| 가격 원천 | 기존 OKX `history-candles` 1m Bar → composite 5m Bar (변경 없음) |
| Window | 기본값 유지: IS 6개월 / OOS 3개월 / holdout 6개월 (`compute_windows`). 5m era 기간이 예상보다 짧으면 구현 시 축소 |
| 그리드 | 전략 A/B 파라미터 축소 그리드. 범위는 Binance 데이터 다운로드·트레이드 수 확인 후 결정 |
| 평가 | 기존 파이프라인 재사용: OOS/IS 샤프 비율 robustness, stitched OOS MC bootstrap, B&H 벤치마크, funding, 스트레스 변형(2x fee / 5x slippage) |
| provenance | 카탈로그 메타데이터에 `open_interest_source="binance-vision-metrics"` 기록. OKX 원천과 혼동 금지 |
| 백테스트 러너 | `BacktestNode`만 사용. 자체 event loop 금지 |
| 라이브 | 변경 없음. Binance 데이터는 라이브 경로에 절대 유입되지 않음 |

## 4. 구성 요소

### 4.1 Binance OI 임포터 — `sngw_trader/data/binance_oi.py`

- 일별 `BTCUSDT-metrics-YYYY-MM-DD.zip` 다운로드 → `create_time, sum_open_interest` 컬럼만 사용
- 각 파일에서 타임스탬프 간격 검출. 5m 간격만 `OpenInterestPoint`로 변환, 25m 간격은 스킵하고 era 경계를 리턴
- 기존 `wrap_open_interest` / 카탈로그 write 경로 재사용. 단 provenance 메타데이터는 Binance 원천으로 기록
- 단위 테스트: 간격 검출, 25m 스킵, dedupe, provenance

### 4.2 executor 확장 — `sngw_trader/research/executor.py`

- OI 전략은 `research/executor.py`의 일반 run_window 경로로 실행 불가(CustomData OI 주입, `oi_bar_type` 필요)
- `sngw_trader/runners/backtest_oi.py`의 조립 로직을 경유하는 run_window 변형 1개 추가. `BacktestNode`가 러너라는 규칙 유지, 로직 추가 없음
- 그리드 파라미터는 `GridSpec` 그대로 전달

### 4.3 WF 실행 + A/B 비교 — `sngw_trader/research/oi_mean_reversion_compare.py`

- 전략 A, B를 동일 window/게이트로 실행하는 compare 모듈. 기존 `kd_macd_compare.py` 패턴 준용
- 리포트: IS 최적 파라미터, OOS 성과, OOS/IS 샤프 비율, holdout, 스트레스 변형. 전부 "Binance OI proxy" 라벨

### 4.4 OI 수집기 스펙 — `docs/specs/oi-collector-railway-spec.md`

별도 repo(Railway 배포) 구현용 스펙 문서. 이 repo에서 구현하지 않는다.

- Railway cron 매일 1회: OKX rubik에서 최근 48h fetch(API 깊이 4~5일이므로 overlap 충분) → ts 기준 dedupe → volume에 월별 parquet 파티션 append (`/data/oi/BTC-USDT-SWAP/2026-09.parquet`)
- 멱등성(재실행 안전), gap 감지(예상 row 수 미달 경고), 로컬 pull-sync 절차
- 비목표: bar/타 심볼 수집 없음, 주문 없음, 라이브 트레이딩과 무관

## 5. 실행 순서

1. C: 수집기 스펙 문서 작성 — 수집 시작이 늦어지면 실측 OKX OI 확보가 늦어지므로 최우선
2. A: Binance OI 임포터 + 카탈로그 적재, 5m era 경계 확인 → 그리드 축소 범위 확정
3. B: executor 확장 + compare 모듈 + IS/WF 실행·리포트

## 6. 리스크

| 리스크 | 대응 |
|---|---|
| Binance OI가 OKX OI와 상이(venue 불일치) | 리포트 전체 proxy 라벨. 실측 OKX OI 축적 후 재검증 단계 명시 |
| 5m era가 짧아 window 수 부족 | era 확인 후 window 축소(IS/OOS 개월 수 env로 이미 조절 가능) |
| 수집기 volume 유실 | 스펙에 주기적 pull-sync 절차 포함 |
| 세션 로직(NY 09:30-16:00)이 24시간 시장에서 과도한 제한 | 첫 oneshot에서 트레이드 수 확인, 파라미터(session_start/end)를 그리드 축에 포함 여부 판단 |
