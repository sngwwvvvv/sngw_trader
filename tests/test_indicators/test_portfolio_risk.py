from decimal import Decimal

import pytest

from sngw_trader.indicators.contract_sizing import PairSizing
from sngw_trader.indicators.cost_model import CostBreakdown
from sngw_trader.indicators.portfolio_risk import (
    PairCandidate,
    PortfolioSnapshot,
    RiskLimits,
    approve_pair,
)


def _limits() -> RiskLimits:
    return RiskLimits(
        max_active_pairs=4,
        max_gross_exposure_ratio=Decimal("0.5"),
        max_pair_margin_ratio=Decimal("0.25"),
        max_asset_exposure_usdt=Decimal("2000"),
        max_daily_loss_usdt=Decimal("500"),
        max_drawdown_ratio=Decimal("0.2"),
        min_liquidation_buffer_usdt=Decimal("500"),
    )


def _snapshot(**overrides: object) -> PortfolioSnapshot:
    values = dict(
        equity_usdt=Decimal("10000"),
        peak_equity_usdt=Decimal("10000"),
        daily_loss_usdt=Decimal("100"),
        liquidation_buffer_usdt=Decimal("2000"),
        active_pairs=frozenset({"existing"}),
        gross_notional_usdt=Decimal("2000"),
        asset_exposure_usdt={"BTC": Decimal("500")},
    )
    values.update(overrides)
    return PortfolioSnapshot(**values)


def _candidate(**overrides: object) -> PairCandidate:
    values = dict(
        pair_id="eth-btc",
        y_asset="ETH",
        x_asset="BTC",
        sizing=PairSizing(
            y_quantity=Decimal("10"),
            x_quantity=Decimal("10"),
            y_notional_usdt=Decimal("1000"),
            x_notional_usdt=Decimal("1000"),
            gross_notional_usdt=Decimal("2000"),
            hedge_error_usdt=Decimal("0"),
        ),
        margin_usdt=Decimal("1000"),
        cost=CostBreakdown(10.0, 10.0, 0.0, 20.0),
        y_price_usdt=Decimal("100"),
        x_price_usdt=Decimal("100"),
        y_side=1,
        x_side=-1,
    )
    values.update(overrides)
    return PairCandidate(**values)


def test_approves_pair_when_all_projected_limits_have_capacity():
    decision = approve_pair(_snapshot(), _candidate(), _limits())

    assert decision.approved is True
    assert decision.reasons == ()
    assert decision.projected_gross_notional_usdt == Decimal("4000")
    assert decision.projected_asset_exposure_usdt == {
        "BTC": Decimal("-500"),
        "ETH": Decimal("1000"),
    }


def test_rejects_shared_asset_exposure_after_signed_aggregation():
    decision = approve_pair(
        _snapshot(asset_exposure_usdt={"BTC": Decimal("1500")}),
        _candidate(x_side=1),
        _limits(),
    )

    assert decision.approved is False
    assert "ASSET_EXPOSURE" in decision.reasons
    assert decision.projected_asset_exposure_usdt["BTC"] == Decimal("2500")


def test_rejects_pair_margin_and_gross_exposure_without_partial_approval():
    decision = approve_pair(
        _snapshot(
            gross_notional_usdt=Decimal("4000"),
            liquidation_buffer_usdt=Decimal("5000"),
        ),
        _candidate(margin_usdt=Decimal("3000")),
        _limits(),
    )

    assert decision.approved is False
    assert set(decision.reasons) == {"PAIR_MARGIN", "GROSS_EXPOSURE"}


@pytest.mark.parametrize(
    ("snapshot", "candidate", "reason"),
    [
        (_snapshot(daily_loss_usdt=Decimal("490")), _candidate(), "DAILY_LOSS"),
        (_snapshot(equity_usdt=Decimal("7900")), _candidate(), "DRAWDOWN"),
        (
            _snapshot(liquidation_buffer_usdt=Decimal("1400")),
            _candidate(),
            "LIQUIDATION_BUFFER",
        ),
    ],
)
def test_rejects_loss_drawdown_and_liquidation_lockouts(snapshot, candidate, reason):
    decision = approve_pair(snapshot, candidate, _limits())

    assert decision.approved is False
    assert reason in decision.reasons


def test_rejects_duplicate_pair_and_active_pair_capacity():
    duplicate = approve_pair(
        _snapshot(active_pairs=frozenset({"eth-btc", "a", "b", "c"})),
        _candidate(),
        _limits(),
    )
    capacity = approve_pair(
        _snapshot(active_pairs=frozenset({"a", "b", "c", "d"})),
        _candidate(),
        _limits(),
    )

    assert duplicate.approved is False
    assert duplicate.reasons == ("DUPLICATE_PAIR",)
    assert capacity.approved is False
    assert capacity.reasons == ("MAX_ACTIVE_PAIRS",)


def test_missing_equity_or_cost_fails_closed():
    missing_equity = approve_pair(
        _snapshot(equity_usdt=None),
        _candidate(),
        _limits(),
    )
    missing_cost = approve_pair(_snapshot(), _candidate(cost=None), _limits())

    assert missing_equity.approved is False
    assert "MISSING_DATA" in missing_equity.reasons
    assert missing_cost.approved is False
    assert "MISSING_DATA" in missing_cost.reasons


def test_missing_nested_exposure_cost_sizing_or_price_fails_closed():
    missing_asset = approve_pair(
        _snapshot(asset_exposure_usdt={"BTC": None}), _candidate(), _limits()
    )
    missing_cost = approve_pair(
        _snapshot(), _candidate(cost=CostBreakdown(10.0, 10.0, 0.0, None)), _limits()
    )
    malformed_cost = approve_pair(
        _snapshot(), _candidate(cost=CostBreakdown(None, 10.0, 0.0, 20.0)), _limits()
    )
    malformed_sizing = approve_pair(
        _snapshot(),
        _candidate(
            sizing=PairSizing(
                y_quantity=Decimal("10"),
                x_quantity=Decimal("10"),
                y_notional_usdt=Decimal("1000"),
                x_notional_usdt=Decimal("1000"),
                gross_notional_usdt=None,
                hedge_error_usdt=Decimal("0"),
            )
        ),
        _limits(),
    )
    missing_price = approve_pair(_snapshot(), _candidate(y_price_usdt=None), _limits())

    assert all(
        not decision.approved
        and decision.reasons == ("MISSING_DATA",)
        for decision in (
            missing_asset,
            missing_cost,
            malformed_cost,
            malformed_sizing,
            missing_price,
        )
    )


def test_negative_gross_or_daily_loss_fails_closed():
    negative_gross = approve_pair(
        _snapshot(gross_notional_usdt=Decimal("-100000")), _candidate(), _limits()
    )
    negative_loss = approve_pair(
        _snapshot(daily_loss_usdt=Decimal("-1000")), _candidate(), _limits()
    )

    assert negative_gross.reasons == ("MISSING_DATA",)
    assert negative_loss.reasons == ("MISSING_DATA",)


def test_pair_candidate_rejects_non_directional_leg():
    with pytest.raises(ValueError, match="side must be 1 or -1"):
        _candidate(y_side=0)


def test_portfolio_snapshot_exposure_is_immutable():
    snapshot = _snapshot()

    with pytest.raises(TypeError):
        snapshot.asset_exposure_usdt["ETH"] = Decimal("1")
