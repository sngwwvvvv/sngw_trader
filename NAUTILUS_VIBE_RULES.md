# NautilusTrader + OKX Vibe Coding Rules

이 파일은 바이브 코딩 시 AI가 **반드시** 따르는 고정 규칙이다.  
전략 러너를 직접 만들지 않는다. **NautilusTrader 노드가 러너다.**

적용 대상: Cursor / Claude Code / Codex / Copilot / 기타 에이전트.  
이 파일과 충돌하는 제안은 거절하고 이 규칙을 따른다.

---

## 0. 한 줄 결정

```
Strategy (순수 로직)
    └── BacktestNode | TradingNode   ← 유일한 러너
            └── OKX adapter        ← 유일한 거래소 I/O
```

- 백테스트 러너 = `BacktestNode` + `ParquetDataCatalog`
- 라이브/데모/샌드박스 러너 = `TradingNode`
- 거래소 = OKX only
- 전략 코드는 백테스트와 라이브에서 **동일 클래스**를 쓴다

라이브 러너는 `TradingNode` + `TradingNodeConfig`로 조립한다.  
**커스텀 이벤트 루프/주문 REST 래퍼를 새로 만들지 않는다.**

---

## 1. 절대 금지

AI는 아래를 생성하거나 제안하지 않는다.

1. `while True` / `asyncio` 루프로 시세를 받아 주문을 내는 자체 러너
2. `okx`, `python-okx`, `ccxt`로 주문·잔고·포지션을 직접 호출
3. 전략 안에서 `requests` / `httpx` / `websocket-client`로 거래소 I/O
4. 백테스트용 전략과 라이브용 전략을 따로 작성
5. 한 프로세스에 `BacktestNode`와 `TradingNode`를 동시에 실행
6. Jupyter에서 `TradingNode.run()`
7. API 키를 소스코드·테스트 픽스처·커밋에 하드코딩
8. `on_bar` / `on_quote`에서 sleep, 네트워크, 모델 학습, 파일 대량 I/O
9. RiskEngine을 우회하는 직접 체결
10. 같은 OKX 계좌를 두 노드가 동시에 거래
11. 새 프레임워크(backtrader, freqtrade, vectorbt live 등)로 러너를 대체
12. “일단 간단한 봇으로 돌리고 나중에 Nautilus에 붙이자”는 임시 구현

막히면 **공식 어댑터/노드 API를 찾고**, 없으면 사용자에게 질문한다.  
추측으로 거래소 클라이언트를 만들지 않는다.

---

## 2. 고정 스택

| 항목 | 값 |
|---|---|
| Engine | NautilusTrader (Python 3.12–3.14) |
| Venue | `OKX` |
| InstrumentId | `{OKX_SYMBOL}.OKX` 예: `BTC-USDT-SWAP.OKX`, `BTC-USDT.OKX` |
| Backtest | `BacktestNode` + `ParquetDataCatalog` |
| Live | `TradingNode` + `OKXLiveDataClientFactory` + `OKXLiveExecClientFactory` |
| Paper 경로 | 1) `OKXEnvironment.DEMO` 2) 필요 시 sandbox exec |
| Secrets | env only: `OKX_API_KEY`, `OKX_API_SECRET`, `OKX_API_PASSPHRASE` |
| State | 라이브는 Redis cache 권장 (`load_state` / `save_state`) |
| Process | 프로세스당 노드 1개. 노드 안에 전략 N개 |

데모 키와 라이브 키는 다르다. 데모는 `OKXEnvironment.DEMO` + 데모 API 키.

---

## 3. 저장소 레이아웃

새 파일은 이 구조를 벗어나지 않는다.

```
sngw_trader/
  AGENTS.md                      # 이 규칙 복사본 (또는 심볼릭)
  pyproject.toml
  .env.example                   # 키 이름만, 값 없음
  src/sngw_trader/
    __init__.py
    strategies/
      __init__.py
      ema_cross.py               # Strategy + StrategyConfig 만
    actors/                      # 선택: 알림, 피처, 브리지
    indicators/                  # 선택: 순수 계산
    runners/
      backtest_okx.py            # BacktestNode 조립만
      live_okx.py                # TradingNode 조립만
      sandbox_okx.py             # 선택
    research/                    # 백테스트 오케스트레이션 (WF/MC). 창 분할·반복 실행·집계만
    config/
      types.py                   # 공용 typed config
  catalog/                       # parquet catalog (git 제외)
  logs/                          # git 제외
  tests/
    test_strategies/
    test_config/
  deploy/
    docker-compose.yml
    Dockerfile
    trader.service
```

역할 분리:

- `strategies/` : 시그널과 `submit_order`만. 노드/어댑터 import 금지에 가깝게.
- `runners/` : 노드 조립, 클라이언트 등록, 전략 부착, `run()`/`dispose()`.
- `actors/` : 전략이 아닌 보조 컴포넌트.
- `deploy/` : systemd / compose. 전략 로직 없음.
- `research/` : 창 분할, BacktestNode 반복 실행, 집계. 매매 조건 없음.

전략 파일이 `TradingNode`나 OKX factory를 import하면 잘못된 설계다.  
러너 파일이 매매 조건을 가지면 잘못된 설계다.

---

## 4. 전략 작성 규칙

### 4.1 필수 형태

```python
from nautilus_trader.config import StrategyConfig
from nautilus_trader.trading import Strategy

class MyStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    # 필요한 파라미터만. 시크릿 넣지 말 것.

class MyStrategy(Strategy):
    def __init__(self, config: MyStrategyConfig) -> None:
        super().__init__(config)

    def on_start(self) -> None: ...
    def on_stop(self) -> None: ...
    def on_bar(self, bar: Bar) -> None: ...
```

- Config와 Strategy를 같은 모듈에 둔다.
- `frozen=True` 가능한 버전에서는 frozen config를 쓴다.
- 파라미터는 생성자가 아니라 config 필드다.
- `strategy_id`, `order_id_tag`가 필요하면 config에서 명시한다.
- OKX는 client order id에 하이픈을 거부한다.  
  전략/노드 설정에서 `use_hyphens_in_client_order_ids=False`를 기본으로 한다.  
  UUID를 쓰면 `use_uuid_client_order_ids=True`와 함께 하이픈 제거를 확인한다.

### 4.2 전략이 해도 되는 일

- `subscribe_bars` / `subscribe_quote_ticks` / `subscribe_trade_ticks` / `subscribe_order_book_deltas`
- indicator 등록
- `self.cache`, `self.portfolio`, `self.clock` 조회
- `self.order_factory`로 주문 생성 후 `self.submit_order`
- `cancel_order`, `modify_order`, `close_all_positions`
- 순수 계산 (numpy 등). 수 ms 이내.

### 4.3 전략이 하면 안 되는 일

- OKX REST/WS 직접 호출
- 글로벌 싱글톤 이벤트 루프
- 장시간 lock
- 별도의 position tracker를 dict로 유지 (Cache/Portfolio를 무시)
- 파일에서 시크릿 읽기
- “다음 틱까지 대기” 같은 blocking wait

### 4.4 주문

- 수량/가격은 `instrument.make_qty`, `instrument.make_price`로 양자화한다.
- 시장가/지정가는 Nautilus 주문 타입을 쓴다. OKX raw 파라미터를 전략에 넣지 않는다.
- reduce-only, post-only가 필요하면 Nautilus 주문 필드로 표현한다.
- GTD는 OKX에서 네이티브 지원이 약하다. 만료는 전략/ExecAlgorithm이 관리한다.

---

## 5. 러너 작성 규칙

러너는 **조립 스크립트**다. 알파가 여기 있으면 실패다.

### 5.1 백테스트 (`runners/backtest_okx.py`)

순서 고정:

1. catalog 경로 확인
2. `BacktestVenueConfig` (시뮬 거래소 이름과 OMS/계좌 타입)
3. `BacktestDataConfig` (data_type, instrument_id, start/end)
4. `BacktestRunConfig`
5. `BacktestNode(configs=[...]).build()`
6. 전략 부착
7. `node.run()`
8. 리포트 출력 후 dispose

전략 부착 우선순위:

1. `node.add_strategy(instance)` (가능하면)
2. `node.add_strategy_from_config(ImportableStrategyConfig(...))`
3. 내장 예제는 `add_builtin_strategy` (프로덕션 전략에 쓰지 말 것)

데이터는 먼저 catalog에 적재하는 별도 스크립트/`actors`로 둔다.  
러너가 CSV를 직접 파싱해 엔진에 넣는 low-level 경로는 기본값이 아니다.  
실험이 필요하면 `runners/backtest_engine_dev.py`처럼 이름에 `dev`를 붙이고,  
라이브 경로와 섞지 않는다.

### 5.2 라이브 (`runners/live_okx.py`)

순서 고정:

1. env에서 모드 읽기 (`demo` | `live`). 기본값은 `demo`
2. `TradingNodeConfig` 생성
   - `trader_id=TraderId(...)`
   - `data_clients={"OKX": OKXDataClientConfig(...)}`
   - `exec_clients={"OKX": OKXExecClientConfig(...)}`
   - Nautilus environment와 OKX environment를 혼동하지 말 것
   - 거래소 데모는 `OKXEnvironment.DEMO`
3. `TradingNode(config=config)` 생성
4. `node.add_data_client_factory("OKX", OKXLiveDataClientFactory)`
5. `node.add_exec_client_factory("OKX", OKXLiveExecClientFactory)`
6. `node.build()`
7. 전략 부착 (`node.trader.add_strategy` / `add_strategy_from_config`)
8. `try: node.run()` / `finally: node.dispose()`

`trader_id` 예: `TRADER-001`. 태그(`001`)는 노드마다 고유.  
`account_id` 예: `OKX-001`.

자격증명은 config에 넣지 않는다. `None`으로 두고 env fallback을 쓴다.

```python
OKXDataClientConfig(
    environment=OKXEnvironment.DEMO,  # or LIVE
    instrument_types=[OKXInstrumentType.SWAP],  # 실제 상품에 맞게
    # api_key/secret/passphrase 생략
)
```

라이브 기본값:

- `with_reconciliation(True)`
- 가능하면 Redis cache + `load_state=True` + `save_state=True`
- `delay_post_stop`를 줘서 종료 시 저장 시간을 확보
- Jupyter 금지, `__main__` 스크립트만

### 5.3 Import 경로

설치된 패키지에서 실제 심볼을 확인한 뒤 쓴다.  
이름이 버전마다 다를 수 있다.

우선 탐색 순서:

- `nautilus_trader.live.node.TradingNode`
- `nautilus_trader.live.config.TradingNodeConfig`
- `nautilus_trader.adapters.okx` 의 `OKXDataClientConfig`, `OKXExecClientConfig`
- Factory: `OKXLiveDataClientFactory` / `OKXLiveExecClientFactory`
- Enum: `OKXEnvironment`, `OKXInstrumentType`, `OKXMarginMode`, `OKXRegion`

import가 실패하면 임의 클래스를 만들지 말고  
`python -c "import nautilus_trader.adapters.okx as m; print(dir(m))"` 로 확인한다.

---

## 6. OKX 고정 규칙

### 6.1 InstrumentId

| 상품 | 심볼 예 | InstrumentId |
|---|---|---|
| Spot | `BTC-USDT` | `BTC-USDT.OKX` |
| Perp SWAP | `BTC-USDT-SWAP` | `BTC-USDT-SWAP.OKX` |
| Futures | `BTC-USD-261225` | `BTC-USD-261225.OKX` |

문자열로 쓸 때 반드시 `.OKX` venue를 붙인다.  
`BTCUSDT`, `BTC/USDT` 같은 임의 표기는 금지.

### 6.2 상품 타입

프로젝트 기본 상품을 코드에 상수로 박지 말고 config로 둔다.  
다만 **한 러너가 다루는 instrument_types는 명시적으로 제한**한다.

예: 퍼프만 거래하면

```python
instrument_types=[OKXInstrumentType.SWAP]
```

스팟+스왑을 한 전략에 섞지 않는 것이 기본이다. 필요하면 전략을 분리한다.

### 6.3 계좌 / 마진 / 포지션 모드

- `margin_mode`: `CROSS` 또는 `ISOLATED`. 러너 config에 명시.
- 포지션 모드(net vs hedge)는 OKX 웹/앱에서 맞추고, 코드와 모순되지 않게 한다.
- OMS 타입(`NETTING` vs `HEDGING`)을 계좌 포지션 모드와 맞춘다.
- 레버리지는 어댑터가 노출하지 않을 수 있다. 전략에서 임의 REST로 레버리지를 바꾸지 않는다.

### 6.4 Region

기본 `OKXRegion.GLOBAL`.  
EEA/US 계정이면 config로 명시한다. 키와 리전이 다르면 `API key doesn't exist`가 난다.

### 6.5 주문 ID

OKX client order id:

- 하이픈 금지
- 영숫자
- 최대 32자

Nautilus 기본 ID 체계가 하이픈을 넣으면 반드시 끈다.

---

## 7. 데이터 / 카탈로그

- 라이브 시세는 OKX data client만 사용한다.
- 백테스트 시세는 catalog의 `QuoteTick` / `TradeTick` / `Bar`를 사용한다.
- catalog writer는 `runners/`가 아니라 `scripts/` 또는 `src/sngw_trader/data/`에 둔다.
- 다운로드 소스(OKX history, Tardis 등)는 writer에만 존재한다.
- 전략은 데이터 출처를 모른다. `BarType`만 안다.

BarType 문자열 예:

```
BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL
```

임의로 `1m`, `BTCUSDT` 같은 약칭을 BarType에 넣지 않는다.

---

## 8. 설정과 시크릿

`.env.example`

```
OKX_API_KEY=
OKX_API_SECRET=
OKX_API_PASSPHRASE=
OKX_ENV=demo
TRADER_ID=TRADER-001
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
```

- `.env`는 gitignore
- 코드는 `os.environ` / `pydantic-settings`만 사용
- 로그에 키·패스프레이즈를 찍지 않는다
- 테스트는 자격증명 없이 전략 순수 로직만 검증

모드 전환은 코드 복제가 아니라 env/config 한 줄이다.

```
OKX_ENV=demo  → OKXEnvironment.DEMO
OKX_ENV=live  → OKXEnvironment.LIVE   # 명시적 확인 없이 켜지 말 것
```

라이브 키로 주문을 내는 경로에는 `CONFIRM_LIVE=YES` 같은 가드를 둔다.

---

## 9. 테스트

필수:

- 전략 config 생성 테스트
- `on_bar` 의사결정 단위 테스트 (가능하면 합성 Bar)
- InstrumentId / BarType 파싱 테스트
- 러너는 smoke 수준 (노드를 실제로 띄우지 않는 조립 테스트)

금지:

- 실계좌를 CI에서 때리기
- 네트워크 필수 테스트를 기본 스위트로 넣기

---

## 10. 배포

- 프로세스 1개 = `TradingNode` 1개
- `restart=on-failure` + SIGINT + stop grace ≥ 30s
- Redis를 쓰면 AOF
- 미니PC/VPS 모두 동일 compose/systemd
- Jupyter 이미지로 라이브 금지
- 출금 권한 없는 API 키, IP 화이트리스트

배포 파일이 전략 파라미터를 하드코딩하지 않는다. env와 config 파일을 읽는다.

---

## 11. 바이브 코딩 작업 순서

AI는 한 턴에 시스템을 전부 만들지 않는다. 아래 순서를 지킨다.

1. 패키지 뼈대 + 이 규칙 파일
2. `Strategy` + `StrategyConfig` + 단위 테스트
3. catalog writer + `BacktestNode` 러너
4. 백테스트가 주문/포지션 리포트를 내는지 확인
5. `live_okx.py`를 **DEMO**로 연결
6. 구독과 reconciliation만 확인 (주문 없는 DataTester/드라이런)
7. 소량 지정가 1개로 exec 확인
8. Redis 상태 저장 + 강제 재시작 복구
9. compose/systemd
10. `CONFIRM_LIVE` 가드 뒤에만 실키

사용자가 “봇 만들어줘”라고 해도 1→2부터 한다.  
5번 전에 라이브 주문 코드를 기본 on으로 두지 않는다.

---

## 12. AI 응답 형식

코드를 바꿀 때:

- 새로 만든/수정한 파일 경로를 먼저 적는다
- 전략 파일과 러너 파일을 한 파일에 합치지 않는다
- 추측한 Nautilus 심볼에는 `UNVERIFIED IMPORT`라고 표시하고, 확인 명령을 함께 제시한다
- 규칙과 다른 설계를 제안하려면 **먼저 이유를 묻고 승인을 받는다**

커밋 메시지 접두:

- `feat(strategy):`
- `feat(runner):`
- `feat(okx):`
- `chore(deploy):`

---

## 13. 복붙용 시스템 프롬프트

에이전트 시작 시 아래를 함께 넣는다.

```
이 저장소는 NautilusTrader가 전략 러너다.
자체 트레이딩 루프, ccxt, python-okx 직접 주문은 금지.
거래소는 OKX만. InstrumentId는 *.OKX.
전략은 nautilus_trader.trading.Strategy 한 클래스.
백테스트는 BacktestNode + ParquetDataCatalog.
라이브는 TradingNode + OKX data/exec factory.
시크릿은 OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE.
기본 실행 모드는 OKX DEMO.
전략 파일은 노드/어댑터를 조립하지 않고,
러너 파일은 매매 조건을 갖지 않는다.
자세한 규칙은 NAUTILUS_VIBE_RULES.md 를 따른다.
```

---

## 14. 구현 전 체크리스트

새 코드를 쓰기 전에 AI가 스스로 확인한다.

- [ ] 이번 변경이 전략인가, 러너인가, 배포인가? 한 가지인가?
- [ ] 거래소 HTTP/WS를 직접 여는 코드가 없는가?
- [ ] InstrumentId에 `.OKX`가 있는가?
- [ ] 라이브 기본값이 demo인가?
- [ ] 키가 파일에 없는가?
- [ ] 콜백이 블로킹되지 않는가?
- [ ] 백테스트 전략 클래스와 라이브 전략 클래스가 같은가?

하나라도 NO면 코드를 제출하지 않고 수정한다.
