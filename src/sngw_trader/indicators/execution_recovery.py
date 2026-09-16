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


_ACTIVE_PHASES = (
    ExecutionPhase.ENTRY_PENDING,
    ExecutionPhase.ENTRY_PARTIAL,
    ExecutionPhase.EXIT_PENDING,
    ExecutionPhase.EXIT_PARTIAL,
)


def _finite_value(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _leg_held(state: ExecutionState, leg: LegState) -> Decimal:
    """Held quantity for a leg: remaining holding during exit, executed fill otherwise."""
    if state.phase is ExecutionPhase.CLOSED:
        return Decimal("0")
    if state.phase in (ExecutionPhase.EXIT_PENDING, ExecutionPhase.EXIT_PARTIAL):
        target = leg.target_quantity or Decimal("0")
        return max(target - leg.filled_quantity, Decimal("0"))
    return leg.filled_quantity


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
            "Y": replace(
                state.legs["Y"],
                client_order_id=y_order_id,
                filled_quantity=Decimal("0"),
                status=LegStatus.PENDING,
            ),
            "X": replace(
                state.legs["X"],
                client_order_id=x_order_id,
                filled_quantity=Decimal("0"),
                status=LegStatus.PENDING,
            ),
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
