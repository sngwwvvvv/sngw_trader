# 거시 금융 프록시 2축 레짐 필터 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** FRED OAS + VIX/VXV와 Yahoo 구리/금으로 NYSE 세션당 `RegimeSnapshot`을 만들고, Stage 0에서 분면이 섹터 ETF 선도수익률을 가르는지 검증한 뒤, 합격할 때만 Stage 1 더미 모멘텀 위에 익스포저 게이트를 올린다.

**Architecture:** writer/data만 FRED·Yahoo HTTP를 연다. `indicators/regime.py`는 robust z / S / G / 라벨 순수 함수다. Stage 0A/0은 `research/` 프로브이고 BacktestNode가 필요 없다. Stage 1 전략은 ETF `Bar`와 이미 정렬된 `RegimeSnapshot`만 보고 시그널을 바꾸지 않으며, 러너는 9개 ETF + Snapshot을 `BacktestNode`에 조립만 한다.

**Tech Stack:** Python 3.12–3.14, NautilusTrader 1.231.0 (`@customdataclass`, `ParquetDataCatalog.write_data` / `custom_data`, `BacktestNode`, `Equity`, `Bar`), optional `yfinance`. FRED는 `urllib` (신규 패키지 없음). numpy/pandas/pyarrow는 nautilus 의존성.

**Spec:** `docs/superpowers/specs/2026-09-14-macro-proxy-regime-filter-design.md`

## Global Constraints

- 라이브 체결은 OKX only. US 섹터 ETF를 OKX/`ccxt`/IB로 주문하는 코드를 이 스펙으로 만들지 않는다.
- writer/data만 FRED/Yahoo HTTP. 전략·indicators·runners·research는 `yfinance` / `api.stlouisfed.org` / `FRED_API_KEY`를 읽지 않는다.
- 전략은 `stress`, `growth`, `regime_code`, `quality_code`만 사용하고 Snapshot을 다시 계산하지 않는다.
- 러너는 매매 조건을 갖지 않는다. 한 프로세스에 BacktestNode와 TradingNode를 같이 올리지 않는다.
- 숏·스탑·볼타겟·시그모이드·히스테리시스·R3 MR·인플레 틸트(`T5YIE`/`DFII5`)·`T10Y2Y`·SGOV·페어·HMM 없음.
- 현금 이자 0. 비용은 편도 5bp, 턴오버 난 레그만.
- Stage 2는 이 계획 밖. 2022 XLK/XLE는 Stage 0 실패가 아니다.
- v1에서 버린 것(SOFR−IORB, HYG/LQD 이중 트랙, Growth `Δ`, 4분면 그림 번호 뒤집기)을 되살리지 않는다.
- 시크릿 하드코딩 금지. 네트워크 필수 테스트는 기본 스위트에 넣지 않는다.
- Nautilus 심볼은 설치된 1.231.0에서 확인된 것만. import 실패 시 임의 클래스 금지.
- 실행은 `uv run pytest ...` (Windows PowerShell). `&&` 금지. 체인 시 `;`.
- 커밋 prefix: `feat(data):`, `feat(indicator):`, `feat(research):`, `feat(strategy):`, `feat(runner):`.

확인된 Nautilus 1.231.0 API (Task 5에서 라운드트립으로 잠근다):

```
from nautilus_trader.model.custom import customdataclass
from nautilus_trader.serialization.arrow.serializer import register_arrow  # customdataclass가 클래스 정의 시 호출
ParquetDataCatalog.write_data(data, data_cls=RegimeSnapshot)
ParquetDataCatalog.custom_data(cls=RegimeSnapshot, as_nautilus=False) -> list[RegimeSnapshot]
Strategy.subscribe_data(DataType(RegimeSnapshot))
Strategy.on_data(data)
BacktestDataConfig(catalog_path=..., data_cls=RegimeSnapshot)  # class 또는 "pkg.mod:Class"
```

`register_custom_data_class` / `write_custom_data`는 1.231.0에 없다. develop 문서를 복사하지 말 것.

## File structure

| 경로 | 역할 |
|---|---|
| `src/sngw_trader/data/regime_universe.py` | 9 SPDR / Cyc / Def / 벤치 / 프록시 티커, InstrumentId 헬퍼 |
| `src/sngw_trader/data/fred_macro.py` | FRED JSON fetch + 파싱. `FRED_API_KEY`는 이 모듈만 |
| `src/sngw_trader/data/copper_gold.py` | HG=F/GC=F 같은 날짜 비율, 롤 스파이크 스캔 |
| `src/sngw_trader/data/regime_snapshot.py` | `@customdataclass RegimeSnapshot` |
| `src/sngw_trader/data/regime_align.py` | NYSE 세션 정렬, as-of, age, quality |
| `src/sngw_trader/data/regime_writer.py` | 다운로드 → 스코어 → catalog + manifest |
| `src/sngw_trader/indicators/regime.py` | robust z, S, G, 라벨 (순수 numpy) |
| `src/sngw_trader/indicators/sector_momentum.py` | skip-1 모멘텀, top-K, 익스포저 표 (순수) |
| `src/sngw_trader/research/regime_config.py` | 표본 게이트·날짜 분할·bootstrap (실행 전 고정). z/창 숫자는 indicators가 소유 |
| `src/sngw_trader/research/regime_stage0a.py` | 데이터 계약·품질 검증. 실패 시 Stage 0/1 금지 |
| `src/sngw_trader/research/regime_stage0.py` | fwd 프로브 + 합격 판정 |
| `src/sngw_trader/research/regime_stage1.py` | Base/overlay/placebo 비교 집계 |
| `src/sngw_trader/strategies/sector_momentum_gate.py` | Strategy + Config만 |
| `src/sngw_trader/runners/backtest_etf_regime.py` | 9 ETF Bar + Snapshot BacktestNode 조립만 |
| `tests/test_data/test_regime_*.py` | writer/align/snapshot 단위 테스트 |
| `tests/test_indicators/test_regime.py` | z/S/G/라벨 |
| `tests/test_indicators/test_sector_momentum.py` | skip-1 / overlay |
| `tests/test_research/test_regime_stage0.py` | fwd·bootstrap·합격 |
| `tests/test_research/test_regime_stage0a.py` | Stage 0A 게이트 |
| `tests/test_research/test_regime_stage1.py` | 비교 메트릭·placebo |
| `tests/test_strategies/test_sector_momentum_gate.py` | 결정 로직 |
| `tests/test_runners/test_backtest_etf_regime.py` | 조립 + 합성 체결 |

`HG=F`/`GC=F`는 catalog에 Equity/주문 대상으로 넣지 않는다. 연구 입력 → Snapshot 필드만.

## Frozen experiment constants

실행 전 고정. 결과를 본 뒤 바꾸지 않는다.

```python
# data/regime_universe.py
FEATURE_VERSION = "macro-proxy-2axis-v1"
QUALITY_INVALID, QUALITY_LAGGED, QUALITY_FRESH = 0, 1, 2
STALE_SESSIONS = 3  # age > 3 → R0 invalid

# indicators/regime.py
MEDIAN_WINDOW = 60
IQR_LONG = 252
Z0 = 0.5
IQR_TO_SIGMA = 0.7413
R0, R1, R2, R3, R4 = 0, 1, 2, 3, 4

# research/regime_config.py
ROBUSTNESS_WINDOW = 120  # 1회만, 채택 그리드 아님
MIN_REGIME_SESSIONS = 40
MIN_REGIME_SHARE = 0.05
FWD_HORIZONS = (5, 20)
MOMENTUM_LOOKBACK = 20  # skip-1: D-21 → D-1
TOP_K = 3
ONE_WAY_FEE = 0.0005
STARTING_BALANCE = "1_000_000 USD"
BOOTSTRAP_ITERS = 1000
BOOTSTRAP_SEED = 42
DEV_END = "2015-12-31"
VALIDATION_END = "2019-12-31"
TEST_START = "2020-01-01"
MIN_AVG_INVESTED = 0.20
MIN_STAGE1_TRADES = 30
```

날짜 분할 용도:

- Stage 0 합격: Validation (`DEV_END` 다음날 ~ `VALIDATION_END`). Dev는 축이 이미 고정된 구간. Test는 Stage 0 합격에 쓰지 않는다.
- Stage 1 채택: Test (`TEST_START` ~ 데이터 끝). Validation은 보고만.

VXVCLS는 대략 2007말 시작이라 라벨은 워밍업 후 ~2008–2009부터다. 2008이 워밍업에 일부 들어가면 Stage 0A가 보고만 하고 축을 바꾸지 않는다.

---

### Task 1: Universe constants + as-of age helpers

**Files:**
- Create: `src/sngw_trader/data/regime_universe.py`
- Create: `src/sngw_trader/research/regime_config.py`
- Test: `tests/test_data/test_regime_universe.py`

**Interfaces:**
- Consumes: `yahoo_etf.instrument_id_for`, `yahoo_etf.etf_bar_type` (이미 있음. XLK 기본 ARCA).
- Produces:
  - `FEATURE_VERSION = "macro-proxy-2axis-v1"`
  - `SECTOR_ETFS: tuple[str, ...] = ("XLY","XLI","XLB","XLF","XLK","XLE","XLP","XLU","XLV")`
  - `CYC_ETFS = ("XLY","XLI","XLB","XLF")`
  - `DEF_ETFS = ("XLP","XLU","XLV")`
  - `BENCHMARK_ETFS = ("SPY",)`
  - `REFERENCE_ETFS = ("QQQ","IWM")`
  - `ALL_EVAL_ETFS = SECTOR_ETFS + BENCHMARK_ETFS + REFERENCE_ETFS`
  - `FRED_OAS = "BAMLH0A0HYM2"`, `FRED_VIX = "VIXCLS"`, `FRED_VXV = "VXVCLS"`
  - `YAHOO_COPPER = "HG=F"`, `YAHOO_GOLD = "GC=F"`
  - `sector_instrument_ids() -> list[str]`
  - `QUALITY_INVALID, QUALITY_LAGGED, QUALITY_FRESH = 0, 1, 2` (모듈 상수. `data/`는 `research/`를 import하지 않는다)
  - `session_index(sessions: list[date]) -> dict[date, int]`
  - `session_age(obs: date, session: date, sessions: list[date]) -> int` — `obs`가 캘린더에 없으면 `obs` 이하 마지막 세션을 쓴다. 관측이 세션보다 뒤면 `ValueError`. 관측이 캘린더 시작 이전이면 age는 세션 인덱스 (크게 나와 invalid 처리).
  - `previous_calendar_date(d: date) -> date` — OAS 상한 `D-1` (캘린더 날짜, 세션 아님).
  - `is_last_session_of_week(session: date, sessions: list[date]) -> bool`
  - `session_date_from_ts(ts_ns: int) -> str` — UTC `YYYY-MM-DD`. writer가 바와 Snapshot에 같은 `ts_event`를 넣으므로 키가 같다.

- [ ] **Step 1: Write the failing tests**

```python
from datetime import date, datetime, timezone

from sngw_trader.data.regime_universe import (
    SECTOR_ETFS,
    session_age,
    previous_calendar_date,
    is_last_session_of_week,
    sector_instrument_ids,
)


def test_nine_sector_etfs_and_arca_ids() -> None:
    assert len(SECTOR_ETFS) == 9
    assert "XLC" not in SECTOR_ETFS
    assert "XLRE" not in SECTOR_ETFS
    ids = sector_instrument_ids()
    assert ids[0] == "XLY.ARCA"
    assert "XLK.ARCA" in ids


def test_session_age_same_day_is_zero() -> None:
    sessions = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    assert session_age(date(2024, 1, 3), date(2024, 1, 3), sessions) == 0


def test_session_age_skips_weekend() -> None:
    sessions = [date(2024, 1, 5), date(2024, 1, 8)]  # Fri, Mon
    assert session_age(date(2024, 1, 5), date(2024, 1, 8), sessions) == 1


def test_session_age_fred_weekend_obs_uses_last_session() -> None:
    sessions = [date(2024, 1, 5), date(2024, 1, 8)]
    assert session_age(date(2024, 1, 7), date(2024, 1, 8), sessions) == 1  # Sun → Fri


def test_oas_cap_is_calendar_d_minus_1() -> None:
    assert previous_calendar_date(date(2024, 1, 8)) == date(2024, 1, 7)


def test_last_session_of_week() -> None:
    sessions = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]
    assert is_last_session_of_week(date(2024, 1, 5), sessions) is True
    assert is_last_session_of_week(date(2024, 1, 4), sessions) is False


def test_session_date_from_ts_utc() -> None:
    from sngw_trader.data.regime_universe import session_date_from_ts

    ts = int(datetime(2020, 1, 2, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    assert session_date_from_ts(ts) == "2020-01-02"
```

Task 1 테스트 파일 상단에 `from datetime import date, datetime, timezone` 를 쓴다.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_data/test_regime_universe.py -v`

Expected: FAIL — `ModuleNotFoundError: regime_universe`

- [ ] **Step 3: Write minimal implementation**

`regime_universe.py`: 유니버스 상수, `FEATURE_VERSION`, quality 코드, `STALE_SESSIONS`, `date` 리스트 이진 탐색으로 age. `is_last_session_of_week`는 다음 세션의 ISO week가 다르거나 다음 세션이 없으면 True. `session_date_from_ts`는 UTC date.

`regime_config.py`: Frozen dataclass `RegimeExperimentConfig` — `MIN_REGIME_SESSIONS`, `MIN_REGIME_SHARE`, `FWD_HORIZONS`, 날짜 분할, bootstrap, Stage 1 최소 표본만. z/창/라벨 코드는 넣지 않는다. Default instance `DEFAULT_REGIME_CONFIG`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_data/test_regime_universe.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/data/regime_universe.py src/sngw_trader/research/regime_config.py tests/test_data/test_regime_universe.py
git commit -m "feat(data): add sector ETF universe and session-age helpers"
```

---

### Task 2: Robust z, Stress, Growth, 4분면 라벨

**Files:**
- Create: `src/sngw_trader/indicators/regime.py`
- Test: `tests/test_indicators/test_regime.py`

**Interfaces:**
- Consumes: `RegimeExperimentConfig` 숫자 기본값 (모듈 상수로 복제해도 됨. indicators는 research를 import하지 않는다).
- Produces:
  - `R0, R1, R2, R3, R4 = 0, 1, 2, 3, 4`
  - `Z0 = 0.5`, `MEDIAN_WINDOW = 60`, `IQR_LONG = 252`, `IQR_TO_SIGMA = 0.7413` (indicators는 research를 import하지 않는다)
  - `robust_z(x: np.ndarray, median_window: int = 60, iqr_long: int = 252, iqr_to_sigma: float = 0.7413) -> np.ndarray`
    - `median_t = median(x[t-W+1:t+1])`
    - `iqr_w = percentile75-25` of the same window; `iqr_L` of 252-window
    - `denom = max(iqr_w, iqr_L / 3) * iqr_to_sigma`
    - `z = (x - median) / denom`; denom==0 또는 워밍업 전은 `nan`
  - `stress_score(oas_z: np.ndarray, vix_vxv_z: np.ndarray) -> np.ndarray` — `0.5 * oas_z + 0.5 * vix_vxv_z` (성분 z를 먼저, 원시계열 합치지 않음)
  - `growth_score(copper_gold_z: np.ndarray) -> np.ndarray` — identity
  - `regime_code(stress: float, growth: float, z0: float = 0.5) -> int | None` — 입력이 nan이면 `None`. `|S|<z0 and |G|<z0` → 0. 아니면 `stress > 0` → S+, `growth > 0` → G+. R1=S−G+, R2=S+G+, R3=S−G−, R4=S+G−. 0은 +가 아님 (`<= 0` → −).
  - `label_series(stress: np.ndarray, growth: np.ndarray, z0: float = 0.5) -> np.ndarray` — dtype int, nan 위치는 `-1` (라벨 없음)

- [ ] **Step 1: Write the failing tests**

```python
import numpy as np

from sngw_trader.indicators.regime import (
    growth_score,
    label_series,
    regime_code,
    robust_z,
    stress_score,
)


def test_warmup_is_nan_before_252() -> None:
    x = np.arange(300, dtype=float)
    z = robust_z(x)
    assert np.all(np.isnan(z[:251]))
    assert not np.isnan(z[251])


def test_constant_series_z_is_nan() -> None:
    x = np.ones(300)
    z = robust_z(x)
    assert np.all(np.isnan(z[251:]))


def test_known_median_iqr_window() -> None:
    x = np.zeros(300)
    x[-1] = 10.0
    z = robust_z(x, median_window=60, iqr_long=252)
    sl60 = x[-60:]
    sl252 = x[-252:]
    med = float(np.median(sl60))
    iqr60 = float(np.percentile(sl60, 75) - np.percentile(sl60, 25))
    iqr252 = float(np.percentile(sl252, 75) - np.percentile(sl252, 25))
    denom = max(iqr60, iqr252 / 3) * 0.7413
    assert np.isclose(z[-1], (10.0 - med) / denom)


def test_stress_is_equal_weight_of_component_z() -> None:
    oas = np.array([2.0, 0.0])
    vx = np.array([0.0, 4.0])
    np.testing.assert_allclose(stress_score(oas, vx), [1.0, 2.0])


def test_growth_is_copper_gold_z() -> None:
    g = np.array([-1.5, 0.2])
    np.testing.assert_allclose(growth_score(g), g)


def test_r0_box_and_weak_axis_still_quadrant() -> None:
    assert regime_code(0.2, 0.2) == 0
    assert regime_code(-0.2, 1.5) == 1
    assert regime_code(0.2, 1.5) == 2
    assert regime_code(-1.0, 1.0) == 1
    assert regime_code(1.0, 1.0) == 2
    assert regime_code(-1.0, -1.0) == 3
    assert regime_code(1.0, -1.0) == 4
    assert regime_code(0.0, 1.5) == 1  # S==0 → S−
    assert regime_code(float("nan"), 1.0) is None


def test_label_series_uses_minus_one_for_nan() -> None:
    s = np.array([np.nan, -1.0])
    g = np.array([1.0, 1.0])
    out = label_series(s, g)
    assert out[0] == -1
    assert out[1] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_indicators/test_regime.py -v`

Expected: FAIL — module not found

- [ ] **Step 3: Write minimal implementation**

순수 numpy. pandas rolling 쓰지 않아도 된다. 창 루프 n≈수천이면 충분. `indicators/`는 nautilus/yfinance/FRED를 import하지 않는다.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_indicators/test_regime.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/indicators/regime.py tests/test_indicators/test_regime.py
git commit -m "feat(indicator): add robust-z stress/growth regime labels"
```

---

### Task 3: FRED EOD client (HTTP mocked)

**Files:**
- Create: `src/sngw_trader/data/fred_macro.py`
- Modify: `.env.example` — `FRED_API_KEY=` 추가
- Modify: `NAUTILUS_VIBE_RULES.md` §7 한 줄: writer만 Yahoo/OKX/**FRED** HTTP
- Test: `tests/test_data/test_fred_macro.py`

**Interfaces:**
- Consumes: `os.environ["FRED_API_KEY"]` (Settings에 넣지 않는다. live runner가 Settings를 들고 다니므로).
- Produces:
  - `FRED_OBS_URL = "https://api.stlouisfed.org/fred/series/observations"`
  - `parse_fred_observations(payload: dict) -> list[tuple[date, float]]` — `value == "."` 또는 빈 값은 건너뜀
  - `fred_api_key() -> str` — 없거나 공백이면 `SystemExit("FRED_API_KEY is required")`
  - `fetch_fred_series(series_id: str, start: date | None, end: date | None, opener=None) -> list[tuple[date, float]]` — `opener` 기본 `urllib.request.urlopen`. 테스트는 opener를 주입한다.

- [ ] **Step 1: Write the failing tests**

```python
from datetime import date

import pytest

from sngw_trader.data.fred_macro import fetch_fred_series, parse_fred_observations


def test_parse_skips_dot_missing() -> None:
    payload = {
        "observations": [
            {"date": "2020-01-02", "value": "4.5"},
            {"date": "2020-01-03", "value": "."},
            {"date": "2020-01-06", "value": "4.7"},
        ]
    }
    rows = parse_fred_observations(payload)
    assert rows == [(date(2020, 1, 2), 4.5), (date(2020, 1, 6), 4.7)]


def test_fetch_uses_injected_opener(monkeypatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", "test-key")
    calls = {}

    class _Resp:
        def read(self) -> bytes:
            return b'{"observations":[{"date":"2020-01-02","value":"1.25"}]}'

        def __enter__(self):
            return self

        def __exit__(self, *a) -> None:
            return None

    def opener(req, timeout=30):
        calls["url"] = req.full_url if hasattr(req, "full_url") else str(req)
        return _Resp()

    rows = fetch_fred_series("BAMLH0A0HYM2", date(2020, 1, 1), date(2020, 1, 31), opener=opener)
    assert rows == [(date(2020, 1, 2), 1.25)]
    assert "api.stlouisfed.org" in calls["url"]
    assert "api_key=test-key" in calls["url"]
    assert "series_id=BAMLH0A0HYM2" in calls["url"]


def test_missing_api_key_exits(monkeypatch) -> None:
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        fetch_fred_series("VIXCLS", None, None, opener=lambda *a, **k: None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_data/test_fred_macro.py -v`

Expected: FAIL — module not found

- [ ] **Step 3: Write minimal implementation**

`urllib.request.Request` + `urlencode`. `file_type=json`. 키를 로그/예외 메시지에 찍지 않는다. `Settings`에 필드를 추가하지 않는다 (모든 `Settings(...)` 픽스처를 건드리지 않기 위함).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_data/test_fred_macro.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/data/fred_macro.py tests/test_data/test_fred_macro.py .env.example NAUTILUS_VIBE_RULES.md
git commit -m "feat(data): add FRED observation client from FRED_API_KEY env"
```

---

### Task 4: Copper/gold same-date ratio + roll spike scan

**Files:**
- Create: `src/sngw_trader/data/copper_gold.py`
- Test: `tests/test_data/test_copper_gold.py`

**Interfaces:**
- Consumes: 날짜 인덱스 `pandas.Series` (close). yfinance 호출은 writer가 나중에 `yahoo_etf.fetch_daily_bars("HG=F", ...)`로 한다. 이 모듈은 순수.
- Produces:
  - `paired_ratio(copper: pd.Series, gold: pd.Series) -> pd.Series` — **같은 날짜 교집합만**. 이름 `HG=F`/`GC=F`를 유니버스에 넣지 않음.
  - `spike_dates(series: pd.Series, abs_ret: float = 0.15) -> list[date]` — `|pct_change| > abs_ret`
  - `futures_quality_report(copper: pd.Series, gold: pd.Series, ratio: pd.Series) -> dict` — n, start, end, copper_spikes, gold_spikes, ratio_spikes (날짜 ISO 리스트)

- [ ] **Step 1: Write the failing tests**

```python
from datetime import date

import pandas as pd

from sngw_trader.data.copper_gold import futures_quality_report, paired_ratio, spike_dates


def test_ratio_keeps_intersection_only() -> None:
    copper = pd.Series([3.0, 3.3, 3.6], index=pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06"]))
    gold = pd.Series([1500.0, 1600.0], index=pd.to_datetime(["2020-01-02", "2020-01-06"]))
    ratio = paired_ratio(copper, gold)
    assert list(ratio.index.date) == [date(2020, 1, 2), date(2020, 1, 6)]
    assert ratio.iloc[0] == 3.0 / 1500.0


def test_spike_dates_flag_large_returns() -> None:
    s = pd.Series([100.0, 101.0, 130.0], index=pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06"]))
    assert spike_dates(s, abs_ret=0.15) == [date(2020, 1, 6)]


def test_quality_report_lists_spikes() -> None:
    idx = pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06"])
    copper = pd.Series([3.0, 3.0, 4.0], index=idx)
    gold = pd.Series([1500.0, 1500.0, 1500.0], index=idx)
    ratio = paired_ratio(copper, gold)
    rep = futures_quality_report(copper, gold, ratio)
    assert date(2020, 1, 6).isoformat() in rep["copper_spikes"]
    assert rep["n"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_data/test_copper_gold.py -v`

Expected: FAIL — module not found

- [ ] **Step 3: Write minimal implementation**

계약 연결 알고리즘 없음. 스파이크를 수정하지 않고 보고만.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_data/test_copper_gold.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/data/copper_gold.py tests/test_data/test_copper_gold.py
git commit -m "feat(data): pair copper/gold on same CME dates and scan roll spikes"
```

---

### Task 5: RegimeSnapshot custom Data + catalog roundtrip (HARD GATE)

**Files:**
- Create: `src/sngw_trader/data/regime_snapshot.py`
- Test: `tests/test_data/test_regime_snapshot.py`

**Interfaces:**
- Consumes: Nautilus 1.231.0 `nautilus_trader.model.custom.customdataclass`
- Produces: `@customdataclass class RegimeSnapshot` with **only** these annotated fields (plus decorator-injected `ts_event`/`ts_init`):

```python
session_date: str          # YYYY-MM-DD
oas: float
vix: float
vxv: float
copper: float
gold: float
copper_gold: float
vix_vxv: float
oas_observation_date: str
vix_observation_date: str
vxv_observation_date: str
copper_observation_date: str
gold_observation_date: str
oas_age: int
vix_age: int
vxv_age: int
copper_gold_age: int
oas_z: float
vix_vxv_z: float
growth_z: float
stress: float
growth: float
regime_code: int
quality_code: int
feature_version: str
source_snapshot_id: str
```

지원 타입은 `str|bool|float|int|bytes|dict|InstrumentId`. `str`이 되므로 snapshot id를 numeric key로 줄일 필요 없다. 그래도 manifest에 같은 문자열을 중복 저장한다.

이 태스크가 실패하면 **이후 태스크를 진행하지 말고** 실제 에러와 설치된 API를 사용자와 합의한다. 대체 경로를 추측으로 만들지 말 것.

- [ ] **Step 1: Write the failing tests**

```python
from pathlib import Path

from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

from sngw_trader.data.regime_snapshot import RegimeSnapshot, make_snapshot


def test_snapshot_has_ts_event() -> None:
    snap = make_snapshot(
        session_date="2020-01-02",
        ts_event=1,
        ts_init=1,
        oas=4.0,
        vix=15.0,
        vxv=16.0,
        copper=3.0,
        gold=1500.0,
        copper_gold=3.0 / 1500.0,
        vix_vxv=15.0 / 16.0,
        oas_observation_date="2020-01-01",
        vix_observation_date="2020-01-02",
        vxv_observation_date="2020-01-02",
        copper_observation_date="2020-01-02",
        gold_observation_date="2020-01-02",
        oas_age=1,
        vix_age=0,
        vxv_age=0,
        copper_gold_age=0,
        oas_z=-0.4,
        vix_vxv_z=-0.2,
        growth_z=0.8,
        stress=-0.3,
        growth=0.8,
        regime_code=1,
        quality_code=2,
        feature_version="macro-proxy-2axis-v1",
        source_snapshot_id="abc",
    )
    assert snap.ts_event == 1
    assert snap.regime_code == 1
    assert snap.session_date == "2020-01-02"


def test_catalog_roundtrip(tmp_path: Path) -> None:
    snap = make_snapshot(
        session_date="2020-01-02",
        ts_event=1_577_923_200_000_000_000,
        ts_init=1_577_923_200_000_000_000,
        oas=4.0, vix=15.0, vxv=16.0, copper=3.0, gold=1500.0,
        copper_gold=0.002, vix_vxv=0.9375,
        oas_observation_date="2020-01-01",
        vix_observation_date="2020-01-02",
        vxv_observation_date="2020-01-02",
        copper_observation_date="2020-01-02",
        gold_observation_date="2020-01-02",
        oas_age=1, vix_age=0, vxv_age=0, copper_gold_age=0,
        oas_z=0.0, vix_vxv_z=0.0, growth_z=0.0,
        stress=0.0, growth=0.0, regime_code=0, quality_code=2,
        feature_version="macro-proxy-2axis-v1",
        source_snapshot_id="snap-1",
    )
    catalog = ParquetDataCatalog(str(tmp_path))
    catalog.write_data([snap], data_cls=RegimeSnapshot)
    out = catalog.custom_data(cls=RegimeSnapshot)
    assert len(out) == 1
    got = out[0]
    assert got.session_date == "2020-01-02"
    assert got.source_snapshot_id == "snap-1"
    assert got.regime_code == 0
```

`make_snapshot`는 필드가 많아서 테스트/writer가 키워드로 생성하기 위한 헬퍼다. decorator `__init__`는 `ts_event`/`ts_init`가 앞에 온다:

```python
RegimeSnapshot(ts_event=..., ts_init=..., session_date=..., ...)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_data/test_regime_snapshot.py -v`

Expected: FAIL — module not found

- [ ] **Step 3: Write RegimeSnapshot + make_snapshot**

`customdataclass`가 `register_arrow`를 클래스 정의 시 호출한다. 수동 `register_arrow` 중복 호출 금지.

라운드트립이 실패하면 여기서 멈춘다. `write_custom_data` 같은 없는 API로 우회하지 말 것.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_data/test_regime_snapshot.py -v`

Expected: PASS. FAIL이면 STOP.

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/data/regime_snapshot.py tests/test_data/test_regime_snapshot.py
git commit -m "feat(data): add RegimeSnapshot custom Data catalog roundtrip"
```

---

### Task 6: As-of alignment, quality, snapshot rows

**Files:**
- Create: `src/sngw_trader/data/regime_align.py`
- Test: `tests/test_data/test_regime_align.py`

**Interfaces:**
- Consumes: `session_age`, `previous_calendar_date`, `robust_z`, `stress_score`, `growth_score`, `label_series`, `make_snapshot`
- Produces:
  - `last_on_or_before(series: dict[date, float], cap: date) -> tuple[date, float] | None`
  - `align_session(...)` → 한 세션의 raw 입력 + ages + quality. CME-only 날은 호출하지 않음 (마스터가 NYSE).
  - `quality_for(oas_age, vix_age, vxv_age, copper_gold_age, has_vix_vxv: bool) -> int`
    - OAS fresh iff `oas_age == 1`. OAS lagged iff `1 < oas_age <= 3`. OAS invalid iff missing or `oas_age > 3`.
    - VIX/VXV/CG fresh iff age == 0, lagged iff `1 <= age <= 3`, invalid iff missing or `age > 3`.
    - VIX 또는 VXV 없어 비율 불가 → invalid.
    - 하나라도 invalid → `QUALITY_INVALID`. 아니면 하나라도 lagged → `QUALITY_LAGGED`. 아니면 `QUALITY_FRESH`.
  - `build_snapshots(sessions, oas, vix, vxv, copper_gold_pairs, copper, gold, feature_version, source_snapshot_id, ts_event_for) -> list[RegimeSnapshot]`
    - 마스터 `sessions`는 NYSE (9 ETF 교집합은 writer가 넣음).
    - 세션 D: OAS cap=`D-1`, 나머지 cap=`D`.
    - invalid → `regime_code=0` (R0). 그 외 워밍업/nan → 그 세션은 **리스트에 넣지 않음** (라벨 없음).
    - lagged여도 분면 라벨은 부호로 부여 (R0 박스 아니면).
    - 무조건부 ffill 없음. 선형보간 없음. `shift(1)`과 `date<=D-1` 동시 사용 없음 (OAS는 cap만).
    - `ts_event_for(session: date) -> int`는 writer가 ETF 바와 같은 ns를 넣게 한다.

- [ ] **Step 1: Write the failing tests**

```python
from datetime import date

from sngw_trader.data.regime_align import build_snapshots, quality_for
from sngw_trader.data.regime_universe import QUALITY_FRESH, QUALITY_INVALID, QUALITY_LAGGED
from sngw_trader.indicators.regime import R0


def test_oas_age_1_is_fresh_others_zero() -> None:
    assert quality_for(1, 0, 0, 0, True) == QUALITY_FRESH


def test_bond_holiday_lags_oas() -> None:
    assert quality_for(2, 0, 0, 0, True) == QUALITY_LAGGED


def test_stale_over_3_is_invalid() -> None:
    assert quality_for(4, 0, 0, 0, True) == QUALITY_INVALID


def test_missing_vxv_ratio_invalid() -> None:
    assert quality_for(1, 0, 0, 0, False) == QUALITY_INVALID


def test_build_uses_oas_d_minus_1_not_double_shift() -> None:
    sessions = [date(2020, 1, d) for d in range(2, 32)] + [date(2020, 2, d) for d in range(3, 28)]
    # 252+ 세션이 필요하므로 길게 만든다
    sessions = []
    d = date(2019, 1, 2)
    while len(sessions) < 280:
        if d.weekday() < 5:
            sessions.append(d)
        d = date.fromordinal(d.toordinal() + 1)

    oas = {s: 4.0 for s in sessions}
    vix = {s: 15.0 for s in sessions}
    vxv = {s: 16.0 for s in sessions}
    cg = {s: 0.002 for s in sessions}
    copper = {s: 3.0 for s in sessions}
    gold = {s: 1500.0 for s in sessions}

    def ts(session: date) -> int:
        return int(session.strftime("%Y%m%d"))

    snaps = build_snapshots(
        sessions, oas, vix, vxv, cg, copper, gold,
        "macro-proxy-2axis-v1", "id1", ts,
    )
    last = snaps[-1]
    assert last.oas_observation_date == sessions[-2].isoformat()  # D-1 session, cap is calendar D-1 which includes previous session
    assert last.oas_age == 1
    assert last.vix_observation_date == sessions[-1].isoformat()
    assert last.vix_age == 0


def test_stale_session_forced_r0() -> None:
    sessions = []
    d = date(2019, 1, 2)
    while len(sessions) < 260:
        if d.weekday() < 5:
            sessions.append(d)
        d = date.fromordinal(d.toordinal() + 1)
    oas = {s: 4.0 for s in sessions[:-5]}  # last 5 sessions missing OAS
    vix = {s: 15.0 for s in sessions}
    vxv = {s: 16.0 for s in sessions}
    cg = {s: 0.002 for s in sessions}
    copper = {s: 3.0 for s in sessions}
    gold = {s: 1500.0 for s in sessions}
    snaps = build_snapshots(
        sessions, oas, vix, vxv, cg, copper, gold, "v1", "id", lambda s: 1,
    )
    assert snaps[-1].regime_code == R0
    assert snaps[-1].quality_code == QUALITY_INVALID
```

OAS `D-1` 테스트: 마지막 세션이 평일이면 `previous_calendar_date`가 주말일 수 있다. `last_on_or_before`가 주말 cap에서 직전 평일 OAS를 고르고 age=1이 되면 통과. 구현이 세션-1을 강제 `shift`하면 안 된다 — cap만 쓴다.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_data/test_regime_align.py -v`

Expected: FAIL — module not found

- [ ] **Step 3: Implement alignment**

`build_snapshots` 순서: 세션마다 raw as-of → 시계열 벡터화 → `robust_z` 세 성분 (OAS, VIX/VXV, copper/gold) → S/G → 라벨. 워밍업(`label==-1`) 행은 쓰지 않는다. invalid는 R0로 쓴다.

`ts_event_for`가 반환한 값으로 `ts_event=ts_init`. 호출 측이 ETF 바와 맞춘다.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_data/test_regime_align.py tests/test_indicators/test_regime.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/data/regime_align.py tests/test_data/test_regime_align.py
git commit -m "feat(data): align macro inputs as-of NYSE sessions with lag rules"
```

---

### Task 7: Writer + manifest (HTTP mocked)

**Files:**
- Create: `src/sngw_trader/data/regime_writer.py`
- Modify: `pyproject.toml` — script `regime-download = "sngw_trader.data.regime_writer:main"`
- Test: `tests/test_data/test_regime_writer.py`

**Interfaces:**
- Consumes: `fred_macro.fetch_fred_series`, `yahoo_etf.fetch_daily_bars` / `download_and_write`, `copper_gold.paired_ratio` / `futures_quality_report`, `regime_align.build_snapshots`, `RegimeSnapshot`
- Produces:
  - `input_hash(parts: dict[str, bytes | str]) -> str` — sha256 hex
  - `write_manifest(path: Path, payload: dict) -> None` — catalog 옆 `regime_manifest.json`
  - `bars_ts_event_by_date(bars: list) -> dict[date, int]`
  - `master_sessions(etf_dates: dict[str, set[date]]) -> list[date]` — **9 섹터 ETF 교집합**. CME-only 날은 자동 제외.
  - `download_inputs(...)` 는 fetch 함수를 인자로 받아 네트워크를 테스트에서 끊는다.
  - `run_write(catalog, inputs, manifest_path) -> str` — snapshots write, return `source_snapshot_id`

Manifest 최소 키:

```python
{
  "source_snapshot_id": "...",
  "downloaded_at": "ISO-8601",
  "feature_version": "macro-proxy-2axis-v1",
  "params": {"median_window": 60, "iqr_long": 252, "z0": 0.5, "stale_sessions": 3},
  "sources": {"BAMLH0A0HYM2": {"n": 0, "start": "", "end": ""}, ...},
  "futures_quality": {"n": 0, "copper_spikes": [], "gold_spikes": [], "ratio_spikes": []},
  "n_snapshots": 0,
  "session_start": "",
  "session_end": "",
}
```

- [ ] **Step 1: Write the failing tests**

```python
from datetime import date, datetime, timezone
from pathlib import Path

from sngw_trader.data.regime_writer import input_hash, master_sessions, write_manifest


def test_master_sessions_are_intersection() -> None:
    etf = {
        "XLY": {date(2020, 1, 2), date(2020, 1, 3)},
        "XLI": {date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6)},
    }
    # pad other 7 with 1-2
    for name in ("XLB", "XLF", "XLK", "XLE", "XLP", "XLU", "XLV"):
        etf[name] = {date(2020, 1, 2), date(2020, 1, 3)}
    assert master_sessions(etf) == [date(2020, 1, 2), date(2020, 1, 3)]


def test_manifest_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "regime_manifest.json"
    write_manifest(path, {"source_snapshot_id": "abc", "n_snapshots": 1})
    text = path.read_text(encoding="utf-8")
    assert "abc" in text


def test_input_hash_stable() -> None:
    a = input_hash({"oas": "1,2,3"})
    b = input_hash({"oas": "1,2,3"})
    c = input_hash({"oas": "1,2,4"})
    assert a == b
    assert a != c
```

writer 통합 (같은 파일에 추가). 9 ETF가 같은 280 영업일을 갖고 FRED/구리금이 상수일 때 Snapshot 수는 280-252=28 이상:

```python
from datetime import date, timedelta
from pathlib import Path

from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

from sngw_trader.data.regime_snapshot import RegimeSnapshot
from sngw_trader.data.regime_universe import SECTOR_ETFS
from sngw_trader.data.regime_writer import run_write


def _weekdays(n: int) -> list[date]:
    out: list[date] = []
    d = date(2019, 1, 2)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def test_run_write_emits_post_warmup_snapshots(tmp_path: Path) -> None:
    days = _weekdays(280)
    inputs = {
        "etf_dates": {s: set(days) for s in SECTOR_ETFS},
        "bar_ts": {d: i + 1 for i, d in enumerate(days)},
        "oas": {d: 4.0 for d in days},
        "vix": {d: 15.0 for d in days},
        "vxv": {d: 16.0 for d in days},
        "copper": {d: 3.0 for d in days},
        "gold": {d: 1500.0 for d in days},
        "feature_version": "macro-proxy-2axis-v1",
        "downloaded_at": "2026-09-15T00:00:00+00:00",
    }
    catalog = ParquetDataCatalog(str(tmp_path))
    sid = run_write(catalog, inputs, tmp_path / "regime_manifest.json")
    snaps = catalog.custom_data(cls=RegimeSnapshot)
    assert sid
    assert len(snaps) >= 28
    assert (tmp_path / "regime_manifest.json").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_data/test_regime_writer.py -v`

Expected: FAIL — module not found

- [ ] **Step 3: Implement writer**

`main()`: `load_settings()`로 catalog path. ETF는 `yahoo_etf.download_and_write`를 재사용 (이미 `CATALOG_SOURCE=yahoo-etf` 경로). 그 다음 FRED 3시리즈 + HG=F/GC=F fetch → align → write snapshots **ascending ts_init**. HG/GC는 `build_equity` 하지 말 것.

`source_snapshot_id = input_hash({series csv + params + downloaded_at date})`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_data/test_regime_writer.py tests/test_data/test_regime_snapshot.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/data/regime_writer.py tests/test_data/test_regime_writer.py pyproject.toml
git commit -m "feat(data): write RegimeSnapshot catalog and snapshot manifest"
```

---

### Task 8: Stage 0A quality gate

**Files:**
- Create: `src/sngw_trader/research/regime_stage0a.py`
- Test: `tests/test_research/test_regime_stage0a.py`

**Interfaces:**
- Consumes: catalog bars + snapshots + manifest (HTTP 없음)
- Produces:
  - `Stage0AReport` dataclass: `ok: bool`, `reasons: list[str]`, `n_snapshots`, `session_start`, `session_end`, `etf_rows: dict[str, int]`, `quality_share: dict[str, float]`, `r0_share`, `lagged_share`, `futures_quality`, `feature_version`, `source_snapshot_id`
  - `evaluate_stage0a(etf_dates, snapshots, manifest, config) -> Stage0AReport`
  - 실패 조건 (하나라도):
    1. 9 ETF 중 빈 티커
    2. 공통 세션 수 < `252 + MIN_REGIME_SESSIONS`
    3. snapshot 수 < `MIN_REGIME_SESSIONS` (워밍업 후)
    4. `feature_version` mismatch
    5. manifest `source_snapshot_id` != snapshot 필드
  - 보고만 (실패 아님): VXV로 인한 시작일, 2008 워밍업 포함 여부, copper/gold spike 목록, QQQ/IWM 결측 수, open 없는 바 수
  - `ok is False`면 Stage 0/1 진입 함수가 `SystemExit` 

- [ ] **Step 1: Write the failing tests**

```python
from datetime import date

from sngw_trader.data.regime_universe import SECTOR_ETFS
from sngw_trader.research.regime_stage0a import evaluate_stage0a


def _snaps(n: int, sid="sid", ver="macro-proxy-2axis-v1"):
    class S:
        def __init__(self, i: int):
            self.session_date = date(2010, 1, 1).fromordinal(date(2010, 1, 1).toordinal() + i).isoformat()
            self.quality_code = 2
            self.regime_code = 1
            self.feature_version = ver
            self.source_snapshot_id = sid
    return [S(i) for i in range(n)]


def test_missing_etf_fails() -> None:
    etf = {s: {date(2020, 1, 2)} for s in SECTOR_ETFS if s != "XLK"}
    report = evaluate_stage0a(etf, _snaps(300), {"source_snapshot_id": "sid", "feature_version": "macro-proxy-2axis-v1"}, None)
    assert report.ok is False
    assert any("XLK" in r for r in report.reasons)


def test_version_mismatch_fails() -> None:
    etf = {s: {date(2020, 1, 2 + i) for i in range(400)} for s in SECTOR_ETFS}
    report = evaluate_stage0a(
        etf, _snaps(300, ver="other"),
        {"source_snapshot_id": "sid", "feature_version": "macro-proxy-2axis-v1"},
        None,
    )
    assert report.ok is False
```

`evaluate_stage0a`의 config는 `None`이면 `DEFAULT_REGIME_CONFIG`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_research/test_regime_stage0a.py -v`

Expected: FAIL — module not found

- [ ] **Step 3: Implement Stage 0A**

research는 catalog만 읽는다. `yahoo_etf`/`fred_macro` import 금지.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_research/test_regime_stage0a.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/research/regime_stage0a.py tests/test_research/test_regime_stage0a.py
git commit -m "feat(research): add Stage 0A regime data-quality gate"
```

---

### Task 9: Stage 0 forward probe + pass criteria

**Files:**
- Create: `src/sngw_trader/research/regime_stage0.py`
- Modify: `pyproject.toml` — `regime-stage0 = "sngw_trader.research.regime_stage0:main"`
- Test: `tests/test_research/test_regime_stage0.py`

**Interfaces:**
- Consumes: snapshots + ETF open/close by session. Stage 0A `ok` 필수.
- Produces:
  - `fwd_return(open_px: dict[date, float], t: date, h: int, sessions: list[date]) -> float | None`
    - `t_index = sessions.index(t)`; `t1 = sessions[t_index+1]`; `tH = sessions[t_index+H]`; `open[tH]/open[t1] - 1`. 시가 없으면 `None` (종가 대체 금지).
  - `basket_fwd(member_opens, t, h, sessions) -> float | None` — 멤버 전부 시가 있을 때만 동일가중. 아니면 None.
  - `block_bootstrap_mean_ci(values: np.ndarray, block: int, iters: int, seed: int) -> tuple[float, float, float]` — mean, p5, p95. 겹치는 fwd를 독립 표본처럼 t-test하지 말 것.
  - `regime_durations(codes: list[int]) -> dict[int, list[int]]` — 연속 run 길이. R0는 합격 중앙값에 넣지 않음.
  - `stage0_pass(table: dict, config) -> tuple[bool, list[str]]` Validation 구간만:
    1. R1: Cyc 20d fwd mean > Def, Cyc >= SPY
    2. R4: Cyc 20d 5%분위가 Def·R1보다 나쁨, Cyc mean < SPY
    3. R1–R4 지속기간 중앙값 >= 10 (R0 제외)
    4. R1과 R4 각각 `n_sessions >= MIN_REGIME_SESSIONS` and `share >= MIN_REGIME_SHARE`. 부족하면 그 레짐 방향성을 합격 근거로 쓰지 않고 전체 실패.
    - R2/R3는 보고만. 2022 XLK vs XLE는 보고만.
    - z0를 결과에 맞춰 바꾸지 않음.
  - `run_stage0(...)` → JSON 직렬화 가능 dict (레짐×라인의 mean/median/p5, n, share, duration median, bootstrap CI, exclusions, W=60 본실험). W=120은 `robustness: true`일 때 **한 번만** 같은 함수를 다른 창으로 돌려 첨부. 120이 더 좋아도 채택하지 않음.

라인: 9 섹터, Cyc, Def, SPY, XLK, XLE, 그리고 참고 QQQ/IWM (합격 조건 아님).

- [ ] **Step 1: Write the failing tests**

```python
from datetime import date

import numpy as np

from sngw_trader.research.regime_stage0 import (
    block_bootstrap_mean_ci,
    fwd_return,
    regime_durations,
    stage0_pass,
)


def test_fwd_open_to_open_skips_missing() -> None:
    sessions = [date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6), date(2020, 1, 7)]
    opens = {date(2020, 1, 2): 10.0, date(2020, 1, 3): 11.0, date(2020, 1, 7): 12.0}
    assert fwd_return(opens, date(2020, 1, 2), 2, sessions) is None  # T+H=1/6 missing open
    opens[date(2020, 1, 6)] = 12.1
    assert abs(fwd_return(opens, date(2020, 1, 2), 2, sessions) - (12.1 / 11.0 - 1)) < 1e-12


def test_duration_median_ignores_r0_for_pass_input() -> None:
    d = regime_durations([1, 1, 1, 0, 0, 4, 4, 4, 4])
    assert d[1] == [3]
    assert d[4] == [4]
    assert d[0] == [2]


def test_pass_requires_all_three_plus_sample() -> None:
    table = {
        "r1_cyc_20": 0.02, "r1_def_20": 0.01, "r1_spy_20": 0.015,
        "r4_cyc_20": -0.03, "r4_spy_20": -0.01,
        "r4_cyc_p5": -0.10, "r4_def_p5": -0.04, "r1_cyc_p5": -0.03,
        "duration_median_r1_r4": 12,
        "n_r1": 40, "n_r4": 40, "share_r1": 0.1, "share_r4": 0.1, "n_total": 400,
    }
    ok, reasons = stage0_pass(table, None)
    assert ok is True
    table["r1_cyc_20"] = 0.005
    ok, _ = stage0_pass(table, None)
    assert ok is False


def test_bootstrap_ci_contains_mean() -> None:
    rng = np.random.default_rng(0)
    x = rng.normal(0.01, 0.02, size=200)
    mean, lo, hi = block_bootstrap_mean_ci(x, block=5, iters=200, seed=1)
    assert lo <= mean <= hi
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_research/test_regime_stage0.py -v`

Expected: FAIL — module not found

- [ ] **Step 3: Implement probe**

`main()`: catalog에서 bars+snapshots+manifest 로드 → Stage 0A → fail면 exit → Validation 구간 합격 판정 → JSON을 `logs/regime_stage0/<utc>/report.json`에 기록. Dev/Test 표도 보고하되 `pass` 플래그는 Validation만.

제외 수(`n_excluded_missing_open`)를 보고.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_research/test_regime_stage0.py tests/test_research/test_regime_stage0a.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/research/regime_stage0.py tests/test_research/test_regime_stage0.py pyproject.toml
git commit -m "feat(research): add Stage 0 quadrant forward-return probe"
```

---

### Task 10: HTTP leak guards + synthetic multi-asset BacktestNode sync

**Files:**
- Create: `src/sngw_trader/runners/backtest_etf_regime.py` (조립 골격 + `build_etf_regime_run_config`)
- Create: `tests/test_data/test_macro_http_leak.py`
- Create: `tests/test_runners/test_backtest_etf_regime.py`
- Modify: `tests/test_strategies/test_no_exchange_io.py` — 기존 FORBIDDEN 유지

**Interfaces:**
- Produces:
  - `build_etf_regime_run_config(catalog_path, settings, start=None, end=None) -> BacktestRunConfig`
    - venue `ARCA`, `AccountType.MARGIN`, `OmsType.NETTING`, `starting_balances=["1_000_000 USD"]`
    - fee taker/maker = `0.0005` (편도 5bp). env `BT_TAKER_FEE`가 있으면 그걸 쓰되 기본은 5bp.
    - data: `BacktestDataConfig(data_cls=Bar, bar_types=[etf_bar_type(id) for id in sector_instrument_ids()])`
    - data: `BacktestDataConfig(data_cls=RegimeSnapshot, catalog_path=...)` — `instrument_id` 없음
    - 이 두 번째 config가 `node.build()`에서 실패하면, 러너는 `engine.add_data(catalog.custom_data(RegimeSnapshot, as_nautilus=True))` 대체 경로를 쓴다. 없는 develop API를 만들지 말 것. 어떤 경로가 쓰였는지를 로그 한 줄로 남긴다.
  - 이 태스크의 전략은 **테스트 전용** `tests/test_runners/test_backtest_etf_regime.py` 안의 stub: 9개 바와 snapshot이 **같은 session_date**로 모이면 다음 시가에 XLY 1주 시장가. 프로덕션 `strategies/`에 더미 전략을 두지 말 것.

- [ ] **Step 1: Write the failing tests**

HTTP leak:

```python
from pathlib import Path

FORBIDDEN = ("yfinance", "api.stlouisfed.org", "FRED_API_KEY")
ALLOW_DATA = True


def test_non_data_modules_do_not_call_macro_http() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "sngw_trader"
    for folder in ("strategies", "indicators", "runners", "research"):
        for path in (root / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for tok in FORBIDDEN:
                assert tok not in text, f"{path} contains {tok}"
```

`research`가 manifest 키 이름 `FRED_API_KEY`를 문자열로 쓰면 안 된다. 시리즈 이름 `BAMLH0A0HYM2`는 괜찮다.

Runner 조립:

```python
from datetime import datetime
from pathlib import Path

from sngw_trader.config.settings import Settings
from sngw_trader.runners.backtest_etf_regime import build_etf_regime_run_config


def _settings(catalog: Path) -> Settings:
    return Settings(
        okx_env="demo", confirm_live="NO", trader_id="TRADER-001", account_id="OKX-001",
        node_name="N", instrument_type="SWAP", symbol="BTC-USDT-SWAP", margin_mode="CROSS",
        region="GLOBAL", catalog_path=catalog, log_dir=catalog / "logs",
        redis_enabled=False, redis_host="", redis_port=6379,
        bt_maker_fee=0.0005, bt_taker_fee=0.0005, bt_latency_ms=200,
        bt_prob_fill_on_limit=0.7, bt_prob_slippage=0.1,
        strategy="err_mom_a", w_f=10, w_e=10, momentum_window=200, theta=0.0,
        ema_fast=20, ema_slow=50, n_pull=24, trade_size="0.01",
        risk_stop_enabled=True, atr_period=14, atr_mult=3.0,
        sizing_mode="vol_target", size_target_vol=0.20, size_half_life=20,
        size_min_scale=0.0, size_max_scale=3.0, size_rebalance_band=0.10,
        catalog_start=datetime(2026, 1, 1), catalog_end=datetime(2026, 3, 1),
    )


def test_run_config_has_nine_bar_types_and_snapshot(tmp_path: Path) -> None:
    cfg = build_etf_regime_run_config(str(tmp_path), _settings(tmp_path))
    assert cfg.venues[0].name == "ARCA"
    assert cfg.venues[0].starting_balances == ["1_000_000 USD"]
    assert len(cfg.data) >= 2
```

합성 체결 테스트는 필수. `tmp_path` catalog에 **4 세션 × 9 ETF** `Equity`+`Bar` (`yahoo_etf.build_equity` / `row_to_bar`)와 같은 `ts_event`의 Snapshot 4개를 쓴다. 테스트 파일 안의 stub Strategy가 9바와 snap이 모인 세션에서 XLY 1주 시장가를 내면, fills의 체결가가 **다음 세션 시가**인지 확인한다. `raise_exception=True`.

`BacktestDataConfig(data_cls=RegimeSnapshot)` 로 `node.build()`/`run()`이 실패하면 에러를 그대로 두고, 러너에서 `engine.add_data(catalog.custom_data(cls=RegimeSnapshot, as_nautilus=True))` 대체 경로를 구현한 뒤 같은 테스트를 다시 통과시킨다. develop의 `write_custom_data` / `register_custom_data_class` 는 1.231.0에 없으므로 쓰지 않는다.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_data/test_macro_http_leak.py tests/test_runners/test_backtest_etf_regime.py -v`

Expected: FAIL — runner module not found 또는 leak 없음(leak 테스트는 새 토큰이 없을 수 있어 PASS 가능). runner 테스트 FAIL이 핵심.

- [ ] **Step 3: Implement runner skeleton**

매매 조건 없음. `attach`는 나중에 전략 태스크에서. 지금은 `build_etf_regime_run_config`만.

합성 체결을 넣으려면 테스트 파일 내부 Strategy stub + `node.add_strategy`.

확인된 수수료 모델: `sngw_trader.runners.backtest_models:OkxRateFeeModel` (이름과 무관하게 rate 모델. 재사용).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_data/test_macro_http_leak.py tests/test_runners/test_backtest_etf_regime.py tests/test_strategies/test_no_exchange_io.py -v`

Expected: PASS. custom data가 노드에 안 실리면 대체 `engine.add_data` 경로를 구현하고 테스트를 다시 통과시킨다. 그래도 안 되면 STOP.

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/runners/backtest_etf_regime.py tests/test_runners/test_backtest_etf_regime.py tests/test_data/test_macro_http_leak.py
git commit -m "feat(runner): assemble ARCA multi-ETF BacktestNode with RegimeSnapshot"
```

---

### Task 11: HARD GATE — real Stage 0 run (not CI)

**Files:**
- 코드 변경 없음. `logs/regime_stage0/` 리포트는 gitignore 대상이면 커밋하지 않는다.

이 태스크는 네트워크가 필요하다. 기본 pytest에 넣지 않는다.

- [ ] **Step 1: Download ETF catalog**

```
$env:CATALOG_SOURCE="yahoo-etf"
$env:ETF_SYMBOLS="XLY,XLI,XLB,XLF,XLK,XLE,XLP,XLU,XLV,SPY,QQQ,IWM"
uv run catalog-download
```

- [ ] **Step 2: Download FRED + copper/gold + snapshots**

`.env`에 `FRED_API_KEY` 필요.

```
uv run regime-download
```

Expected: catalog에 `RegimeSnapshot` parquet + `regime_manifest.json`. Stage 0A가 writer 끝에서 요약 인쇄.

- [ ] **Step 3: Run Stage 0**

```
uv run regime-stage0
```

Expected: `report.json` with `pass` true/false on Validation, plus Dev/Test tables, bootstrap CI, durations, exclusion counts, W=120 appendix.

- [ ] **Step 4: Gate**

`pass == true`이고 Stage 0A `ok`일 때만 Task 12로. 실패면 Stage 1 파일을 만들지 말고 리포트만 남긴다. 창·z0·축을 고쳐서 재돌리지 말 것 (스펙: 결과를 본 뒤 z0 조정 금지). W=120만 좋아도 채택하지 말고 사용자와 논의.

- [ ] **Step 5: Commit**

코드 변경이 없으면 커밋 없음. 게이트 결과를 세션에 기록.

---

### Task 12: skip-1 momentum + overlay policy (pure)

**Files:**
- Create: `src/sngw_trader/indicators/sector_momentum.py`
- Test: `tests/test_indicators/test_sector_momentum.py`

**Interfaces:**
- Consumes: 없음 (순수)
- Produces:
  - `skip1_return(closes: list[float], lookback: int = 20) -> float | None` — 길이 `< lookback+1`이면 None. `closes[-1]/closes[-1-lookback] - 1` 이 아니라 **마지막 종가를 빼고** `closes[-2]/closes[-2-lookback] - 1` (D-21→D-1, D 종가 미사용).
  - `top_k(scores: dict[str, float], k: int = 3) -> list[str]` — 점수 내림차순, 동점은 티커 오름차순. 유한 점수 부족하면 가능한 만큼.
  - `exposure_for(regime_code: int, overlay: str) -> float`
    - `base`: 항상 1.0
    - `exposure`: R1→1.0, R2/R3/R0→0.5, R4→0.0
    - `fixed50`: 0.5, `fixed100`: 1.0
    - `defensive`: R1→1.0, R2/R3→0.5, R0→0.5, R4→0.0 (유니버스 변경은 별도)
  - `target_names(overlay, regime_code, ranked, def_names) -> list[str]`
    - `defensive` and regime in {2,3}: `def_names` (XLP,XLU,XLV)
    - R4: `[]`
    - else: `ranked[:K]`
  - `can_reenter(overlay_state: str, is_week_end: bool) -> bool` — R4 청산 후 `flat_until_week`이면 주말 세션 전까지 False
  - `next_overlay_state(prev: str, regime_code: int, is_week_end: bool) -> str` — `"in_market" | "flat_until_week"`

R2와 R3 익스포저는 의도적으로 같다.

- [ ] **Step 1: Write the failing tests**

```python
from sngw_trader.indicators.sector_momentum import (
    can_reenter,
    exposure_for,
    next_overlay_state,
    skip1_return,
    target_names,
    top_k,
)


def test_skip1_ignores_last_close() -> None:
    # indices: D-21 ... D-1, D
    closes = [10.0] * 21 + [99.0]
    closes[0] = 10.0
    closes[-2] = 12.0
    assert abs(skip1_return(closes, 20) - (12.0 / 10.0 - 1)) < 1e-12


def test_top_k_tie_breaks_ticker() -> None:
    assert top_k({"XLK": 0.1, "XLE": 0.1, "XLY": 0.09, "XLF": 0.08}, 3) == ["XLE", "XLK", "XLY"]


def test_exposure_table() -> None:
    assert exposure_for(1, "exposure") == 1.0
    assert exposure_for(2, "exposure") == 0.5
    assert exposure_for(3, "exposure") == 0.5
    assert exposure_for(0, "exposure") == 0.5
    assert exposure_for(4, "exposure") == 0.0
    assert exposure_for(4, "base") == 1.0


def test_r4_exits_daily_reenter_weekly_only() -> None:
    assert next_overlay_state("in_market", 4, False) == "flat_until_week"
    assert can_reenter("flat_until_week", False) is False
    assert can_reenter("flat_until_week", True) is True
    assert target_names("defensive", 2, ["XLK", "XLY", "XLI"], ["XLP", "XLU", "XLV"]) == ["XLP", "XLU", "XLV"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_indicators/test_sector_momentum.py -v`

Expected: FAIL — module not found

- [ ] **Step 3: Implement pure policy**

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_indicators/test_sector_momentum.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/indicators/sector_momentum.py tests/test_indicators/test_sector_momentum.py
git commit -m "feat(indicator): add skip-1 sector momentum and overlay policy"
```

---

### Task 13: Stage 1 Strategy + Config

**Files:**
- Create: `src/sngw_trader/strategies/sector_momentum_gate.py`
- Test: `tests/test_strategies/test_sector_momentum_gate.py`

**Interfaces:**
- Consumes: `sector_momentum.*`, `RegimeSnapshot`, `is_last_session_of_week` (세션 리스트는 config가 아니라 런타임에 쌓은 session_date 리스트)
- Produces:

```python
class SectorMomentumGateConfig(StrategyConfig, frozen=True):
    instrument_ids: str  # comma-separated, 9 names
    overlay: str = "exposure"  # base|exposure|defensive|fixed50|fixed100
    top_k: int = 3
    lookback: int = 20
    trade_size: str = "0"  # unused; notional from equity * exposure / k
    starting_equity: float = 1_000_000.0
    feature_version: str = "macro-proxy-2axis-v1"
    source_snapshot_id: str = ""
    close_positions_on_stop: bool = True
```

`SectorMomentumGate(Strategy)`:

- `on_start`: 9개 `subscribe_bars`; `subscribe_data(DataType(RegimeSnapshot))`. source_snapshot_id가 비어 있지 않으면 이벤트 불일치 시 무시.
- 버퍼: `session_date -> {bars: dict[id, Bar], snap: RegimeSnapshot | None}`. 9바와 snap이 채워지기 전 리밸런스 없음.
- session_date는 bar `ts_event`를 `America/New_York` 날짜로 (Yahoo naive UTC면 UTC date — writer가 바와 snapshot에 **같은 ts_event**를 넣었으므로 동일 키가 된다). 헬퍼 `session_date_from_ts(ts_ns) -> str`.
- 완성된 세션 D:
  1. snapshot.regime_code / quality 사용. 재계산 금지.
  2. R4면 시장가 flatten, state=`flat_until_week` (일간).
  3. 주말 세션이 아니면 return.
  4. `flat_until_week`이고 주말이면 재진입 허용.
  5. skip-1 점수 → top_k → overlay 유니버스/익스포저 → 타깃 수량으로 리밸런스.
- 주문: `order_factory.market`, `instrument.make_qty`. 롱온리. 숏/스탑/볼타겟 없음.
- 타깃 달러 = `account equity * exposure / len(names)`. 기존 포지션과 차이를 매매. 턴오버 없는 레그는 주문 없음.
- `feature_version` 불일치 세션은 스킵 (포지션 유지).

결정 로직은 모듈 레벨 순수 함수로 빼서 엔진 없이 테스트:

- `complete_session(bars, snap) -> bool`
- `orders_for_session(...)` → `list[tuple[str, int]]` side/qty 의사결정. Strategy는 이걸 submit.

- [ ] **Step 1: Write the failing tests** (순수 함수 위주)

```python
from sngw_trader.strategies.sector_momentum_gate import orders_for_session


def test_r4_flattens_even_midweek() -> None:
    pos = {"XLY.ARCA": 10.0, "XLI.ARCA": 10.0, "XLB.ARCA": 10.0}
    out = orders_for_session(
        overlay="exposure",
        regime_code=4,
        is_week_end=False,
        state="in_market",
        ranked=["XLY.ARCA", "XLI.ARCA", "XLB.ARCA"],
        positions=pos,
        prices={k: 100.0 for k in pos},
        equity=1_000_000.0,
        top_k=3,
    )
    assert out.state == "flat_until_week"
    assert all(qty < 0 for _, qty in out.trades)


def test_no_rebalance_until_all_inputs() -> None:
    from sngw_trader.strategies.sector_momentum_gate import session_ready
    assert session_ready(n_bars=8, has_snap=True, n_universe=9) is False
    assert session_ready(n_bars=9, has_snap=False, n_universe=9) is False
    assert session_ready(n_bars=9, has_snap=True, n_universe=9) is True
```

Config 생성 테스트: `SectorMomentumGateConfig(instrument_ids="XLY.ARCA,...")` 인스턴스.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_strategies/test_sector_momentum_gate.py -v`

Expected: FAIL — module not found

- [ ] **Step 3: Implement strategy**

노드/OKX/yfinance import 금지. `BacktestNode` 문자열도 넣지 말 것 (`test_no_exchange_io`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_strategies/test_sector_momentum_gate.py tests/test_strategies/test_no_exchange_io.py tests/test_data/test_macro_http_leak.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/strategies/sector_momentum_gate.py tests/test_strategies/test_sector_momentum_gate.py
git commit -m "feat(strategy): add sector momentum exposure-gate strategy"
```

---

### Task 14: Wire strategy into ETF regime runner

**Files:**
- Modify: `src/sngw_trader/runners/backtest_etf_regime.py`
- Modify: `pyproject.toml` — `backtest-etf-regime = "sngw_trader.runners.backtest_etf_regime:main"`
- Modify: `tests/test_runners/test_backtest_etf_regime.py`

**Interfaces:**
- Consumes: `SectorMomentumGate`, `build_etf_regime_run_config`
- Produces: `attach_etf_strategy(node, run_id, overlay: str, source_snapshot_id: str) -> None`. `main()`은 overlay=`exposure` 기본. 매매 if문 없음.

- [ ] **Step 1: Write the failing test**

```python
def test_attach_builds_gate_strategy() -> None:
    node = _StubNode()
    attach_etf_strategy(node, "id", overlay="base", source_snapshot_id="snap-1")
    assert node.added[0].config.overlay == "base"
    assert node.added[0].config.source_snapshot_id == "snap-1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_runners/test_backtest_etf_regime.py::test_attach_builds_gate_strategy -v`

Expected: FAIL — `attach_etf_strategy` 없음

- [ ] **Step 3: Implement attach + main**

`live_okx` / `strategy_factory`에 이 전략을 등록하지 말 것 (라이브 ETF 경로 생성 금지).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_runners/test_backtest_etf_regime.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/runners/backtest_etf_regime.py tests/test_runners/test_backtest_etf_regime.py pyproject.toml
git commit -m "feat(runner): attach sector momentum gate to ETF backtest node"
```

---

### Task 15: Placebo snapshots

**Files:**
- Create: `src/sngw_trader/research/regime_placebo.py`
- Test: `tests/test_research/test_regime_placebo.py`

**Interfaces:**
- Consumes: `list[RegimeSnapshot]` (또는 `list[int]` codes)
- Produces:
  - `extract_runs(codes: list[int]) -> list[tuple[int, int]]` — (code, length)
  - `shuffle_runs(runs, seed: int) -> list[tuple[int, int]]` — run 순서만 셔플. 길이·코드 도수 보존.
  - `expand_runs(runs) -> list[int]`
  - `placebo_snapshots(snaps: list[RegimeSnapshot], seed: int = 42) -> list[RegimeSnapshot]` — `regime_code`만 교체. stress/growth/quality/dates 유지. `source_snapshot_id`에 `":placebo:{seed}"` suffix. 새 객체 (원본 mutate 금지).

- [ ] **Step 1: Write the failing tests**

```python
from sngw_trader.research.regime_placebo import extract_runs, expand_runs, shuffle_runs


def test_shuffle_preserves_duration_histogram() -> None:
    from collections import Counter

    codes = [1, 1, 1, 4, 4, 0, 2, 2, 2, 2]
    runs = extract_runs(codes)
    sh = shuffle_runs(runs, seed=7)
    assert sorted(sh) == sorted(runs)
    assert Counter(expand_runs(sh)) == Counter(codes)
    shuffled_once = False
    for seed in range(20):
        if expand_runs(shuffle_runs(runs, seed=seed)) != codes:
            shuffled_once = True
            break
    assert shuffled_once
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_research/test_regime_placebo.py -v`

Expected: FAIL — module not found

- [ ] **Step 3: Implement placebo**

catalog에 쓰려면 `write_data` 전용 함수 `write_placebo(catalog, snaps, seed)` — research가 아니라 writer? Spec: research는 HTTP 없고 catalog 쓰기는 회색. Stage 1 비교 스크립트가 tmp catalog 또는 같은 catalog identifier로 placebo를 쓰게 한다. `data/regime_writer.py`에 `write_snapshots(catalog, snaps)`를 재사용하고 research는 그걸 호출. research가 FRED를 열지 않으면 된다.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_research/test_regime_placebo.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/research/regime_placebo.py tests/test_research/test_regime_placebo.py
git commit -m "feat(research): shuffle regime runs for placebo gate"
```

---

### Task 16: Stage 1 comparison harness + adoption report

**Files:**
- Create: `src/sngw_trader/research/regime_stage1.py`
- Modify: `pyproject.toml` — `regime-stage1 = "sngw_trader.research.regime_stage1:main"`
- Test: `tests/test_research/test_regime_stage1.py`

**Interfaces:**
- Consumes: 같은 snapshot_id, 같은 기간, 같은 5bp, 다음 시가. overlay 목록:
  1. `base`
  2. `exposure` (1차 채택)
  3. `defensive` (별도 실험, 채택 아님)
  4. `fixed50`, `fixed100`
  5. `placebo` (exposure 규칙 + shuffled codes)
- Produces:
  - `stage1_metrics(equity_marks, fills, initial, periods_per_year=252) -> dict` — max DD, Sharpe (252), CAGR, avg invested weight, turnover, n_trades. crypto `metrics.py`의 365.25 Sharpe를 그대로 쓰지 말 것.
  - `avg_invested(weights: list[float]) -> float` — 세션별 long notional / equity
  - `stage1_pass(base: dict, exposure: dict, fixed50: dict, placebo: dict, config) -> tuple[bool, list[str]]` **Test 구간만**:
    1. exposure maxDD < base maxDD
    2. exposure Sharpe >= base Sharpe
    3. exposure가 fixed50 **그리고** placebo보다 DD 또는 Sharpe에서 개선 (둘 다 못 이기면 레짐 정보 기여 없음 → 실패)
    4. `avg_invested >= MIN_AVG_INVESTED`, `n_trades >= MIN_STAGE1_TRADES`
    5. Sharpe만 오르고 DD가 커지면 실패
    6. 거래가 사라져 통계가 비면 실패
  - `main()`은 Stage 0A ok + 같은 `source_snapshot_id`/`feature_version`를 요구. Stage 0 `pass`가 false면 `SystemExit("Stage 0 did not pass; refusing Stage 1")`.

비교 대상은 SPY B&H가 아니라 Base다. SPY는 보고 라인만.

- [ ] **Step 1: Write the failing tests**

```python
from sngw_trader.research.regime_stage1 import stage1_pass


def test_exposure_must_cut_dd_and_not_lose_sharpe() -> None:
    base = {"max_dd": 0.30, "sharpe": 0.50, "avg_invested": 1.0, "n_trades": 80}
    good = {"max_dd": 0.20, "sharpe": 0.55, "avg_invested": 0.6, "n_trades": 70}
    fixed = {"max_dd": 0.22, "sharpe": 0.40, "avg_invested": 0.5, "n_trades": 70}
    placebo = {"max_dd": 0.25, "sharpe": 0.45, "avg_invested": 0.6, "n_trades": 70}
    ok, _ = stage1_pass(base, good, fixed, placebo, None)
    assert ok is True
    worse_dd = dict(good, max_dd=0.35, sharpe=0.80)
    ok, _ = stage1_pass(base, worse_dd, fixed, placebo, None)
    assert ok is False


def test_empty_trades_fail() -> None:
    base = {"max_dd": 0.3, "sharpe": 0.5, "avg_invested": 1.0, "n_trades": 80}
    empty = {"max_dd": 0.0, "sharpe": None, "avg_invested": 0.0, "n_trades": 0}
    ok, reasons = stage1_pass(base, empty, empty, empty, None)
    assert ok is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_research/test_regime_stage1.py -v`

Expected: FAIL — module not found

- [ ] **Step 3: Implement compare + pass**

`main`이 실제 BacktestNode를 여러 번 돌린다. 단위 테스트는 `stage1_pass` / `stage1_metrics`만. 노드 루프는 러너 `build_etf_regime_run_config` + overlay만 바꿔 attach. research에 시그널 if문 금지.

Sharpe: `mean(daily_ret)/std * sqrt(252)`, 무위험 0.

avg invested: 세션마다 `sum(abs(position_notional))/equity`. 러너/엔진에서 포지션 스냅샷을 못 뽑으면 fills로 추정하지 말고, 전략이 `self.log.info` 대신 순수하게 research가 같은 overlay 함수로 **사후 계산하지 않는다**. 엔진 `portfolio.net_exposures` 또는 account events + fills로 근사. 구현 시 설치된 API:

```
uv run python -c "from nautilus_trader.portfolio.portfolio import Portfolio; print([x for x in dir(Portfolio) if not x.startswith('_')])"
```

없으면 전략 config에 기록용 callback을 두지 말고, 테스트에서는 `stage1_metrics`에 weights를 직접 넣는다. live 집계가 막히면 사용자에게 API를 확인하게 하고 추측 포트폴리오 트래커를 전략 안에 만들지 말 것 (규칙: Cache/Portfolio 무시 금지).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_research/test_regime_stage1.py tests/test_research/test_regime_placebo.py tests/test_data/test_macro_http_leak.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/research/regime_stage1.py tests/test_research/test_regime_stage1.py pyproject.toml
git commit -m "feat(research): compare Stage 1 overlays against base and placebo"
```

---

## Execution notes for agents

1. Task 5 라운드트립 실패 또는 Task 10 노드 적재 실패 → 코드 추측 금지, 사용자와 합의.
2. Task 11이 FAIL이면 Task 12–16을 구현하지 않는다.
3. `strategy_factory.py` / `live_okx.py`에 이 전략을 추가하지 않는다.
4. Stage 2 (`T5YIE`, 8분면, XLC/XLRE) 코드 경로를 미리 만들지 않는다.
5. 기본 스위트: `uv run pytest tests/test_data/test_regime_universe.py tests/test_data/test_fred_macro.py tests/test_data/test_copper_gold.py tests/test_data/test_regime_snapshot.py tests/test_data/test_regime_align.py tests/test_data/test_regime_writer.py tests/test_data/test_macro_http_leak.py tests/test_indicators/test_regime.py tests/test_indicators/test_sector_momentum.py tests/test_research/test_regime_stage0a.py tests/test_research/test_regime_stage0.py tests/test_research/test_regime_placebo.py tests/test_research/test_regime_stage1.py tests/test_strategies/test_sector_momentum_gate.py tests/test_runners/test_backtest_etf_regime.py tests/test_strategies/test_no_exchange_io.py -v`
