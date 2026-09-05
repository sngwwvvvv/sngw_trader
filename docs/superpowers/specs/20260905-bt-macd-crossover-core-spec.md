# MACD Crossover Core (swing) 백테스트 설계

- 날짜: 2026-09-05
- Handoff: `20260905-bt-macd-crossover-core` (40_AGENT_WORKSPACE/inbox, librarian → trading-agent)
- 전략 문서: `20_WIKI/trading/strategies/trading-strategy-macd-crossover-core.md` (status: research)
- 근거 논문: raw-trading-0003 (Chong & Ng 2008, FT30), raw-trading-0004 (Rosillo 2013, 스페인), raw-trading-0005 (Chio 2022, arXiv:2206.12282)
- 상태: 설계 (구현 전)
- 스코프 격리: 본 spec/result에는 MACD Crossover Core 단일 전략 규칙만 기재한다. 유사 지표를 쓰는 다른 전략 문서의 규칙·파라미터·비교 서술은 일절 포함하지 않는다. (MACD 지표 정의와 `indicators/kd_macd.py`의 Macd 컴포넌트 재사용은 공통 인프라 재사용이지 전략 규칙 공유가 아니다.)

## 1. 규칙 (전략 문서 그대로 — 추가 규칙 없음)

- 진입: MACD line(EMA12 − EMA26)이 signal line(EMA9 of MACD line)을 **상향 crossover** 시 롱.
- 청산/숏: MACD line이 signal line을 **하향 crossover** 시.
- 파라미터 12/26/9: raw-trading-0005 명시 + 관례 기본값. raw-trading-0003/0004는 full text 미확보로 미확인 — **파라미터 튜닝/최적화 금지**, 12/26/9 고정.
- 익절/손절 규칙: 없음 (문서에 없는 규칙 추가 금지). 반대 crossover만 청산.
- position sizing: 문서에서 미확인 → **기본 unit qty**. vol-target은 sensitivity 변형으로만 병행 (일반 관행이지 검증된 요소가 아니라는 문서 명시 반영).
- 횡보장 필터(ADX 등): 미적용 — 문서에 규칙 없음.

## 2. 근거 시장 재현 가능성 — limitation (확정)

근거 논문 3건 모두 주식 시장(FT30 / 스페인 개별종목 / 미국 3대 지수 개별주)이며, KOSPI/KOSDAQ 재현 요청도 있으나 카탈로그에 주식 일봉 데이터가 전무하다 (보유: `BTC-USDT-SWAP.OKX` 1분봉 2019-12-16~2025-12-30만). FT30 60년·스페인·미국·KOSPI/KOSDAQ 재현은 **데이터 미확보로 수행 불가** → result에 limitation + assumption 명시하고, 사용자 결정에 따라 KD-MACD 선례처럼 BTC 일봉 근사 백테스트로 대체한다. 이는 근거 시장과 자산 클래스가 다른 **근사(approximate) 비교**이며 재현이 아니다.

## 3. 결정 사항

| 항목 | 결정 |
|---|---|
| 전략 파일 | `src/sngw_trader/strategies/macd_crossover.py` **단일 클래스** `MacdCrossover` + `MacdCrossoverConfig`. 롱온리/롱+숏은 config 분기 (`allow_short: bool`) |
| Config | `MacdCrossoverConfig`: `macd_fast=12, macd_slow=26, macd_signal=9, allow_short=False, trigger_mode="cross", trade_size`, vol-target 필드(`sizing_mode="unit"`, `size_target_vol=0.20, size_half_life=20, size_min_scale=0, size_max_scale=3.0, size_rebalance_band=0.10`) |
| 지표 | `indicators/kd_macd.py`의 `Macd` + `crossed_up/crossed_down` 재사용 — **신규 지표 구현 불필요** |
| 봉 | 일봉(1d). 1분 소스봉을 `BarAggregator(86_400)`로 일간 집계 |
| 유니버스 | `BTC-USDT-SWAP.OKX` (카탈로그 가용 유일 종목) |
| 체결 | 봉 마감 신호 → 다음 봉 시가 체결(기존 관례). 레버리지 없음, 기본 unit qty = `trade_size` |
| 방향 변형 | runner 1종, 실행 config 2종: (A) 롱온리 `allow_short=False` — 하향 크로스 청산만. (B) 롱+숏 `allow_short=True` — 하향 크로스 숏 진입, 상향 크로스 반전. 비교 보고 |
| 사이징 | 기본 `sizing_mode="unit"` (unit qty 고정). sensitivity 변형: `sizing_mode="vol_target"` (VolTargetSizer, periods_per_year=365) — 결과는 sensitivity로만 보고 |
| IS/OOS | IS: 2021-01-01~2022-12-31, OOS: 2023-01-01~2025-12-30 (카탈로그 가용 범위 기준). **파라미터 튜닝 금지** — 12/26/9 고정 |
| 민감도 | 파라미터 최적화 금지. 12/26/9 고정 유지, `trigger_mode` cross/state 와 sizing unit/vol_target 만 변형 (sensitivity). fast/slow/signal 그리드 스캔은 handoff의 "최적화된 파라미터 사용 금지" 원칙 위반이므로 수행하지 않음 |
| 비용 | 기본: OKX taker 0.05% × 2 + 슬리피지 0.01% (기존 백테스트 관례). 스트레스: 0.1% + 0.05% |
| 성과 지표 | CAGR, Sharpe, MDD, win rate, PF, trade count — `research/metrics.py` 재사용 |
| 러너 | `BacktestNode` + ParquetDataCatalog. `research/executor.run_window` 패턴 재사용, 실행 스크립트만 추가 |
| funding | 일봉 롱온리/숏 보유 기간 짧지 않음 → funding 반영 여부는 result에 명시 (data 부재 시 무시하고 limitation 기재) |

## 4. 파일 구성

```
src/sngw_trader/
  strategies/
    macd_crossover.py          # 신규: MacdCrossover + MacdCrossoverConfig
research/
  macd_crossover_compare.py    # 신규 실행 스크립트 (kd_macd_compare.py 패턴)
tests/test_strategies/
  test_macd_crossover.py       # 신규 단위 테스트
docs/superpowers/plans/
  20260905-bt-macd-crossover-core-plan.md   # 후속 작성
```

### 4.1 `strategies/macd_crossover.py`

- `kd_macd_crypto.py`의 구조를 그대로 따름: `BarAggregator` → 일봉 완성 시 `_decide` → `OrderIntent` → `_execute`. `Macd` 인디케이터와 `crossed_up/crossed_down`만 사용 (KD 없음).
- 시그널 (`trigger_mode`):
  - cross(기본): MACD line 상향 `crossed_up(prev_macd, prev_signal, macd, signal)` → golden; 하향 cross → death
  - state(sensitivity): (macd > signal) 상태 전이를 golden/death로
- 타깃 방향: golden → +1 / death → 0 (롱온리) or −1 (allow_short) / 신호 없음 → 홀딩
- 사이징: `sizing_mode="unit"`이면 sizer 업데이트만 하고 desired = target × trade_size. `"vol_target"`이면 `VolTargetSizer.desired_qty` 사용, 워밍업 전 unit qty fallback, 보유 중 밴드 리밸런스 (`_intents_for` 패턴 재사용)
- 워밍업: macd_slow + macd_signal + vol half_life×3 일 + start 전 버퍼
- on_bar 내 네트워크/파일 I/O 없음. 러너/노드 조립 코드 미포함

### 4.2 `research/macd_crossover_compare.py`

- 변형: `{long_only, long_short} × {cross, state} × {unit, vol_target}` — 기본 보고는 long_only/cross/unit vs long_short/cross/unit, 나머지는 sensitivity
- `executor.run_window` 재사용, 세그먼트: IS(2021-22) / OOS(2023-25)
- 각 run: `compute_equity_metrics` + `compute_trade_metrics` 출력, JSON 저장 (`logs/macd_crossover/`)

## 5. 검증 관문 (순서 고정)

1. **단위 테스트**: 합성 시퀀스로 MACD 값, 크로스 검출, allow_short 분기, unit/vol_target 사이징 검증 (`tests/test_strategies/test_macd_crossover.py`)
2. **IS** (2021-01-01~2022-12-31, BTC): 기본 비용 + 스트레스 비용, long_only vs long_short (cross/unit 기준)
3. **OOS** (2023-01-01~2025-12-30): 동일 구성, 파라미터 무튜닝 그대로
4. **민감도**: trigger_mode(state), sizing(vol_target) 변형만 — 파라미터 최적화 없음
5. **리포트**: result에 handoff_id, 규칙/파라미터/유니버스/기간/비용, assumption vs verified 구분 기재 → receipt 작성 (accepted)

## 6. 가정(assumption) 목록 — result에 명시

1. 근거 시장(FT30/스페인/미국 개별주) 재현 불가 — 카탈로그 주식 데이터 부재. BTC-USDT-SWAP 일봉으로 **근사** 백테스트 (자산 클래스 다름)
2. KOSPI/KOSDAQ 재현 불가 (데이터 부재) — 전략 문서 Open Question으로 남음
3. 거래비용/IS/OOS 설계는 원문 미확인 → 본 repo 관례(OKX taker 0.05%×2 + 슬리피지 0.01%) 채택
4. 익절/손절 없음 (문서 규칙 그대로 — 반대 crossover만 청산)
5. 12/26/9 파라미터: raw-trading-0005 기준 + 관례 기본값 (raw-trading-0003/0004는 미확인)

## 7. Acceptance criteria 대응

- [ ] spec/plan/result/receipt 파일 생성
- [ ] IS/OOS + 비용 반영 결과 산출
- [ ] result에 handoff_id `20260905-bt-macd-crossover-core` 명시, 다른 전략 문서 내용 배제