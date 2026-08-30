# ERROR-Momentum A/B Strategies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Spec A (daily ERMOM regime = position) and Spec B (daily ERMOM regime + 30m EMA20/50 band-reentry entry machine) as NautilusTrader strategies sharing one ERMOM indicator, with a common risk layer (daily ATR stop + volatility kill switch), wired into the existing BacktestNode runner, plus funding-cost post-processing.

**Architecture:** Pure decision logic (aggregator, ERMOM, FSM, risk metrics) lives in `indicators/` and as module-level pure classes/functions inside the strategy modules so every rule is unit-testable without a Nautilus engine. The `Strategy` subclasses are thin adapters: bars in → pure logic → `order_factory.market` orders. Both strategies subscribe the same 1-minute BarType and internally aggregate 30m/daily bars, so backtest and live use the identical class.

**Tech Stack:** Python 3.12, NautilusTrader 1.231.0 (installed via uv), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-30-err-momentum-design.md` (read it first — it defines the A/B rules, risk layer, and acceptance conditions this plan implements)

## Global Constraints

- NautilusTrader 1.231.0, Python 3.12. Run tests with `uv run pytest <path> -v` (never bare `pytest`/`python`). Run all commands from the repo root.
- Strategy files contain ONLY Strategy + StrategyConfig + pure decision logic. No runner imports, no exchange I/O, no `requests`/`httpx`/`websocket-client`, no ccxt, no `while True`.
- InstrumentId always ends `.OKX` (e.g. `BTC-USDT-SWAP.OKX`).
- No secrets in code. No new dependencies (pandas/pyarrow come with nautilus_trader).
- No comments except `# ponytail:` notes and the repo-convention module docstring.
- Commit prefixes: `feat(strategy):`, `feat(runner):`, `feat(indicator):`, `feat(data):`.
- No lookahead: a signal computed from a bar's close must only act as if filled at the NEXT bar's open. A market order submitted inside `on_bar(t)` fills at the next bar's open in Nautilus backtests — this is the enforcement mechanism; do not "fix" it.
- All unit tests run offline. If a Nautilus import path fails, do NOT invent a class; verify with `uv run python -c "..."` as instructed per task.
- Create `tests/test_indicators/__init__.py` and `tests/test_data/__init__.py` (empty) alongside the first test in each directory if pytest rootdir collection needs them.

---

### Task 1: Bar aggregator (1m → 30m / 1D UTC)

**Files:**
- Create: `src/sngw_trader/indicators/bar_aggregator.py`
- Test: `tests/test_indicators/test_bar_aggregator.py`

**Interfaces:**
- Consumes: nothing (pure).
- Produces:
  - `CompletedBar` frozen dataclass: `ts_open_ns: int, ts_close_ns: int, open: float, high: float, low: float, close: float`
  - `BarAggregator(bucket_seconds: int, source_seconds: int = 60)` with `update(ts_close_ns: int, o: float, h: float, l: float, c: float) -> CompletedBar | None`. Emits a `CompletedBar` only when a source bar starts a new bucket, so every emitted bar is fully closed. Final incomplete bucket is never emitted.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_indicators/test_bar_aggregator.py
from sngw_trader.indicators.bar_aggregator import BarAggregator

M = 60 * 1_000_000_000


def _feed(agg, open_minute, o, h, l, c):
    """A 1m bar that OPENS at open_minute closes at open_minute+1."""
    return agg.update((open_minute + 1) * 60_000_000_000, o, h, l, c)


def test_30m_merges_and_flushes():
    agg = BarAggregator(bucket_seconds=1800)
    assert _feed(agg, 0, 100, 101, 99, 100.5) is None
    assert _feed(agg, 1, 101, 102, 99.5, 101) is None
    done = _feed(agg, 30, 101, 102, 101, 101.5)  # opens new 30m bucket
    assert done is not None
    assert done.open == 100
    assert done.high == 102
    assert done.low == 99
    assert done.close == 101
    assert done.ts_open_ns == 0
    assert done.ts_close_ns == 30 * 60_000_000_000  # bucket-end boundary


def test_daily_boundary_utc():
    agg = BarAggregator(bucket_seconds=86400)
    m0 = 23 * 60  # minute 1380 of day 0 -> opens 23:00, closes 23:01
    for m in (m0 := 23 * 60, m0 + 1, m0 + 59):
        assert _feed(agg, m, 100, 101, 99, 100.5) is None
    done = _feed(agg, m0 + 60, 100.5, 102, 100, 101)  # first bar of next day
    assert done is not None
    assert done.ts_open_ns == m0 * M
    assert done.ts_close_ns == (m0 + 60) * 60_000_000_000
    assert done.close == 100.5


def test_incomplete_bucket_never_emitted():
    agg = BarAggregator(bucket_seconds=86400)
    assert _feed(agg, 0, 1, 2, 0.5, 1.5) is None
    assert _feed(agg, 1, 1.5, 2, 1, 1.2) is None
    assert _feed(agg, 2, 1.5, 2, 1, 1.2) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_indicators/test_bar_aggregator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sngw_trader.indicators.bar_aggregator'`

- [ ] **Step 3: Implement**

```python
"""Aggregate 1-minute source bars into 30m or daily (UTC) buckets.

Pure logic, no Nautilus imports, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

NS = 1_000_000_000


@dataclass(frozen=True)
class CompletedBar:
    ts_open_ns: int
    ts_close_ns: int
    open: float
    high: float
    low: float
    close: float


# ponytail: drops the final incomplete bucket; acceptable for daily/30m aggregation
class BarAggregator:
    def __init__(self, bucket_seconds: int, source_seconds: int = 60) -> None:
        self._bucket_ns = bucket_seconds * NS
        self._source_ns = source_seconds * NS
        self._bucket = -1
        self._o = self._h = self._l = self._c = 0.0
        self._ts_close = 0

    def update(self, ts_close_ns: int, o: float, h: float, l: float, c: float) -> CompletedBar | None:
        ts_open = ts_close_ns - self._source_ns
        bucket = ts_open // self._bucket_ns
        if self._bucket < 0:
            self._bucket = bucket
            self._o, self._h, self._l, self._c = o, h, l, c
            self._ts_open = ts_open
            self._ts_close = (bucket + 1) * self._bucket_ns
            return None
        if bucket == self._bucket:
            self._h = max(self._h, h)
            self._l = min(self._l, l)
            self._c = c
            return None
        done = CompletedBar(
            ts_open_ns=self._ts_open,
            ts_close_ns=self._ts_close,
            open=self._o,
            high=self._h,
            low=self._l,
            close=self._c,
        )
        self._bucket = bucket
        self._o, self._h, self._l, self._c = o, h, l, c
        self._ts_open = ts_open
        self._ts_close = (bucket + 1) * self._bucket_ns
        return done
```

Add `self._ts_open = 0` to `__init__`.

UTC day buckets are correct automatically: epoch-ns open time // 86_400s gives the UTC day index, so no timezone handling is needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_indicators/test_bar_aggregator.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/indicators/bar_aggregator.py tests/test_indicators/test_bar_aggregator.py
git commit -m "feat(indicator): 1m->30m/1D bar aggregator"
```

---

### Task 2: Streaming ERMOM indicator

**Files:**
- Create: `src/sngw_trader/indicators/err_momentum.py`
- Test: `tests/test_indicators/test_err_momentum.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `ErrorAdjustedMomentum(w_f: int = 10, w_e: int = 10, momentum_window: int = 200)` with `update(daily_close: float) -> float | None`
  - `regime_target(ermom: float | None, theta: float = 0.0) -> int` — the shared regime mapping used by BOTH strategies (spec 2.3)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_indicators/test_err_momentum.py
"""Golden tests: streaming ERMOM must match a naive full-array reference."""

import random

from sngw_trader.indicators.err_momentum import ErrorAdjustedMomentum, regime_target


def _reference_ermom(closes: list[float], w_f: int, w_e: int, l: int) -> list[float | None]:
    """Naive batch implementation of spec 2.2, written independently."""
    n = len(closes)
    r: list[float | None] = [None] * n
    for t in range(1, n):
        r[t] = closes[t] / closes[t - 1] - 1
    f: list[float | None] = [None] * n
    for t in range(n):
        if t >= w_f:
            f[t] = sum(r[t - w_f + 1 : t + 1]) / w_f
    adj: list[float | None] = [None] * n
    for t in range(1, n):
        if t >= w_f + 1:
            window = []
            for k in range(t - w_e + 1, t + 1):
                if f[k - 1] is not None and r[k] is not None:
                    window.append(abs(r[k] - f[k - 1]))
            if len(window) == w_e:
                mae = sum(window) / w_e
                if mae > 0:
                    adj[t] = r[t] / mae
    out: list[float | None] = []
    for t in range(n):
        prior = [a for a in adj[: t + 1] if a is not None]
        if len(prior) >= l and t + 1 >= w_f + w_e + l:
            out.append(sum(prior[-l:]) / l)
        else:
            out.append(None)
    return out


def test_matches_reference_on_random_walk():
    random.seed(7)
    closes = [100.0]
    for _ in range(400):
        closes.append(closes[-1] * (1 + random.gauss(0, 0.02)))
    ref = _reference_ermom(closes, w_f=3, w_e=3, l=10)
    ind = ErrorAdjustedMomentum(w_f=3, w_e=3, momentum_window=10)
    got = [ind.update(c) for c in closes]
    for t, (a, b) in enumerate(zip(got, ref)):
        assert a == b, f"day {t}: streaming={a} reference={b}"


def test_one_bar_lag_hand_computed():
    # w_f=1 -> f_t = r_t, e_t = r_t - r_{t-1} (1-bar lag), MAE=|e|, l=1
    ind = ErrorAdjustedMomentum(w_f=1, w_e=1, momentum_window=1)
    c0, c1, c2 = 100.0, 101.0, 100.5
    assert ind.update(c0) is None
    assert ind.update(c1) is None
    r2 = c2 / c1 - 1
    e2 = r2 - (c1 / c0 - 1)
    assert ind.update(c2) == r2 / abs(e2)


def test_zero_mae_stays_invalid():
    ind = ErrorAdjustedMomentum(w_f=1, w_e=2, momentum_window=2)
    for _ in range(50):
        assert ind.update(100.0) is None


def test_warmup_respected_with_defaults():
    ind = ErrorAdjustedMomentum()  # 10/10/200 -> warm-up 220
    for i in range(219):
        assert ind.update(100.0 + i * 0.01) is None
    values = [ind.update(100.0 + i * 0.01) for i in range(219, 240)]
    # call 220 (i=219) is the first valid ERMOM (warm-up = L+W_f+W_e = 220)
    assert all(v is not None for v in values)


def test_regime_target():
    assert regime_target(None) == 0
    assert regime_target(0.5) == 1
    assert regime_target(-0.5) == -1
    assert regime_target(0.0) == 0
    assert regime_target(0.05, theta=0.1) == 0
    assert regime_target(0.2, theta=0.1) == 1
    assert regime_target(-0.2, theta=0.1) == -1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_indicators/test_err_momentum.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
"""Streaming ERROR-adjusted momentum (Varadi 2014).

Inputs are DAILY closes only. One indicator instance serves both Spec A
and Spec B so the two strategies share an identical ERMOM series.
Pure logic, no Nautilus imports.
"""

from __future__ import annotations

from collections import deque


def regime_target(ermom: float | None, theta: float = 0.0) -> int:
    if ermom is None:
        return 0
    if ermom > theta:
        return 1
    if ermom < -theta:
        return -1
    return 0


class ErrorAdjustedMomentum:
    def __init__(self, w_f: int = 10, w_e: int = 10, momentum_window: int = 200) -> None:
        self._wf = w_f
        self._we = w_e
        self._l = momentum_window
        self._prev_close: float | None = None
        self._returns: deque[float] = deque(maxlen=w_f)
        self._abs_err: deque[float] = deque(maxlen=w_e)
        self._adj_r: deque[float] = deque(maxlen=momentum_window)
        self._n_closes = 0

    @property
    def warmup_bars(self) -> int:
        return self._l + self._wf + self._we

    @property
    def value(self) -> float | None:
        """Last computed ERMOM (or None). Read by strategies for regime."""
        return self._value

    def update(self, daily_close: float) -> float | None:
        if self._prev_close is None:
            self._prev_close = daily_close
            self._n_closes = 1
            self._value = None
            return None
        r = daily_close / self._prev_close - 1
        self._prev_close = daily_close
        self._n_closes += 1

        e: float | None = None
        if len(self._returns) == self._wf:
            e = r - sum(self._returns) / self._wf  # e_t = r_t - f_{t-1}, 1-bar lag
        self._returns.append(r)

        if e is not None:
            self._abs_err.append(abs(e))
            if len(self._abs_err) == self._we:
                mae = sum(self._abs_err) / self._we
                if mae > 0:
                    self._adj_r.append(r / mae)

        if len(self._adj_r) == self._l and self._n_closes >= self._l + self._wf + self._we:
            self._value = sum(self._adj_r) / self._l
        else:
            self._value = None
        return self._value
```

Add `self._value: float | None = None` to `__init__` (before the other fields).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_indicators/test_err_momentum.py -v`
Expected: PASS. If `test_matches_reference_on_random_walk` disagrees, fix the implementation (not the reference).

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/indicators/err_momentum.py tests/test_indicators/test_err_momentum.py
git commit -m "feat(indicator): streaming error-adjusted momentum"
```

---

### Task 3: Risk metrics — EMA, daily ATR (Wilder), realized vol, stop helpers

**Files:**
- Create: `src/sngw_trader/indicators/risk_metrics.py`
- Test: `tests/test_indicators/test_risk_metrics.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Ema(span: int).update(value: float) -> float | None` — SMA seed over first `span` values, then `alpha = 2/(span+1)`; valid from the `span`-th update
  - `DailyAtr(period: int = 14).update(o, h, l, c) -> float | None` — Wilder; seed = mean of first `period` TRs (first TR = h−l with no prev close); valid from the `period`-th update; then `atr = (atr*(period-1) + tr)/period`
  - `RealizedVol(lookback: int = 20, periods_per_year: int = 365).update(daily_close) -> float | None` — sample std of the last `lookback` simple returns × √365; valid after `lookback + 1` closes
  - `stop_price(side: int, ref_price: float, atr: float, mult: float) -> float` — long: `ref − mult·atr`, short: `ref + mult·atr`
  - `is_stop_hit(side: int, price: float, stop: float | None) -> bool` — side>0 hit when price<stop; side<0 when price>stop; False when side==0 or stop is None

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_indicators/test_risk_metrics.py
import math

from sngw_trader.indicators.risk_metrics import (
    DailyAtr,
    Ema,
    RealizedVol,
    is_stop_hit,
    stop_price,
)


def test_ema_valid_after_span_and_smooths():
    ema = Ema(span=3)
    assert ema.update(1.0) is None
    assert ema.update(2.0) is None
    assert ema.update(3.0) == 2.0  # SMA seed
    v = ema.update(4.0)
    assert math.isclose(v, 2.0 + (2 / 4) * (4.0 - 2.0))  # alpha = 2/(3+1)


def test_atr_seed_then_wilder():
    atr = DailyAtr(period=3)
    # TRs: (h-l)=2 ; (h-l=4, |h-pc|=1, |l-pc|=2 -> 3) ; (h-l=2, |h-pc|=1, |l-pc|=1 -> 2)
    assert atr.update(10, 11, 9, 9.5) is None
    assert atr.update(9, 12, 9, 9.0) is None
    v3 = atr.update(9, 10, 9, 9.0)
    assert math.isclose(v3, (2.0 + 3.0 + 1.0) / 3)  # seed at 3rd TR
    tr4 = max(11 - 9, abs(11 - 9), abs(9 - 9))  # bar4: h=11,l=9,pc=9 -> 2.0
    v4 = atr.update(9, 11, 9, 10.5)
    assert math.isclose(v4, (v3 * 2 + tr4) / 3)  # Wilder with period=3


def test_atr_constant_range():
    atr = DailyAtr(period=5)
    v = None
    for _ in range(10):
        v = atr.update(100, 101, 99, 100)
    assert v is not None
    assert math.isclose(v, 2.0)


def test_realized_vol_zero_for_flat():
    vol = RealizedVol(lookback=5)
    result = None
    for _ in range(30):
        result = vol.update(100.0)
    assert result == 0.0


def test_realized_vol_valid_after_warmup():
    vol = RealizedVol(lookback=5)
    for i in range(6):
        v = vol.update(100.0 + i)
    assert v is not None and v > 0


def test_stop_helpers():
    assert stop_price(+1, ref_price=100.0, atr=2.0, mult=3.0) == 94.0
    assert stop_price(-1, ref_price=100.0, atr=2.0, mult=3.0) == 106.0
    assert is_stop_hit(+1, price=93.0, stop=100.0) is True
    assert is_stop_hit(+1, price=101.0, stop=99.0) is False
    assert is_stop_hit(-1, price=115.0, stop=110.0) is True  # short hits when price > stop
    assert is_stop_hit(-1, price=100.0, stop=110.0) is False
    assert is_stop_hit(0, price=50.0, stop=60.0) is False
    assert is_stop_hit(1, price=50.0, stop=None) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_indicators/test_risk_metrics.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
"""Pure daily-bar risk math shared by both error-momentum strategies.

Streaming, one update per completed bar, valid values only after warm-up.
"""

from __future__ import annotations

import math
from collections import deque


class Ema:
    """Span-based EMA seeded with the SMA of the first `span` values."""

    def __init__(self, span: int) -> None:
        self._span = span
        self._seed: list[float] = []
        self._value: float | None = None

    @property
    def value(self) -> float | None:
        return self._value

    def update(self, value: float) -> float | None:
        if self._value is None:
            self._seed.append(value)
            if len(self._seed) == self._span:
                self._value = sum(self._seed) / self._span
            return self._value
        self._value += (2.0 / (self._span + 1)) * (value - self._value)
        return self._value


class DailyAtr:
    """Wilder ATR. Valid after `period` updates (seed = mean of first TRs)."""

    def __init__(self, period: int = 14) -> None:
        self._period = period
        self._prev_close: float | None = None
        self._seed: list[float] = []
        self._atr: float | None = None

    @property
    def value(self) -> float | None:
        return self._atr

    def update(self, o: float, h: float, l: float, c: float) -> float | None:
        if self._prev_close is None:
            tr = h - l
        else:
            pc = self._prev_close
            tr = max(h - l, abs(h - pc), abs(l - pc))
        self._prev_close = c
        if self._atr is None:
            self._seed.append(tr)
            if len(self._seed) == self._period:
                self._atr = sum(self._seed) / self._period
            return self._atr
        self._atr = (self._atr * (self._period - 1) + tr) / self._period
        return self._atr


class RealizedVol:
    """Annualized sample std of the last `lookback` daily simple returns."""

    def __init__(self, lookback: int = 20, periods_per_year: int = 365) -> None:
        self._lookback = lookback
        self._ppy = periods_per_year
        self._prev: float | None = None
        self._returns: list[float] = []

    @property
    def value(self) -> float | None:
        if len(self._returns) < self._lookback:
            return None
        mean = sum(self._returns) / self._lookback
        var = sum((r - mean) ** 2 for r in self._returns) / (self._lookback - 1)
        return math.sqrt(var) * math.sqrt(self._ppy)

    def update(self, close: float) -> float | None:
        if self._prev is not None:
            self._returns.append(close / self._prev - 1)
        self._prev = close
        return self.value


def stop_price(side: int, ref_price: float, atr: float, mult: float) -> float:
    return ref_price - mult * atr if side > 0 else ref_price + mult * atr


def is_stop_hit(side: int, price: float, stop: float | None) -> bool:
    if stop is None or side == 0:
        return False
    return price < stop if side > 0 else price > stop
```

(Adjust the `RealizedVol` class to keep `self._returns` as a bounded list: append then, if longer than `lookback`, pop from the front — or use `deque(maxlen=lookback)`; both fine, pick one.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_indicators/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/indicators/risk_metrics.py tests/test_indicators/test_risk_metrics.py
git commit -m "feat(indicator): EMA, daily ATR (Wilder), realized vol, stop helpers"
```

---

### Task 4: Spec A strategy — `err_momentum_regime.py`

**Files:**
- Create: `src/sngw_trader/strategies/err_momentum_regime.py`
- Test: `tests/test_strategies/test_err_mom_regime.py`

**Interfaces:**
- Consumes: `BarAggregator`, `ErrorAdjustedMomentum`, `regime_target`, `DailyAtr`, `RealizedVol`, `is_stop_hit`, `stop_price`.
- Produces: `ErrMomentumRegimeConfig`, `ErrMomentumRegime` (Strategy), pure helper `apply_entry_block(current: int, target: int, entry_blocked: bool) -> int`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_strategies/test_err_mom_regime.py
from decimal import Decimal

from nautilus_trader.model import BarType, InstrumentId

from sngw_trader.strategies.err_momentum_regime import (
    ErrMomentumRegime,
    ErrMomentumRegimeConfig,
    apply_entry_block,
)


def test_entry_block_rules():
    assert apply_entry_block(0, 1, True) == 0  # flat + blocked -> stay flat
    assert apply_entry_block(1, 1, True) == 1  # long held, target long -> hold
    assert apply_entry_block(1, -1, True) == 0  # flip blocked -> close only
    assert apply_entry_block(-1, 1, True) == 0
    assert apply_entry_block(1, -1, False) == -1  # not blocked -> normal
    assert apply_entry_block(0, -1, False) == -1
    assert apply_entry_block(1, 0, False) == 0


def test_config_builds():
    config = ErrMomentumRegimeConfig(
        instrument_id=InstrumentId.from_str("BTC-USDT-SWAP.OKX"),
        bar_type=BarType.from_str("BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL"),
        trade_size=Decimal("0.01"),
    )
    strategy = ErrMomentumRegime(config=config)
    assert strategy.config.w_f == 10
    assert strategy.config.momentum_window == 200
    assert strategy.config.instrument_id.value.endswith(".OKX")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_strategies/test_err_mom_regime.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3a: Verify the OrderFilled import path**

Run: `uv run python -c "from nautilus_trader.model.events import OrderFilled; print(OrderFilled)"`
Expected: prints the class. If it fails, find the right path: `uv run python -c "import nautilus_trader.model as m; print([x for x in dir(m) if 'Fill' in x])"` and adjust. If the symbol cannot be located, simplify: drop `on_event` and set the stop at submission time using the current daily bar's close as the entry proxy (`# ponytail: entry-proxy stop; move to fill-based if fills drift from signal close`).

- [ ] **Step 3b: Implement**

```python
"""Spec A: daily ERMOM regime IS the position.

Strategy logic only. Do not import TradingNode or OKX factories here.
"""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model import Bar, BarType, InstrumentId
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.trading import Strategy

from sngw_trader.indicators.bar_aggregator import BarAggregator, CompletedBar
from sngw_trader.indicators.err_momentum import ErrorAdjustedMomentum, regime_target
from sngw_trader.indicators.risk_metrics import (
    DailyAtr,
    RealizedVol,
    is_stop_hit,
    stop_price,
)


class ErrMomentumRegimeConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    w_f: int = 10
    w_e: int = 10
    momentum_window: int = 200
    theta: float = 0.0
    risk_stop_enabled: bool = True
    atr_period: int = 14
    atr_mult: float = 3.0
    vol_filter_enabled: bool = True
    vol_lookback: int = 20
    vol_threshold: float = 0.80
    close_positions_on_stop: bool = True


def apply_entry_block(current: int, target: int, entry_blocked: bool) -> int:
    """Kill switch blocks NEW positions only: flip -> close to flat, hold stays."""
    if not entry_blocked:
        return target
    if current == 0:
        return 0
    if target == 0 or (target > 0) != (current > 0):
        return 0
    return current


class ErrMomentumRegime(Strategy):
    def __init__(self, config: ErrMomentumRegimeConfig) -> None:
        super().__init__(config)
        self._daily = BarAggregator(86_400)
        self._ermom = ErrorAdjustedMomentum(config.w_f, config.w_e, config.momentum_window)
        self._atr = DailyAtr(config.atr_period)
        self._vol = RealizedVol(config.vol_lookback)
        self._stop: float | None = None
        self._pending_atr: float | None = None

    def on_start(self) -> None:
        self.subscribe_bars(self.config.bar_type)

    def on_stop(self) -> None:
        if self.config.close_positions_on_stop:
            self.close_all_positions(self.config.instrument_id)

    def on_bar(self, bar: Bar) -> None:
        daily = self._daily.update(
            int(bar.ts_init),
            bar.open.as_double(),
            bar.high.as_double(),
            bar.low.as_double(),
            bar.close.as_double(),
        )
        if daily is None:
            return
        self._ermom.update(daily.close)
        self._atr.update(daily.open, daily.high, daily.low, daily.close)
        self._vol.update(daily.close)
        self._on_daily(daily)

    def on_event(self, event) -> None:
        if not isinstance(event, OrderFilled) or event.instrument_id != self.config.instrument_id:
            return
        side = self._current_side()
        if side == 0:
            self._stop = None
        elif self._pending_atr is not None:
            self._stop = stop_price(side, event.last_px.as_double(), self._pending_atr, self.config.atr_mult)

    def _current_side(self) -> int:
        if self.portfolio.is_net_long(self.config.instrument_id):
            return 1
        if self.portfolio.is_net_short(self.config.instrument_id):
            return -1
        return 0

    def _check_stop(self, price: float) -> bool:
        if self.config.risk_stop_enabled and is_stop_hit(self._current_side(), price, self._stop):
            self.close_all_positions(self.config.instrument_id)
            self._stop = None
            return True
        return False

    def _on_daily(self, daily: CompletedBar) -> None:
        if self._current_side() != 0 and self._check_stop(daily.close):
            return  # re-entry next daily bar via normal regime rule
        entry_blocked = (
            self.config.vol_filter_enabled
            and self._vol.value is not None
            and self._vol.value > self.config.vol_threshold
        )
        self._current_side()
        target = apply_entry_block(current, regime_target(self._ermom.value, self.config.theta), entry_blocked)
        if current == target:
            return
        if current != 0:
            self.close_all_positions(self.config.instrument_id)
            self._stop = None
        if target != 0:
            self._submit(OrderSide.BUY if target > 0 else OrderSide.SELL)

    def _submit(self, side: OrderSide) -> None:
        instrument = self.cache.instrument(self.config.instrument_id)
        if instrument is None:
            self.log.error(f"Instrument not in cache: {self.config.instrument_id}")
            return
        self._pending_atr = self._atr.value
        self.submit_order(
            self.order_factory.market(
                self.config.instrument_id,
                side,
                instrument.make_qty(self.config.trade_size),
            )
        )
```

Fix the `_on_daily` draft: `self._ermom.update()` is already called in `on_bar`; remove the duplicate line and just read `self._ermom.value` (add a `value` property to `ErrorAdjustedMomentum` returning the last computed ERMOM or None — one-line property). Also `is_stop_hit(side, price, stop)` takes the ABSOLUTE stop price (see Task 3), and `stop_price(side, ref, atr, mult)` computes `ref ∓ mult·atr`, so `self._stop` stored at fill is absolute and `_check_stop(price)` compares directly.

The duplicate-ERMOM-update rule for both strategies: **call `self._ermom.update(daily.close)` exactly once per completed daily bar** (inside `on_bar`/`_on_daily`, not both).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_strategies/test_err_mom_regime.py tests/test_strategies/test_no_exchange_io.py -v`
Expected: PASS (including the no-exchange-IO guard over the new strategy file).

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/strategies/err_momentum_regime.py tests/test_strategies/test_err_mom_regime.py
git commit -m "feat(strategy): spec A err-momentum regime strategy"
```

---

### Task 5: Spec B strategy — ERMOM regime + 30m EMA band entry

**Files:**
- Create: `src/sngw_trader/strategies/err_mom_ema30_entry.py`
- Test: `tests/test_strategies/test_err_mom_ema30_entry.py`

**Interfaces:**
- Consumes: same indicators as Task 4 plus `Ema`.
- Produces:
  - `RibbonEntryMachine(direction: int, n_pull: int)` — pure mirror FSM; `update(o, h, l, c, ema_fast, ema_slow, regime_allows) -> bool` (True on the trigger bar); `state` in `{"IDLE","EXT","PULL","TRIG"}`; `reset()`.
  - `should_exit(side: int, regime: int, close: float, ema_fast: float, ema_slow: float) -> bool`
  - `ErrMomEma30EntryConfig` / `ErrMomEma30Entry`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_strategies/test_err_mom_ema30_entry.py
from decimal import Decimal

from nautilus_trader.model import BarType, InstrumentId

from sngw_trader.strategies.err_mom_ema30_entry import (
    ErrMomEma30Entry,
    ErrMomEma30EntryConfig,
    RibbonEntryMachine,
    should_exit,
)

EF, ES = 105.0, 100.0


def test_long_fsm_full_path():
    m = RibbonEntryMachine(direction=1, n_pull=3)
    assert m.update(o=106, h=111, l=105, c=110, ema_fast=EF, ema_slow=ES, regime_allows=True) is False
    assert m.state == "EXT"
    assert m.update(o=110, h=110, l=104, c=108, ema_fast=EF, ema_slow=ES, regime_allows=True) is False
    assert m.state == "PULL"
    assert m.update(o=106, h=110, l=104.5, c=106, ema_fast=EF, ema_slow=ES, regime_allows=True) is True
    assert m.state == "TRIG"


def test_first_bar_in_ext_cannot_touch_into_pull():
    m = RibbonEntryMachine(direction=1, n_pull=3)
    assert m.update(o=106, h=111, l=105, c=110, ema_fast=EF, ema_slow=ES, regime_allows=True) is False
    # wick touches band on the same bar EXT was entered -> not PULL yet
    # (IDLE->EXT consumed this bar's single transition)
    assert m.update(o=104, h=105, l=104, c=104, ema_fast=105, ema_slow=100, regime_allows=True) is False
    assert m.state == "PULL"


def test_band_start_without_ext_never_triggers():
    m = RibbonEntryMachine(direction=1, n_pull=3)
    # closes stay BELOW ema_fast throughout -> never arms EXT -> never triggers
    for c in (104.0, 104.5, 104.0, 103.0):
        assert m.update(o=104, h=107, l=103, c=c, ema_fast=105, ema_slow=100, regime_allows=True) is False
    assert m.state == "IDLE"


def test_regime_loss_resets_setup():
    m = RibbonEntryMachine(direction=1, n_pull=3)
    m.update(o=106, h=111, l=105, c=110, ema_fast=105, ema_slow=100, regime_allows=True)
    assert m.state == "EXT"
    m.update(o=106, h=111, l=104, c=106, ema_fast=105, ema_slow=100, regime_allows=False)
    assert m.state == "IDLE"


def test_pull_timeout_resets():
    m = RibbonEntryMachine(direction=1, n_pull=1)
    assert m.update(o=106, h=111, l=105, c=110, ema_fast=105, ema_slow=100, regime_allows=True) is False
    assert m.update(o=104, h=105, l=102, c=104, ema_fast=105, ema_slow=100, regime_allows=True) is False
    assert m.state == "PULL"
    assert m.update(o=104, h=105, l=103, c=104.5, ema_fast=105, ema_slow=100, regime_allows=True) is False
    assert m.state == "IDLE"


def test_ribbon_break_resets():
    m = RibbonEntryMachine(direction=1, n_pull=3)
    m.update(o=106, h=111, l=105, c=110, ema_fast=105, ema_slow=100, regime_allows=True)
    # bar2: low BELOW ema_slow (no band touch), close < ema_slow -> EXT resets to IDLE
    m.update(o=106, h=107, l=98, c=99, ema_fast=105, ema_slow=100, regime_allows=True)
    assert m.state == "IDLE"


def test_short_mirror():
    m = RibbonEntryMachine(direction=-1, n_pull=3)
    assert m.update(o=94, h=95, l=89, c=90, ema_fast=95, ema_slow=100, regime_allows=True) is False
    assert m.state == "EXT"
    assert m.update(o=90, h=96, c=92, l=89, ema_fast=95, ema_slow=100, regime_allows=True) is False
    assert m.state == "PULL"
    assert m.update(o=94, h=95.5, l=90, c=94, ema_fast=95, ema_slow=100, regime_allows=True) is True
    assert m.state == "TRIG"


def test_should_exit_priority():
    assert should_exit(1, 0, close=120, ema_fast=105, ema_slow=100) is True
    assert should_exit(1, 1, close=99, ema_fast=105, ema_slow=100) is True
    assert should_exit(1, 1, close=106, ema_fast=100, ema_slow=105) is True
    assert should_exit(1, 1, close=106, ema_fast=105, ema_slow=100) is False
    assert should_exit(-1, 1, close=90, ema_fast=95, ema_slow=100) is True
    assert should_exit(-1, -1, close=95, ema_fast=95, ema_slow=100) is False


def test_acceptance_no_entry_without_regime():
    m = RibbonEntryMachine(direction=1, n_pull=3)
    for i, c in enumerate([110, 111, 104, 104.2, 106]):
        assert m.update(o=105, h=111, l=100 + i * 0.1, c=c, ema_fast=105, ema_slow=100, regime_allows=False) is False


def test_config_builds():
    config = ErrMomEma30EntryConfig(
        instrument_id=InstrumentId.from_str("BTC-USDT-SWAP.OKX"),
        bar_type=BarType.from_str("BTC-USDT-SWAP.OKX-1-MINUTE-LAST-EXTERNAL"),
        trade_size=Decimal("0.01"),
    )
    strategy = ErrMomEma30Entry(config=config)
    assert strategy.config.n_pull == 24
    assert strategy.config.ema_fast < strategy.config.ema_slow
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_strategies/test_err_mom_ema30_entry.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
"""Spec B: daily ERMOM regime permission + 30m EMA20/50 band-reentry entry.

Strategy logic only. Do not import TradingNode or OKX factories here.
"""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model import Bar, BarType, InstrumentId
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.trading import Strategy

from sngw_trader.indicators.bar_aggregator import BarAggregator, CompletedBar
from sngw_trader.indicators.err_momentum import ErrorAdjustedMomentum, regime_target
from sngw_trader.indicators.risk_metrics import (
    DailyAtr,
    Ema,
    RealizedVol,
    is_stop_hit,
    stop_price,
)


class ErrMomEma30EntryConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    w_f: int = 10
    w_e: int = 10
    momentum_window: int = 200
    theta: float = 0.0
    ema_fast: int = 20
    ema_slow: int = 50
    n_pull: int = 24
    risk_stop_enabled: bool = True
    atr_period: int = 14
    atr_mult: float = 3.0
    vol_filter_enabled: bool = True
    vol_lookback: int = 20
    vol_threshold: float = 0.80
    close_positions_on_stop: bool = True


class RibbonEntryMachine:
    """Mirror EXT->PULL->TRIG setup machine (spec 3.3/3.4).

    direction=+1 long, -1 short. One transition per completed 30m bar:
    after IDLE->EXT on bar k, EXT checks run from bar k+1 (N_ext >= 1).
    A bar that starts inside the band without having been in EXT can
    never become a trigger (spec 9 acceptance).
    """

    IDLE = "IDLE"
    EXT = "EXT"
    PULL = "PULL"
    TRIG = "TRIG"

    def __init__(self, direction: int, n_pull: int) -> None:
        self._d = direction
        self._n_pull = n_pull
        self.state = self.IDLE
        self._ext_bars = 0
        self._pull_bars = 0

    def reset(self) -> None:
        self.state = self.IDLE
        self._ext_bars = 0
        self._pull_bars = 0

    def update(
        self,
        o: float,
        h: float,
        l: float,
        c: float,
        ema_fast: float,
        ema_slow: float,
        regime_allows: bool,
    ) -> bool:
        """Returns True on the trigger bar (enter at next 30m open)."""
        if not regime_allows or self._d * (ema_fast - ema_slow) <= 0:
            self.reset()
            return False
        if self.state == self.IDLE:
            if self._d * (c - ema_fast) > 0:
                self.state = self.EXT
                self._ext_bars = 0
            return False
        if self.state == self.EXT:
            self._ext_bars += 1
            touch = l if self._d > 0 else h
            if self._d * (touch - ema_slow) >= 0 and self._d * (touch - ema_fast) <= 0:
                self.state = self.PULL
                self._pull_bars = 1
            elif self._d * (c - ema_slow) < 0:
                self.reset()
            return False
        if self.state == self.PULL:
            self._pull_bars += 1
            if self._d * (c - ema_fast) > 0:
                self.state = self.TRIG
                return True
            if self._d * (c - ema_slow) < 0 or self._pull_bars > self._n_pull:
                self.reset()
            return False
        return False


def should_exit(side: int, regime: int, close: float, ema_fast: float, ema_slow: float) -> bool:
    """Spec 4 priorities: regime reversal > EMA50 failure > ribbon cross."""
    if side > 0:
        return regime <= 0 or close < ema_slow or ema_fast <= ema_slow
    return regime >= 0 or close > ema_slow or ema_fast >= ema_slow


class ErrMomEma30Entry(Strategy):
    def __init__(self, config: ErrMomEma30EntryConfig) -> None:
        super().__init__(config)
        self._daily = BarAggregator(86_400)
        self._m30 = BarAggregator(1_800)
        self._ermom = ErrorAdjustedMomentum(config.w_f, config.w_e, config.momentum_window)
        self._atr = DailyAtr(config.atr_period)
        self._vol = RealizedVol(config.vol_lookback)
        self._ema_fast = Ema(config.ema_fast)
        self._ema_slow = Ema(config.ema_slow)
        self._long = RibbonEntryMachine(1, config.n_pull)
        self._short = RibbonEntryMachine(-1, config.n_pull)
        self._stop: float | None = None
        self._pending_atr: float | None = None

    def on_start(self) -> None:
        self.subscribe_bars(self.config.bar_type)

    def on_stop(self) -> None:
        if self.config.close_positions_on_stop:
            self.close_all_positions(self.config.instrument_id)

    def on_bar(self, bar: Bar) -> None:
        ts = int(bar.ts_init)
        o, h, l, c = (bar.open.as_double(), bar.high.as_double(), bar.low.as_double(), bar.close.as_double())
        daily = self._daily.update(ts, o, h, l, c)  # daily first: fresh regime wins the 00:00 bar
        if daily is not None:
            self._ermom.update(daily.close)
            self._atr.update(daily.open, daily.high, daily.low, daily.close)
            self._vol.update(daily.close)
        m30 = self._m30.update(ts, o, h, l, c)
        if m30 is not None:
            self._on_30m(m30)

    def _on_30m(self, bar30: CompletedBar) -> None:
        ema_f = self._ema_fast.update(bar30.close)
        ema_s = self._ema_slow.update(bar30.close)
        regime = regime_target(self._ermom.value, self.config.theta)

        if not self.portfolio.is_net_flat(self.config.instrument_id):
            side = 1 if self.portfolio.is_net_long(self.config.instrument_id) else -1
            if self.config.risk_stop_enabled and is_stop_hit(side, bar30.close, self._stop):
                self.close_all_positions(self.config.instrument_id)
                self._stop = None
                return
            if ema_f is not None and ema_s is not None and should_exit(side, regime, bar30.close, ema_f, ema_s):
                self.close_all_positions(self.config.instrument_id)
                self._stop = None
                self._long.reset()
                self._short.reset()
            return

        blocked = (
            regime == 0
            or (self.config.vol_filter_enabled and self._vol.value is not None and self._vol.value > self.config.vol_threshold)
        )
        if blocked or ema_f is None or ema_s is None:
            self._long.reset()
            self._short.reset()
            return

        machine = self._long if regime > 0 else self._short
        if machine.update(
            o=bar30.open, h=bar30.high, l=bar30.low, c=bar30.close,
            ema_fast=ema_f, ema_slow=ema_s, regime_allows=True,
        ):
            self._submit(OrderSide.BUY if regime > 0 else OrderSide.SELL)

    def _submit(self, side: OrderSide) -> None:
        instrument = self.cache.instrument(self.config.instrument_id)
        if instrument is None:
            self.log.error(f"Instrument not in cache: {self.config.instrument_id}")
            return
        self._pending_atr = self._atr.value
        self.submit_order(
            self.order_factory.market(
                self.config.instrument_id,
                side,
                instrument.make_qty(self.config.trade_size),
            )
        )

    def on_event(self, event) -> None:
        if not isinstance(event, OrderFilled) or event.instrument_id != self.config.instrument_id:
            return
        if self.portfolio.is_net_flat(self.config.instrument_id):
            self._stop = None
            return
        if self._pending_atr is not None:
            side = 1 if self.portfolio.is_net_long(self.config.instrument_id) else -1
            self._stop = stop_price(side, event.last_px.as_double(), self._pending_atr, self.config.atr_mult)
```

Notes:
- While a position is held, the machines are never updated (frozen), so no new trigger can fire mid-position; after an exit they are reset, forcing a fresh EXT→PULL→TRIG (spec 4: 재진입 규칙).
- `# ponytail: market orders assumed fully filled; partial-fill retry is live-phase work (spec 5)` above `on_event`.
- Remove the unused `_ext_bars` counter if the linter flags it — it exists to document N_ext ≥ 1; keeping it is fine.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_strategies/ -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/strategies/err_mom_ema30_entry.py tests/test_strategies/test_err_mom_ema30_entry.py
git commit -m "feat(strategy): spec B 30m EMA band-reentry entry machine"
```

---

### Task 6: Settings, strategy factory, runner wiring

**Files:**
- Modify: `src/sngw_trader/config/settings.py`
- Modify: `src/sngw_trader/runners/strategy_factory.py` (remove `build_ema_cross`, `importable_ema_cross_config`)
- Modify: `src/sngw_trader/runners/backtest_okx.py:89-104`
- Modify: `src/sngw_trader/runners/live_okx.py:14,96-102`
- Test: extend `tests/test_config/test_settings.py`, create `tests/test_runners/test_strategy_factory.py`

**Interfaces:**
- Consumes: `ErrMomentumRegimeConfig`, `ErrMomEma30EntryConfig`, `Settings`.
- Produces: `build_strategy(settings: Settings) -> Strategy` dispatching on `settings.strategy` in `{"err_mom_a", "err_mom_b"}`; `source_bar_type(instrument_id: str) -> str` (same 1-MINUTE string as before).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_runners/test_strategy_factory.py
from sngw_trader.config import load_settings
from sngw_trader.runners.strategy_factory import build_strategy
from sngw_trader.strategies.err_mom_ema30_entry import ErrMomEma30Entry
from sngw_trader.strategies.err_momentum_regime import ErrMomentumRegime


def test_build_dispatch_default_is_a():
    assert isinstance(build_strategy(load_settings()), ErrMomentumRegime)
```

Extend `tests/test_config/test_settings.py`:

```python
def test_err_mom_defaults():
    s = load_settings()
    assert s.strategy in {"err_mom_a", "err_mom_b"}
    assert (s.w_f, s.w_e, s.momentum_window) == (10, 10, 200)
    assert s.ema_fast < s.ema_slow
    assert s.vol_threshold == 0.80
    assert s.trade_size == "0.01"
```

- [ ] **Step 2: Run tests to verify they fail** — `uv run pytest tests/test_runners/test_strategy_factory.py tests/test_config -v` → FAIL (`KeyError: 'strategy'` / AttributeError).

- [ ] **Step 3: Implement settings + factory + runner rewiring**

`settings.py` — add fields after `bt_prob_slippage`:

```python
    strategy: str
    w_f: int
    w_e: int
    momentum_window: int
    theta: float
    ema_fast: int
    ema_slow: int
    n_pull: int
    trade_size: str
    risk_stop_enabled: bool
    atr_period: int
    atr_mult: float
    vol_filter_enabled: bool
    vol_lookback: int
    vol_threshold: float
```

and in `load_settings()`:

```python
        strategy=_env("STRATEGY", "err_mom_a"),
        w_f=int(_env("ERMOM_WF", "10")),
        w_e=int(_env("ERMOM_WE", "10")),
        momentum_window=int(_env("ERMOM_L", "200")),
        theta=float(_env("ERMOM_THETA", "0")),
        ema_fast=int(_env("EMA_FAST", "20")),
        ema_slow=int(_env("EMA_SLOW", "50")),
        n_pull=int(_env("N_PULL", "24")),
        trade_size=_env("TRADE_SIZE", "0.01"),
        risk_stop_enabled=_env("RISK_STOP_ENABLED", "true").lower() in {"1", "true", "yes"},
        atr_period=int(_env("ATR_PERIOD", "14")),
        atr_mult=float(_env("ATR_MULT", "3")),
        vol_filter_enabled=_env("VOL_FILTER_ENABLED", "true").lower() in {"1", "true", "yes"},
        vol_lookback=int(_env("VOL_LOOKBACK", "20")),
        vol_threshold=float(_env("VOL_THRESHOLD", "0.80")),
```

`strategy_factory.py` — replace entire file:

```python
"""Attach the configured strategy to either node type. No signal logic here."""

from __future__ import annotations

from decimal import Decimal

from nautilus_trader.model import BarType, InstrumentId
from nautilus_trader.trading import Strategy

from sngw_trader.config.settings import Settings
from sngw_trader.strategies.err_mom_ema30_entry import ErrMomEma30Entry, ErrMomEma30EntryConfig
from sngw_trader.strategies.err_momentum_regime import ErrMomentumRegime, ErrMomentumRegimeConfig


def source_bar_type(instrument_id: str) -> str:
    return f"{instrument_id}-1-MINUTE-LAST-EXTERNAL"


def build_err_mom_a(settings: Settings) -> Strategy:
    s = settings
    return ErrMomentumRegime(
        config=ErrMomentumRegimeConfig(
            instrument_id=InstrumentId.from_str(s.instrument_id_str),
            bar_type=BarType.from_str(source_bar_type(s.instrument_id_str)),
            trade_size=Decimal(s.trade_size),
            w_f=s.w_f,
            w_e=s.w_e,
            momentum_window=s.momentum_window,
            theta=s.theta,
            risk_stop_enabled=s.risk_stop_enabled,
            atr_period=s.atr_period,
            atr_mult=s.atr_mult,
            vol_filter_enabled=s.vol_filter_enabled,
            vol_lookback=s.vol_lookback,
            vol_threshold=s.vol_threshold,
        )
    )


def build_err_mom_b(settings: Settings) -> Strategy:
    s = settings
    return ErrMomEma30Entry(
        config=ErrMomEma30EntryConfig(
            instrument_id=InstrumentId.from_str(s.instrument_id_str),
            bar_type=BarType.from_str(source_bar_type(s.instrument_id_str)),
            trade_size=Decimal(s.trade_size),
            w_f=s.w_f,
            w_e=s.w_e,
            momentum_window=s.momentum_window,
            theta=s.theta,
            ema_fast=s.ema_fast,
            ema_slow=s.ema_slow,
            n_pull=s.n_pull,
            risk_stop_enabled=s.risk_stop_enabled,
            atr_period=s.atr_period,
            atr_mult=s.atr_mult,
            vol_filter_enabled=s.vol_filter_enabled,
            vol_lookback=s.vol_lookback,
            vol_threshold=s.vol_threshold,
        )
    )


def build_strategy(settings: Settings) -> Strategy:
    builders = {"err_mom_a": build_err_mom_a, "err_mom_b": build_err_mom_b}
    try:
        return builders[settings.strategy](settings)
    except KeyError as exc:
        raise SystemExit(f"Unknown STRATEGY '{settings.strategy}'. Use err_mom_a or err_mom_b.") from exc
```

`backtest_okx.py`: replace the old `attach_strategy` body with `strategy = build_strategy(settings)` (drop `build_ema_cross` import and `default_bar_type`).

`live_okx.py`: replace `from sngw_trader.runners.strategy_factory import build_ema_cross` with `from sngw_trader.runners.strategy_factory import build_strategy`; `attach_strategy` becomes:

```python
def attach_strategy(node: TradingNode, settings: Settings) -> None:
    node.trader.add_strategy(build_strategy(settings))
```

The `strategies/example/ema_cross.py` example file and `strategies/__init__.py` exports stay untouched (example remains unused by runners).

- [ ] **Step 3b: Add a B-dispatch test variant**

Set `STRATEGY=err_mom_b` via monkeypatch in a second test:

```python
def test_build_dispatch_b(monkeypatch):
    monkeypatch.setenv("STRATEGY", "err_mom_b")
    assert isinstance(build_strategy(load_settings()), ErrMomEma30Entry)
```

- [ ] **Step 4: Run full test suite**

Run: `uv run pytest -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/config/settings.py src/sngw_trader/runners/strategy_factory.py src/sngw_trader/runners/backtest_okx.py src/sngw_trader/runners/live_okx.py tests/
git commit -m "feat(runner): wire err-mom strategies into runners via STRATEGY selector"
```

---

### Task 7: Fills export + funding post-processing

**Files:**
- Modify: `src/sngw_trader/runners/backtest_okx.py` (export fills CSV after run)
- Create: `src/sngw_trader/data/funding.py`
- Test: `tests/test_data/test_funding.py` (+ `tests/test_data/__init__.py`)

**Interfaces:**
- Consumes: fills report CSV written by the runner.
- Produces:
  - `fetch_funding_rates(inst_id: str, start_ms: int, end_ms: int) -> dict[int, float]` — public OKX history, `{funding_ts_ms: rate}`
  - `funding_cost(fills: list[tuple[int, float, float]], rates: dict[int, float]) -> float` — fills are `(ts_ns, signed_qty_at_fill, fill_price)`; returns total funding PnL (positive = paid)
  - `daily_returns(pos: list[float], closes: list[float]) -> list[float]` — day t return = position held during day t × close-to-close return
  - `summarize(rets: list[float]) -> dict[str, float]` — total_return, cagr, sharpe, sortino, max_dd

- [ ] **Step 1: Write the failing test**

```python
# tests/test_data/test_funding.py
"""Unit tests for funding post-processing. No network."""

from sngw_trader.data.funding import daily_returns, funding_cost, summarize


def test_funding_cost_long_pays_positive_rate():
    fills = [(0, 1.0, 100.0)]  # long 1 unit at 100 at t=0
    rates = {0: 0.0001, 8 * 3_600_000: 0.0001, 16 * 3_600_000: 0.0001}
    cost = funding_cost(fills, rates)
    assert abs(cost - 3 * 0.0001 * 100.0) < 1e-12


def test_funding_cost_short_receives():
    fills = [(0, -1.0, 100.0)]
    rates = {0: 0.0001}
    cost = funding_cost(fills, rates)
    assert abs(cost + 0.0001 * 100.0) < 1e-12  # short receives -> cost negative


def test_daily_returns_and_summary():
    closes = [100.0, 101.0, 99.0]
    pos = [0.0, 1.0, 1.0]  # position held during days 1, 2
    rets = daily_returns(pos, closes)
    assert rets[0] == 0.0
    assert abs(rets[1] - 0.01) < 1e-12
    assert abs(rets[2] - (99.0 / 101.0 - 1)) < 1e-12
    stats = summarize(rets)
    assert stats["sharpe"] < 0  # +1% then -2% day
    assert stats["max_dd"] <= 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_data/test_funding.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
"""Funding-cost post-processing and perf metrics (spec 7.3).

The backtest engine runs WITHOUT funding; this module applies it outside
the engine from the fills report and prints funding-excluded vs included
metrics. No orders, no nodes here.
"""

from __future__ import annotations

import json
import math
import urllib.request
from pathlib import Path

FUNDING_URL = "https://www.okx.com/api/v5/public/funding-rate-history"


def fetch_funding_rates(inst_id: str, start_ms: int, end_ms: int) -> dict[int, float]:
    """Public OKX funding-rate-history (100/page, newest first) -> {ts_ms: rate}."""
    rates: dict[int, float] = {}
    after = end_ms + 1
    while True:
        url = f"{FUNDING_URL}?instId={inst_id}&after={after}&limit=100"
        with urllib.request.urlopen(url, timeout=30) as resp:
            rows = json.loads(resp.read())["data"]
        if not rows:
            break
        for row in rows:
            ts = int(row["fundingTime"])
            if start_ms <= ts <= end_ms:
                rates[ts] = float(row["fundingRate"])
        oldest = min(int(row["fundingTime"]) for row in rows)
        if oldest <= start_ms or len(rows) < 100:
            break
        after = oldest
    return dict(sorted(rates.items()))


def funding_cost(fills: list[tuple[int, float, float]], rates: dict[int, float]) -> float:
    """fills: (ts_ns, signed_qty, fill_price) -> cumulative funding PnL.
    Long pays positive funding; short receives it.
    ponytail: notional approximated by the most recent fill price;
    switch to mark closes from the catalog if too coarse."""
    events = sorted(fills)
    total = 0.0
    qty, ref = 0.0, None
    idx = 0
    for ts_ms, rate in sorted(rates.items()):
        t_ns = ts_ms * 1_000_000
        while idx < len(events) and events[idx][0] <= t_ns:
            qty += events[idx][1]
            ref = events[idx][2]
            idx += 1
        if ref is not None and qty != 0:
            total += rate * abs(qty) * ref * (1.0 if qty > 0 else -1.0)
    return total


def daily_returns(pos: list[float], closes: list[float]) -> list[float]:
    """Day t return = position held during day t * close-to-close return."""
    rets = [0.0]
    for t in range(1, len(closes)):
        rets.append(pos[t] * (closes[t] / closes[t - 1] - 1))
    return rets if len(closes) > 1 else [0.0]


def summarize(rets: list[float]) -> dict[str, float]:
    mean = sum(rets) / len(rets) if rets else 0.0
    var = sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1)
    sharpe = mean / (var ** 0.5) * math.sqrt(365) if var > 0 else 0.0
    downside = [r for r in rets if r < 0]
    dvar = sum(r**2 for r in downside) / max(1, len(rets)) if downside else 0.0
    sortino = mean / (dvar ** 0.5) * math.sqrt(365) if downside and dvar > 0 else 0.0
    equity, peak, max_dd = 1.0, 1.0, 0.0
    for r in rets:
        equity *= 1 + r
        peak = max(peak, equity)
        max_dd = min(max_dd, equity / peak - 1)
    years = len(rets) / 365 if len(rets) else 0
    cagr = equity ** (1 / years) - 1 if years > 0 and equity > 0 else 0.0
    return {"total_return": equity - 1, "cagr": cagr, "sharpe": sharpe, "sortino": sortino, "max_dd": max_dd}
```

Also add a `main()` to `data/funding.py` for Task 8:

```python
def main() -> None:
    import csv
    from pathlib import Path

    fills_path = Path("logs/fills.csv")
    with fills_path.open() as f:
        rows = list(csv.DictReader(f))
    fills = [
        (int(r["filled_ts"]), (-1.0 if r["order_side"] == "SELL" else 1.0) * float(r["last_qty"]), float(r["last_px"]))
        for r in rows
        if r.get("filled_ts") or r.get("ts_event")
    ]
    start_ms = min(f[0] for f in fills) // 1_000_000
    end_ms = max(f[0] for f in fills) // 1_000_000
    rates = fetch_funding_rates("BTC-USDT-SWAP", start_ms, end_ms)
    cost = funding_cost(fills, rates)
    print(f"funding cost (signed PnL): {cost:.4f}")
```

Mark `fill csv` column names (`filled_ts` / `order_side` / `last_px` / `last_qty`) as UNVERIFIED — introspect the actual report columns during execution and adapt the parser.

- [ ] **Step 3b: `backtest_okx.main` exports fills**

After `results = node.run()`:

```python
    _export_fills(node, results, Path(settings.log_dir) / "fills.csv")
```

```python
def _export_fills(node: BacktestNode, results: object, path: Path) -> None:
    trader = None
    for r in (results or []):
        engine = getattr(r, "engine", None) or getattr(r, "backtest_engine", None)
        if engine is not None and hasattr(engine, "trader"):
            trader = engine.trader
            break
    if trader is None and hasattr(node, "trader"):
        trader = node.trader
    if trader is None:
        raise RuntimeError(
            "Cannot locate trader for the fills report. Inspect: "
            'uv run python -c "from nautilus_trader.backtest.node import BacktestNode; print(dir(BacktestNode))"'
        )
    df = trader.generate_order_fills_report()
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
```

If `generate_order_fills_report` doesn't exist on the trader, introspect the closest report method (`[m for m in dir(trader) if "report" in m]`) and adapt the column mapping in `data/funding.py`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_data/test_funding.py tests/test_runners -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/data/funding.py src/sngw_trader/runners/backtest_okx.py tests/test_data
git commit -m "feat(runner): fills export + funding post-processing and perf metrics"
```

---

### Task 8: End-to-end backtest smoke + acceptance verification

**Files:** none (verification only). Requires catalog populated with BTC-USDT-SWAP 1m bars (`CATALOG_PATH`, default `./catalog`).

- [ ] **Step 1: Run backtest A**

```powershell
$env:STRATEGY = "err_mom_a"; uv run python -m sngw_trader.runners.backtest_okx
```

Expected: node runs, `logs/fills.csv` written. If the catalog is empty or the data config fails, STOP and report — do not fabricate results.

- [ ] **Step 2: Lookahead check**

Spot-check the first entry fills: each fill price must match the OPEN of the bar following the signal bar (compare against catalog bar data), never the signal bar's close. Record the result.

- [ ] **Step 3: Run backtest B and the funding report**

```powershell
$env:STRATEGY = "err_mom_b"; uv run python -m sngw_trader.runners.backtest_okx
uv run python -m sngw_trader.data.funding
```

- [ ] **Step 4: Full suite**

`uv run pytest -v` → all PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat(runner): end-to-end backtest smoke and funding report"
```

---

## Self-Review Notes

- Spec coverage: ERMOM indicator + shared regime mapping (Task 2), aggregator (Task 1), risk layer (Task 3; wired in Tasks 4/5), Spec A (Task 4), Spec B FSM incl. mirror, exits, acceptance conditions (Task 5), settings/factory/runners (Task 6), funding + 7.3 metrics (Task 7), end-to-end verification (Task 8).
- Deliberate simplifications (marked `ponytail:` in code): funding notional uses fill-price carry-forward, not mark closes; market orders assumed fully filled (partial-fill retry is live-phase work per spec 5); final incomplete aggregated bucket dropped.
- Engine-behavior dependency: market order submitted in `on_bar(t)` fills at next bar open — verified from the fills report in Task 8 before any acceptance claim.
