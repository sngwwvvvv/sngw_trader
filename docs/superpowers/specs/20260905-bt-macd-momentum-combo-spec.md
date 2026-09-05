# MACD + Momentum Combo (RSI/MFI, swing) 백테스트 설계

- 날짜: 2026-09-05
- Handoff: `20260905-bt-macd-momentum-combo` (40_AGENT_WORKSPACE/inbox, librarian → trading-agent)
- 전략 문서: `20_WIKI/trading/strategies/trading-strategy-macd-momentum-combo.md` (status: research)
- 근거 논문: raw-trading-0005 (Chio 2022, arXiv:2206.12282) — **PDF 전문 확보 완료, Table 4에서 결합 규칙 확정**
- 상태: 설계 (구현 전)
- 스코프 격리: 본 spec/result에는 MACD+Momentum Combo 단일 전략 규칙만 기재한다. 다른 전략 문서의 규칙·파라미터·비교 서술은 일절 포함하지 않는다. (`Macd` 인디케이터·`BarAggregator`·`VolTargetSizer`·executor 재사용은 공통 인프라 재사용이지 전략 규칙 공유가 아니다.)

## 1. 규칙 — 논문 전문 Table 4에서 확정 (verified, 더 이상 assumption 아님)

전략 문서의 "결합 규칙 미확정" blocker는 논문 PDF 전문으로 해소됨. 두 변형 모두 동일 구조:

**MACD&RSI**: MACD(12,26,9) + RSI(window=14, lower=35, upper=70)
**MACD&MFI**: MACD(12,26,9) + MFI(window=14, lower=25, upper=70)

- Buy: `MACD_t > Signal_t` (상태 조건 — crossover 아님) AND 최근 6개봉 {t, t-1, ..., t-5}의 모멘텀 지표값이 **전부** ≤ Lower Threshold
- Sell: `MACD_t < Signal_t` (상태 조건) AND 최근 6개봉이 **전부** ≥ Upper Threshold
- 익절/손절/리스크 규칙: 없음 (논문에 없음). Sell 신호만 청산.
- 롱온리 (논문 "Shorting behavior is not considered").

논문 백테스트 가정 (verified): 신호 발생 **익일** 체결, 매수 시 전재산 매수(all-in), 매도 시 전량 매도, 초기 $80k, 비용 없음, Yahoo Finance 일봉, TA-Lib 지표.

논문 보고 수치 (참고, 비용 미반영): win rate MACD&RSI 0.84/0.86/0.78, MACD&MFI 0.69/0.70/0.67 (DJ/Nasdaq/S&P 500, 2015-01~2021-08). 거래 수 MACD 단독 대비 ~1/20 수준 (Dow: 160/334건).

## 2. 근거 시장 재현 가능성 — limitation (확정)

근거 시장은 미국 3대 지수 개별주 + KOSPI/KOSDAQ 재현 요청이 있으나 카탈로그 주식 데이터 전무 (보유: `BTC-USDT-SWAP.OKX` 1분봉 2019-12-16~2025-12-30만). **데이터 미확보로 재현 불가** → 사용자 결정에 따라 BTC 일봉 **근사(approximate)** 백테스트로 대체. 자산 클래스가 다른 근사이며 재현이 아님 — result에 limitation + assumption 명시. 또한 BTC는 거래소 데이터라 주식형 all-in/rebalancing 체결 가정과 차이 존재.

## 3. 결정 사항

| 항목 | 결정 |
|---|---|
| 전략 파일 | `src/sngw_trader/strategies/macd_momentum_combo.py` **단일 클래스** `MacdMomentumCombo` + `MacdMomentumComboConfig`. RSI/MFI는 `momentum: "rsi"|"mfi"` config 분기 |
| 지표 | `Macd`(indicators/kd_macd.py) 재사용. RSI·MFI는 신규 `indicators/rsi.py`, `indicators/mfi.py` (소규모 pure 구현, 논문 §2.3/§2.4 정의). MFI의 typical price·volume은 일봉 필요 → BarAggregator는 volume 보존 확인됨 (인프라 재사용) |
| 신호 모드 | 논문 그대로 **상태(state) 조건 + 6봉 전부(all-of-6)** 조건. crossover 변형 없음 (논문에 없는 규칙 추가 금지) |
| 방향 | **롱온리 vs 롱+숏 2변형 비교** (`allow_short: bool` config 분기, repo convention). short 규칙은 논문 Sell 조건의 미러(사용자 확정): 숏 진입 = `MACD_t < Signal_t` AND 6봉 전부 ≥ Upper; 숏 청산/롱 복귀 = 논문 Buy 조건 충족 시 반전. 롱온리 셀이 논문 근사 헤드라인, 롱+숏은 방향 대칭 확장으로 별도 보고 (result에 assumption 명시 — 논문은 롱온리) |
| 체결 | 봉 마감 신호 → **다음 봉 시가** 체결 (논문 "Trade executes after the day of trading signal" 반영, 기존 관례와 동일) |
| 사이징 | 기본 **all-in** (논문 문면 그대로): 매수 시 가용 현금 전액/시가, 수량은 instrument qty step 내림. 비교 셀: unit qty (`trade_size` 고정). all-in 구현은 포트폴리오 계좌 잔고 조회 기반 — 백테스트 엔진 의존이므로 pure core에서는 target 방향만 결정하고 qty 계산은 `_execute` 단에서 수행 |
| 비용 | **repo 관례 비용 셀만** (사용자 결정): OKX taker 0.05% × 2 + 슬리피지 0.01%, 스트레스 0.1% + 0.05%. 논문의 무비용 수치는 result에서 narrative 참고로만 기재 (셀로 재현하지 않음 — 사용자 결정) |
| IS/OOS | IS: 2021-01-01~2022-12-31, OOS: 2023-01-01~2025-12-30 (카탈로그 가용 범위). **파라미터 튜닝 금지** — 12/26/9 + RSI 14 (35/70) + MFI 14 (25/70) 논문 고정값 |
| 민감도 | 파라미터 그리드 없음. 변형은 {rsi, mfi} × {long_only, long_short} × {all_in, unit} × {기본비용, 스트레스비용}. TP/SL sensitivity 셀 추가 (사용자 결정): ATR(14)×3 stop + 1:1 TP, 롱+숏 변형에만 적용 (KD-MACD 선례 방식, `risk_metrics.DailyAtr/stop_price/bracket_hit` 재사용). 기본 셀은 TP/SL 없음 (논문 규칙 그대로, 반대 신호만 청산) — TP/SL 셀 결과는 sensitivity로만 보고 |
| 성과 지표 | CAGR, Sharpe, MDD, win rate, PF, trade count — `research/metrics.py` 재사용. 논문 지표와의 대응: P&L ratio ≈ PF 참고 |
| 러너 | `BacktestNode` + ParquetDataCatalog. `research/executor.run_window` 재사용, 실행 스크립트만 추가 (`macd_crossover_compare.py` 패턴) |
| 워밍업 | macd_slow+signal + RSI/MFI window + 6봉 조건 버퍼 + start 전 버퍼 |

## 4. 파일 구성

```
src/sngw_trader/
  indicators/
    rsi.py                        # 신규: RSI(14) — Wilder smoothing, 논문 §2.3
    mfi.py                        # 신규: MFI(14) — typical price money flow, 논문 §2.4
  strategies/
    macd_momentum_combo.py        # 신규: MacdMomentumCombo + Config (macd_crossover.py 패턴)
research/
  macd_momentum_combo_compare.py  # 신규 실행 스크립트
tests/test_indicators/
  test_rsi.py, test_mfi.py        # 신규
tests/test_strategies/
  test_macd_momentum_combo.py     # 신규 단위 테스트
docs/superpowers/plans/
  20260905-bt-macd-momentum-combo-plan.md   # 후속 작성
```

### 4.1 `strategies/macd_momentum_combo.py`

- `macd_crossover.py` 참조 패턴: `BarAggregator`(86_400, volume 전달) → 완성 일봉마다 `_decide` → `OrderIntent` → thin `_execute`. position 추적은 `_signed_qty: Decimal` (OrderFilled 누적) 패턴 그대로
- `_decide`: MACD 상태 비교 + 모멘텀 지표 최근 6값 버퍼( deque ) all-of-6 판정 → target ∈ {−1(allow_short), 0, +1}. buy_cond → +1, sell_cond → −1 또는 0, 없으면 홀딩. pure, 엔진 불필요
- TP/SL sensitivity: `use_bracket: bool` config (기본 False). True면 ATR(14)×3 stop + 1:1 TP를 fill가 기준 시딩, `bracket_hit`으로 일봉 high/low 판정 (kd_macd_crypto.py 선례 패턴, `risk_metrics.DailyAtr/stop_price/bracket_hit` 재사용)
- all-in qty: `_execute`에서 `self.portfolio` 계좌 가용 잔고 기반 산출 — pure core 밖이므로 단위테스트는 unit 모드로, all-in은 스모크 런으로 검증
- on_bar 내 네트워크/파일 I/O 없음. 러너/노드 조립 코드 미포함

### 4.2 `research/macd_momentum_combo_compare.py`

- 변형 행렬: {rsi, mfi} × {long_only, long_short} × {all_in, unit} × {base, stress} = 16셀 × {IS, OOS} + TP/SL sensitivity 셀 (long_short × {rsi, mfi} × all_in × base = 2셀)
- 각 run: `compute_equity_metrics` + `compute_trade_metrics`, JSON 저장 (`logs/macd_momentum_combo/`)
- 전체 행렬 실행은 수 분 소요 → terminal background + notify

## 5. 검증 관문 (순서 고정)

1. **단위 테스트**: RSI/MFI 합성 시퀀스 기대값, all-of-6 조건 판정, MACD 상태 조건, unit 모드 진입/청산 인텐트 (`tests/...`)
2. **IS** (2021-01-01~2022-12-31, BTC): rsi/mfi × all_in/unit, 기본 비용 + 스트레스
3. **OOS** (2023-01-01~2025-12-30): 동일 구성, 무튜닝
4. **리포트**: result에 handoff_id, 규칙/파라미터/유니버스/기간/비용, verified vs assumption 구분 → receipt (accepted)
5. 신호 흔적 확인: 6봉 all-of-6 조건 특성상 신호 빈도가 매우 낮을 수 있음 — trade count 0에 가까운 셀은 신호 trace로 원인 구분 (규칙 특성 vs 구현 결함) 후 보고

## 6. 가정(assumption) 목록 — result에 명시

1. 근거 시장(미국 개별주)·KOSPI/KOSDAQ 재현 불가 (데이터 부재) → BTC 일봉 **근사** 백테스트 (자산 클래스 다름, 재현 아님)
2. BTC 체결 구조(24/7, 스프레드)와 주식 all-in 체결 가정의 차이
3. all-in 수량 산출 시 가용 잔고 정의 (백테스트 엔진 계좌 기준) — 논문 "use all the money"의 엔진 구현상 해석
4. 무비용 셀 미수행 (사용자 결정) — 논문 win rate 비교는 narrative 참고 수준으로만 기재
5. TA-Lib 대비 RSI/MFI 자체 구현의 수치 차이 가능성 (Wilder smoothing 정의 준수, 단위테스트로 완화)
6. 롱+숏 변형 및 TP/SL(ATR 1:1)은 논문에 없는 규칙 — 사용자 결정에 따른 방향 대칭 확장/sensitivity이며 논문 규칙이 아님을 명시

## 7. Acceptance criteria 대응

- [ ] spec/plan/result/receipt 파일 생성
- [ ] result에 handoff_id `20260905-bt-macd-momentum-combo` 명시
- [ ] 사용 규칙·파라미터·유니버스·기간·비용 모델 전부 명시
- [ ] verified (논문 Table 4 규칙) vs assumption 구분 기재
- [ ] 다른 전략 문서 내용 배제
