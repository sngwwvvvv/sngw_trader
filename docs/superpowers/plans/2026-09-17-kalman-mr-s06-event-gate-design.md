# S06 Event Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pure, deterministic, fail-closed event gate (`evaluate_gate`) with typed event records, stale/missing fail-closed entry control, disputed-source handling, and MANUAL_KILL → emergency-flatten exit routing — no I/O, no Nautilus, no strategy integration.

**Architecture:** One new pure module `src/sngw_trader/indicators/event_gate.py` mirroring the S05 `execution_recovery.py` style (frozen dataclasses, `__post_init__` validation, reason-code tuples, intent constants). `evaluate_gate(events, symbol, now, last_event_check, limits) -> GateDecision` returns `entries_allowed`, `reasons`, and an `exit_action` intent constant that a later strategy adapter translates into S05 transitions (`begin_exit` for `ORDERLY_EXIT`, `emergency_flatten` for `EMERGENCY_FLATTEN`).

**Tech Stack:** Python 3.12+, stdlib only (dataclasses, enum, math), pytest.

**Spec:** [S06 Event Gate Design](../specs/2026-09-17-kalman-mr-s06-event-gate-design.md)

## Global Constraints

- Pure module: no Nautilus imports, no HTTP/exchange clients, no file IO, no wall-clock reads — `now` and `last_event_check` are always arguments (spec: Module Boundary, Error and Safety Rules).
- Missing or stale event state fails closed for new entries (spec: Error and Safety Rules).
- Decisions are deterministic for the same timestamp and input set — evaluation order is normalized (sorted by `event_id`), reasons deduped (spec: Test Contract).
- Monetary-free module: timestamps are UTC epoch `float`s, validated finite.
- A disputed source blocks entries but never routes an exit (spec: Entry rules).
- Exit actions are directives only; the adapter applies them when a position is open (spec: Exit routing).

## Design Decisions (locked by this plan)

- `MISSING_EVENT_STATE` and `STALE_EVENT_STATE` are not exclusive — both reported when both apply (spec).
- `INVALID_INPUT` reasons: empty `symbol`, non-finite `now`/`last_event_check`, or `last_event_check > now` (a future poll timestamp is clock skew and fails closed). Any invalid input short-circuits to `GateDecision(False, ("INVALID_INPUT",), EXIT_NONE)`.
- Window boundaries: `now == effective_at` is active; `now == expires_at` is active (expiry is strict `now > expires_at`).
- Reasons are deduplicated in first-seen order, with events evaluated sorted by `event_id` so input order cannot change the decision.
- Exit actions require fresh event state: when `MISSING_EVENT_STATE` or `STALE_EVENT_STATE` is present, `exit_action` is forced to `NONE` regardless of active events — stale data is not an instruction to flatten (spec: Exit routing, last bullet).
- `EventRecord.observed_at` is validated, caller-owned provenance metadata; the module's freshness judgment uses `last_event_check` only. The field exists so adapters can carry provenance without a second type.
- `GateDecision` enforces its own consistency: `entries_allowed=True` with reasons, or `False` without reasons, raises `ValueError`.

---

### Task 1: Module skeleton — event types, records, and decision validation

**Files:**
- Create: `src/sngw_trader/indicators/event_gate.py`
- Test: `tests/test_indicators/test_event_gate.py`

**Interfaces:**
- Consumes: nothing (stdlib only).
- Produces: `EventType` (enum: `NEWS`, `HALT`, `MANUAL_KILL`), constants `SOURCE_ACTIVE = "ACTIVE"`, `SOURCE_DISPUTED = "DISPUTED"`, `EXIT_NONE = "NONE"`, `EXIT_ORDERLY = "ORDERLY_EXIT"`, `EXIT_EMERGENCY = "EMERGENCY_FLATTEN"`, `EventRecord(event_id, event_type, symbol, effective_at, expires_at, source_status, observed_at)`, `GateLimits(max_event_age_seconds)`, `GateDecision(entries_allowed, reasons, exit_action)` — all frozen dataclasses, validated in `__post_init__`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_indicators/test_event_gate.py
import pytest

from sngw_trader.indicators.event_gate import (
    EXIT_EMERGENCY,
    EXIT_NONE,
    EXIT_ORDERLY,
    EventRecord,
    EventType,
    GateDecision,
    GateLimits,
    SOURCE_ACTIVE,
    SOURCE_DISPUTED,
)


def _record(**overrides: object) -> EventRecord:
    values = dict(
        event_id="ev-1",
        event_type=EventType.NEWS,
        symbol="BTCUSDT",
        effective_at=100.0,
        expires_at=200.0,
        source_status=SOURCE_ACTIVE,
        observed_at=90.0,
    )
    values.update(overrides)
    return EventRecord(**values)


def test_record_defaults_shape():
    record = _record()
    assert record.event_type is EventType.NEWS
    assert record.source_status == SOURCE_ACTIVE


def test_global_scope_record_has_none_symbol():
    record = _record(symbol=None)
    assert record.symbol is None


def test_rejects_empty_event_id():
    with pytest.raises(ValueError):
        _record(event_id="")


def test_rejects_unknown_source_status():
    with pytest.raises(ValueError):
        _record(source_status="MAYBE")


def test_rejects_expiry_before_effective():
    with pytest.raises(ValueError):
        _record(expires_at=99.0)


def test_rejects_nonfinite_timestamps():
    with pytest.raises(ValueError):
        _record(effective_at=float("nan"))
    with pytest.raises(ValueError):
        _record(observed_at=float("inf"))


def test_rejects_empty_symbol_string():
    with pytest.raises(ValueError):
        _record(symbol="")


def test_limits_reject_nonpositive_age():
    with pytest.raises(ValueError):
        GateLimits(max_event_age_seconds=0.0)
    with pytest.raises(ValueError):
        GateLimits(max_event_age_seconds=float("nan"))


def test_decision_rejects_contradictory_state():
    with pytest.raises(ValueError):
        GateDecision(True, ("SOMETHING",), EXIT_NONE)
    with pytest.raises(ValueError):
        GateDecision(False, (), EXIT_NONE)


def test_decision_rejects_unknown_exit_action():
    with pytest.raises(ValueError):
        GateDecision(True, (), "FLATTEN_NOW")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_indicators/test_event_gate.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'sngw_trader.indicators.event_gate'`

- [ ] **Step 3: Write the module skeleton**

```python
# src/sngw_trader/indicators/event_gate.py
"""Pure event gate evaluation for fail-closed entry control and exit routing.

``cooldown_deadline`` from S05 is unrelated here; this module is stateless.
``cooldown_deadline`` enforcement lives in S05's callers; this module owns no
state at all — every evaluation takes its timestamps as arguments.
"""

from __future__ import annotations

import enum
import math
from collections.abc import Sequence
from dataclasses import dataclass


class EventType(enum.Enum):
    NEWS = "NEWS"
    HALT = "HALT"
    MANUAL_KILL = "MANUAL_KILL"


SOURCE_ACTIVE = "ACTIVE"
SOURCE_DISPUTED = "DISPUTED"

EXIT_NONE = "NONE"
EXIT_ORDERLY = "ORDERLY_EXIT"
EXIT_EMERGENCY = "EMERGENCY_FLATTEN"


def _finite_float(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")


def _finite_value(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


@dataclass(frozen=True)
class EventRecord:
    event_id: str
    event_type: EventType
    symbol: str | None
    effective_at: float
    expires_at: float | None
    source_status: str
    observed_at: float

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("event_id is required")
        if not isinstance(self.event_type, EventType):
            raise ValueError("event_type must be an EventType")
        if self.symbol is not None and not self.symbol:
            raise ValueError("symbol must be None or non-empty")
        _finite_float("effective_at", self.effective_at)
        if self.expires_at is not None:
            _finite_float("expires_at", self.expires_at)
            if self.expires_at <= self.effective_at:
                raise ValueError("expires_at must be after effective_at")
        if self.source_status not in (SOURCE_ACTIVE, SOURCE_DISPUTED):
            raise ValueError("source_status must be ACTIVE or DISPUTED")
        _finite_float("observed_at", self.observed_at)


@dataclass(frozen=True)
class GateLimits:
    max_event_age_seconds: float

    def __post_init__(self) -> None:
        _finite_float("max_event_age_seconds", self.max_event_age_seconds)
        if self.max_event_age_seconds <= 0:
            raise ValueError("max_event_age_seconds must be positive")


@dataclass(frozen=True)
class GateDecision:
    entries_allowed: bool
    reasons: tuple[str, ...]
    exit_action: str

    def __post_init__(self) -> None:
        if self.exit_action not in (EXIT_NONE, EXIT_ORDERLY, EXIT_EMERGENCY):
            raise ValueError("exit_action must be a known exit action constant")
        if self.entries_allowed and self.reasons:
            raise ValueError("entries_allowed with reasons is contradictory")
        if not self.entries_allowed and not self.reasons:
            raise ValueError("blocked decision requires a reason")
```

Note: `Sequence` is imported now for the `evaluate_gate` signature in Task 2; the docstring's cooldown sentence states the caller-enforcement ruling from the S05 final review for adapter authors.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_indicators/test_event_gate.py -q`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```powershell
git add src/sngw_trader/indicators/event_gate.py tests/test_indicators/test_event_gate.py
git commit -m "feat(s06): add event gate types and validation"
```

---

### Task 2: evaluate_gate — entry rules (fail-closed)

**Files:**
- Modify: `src/sngw_trader/indicators/event_gate.py` (append)
- Test: `tests/test_indicators/test_event_gate.py` (append)

**Interfaces:**
- Consumes: Task 1 types; `_finite_value`.
- Produces:
  - `evaluate_gate(events: Sequence[EventRecord], symbol: str, now: float, last_event_check: float, limits: GateLimits) -> GateDecision`
  - Entry rules: empty events → `MISSING_EVENT_STATE`; `now - last_event_check > limits.max_event_age_seconds` → `STALE_EVENT_STATE` (both can coexist); active relevant events → `EVENT_ACTIVE_{TYPE}` (deduped); disputed relevant → `SOURCE_DISPUTED` (never an exit trigger); out-of-window ignored; invalid inputs short-circuit to `INVALID_INPUT`; evaluation order normalized by `event_id` so reasons are deterministic; `exit_action` is always `EXIT_NONE` in this task.

- [ ] **Step 1: Write the failing tests**

```python
from sngw_trader.indicators.event_gate import evaluate_gate


def _limits(**overrides: object) -> GateLimits:
    values = dict(max_event_age_seconds=60.0)
    values.update(overrides)
    return GateLimits(**values)


def _evaluate(events=(), symbol="BTCUSDT", now=150.0, last_event_check=120.0, limits=None):
    return evaluate_gate(events, symbol, now, last_event_check, limits or _limits())


def test_fresh_empty_feed_blocks_entries_as_missing():
    decision = _evaluate()
    assert decision.entries_allowed is False
    assert decision.reasons == ("MISSING_EVENT_STATE",)
    assert decision.exit_action == EXIT_NONE


def test_stale_feed_blocks_entries():
    decision = _evaluate(last_event_check=80.0)
    assert decision.reasons == ("STALE_EVENT_STATE",)


def test_missing_and_stale_accumulate_together():
    decision = _evaluate(last_event_check=80.0)
    assert decision.reasons == ("MISSING_EVENT_STATE", "STALE_EVENT_STATE")
    decision = _evaluate(events=(_record(),), last_event_check=80.0)
    assert decision.reasons == ("STALE_EVENT_STATE",)


def test_active_symbol_event_blocks_entries():
    decision = _evaluate(events=(_record(),))
    assert decision.reasons == ("EVENT_ACTIVE_NEWS",)


def test_active_global_event_blocks_any_symbol():
    decision = _evaluate(events=(_record(symbol=None),), symbol="ETHUSDT")
    assert decision.reasons == ("EVENT_ACTIVE_NEWS",)


def test_other_symbol_event_ignored():
    decision = _evaluate(events=(_record(symbol="ETHUSDT"),), symbol="BTCUSDT")
    assert decision.entries_allowed is True
    assert decision.reasons == ()


def test_expired_event_ignored():
    decision = _evaluate(events=(_record(expires_at=140.0),))
    assert decision.entries_allowed is True


def test_pending_event_ignored():
    decision = _evaluate(events=(_record(effective_at=160.0),))
    assert decision.entries_allowed is True


def test_boundary_timestamps_are_active():
    decision = _evaluate(events=(_record(),), now=100.0, last_event_check=100.0)
    assert decision.reasons == ("EVENT_ACTIVE_NEWS",)
    decision = _evaluate(events=(_record(),), now=200.0)
    assert decision.reasons == ("EVENT_ACTIVE_NEWS",)


def test_duplicate_events_dedupe_reasons():
    a = _record(event_id="a")
    b = _record(event_id="b")
    decision = _evaluate(events=(a, b))
    assert decision.reasons == ("EVENT_ACTIVE_NEWS",)


def test_event_order_does_not_change_decision():
    a = _record(event_id="a", event_type=EventType.NEWS)
    b = _record(event_id="b", event_type=EventType.HALT)
    assert _evaluate(events=(a, b)) == _evaluate(events=(b, a))


def test_invalid_symbol_fails_closed():
    decision = _evaluate(symbol="")
    assert decision.reasons == ("INVALID_INPUT",)


def test_future_poll_fails_closed():
    decision = _evaluate(last_event_check=200.0)
    assert decision.reasons == ("INVALID_INPUT",)


def test_nonfinite_now_fails_closed():
    decision = _evaluate(now=float("nan"))
    assert decision.reasons == ("INVALID_INPUT",)


def test_invalid_input_beats_all_other_reasons():
    decision = _evaluate(last_event_check=200.0, events=(_record(),))
    assert decision.reasons == ("INVALID_INPUT",)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_indicators/test_event_gate.py -q`
Expected: FAIL with `ImportError: cannot import name 'evaluate_gate'`

- [ ] **Step 3: Implement evaluate_gate (entry rules)**

Append to the module:

```python
def _add_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def evaluate_gate(
    events: Sequence[EventRecord],
    symbol: str,
    now: float,
    last_event_check: float,
    limits: GateLimits,
) -> GateDecision:
    if (
        not symbol
        or not _finite_value(now)
        or not _finite_value(last_event_check)
        or last_event_check > now
    ):
        return GateDecision(False, ("INVALID_INPUT",), EXIT_NONE)
    reasons: list[str] = []
    if not events:
        _add_reason(reasons, "MISSING_EVENT_STATE")
    if now - last_event_check > limits.max_event_age_seconds:
        _add_reason(reasons, "STALE_EVENT_STATE")
    for event in sorted(events, key=lambda e: e.event_id):
        if event.symbol is not None and event.symbol != symbol:
            continue
        if now < event.effective_at:
            continue
        if event.expires_at is not None and now > event.expires_at:
            continue
        if event.source_status == SOURCE_DISPUTED:
            _add_reason(reasons, "SOURCE_DISPUTED")
            continue
        _add_reason(reasons, f"EVENT_ACTIVE_{event.event_type.value}")
    return GateDecision(not reasons, tuple(reasons), EXIT_NONE)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_indicators/test_event_gate.py -q`
Expected: PASS (26 tests: 11 from Task 1 + 15 new)

- [ ] **Step 5: Commit**

```powershell
git add src/sngw_trader/indicators/event_gate.py tests/test_indicators/test_event_gate.py
git commit -m "feat(s06): add fail-closed entry rules to event gate"
```

---

### Task 3: Exit routing — MANUAL_KILL priority and disputed neutrality

**Files:**
- Modify: `src/sngw_trader/indicators/event_gate.py` (extend `evaluate_gate`)
- Test: `tests/test_indicators/test_event_gate.py` (append)

**Interfaces:**
- Consumes: Task 2 `evaluate_gate` structure.
- Produces: `evaluate_gate` now routes exits: active `MANUAL_KILL` → `EXIT_EMERGENCY`; active `NEWS`/`HALT` → `EXIT_ORDERLY` (never downgrading an already-emergency decision); `EMERGENCY` wins when both present; disputed and out-of-window records emit no exit; stale/missing state forces `EXIT_NONE` even with active events.

- [ ] **Step 1: Write the failing tests**

```python
def test_manual_kill_routes_emergency_flatten():
    decision = _evaluate(events=(_record(event_type=EventType.MANUAL_KILL),))
    assert decision.exit_action == EXIT_EMERGENCY
    assert decision.reasons == ("EVENT_ACTIVE_MANUAL_KILL",)


def test_news_routes_orderly_exit():
    decision = _evaluate(events=(_record(),))
    assert decision.exit_action == EXIT_ORDERLY


def test_halt_routes_orderly_exit():
    decision = _evaluate(events=(_record(event_type=EventType.HALT),))
    assert decision.exit_action == EXIT_ORDERLY


def test_emergency_wins_over_orderly():
    news = _record(event_id="a", event_type=EventType.NEWS)
    kill = _record(event_id="b", event_type=EventType.MANUAL_KILL)
    decision = _evaluate(events=(news, kill))
    assert decision.exit_action == EXIT_EMERGENCY
    reversed_decision = _evaluate(events=(kill, news))
    assert reversed_decision.exit_action == EXIT_EMERGENCY
    assert reversed_decision.reasons == decision.reasons


def test_orderly_does_not_downgrade_emergency():
    kill = _record(event_id="a", event_type=EventType.MANUAL_KILL)
    news = _record(event_id="b", event_type=EventType.NEWS)
    assert _evaluate(events=(kill, news)).exit_action == EXIT_EMERGENCY


def test_expired_kill_emits_no_exit():
    decision = _evaluate(events=(_record(event_type=EventType.MANUAL_KILL, expires_at=140.0),))
    assert decision.exit_action == EXIT_NONE
    assert decision.entries_allowed is True


def test_disputed_event_blocks_entries_without_exit():
    decision = _evaluate(events=(_record(source_status=SOURCE_DISPUTED),))
    assert decision.entries_allowed is False
    assert decision.reasons == ("SOURCE_DISPUTED",)
    assert decision.exit_action == EXIT_NONE


def test_stale_feed_suppresses_exit_action():
    decision = _evaluate(
        events=(_record(event_type=EventType.MANUAL_KILL),), last_event_check=80.0
    )
    assert decision.exit_action == EXIT_NONE
    assert decision.reasons == ("STALE_EVENT_STATE", "EVENT_ACTIVE_MANUAL_KILL")


def test_missing_feed_suppresses_exit_action():
    decision = _evaluate(events=())
    assert decision.exit_action == EXIT_NONE
```

Note on `test_missing_feed_suppresses_exit_action`: it duplicates the behavior asserted in Task 2's `test_fresh_empty_feed_blocks_entries_as_missing` (`exit_action == EXIT_NONE`); it is kept as an explicit statement of the suppression rule.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_indicators/test_event_gate.py -q`
Expected: FAIL — 8 new tests fail because `exit_action` is always `EXIT_NONE` (Task 2 returns it unconditionally).

- [ ] **Step 3: Extend evaluate_gate with exit routing**

In `evaluate_gate`, introduce `exit_action = EXIT_NONE` before the loop, and inside the loop replace the final `_add_reason(reasons, f"EVENT_ACTIVE_{event.event_type.value}")` block with:

```python
        _add_reason(reasons, f"EVENT_ACTIVE_{event.event_type.value}")
        if event.event_type is EventType.MANUAL_KILL:
            exit_action = EXIT_EMERGENCY
        elif exit_action is not EXIT_EMERGENCY:
            exit_action = EXIT_ORDERLY
```

Replace the final return with:

```python
    if "MISSING_EVENT_STATE" in reasons or "STALE_EVENT_STATE" in reasons:
        exit_action = EXIT_NONE
    return GateDecision(not reasons, tuple(reasons), exit_action)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_indicators/test_event_gate.py -q`
Expected: PASS (35 tests: 26 from Tasks 1-2 + 9 new)

- [ ] **Step 5: Commit**

```powershell
git add src/sngw_trader/indicators/event_gate.py tests/test_indicators/test_event_gate.py
git commit -m "feat(s06): add exit routing and kill-switch priority to event gate"
```

---

### Task 4: Full verification

**Files:**
- No new files.

- [ ] **Step 1: Run the focused suite verbosely**

Run: `python -m pytest tests/test_indicators/test_event_gate.py -v`
Expected: PASS (35 tests) covering the spec's test contract: active, expired, stale, symbol-specific, global, missing event states; disputed source; kill-switch priority; deterministic repeat evaluation (`test_event_order_does_not_change_decision` plus tuple equality assertions); invalid record construction; exit routing.

- [ ] **Step 2: Run the full indicators suite**

Run: `python -m pytest tests/test_indicators/ -q`
Expected: PASS (191 + 35 = 226 tests) — no regressions in S01–S05 suites.

- [ ] **Step 3: Confirm the module boundary**

Run: `python -c "import ast; tree = ast.parse(open('src/sngw_trader/indicators/event_gate.py', encoding='utf-8').read()); imports = sorted({(n.module or '') if isinstance(n, ast.ImportFrom) else n.names[0].name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))}); print(imports)"`
Expected: only stdlib imports (`__future__`, `enum`, `math`, `collections.abc`, `dataclasses`) — no HTTP, exchange, or Nautilus imports.

- [ ] **Step 4: Determinism spot-check**

Run: `python -c "import sys; sys.path.insert(0, 'src'); from sngw_trader.indicators.event_gate import EventRecord, EventType, GateLimits, evaluate_gate, SOURCE_ACTIVE; r = EventRecord('ev-1', EventType.NEWS, 'BTCUSDT', 100.0, 200.0, SOURCE_ACTIVE, 90.0); d1 = evaluate_gate((r,), 'BTCUSDT', 150.0, 120.0, GateLimits(60.0)); d2 = evaluate_gate((r,), 'BTCUSDT', 150.0, 120.0, GateLimits(60.0)); assert d1 == d2; print('deterministic')"`
Expected: prints `deterministic`.

- [ ] **Step 5: Update the stage plan checklist**

Mark the [S06 plan](2026-09-15-kalman-mr-s06-event-gate.md) tasks complete and commit:

```powershell
git add docs/superpowers/plans/2026-09-15-kalman-mr-s06-event-gate.md
git commit -m "docs(s06): mark event gate plan tasks complete"
```

Note: the old plan's "Integrate the gate at the strategy boundary" task is satisfied by this module + spec contract only — strategy adapter integration remains excluded (same ruling as S05); note that inline when marking it complete.

---

## Self-Review Notes

- Spec coverage: entry rules (T2), exit routing + kill priority (T3), disputed neutrality (T3), staleness/missing fail-closed (T2), determinism (T2 order-independence + T4 spot-check), construction validation (T1), purity (T4).
- Spec's "stale/missing emits no exit action" is implemented as a post-loop suppression so an active kill on a stale feed does not flatten — consistent with the spec bullet verbatim.
- `observed_at` is validated provenance metadata only; `last_event_check` drives freshness (spec carries both; ambiguity resolved here).
- Reason dedupe + `event_id` sort make decisions order-independent; `GateDecision.__post_init__` makes contradictory decisions unconstructible.