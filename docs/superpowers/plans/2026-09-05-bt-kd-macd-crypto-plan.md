# KD + MACD Combo (crypto) Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the KD-MACD combo strategy (Wang & Huang 2026 rules) as a single `Strategy` class with `allow_short` config branching, Harvey-style vol-target sizing on both variants, and run long-only vs long+short comparison over paper-period / IS / OOS segments.

**Architecture:** One strategy file (`kd_macd_crypto.py`) holding pure signal math (KD stochastic, MACD) + `VolTargetSizer` reuse. UTC-daily aggregation of the 1-MINUTE source bars via existing `BarAggregator`. Direction target = f(KD cross, MACD bar, allow_short). Existing runner (`backtest_okx`, `BacktestNode`) untouched; research script drives runs via `executor.run_window`.

**Tech Stack:** Python 3.12+, NautilusTrader Strategy/StrategyConfig, pytest, Decimal.

**Spec:** `docs/superpowers/specs/20260905-bt-kd-macd-crypto-spec.md`
**Handoff:** `20260905-bt-kd-macd-crypto` (result + receipt paths in spec §6)

## Global Constraints

- No custom runner, no `ccxt` / `python-okx` / exchange I/O in strategy or indicators.
- Indicators must not import Nautilus Strategy/Actor/nodes.
- Strategy file must not assemble nodes/runners; runner files must not hold trade conditions.
- Parameter optimization forbidden: 12/26/9 MACD, kd_n=9, kd_alpha=0.5 fixed (spec §1.2 — Zenodo data fit; α=0.5 vs paper text 1/3 recorded as assumption).
- No other strategy documents' rules (macd-crossover-core, macd-momentum-combo, renko-macd-intraday, vpvma) may leak into this strategy or the result file.
- TDD: failing test first for each behavior. `pytest` from repo root.
- Universe: `BTC-USDT-SWAP.OKX` only (catalog data limit — limitation noted in result).

## File map

| File | Role |
|---|---|
| `src/sngw_trader/indicators/kd_macd.py` | Create: pure math — `KdStochastic`, `Macd`, cross helpers |
| `tests/test_indicators/test_kd_macd.py` | Create: indicator unit tests (incl. Zenodo CSV fixture check) |
| `src/sngw_trader/strategies/kd_macd_crypto.py` | Create: `KdMacdCrypto` + `KdMacdCryptoConfig` |
| `tests/test_strategies/test_kd_macd_crypto.py` | Create: config/branch/sizing tests |
| `src/sngw_trader/research/kd_macd_compare.py` | Create: run matrix (2 variants × 3 segments × 2 cost modes) |
| `research/kd_macd/` | Output dir for JSON results |
| `docs/superpowers/specs/20260905-bt-kd-macd-crypto-spec.md` | Read-only spec |

---

### Task 1: Pure indicators — KdStochastic, Macd

**Files:**
- Create: `src/sngw_trader/indicators/kd_macd.py`
- Test: `tests/test_indicators/test_kd_macd.py`

**Interfaces:**
- Consumes: stdlib only (floats).
- Produces:
  - `KdStochastic(n: int, alpha: float)` — `.update(high, low, close) -> tuple[float, float] | None` (K, D; None until `n` bars). Rolling n-bar H/L window, RSV = (C−L)/(H−L)×100 (0 if H==L), K_t = α·RSV + (1−α)·K_{t−1}, D_t = α·K_t + (1−α)·D_{t−1}, seed K=D=50.
  - `Macd(fast: int, slow: int, signal: int)` — `.update(close) -> tuple[float, float, float] | None` (dif, dea, bar; None until `slow+signal` bars). Standard EMA(2/(n+1) span) seeded with first value.
  - `crossed_up(prev_k, prev_d, k, d) -> bool`, `crossed_down(prev_k, prev_d, k, d) -> bool` (strict prev ≤ → > semantics).

- [ ] **Step 1: Failing tests** — hand-calculated sequences:
  - KD on a monotonic rising window: RSV=100 → K drifts toward 100; falling → toward 0.
  - Flat prices: RSV=0 branch (H==L) doesn't divide by zero.
  - MACD on constant close: dif=0, bar=0.
  - MACD hand-check on a 5-value ramp vs manual EMA.
  - Cross helpers: (10,20)→(21,20) up True; (21,20)→(20,21) down True; equal-then-cross edge cases.
- [ ] **Step 2: Implement minimal code, run pytest.**
- [ ] **Step 3: Zenodo fixture check (verified-fact anchor):** download `bitcoin_technical_indicators.csv` once into `tests/fixtures/kd_macd/` (commit only if <1MB, else generate a 60-row excerpt file and document the source URL+md5 in the test docstring). Assert our `KdStochastic(n=9, alpha=0.5)` K/D within ±0.15 (rolling-HL warmup differences allowed for the first 30 bars) and `Macd(12,26,9)` dif within 0.1% relative on the last 100 rows.
- [ ] **Step 4: `pytest tests/test_indicators/test_kd_macd.py` green.**

### Task 2: Strategy — KdMacdCrypto + KdMacdCryptoConfig

**Files:**
- Create: `src/sngw_trader/strategies/kd_macd_crypto.py`
- Test: `tests/test_strategies/test_kd_macd_crypto.py`

**Interfaces:**
- Consumes: `BarAggregator(86_400)` (1m→UTC day), `KdStochastic`, `Macd`, `VolTargetSizer`/`VolTargetConfig`, `Strategy` base — pattern cloned from `err_momentum_regime.py` (side/qty helpers, `_sync_size`, `_submit`), **minus** ATR stop / watermark / cooldown (paper strategy has none).
- Produces:

```python
class KdMacdCryptoConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    trade_size: Decimal
    kd_n: int = 9
    kd_alpha: float = 0.5
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    allow_short: bool = False          # False: long-only (paper literal), True: long+short variant
    trigger_mode: str = "cross"        # "cross" (paper literal) | "state" (sensitivity)
    sizing_mode: str = "vol_target"
    size_target_vol: float = 0.20
    size_half_life: int = 20
    size_min_scale: float = 0.0
    size_max_scale: float = 3.0
    size_rebalance_band: float = 0.10
    close_positions_on_stop: bool = True

def direction_target(golden: bool, death: bool, current: int, allow_short: bool) -> int
```

- [ ] **Step 1: Failing tests**
  - `direction_target` truth table: golden→+1 (both modes); death→0 if long-only, −1 if allow_short; no-signal→current.
  - Config defaults: kd_n=9, alpha=0.5, 12/26/9, allow_short=False, sizing_mode="vol_target"; daily aggregator bucket = 86_400s.
  - `on_bar` with synthetic 1m bars aggregated to days: after warmup, a golden-cross day opens long with qty scaled by vol-target (use `sizing_mode="fixed"` to assert exact unit qty first, then a vol_target case asserting qty ∈ {unit×scale} bounds and band non-resize).
  - Long-only: death cross day closes to flat (no short order submitted).
  - Long+short: death cross day flips to short (close + open, NETTING-safe sequence via `close_all_positions` then `_submit`).
  - No exchange I/O: strategy source contains no `requests`/`httpx`/`ccxt` import (mirror `test_no_exchange_io.py` pattern).
- [ ] **Step 2: Implement.** on_bar flow: aggregate 1m→daily; on completed day: `sizer.update(close)`, `kd.update(h,l,c)`, `macd.update(close)`; compute golden/death by `trigger_mode` (cross = strict prev-vs-now crossing; state = condition-transition `long_cond` turning True/False); `target = direction_target(...)`; `_sync_size(target, ...)` with same-sign band rebalance allowed on daily close when holding (clone `err_momentum_regime._sync_size`, `seed_stop=False` always). Vol-target warmup: scale is None → fall back to unit `trade_size` qty (document in docstring).
- [ ] **Step 3: `pytest tests/test_strategies/test_kd_macd_crypto.py` green, then full `pytest`.**

### Task 3: Research runner — comparison matrix

**Files:**
- Create: `src/sngw_trader/research/kd_macd_compare.py`
- Output: `research/kd_macd/results.json` + console table.

**Interfaces:**
- Consumes: `executor.run_window`, `metrics.compute_equity_metrics`, `metrics.compute_trade_metrics`, `config.load_settings`.
- Produces: dict keyed `variant/segment/cost` → {cagr, sharpe, mdd, win_rate, profit_factor, n_trades, total_pnl}.

- [ ] **Step 1: Implement matrix** (no test harness required — script; but importable pure functions):
  - Variants: `long_only` (allow_short=False), `long_short` (allow_short=True). Both trigger_mode="cross". (state + kd_n∈{5,14} runs are optional extras behind `--sensitivity` flag.)
  - Segments: `paper` = 2020-07-01→2020-12-31, `is` = 2021-01-01→2022-12-31, `oos` = 2023-01-01→2025-12-30. Warmup pad: 120 days (kd 9 + macd 35 + vol half-life×3=60 → 120 safe).
  - Cost modes: default Settings (`bt_taker_fee` etc.) vs stress — stress via Settings env override in subprocess or a Settings copy (check `Settings` mutability; prefer constructing `Settings(**overrides)`).
  - Use `GridSpec(strategy_path="sngw_trader.strategies.kd_macd_crypto:KdMacdCrypto", config_path="...:KdMacdCryptoConfig", fixed={"trade_size": "0.01"})`.
- [ ] **Step 2: Smoke run** one cell (`long_only/paper`) to validate wiring end-to-end (instrument, catalog window, equity extraction). Expect ≥1 trade in 2020H2 given the strong BTC trend; if 0 trades, debug signals before proceeding.
- [ ] **Step 3: Full matrix run**, save `results.json`, print comparison table (long_only vs long_short per segment).

### Task 4: Reporting — handoff result + receipt

**Files:**
- Create: wiki result at `$LLM_WIKI_PATH/30_PROJECTS/trading/20260905-bt-kd-macd-crypto-result.md` (local path: `C:\Users\seung\Seungwoo5760 Dropbox\최승우\앱\LLM_WIKI\30_PROJECTS\trading\`)
- Create: receipt at `...\40_AGENT_WORKSPACE\agents\trading-agent\20260905-bt-kd-macd-crypto-receipt.md`

- [ ] **Step 1: Result doc** must contain: `handoff_id: 20260905-bt-kd-macd-crypto`; rules/params/universe/periods/cost model (spec §1–2); long_only vs long_short table per segment incl. vol-target effect (compare vs unsized? — optional ablation run with `sizing_mode="fixed"`); verified vs assumption separation (spec §5 list); scope isolation — no other strategy's rules; paper-reproduction section marked "근사 비교" only.
- [ ] **Step 2: Receipt** — `accepted` (if gates 1–3 completed) with pointers to spec/plan/result/logs; or `blocked` with cause if any gate failed.
- [ ] **Step 3: `pytest` full suite green; `git status` shows only intended files.**

---

## Verification gates (from spec §4, order fixed)

1. Unit tests incl. Zenodo fixture anchor.
2. Paper-period segment sanity (trade count ~10–20, direction of returns vs paper narrative).
3. IS/OOS + stress cost matrix, long_only vs long_short.
4. Sensitivity (optional flag runs) — reported, never selected.
5. Result + receipt written; handoff acceptance criteria checklist satisfied.
