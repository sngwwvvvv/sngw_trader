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
