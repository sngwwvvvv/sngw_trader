# Volatility Target Sizing Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the on/off `vol_filter` with a shared Harvey-style vol-target sizer (pure indicator) and band rebalance, wired into Spec A and Spec B.

**Architecture:** `VolTargetSizer` in `indicators/` computes EWMA σ, scale, desired signed qty, and whether to rebalance. Strategies own direction and `submit_order`. Factory copies config fields only. No Actor, no new runner.

**Tech Stack:** Python 3.12+, NautilusTrader Strategy/StrategyConfig, pytest, Decimal.

**Spec:** `docs/superpowers/specs/2026-09-01-vol-target-sizing-design.md`

## Global Constraints

- No custom runner, no `ccxt` / `python-okx` / exchange I/O in strategies or indicators.
- Indicators must not import Nautilus Strategy/Actor/nodes.
- Factory must not import `vol_targeting` (strategies construct the sizer).
- `vol_filter_*` is deleted, not disabled. No alias.
- No ex-post `k`. `periods_per_year` is 365, not a Settings field.
- ATR stop / ERMOM / ribbon FSM formulas stay. 4-cell research harness is out of scope.
- TDD: failing test first for each behavior. `pytest` from repo root.

## File map

| File | Role |
|---|---|
| `src/sngw_trader/indicators/vol_targeting.py` | Create: `VolTargetConfig`, `VolTargetSizer` |
| `tests/test_indicators/test_vol_targeting.py` | Create: engine unit tests |
| `src/sngw_trader/indicators/risk_metrics.py` | Delete `RealizedVol` |
| `tests/test_indicators/test_risk_metrics.py` | Delete RealizedVol tests |
| `src/sngw_trader/config/settings.py` | Replace vol_filter fields with sizing fields |
| `.env.example` | SIZE_* / SIZING_MODE |
| `src/sngw_trader/runners/strategy_factory.py` | Pass sizing fields, drop vol_filter |
| both strategies | Drop filter, hold sizer, UTC daily update, `_submit(side, qty, seed_stop)` |
| Settings/strategy/runner tests | Fixture field swap |
| `research/grids/*.json` | Drop vol_filter / vol_threshold axes |

---

### Task 1: VolTargetSizer math (config, EWMA, scale, desired_qty)

**Files:**
- Create: `src/sngw_trader/indicators/vol_targeting.py`
- Test: `tests/test_indicators/test_vol_targeting.py`

**Interfaces:**
- Consumes: stdlib + `decimal.Decimal` only
- Produces:
  - `VolTargetConfig(target_vol, half_life, min_scale, max_scale, rebalance_band, periods_per_year, mode)` frozen dataclass
  - `VolTargetSizer(config).update(close: float) -> float | None`
  - `VolTargetSizer.scale() -> float | None`
  - `VolTargetSizer.desired_qty(direction: int, unit_qty: Decimal) -> Decimal | None`

- [ ] **Step 1: Write failing tests**

```python
import math
from decimal import Decimal

import pytest

from sngw_trader.indicators.vol_targeting import VolTargetConfig, VolTargetSizer


def _cfg(**kw) -> VolTargetConfig:
    base = dict(
        target_vol=0.20, half_life=1, min_scale=0.0, max_scale=3.0,
        rebalance_band=0.10, periods_per_year=365, mode="vol_target",
    )
    base.update(kw)
    return VolTargetConfig(**base)


def test_config_rejects_bad_mode():
    with pytest.raises(ValueError):
        VolTargetConfig(**{**_cfg().__dict__, "mode": "nope"})


def test_ewma_variance_matches_hand_calc():
    s = VolTargetSizer(_cfg(half_life=1, target_vol=0.20))
    assert s.update(100.0) is None  # seed prev close, no return yet
    lam = 2 ** (-1 / 1)
    r1 = 110 / 100 - 1
    s.update(110.0)
    r2 = 99 / 110 - 1
    s.update(99.0)
    r3 = 108 / 99 - 1
    sigma = s.update(108.0)  # 3rd return: warmup 3*half_life=3
    v = r1 * r1
    v = lam * v + (1 - lam) * r2 * r2
    v = lam * v + (1 - lam) * r3 * r3
    assert sigma is not None
    assert math.isclose(sigma, math.sqrt(v * 365), rel_tol=1e-12)
    assert math.isclose(s.scale(), min(3.0, max(0.0, 0.20 / sigma)))


def test_zero_sigma_uses_max_scale():
    s = VolTargetSizer(_cfg(half_life=1, max_scale=2.5))
    for px in (100.0, 100.0, 100.0, 100.0):
        s.update(px)
    assert s.scale() == 2.5


def test_desired_qty_sign_and_warmup():
    s = VolTargetSizer(_cfg(half_life=1))
    unit = Decimal("0.01")
    assert s.desired_qty(0, unit) == Decimal("0")
    assert s.desired_qty(1, unit) is None
    with pytest.raises(ValueError):
        s.desired_qty(2, unit)
    for px in (100.0, 101.0, 99.0, 102.0):
        s.update(px)
    q = s.desired_qty(1, unit)
    assert q is not None and q > 0
    assert s.desired_qty(-1, unit) == -q
    assert s.desired_qty(0, unit) == Decimal("0")


def test_fixed_mode_scale_one_no_warmup():
    s = VolTargetSizer(_cfg(mode="fixed"))
    assert s.scale() == 1.0
    assert s.desired_qty(1, Decimal("0.01")) == Decimal("0.01")
    s.update(100.0)
    assert s.desired_qty(-1, Decimal("0.01")) == Decimal("-0.01")
```

- [ ] **Step 2: Run tests, expect import/collection failure or assertion fail**

Run: `python -m pytest tests/test_indicators/test_vol_targeting.py -v`

- [ ] **Step 3: Implement `vol_targeting.py`**

```python
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

_MODES = frozenset({"vol_target", "fixed"})


@dataclass(frozen=True)
class VolTargetConfig:
    target_vol: float = 0.20
    half_life: int = 20
    min_scale: float = 0.0
    max_scale: float = 3.0
    rebalance_band: float = 0.10
    periods_per_year: int = 365
    mode: str = "vol_target"

    def __post_init__(self) -> None:
        if self.mode not in _MODES:
            raise ValueError(f"mode must be vol_target|fixed, got {self.mode!r}")
        if self.target_vol <= 0 or self.half_life < 1 or self.min_scale < 0:
            raise ValueError("invalid vol-target bounds")
        if self.max_scale <= self.min_scale:
            raise ValueError("max_scale must be > min_scale")
        if self.rebalance_band < 0 or self.periods_per_year < 1:
            raise ValueError("invalid band or periods_per_year")


class VolTargetSizer:
    def __init__(self, config: VolTargetConfig) -> None:
        self._cfg = config
        self._lam = 2 ** (-1 / config.half_life)
        self._warmup = 3 * config.half_life
        self._prev: float | None = None
        self._var: float | None = None
        self._n: int = 0

    def update(self, close: float) -> float | None:
        if self._prev is None:
            self._prev = close
            return None
        r = close / self._prev - 1.0
        self._prev = close
        self._var = r * r if self._var is None else self._lam * self._var + (1 - self._lam) * r * r
        self._n += 1
        sig = self._sigma()
        return sig

    def _sigma(self) -> float | None:
        if self._cfg.mode == "fixed" or self._var is None or self._n < self._warmup:
            return None
        return math.sqrt(self._var * self._cfg.periods_per_year)

    def scale(self) -> float | None:
        if self._cfg.mode == "fixed":
            return 1.0
        sig = self._sigma()
        if sig is None:
            return None
        if sig == 0.0:
            return self._cfg.max_scale
        s = self._cfg.target_vol / sig
        return min(self._cfg.max_scale, max(self._cfg.min_scale, s))

    def desired_qty(self, direction: int, unit_qty: Decimal) -> Decimal | None:
        if direction not in (-1, 0, 1):
            raise ValueError(f"direction must be -1, 0, +1, got {direction}")
        if direction == 0:
            return Decimal("0")
        sc = self.scale()
        if sc is None:
            return None
        return Decimal(direction) * Decimal(str(sc)) * unit_qty
```

`update` in fixed mode still tracks nothing required; returning None is fine.

- [ ] **Step 4: Re-run tests, expect PASS**

Run: `python -m pytest tests/test_indicators/test_vol_targeting.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/indicators/vol_targeting.py tests/test_indicators/test_vol_targeting.py
git commit -m "feat: add VolTargetSizer EWMA scale and desired qty"
```

---

### Task 2: should_rebalance

**Files:**
- Modify: `src/sngw_trader/indicators/vol_targeting.py`
- Test: `tests/test_indicators/test_vol_targeting.py`

**Interfaces:**
- Consumes: `VolTargetSizer` from Task 1
- Produces: `should_rebalance(current_qty: Decimal, desired_qty: Decimal) -> bool`

- [ ] **Step 1: Write failing tests**

```python
def test_should_rebalance_rules():
    s = VolTargetSizer(_cfg(rebalance_band=0.10))
    z, a, b = Decimal("0"), Decimal("0.010"), Decimal("0.011")
    assert s.should_rebalance(z, z) is False
    assert s.should_rebalance(z, a) is True
    assert s.should_rebalance(a, z) is True
    assert s.should_rebalance(a, Decimal("-0.010")) is True
    assert s.should_rebalance(a, b) is False  # 10% exactly? 0.011/0.010-1=0.10, spec is `>` so False
    assert s.should_rebalance(a, Decimal("0.012")) is True


def test_should_rebalance_band_zero():
    s = VolTargetSizer(_cfg(rebalance_band=0.0))
    assert s.should_rebalance(Decimal("0.01"), Decimal("0.0100001")) is True
    assert s.should_rebalance(Decimal("0.01"), Decimal("0.01")) is False
```

Use strict `>` for the band, so 10% exact stays put.

- [ ] **Step 2: Run, expect AttributeError / fail**

Run: `python -m pytest tests/test_indicators/test_vol_targeting.py::test_should_rebalance_rules tests/test_indicators/test_vol_targeting.py::test_should_rebalance_band_zero -v`

- [ ] **Step 3: Implement**

```python
def should_rebalance(self, current_qty: Decimal, desired_qty: Decimal) -> bool:
    if current_qty == 0 and desired_qty == 0:
        return False
    if current_qty == 0 or desired_qty == 0:
        return True
    if (current_qty > 0) != (desired_qty > 0):
        return True
    drift = abs(desired_qty / current_qty - 1)
    return drift > Decimal(str(self._cfg.rebalance_band))
```

- [ ] **Step 4: Re-run, expect PASS**

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/indicators/vol_targeting.py tests/test_indicators/test_vol_targeting.py
git commit -m "feat: add vol-target rebalance band"
```

---

### Task 3: Settings, factory, fixtures, delete RealizedVol and vol_filter

**Files:**
- Modify: `src/sngw_trader/config/settings.py`
- Modify: `src/sngw_trader/runners/strategy_factory.py`
- Modify: `.env.example`
- Modify: `tests/test_config/test_settings.py`, `tests/test_config/test_live_guard.py`
- Modify: `tests/test_runners/test_backtest_okx.py`
- Modify: `src/sngw_trader/indicators/risk_metrics.py` (delete `RealizedVol`)
- Modify: `tests/test_indicators/test_risk_metrics.py` (delete RealizedVol tests)
- Modify: `research/grids/err_mom_ema30_entry_ltf.json` (drop `vol_filter_enabled` grid and `vol_threshold` fixed)
- Modify: `research/grids/err_momentum_regime_4h.json` (drop `vol_threshold` grid)

**Interfaces:**
- Consumes: none of the sizer (factory does not import it)
- Produces: `Settings` fields `sizing_mode, size_target_vol, size_half_life, size_min_scale, size_max_scale, size_rebalance_band`. Env: `SIZING_MODE`, `SIZE_TARGET_VOL`, `SIZE_HALF_LIFE`, `SIZE_MIN_SCALE`, `SIZE_MAX_SCALE`, `SIZE_REBALANCE_BAND`. Invalid `SIZING_MODE` → `SystemExit`.

Strategy configs still have old vol fields until Tasks 4–5; factory in this task should pass the **new** fields. That means Tasks 4–5 strategy config changes are required for factory tests that instantiate strategies via `build_strategy`. **Do factory strategy-config wiring in Tasks 4 and 5.** This task only Settings + fixtures that construct `Settings(...)`, RealizedVol deletion, grids, `.env.example`.

If `build_strategy(load_settings())` is called in tests, it will fail until Tasks 4–5. `test_strategy_factory.py` uses `load_settings()` — keep it running by doing Settings + both StrategyConfigs + factory in this task together with a minimal strategy `__init__` that accepts new fields and still constructs something. That couples tasks.

**Revised:** This task updates Settings, all `Settings(...)` literals, `.env.example`, grids, RealizedVol. Factory and StrategyConfig wait for 4–5 so `build_strategy` is updated in the same commit as the strategies. `test_err_mom_defaults` currently asserts `s.vol_threshold == 0.80` — change to `s.sizing_mode == "vol_target"` and `s.size_target_vol == 0.20`.

- [ ] **Step 1: Write/adjust failing Settings tests**

In `test_err_mom_defaults` replace `assert s.vol_threshold == 0.80` with:

```python
assert s.sizing_mode == "vol_target"
assert s.size_target_vol == 0.20
assert s.size_half_life == 20
assert s.size_rebalance_band == 0.10
```

Add:

```python
def test_bad_sizing_mode_exits(monkeypatch):
    monkeypatch.setenv("SIZING_MODE", "nope")
    with pytest.raises(SystemExit):
        load_settings()
```

Every `Settings(` literal loses `vol_filter_enabled, vol_lookback, vol_threshold` and gains:

```python
sizing_mode="vol_target",
size_target_vol=0.20,
size_half_life=20,
size_min_scale=0.0,
size_max_scale=3.0,
size_rebalance_band=0.10,
```

Files with literals: `test_settings.py` (2), `test_live_guard.py` (1), `test_backtest_okx.py` (1).

- [ ] **Step 2: Run `python -m pytest tests/test_config/test_settings.py -v` — fail on missing fields / TypeError**

- [ ] **Step 3: Implement Settings**

Replace vol fields on `Settings` and in `load_settings()`:

```python
sizing_mode=_env("SIZING_MODE", "vol_target"),
size_target_vol=float(_env("SIZE_TARGET_VOL", "0.20")),
size_half_life=int(_env("SIZE_HALF_LIFE", "20")),
size_min_scale=float(_env("SIZE_MIN_SCALE", "0.0")),
size_max_scale=float(_env("SIZE_MAX_SCALE", "3.0")),
size_rebalance_band=float(_env("SIZE_REBALANCE_BAND", "0.10")),
```

After building mode string, if not in `{"vol_target", "fixed"}`: `raise SystemExit(...)`.

`.env.example` append:

```
SIZING_MODE=vol_target
SIZE_TARGET_VOL=0.20
SIZE_HALF_LIFE=20
SIZE_MIN_SCALE=0.0
SIZE_MAX_SCALE=3.0
SIZE_REBALANCE_BAND=0.10
```

Delete `RealizedVol` class from `risk_metrics.py` and its three tests. Delete `import math` from `risk_metrics.py` if unused (ATR/Ema don't need it — check; `math` is only used by RealizedVol, so remove it).

Grids: remove `vol_filter_enabled` / `vol_threshold` keys. Leave other axes.

- [ ] **Step 4: Run**

```
python -m pytest tests/test_config tests/test_indicators/test_risk_metrics.py tests/test_runners/test_backtest_okx.py -v
```

Expect PASS for settings/risk_metrics/backtest settings construction. `test_strategy_factory` still uses `load_settings()` + old StrategyConfig — it will fail until Task 4 if factory still passes `vol_filter_*`. **Leave factory unchanged in this task** so `build_strategy` still compiles against old strategy configs. Then `load_settings()` no longer has `vol_filter_*` and factory will AttributeError.

So factory **must** be updated in the same task as Settings, which **requires** StrategyConfig fields in the same task.

**Do StrategyConfig field swap in this task without changing trading logic yet:** replace vol_filter fields with sizing fields on both configs; strategies still use `trade_size` only; drop `_vol` usage so they import/compile. Filter branches deleted here (behavior: high vol no longer blocks). Sizer wiring in Tasks 4–5.

Minimal strategy change this task:
- Config: drop `vol_filter_*`, add sizing fields with spec defaults
- `__init__`: remove `RealizedVol`; do not construct sizer yet
- Remove vol from `entry_blocked` / `blocked`
- Factory: pass sizing fields instead of vol_filter
- Delete `test_vol_block_still_applies_without_cooldown` and `_feed_vol_high`
- `test_config_builds`: drop `vol_lookback` / `_vol._ppy` asserts

- [ ] **Step 5: Commit**

```bash
git add src/sngw_trader/config/settings.py src/sngw_trader/runners/strategy_factory.py src/sngw_trader/indicators/risk_metrics.py src/sngw_trader/strategies/err_momentum_regime.py src/sngw_trader/strategies/err_mom_ema30_entry.py tests .env.example research/grids
git commit -m "refactor: replace vol_filter settings with sizing fields"
```

---

### Task 4: Spec A uses sizer + band delta

**Files:**
- Modify: `src/sngw_trader/strategies/err_momentum_regime.py`
- Test: `tests/test_strategies/test_err_mom_regime.py`

**Interfaces:**
- Consumes: `VolTargetConfig`, `VolTargetSizer`
- Produces: UTC daily aggregator + sizer; `_submit(side, qty, seed_stop)`; 4h direction uses desired qty (band ignored); daily close same-direction band resize; stop not reset on delta

`__init__` after Task 3 already has sizing config fields:

```python
from sngw_trader.indicators.vol_targeting import VolTargetConfig, VolTargetSizer

self._utc_day = BarAggregator(86_400)
self._sizer = VolTargetSizer(VolTargetConfig(
    target_vol=config.size_target_vol,
    half_life=config.size_half_life,
    min_scale=config.size_min_scale,
    max_scale=config.size_max_scale,
    rebalance_band=config.size_rebalance_band,
    periods_per_year=365,
    mode=config.sizing_mode,
))
self._sized_this_bar = False
```

`on_bar`: after trailing, `day = self._utc_day.update(...)`; if day: `self._sizer.update(day.close)`. Then existing 4h path. If day completed and 4h path did not submit (`_sized_this_bar` is False) and in a position: `_sync_size(self._current_side(), allow_new=False)`. Reset `_sized_this_bar` at start of `on_bar`.

`_target_4h`: cooldown only (already after Task 3).

Replace flatten+full `trade_size` with `_sync_size(target, allow_new=True)`:

```python
def _signed_qty(self) -> Decimal:
    if self.portfolio.is_flat(self.config.instrument_id):
        return Decimal("0")
    qty = abs(Decimal(str(self.portfolio.net_position(self.config.instrument_id))))
    return qty if self.portfolio.is_net_long(self.config.instrument_id) else -qty

def _sync_size(self, direction: int, *, allow_new: bool) -> None:
    desired = self._sizer.desired_qty(direction, self.config.trade_size)
    current = self._signed_qty()
    if desired is None:
        return
    if current == 0 and desired != 0 and not allow_new:
        return
    if not self._sizer.should_rebalance(current, desired) and not (
        current != 0 and desired != 0 and (current > 0) != (desired > 0)
    ):
        # should_rebalance already True on flip/exit/entry; keep this as the single gate
        return
    if not self._sizer.should_rebalance(current, desired):
        return
    if current != 0 and (desired == 0 or (current > 0) != (desired > 0)):
        self.close_all_positions(self.config.instrument_id)
        self._reset_stop()
        current = Decimal("0")
        self._sized_this_bar = True
        if desired == 0:
            return
    if current == 0 and desired != 0:
        self._submit(OrderSide.BUY if desired > 0 else OrderSide.SELL, abs(desired), seed_stop=True)
        self._sized_this_bar = True
        return
    delta = desired - current
    if delta == 0:
        return
    self._submit(OrderSide.BUY if delta > 0 else OrderSide.SELL, abs(delta), seed_stop=False)
    self._sized_this_bar = True

def _submit(self, side: OrderSide, qty: Decimal, seed_stop: bool) -> None:
    instrument = self.cache.instrument(self.config.instrument_id)
    if instrument is None:
        self.log.error(f"Instrument not in cache: {self.config.instrument_id}")
        return
    q = instrument.make_qty(qty)
    if q == 0:
        return
    if seed_stop:
        self._pending_atr = self._atr.value
    self.submit_order(self.order_factory.market(self.config.instrument_id, side, q))
```

Gate with `should_rebalance` only (it already covers entry/exit/flip). `allow_new=False` blocks `current==0 and desired!=0` before that.

- [ ] **Step 1: Tests**

```python
def test_config_builds_sizer_and_daily_bucket():
    s = _make_strategy()
    assert s._utc_day._bucket_ns == 86_400 * 1_000_000_000
    assert s._sizer.scale() == 1.0 or s.config.sizing_mode == "vol_target"
    # default mode vol_target: scale None until warmup
    assert s._sizer.scale() is None


def test_fixed_mode_desired_is_trade_size():
    s = _make_strategy(sizing_mode="fixed")
    assert s._sizer.desired_qty(1, s.config.trade_size) == Decimal("0.01")


def test_sync_skips_when_warming_and_flat():
    s = _make_strategy()  # vol_target, no updates
    s._signed_qty = lambda: Decimal("0")  # if you instead patch portfolio, do that
    calls = []
    s._submit = lambda *a, **k: calls.append((a, k))
    s._sync_size(1, allow_new=True)
    assert calls == []


def test_sync_delta_does_not_reset_stop():
    s = _make_strategy(sizing_mode="fixed", size_rebalance_band=0.05)
    s._stop_price = 94.0
    s._high_water = 103.0
    s._signed_qty = lambda: Decimal("0.01")
    submitted = []
    s._submit = lambda side, qty, seed_stop: submitted.append((side, qty, seed_stop))
    s.close_all_positions = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no flatten"))
    # fixed desired 0.01, current 0.01 -> no order
    s._sync_size(1, allow_new=True)
    assert submitted == []
    assert s._stop_price == 94.0
```

For a real delta, temporarily force desired via a sizer in fixed mode cannot drift. Use `vol_target` after feeding returns, or patch `desired_qty`.

```python
def test_band_resize_keeps_stop():
    s = _make_strategy(sizing_mode="fixed")
    s._stop_price = 94.0
    s._signed_qty = lambda: Decimal("0.010")
    s._sizer.desired_qty = lambda d, u: Decimal("0.013")
    submitted = []
    s._submit = lambda side, qty, seed_stop: submitted.append((side, qty, seed_stop))
    s.close_all_positions = lambda *a, **k: (_ for _ in ()).throw(AssertionError("no flatten"))
    s._sync_size(1, allow_new=False)
    assert submitted[0][2] is False
    assert submitted[0][1] == Decimal("0.003")
    assert s._stop_price == 94.0
```

`test_config_builds`: remove vol asserts; keep 4h bucket / ERMOM warmup.

- [ ] **Step 2–4:** implement `_sync_size`, wire `on_bar`, run `python -m pytest tests/test_strategies/test_err_mom_regime.py -v`

- [ ] **Step 5: Commit** `feat: size Spec A with vol-target band rebalance`

If `portfolio.net_position` is not Decimal, wrap with `Decimal(str(...))`. Confirm against installed nautilus in this task (`python -c "import inspect, nautilus_trader"`). Prefer `cache.positions` only if net_position is missing — do not invent a position dict.

---

### Task 5: Spec B uses sizer; no sizer-created entries

**Files:**
- Modify: `src/sngw_trader/strategies/err_mom_ema30_entry.py`
- Test: `tests/test_strategies/test_err_mom_ema30_entry.py`

**Interfaces:** same sizer as A. TRIG path: `_submit` with `desired_qty` (allow_new). Daily close while in `L_IN`/`S_IN`: resize only (`allow_new=False`). `desired==0` flatten resets FSM like other exits.

- [ ] **Step 1: Tests**

```python
def test_trig_uses_desired_qty_not_unit_when_scaled():
    s = _make_strategy(sizing_mode="fixed")
    s._ermom._value = 0.5
    s._ema_fast._value = 105.0
    s._ema_slow._value = 100.0
    qtys = []
    with patch.object(type(s), "portfolio", new_callable=PropertyMock) as pf:
        pf.return_value.is_flat.return_value = True
        s._submit = lambda side, qty, seed_stop=True: qtys.append(qty)
        s._long.update(o=106, h=111, l=105, c=110, ema_fast=105, ema_slow=100, regime_allows=True)
        s._long.update(o=110, h=110, l=104, c=108, ema_fast=105, ema_slow=100, regime_allows=True)
        s._on_entry(CompletedBar(0, 1, 106, 110, 104.5, 106))
    assert qtys == [Decimal("0.01")]


def test_daily_resize_does_not_enter_when_flat():
    s = _make_strategy(sizing_mode="fixed")
    s._signed_qty = lambda: Decimal("0")
    calls = []
    s._submit = lambda *a, **k: calls.append(1)
    s._sync_size(1, allow_new=False)
    assert calls == []
```

Reuse the same `_sync_size` / `_submit(side, qty, seed_stop)` pattern as Spec A (copy, do not extract a shared strategy base — vibe layout has no strategy base class).

Daily: existing `self._daily.update` → `self._sizer.update(daily.close)` then if in position, `_sync_size(side, allow_new=False)`. If `_on_entry` already submitted this bar, skip (flag like A).

When flattening via desired 0, `self._long.reset(); self._short.reset(); self._stop_price = None`.

- [ ] **Step 2–4:** implement, run `python -m pytest tests/test_strategies/test_err_mom_ema30_entry.py tests/test_runners/test_strategy_factory.py -v`

- [ ] **Step 5: Commit** `feat: size Spec B with vol-target band rebalance`

---

### Task 6: Indicator import guard + full pytest

**Files:**
- Modify: `tests/test_strategies/test_no_exchange_io.py` (extend to indicators) **or** add one test in `test_vol_targeting.py`

- [ ] **Step 1: Test that `vol_targeting.py` source has no `nautilus_trader`**

```python
def test_vol_targeting_module_has_no_nautilus():
    from pathlib import Path
    text = Path("src/sngw_trader/indicators/vol_targeting.py").read_text(encoding="utf-8")
    assert "nautilus_trader" not in text
```

- [ ] **Step 2–4:** should already pass; run full suite

```
python -m pytest tests -q
```

Fix any leftover `vol_filter` / `RealizedVol` / Settings constructor misses (`grep` those symbols under `src/` and `tests/`).

- [ ] **Step 5: Commit** if anything changed; otherwise skip.

---

## Spec coverage

| Spec section | Task |
|---|---|
| 4 engine math, warmup, fixed, clip, k=1 | 1 |
| 4.3 should_rebalance | 2 |
| 6–7 settings, vol_filter delete, RealizedVol, grids | 3 |
| 5.2 Spec A daily σ, cooldown-only block, delta keeps stop | 4 |
| 5.3 Spec B TRIG sizes, no sizer entries, FSM reset on size-flatten | 5 |
| 4.4 / 9 no nautilus in engine | 6 |
| 8.2 4-cell harness | out of scope |

## Placeholder scan

No TBD. `portfolio.net_position` verified at Task 4 against installed Nautilus; fallback is `Decimal(str(...))` of the API that exists, not a home-grown book.
