# MACD Crossover Core (swing) Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the MACD(12,26,9) crossover strategy as a single `Strategy` class with `allow_short` config branching, unit-qty sizing as default with vol-target as a sensitivity variant, and run long-only vs long+short comparison over IS/OOS segments with default + stress costs.

**Architecture:** One strategy file (`macd_crossover.py`) reusing the existing pure `Macd` indicator and cross helpers from `indicators/kd_macd.py` — **no new indicator code**. Structure cloned from `kd_macd_crypto.py` (`BarAggregator` → `_decide` → `OrderIntent` → `_execute`), minus the KD stochastic and minus vol-target-as-default. Existing runner (`backtest_okx`, `BacktestNode`) untouched; research script drives runs via `executor.run_window`.

**Tech Stack:** Python 3.12+, NautilusTrader Strategy/StrategyConfig, pytest, Decimal.

**Spec:** `docs/superpowers/specs/20260905-bt-macd-crossover-core-spec.md`
**Handoff:** `20260905-bt-macd-crossover-core` (result + receipt paths in spec §4)

## Global Constraints

- No custom runner, no `ccxt` / `python-okx` / exchange I/O in strategy or indicators.
- Strategy file must not assemble nodes/runners; runner files must not hold trade conditions.
- Parameter optimization forbidden: MACD 12/26/9 fixed (raw-trading-0005 + convention; raw-trading-0003/0004 params unconfirmed — spec §6 assumption 5). No fast/slow/signal grid scan.
- No rules not in the strategy doc: no take-profit, no stop-loss, no regime/ADX filter, no funding assumptions beyond result-file disclosure.
- **Scope isolation:** no other strategy documents' rules (kd-macd-crypto, macd-momentum-combo, renko-macd-intraday, vpvma) may leak into this strategy or the result file. Reusing the shared `Macd` indicator component is infra reuse, not rule sharing.
- TDD: failing test first for each behavior. `pytest` from repo root.
- Universe: `BTC-USDT-SWAP.OKX` only (catalog data limit — equity-market reproduction impossible; limitation + assumption in result, spec §2/§6).

## File map

| File | Role |
|---|---|
| `src/sngw_trader/indicators/kd_macd.py` | Existing — reuse `Macd`, `crossed_up`, `crossed_down` (on MACD line vs signal). **No changes.** |
| `src/sngw_trader/strategies/macd_crossover.py` | Create: `MacdCrossover` + `MacdCrossoverConfig` |
| `tests/test_strategies/test_macd_crossover.py` | Create: config/branch/sizing tests |
| `src/sngw_trader/research/macd_crossover_compare.py` | Create: run matrix (2 direction variants × default/sensitivity sizing × IS/OOS × 2 cost modes) |
| `research/macd_crossover/` | Output dir for JSON results |
| `docs/superpowers/specs/20260905-bt-macd-crossover-core-spec.md` | Read-only spec |

---

### Task 1: Strategy — MacdCrossover + MacdCrossoverConfig

**Files:**
- Create: `src/sngw_trader/strategies/macd_crossover.py`
- Test: `tests/test_strategies/test_macd_crossover.py`

**Interfaces:**
- Consumes: `BarAggregator(86_400)` (1m→UTC day), `Macd`, `crossed_up`, `crossed_down`, `VolTargetSizer`/`VolTargetConfig` (sensitivity only), `Strategy` base — structure cloned from `kd_macd_crypto.py` (`_decide`/`OrderIntent`/`_execute`, `on_event` fill reconciliation), minus all KD state.
- Produces:

```python
class MacdCrossoverConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    allow_short: bool = False        # False: long-only (doc literal), True: long+short variant
    trigger_mode: str = "cross"      # "cross" (doc literal) | "state" (sensitivity)
    sizing_mode: str = "unit"        # "unit" (default: doc silent on sizing) | "vol_target" (sensitivity)
    size_target_vol: float = 0.20
    size_half_life: int = 20
    size_min_scale: float = 0.0
    size_max_scale: float = 3.0
    size_rebalance_band: float = 0.10
    size_increment: str = "0.01"
    close_positions_on_stop: bool = True

def direction_target(golden: bool, death: bool, current: int, allow_short: bool) -> int
```

- Signal semantics:
  - `cross` mode: golden = `crossed_up(prev_macd_line, prev_signal, macd_line, signal)`; death = `crossed_down(...)` — i.e. the same helpers applied to (macd_line, signal) instead of (K, D).
  - `state` mode (sensitivity): transition of `macd_line > signal` (golden) / `macd_line < signal` (death).

- [ ] **Step 1: Failing tests**
  - `direction_target` truth table: golden→+1 (both modes); death→0 if long-only, −1 if allow_short; no-signal→current.
  - Config defaults: 12/26/9, allow_short=False, trigger_mode="cross", sizing_mode="unit"; invalid trigger_mode raises ValueError; daily aggregator bucket = 86_400s.
  - `_decide` with synthetic ramp: MACD crosses up on the first day dif turns positive after warmup → intent buys unit qty (`sizing_mode="unit"`); constant close after warmup → no crossover, no intents.
  - Long-only: death-cross day closes to flat (single sell intent, no short).
  - Long+short: death-cross day emits close-then-sell intents (NETTING-safe sequence, current==0 after).
  - `sizing_mode="vol_target"`: after vol warmup, desired qty = unit × scale, rounded to `size_increment`; band: same-sign desired within 10% of current → no rebalance intent.
  - No exchange I/O: strategy source contains no `requests`/`httpx`/`ccxt` import (mirror existing no-io test pattern).
- [ ] **Step 2: Implement.** on_bar flow: aggregate 1m→daily; on completed day: `sizer.update(close)` (fed always so vol warmup advances even in unit mode), `macd.update(close)`; compute golden/death by `trigger_mode`; `target = direction_target(...)`; `_intents_for(target)` cloned from `kd_macd_crypto._intents_for` — in `unit` mode desired = target × trade_size (no sizer scale), in `vol_target` mode `sizer.desired_qty` with unit fallback before warmup + band check. Web search: none. on_bar no network/file I/O.
- [ ] **Step 3: `pytest tests/test_strategies/test_macd_crossover.py` green, then full `pytest`.**

### Task 2: Research runner — comparison matrix

**Files:**
- Create: `src/sngw_trader/research/macd_crossover_compare.py`
- Output: `research/macd_crossover/results.json` + console table.

**Interfaces:**
- Consumes: `executor.run_window`, `metrics.compute_equity_metrics`, `metrics.compute_trade_metrics`, `config.load_settings`, `GridSpec`. Clone `kd_macd_compare.py` structure including the end-of-window open-position valuation at engine last price (closed-position-only extraction would miss an always-held long).
- Produces: dict keyed `variant/segment/cost` → {cagr, sharpe, mdd, win_rate, profit_factor, n_trades, total_pnl}.

- [ ] **Step 1: Implement matrix** (script, but importable pure functions):
  - Base variants: `long_only` (allow_short=False), `long_short` (allow_short=True) — both trigger_mode="cross", sizing_mode="unit".
  - Sensitivity variants (reported, never selected): trigger_mode="state"; sizing_mode="vol_target" (both directions).
  - Segments: `is` = 2021-01-01→2022-12-31, `oos` = 2023-01-01→2025-12-30 (catalog range 2019-12-16~2025-12-30). No paper-period segment — this handoff's evidence markets are equities, unreproducible (spec §2).
  - Warmup pad: 120 days (macd slow 26 + signal 9 + vol half-life×3 = 60 → 120 safe).
  - Cost modes: default Settings vs stress (`bt_taker_fee`×2, `bt_prob_slippage`×5 — clone `kd_macd_compare.stress_settings`).
  - `GridSpec(strategy_path="sngw_trader.strategies.macd_crossover:MacdCrossover", config_path="...:MacdCrossoverConfig", fixed={"trade_size": "0.02"})` — unit sizing means trade_size is the fixed position size; keep 0.02 BTC consistent with prior runs for comparability.
- [ ] **Step 2: Smoke run** one cell (`long_only/is`) to validate wiring end-to-end. Expect ≥1 trade over 2 years of BTC trend; if 0 trades, debug signals (check MACD line/signal crossing logic) before proceeding.
- [ ] **Step 3: Full matrix run**, save `results.json`, print comparison table (long_only vs long_short per segment × cost; sensitivity rows separate).

### Task 3: Reporting — handoff result + receipt

**Files:**
- Create: wiki result at `$LLM_WIKI_PATH/30_PROJECTS/trading/20260905-bt-macd-crossover-core-result.md` (local path: `C:\Users\seung\Seungwoo5760 Dropbox\최승우\앱\LLM_WIKI\30_PROJECTS\trading\`)
- Create: receipt at `...\40_AGENT_WORKSPACE\agents\trading-agent\20260905-bt-macd-crossover-core-receipt.md`

- [ ] **Step 1: Result doc** must contain: `handoff_id: 20260905-bt-macd-crossover-core`; rules/params/universe/periods/cost model (spec §1/§3); long_only vs long_short table per segment incl. stress costs; sensitivity rows (state trigger, vol-target sizing) clearly separated from base results; **equity-market reproduction section: 수행 불가 (catalog has no equity data) — BTC 근사 백테스트임을 명시, 논문 결과와의 비교는 direct-comparison 불가로 서술**; verified vs assumption separation (spec §6 list); scope isolation — no other strategy's rules or comparisons.
- [ ] **Step 2: Receipt** — `accepted` (if gates 1–4 completed) with pointers to spec/plan/result/logs; or `blocked` with cause if any gate failed.
- [ ] **Step 3: `pytest` full suite green; `git status` shows only intended files.**

---

## Verification gates (from spec §5, order fixed)

1. Unit tests: MACD crossover detection, allow_short branching, unit/vol_target sizing, no-exchange-IO.
2. IS segment sanity (2021-22, BTC): trade count > 0, base-cost vs stress-cost direction sane.
3. OOS segment (2023-25): same config, no tuning; long_only vs long_short base comparison.
4. Sensitivity (state trigger, vol-target sizing) — reported, never selected.
5. Result + receipt written; handoff acceptance criteria checklist satisfied (handoff_id, full rule/param/universe/period/cost disclosure, assumption/verified separation, no cross-strategy contamination).