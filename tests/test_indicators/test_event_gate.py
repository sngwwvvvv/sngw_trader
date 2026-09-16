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
from sngw_trader.indicators.event_gate import evaluate_gate


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
    decision = _evaluate(events=(_record(expires_at=140.0),), last_event_check=80.0)
    assert decision.reasons == ("STALE_EVENT_STATE",)


def test_missing_and_stale_accumulate_together():
    decision = _evaluate(last_event_check=80.0)
    assert decision.reasons == ("MISSING_EVENT_STATE", "STALE_EVENT_STATE")
    decision = _evaluate(events=(_record(),), last_event_check=80.0)
    assert decision.reasons == ("STALE_EVENT_STATE", "EVENT_ACTIVE_NEWS")


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
    decision = _evaluate(events=(_record(),), now=200.0, last_event_check=180.0)
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
