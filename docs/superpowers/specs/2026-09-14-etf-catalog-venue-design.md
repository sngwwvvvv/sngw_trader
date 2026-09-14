# US ETF 일봉 카탈로그 + 백테스트 venue 추론

- 날짜: 2026-09-14
- 상태: 승인됨
- 적용 규칙: `NAUTILUS_VIBE_RULES.md` (다운로드는 writer에만, 전략은 BarType만 인지)

## 1. 목적

OKX-only 계약을 **데이터 경로만** 넓힌다. Yahoo에서 US ETF 일봉을 `ParquetDataCatalog`에 적재하고, `BacktestNode`의 venue 이름을 `InstrumentId`에서 읽게 한다.

라이브 체결은 계속 OKX만. 토스증권·IB·CME 주문/어댑터는 넣지 않는다.

## 2. 확정된 요구사항

| 항목 | 결정 |
|---|---|
| 범위 | 데이터 + 백테스트 venue 추론. 라이브 팩토리 변경 없음 |
| 신규 자산 | US ETF only. CME 선물·FX 현물 없음 |
| 데이터 소스 | Yahoo Finance 일봉 (`yfinance`, `auto_adjust=True`) |
| 히스토리 | 상장일~오늘 (max). Yahoo 1시간봉 rolling 불가이므로 일봉만 |
| 기본 심볼 | `SPY`, `QQQ`, `IWM` |
| InstrumentId | `SPY.ARCA`, `QQQ.NASDAQ`, `IWM.ARCA`. 그 외 기본 `ARCA` |
| BarType | `{instrument_id}-1-DAY-LAST-EXTERNAL` |
| OKX 경로 | 기존 1분봉 writer·`CATALOG_START`/`END` 필수 규칙 유지 |
| 라이브 | `live_okx.py` 그대로. ETF 라이브 없음 |

시간봉(Alpaca SIP 등)은 이번 범위 밖이다. 일봉 catalog와 일봉 백테스트가 돈 뒤에 별도 스펙으로 넣는다.

## 3. 규칙 문서 변경

`NAUTILUS_VIBE_RULES.md`와 `AGENTS.md`에서 “거래소는 OKX만”을 아래로 교체한다. Nautilus 노드가 러너인 원칙은 유지한다.

한 줄 결정:

```
Strategy
    └── BacktestNode | TradingNode
            ├── OKX adapter          ← 유일한 라이브 I/O (당분간)
            └── ParquetDataCatalog   ← OKX 1분 + US ETF 일봉
```

고정 스택 표:

| 항목 | 값 |
|---|---|
| Engine | NautilusTrader (Python 3.12–3.14) |
| Live venue | `OKX` only |
| Catalog venues | `OKX` (crypto 1분), `ARCA` / `NASDAQ` (US ETF 일봉) |
| InstrumentId | `{SYMBOL}.{VENUE}` 예: `BTC-USDT-SWAP.OKX`, `SPY.ARCA` |
| Backtest | `BacktestNode` + `ParquetDataCatalog`. venue 이름은 InstrumentId에서 읽음 |
| Live | `TradingNode` + OKX data/exec factory. 기본 DEMO |
| Secrets | env. OKX 키 + 선택 `yfinance` extra. 토스 키 없음 |

금지 목록에 추가:

- 토스증권·키움·IB·CME로 주문하는 코드
- Yahoo/yfinance를 전략·라이브 러너에서 import
- ETF 1분/1시간 수집을 Yahoo rolling으로 우회하는 코드

데이터 절:

- 라이브 시세는 OKX data client만.
- 백테스트 시세는 catalog의 `Bar` (OKX 1분 또는 ETF 일봉).
- writer만 Yahoo/OKX HTTP를 연다.
- 전략은 출처를 모른다. `BarType`만 안다.

복붙용 시스템 프롬프트도 같은 문장으로 맞춘다.

`README.md` 한 줄: OKX가 유일한 **라이브** 거래소이고, catalog는 US ETF 일봉을 받을 수 있다.

## 4. 아키텍처

```
data/catalog_writer.py
  main()
    CATALOG_SOURCE=okx        → 기존 OKX 1분 경로
    CATALOG_SOURCE=yahoo-etf  → yahoo_etf.download_and_write(...)

data/yahoo_etf.py
  venue_for(symbol)           : SPY/IWM→ARCA, QQQ→NASDAQ, else ARCA
  instrument_id_for(symbol)   : "{SYM}.{VENUE}"
  etf_bar_type(instrument_id) : "{id}-1-DAY-LAST-EXTERNAL"
  build_equity(...)           : Nautilus Equity (구현 시 생성자 확인)
  row_to_bar(...)             : Yahoo 일봉 행 → Bar (순수)
  fetch_daily_bars(symbol, start, end)  : yfinance, 네트워크
  download_and_write(settings, catalog)

runners/backtest_okx.py
  venue_name(instrument_id)   : 마지막 '.' 뒤
  default_bar_type(id)        : OKX → 1-MINUTE, 그 외 → 1-DAY
  build_run_config            : venue name / 잔고 통화 / 수수료 기본값을 venue에 맞춤

runners/strategy_factory.py
  source_bar_type             : default_bar_type과 동일 규칙 (중복 구현 금지, 한쪽을 import)
```

역할 분리:

- `yahoo_etf.py`는 주문·노드·전략을 import하지 않는다.
- 전략은 venue 문자열을 하드코딩하지 않는다. config의 `instrument_id` / `bar_type`만 쓴다.
- OKX `raw_candle_to_bar`와 테스트는 유지한다.

## 5. 다운로드 흐름 (`yahoo-etf`)

1. `yfinance` import. 없으면 `SystemExit`: `pip install -e ".[etf]"` 안내.
2. 심볼 목록: `ETF_SYMBOLS` (콤마 구분, 기본 `SPY,QQQ,IWM`). 공백 제거, 대문자.
3. 심볼마다:
   - `Equity` 인스트루먼트 생성 → `catalog.write_data([inst], data_cls=Instrument)` (또는 catalog가 받는 실제 API).
   - `Ticker(symbol).history(interval="1d", auto_adjust=True, actions=False)`
     - `CATALOG_START`/`END`가 있으면 `start`/`end`로 전달.
     - 둘 다 없으면 `period="max"`.
     - 하나만 있으면 있는 쪽만 전달, 나머지는 Yahoo 기본(max/오늘).
   - 빈 DataFrame이면 그 심볼은 `SystemExit` (부분 성공으로 catalog를 어중간하게 남기지 않음). 이미 쓴 심볼이 있으면 에러 메시지에 적는다.
   - 각 행 → `row_to_bar` → `catalog.write_data(bars, data_cls=Bar)`.
   - 다음 심볼 전 `time.sleep(0.2)`.
4. 심볼별 개수와 기간을 stdout에 출력.

### 5.1 Bar 변환

- 인덱스는 timezone-aware면 UTC ns로, naive면 해당 날짜 00:00 UTC로 본다. 세션 종료 시각을 추정하지 않는다.
- OHLC: adj 값 (`auto_adjust=True`). `Decimal`로 변환.
- volume: Yahoo volume. 없으면 0.
- `BarType = {instrument_id}-1-DAY-LAST-EXTERNAL`.
- `price_precision=2`, `size_precision=0` (ETF 주식 수).
- OKX와 같이 `Bar.from_raw` 또는 설치된 Nautilus의 동등 API. 구현 시 확인.

### 5.2 Equity 인스트루먼트

구현 시 `nautilus_trader.model.instruments.Equity` 시그니처를 확인한다. 필요한 값:

- `instrument_id`: `SPY.ARCA` 등
- `raw_symbol`: `SPY`
- `currency`: USD
- `price_precision`: 2
- `price_increment`: 0.01
- `lot_size`: 1

임의 Equity 클래스를 만들지 않는다. import가 실패하면 설치된 패키지에서 확인한다 (규칙 §5.3).

## 6. 설정

`Settings`에 기본값 있는 필드를 추가한다. 기존 테스트의 `Settings(...)` 키워드 인자는 그대로 컴파일되어야 한다.

| env | 기본 | 설명 |
|---|---|---|
| `CATALOG_SOURCE` | `okx` | `okx` \| `yahoo-etf`. 그 외 `SystemExit` |
| `ETF_SYMBOLS` | `SPY,QQQ,IWM` | yahoo-etf 다운로드 목록 |
| `INSTRUMENT_ID` | 빈 값 | 있으면 `instrument_id_str`로 그대로 사용. venue 포함 필수 |

`instrument_id_str` 우선순위:

1. `INSTRUMENT_ID`가 비어 있지 않으면 그 문자열. `.`가 없으면 `SystemExit`.
2. 아니면 기존 로직: `OKX_SYMBOL`이 이미 `.OKX`로 끝나면 그대로, 아니면 `{symbol}.OKX`.

yahoo-etf 다운로드는 `ETF_SYMBOLS`를 쓴다. 백테스트 한 종목은 `INSTRUMENT_ID=SPY.ARCA` (또는 이후 심볼).

`CATALOG_START`/`END`:

- `okx`: 둘 다 필수 (현행).
- `yahoo-etf`: 둘 다 선택. 있으면 그 구간, 없으면 max.

`.env.example`에 `CATALOG_SOURCE`, `ETF_SYMBOLS`, `INSTRUMENT_ID`를 추가한다. 값 예시는 넣되 시크릿은 없다.

`pyproject.toml`:

```
[project.optional-dependencies]
etf = ["yfinance"]
```

console script는 기존 `catalog-download` 유지.

## 7. 백테스트 러너

`venue_name(instrument_id: str) -> str`: 마지막 `.` 뒤. 예: `SPY.ARCA` → `ARCA`, `BTC-USDT-SWAP.OKX` → `OKX`. `.` 없으면 `SystemExit`.

`default_bar_type(instrument_id: str) -> str`:

- venue `OKX` → `{id}-1-MINUTE-LAST-EXTERNAL`
- 그 외 → `{id}-1-DAY-LAST-EXTERNAL`

`strategy_factory.source_bar_type`은 이 함수를 import해서 쓴다. 1-MINUTE 하드코딩을 제거한다.

`build_run_config`:

| | OKX | 그 외 (ETF) |
|---|---|---|
| `BacktestVenueConfig.name` | `OKX` | `venue_name(...)` (`ARCA` 등) |
| `starting_balances` | `10_000 USDT` | `10_000 USD` |
| `account_type` | `MARGIN` | `MARGIN` (숏 가능, 토스 제약은 나중에) |
| `oms_type` | `NETTING` | `NETTING` |
| 수수료 | `settings.bt_maker_fee` / `bt_taker_fee` (기본 0.0002 / 0.0005) | env에 `BT_MAKER_FEE`/`BT_TAKER_FEE`가 **있으면** 그 값, **없으면** 0.0001 / 0.0001 |

수수료 분기: `os.environ`에 키가 있는지로 본다. settings 숫자만 보면 OKX 기본값과 구분할 수 없다.

`live_okx.py`는 `instrument_id_str`가 `.OKX`가 아니면 시작 시 `SystemExit` (실수로 ETF id를 라이브 OKX에 넣지 못하게).

기존 crypto 워크포워드 그리드는 `INSTRUMENT_ID`를 안 쓰면 지금과 같다.

## 8. 테스트

네트워크 필수 테스트는 기본 스위트에 넣지 않는다.

| 대상 | 검증 |
|---|---|
| `venue_for` / `instrument_id_for` | SPY→ARCA, QQQ→NASDAQ, IWM→ARCA, XLK→ARCA |
| `row_to_bar` | 고정 OHLC/volume/날짜 → BarType `SPY.ARCA-1-DAY-LAST-EXTERNAL`, OHLC 일치 |
| `default_bar_type` | OKX는 1-MINUTE, `SPY.ARCA`는 1-DAY. catalog BarType과 일치 |
| `venue_name` | `SPY.ARCA`→`ARCA` |
| `build_run_config(..., "SPY.ARCA")` | venue name `ARCA`, 잔고에 `USD` |
| `build_run_config(..., "BTC-USDT-SWAP.OKX")` | 기존과 동일: venue `OKX`, `USDT` |
| `instrument_id_str` | 기본은 `.OKX` append. `INSTRUMENT_ID=SPY.ARCA`면 `.OKX`를 붙이지 않음 |
| `CATALOG_SOURCE` | `yahoo-etf` 허용, 오타 `SystemExit` |
| `source_bar_type` | `default_bar_type`과 동일 문자열 |
| 기존 OKX `raw_candle_to_bar` | 그대로 통과 |

`Settings`에 필드가 늘면 기존 테스트 픽스처는 **기본값 있는 새 필드** 덕분에 수정 없이 돌아가야 한다. 안 되면 픽스처에 기본만 보탠다.

## 9. UNVERIFIED IMPORT (구현 시 확인)

- `nautilus_trader.model.instruments.Equity` 생성자 인자
- `ParquetDataCatalog.write_data` 의 instrument dtype (`Instrument` vs `Equity`)
- `Bar.from_raw` 일봉 `ts_event` 단위 (ns)
- `yfinance.Ticker.history` 컬럼명 (`Open`/`High`/`Low`/`Close`/`Volume`)과 adj 동작
- `BacktestVenueConfig.starting_balances` 가 `USD` 문자열을 받는지 (OKX는 `USDT`)

확인 명령 예:

```
python -c "from nautilus_trader.model.instruments import Equity; import inspect; print(inspect.signature(Equity.__init__))"
```

## 10. 범위 밖 (YAGNI)

- 토스증권/IB 라이브 어댑터
- CME 선물, FX 현물, Yahoo 1분/1시간
- Alpaca 시간봉/분봉 catalog (일봉 백테스트 이후 별도 스펙)
- Databento
- ETF 섹터 11종 기본 리스트 확장 (env로 가능, 기본은 3종)
- 점진적 catalog 이어받기 (재실행 시 해당 심볼 다시 씀)
- 백테스트 수수료를 토스 실제 테이블로 맞추기
- 새 평균회귀 전략 클래스 (이번엔 데이터/러너만)

## 11. 수동 검증 (구현 후)

```
pip install -e ".[etf]"
# .env: CATALOG_SOURCE=yahoo-etf
catalog-download
```

기대: `catalog/data/` 아래 `SPY.ARCA`, `QQQ.NASDAQ`, `IWM.ARCA` 인스트루먼트와 `*-1-DAY-LAST-EXTERNAL` 바가 생기고, SPY 첫 날짜가 1990년대다.

```
INSTRUMENT_ID=SPY.ARCA STRATEGY=...  # 기존 전략으로 smoke 백테스트
```

venue 불일치로 빈 데이터가 나오면 실패로 본다.
