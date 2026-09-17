import pytest

from sngw_trader.indicators.cost_model import (
    Fill,
    FundingObservation,
    calculate_cost,
)


def _fills() -> list[Fill]:
    return [
        Fill("factor", 1.0, 10_000.0, 0.0005, 0.0003),
        Fill("factor", -1.0, 10_000.0, 0.0005, 0.0003),
        Fill("peer", 2.0, 5_000.0, 0.0005, 0.0006),
        Fill("peer", -2.0, 5_000.0, 0.0005, 0.0006),
    ]


def test_four_fills_charge_fee_and_half_spread_per_fill():
    result = calculate_cost(
        _fills(),
        [
            FundingObservation("factor", 1, 1.0, 10_000.0, 0.0),
            FundingObservation("peer", 1, 2.0, 5_000.0, 0.0),
        ],
        expected_funding_timestamps=[1],
    )

    assert result.fee_usdt == pytest.approx(20.0)
    assert result.spread_usdt == pytest.approx(18.0)
    assert result.funding_usdt == pytest.approx(0.0)
    assert result.total_usdt == pytest.approx(38.0)


def test_funding_uses_signed_quantity_for_positive_negative_and_multiple_periods():
    result = calculate_cost(
        _fills(),
        [
            FundingObservation("factor", 1, 1.0, 100.0, 0.0001),
            FundingObservation("factor", 2, 1.0, 100.0, -0.0002),
            FundingObservation("peer", 1, -2.0, 50.0, 0.0003),
            FundingObservation("peer", 2, -2.0, 50.0, -0.0001),
        ],
        expected_funding_timestamps=[1, 2],
    )

    assert result.funding_usdt == pytest.approx(-0.03)
    assert result.total_usdt == pytest.approx(37.97)


def test_missing_funding_data_fails_closed():
    with pytest.raises(ValueError, match="funding observations are required"):
        calculate_cost(_fills(), [], expected_funding_timestamps=[1])


def test_missing_leg_funding_data_fails_closed():
    with pytest.raises(ValueError, match="missing funding observations for peer"):
        calculate_cost(
            _fills(),
            [FundingObservation("factor", 1, 1.0, 10_000.0, 0.0)],
            expected_funding_timestamps=[1],
        )


def test_missing_funding_period_fails_closed_for_each_leg():
    with pytest.raises(ValueError, match="missing funding observation for factor at 2"):
        calculate_cost(
            _fills(),
            [
                FundingObservation("factor", 1, 1.0, 10_000.0, 0.0),
                FundingObservation("peer", 1, 2.0, 5_000.0, 0.0),
                FundingObservation("peer", 2, 2.0, 5_000.0, 0.0),
            ],
            expected_funding_timestamps=[1, 2],
        )


def test_duplicate_funding_period_fails_closed():
    with pytest.raises(ValueError, match="duplicate funding observation"):
        calculate_cost(
            _fills(),
            [
                FundingObservation("factor", 1, 1.0, 10_000.0, 0.0),
                FundingObservation("factor", 1, 1.0, 10_000.0, 0.0),
                FundingObservation("peer", 1, 2.0, 5_000.0, 0.0),
            ],
            expected_funding_timestamps=[1],
        )
