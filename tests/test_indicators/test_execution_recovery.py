# tests/test_indicators/test_execution_recovery.py
from decimal import Decimal

import pytest

from sngw_trader.indicators.execution_recovery import (
    ActionIntent,
    ExecutionPhase,
    ExecutionState,
    FillObservation,
    LegStatus,
    LegState,
    TransitionResult,
    on_fill,
    on_order_failure,
    on_timeout,
    begin_entry,
    begin_exit,
    CANCEL_UNFILLED,
    FLATTEN_FILLED,
    SUBMIT,
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
