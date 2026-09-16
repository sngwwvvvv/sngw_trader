# OI A/B Mean-Reversion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two independent, reproducible BacktestNode strategies that compare OI crowding rollover (A) with OI liquidation contraction (B) on BTC-USDT-SWAP.OKX 5-minute mean reversion.

**Architecture:** Keep the existing 1-minute OKX catalog as the price source and let each Strategy consume a composite internal 5-minute Bar. Store OI as a registered Nautilus Python custom data class, query it from the ParquetDataCatalog after the BacktestNode engine is built, and add it to the engine before replay. A and B remain separate Strategy classes with shared pure signal helpers only where duplication is proven.

**Tech Stack:** Python 3.12+, NautilusTrader, `ParquetDataCatalog`, `BacktestNode`, PyArrow custom-data serialization, pytest, OKX public historical market-data API.

**Spec:** `docs/superpowers/specs/2026-09-16-oi-mean-reversion-design.md`

## Global Constraints

- Use `BacktestNode` as the runner; do not create a custom event loop or pandas backtest engine.
- Use `BTC-USDT-SWAP.OKX`; live venue remains OKX only.
- Use existing 1-minute external price Bars and `5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL` in the strategies.
- Register custom data with the installed Nautilus API: `nautilus_trader.core.nautilus_pyo3.model.register_custom_data_class`.
- Use `BacktestEngine.add_data` after `BacktestNode.build()` for arbitrary custom OI data; do not pretend it is supported by `BacktestDataConfig`.
- Current price bar `t` may only use OI through the last completed point before `t`; never use same-bar or future OI.
- A means price excursion plus OI increase followed by OI rollover; B means price excursion plus OI decrease.
- BB defaults are SMA(20, 2.0); ATR is Wilder(14); SL is 2.5 ATR; re-entry window is five bars.
- Entries are submitted for the next 5-minute bar, sessions use `America/New_York`, and each session allows at most three filled entries.
- Strategy modules contain only Strategy/config and signal/order logic; no OKX HTTP or node imports.
- Catalog writers may use public OKX HTTP; strategies and runners may not call exchange APIs.
- Do not add a live OI adapter, funding engine integration, automatic walk-forward integration, liquidation data, or extra indicators in this plan.
- Do not commit changes unless the user explicitly requests commits.

---

## File Map

| File | Responsibility |
|---|---|
| `src/sngw_trader/data/open_interest.py` | `OpenInterestPoint`, custom-data registration/wrapping, catalog query/write helpers, raw OI normalization |
| `src/sngw_trader/data/catalog_writer.py` | Existing price download plus optional `OI_ENABLED=true` OI download/write branch |
| `src/sngw_trader/indicators/oi_mean_reversion.py` | Small pure functions for lagged OI return, rollover, re-entry, and pending setup state |
| `src/sngw_trader/strategies/oi_crowding_mean_reversion.py` | A StrategyConfig and Strategy for OI increase plus rollover |
| `src/sngw_trader/strategies/oi_liquidation_mean_reversion.py` | B StrategyConfig and Strategy for OI decrease |
| `src/sngw_trader/runners/backtest_oi.py` | BacktestNode assembly, custom OI loading, A/B selection, reporting |
| `src/sngw_trader/config/settings.py` | OI download and OI backtest environment settings with defaults |
| `.env.example` | Non-secret OI/backtest configuration examples |
| `tests/test_data/test_open_interest.py` | Custom data serialization, normalization, catalog round-trip |
| `tests/test_strategies/test_oi_crowding_mean_reversion.py` | A signal, session, and risk behavior |
| `tests/test_strategies/test_oi_liquidation_mean_reversion.py` | B signal and shared behavior |
| `tests/test_runners/test_backtest_oi.py` | Runner settings, strategy selection, and data injection |

---

### Task 1: Add Registered Open-Interest Custom Data

**Files:**
- Create: `src/sngw_trader/data/open_interest.py`
- Create: `tests/test_data/test_open_interest.py`

**Interfaces:**
- Produces `OpenInterestPoint`, `open_interest_data_type(instrument_id: str) -> DataType`, `wrap_open_interest(point: OpenInterestPoint) -> CustomData`, `register_open_interest() -> None`, and `query_open_interest(catalog: ParquetDataCatalog, instrument_id: str, start: int | None = None, end: int | None = None) -> list[CustomData]`.
- `OpenInterestPoint` carries `instrument_id: InstrumentId`, `open_interest: float`, and decorator-provided `ts_event`/`ts_init` nanoseconds. The payload uses `InstrumentId` because the installed catalog extracts custom-data identifiers through `obj.instrument_id.value`.
- Uses `@customdataclass_pyo3`; call `register_custom_data_class(OpenInterestPoint)` exactly once through an idempotent `register_open_interest()` helper.

- [ ] **Step 1: Confirm the installed custom-data API before writing production code**

Run:

```powershell
uv run python -c "from nautilus_trader.model.custom import customdataclass_pyo3; from nautilus_trader.core.nautilus_pyo3.model import register_custom_data_class; from nautilus_trader.persistence.catalog import ParquetDataCatalog; print(customdataclass_pyo3, register_custom_data_class, ParquetDataCatalog.write_data, ParquetDataCatalog.query)"
```

Expected: all four symbols import successfully. Use `catalog.write_data([...], data_cls=...)` and `catalog.query(data_cls=..., identifiers=[...])` in this installed version; do not add a nonexistent `write_custom_data` method.

- [ ] **Step 2: Write the failing serialization and catalog tests**

Add tests with this shape:

```python
def test_open_interest_point_round_trips_dict_and_bytes():
    point = OpenInterestPoint(
        ts_event=300,
        ts_init=300,
        instrument_id=InstrumentId.from_str("BTC-USDT-SWAP.OKX"),
        open_interest=123.5,
    )
    assert OpenInterestPoint.from_dict(point.to_dict()) == point
    assert OpenInterestPoint.from_bytes(point.to_bytes()) == point


def test_open_interest_point_round_trips_arrow_batch():
    point = OpenInterestPoint(
        ts_event=300,
        ts_init=300,
        instrument_id=InstrumentId.from_str("BTC-USDT-SWAP.OKX"),
        open_interest=123.5,
    )
    batch = point.encode_record_batch_py([point])
    assert OpenInterestPoint.decode_record_batch_py({}, batch) == [point]


def test_catalog_round_trip_returns_custom_data(tmp_path):
    register_open_interest()
    catalog = ParquetDataCatalog(str(tmp_path))
    points = [
        OpenInterestPoint(100, 100, InstrumentId.from_str("BTC-USDT-SWAP.OKX"), 100.0),
        OpenInterestPoint(200, 200, InstrumentId.from_str("BTC-USDT-SWAP.OKX"), 101.0),
    ]
    catalog.write_data(
        [wrap_open_interest(point) for point in points],
        data_cls=OpenInterestPoint,
    )
    result = query_open_interest(catalog, "BTC-USDT-SWAP.OKX")
    assert [item.data.open_interest for item in result] == [100.0, 101.0]
```

Run: `uv run pytest tests/test_data/test_open_interest.py -q`

Expected: FAIL because the module and interfaces do not exist yet. If the failure is an import or constructor mismatch rather than the missing behavior, correct the test to match the installed `customdataclass_pyo3` contract and rerun until the failure is feature-related.

- [ ] **Step 3: Implement the minimal registered custom data**

Implement:

```python
@customdataclass_pyo3
class OpenInterestPoint:
    instrument_id: InstrumentId
    open_interest: float


def open_interest_data_type(instrument_id: str) -> DataType:
    return DataType(
        OpenInterestPoint,
        metadata={"instrument_id": instrument_id},
    )
```

Use the decorator-generated timestamps. `wrap_open_interest` must create `CustomData(open_interest_data_type(point.instrument_id.value), point)`. `register_open_interest` must guard repeated registration in the same process. `query_open_interest` must call `catalog.query(data_cls=OpenInterestPoint, identifiers=[instrument_id], start=start, end=end)`, sort by `data.ts_init`, and return the custom wrappers.

- [ ] **Step 4: Run the focused tests to verify green**

Run: `uv run pytest tests/test_data/test_open_interest.py -q`

Expected: all custom-data serialization and catalog round-trip tests pass without network access.

- [ ] **Step 5: Run existing data tests**

Run: `uv run pytest tests/test_data -q`

Expected: existing funding, catalog writer, and Yahoo ETF tests remain green.

---

### Task 2: Normalize and Store Historical OKX OI

**Files:**
- Modify: `src/sngw_trader/data/open_interest.py`
- Modify: `src/sngw_trader/data/catalog_writer.py`
- Modify: `src/sngw_trader/config/settings.py`
- Modify: `.env.example`
- Modify: `tests/test_data/test_open_interest.py`
- Modify: `tests/test_config/test_settings.py` only if a new default is directly asserted

**Interfaces:**
- Produces `raw_open_interest_to_point(row: list[str] | dict[str, str], instrument_id: str) -> OpenInterestPoint`.
- Produces `parse_open_interest_rows(rows: list[list[str] | dict[str, str]], instrument_id: str, start_ms: int, end_ms: int) -> list[OpenInterestPoint]`.
- Produces `fetch_open_interest(inst_id: str, start_ms: int, end_ms: int, period: str = "5m") -> list[OpenInterestPoint]`.
- Produces `download_open_interest(settings: Settings, instrument_id: str) -> list[OpenInterestPoint]`.
- Adds `Settings.oi_enabled: bool = False` and `Settings.oi_period: str = "5m"` after existing defaulted fields so existing keyword fixtures remain valid.

- [ ] **Step 1: Verify the real OKX response shape with a read-only public request**

Run a public endpoint request using the endpoint documented for historical contracts open interest and print only HTTP status, response code, row count, and the first row. Do not add the endpoint until the response is verified. Confirm:

- exact path and query names
- pagination direction and cursor field
- timestamp field and unit
- OI field and unit
- accepted 5-minute period value

Expected: a successful response for `BTC-USDT-SWAP` with enough metadata to write a parser. If the endpoint does not provide 5-minute history, stop this task and update the spec rather than resampling or interpolating.

- [ ] **Step 2: Write failing parser tests from the verified response shape**

Use a fixed, network-free fixture copied from the verified response shape. Cover ordering, range filtering, duplicate timestamps, and invalid response data:

```python
def test_parse_open_interest_rows_filters_deduplicates_and_sorts():
    rows = [
        VERIFIED_ROW_AT_200,
        VERIFIED_ROW_AT_100,
        VERIFIED_ROW_AT_200,
    ]
    points = parse_open_interest_rows(
        rows,
        "BTC-USDT-SWAP.OKX",
        start_ms=100,
        end_ms=200,
    )
    assert [point.ts_event for point in points] == [100_000_000, 200_000_000]


def test_parse_open_interest_rows_rejects_non_5m_period_shape():
    with pytest.raises(ValueError, match="5-minute"):
        parse_open_interest_rows([VERIFIED_ROW_WITH_INVALID_PERIOD], "BTC-USDT-SWAP.OKX", 0, 1_000)
```

Replace the fixture names above with concrete module constants containing the real verified rows. Run: `uv run pytest tests/test_data/test_open_interest.py -q`. Expected: FAIL because the parser does not exist.

- [ ] **Step 3: Implement normalization and pagination with no trading I/O**

Implement the parser against the verified response, converting source milliseconds to nanoseconds and returning `OpenInterestPoint` values with `ts_event == ts_init` at ingest. Filter inclusive `[start_ms, end_ms]`, deduplicate by timestamp, sort ascending, and reject malformed rows instead of silently dropping them.

Implement `fetch_open_interest` with `urllib.request` and the same public-data-only style as `catalog_writer.py`. Follow the API's verified pagination cursor until the oldest returned timestamp is before `start_ms`; enforce a finite page size and a monotonic cursor so a malformed API response cannot loop forever. Never import `okx`, `ccxt`, or `python-okx`.

- [ ] **Step 4: Add the optional catalog writer branch**

Extend `Settings` parsing:

```python
oi_enabled=_env("OI_ENABLED", "false").lower() in {"1", "true", "yes"},
oi_period=_env("OI_PERIOD", "5m"),
```

In `run_download`, after writing the instrument and price Bars, call `download_open_interest` only when `settings.oi_enabled` is true. Register the custom type before writing and write wrappers with `catalog.write_data(..., data_cls=OpenInterestPoint)`. Print the count and UTC range. Empty results must raise `SystemExit` with the requested instrument and range.

Add to `.env.example`:

```dotenv
OI_ENABLED=false
OI_PERIOD=5m
```

- [ ] **Step 5: Verify parser and existing writer tests**

Run: `uv run pytest tests/test_data/test_open_interest.py tests/test_data/test_catalog_writer.py tests/test_config/test_settings.py -q`

Expected: all pass without network access. Do not claim the live endpoint works until the explicit public endpoint smoke command from Step 1 is run.

---

### Task 3: Add Pure OI and Re-Entry Signal Helpers

**Files:**
- Create: `src/sngw_trader/indicators/oi_mean_reversion.py`
- Create: `tests/test_indicators/test_oi_mean_reversion.py`

**Interfaces:**
- Produces `oi_return(values: Sequence[float], lookback: int) -> float | None`.
- Produces `is_oi_increasing(values: Sequence[float], lookback: int, threshold: float) -> bool`.
- Produces `is_oi_decreasing(values: Sequence[float], lookback: int, threshold: float) -> bool`.
- Produces `has_oi_rollover(latest_oi: float, peak_oi: float, threshold: float) -> bool`.
- Produces `reentry_side(side: int, close: float, lower: float, upper: float) -> bool`.

- [ ] **Step 1: Write failing pure-function tests**

Add tests for:

```python
def test_oi_return_requires_lookback_history():
    assert oi_return([100.0, 101.0], 3) is None


def test_oi_increase_and_decrease_use_percentage_change():
    values = [100.0, 102.0, 104.0]
    assert is_oi_increasing(values, 2, 0.01)
    assert not is_oi_decreasing(values, 2, 0.01)


def test_rollover_requires_current_oi_not_above_peak():
    assert has_oi_rollover(105.0, 105.0, 0.0)
    assert has_oi_rollover(104.0, 105.0, 0.0)
    assert not has_oi_rollover(106.0, 105.0, 0.0)


def test_reentry_is_directional():
    assert reentry_side(1, 100.0, 99.0, 101.0)
    assert reentry_side(-1, 100.0, 99.0, 101.0)
    assert not reentry_side(1, 98.0, 99.0, 101.0)
    assert not reentry_side(-1, 102.0, 99.0, 101.0)
```

Run: `uv run pytest tests/test_indicators/test_oi_mean_reversion.py -q`. Expected: FAIL because the module does not exist.

- [ ] **Step 2: Implement only the pure calculations**

Use percentage change `latest / oldest - 1`. Return `None` for insufficient history or a non-positive baseline. Increase uses `change >= threshold`; decrease uses `change <= -threshold`. Rollover uses `latest <= peak * (1 - threshold)`. Re-entry returns `close >= lower` for long and `close <= upper` for short.

- [ ] **Step 3: Run focused and full indicator tests**

Run: `uv run pytest tests/test_indicators/test_oi_mean_reversion.py tests/test_indicators -q`

Expected: all pass, including existing indicator behavior.

---

### Task 4: Implement Strategy A, OI Crowding Rollover

**Files:**
- Create: `src/sngw_trader/strategies/oi_crowding_mean_reversion.py`
- Create: `tests/test_strategies/test_oi_crowding_mean_reversion.py`

**Interfaces:**
- Produces `OiCrowdingMeanReversionConfig(StrategyConfig, frozen=True)` with the fields in the spec.
- Produces `OiCrowdingMeanReversion(Strategy)`.
- Config `bar_type` is the composite 5-minute BarType; `instrument_id` is `BTC-USDT-SWAP.OKX`.
- The strategy subscribes to the composite Bar and `open_interest_data_type(str(config.instrument_id))`.

- [ ] **Step 1: Write failing config and signal-state tests**

Test the wished-for behavior without requiring a running BacktestNode:

```python
def test_a_config_defaults_match_spec():
    strategy = make_strategy()
    assert strategy.config.bb_period == 20
    assert strategy.config.bb_std == 2.0
    assert strategy.config.atr_period == 14
    assert strategy.config.atr_mult == 2.5
    assert strategy.config.reentry_bars == 5
    assert strategy.config.oi_lookback_bars == 3


def test_a_long_requires_lower_breach_oi_increase_rollover_and_reentry():
    strategy = make_strategy()
    strategy._test_feed_oi([100.0, 102.0, 104.0])
    strategy._test_start_setup(side=1, close=98.0, lower=99.0)
    assert strategy._pending_setup is not None
    assert strategy._test_reentry(side=1, close=100.0, lower=99.0, upper=101.0, oi=103.0)


def test_a_does_not_enter_while_oi_keeps_rising():
    strategy = make_strategy()
    strategy._test_start_setup(side=1, close=98.0, lower=99.0)
    assert not strategy._test_reentry(side=1, close=100.0, lower=99.0, upper=101.0, oi=105.0)


def test_a_expires_after_five_reentry_bars():
    strategy = make_strategy()
    strategy._test_start_setup(side=1, close=98.0, lower=99.0)
    for _ in range(5):
        strategy._test_advance_pending(close=98.5, lower=99.0, upper=101.0, oi=104.0)
    assert strategy._pending_setup is None
```

The `_test_*` calls above are not production interfaces. Implement them as the smallest test-only state injection helpers if direct `on_bar` setup would require a full Nautilus engine; keep them private and document that they do not submit orders. Run: `uv run pytest tests/test_strategies/test_oi_crowding_mean_reversion.py -q`. Expected: FAIL because the strategy does not exist.

- [ ] **Step 2: Implement subscriptions and indicator warm-up**

Use `BollingerBands(config.bb_period, config.bb_std)` and `AverageTrueRange(config.atr_period, MovingAverageType.WILDER, use_previous=True)`. Update both only from completed composite Bars. Maintain the latest OI values received through `on_data`; when processing price bar `t`, take the most recent OI point with `ts_event < bar.ts_event` and never the current/future point.

Subscribe in `on_start` and close positions in `on_stop` when configured. Do not query the catalog or call any HTTP API from the Strategy.

- [ ] **Step 3: Implement A pending setup and entry trigger**

On a session-valid, flat bar after BB/ATR warm-up:

1. Detect `close < lower` for long or `close > upper` for short.
2. Use the lagged OI history and `is_oi_increasing`.
3. Store side, age, OI peak, ATR value, and breach event state.
4. For each of the next five bars, update peak OI and age.
5. At the first directional re-entry, require `has_oi_rollover`; otherwise keep waiting or expire.
6. Capture the re-entry bar's opposite BB and ATR.
7. Submit one next-bar market entry and clear the setup.

Do not create a second setup while a setup, position, or entry order is active. After a position closes, clear the pending setup, captured ATR, captured target, peak OI, and entry-order reference before accepting a new setup.

- [ ] **Step 4: Implement fixed bracket behavior**

On the entry fill, calculate:

```python
stop = fill_price - 2.5 * captured_atr  # long
stop = fill_price + 2.5 * captured_atr  # short
target = captured_opposite_band
```

Quantize both with the instrument. Use Nautilus order factory bracket/OCO support if the installed API exposes it; otherwise submit the reduce-only stop and target orders after the fill using native Nautilus order types. Do not implement a polling or sleep loop. If the captured target is not on the profitable side of the fill, do not submit the bracket and close/skip according to the tested conservative behavior.

- [ ] **Step 5: Add session and trade-limit tests, then implement them**

Add failing tests for a weekend timestamp, 09:29, 16:00, a DST transition date, and the fourth filled entry in one New York session. Assert no new order is submitted and that 16:00 closes an open position. Use timezone-aware `datetime` with `zoneinfo.ZoneInfo("America/New_York")`; never compare fixed UTC offsets.

Implement the session counter reset by New York calendar date and increment only on an entry `OrderFilled` event. Force flat at the first completed bar at or after 16:00, cancel pending setups, and reject new entries after three fills.

- [ ] **Step 6: Run A strategy tests**

Run: `uv run pytest tests/test_strategies/test_oi_crowding_mean_reversion.py -q`

Expected: all A signal, bracket, session, DST, and trade-limit tests pass without a node or network.

---

### Task 5: Implement Strategy B, OI Liquidation Contraction

**Files:**
- Create: `src/sngw_trader/strategies/oi_liquidation_mean_reversion.py`
- Create: `tests/test_strategies/test_oi_liquidation_mean_reversion.py`

**Interfaces:**
- Produces `OiLiquidationMeanReversionConfig(StrategyConfig, frozen=True)` with the common fields plus `oi_decrease_threshold`.
- Produces `OiLiquidationMeanReversion(Strategy)`.
- Uses the same composite 5-minute Bar, session, sizing, ATR, and bracket rules as A.

- [ ] **Step 1: Write failing B-specific tests**

Add tests with these assertions:

```python
def test_b_long_requires_lower_breach_and_oi_decrease():
    strategy = make_strategy()
    strategy._test_feed_oi([104.0, 102.0, 100.0])
    assert strategy._test_start_setup(side=1, close=98.0, lower=99.0)


def test_b_short_requires_upper_breach_and_oi_decrease():
    strategy = make_strategy()
    strategy._test_feed_oi([104.0, 102.0, 100.0])
    assert strategy._test_start_setup(side=-1, close=102.0, upper=101.0)


def test_b_rejects_oi_increase():
    strategy = make_strategy()
    strategy._test_feed_oi([100.0, 102.0, 104.0])
    assert not strategy._test_start_setup(side=1, close=98.0, lower=99.0)
```

Run: `uv run pytest tests/test_strategies/test_oi_liquidation_mean_reversion.py -q`. Expected: FAIL because B does not exist.

- [ ] **Step 2: Implement B by reusing only proven pure helpers**

Use `is_oi_decreasing` for both long and short breach candidates. Do not copy A's rollover state into B and do not make B depend on A's class. Keep the same five-bar first-re-entry behavior and bracket/session implementation.

- [ ] **Step 3: Verify B and all strategy tests**

Run: `uv run pytest tests/test_strategies/test_oi_liquidation_mean_reversion.py tests/test_strategies/test_oi_crowding_mean_reversion.py tests/test_strategies -q`

Expected: all new and existing strategy tests pass.

---

### Task 6: Add the OI Backtest Runner and Settings

**Files:**
- Create: `src/sngw_trader/runners/backtest_oi.py`
- Modify: `src/sngw_trader/config/settings.py`
- Modify: `.env.example`
- Create: `tests/test_runners/test_backtest_oi.py`

**Interfaces:**
- Produces `oi_bar_type(instrument_id: str) -> BarType` for the composite 5-minute type.
- Produces `build_oi_strategy(settings: Settings) -> Strategy`.
- Produces `build_oi_run_config(settings: Settings, start: datetime | None = None, end: datetime | None = None) -> BacktestRunConfig`.
- Produces `attach_oi_data(engine: BacktestEngine, catalog: ParquetDataCatalog, instrument_id: str, start_ns: int | None, end_ns: int | None) -> None`.
- `main()` builds one `BacktestNode`, adds the queried OI data before `node.run()`, exports fills, and disposes in `finally`.

- [ ] **Step 1: Write failing runner tests**

Add tests with a small fake node/engine and real strategy configs:

```python
def test_oi_bar_type_is_composite_5m():
    assert str(oi_bar_type("BTC-USDT-SWAP.OKX")) == (
        "BTC-USDT-SWAP.OKX-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL"
    )


def test_build_oi_strategy_selects_a(monkeypatch):
    settings = make_settings(oi_strategy="oi_a")
    assert isinstance(build_oi_strategy(settings), OiCrowdingMeanReversion)


def test_build_oi_strategy_selects_b(monkeypatch):
    settings = make_settings(oi_strategy="oi_b")
    assert isinstance(build_oi_strategy(settings), OiLiquidationMeanReversion)


def test_build_oi_strategy_rejects_unknown_name():
    with pytest.raises(SystemExit, match="OI_STRATEGY"):
        build_oi_strategy(make_settings(oi_strategy="bad"))
```

Run: `uv run pytest tests/test_runners/test_backtest_oi.py -q`. Expected: FAIL because the runner does not exist.

- [ ] **Step 2: Add settings with backward-compatible defaults**

Add to the end of `Settings`' existing defaulted fields:

```python
oi_enabled: bool = False
oi_period: str = "5m"
oi_strategy: str = "oi_a"
```

Parse `OI_ENABLED`, `OI_PERIOD`, and `OI_STRATEGY`. Preserve the current default `STRATEGY` and existing `Settings(...)` fixtures. OI runner uses `oi_strategy`; existing runner uses `strategy`.

Add these examples to `.env.example`:

```dotenv
OI_ENABLED=false
OI_PERIOD=5m
OI_STRATEGY=oi_a
```

- [ ] **Step 3: Implement composite BarType and strategy selection**

Construct the composite BarType exactly as:

```python
BarType.from_str(
    f"{instrument_id}-5-MINUTE-LAST-INTERNAL@1-MINUTE-EXTERNAL"
)
```

Build either StrategyConfig using `InstrumentId.from_str`, the composite BarType, `Decimal(settings.trade_size)`, and the config defaults from the spec. Invalid `oi_strategy` raises `SystemExit("Unknown OI_STRATEGY ...")`.

- [ ] **Step 4: Implement BacktestNode assembly and custom-data injection**

Reuse `build_run_config`'s venue, fee, fill, latency, and starting-balance conventions, but set the price `BacktestDataConfig` to the existing 1-minute external Bar. After `node.build()`, obtain the engine with `node.get_engine(run_config.id)`, call `register_open_interest()`, query OI through `ParquetDataCatalog`, and call `engine.add_data(wrapped_oi, sort=True)` before adding the strategy and running the node.

Do not import OI HTTP functions in this runner. The runner only reads the catalog. If no OI rows are found, raise a clear `RuntimeError` naming the catalog, instrument, and requested range before `node.run()`.

- [ ] **Step 5: Verify runner tests and existing runner tests**

Run: `uv run pytest tests/test_runners/test_backtest_oi.py tests/test_runners/test_backtest_okx.py -q`

Expected: new selection/injection tests and all existing runner tests pass. The tests must not run a full network or live node.

---

### Task 7: End-to-End Synthetic Backtest and Full Verification

**Files:**
- Modify: `tests/test_runners/test_backtest_oi.py`
- Modify: `tests/test_data/test_open_interest.py` if an end-to-end fixture helper is needed
- Modify: `README.md` only for the final local execution commands

**Interfaces:**
- Consumes the catalog writer, custom data, A/B strategies, and `backtest_oi.py` from Tasks 1-6.
- Produces a network-free smoke path proving that a custom OI point reaches `on_data`, a 5-minute price bar reaches `on_bar`, and the runner can execute one synthetic setup without lookahead.

- [ ] **Step 1: Write the failing end-to-end replay test**

Build a temporary catalog containing:

- one `BTC-USDT-SWAP.OKX` instrument from the existing test fixture/helper
- enough ascending 1-minute Bars to warm BB(20) and ATR(14), plus a lower-band breach and first re-entry
- OI points whose timestamp is one 5-minute period before the price bar that consumes them

Assert that:

```python
result = run_synthetic_oi_backtest(..., oi_strategy="oi_a")
assert result.n_trades == 1
assert result.first_signal_oi_ts < result.entry_bar_ts
```

Repeat with OI contraction for `oi_b`. Before implementation this test must fail because the synthetic runner helper does not exist.

- [ ] **Step 2: Implement the smallest test-only catalog/backtest helper**

Use `BacktestNode` and the same `build_oi_run_config` path. Add data through `engine.add_data` before `node.run()`. Return only the fields needed by the test, such as closed trade count, first signal OI timestamp, and entry bar timestamp. Do not create a second production runner or event loop.

- [ ] **Step 3: Run the full suite**

Run:

```powershell
uv run pytest -q
```

Expected: all existing and new tests pass without network access.

- [ ] **Step 4: Run the explicit public-data smoke check**

After setting a bounded `CATALOG_START`/`CATALOG_END` and `OI_ENABLED=true` in a local environment, run:

```powershell
uv run catalog-download
```

Expected: the catalog contains the existing 1-minute price Bars and registered `OpenInterestPoint` data for the requested range. If the endpoint response, unit, or period differs from the plan, stop and update the spec rather than silently adapting the data.

- [ ] **Step 5: Run a bounded A and B BacktestNode smoke**

With a local catalog containing both datasets, run:

```powershell
$env:OI_STRATEGY="oi_a"; uv run python -m sngw_trader.runners.backtest_oi
$env:OI_STRATEGY="oi_b"; uv run python -m sngw_trader.runners.backtest_oi
```

Expected: both runs build and dispose one BacktestNode, export fills, and use different OI conditions. No live node, API key, or custom event loop is involved.

- [ ] **Step 6: Review the final diff without committing**

Run:

```powershell
```

Confirm that only the files in the File Map changed, no secrets or catalog files are tracked, and no direct OKX order/API code entered a Strategy or runner.

## Verification Matrix

| Requirement | Plan coverage |
|---|---|
| A price excursion + OI increase + rollover | Tasks 3-4 |
| B price excursion + OI decrease | Tasks 3 and 5 |
| BB(20, 2), Wilder ATR(14), 2.5 ATR SL | Task 4, shared by Task 5 |
| Opposite BB TP snapshot | Task 4, shared by Task 5 |
| Five-bar first re-entry | Tasks 3-5 |
| 09:30-16:00 ET, weekdays, DST | Task 4 |
| Three filled entries per session | Task 4 |
| No current/future OI lookahead | Tasks 1, 4, 7 |
| Parquet custom OI data | Tasks 1-2 |
| BacktestNode only | Tasks 6-7 |
| Existing runner behavior preserved | Tasks 2 and 6 |
| No live OI adapter | Global constraints and Task 7 |
