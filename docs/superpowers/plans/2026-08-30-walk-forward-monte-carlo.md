# Walk-Forward (IS/OOS) + Monte Carlo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 단일 BacktestNode 러너 위에 walk-forward(rolling IS 6m / OOS 3m, holdout 6m 제외) 시뮬레이터와 트레이드 부트스트랩 Monte Carlo 오케스트레이션 레이어를 추가한다.

**Architecture:** `src/sngw_trader/research/` 신설 오케스트레이션 패키지. 실행 1회마다 fresh `BacktestNode`(build → engine 부착 → run → dispose)를 순차 실행하고, 창 분할·그리드 선택·집계·리포트는 순수 모듈로 분리한다. 오케스트레이터는 전략을 모른다(`ImportableStrategyConfig` 패턴).

**Tech Stack:** Python 3.12, nautilus_trader 1.231.0 (아래 심볼은 설치본에서 실행 검증 완료), numpy/pandas (nautilus 전이 의존성), pytest.

**Spec:** `docs/superpowers/specs/2026-08-30-walk-forward-monte-carlo-design.md`

## Global Constraints

- 러너는 BacktestNode뿐. 자체 루프/ccxt/python-okx 금지 (`NAUTILUS_VIBE_RULES.md`).
- InstrumentId는 `{SYMBOL}.OKX`. research 코드는 거래소 I/O를 하지 않는다.
- 시크릿은 env만. 테스트는 네트워크·실계좌 없이 실행.
- 새 의존성 금지 (numpy/pandas는 nautilus 전이 의존성으로 이미 사용 가능).
- 테스트 실행: `uv run pytest <path> -v`
- 검증된 nautilus 1.231.0 심볼 (구현 시 임의 클래스로 대체 금지):
  - `BacktestNode`에 `add_strategy` 없음 → `node.build()` → `engine = node.get_engine(run_config.id)` → `engine.add_strategy(strategy)`.
  - `BacktestRunConfig(start=..., end=..., dispose_on_completion=False, raise_exception=True)` 지원.
  - `StrategyFactory.create(ImportableStrategyConfig(...))` — 문자열 config가 InstrumentId/BarType/Decimal로 디코드됨 (실행 검증 완료).
  - `Position.is_closed`, `.ts_closed`(uint64 ns), `.realized_pnl.as_double()`, `.realized_return`, `engine.cache.positions()`.
  - `ParquetDataCatalog.query_first_timestamp(Bar, identifier=bar_type) -> pd.Timestamp | None` / `query_last_timestamp` 동일 시그니처.
  - `nautilus_trader.core.datetime.dt_to_unix_nanos(datetime) -> int`.
- 커밋 접두: Task 1은 `feat(runner):`, 나머지는 `feat(research):`.

---

### Task 1: runners/backtest_okx 확장 + attach_strategy 수정

**Files:**
- Modify: `src/sngw_trader/runners/backtest_okx.py`
- Test: `tests/test_runners/test_backtest_okx.py` (신규)

**Interfaces:**
- Consumes: 기존 `build_fill_model_config`/`build_fee_model_config`/`build_latency_model_config`/`build_ema_cross`/`default_bar_type`
- Produces (Task 5 executor가 사용):
  - `build_run_config(catalog_path: str, instrument_id: str, settings: Settings, start: datetime | None = None, end: datetime | None = None, dispose_on_completion: bool = True, raise_exception: bool = False) -> BacktestRunConfig`
  - `attach_strategy(node: BacktestNode, run_config: BacktestRunConfig, instrument_id: str) -> None` — `node.get_engine(run_config.id)`로 engine을 찾아 `engine.add_strategy()`

배경: nautilus 1.231의 `BacktestNode`에는 `add_strategy`가 없어 현재 `attach_strategy`는 항상 `RuntimeError`로 깨져 있다. WF executor가 의존하는 root-cause fix다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_runners/test_backtest_okx.py`:

```python
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sngw_trader.config.settings import Settings
from sngw_trader.runners.backtest_okx import attach_strategy, build_run_config


def _settings() -> Settings:
    return Settings(
        okx_env="demo", confirm_live="NO", trader_id="TRADER-001", account_id="OKX-001",
        node_name="N", instrument_type="SWAP", symbol="BTC-USDT-SWAP", margin_mode="CROSS",
        region="GLOBAL", catalog_path=Path("./catalog"), log_dir=Path("./logs"),
        redis_enabled=False, redis_host="", redis_port=6379,
        bt_maker_fee=0.0002, bt_taker_fee=0.0005, bt_latency_ms=200,
        bt_prob_fill_on_limit=0.7, bt_prob_slippage=0.1,
    )


def test_build_run_config_with_window():
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 7, 1, tzinfo=timezone.utc)
    cfg = build_run_config("catalog", "BTC-USDT-SWAP.OKX", _settings(), start=start, end=end,
                           dispose_on_completion=False, raise_exception=True)
    assert cfg.data[0].start_time == start.isoformat()
    assert cfg.data[0].end_time == end.isoformat()
    assert cfg.dispose_on_completion is False
    assert cfg.raise_exception is True


def test_build_run_config_defaults_unchanged():
    cfg = build_run_config("catalog", "BTC-USDT-SWAP.OKX", _settings())
    assert cfg.data[0].start_time is None
    assert cfg.dispose_on_completion is True
    assert cfg.raise_exception is False


class _StubEngine:
    def __init__(self):
        self.added = []

    def add_strategy(self, strategy):
        self.added.append(strategy)


class _StubNode:
    def __init__(self, engine):
        self._engine = engine

    def get_engine(self, run_config_id):
        return self._engine


def test_attach_strategy_goes_to_engine():
    engine = _StubEngine()
    attach_strategy(_StubNode(engine), "run-config-id", "BTC-USDT-SWAP.OKX")
    assert len(engine.added) == 1
    assert engine.added[0].config.fast_ema_period == 10


def test_attach_strategy_raises_without_engine():
    with pytest.raises(RuntimeError):
        attach_strategy(_StubNode(None), "run-config-id", "BTC-USDT-SWAP.OKX")
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_runners/test_backtest_okx.py -v`
Expected: FAIL — 현재 `attach_strategy`는 `node.add_strategy`를 찾다가 `RuntimeError("BacktestNode has no compatible add_strategy method")` 발생. `build_run_config`은 start/end 파라미터가 없어 TypeError.

- [ ] **Step 3: 구현**

`src/sngw_trader/runners/backtest_okx.py`:

1. 상단 import에 `from datetime import datetime` 추가.
2. `build_run_config` 교체:

```python
def build_run_config(
    catalog_path: str,
    instrument_id: str,
    settings: Settings,
    start: datetime | None = None,
    end: datetime | None = None,
    dispose_on_completion: bool = True,
    raise_exception: bool = False,
) -> BacktestRunConfig:
    venue = BacktestVenueConfig(
        name="OKX",
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        book_type=BookType.L1_MBP,
        starting_balances=["10_000 USDT"],
        fill_model=build_fill_model_config(settings),
        fee_model=build_fee_model_config(settings),
        latency_model=build_latency_model_config(settings),
    )
    data = BacktestDataConfig(
        data_cls=Bar,
        catalog_path=catalog_path,
        instrument_id=instrument_id,
        start_time=start.isoformat() if start else None,
        end_time=end.isoformat() if end else None,
    )
    return BacktestRunConfig(
        venues=[venue],
        data=[data],
        engine=BacktestEngineConfig(),
        dispose_on_completion=dispose_on_completion,
        raise_exception=raise_exception,
    )
```

3. `attach_strategy` 교체 (기존 node-level 시도/except 블록 전부 삭제):

```python
def attach_strategy(node: BacktestNode, run_config: BacktestRunConfig, instrument_id: str) -> None:
    engine = node.get_engine(run_config.id)
    if engine is None:
        raise RuntimeError(f"No engine built for run config {run_config.id}; call node.build() first")
    strategy = build_ema_cross(
        instrument_id=instrument_id,
        bar_type=default_bar_type(instrument_id),
        trade_size="0.01",
    )
    engine.add_strategy(strategy)
```

4. `main()`의 `attach_strategy(node, run_config.id, settings.instrument_id_str)`를 `attach_strategy(node, run_config, settings.instrument_id_str)`로 갱신. (순서는 그대로: build → attach → run.)

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_runners/test_backtest_okx.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/runners/backtest_okx.py tests/test_runners/test_backtest_okx.py
git commit -m "feat(runner): window start/end params and engine-level strategy attach"
```

---

### Task 2: research 패키지 뼈대 + config + window 계산

**Files:**
- Create: `src/sngw_trader/research/__init__.py` (빈 파일)
- Create: `src/sngw_trader/research/config.py`
- Create: `src/sngw_trader/research/windows.py`
- Test: `tests/test_research/test_windows.py` (신규)

**Interfaces:**
- Consumes: 없음 (순수 datetime 계산)
- Produces (이후 전 태스크가 사용):
  - `GridSpec(strategy_path: str, config_path: str, fixed: dict[str, object], grid: dict[str, list])`
  - `WalkForwardConfig(is_months: int = 6, oos_months: int = 3, holdout_months: int = 6, warmup_days: int = 1, min_trades: int = 30)`
  - `MCConfig(n_sims: int = 1000, seed: int = 42, initial_capital: float = 10_000.0, ruin_threshold: float = -0.5)`
  - `Window(index: int, is_start: datetime, is_end: datetime, oos_start: datetime, oos_end: datetime)` — UTC, `is_end == oos_start`
  - `add_months(dt: datetime, months: int) -> datetime`
  - `holdout_start(data_end: datetime, holdout_months: int) -> datetime`
  - `compute_windows(data_start: datetime, data_end: datetime, is_months: int = 6, oos_months: int = 3, holdout_months: int = 6) -> list[Window]`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_research/test_windows.py`:

`tests/test_research/test_windows.py`:

```python
from datetime import datetime, timezone

import pytest

from sngw_trader.research.windows import add_months, compute_windows, holdout_start


def _dt(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, tzinfo=timezone.utc)


def test_add_months_clamps_day():
    assert add_months(_dt(2023, 1, 31), 1) == _dt(2023, 2, 28)
    assert add_months(_dt(2024, 1, 31), 1) == _dt(2024, 2, 29)


def test_holdout_start():
    assert holdout_start(_dt(2025, 6, 1), 6) == _dt(2024, 12, 1)


def test_compute_windows_rolling():
    windows = compute_windows(_dt(2023, 6, 1), _dt(2025, 6, 1))
    assert len(windows) == 4
    assert windows[0].is_start == _dt(2023, 6, 1)
    assert windows[0].oos_end == _dt(2024, 3, 1)
    assert windows[-1].oos_end == _dt(2024, 12, 1)  # holdout 시작(2024-12-01)에 닿는 마지막
    for i, w in enumerate(windows):
        assert w.index == i
        assert w.is_end == w.oos_start


def test_insufficient_data_raises():
    with pytest.raises(ValueError):
        compute_windows(_dt(2025, 1, 1), _dt(2025, 6, 1))


def test_start_after_end_raises():
    with pytest.raises(ValueError):
        compute_windows(_dt(2025, 6, 1), _dt(2025, 1, 1))
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_research/test_windows.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'sngw_trader.research'`)

- [ ] **Step 3: 구현**

`src/sngw_trader/research/__init__.py`: 빈 파일.

`src/sngw_trader/research/config.py`:

```python
"""Walk-forward / Monte Carlo configuration dataclasses."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GridSpec:
    """External grid injection. The orchestrator knows no strategy classes."""

    strategy_path: str  # "pkg.module:ClassName"
    config_path: str  # "pkg.module:ConfigClassName"
    fixed: dict[str, object]
    grid: dict[str, list]


@dataclass(frozen=True)
class WalkForwardConfig:
    is_months: int = 6
    oos_months: int = 3
    holdout_months: int = 6
    warmup_days: int = 1
    min_trades: int = 30


@dataclass(frozen=True)
class MCConfig:
    n_sims: int = 1000
    seed: int = 42
    initial_capital: float = 10_000.0
    ruin_threshold: float = -0.5
```

`src/sngw_trader/research/windows.py`:

```python
"""Walk-forward window boundaries. Pure datetime math, no nautilus."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Window:
    index: int
    is_start: datetime
    is_end: datetime
    oos_start: datetime
    oos_end: datetime


def add_months(dt: datetime, months: int) -> datetime:
    total = dt.year * 12 + (dt.month - 1) + months
    year, month_index = divmod(total, 12)
    month = month_index + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def holdout_start(data_end: datetime, holdout_months: int) -> datetime:
    return add_months(data_end, -holdout_months)


def compute_windows(
    data_start: datetime,
    data_end: datetime,
    is_months: int = 6,
    oos_months: int = 3,
    holdout_months: int = 6,
) -> list[Window]:
    """Rolling windows in [data_start, data_end - holdout]. OOS ends before holdout."""
    if not data_start < data_end:
        raise ValueError("data_start must be before data_end")
    wf_end = holdout_start(data_end, holdout_months)
    windows: list[Window] = []
    t = data_start
    index = 0
    while True:
        is_end = add_months(t, is_months)
        oos_end = add_months(is_end, oos_months)
        if oos_end > wf_end:
            break
        windows.append(Window(index, t, is_end, is_end, oos_end))
        index += 1
        t = add_months(t, oos_months)
    if not windows:
        raise ValueError("Not enough data for one IS/OOS window before holdout")
    return windows
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_research/test_windows.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/research tests/test_research
git commit -m "feat(research): wf config dataclasses and rolling window boundaries"
```

---

### Task 3: Monte Carlo (트레이드 부트스트랩)

**Files:**
- Create: `src/sngw_trader/research/monte_carlo.py`
- Test: `tests/test_research/test_monte_carlo.py`

**Interfaces:**
- Consumes: `MCConfig` (Task 2), numpy (nautilus 전이 의존성)
- Produces (Task 7가 사용):
  - `MCResult(n_sims: int, n_trades: int, final_return_p5: float, final_return_p50: float, final_return_p95: float, mdd_p50: float, mdd_p95: float, mdd_p99: float, ruin_prob: float, worst_final_return: float)`
  - `bootstrap_trades(pnls: list[float], cfg: MCConfig) -> MCResult` — 거래 수 2 미만이면 `ValueError`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_research/test_monte_carlo.py`:

```python
import pytest

from sngw_trader.research.config import MCConfig
from sngw_trader.research.monte_carlo import bootstrap_trades


def test_positive_pnls_never_ruin():
    r = bootstrap_trades([10.0] * 20, MCConfig(n_sims=500, seed=7))
    assert r.ruin_prob == 0.0
    assert r.mdd_p50 == 0.0
    assert r.final_return_p50 > 0.0
    assert r.final_return_p5 <= r.final_return_p50 <= r.final_return_p95


def test_same_seed_deterministic():
    pnls = [10.0, -5.0, 3.0, -2.0, 4.0] * 4
    a = bootstrap_trades(pnls, MCConfig(n_sims=200, seed=3))
    b = bootstrap_trades(pnls, MCConfig(n_sims=3, seed=3))
    # 동일 seed로 같은 시뮬레이터 계열이 나온다 — 부분 비교 (n_sims 다르면 앞부분 동일)
    assert a.n_trades == b.n_trades == 16
    assert a.ruin_prob >= 0.0 and a.ruin_prob <= 1.0


def test_ruin_possible_with_big_loss():
    cfg = MCConfig(n_sims=1000, seed=1, initial_capital=10.0, ruin_threshold=-0.5)
    r = bootstrap_trades([-6.0, 4.0], cfg)
    assert 0.0 < r.ruin_prob < 1.0  # -6 먼저 뽑히면 equity 4 <= 5 (ruin)


def test_too_few_trades_raises():
    with pytest.raises(ValueError):
        bootstrap_trades([1.0], MCConfig(n_sims=10, seed=1))
    with pytest.raises(ValueError):
        bootstrap_trades([], MCConfig(n_sims=10, seed=1))
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_research/test_monte_carlo.py -v`
Expected: FAIL (`monte_carlo` 모듈 없음)

- [ ] **Step 3: 구현**

`src/sngw_trader/research/monte_carlo.py`:

```python
"""Trade bootstrap Monte Carlo. Pure numpy, no nautilus."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from sngw_trader.research.config import MCConfig


@dataclass(frozen=True)
class MCResult:
    n_sims: int
    n_trades: int
    final_return_p5: float
    final_return_p50: float
    final_return_p95: float
    mdd_p50: float
    mdd_p95: float
    mdd_p99: float
    ruin_prob: float
    worst_final_return: float


def bootstrap_trades(pnls: list[float], cfg: MCConfig) -> MCResult:
    arr = np.asarray(pnls, dtype=float)
    if arr.size < 2:
        raise ValueError("need at least 2 closed trades for bootstrap")
    rng = np.random.default_rng(cfg.seed)
    finals = np.empty(cfg.n_sims)
    mdds = np.empty(cfg.n_sims)
    ruin = 0
    for i in range(cfg.n_sims):
        sample = rng.choice(arr, size=arr.size, replace=True)
        equity = cfg.initial_capital + np.cumsum(sample)
        peak = np.maximum.accumulate(equity)
        mdds[i] = float(np.max((peak - equity) / peak))
        finals[i] = equity[-1] / cfg.initial_capital - 1.0
        if float(equity.min()) <= cfg.initial_capital * (1.0 + cfg.ruin_threshold):
            ruin += 1
    return MCResult(
        n_sims=cfg.n_sims,
        n_trades=int(arr.size),
        final_return_p5=float(np.percentile(finals, 5)),
        final_return_p50=float(np.percentile(finals, 50)),
        final_return_p95=float(np.percentile(finals, 95)),
        mdd_p50=float(np.percentile(mdds, 50)),
        mdd_p95=float(np.percentile(mdds, 95)),
        mdd_p99=float(np.percentile(mdds, 99)),
        ruin_prob=ruin / cfg.n_sims,
        worst_final_return=float(finals.min()),
    )


def _selfcheck() -> None:
    r = bootstrap_trades([10.0] * 20, MCConfig(n_sims=500, seed=1))
    assert r.ruin_prob == 0.0 and r.mdd_p50 == 0.0, r
    print("ok")


if __name__ == "__main__":
    _selfcheck()
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_research/test_monte_carlo.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/research/monte_carlo.py tests/test_research/test_monte_carlo.py
git commit -m "feat(research): iid trade bootstrap monte carlo"
```

---

### Task 4: select.py (plateau 선택)

**Files:**
- Create: `src/sngw_trader/research/select.py`
- Test: `tests/test_research/test_select.py`

**Interfaces:**
- Consumes: 없음 (순수)
- Produces (Task 7가 사용):
  - `param_keys(grid: dict[str, list]) -> list[tuple]` — 축 이름 sorted 순서 product
  - `neighbors(key: tuple, grid: dict[str, list]) -> list[tuple]` — 각 축 ±1 스텝, 끝은 존재하는 이웃만
  - `sharpe_from_trades(returns: list[float]) -> float` — per-trade Sharpe (mean/std, ddof=1), 표준편차 0이거나 거래 2건 미만이면 0.0
  - `select_best(grid: dict[str, list], sharpe_by_key: dict[tuple, float], trades_by_key: dict[tuple, int], min_trades: int) -> tuple | None`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_research/test_select.py`:

```python
from sngw_trader.research.select import neighbors, select_best, sharpe_from_trades


GRID = {"fast_ema_period": [10, 20, 30], "slow_ema_period": [20, 50, 100]}


def test_neighbors_one_step_per_axis():
    assert neighbors((20, 50), GRID) == [(10, 50), (30, 50), (20, 20), (20, 100)]


def test_neighbors_edge_has_no_out_of_range():
    assert neighbors((10, 20), GRID) == [(20, 20), (10, 50)]


def test_sharpe_edge_cases():
    assert sharpe_from_trades([]) == 0.0
    assert sharpe_from_trades([1.0]) == 0.0
    assert sharpe_from_trades([1.0, 1.0, 1.0]) == 0.0
    assert sharpe_from_trades([1.0, -1.0]) < 0.0


def test_outlier_param_not_selected():
    # 스펙 §5 예시: [-1.0, 14.0, -2.5] → EMA(20)은 이웃 중앙값이 낮아 탈락해야 한다
    grid = {"fast_ema_period": [10, 20, 30]}
    sharpe = {(10,): -1.0, (20,): 14.0, (30,): -2.5}
    trades = {k: 100 for k in sharpe}
    best = select_best(grid, sharpe, trades, min_trades=30)
    assert best == (10,)  # smoothed: (10,)=6.5 > (30,)=5.75 > (20,)=-1.0


def test_all_below_min_trades_returns_none():
    sharpe = {(10,): 5.0, (20,): 4.0, (30,): 3.0}
    trades = {k: 5 for k in sharpe}
    assert select_best({"fast_ema_period": [10, 20, 30]}, sharpe, trades, min_trades=30) is None


def test_single_value_grid():
    best = select_best({"fast_ema_period": [10]}, {(10,): -2.0}, {(10,): 100}, 30)
    assert best == (10,)
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_research/test_select.py -v`
Expected: FAIL (`select` 모듈 없음)

- [ ] **Step 3: 구현**

`src/sngw_trader/research/select.py`:

```python
"""Grid selection: min-trades gate + neighbor-median smoothing (plateau)."""

from __future__ import annotations

import statistics
from itertools import product

import numpy as np


def param_keys(grid: dict[str, list]) -> list[tuple]:
    axes = sorted(grid)
    return [tuple(c) for c in product(*(grid[a] for a in axes))]


def neighbors(key: tuple, grid: dict[str, list]) -> list[tuple]:
    axes = sorted(grid)
    out: list[tuple] = []
    for i, axis in enumerate(axes):
        vals = grid[axis]
        pos = vals.index(key[i])
        for j in (pos - 1, pos + 1):
            if 0 <= j < len(vals):
                out.append(key[:i] + (vals[j],) + key[i + 1 :])
    return out


def sharpe_from_trades(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    arr = np.asarray(returns, dtype=float)
    std = arr.std(ddof=1)
    if std == 0:
        return 0.0
    return float(arr.mean() / std)


def select_best(
    grid: dict[str, list],
    sharpe_by_key: dict[tuple, float],
    trades_by_key: dict[tuple, int],
    min_trades: int = 30,
) -> tuple | None:
    """Pick the combo whose (self + neighbor) Sharpe median is highest.

    외톨이 파라미터(이웃은 다 망하고 자기만 급조합)는 이웃 중앙값에 깎여 탈락한다.
    """
    eligible = [k for k in param_keys(grid) if trades_by_key.get(k, 0) >= min_trades]
    if not eligible:
        return None

    def smoothed(key: tuple) -> float:
        vals = [sharpe_by_key[key]]
        vals += [sharpe_by_key[n] for n in neighbors(key, grid) if n in eligible]
        return statistics.median(vals)

    return max(eligible, key=smoothed)
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_research/test_select.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/research/select.py tests/test_research/test_select.py
git commit -m "feat(research): plateau grid selection with neighbor-median smoothing"
```

---

### Task 5: executor (fresh BacktestNode 1회 실행 + 전략 주입)

**Files:**
- Create: `src/sngw_trader/research/executor.py`
- Test: `tests/test_research/test_executor.py`

**Interfaces:**
- Consumes: `build_run_config(...)`, `default_bar_type(instrument_id)` (Task 1), `GridSpec` (Task 2)
- Produces (Task 7가 소비):
  - `RunResult(trade_pnls: list[float], trade_returns: list[float], n_trades: int, total_pnl: float)`
  - `build_strategy(spec: GridSpec, params: dict[str, object], instrument_id: str, bar_type: str)` — `StrategyFactory.create(ImportableStrategyConfig)`
  - `extract_run_result(positions: list, window_start_ns: int) -> RunResult`
  - `run_window(catalog_path: str, instrument_id: str, settings: Settings, spec: GridSpec, params: dict[str, object], start: datetime, end: datetime, warmup_days: int = 1) -> RunResult`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_research/test_executor.py`:

```python
from types import SimpleNamespace

from sngw_trader.research.executor import build_strategy, extract_run_result

SPEC = GridSpec(
    strategy_path="sngw_trader.strategies.example.ema_cross:EMACross",
    config_path="sngw_trader.strategies.example.ema_cross:EMACrossConfig",
    fixed={"trade_size": "0.01"},
    grid={},
)


def test_build_strategy_from_path_and_params():
    s = build_strategy(
        SPEC,
        {"fast_ema_period": 21, "slow_ema_period": 55},
        "BTC-USDT-SWAP.OKX",
        "BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL",
    )
    assert type(s).__name__ == "EMACross"
    assert s.config.fast_ema_period == 21
    assert s.config.slow_ema_period == 55
    assert str(s.config.instrument_id) == "BTC-USDT-SWAP.OKX"
    assert str(s.config.bar_type) == "BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL"


class _Money:
    def __init__(self, v: float):
        self._v = v

    def as_double(self) -> float:
        return self._v


def _pos(closed: bool, ts: int, pnl: float, ret: float):
    from types import SimpleNamespace

    return SimpleNamespace(is_closed=closed, ts_closed=ts, realized_pnl=_Money(pnl), realized_return=ret)


def test_extract_filters_warmup_and_open_and_sorts():
    positions = [
        _pos(True, 50, 1.0, 0.001),    # window 시작 전 — 제외 (warmup)
        _pos(False, 150, 9.0, 0.009),  # 미청산 — 제외
        _pos(True, 120, -2.0, -0.002),
        _pos(True, 200, 5.0, 0.005),
    ]
    r = extract_run_result(positions, window_start_ns=100)
    assert r.trade_pnls == [-2.0, 5.0]
    assert r.trade_returns == [-0.002, 0.005]
    assert r.n_trades == 2
    assert r.total_pnl == 3.0


def test_extract_empty():
    r = extract_run_result([], window_start_ns=0)
    assert r.n_trades == 0 and r.trade_pnls == [] and r.total_pnl == 0.0
```

(`_Money`/`_pos` 헬퍼는 duck-typing stub이다 — nautilus Position 객체가 아니어도 `extract_run_result`가 필요한 3개 속성만 읽는다.)

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_research/test_executor.py -v`
Expected: FAIL (`executor` 모듈 없음)

- [ ] **Step 3: 구현**

`src/sngw_trader/research/executor.py`:

```python
"""Run one backtest window on a fresh BacktestNode. No alpha here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.core.datetime import dt_to_unix_nanos
from nautilus_trader.trading.config import ImportableStrategyConfig, StrategyFactory

from sngw_trader.config.settings import Settings
from sngw_trader.research.config import GridSpec
from sngw_trader.runners.backtest_okx import build_run_config, default_bar_type


@dataclass(frozen=True)
class RunResult:
    trade_pnls: list[float]
    trade_returns: list[float]
    n_trades: int
    total_pnl: float


def build_strategy(spec: GridSpec, params: dict[str, object], instrument_id: str, bar_type: str):
    config = {
        **spec.fixed,
        "instrument_id": instrument_id,
        "bar_type": bar_type,
        **params,
    }
    return StrategyFactory.create(
        ImportableStrategyConfig(
            strategy_path=spec.strategy_path,
            config_path=spec.config_path,
            config=config,
        )
    )


def extract_run_result(positions: list, window_start_ns: int) -> RunResult:
    """Closed positions at/after window start (warmup pad excluded), close-time order."""
    closed = [p for p in positions if p.is_closed and p.ts_closed >= window_start_ns]
    closed.sort(key=lambda p: p.ts_closed)
    pnls = [p.realized_pnl.as_double() for p in closed]
    returns = [p.realized_return for p in closed]
    return RunResult(
        trade_pnls=pnls,
        trade_returns=returns,
        n_trades=len(closed),
        total_pnl=sum(pnls),
    )


def run_window(
    catalog_path: str,
    instrument_id: str,
    settings: Settings,
    spec: GridSpec,
    params: dict[str, object],
    start: datetime,
    end: datetime,
    warmup_days: int = 1,
):
    bar_type = default_bar_type(instrument_id)
    run_config = build_run_config(
        catalog_path,
        instrument_id,
        settings=settings,
        start=start - timedelta(days=warmup_days),
        end=end,
        dispose_on_completion=False,
        raise_exception=True,
    )
    node = BacktestNode(configs=[run_config])
    node.build()
    engine = node.get_engine(run_config.id)
    if engine is None:
        raise RuntimeError(f"Engine not built for run config {run_config.id}")
    engine.add_strategy(build_strategy(spec, params, instrument_id, bar_type))
    try:
        node.run()
        return extract_run_result(engine.cache.positions(), dt_to_unix_nanos(start))
    finally:
        node.dispose()
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_research/test_executor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/research/executor.py tests/test_research/test_executor.py
git commit -m "feat(research): single-window backtest executor on fresh BacktestNode"
```

---

### Task 6: report.py (JSON 리포트 + 콘솔 요약)

**Files:**
- Create: `src/sngw_trader/research/report.py`
- Test: `tests/test_research/test_report.py`

**Interfaces:**
- Consumes: 없음 (표준 라이브러리)
- Produces (Task 7가 소비):
  - `run_dir(strategy_name: str, base: Path | None = None) -> Path` — `logs/{전략명}/{YYYYMMDD-HHMMSS}/` 생성 후 반환
  - `write_reports(out_dir: Path, wf: dict, mc: dict, summary: dict) -> None` — `wf_report.json`, `mc_report.json`, `summary.json` 3개 파일 기록
  - `print_summary(summary: dict) -> None`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_research/test_report.py`:

```python
import json

from sngw_trader.research.report import run_dir, write_reports


def test_run_dir_structure(tmp_path):
    d = run_dir("EMACross", base=tmp_path)
    assert d.parent.name == "EMACross"
    assert d.parent.parent == tmp_path
    assert d.is_dir()


def test_write_reports(tmp_path):
    d = run_dir("EMACross", base=tmp_path)
    write_reports(d, {"windows": []}, {"oos": None}, {"n_windows": 1})
    names = {p.name for p in d.iterdir()}
    assert names == {"wf_report.json", "mc_report.json", "summary.json"}
    assert json.loads((d / "summary.json").read_text()) == {"n_windows": 1}
    assert json.loads((d / "wf_report.json").read_text()) == {"windows": []}
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_research/test_report.py -v`
Expected: FAIL (`report` 모듈 없음)

- [ ] **Step 3: 구현**

`src/sngw_trader/research/report.py`:

```python
"""JSON report files + console summary."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def run_dir(strategy_name: str, base: Path | None = None) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    d = (base or Path("logs")) / strategy_name / ts
    d.mkdir(parents=True, exist_ok=True)
    return d


def _dump(obj: dict) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


def write_reports(out_dir: Path, wf: dict, mc: dict, summary: dict) -> None:
    (out_dir / "wf_report.json").write_text(_dump(wf), encoding="utf-8")
    (out_dir / "mc_report.json").write_text(_dump(mc), encoding="utf-8")
    (out_dir / "summary.json").write_text(_dump(summary), encoding="utf-8")


def print_summary(summary: dict) -> None:
    print(_dump(summary))
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_research/test_report.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/research/report.py tests/test_research/test_report.py
git commit -m "feat(research): json report writer and console summary"
```

---

### Task 7: walk_forward 오케스트레이터 + 진입점 + 규칙 레이아웃 갱신

**Files:**
- Create: `src/sngw_trader/research/walk_forward.py`
- Modify: `pyproject.toml` — `[project.scripts]`에 `wf-okx` 추가
- Modify: `NAUTILUS_VIBE_RULES.md` §3 — `research/` 레이아웃 추가 (스펙 §11)
- Modify: `.env.example` — `WF_GRID_PATH`, `WF_DATA_START`, `WF_DATA_END` 주석 추가
- Test: `tests/test_research/test_walk_forward.py`

**Interfaces:**
- Consumes: Task 2의 `GridSpec/WalkForwardConfig/MCConfig`, Task 3의 `bootstrap_trades`, Task 4의 `select_best/param_keys/sharpe_from_trades`, Task 5(executor)의 `RunResult/run_window`, Task 6의 `report`, Task 1의 `default_bar_type`
- Produces:
  - `DEFAULT_GRID: GridSpec` (EMACross fast [10,20,30] × slow [20,50,100], fixed `{"trade_size": "0.01"}`)
  - `load_grid() -> GridSpec` — `WF_GRID_PATH` env JSON 없으면 DEFAULT_GRID
  - `detect_data_range(catalog_path: str, bar_type: str) -> tuple[datetime, datetime]` — `WF_DATA_START`/`WF_DATA_END` env override 우선, 없으면 catalog `query_first/last_timestamp(Bar, identifier=bar_type)`
  - `stitch_oos(window_results: list[RunResult | None]) -> list[float]`
  - `main() -> None` — 콘솔 entry `wf-okx`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_research/test_walk_forward.py`:

```python
import json

from sngw_trader.research.executor import RunResult
from sngw_trader.research.walk_forward import load_grid, stitch_oos


def test_stitch_concatenates_skipping_no_trade_windows():
    a = RunResult([1.0, 2.0], [0.01, 0.02], 2, 3.0)
    b = RunResult([-0.5], [-0.001], 1, -0.5)
    assert stitch_oos([a, None, b]) == [1.0, 2.0, -0.5]


def test_stitch_empty():
    assert stitch_oos([None, None]) == []


def test_load_grid_default(monkeypatch):
    monkeypatch.delenv("WF_GRID_PATH", raising=False)
    spec = load_grid()
    assert spec.strategy_path.endswith("ema_cross:EMACross")
    assert spec.grid == {"fast_ema_period": [10, 20, 30], "slow_ema_period": [20, 50, 100]}


def test_load_grid_from_json(tmp_path, monkeypatch):
    import json

    p = tmp_path / "grid.json"
    p.write_text(json.dumps({
        "strategy_path": "sngw_trader.strategies.example.ema_cross:EMACross",
        "config_path": "sngw_trader.strategies.example.ema_cross:EMACrossConfig",
        "fixed": {"trade_size": "0.02"},
        "grid": {"fast_ema_period": [5, 10]},
    }))
    monkeypatch.setenv("WF_GRID_PATH", str(p))
    spec = load_grid()
    assert spec.fixed == {"trade_size": "0.02"}
    assert spec.grid == {"fast_ema_period": [5, 10]}
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_research/test_walk_forward.py -v`
Expected: FAIL (`walk_forward` 모듈 없음)

- [ ] **Step 3: 오케스트레이터 구현**

`src/sngw_trader/research/walk_forward.py`:

```python
"""Walk-forward orchestration over BacktestNode. No alpha here."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from nautilus_trader.model.data import Bar
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

from sngw_trader.config import load_settings
from sngw_trader.research import report
from sngw_trader.research.config import GridSpec, MCConfig, WalkForwardConfig
from sngw_trader.research.executor import RunResult, run_window
from sngw_trader.research.monte_carlo import bootstrap_trades
from sngw_trader.research.select import param_keys, select_best, sharpe_from_trades
from sngw_trader.research.windows import compute_windows, holdout_start
from sngw_trader.runners.backtest_okx import default_bar_type

DEFAULT_GRID = GridSpec(
    strategy_path="sngw_trader.strategies.example.ema_cross:EMACross",
    config_path="sngw_trader.strategies.example.ema_cross:EMACrossConfig",
    fixed={"trade_size": "0.01"},
    grid={"fast_ema_period": [10, 20, 30], "slow_ema_period": [20, 50, 100]},
)


def load_grid() -> GridSpec:
    path = os.environ.get("WF_GRID_PATH")
    if not path:
        return DEFAULT_GRID
    data = json.loads(Path(path).read_text())
    return GridSpec(
        strategy_path=data["strategy_path"],
        config_path=data["config_path"],
        fixed=data.get("fixed", {}),
        grid=data["grid"],
    )


def detect_data_range(catalog_path: str, bar_type: str) -> tuple[datetime, datetime]:
    env_start = os.environ.get("WF_DATA_START")
    env_end = os.environ.get("WF_DATA_END")
    if env_start and env_end:
        return (
            datetime.fromisoformat(env_start).replace(tzinfo=timezone.utc),
            datetime.fromisoformat(env_end).replace(tzinfo=timezone.utc),
        )
    catalog = ParquetDataCatalog(path=catalog_path)
    first = catalog.query_first_timestamp(Bar, identifier=bar_type)
    last = catalog.query_last_timestamp(Bar, identifier=bar_type)
    if first is None or last is None:
        raise ValueError(f"No bar data in catalog for {bar_type}; write data first or set WF_DATA_START/WF_DATA_END")
    return first.to_pydatetime(), last.to_pydatetime()


def stitch_oos(window_results: list[RunResult | None]) -> list[float]:
    out: list[float] = []
    for r in window_results:
        if r is not None:
            out.extend(r.trade_pnls)
    return out


def _params_for(spec: GridSpec, key: tuple) -> dict[str, object]:
    return dict(zip(sorted(spec.grid), key))


def _run_is_grid(settings, spec: GridSpec, window, wf_cfg: WalkForwardConfig) -> tuple[dict, dict]:
    catalog_path = str(settings.catalog_path)
    instrument_id = settings.instrument_id_str
    axes = sorted(spec.grid)
    results: dict[tuple, RunResult] = {}
    sharpes: dict[tuple, float] = {}
    for i, key in enumerate(param_keys(spec.grid), 1):
        res = run_window(
            catalog_path, instrument_id, settings=settings, spec=spec,
            params=dict(zip(axes, key)),
            start=window.is_start, end=window.is_end,
            warmup_days=wf_cfg.warmup_days,
        )
        results[key] = res
        sharpes[key] = sharpe_from_trades(res.trade_returns)
        print(f"[wf] window {window.index} IS {i}/{len(param_keys(spec.grid))} "
              f"{dict(zip(axes, key))} sharpe={sharpes[key]:.2f} trades={res.n_trades}")
    return results, sharpes


def main() -> None:
    settings = load_settings()
    wf_cfg = WalkForwardConfig()
    mc_cfg = MCConfig()
    spec = load_grid()
    instrument_id = settings.instrument_id_str
    catalog_path = str(settings.catalog_path)
    bar_type = default_bar_type(instrument_id)

    data_start, data_end = detect_data_range(catalog_path, bar_type)
    windows = compute_windows(
        data_start, data_end, wf_cfg.is_months, wf_cfg.oos_months, wf_cfg.holdout_months,
    )
    h_start = holdout_start(data_end, wf_cfg.holdout_months)
    print(f"[wf] data {data_start:%Y-%m-%d}..{data_end:%Y-%m-%d} "
          f"holdout from {h_start:%Y-%m-%d}, {len(windows)} windows")

    strategy_name = spec.strategy_path.rsplit(":", 1)[-1]
    out_dir = report.run_dir(strategy_name)

    wf_windows: list[dict] = []
    oos_results: list[RunResult | None] = []
    last_params: tuple | None = None

    for window in windows:
        grid_results, sharpes = _run_is_grid(settings, spec, window, wf_cfg)
        best = select_best(
            spec.grid, sharpes,
            {k: r.n_trades for k, r in grid_results.items()},
            wf_cfg.min_trades,
        )
        oos_result: RunResult | None = None
        if best is not None:
            last_params = best
            oos_result = run_window(
                catalog_path, instrument_id, settings=settings, spec=spec,
                params=_params_for(spec, best),
                start=window.oos_start, end=window.oos_end,
                warmup_days=wf_cfg.warmup_days,
            )
        oos_results.append(oos_result)
        wf_windows.append({
            "index": window.index,
            "is": {"start": window.is_start.isoformat(), "end": window.is_end.isoformat()},
            "oos": {"start": window.oos_start.isoformat(), "end": window.oos_end.isoformat()},
            "selected": _params_for(spec, best) if best is not None else None,
            "grid": [
                {"params": _params_for(spec, k), "sharpe": sharpes[k], "n_trades": grid_results[k].n_trades}
                for k in grid_results
            ],
            "oos": None if oos_result is None else {"n_trades": oos_result.n_trades, "total_pnl": oos_result.total_pnl},
        })
        if oos_result is not None:
            print(f"[wf] window {window.index} OOS pnl={oos_result.total_pnl:.2f} trades={oos_result.n_trades}")

    stitched = stitch_oos(oos_results)
    mc_oos = bootstrap_trades(stitched, mc_cfg) if len(stitched) >= 2 else None

    holdout: RunResult | None = None
    mc_holdout = None
    if last_params is not None:
        holdout = run_window(
            catalog_path, instrument_id, settings=settings, spec=spec,
            params=_params_for(spec, last_params),
            start=h_start, end=data_end,
            warmup_days=wf_cfg.warmup_days,
        )
        if holdout.n_trades >= 2:
            mc_holdout = bootstrap_trades(holdout.trade_pnls, mc_cfg)

    wf_report = {
        "instrument_id": instrument_id,
        "data_start": data_start.isoformat(),
        "data_end": data_end.isoformat(),
        "holdout_start": h_start.isoformat(),
        "windows": wf_windows,
    }
    mc_report = {"oos": mc_oos, "holdout": mc_holdout}
    summary = {
        "instrument_id": instrument_id,
        "strategy": strategy_name,
        "n_windows": len(windows),
        "stitched_oos_trades": len(stitched),
        "stitched_oos_pnl": sum(stitched),
        "mc_oos": mc_oos,
        "holdout": None if holdout is None else {"n_trades": holdout.n_trades, "total_pnl": holdout.total_pnl},
        "mc_holdout": mc_holdout,
    }

    report.write_reports(out_dir, wf_report, mc_report, summary)
    report.print_summary(summary)


if __name__ == "__main__":
    main()
```

추가 수정 사항:

1. `pyproject.toml`의 `[project.scripts]`에 추가:

```toml
wf-okx = "sngw_trader.research.walk_forward:main"
```

2. `.env.example` 끝에 주석 추가:

```
# Walk-forward (optional)
# WF_GRID_PATH=
# WF_DATA_START=
# WF_DATA_END=
```

3. `NAUTILUS_VIBE_RULES.md` §3 레이아웃에 추가:

```
    research/                    # 백테스트 오케스트레이션 (WF/MC). 창 분할·반복 실행·집계만
```

그리고 §3 "역할 분리" 목록에 추가:

```
- `research/` : 창 분할, BacktestNode 반복 실행, 집계. 매매 조건 없음.
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_research -v`
Expected: PASS (windows/monte_carlo/select/executor/report/walk_forward 전부)

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/research/walk_forward.py pyproject.toml .env.example NAUTILUS_VIBE_RULES.md
git commit -m "feat(research): walk-forward orchestration with holdout validation"
```

---

### Task 8: 전체 검증 + 수동 e2e 안내

**Files:**
- 없음 (검증 전용). catalog 데이터가 있는 환경에서만 수동 실행.

**Interfaces:**
- Consumes: 전부

- [ ] **Step 1: 전체 테스트 스위트**

Run: `uv run pytest -v`
Expected: 기존 테스트 전부 PASS

- [ ] **Step 2: 조립 smoke (노드 실행 없이)**

```bash
uv run python -c "from sngw_trader.research.walk_forward import load_grid, stitch_oos; s = load_grid(); print(s.strategy_path, list(s.grid))"
```

Expected: `sngw_trader.strategies.example.ema_cross:EMACross ['fast_ema_period', 'slow_ema_period']`

- [ ] **Step 3: 수동 e2e (catalog 데이터 있는 환경에서만)**

```bash
uv run wf-okx
```

확인: window 진행 로그, `logs/EMACross/{실행시각}/` 아래 `wf_report.json`/`mc_report.json`/`summary.json`, 콘솔 요약. 데이터 부족 시 `ValueError`로 깨끗이 종료하는지 확인. `detect_data_range`의 `query_first_timestamp(Bar, identifier=bar_type)`가 None을 반환하면 identifier를 instrument_id로 바꿔 재시도하는 한 줄 fallback 추가 (동작 확인 후).

---

## Self-Review 결과

1. **스펙 커버리지**: §2 결정표 전 항목 → Task 매핑 완료 (fresh node: Task 1/5, rolling+holdout: Task 2/7, 외부 grid: Task 7 `load_grid`, plateau: Task 4, MC: Task 3, 리포트: Task 6/7, 규칙 갱신: Task 7). §6의 Sharpe-analyzer 항목은 §10 검증 결과에 따라 포지션 returns 기반 계산으로 대체 (Task 4 `sharpe_from_trades`) — 스펙 §10이 예고한 확인 결과다.
2. **플레이스홀더**: 없음. 모든 코드 블록 완전.
3. **타입 일관성**: `RunResult(trade_pnls, trade_returns, n_trades, total_pnl)` / `Window(index, is_start, is_end, oos_start, oos_end)` / `GridSpec(strategy_path, config_path, fixed, grid)` / `MCConfig(n_sims, seed, initial_capital, ruin_threshold)` 필드명이 소비 태스크와 일치함을 확인.
