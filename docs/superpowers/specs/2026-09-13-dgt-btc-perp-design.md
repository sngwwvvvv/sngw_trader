# Dynamic Grid Trading (DGT) BTC Perp 설계 (초안)

- 날짜: 2026-09-13
- 상태: 초안 (사용자 검토 대기)
- 핸드오프: `20260906-bt-crypto-alpha-papers` / raw-trading-0012 (arXiv:2506.11921)
- 적용 규칙: `NAUTILUS_VIBE_RULES.md`

이 문서는 5편 중 **DGT만** 다룬다. AdaptiveTrend, on-chain flows, MacroHFT, DRL 과적합 방법론은 범위 밖이다.

## 1. 목적

논문 Dynamic Grid-based Trading을 이 저장소 규약으로 백테스트한다.

- 상품은 Binance spot가 아니라 `BTC-USDT-SWAP.OKX`
- 시세는 catalog 1분봉만 사용한다
- 체결·수수료·슬리피지·지연은 다른 전략과 같은 venue 모델이다
- 펀딩은 엔진 밖 후처리로 제외/포함을 병기한다
- 평가는 기존 walk-forward다. 논문 IRR 숫자를 맞추는 것이 목표가 아니다

저자 공개 코드는 체결 시뮬이 아니라 닫힌 식 회계이고, DGT 그리드는 등차다. 이 스펙은 논문 Algorithm 1(등비 그리드 + 동적 리셋)을 Nautilus 주문으로 다시 집행한다.

## 2. 확정된 요구사항

| 항목 | 결정 |
|---|---|
| 상품 | `BTC-USDT-SWAP.OKX` |
| 데이터 | catalog `BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL` |
| 방향 | 롱온리. 숏 금지 |
| 계좌 | 시작 10,000 USDT, 중간 입금 없음 |
| 인벤토리 | 백(`bag_qty`) / 워킹 그리드(`grid_qty`) 분할. NETTING 순포지션 = 합 |
| 그리드 | geometric. 레벨 `P * (1+k)^i`, `i = -h..+h` |
| 집행 | 워킹 래더 전체 LIMIT. 기존 `ProbabilisticFillModel` + `OkxRateFeeModel` + latency 200ms |
| 리셋 | `reset_enabled=true` → DGT. `false` → 전통 그리드(경계에서 종료) |
| IS 선택 | equity Sharpe + 이웃 중앙값 평탄화. 거래 Sharpe 사용 금지 |
| WF | IS 6개월 / OOS 3개월 / holdout 6개월 / 스텝 3개월 (전역 기본값, 변경 없음) |
| 워밍업 | 이 런만 `WF_WARMUP_DAYS=1` |
| 창 종료 | 백 강제 청산 없음 (`close_positions_on_stop=false`) |
| 펀딩 | `data/funding.py` 후처리. stitched OOS와 holdout에 제외/포함 병기 |
| MC | 거래 iid 부트스트랩 사용 안 함. DGT 리포트 `mc`는 null + 사유 |
| 사이징 | vol targeting / ATR 스톱 없음 |
| 러너 | 새로 만들지 않음. `BacktestNode` + 기존 `build_run_config()` |

## 3. 스팟 → 퍼프 매핑

논문은 스팟 USDT/COIN이다. 퍼프 단일 계좌에서는 다음이 동일하다.

| 논문 | 이 저장소 |
|---|---|
| USDT | 미사용 마진 (포지션 밖 현금) |
| COIN | 롱 수량. 매도는 롱 축소만 |
| 상한 리셋 | `grid_qty`만 청산. `bag_qty` 유지. 회수 현금으로 새 워킹 그리드 |
| 하한 리셋 | `grid_qty`를 `bag_qty`에 합침. 새 원금 = 그때 현금. 외부 입금 없음 |
| 아비트라지 이익 < 원금 | 저자 `fund_next_grid`의 외부 보전을 하지 않음. 그리드를 줄이거나, 최소 수량을 못 채우면 새 그리드를 열지 않고 백만 보유 |

`bag_qty`는 이후 매도로 줄이지 않는다. 워킹 매도 수량은 항상 `grid_qty` 이하다. 매도 주문은 `reduce_only=True`로 숏을 막는다.

펀딩 대상은 순롱 전체(`bag_qty + grid_qty`)다.

## 4. 전략

### 4.1 Config

모듈: `src/sngw_trader/strategies/dynamic_grid.py`  
클래스: `DynamicGridConfig` / `DynamicGrid` (같은 파일, `Strategy` 한 개).

| 필드 | 의미 |
|---|---|
| `instrument_id` | `InstrumentId` |
| `bar_type` | 1분봉 `BarType` |
| `grid_size` | `k`, 등비 비율. 예: 0.01 |
| `grid_numbers_half` | `h`. 총 칸 `n = 2h`, 레벨 수 `n+1` |
| `reset_enabled` | `true` DGT / `false` 전통 종료 |
| `close_positions_on_stop` | 기본 `false` |

원금 `M`은 config에 두지 않는다. 계좌 잔고에서 읽는다. 시작 시 `M` = 가용 USDT (백테스트 시작 잔고 10,000).

시크릿, 노드, OKX adapter import 금지.

### 4.2 레벨

중심가 `P` (그리드 시작/리셋 시점의 기준가):

```
level(i) = P * (1+k)^i    for i in {-h, ..., 0, ..., +h}
lower    = level(-h)
upper    = level(+h)
```

가격/수량은 `instrument.make_price` / `make_qty`로 양자화한다. 양자화 결과가 0이면 그 레벨 주문은 내지 않는다. `h=10`이고 BTC 가격이 높으면 칸당 명목이 OKX 최소수량(대략 0.01 BTC) 아래로 떨어질 수 있다. 그때 그 조합은 더 성긴 그리드처럼 동작한다. 계좌를 키우지 않는다.

### 4.3 시작

첫 유효 1분봉에서:

1. `P = bar.close`. 레벨을 계산한다
2. 현금 `M/2`에 해당하는 수량만큼 **시장가 매수** → `grid_qty` (taker). 최소수량 미만이면 그리드를 시작하지 않는다
3. 나머지 현금 `M/2`는 워킹 매수 여력
4. 워킹 LIMIT 래더를 올린다

워킹 현금은 전략 내부 장부다. 백의 미실현 손익을 다음 그리드 원금에 넣지 않는다. 주문 수량이 계좌에서 거부되면 그 주문은 실패로 두고, 대기 루프나 재시도 루프를 만들지 않는다.

### 4.4 래더 수량 (경로 일관)

논문은 레벨을 건널 때마다 “위쪽 남은 회색 칸 수 `G_i`분의 1 매도 / 아래쪽 `G_j`분의 1 매수”다. 래더를 한 번에 올리면, 각 주문을 **지금 보유량의 1/G**로 잡으면 합이 `grid_qty`를 넘는다.

그래서 래더를 쌓을 때 수량은 **순차 경로의 증분**이다.

- 매도 `i = 1..h` (중심에서 위로): `G_i = h-i+1`, `sell_i = remaining_qty / G_i`, 그다음 `remaining_qty -= sell_i`
- 매수 `j = 1..h` (중심에서 아래로): `G_j = h-j+1`, `cash_j = remaining_cash / G_j`, `buy_j = cash_j / level(-j)`, 그다음 `remaining_cash -= cash_j`

부분 체결 후에는 미체결을 취소하고, **현재** `grid_qty`와 워킹 현금으로 남은 방향의 래더만 다시 계산한다.

매수 LIMIT은 `level(-j)`, 매도 LIMIT은 `level(+i)`, 매도는 `reduce_only`이며 합은 `grid_qty`를 넘지 않는다. 백 수량으로는 매도 주문을 만들지 않는다.

한 1분봉이 여러 레벨을 관통하면 여러 건 체결을 허용한다. 논문의 “분당 ≤1회”는 가정으로 기록만 하고 강제하지 않는다.

### 4.5 리셋 / 종료

상한: 봉 처리 후 가격이 `upper`를 넘거나 워킹이 위 끝에 도달.

- `grid_qty`를 시장가로 청산 (taker). `bag_qty`는 유지
- `reset_enabled=true`: 회수 현금을 새 `M`으로, `P` = 이탈 시점 가격, 4.3부터 반복. 새 `M/2`가 최소수량 미만이면 새 그리드를 열지 않는다
- `reset_enabled=false`: 주문 전부 취소. 추가 매매 없음. 백만 있으면 보유

하한: 가격이 `lower` 아래이거나 워킹이 아래 끝에 도달.

- `bag_qty += grid_qty`, `grid_qty = 0`. 워킹 주문 취소
- `reset_enabled=true`: 새 `M` = 그때 현금. 4.3 반복. 현금이 부족하면 새 그리드 없이 백만 보유
- `reset_enabled=false`: 추가 매매 없음

룩어헤드: 완성된 1분봉만 구독한다. 미래 봉을 쓰지 않는다. 체결 가격은 전략이 합성하지 않고 엔진 fill model이 정한다.

### 4.6 주문 ID

OKX client order id는 하이픈 금지, 영숫자, 최대 32자. 기존 러너 설정을 따른다 (`use_hyphens_in_client_order_ids=False`). 그리드는 주문이 많으므로 id가 32자를 넘지 않게 한다.

## 5. 비용

엔진 안 (다른 전략과 동일, `build_run_config`):

- maker 0.0002 / taker 0.0005 (`OkxRateFeeModel`). LIMIT은 maker, 시장가(시작 매수·리셋 청산)는 taker
- `prob_fill_on_limit=0.7`, `prob_slippage=0.1`
- latency 200ms
- 시작 잔고 10,000 USDT, OMS `NETTING`, `AccountType.MARGIN`, `book_type=L1_MBP`

엔진 밖:

- `funding.py`로 OOS·holdout fills에서 포지션 재구성 × OKX `BTC-USDT-SWAP` 펀딩 히스토리
- 리포트에 펀딩 제외 equity 지표와 펀딩 포함 지표를 둘 다 적는다
- 백테스트 엔진에 펀딩을 넣지 않는다

논문 일괄 8bps는 쓰지 않는다.

## 6. 리서치 오케스트레이션

새 러너를 만들지 않는다. 창 분할·반복 실행·집계만 `research/`가 한다.

### 6.1 파라미터 그리드

`research/grids/dgt_btc.json`:

```json
{
  "strategy_path": "sngw_trader.strategies.dynamic_grid:DynamicGrid",
  "config_path": "sngw_trader.strategies.dynamic_grid:DynamicGridConfig",
  "select": "equity_sharpe",
  "fixed": {},
  "grid": {
    "grid_size": [0.005, 0.01, 0.02, 0.05],
    "grid_numbers_half": [3, 5, 10],
    "reset_enabled": [true, false]
  }
}
```

24칸. `reset_enabled=false`가 핸드오프의 0/무보정(전통 그리드)이다.

`walk_forward.py`는 `select`가 `equity_sharpe`이면 `compute_equity_metrics(...).sharpe`로 고른다. 게이트는 닫힌 포지션 수가 아니라 **equity mark ≥ 2**. 지표가 `None`(잔고 ≤ 0 등)이면 탈락. 이웃 중앙값 평탄화는 유지한다. 다른 전략 JSON은 기존 거래 Sharpe 기본값을 유지한다.

전역 `WalkForwardConfig` 기본값(6/3/6, min_trades 등)은 바꾸지 않는다. DGT 실행 시에만 `WF_GRID_PATH=research/grids/dgt_btc.json`, `WF_WARMUP_DAYS=1`.

### 6.2 본평가 (walk-forward)

기존 파이프라인:

- catalog 전체 구간 자동 감지 (`WF_DATA_START/END` 없으면)
- IS 6개월 그리드 서치 → 선택 1개 → OOS 3개월
- 3개월 스텝, 마지막 6개월 holdout 1회
- OOS를 이어 붙여 stitched OOS
- naive baseline: 기존 B&H (`bh_metrics`)
- 주 지표: equity CAGR, Sharpe, MDD, Calmar
- 체결 수는 활동량으로만 기록

창이 끝날 때 백을 팔지 않는다. 미실현은 analyzer equity에만 들어간다.

### 6.3 진단 (논문 구간, 승격 금지)

2021-01-01 ~ 2024-07-31 UTC, `reset_enabled=true` 12칸만. 각 칸 `run_window` 1회. B&H와 비교. WF 없음. 라벨은 IS-only.

새 러너를 만들지 않는다. `walk_forward.main`에서 `WF_ONESHOT=1`이면 창 분할을 건너뛰고, 같은 `dgt_btc.json` 중 `reset_enabled=true` 12칸만 `executor.run_window`로 한 번씩 돌린다. `WF_DATA_START` / `WF_DATA_END`가 논문 구간이다.

저자 등차 그리드 민감도는 이 스펙의 필수 런이 아니다.

### 6.4 Monte Carlo

기존 거래 iid 부트스트랩은 그리드에 맞지 않는다 (상한 전까지 포지션이 안 닫힘). DGT 리포트는 `mc_oos` / `mc_holdout`을 null로 두고 사유를 적는다. 일별 equity 부트스트랩은 후속 작업이다.

## 7. 파일

```
src/sngw_trader/strategies/dynamic_grid.py          # 신규: Config + Strategy
research/grids/dgt_btc.json                         # 신규: 24칸 + select
src/sngw_trader/research/config.py                  # 수정: GridSpec.select 선택적 필드
src/sngw_trader/research/walk_forward.py            # 수정: equity Sharpe 선택 분기, DGT 펀딩 병기
src/sngw_trader/research/executor.py                # 수정: 펀딩용 fills 추출 (필요 시)
tests/test_strategies/test_dynamic_grid.py          # 신규
tests/test_research/test_walk_forward.py            # 수정: select=equity_sharpe
```

`strategy_factory.py`에는 넣지 않는다. 본평가는 `ImportableStrategyConfig` + `WF_GRID_PATH`다. 라이브 `TradingNode` 연결은 이 스펙 밖이다.

## 8. 테스트

단위 테스트는 합성 1분봉, 네트워크 없음.

- geometric 레벨: 중심·상한·하한, `h=3`이면 레벨 7개
- 래더 증분 합 = 시작 `grid_qty` / 시작 워킹 현금 (오차 없이 소진)
- 상향 체결은 `bag_qty`를 줄이지 않음
- 하한 리셋: `grid_qty` → `bag`, 현금만으로 다음 `M`
- 상한 리셋: `grid_qty` 0, `bag` 유지
- `reset_enabled=false`: 첫 경계 이후 신규 주문 없음
- 숏으로 가는 매도 수량이 나오지 않음
- 최소수량 미만이면 그 레벨 주문이 없음
- `select=equity_sharpe` 분기가 거래 Sharpe를 쓰지 않음

러너 smoke는 기존처럼 노드를 띄우지 않는 조립 테스트만.

## 9. 가정 vs 사실

사실:

- 핸드오프 1순위가 DGT이고, 평가는 0015 기준(WF, baseline, 비용, 0/무보정)이다
- catalog는 OKX BTC perp 1분봉이다
- 다른 전략의 엔진 비용 모델과 `funding.py`가 이미 있다

가정 (결과 파일에 그대로 적는다):

- 스팟 그리드를 롱온리 퍼프 + 백/워킹 분할로 옮긴 것은 재현이지 동일 전략이 아니다
- 1분봉 L1 + 확률적 LIMIT 체결은 틱 북이 아니다. 한 봉 다중 체결을 허용한다
- 중간 입금을 막아 저자 IRR과 분모가 다르다
- 논문 8bps / Binance spot / 등차 구현을 쓰지 않는다
- 이 결과만으로 lifecycle을 `backtested` 이상으로 올리지 않는다

## 10. 비범위

- ETH, 멀티 자산
- 숏 그리드 / 헤지 모드
- 등차 그리드 본선
- 라이브/데모 주문
- Wiki 수정, vault handoff 원문 수정
- 나머지 4편 논문
- 새 `BacktestEngine` 경로, ccxt, 자체 루프

## 11. 수락 조건

- [ ] 전략 모듈은 `Strategy` + `Config`만 있고 노드/어댑터를 조립하지 않는다
- [ ] InstrumentId는 `*.OKX`, BarType은 `1-MINUTE-LAST-EXTERNAL`
- [ ] 24칸 JSON으로 WF가 돌아가고, IS 선택은 equity Sharpe다
- [ ] `reset_enabled=false`가 같은 클래스에서 전통 종료로 동작한다
- [ ] 리포트에 B&H, 펀딩 제외/포함, stitched OOS, holdout이 있다
- [ ] MC 거래 부트스트랩을 DGT에 적용하지 않는다
- [ ] 단위 테스트가 섹션 8을 커버한다
- [ ] vault 결과는 result/receipt로만 남기고 Wiki/lifecycle을 건드리지 않는다
