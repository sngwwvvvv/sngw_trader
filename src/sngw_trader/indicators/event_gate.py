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
