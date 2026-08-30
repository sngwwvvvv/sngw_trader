# OKX 카탈로그 라이터 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** OKX 공개 REST로 1분봉 바 + 인스트루먼트를 `./catalog` ParquetDataCatalog에 적재하는 `catalog-download` CLI를 만든다.

**Architecture:** `catalog_writer.py`가 (1) `OKXInstrumentProvider`로 실제 인스트루먼트를 로드해 catalog에 쓰고, (2) OKX 공개 `/market/history-candles`를 stdlib `urllib`로 페이지네이션해 원시 캔들을 `Bar`로 변환한 뒤 `catalog.write_data`로 적재한다. HTTP는 네트워크라 단위 테스트에서 제외하고, 캔들→`Bar` 변환과 설정 검증만 순수 함수로 단위 테스트한다.

**Tech Stack:** Python 3.12, NautilusTrader 1.231.0 (`ParquetDataCatalog`, `OKXInstrumentProvider`, `Bar.from_raw`), stdlib `urllib`.

**Spec:** `docs/superpowers/specs/2026-08-30-okx-catalog-writer-design.md`

## Global Constraints

- Nautilus 심볼은 설치된 패키지(1.231.0)에서 확인된 것만 쓴다. import 실패 시 임의 클래스 금지(규칙 §5.3).
- 네트워크 필수 테스트는 기본 스위트에 넣지 않는다(규칙 §9).
- `.env` / 소스에 시크릿 하드코딩 금지(규칙 §8).
- 실행 명령은 `uv run pytest ...` (Windows PowerShell). `&&` 금지, `;`와 `if ($?)` 사용.
- 커밋 접두: `feat(data):`.

---

### Task 1: 설정에 `CATALOG_START`/`CATALOG_END` 추가

**Files:**
- Modify: `src/sngw_trader/config/settings.py`
- Modify: `tests/test_config/test_settings.py`
- Modify: `.env.example`

**Interfaces:**
- Produces: `Settings.catalog_start: datetime`, `Settings.catalog_end: datetime` — 필수 필드. `load_settings()`가 `CATALOG_START`/`CATALOG_END`를 파싱하고, 비었거나 형식이 틀리면 `SystemExit` 발생.

- [ ] **Step 1: 기존 설정 테스트에 새 필수 필드 추가**

`tests/test_config/test_settings.py`의 두 테스트 `Settings(...)` 생성자에 필드를 추가한다.

```python
        catalog_start=datetime(2026, 1, 1),
        catalog_end=datetime(2026, 3, 1),
```

`from datetime import datetime` import를 파일 상단에 추가한다.

- [ ] **Step 2: 필수값/파싱 검증 테스트 작성**

같은 파일에 추가:

```python
from sngw_trader.config import load_settings


def _base_env() -> dict[str, str]:
    return {
        "OKX_ENV": "demo",
        "CATALOG_START": "2026-01-01",
        "CATALOG_END": "2026-03-01",
    }


def test_load_settings_parses_catalog_range(monkeypatch) -> None:
    for k, v in _base_env().items():
        monkeypatch.setenv(k, v)
    s = load_settings()
    assert s.catalog_start == datetime(2026, 1, 1)
    assert s.catalog_end == datetime(2026, 3, 1)


def test_load_settings_requires_catalog_start(monkeypatch) -> None:
    for k, v in _base_env().items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("CATALOG_START")
    import pytest
    with pytest.raises(SystemExit):
        load_settings()
```

- [ ] **Step 3: 테스트 실행해 실패 확인**

Run: `uv run pytest tests/test_config/test_settings.py -v`
Expected: FAIL — `Settings`에 `catalog_start`/`catalog_end` 필드 없음.

- [ ] **Step 4: `Settings`에 필드 추가 + 파싱/검증 구현**

`src/sngw_trader/config/settings.py`:
- 상단에 `from datetime import datetime` 추가.
- `Settings` dataclass에 두 필드 추가 (bt 필드 근처):
  ```python
      catalog_start: datetime
      catalog_end: datetime
  ```
- `load_settings()`에 필수값 파싱 함수 추가 (모듈 레벨):
  ```python
  def _required_ts(name: str) -> datetime:
      raw = _env(name)
      if not raw:
          raise SystemExit(f"{name} is required for catalog download")
      try:
          return datetime.fromisoformat(raw)
      except ValueError:
          raise SystemExit(f"{name} must be ISO8601/YYYY-MM-DD, got {raw!r}")
  ```
- `load_settings()` 반환에 추가:
  ```python
      catalog_start=_required_ts("CATALOG_START"),
      catalog_end=_required_ts("CATALOG_END"),
  ```
- 범위 순서 검증: `catalog_end <= catalog_start`이면 `SystemExit`.
  ```python
  catalog_start=_required_ts("CATALOG_START"),
  catalog_end=_required_ts("CATALOG_END"),
  ```
  대신 `load_settings()`에서 한 번 검증:
  ```python
  _start = _required_ts("CATALOG_START")
  _end = _required_ts("CATALOG_END")
  if _end <= _start:
      raise SystemExit(f"CATALOG_END ({_end}) must be after CATALOG_START ({_start})")
  ```

- [ ] **Step 5: 테스트 실행해 통과 확인**

Run: `uv run pytest tests/test_config/test_settings.py -v`
Expected: PASS (기존 2개 + 새 2개)

- [ ] **Step 6: `.env.example`에 변수 추가**

`.env.example`의 설정 블록에 추가 (값 비움):

```
CATALOG_START=
CATALOG_END=
```

- [ ] **Step 7: Commit**

```bash
git add src/sngw_trader/config/settings.py tests/test_config/test_settings.py .env.example
git commit -m "feat(data): add required CATALOG_START/END settings"
```

---

### Task 2: 캔들 → `Bar` 변환 순수 함수

**Files:**
- Modify: `src/sngw_trader/data/catalog_writer.py`
- Create: `tests/test_data/test_catalog_writer.py`

**Interfaces:**
- Consumes: (없음 — 이전 태스크와 독립)
- Produces: `raw_candle_to_bar(raw: list[str], instrument_id: str, price_prec: int, size_prec: int) -> Bar` — OKX history-candles 원시 배열 1줄을 Nautilus `Bar`로 변환. 기존 placeholder(`write_placeholder_note`)는 유지한다.

- [ ] **Step 1: 실패 테스트 작성**

`tests/test_data/` 디렉터리를 만들고 `tests/test_data/test_catalog_writer.py`:

```python
from nautilus_trader.model import Bar

from sngw_trader.data.catalog_writer import raw_candle_to_bar


def test_raw_candle_to_bar_maps_fields() -> None:
    raw = ["1704067200000", "43000", "43100", "42900", "43050", "12.5", "537500", "537500", "1"]
    bar = raw_candle_to_bar(
        raw,
        instrument_id="BTC-USDT-SWAP.OKX",
        price_prec=1,
        size_prec=0,
    )
    assert isinstance(bar, Bar)
    assert bar.bar_type == BarType.from_str("BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL")
    assert str(bar.open) == "43000"
    assert str(bar.high) == "43100"
    assert str(bar.low) == "42900"
    assert str(bar.close) == "43050"
    assert str(bar.volume) == "12.5"
```

상단 import에 `BarType` 추가:

```python
from nautilus_trader.model import Bar, BarType
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `uv run pytest tests/test_data/test_catalog_writer.py -v`
Expected: FAIL — `raw_candle_to_bar` 없음 (ImportError).

- [ ] **Step 3: 구현**

`src/sngw_trader/data/catalog_writer.py`에 기존 내용 유지하며 추가:

```python
from decimal import Decimal

from nautilus_trader.model import Bar, BarType


def raw_candle_to_bar(
    raw: list[str],
    instrument_id: str,
    price_prec: int,
    size_prec: int,
) -> Bar:
    """Map one OKX history-candles row to a Nautilus Bar.

    row: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
    """
    ts_ms = int(raw[0])
    ts_ns = ts_ms * 1_000_000
    bar_type = BarType.from_str(f"{instrument_id}-1-MINUTE-LAST-EXTERNAL")
    return Bar.from_raw(
        bar_type=bar_type,
        open=Decimal(raw[1]),
        high=Decimal(raw[2]),
        low=Decimal(raw[3]),
        close=Decimal(raw[4]),
        price_prec=price_prec,
        volume=Decimal(raw[5]),
        size_prec=size_prec,
        ts_event=ts_ns,
        ts_init=ts_ns,
    )
```

- [ ] **Step 4: 테스트 실행해 통과 확인**

Run: `uv run pytest tests/test_data/test_catalog_writer.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/data/catalog_writer.py tests/test_data/test_catalog_writer.py
git commit -m "feat(data): map OKX candle row to Nautilus Bar"
```

---

### Task 3: 인스트루먼트 로드 + 다운로드 + 진입점

**Files:**
- Modify: `src/sngw_trader/data/catalog_writer.py`
- Modify: `pyproject.toml`
- Test: `tests/test_data/test_catalog_writer.py` (추가 assertion)

**Interfaces:**
- Consumes: `Settings` (catalog_path, symbol, instrument_id_str, catalog_start, catalog_end) — Task 1. `raw_candle_to_bar` — Task 2.
- Produces: `load_instrument(settings) -> Instrument`, `download_bars(settings) -> list[Bar]`, `main() -> None`. `pyproject.toml`에 `catalog-download` 스크립트.

- [ ] **Step 1: BarType 일치 단위 테스트 추가**

`tests/test_data/test_catalog_writer.py`에 추가:

```python
from sngw_trader.runners.backtest_okx import default_bar_type


def test_bar_type_matches_backtest_runner() -> None:
    assert raw_candle_to_bar(["1", "2", "3", "2", "2", "1", "", "", ""],
                             "BTC-USDT-SWAP.OKX", 1, 0).bar_type \
        == BarType.from_str(default_bar_type("BTC-USDT-SWAP.OKX"))
```

- [ ] **Step 2: 테스트 실행해 실패 확인**

Run: `uv run pytest tests/test_data/test_catalog_writer.py::test_bar_type_matches_backtest_runner -v`
Expected: FAIL — `raw_candle_to_bar` 없음(현재 구현이 아직 없으므로). *이 순서는 Task 2 이후이므로 실제로는 통과한다 — 이미 구현된 상태면 그대로 PASS 기대.*

> 참고: Task 3 시작 시점엔 `raw_candle_to_bar`가 이미 있으므로 이 테스트는 PASS다. 여기선 일치 계약을 고정하는 회귀 테스트로 추가한다.

- [ ] **Step 3: 인스트루먼트 로드 + 다운로드 구현**

`src/sngw_trader/data/catalog_writer.py`에 추가 (기존 유지):

```python
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from nautilus_trader.model.instruments import Instrument

from sngw_trader.config import Settings

_HISTORY_URL = "https://www.okx.com/api/v5/market/history-candles"


def _okx_http_client():
    from nautilus_trader.adapters.okx.factories import get_cached_okx_http_client
    return get_cached_okx_http_client()


def load_instrument(settings: Settings) -> Instrument:
    from nautilus_trader.adapters.okx import OKXInstrumentProvider
    from nautilus_trader.core.nautilus_pyo3.okx import OKXInstrumentType

    client = _okx_http_client()
    provider = OKXInstrumentProvider(
        client,
        instrument_types=(OKXInstrumentType.SWAP,),
    )
    provider.load_all()
    instruments = provider.get_all()
    return instruments[settings.instrument_id_str]


def _fetch_candles(symbol: str, after_ms: int | None, limit: int = 100) -> list[list[str]]:
    params = {"instId": symbol, "bar": "1m", "limit": str(limit)}
    if after_ms is not None:
        params["after"] = str(after_ms)
    url = _HISTORY_URL + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("code") != "0":
        raise RuntimeError(f"OKX history-candles error: {payload}")
    return payload["data"]


def download_bars(settings: Settings) -> list[Bar]:
    instrument = load_instrument(settings)
    symbol = settings.symbol.upper().replace(".OKX", "")
    start_ms = int(settings.catalog_start.replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(settings.catalog_end.replace(tzinfo=timezone.utc).timestamp() * 1000)

    bars: list[Bar] = []
    oldest = None
    while True:
        rows = _fetch_candles(symbol, oldest)
        if not rows:
            break
        for raw in rows:
            ts_ms = int(raw[0])
            if end_ms < ts_ms or ts_ms < start_ms:
                continue
            bars.append(
                raw_candle_to_bar(
                    raw,
                    settings.instrument_id_str,
                    instrument.price_precision,
                    instrument.size_precision,
                )
            )
        oldest = int(rows[-1][0])
        if oldest < start_ms or len(rows) < 100:
            break
        time.sleep(0.1)  # OKX rate limit guard; public candles, no auth
    return bars
```

- [ ] **Step 4: `main()` 구현 + 진입점**

같은 파일에 추가:

```python
from nautilus_trader.persistence.catalog import ParquetDataCatalog


def main() -> None:
    settings = load_settings()
    settings.catalog_path.mkdir(parents=True, exist_ok=True)
    catalog = ParquetDataCatalog(settings.catalog_path)

    instrument = load_instrument(settings)
    catalog.write_data([instrument], data_cls=Instrument)

    bars = download_bars(settings)
    if not bars:
        raise SystemExit("No candles downloaded for the requested range")
    catalog.write_data(bars, data_cls=Bar)

    first = min(b.ts_event for b in bars)
    last = max(b.ts_event for b in bars)
    print(
        f"Wrote {len(bars)} bars for {settings.instrument_id_str} "
        f"({_fmt(first)} -> {_fmt(last)}) to {settings.catalog_path}"
    )


def _fmt(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).isoformat()
```

`import` 추가: `from sngw_trader.config import load_settings, Settings`.

`pyproject.toml` `[project.scripts]`에 추가:

```
catalog-download = "sngw_trader.data.catalog_writer:main"
```

- [ ] **Step 5: 테스트 실행**

Run: `uv run pytest tests/test_data/test_catalog_writer.py -v`
Expected: PASS (2개)

- [ ] **Step 6: 수동 smoke — 실제 다운로드 검증**

env에서 (PowerShell, 키 값은 .env 또는 더미 — 공개 엔드포인트는 서명 불요):

```powershell
uv run catalog-download
```

Expected: `Wrote N bars for BTC-USDT-SWAP.OKX (2026-... -> 2026-...) to ...\catalog` 출력. `./catalog`에 parquet 생성.

- [ ] **Step 7: Commit**

```bash
git add src/sngw_trader/data/catalog_writer.py pyproject.toml tests/test_data/test_catalog_writer.py
git commit -m "feat(data): add catalog-download CLI for OKX 1m bars"
```

---

## Self-Review

**1. Spec coverage:**
- §2 소스/타입/BarType/심볼/범위/페이지네이션/코드위치/진입점 → Task 1,2,3 모두 구현.
- §3 역할분리(HTTP는 writer에만, 주문 없음) → `data/catalog_writer.py`에만 네트워크, 전략/러너 무관.
- §4 다운로드 흐름(역방향 페이지네이션, 종료조건) → Task 3 `download_bars`.
- §4.1 Bar 변환(ms→datetime, Decimal, BarType) → Task 2 `raw_candle_to_bar` (ts_ns로 변환).
- §5 설정 필수값 → Task 1.
- §6 진입점 → Task 3 pyproject.
- §7 테스트(순수 변환, 필수값, BarType 일치) → Task 1/2/3.
- §9 YAGNI(틱/Tardis/incremental/다중심볼/프라이빗) → 계획에 포함 안 함. ✓

**2. Placeholder scan:** 모든 코드 단계에 실제 코드 있음. "적절한 에러 처리"류 없음.

**3. Type consistency:** `raw_candle_to_bar(raw, instrument_id, price_prec, size_prec)`가 Task 2(정의)와 Task 3(사용)에서 동일. `Settings.catalog_start/catalog_end` Task 1 정의 = Task 3 사용 동일. `default_bar_type`는 기존 `backtest_okx.py` 재사용, `instrument.price_precision/size_precision`는 Nautilus Instrument 표준 속성.

**알려진 계획상 가정 (구현 시 재확인):**
- `get_cached_okx_http_client()`는 `OKX_API_KEY/SECRET/PASSPHRASE` env가 **설정돼 있어야** 생성됨(공개 호출이라 값은 유효성 검증 안 됨). `.env`에서 로드되므로 정상. 실제 요청 전 `_fetch_candles`/`load_instrument` 호출 결과로 확인.
- OKX `history-candles`는 최신→과거 순 반환, `after`는 "이 시각보다 과거" 필터. 실데이터로 페이지네이션 방향 재확인.
- `provider.load_all()`는 네트워크 호출. 실제로 가져오는지 수동 smoke에서 확인.