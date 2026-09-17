# S05 Execution and Recovery Implementation Plan (Design Review Version)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pure two-leg execution state machine (entry/exit transitions, timeout rollback, emergency flatten, slippage gate, reconciliation, restart gate, persistence round-trip) with no exchange or Nautilus dependency.

**Architecture:** One new pure module `src/sngw_trader/indicators/execution_recovery.py` with frozen dataclasses and pure transition functions, mirroring the S04 `portfolio_risk.py` style. Transitions return `TransitionResult(state, actions, reasons)` where `actions` are intent constants (`SUBMIT`, `CANCEL_UNFILLED`, `FLATTEN_FILLED`, `BLOCK_ENTRIES`, `ENABLE_ENTRIES`) — never exchange operations. A later strategy adapter translates intents to official Nautilus order APIs.

**Tech Stack:** Python 3.12+, stdlib only (dataclasses, decimal, enum, math, types), pytest.

**Spec:** [S05 Execution and Recovery Design](../specs/2026-09-17-kalman-mr-s05-execution-recovery-design.md) (supersedes the task list in the older [S05 plan](2026-09-15-kalman-mr-s05-execution-recovery.md); Nautilus callback connection is explicitly out of scope there and in this plan)

## Global Constraints

- No Nautilus imports, no exchange calls, no file persistence, no order submission/cancellation inside the module (spec: Module Boundary).
- No custom event loop or runner; no direct OKX/ccxt/python-okx calls (AGENTS.md / NAUTILUS_VIBE_RULES.md).
- No unmanaged one-leg exposure after its deadline (spec: Error and Safety Rules).
- Invalid quantities, missing leg identity, duplicate fill identity, invalid slippage inputs fail closed (spec: Error and Safety Rules).
- Monetary values use `Decimal`; timestamps and Kalman state use finite `float` (mirrors S04 style).
- Legs are fixed to the two pair legs `"Y"` and `"X"`.
- Malformed `FillObservation` / `ExecutionState` data raises `ValueError` at the construction boundary; state-dependent failures inside transitions return reason codes (this is the fail-closed contract).

## Design Decisions (locked by this plan)

- `ExecutionState.legs` is a `Mapping[str, LegState]` with exactly keys `"Y"` and `"X"`.
- Held quantity per leg: for `EXIT_PENDING`/`EXIT_PARTIAL` phases it is `target_quantity - filled_quantity` (exit fills reduce the holding); for all other phases it is `filled_quantity`. `ExecutionState.exposure` sums held quantity across both legs.
- Rollback (post-deadline timeout, order failure, slippage rejection, emergency flatten) emits per leg: `CANCEL_UNFILLED` for legs with an active pending order, `FLATTEN_FILLED` for legs with held quantity. With exposure the phase becomes `FLATTENING`; without exposure it returns to `IDLE`.
- On transition to `FLATTENING`, each held leg's `filled_quantity` is set to the held amount, which `confirm_flatten` then decrements; exposure reaching 0 moves the state to `CLOSED` and records `cooldown_deadline`.
- `emergency_flatten` with no exposure is a no-op returning reason `NO_EXPOSURE` (nothing to confirm, no cooldown to record).
- `reconcile` failure sets phase to `BLOCKED` and stores the first reason in `recovery_reason`; `begin_entry`/`begin_exit` refuse while `recovery_reason` is set. A later matching reconciliation restores `BLOCKED` → `IDLE` and clears `recovery_reason`.
- Reconciliation treats a non-zero observed position with zero expected position as `UNEXPECTED_POSITION_{leg}` and emits a `FLATTEN_FILLED` intent for it; the observed quantity itself is advisory only (the module cannot place orders).
- `fill_ids` is a frozenset that grows for the life of the state; `begin_entry`/`begin_exit` reset it. Acceptable for a per-pair lifecycle.

---

### Task 1: Module skeleton — state types and validation

**Files:**
- Create: `src/sngw_trader/indicators/execution_recovery.py`
- Test: `tests/test_indicators/test_execution_recovery.py`

**Interfaces:**
- Consumes: nothing (stdlib only).
- Produces: `ExecutionPhase`, `LegStatus` (enums), `SUBMIT`, `CANCEL_UNFILLED`, `FLATTEN_FILLED`, `BLOCK_ENTRIES`, `ENABLE_ENTRIES` (str constants), `ActionIntent(kind, leg=None, client_order_id=None, quantity=None)`, `LegState(leg, target_quantity=None, filled_quantity=Decimal("0"), client_order_id=None, status=LegStatus.IDLE)`, `FillObservation(leg, fill_id, quantity, fill_price, reference_price)`, `ExecutionState(phase=IDLE, legs={}, pending_order_ids=frozenset(), deadline=None, entry_beta=None, cooldown_deadline=None, entries_started=0, exits_started=0, timeouts=0, rollbacks=0, slippage_rejections=0, recovery_reason=None, kalman=None, max_slippage_bps=Decimal("50"), fill_ids=frozenset())` with `.exposure -> Decimal`, `TransitionResult(state, actions=(), reasons=())`. All frozen dataclasses; all validated in `__post_init__`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_indicators/test_execution_recovery.py
from decimal import Decimal

import pytest

from sngw_trader.indicators.execution_recovery import (
    ExecutionPhase,
    ExecutionState,
    FillObservation,
    LegStatus,
    LegState,
    TransitionResult,
)


def test_default_state_is_idle_with_zero_exposure():
    state = ExecutionState()
    assert state.phase is ExecutionPhase.IDLE
    assert state.exposure == Decimal("0")
    assert state.legs["Y"].status is LegStatus.IDLE


def test_legs_default_to_idle_pair_legs():
    state = ExecutionState()
    assert set(state.legs) == {"Y", "X"}


def test_rejects_non_numeric_fill_quantity():
    with pytest.raises(ValueError):
        FillObservation(leg="Y", fill_id="f1", quantity="abc", fill_price="100", reference_price="100")


def test_rejects_nonpositive_fill_or_reference_price():
    with pytest.raises(ValueError):
        FillObservation(leg="Y", fill_id="f1", quantity="1", fill_price="0", reference_price="100")
    with pytest.raises(ValueError):
        FillObservation(leg="Y", fill_id="f1", quantity="1", fill_price="100", reference_price="-1")


def test_rejects_missing_leg_or_fill_identity():
    with pytest.raises(ValueError):
        FillObservation(leg="", fill_id="f1", quantity="1", fill_price="100", reference_price="100")
    with pytest.raises(ValueError):
        FillObservation(leg="Y", fill_id="", quantity="1", fill_price="100", reference_price="100")


def test_normalizes_decimal_fields():
    obs = FillObservation(leg="Y", fill_id="f1", quantity="1.5", fill_price="100", reference_price="100")
    assert obs.quantity == Decimal("1.5")
    assert obs.fill_price == Decimal("100")


def test_rejects_negative_counter():
    with pytest.raises(ValueError):
        ExecutionState(timeouts=-1)


def test_rejects_bool_counter():
    with pytest.raises(ValueError):
        ExecutionState(timeouts=True)


def test_rejects_nonfinite_float_field():
    with pytest.raises(ValueError):
        ExecutionState(deadline=float("nan"))


def test_rejects_nonpositive_slippage_limit():
    with pytest.raises(ValueError):
        ExecutionState(max_slippage_bps=Decimal("0"))


def test_rejects_mismatched_leg_key():
    with pytest.raises(ValueError):
        ExecutionState(legs={"Y": LegState(leg="X")})


def test_rejects_nonfinite_kalman_value():
    with pytest.raises(ValueError):
        ExecutionState(kalman={"alpha": float("inf")})


def test_transition_result_defaults():
    result = TransitionResult(ExecutionState())
    assert result.actions == ()
    assert result.reasons == ()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_indicators/test_execution_recovery.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'sngw_trader.indicators.execution_recovery'`

- [ ] **Step 3: Write the module skeleton**

```python
# src/sngw_trader/indicators/execution_recovery.py
"""Pure two-leg execution state transitions, reconciliation, and restart gating."""

from __future__ import annotations

import enum
import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any


class ExecutionPhase(enum.Enum):
    IDLE = "IDLE"
    ENTRY_PENDING = "ENTRY_PENDING"
    ENTRY_PARTIAL = "ENTRY_PARTIAL"
    OPEN = "OPEN"
    EXIT_PENDING = "EXIT_PENDING"
    EXIT_PARTIAL = "EXIT_PARTIAL"
    CLOSED = "CLOSED"
    FLATTENING = "FLATTENING"
    BLOCKED = "BLOCKED"


class LegStatus(enum.Enum):
    IDLE = "IDLE"
    PENDING = "PENDING"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    FLATTENING = "FLATTENING"


SUBMIT = "SUBMIT"
CANCEL_UNFILLED = "CANCEL_UNFILLED"
FLATTEN_FILLED = "FLATTEN_FILLED"
BLOCK_ENTRIES = "BLOCK_ENTRIES"
ENABLE_ENTRIES = "ENABLE_ENTRIES"


def _decimal(name: str, value: object) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a finite Decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    return result


def _finite_float(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")


@dataclass(frozen=True)
class ActionIntent:
    kind: str
    leg: str | None = None
    client_order_id: str | None = None
    quantity: Decimal | None = None


@dataclass(frozen=True)
class LegState:
    leg: str
    target_quantity: Decimal | None = None
    filled_quantity: Decimal = Decimal("0")
    client_order_id: str | None = None
    status: LegStatus = LegStatus.IDLE

    def __post_init__(self) -> None:
        if not self.leg:
            raise ValueError("leg identity is required")
        if not isinstance(self.status, LegStatus):
            raise ValueError("status must be a LegStatus")
        if self.target_quantity is not None:
            object.__setattr__(self, "target_quantity", _decimal("target_quantity", self.target_quantity))
        object.__setattr__(self, "filled_quantity", _decimal("filled_quantity", self.filled_quantity))
        if self.filled_quantity < 0:
            raise ValueError("filled_quantity must not be negative")
        if self.client_order_id is not None and not self.client_order_id:
            raise ValueError("client_order_id must be non-empty when present")


@dataclass(frozen=True)
class FillObservation:
    leg: str
    fill_id: str
    quantity: Decimal
    fill_price: Decimal
    reference_price: Decimal

    def __post_init__(self) -> None:
        if not self.leg:
            raise ValueError("leg identity is required")
        if not self.fill_id:
            raise ValueError("fill_id is required")
        quantity = _decimal("quantity", self.quantity)
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        fill_price = _decimal("fill_price", self.fill_price)
        reference_price = _decimal("reference_price", self.reference_price)
        if fill_price <= 0 or reference_price <= 0:
            raise ValueError("prices must be positive")
        object.__setattr__(self, "quantity", quantity)
        object.__setattr__(self, "fill_price", fill_price)
        object.__setattr__(self, "reference_price", reference_price)


@dataclass(frozen=True)
class ExecutionState:
    phase: ExecutionPhase = ExecutionPhase.IDLE
    legs: Mapping[str, LegState] = MappingProxyType(
        {"Y": LegState(leg="Y"), "X": LegState(leg="X")}
    )
    pending_order_ids: frozenset[str] = frozenset()
    deadline: float | None = None
    entry_beta: float | None = None
    cooldown_deadline: float | None = None
    entries_started: int = 0
    exits_started: int = 0
    timeouts: int = 0
    rollbacks: int = 0
    slippage_rejections: int = 0
    recovery_reason: str | None = None
    kalman: Mapping[str, float] | None = None
    max_slippage_bps: Decimal = Decimal("50")
    fill_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not isinstance(self.phase, ExecutionPhase):
            raise ValueError("phase must be an ExecutionPhase")
        for name in ("deadline", "entry_beta", "cooldown_deadline"):
            value = getattr(self, name)
            if value is not None:
                _finite_float(name, value)
        for name in (
            "entries_started",
            "exits_started",
            "timeouts",
            "rollbacks",
            "slippage_rejections",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        object.__setattr__(self, "max_slippage_bps", _decimal("max_slippage_bps", self.max_slippage_bps))
        if self.max_slippage_bps <= 0:
            raise ValueError("max_slippage_bps must be positive")
        if not isinstance(self.pending_order_ids, frozenset) or any(
            not isinstance(value, str) for value in self.pending_order_ids
        ):
            raise ValueError("pending_order_ids must be a frozenset of strings")
        if not isinstance(self.fill_ids, frozenset):
            raise ValueError("fill_ids must be a frozenset")
        legs = dict(self.legs)
        for name, leg in legs.items():
            if not isinstance(leg, LegState) or leg.leg != name:
                raise ValueError("legs must map leg name to a matching LegState")
        object.__setattr__(self, "legs", MappingProxyType(legs))
        if self.kalman is not None:
            kalman = {}
            for key, value in self.kalman.items():
                _finite_float(f"kalman[{key!r}]", value)
                kalman[key] = float(value)
            object.__setattr__(self, "kalman", MappingProxyType(kalman))

    @property
    def exposure(self) -> Decimal:
        return sum(
            (_leg_held(self, leg) for leg in self.legs.values()), Decimal("0")
        )


@dataclass(frozen=True)
class TransitionResult:
    state: ExecutionState
    actions: tuple[ActionIntent, ...] = ()
    reasons: tuple[str, ...] = ()
```

Note: `_leg_held` and the `_ACTIVE_PHASES` constant are defined in Task 2/3 but referenced here — to keep Task 1 self-contained and green, add this minimal version at the bottom of the file now (Task 3 keeps it as-is):

```python
_ACTIVE_PHASES = (
    ExecutionPhase.ENTRY_PENDING,
    ExecutionPhase.ENTRY_PARTIAL,
    ExecutionPhase.EXIT_PENDING,
    ExecutionPhase.EXIT_PARTIAL,
)


def _leg_held(state: ExecutionState, leg: LegState) -> Decimal:
    """Held quantity for a leg: remaining holding during exit, executed fill otherwise."""
    if state.phase in (ExecutionPhase.EXIT_PENDING, ExecutionPhase.EXIT_PARTIAL):
        target = leg.target_quantity or Decimal("0")
        return max(target - leg.filled_quantity, Decimal("0"))
    return leg.filled_quantity
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_indicators/test_execution_recovery.py -q`
Expected: PASS (11 tests)

- [ ] **Step 5: Commit**

```powershell
git add src/sngw_trader/indicators/execution_recovery.py tests/test_indicators/test_execution_recovery.py
git commit -m "feat(s05): add execution recovery state types and validation"
```

---

### Task 2: begin_entry / begin_exit / on_fill transitions

**Files:**
- Modify: `src/sngw_trader/indicators/execution_recovery.py` (append)
- Test: `tests/test_indicators/test_execution_recovery.py` (append)

**Interfaces:**
- Consumes: Task 1 types; `dataclasses.replace`; `MappingProxyType`.
- Produces:
  - `begin_entry(state, y_quantity: Decimal, x_quantity: Decimal, y_order_id: str, x_order_id: str, deadline: float, entry_beta: float) -> TransitionResult` — valid from `IDLE`/`CLOSED` with no `recovery_reason`; emits `SUBMIT` per leg.
  - `begin_exit(state, y_order_id: str, x_order_id: str, deadline: float) -> TransitionResult` — valid from `OPEN` only; exit targets are the held quantities; emits `SUBMIT` per leg.
  - `on_fill(state, fill: FillObservation) -> TransitionResult` — valid in `_ACTIVE_PHASES`; dedupes by `fill_id`; full two-leg fill → `OPEN` (entry) / `CLOSED` (exit) with pending orders and deadline cleared.

- [ ] **Step 1: Write the failing tests**

```python
from sngw_trader.indicators.execution_recovery import (
    ExecutionPhase,
    ExecutionState,
    FillObservation,
    LegStatus,
    on_fill,
    begin_entry,
    begin_exit,
    SUBMIT,
)


def _entered(**overrides: object) -> ExecutionState:
    result = begin_entry(
        ExecutionState(**overrides),
        y_quantity="10",
        x_quantity="8",
        y_order_id="o-y",
        x_order_id="o-x",
        deadline=100.0,
        entry_beta=1.25,
    )
    assert result.reasons == ()
    return result.state


def test_begin_entry_emits_submit_intents():
    result = begin_entry(
        ExecutionState(),
        y_quantity="10",
        x_quantity="8",
        y_order_id="o-y",
        x_order_id="o-x",
        deadline=100.0,
        entry_beta=1.25,
    )
    assert result.state.phase is ExecutionPhase.ENTRY_PENDING
    assert [a.kind for a in result.actions] == [SUBMIT, SUBMIT]
    assert result.state.legs["Y"].target_quantity == Decimal("10")
    assert result.state.legs["X"].client_order_id == "o-x"
    assert result.state.pending_order_ids == frozenset({"o-y", "o-x"})
    assert result.state.deadline == 100.0
    assert result.state.entry_beta == 1.25
    assert result.state.entries_started == 1


def test_begin_entry_rejects_nonpositive_quantity():
    result = begin_entry(
        ExecutionState(), "0", "8", "o-y", "o-x", deadline=100.0, entry_beta=1.0
    )
    assert result.state.phase is ExecutionPhase.IDLE
    assert result.reasons == ("INVALID_QUANTITY",)


def test_begin_entry_rejects_when_not_idle():
    result = begin_entry(
        _entered(), "10", "8", "o-y2", "o-x2", deadline=200.0, entry_beta=1.0
    )
    assert result.reasons == ("PHASE_NOT_IDLE",)


def test_begin_exit_requires_open():
    blocked = begin_exit(_entered(), "e-y", "e-x", deadline=150.0)
    assert blocked.reasons == ("PHASE_NOT_OPEN",)
    opened = on_fill(
        _entered(), FillObservation("Y", "f1", "10", "100", "100")
    )
    opened = on_fill(opened.state, FillObservation("X", "f2", "8", "99", "99"))
    assert opened.state.phase is ExecutionPhase.OPEN
    exit_result = begin_exit(opened.state, "e-y", "e-x", deadline=150.0)
    assert exit_result.state.phase is ExecutionPhase.EXIT_PENDING
    assert [a.kind for a in exit_result.actions] == [SUBMIT, SUBMIT]
    assert exit_result.state.legs["Y"].target_quantity == Decimal("10")


def test_full_fill_both_legs_reaches_open():
    result = on_fill(_entered(), FillObservation("Y", "f1", "10", "100", "100"))
    assert result.state.phase is ExecutionPhase.ENTRY_PARTIAL
    assert result.state.legs["Y"].status is LegStatus.FILLED
    result = on_fill(result.state, FillObservation("X", "f2", "8", "99", "99"))
    assert result.state.phase is ExecutionPhase.OPEN
    assert result.state.legs["X"].filled_quantity == Decimal("8")
    assert result.state.pending_order_ids == frozenset()
    assert result.state.deadline is None


def test_partial_fill_keeps_partial_phase():
    result = on_fill(_entered(), FillObservation("Y", "f1", "4", "100", "100"))
    assert result.state.phase is ExecutionPhase.ENTRY_PARTIAL
    assert result.state.legs["Y"].status is LegStatus.PARTIAL
    assert result.state.legs["X"].status is LegStatus.PENDING
    assert result.state.pending_order_ids == frozenset({"o-y", "o-x"})


def test_duplicate_fill_ignored():
    first = on_fill(_entered(), FillObservation("Y", "f1", "4", "100", "100"))
    repeat = on_fill(first.state, FillObservation("Y", "f1", "4", "100", "100"))
    assert repeat.reasons == ("DUPLICATE_FILL",)
    assert repeat.state == first.state


def test_fill_unknown_leg_fails_closed():
    result = on_fill(_entered(), FillObservation("Z", "f1", "1", "100", "100"))
    assert result.reasons == ("MISSING_LEG",)


def test_overfill_fails_closed():
    result = on_fill(_entered(), FillObservation("Y", "f1", "11", "100", "100"))
    assert result.reasons == ("OVERFILL",)


def test_exit_full_fill_reaches_closed():
    opened = on_fill(_entered(), FillObservation("Y", "f1", "10", "100", "100"))
    opened = on_fill(opened.state, FillObservation("X", "f2", "8", "99", "99"))
    exited = begin_exit(opened.state, "e-y", "e-x", deadline=150.0)
    exited = on_fill(exited.state, FillObservation("Y", "f3", "10", "100", "100"))
    exited = on_fill(exited.state, FillObservation("X", "f4", "8", "99", "99"))
    assert exited.state.phase is ExecutionPhase.CLOSED
    assert exited.state.exposure == Decimal("0")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_indicators/test_execution_recovery.py -q`
Expected: FAIL with `ImportError: cannot import name 'on_fill'`

- [ ] **Step 3: Implement the transitions**

Append to the module:

```python
def begin_entry(
    state: ExecutionState,
    y_quantity: Decimal,
    x_quantity: Decimal,
    y_order_id: str,
    x_order_id: str,
    deadline: float,
    entry_beta: float,
) -> TransitionResult:
    if state.recovery_reason is not None or state.phase is ExecutionPhase.BLOCKED:
        return TransitionResult(state, reasons=("ENTRIES_BLOCKED",))
    if state.phase not in (ExecutionPhase.IDLE, ExecutionPhase.CLOSED):
        return TransitionResult(state, reasons=("PHASE_NOT_IDLE",))
    try:
        y_qty = _decimal("y_quantity", y_quantity)
        x_qty = _decimal("x_quantity", x_quantity)
    except ValueError:
        return TransitionResult(state, reasons=("INVALID_QUANTITY",))
    if y_qty <= 0 or x_qty <= 0:
        return TransitionResult(state, reasons=("INVALID_QUANTITY",))
    if not y_order_id or not x_order_id:
        return TransitionResult(state, reasons=("MISSING_ORDER_ID",))
    if not _finite_value(deadline) or not _finite_value(entry_beta):
        return TransitionResult(state, reasons=("INVALID_INPUT",))
    legs = MappingProxyType(
        {
            "Y": LegState("Y", y_qty, Decimal("0"), y_order_id, LegStatus.PENDING),
            "X": LegState("X", x_qty, Decimal("0"), x_order_id, LegStatus.PENDING),
        }
    )
    new_state = replace(
        state,
        phase=ExecutionPhase.ENTRY_PENDING,
        legs=legs,
        pending_order_ids=frozenset({y_order_id, x_order_id}),
        deadline=deadline,
        entry_beta=entry_beta,
        cooldown_deadline=None,
        entries_started=state.entries_started + 1,
        recovery_reason=None,
        fill_ids=frozenset(),
    )
    actions = (
        ActionIntent(SUBMIT, leg="Y", client_order_id=y_order_id, quantity=y_qty),
        ActionIntent(SUBMIT, leg="X", client_order_id=x_order_id, quantity=x_qty),
    )
    return TransitionResult(new_state, actions)


def begin_exit(
    state: ExecutionState,
    y_order_id: str,
    x_order_id: str,
    deadline: float,
) -> TransitionResult:
    if state.phase is not ExecutionPhase.OPEN:
        return TransitionResult(state, reasons=("PHASE_NOT_OPEN",))
    if state.recovery_reason is not None:
        return TransitionResult(state, reasons=("ENTRIES_BLOCKED",))
    if not y_order_id or not x_order_id:
        return TransitionResult(state, reasons=("MISSING_ORDER_ID",))
    if not _finite_value(deadline):
        return TransitionResult(state, reasons=("INVALID_INPUT",))
    legs = MappingProxyType(
        {
            "Y": replace(state.legs["Y"], client_order_id=y_order_id, status=LegStatus.PENDING),
            "X": replace(state.legs["X"], client_order_id=x_order_id, status=LegStatus.PENDING),
        }
    )
    new_state = replace(
        state,
        phase=ExecutionPhase.EXIT_PENDING,
        legs=legs,
        pending_order_ids=frozenset({y_order_id, x_order_id}),
        deadline=deadline,
        exits_started=state.exits_started + 1,
        fill_ids=frozenset(),
    )
    actions = (
        ActionIntent(SUBMIT, leg="Y", client_order_id=y_order_id, quantity=state.legs["Y"].filled_quantity),
        ActionIntent(SUBMIT, leg="X", client_order_id=x_order_id, quantity=state.legs["X"].filled_quantity),
    )
    return TransitionResult(new_state, actions)


def on_fill(state: ExecutionState, fill: FillObservation) -> TransitionResult:
    if state.phase not in _ACTIVE_PHASES:
        return TransitionResult(state, reasons=("PHASE_NOT_ACTIVE",))
    leg_state = state.legs.get(fill.leg)
    if leg_state is None:
        return TransitionResult(state, reasons=("MISSING_LEG",))
    if fill.fill_id in state.fill_ids:
        return TransitionResult(state, reasons=("DUPLICATE_FILL",))
    target = leg_state.target_quantity or Decimal("0")
    filled = leg_state.filled_quantity + fill.quantity
    if filled > target:
        return TransitionResult(state, reasons=("OVERFILL",))
    legs = dict(state.legs)
    legs[fill.leg] = replace(
        leg_state,
        filled_quantity=filled,
        status=LegStatus.FILLED if filled == target else LegStatus.PARTIAL,
    )
    all_filled = all(leg.status is LegStatus.FILLED for leg in legs.values())
    entry = state.phase in (ExecutionPhase.ENTRY_PENDING, ExecutionPhase.ENTRY_PARTIAL)
    if all_filled:
        phase = ExecutionPhase.OPEN if entry else ExecutionPhase.CLOSED
    else:
        phase = ExecutionPhase.ENTRY_PARTIAL if entry else ExecutionPhase.EXIT_PARTIAL
    new_state = replace(
        state,
        phase=phase,
        legs=MappingProxyType(legs),
        fill_ids=state.fill_ids | {fill.fill_id},
        pending_order_ids=frozenset() if all_filled else state.pending_order_ids,
        deadline=None if all_filled else state.deadline,
    )
    return TransitionResult(new_state)
```

Also add next to `_ACTIVE_PHASES`:

```python
def _finite_value(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_indicators/test_execution_recovery.py -q`
Expected: PASS (all tests including Task 1)

- [ ] **Step 5: Commit**

```powershell
git add src/sngw_trader/indicators/execution_recovery.py tests/test_indicators/test_execution_recovery.py
git commit -m "feat(s05): add entry/exit begin and fill transitions"
```

---

### Task 3: Timeout and order-failure rollback

**Files:**
- Modify: `src/sngw_trader/indicators/execution_recovery.py` (append)
- Test: `tests/test_indicators/test_execution_recovery.py` (append)

**Interfaces:**
- Consumes: Task 1 `ExecutionState`/`_ACTIVE_PHASES`/`_leg_held`; Task 2 `_entered` test helper pattern, `begin_entry`, `on_fill`.
- Produces:
  - `on_timeout(state, now: float) -> TransitionResult` — `TIMEOUT_NOT_DUE` before the deadline (state unchanged); after the deadline: no exposure → `IDLE` + `CANCEL_UNFILLED`; any exposure → `FLATTENING` + `CANCEL_UNFILLED` + `FLATTEN_FILLED` per held leg; increments `timeouts`.
  - `on_order_failure(state, leg: str) -> TransitionResult` — same rollback rule as a post-deadline timeout; increments `rollbacks`.

- [ ] **Step 1: Write the failing tests**

```python
from sngw_trader.indicators.execution_recovery import (
    CANCEL_UNFILLED,
    FLATTEN_FILLED,
    on_order_failure,
    on_timeout,
)


def _partially_filled() -> ExecutionState:
    result = on_fill(_entered(), FillObservation("Y", "f1", "10", "100", "100"))
    return result.state


def test_timeout_before_deadline_is_not_due():
    result = on_timeout(_entered(), now=99.9)
    assert result.reasons == ("TIMEOUT_NOT_DUE",)
    assert result.state.phase is ExecutionPhase.ENTRY_PENDING
    assert result.state.timeouts == 0


def test_timeout_after_deadline_without_exposure_returns_idle():
    result = on_timeout(_entered(), now=100.1)
    assert result.state.phase is ExecutionPhase.IDLE
    assert result.state.timeouts == 1
    assert result.state.pending_order_ids == frozenset()
    assert result.state.deadline is None
    assert {a.kind for a in result.actions} == {CANCEL_UNFILLED}
    cancel_legs = {a.leg for a in result.actions if a.kind == CANCEL_UNFILLED}
    assert cancel_legs == {"Y", "X"}


def test_timeout_after_deadline_with_exposure_flattens():
    result = on_timeout(_partially_filled(), now=100.1)
    assert result.state.phase is ExecutionPhase.FLATTENING
    assert result.reasons == ()
    kinds = {a.kind for a in result.actions}
    assert CANCEL_UNFILLED in kinds
    assert FLATTEN_FILLED in kinds
    flatten = [a for a in result.actions if a.kind == FLATTEN_FILLED]
    assert flatten == [ActionIntent(FLATTEN_FILLED, leg="Y", quantity=Decimal("10"))]
    assert result.state.timeouts == 1


def test_order_failure_rolls_back_like_timeout():
    no_exposure = on_order_failure(_entered(), leg="Y")
    assert no_exposure.state.phase is ExecutionPhase.IDLE
    assert no_exposure.state.rollbacks == 1
    with_exposure = on_order_failure(_partially_filled(), leg="X")
    assert with_exposure.state.phase is ExecutionPhase.FLATTENING
    assert any(a.kind == FLATTEN_FILLED for a in with_exposure.actions)


def test_timeout_inactive_phase_is_not_applicable():
    result = on_timeout(ExecutionState(), now=1.0)
    assert result.reasons == ("TIMEOUT_NOT_APPLICABLE",)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_indicators/test_execution_recovery.py -q`
Expected: FAIL with `ImportError: cannot import name 'on_timeout'`

- [ ] **Step 3: Implement rollback**

Append to the module:

```python
def _rollback(state: ExecutionState, counter: str) -> TransitionResult:
    """Shared rollback: cancel pending orders, flatten any held exposure, else return to IDLE."""
    actions: list[ActionIntent] = []
    legs: dict[str, LegState] = {}
    has_exposure = False
    for name, leg in state.legs.items():
        held = _leg_held(state, leg)
        pending = leg.client_order_id is not None and leg.status in (
            LegStatus.PENDING,
            LegStatus.PARTIAL,
        )
        if pending:
            actions.append(ActionIntent(CANCEL_UNFILLED, leg=name, client_order_id=leg.client_order_id))
        if held > 0:
            has_exposure = True
            actions.append(ActionIntent(FLATTEN_FILLED, leg=name, quantity=held))
            legs[name] = replace(leg, target_quantity=held, filled_quantity=held, status=LegStatus.FLATTENING)
        else:
            legs[name] = replace(leg, target_quantity=None, filled_quantity=Decimal("0"), status=LegStatus.IDLE)
    new_state = replace(
        state,
        phase=ExecutionPhase.FLATTENING if has_exposure else ExecutionPhase.IDLE,
        legs=MappingProxyType(legs),
        pending_order_ids=frozenset(),
        deadline=None,
        **{counter: getattr(state, counter) + 1},
    )
    return TransitionResult(new_state, tuple(actions))


def on_timeout(state: ExecutionState, now: float) -> TransitionResult:
    if state.phase not in _ACTIVE_PHASES or state.deadline is None:
        return TransitionResult(state, reasons=("TIMEOUT_NOT_APPLICABLE",))
    if now < state.deadline:
        return TransitionResult(state, reasons=("TIMEOUT_NOT_DUE",))
    return _rollback(state, "timeouts")


def on_order_failure(state: ExecutionState, leg: str) -> TransitionResult:
    if state.phase not in _ACTIVE_PHASES:
        return TransitionResult(state, reasons=("PHASE_NOT_ACTIVE",))
    if leg not in state.legs:
        return TransitionResult(state, reasons=("MISSING_LEG",))
    return _rollback(state, "rollbacks")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_indicators/test_execution_recovery.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git add src/sngw_trader/indicators/execution_recovery.py tests/test_indicators/test_execution_recovery.py
git commit -m "feat(s05): add timeout and order-failure rollback"
```

---

### Task 4: Slippage rejection gate

**Files:**
- Modify: `src/sngw_trader/indicators/execution_recovery.py` (extend `on_fill`)
- Test: `tests/test_indicators/test_execution_recovery.py` (append)

**Interfaces:**
- Consumes: Task 2 `on_fill`, Task 3 `_rollback`.
- Produces: `on_fill` now computes `abs(fill_price - reference_price) / reference_price * 10_000` and, when it exceeds `state.max_slippage_bps`, applies the fill then routes the state to `FLATTENING` (or `IDLE` with no remaining exposure) with reason `SLIPPAGE_EXCEEDED`, `slippage_rejections += 1`, and `CANCEL_UNFILLED`/`FLATTEN_FILLED` intents.

- [ ] **Step 1: Write the failing tests**

```python
def test_fill_within_slippage_limit_passes():
    result = on_fill(_entered(), FillObservation("Y", "f1", "4", "100.4", "100"))
    assert result.reasons == ()
    assert result.state.phase is ExecutionPhase.ENTRY_PARTIAL


def test_fill_beyond_slippage_limit_flattens():
    result = on_fill(_entered(), FillObservation("Y", "f1", "4", "100.6", "100"))
    assert result.reasons == ("SLIPPAGE_EXCEEDED",)
    assert result.state.phase is ExecutionPhase.FLATTENING
    assert result.state.slippage_rejections == 1
    assert ActionIntent(FLATTEN_FILLED, leg="Y", quantity=Decimal("4")) in result.actions


def test_slippage_uses_default_limit_of_50_bps():
    assert on_fill(_entered(), FillObservation("Y", "f1", "1", "100.4", "100")).reasons == ()
    assert on_fill(_entered(), FillObservation("Y", "f2", "1", "100.6", "100")).reasons == ("SLIPPAGE_EXCEEDED",)


def test_slippage_rejection_with_strict_limit():
    strict = _entered(max_slippage_bps=Decimal("10"))
    result = on_fill(strict, FillObservation("Y", "f1", "4", "100.3", "100"))
    assert result.reasons == ("SLIPPAGE_EXCEEDED",)
```

(Tests assert the literal string `"SLIPPAGE_EXCEEDED"`; no import is needed.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_indicators/test_execution_recovery.py -q`
Expected: FAIL — `test_fill_beyond_slippage_limit_flattens` and friends fail because `on_fill` currently accepts any fill (phase stays `ENTRY_PARTIAL`).

- [ ] **Step 3: Extend on_fill with the slippage gate**

In `on_fill`, after the duplicate-fill check, compute the slippage and route to rollback before the normal return:

```python
    slippage_bps = abs(fill.fill_price - fill.reference_price) / fill.reference_price * Decimal("10000")
```

Replace the final `return TransitionResult(new_state)` with:

```python
    if slippage_bps > state.max_slippage_bps:
        result = _rollback(new_state, "slippage_rejections")
        return TransitionResult(result.state, result.actions, ("SLIPPAGE_EXCEEDED",))
    return TransitionResult(new_state)
```

(The fill quantity is still accumulated first: it happened, and `_rollback` flattens held exposure including this fill.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_indicators/test_execution_recovery.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git add src/sngw_trader/indicators/execution_recovery.py tests/test_indicators/test_execution_recovery.py
git commit -m "feat(s05): add slippage rejection gate to fills"
```

---

### Task 5: Emergency flatten and flatten confirmation with cooldown

**Files:**
- Modify: `src/sngw_trader/indicators/execution_recovery.py` (append)
- Test: `tests/test_indicators/test_execution_recovery.py` (append)

**Interfaces:**
- Consumes: Task 3 `_rollback`, `_ACTIVE_PHASES`.
- Produces:
  - `emergency_flatten(state) -> TransitionResult` — from any active phase: `_rollback` (counter `rollbacks`); from `FLATTENING`: reason `ALREADY_FLATTENING`; from `IDLE`/`CLOSED`: reason `NO_EXPOSURE`, state unchanged.
  - `confirm_flatten(state, fills: Sequence[FillObservation], cooldown_deadline: float) -> TransitionResult` — valid only in `FLATTENING`; duplicate fill ids ignored; overfill rejected; when exposure reaches 0 → `CLOSED`, `cooldown_deadline` recorded, reason `FLATTEN_INCOMPLETE` until then.

- [ ] **Step 1: Write the failing tests**

```python
def _flattening() -> ExecutionState:
    return on_timeout(_partially_filled(), now=100.1).state


def test_emergency_flatten_from_partial_fill():
    result = emergency_flatten(_partially_filled())
    assert result.state.phase is ExecutionPhase.FLATTENING
    assert result.state.rollbacks == 1
    assert ActionIntent(FLATTEN_FILLED, leg="Y", quantity=Decimal("10")) in result.actions


def test_emergency_flatten_without_exposure_is_noop():
    result = emergency_flatten(ExecutionState())
    assert result.reasons == ("NO_EXPOSURE",)
    assert result.state.phase is ExecutionPhase.IDLE


def test_emergency_flatten_twice_is_rejected():
    once = emergency_flatten(_partially_filled())
    again = emergency_flatten(once.state)
    assert again.reasons == ("ALREADY_FLATTENING",)


def test_confirm_flatten_completes_to_closed_with_cooldown():
    state = _flattening()
    result = confirm_flatten(
        state,
        (FillObservation("Y", "g1", "10", "100", "100"),),
        cooldown_deadline=999.0,
    )
    assert result.state.phase is ExecutionPhase.CLOSED
    assert result.state.cooldown_deadline == 999.0
    assert result.state.exposure == Decimal("0")


def test_confirm_flatten_incomplete_stays_flattening():
    result = confirm_flatten(_flattening(), (FillObservation("Y", "g1", "4", "100", "100"),), cooldown_deadline=999.0)
    assert result.reasons == ("FLATTEN_INCOMPLETE",)
    assert result.state.phase is ExecutionPhase.FLATTENING
    assert result.state.cooldown_deadline is None


def test_confirm_flatten_rejects_overfill():
    result = confirm_flatten(_flattening(), (FillObservation("Y", "g1", "11", "100", "100"),), cooldown_deadline=999.0)
    assert result.reasons == ("INVALID_QUANTITY",)


def test_confirm_flatten_ignores_duplicate_fill_id():
    state = _flattening()
    first = confirm_flatten(state, (FillObservation("Y", "g1", "4", "100", "100"),), cooldown_deadline=999.0)
    assert first.reasons == ("FLATTEN_INCOMPLETE",)
    second = confirm_flatten(first.state, (FillObservation("Y", "g1", "4", "100", "100"),), cooldown_deadline=999.0)
    assert second.reasons == ("FLATTEN_INCOMPLETE",)
    assert second.state.legs["Y"].filled_quantity == Decimal("6")


def test_confirm_flatten_requires_flattening_phase():
    result = confirm_flatten(ExecutionState(), (), cooldown_deadline=999.0)
    assert result.reasons == ("PHASE_NOT_FLATTENING",)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_indicators/test_execution_recovery.py -q`
Expected: FAIL with `ImportError: cannot import name 'emergency_flatten'`

- [ ] **Step 3: Implement emergency_flatten and confirm_flatten**

Append to the module:

```python
def emergency_flatten(state: ExecutionState) -> TransitionResult:
    if state.phase is ExecutionPhase.FLATTENING:
        return TransitionResult(state, reasons=("ALREADY_FLATTENING",))
    if state.phase in (ExecutionPhase.IDLE, ExecutionPhase.CLOSED):
        return TransitionResult(state, reasons=("NO_EXPOSURE",))
    return _rollback(state, "rollbacks")


def confirm_flatten(
    state: ExecutionState,
    fills: Sequence[FillObservation],
    cooldown_deadline: float,
) -> TransitionResult:
    if state.phase is not ExecutionPhase.FLATTENING:
        return TransitionResult(state, reasons=("PHASE_NOT_FLATTENING",))
    if not _finite_value(cooldown_deadline):
        return TransitionResult(state, reasons=("INVALID_INPUT",))
    current = state
    for fill in fills:
        leg_state = current.legs.get(fill.leg)
        if leg_state is None:
            return TransitionResult(current, reasons=("MISSING_LEG",))
        if fill.fill_id in current.fill_ids:
            continue
        if fill.quantity > leg_state.filled_quantity:
            return TransitionResult(current, reasons=("INVALID_QUANTITY",))
        remaining = leg_state.filled_quantity - fill.quantity
        legs = dict(current.legs)
        legs[fill.leg] = replace(
            leg_state,
            filled_quantity=remaining,
            status=LegStatus.FLATTENING if remaining > 0 else LegStatus.IDLE,
        )
        current = replace(
            current,
            legs=MappingProxyType(legs),
            fill_ids=current.fill_ids | {fill.fill_id},
        )
    if current.exposure > 0:
        return TransitionResult(current, reasons=("FLATTEN_INCOMPLETE",))
    new_state = replace(
        current,
        phase=ExecutionPhase.CLOSED,
        pending_order_ids=frozenset(),
        deadline=None,
        cooldown_deadline=cooldown_deadline,
    )
    return TransitionResult(new_state)
```

Add `Sequence` to the `collections.abc` import line.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_indicators/test_execution_recovery.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git add src/sngw_trader/indicators/execution_recovery.py tests/test_indicators/test_execution_recovery.py
git commit -m "feat(s05): add emergency flatten, flatten confirmation, and cooldown"
```

---

### Task 6: Reconciliation and restart gate

**Files:**
- Modify: `src/sngw_trader/indicators/execution_recovery.py` (append)
- Test: `tests/test_indicators/test_execution_recovery.py` (append)

**Interfaces:**
- Consumes: Task 2 `_leg_held`, `begin_entry`, `on_fill`; Task 1 types.
- Produces:
  - `reconcile(state, observed_positions: Mapping[str, Decimal], observed_order_ids: frozenset[str] = frozenset()) -> TransitionResult`
    - Exact match → `RECONCILED` reason, `ENABLE_ENTRIES` action, `recovery_reason` cleared, `BLOCKED` phase restored to `IDLE`.
    - Failures → phase `BLOCKED`, first reason stored in `recovery_reason`, reasons include `MISSING_POSITION_{leg}` / `POSITION_MISMATCH_{leg}` / `UNEXPECTED_POSITION_{leg}` / `PENDING_ORDER_MISMATCH`; unexpected/observed exposure emits `FLATTEN_FILLED`, stale pending ids emit `CANCEL_UNFILLED`, plus one `BLOCK_ENTRIES` action.
  - `begin_entry`/`begin_exit` already refuse while `recovery_reason` is set or phase is `BLOCKED` (Task 2's `recovery_reason` gate; extend `begin_exit` guard to also check `BLOCKED` phase).

- [ ] **Step 1: Write the failing tests**

```python
import sngw_trader.indicators.execution_recovery as er


def _open_state() -> ExecutionState:
    result = on_fill(_entered(), FillObservation("Y", "f1", "10", "100", "100"))
    result = on_fill(result.state, FillObservation("X", "f2", "8", "99", "99"))
    return result.state


def test_matching_reconciliation_enables_entries():
    result = er.reconcile(
        _open_state(),
        observed_positions={"Y": Decimal("10"), "X": Decimal("8")},
        observed_order_ids=frozenset(),
    )
    assert result.reasons == ("RECONCILED",)
    assert [a.kind for a in result.actions] == [er.ENABLE_ENTRIES]
    assert result.state.recovery_reason is None


def test_missing_position_blocks():
    result = er.reconcile(
        _open_state(),
        observed_positions={"Y": Decimal("10"), "X": Decimal("0")},
        observed_order_ids=frozenset(),
    )
    assert result.state.phase is ExecutionPhase.BLOCKED
    assert "MISSING_POSITION_X" in result.reasons
    assert result.state.recovery_reason == "MISSING_POSITION_X"
    assert er.ActionIntent(kind=er.BLOCK_ENTRIES) in result.actions


def test_quantity_mismatch_blocks():
    result = er.reconcile(
        _open_state(),
        observed_positions={"Y": Decimal("9"), "X": Decimal("8")},
        observed_order_ids=frozenset(),
    )
    assert "POSITION_MISMATCH_Y" in result.reasons


def test_unexpected_position_blocks_with_flatten_intent():
    result = er.reconcile(
        _open_state(),
        observed_positions={"Y": Decimal("10"), "X": Decimal("8"), "SOL": Decimal("5")},
        observed_order_ids=frozenset(),
    )
    assert "UNEXPECTED_POSITION_SOL" in result.reasons
    assert er.ActionIntent(kind=er.FLATTEN_FILLED, leg="SOL", quantity=Decimal("5")) in result.actions


def test_pending_order_mismatch_blocks_with_cancel():
    result = er.reconcile(
        ExecutionState(),
        observed_positions={},
        observed_order_ids=frozenset({"stale-1"}),
    )
    assert "PENDING_ORDER_MISMATCH" in result.reasons


def test_blocked_state_refuses_new_entry_until_recovery():
    blocked = er.reconcile(
        _open_state(),
        observed_positions={"Y": Decimal("10"), "X": Decimal("0")},
        observed_order_ids=frozenset(),
    ).state
    refused = begin_entry(blocked, "10", "8", "o-y", "o-x", deadline=100.0, entry_beta=1.0)
    assert refused.reasons == ("ENTRIES_BLOCKED",)
    recovered = er.reconcile(
        blocked,
        observed_positions={"Y": Decimal("10"), "X": Decimal("8")},
        observed_order_ids=frozenset(),
    )
    assert recovered.reasons == ("RECONCILED",)
    allowed = begin_entry(recovered.state, "10", "8", "o-y", "o-x", deadline=100.0, entry_beta=1.0)
    assert allowed.reasons == ()


def test_reconcile_uses_exit_remaining_as_expected_position():
    opened = _open_state()
    exiting = begin_exit(opened, "e-y", "e-x", deadline=150.0)
    partial_exit = on_fill(exiting.state, FillObservation("Y", "f3", "10", "100", "100"))
    result = er.reconcile(
        partial_exit.state,
        observed_positions={"Y": Decimal("0"), "X": Decimal("8")},
        observed_order_ids=frozenset({"e-y", "e-x"}),
    )
    assert result.reasons == ("RECONCILED",)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_indicators/test_execution_recovery.py -q`
Expected: FAIL with `AttributeError: module ... has no attribute 'reconcile'`

- [ ] **Step 3: Implement reconcile**

Append to the module:

```python
def reconcile(
    state: ExecutionState,
    observed_positions: Mapping[str, Decimal],
    observed_order_ids: frozenset[str] = frozenset(),
) -> TransitionResult:
    reasons: list[str] = []
    actions: list[ActionIntent] = []
    for name in ("Y", "X"):
        leg = state.legs.get(name)
        if leg is None:
            continue
        expected = _leg_held(state, leg)
        observed = observed_positions.get(name)
        if observed is None:
            if expected > 0:
                reasons.append(f"MISSING_POSITION_{name}")
            continue
        observed_qty = _decimal(f"observed[{name}]", observed)
        if observed_qty == expected:
            continue
        if expected == 0 and observed_qty > 0:
            reasons.append(f"UNEXPECTED_POSITION_{name}")
            actions.append(ActionIntent(FLATTEN_FILLED, leg=name, quantity=observed_qty))
        elif expected > 0 and observed_qty == 0:
            reasons.append(f"MISSING_POSITION_{name}")
        else:
            reasons.append(f"POSITION_MISMATCH_{name}")
    for name, value in observed_positions.items():
        if name in state.legs:
            continue
        observed_qty = _decimal(f"observed[{name}]", value)
        if observed_qty != 0:
            reasons.append(f"UNEXPECTED_POSITION_{name}")
            actions.append(ActionIntent(FLATTEN_FILLED, leg=name, quantity=observed_qty))
    if state.pending_order_ids != frozenset(observed_order_ids):
        reasons.append("PENDING_ORDER_MISMATCH")
        for order_id in state.pending_order_ids - frozenset(observed_order_ids):
            actions.append(ActionIntent(CANCEL_UNFILLED, client_order_id=order_id))
    if reasons:
        blocked = replace(state, phase=ExecutionPhase.BLOCKED, recovery_reason=reasons[0])
        return TransitionResult(
            blocked,
            tuple(actions) + (ActionIntent(BLOCK_ENTRIES),),
            tuple(reasons),
        )
    enabled_phase = ExecutionPhase.IDLE if state.phase is ExecutionPhase.BLOCKED else state.phase
    enabled = replace(state, phase=enabled_phase, recovery_reason=None)
    return TransitionResult(enabled, (ActionIntent(ENABLE_ENTRIES),), ("RECONCILED",))
```

Also strengthen the `begin_exit` gate in Task 2 to match `begin_entry` (replace the recovery check):

```python
    if state.recovery_reason is not None or state.phase is ExecutionPhase.BLOCKED:
        return TransitionResult(state, reasons=("ENTRIES_BLOCKED",))
```

(`begin_entry`'s gate was already written in this order in Task 2: the `ENTRIES_BLOCKED` check comes before the `PHASE_NOT_IDLE` check, so a `BLOCKED` state reports `ENTRIES_BLOCKED`, not `PHASE_NOT_IDLE`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_indicators/test_execution_recovery.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git add src/sngw_trader/indicators/execution_recovery.py tests/test_indicators/test_execution_recovery.py
git commit -m "feat(s05): add reconciliation and restart entry gate"
```

---

### Task 7: Persisted state round-trip

**Files:**
- Modify: `src/sngw_trader/indicators/execution_recovery.py` (append)
- Test: `tests/test_indicators/test_execution_recovery.py` (append)

**Interfaces:**
- Consumes: All prior tasks. Storage/serialization IO stays with the caller (spec: Persistence Contract); this plan only provides pure dict conversion + validation.
- Produces:
  - `persist_state(state) -> dict[str, Any]` — JSON-safe dict (Decimals as `str`, enums as `.value`, frozensets as `sorted` lists).
  - `restore_state(data: Mapping[str, Any]) -> ExecutionState` — validates every field before constructing; raises `ValueError` on missing keys, unknown enum values, negative counters, non-finite floats, malformed Decimals.

- [ ] **Step 1: Write the failing tests**

```python
from dataclasses import replace

from sngw_trader.indicators.execution_recovery import persist_state, restore_state


def test_persist_restore_round_trip():
    state = _open_state()
    state = replace(state, kalman={"alpha": 0.5, "beta": 1.25}, entry_beta=1.25)
    restored = restore_state(persist_state(state))
    assert restored == state


def test_round_trip_through_blocked_state():
    blocked = er.reconcile(
        _open_state(),
        observed_positions={"Y": Decimal("10"), "X": Decimal("0")},
        observed_order_ids=frozenset(),
    ).state
    assert restore_state(persist_state(blocked)) == blocked


def test_restore_rejects_unknown_phase():
    data = persist_state(ExecutionState())
    data["phase"] = "NOPE"
    with pytest.raises(ValueError):
        restore_state(data)


def test_restore_rejects_negative_counter():
    data = persist_state(ExecutionState())
    data["timeouts"] = -1
    with pytest.raises(ValueError):
        restore_state(data)


def test_restore_rejects_nonfinite_float():
    data = persist_state(ExecutionState())
    data["deadline"] = float("inf")
    with pytest.raises(ValueError):
        restore_state(data)


def test_restore_rejects_malformed_decimal():
    data = persist_state(_entered())
    data["legs"]["Y"]["target_quantity"] = "not-a-number"
    with pytest.raises(ValueError):
        restore_state(data)


def test_restore_rejects_missing_key():
    data = persist_state(ExecutionState())
    del data["phase"]
    with pytest.raises(ValueError):
        restore_state(data)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_indicators/test_execution_recovery.py -q`
Expected: FAIL with `ImportError: cannot import name 'persist_state'`

- [ ] **Step 3: Implement persist/restore**

Append to the module:

```python
def persist_state(state: ExecutionState) -> dict[str, Any]:
    return {
        "phase": state.phase.value,
        "legs": {
            name: {
                "leg": leg.leg,
                "target_quantity": None if leg.target_quantity is None else str(leg.target_quantity),
                "filled_quantity": str(leg.filled_quantity),
                "client_order_id": leg.client_order_id,
                "status": leg.status.value,
            }
            for name, leg in state.legs.items()
        },
        "pending_order_ids": sorted(state.pending_order_ids),
        "deadline": state.deadline,
        "entry_beta": state.entry_beta,
        "cooldown_deadline": state.cooldown_deadline,
        "entries_started": state.entries_started,
        "exits_started": state.exits_started,
        "timeouts": state.timeouts,
        "rollbacks": state.rollbacks,
        "slippage_rejections": state.slippage_rejections,
        "recovery_reason": state.recovery_reason,
        "kalman": None if state.kalman is None else dict(state.kalman),
        "max_slippage_bps": str(state.max_slippage_bps),
        "fill_ids": sorted(state.fill_ids),
    }


def restore_state(data: Mapping[str, Any]) -> ExecutionState:
    try:
        phase = ExecutionPhase(data["phase"])
        legs = {}
        for name, item in data["legs"].items():
            legs[name] = LegState(
                leg=item["leg"],
                target_quantity=None if item["target_quantity"] is None else _decimal("target_quantity", item["target_quantity"]),
                filled_quantity=_decimal("filled_quantity", item["filled_quantity"]),
                client_order_id=item["client_order_id"],
                status=LegStatus(item["status"]),
            )
        pending = frozenset(data["pending_order_ids"])
        deadline = data["deadline"]
        entry_beta = data["entry_beta"]
        cooldown = data["cooldown_deadline"]
        counters = {key: data[key] for key in ("entries_started", "exits_started", "timeouts", "rollbacks", "slippage_rejections")}
        recovery_reason = data["recovery_reason"]
        kalman = data["kalman"]
        max_slippage_bps = _decimal("max_slippage_bps", data["max_slippage_bps"])
        fill_ids = frozenset(data["fill_ids"])
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise ValueError(f"invalid persisted state: {exc}") from exc
    return ExecutionState(
        phase=phase,
        legs=legs,
        pending_order_ids=pending,
        deadline=deadline,
        entry_beta=entry_beta,
        cooldown_deadline=cooldown,
        **counters,
        recovery_reason=recovery_reason,
        kalman=kalman,
        max_slippage_bps=max_slippage_bps,
        fill_ids=fill_ids,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_indicators/test_execution_recovery.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git add src/sngw_trader/indicators/execution_recovery.py tests/test_indicators/test_execution_recovery.py
git commit -m "feat(s05): add persisted state round-trip validation"
```

---

### Task 8: Full verification

**Files:**
- No new files.

- [ ] **Step 1: Run the full indicators test suite**

Run: `python -m pytest tests/test_indicators/ -q`
Expected: PASS — no regressions in S01–S04 suites.

- [ ] **Step 2: Run the focused suite verbosely and confirm the spec's test contract is covered**

Run: `python -m pytest tests/test_indicators/test_execution_recovery.py -v`
Expected: PASS, including: full fill, partial fill, timeout before/after deadline, rollback with/without exposure, emergency flatten, slippage rejection, duplicate fill, matching/missing/unexpected reconciliation, failed recovery entry block, persisted state round-trip.

- [ ] **Step 3: Confirm the module boundary**

Run: `python -c "import ast, sys; tree = ast.parse(open('src/sngw_trader/indicators/execution_recovery.py', encoding='utf-8').read()); imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]; print(imports)"`
Expected: only stdlib imports (`enum`, `math`, `collections.abc`, `dataclasses`, `decimal`, `types`, `typing`) — no `nautilus_trader`, no exchange SDKs.

- [ ] **Step 4: Commit (if anything was touched) and update handoff docs**

```powershell
git add docs/superpowers/plans/2026-09-15-kalman-mr-s05-execution-recovery.md
git commit -m "docs(s05): mark execution recovery plan tasks complete"
```

Update the older S05 plan checklist and this plan's checkboxes as tasks complete.

---

## Self-Review Notes

- Spec coverage: entry/exit transitions (T2), timeout/rollback (T3), slippage (T4), emergency flatten + cooldown (T5), reconcile + restart gate (T6), persistence round-trip (T7), all Test Contract bullets mapped to named tests in T1–T7, boundary check (T8).
- `Sequence` import added in T5; `_finite_value` helper defined in T2, reused by T5/`confirm_flatten`.
- `test_confirm_flatten_completes_to_closed_with_cooldown` passes fills only for leg Y (X has zero held) — a zero-quantity `FillObservation` cannot be constructed by design.