# US ETF 일봉 카탈로그 + 백테스트 venue 추론 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Yahoo US ETF 일봉을 `ParquetDataCatalog`에 적재하고, `BacktestNode`가 `InstrumentId`에서 venue(ARCA/NASDAQ/OKX)를 읽게 한다.

**Architecture:** `yahoo_etf.py`는 venue 매핑·Equity·일봉→`Bar` 변환과 Yahoo 다운로드만 담당한다. `catalog_writer.main`이 `CATALOG_SOURCE`로 OKX 1분 / Yahoo 일봉을 분기한다. `backtest_okx.venue_name` / `default_bar_type`이 venue와 BarType을 정하고, `strategy_factory.source_bar_type`은 그걸 import한다. 라이브는 `*.OKX`가 아니면 거절한다.

**Tech Stack:** Python 3.12–3.14, NautilusTrader (`Equity`, `Bar.from_raw`, `ParquetDataCatalog`, `BacktestVenueConfig`), optional `yfinance`. 네트워크 테스트는 기본 스위트에 넣지 않는다.

**Spec:** `docs/superpowers/specs/2026-09-14-etf-catalog-venue-design.md`

## Global Constraints

- 라이브 체결은 OKX only. ETF 라이브 어댑터 없음.
- writer만 Yahoo/OKX HTTP를 연다. 전략·라이브 러너는 `yfinance`를 import하지 않는다.
- 전략은 출처를 모른다. `BarType`만 안다.
- ETF 1분/1시간 Yahoo rolling 우회 금지. Alpaca 시간봉은 이번 범위 밖.
- 시크릿 하드코딩 금지. 네트워크 필수 테스트는 기본 스위트에 넣지 않는다.
- Nautilus 심볼은 설치된 패키지에서 확인된 것만. import 실패 시 임의 클래스 금지(규칙 §5.3).
- 실행 명령은 `uv run pytest ...` (Windows PowerShell). `&&` 금지.
- `source_bar_type`이 `backtest_okx.default_bar_type`을 **함수 안에서** import한다. 모듈 탑레벨 import는 `backtest_okx` → `strategy_factory` 순환을 만든다.

## File structure

| 경로 | 역할 |
|---|---|
| `src/sngw_trader/data/yahoo_etf.py` | venue/id/BarType, Equity, 일봉→Bar, Yahoo 다운로드 (신규) |
| `tests/test_data/test_yahoo_etf.py` | 위 순수 함수 단위 테스트 (신규) |
| `src/sngw_trader/config/settings.py` | `catalog_source`, `etf_symbols`, `instrument_id` |
| `src/sngw_trader/data/catalog_writer.py` | `CATALOG_SOURCE` 분기. OKX 경로 유지 |
| `src/sngw_trader/runners/backtest_okx.py` | `venue_name`, `default_bar_type` 분기, venue/잔고/수수료 |
| `src/sngw_trader/runners/strategy_factory.py` | `source_bar_type` → `default_bar_type` |
| `src/sngw_trader/runners/live_okx.py` | `*.OKX` 아니면 `SystemExit` |
| `pyproject.toml` | `[project.optional-dependencies] etf = ["yfinance"]` |
| `NAUTILUS_VIBE_RULES.md`, `AGENTS.md`, `README.md`, `.env.example` | 규칙/문서 |

확인된 Nautilus 시그니처 (재확인 명령은 Task 3):

```
Equity(instrument_id, raw_symbol, currency, price_precision, price_increment, lot_size, ts_event, ts_init, ...)
Bar.from_raw(bar_type, open, high, low, close, price_prec, volume, size_prec, ts_event, ts_init)
BacktestVenueConfig.starting_balances: list[str]  # "10_000 USD" 가능
```

---

### Task 1: Settings — `CATALOG_SOURCE` / `ETF_SYMBOLS` / `INSTRUMENT_ID`

**Files:**
- Modify: `src/sngw_trader/config/settings.py`
- Modify: `tests/test_config/test_settings.py`
- Modify: `.env.example`

**Interfaces:**
- Produces: `Settings.catalog_source: str = "okx"`, `Settings.etf_symbols: str = "SPY,QQQ,IWM"`, `Settings.instrument_id: str = ""` (모두 기본값 있음 — 기존 `Settings(...)` 키워드 인자는 수정하지 않는다).
- Produces: `instrument_id_str` — `instrument_id`가 비어 있지 않으면 그 문자열(`.` 필수), 아니면 기존 `{symbol}.OKX` 로직.
- Produces: `load_settings()`가 `CATALOG_SOURCE` 오타면 `SystemExit`.

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_config/test_settings.py` 하단에 추가. 기존 `Settings(...)` 픽스처는 건드리지 않는다.

```python
def test_instrument_id_env_overrides_okx_append(monkeypatch) -> None:
    for k, v in _base_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("INSTRUMENT_ID", "SPY.ARCA")
    s = load_settings()
    assert s.instrument_id_str == "SPY.ARCA"


def test_instrument_id_env_without_venue_exits(monkeypatch) -> None:
    import pytest

    for k, v in _base_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("INSTRUMENT_ID", "SPY")
    with pytest.raises(SystemExit):
        load_settings()


def test_catalog_source_default_okx(monkeypatch) -> None:
    for k, v in _base_env().items():
        monkeypatch.setenv(k, v)
    s = load_settings()
    assert s.catalog_source == "okx"
    assert s.etf_symbols == "SPY,QQQ,IWM"


def test_catalog_source_yahoo_etf_allowed(monkeypatch) -> None:
    for k, v in _base_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("CATALOG_SOURCE", "yahoo-etf")
    monkeypatch.setenv("ETF_SYMBOLS", " spy, qqq ")
    s = load_settings()
    assert s.catalog_source == "yahoo-etf"
    assert s.etf_symbols == "spy, qqq"


def test_catalog_source_typo_exits(monkeypatch) -> None:
    import pytest

    for k, v in _base_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("CATALOG_SOURCE", "yahoo")
    with pytest.raises(SystemExit):
        load_settings()
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `uv run pytest tests/test_config/test_settings.py -v`

Expected: FAIL — `Settings`에 `catalog_source` / `instrument_id` 없음, 또는 `instrument_id_str`가 `SPY.ARCA.OKX`.

- [ ] **Step 3: Settings 필드 + 파싱 구현**

`src/sngw_trader/config/settings.py`:

dataclass **맨 끝**에 기본값 있는 필드만 추가 (기본값 없는 필드 뒤에 와야 한다):

```python
    catalog_start: datetime | None
    catalog_end: datetime | None
    catalog_source: str = "okx"
    etf_symbols: str = "SPY,QQQ,IWM"
    instrument_id: str = ""
```

`instrument_id_str` 교체:

```python
    @property
    def instrument_id_str(self) -> str:
        override = self.instrument_id.strip()
        if override:
            if "." not in override:
                raise SystemExit(
                    "INSTRUMENT_ID must include venue after '.', e.g. SPY.ARCA"
                )
            return override
        symbol = self.symbol.upper()
        if symbol.endswith(".OKX"):
            return symbol
        return f"{symbol}.OKX"
```

모듈 레벨:

```python
_ALLOWED_CATALOG_SOURCES = frozenset({"okx", "yahoo-etf"})


def _catalog_source() -> str:
    raw = _env("CATALOG_SOURCE", "okx")
    if raw not in _ALLOWED_CATALOG_SOURCES:
        raise SystemExit(
            f"CATALOG_SOURCE must be 'okx' or 'yahoo-etf', got {raw!r}"
        )
    return raw
```

`load_settings()` 반환에 추가하고, `INSTRUMENT_ID`에 값이 있으면 `.` 검증을 여기서도 한다:

```python
        catalog_start=_optional_ts("CATALOG_START"),
        catalog_end=_optional_ts("CATALOG_END"),
        catalog_source=_catalog_source(),
        etf_symbols=_env("ETF_SYMBOLS", "SPY,QQQ,IWM"),
        instrument_id=_env("INSTRUMENT_ID"),
```

`instrument_id`가 비어 있지 않고 `.`가 없으면 `Settings(...)` 생성 전에 `SystemExit`. `_env` 직후:

```python
    instrument_id = _env("INSTRUMENT_ID")
    if instrument_id and "." not in instrument_id:
        raise SystemExit(
            "INSTRUMENT_ID must include venue after '.', e.g. SPY.ARCA"
        )
    return Settings(
        ...
        instrument_id=instrument_id,
    )
```

`.env.example` 카탈로그 근처에 추가 (값은 예시, 시크릿 없음):

```
# okx | yahoo-etf
CATALOG_SOURCE=okx
ETF_SYMBOLS=SPY,QQQ,IWM
# Backtest one name with venue, e.g. SPY.ARCA. Empty → {OKX_SYMBOL}.OKX
INSTRUMENT_ID=
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/test_config/test_settings.py tests/test_config/test_live_guard.py tests/test_runners/test_backtest_okx.py -v`

Expected: PASS. 기존 `Settings(...)` 호출은 새 필드 기본값 덕분에 수정 없이 통과해야 한다.

- [ ] **Step 5: Commit**

```
git add src/sngw_trader/config/settings.py tests/test_config/test_settings.py .env.example
git commit -m "feat(config): add ETF catalog source and instrument id override"
```

---

### Task 2: ETF venue / InstrumentId / BarType / 심볼 파싱

**Files:**
- Create: `src/sngw_trader/data/yahoo_etf.py`
- Create: `tests/test_data/test_yahoo_etf.py`

**Interfaces:**
- Consumes: 없음 (Nautilus Bar/Equity는 Task 3).
- Produces:
  - `venue_for(symbol: str) -> str`
  - `instrument_id_for(symbol: str) -> str`
  - `etf_bar_type(instrument_id: str) -> str`
  - `parse_etf_symbols(raw: str) -> list[str]`

이 파일은 `runners/` / `strategies/` / `yfinance`를 import하지 않는다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_data/test_yahoo_etf.py`:

```python
import pytest

from sngw_trader.data.yahoo_etf import (
    etf_bar_type,
    instrument_id_for,
    parse_etf_symbols,
    venue_for,
)


def test_venue_for_known_and_default() -> None:
    assert venue_for("SPY") == "ARCA"
    assert venue_for("spy") == "ARCA"
    assert venue_for("IWM") == "ARCA"
    assert venue_for("QQQ") == "NASDAQ"
    assert venue_for("XLK") == "ARCA"


def test_instrument_id_for() -> None:
    assert instrument_id_for("SPY") == "SPY.ARCA"
    assert instrument_id_for("QQQ") == "QQQ.NASDAQ"
    assert instrument_id_for("IWM") == "IWM.ARCA"
    assert instrument_id_for("XLK") == "XLK.ARCA"


def test_etf_bar_type() -> None:
    assert etf_bar_type("SPY.ARCA") == "SPY.ARCA-1-DAY-LAST-EXTERNAL"


def test_parse_etf_symbols() -> None:
    assert parse_etf_symbols("SPY,QQQ,IWM") == ["SPY", "QQQ", "IWM"]
    assert parse_etf_symbols(" spy, qqq ") == ["SPY", "QQQ"]
    assert parse_etf_symbols("") == []
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `uv run pytest tests/test_data/test_yahoo_etf.py -v`

Expected: FAIL — `ModuleNotFoundError: yahoo_etf`.

- [ ] **Step 3: 최소 구현**

`src/sngw_trader/data/yahoo_etf.py`:

```python
"""US ETF daily bars from Yahoo into ParquetDataCatalog.

Do not place orders here. Do not import runners or strategies.
"""

from __future__ import annotations

_VENUE = {
    "SPY": "ARCA",
    "IWM": "ARCA",
    "QQQ": "NASDAQ",
}


def venue_for(symbol: str) -> str:
    return _VENUE.get(symbol.strip().upper(), "ARCA")


def instrument_id_for(symbol: str) -> str:
    sym = symbol.strip().upper()
    return f"{sym}.{venue_for(sym)}"


def etf_bar_type(instrument_id: str) -> str:
    return f"{instrument_id}-1-DAY-LAST-EXTERNAL"


def parse_etf_symbols(raw: str) -> list[str]:
    return [part.strip().upper() for part in raw.split(",") if part.strip()]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/test_data/test_yahoo_etf.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```
git add src/sngw_trader/data/yahoo_etf.py tests/test_data/test_yahoo_etf.py
git commit -m "feat(data): map US ETF symbols to ARCA/NASDAQ instrument ids"
```

---

### Task 3: Yahoo 일봉 행 → Bar, Equity 인스트루먼트

**Files:**
- Modify: `src/sngw_trader/data/yahoo_etf.py`
- Modify: `tests/test_data/test_yahoo_etf.py`

**Interfaces:**
- Consumes: `etf_bar_type(instrument_id: str) -> str`
- Produces:
  - `row_to_bar(instrument_id: str, when: datetime, open_: object, high: object, low: object, close: object, volume: object | None = None) -> Bar`
  - `build_equity(symbol: str) -> Equity`

`Bar.from_raw` 스케일은 기존 OKX `raw_candle_to_bar`와 같다: `Decimal(value) * Decimal(10) ** 9`. `price_precision=2`, `size_precision=0`.

naive `when` → 그 시각을 UTC로 간주 (날짜만이면 00:00 UTC). aware면 UTC로 변환.

- [ ] **Step 1: Equity 생성자 재확인**

Run:

```
uv run python -c "from nautilus_trader.model.instruments import Equity; print(Equity.__doc__.split('Raises')[0])"
```

Expected: 파라미터에 `instrument_id`, `raw_symbol`, `currency`, `price_precision`, `price_increment`, `lot_size`, `ts_event`, `ts_init`. 다르면 이 태스크를 멈추고 사용자에게 실제 시그니처를 보고한다. 임의 Equity 클래스를 만들지 않는다.

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/test_data/test_yahoo_etf.py`에 추가:

```python
from datetime import datetime, timezone
from decimal import Decimal

from nautilus_trader.model import Bar, BarType
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import Equity

from sngw_trader.data.yahoo_etf import build_equity, row_to_bar


def test_row_to_bar_naive_utc_midnight() -> None:
    bar = row_to_bar(
        "SPY.ARCA",
        datetime(2020, 1, 2),
        "100.00",
        "101.50",
        "99.25",
        "100.75",
        "123456",
    )
    assert isinstance(bar, Bar)
    assert bar.bar_type == BarType.from_str("SPY.ARCA-1-DAY-LAST-EXTERNAL")
    assert str(bar.open) == "100.00"
    assert str(bar.high) == "101.50"
    assert str(bar.low) == "99.25"
    assert str(bar.close) == "100.75"
    assert str(bar.volume) == "123456"
    assert bar.ts_event == int(
        datetime(2020, 1, 2, tzinfo=timezone.utc).timestamp() * 1_000_000_000
    )
    assert bar.ts_init == bar.ts_event


def test_row_to_bar_aware_converts_to_utc() -> None:
    when = datetime(2020, 1, 2, 0, 0, tzinfo=timezone.utc)
    bar = row_to_bar("QQQ.NASDAQ", when, 1, 1, 1, 1, None)
    assert bar.bar_type == BarType.from_str("QQQ.NASDAQ-1-DAY-LAST-EXTERNAL")
    assert str(bar.volume) == "0"


def test_build_equity_spy() -> None:
    inst = build_equity("SPY")
    assert isinstance(inst, Equity)
    assert inst.id == InstrumentId.from_str("SPY.ARCA")
    assert inst.raw_symbol == Symbol("SPY")
    assert inst.quote_currency.code == "USD"
    assert inst.price_precision == 2
    assert inst.size_precision == 0
```

`str(bar.open)` 포맷이 설치된 Nautilus에서 `"100.00"`이 아니면 (예: `"100.0"`) 실제 `str(bar.open)`에 assertion을 맞춘다. OKX 테스트는 `str(bar.open) == "43000.0"`이다. ETF `price_precision=2`면 `"100.00"`일 가능성이 높다. 첫 실행이 값만 다른 FAIL이면 기대 문자열을 실제 `str`에 맞추되 OHLC 숫자는 `Decimal(str(bar.open)) == Decimal("100.00")`으로 고정한다.

- [ ] **Step 3: 테스트 실행해 실패 확인**

Run: `uv run pytest tests/test_data/test_yahoo_etf.py::test_row_to_bar_naive_utc_midnight tests/test_data/test_yahoo_etf.py::test_build_equity_spy -v`

Expected: FAIL — `row_to_bar` / `build_equity` import 실패.

- [ ] **Step 4: 구현**

`yahoo_etf.py`에 import와 함수 추가:

```python
from datetime import datetime, timezone
from decimal import Decimal

from nautilus_trader.model import Bar, BarType
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.identifiers import InstrumentId, Symbol
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Price, Quantity

_PRICE_PREC = 2
_SIZE_PREC = 0
_RAW_SCALE = Decimal(10) ** 9


def _ts_ns(when: datetime) -> int:
    if hasattr(when, "to_pydatetime"):
        when = when.to_pydatetime()
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    else:
        when = when.astimezone(timezone.utc)
    return int(when.timestamp() * 1_000_000_000)


def _raw_px(value: object) -> Decimal:
    return Decimal(str(value)) * _RAW_SCALE


def _raw_qty(value: object | None) -> Decimal:
    if value is None or value == "":
        return Decimal(0)
    text = str(value)
    if text.lower() == "nan":
        return Decimal(0)
    return Decimal(text) * _RAW_SCALE


def row_to_bar(
    instrument_id: str,
    when: datetime,
    open_: object,
    high: object,
    low: object,
    close: object,
    volume: object | None = None,
) -> Bar:
    ts_ns = _ts_ns(when)
    return Bar.from_raw(
        bar_type=BarType.from_str(etf_bar_type(instrument_id)),
        open=_raw_px(open_),
        high=_raw_px(high),
        low=_raw_px(low),
        close=_raw_px(close),
        price_prec=_PRICE_PREC,
        volume=_raw_qty(volume),
        size_prec=_SIZE_PREC,
        ts_event=ts_ns,
        ts_init=ts_ns,
    )


def build_equity(symbol: str) -> Equity:
    instrument_id = instrument_id_for(symbol)
    raw = symbol.strip().upper()
    return Equity(
        instrument_id=InstrumentId.from_str(instrument_id),
        raw_symbol=Symbol(raw),
        currency=USD,
        price_precision=_PRICE_PREC,
        price_increment=Price.from_str("0.01"),
        lot_size=Quantity.from_int(1),
        ts_event=0,
        ts_init=0,
    )
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `uv run pytest tests/test_data/test_yahoo_etf.py tests/test_data/test_catalog_writer.py -v`

Expected: PASS. OKX `raw_candle_to_bar`도 그대로 통과.

- [ ] **Step 6: Commit**

```
git add src/sngw_trader/data/yahoo_etf.py tests/test_data/test_yahoo_etf.py
git commit -m "feat(data): convert Yahoo daily rows to Nautilus ETF bars"
```

---

### Task 4: `download_and_write` + `catalog_writer` 분기 + `yfinance` extra

**Files:**
- Modify: `src/sngw_trader/data/yahoo_etf.py`
- Modify: `src/sngw_trader/data/catalog_writer.py`
- Modify: `tests/test_data/test_yahoo_etf.py`
- Modify: `tests/test_data/test_catalog_writer.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: `Settings.etf_symbols`, `Settings.catalog_start`, `Settings.catalog_end`, `parse_etf_symbols`, `build_equity`, `row_to_bar`, `instrument_id_for`
- Produces:
  - `fetch_daily_bars(symbol: str, start: datetime | None, end: datetime | None)` — yfinance, 단위 테스트에서 mock
  - `download_and_write(settings: Settings, catalog) -> None`
  - `catalog_writer.main` : `catalog_source == "yahoo-etf"`이면 `download_and_write`, 아니면 기존 OKX 경로 (`_validate_range`는 OKX만)

빈 DataFrame이면 `SystemExit`. 이미 쓴 심볼이 있으면 메시지에 적는다. 심볼 사이 `time.sleep(0.2)`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_data/test_yahoo_etf.py`에 추가. pandas는 nautilus 의존으로 이미 있다. yfinance는 mock한다.

```python
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from nautilus_trader.model import Bar
from nautilus_trader.model.instruments import Instrument

from sngw_trader.config.settings import Settings
from sngw_trader.data.yahoo_etf import download_and_write


def _settings(**kwargs) -> Settings:
    base = dict(
        okx_env="demo", confirm_live="NO", trader_id="T", account_id="A",
        node_name="N", instrument_type="SWAP", symbol="BTC-USDT-SWAP",
        margin_mode="CROSS", region="GLOBAL",
        catalog_path=Path("catalog"), log_dir=Path("logs"),
        redis_enabled=False, redis_host="", redis_port=6379,
        bt_maker_fee=0.0002, bt_taker_fee=0.0005, bt_latency_ms=200,
        bt_prob_fill_on_limit=0.7, bt_prob_slippage=0.1,
        strategy="err_mom_a", w_f=10, w_e=10, momentum_window=200, theta=0.0,
        ema_fast=20, ema_slow=50, n_pull=24, trade_size="0.01",
        risk_stop_enabled=True, atr_period=14, atr_mult=3.0,
        sizing_mode="vol_target", size_target_vol=0.20, size_half_life=20,
        size_min_scale=0.0, size_max_scale=3.0, size_rebalance_band=0.10,
        catalog_start=None, catalog_end=None,
        catalog_source="yahoo-etf", etf_symbols="SPY,QQQ", instrument_id="",
    )
    base.update(kwargs)
    return Settings(**base)


class _FakeCatalog:
    def __init__(self) -> None:
        self.writes: list[tuple[list, object]] = []

    def write_data(self, data, data_cls=None):
        self.writes.append((list(data), data_cls))


def test_download_and_write_empty_symbols_exits() -> None:
    with pytest.raises(SystemExit):
        download_and_write(_settings(etf_symbols=" , "), _FakeCatalog())


def test_download_and_write_writes_equity_and_bars(monkeypatch) -> None:
    df = pd.DataFrame(
        {
            "Open": [100.0],
            "High": [101.0],
            "Low": [99.0],
            "Close": [100.5],
            "Volume": [1000],
        },
        index=pd.DatetimeIndex(["2020-01-02"], tz="UTC"),
    )
    monkeypatch.setattr(
        "sngw_trader.data.yahoo_etf.fetch_daily_bars",
        lambda symbol, start, end: df,
    )
    monkeypatch.setattr("sngw_trader.data.yahoo_etf.time.sleep", lambda _s: None)
    catalog = _FakeCatalog()
    download_and_write(_settings(), catalog)
    classes = [cls for _, cls in catalog.writes]
    assert Instrument in classes or any(
        isinstance(item[0], type(catalog.writes[0][0][0]))
        for item in catalog.writes
        if item[0]
    )
    bar_batches = [rows for rows, cls in catalog.writes if cls is Bar]
    assert len(bar_batches) == 2
    assert all(row.bar_type.value.endswith("-1-DAY-LAST-EXTERNAL") for rows in bar_batches for row in rows)


def test_download_and_write_empty_frame_mentions_written(monkeypatch) -> None:
    spy = pd.DataFrame(
        {"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1]},
        index=pd.DatetimeIndex(["2020-01-02"], tz="UTC"),
    )
    empty = pd.DataFrame()

    def fake_fetch(symbol, start, end):
        return spy if symbol == "SPY" else empty

    monkeypatch.setattr("sngw_trader.data.yahoo_etf.fetch_daily_bars", fake_fetch)
    monkeypatch.setattr("sngw_trader.data.yahoo_etf.time.sleep", lambda _s: None)
    with pytest.raises(SystemExit, match="QQQ") as exc:
        download_and_write(_settings(etf_symbols="SPY,QQQ"), _FakeCatalog())
    assert "SPY" in str(exc.value)
```

`test_download_and_write_writes_equity_and_bars`의 Instrument assertion이 장황하면 이렇게 단순화한다: 첫 write는 Equity 1개, 그 다음 write는 Bar 리스트.

`tests/test_data/test_catalog_writer.py`에 분기 테스트 추가:

```python
from sngw_trader.data.catalog_writer import run_download


def test_run_download_dispatches_yahoo_etf(monkeypatch) -> None:
    called = {}

    def fake_download(settings, catalog):
        called["yes"] = True

    monkeypatch.setattr(
        "sngw_trader.data.yahoo_etf.download_and_write", fake_download
    )
    settings = type("S", (), {"catalog_source": "yahoo-etf"})()
    run_download(settings, catalog=object())
    assert called["yes"] is True
```

`run_download`가 아직 없으면 이 테스트가 FAIL.

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `uv run pytest tests/test_data/test_yahoo_etf.py tests/test_data/test_catalog_writer.py::test_run_download_dispatches_yahoo_etf -v`

Expected: FAIL — `download_and_write` / `run_download` 없음.

- [ ] **Step 3: 구현**

`pyproject.toml`:

```toml
[project.optional-dependencies]
dev = [
  "pytest>=8",
]
etf = ["yfinance"]
```

`yahoo_etf.py` — `import time` 추가 후:

```python
def fetch_daily_bars(symbol: str, start: datetime | None, end: datetime | None):
    try:
        import yfinance as yf
    except ImportError as exc:
        raise SystemExit('yfinance is required. Install with: pip install -e ".[etf]"') from exc
    kwargs: dict = {"interval": "1d", "auto_adjust": True, "actions": False}
    if start is None and end is None:
        kwargs["period"] = "max"
    else:
        if start is not None:
            kwargs["start"] = start
        if end is not None:
            kwargs["end"] = end
    return yf.Ticker(symbol).history(**kwargs)


def download_and_write(settings, catalog) -> None:
    try:
        import yfinance  # noqa: F401
    except ImportError as exc:
        raise SystemExit('yfinance is required. Install with: pip install -e ".[etf]"') from exc

    symbols = parse_etf_symbols(settings.etf_symbols)
    if not symbols:
        raise SystemExit("ETF_SYMBOLS is empty")

    written: list[str] = []
    for i, symbol in enumerate(symbols):
        instrument_id = instrument_id_for(symbol)
        inst = build_equity(symbol)
        catalog.write_data([inst], data_cls=Instrument)
        df = fetch_daily_bars(symbol, settings.catalog_start, settings.catalog_end)
        if df is None or getattr(df, "empty", True):
            extra = f" already wrote {','.join(written)}" if written else ""
            raise SystemExit(f"No Yahoo daily bars for {symbol}.{extra}")
        bars = []
        for idx, row in df.iterrows():
            vol = row["Volume"] if "Volume" in row.index else 0
            bars.append(
                row_to_bar(
                    instrument_id,
                    idx,
                    row["Open"],
                    row["High"],
                    row["Low"],
                    row["Close"],
                    vol,
                )
            )
        catalog.write_data(bars, data_cls=Bar)
        first = min(b.ts_event for b in bars)
        last = max(b.ts_event for b in bars)
        print(
            f"Wrote {len(bars)} daily bars for {instrument_id} "
            f"({datetime.fromtimestamp(first / 1e9, tz=timezone.utc).date()} -> "
            f"{datetime.fromtimestamp(last / 1e9, tz=timezone.utc).date()})"
        )
        written.append(symbol)
        if i + 1 < len(symbols):
            time.sleep(0.2)
```

`yahoo_etf.py` 상단에 `from nautilus_trader.model.instruments import Instrument` 추가 (`write_data(..., data_cls=Instrument)`). `Equity`는 이미 Task 3에서 import.

`catalog_writer.py` — 기존 `main` 몸체를 `run_download`로 옮긴다:

```python
def run_download(settings: Settings, catalog: ParquetDataCatalog) -> None:
    if settings.catalog_source == "yahoo-etf":
        from sngw_trader.data.yahoo_etf import download_and_write

        download_and_write(settings, catalog)
        return
    instrument = load_instrument(settings)
    catalog.write_data([instrument], data_cls=Instrument)
    bars = download_bars(settings, instrument)
    if not bars:
        raise SystemExit("No candles downloaded for the requested range")
    catalog.write_data(bars, data_cls=Bar)
    first = min(b.ts_event for b in bars)
    last = max(b.ts_event for b in bars)
    print(
        f"Wrote {len(bars)} bars for {settings.instrument_id_str} "
        f"({_fmt(first)} -> {_fmt(last)}) to {settings.catalog_path}"
    )


def main() -> None:
    settings = load_settings()
    settings.catalog_path.mkdir(parents=True, exist_ok=True)
    catalog = ParquetDataCatalog(settings.catalog_path)
    run_download(settings, catalog)
```

OKX 경로는 기존처럼 `download_bars` → `_validate_range`가 start/end를 강제한다. yahoo-etf는 `_validate_range`를 호출하지 않는다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/test_data/test_yahoo_etf.py tests/test_data/test_catalog_writer.py tests/test_config/test_settings.py -v`

Expected: PASS.

`test_download_and_write_writes_equity_and_bars`가 `bar_type.value` 때문에 FAIL이면 `str(row.bar_type)`가 `SPY.ARCA-1-DAY-LAST-EXTERNAL`을 포함하는지로 바꾼다.

- [ ] **Step 5: Commit**

```
git add src/sngw_trader/data/yahoo_etf.py src/sngw_trader/data/catalog_writer.py tests/test_data/test_yahoo_etf.py tests/test_data/test_catalog_writer.py pyproject.toml
git commit -m "feat(data): download Yahoo ETF daily bars into the catalog"
```

---

### Task 5: 백테스트 venue 추론, BarType, 잔고, 수수료

**Files:**
- Modify: `src/sngw_trader/runners/backtest_okx.py`
- Modify: `src/sngw_trader/runners/strategy_factory.py`
- Modify: `tests/test_runners/test_backtest_okx.py`
- Modify: `tests/test_runners/test_strategy_factory.py`
- Modify: `tests/test_data/test_catalog_writer.py` (OKX BarType 일치 테스트는 그대로 통과해야 함)

**Interfaces:**
- Consumes: `Settings.bt_maker_fee` / `bt_taker_fee` (OKX), `os.environ` 키 존재 여부 (ETF)
- Produces:
  - `venue_name(instrument_id: str) -> str` — 마지막 `.` 뒤. `.` 없으면 `SystemExit`
  - `default_bar_type(instrument_id: str) -> str` — OKX → `1-MINUTE`, 그 외 → `1-DAY`
  - `source_bar_type(instrument_id: str) -> str` — `default_bar_type`과 동일 문자열
  - `build_run_config` venue `name` / `starting_balances` / 수수료

ETF 수수료: `BT_MAKER_FEE` / `BT_TAKER_FEE`가 **os.environ에 있으면** 그 값, **없으면** `0.0001` / `0.0001`. settings 숫자만 보지 않는다.

순환 import: `source_bar_type` 함수 **내부**에서 `from sngw_trader.runners.backtest_okx import default_bar_type`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_runners/test_backtest_okx.py`에 추가:

```python
import pytest

from sngw_trader.runners.backtest_okx import default_bar_type, venue_name


def test_venue_name() -> None:
    assert venue_name("SPY.ARCA") == "ARCA"
    assert venue_name("BTC-USDT-SWAP.OKX") == "OKX"


def test_venue_name_without_dot_exits() -> None:
    with pytest.raises(SystemExit):
        venue_name("SPY")


def test_default_bar_type_okx_minute_etf_day() -> None:
    assert default_bar_type("BTC-USDT-SWAP.OKX") == (
        "BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL"
    )
    assert default_bar_type("SPY.ARCA") == "SPY.ARCA-1-DAY-LAST-EXTERNAL"


def test_build_run_config_etf_venue_usd(monkeypatch) -> None:
    monkeypatch.delenv("BT_MAKER_FEE", raising=False)
    monkeypatch.delenv("BT_TAKER_FEE", raising=False)
    cfg = build_run_config("catalog", "SPY.ARCA", _settings())
    assert cfg.venues[0].name == "ARCA"
    assert cfg.venues[0].starting_balances == ["10_000 USD"]
    assert cfg.venues[0].fee_model.config["maker_fee_rate"] == 0.0001
    assert cfg.venues[0].fee_model.config["taker_fee_rate"] == 0.0001


def test_build_run_config_etf_uses_env_fees_when_present(monkeypatch) -> None:
    monkeypatch.setenv("BT_MAKER_FEE", "0.0003")
    monkeypatch.setenv("BT_TAKER_FEE", "0.0004")
    cfg = build_run_config("catalog", "SPY.ARCA", _settings())
    assert cfg.venues[0].fee_model.config["maker_fee_rate"] == 0.0003
    assert cfg.venues[0].fee_model.config["taker_fee_rate"] == 0.0004


def test_build_run_config_okx_unchanged() -> None:
    cfg = build_run_config("catalog", "BTC-USDT-SWAP.OKX", _settings())
    assert cfg.venues[0].name == "OKX"
    assert cfg.venues[0].starting_balances == ["10_000 USDT"]
    assert cfg.venues[0].fee_model.config["maker_fee_rate"] == 0.0002
    assert cfg.venues[0].fee_model.config["taker_fee_rate"] == 0.0005
```

`tests/test_runners/test_strategy_factory.py`의 기존 OKX 테스트는 유지하고 추가:

```python
def test_source_bar_type_matches_default_bar_type() -> None:
    from sngw_trader.runners.backtest_okx import default_bar_type

    assert source_bar_type("BTC-USDT-SWAP.OKX") == default_bar_type(
        "BTC-USDT-SWAP.OKX"
    )
    assert source_bar_type("SPY.ARCA") == default_bar_type("SPY.ARCA")
    assert source_bar_type("SPY.ARCA") == "SPY.ARCA-1-DAY-LAST-EXTERNAL"
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `uv run pytest tests/test_runners/test_backtest_okx.py tests/test_runners/test_strategy_factory.py -v`

Expected: FAIL — `venue_name` 없음, `default_bar_type("SPY.ARCA")`가 1-MINUTE, venue name이 항상 `OKX`.

- [ ] **Step 3: 구현**

`backtest_okx.py`:

```python
import os


def venue_name(instrument_id: str) -> str:
    if "." not in instrument_id:
        raise SystemExit(
            f"instrument id must include venue after '.', got {instrument_id!r}"
        )
    return instrument_id.rsplit(".", 1)[-1]


def default_bar_type(instrument_id: str) -> str:
    if venue_name(instrument_id) == "OKX":
        return f"{instrument_id}-1-MINUTE-LAST-EXTERNAL"
    return f"{instrument_id}-1-DAY-LAST-EXTERNAL"


def _fee_rates(instrument_id: str, settings: Settings) -> tuple[float, float]:
    if venue_name(instrument_id) == "OKX":
        return settings.bt_maker_fee, settings.bt_taker_fee
    maker = (
        float(os.environ["BT_MAKER_FEE"])
        if "BT_MAKER_FEE" in os.environ
        else 0.0001
    )
    taker = (
        float(os.environ["BT_TAKER_FEE"])
        if "BT_TAKER_FEE" in os.environ
        else 0.0001
    )
    return maker, taker
```

`build_fee_model_config(settings)`를 `build_fee_model_config(settings, instrument_id: str)`로 바꾸고 내부에서 `_fee_rates`를 쓴다. `build_run_config` 안의 호출을 맞춘다.

`build_run_config` venue 블록:

```python
    venue_id = venue_name(instrument_id)
    quote = "USDT" if venue_id == "OKX" else "USD"
    maker, taker = _fee_rates(instrument_id, settings)
    venue = BacktestVenueConfig(
        name=venue_id,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        book_type="L1_MBP",
        starting_balances=[f"10_000 {quote}"],
        fill_model=build_fill_model_config(settings),
        fee_model=build_fee_model_config(settings, instrument_id),
        latency_model=build_latency_model_config(settings),
    )
```

`strategy_factory.py`:

```python
def source_bar_type(instrument_id: str) -> str:
    from sngw_trader.runners.backtest_okx import default_bar_type

    return default_bar_type(instrument_id)
```

`build_fee_model_config`를 다른 곳에서 직접 호출하는지 검색한다. 있으면 `instrument_id`를 넘긴다. 현재는 `build_run_config`만 호출한다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/test_runners/test_backtest_okx.py tests/test_runners/test_strategy_factory.py tests/test_runners/test_backtest_models.py tests/test_data/test_catalog_writer.py -v`

Expected: PASS. `test_bar_type_matches_backtest_runner`는 OKX라 1-MINUTE 그대로. `test_run_config_has_fill_fee_latency`는 `load_settings()`의 OKX id라 settings 수수료 그대로.

- [ ] **Step 5: Commit**

```
git add src/sngw_trader/runners/backtest_okx.py src/sngw_trader/runners/strategy_factory.py tests/test_runners/test_backtest_okx.py tests/test_runners/test_strategy_factory.py
git commit -m "feat(runner): infer backtest venue and ETF daily bar type from instrument id"
```

---

### Task 6: 라이브 러너 ETF id 거절

**Files:**
- Modify: `src/sngw_trader/runners/live_okx.py`
- Modify: `tests/test_config/test_live_guard.py`
- Modify: `tests/test_strategies/test_no_exchange_io.py`

**Interfaces:**
- Consumes: `Settings.instrument_id_str`
- Produces: `assert_okx_instrument(settings: Settings) -> None` — `instrument_id_str`가 `.OKX`로 끝나지 않으면 `SystemExit`. `main()`이 `assert_safe_mode` 다음에 호출.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_config/test_live_guard.py`에 추가:

```python
from sngw_trader.runners.live_okx import assert_okx_instrument


def test_live_rejects_etf_instrument_id() -> None:
    with pytest.raises(SystemExit):
        assert_okx_instrument(_settings(instrument_id="SPY.ARCA"))


def test_live_accepts_okx_instrument_id() -> None:
    assert_okx_instrument(_settings())
```

`_settings`는 이미 `kwargs`로 `Settings`를 덮어쓴다. Task 1 기본값 덕분에 `instrument_id="SPY.ARCA"`만 넘기면 된다.

`tests/test_strategies/test_no_exchange_io.py`의 `FORBIDDEN`에 `"yfinance"`를 추가한다.

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `uv run pytest tests/test_config/test_live_guard.py tests/test_strategies/test_no_exchange_io.py -v`

Expected: FAIL — `assert_okx_instrument` 없음. `yfinance`를 FORBIDDEN에 넣어도 전략 파일에 없으면 그 테스트는 PASS할 수 있다. live 거절 테스트가 FAIL이면 충분하다.

- [ ] **Step 3: 구현**

`live_okx.py`:

```python
def assert_okx_instrument(settings: Settings) -> None:
    if not settings.instrument_id_str.endswith(".OKX"):
        raise SystemExit(
            "live_okx only accepts *.OKX instrument ids, "
            f"got {settings.instrument_id_str}"
        )
```

`main()`:

```python
    settings = load_settings()
    assert_safe_mode(settings)
    assert_okx_instrument(settings)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/test_config/test_live_guard.py tests/test_strategies/test_no_exchange_io.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```
git add src/sngw_trader/runners/live_okx.py tests/test_config/test_live_guard.py tests/test_strategies/test_no_exchange_io.py
git commit -m "feat(runner): refuse non-OKX instruments on the live node"
```

---

### Task 7: 규칙 문서 / README / spec 상태

**Files:**
- Modify: `NAUTILUS_VIBE_RULES.md`
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-09-14-etf-catalog-venue-design.md` (상태 줄만)

코드 동작은 이미 Task 1–6. 이 태스크는 스펙 §3 문서 계약.

- [ ] **Step 1: `NAUTILUS_VIBE_RULES.md` §0 한 줄 결정 교체**

```
Strategy
    └── BacktestNode | TradingNode
            ├── OKX adapter          ← 유일한 라이브 I/O (당분간)
            └── ParquetDataCatalog   ← OKX 1분 + US ETF 일봉
```

불릿에서 “거래소 = OKX only”를 “라이브 거래소 = OKX only. catalog venue는 OKX / ARCA / NASDAQ”으로 바꾼다.

- [ ] **Step 2: §2 고정 스택 표 교체**

스펙 표를 그대로 넣는다:

| 항목 | 값 |
|---|---|
| Engine | NautilusTrader (Python 3.12–3.14) |
| Live venue | `OKX` only |
| Catalog venues | `OKX` (crypto 1분), `ARCA` / `NASDAQ` (US ETF 일봉) |
| InstrumentId | `{SYMBOL}.{VENUE}` 예: `BTC-USDT-SWAP.OKX`, `SPY.ARCA` |
| Backtest | `BacktestNode` + `ParquetDataCatalog`. venue 이름은 InstrumentId에서 읽음 |
| Live | `TradingNode` + OKX data/exec factory. 기본 DEMO |
| Secrets | env. OKX 키 + 선택 `yfinance` extra. 토스 키 없음 |

`Paper 경로` / `State` / `Process` 줄은 유지한다.

- [ ] **Step 3: §1 금지 목록에 추가**

기존 12번 뒤에:

```
13. 토스증권·키움·IB·CME로 주문하는 코드
14. Yahoo/yfinance를 전략·라이브 러너에서 import
15. ETF 1분/1시간 수집을 Yahoo rolling으로 우회하는 코드
```

- [ ] **Step 4: §7 데이터 절 교체**

스펙 문장 그대로:

- 라이브 시세는 OKX data client만.
- 백테스트 시세는 catalog의 `Bar` (OKX 1분 또는 ETF 일봉).
- writer만 Yahoo/OKX HTTP를 연다.
- 전략은 출처를 모른다. `BarType`만 안다.

BarType 예:

```
BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL
SPY.ARCA-1-DAY-LAST-EXTERNAL
```

- [ ] **Step 5: §13 복붙용 시스템 프롬프트 교체**

```
이 저장소는 NautilusTrader가 전략 러너다.
자체 트레이딩 루프, ccxt, python-okx 직접 주문은 금지.
라이브 거래소는 OKX만. catalog는 OKX 1분과 US ETF 일봉(ARCA/NASDAQ)을 받는다.
InstrumentId는 {SYMBOL}.{VENUE} (예: BTC-USDT-SWAP.OKX, SPY.ARCA).
전략은 nautilus_trader.trading.Strategy 한 클래스.
백테스트는 BacktestNode + ParquetDataCatalog. venue 이름은 InstrumentId에서 읽는다.
라이브는 TradingNode + OKX data/exec factory.
시크릿은 env. 기본 실행 모드는 OKX DEMO.
전략 파일은 노드/어댑터를 조립하지 않고,
러너 파일은 매매 조건을 갖지 않는다.
Yahoo/yfinance는 catalog writer에만 둔다.
자세한 규칙은 NAUTILUS_VIBE_RULES.md 를 따른다.
```

- [ ] **Step 6: `AGENTS.md` 한 줄 요약 교체**

“거래소는 OKX만. `InstrumentId`는 `*.OKX`.” →

```
- 라이브 거래소는 OKX만. catalog venue는 OKX / ARCA / NASDAQ. InstrumentId는 {SYMBOL}.{VENUE}.
```

- [ ] **Step 7: `README.md` 첫 문단을 스펙대로**

첫 줄을 다음으로 바꾼다:

```
NautilusTrader가 러너다. OKX가 유일한 라이브 거래소이고, catalog는 US ETF 일봉을 받을 수 있다.
```

- [ ] **Step 8: spec 상태 줄**

`docs/superpowers/specs/2026-09-14-etf-catalog-venue-design.md` 상태: `초안 (설계 협의 완료, spec 승인 대기)` → `승인됨`.

- [ ] **Step 9: 전체 단위 테스트**

Run: `uv run pytest tests/test_config tests/test_data tests/test_runners tests/test_strategies/test_no_exchange_io.py -v`

Expected: PASS.

- [ ] **Step 10: Commit**

```
git add NAUTILUS_VIBE_RULES.md AGENTS.md README.md docs/superpowers/specs/2026-09-14-etf-catalog-venue-design.md
git commit -m "docs: allow US ETF daily catalog venues beside live OKX"
```

---

## Manual verification (구현 후, CI 아님)

네트워크와 Yahoo가 필요하다. 기본 스위트에 넣지 않는다.

```
uv pip install -e ".[etf]"
# .env: CATALOG_SOURCE=yahoo-etf
uv run catalog-download
```

기대: `catalog/data/` 아래 `SPY.ARCA`, `QQQ.NASDAQ`, `IWM.ARCA`와 `*-1-DAY-LAST-EXTERNAL`. SPY 첫 날짜가 1990년대.

```
# .env: INSTRUMENT_ID=SPY.ARCA
uv run backtest-okx
```

venue 불일치로 빈 데이터면 실패.

---

## Self-review (spec coverage)

| Spec | Task |
|---|---|
| §2 Yahoo 일봉, SPY/QQQ/IWM, InstrumentId, 1-DAY BarType | 2, 3, 4 |
| §2 OKX 경로 유지, 라이브 팩토리 변경 없음 | 4 (분기), 6 |
| §3 규칙/AGENTS/README/시스템 프롬프트 | 7 |
| §4 yahoo_etf.py + catalog_writer 분기 | 2–4 |
| §4 venue_name / default_bar_type / source_bar_type import | 5 |
| §5 다운로드 흐름, 빈 DF SystemExit, sleep 0.2 | 4 |
| §5.1 row_to_bar | 3 |
| §5.2 Equity | 3 |
| §6 Settings env, instrument_id_str, CATALOG_START optional for yahoo | 1, 4 |
| §6 pyproject `etf = ["yfinance"]` | 4 |
| §7 러너 venue/USD/수수료 env 키 존재 | 5 |
| §7 live_okx `*.OKX` 가드 | 6 |
| §8 테스트 표 | 각 태스크. 네트워크 없음 |
| §10 Alpaca/시간봉 안 함 | 전 태스크 범위 밖 |
