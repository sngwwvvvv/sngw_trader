# KD + MACD Combo (crypto) 백테스트 설계

- 날짜: 2026-09-05
- Handoff: `20260905-bt-kd-macd-crypto` (40_AGENT_WORKSPACE/inbox, librarian → trading-agent)
- 전략 문서: `20_WIKI/trading/strategies/trading-strategy-kd-macd-crypto.md` (status: research)
- 근거 논문: raw-trading-0007 — Wang & Huang 2026, "A Hybrid SVR-Based Framework for Cryptocurrency Price Forecasting and Strategy Backtesting", Applied Artificial Intelligence 40(1), DOI 10.1080/08839514.2026.2612793 (CC BY-NC, gold OA)
- 상태: 설계 (구현 전)

## 1. 핵심 진전: 규칙 확정 (handoff의 미확인 항목 해소)

Handoff 작성 시점에는 KD-MACD 규칙이 미확인이었으나, 이 spec 작성 과정에서
**논문 full text(tandfonline gold OA)와 저자 배포 Zenodo 데이터셋(DOI 10.5281/zenodo.15268767)을 확보**해 규칙을 확정했다.

### 1.1 논문에 명시된 규칙 (검증된 사실, 출처: full text §"A combined KD-MACD-based strategy")

- **진입(매수)**: KD 골든크로스(K가 D를 상향 돌파) **그리고** MACD bar > 0
- **청산(매도)**: KD 데드크로스(K가 D를 하향 돌파) **그리고** MACD bar < 0
- 지표 정의 (논문 식 2~11):
  - RSV = (C − L_n) / (H_n − L_n) × 100, n일 창
  - K_t = α·RSV_t + (1−α)·K_{t−1}, D_t = α·K_t + (1−α)·D_{t−1}
  - MACD: DIF = EMA12 − EMA26, DEA = EMA9(DIF), bar = DIF − DEA
- 유니버스: BTC, ETH, XRP, LTC (Yahoo Finance 일봉 종가, 2018-01-01 ~ 2020-12-31)
- 테스트 기간: 2020-07-01 ~ 2020-12-31 (학습 2018-01-01~2020-06-30은 SVR용 — **KD-MACD 규칙은 SVR과 무관**)
- 논문 백테스트 조건: 일봉 종가 체결, 레버리지 없음, **수수료/슬리피지 없음** (논문 스스로 명시)
- 논문 결과 (§Table 6, text): Ripple 최고 75%, LTC·BTC가 그 뒤, ETH 음수. 종목당 약 20회 거래. Segment 2(2020-11~12)에서 손실 확대.

### 1.2 파라미터 역검증 (Zenodo 데이터로 수행, 검증된 사실)

- `bitcoin_technical_indicators.csv`의 K/D를 역산한 결과 **n=9, α=0.5**가 최적 적합
  (n∈3..30 × α∈{1/3, 0.5} 그리드, H/L 사용 시). 논문 본문은 "α generally 1/3"로 기술 — **본문과 데이터 불일치**. 데이터 적합(α=0.5)을 채택하되 result에 assumption으로 병기.
- `Macd` 컬럼 = EMA12 − EMA26 (DIF) 확인: 재계산 R²≈1.0, 상대오차 <0.05%.
- MACD 12/26/9는 논문 본문 명시와 데이터 일치.

### 1.3 규칙 재현 검증 (참고, 미확정)

- Zenodo `*_strategy3_kd_macd.csv`는 **예측가 기반 지표**를 담음 (2020-08-11~12-31, 143일).
- 실제 종가 + 2018년부터 워밍업한 지표로 "K>D state & DIF>0" 변형을 돌리면
  Ripple 74.5% (~논문 75%), 거래 15회 (~논문 ~20회) — 대체로 재현되나 ETH 부호 등 세부는 불일치.
- **판단**: 논문 수치의 정밀 재현은 보류(트리거 방식 state/cross, 실행가 예측/실제 등 변형이 갈리고 논문 표 수치는 이미지라 텍스트 확보 불가). 스펙은 규칙(§1.1)을 충실히 구현하고, 논문 기간 재현은 "근사 비교"로 보고한다.

## 2. 결정 사항

| 항목 | 결정 |
|---|---|
| 전략 파일 | `src/sngw_trader/strategies/kd_macd_crypto.py` **단일 클래스** `KdMacdCrypto` + `KdMacdCryptoConfig`. 롱온리/롱+숏은 클래스 분리가 아니라 config로 분기 (`allow_short: bool`) — 규칙상 "전략 파일은 러너를 조립하지 않고, 러너는 매매 조건을 갖지 않는다" |
| Config | `KdMacdCryptoConfig`: `kd_n=9, kd_alpha=0.5, macd_fast=12, macd_slow=26, macd_signal=9, allow_short=False, trade_size`, vol-target 필드(`sizing_mode, size_target_vol, size_half_life, size_min_scale, size_max_scale, size_rebalance_band`) |
| 봉 | **일봉** (1d). 1분 소스봉을 `BarAggregator(86_400)`로 일간 집계 — err_momentum_regime 패턴 재사용 |
| 유니버스 | `BTC-USDT-SWAP.OKX` (카탈로그에 1분봉 존재하는 유일 종목). ETH/XRP/LTC는 카탈로그 데이터 없어 **제외하고 result에 limitation 명시** |
| 체결 | 봉 마감 시장가 → 다음 봉 시가 체결 (기존 관례). 레버리지 없음, unit qty = `trade_size` |
| 방향 변형 | **runner는 1종, 실행 config 2종**: (A) 롱온리 `allow_short=False` — KD 데드크로스+bar<0이면 청산만. (B) 롱+숏 `allow_short=True` — 데드크로스+bar<0에서 공매도 진입, 골든크로스+bar>0에서 반전. 비교는 research 스크립트에서 두 config를 동일 기간 실행 |
| vol targeting | 두 변형 모두 `VolTargetSizer` (Harvey-style, `indicators/vol_targeting.py` 재사용) 적용. 일봉 기준: `periods_per_year=365`, 기본 `target_vol=0.20, half_life=20, min_scale=0, max_scale=3.0, rebalance_band=0.10`. 포지션 중에도 일봉마다 리밸런스 밴드 체크 (`_sync_size(allow_resize=True)` 패턴) |
| 트리거 방식 | cross 기반(§1.1 문자 해석)을 기본으로 하고, state 기반(조건 성립 전이)은 sensitivity 변형으로 병행 — 논문 문장이 모호하기 때문 |
| IS/OOS | IS: 2021-01-01~2022-12-31, OOS: 2023-01-01~2025-12-30 (카탈로그 가용 범위 기준). **파라미터 튜닝 금지** — 논문 기본값 12/26/9, n=9 고정 |
| 논문 기간 재현 | 2020-07-01~2020-12-31 (카탈로그 커버) 별도 세그먼트로 보고. Yahoo BTC-USD vs OKX 스왑 차이는 assumption 명시 |
| 비용 | 기본: OKX taker 0.05% × 2 + 슬리피지 0.01% (기존 백테스트 관례). 스트레스: 0.1%+0.05% |
| 성과 지표 | CAGR, Sharpe, MDD, win rate, PF, trade count — `research/metrics.py` 재사용 |
| 러너 | `BacktestNode` + ParquetDataCatalog. 러너 무변경, `research/compare_strategies.py` / `executor.run_window` 패턴으로 실행 스크립트만 추가 |
| 제외 | SVR 예측가 트랜잭션(논문 방식) — 규칙 전략과 무관. funding(일봉 롱온리 홀딩 짧아 무시하고 명시) |

## 3. 파일 구성

```
src/sngw_trader/
  strategies/
    kd_macd_crypto.py        # 신규: KdMacdCrypto + KdMacdCryptoConfig
research/
  kd_macd_compare.py         # 신규 실행 스크립트 (compare_strategies.py 패턴)
docs/superpowers/plans/
  20260905-bt-kd-macd-crypto-plan.md   # 후속 작성
```

### 3.1 `strategies/kd_macd_crypto.py`

- 인디케이터: 일봉 완성 시마다
  - RSV(9일 high/low/close) → K = 0.5·RSV + 0.5·K_prev, D = 0.5·K + 0.5·D_prev (초기 K=D=50)
  - EMA12, EMA26 → DIF → DEA=EMA9(DIF) → bar = DIF − DEA
- 시그널 (cross 기본, `trigger_mode`로 state 전환 가능):
  - golden = K가 D 상향돌파 && bar>0 / death = K가 D 하향돌파 && bar<0
- 타깃 방향 (`allow_short` 분기):
  - `allow_short=False` (롱온리): golden → +1, death → 0
  - `allow_short=True` (롱+숏): golden → +1, death → −1
- 사이징: `VolTargetSizer.desired_qty(target, trade_size)` — 워밍업 전(scale None)이면 unit qty 그대로. 포지션 보유 중 일봉마다 `should_rebalance` 밴드 체크 후 리사이즈
- 워밍업: max(kd_n, macd_slow+macd_signal) + vol half_life×3 일 + start 전 버퍼
- on_bar 내 네트워크/파일 I/O 없음 (rules 준수). 러너/노드 조립 코드 미포함

### 3.2 `research/kd_macd_compare.py`

- 변형 4종 × 세그먼트: `{long_only, long_short} × {cross, state}` — 단, 기본 보고는 long_only/cross vs long_short/cross 비교이고 나머지는 sensitivity
- `executor.run_window` 재사용, 세그먼트: 논문(2020H2) / IS(2021-22) / OOS(2023-25)
- 각 run: `compute_equity_metrics` + `compute_trade_metrics` 출력, JSON 저장 (`logs/kd_macd/`)

## 4. 검증 관문 (순서 고정)

1. **단위 테스트**: 합성 시퀀스로 KD/MACD 값, 크로스 검출, allow_short 분기, vol-target 리사이즈 검증 (`tests/test_strategies/test_kd_macd_crypto.py`). Zenodo bitcoin_technical_indicators.csv 값과 K/D/DIF 일치 확인(허용오차) 포함.
2. **논문 기간 세그먼트** (2020H2, BTC): 거래 수, 수익률을 논문 text 서술과 정성 비교.
3. **IS/OOS** (2021~2022 / 2023~2025, BTC): 기본 비용 + 스트레스 비용, long_only vs long_short (cross 기준) 비교가 본 보고, state 트리거는 sensitivity.
4. **민감도**: 파라미터 고정(최적화 금지). kd_n ∈ {5,9,14}만 robustness 참고로 부착 (선택 결과 아님).
5. **리포트**: result에 handoff_id, 규칙/파라미터/유니버스/기간/비용, assumption vs verified 구분 기재 → receipt 작성.

## 5. 가정(assumption) 목록 — result에 명시

1. α=0.5 (논문 본문은 1/3 기술하나 데이터 적합은 0.5)
2. Yahoo BTC-USD 일봉 대신 OKX BTC-USDT-SWAP 일봉으로 논문 기간 근사 재현
3. 논문 "predicted prices used for transactions"는 미반영 (SVR 요소, 규칙과 무관)
4. ETH/XRP/LTC 유니버스 제외 (카탈로그 데이터 부재)
5. 논문 표 6의 정확 수치는 이미지라 미확보 — text 서술치(75% 등)와만 비교

## 6. Acceptance criteria 대응

- [x] 규칙 확정 — full text + Zenodo 데이터 검증 완료 (§1)
- [ ] spec/plan/result/receipt 파일 생성
- [ ] IS/OOS + 비용 반영 결과 산출
- [ ] result에 handoff_id `20260905-bt-kd-macd-crypto` 명시, 다른 전략 문서 내용 배제
