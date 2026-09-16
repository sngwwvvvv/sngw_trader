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


def _leg_held(state: ExecutionState, leg: LegState) -> Decimal:
    """Held quantity for a leg: remaining holding during exit, executed fill otherwise."""
    if state.phase in (ExecutionPhase.EXIT_PENDING, ExecutionPhase.EXIT_PARTIAL):
        target = leg.target_quantity or Decimal("0")
        return max(target - leg.filled_quantity, Decimal("0"))
    return leg.filled_quantity
