# VPVMA (swing) Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the VPVMA(12,26,9,bw=0.1) strategy from raw-trading-0005 full text as a single `Strategy` class with `allow_short` config branching, unit-qty sizing as default with vol-target as a sensitivity variant, and run long-only vs long+short comparison over IS/OOS segments with default + stress costs.

**Architecture:** One new indicator file (`indicators/vpvma.py`) — VWMA over typical price, daily volatility of the 4 prices, EMA-smoothed products, SMA signal — plus one strategy file (`strategies/vpvma.py`) cloned from the already-implemented `macd_crossover.py` (`BarAggregator` → `_decide` → `OrderIntent` → `_execute`), swapping the MACD-cross signal for the paper's asymmetric band signal. Research runner clones `macd_crossover_compare.py` verbatim except strategy/config paths, variant dict, and output dir. Existing runner (`backtest_okx`, `BacktestNode`) untouched.

**Tech Stack:** Python 3.12+, NautilusTrader Strategy/StrategyConfig, pytest, Decimal.

**Spec:** `docs/superpowers/specs/20260905-bt-vpvma-spec.md`
**Handoff:** `20260905-bt-vpvma` (result + receipt paths in spec §Output paths)

## Global Constraints

- No custom runner, no `ccxt` / `python-okx` / exchange I/O in strategy or indicators.
- Strategy file must not assemble nodes/runners; runner files must not hold trade conditions.
- Parameter optimization forbidden: window_fast=12, window_slow=26, window_sign=9, bandwidth=0.1 fixed (raw-trading-0005 Table 8). Bandwidth is the paper's own trade-count control variable — **no bandwidth grid scan** (spec §3).
- No rules not in the paper: no take-profit, no stop-loss, no `trigger_mode` variant (unlike MACD-crossover, the band rule is the doc-literal signal — no state/cross duality exists), no funding assumptions beyond result-file disclosure.
- **Scope isolation:** no other strategy documents' rules (kd-macd-crypto, macd-momentum-combo, macd-crossover-core, renko-macd-intraday) may leak into this strategy or the result file. Reusing the EMA convention and `BarAggregator`/sizer plumbing is infra reuse, not rule sharing.
- TDD: failing test first for each behavior. `pytest` from repo root.
- Universe: `BTC-USDT-SWAP.OKX` only (catalog data limit — US-equity reproduction impossible; limitation + assumption in result, spec §2/§6).

## File map

| File | Role |
|---|---|
| `src/sngw_trader/indicators/kd_macd.py` | Existing — EMA alpha convention (`2/(n+1)`) reused as reference. **No changes.** |
| `src/sngw_trader/indicators/vpvma.py` | Create: pure incremental `Vpvma` indicator (paper §4.1 formulas 4.2-1~8) |
| `src/sngw_trader/strategies/vpvma.py` | Create: `Vpvma` + `VpvmaConfig` (clone of `macd_crossover.py`, new signal) |
| `tests/test_strategies/test_vpvma.py` | Create: indicator/signal/branch/sizing tests |
| `src/sngw_trader/research/vpvma_compare.py` | Create: run matrix (2 direction variants × default/sensitivity sizing × IS/OOS × 2 cost modes) |
| `research/vpvma/` | Output dir for JSON results |
| `docs/superpowers/specs/20260905-bt-vpvma-spec.md` | Read-only spec |

---

### Task 1: Indicator — incremental `Vpvma`

**Files:**
- Create: `src/sngw_trader/indicators/vpvma.py`
- Test: `tests/test_strategies/test_vpvma.py`

**Interfaces:**
- Consumes: nothing new — plain Python (`deque`), EMA alpha `2/(n+1)` seeded like repo's `Macd` (first-value seed; convention reuse).
- Produces:

```python
class Vpvma:
    def __init__(self, fast: int = 12, slow: int = 26, sign: int = 9) -> None
    def update(self, high, low, close, open_, volume) -> tuple[float, float] | None  # (vpvma, vpvmas) once warmed
    @property
    def value / signal / warmed
```

- Formulas (paper literal, spec §1.1): `TP=(H+L+C)/3`; `VWMA_n = Σ(TP·V)/ΣV` rolling n; `DV = std([H,L,C,O])` (sample std, ddof=1 — spec assumption 5, disclosed in result); `ESVMap=EMA(VWMA_fast·DV, fast)`, `ELVMap=EMA(VWMA_slow·DV, slow)` (spec assumption 1: formula 4.2-5 `SVWMA` is a typo for `LVWMA`); `VPVMA=ESVMap−ELVMap`; `VPVMAS=SMA(VPVMA, sign)`.

- [ ] **Step 1: Failing tests** — hand-computed tiny sequence (e.g. 3 bars, fast=slow=1 degenerate case) validates VWMA, DV, EMA seeding/convergence, VPVMA/VPVMAS arithmetic; `warmed` False until `slow + sign` bars; monotone-price synthetic series gives VPVMA>0.
- [ ] **Step 2: Implement** the one class. No pandas, no exchange I/O.
- [ ] **Step 3: `pytest tests/test_strategies/test_vpvma.py` green.**

### Task 2: Strategy — `Vpvma` + `VpvmaConfig`

**Files:**
- Create: `src/sngw_trader/strategies/vpvma.py`
- Test: `tests/test_strategies/test_vpvma.py` (extend)

**Interfaces:**
- Consumes: `BarAggregator(86_400)`, `VolTargetSizer`/`VolTargetConfig` (sensitivity only), `Strategy` base — structure cloned from `macd_crossover.py` (`_decide`/`OrderIntent`/`_execute`, `on_event` fill reconciliation, `_intents_for`), MACD state swapped for Task-1 indicator.
- Produces:

```python
class VpvmaConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    fast: int = 12
    slow: int = 26
    sign: int = 9
    bandwidth: float = 0.10
    allow_short: bool = False        # False: paper literal long-only, True: repo-convention long+short variant
    sizing_mode: str = "unit"        # "unit" (default) | "vol_target" (sensitivity)
    # vol-target fields identical to macd_crossover (size_target_vol=0.20, size_half_life=20,
    # size_min_scale=0.0, size_max_scale=3.0, size_rebalance_band=0.10, size_increment="0.01")
    close_positions_on_stop: bool = True

def band_signal(prev_v, prev_s, v, s, bandwidth) -> int  # +1 buy, -1 sell, 0 none
def direction_target(signal: int, current: int, allow_short: bool) -> int
```

- Signal semantics (paper Table 8 literal, spec §1.2):
  - Buy: `v > (1 + bandwidth)·s` and `prev_v <= prev_s`
  - Sell: `v < (1 − 2·bandwidth)·s` and `prev_v <= prev_s` (asymmetric; paper literal — spec assumption 2)
- `direction_target`: buy→+1; sell→0 if long-only, −1 if allow_short; no signal→current (hold).

- [ ] **Step 1: Failing tests**
  - `band_signal` truth table: above upper band after prev below/equal → +1; below lower band after prev below/equal → −1; no prev condition met → 0; inside bands → 0.
  - `direction_target` truth table: +1 on buy; sell→0 (long-only) / −1 (allow_short); 0→current.
  - Config defaults: 12/26/9, bandwidth=0.10, allow_short=False, sizing_mode="unit"; daily aggregator bucket = 86_400s.
  - `_decide` with synthetic ramp driving VPVMA above upper band → intent buys unit qty; then sequence driving it below lower band → long-only closes to flat (single sell, no short); allow_short emits close-then-sell intents (NETTING-safe, current==0 after).
  - Sell signal with no position → no intents (long-only).
  - `sizing_mode="vol_target"`: desired = unit × scale after warmup, band check suppresses <10% rebalance.
  - No exchange I/O: strategy source contains no `requests`/`httpx`/`ccxt` import (mirror existing no-io test pattern).
- [ ] **Step 2: Implement.** on_bar flow: aggregate 1m→daily; on completed day: `sizer.update(close)`, `vpvma.update(h, l, c, o, v)`; `sig = band_signal(...)`; `target = direction_target(...)`; `_intents_for(target)` cloned from `macd_crossover._intents_for` (unit mode: desired = target × trade_size; vol_target mode: `sizer.desired_qty` with unit fallback + band check). Web search: none. on_bar no network/file I/O.
- [ ] **Step 3: `pytest tests/test_strategies/test_vpvma.py` green, then full `pytest`.**

### Task 3: Research runner — comparison matrix

**Files:**
- Create: `src/sngw_trader/research/vpvma_compare.py`
- Output: `research/vpvma/results.json` + console table.

**Interfaces:**
- Consumes: `executor.run_window` + end-of-window open-position valuation pattern, `metrics.compute_equity_metrics`/`compute_trade_metrics`, `config.load_settings`, `GridSpec` — clone `macd_crossover_compare.py` (structure, `stress_settings`, `evaluate`, capital, trade_size all identical for comparability).
- Produces: dict keyed `variant/segment/cost` → {cagr, sharpe, mdd, win_rate, profit_factor, n_trades, total_pnl}.

- [ ] **Step 1: Implement matrix** (script, but importable pure functions):
  - Base variants: `long_only` (allow_short=False), `long_short` (allow_short=True) — both sizing_mode="unit".
  - Sensitivity variants (reported, never selected): `long_only/vt`, `long_short/vt` (sizing_mode="vol_target").
  - Segments: `is` = 2021-01-01→2022-12-31, `oos` = 2023-01-01→2025-12-30. No paper-period segment — evidence markets are US equities, unreproducible (spec §2).
  - Warmup pad: 120 days (slow 26 + sign 9 + EMA convergence ≪ 120).
  - Cost modes: default Settings vs stress (`stress_settings` clone).
  - `GridSpec(strategy_path="sngw_trader.strategies.vpvma:Vpvma", config_path="...:VpvmaConfig", fixed={"trade_size": "0.02"})`.
- [ ] **Step 2: Smoke run** one cell (`long_only/is`). Expect ≥1 trade over 2 years of BTC trend; if 0 trades, check band arithmetic (bw=0.1 vs 2·bw asymmetry) and warmup before proceeding.
- [ ] **Step 3: Full matrix run**, save `results.json`, print comparison table (long_only vs long_short per segment × cost; sensitivity rows separate).

### Task 4: Reporting — handoff result + receipt

**Files:**
- Create: wiki result at `$LLM_WIKI_PATH/30_PROJECTS/trading/20260905-bt-vpvma-result.md` (local path: `C:\Users\seung\Seungwoo5760 Dropbox\최승우\앱\LLM_WIKI\30_PROJECTS\trading\`)
- Create: receipt at `...\40_AGENT_WORKSPACE\agents\trading-agent\20260905-bt-vpvma-receipt.md`

- [ ] **Step 1: Result doc** must contain: `handoff_id: 20260905-bt-vpvma`; paper-literal rules/params (12/26/9, bw=0.1, asymmetric bands)/universe/periods/cost model (spec §1/§3); long_only vs long_short table per segment incl. stress costs; sensitivity rows (vol-target sizing) separate; **US-equity reproduction section: 수행 불가 (catalog has no equity data) — BTC 근사 백테스트 명시, 논문 수치와 direct-comparison 불가**; verified (full-text formulas/params) vs assumption (spec §6 list) separation; scope isolation — no other strategy's rules or comparisons.
- [ ] **Step 2: Receipt** — `accepted` (if gates 1–4 completed) with pointers to spec/plan/result/logs; or `blocked` with cause if any gate failed.
- [ ] **Step 3: `pytest` full suite green; `git status` shows only intended files.**

---

## Verification gates (from spec §5, order fixed)

1. Unit tests: VPVMA arithmetic, band-signal detection, allow_short branching, unit/vol_target sizing, no-exchange-IO.
2. IS segment sanity (2021-22, BTC): trade count > 0, base-cost vs stress-cost direction sane.
3. OOS segment (2023-25): same config, no tuning; long_only vs long_short base comparison.
4. Sensitivity (vol-target sizing) — reported, never selected.
5. Result + receipt written; handoff acceptance criteria checklist satisfied (handoff_id, full rule/param/universe/period/cost disclosure, assumption/verified separation, no cross-strategy contamination).
