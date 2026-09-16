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
