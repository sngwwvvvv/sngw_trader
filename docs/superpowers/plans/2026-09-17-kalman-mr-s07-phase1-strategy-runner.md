# Kalman MR S07 Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `KalmanSpreadStrategy` (S01-S06 binding) and the walk-forward `BacktestNode` runner so Phase 2 can run and judge validation.

**Architecture:** One Nautilus `Strategy` instance manages the whole 15-pair universe. Per pair a FORMATION(720 hourly mark bars) -> TRADING(240 hours) cycle freezes `x_center`/`x_scale` and the screen verdict, then trades S01 z-bands. Sizing/risk/execution/events/costs are delegated to the existing pure modules S03/S04/S05/S06/S02. The runner (`research/kalman_walkforward.py`) assembles `BacktestNode` from a JSON config and holds no trading conditions.

**Tech Stack:** NautilusTrader 1.231.0 `BacktestNode` + `ParquetDataCatalog`, existing `sngw_trader.indicators.*` pure modules, pytest.

**Spec:** [S07 Walk-Forward Validation Design](../specs/2026-09-17-kalman-mr-s07-validation-design.md) — Phase 1 sections. Stage spec: [2026-09-15-kalman-mr-s07-validation.md](2026-09-15-kalman-mr-s07-validation.md).

## Global Constraints

- Run tests from the worktree root with `.venv\Scripts\python.exe -m pytest <target> -q` (worktree `.venv` has nautilus_trader 1.231.0).
- `src/sngw_trader/indicators/*` stays nautilus-free; only `strategies/`, `runners/`, `research/` import nautilus.
- Trading conditions live in the strategy; the runner only assembles nodes and reads/writes config/results.
- Signal data: completed 1-hour OKX mark candles only (`{SYMBOL}-USDT-SWAP.OKX-1-HOUR-MARK-EXTERNAL`). No smoother. Execution is next-bar (market orders submitted on the bar after the signal bar close).
- InstrumentId format `{SYMBOL}-USDT-SWAP.OKX`. OKX only.
- Funding observations must exist for every open funding timestamp; missing data fails cost accounting closed (S02 contract) — never substitute 0.
- Documented v1 screening ceilings (also stated in the design doc): liquidity/OI/depth gates are fixed assumptions (reported `NOT_ASSESSED`); ADF is omitted, Hurst < 0.48 stays the primary relationship gate.
- Hard gate values (spec 짠6.1): rho in [0.55, 0.92]; HL in [6h, 240h]; Hurst < 0.48; IQR(beta)/|median| < 0.40; \|z\|>4.5 at most 2 times; one-sided 12h jump at most 2 times; \|corr(dFunding, e)\| <= 0.35; one-direction funding <= 7 days; 2*sigma_e > 2.5*c with c = 30bp (FACTOR) / 36bp (PEER). Soft score cut = 60 of 100.

---

### Task 1: Formation screening statistics (pure)

**Files:**
- Create: `src/sngw_trader/indicators/pair_screening.py`
- Test: `tests/test_indicators/test_pair_screening.py`

**Interfaces:**
- Consumes: `KalmanSpreadConfig`, `KalmanSpreadFilter` from `sngw_trader.indicators.kalman_spread`.
- Produces (used by Task 2):
  - `formation_stats(y_marks: Sequence[float], x_marks: Sequence[float], r: float, delta: float) -> FormationStats`
  - `screen_pair(stats: FormationStats, book: str, *, funding_corr: float, funding_one_sided_days: int, cost_multiplier: float = 1.0) -> ScreenDecision`
  - `FormationStats(x_center, x_scale, sigma_e, sigma_u, rho, hl_hours, hurst, beta_iqr_ratio, jump45_count, oneside_jump_count, filter)` — `filter` is the warm `KalmanSpreadFilter` to continue with.
  - `ScreenDecision(passed: bool, soft_score: int, reasons: tuple[str, ...])`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_indicators/test_pair_screening.py
import math
import random

from sngw_trader.indicators.kalman_spread import KalmanSpreadConfig, KalmanSpreadFilter
from sngw_trader.indicators.pair_screening import (
    FormationStats,
    formation_stats,
    screen_pair,
    _half_life_hours,
)


def _seeded_pair(n=720, seed=7):
    rng = random.Random(seed)
    x, e = 30_000.0, 0.0
    y_px, x_px = [], []
    for _ in range(n):
        x *= math.exp(rng.gauss(0.0, 0.01))
        e = 0.9714 * e + rng.gauss(0.0, 0.001)
        y_px.append(x * math.exp(e))
        x_px.append(x)
    return y_px, x_px


def _stats(**overrides) -> FormationStats:
    kf = KalmanSpreadFilter(KalmanSpreadConfig(0.001, 0.0001, 10.0, 0.1))
    base = dict(
        x_center=10.0, x_scale=0.1, sigma_e=0.004, sigma_u=0.01, rho=0.70,
        hl_hours=24.0, hurst=0.40, beta_iqr_ratio=0.20, jump45_count=0,
        oneside_jump_count=0, filter=kf,
    )
    base.update(overrides)
    return FormationStats(**base)


def test_formation_stats_recovers_relationship():
    y_px, x_px = _seeded_pair()
    stats = formation_stats(y_px, x_px, r=0.001, delta=0.0001)
    assert stats.x_center > 0 and stats.x_scale > 0
    assert stats.rho < 0.92          # noise keeps correlation inside the hard band
    assert 10.0 < stats.hl_hours < 60.0   # AR(1) phi=0.9714 -> HL ~ 24h
    assert stats.sigma_e > 0 and stats.sigma_u > 0
    assert stats.jump45_count == 0   # seeded shocks stay below 4.5 sigma


def test_half_life_matches_ar1_coefficient():
    rng = random.Random(3)
    e, series = 0.0, []
    for _ in range(4000):
        e = 0.5 * e + rng.gauss(0.0, 0.001)
        series.append(e)
    hl = _half_life_hours(series[1:])
    assert 0.5 < hl < 2.5            # phi=0.5 -> HL ~ 1h


def test_screen_pair_passes_clean_pair():
    d = screen_pair(_stats(), "FACTOR", funding_corr=0.05, funding_one_sided_days=2)
    assert d.passed and d.soft_score >= 60
    d = screen_pair(_stats(), "PEER", funding_corr=0.05, funding_one_sided_days=2)
    assert d.passed


def test_screen_pair_hard_gate_reasons():
    cases = [
        (dict(rho=0.95), "RHO_OUT_OF_BAND"),
        (dict(rho=0.40), "RHO_OUT_OF_BAND"),
        (dict(hl_hours=2.0), "HALF_LIFE_OUT_OF_BAND"),
        (dict(hl_hours=300.0), "HALF_LIFE_OUT_OF_BAND"),
        (dict(hurst=0.55), "HURST_NOT_MR"),
        (dict(beta_iqr_ratio=0.5), "BETA_UNSTABLE"),
        (dict(jump45_count=3), "TOO_MANY_JUMPS"),
        (dict(oneside_jump_count=3), "TOO_MANY_ONESIDE_JUMPS"),
        (dict(sigma_e=0.001), "COST_MULTIPLE"),
    ]
    for overrides, reason in cases:
        d = screen_pair(_stats(**overrides), "FACTOR", funding_corr=0.05, funding_one_sided_days=2)
        assert not d.passed and reason in d.reasons, overrides


def test_screen_pair_funding_gates():
    d = screen_pair(_stats(), "FACTOR", funding_corr=0.5, funding_one_sided_days=2)
    assert not d.passed and "FUNDING_RESID_COUPLED" in d.reasons
    d = screen_pair(_stats(), "FACTOR", funding_corr=0.05, funding_one_sided_days=9)
    assert not d.passed and "FUNDING_ONE_SIDED" in d.reasons


def test_screen_pair_cost_multiplier_stress_fails():
    d = screen_pair(_stats(sigma_e=0.004), "FACTOR", funding_corr=0.05, funding_one_sided_days=2, cost_multiplier=2.0)
    assert not d.passed and "COST_MULTIPLE" in d.reasons


def test_screen_pair_soft_score_components():
    weak = _stats(hl_hours=150.0, hurst=0.46, beta_iqr_ratio=0.3, sigma_e=0.002, sigma_u=0.01)
    d = screen_pair(weak, "FACTOR", funding_corr=0.2, funding_one_sided_days=4)
    assert d.reasons == () and d.passed is (d.soft_score >= 60)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_indicators/test_pair_screening.py -q`
Expected: FAIL — `ModuleNotFoundError: sngw_trader.indicators.pair_screening`

- [ ] **Step 3: Implement `pair_screening.py`**

```python
"""Formation-window statistics and Hard/Soft screening gates (S07 Phase 1).

Pure math over completed hourly mark closes. No nautilus imports, no I/O.
Gates follow OKX_Kalman_MR_Spec 짠6. Liquidity/OI/depth gates are fixed
assumptions in v1 validation (NOT_ASSESSED); ADF is omitted and Hurst < 0.48
stays the primary mean-reversion gate. Both ceilings are reported with the
S07 results.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from sngw_trader.indicators.kalman_spread import KalmanSpreadConfig, KalmanSpreadFilter

BOOK_ROUND_TRIP_COST = {"FACTOR": 0.0030, "PEER": 0.0036}
COST_MULTIPLE_MIN = 2.5
RHO_MIN, RHO_MAX = 0.55, 0.92
HL_MIN_HOURS, HL_MAX_HOURS = 6.0, 240.0
HURST_MAX = 0.48
BETA_IQR_MAX = 0.40
JUMP45_MAX = 2
ONESIDE_JUMP_MAX = 2
FUNDING_CORR_MAX = 0.35
FUNDING_ONESIDED_MAX_DAYS = 7

_RS_CHUNK_SIZES = (8, 16, 32, 64, 128, 256)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def _pstdev(values: Sequence[float]) -> float:
    m = _mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / len(values))


def _pearson(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or len(a) < 2:
        return 0.0
    ma, mb = _mean(a), _mean(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return 0.0
    return cov / math.sqrt(va * vb)


def _iqr(values: Sequence[float]) -> float:
    ordered = sorted(values)
    def _pct(p):
        idx = p * (len(ordered) - 1)
        lo, hi = int(idx), min(int(idx) + 1, len(ordered) - 1)
        frac = idx - lo
        return ordered[lo] * (1 - frac) + ordered[hi] * frac
    return _pct(0.75) - _pct(0.25)


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if n % 2:
        return ordered[n // 2]
    return (ordered[n // 2 - 1] + ordered[n // 2]) / 2


def _half_life_hours(residuals: Sequence[float]) -> float:
    """AR(1) fit e_t = phi e_{t-1} + u_t; HL = ln 0.5 / ln|phi| (spec 짠6.1)."""
    if len(residuals) < 3:
        return math.inf
    num = sum(a * b for a, b in zip(residuals, residuals[1:]))
    den = sum(a * a for a in residuals[:-1])
    if den <= 0:
        return math.inf
    phi = num / den
    if abs(phi) >= 1.0 or phi <= 0.0:
        return math.inf
    return math.log(0.5) / math.log(phi)


def _hurst_rs(series: Sequence[float]) -> float:
    """Rescaled-range Hurst estimate; 0.5 fallback when data is too short."""
    n = len(series)
    if n < 16:
        return 0.5
    xs, ys = [], []
    for size in _RS_CHUNK_SIZES:
        if size > n // 2:
            break
        ratios = []
        for start in range(0, n - size + 1, size):
            chunk = series[start : start + size]
            m = _mean(chunk)
            cum = mx = mn = 0.0
            ss = 0.0
            for v in chunk:
                d = v - m
                cum += d
                ss += d * d
                mx = max(mx, cum)
                mn = min(mn, cum)
            rng = mx - mn
            std = math.sqrt(ss / size)
            if std > 0 and rng > 0:
                ratios.append(math.log10(rng / std))
        if ratios:
            xs.append(math.log10(size))
            ys.append(_mean(ratios))
    if len(xs) < 2:
        return 0.5
    mx, my = _mean(xs), _mean(ys)
    denom = sum((x - mx) ** 2 for x in xs)
    if denom <= 0:
        return 0.5
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom


@dataclass(frozen=True)
class ScreenDecision:
    passed: bool
    soft_score: int
    reasons: tuple[str, ...]


@dataclass
class FormationStats:
    x_center: float
    x_scale: float
    sigma_e: float
    sigma_u: float
    rho: float
    hl_hours: float
    hurst: float
    beta_iqr_ratio: float
    jump45_count: int
    oneside_jump_count: int
    filter: KalmanSpreadFilter


def formation_stats(
    y_marks: Sequence[float], x_marks: Sequence[float], r: float, delta: float
) -> FormationStats:
    """Replay the formation window through S01 and compute screening stats.

    Causality: only the supplied window is used; the returned filter has
    consumed exactly these bars and continues online during trading.
    """
    if len(y_marks) != len(x_marks) or len(y_marks) < 3:
        raise ValueError("formation window requires >=3 aligned (y, x) marks")
    y_log = [math.log(v) for v in y_marks]
    x_log = [math.log(v) for v in x_marks]
    x_center = _mean(x_log)
    x_scale = max(_pstdev(x_log), 1e-6)
    kf = KalmanSpreadFilter(KalmanSpreadConfig(r=r, delta=delta, x_center=x_center, x_scale=x_scale))
    e_path: list[float] = []
    z_path: list[float] = []
    b_path: list[float] = []
    for y, x in zip(y_marks, x_marks):
        res = kf.update(y, x)
        e_path.append(res.innovation)
        z_path.append(res.z_score)
        b_path.append(res.beta)
    core = e_path[1:]  # skip the lazy-initialization observation
    sigma_e = max(_pstdev(core), 1e-9)
    # Unit-notional hourly spread return r_t = dln y - beta_t * dln x.
    spread_returns = []
    for i in range(1, len(y_log)):
        spread_returns.append(
            (y_log[i] - y_log[i - 1]) - b_path[i] * (x_log[i] - x_log[i - 1])
        )
    sigma_u = max(_pstdev(spread_returns), 1e-9)
    dy = [y_log[i] - y_log[i - 1] for i in range(1, len(y_log))]
    dx = [x_log[i] - x_log[i - 1] for i in range(1, len(x_log))]
    beta_med = _median(b_path[1:])
    beta_iqr = _iqr(b_path[1:]) / abs(beta_med) if abs(beta_med) > 1e-12 else math.inf
    # One-sided 12h jump: |dln y| over 12h >= 20% while |dln x| <= 5%.
    oneside = 0
    for i in range(12, len(y_log)):
        if abs(y_log[i] - y_log[i - 12]) >= 0.20 and abs(x_log[i] - x_log[i - 12]) <= 0.05:
            oneside += 1
    return FormationStats(
        x_center=x_center,
        x_scale=x_scale,
        sigma_e=sigma_e,
        sigma_u=sigma_u,
        rho=_pearson(dy, dx),
        hl_hours=_half_life_hours(core),
        hurst=_hurst_rs(core),
        beta_iqr_ratio=beta_iqr,
        jump45_count=sum(1 for z in z_path[1:] if abs(z) > 4.5),
        oneside_jump_count=oneside,
        filter=kf,
    )


def screen_pair(
    stats: FormationStats,
    book: str,
    *,
    funding_corr: float,
    funding_one_sided_days: int,
    cost_multiplier: float = 1.0,
) -> ScreenDecision:
    """Apply spec 짠6.1 Hard gates and 짠6.2 Soft score. Liquidity gates are
    fixed assumptions in v1 (NOT_ASSESSED); ADF is omitted."""
    if book not in BOOK_ROUND_TRIP_COST:
        raise ValueError(f"unknown book {book!r}")
    reasons: list[str] = []
    if not RHO_MIN <= stats.rho <= RHO_MAX:
        reasons.append("RHO_OUT_OF_BAND")
    if not HL_MIN_HOURS <= stats.hl_hours <= HL_MAX_HOURS:
        reasons.append("HALF_LIFE_OUT_OF_BAND")
    if stats.hurst >= HURST_MAX:
        reasons.append("HURST_NOT_MR")
    if stats.beta_iqr_ratio >= BETA_IQR_MAX:
        reasons.append("BETA_UNSTABLE")
    if stats.jump45_count > JUMP45_MAX:
        reasons.append("TOO_MANY_JUMPS")
    if stats.oneside_jump_count > ONESIDE_JUMP_MAX:
        reasons.append("TOO_MANY_ONESIDE_JUMPS")
    if abs(funding_corr) > FUNDING_CORR_MAX:
        reasons.append("FUNDING_RESID_COUPLED")
    if funding_one_sided_days > FUNDING_ONESIDED_MAX_DAYS:
        reasons.append("FUNDING_ONE_SIDED")
    c = BOOK_ROUND_TRIP_COST[book] * cost_multiplier
    if 2.0 * stats.sigma_e <= COST_MULTIPLE_MIN * c:
        reasons.append("COST_MULTIPLE")
    score = 0
    score += 25 if 24.0 <= stats.hl_hours <= 96.0 else 0
    score += 20 if 2.0 * stats.sigma_e / c >= 5.0 else 0
    score += 15 if stats.hurst <= 0.35 else 0
    score += 15 if stats.beta_iqr_ratio <= 0.15 else 0
    score += 10 if stats.jump45_count == 0 else 0
    score += 10 if abs(funding_corr) <= 0.10 else 0
    score += 5  # depth: assumed pass in v1 validation
    return ScreenDecision(passed=not reasons and score >= 60, soft_score=score, reasons=tuple(reasons))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_indicators/test_pair_screening.py -q`
Expected: all PASS. (Seeded random tests are deterministic; if a seeded stat lands outside its asserted band, adjust the seed constant once — never loosen the gate values.)

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/indicators/pair_screening.py tests/test_indicators/test_pair_screening.py
git commit -m "feat(s07): formation screening statistics and hard/soft gates"
```

---

### Task 2: Strategy — pair table, config, phase machine, decision core

**Files:**
- Create: `src/sngw_trader/strategies/kalman_spread.py`
- Test: `tests/test_strategies/test_kalman_spread_strategy.py`

**Interfaces:**
- Consumes: `formation_stats`, `screen_pair`, `FormationStats`, `ScreenDecision` (Task 1); `KalmanSpreadFilter` (S01); `EventRecord`, `EventType`, `GateLimits`, `evaluate_gate`, `EXIT_NONE/ORDERLY/EMERGENCY` (S06); `ExecutionState`, `ExecutionPhase` (S05).
- Produces (used by Task 3 and the runner):
  - `PairBandSpec` dataclass + `PAIR_BANDS: tuple[PairBandSpec, ...]` (15 rows, spec 짠7.1).
  - `KalmanSpreadConfig(StrategyConfig, frozen=True)` — fields listed below.
  - `KalmanSpreadStrategy(Strategy)` with `on_bar(bar)`; per-pair close emits decision dataclasses into `self._pending_requests` (Task 3 replaces the no-op `_execute_requests` with real submission):
    - `EntryRequest(pair_id, side, beta, y_mark, x_mark, ts_ms)`
    - `ExitRequest(pair_id, reason, emergency, ts_ms)` — reason in `EXIT_MEAN|STOP_Z|STOP_TIME|STOP_FLIP|STOP_EVENT|WINDOW_END`
    - `RehedgeRequest(pair_id, beta, ts_ms)`
  - `KalmanSpreadStrategyConfig` pairs are selected by `pair_ids`; instruments by `instrument_ids` (tuple of `"...-USDT-SWAP.OKX"` strings).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_strategies/test_kalman_spread_strategy.py
import math
import random
from decimal import Decimal

import pytest
from nautilus_trader.model import Bar, BarType, InstrumentId

from sngw_trader.strategies.kalman_spread import (
    PAIR_BANDS,
    KalmanSpreadConfig,
    KalmanSpreadStrategy,
)

INSTRUMENT_IDS = ("ETH-USDT-SWAP.OKX", "BTC-USDT-SWAP.OKX")
BAR_TYPES = {
    iid: BarType.from_str(f"{iid}-1-HOUR-MARK-EXTERNAL") for iid in INSTRUMENT_IDS
}
HOUR_MS = 3_600_000


def _config(**overrides) -> KalmanSpreadConfig:
    base = dict(
        instrument_ids=INSTRUMENT_IDS,
        pair_ids=(1,),
        formation_hours=720,
        trading_hours=240,
    )
    base.update(overrides)
    return KalmanSpreadConfig(**base)


def _seeded_pair(n=720, seed=7):
    """Same generator as tests/test_indicators/test_pair_screening.py."""
    rng = random.Random(seed)
    x, e = 30_000.0, 0.0
    y_px, x_px = [], []
    for _ in range(n):
        x *= math.exp(rng.gauss(0.0, 0.01))
        e = 0.9714 * e + rng.gauss(0.0, 0.001)
        y_px.append(x * math.exp(e))
        x_px.append(x)
    return y_px, x_px


def _bar(symbol: str, ts_ms: int, close: float) -> Bar:
    return Bar.from_raw(
        bar_type=BAR_TYPES[f"{symbol}-USDT-SWAP.OKX"],
        open=Decimal(str(close - 1)),
        high=Decimal(str(close)),
        low=Decimal(str(close - 2)),
        close=Decimal(str(close)),
        price_prec=2,
        volume=Decimal("100"),
        size_prec=3,
        ts_event=ts_ms * 1_000_000,
        ts_init=ts_ms * 1_000_000,
    )


# --- pair table --------------------------------------------------------------


def test_pair_table_has_15_rows_with_spec_bands():
    assert len(PAIR_BANDS) == 15
    assert [b.pair_id for b in PAIR_BANDS] == list(range(1, 16))
    assert [b.book for b in PAIR_BANDS[:8]] == ["FACTOR"] * 8
    assert [b.book for b in PAIR_BANDS[8:]] == ["PEER"] * 7
    p1 = PAIR_BANDS[0]
    assert (p1.y_symbol, p1.x_symbol) == ("ETH-USDT-SWAP", "BTC-USDT-SWAP")
    assert (p1.entry_z, p1.exit_z, p1.stop_z) == (1.60, 0.30, 3.80)
    assert (p1.stop_duration_hours, p1.max_hold_hours, p1.hl_multiple) == (4, 96, 2.0)
    assert (p1.rehedge_frac, p1.cooldown_hours, p1.size_weight) == (0.10, 48, 1.00)
    assert (p1.sigma_multiple, p1.margin_cap_ratio, p1.mutex) == (1.00, 0.25, (9, 15))
    p15 = PAIR_BANDS[14]
    assert (p15.entry_z, p15.cooldown_hours, p15.mutex) == (1.90, 72, (1,))


def test_pair_table_mutex_targets_exist():
    ids = {b.pair_id for b in PAIR_BANDS}
    for band in PAIR_BANDS:
        for m in band.mutex:
            assert m in ids


# --- phase machine -----------------------------------------------------------


def _drive_formation(s: KalmanSpreadStrategy, hours: int, start_ms: int) -> int:
    rng = random.Random(11)
    x = 30_000.0
    ts = start_ms
    for _ in range(hours):
        x *= math.exp(rng.gauss(0.0, 0.01))
        y = x * 2.0 * math.exp(rng.gauss(0.0, 0.01))
        s.on_bar(_bar("BTC", ts, x))
        s.on_bar(_bar("ETH", ts, y))
        ts += HOUR_MS
    return ts


def test_formation_freezes_and_transitions_to_trading(monkeypatch):
    from sngw_trader.indicators import pair_screening as ps

    warm = ps.formation_stats(*_seeded_pair(), r=0.001, delta=0.0001)
    monkeypatch.setattr(
        "sngw_trader.strategies.kalman_spread.formation_stats", lambda *a, **k: warm
    )
    monkeypatch.setattr(
        "sngw_trader.strategies.kalman_spread.screen_pair",
        lambda *a, **k: ps.ScreenDecision(True, 80, ()),
    )
    s = KalmanSpreadStrategy(config=_config())
    _drive_formation(s, 720, start_ms=0)
    rt = s._pairs[1]
    assert rt.phase == "TRADING"
    assert rt.hours == 0
    assert rt.kf is warm.filter
    assert rt.hl_hours == warm.hl_hours
    assert s._screen_results[1].passed is True


def test_screen_fail_resets_formation_window(monkeypatch):
    from sngw_trader.indicators import pair_screening as ps

    warm = ps.formation_stats(*_seeded_pair(), r=0.001, delta=0.0001)
    monkeypatch.setattr(
        "sngw_trader.strategies.kalman_spread.formation_stats", lambda *a, **k: warm
    )
    monkeypatch.setattr(
        "sngw_trader.strategies.kalman_spread.screen_pair",
        lambda *a, **k: ps.ScreenDecision(False, 40, ("HURST_NOT_MR",)),
    )
    s = KalmanSpreadStrategy(config=_config())
    _drive_formation(s, 720, start_ms=0)
    rt = s._pairs[1]
    assert rt.phase == "FORMATION"
    assert rt.hours == 0 and not rt.y_marks
    assert s._screen_results[1].passed is False


def test_window_end_returns_to_formation(monkeypatch):
    from sngw_trader.indicators import pair_screening as ps

    warm = ps.formation_stats(*_seeded_pair(), r=0.001, delta=0.0001)
    monkeypatch.setattr(
        "sngw_trader.strategies.kalman_spread.formation_stats", lambda *a, **k: warm
    )
    monkeypatch.setattr(
        "sngw_trader.strategies.kalman_spread.screen_pair",
        lambda *a, **k: ps.ScreenDecision(True, 80, ()),
    )
    s = KalmanSpreadStrategy(config=_config(formation_hours=72, trading_hours=24))
    ts = _drive_formation(s, 72, start_ms=0)
    assert s._pairs[1].phase == "TRADING"
    _drive_formation(s, 25, start_ms=ts)  # trading window + 1 bar
    assert s._pairs[1].phase == "FORMATION"
    assert s._pairs[1].hours == 0  # the 25th bar itself triggered the reset


def test_entry_crossing_emits_request():
    s = KalmanSpreadStrategy(config=_config())
    rt = s._pairs[1]
    rt.phase = "TRADING"
    rt.kf = _StubFilter([0.5, 1.7])
    s.on_bar(_bar("BTC", 0, 30_000.0))
    s.on_bar(_bar("ETH", 0, 60_000.0))
    s.on_bar(_bar("BTC", HOUR_MS, 30_001.0))
    s.on_bar(_bar("ETH", HOUR_MS, 60_001.0))
    entry = [r for r in s._request_log if isinstance(r, EntryRequest)]
    assert len(entry) == 1
    assert entry[0].side == -1 and entry[0].pair_id == 1  # z>0 cross -> short spread
    assert entry[0].y_mark > 0 and entry[0].x_mark > 0


def test_exit_mean_emits_request():
    s = KalmanSpreadStrategy(config=_config())
    rt = s._pairs[1]
    rt.phase, rt.kf, rt.entry_side = "TRADING", _StubFilter([0.1]), -1
    s.on_bar(_bar("BTC", 0, 30_000.0)); s.on_bar(_bar("ETH", 0, 60_000.0))
    exits = [r for r in s._request_log if isinstance(r, ExitRequest)]
    assert len(exits) == 1 and exits[0].reason == "EXIT_MEAN"


def test_rehedge_request_on_beta_drift():
    s = KalmanSpreadStrategy(config=_config())
    rt = s._pairs[1]
    rt.phase, rt.entry_side, rt.entry_beta = "TRADING", 1, 1.0
    rt.kf = _StubFilter([0.5], beta=1.10)   # |1.10-1.0|/1.0 >= 0.10
    s.on_bar(_bar("BTC", 0, 30_000.0)); s.on_bar(_bar("ETH", 0, 60_000.0))
    rehedges = [r for r in s._request_log if isinstance(r, RehedgeRequest)]
    assert len(rehedges) == 1 and rehedges[0].beta == 1.10


def test_stop_z_requires_sustained_breach():
    s = KalmanSpreadStrategy(config=_config())
    rt = s._pairs[1]
    rt.phase, rt.kf, rt.entry_side = "TRADING", _StubFilter([4.0, 4.0, 4.0, 4.0]), -1
    for i in range(4):
        ts = i * HOUR_MS
        s.on_bar(_bar("BTC", ts, 30_000.0)); s.on_bar(_bar("ETH", ts, 60_000.0))
    exits = [r for r in s._request_log if isinstance(r, ExitRequest)]
    assert exits[-1].reason == "STOP_Z"   # only on the 4th sustained bar


def test_stop_flip_on_adverse_excursion():
    s = KalmanSpreadStrategy(config=_config())
    rt = s._pairs[1]
    rt.phase, rt.kf, rt.entry_side = "TRADING", _StubFilter([-1.2]), -1
    s.on_bar(_bar("BTC", 0, 30_000.0)); s.on_bar(_bar("ETH", 0, 60_000.0))
    exits = [r for r in s._request_log if isinstance(r, ExitRequest)]
    assert exits[-1].reason == "STOP_FLIP"


def test_manual_kill_event_exits_emergency(tmp_path):
    events = tmp_path / "events.json"
    events.write_text(
        '[{"event_id":"e1","event_type":"MANUAL_KILL","symbol":"ETH-USDT-SWAP",'
        '"effective_at":0.0,"expires_at":null,"source_status":"ACTIVE"}]'
    )
    s = KalmanSpreadStrategy(config=_config(events_path=str(events)))
    rt = s._pairs[1]
    rt.phase, rt.kf, rt.entry_side = "TRADING", _StubFilter([0.1]), -1
    s.on_bar(_bar("BTC", 0, 30_000.0)); s.on_bar(_bar("ETH", 0, 60_000.0))
    exits = [r for r in s._request_log if isinstance(r, ExitRequest)]
    assert exits[-1].reason == "STOP_EVENT" and exits[-1].emergency is True
    assert rt.phase == "FORMATION"          # killed pair re-forms


def test_cooldown_blocks_reentry():
    s = KalmanSpreadStrategy(config=_config())
    rt = s._pairs[1]
    rt.phase = "TRADING"
    rt.kf = _StubFilter([0.5, 1.7])
    rt.exec_state = ExecutionState(cooldown_deadline=HOUR_MS)  # cooldown until 1h
    s.on_bar(_bar("BTC", 0, 30_000.0)); s.on_bar(_bar("ETH", 0, 60_000.0))
    s.on_bar(_bar("BTC", HOUR_MS, 30_001.0)); s.on_bar(_bar("ETH", HOUR_MS, 60_001.0))
    assert [r for r in s._request_log if isinstance(r, EntryRequest)] == []


def test_mutex_pair_blocks_entry():
    s = KalmanSpreadStrategy(config=_config(
        instrument_ids=INSTRUMENT_IDS + ("SOL-USDT-SWAP.OKX",), pair_ids=(1, 9)))
    rt1, rt9 = s._pairs[1], s._pairs[9]
    rt9.phase, rt9.entry_side = "TRADING", 1       # pair 9 open -> pair 1 mutexed
    rt1.phase, rt1.kf = "TRADING", _StubFilter([0.5, 1.7])
    s.on_bar(_bar("BTC", 0, 30_000.0))
    s.on_bar(_bar("ETH", 0, 60_000.0))
    s.on_bar(_bar("BTC", HOUR_MS, 30_001.0))
    s.on_bar(_bar("ETH", HOUR_MS, 60_001.0))
    assert [r for r in s._request_log if isinstance(r, EntryRequest)] == []


class _StubFilter:
    def __init__(self, z_values, beta=1.0):
        from sngw_trader.indicators.kalman_spread import KalmanSpreadResult

        self._results = [
            KalmanSpreadResult(0.001, 0.0, 0.001, z, beta, True, 72) for z in z_values
        ]

    def update(self, y_price, x_price):
        return self._results.pop(0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_strategies/test_kalman_spread_strategy.py -q`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement the strategy module**

```python
"""Kalman MR spread strategy binding S01-S06 (S07 Phase 1).

One Strategy instance manages the 15-pair universe. Per pair: FORMATION
(720 completed 1h mark bars) freezes x_center/x_scale plus the screen
verdict, then TRADING (240 hours) trades spec 짠7.1 z-bands. Sizing, risk,
execution, events, and realized costs delegate to S03/S04/S05/S06/S02.
Strategy logic only — no runner assembly, no exchange I/O.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model import Bar, BarType, InstrumentId
from nautilus_trader.trading import Strategy

from sngw_trader.indicators.event_gate import (
    EXIT_EMERGENCY,
    EXIT_NONE,
    EXIT_ORDERLY,
    EventRecord,
    EventType,
    GateLimits,
    evaluate_gate,
)
from sngw_trader.indicators.execution_recovery import ExecutionState
from sngw_trader.indicators.kalman_spread import KalmanSpreadFilter
from sngw_trader.indicators.pair_screening import (
    FormationStats,
    ScreenDecision,
    formation_stats,
    screen_pair,
)

HOUR_MS = 3_600_000
HOUR_NS = 3_600_000_000_000
DAY_MS = 86_400_000
FUNDING_PERIOD_MS = 8 * HOUR_MS
ACTIVE_EXEC_PHASES = frozenset(
    {"ENTRY_PENDING", "ENTRY_PARTIAL", "OPEN", "EXIT_PENDING", "EXIT_PARTIAL", "FLATTENING"}
)


@dataclass(frozen=True)
class PairBandSpec:
    pair_id: int
    book: str
    y_symbol: str
    x_symbol: str
    entry_z: float
    exit_z: float
    stop_z: float
    stop_duration_hours: int
    max_hold_hours: int
    hl_multiple: float
    rehedge_frac: float
    cooldown_hours: int
    size_weight: float
    sigma_multiple: float
    margin_cap_ratio: float
    mutex: tuple[int, ...] = ()


PAIR_BANDS: tuple[PairBandSpec, ...] = (
    PairBandSpec(1, "FACTOR", "ETH-USDT-SWAP", "BTC-USDT-SWAP", 1.60, 0.30, 3.80, 4, 96, 2.0, 0.10, 48, 1.00, 1.00, 0.25, (9, 15)),
    PairBandSpec(2, "FACTOR", "SOL-USDT-SWAP", "BTC-USDT-SWAP", 1.50, 0.30, 3.50, 3, 72, 2.0, 0.08, 48, 1.10, 1.00, 0.25, (9,)),
    PairBandSpec(3, "FACTOR", "BNB-USDT-SWAP", "BTC-USDT-SWAP", 1.70, 0.35, 3.40, 3, 60, 1.8, 0.08, 48, 0.80, 1.00, 0.20, ()),
    PairBandSpec(4, "FACTOR", "SUI-USDT-SWAP", "BTC-USDT-SWAP", 1.55, 0.30, 3.40, 3, 60, 1.8, 0.08, 48, 0.90, 1.00, 0.20, (10,)),
    PairBandSpec(5, "FACTOR", "XRP-USDT-SWAP", "BTC-USDT-SWAP", 1.80, 0.40, 3.20, 2, 48, 1.5, 0.10, 72, 0.60, 0.80, 0.15, ()),
    PairBandSpec(6, "FACTOR", "ADA-USDT-SWAP", "BTC-USDT-SWAP", 1.75, 0.30, 3.60, 4, 72, 2.0, 0.10, 48, 0.70, 1.00, 0.15, ()),
    PairBandSpec(7, "FACTOR", "AVAX-USDT-SWAP", "BTC-USDT-SWAP", 1.60, 0.30, 3.50, 3, 60, 1.8, 0.08, 48, 0.80, 1.00, 0.18, (12,)),
    PairBandSpec(8, "FACTOR", "DOGE-USDT-SWAP", "BTC-USDT-SWAP", 1.90, 0.40, 3.20, 2, 36, 1.5, 0.10, 72, 0.50, 0.70, 0.12, ()),
    PairBandSpec(9, "PEER", "ETH-USDT-SWAP", "SOL-USDT-SWAP", 1.90, 0.35, 3.40, 3, 48, 1.5, 0.08, 48, 0.50, 0.80, 0.15, (1, 2)),
    PairBandSpec(10, "PEER", "SUI-USDT-SWAP", "APT-USDT-SWAP", 1.85, 0.35, 3.30, 3, 48, 1.5, 0.08, 48, 0.55, 0.80, 0.12, (4,)),
    PairBandSpec(11, "PEER", "ARB-USDT-SWAP", "OP-USDT-SWAP", 1.85, 0.35, 3.30, 3, 48, 1.5, 0.08, 48, 0.55, 0.80, 0.12, ()),
    PairBandSpec(12, "PEER", "AVAX-USDT-SWAP", "NEAR-USDT-SWAP", 1.85, 0.35, 3.30, 3, 48, 1.5, 0.08, 48, 0.50, 0.80, 0.12, (7,)),
    PairBandSpec(13, "PEER", "AAVE-USDT-SWAP", "LINK-USDT-SWAP", 1.80, 0.30, 3.40, 3, 60, 1.8, 0.08, 48, 0.60, 0.90, 0.15, (14,)),
    PairBandSpec(14, "PEER", "UNI-USDT-SWAP", "AAVE-USDT-SWAP", 1.80, 0.30, 3.40, 3, 60, 1.8, 0.08, 48, 0.55, 0.90, 0.15, (13,)),
    PairBandSpec(15, "PEER", "LDO-USDT-SWAP", "ETH-USDT-SWAP", 1.90, 0.40, 3.20, 2, 36, 1.5, 0.10, 72, 0.45, 0.70, 0.10, (1,)),
)


@dataclass(frozen=True)
class EntryRequest:
    pair_id: int
    side: int          # +1 long spread (z<0), -1 short spread (z>0)
    beta: float
    y_mark: float
    x_mark: float
    ts_ms: int


@dataclass(frozen=True)
class ExitRequest:
    pair_id: int
    reason: str        # EXIT_MEAN | STOP_Z | STOP_TIME | STOP_FLIP | STOP_EVENT | WINDOW_END
    emergency: bool
    ts_ms: int


@dataclass(frozen=True)
class RehedgeRequest:
    pair_id: int
    beta: float
    ts_ms: int


@dataclass
class _PairRuntime:
    spec: PairBandSpec
    phase: str = "FORMATION"
    hours: int = 0
    y_marks: list[float] = field(default_factory=list)
    x_marks: list[float] = field(default_factory=list)
    kf: KalmanSpreadFilter | None = None
    hl_hours: float | None = None
    sigma_u: float | None = None
    prev_z: float | None = None
    stop_hours: int = 0
    hold_hours: int = 0
    entry_side: int = 0
    entry_beta: float | None = None
    entry_ts_ms: int = 0
    exec_state: ExecutionState = field(default_factory=ExecutionState)
    last_gate_check_s: float = 0.0
    decision_marks: dict[str, float] = field(default_factory=dict)   # leg -> decision-time mark
    trade_fills: list[tuple] = field(default_factory=list)           # (leg, signed_qty, price)
    s02_fills: list = field(default_factory=list)                    # S02 Fill records for calculate_cost
    funding_obs: list = field(default_factory=list)                  # S02 FundingObservation
    entry_avg: dict[str, float] = field(default_factory=dict)        # leg -> avg entry price
    exit_reason: str | None = None
    exit_emergency: bool = False
    rehedge_target_x: Decimal | None = None

    @property
    def has_exposure(self) -> bool:
        return self.exec_state.phase.name in ACTIVE_EXEC_PHASES


class KalmanSpreadConfig(StrategyConfig, frozen=True):
    instrument_ids: tuple[str, ...]
    pair_ids: tuple[int, ...]
    formation_hours: int = 720
    trading_hours: int = 240
    r: float = 0.001
    delta: float = 0.0001
    equity_usdt: float = 10_000.0
    risk_frac: float = 0.005
    leverage: float = 2.0
    btc_half_spread_bps: float = 3.0
    alt_half_spread_bps: float = 6.0
    taker_fee: float = 0.0005
    cost_multiplier: float = 1.0
    funding_rate_assumption: float = 0.0004
    max_slippage_bps: float = 50.0
    leg_timeout_hours: float = 2.0
    gate_max_age_hours: float = 26.0
    funding_dir: str = ""
    events_path: str = ""
    contract_specs_path: str = ""


class KalmanSpreadStrategy(Strategy):
    """Kalman mean-reversion spread over the OKX USDT perp universe."""

    def __init__(self, config: KalmanSpreadConfig) -> None:
        super().__init__(config)
        wanted = set(config.pair_ids)
        self._pairs: dict[int, _PairRuntime] = {
            spec.pair_id: _PairRuntime(spec) for spec in PAIR_BANDS if spec.pair_id in wanted
        }
        self._instrument_ids = list(config.instrument_ids)
        self._bars: dict[InstrumentId, Bar] = {}
        self._processed_hours: set[tuple[int, int]] = set()
        self._events: tuple[EventRecord, ...] = ()
        self._gate_limits = GateLimits(max_event_age_seconds=config.gate_max_age_hours * 3600)
        self._screen_results: dict[int, ScreenDecision] = {}
        self._pending_requests: list[EntryRequest | ExitRequest | RehedgeRequest] = []
        self._request_log: list[EntryRequest | ExitRequest | RehedgeRequest] = []

    # --- lifecycle -----------------------------------------------------------

    def on_start(self) -> None:
        self._events = _load_events(self.config.events_path)
        for iid in self._instrument_ids:
            instrument_id = InstrumentId.from_str(iid)
            self.subscribe_bars(BarType.from_str(f"{iid}-1-HOUR-MARK-EXTERNAL"))

    # --- bar routing ---------------------------------------------------------

    def on_bar(self, bar: Bar) -> None:
        inst = bar.bar_type.instrument_id
        self._bars[inst] = bar
        for rt in self._pairs.values():
            y_inst = InstrumentId.from_str(f"{rt.spec.y_symbol}.OKX")
            x_inst = InstrumentId.from_str(f"{rt.spec.x_symbol}.OKX")
            if inst not in (y_inst, x_inst):
                continue
            yb, xb = self._bars.get(y_inst), self._bars.get(x_inst)
            if yb is None or xb is None:
                continue
            hour = yb.ts_event // HOUR_NS
            if hour != xb.ts_event // HOUR_NS:
                continue
            key = (rt.spec.pair_id, hour)
            if key in self._processed_hours:
                continue
            self._processed_hours.add(key)
            requests = self._on_pair_close(
                rt,
                ts_ms=hour * 3_600_000,
                y_mark=float(yb.close.as_decimal()),
                x_mark=float(xb.close.as_decimal()),
            )
            self._pending_requests.extend(requests)
        self._execute_requests()

    def _execute_requests(self) -> None:
        """Task 3 replaces this with real sizing/risk/execution; the audit
        log of every emitted request stays in both tasks."""
        self._request_log.extend(self._pending_requests)
        self._pending_requests = []

    # --- per-pair decision core ----------------------------------------------

    def _on_pair_close(self, rt: _PairRuntime, ts_ms: int, y_mark: float, x_mark: float) -> list:
        cfg = self.config
        spec = rt.spec
        now_s = ts_ms / 1000.0
        requests: list = []
        timeout_request = self._check_timeout(rt, now_s)   # Task 3; returns None in Phase 1
        if rt.phase == "FORMATION":
            rt.y_marks.append(y_mark)
            rt.x_marks.append(x_mark)
            rt.hours += 1
            if rt.hours >= cfg.formation_hours:
                self._complete_formation(rt, ts_ms)
            return requests
        rt.hours += 1
        gate = self._evaluate_gate(rt, ts_ms)
        if gate == EXIT_EMERGENCY:
            if rt.has_exposure:
                requests.append(ExitRequest(spec.pair_id, "STOP_EVENT", True, ts_ms))
            self._reset_to_formation(rt)
            return requests
        if rt.has_exposure and gate == EXIT_ORDERLY and rt.entry_side != 0:
            requests.append(ExitRequest(spec.pair_id, "STOP_EVENT", False, ts_ms))
            return requests
        if rt.hours > cfg.trading_hours:
            if rt.has_exposure:
                requests.append(ExitRequest(spec.pair_id, "WINDOW_END", False, ts_ms))
            self._reset_to_formation(rt)
            return requests
        if rt.kf is None:
            return requests
        res = rt.kf.update(y_mark, x_mark)
        if not res.ready:
            rt.prev_z = None
            return requests
        z = res.z_score
        rt.decision_marks = {"Y": y_mark, "X": x_mark}
        if rt.entry_side != 0:
            self._accrue_funding(rt, ts_ms)
            rt.hold_hours += 1
            request = self._exit_decision(rt, res, ts_ms)
            if request is not None:
                requests.append(request)
            elif abs(res.beta - rt.entry_beta) / abs(rt.entry_beta) >= spec.rehedge_frac:
                requests.append(RehedgeRequest(spec.pair_id, res.beta, ts_ms))
        else:
            request = self._entry_decision(rt, res, ts_ms, y_mark, x_mark)
            if request is not None:
                requests.append(request)
        rt.prev_z = z
        return requests

    def _exit_decision(self, rt: _PairRuntime, res, ts_ms: int):
        spec = rt.spec
        z = res.z_score
        if abs(z) <= spec.exit_z:
            return ExitRequest(spec.pair_id, "EXIT_MEAN", False, ts_ms)
        if abs(z) >= spec.stop_z:
            rt.stop_hours += 1
            if rt.stop_hours >= spec.stop_duration_hours:
                return ExitRequest(spec.pair_id, "STOP_Z", False, ts_ms)
        else:
            rt.stop_hours = 0
        if z * rt.entry_side > 1.0:
            return ExitRequest(spec.pair_id, "STOP_FLIP", False, ts_ms)
        max_hold = min(spec.max_hold_hours, spec.hl_multiple * (rt.hl_hours or spec.max_hold_hours))
        if rt.hold_hours >= max_hold:
            return ExitRequest(spec.pair_id, "STOP_TIME", False, ts_ms)
        return None

    def _entry_decision(self, rt: _PairRuntime, res, ts_ms: int, y_mark: float, x_mark: float):
        spec = rt.spec
        if rt.prev_z is None:
            return None
        if rt.exec_state.cooldown_deadline is not None and ts_ms / 1000.0 < rt.exec_state.cooldown_deadline:
            return None
        if any(self._pairs[m].entry_side != 0 for m in spec.mutex if m in self._pairs):
            return None
        if abs(rt.prev_z) <= spec.entry_z < abs(res.z_score):
            return EntryRequest(spec.pair_id, -1 if res.z_score > 0 else 1, res.beta,
                                y_mark, x_mark, ts_ms)
        return None

    def _accrue_funding(self, rt: _PairRuntime, ts_ms: int) -> None:
        """Task 3 wires the funding history; no-op keeps the call site fixed."""

    def _complete_formation(self, rt: _PairRuntime, ts_ms: int) -> None:
        cfg = self.config
        stats: FormationStats = formation_stats(rt.y_marks, rt.x_marks, cfg.r, cfg.delta)
        funding_corr, one_sided = self._funding_screen_inputs(rt, ts_ms)
        decision = screen_pair(stats, rt.spec.book, funding_corr=funding_corr,
                               funding_one_sided_days=one_sided,
                               cost_multiplier=cfg.cost_multiplier)
        self._screen_results[rt.spec.pair_id] = decision
        if decision.passed:
            rt.kf = stats.filter
            rt.hl_hours = stats.hl_hours
            rt.sigma_u = stats.sigma_u
            rt.phase = "TRADING"
        rt.hours = 0
        rt.y_marks = []
        rt.x_marks = []

    def _reset_to_formation(self, rt: _PairRuntime) -> None:
        rt.phase = "FORMATION"
        rt.hours = 0
        rt.y_marks = []
        rt.x_marks = []
        rt.kf = None
        rt.hl_hours = None
        rt.sigma_u = None
        rt.prev_z = None
        rt.stop_hours = 0
        rt.hold_hours = 0
        rt.entry_side = 0
        rt.entry_beta = None
        rt.entry_ts_ms = 0

    def _evaluate_gate(self, rt: _PairRuntime, ts_ms: int) -> str:
        """Return the worst exit action across both legs (EXIT_NONE when allowed)."""
        now_s = ts_ms / 1000.0
        last = rt.last_gate_check_s if rt.last_gate_check_s > 0 else now_s
        rt.last_gate_check_s = now_s
        worst = EXIT_NONE
        for symbol in (rt.spec.y_symbol, rt.spec.x_symbol):
            decision = evaluate_gate(self._events, symbol, now_s, last, self._gate_limits)
            if decision.exit_action == EXIT_EMERGENCY:
                return EXIT_EMERGENCY
            if decision.exit_action == EXIT_ORDERLY:
                worst = EXIT_ORDERLY
        return worst

    def _funding_screen_inputs(self, rt: _PairRuntime, ts_ms: int) -> tuple[float, int]:
        """(corr(dFunding, e), max one-sided days) over the formation window."""
        return 0.0, 0   # Task 3 wires the funding history; 0 keeps gates neutral


def _load_events(path: str) -> tuple[EventRecord, ...]:
    if not path:
        return ()
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    return tuple(
        EventRecord(
            event_id=row["event_id"],
            event_type=EventType(row["event_type"]),
            symbol=row.get("symbol"),
            effective_at=row["effective_at"],
            expires_at=row.get("expires_at"),
            source_status=row.get("source_status", "ACTIVE"),
            observed_at=row.get("observed_at", row["effective_at"]),
        )
        for row in rows
    )
```

Implementation notes (binding for the executor):
- `_check_timeout` is introduced as a stub in this task (`return None`) and implemented in Task 3; keep the call site so the ordering (timeout before decisions) is fixed now.
- Entry requests in `_entry_decision` carry the decision-time `y_mark`/`x_mark`; Task 3 uses them for sizing, the S04 candidate, and the slippage reference prices.
- `on_start` must not touch funding/contracts in this task (Task 3).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_strategies/test_kalman_spread_strategy.py -q`
Expected: all PASS. Where a stubbed `_StubFilter` sequence drives exit logic, the entry/exit/stop/rehedge/flip/time-stop branches must all be exercised by at least one test.

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/strategies/kalman_spread.py tests/test_strategies/test_kalman_spread_strategy.py
git commit -m "feat(s07): kalman spread strategy phase machine and decision core"
```

---

### Task 3: Strategy execution binding (S03/S04/S05/S06/S02)

**Files:**
- Modify: `src/sngw_trader/strategies/kalman_spread.py` (replace `_execute_requests` no-op with real binding)
- Test: `tests/test_strategies/test_kalman_spread_strategy.py` (append)

**Interfaces:**
- Consumes: `size_pair`, `contract_spec_from_instrument`, `ContractSpec` (S03); `RiskLimits`, `PortfolioSnapshot`, `PairCandidate`, `approve_pair` (S04); `begin_entry`, `begin_exit`, `on_fill`, `on_timeout`, `on_order_failure`, `emergency_flatten`, `confirm_flatten`, `FillObservation`, `ActionIntent`, `SUBMIT/CANCEL_UNFILLED/FLATTEN_FILLED`, `ExecutionPhase` (S05); `Fill`, `FundingObservation`, `CostBreakdown`, `calculate_cost` (S02).
- Produces: market-order submission through `self.order_factory.market(..., client_order_id=ClientOrderId(f"K{pair_id:02d}-{leg}-{seq}"))`; fill/reject/timeout bookkeeping; `self._trade_records: list[dict]` with `{pair_id, entry_ts_ms, exit_ts_ms, reason, pnl_usdt, cost: CostBreakdown|None, cost_error: str|None}`.

Contract-spec injection: `KalmanSpreadConfig.contract_specs_path` points to a JSON `{instId: {ct_val, lot_sz, min_sz, price_precision}}`; when empty the strategy falls back to `self.cache.instrument(...)` + `contract_spec_from_instrument`. Unit tests inject a tmp JSON so no engine is needed.

- [ ] **Step 1: Write the failing tests (append)**

```python
# appended to tests/test_strategies/test_kalman_spread_strategy.py
import json as _json

from sngw_trader.indicators.cost_model import CostBreakdown
from sngw_trader.indicators.execution_recovery import ExecutionPhase, ExecutionState


def _specs_file(tmp_path):
    specs = {
        "ETH-USDT-SWAP.OKX": {"ct_val": 0.1, "lot_sz": 0.001, "min_sz": 0.001, "price_precision": 2},
        "BTC-USDT-SWAP.OKX": {"ct_val": 0.001, "lot_sz": 0.001, "min_sz": 0.001, "price_precision": 2},
    }
    p = tmp_path / "specs.json"
    p.write_text(_json.dumps(specs))
    return str(p)


class _RecordingStrategy(KalmanSpreadStrategy):
    def __init__(self, config):
        super().__init__(config)
        self.submitted: list[tuple[str, str, int, str, str]] = []
        self.cancelled: list[str] = []

    def _submit_market(self, rt, leg, side, qty, coid):
        self.submitted.append((rt.spec.pair_id, leg, side, str(qty), coid))

    def _cancel_order(self, coid):
        self.cancelled.append(coid)


def _trading_strategy(tmp_path, **overrides):
    s = _RecordingStrategy(config=_config(
        formation_hours=72, trading_hours=240,
        contract_specs_path=_specs_file(tmp_path), **overrides))
    rt = s._pairs[1]
    rt.phase = "TRADING"
    rt.kf = _StubFilter([1.7])          # z crosses up -> short spread
    rt.sigma_u = 0.01
    rt.hl_hours = 24.0
    return s, rt


def _funding_dir(tmp_path, rates=(0.0001,)):
    """Funding history JSONs for both legs with a stamp at 8h."""
    d = tmp_path / "funding"
    d.mkdir(exist_ok=True)
    stamp = 8 * HOUR_MS
    (d / "ETH-USDT-SWAP.json").write_text(_json.dumps({str(stamp): rates[0]}))
    (d / "BTC-USDT-SWAP.json").write_text(_json.dumps({str(stamp): rates[0]}))
    return str(d)


def _fill(s, rt, leg, fill_id, price, ts_ms, close=True):
    leg_state = rt.exec_state.legs[leg]
    if close:
        qty = leg_state.target_quantity          # exit fill closes the full leg
    else:
        qty = leg_state.target_quantity          # entry fill reaches the full target
    side = -rt.entry_side if leg == "Y" else rt.entry_side   # closing side
    s._apply_fill(rt, leg, fill_id, qty, Decimal(str(price)), ts_ms, side)


def test_entry_request_sizes_approves_and_submits(tmp_path):
    s, rt = _trading_strategy(tmp_path)
    s.on_bar(_bar("BTC", 0, 30_000.0))
    s.on_bar(_bar("ETH", 0, 60_000.0))
    assert rt.exec_state.phase == ExecutionPhase.ENTRY_PENDING
    assert rt.entry_side == -1 and rt.entry_beta == 1.0
    kinds = [(leg, side) for _, leg, side, _, _ in s.submitted]
    assert kinds == [("Y", -1), ("X", 1)]          # short spread: sell Y, buy X
    assert len(s.submitted) == 2


def test_fills_open_position_and_fills_close_trade(tmp_path):
    s, rt = _trading_strategy(tmp_path, funding_dir=_funding_dir(tmp_path))
    s.on_bar(_bar("BTC", 0, 30_000.0)); s.on_bar(_bar("ETH", 0, 60_000.0))
    s._apply_fill(rt, "Y", "f1", rt.exec_state.legs["Y"].target_quantity, Decimal(str(60000)), HOUR_MS, -1)
    s._apply_fill(rt, "X", "f2", rt.exec_state.legs["X"].target_quantity, Decimal(str(30000)), HOUR_MS, 1)
    assert rt.exec_state.phase == ExecutionPhase.OPEN
    rt.kf = _StubFilter([1.0, 0.1])                 # h=8h no exit; h=9h back inside band
    s.on_bar(_bar("BTC", 8 * HOUR_MS, 30_000.0)); s.on_bar(_bar("ETH", 8 * HOUR_MS, 60_000.0))
    assert rt.funding_obs and len(rt.funding_obs) == 2   # accrued at the 8h stamp
    s.on_bar(_bar("BTC", 9 * HOUR_MS, 30_000.0)); s.on_bar(_bar("ETH", 9 * HOUR_MS, 60_000.0))
    assert rt.exec_state.phase == ExecutionPhase.EXIT_PENDING
    s._apply_fill(rt, "Y", "f3", rt.exec_state.legs["Y"].target_quantity, Decimal(str(60100)), 9 * HOUR_MS, 1)
    s._apply_fill(rt, "X", "f4", rt.exec_state.legs["X"].target_quantity, Decimal(str(30050)), 9 * HOUR_MS, -1)
    assert rt.exec_state.phase == ExecutionPhase.CLOSED
    record = s._trade_records[-1]
    assert record["reason"] == "EXIT_MEAN"
    assert record["cost"] is not None and record["cost"].total_usdt > 0
    assert record["cost_error"] is None
    assert rt.entry_side == 0


def test_risk_rejection_blocks_entry(tmp_path):
    s, rt = _trading_strategy(tmp_path)
    s._realized_pnl_usdt = -10_000.0               # equity <= 0 -> MISSING_DATA
    s.on_bar(_bar("BTC", 0, 30_000.0)); s.on_bar(_bar("ETH", 0, 60_000.0))
    assert s.submitted == []
    assert rt.entry_side == 0


def test_missing_funding_fails_cost_closed(tmp_path):
    s, rt = _trading_strategy(tmp_path)
    s.on_bar(_bar("BTC", 0, 30_000.0)); s.on_bar(_bar("ETH", 0, 60_000.0))
    s._apply_fill(rt, "Y", "f1", rt.exec_state.legs["Y"].target_quantity, Decimal(str(60000)), HOUR_MS, -1)
    s._apply_fill(rt, "X", "f2", rt.exec_state.legs["X"].target_quantity, Decimal(str(30000)), HOUR_MS, 1)
    rt.kf = _StubFilter([0.1])
    s.on_bar(_bar("BTC", 9 * HOUR_MS, 30_000.0)); s.on_bar(_bar("ETH", 9 * HOUR_MS, 60_000.0))
    s._apply_fill(rt, "Y", "f3", rt.exec_state.legs["Y"].target_quantity, Decimal(str(60100)), 9 * HOUR_MS, 1)
    s._apply_fill(rt, "X", "f4", rt.exec_state.legs["X"].target_quantity, Decimal(str(30050)), 9 * HOUR_MS, -1)
    assert s._trade_records[-1]["cost_error"] is not None
    assert s._trade_records[-1]["cost"] is None


def test_timeout_rolls_back_pending_entry(tmp_path):
    s, rt = _trading_strategy(tmp_path, leg_timeout_hours=1.0)
    s.on_bar(_bar("BTC", 0, 30_000.0)); s.on_bar(_bar("ETH", 0, 60_000.0))
    assert rt.exec_state.phase == ExecutionPhase.ENTRY_PENDING
    rt.kf = _StubFilter([0.5])
    # next bar is past the 1h deadline with no fills
    s.on_bar(_bar("BTC", 2 * HOUR_MS, 30_000.0)); s.on_bar(_bar("ETH", 2 * HOUR_MS, 60_000.0))
    assert rt.exec_state.phase == ExecutionPhase.IDLE
    assert len(s.cancelled) == 2
    assert rt.entry_side == 0                      # optimistic side reset on rollback
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_strategies/test_kalman_spread_strategy.py -q`
Expected: new tests FAIL (no `_apply_fill`, `_execute_requests` is a no-op).

- [ ] **Step 3: Implement the execution binding**

Add to `KalmanSpreadStrategy` (and imports: `Decimal` already imported; add `from dataclasses import replace`, and S02/S03/S04/S05 imports):

```python
    # --- lifecycle additions (extend on_start) -------------------------------

    def on_start(self) -> None:
        self._events = _load_events(self.config.events_path)
        self._contracts = _load_contract_specs(self.config.contract_specs_path)
        self._funding = _load_funding_dir(self.config.funding_dir)
        self._order_seq = 0
        self._coid_legs: dict[str, tuple[int, str]] = {}
        self._trade_records: list[dict] = []
        self._realized_pnl_usdt = 0.0
        self._equity_high = self.config.equity_usdt
        self._day_key = -1
        self._day_start_equity = self.config.equity_usdt
        for iid in self._instrument_ids:
            self.subscribe_bars(BarType.from_str(f"{iid}-1-HOUR-MARK-EXTERNAL"))

    # --- request execution ----------------------------------------------------

    def _execute_requests(self) -> None:
        pending, self._pending_requests = self._pending_requests, []
        for request in pending:
            rt = self._pairs[request.pair_id]
            if isinstance(request, EntryRequest):
                self._handle_entry(rt, request)
            elif isinstance(request, ExitRequest):
                self._handle_exit(rt, request)
            else:
                self._handle_rehedge(rt, request)

    def _handle_entry(self, rt: _PairRuntime, request: EntryRequest) -> None:
        cfg = self.config
        if rt.exec_state.phase not in (ExecutionPhase.IDLE, ExecutionPhase.CLOSED):
            return
        target_n = self._target_notional(rt, request.beta)
        if target_n is None:
            return
        try:
            sizing = size_pair(
                target_notional_usdt=Decimal(str(target_n)),
                beta=Decimal(str(request.beta)),
                y_price=Decimal(str(request.y_mark)),
                x_price=Decimal(str(request.x_mark)),
                y_contract=self._contract_spec(f"{rt.spec.y_symbol}.OKX"),
                x_contract=self._contract_spec(f"{rt.spec.x_symbol}.OKX"),
            )
        except ValueError as exc:   # below minSz etc. -> skip this entry
            self.log.warning(f"pair {rt.spec.pair_id} sizing failed: {exc}")
            return
        candidate = PairCandidate(
            pair_id=str(rt.spec.pair_id),
            y_asset=rt.spec.y_symbol.split("-")[0],
            x_asset=rt.spec.x_symbol.split("-")[0],
            sizing=sizing,
            margin_usdt=sizing.gross_notional_usdt / Decimal(str(cfg.leverage)),
            cost=self._estimate_cost(rt, sizing),
            y_price_usdt=Decimal(str(request.y_mark)),
            x_price_usdt=Decimal(str(request.x_mark)),
            y_side=request.side,
            x_side=-request.side,
        )
        decision = approve_pair(self._snapshot(), candidate, self._risk_limits(rt.spec))
        if not decision.approved:
            self.log.warning(f"pair {rt.spec.pair_id} entry rejected: {decision.reasons}")
            return
        self._order_seq += 1
        y_coid = f"K{rt.spec.pair_id:02d}-Y-{self._order_seq}"
        x_coid = f"K{rt.spec.pair_id:02d}-X-{self._order_seq}"
        deadline = request.ts_ms / 1000.0 + cfg.leg_timeout_hours * 3600
        result = begin_entry(rt.exec_state, sizing.y_quantity, sizing.x_quantity,
                             y_coid, x_coid, deadline, request.beta)
        rt.exec_state = result.state
        if result.reasons:
            return
        rt.entry_side = request.side
        rt.entry_beta = request.beta
        rt.entry_ts_ms = request.ts_ms
        rt.stop_hours = 0
        rt.hold_hours = 0
        rt.trade_fills = []
        rt.funding_obs = []
        rt.entry_avg = {}
        rt.decision_marks = {"Y": request.y_mark, "X": request.x_mark}
        for intent in result.actions:
            self._submit_intent(rt, intent, request.side)

    def _submit_intent(self, rt: _PairRuntime, intent, y_side: int) -> None:
        """y_side is the Y-leg side of the enclosing lifecycle step
        (+entry_side for entries, -entry_side for exits)."""
        if intent.kind == SUBMIT:
            leg = intent.leg
            side = y_side if leg == "Y" else -y_side
            self._submit_market(rt, leg, side, intent.quantity, intent.client_order_id)
        elif intent.kind == CANCEL_UNFILLED:
            self._cancel_order(intent.client_order_id)
        elif intent.kind == FLATTEN_FILLED:
            side = -self._held_side(rt, intent.leg)
            self._submit_market(rt, intent.leg, side, intent.quantity, self._next_coid(rt, intent.leg))

    def _handle_exit(self, rt: _PairRuntime, request: ExitRequest) -> None:
        if rt.exec_state.phase is not ExecutionPhase.OPEN:
            return
        self._order_seq += 1
        y_coid = f"K{rt.spec.pair_id:02d}-Y-{self._order_seq}"
        x_coid = f"K{rt.spec.pair_id:02d}-X-{self._order_seq}"
        result = begin_exit(rt.exec_state, y_coid, x_coid,
                            request.ts_ms / 1000.0 + self.config.leg_timeout_hours * 3600)
        rt.exec_state = result.state
        rt.exit_reason = request.reason
        rt.exit_emergency = request.emergency
        for intent in result.actions:   # SUBMIT with quantity = held qty
            self._submit_intent(rt, intent, -rt.entry_side)

    def _handle_rehedge(self, rt: _PairRuntime, request: RehedgeRequest) -> None:
        """Adjust only the X leg toward the new beta target (spec 짠2.4).
        Rehedge orders live outside the S05 entry/exit lifecycle; their fills
        patch the X leg target/filled directly."""
        if rt.exec_state.phase is not ExecutionPhase.OPEN:
            return
        x_mark = self._last_mark(f"{rt.spec.x_symbol}.OKX")
        y_mark = self._last_mark(f"{rt.spec.y_symbol}.OKX")
        target_n = self._target_notional(rt, request.beta)
        if target_n is None:
            return
        try:
            sizing = size_pair(
                target_notional_usdt=Decimal(str(target_n)),
                beta=Decimal(str(request.beta)),
                y_price=Decimal(str(y_mark)),
                x_price=Decimal(str(x_mark)),
                y_contract=self._contract_spec(f"{rt.spec.y_symbol}.OKX"),
                x_contract=self._contract_spec(f"{rt.spec.x_symbol}.OKX"),
            )
        except ValueError as exc:
            self.log.warning(f"pair {rt.spec.pair_id} rehedge sizing failed: {exc}")
            return
        ct_val = float(self._contract_spec(f"{rt.spec.x_symbol}.OKX").ct_val)
        held_coin = float(rt.exec_state.legs["X"].filled_quantity) * ct_val
        target_coin = float(sizing.x_quantity) * ct_val
        held_signed = -rt.entry_side * held_coin       # X held signed (long spread: X short)
        target_signed = -rt.entry_side * target_coin
        delta_signed = target_signed - held_signed
        min_coin = float(self._contract_spec(f"{rt.spec.x_symbol}.OKX").min_sz) * ct_val
        if abs(delta_signed) < min_coin:
            return
        self._order_seq += 1
        coid = f"K{rt.spec.pair_id:02d}-X-{self._order_seq}"
        self._coid_legs[coid] = (rt.spec.pair_id, "X")
        self._rehedge_coids.add(coid)
        side = 1 if delta_signed > 0 else -1
        qty = (Decimal(str(abs(delta_signed) / ct_val))
               .quantize(self._contract_spec(f"{rt.spec.x_symbol}.OKX").lot_sz))
        self._submit_market(rt, "X", side, qty, coid)
        rt.rehedge_target_x = sizing.x_quantity

    # --- fills, rejects, timeouts ----------------------------------------------

    def _apply_fill(self, rt: _PairRuntime, leg: str, fill_id: str, qty: Decimal,
                    price: Decimal, ts_ms: int, side: int) -> None:
        """side = order side of the fill (+1 buy, -1 sell); comes from the
        OrderFilled event (or the test)."""
        phase_before = rt.exec_state.phase
        if phase_before is ExecutionPhase.FLATTENING:
            result = confirm_flatten(rt.exec_state, [FillObservation(leg, fill_id, qty, price, price)],
                                     cooldown_deadline=ts_ms / 1000.0 + rt.spec.cooldown_hours * 3600)
        else:
            reference = Decimal(str(rt.decision_marks.get(leg, float(price))))
            result = on_fill(rt.exec_state, FillObservation(leg, fill_id, qty, price, reference))
        rt.exec_state = result.state
        if result.reasons == ("DUPLICATE_FILL",):
            return
        inst_id = f"{rt.spec.y_symbol if leg == 'Y' else rt.spec.x_symbol}.OKX"
        rt.trade_fills.append((leg, float(qty) * side, float(price)))
        rt.s02_fills.append(Fill(leg, float(qty) * side, float(price),
                                 self.config.taker_fee * self.config.cost_multiplier,
                                 self._half_spread_rate(inst_id)))
        if rt.exec_state.phase is ExecutionPhase.OPEN:
            rt.entry_avg[leg] = float(price)
        if rt.exec_state.phase is ExecutionPhase.CLOSED:
            self._finalize_trade(rt, ts_ms)

    def _on_order_filled(self, event) -> None:
        coid = event.client_order_id.value
        mapping = self._coid_legs.get(coid)
        if mapping is None:
            return
        pair_id, leg = mapping
        rt = self._pairs[pair_id]
        qty = event.last_qty.as_decimal()
        price = event.last_px.as_decimal()
        side = 1 if event.order_side == OrderSide.BUY else -1
        fill_id = f"{coid}:{event.ts_init}"
        if coid in self._rehedge_coids:
            self._apply_rehedge_fill(rt, leg, qty, price, side)
            return
        self._apply_fill(rt, leg, fill_id, qty, price, event.ts_init // 1_000_000, side)

    def _apply_rehedge_fill(self, rt: _PairRuntime, leg: str, qty: Decimal, price: Decimal, side: int) -> None:
        """Rehedge fills patch the X leg target/filled directly (no S05 phase)."""
        ct_val = self._contract_spec(f"{rt.spec.x_symbol}.OKX").ct_val
        coin_delta = Decimal(str(float(qty) * ct_val)) * side
        x_leg = rt.exec_state.legs["X"]
        new_filled = x_leg.filled_quantity + coin_delta / ct_val
        new_target = rt.rehedge_target_x if rt.rehedge_target_x is not None else x_leg.target_quantity
        legs = dict(rt.exec_state.legs)
        legs["X"] = replace(x_leg, target_quantity=new_target, filled_quantity=new_filled,
                            status=LegStatus.FILLED)
        rt.exec_state = replace(rt.exec_state, legs=MappingProxyType(legs))
        rt.trade_fills.append(("X", float(qty) * side, float(price)))
        rt.s02_fills.append(Fill("X", float(qty) * side, float(price),
                                 self.config.taker_fee * self.config.cost_multiplier,
                                 self._half_spread_rate(f"{rt.spec.x_symbol}.OKX")))

    def on_event(self, event) -> None:
        if isinstance(event, OrderFilled):
            self._on_order_filled(event)
        elif isinstance(event, OrderRejected):
            mapping = self._coid_legs.get(event.client_order_id.value)
            if mapping is None:
                return
            rt = self._pairs[mapping[0]]
            result = on_order_failure(rt.exec_state, mapping[1])
            rt.exec_state = result.state
            for intent in result.actions:
                self._submit_intent(rt, intent, rt.entry_side)
            if rt.exec_state.phase is ExecutionPhase.CLOSED:
                self._finalize_trade(rt, self._last_ts_ms)
            elif rt.exec_state.phase is ExecutionPhase.IDLE:
                self._reset_open_flags(rt)

    def _reset_open_flags(self, rt: _PairRuntime) -> None:
        rt.entry_side = 0
        rt.entry_beta = None
        rt.stop_hours = 0
        rt.hold_hours = 0
        rt.trade_fills = []
        rt.s02_fills = []
        rt.funding_obs = []
        rt.entry_avg = {}

    def _check_timeout(self, rt: _PairRuntime, now_s: float):
        result = on_timeout(rt.exec_state, now_s)
        if result.state is not rt.exec_state:
            rt.exec_state = result.state
            for intent in result.actions:
                self._submit_intent(rt, intent, rt.entry_side)
            if rt.exec_state.phase is ExecutionPhase.CLOSED:
                self._finalize_trade(rt, int(now_s * 1000))
            elif rt.exec_state.phase is ExecutionPhase.IDLE:
                self._reset_open_flags(rt)
        return None

    # --- cost accounting and portfolio snapshot ---------------------------------

    def _finalize_trade(self, rt: _PairRuntime, ts_ms: int) -> None:
        fills = rt.s02_fills
        funding = rt.funding_obs
        expected = list(range(rt.entry_ts_ms // FUNDING_PERIOD_MS * FUNDING_PERIOD_MS + FUNDING_PERIOD_MS,
                              ts_ms, FUNDING_PERIOD_MS))
        cost, cost_error = None, None
        if fills and expected:
            try:
                cost = calculate_cost(fills, funding, expected_funding_timestamps=expected)
            except ValueError as exc:
                cost_error = str(exc)   # missing/duplicate funding fails closed (S02 contract)
        elif fills:
            cost_error = "no funding timestamps in holding period"
        pnl = -sum(signed * price for _, signed, price in rt.trade_fills)
        self._realized_pnl_usdt += pnl
        if cost is not None:
            self._realized_pnl_usdt -= cost.total_usdt
        self._trade_records.append({
            "pair_id": rt.spec.pair_id,
            "entry_ts_ms": rt.entry_ts_ms,
            "exit_ts_ms": ts_ms,
            "reason": rt.exit_reason or "UNKNOWN",
            "pnl_usdt": pnl,
            "cost": cost,
            "cost_error": cost_error,
        })
        rt.exec_state = replace(rt.exec_state,
                                cooldown_deadline=ts_ms / 1000.0 + rt.spec.cooldown_hours * 3600)
        self._reset_open_flags(rt)

    def _accrue_funding(self, rt: _PairRuntime, ts_ms: int) -> None:
        """Record S02 FundingObservations for both legs at OKX 8h stamps while open."""
        if rt.entry_side == 0 or ts_ms % FUNDING_PERIOD_MS != 0:
            return
        for leg, symbol in (("Y", rt.spec.y_symbol), ("X", rt.spec.x_symbol)):
            inst = f"{symbol}.OKX"
            rate = self._funding.get(inst, {}).get(ts_ms)
            if rate is None:
                continue   # absent observation -> calculate_cost fails closed at close
            held_side = self._held_side(rt, leg)
            mark = self._last_mark(inst)
            rt.funding_obs.append(FundingObservation(
                leg, ts_ms, float(rt.exec_state.legs[leg].filled_quantity) * held_side, mark, rate))

    def _snapshot(self) -> PortfolioSnapshot:
        equity = Decimal(str(self._equity()))
        gross = Decimal(str(self._gross_notional()))
        margin_in_use = sum(
            (self._pairs[p].exec_state.legs["Y"].filled_quantity * Decimal(str(self._last_mark(f"{self._pairs[p].spec.y_symbol}.OKX"))) * Decimal(str(self._contract_spec(f"{self._pairs[p].spec.y_symbol}.OKX").ct_val))
             for p in self._open_pairs()), Decimal("0"))
        day = self._last_ts_ms // DAY_MS
        if day != self._day_key:
            self._day_key = day
            self._day_start_equity = float(equity)
        self._equity_high = max(self._equity_high, float(equity))
        return PortfolioSnapshot(
            equity_usdt=equity,
            peak_equity_usdt=Decimal(str(self._equity_high)),
            daily_loss_usdt=Decimal(str(max(0.0, self._day_start_equity - float(equity)))),
            liquidation_buffer_usdt=equity - margin_in_use,
            active_pairs=frozenset(str(p) for p in self._open_pairs()),
            gross_notional_usdt=gross,
            asset_exposure_usdt=self._asset_exposure(),
        )

    def _risk_limits(self, spec: PairBandSpec) -> RiskLimits:
        equity = Decimal(str(self._equity()))
        return RiskLimits(
            max_active_pairs=4,
            max_gross_exposure_ratio=Decimal("1.5"),
            max_pair_margin_ratio=Decimal(str(spec.margin_cap_ratio)),
            max_asset_exposure_usdt=equity * Decimal("1.5"),
            max_daily_loss_usdt=equity * Decimal("0.03"),
            max_drawdown_ratio=Decimal("0.15"),
            min_liquidation_buffer_usdt=Decimal("0"),
        )
```

Plus module-level loaders and small helpers (`_load_contract_specs`, `_load_funding_dir`, `_load_events` already exists, `_contract_spec`, `_last_mark`, `_half_spread_rate`, `_target_notional`, `_estimate_cost`, `_equity`, `_gross_notional`, `_open_pairs`, `_asset_exposure`, `_held_side`, `_next_coid`, `_submit_market`, `_cancel_order`, `_reset_open_flags` as defined above, and `_last_ts_ms` tracking in `on_bar`). Imports to add at module top:

```python
import math
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import OrderFilled, OrderRejected

from sngw_trader.indicators.cost_model import CostBreakdown, Fill, FundingObservation, calculate_cost
from sngw_trader.indicators.contract_sizing import ContractSpec, contract_spec_from_instrument, size_pair
from sngw_trader.indicators.event_gate import EXIT_EMERGENCY, EXIT_NONE, EXIT_ORDERLY  # already imported in Task 2
from sngw_trader.indicators.execution_recovery import (
    LegStatus,
    begin_entry,
    begin_exit,
    confirm_flatten,
    FillObservation,
    on_fill,
    on_order_failure,
    on_timeout,
    SUBMIT,
    CANCEL_UNFILLED,
    FLATTEN_FILLED,
)
from sngw_trader.indicators.portfolio_risk import (
    PairCandidate,
    PortfolioSnapshot,
    RiskLimits,
    approve_pair,
)
```

Module-level loaders:

```python
def _load_contract_specs(path: str) -> dict[str, ContractSpec]:
    if not path:
        return {}
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    return {
        inst_id: ContractSpec(Decimal(str(row["ct_val"])), Decimal(str(row["lot_sz"])),
                              Decimal(str(row["min_sz"])), int(row["price_precision"]))
        for inst_id, row in rows.items()
    }


def _load_funding_dir(path: str) -> dict[str, dict[int, float]]:
    if not path:
        return {}
    out: dict[str, dict[int, float]] = {}
    for file in Path(path).glob("*.json"):
        with open(file, encoding="utf-8") as f:
            rows = json.load(f)
        out[file.stem] = {int(ts): float(rate) for ts, rate in rows.items()}
    return out
```

```python
    # methods on KalmanSpreadStrategy

    def _contract_spec(self, inst_id: str) -> ContractSpec:
        spec = self._contracts.get(inst_id)
        if spec is not None:
            return spec
        instrument = self.cache.instrument(InstrumentId.from_str(inst_id))
        if instrument is None:
            raise ValueError(f"no contract spec for {inst_id}")
        return contract_spec_from_instrument(instrument)

    def _last_mark(self, inst_id: str) -> float:
        bar = self._bars.get(InstrumentId.from_str(inst_id))
        if bar is None:
            raise ValueError(f"no mark yet for {inst_id}")
        return float(bar.close.as_decimal())

    def _half_spread_rate(self, inst_id: str) -> float:
        bps = self.config.btc_half_spread_bps if inst_id.startswith("BTC") else self.config.alt_half_spread_bps
        return bps / 10_000 * self.config.cost_multiplier

    def _target_notional(self, rt: _PairRuntime, beta: float) -> float | None:
        """N = equity * risk_frac * sigma_multiple * size_weight / sigma_u,
        clamped by the pair margin cap (margin = gross / leverage)."""
        if rt.sigma_u is None or rt.sigma_u <= 0:
            return None
        cfg = self.config
        equity = self._equity()
        n = equity * cfg.risk_frac * rt.spec.sigma_multiple * rt.spec.size_weight / rt.sigma_u
        gross = n * (1.0 + beta)
        cap = equity * rt.spec.margin_cap_ratio
        if gross > cap:
            n *= cap / gross
        return n

    def _estimate_cost(self, rt: _PairRuntime, sizing) -> CostBreakdown:
        """Entry-time round-trip estimate for the S04 gate (realized cost is
        accounted per fill via S02 at trade close)."""
        cfg = self.config
        gross = float(sizing.gross_notional_usdt)
        fee = 2 * cfg.taker_fee * cfg.cost_multiplier * gross
        spread = 2 * 0.5 * (self._half_spread_rate(f"{rt.spec.y_symbol}.OKX") + self._half_spread_rate(f"{rt.spec.x_symbol}.OKX")) * gross
        funding = cfg.funding_rate_assumption * gross
        return CostBreakdown(fee, spread, funding, fee + spread + funding)

    def _equity(self) -> float:
        return self.config.equity_usdt + self._realized_pnl_usdt + self._unrealized_pnl()

    def _open_pairs(self) -> list[int]:
        return [pid for pid, rt in self._pairs.items() if rt.entry_side != 0]

    def _unrealized_pnl(self) -> float: ...
    def _gross_notional(self) -> float: ...
    def _asset_exposure(self) -> dict[str, Decimal]: ...
    def _held_side(self, rt, leg) -> int: ...
    def _next_coid(self, rt, leg) -> str: ...
    def _submit_market(self, rt, leg, side, qty, coid): ...
    def _cancel_order(self, coid): ...
```

The remaining elided helpers have fixed contracts (write them fully in the implementation):
- `_unrealized_pnl`: sum over open pairs of signed held coin qty * (last mark - entry avg price) per leg -> USDT. Signed held qty for leg = `self._held_side(rt, leg) * filled_quantity * ct_val`; skip legs with no `rt.entry_avg[leg]`.
- `_gross_notional`: sum over open pairs of per-leg coin qty * ct_val * last mark.
- `_asset_exposure`: dict[base_asset, signed Decimal notional] across open pairs (base asset = symbol.split("-")[0]).
- `_held_side(rt, leg)`: `rt.entry_side` for "Y", `-rt.entry_side` for "X" (0 when flat).
- `_next_coid(rt, leg)`: increments `self._order_seq`, returns `f"K{rt.spec.pair_id:02d}-{leg}-{self._order_seq}"` and registers `self._coid_legs[coid] = (rt.spec.pair_id, leg)`.
- `_submit_market`: builds `Quantity(qty, size_precision of the instrument contract spec)` and calls `self.order_factory.market(instrument_id=InstrumentId.from_str(inst_id), order_side=OrderSide.BUY if side > 0 else OrderSide.SELL, quantity=..., client_order_id=ClientOrderId(coid))`, then registers `self._coid_legs[coid] = (rt.spec.pair_id, leg)`.
- `_cancel_order`: looks up the order in `self.cache.order(ClientOrderId(coid))` and cancels it if present (unit tests override this with a recording no-op).
- `_last_ts_ms`: set in `on_bar` from the bar timestamp before dispatch; also update `on_start` to initialize `self._last_ts_ms = 0` and `self._rehedge_coids: set[str] = set()`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_strategies/test_kalman_spread_strategy.py -q`
Expected: all PASS (Task 2 tests + Task 3 tests).

- [ ] **Step 5: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: no regressions in existing suites.

- [ ] **Step 6: Commit**

```bash
git add src/sngw_trader/strategies/kalman_spread.py tests/test_strategies/test_kalman_spread_strategy.py
git commit -m "feat(s07): bind sizing, risk, execution, events, and cost accounting"
```

---

### Task 4: Catalog writer — 1H mark candles + funding history

**Files:**
- Modify: `src/sngw_trader/data/catalog_writer.py`
- Test: `tests/test_data/test_catalog_writer.py` (append)

**Interfaces:**
- Consumes: `fetch_funding_rates(inst_id, start_ms, end_ms) -> dict[int, float]` (existing `data/funding.py`).
- Produces (used by Task 5 / Phase 2):
  - `download_mark_bars(settings, instrument, symbol: str, bar: str = "1H") -> list[Bar]` — paginates `history-mark-price-candles`.
  - `download_universe_mark_bars(settings, catalog, symbols: Sequence[str], bar: str = "1H") -> dict` — writes instrument + bars per symbol, returns report `{symbol: {"n": int, "first": str, "last": str}}`.
  - `write_funding_history(inst_ids: Sequence[str], start_ms: int, end_ms: int, out_dir: Path) -> dict[str, dict[int, float]]` — one JSON per instId (`{str(ts_ms): rate}`), overwrite (idempotent).

- [ ] **Step 1: Write the failing tests (append)**

```python
# appended to tests/test_data/test_catalog_writer.py
from decimal import Decimal

from nautilus_trader.model import BarType

from sngw_trader.data import catalog_writer as cw


def test_raw_mark_candle_to_bar_maps_fields():
    bar = cw.raw_mark_candle_to_bar(
        ["1700000000000", "30000.5", "30100.0", "29950.0", "30080.2", "1"],
        "BTC-USDT-SWAP.OKX",
        price_prec=2,
        size_prec=3,
    )
    assert str(bar.bar_type) == "BTC-USDT-SWAP.OKX-1-HOUR-MARK-EXTERNAL"
    assert bar.close.as_decimal() == Decimal("30080.2")
    assert bar.volume.as_decimal() == Decimal("0")   # mark candles carry no volume


def test_download_mark_bars_paginates_and_sorts(monkeypatch):
    pages = {
        0: [["1700000000000", "1", "1", "1", "1", "1"], ["1700003600000", "2", "2", "2", "2", "1"]],
    }

    def fake_fetch(url, params):
        assert "history-mark-price-candles" in url
        after = int(params.get("after", 0))
        if after == 0:
            return pages[0]
        return []          # second page empty -> stop

    monkeypatch.setattr(cw, "_fetch_okx", fake_fetch)
    bars = cw.download_mark_bars(_settings_stub(), _instrument_stub(), "BTC-USDT-SWAP", "1H")
    assert [b.ts_init for b in bars] == sorted(b.ts_init for b in bars)
    assert len(bars) == 2


def test_write_funding_history_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cw, "fetch_funding_rates", lambda inst, s, e: {1_700_000_000_000: 0.0001, 1_700_028_800_000: -0.0002}
    )
    out = cw.write_funding_history(["BTC-USDT-SWAP"], 0, 2_000_000_000_000, tmp_path)
    assert out["BTC-USDT-SWAP"][1_700_000_000_000] == 0.0001
    loaded = (tmp_path / "BTC-USDT-SWAP.json").read_text()
    assert "1700000000000" in loaded
```

`_settings_stub` / `_instrument_stub`: follow the existing fakes already used in `tests/test_data/test_catalog_writer.py` (read that file first and reuse its pattern; if it has none, build minimal objects exposing `catalog_start`, `catalog_end`, `instrument_id_str`, `symbol`, `price_precision`, `size_precision`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_data/test_catalog_writer.py -q`
Expected: new tests FAIL.

- [ ] **Step 3: Implement**

```python
# additions to src/sngw_trader/data/catalog_writer.py

_MARK_HISTORY_URL = "https://www.okx.com/api/v5/market/history-mark-price-candles"

_BAR_SPEC = {"1H": "1-HOUR", "1m": "1-MINUTE"}

# module-level import (top of file): from sngw_trader.data.funding import fetch_funding_rates
# so tests can monkeypatch cw.fetch_funding_rates; funding.py imports nothing from this module.


def _fetch_okx(url: str, params: dict) -> list[list[str]]:
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("code") != "0":
        raise RuntimeError(f"OKX API error: {payload}")
    return payload["data"]


def raw_mark_candle_to_bar(
    raw: list[str], instrument_id: str, price_prec: int, size_prec: int, bar: str = "1H"
) -> Bar:
    """row: [ts, o, h, l, c, confirm]; mark candles carry no volume."""
    ts_ns = int(raw[0]) * 1_000_000
    bar_type = BarType.from_str(f"{instrument_id}-{_BAR_SPEC[bar]}-MARK-EXTERNAL")
    scale = Decimal(10) ** 9
    return Bar.from_raw(
        bar_type=bar_type,
        open=Decimal(raw[1]) * scale,
        high=Decimal(raw[2]) * scale,
        low=Decimal(raw[3]) * scale,
        close=Decimal(raw[4]) * scale,
        price_prec=price_prec,
        volume=Decimal("0"),
        size_prec=size_prec,
        ts_event=ts_ns,
        ts_init=ts_ns,
    )


def download_mark_bars(settings, instrument, symbol: str, bar: str = "1H") -> list[Bar]:
    _validate_range(settings)
    start_ms = int(settings.catalog_start.astimezone(timezone.utc).timestamp() * 1000)
    end_ms = int(settings.catalog_end.astimezone(timezone.utc).timestamp() * 1000)
    bars: list[Bar] = []
    oldest = None
    while True:
        rows = _fetch_okx(_MARK_HISTORY_URL, {"instId": symbol, "bar": bar, "limit": "100", **({"after": str(oldest)} if oldest else {})})
        if not rows:
            break
        for raw in rows:
            ts_ms = int(raw[0])
            if start_ms <= ts_ms <= end_ms:
                bars.append(raw_mark_candle_to_bar(raw, settings.instrument_id_str,
                                                   instrument.price_precision, instrument.size_precision, bar))
        oldest = int(rows[-1][0])
        if oldest < start_ms or len(rows) < 100:
            break
        time.sleep(0.15)  # OKX public rate limit guard
    bars.sort(key=lambda b: b.ts_init)
    return bars


def download_universe_mark_bars(settings, catalog, symbols, bar: str = "1H") -> dict:
    from nautilus_trader.model import Instrument

    instrument = load_instrument(settings)
    catalog.write_data([instrument], data_cls=Instrument)
    report = {}
    for symbol in symbols:
        bars = download_mark_bars(settings, instrument, symbol, bar)
        if not bars:
            report[symbol] = {"n": 0, "first": None, "last": None}
            continue
        catalog.write_data(bars, data_cls=Bar)
        report[symbol] = {
            "n": len(bars),
            "first": _fmt(bars[0].ts_event),
            "last": _fmt(bars[-1].ts_event),
        }
        print(f"{symbol}: {len(bars)} bars {_fmt(bars[0].ts_event)} -> {_fmt(bars[-1].ts_event)}")
    return report


def write_funding_history(inst_ids, start_ms: int, end_ms: int, out_dir: Path) -> dict[str, dict[int, float]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    history: dict[str, dict[int, float]] = {}
    for inst_id in inst_ids:
        rates = fetch_funding_rates(inst_id, start_ms, end_ms)
        history[inst_id] = rates
        with (out_dir / f"{inst_id}.json").open("w", encoding="utf-8") as f:
            json.dump({str(ts): rate for ts, rate in sorted(rates.items())}, f)
    return history
```

Also refactor `_fetch_candles` to delegate to `_fetch_okx(_HISTORY_URL, params)` so there is one HTTP path (behavior unchanged, existing tests stay green).

- [ ] **Step 4: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_data/ -q`
Expected: all PASS (existing catalog-writer tests included).

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/data/catalog_writer.py tests/test_data/test_catalog_writer.py
git commit -m "feat(s07): okx 1h mark candle and funding history download"
```

---

### Task 5: Multi-instrument run config + walk-forward runner

**Files:**
- Modify: `src/sngw_trader/runners/backtest_okx.py` (add builder; change nothing existing)
- Create: `research/kalman_walkforward.py`
- Test: `tests/test_runners/test_kalman_runner.py` (create)

**Interfaces:**
- Consumes: `BacktestVenueConfig`, `BacktestDataConfig`, `OkxRateFeeModel(Config)`, `FillModelConfig`, `LatencyModelConfig` (existing runner helpers).
- Produces:
  - `build_multi_instrument_run_config(catalog_path: str, instrument_ids: Sequence[str], *, start: datetime, end: datetime, taker_fee: float, maker_fee: float, prob_slippage: float, prob_fill_on_limit: float, latency_ms: int = 10, starting_balance: str = "10_000 USDT", quiet: bool = True) -> BacktestRunConfig`
  - `research/kalman_walkforward.py` CLI: `python research/kalman_walkforward.py --config research/kalman/config.json` — runs node, writes `results.json` (config echo + order/trade counts) and `fills.csv` next to it.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_runners/test_kalman_runner.py
from datetime import datetime, timezone

from sngw_trader.runners.backtest_okx import build_multi_instrument_run_config

IDS = ("ETH-USDT-SWAP.OKX", "BTC-USDT-SWAP.OKX")


def test_multi_instrument_run_config_shape():
    cfg = build_multi_instrument_run_config(
        "catalog", IDS,
        start=datetime(2024, 9, 1, tzinfo=timezone.utc),
        end=datetime(2024, 10, 1, tzinfo=timezone.utc),
        taker_fee=0.0005, maker_fee=0.0002,
        prob_slippage=0.5, prob_fill_on_limit=0.9,
    )
    assert len(cfg.data) == 2
    assert cfg.venues[0].name == "OKX"
    assert cfg.venues[0].fee_model.config["taker_fee_rate"] == 0.0005
    assert cfg.venues[0].fill_model.config.prob_slippage == 0.5
    assert cfg.venues[0].fill_model.config.prob_fill_on_limit == 0.9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_runners/test_kalman_runner.py -q`
Expected: FAIL — `ImportError: build_multi_instrument_run_config`.

- [ ] **Step 3: Implement builder + runner**

```python
# addition to src/sngw_trader/runners/backtest_okx.py

def build_multi_instrument_run_config(
    catalog_path: str,
    instrument_ids,
    *,
    start: datetime,
    end: datetime,
    taker_fee: float,
    maker_fee: float,
    prob_slippage: float,
    prob_fill_on_limit: float,
    latency_ms: int = 10,
    starting_balance: str = "10_000 USDT",
    quiet: bool = True,
) -> BacktestRunConfig:
    """OKX venue + one bar-data config per instrument. Assembly only."""
    venue = BacktestVenueConfig(
        name="OKX",
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        book_type="L1_MBP",
        starting_balances=[starting_balance],
        fill_model=ImportableFillModelConfig(
            fill_model_path="nautilus_trader.backtest.models:ProbabilisticFillModel",
            config_path="nautilus_trader.backtest.config:FillModelConfig",
            config=FillModelConfig(
                prob_fill_on_limit=prob_fill_on_limit,
                prob_slippage=prob_slippage,
                random_seed=42,
            ),
        ),
        fee_model=ImportableFeeModelConfig(
            fee_model_path="sngw_trader.runners.backtest_models:OkxRateFeeModel",
            config_path="sngw_trader.runners.backtest_models:OkxRateFeeModelConfig",
            config={"maker_fee_rate": maker_fee, "taker_fee_rate": taker_fee},
        ),
        latency_model=ImportableLatencyModelConfig(
            latency_model_path="nautilus_trader.backtest.models:LatencyModel",
            config_path="nautilus_trader.backtest.config:LatencyModelConfig",
            config=LatencyModelConfig(base_latency_nanos=latency_ms * 1_000_000),
        ),
    )
    data = [
        BacktestDataConfig(
            data_cls=Bar,
            catalog_path=catalog_path,
            instrument_id=iid,
            start_time=start.isoformat(),
            end_time=end.isoformat(),
        )
        for iid in instrument_ids
    ]
    return BacktestRunConfig(
        venues=[venue],
        data=data,
        engine=BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True)) if quiet else BacktestEngineConfig(),
        dispose_on_completion=True,
        raise_exception=True,
    )
```

```python
# research/kalman_walkforward.py
"""S07 walk-forward runner. BacktestNode assembly only — no trading conditions."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.trading.config import ImportableStrategyConfig

from sngw_trader.runners.backtest_okx import build_multi_instrument_run_config

DEFAULT_CONFIG = {
    "catalog_path": "catalog",
    "instrument_ids": ["ETH-USDT-SWAP.OKX", "BTC-USDT-SWAP.OKX"],
    "pair_ids": [1],
    "start": "2024-09-01T00:00:00+00:00",
    "end": "2025-09-01T00:00:00+00:00",
    "taker_fee": 0.0005,
    "maker_fee": 0.0002,
    "cost_multiplier": 1.0,
    "prob_slippage": 0.5,
    "prob_fill_on_limit": 0.9,
    "formation_hours": 720,
    "trading_hours": 240,
    "equity_usdt": 10000.0,
    "funding_dir": "catalog/funding",
    "events_path": "",
    "output_dir": "research/kalman",
}


def build_strategy_config(cfg: dict) -> dict:
    return {
        "strategy_path": "sngw_trader.strategies.kalman_spread:KalmanSpreadStrategy",
        "config_path": "sngw_trader.strategies.kalman_spread:KalmanSpreadConfig",
        "config": {
            "instrument_ids": tuple(cfg["instrument_ids"]),
            "pair_ids": tuple(cfg["pair_ids"]),
            "formation_hours": cfg["formation_hours"],
            "trading_hours": cfg["trading_hours"],
            "equity_usdt": cfg["equity_usdt"],
            "taker_fee": cfg["taker_fee"] * cfg["cost_multiplier"],
            "cost_multiplier": cfg["cost_multiplier"],
            "funding_dir": cfg["funding_dir"],
            "events_path": cfg["events_path"],
        },
    }


def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="research/kalman/config.json")
    args = parser.parse_args(argv)
    cfg = {**DEFAULT_CONFIG, **json.loads(Path(args.config).read_text(encoding="utf-8"))}
    run_config = build_multi_instrument_run_config(
        cfg["catalog_path"], cfg["instrument_ids"],
        start=datetime.fromisoformat(cfg["start"]), end=datetime.fromisoformat(cfg["end"]),
        taker_fee=cfg["taker_fee"] * cfg["cost_multiplier"],
        maker_fee=cfg["maker_fee"] * cfg["cost_multiplier"],
        prob_slippage=cfg["prob_slippage"], prob_fill_on_limit=cfg["prob_fill_on_limit"],
    )
    node = BacktestNode(configs=[run_config])
    node.build()
    strategy_cfg = ImportableStrategyConfig(**build_strategy_config(cfg))
    node.add_strategy(run_config.id, strategy_cfg)
    try:
        node.run()
        trader = next(e.trader for e in node.get_engines() if hasattr(e, "trader"))
        out_dir = Path(cfg["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        trader.generate_order_fills_report().to_csv(out_dir / "fills.csv", index=False)
        results = {
            "config": {k: v for k, v in cfg.items()},
            "n_orders": len(trader.generate_orders_report()),
        }
        (out_dir / "results.json").write_text(json.dumps(results, indent=2, default=str))
        print(f"wrote {out_dir / 'results.json'}")
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
```

If `node.add_strategy(run_config.id, strategy_cfg)` does not accept an `ImportableStrategyConfig` on nautilus 1.231.0, use the `StrategyFactory.create(...)` instance + `attach_strategy(...)` pattern from `src/sngw_trader/research/executor.py` instead — same behavior, instance-based.

- [ ] **Step 4: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_runners/ -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/runners/backtest_okx.py research/kalman_walkforward.py tests/test_runners/test_kalman_runner.py
git commit -m "feat(s07): multi-instrument backtest run config and walk-forward runner"
```

---

### Task 6: End-to-end smoke run on synthetic catalog

**Files:**
- Create: `tests/test_research/test_kalman_smoke.py`

**Interfaces:**
- Consumes: Task 2-5 outputs; `CryptoPerpetual` constructor (test-kit pattern); `ParquetDataCatalog.write_data`.

- [ ] **Step 1: Write the smoke test**

```python
# tests/test_research/test_kalman_smoke.py
"""E2E smoke: 2 synthetic instruments, ~42 days of 1h mark bars, one pair.

Uses a shrunk formation/trading window (72/48) so full cycles fit the
synthetic window, and forces the screen verdict via monkeypatch (gate logic
is covered by the Task 1 unit tests) so entries are deterministic. The
spread is shocked during the first two trading windows so |z| crosses the
entry band. Asserts the run completes and produces fill pairs.
"""

import json
import math
import random
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from nautilus_trader.model import Bar
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments.crypto_perpetual import CryptoPerpetual
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog

from sngw_trader.research.kalman_walkforward import (
    DEFAULT_CONFIG,
    build_multi_instrument_run_config,
    build_strategy_config,
)

IDS = ("ETH-USDT-SWAP.OKX", "BTC-USDT-SWAP.OKX")
HOUR_MS = 3_600_000


def _okx_swap(inst_id: str) -> CryptoPerpetual:
    symbol = inst_id.split("-")[0]
    return CryptoPerpetual(
        instrument_id=InstrumentId(Symbol(f"{symbol}-USDT-SWAP"), Venue("OKX")),
        raw_symbol=Symbol(f"{symbol}USDT"),
        base_currency=None,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=2,
        size_precision=3,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.001"),
        max_quantity=Quantity.from_str("10000.000"),
        min_quantity=Quantity.from_str("0.001"),
        max_notional=None,
        min_notional=Money(10.00, USDT),
        max_price=Price.from_str("1000000.00"),
        min_price=Price.from_str("0.01"),
        margin_init=Decimal("1.00"),
        margin_maint=Decimal("0.35"),
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0005"),
        ts_event=0,
        ts_init=0,
    )


def _write_synthetic_catalog(catalog: ParquetDataCatalog) -> None:
    from nautilus_trader.model import BarType

    for inst_id in IDS:
        catalog.write_data([_okx_swap(inst_id)])
    rng = random.Random(5)
    x = 30_000.0
    e = 0.0
    start_ms = 1_700_000_000_000 - (1_700_000_000_000 % HOUR_MS)
    y_bars, x_bars = [], []
    for h in range(1000):
        shock = 0.03 if (80 <= h < 100 or 200 <= h < 220) else 0.0  # z spikes in trading windows
        x *= math.exp(rng.gauss(0.0, 0.008))
        e = 0.9 * e + rng.gauss(0.0, 0.004) + shock
        y = 2.0 * x * math.exp(e)
        ts = start_ms + h * HOUR_MS
        for inst_id, close, bucket in ((IDS[1], x, x_bars), (IDS[0], y, y_bars)):
            ns = ts * 1_000_000
            bucket.append(Bar.from_raw(
                bar_type=BarType.from_str(f"{inst_id}-1-HOUR-MARK-EXTERNAL"),
                open=Decimal(str(close)), high=Decimal(str(close)),
                low=Decimal(str(close)), close=Decimal(str(close)),
                price_prec=2, volume=Decimal("100"), size_prec=3,
                ts_event=ns, ts_init=ns,
            ))
    catalog.write_data(x_bars + y_bars, data_cls=Bar)


def test_smoke_run_completes_with_output(tmp_path, monkeypatch):
    from sngw_trader.indicators import pair_screening as ps
    import sngw_trader.strategies.kalman_spread as strategy_module

    # Deterministic screen verdict; gate math is unit-tested in Task 1.
    monkeypatch.setattr(strategy_module, "screen_pair",
                        lambda *a, **k: ps.ScreenDecision(True, 80, ()))
    catalog = ParquetDataCatalog(tmp_path / "catalog")
    _write_synthetic_catalog(catalog)
    cfg = {
        **DEFAULT_CONFIG,
        "catalog_path": str(tmp_path / "catalog"),
        "instrument_ids": list(IDS),
        "pair_ids": [1],
        "formation_hours": 72,
        "trading_hours": 48,
        "funding_dir": "",
        "events_path": "",
        "output_dir": str(tmp_path / "out"),
        "start": "2023-11-14T22:00:00+00:00",
        "end": "2023-12-27T00:00:00+00:00",
    }
    from nautilus_trader.backtest.node import BacktestNode
    from nautilus_trader.trading.config import ImportableStrategyConfig

    run_config = build_multi_instrument_run_config(
        cfg["catalog_path"], cfg["instrument_ids"],
        start=datetime(2023, 11, 14, 22, tzinfo=timezone.utc),
        end=datetime(2023, 12, 27, tzinfo=timezone.utc),
        taker_fee=0.0005, maker_fee=0.0002,
        prob_slippage=0.5, prob_fill_on_limit=0.9,
    )
    node = BacktestNode(configs=[run_config])
    node.build()
    node.add_strategy(run_config.id, ImportableStrategyConfig(**build_strategy_config(cfg)))
    try:
        node.run()
        trader = next(e.trader for e in node.get_engines() if hasattr(e, "trader"))
        fills = trader.generate_order_fills_report()
        assert len(fills) >= 2, "expected at least one entry fill pair"
    finally:
        node.dispose()
```

Contract specs resolve from the catalog instruments via `contract_spec_from_instrument` (cache fallback), so no specs JSON is needed.

- [ ] **Step 2: Run the smoke test**

Run: `.venv\Scripts\python.exe -m pytest tests/test_research/test_kalman_smoke.py -q`
Expected: PASS. If the engine rejects orders due to missing quotes, verify the bars are `MARK-EXTERNAL` type on both data config and subscription — the venue needs the same bar type it receives.

- [ ] **Step 3: Full suite + commit**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: all PASS (nautilus-dependent suite runs in the worktree venv).

```bash
git add tests/test_research/test_kalman_smoke.py
git commit -m "test(s07): end-to-end kalman spread backtest smoke on synthetic catalog"
```

---

## Phase 1 Completion Checklist

- [ ] All tasks committed; full suite green in the worktree venv.
- [ ] Strategy emits no exchange I/O outside `order_factory` wrappers; runner contains no trading conditions.
- [ ] Phase 2 prerequisites ready: catalog writer can produce 1H mark bars + funding JSONs; runner reads a JSON config; strategy accepts `cost_multiplier`/fill params via config.
- [ ] Session handoff note appended to `docs/superpowers/plans/2026-09-15-kalman-mr-s07-validation.md` (decisions, tests run, open items for Phase 2).
