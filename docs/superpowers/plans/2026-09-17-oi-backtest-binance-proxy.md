# OI A/B 백테스트 (Binance OI proxy) + OI 수집기 스펙 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Binance 무료 OI proxy로 OI A/B 평균회귀 전략의 IS/WF 백테스트를 실행하고, 별도 repo에서 구현할 OKX OI 수집기의 스펙 문서를 작성한다.

**Architecture:** 기존 research 파이프라인(`windows`/`walk_forward`/`executor`/`metrics`/`select`/`report`)을 최대 재사용. 신규는 (1) Binance metrics CSV→카탈로그 임포터, (2) executor의 OI run_window 변형, (3) A/B 비교 모듈, (4) 수집기 스펙 문서. 러너는 `BacktestNode`만.

**Tech Stack:** Python 3.12, nautilus_trader, pytest, urllib/zipfile(stdlib), ParquetDataCatalog

**Spec:** `docs/superpowers/specs/2026-09-17-oi-backtest-binance-proxy-design.md`

## Global Constraints

- 러너는 `BacktestNode`만. 자체 event loop 금지 (`NAUTILUS_VIBE_RULES.md`)
- 라이브 거래소는 OKX만. Binance 데이터는 백테스트 연구 proxy로만 사용하고 라이브 경로에 절대 유입 금지
- 모든 리포트에 "Binance OI proxy — NOT OKX data" 라벨 필수
- OI 주기 5m. 25m era 데이터 스킵
- 시크릿은 env. `ccxt`, `python-okx` 금지 — HTTP는 `urllib.request` (기존 `open_interest.py` 선례)
- OI 전략은 `BTC-USDT-SWAP.OKX` 전용 (`OI_INSTRUMENT_ID` 검증 유지)
- 테스트는 네트워크 호출 금지 — fetch는 monkeypatch로 대체

---

### Task 1: OI 수집기 스펙 문서 (별도 repo 구현용)

**Files:**
- Create: `docs/specs/oi-collector-railway-spec.md`

**Interfaces:**
- Consumes: 없음 (설계 문서 §4.4)
- Produces: 별도 repo에서 구현할 수집기의 단일 스펙. 이 repo의 후속 태스크와 코드 결합 없음

- [ ] **Step 1: 스펙 문서 작성**

아래 내용 그대로 작성한다:

```markdown
# OKX OI 수집기 스펙 (Railway 배포, 별도 repo)

- 작성: 2026-09-17
- 배경: OKX rubik open-interest-history API는 약 4~5일만 소급 가능(2026-09-17 프로브).
  실측 OKX OI를 축적하려면 지금부터 전진 수집이 필요하다.
- 이 문서는 별도 repo에서 구현한다. tripletail repo에 구현하지 않는다.

## 1. 목적

OKX BTC-USDT-SWAP 5분 Open Interest를 매일 수집하여 Railway Volume에
월별 parquet으로 축적한다. 축적 데이터는 tripletail 백테스트 카탈로그에
pull-sync 되어 OI 전략의 실측 재검증 구간을 만든다.

## 2. 비목표

- 가격 bar 수집 없음 (OKX 1m bar는 tripletail의 catalog_writer로 소급 가능)
- 타 심볼/타 거래소 수집 없음
- 주문/트레이딩 기능 없음. `ccxt`, `python-okx` 사용 금지 — 원본 REST만
- 실시간 WebSocket 스트리밍 없음 (REST 히스토리 폴링만)

## 3. 데이터 원천

- Endpoint: `GET https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-history`
- Params: `instId=BTC-USDT-SWAP&period=5m&end=<cursor_ms>&limit=100`
- 응답 rows: `[ts_ms, oi, oiCcy, oiCcyQuote]`. **저장 필드는 `ts`와 `oi`만**
  (tripletail `OpenInterestPoint`와 동일한 contract-value 필드)
- 페이지네이션: 가장 오래된 row의 ts를 다음 `end`로 쓰며 소급. 48시간 커버에
  필요한 만큼 반복 (약 576 rows, 6페이지)
- Rate limit: 페이지당 0.5초 sleep

## 4. 수집 동작 (Railway Cron, 매일 03:00 UTC)

1. `end = now_ms + 300_000` 커서로 소급 fetch 시작
2. 최근 **48시간** 구간의 rows 수집 (API 깊이 4~5일 > 48h이므로 overlap 충분.
   cron 실패 1회는 다음날 실행이 덮는다. 2회 연속 실패까지도 5일 깊이가 커버)
3. `ts` 기준 5분 정렬 검증, `ts` 중복 dedupe (key = ts_ms)
4. 기존 월 parquet과 병합: 같은 ts가 있으면 기존 값 유지(불변 원칙), 새 ts만 append
5. `/data/oi/BTC-USDT-SWAP/YYYY-MM.parquet` 에 write (파티션 = 월)
   - 스키마: `ts_ms: int64, open_interest: float64`
   - write는 임시 파일 생성 후 atomic rename
6. 결과 로그: 수집 rows 수, 신규 append 수, 스킵(중복) 수
7. **Gap 감지**: 신규 append가 기대치(기존 마지막 ts ~ 현재 사이 5분 경계 수)의
   90% 미만이면 stderr 경고 로그 + exit code 2 (Railway 대시보드에서 실패로 보임)

## 5. 멱등성

- 같은 날 재실행해도 결과 동일: 중복 ts는 스킵, 값 변경 없음
- 월 경계 처리: 48h 커버가 두 달에 걸치면 두 파티션에 각각 append

## 6. 저장소 (Railway Volume)

- Volume mount: `/data`
- 레이아웃:
  ```
  /data/oi/BTC-USDT-SWAP/2026-09.parquet
  /data/oi/BTC-USDT-SWAP/2026-10.parquet
  ```
- 크기 추정: 5m OI = 하루 288 rows ≈ 연간 ~420KB parquet. 10GB volume이면
  사실상 무제한

## 7. 백업 / pull-sync (volume 유실 대비)

- Railway Volume은 서비스 삭제 시 데이터 소실. **월 1회 이상** 로컬로 pull:
  ```
  railway ssh --service oi-collector -- "cat /data/oi/BTC-USDT-SWAP/2026-09.parquet" > 2026-09.parquet
  ```
  (파일별로 반복. 파일 목록은 로그 또는 `ls`로 확인)
- pull-sync 받은 parquet은 tripletail repo의 카탈로그에 병합하는 변환기가
  필요하면 그때 tripletail에 추가한다 (지금 만들지 않음)

## 8. 구현 요구사항 (별도 repo)

- 언어: Python 3.12. 의존성은 `pyarrow`만 (HTTP는 stdlib urllib)
- 파일 구성:
  ```
  main.py          # collect() 1회 실행 — Railway cron이 이것을 호출
  requirements.txt # pyarrow
  .env.example     # 변수 없음(공개 API). 필요시 OI_SYMBOL만
  README.md        # 이 스펙 요약 + Railway 배포 절차
  ```
- Railway 설정: Service type = Cron (매일 03:00 UTC), Volume mount `/data`
- 배포 절차는 README에 5줄 이내로: repo 연결 → Cron 설정 → Volume attach → 최초
  수동 실행 → 로그 확인

## 9. 검수 기준

- [ ] 2회 연속 실행해도 parquet row 수가 중복 없이 동일하게 유지된다
- [ ] 48h 이전 데이터(API 깊이 밖) 요청 시 경고 없이 정상 종료된다
- [ ] 의도적으로 ts를 하나 빼고 재실행하면 해당 ts만 append된다
- [ ] gap 감지 경고가 exit code 2로 이어진다
```

- [ ] **Step 2: 커밋**

```bash
git add docs/specs/oi-collector-railway-spec.md
git commit -m "docs: OKX OI collector spec for separate Railway repo"
```

---

### Task 2: Binance metrics CSV 파서 + 간격 검출

**Files:**
- Create: `src/sngw_trader/data/binance_oi.py`
- Test: `tests/test_data/test_binance_oi.py`

**Interfaces:**
- Consumes: `sngw_trader.data.open_interest.OpenInterestPoint`, `OI_INSTRUMENT_ID`
- Produces: `parse_metrics_csv(csv_text: str) -> tuple[list[OpenInterestPoint], str]` — (5m 포인트 목록, 검출된 interval "5m"/"25m"/기타). Task 3의 `scan_binance_oi`가 소비

- [ ] **Step 1: 실패하는 테스트 작성**

```python
from datetime import datetime, timezone

from sngw_trader.data.binance_oi import parse_metrics_csv

HEADER = "create_time,symbol,sum_open_interest,sum_open_interest_value"


def _csv(*rows: str) -> str:
    return "\n".join([HEADER, *rows])


def _five_minute_csv() -> str:
    return _csv(
        "2023-06-01 00:00:00,BTCUSDT,111645.70,3036897096",
        "2023-06-01 00:05:00,BTCUSDT,111650.87,3039706128",
        "2023-06-01 00:10:00,BTCUSDT,111706.19,3042117013",
    )


def test_parse_metrics_csv_returns_five_minute_points() -> None:
    points, interval = parse_metrics_csv(_five_minute_csv())
    assert interval == "5m"
    assert len(points) == 3
    expected = int(
        datetime(2023, 6, 1, 0, 5, tzinfo=timezone.utc).timestamp() * 1_000_000_000
    )
    assert points[1].ts_event == expected
    assert points[1].ts_init == expected
    assert points[1].open_interest == 111650.87
    assert str(points[1].instrument_id) == "BTC-USDT-SWAP.OKX"


def test_parse_metrics_csv_rejects_coarser_interval() -> None:
    text = _csv(
        "2026-09-15 00:00:00,BTCUSDT,103513.56,8088539305",
        "2026-09-15 00:25:00,BTCUSDT,103351.07,8059947582",
        "2026-09-15 00:50:00,BTCUSDT,103176.44,8039975408",
    )
    points, interval = parse_metrics_csv(text)
    assert points == []
    assert interval == "25m"


def test_parse_metrics_csv_unknown_on_short_input() -> None:
    assert parse_metrics_csv("create_time,symbol\n") == ([], "unknown")
    assert parse_metrics_csv("") == ([], "unknown")
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_data/test_binance_oi.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sngw_trader.data.binance_oi'`

- [ ] **Step 3: 최소 구현**

```python
"""Binance Vision OI metrics importer. Research proxy only — never live data.

Binance BTCUSDT futures metrics CSV provides `sum_open_interest` at 5m
intervals for the older era (25m in recent years). This proxies the OKX OI
hypothesis for backtests. NAUTILUS_VIBE_RULES: live is OKX only.
"""

from __future__ import annotations

import io
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone

from nautilus_trader.model import InstrumentId

from sngw_trader.data.open_interest import OI_INSTRUMENT_ID, OpenInterestPoint

METRICS_URL = "https://data.binance.vision/data/futures/um/daily/metrics/BTCUSDT"
_FIVE_MINUTE_S = 300
BINANCE_OI_SOURCE = "binance-vision-metrics (research proxy, not OKX)"


def parse_metrics_csv(csv_text: str) -> tuple[list[OpenInterestPoint], str]:
    """-> (5m points, detected interval). Non-5m days yield no points."""
    lines = [line for line in csv_text.strip().splitlines() if line.strip()]
    if len(lines) < 3:
        return [], "unknown"
    header = lines[0].split(",")
    ts_col = header.index("create_time")
    oi_col = header.index("sum_open_interest")
    rows = []
    for line in lines[1:]:
        cols = line.split(",")
        ts = datetime.strptime(cols[ts_col], "%Y-%m-%d %H:%M:%S")
        rows.append((int(ts.replace(tzinfo=timezone.utc).timestamp()), float(cols[oi_col])))
    diffs = sorted(b - a for (a, _), (b, _) in zip(rows, rows[1:]))
    median = diffs[len(diffs) // 2]
    interval = "5m" if median == _FIVE_MINUTE_S else f"{median // 60}m"
    if interval != "5m":
        return [], interval
    points = [
        OpenInterestPoint(
            ts_event=ts * 1_000_000_000,
            ts_init=ts * 1_000_000_000,
            instrument_id=InstrumentId.from_str(OI_INSTRUMENT_ID),
            open_interest=oi,
        )
        for ts, oi in rows
    ]
    return points, interval
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_data/test_binance_oi.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: 커밋**

```bash
git add src/sngw_trader/data/binance_oi.py tests/test_data/test_binance_oi.py
git commit -m "feat(data): Binance metrics OI CSV parser with interval detection"
```

---

### Task 3: zip fetch + era 스캔 + provenance override + 카탈로그 write CLI

**Files:**
- Modify: `src/sngw_trader/data/open_interest.py:185-199` (`open_interest_data_type`, `wrap_open_interest`에 source 파라미터 추가)
- Modify: `src/sngw_trader/data/binance_oi.py` (fetch/scan/write/main 추가)
- Test: `tests/test_data/test_binance_oi.py` (케이스 추가), `tests/test_data/test_open_interest.py` (source override 케이스 추가)

**Interfaces:**
- Consumes: Task 2의 `parse_metrics_csv`
- Produces:
  - `fetch_metrics_day(day: date, timeout: int = 60) -> str | None` (404면 None)
  - `scan_binance_oi(start: date, end: date, fetch=fetch_metrics_day) -> tuple[list[OpenInterestPoint], date | None]` — (전체 포인트, 최초 비-5m 날짜 또는 None)
  - `write_binance_oi(settings, points) -> None`
  - `main()` CLI: `python -m sngw_trader.data.binance_oi --start 2020-09-01 --end 2026-09-16`
  - `open_interest_data_type(instrument_id, source=None)`, `wrap_open_interest(point, source=None)` — 기본 동작 불변(OKX provenance)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_data/test_binance_oi.py`에 추가:

```python
from datetime import date

from sngw_trader.data.binance_oi import scan_binance_oi


def test_scan_stops_at_era_end_and_dedupes() -> None:
    days = {
        date(2023, 5, 31): _five_minute_csv(),
        date(2023, 6, 1): _five_minute_csv(),
        date(2023, 6, 2): _csv(
            "2023-06-02 00:00:00,BTCUSDT,1.0,1.0",
            "2023-06-02 00:25:00,BTCUSDT,1.1,1.1",
            "2023-06-02 00:50:00,BTCUSDT,1.2,1.2",
        ),
    }
    points, era_end = scan_binance_oi(
        date(2023, 5, 30), date(2023, 6, 30), fetch=days.get
    )
    assert era_end == date(2023, 6, 2)
    # 30일은 404(None)로 스킵, 5m era 2일치 = 6 points (288 rows가 아닌 축소 샘플)
    assert len(points) == 6
    assert all(str(p.instrument_id) == "BTC-USDT-SWAP.OKX" for p in points)


def test_scan_returns_all_5m_when_era_never_ends() -> None:
    days = {date(2023, 6, d): _five_minute_csv() for d in (1, 2, 3)}
    points, era_end = scan_binance_oi(date(2023, 6, 1), date(2023, 6, 3), fetch=days.get)
    assert era_end is None
    assert len(points) == 9
```

`tests/test_data/test_open_interest.py`에 추가:

```python
def test_open_interest_data_type_accepts_source_override():
    metadata = open_interest_data_type("BTC-USDT-SWAP.OKX", source="custom").metadata
    assert metadata["open_interest_source"] == "custom"
    # 기본값 불변
    assert (
        open_interest_data_type("BTC-USDT-SWAP.OKX").metadata["open_interest_source"]
        == "OKX oi contract-value field"
    )
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_data/test_binance_oi.py tests/test_data/test_open_interest.py -v`
Expected: FAIL (`scan_binance_oi` 미정의, source 파라미터 없음)

- [ ] **Step 3: 구현**

`open_interest.py`의 `open_interest_data_type`/`wrap_open_interest`를 교체:

```python
def open_interest_data_type(instrument_id: str, source: str | None = None) -> DataType:
    _require_oi_instrument(instrument_id)
    return DataType(
        OpenInterestPoint,
        metadata={
            "instrument_id": instrument_id,
            "source_endpoint": _OPEN_INTEREST_HISTORY_URL,
            "open_interest_source": source or "OKX oi contract-value field",
        },
    )


def wrap_open_interest(point: OpenInterestPoint, source: str | None = None) -> CustomData:
    _require_oi_instrument(point.instrument_id.value)
    return CustomData(open_interest_data_type(point.instrument_id.value, source), point)
```

`binance_oi.py`에 추가:

```python
def fetch_metrics_day(day: date, timeout: int = 60) -> str | None:
    url = f"{METRICS_URL}/BTCUSDT-metrics-{day:%Y-%m-%d}.zip"
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".csv"))
        return zf.read(name).decode("utf-8")


def scan_binance_oi(
    start: date, end: date, fetch=fetch_metrics_day
) -> tuple[list[OpenInterestPoint], date | None]:
    """Days oldest->newest, stop at the first non-5m day. -> (points, era_end)."""
    points: dict[int, OpenInterestPoint] = {}
    day = start
    while day <= end:
        text = fetch(day)
        if text is not None:
            day_points, interval = parse_metrics_csv(text)
            if interval != "5m":
                return [points[ts] for ts in sorted(points)], day
            for point in day_points:
                points[point.ts_event] = point
        day += timedelta(days=1)
    return [points[ts] for ts in sorted(points)], None


def write_binance_oi(settings, points: list[OpenInterestPoint]) -> None:
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    from sngw_trader.data.open_interest import register_open_interest, wrap_open_interest

    register_open_interest()
    catalog = ParquetDataCatalog(str(settings.catalog_path))
    catalog.write_data(
        [wrap_open_interest(point, source=BINANCE_OI_SOURCE) for point in points],
        data_cls=OpenInterestPoint,
    )


def main() -> None:
    import argparse

    from sngw_trader.config import load_settings

    parser = argparse.ArgumentParser(description="Download Binance OI proxy into the catalog")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD inclusive")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD inclusive")
    args = parser.parse_args()
    settings = load_settings()
    points, era_end = scan_binance_oi(date.fromisoformat(args.start), date.fromisoformat(args.end))
    if not points:
        raise SystemExit("No 5m Binance OI rows in the requested range")
    write_binance_oi(settings, points)
    print(f"binance oi points={len(points)} era_end={era_end}")
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_data/ -v`
Expected: PASS (기존 테스트 포함 전부 — 기본 provenance 불변 확인)

- [ ] **Step 5: 커밋**

```bash
git add src/sngw_trader/data/binance_oi.py src/sngw_trader/data/open_interest.py tests/test_data/
git commit -m "feat(data): Binance OI proxy era scan + catalog write with source provenance"
```

---

### Task 4: executor `run_oi_window`

**Files:**
- Modify: `src/sngw_trader/research/executor.py` (`run_window`의 extraction 블록을 `_collect_result` 헬퍼로 추출 + `run_oi_window` 추가)
- Test: `tests/test_research/test_executor.py`

**Interfaces:**
- Consumes: `sngw_trader.runners.backtest_oi.build_oi_run_config/attach_oi_data/oi_bar_type`, 기존 `build_strategy`
- Produces: `run_oi_window(catalog_path: str, instrument_id: str, settings, spec: GridSpec, params: dict, start: datetime, end: datetime, warmup_days: int = 1, quiet: bool = True) -> RunResult` — Task 5의 compare 모듈이 소비

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_research/test_executor.py`에 추가:

```python
from datetime import datetime, timezone
from types import SimpleNamespace

from sngw_trader.research.config import GridSpec


def test_run_oi_window_wires_oi_strategy_and_data(monkeypatch, tmp_path):
    """run_oi_window가 composite bar_type 전략을 붙이고 OI 데이터를 attach하는지 확인."""
    import sngw_trader.research.executor as ex
    from sngw_trader.strategies.oi_crowding_mean_reversion import OiCrowdingMeanReversion

    OI_SPEC = GridSpec(
        strategy_path="sngw_trader.strategies.oi_crowding_mean_reversion:OiCrowdingMeanReversion",
        config_path="sngw_trader.strategies.oi_crowding_mean_reversion:OiCrowdingMeanReversionConfig",
        fixed={"trade_size": "0.01", "oi_client_id": "BACKTEST"},
        grid={},
    )

    attached = {}

    def fake_attach(engine, catalog, instrument_id, start_ns, end_ns):
        attached["args"] = (instrument_id, start_ns, end_ns)

    captured = {}

    class FakeEngine:
        def __init__(self):
            self.cache = SimpleNamespace(positions=lambda: [], position_snapshots=lambda: [])
            self.portfolio = SimpleNamespace(
                analyzer=SimpleNamespace(portfolio_returns=lambda: None)
            )
            self.trader = SimpleNamespace(
                generate_order_fills_report=lambda: None
            )

        def add_strategy(self, strategy):
            captured["strategy"] = strategy

    class FakeNode:
        def __init__(self, configs):
            captured["configs"] = configs

        def build(self):
            pass

        def get_engine(self, run_id):
            return FakeEngine()

        def run(self):
            pass

        def dispose(self):
            pass

    monkeypatch.setattr(ex, "BacktestNode", FakeNode)
    monkeypatch.setattr(ex, "attach_oi_data", fake_attach)
    monkeypatch.setattr(
        ex, "build_oi_run_config",
        lambda settings, start, end: SimpleNamespace(id="r1"),
    )

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    result = ex.run_oi_window(
        str(tmp_path), "BTC-USDT-SWAP.OKX", settings=SimpleNamespace(),
        spec=OI_SPEC, params={}, start=start,
        end=datetime(2026, 2, 1, tzinfo=timezone.utc), warmup_days=1,
    )

    assert result.n_trades == 0  # 빈 엔진에서 빈 결과
    assert type(captured["strategy"]).__name__ == "OiCrowdingMeanReversion"
    assert str(captured["strategy"].config.bar_type) == (
        "BTC-USDT-SWAP.OKX-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL"
    )
    instrument_id, start_ns, end_ns = attached["args"]
    assert instrument_id == "BTC-USDT-SWAP.OKX"
    assert start_ns == int(start.timestamp() * 1_000_000_000) - 86_400_000_000_000
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_research/test_executor.py -v`
Expected: FAIL with `AttributeError: ... has no attribute 'run_oi_window'`

- [ ] **Step 3: 구현**

`executor.py` 수정. 상단 import 추가:

```python
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from sngw_trader.runners.backtest_oi import (
    attach_oi_data,
    build_oi_run_config,
    oi_bar_type,
)
```

`run_window`의 extraction 블록(positions→marks→fills)을 헬퍼로 추출하고 `run_window` 마지막은 이를 사용:

```python
def _collect_result(engine, start: datetime) -> RunResult:
    window_start_ns = dt_to_unix_nanos(start)
    result = extract_run_result(
        engine.cache.positions() + engine.cache.position_snapshots(),
        window_start_ns,
    )
    marks = extract_analyzer_equity_marks(
        engine.portfolio.analyzer, 10_000.0, window_start_ns
    )
    fills_df = engine.trader.generate_order_fills_report()
    fills = extract_fills(fills_df, window_start_ns)
    return replace(result, equity_marks=marks, fills=fills)
```

`run_oi_window` 추가:

```python
def run_oi_window(
    catalog_path: str,
    instrument_id: str,
    settings: Settings,
    spec: GridSpec,
    params: dict[str, object],
    start: datetime,
    end: datetime,
    warmup_days: int = 1,
    quiet: bool = True,
):
    """One OI backtest window: OKX 1m bars + custom OI points, BacktestNode runner."""
    run_config = build_oi_run_config(
        settings,
        start=start - timedelta(days=warmup_days),
        end=end,
    )
    node = BacktestNode(configs=[run_config])
    node.build()
    engine = node.get_engine(run_config.id)
    if engine is None:
        raise RuntimeError(f"Engine not built for run config {run_config.id}")
    engine.add_strategy(
        build_strategy(spec, params, instrument_id, str(oi_bar_type(instrument_id)))
    )
    try:
        attach_oi_data(
            engine,
            ParquetDataCatalog(catalog_path),
            instrument_id,
            dt_to_unix_nanos(start - timedelta(days=warmup_days)),
            dt_to_unix_nanos(end),
        )
        node.run()
        return _collect_result(engine, start)
    finally:
        node.dispose()
```

`run_window`의 try 블록도 `_collect_result(engine, start)`로 교체 (중복 제거).

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_research/test_executor.py tests/test_runners/ -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add src/sngw_trader/research/executor.py tests/test_research/test_executor.py
git commit -m "feat(research): run_oi_window executor variant for OI strategies"
```

---

### Task 5: OI A/B compare 모듈 (oneshot + WF + stress)

**Files:**
- Create: `src/sngw_trader/research/oi_mean_reversion_compare.py`
- Test: `tests/test_research/test_oi_mean_reversion_compare.py`

**Interfaces:**
- Consumes: Task 4 `run_oi_window`, `walk_forward`(detect_data_range, build_wf_configs, robustness_summary, stitch_*, bh_metrics, load_daily_closes, run_metrics, oos_is_sharpe_ratio, bootstrap_trades 경유), `select`(param_keys, select_best, sharpe_from_trades), `windows`(compute_windows, holdout_start), `report`
- Produces: `run_compare(settings, wf_cfg, mc_cfg, out_dir, days, specs=SPECS, run_window=_run_window)`, `run_oneshot(...)`, `main()` — CLI `python -m sngw_trader.research.oi_mean_reversion_compare`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
from datetime import datetime, timezone
from types import SimpleNamespace

from sngw_trader.research.config import GridSpec, MCConfig, WalkForwardConfig
from sngw_trader.research.executor import RunResult
from sngw_trader.research.oi_mean_reversion_compare import run_compare



def _spec(name: str, grid: dict) -> GridSpec:
    return GridSpec(
        strategy_path=f"pkg:{name}",
        config_path=f"pkg:{name}Config",
        fixed={},
        grid=grid,
    )


def test_run_compare_selects_best_and_reports(monkeypatch, tmp_path):
    import sngw_trader.research.oi_mean_reversion_compare as cmp

    monkeypatch.setattr(
        cmp, "detect_data_range",
        lambda path, bar_id: (
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 9, 1, tzinfo=timezone.utc),
        ),
    )

    def fake_window(settings, spec, params, start, end, warmup_days):
        # reentry_bars=3일 때만 2개의 좋은 트레이드, 아니면 min_trades 미달 1개
        if params.get("reentry_bars") == 3:
            return RunResult([1.0, 2.0], [0.01, 0.02], 2, 3.0)
        return RunResult([0.5], [0.005], 1, 0.5)

    specs = {"oi_a": _spec("oi_a", {"reentry_bars": [3, 5]})}
    settings = SimpleNamespace(catalog_path=tmp_path, instrument_id_str="BTC-USDT-SWAP.OKX")
    wf_cfg = WalkForwardConfig(is_months=1, oos_months=1, holdout_months=1, warmup_days=1, min_trades=2)
    mc_cfg = MCConfig(initial_capital=10_000.0)

    summary = run_compare(
        settings, wf_cfg, mc_cfg, out_dir=tmp_path,
        days=[(0, 100.0)], specs=specs, run_window=fake_window,
    )

    assert summary["proxy"] == cmp.PROXY_LABEL
    per_a = summary["strategies"]["oi_a"]
    assert per_a["windows"][0]["selected"] == {"reentry_bars": 3}
    assert per_a["windows"][0]["oos"]["n_trades"] == 2
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "wf_report.json").exists()


def test_run_compare_skips_window_without_eligible_params(monkeypatch, tmp_path):
    import sngw_trader.research.oi_mean_reversion_compare as cmp

    monkeypatch.setattr(
        cmp, "detect_data_range",
        lambda path, bar_id: (
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 9, 1, tzinfo=timezone.utc),
        ),
    )

    def fake_window(settings, spec, params, start, end, warmup_days):
        return RunResult([0.5], [0.005], 1, 0.5)  # 항상 min_trades 미달

    specs = {"oi_a": _spec("oi_a", {"reentry_bars": [3, 5]})}
    settings = SimpleNamespace(catalog_path=tmp_path, instrument_id_str="BTC-USDT-SWAP.OKX")
    wf_cfg = WalkForwardConfig(is_months=1, oos_months=1, holdout_months=1, warmup_days=1, min_trades=2)

    summary = run_compare(
        settings, wf_cfg, mc_cfg=MCConfig(initial_capital=10_000.0), out_dir=tmp_path,
        days=[(0, 100.0)], specs=specs, run_window=fake_window,
    )
    per_a = summary["strategies"]["oi_a"]
    assert per_a["windows"][0]["selected"] is None
    assert per_a["windows"][0]["oos"]["n_trades"] is None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest tests/test_research/test_oi_mean_reversion_compare.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: 구현**

```python
"""OI A/B IS/WF compare on the Binance OI proxy. BacktestNode is the runner.

OI proxy: Binance BTCUSDT metrics (NOT OKX). Every report labels the proxy.
Grids live in SPECS below; adjust after the oneshot sanity run (Task 6).
"""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timezone

from sngw_trader.config import load_settings
from sngw_trader.data.open_interest import OI_INSTRUMENT_ID
from sngw_trader.research import report
from sngw_trader.research.config import GridSpec
from sngw_trader.research.executor import RunResult, run_oi_window
from sngw_trader.research.monte_carlo import bootstrap_trades
from sngw_trader.research.select import param_keys, select_best, sharpe_from_trades
from sngw_trader.research.walk_forward import (
    bh_metrics,
    build_wf_configs,
    detect_data_range,
    load_daily_closes,
    oos_is_sharpe_ratio,
    robustness_summary,
    run_metrics,
    stitch_oos,
)
from sngw_trader.research.windows import compute_windows, holdout_start

PROXY_LABEL = "Binance OI proxy (BTCUSDT metrics) — NOT OKX data"

ASSUMPTIONS = [
    "OI는 Binance BTCUSDT metrics 합계(sum_open_interest)이며 OKX OI가 아니다. venue 불일치를 전제로 한 proxy 결과다",
    "Binance sum_open_interest는 BTC 계약 수, OKX는 컨트랙트 밸류 단위다. 전략은 상대 변화율만 쓰므로 단위 차이는 신호에 영향 없다",
    "Binance 5m era 종료일 이전 구간만 사용한다 (25m era 제외)",
    "이 결과만으로 라이브 투입 근거로 삼지 않는다. 실측 OKX OI 축적 후 재검증한다",
]

SPECS: dict[str, GridSpec] = {
    "oi_a": GridSpec(
        strategy_path="sngw_trader.strategies.oi_crowding_mean_reversion:OiCrowdingMeanReversion",
        config_path="sngw_trader.strategies.oi_crowding_mean_reversion:OiCrowdingMeanReversionConfig",
        fixed={"trade_size": "0.01", "oi_client_id": "BACKTEST"},
        grid={},
    ),
    "oi_b": GridSpec(
        strategy_path="sngw_trader.strategies.oi_liquidation_mean_reversion:OiLiquidationMeanReversion",
        config_path="sngw_trader.strategies.oi_liquidation_mean_reversion:OiLiquidationMeanReversionConfig",
        fixed={"trade_size": "0.01", "oi_client_id": "BACKTEST"},
        grid={},
    ),
}


def stress_settings(settings):
    """2x taker fee + 5x slippage stress variant (kd_macd_compare 선례)."""
    return replace(
        settings,
        bt_taker_fee=settings.bt_taker_fee * 2,
        bt_prob_slippage=min(0.99, settings.bt_prob_slippage * 5),
    )


def _params_for(spec: GridSpec, key: tuple) -> dict[str, object]:
    return dict(zip(sorted(spec.grid), key))


def _run_window(settings, spec, params, start, end, warmup_days):
    return run_oi_window(
        str(settings.catalog_path), settings.instrument_id_str, settings=settings,
        spec=spec, params=params, start=start, end=end, warmup_days=warmup_days,
    )


def _data_range(settings):
    start, end = detect_data_range(
        str(settings.catalog_path), f"{settings.instrument_id_str}-1-MINUTE-LAST-EXTERNAL"
    )
    era_end = os.environ.get("OI_ERA_END")
    if era_end:
        end = min(end, datetime.fromisoformat(era_end).replace(tzinfo=timezone.utc))
    return start, end


def _wf_windows(settings, spec, windows, wf_cfg, mc_cfg, run_window):
    out = []
    oos_results: list[RunResult | None] = []
    last_params: tuple | None = None
    n_cells = len(param_keys(spec.grid))
    for window in windows:
        grid_results = {}
        for i, key in enumerate(param_keys(spec.grid), 1):
            params = _params_for(spec, key)
            grid_results[key] = run_window(
                settings, spec, params, window.is_start, window.is_end, wf_cfg.warmup_days
            )
            print(f"[oi-compare] {spec.strategy_path.rsplit(':', 1)[-1]} "
                  f"w{window.index} IS {i}/{n_cells} {params} trades={grid_results[key].n_trades}")
        sharpes = {k: sharpe_from_trades(r.trade_returns) for k, r in grid_results.items()}
        gates = {k: r.n_trades for k, r in grid_results.items()}
        best = select_best(spec.grid, sharpes, gates, wf_cfg.min_trades)
        oos_result = None
        if best is not None:
            last_params = best
            oos_result = run_window(
                settings, spec, _params_for(spec, best),
                window.oos_start, window.oos_end, wf_cfg.warmup_days,
            )
        oos_results.append(oos_result)
        is_metrics = run_metrics(grid_results[best], mc_cfg.initial_capital) if best is not None else None
        oos_metrics = run_metrics(oos_result, mc_cfg.initial_capital) if oos_result else None
        ratio = (
            oos_is_sharpe_ratio(sharpes[best], oos_result)
            if best is not None and oos_result is not None else None
        )
        out.append({
            "index": window.index,
            "is": {"start": window.is_start.isoformat(), "end": window.is_end.isoformat()},
            "oos": {
                "start": window.oos_start.isoformat(),
                "end": window.oos_end.isoformat(),
                "n_trades": oos_result.n_trades if oos_result else None,
                "total_pnl": oos_result.total_pnl if oos_result else None,
            },
            "selected": _params_for(spec, best) if best is not None else None,
            "is_metrics": is_metrics,
            "oos_metrics": oos_metrics,
            "oos_is_sharpe_ratio": ratio,
        })
    return out, oos_results, last_params


def run_compare(settings, wf_cfg, mc_cfg, out_dir, days, specs=SPECS, run_window=_run_window):
    data_start, data_end = _data_range(settings)
    windows = compute_windows(
        data_start, data_end, wf_cfg.is_months, wf_cfg.oos_months, wf_cfg.holdout_months,
    )
    h_start = holdout_start(data_end, wf_cfg.holdout_months)
    print(f"[oi-compare] data {data_start:%Y-%m-%d}..{data_end:%Y-%m-%d} "
          f"holdout from {h_start:%Y-%m-%d}, {len(windows)} windows")

    strategies: dict[str, dict] = {}
    for name, spec in specs.items():
        wf_windows, oos_results, last_params = _wf_windows(
            settings, spec, windows, wf_cfg, mc_cfg, run_window,
        )
        stitched = stitch_oos(oos_results)
        # stress: 각 OOS window를 선택된 파라미터로 스트레스 설정 재실행
        stress_results = []
        for entry, window in zip(wf_windows, windows):
            if entry["selected"] is None:
                continue
            stress_results.append(run_window(
                stress_settings(settings), spec, entry["selected"],
                window.oos_start, window.oos_end, wf_cfg.warmup_days,
            ))
        holdout = None
        if last_params is not None:
            holdout = run_window(
                settings, spec, _params_for(spec, last_params),
                h_start, data_end, wf_cfg.warmup_days,
            )
        strategies[name] = {
            "windows": wf_windows,
            "stitched_oos_trades": len(stitched),
            "stitched_oos_pnl": sum(stitched),
            "stress_oos_pnl": sum(r.total_pnl for r in stress_results),
            "mc_oos": bootstrap_trades(stitched, mc_cfg) if len(stitched) >= 2 else None,
            "robustness": robustness_summary(wf_windows),
            "holdout": None if holdout is None else {
                "n_trades": holdout.n_trades,
                "total_pnl": holdout.total_pnl,
                "metrics": run_metrics(holdout, mc_cfg.initial_capital),
            },
        }
        print(f"[oi-compare] {name}: oos_pnl={sum(stitched):.2f} "
              f"stress={sum(r.total_pnl for r in stress_results):.2f} "
              f"holdout={holdout.total_pnl if holdout else None}")

    summary = {
        "label": "IS/WF compare",
        "proxy": PROXY_LABEL,
        "instrument_id": settings.instrument_id_str,
        "data_start": data_start.isoformat(),
        "data_end": data_end.isoformat(),
        "holdout_start": h_start.isoformat(),
        "strategies": strategies,
        "oos_benchmark": bh_metrics(days, data_start, h_start),
        "assumptions": ASSUMPTIONS,
    }
    report.write_reports(
        out_dir,
        {"proxy": PROXY_LABEL, "data_start": data_start.isoformat(),
         "data_end": data_end.isoformat(), "strategies": strategies},
        {"oos": {n: s["mc_oos"] for n, s in strategies.items()}},
        summary,
    )
    return summary


def run_oneshot(settings, wf_cfg, mc_cfg, out_dir, days, specs=SPECS, run_window=_run_window):
    """Single full-range sanity run with default params. Grid 결정 전 트레이드 수 확인용."""
    data_start, data_end = _data_range(settings)
    cells = {}
    for name, spec in specs.items():
        res = run_window(settings, spec, {}, data_start, data_end, wf_cfg.warmup_days)
        cells[name] = {
            "n_trades": res.n_trades,
            "total_pnl": res.total_pnl,
            "metrics": run_metrics(res, mc_cfg.initial_capital),
            "benchmark": bh_metrics(days, data_start, data_end),
        }
        print(f"[oneshot] {name} trades={res.n_trades} pnl={res.total_pnl:.2f}")
    summary = {
        "label": "oneshot",
        "proxy": PROXY_LABEL,
        "instrument_id": settings.instrument_id_str,
        "data_start": data_start.isoformat(),
        "data_end": data_end.isoformat(),
        "cells": cells,
        "assumptions": ASSUMPTIONS,
    }
    report.write_reports(out_dir, {"label": "oneshot", "proxy": PROXY_LABEL}, {}, summary)
    return summary


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def main() -> None:
    settings = load_settings()
    if settings.instrument_id_str != OI_INSTRUMENT_ID:
        raise SystemExit(f"OI compare supports only {OI_INSTRUMENT_ID}, got {settings.instrument_id_str}")
    wf_cfg, mc_cfg = build_wf_configs()
    out_dir = report.run_dir("oi_mean_reversion_compare")
    days = load_daily_closes()
    if _env_flag("OI_ONESHOT"):
        run_oneshot(settings, wf_cfg, mc_cfg, out_dir, days)
    else:
        run_compare(settings, wf_cfg, mc_cfg, out_dir, days)
    report.print_summary({"proxy": PROXY_LABEL, "out_dir": str(out_dir)})


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest tests/test_research/ -v`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add src/sngw_trader/research/oi_mean_reversion_compare.py tests/test_research/test_oi_mean_reversion_compare.py
git commit -m "feat(research): OI A/B IS/WF compare on Binance OI proxy"
```

---

### Task 6: 데이터 다운로드 + oneshot sanity → 그리드 확정 (운영)

**Files:**
- Create: 없음 (코드 변경 없음. `.env`는 커밋 금지)

**Interfaces:**
- Consumes: Task 3 CLI, 기존 `catalog_writer`(bars+instrument), Task 5 oneshot
- Produces: 카탈로그 데이터(instrument + OKX 1m bars + Binance OI proxy), 5m era 종료일, 그리드 축 확정

- [ ] **Step 1: OKX 상장 시점 확인 (bars 소급 시작점)**

`.env`에 `INSTRUMENT_ID=BTC-USDT-SWAP.OKX`, `CATALOG_SOURCE=okx`, `OI_ENABLED=false` 설정.
`CATALOG_START=2020-09-01T00:00:00Z`, `CATALOG_END=2020-10-01T00:00:00Z`로
`python -m sngw_trader.data.catalog_writer` 실행. `No candles downloaded`가
나오면 시작점을 1개월씩 앞당겨 재시도 (OKX BTC-USDT-SWAP 상장일 확인 목적).
실제 데이터가 나오는 달을 기록.

- [ ] **Step 2: 전체 bar + instrument 다운로드**

`CATALOG_START`를 Step 1에서 확인한 달, `CATALOG_END=2026-09-16T00:00:00Z`로
설정하고 `python -m sngw_trader.data.catalog_writer` 실행.

주의: 1m bar 수년치 → 수천 회 요청, 15~40분 소요. 중단 시 재실행 (덮어쓰기).
완료 후 로그의 bar 수와 기간을 기록.

- [ ] **Step 3: Binance OI proxy 다운로드**

```bash
python -m sngw_trader.data.binance_oi --start <Step1의 시작일> --end 2026-09-16
```

출력의 `era_end`(최초 비-5m 날짜)를 기록. 이후 WF 범위 상한으로 사용
(`OI_ERA_END=<era_end>` env). 예상 소요: 일 단위 zip 다운로드 ~15분.

- [ ] **Step 4: oneshot sanity 실행**

```
OI_ONESHOT=1 python -m sngw_trader.research.oi_mean_reversion_compare
```

결과에서 확인: 전략별 n_trades, total_pnl, benchmark 대비 성과.
**판정 기준:**
- 두 전략 모두 0 트레이드 → 세션 파라미터(`session_start/end`)와
  `oi_increase/rollover/decrease_threshold`를 확인 후 SPECS fixed에 완화값 적용하고 재실행
- 트레이드 수 과다(수천) → 세션/스레숄 재확인
- 적정(수백~수천) → 다음 단계

- [ ] **Step 5: 그리드 축 확정 (스펙의 "데이터 확인 후 결정" 항목)**

oneshot 결과를 보고 `SPECS[*].grid`를 확정한다. 시작점(과도하면 축소):

```python
grid={
    "atr_mult": [2.0, 2.5, 3.0],
    "reentry_bars": [3, 5, 8],
}
```

A 전략은 `oi_increase_threshold`/`oi_rollover_threshold`, B 전략은
`oi_decrease_threshold`를 필요시 축에 추가한다. 축 추가 시 조합 수가
windows 수 × 전략 수 × 조합 수 실행 시간으로 직결됨을 감안해 총 9~12 셀 유지.
수정 후 커밋:

```bash
git add src/sngw_trader/research/oi_mean_reversion_compare.py
git commit -m "feat(research): finalize OI compare grids after oneshot sanity"
```

---

### Task 7: WF 전체 실행 + 결과 리뷰 (운영)

**Files:**
- Create: 없음 (결과는 `logs/oi_mean_reversion_compare/<ts>/`에 JSON)

**Interfaces:**
- Consumes: Task 5 compare WF 모드, Task 6 데이터 + 그리드
- Produces: A/B 비교 리포트. **스펙 평가 기준으로 판정하는 것이 이 태스크의 산출물**

- [ ] **Step 1: WF 실행**

```
OI_ERA_END=<Task 6의 era_end> python -m sngw_trader.research.oi_mean_reversion_compare
```

실행 시간: windows 수 × 전략 수 × (그리드 셀 + 1) 회 BacktestNode 실행.
10~16 windows × 2 × 10 셀 기준 수 시간. 중단해도 재실행 가능(멱등).

- [ ] **Step 2: 리포트 판정 (스펙 §3 평가 기준)**

`summary.json`에서 전략별로 확인:
1. `robustness.oos_is_sharpe_ratios` — OOS/IS 샤프 비율의 평균이 유의미(대략 0.5 이상)하고, `n_excluded_ratio_windows`가 과반 이하
2. `mc_oos` — stitched OOS 트레이드 bootstrap에서 중앙값/하위 5% pnl이 0 이상
3. `stress_oos_pnl` — 2x fee/5x slippage에서도 `stitched_oos_pnl`의 부호 유지
4. `oos_benchmark` 대비 초과 성과
5. holdout이 stitched OOS와 부호 일치

- [ ] **Step 3: 결과 정리**

판정 결과(A/B 각각 채택/기각/재설계)를 콘솔에 요약 출력. 리포트는 이미
`Binance OI proxy` 라벨을 포함하므로 추가 문서 작업 없음. 후속 결정(실측 OKX
OI 축적 후 재검증 일정)은 수집기 스펙(Task 1) 문서와 연결.

---

## Self-Review 결과

1. **Spec 커버리지**: 스펙 §4.1→Task 2/3, §4.2→Task 4, §4.3→Task 5, §4.4→Task 1, §5 실행순서→Task 1→6→7, §6 리스크(provenance/era/window 축소/세션)→Task 3 provenance, Task 6 era cap(`OI_ERA_END`)+oneshot, ASSUMPTIONS 라벨. 누락 없음
2. **Placeholder 스캔**: 없음. Task 6 Step 5의 그리드 값은 스펙이 명시한 "데이터 확인 후 결정" 결정 포인트이며 시작값을 구체적으로 제시
3. **타입 일치**: `scan_binance_oi(start, end, fetch=...)`/`parse_metrics_csv` 시그니처 Task 2/3 일치. `run_oi_window` 시그니처 Task 4 정의 = Task 5 `_run_window` 호출 일치. `run_compare(..., run_window=...)` 테스트 주입 시그니처 `(settings, spec, params, start, end, warmup_days)` 일치
