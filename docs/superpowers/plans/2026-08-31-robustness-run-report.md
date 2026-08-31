# Robustness Run Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** walk-forward 백테스트 각 run에 강건성 지표(trade/equity)를 계산해 기존 리포트 3종에 확장 반영한다.

**Architecture:** 순수 numpy 지표 모듈(`metrics.py`) 신규 추가 → executor가 계좌 잔고 이벤트에서 equity 마크 수집 → walk_forward가 선택된 조합/OOS/holdout에 지표 계산 후 `wf_report.json`/`summary.json` 확장. 전략·노드 코드는 수정하지 않는다.

**Tech Stack:** Python 3.12, numpy (기존 설치), nautilus_trader 1.231.0 (executor만), pytest.

**Spec:** `docs/superpowers/specs/2026-08-31-robustness-run-report-design.md`

## Global Constraints

- nautilus import는 executor/walk_forward 계층에만. `metrics.py`는 순수 numpy, nautilus import 금지.
- 새 의존성 추가 금지. numpy/pytest만 사용.
- 모든 지표 함수는 NaN을 반환하지 않는다. 계산 불가 항목은 `None` (spec §4.1 특이값 표).
- `mc_report.json`은 변경 없음.
- 전략 파일 수정 금지, research 계층만 수정.
- 테스트 실행: `.venv\Scripts\python.exe -m pytest tests/test_research -v` (PowerShell, repo 루트에서)
- nautilus 로그가 콘솔을 오염시키므로 검증 명령은 테스트로만 확인한다.
- 커밋 접두: `feat(research):`

---

### Task 1: `compute_trade_metrics` — trade 기반 지표

**Files:**
- Create: `src/sngw_trader/research/metrics.py`
- Test: `tests/test_research/test_metrics.py`

**Interfaces:**
- Consumes: 없음 (순수 numpy)
- Produces: `compute_trade_metrics(pnls: list[float], returns: list[float]) -> dict | None`
  - 반환 키: `win_rate, profit_factor, expectancy, payoff_ratio, max_consecutive_losses`
  - `pnls` 비어 있으면 `None`. `profit_factor`는 총손실 0이면 `None`. `payoff_ratio`는 무손실 또는 무이익 거래면 `None`.

- [ ] **Step 1: Write the failing test**

```python
import pytest

from sngw_trader.research.metrics import compute_trade_metrics


def test_trade_metrics_values():
    m = compute_trade_metrics(
        [10.0, -5.0, 20.0, -5.0, -5.0], [0.01, -0.005, 0.02, -0.005, -0.005]
    )
    assert m["win_rate"] == pytest.approx(2 / 5)
    assert m["profit_factor"] == pytest.approx(30 / 15)
    assert m["expectancy"] == pytest.approx(3.0)
    assert m["payoff_ratio"] == pytest.approx(15 / 5)
    assert m["max_consecutive_losses"] == 2


def test_trade_metrics_empty_returns_none():
    assert compute_trade_metrics([], []) is None


def test_trade_metrics_no_losses():
    m = compute_trade_metrics([1.0, 2.0], [0.01, 0.02])
    assert m["profit_factor"] is None
    assert m["payoff_ratio"] is None
    assert m["win_rate"] == 1.0


def test_trade_metrics_no_wins():
    m = compute_trade_metrics([-1.0, -2.0], [-0.01, -0.02])
    assert m["payoff_ratio"] is None
    assert m["profit_factor"] == pytest.approx(0.0)
    assert m["max_consecutive_losses"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_research/test_metrics.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sngw_trader.research.metrics'`

- [ ] **Step 3: Write minimal implementation**

```python
"""Run-level robustness metrics. Pure numpy, no nautilus."""

from __future__ import annotations

import numpy as np

NS_PER_YEAR = 365.25 * 24 * 60 * 60 * 1_000_000_000


def _max_consecutive_losses(mask: np.ndarray) -> int:
    best = cur = 0
    for flag in mask:
        cur = cur + 1 if flag else 0
        best = max(best, cur)
    return best


def compute_trade_metrics(pnls: list[float], returns: list[float]) -> dict | None:
    if not pnls:
        return None
    arr = np.asarray(pnls, dtype=float)
    wins = arr[arr > 0]
    losses = arr[arr < 0]
    gross_win = float(wins.sum())
    gross_loss = float(-losses.sum())
    return {
        "win_rate": float(wins.size / arr.size),
        "profit_factor": None if gross_loss == 0 else gross_win / gross_loss,
        "expectancy": float(arr.mean()),
        "payoff_ratio": (
            None if wins.size == 0 or losses.size == 0
            else float(wins.mean() / losses.mean())
        ),
        "max_consecutive_losses": _max_consecutive_losses(arr < 0),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_research/test_metrics.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/research/metrics.py tests/test_research/test_metrics.py
git commit -m "feat(research): add trade-level robustness metrics"
```

---

### Task 2: `compute_equity_metrics` — equity 마크 기반 지표

**Files:**
- Modify: `src/sngw_trader/research/metrics.py` (아래 함수 추가)
- Test: `tests/test_research/test_metrics.py` (아래 테스트 추가)

**Interfaces:**
- Consumes: 없음
- Produces: `compute_equity_metrics(marks: list[tuple[int, float]], initial_capital: float) -> dict | None`
  - `marks`: `(ts_ns, balance)` 오름차순 아님 — 함수 내부에서 정렬.
  - 반환 키: `sortino, calmar, mdd_ratio, mdd_duration, recovery_duration, underwater_mean, tail_ratio, annualized_return`
    - duration 단위: 초 (ns / 1e9)
    - `mdd_ratio`: 드로다운 없으면 `0.0` (None 아님)
    - `calmar`: mdd_ratio 0 또는 annualized_return이 None/음수면 `None`
    - `sortino`: 하방 표준편차 0이면 `None` (per-mark, 미연율화 — `sharpe_from_trades`와 동일 스케일)
    - `tail_ratio`: p5 수익률이 0이면 `None`
    - equity 값에 0 이하가 있으면 전체 `None`
  - `marks` 2개 미만이면 `None`.
  - 연율화: `initial_capital → eq[-1]` 복리 수익률을 마크 구간 기간으로 연율화.

- [ ] **Step 1: Write the failing test**

```python
from sngw_trader.research.metrics import compute_equity_metrics

NS = 1_000_000_000


def test_equity_metrics_values():
    marks = [
        (0 * NS, 100.0),
        (1 * NS, 120.0),
        (2 * NS, 90.0),
        (3 * NS, 100.0),
        (4 * NS, 130.0),
    ]
    m = compute_equity_metrics(marks, 100.0)
    assert m["mdd_ratio"] == pytest.approx(0.25)
    assert m["mdd_duration"] == pytest.approx(3.0)       # peak(1s) → recovery(4s)
    assert m["recovery_duration"] == pytest.approx(2.0)  # trough(2s) → recovery(4s)
    assert m["underwater_mean"] == pytest.approx((0.0 + 0.0 - 0.25 - 1 / 6 + 0.0) / 5)
    # rets = [0.2, -0.25, 1/9, 0.3]; downside std = sqrt(0.25^2/4) = 0.125
    assert m["sortino"] == pytest.approx(0.0902777 / 0.125, rel=1e-3)
    assert m["tail_ratio"] > 1.0
    # ann = (130/100)^(1/4) - 1 > 0, mdd > 0 → calmar 산출
    assert m["annualized_return"] == pytest.approx((1.3) ** 0.25 - 1, rel=1e-6)
    assert m["calmar"] == pytest.approx(m["annualized_return"] / 0.25, rel=1e-6)


def test_equity_metrics_no_drawdown():
    m = compute_equity_metrics([(0, 100.0), (NS, 101.0), (2 * NS, 102.0)], 100.0)
    assert m["mdd_ratio"] == 0.0
    assert m["mdd_duration"] == 0.0
    assert m["recovery_duration"] is None
    assert m["calmar"] is None  # mdd 0
    assert m["sortino"] is None  # 하방편차 0


def test_equity_metrics_negative_annual_return():
    m = compute_equity_metrics([(0, 100.0), (NS, 50.0)], 100.0)
    assert m["annualized_return"] < 0
    assert m["calmar"] is None


def test_equity_metrics_none_cases():
    assert compute_equity_metrics([(0, 100.0)], 100.0) is None          # 마크 < 2
    assert compute_equity_metrics([(0, 100.0), (NS, 0.0)], 100.0) is None  # 잔고 0
    m = compute_equity_metrics([(0, 100.0), (NS, 100.0), (2 * NS, 100.0)], 100.0)
    assert m["tail_ratio"] is None  # p5 == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_research/test_metrics.py -v`
Expected: FAIL with `ImportError: cannot import name 'compute_equity_metrics'`

- [ ] **Step 3: Write minimal implementation**

`metrics.py`에 추가:

```python
def _drawdown_episodes(ts: np.ndarray, underwater: np.ndarray) -> list[tuple[int, int, int, bool]]:
    """(peak_i, trough_i, end_i, recovered) per drawdown episode."""
    episodes: list[tuple[int, int, int, bool]] = []
    in_dd = False
    peak_i = trough_i = 0
    for i in range(1, len(ts)):
        if underwater[i] < 0:
            if not in_dd:
                in_dd, peak_i, trough_i = True, i - 1, i
            elif underwater[i] < underwater[trough_i]:
                trough_i = i
        elif in_dd:
            episodes.append((peak_i, trough_i, i, True))
            in_dd = False
    if in_dd:
        episodes.append((peak_i, trough_i, len(ts) - 1, False))
    return episodes


def compute_equity_metrics(
    marks: list[tuple[int, float]], initial_capital: float
) -> dict | None:
    if len(marks) < 2:
        return None
    marks = sorted(marks, key=lambda m: m[0])
    ts = np.asarray([m[0] for m in marks], dtype=np.int64)
    eq = np.asarray([m[1] for m in marks], dtype=float)
    if np.any(eq <= 0):
        return None

    rets = eq[1:] / eq[:-1] - 1.0
    peak = np.maximum.accumulate(eq)
    underwater = eq / peak - 1.0
    mdd_ratio = float(-underwater.min())

    episodes = _drawdown_episodes(ts, underwater)
    mdd_duration = max(((ts[e] - ts[p]) / 1e9 for p, t, e, ok in episodes), default=0.0)
    worst = min(episodes, key=lambda ep: underwater[ep[1]], default=None)
    recovery_duration = (
        (ts[worst[2]] - ts[worst[1]]) / 1e9 if worst is not None and worst[3] else None
    )

    span_ns = float(ts[-1] - ts[0])
    years = span_ns / NS_PER_YEAR
    annualized_return = (
        (float(eq[-1]) / initial_capital) ** (1.0 / years) - 1.0
        if years > 0 and initial_capital > 0 else None
    )
    calmar = (
        annualized_return / mdd_ratio
        if mdd_ratio > 0 and annualized_return is not None and annualized_return > 0
        else None
    )

    downside = np.minimum(rets, 0.0)
    down_std = float(np.sqrt(np.mean(downside**2)))
    sortino = None if down_std == 0 else float(rets.mean() / down_std)

    p95, p5 = float(np.percentile(rets, 95)), float(np.percentile(rets, 5))
    tail_ratio = None if p5 == 0 else abs(p95) / abs(p5)

    return {
        "sortino": sortino,
        "calmar": calmar,
        "mdd_ratio": mdd_ratio,
        "mdd_duration": mdd_duration,
        "recovery_duration": recovery_duration,
        "underwater_mean": float(underwater.mean()),
        "tail_ratio": tail_ratio,
        "annualized_return": annualized_return,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_research/test_metrics.py -v`
Expected: 8 PASS (Task 1 포함)

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/research/metrics.py tests/test_research/test_metrics.py
git commit -m "feat(research): add equity-mark robustness metrics"
```

---

### Task 3: executor equity 마크 수집

**Files:**
- Modify: `src/sngw_trader/research/executor.py:1-85`
- Test: `tests/test_research/test_executor.py` (아래 테스트 추가)

**Interfaces:**
- Consumes: nautilus `cache.account_for_venue(Venue)` + `Account.events()` (검증 완료 — nautilus_trader 1.231.0에서 `CashAccount.events`, `balances`, `ts_init` 존재 확인함)
- Produces:
  - `RunResult` 신규 필드 `equity_marks: list[tuple[int, float]]` (default `field(default_factory=list)`)
  - `extract_equity_marks(cache, venue: str, window_start_ns: int) -> list[tuple[int, float]]` — ts 오름차순, warmup 제외, 계좌/이벤트 부재 시 `[]`
  - `run_window` 반환값의 `equity_marks`가 채워짐 (기존 필드 변화 없음)

- [ ] **Step 1: Write the failing test**

`tests/test_research/test_executor.py`에 추가:

```python
from sngw_trader.research.executor import extract_equity_marks


class _Bal:
    def __init__(self, v: float):
        self.total = _Money(v)


class _Ev:
    def __init__(self, ts: int, bal: float):
        self.ts_init = ts
        self.balances = {"USDT": _Bal(bal)}


class _Acc:
    def __init__(self, events):
        self._events = events

    def events(self):
        return self._events


class _Cache:
    def __init__(self, account):
        self._account = account

    def account_for_venue(self, venue):
        return self._account


def test_extract_equity_marks_filters_warmup_and_sorts():
    acc = _Acc([_Ev(200, 10050.0), _Ev(50, 10000.0), _Ev(150, 9800.0)])
    marks = extract_equity_marks(_Cache(acc), "OKX", window_start_ns=100)
    assert marks == [(150, 9800.0), (200, 10050.0)]


def test_extract_equity_marks_no_account_or_events():
    assert extract_equity_marks(_Cache(None), "OKX", 0) == []
    assert extract_equity_marks(_Cache(_Acc([])), "OKX", 0) == []
    assert extract_equity_marks(_Cache(_Acc([_Ev(50, 100.0)])), "OKX", 100) == []


def test_run_result_default_equity_marks():
    r = RunResult([1.0], [0.01], 1, 1.0)
    assert r.equity_marks == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_research/test_executor.py -v`
Expected: FAIL with `ImportError: cannot import name 'extract_equity_marks'`

- [ ] **Step 3: Write minimal implementation**

`executor.py` 수정 — import와 dataclass 필드, 신규 함수, `run_window` 반환부:

```python
from dataclasses import dataclass, field, replace
from nautilus_trader.model.identifiers import Venue


@dataclass(frozen=True)
class RunResult:
    trade_pnls: list[float]
    trade_returns: list[float]
    n_trades: int
    total_pnl: float
    equity_marks: list[tuple[int, float]] = field(default_factory=list)


def extract_equity_marks(cache, venue: str, window_start_ns: int) -> list[tuple[int, float]]:
    """(ts_ns, balance total) marks from account state events at/after window start."""
    account = cache.account_for_venue(Venue(venue))
    if account is None:
        return []
    marks: list[tuple[int, float]] = []
    for ev in account.events():
        if ev.ts_init < window_start_ns or not ev.balances:
            continue
        # ponytail: 단일 통화 계좌 전제. 다중 통화면 최대 잔고 1개만 사용.
        total = max(b.total.as_double() for b in ev.balances.values())
        marks.append((ev.ts_init, total))
    marks.sort(key=lambda m: m[0])
    return marks
```

`run_window`의 `try` 블록 반환부를 교체:

```python
    try:
        node.run()
        window_start_ns = dt_to_unix_nanos(start)
        result = extract_run_result(engine.cache.positions(), window_start_ns)
        marks = extract_equity_marks(
            engine.cache, instrument_id.rsplit(".", 1)[-1], window_start_ns
        )
        return replace(result, equity_marks=marks)
    finally:
        node.dispose()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_research/test_executor.py -v`
Expected: PASS (기존 테스트 포함 — `RunResult` 기본값 덕에 위치 인자 생성 호환)

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/research/executor.py tests/test_research/test_executor.py
git commit -m "feat(research): collect account balance equity marks per run"
```

---

### Task 4: walk_forward 지표 결합 + robustness 집계 (순수 헬퍼)

**Files:**
- Modify: `src/sngw_trader/research/walk_forward.py` (아래 3개 헬퍼 함수 추가 — `main()` 결합은 Task 5)
- Test: `tests/test_research/test_walk_forward.py` (아래 테스트 추가)

**Interfaces:**
- Consumes: `compute_trade_metrics`, `compute_equity_metrics` (Task 1·2), `RunResult.equity_marks` (Task 3), `sharpe_from_trades` (기존 `select.py`)
- Produces:
  - `run_metrics(result: RunResult, initial_capital: float) -> dict`
    - `{"trade": dict|None, "equity": dict|None}`
  - `oos_is_sharpe_ratio(is_sharpe: float, oos_result: RunResult) -> float | None`
    - `is_sharpe <= 0`이면 `None` (spec §4.3). 아니면 `sharpe_from_trades(oos.trade_returns) / is_sharpe`.
  - `robustness_summary(wf_windows: list[dict]) -> dict`
    - `oos_is_sharpe_ratios`: None 아닌 비율 리스트
    - `n_excluded_ratio_windows`: 비율 None 윈도우 수
    - `oos_sortino` / `oos_calmar`: `{"mean": float|None, "n_excluded": int}`
    - `oos_mdd_ratio`: `{"mean": float|None, "max": float|None, "n_excluded": int}` (max = 최악)
    - 입력 윈도우 dict 키: `"oos_is_sharpe_ratio"`, `"oos_metrics"` (`{"trade":..., "equity":...}` 또는 None)

- [ ] **Step 1: Write the failing test**

`tests/test_research/test_walk_forward.py`에 추가 (상단 import는 파일 기존 import 뒤에):

```python
import pytest

from sngw_trader.research.select import sharpe_from_trades
from sngw_trader.research.walk_forward import (
    oos_is_sharpe_ratio,
    robustness_summary,
    run_metrics,
)

NS = 1_000_000_000


def test_oos_is_sharpe_ratio_nonpositive_is_sharpe_is_none():
    oos = RunResult([1.0, -1.0], [0.01, -0.01], 2, 0.0)
    assert oos_is_sharpe_ratio(0.0, oos) is None
    assert oos_is_sharpe_ratio(-1.0, oos) is None


def test_oos_is_sharpe_ratio_value():
    oos = RunResult([1.0, 1.0, -1.0], [0.01, 0.01, -0.01], 3, 1.0)
    r = oos_is_sharpe_ratio(1.0, oos)
    assert r == pytest.approx(sharpe_from_trades(oos.trade_returns))


def test_run_metrics_none_when_no_trades_and_marks():
    r = run_metrics(RunResult([], [], 0, 0.0), 10000.0)
    assert r == {"trade": None, "equity": None}


def test_run_metrics_populates_both():
    result = RunResult(
        [10.0, -5.0], [0.01, -0.005], 2, 5.0,
        equity_marks=[(0, 10000.0), (NS, 10010.0), (2 * NS, 10005.0)],
    )
    r = run_metrics(result, 10000.0)
    assert r["trade"]["win_rate"] == pytest.approx(0.5)
    assert r["equity"]["mdd_ratio"] == pytest.approx(0.000499, rel=1e-3)


def test_robustness_summary_aggregates_and_excludes():
    windows = [
        {"oos_is_sharpe_ratio": 1.0,
         "oos_metrics": {"trade": None,
                         "equity": {"sortino": 0.2, "calmar": None, "mdd_ratio": 0.1}}},
        {"oos_is_sharpe_ratio": None,
         "oos_metrics": {"trade": None,
                         "equity": {"sortino": None, "calmar": 1.5, "mdd_ratio": 0.3}}},
        {"oos_is_sharpe_ratio": None, "oos_metrics": None},
    ]
    r = robustness_summary(windows)
    assert r["oos_is_sharpe_ratios"] == [1.0]
    assert r["n_excluded_ratio_windows"] == 2
    assert r["oos_sortino"] == {"mean": pytest.approx(0.2), "n_excluded": 1}
    assert r["oos_calmar"] == {"mean": pytest.approx(1.5), "n_excluded": 0}
    assert r["oos_mdd_ratio"] == {"mean": pytest.approx(0.2), "max": pytest.approx(0.3), "n_excluded": 0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_research/test_walk_forward.py -v`
Expected: FAIL with `ImportError: cannot import name 'oos_is_sharpe_ratio'`

- [ ] **Step 3: Write minimal implementation**

`walk_forward.py` — import 추가:

```python
from sngw_trader.research.metrics import compute_equity_metrics, compute_trade_metrics
```

`stitch_oos` 아래에 헬퍼 추가:

```python
def run_metrics(result: RunResult, initial_capital: float) -> dict:
    return {
        "trade": compute_trade_metrics(result.trade_pnls, result.trade_returns),
        "equity": compute_equity_metrics(result.equity_marks, initial_capital),
    }


def oos_is_sharpe_ratio(is_sharpe: float, oos_result: RunResult) -> float | None:
    """IS Sharpe <= 0이면 None — 비율이 발산하거나 '과적합 없음'으로 위장한다."""
    if is_sharpe <= 0:
        return None
    return sharpe_from_trades(oos_result.trade_returns) / is_sharpe


def robustness_summary(wf_windows: list[dict]) -> dict:
    ratios = [w["oos_is_sharpe_ratio"] for w in wf_windows
              if w.get("oos_is_sharpe_ratio") is not None]
    eqs = [w["oos_metrics"]["equity"] for w in wf_windows
           if w.get("oos_metrics") and w["oos_metrics"]["equity"]]

    def agg(key: str, with_max: bool) -> dict:
        vals = [m[key] for m in eqs if m.get(key) is not None]
        out: dict = {"mean": sum(vals) / len(vals) if vals else None,
                     "n_excluded": len(eqs) - len(vals)}
        if with_max:
            out["max"] = max(vals) if vals else None
        return out

    return {
        "oos_is_sharpe_ratios": ratios,
        "n_excluded_ratio_windows":
            sum(1 for w in wf_windows if w.get("oos_is_sharpe_ratio") is None),
        "oos_sortino": agg("sortino", with_max=False),
        "oos_calmar": agg("calmar", with_max=False),
        "oos_mdd_ratio": agg("mdd_ratio", with_max=True),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_research/test_walk_forward.py -v`
Expected: PASS (기존 테스트 포함)

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/research/walk_forward.py tests/test_research/test_walk_forward.py
git commit -m "feat(research): add run metrics helpers and robustness aggregation"
```

---

### Task 5: `main()` 결합 + 리포트 확장 + 전체 검증

**Files:**
- Modify: `src/sngw_trader/research/walk_forward.py:147-213` (`main()` 내 OOS/holdout/리포트 조립부)

**Interfaces:**
- Consumes: Task 4 헬퍼 3종, `mc_cfg.initial_capital`
- Produces: 리포트 스키마 확장
  - `wf_report.json` 윈도우 항목 신규 키: `is_metrics`, `oos_metrics`, `oos_is_sharpe_ratio`
  - `summary.json`: `robustness` 블록 (Task 4의 `robustness_summary` 반환값), `holdout.metrics`
  - `mc_report.json`: 변경 없음

- [ ] **Step 1: main() OOS 블록 수정**

`main()`의 OOS 실행 블록(`oos_result = run_window(...)` 이후)을 다음으로 교체:

```python
        oos_result = run_window(
            catalog_path, instrument_id, settings=settings, spec=spec,
            params=_params_for(spec, best),
            start=window.oos_start, end=window.oos_end,
            warmup_days=wf_cfg.warmup_days,
        )
```

그리고 `wf_windows.append({...})` 직전에 계산 후 항목에 추가:

```python
        oos_metrics = run_metrics(oos_result, mc_cfg.initial_capital) if oos_result else None
        is_metrics = run_metrics(grid_results[best], mc_cfg.initial_capital) if best is not None else None
        ratio = (oos_is_sharpe_ratio(sharpes[best], oos_result)
                 if best is not None and oos_result is not None else None)
```

`wf_windows.append` dict에 키 추가:

```python
            "is_metrics": is_metrics,
            "oos_metrics": oos_metrics,
            "oos_is_sharpe_ratio": ratio,
```

- [ ] **Step 2: holdout 지표 추가**

holdout 실행 블록 이후:

```python
        holdout_metrics = run_metrics(holdout, mc_cfg.initial_capital)
```

`summary`의 `"holdout"` 값에 `"metrics": holdout_metrics` 추가 (비율 없음 — holdout에는 IS가 없다, spec §4.3).

- [ ] **Step 3: summary robustness 블록**

`summary = {...}` dict에 추가:

```python
        "robustness": robustness_summary(wf_windows),
```

- [ ] **Step 4: 전체 테스트 실행**

Run: `.venv\Scripts\python.exe -m pytest tests -v`
Expected: 전체 PASS. `main()` 자체는 기존과 동일하게 smoke 테스트 대상이 아니며,
결합부는 Task 4 헬퍼 테스트로 커버된다.

- [ ] **Step 5: 수동 smoke (catalog가 있을 때만, 없으면 건너뜀)**

기존 catalog로 `wf-okx` 실행해 `wf_report.json`에 신규 키가 JSON으로 직렬화되는지 확인
(`None` → `null` 확인). catalog가 없으면 건너뛰고 스킵 사실을 커밋 메시지에 남기지 않는다(생략 가능 단계).

- [ ] **Step 6: Commit**

```bash
git add src/sngw_trader/research/walk_forward.py
git commit -m "feat(research): wire robustness metrics into wf reports"
```
